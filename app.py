import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
import time

os.environ['TZ'] = 'Asia/Seoul'
time.tzset()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chatclub_secret_key_1234")

# Neon DB 연동 설정
DATABASE_URL = os.environ.get("DATABASE_URL", "your_neon_db_connection_string_here")

def get_db_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 유저 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(50) PRIMARY KEY,
            password VARCHAR(255) NOT NULL,
            nickname VARCHAR(50) NOT NULL,
            bio VARCHAR(255) DEFAULT '안녕하세요! ChatClub입니다.',
            profile_img VARCHAR(255) DEFAULT 'default.png',
            is_active BOOLEAN DEFAULT TRUE,
            last_seen TIMESTAMP
        );
    """)
    
    # 2. 팔로우 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            column_id SERIAL PRIMARY KEY,
            follower VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            following VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE (follower, following)
        );
    """)
    
    # 3. 익명 에스크 메시지 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ask_messages (
            id SERIAL PRIMARY KEY,
            target_user VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            content TEXT NOT NULL,
            answer TEXT,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # 4. 단톡방 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id SERIAL PRIMARY KEY,
            room_name VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL
        );
    """)

    # 4-1. 단톡방 멤버 테이블 (기존 코드 누락분 보완)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS room_members (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE (room_id, user_id)
        );
    """)
    
    # 5. 단톡방 메시지 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS room_messages (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 6. 1:1 개인톡(DM) 메시지 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS direct_messages (
            id SERIAL PRIMARY KEY,
            sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            receiver VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 7. 오픈채팅방 테이블 (기존 코드 누락분 보완)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_rooms (
            id SERIAL PRIMARY KEY,
            title VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
            sub_host VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 8. 오픈채팅 메시지 테이블 (기존 코드 누락분 보완)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_messages (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
            sender_anon VARCHAR(50) NOT NULL,
            sender_real_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 9. 오픈채팅 강퇴 유저 테이블 (기존 코드 누락분 보완)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_banned_users (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
            username VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE (room_id, username)
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()

# 앱 시작 시 DB 초기화
init_db()

@app.before_request
def update_last_seen():
    user = session.get('user')
    role = session.get('role', 'USER')
    if user and role not in ['ADMIN', 'H_ADMIN'] and user != 'admin':
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = %s", (user,))
            conn.commit()
            cur.close()
            conn.close()
        except:
            pass

@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if user:
        query_rooms = """
            SELECT DISTINCT cr.id, cr.room_name 
            FROM chat_rooms cr
            LEFT JOIN room_members rm ON cr.id = rm.room_id
            WHERE cr.created_by = %s OR rm.user_id = %s
            ORDER BY cr.id DESC
        """
        cur.execute(query_rooms, (user, user))
        my_rooms = cur.fetchall()
        
        cur.execute("""
            SELECT u.username, u.nickname,
                   CASE WHEN u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes' THEN TRUE ELSE FALSE END as is_online
            FROM users u
            JOIN follows f ON u.username = f.following
            WHERE f.follower = %s AND u.is_active = TRUE
            ORDER BY is_online DESC, u.nickname ASC
        """, (user,))
        all_users = cur.fetchall()
        
    cur.close()
    conn.close()
    return render_template('index.html', my_rooms=my_rooms, all_users=all_users, user=user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        nickname = request.form.get('nickname').strip()
        
        if not username or not password or not nickname:
            return "모든 필드를 입력해주세요.", 400
            
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT is_active FROM users WHERE username = %s", (username,))
        existing = cur.fetchone()
        
        if existing:
            if existing['is_active'] == False:
                cur.execute("""
                    UPDATE users 
                    SET password = %s, nickname = %s, bio = '안녕하세요! ChatClub입니다.', profile_img = 'default.png', is_active = TRUE 
                    WHERE username = %s
                """, (password, nickname, username))
                conn.commit()
                cur.close()
                conn.close()
                session['user'] = username
                return redirect(url_for('index'))
            else:
                cur.close()
                conn.close()
                return "이미 존재하는 아이디입니다.", 400
        
        cur.execute("INSERT INTO users (username, password, nickname) VALUES (%s, %s, %s)", (username, password, nickname))
        conn.commit()
        cur.close()
        conn.close()
        session['user'] = username
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')

        if username == 'admin' and password == 'admin1234':
            session['user'] = 'admin'
            return "<script>alert('👑 최고 관리자 모드로 로그인되었습니다.'); location.href='/admin/dashboard';</script>"

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s AND password = %s AND is_active = TRUE", (username, password))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            session['user'] = user['username']
            return redirect(url_for('index'))
        else:
            return "아이디 또는 비밀번호가 잘못되었거나 탈퇴한 회원입니다.", 401
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/delete_account', methods=['POST'])
def delete_account():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = FALSE WHERE username = %s", (user,))
    cur.execute("DELETE FROM follows WHERE follower = %s OR following = %s", (user, user))
    conn.commit()
    cur.close()
    conn.close()
    
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
def dm_chat(username):
    my_id = session.get('user')
    if not my_id: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, nickname FROM users WHERE username = %s AND is_active = TRUE", (username,))
    receiver = cur.fetchone()
    if not receiver:
        cur.close()
        conn.close()
        return "존재하지 않거나 탈퇴한 회원입니다.", 404
        
    cur.execute("""
        UPDATE direct_messages 
        SET is_read = TRUE 
        WHERE sender = %s AND receiver = %s AND is_read = FALSE
    """, (username, my_id))
    conn.commit()
        
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cur.execute("INSERT INTO direct_messages (sender, receiver, message) VALUES (%s, %s, %s)", (my_id, username, message))
            conn.commit()
            return redirect(url_for('dm_chat', username=username))
            
    cur.execute("""
        SELECT sender, message, created_at, is_read FROM direct_messages 
        WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s)
        ORDER BY id ASC
    """, (my_id, username, username, my_id))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('dm.html', receiver=receiver, messages=messages)

@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        invited_users = request.form.getlist('invited_users')
        
        if not room_name:
            return "방 이름을 입력해주세요.", 400
            
        try:
            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id", (room_name, user))
            row = cur.fetchone()
            room_id = row.get('id') if isinstance(row, dict) else row[0]
                
            cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s)", (room_id, user))
            
            for invited_user in invited_users:
                if invited_user != user:
                    cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s)", (room_id, invited_user))
                    
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('group_chat', room_id=room_id))
            
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return f"단톡방 생성 중 오류가 발생했습니다: {str(e)}", 500
            
    try:
        query = """
            SELECT u.username, u.nickname 
            FROM follows f
            JOIN users u ON f.following = u.username
            WHERE f.follower = %s AND u.is_active = TRUE
        """
        cur.execute(query, (user,))
        user_list = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        cur.close()
        conn.close()
        user_list = []
        
    return render_template('create_group.html', user_list=user_list)

@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def group_chat(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 1 FROM chat_rooms cr
        LEFT JOIN room_members rm ON cr.id = rm.room_id
        WHERE cr.id = %s AND (cr.created_by = %s OR rm.user_id = %s)
    """, (room_id, user, user))
    
    is_member = cur.fetchone()
    if not is_member:
        cur.close()
        conn.close()
        return "❌ 이 단톡방에 초대받지 않았습니다. 입장 권한이 없습니다!", 403
        
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cur.execute("INSERT INTO room_messages (room_id, sender, message) VALUES (%s, %s, %s)", (room_id, user, message))
            conn.commit()
            return redirect(url_for('group_chat', room_id=room_id))
                
    cur.execute("SELECT room_name FROM chat_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
        
    cur.execute("SELECT sender, message, created_at FROM room_messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
    messages = cur.fetchall()
        
    cur.close()
    conn.close()
    return render_template('chat.html', room=room, room_id=room_id, messages=messages)

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    results = []
    if query:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT username, nickname FROM users WHERE username LIKE %s AND is_active = TRUE", (f"%{query}%",))
        results = cur.fetchall()
        cur.close()
        conn.close()
    return render_template('search_results.html', query=query, results=results)
@app.route('/my_chats')
def my_joined_rooms():
    current_user_id = session.get('user_id')
    
    if not current_user_id:
        flash("로그인이 필요한 서비스입니다.")
        return redirect('/login')
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 💡 핵심 SQL: 내가 참여(room_members)한 방들의 상세 정보(chat_rooms)를 결합해서 가져옵니다.
    # 방장 ID(creator_id)도 같이 가져와서 내가 방장인 방은 따로 표시할 수 있게 합니다.
    cur.execute("""
        SELECT r.id, r.title, r.creator_id, 
               (SELECT COUNT(*) FROM room_members WHERE room_id = r.id) as member_count
        FROM chat_rooms r
        JOIN room_members m ON r.id = m.room_id
        WHERE m.user_id = %s
        ORDER BY r.id DESC
    """, (current_user_id,))
    
    my_rooms = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # 내일 완성할 HTML 파일로 데이터를 넘겨줍니다.
    return render_template('my_chats.html', rooms=my_rooms, current_user_id=int(current_user_id))
@app.route('/user/<username>')
def user_profile(username):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s AND is_active = TRUE", (username,))
    profile_user = cur.fetchone()
    
    if not profile_user:
        cur.close()
        conn.close()
        return "존재하지 않거나 탈퇴한 유저입니다.", 404
        
    if session.get('user') == username:
        cur.execute("UPDATE ask_messages SET is_read = TRUE WHERE target_user = %s", (username,))
        conn.commit()

    cur.execute("SELECT COUNT(*) AS cnt FROM follows WHERE following = %s", (username,))
    followers_count = cur.fetchone()['cnt']
    
    is_following = False
    if session.get('user'):
        cur.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (session['user'], username))
        is_following = cur.fetchone() is not None
        
    cur.execute("SELECT * FROM ask_messages WHERE target_user = %s ORDER BY id DESC", (username,))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('user.html', profile_user=profile_user, followers_count=followers_count, is_following=is_following, messages=messages)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    bio = request.form.get('bio', '')
    profile_img = request.files.get('profile_img')
    conn = get_db_connection()
    cur = conn.cursor()
    if profile_img and profile_img.filename != '':
        filename = f"{user}_{profile_img.filename}"
        try:
            if not os.path.exists('static'): os.makedirs('static')
            profile_img.save(os.path.join('static', filename))
            cur.execute("UPDATE users SET bio = %s, profile_img = %s WHERE username = %s", (bio, filename, user))
        except:
            cur.execute("UPDATE users SET bio = %s WHERE username = %s", (bio, user))
    else:
        cur.execute("UPDATE users SET bio = %s WHERE username = %s", (bio, user))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('user_profile', username=user))

@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content')
    if content:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO ask_messages (target_user, content) VALUES (%s, %s)", (username, content))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('user_profile', username=username))

@app.route('/answer/<int:msg_id>', methods=['POST'])
def answer(msg_id):
    user = session.get('user')
    answer_text = request.form.get('answer')
    if user:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE ask_messages SET answer = %s WHERE id = %s AND target_user = %s", (answer_text, msg_id, user))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for('user_profile', username=user))

@app.route('/follow/<username>', methods=['POST'])
def follow(username):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (user, username))
    if cur.fetchone():
        cur.execute("DELETE FROM follows WHERE follower = %s AND following = %s", (user, username))
    else:
        cur.execute("INSERT INTO follows (follower, following) VALUES (%s, %s)", (user, username))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('user_profile', username=username))

@app.route('/group/leave/<int:room_id>', methods=['POST'])
def leave_group(room_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT 1 FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        is_member = cur.fetchone()
        
        cur.execute("SELECT created_by, room_name FROM chat_rooms WHERE id = %s", (room_id,))
        room_info = cur.fetchone()
        
        if not is_member and (room_info and room_info['created_by'] != user):
            cur.close()
            conn.close()
            return "이 방의 멤버가 아닙니다.", 400
            
        cur.execute("SELECT nickname FROM users WHERE username = %s", (user,))
        my_info = cur.fetchone()
        nickname = my_info['nickname'] if my_info else user
        
        system_msg = f"📢 {nickname}(@{user})님이 퇴장하셨습니다."
        cur.execute("INSERT INTO room_messages (room_id, sender, message) VALUES (%s, %s, %s)", (room_id, user, system_msg))
        
        cur.execute("DELETE FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        
        if room_info and room_info['created_by'] == user:
            cur.execute("UPDATE chat_rooms SET created_by = NULL WHERE id = %s", (room_id,))

        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('index'))
        
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return f"방을 나가는 중 오류가 발생했습니다: {str(e)}", 500

# ----------------------------------------------------------------
# 🌿 오픈채팅 기능 라우팅
# ----------------------------------------------------------------

@app.route('/open_chat_list')
def open_chat_list():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, created_by, sub_host, created_at FROM open_rooms ORDER BY created_at DESC")
    rooms = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template('open_room_list.html', rooms=rooms)

@app.route('/create_open_room', methods=['POST'])
def create_open_room():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    room_title = request.form.get('room_title', '').strip()
    if not room_title:
        return "방 제목을 입력해주세요.", 400
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO open_rooms (title, created_by) VALUES (%s, %s) RETURNING id", (room_title, user))
    new_room_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect(url_for('open_chat_room', room_id=new_room_id))

@app.route('/open_chat/room/<int:room_id>', methods=['GET', 'POST'])
def open_chat_room(room_id):
    user = session.get('user')
    if not user: 
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT 1 FROM open_banned_users WHERE room_id = %s AND username = %s", (room_id, user))
    if cur.fetchone():
        cur.close()
        conn.close()
        return "<script>alert('해당 방장 또는 부방장에 의해 강퇴 처리되어 입장할 수 없습니다.'); history.back();</script>"
    
    cur.execute("SELECT id, title, created_by, sub_host FROM open_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    if not room:
        cur.close()
        conn.close()
        return "존재하지 않는 방입니다.", 404
        
    is_host = (room['created_by'] == user or user == 'admin')
    is_sub_host = (room['sub_host'] == user)
    
    if request.method == 'POST':
        custom_name = request.form.get('custom_name', '').strip()
        if custom_name:
            session[f'anon_name_{room_id}'] = custom_name
            cur.close()
            conn.close()
            return redirect(url_for('open_chat_room', room_id=room_id))
            
    anon_name = session.get(f'anon_name_{room_id}')
    
    if not anon_name:
        cur.close()
        conn.close()
        return render_template('open_chat.html', room=room, anon_name=None)
    
    cur.execute("""
        SELECT id, sender_anon, sender_real_id, message, created_at 
        FROM open_messages 
        WHERE room_id = %s 
        ORDER BY created_at ASC LIMIT 100
    """, (room_id,))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template(
        'open_chat.html', 
        room=room, 
        messages=messages, 
        anon_name=anon_name, 
        is_host=is_host, 
        is_sub_host=is_sub_host
    )

@app.route('/send_open_message/<int:room_id>', methods=['POST'])
def send_open_message(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    message = request.form.get('message', '').strip()
    anon_name = session.get(f'anon_name_{room_id}', '익명의 유저')
    
    if message:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT 1 FROM open_banned_users WHERE room_id = %s AND username = %s", (room_id, user))
        if cur.fetchone():
            cur.close()
            conn.close()
            return "채팅 권한이 없습니다.", 403
            
        cur.execute("""
            INSERT INTO open_messages (room_id, sender_anon, sender_real_id, message) 
            VALUES (%s, %s, %s, %s)
        """, (room_id, anon_name, user, message))
        conn.commit()
        cur.close()
        conn.close()
        
    return redirect(url_for('open_chat_room', room_id=room_id))

@app.route('/open_chat/room/<int:room_id>/set_sub', methods=['POST'])
def open_chat_set_sub(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    target_user = request.form.get('target_user')
    action = request.form.get('action')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT created_by FROM open_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    if not room or room['created_by'] != user:
        cur.close()
        conn.close()
        return "방장만 부방장을 지정할 수 있습니다.", 403
        
    if action == 'appoint':
        cur.execute("UPDATE open_rooms SET sub_host = %s WHERE id = %s", (target_user, room_id))
    elif action == 'dismiss':
        cur.execute("UPDATE open_rooms SET sub_host = NULL WHERE id = %s", (room_id,))
        
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('open_chat_room', room_id=room_id))

@app.route('/open_chat/room/<int:room_id>/ban', methods=['POST'])
def open_chat_ban_user(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    target_user = request.form.get('target_user')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT created_by, sub_host FROM open_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    if not room:
        cur.close()
        conn.close()
        return "방이 존재하지 않습니다.", 404
        
    is_host = (room['created_by'] == user or user == 'admin')
    is_sub_host = (room['sub_host'] == user)
    
    if not (is_host or is_sub_host):
        cur.close()
        conn.close()
        return "강퇴 권한이 없습니다.", 403
        
    if is_sub_host and target_user == room['created_by']:
        cur.close()
        conn.close()
        return "부방장은 방장을 강퇴할 수 없습니다.", 403
        
    cur.execute("INSERT INTO open_banned_users (room_id, username) VALUES (%s, %s) ON CONFLICT DO NOTHING", (room_id, target_user))
    cur.execute("DELETE FROM open_messages WHERE room_id = %s AND sender_real_id = %s", (room_id, target_user))
    
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('open_chat_room', room_id=room_id))

# ----------------------------------------------------------------
# 👑 [최고 관리자 MASTER PANEL] 전용 백엔드 기능 
# ----------------------------------------------------------------

# [통합 및 보완 완료] 유저 전체 상세조회 및 안정적 결함 방지 반영 대시보드
@app.route('/admin/dashboard')
def admin_dashboard():
    user = session.get('user')
    role = session.get('role', 'USER')
    
    # 👑 최고관리자 검증 (admin이거나 ADMIN 역할일 때만 허용)
    if user != 'admin' and role not in ['ADMIN', 'H_ADMIN']:
        return "관리자 권한이 없습니다.", 403
        
    my_rooms = []
    all_users = []
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. HTML의 {% for g in my_rooms %} 구조에 맞게 단톡방 조회
        # 데이터 순서: g[0] = id, g[1] = room_name
        cur.execute("SELECT id, room_name FROM chat_rooms ORDER BY id DESC")
        my_rooms = cur.fetchall()
        
        # 2. HTML의 {% for u in all_users %} 구조에 맞게 회원 조회 (role 컬럼 제거)
        # 데이터 순서: u[0] = username, u[1] = nickname, u[2] = bio, u[3] = is_active
        cur.execute("""
            SELECT username, nickname, bio, is_active 
            FROM users 
            ORDER BY username ASC
        """)
        all_users = cur.fetchall()
        
    except Exception as e:
        print(f"DB Error: {e}")
        pass
        
    cur.close()
    conn.close()
    
    return render_template('admin_dashboard.html', my_rooms=my_rooms, all_users=all_users, user=user, role=role)
@app.route('/admin/ban_user/<username>', methods=['POST'])
def admin_ban_user(username):
    if session.get('user') != 'admin':
        return "권한이 없습니다.", 403
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("UPDATE users SET is_active = FALSE WHERE username = %s", (username,))
    cur.execute("DELETE FROM follows WHERE follower = %s OR following = %s", (username, username))
    
    conn.commit()
    cur.close()
    conn.close()
    return "<script>alert('해당 유저가 영구 차단(탈퇴) 되었습니다.'); location.href='/admin/dashboard';</script>"

@app.route('/admin/delete_group/<int:room_id>', methods=['POST'])
def admin_delete_group(room_id):
    if session.get('user') != 'admin':
        return "권한이 없습니다.", 403
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_rooms WHERE id = %s", (room_id,))
    conn.commit()
    cur.close()
    conn.close()
    return "<script>alert('일반 단톡방이 강제 삭제되었습니다.'); location.href='/admin/dashboard';</script>"

@app.route('/open_chat/room/<int:room_id>/delete', methods=['POST'])
def delete_open_room(room_id):
    user = session.get('user')
    if not user: 
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT created_by FROM open_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    if not room:
        cur.close()
        conn.close()
        return "존재하지 않는 방입니다.", 404
        
    if user == 'admin' or room['created_by'] == user:
        cur.execute("DELETE FROM open_rooms WHERE id = %s", (room_id,))
        conn.commit()
        cur.close()
        conn.close()
        return "<script>alert('오픈채팅방이 성공적으로 삭제되었습니다.'); location.href='/open_chat_list';</script>"
    else:
        cur.close()
        conn.close()
        return "삭제 권한이 없습니다.", 403
# 파일 맨 아래에 기존 내용을 지우지 말고 그냥 추가로 붙여넣으세요!
from flask import jsonify, session

@app.route('/delete_chat_message/<string:chat_type>/<int:message_id>', methods=['POST'])
def delete_message(chat_type, message_id):
    user = session.get('user')
    
    if not user:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 오픈채팅 메시지 삭제 로직
        if chat_type == 'open':
            cur.execute("SELECT sender_real_id, room_id FROM open_messages WHERE id = %s", (message_id,))
            msg = cur.fetchone()
            if not msg:
                return jsonify({"success": False, "message": "존재하지 않는 메시지입니다."}), 404
                
            msg_sender = msg['sender_real_id'] if isinstance(msg, dict) else msg[0]
            room_id = msg['room_id'] if isinstance(msg, dict) else msg[1]
            
            # 방장 조회
            cur.execute("SELECT created_by FROM chat_rooms WHERE id = %s", (room_id,))
            room = cur.fetchone()
            room_owner = room['created_by'] if isinstance(room, dict) else (room[0] if room else None)
            
            # 👑 권한 체크 (최고관리자, 오픈챗 방장, 본인)
            if user == 'admin' or user == room_owner or user == msg_sender:
                cur.execute("DELETE FROM open_messages WHERE id = %s", (message_id,))
                conn.commit()
                return jsonify({"success": True, "message": "오픈채팅 메시지가 삭제되었습니다."})
                
        # 2. 일반채팅(DM) 메시지 삭제 로직
        elif chat_type == 'general':
            # 일반채팅 테이블명과 컬럼명(sender_id 등)은 본인의 DB 구조에 맞게 수정될 수 있습니다.
            cur.execute("SELECT sender_id FROM messages WHERE id = %s", (message_id,))
            msg = cur.fetchone()
            if not msg:
                return jsonify({"success": False, "message": "존재하지 않는 메시지입니다."}), 404
                
            msg_sender = msg['sender_id'] if isinstance(msg, dict) else msg[0]
            
            # 👑 권한 체크 (최고관리자, 본인 - 일반 DM은 방장이 없으므로 본인과 최고관리자만 가능)
            if user == 'admin' or user == msg_sender:
                cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))
                conn.commit()
                return jsonify({"success": True, "message": "일반채팅 메시지가 삭제되었습니다."})
                
        return jsonify({"success": False, "message": "삭제 권한이 없거나 잘못된 요청입니다."}), 403
        
    except Exception as e:
        print(f"Delete Error: {e}")
        return jsonify({"success": False, "message": "서버 오류가 발생했습니다."}), 500
    finally:
        cur.close()
        conn.close()
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
