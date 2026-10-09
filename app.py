import os
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

os.environ['TZ'] = 'Asia/Seoul'
try:
    time.tzset()
except AttributeError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chatclub_secret_key_1234")

DATABASE_URL = os.environ.get("DATABASE_URL", "your_neon_db_connection_string_here")

def get_db_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
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
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            column_id SERIAL PRIMARY KEY,
            follower VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            following VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE (follower, following)
        );
    """)
    
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
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id SERIAL PRIMARY KEY,
            room_name VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL
        );
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS room_members (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE (room_id, user_id)
        );
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS room_messages (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
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
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_rooms (
            id SERIAL PRIMARY KEY,
            title VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
            sub_host VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
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
        except Exception:
            pass

@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    dm_list = []
    unread_total = 0
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if user:
        # 1. 일반 단톡방 목록
        try:
            query_rooms = """
                SELECT DISTINCT cr.id, cr.room_name 
                FROM chat_rooms cr
                LEFT JOIN room_members rm ON cr.id = rm.room_id
                WHERE cr.created_by = %s OR rm.user_id = %s
                ORDER BY cr.id DESC
            """
            cur.execute(query_rooms, (user, user))
            my_rooms = cur.fetchall()
        except Exception as e:
            conn.rollback()

        # 2. 내 팔로잉 목록
        try:
            cur.execute("""
                SELECT u.username, u.nickname,
                       CASE WHEN u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes' THEN TRUE ELSE FALSE END as is_online
                FROM users u
                JOIN follows f ON u.username = f.following
                WHERE f.follower = %s AND u.is_active = TRUE
                ORDER BY is_online DESC, u.nickname ASC
            """, (user,))
            all_users = cur.fetchall()
        except Exception as e:
            conn.rollback()

        # 3. 1:1 대화 및 미팔로우 선톡 목록 (핵심 쿼리)
        try:
            cur.execute("""
                WITH partners AS (
                    SELECT sender AS partner_id FROM direct_messages WHERE receiver = %s
                    UNION
                    SELECT receiver AS partner_id FROM direct_messages WHERE sender = %s
                )
                SELECT 
                    u.username,
                    u.nickname,
                    CASE WHEN f.column_id IS NOT NULL THEN TRUE ELSE FALSE END AS is_following,
                    COALESCE(
                        (SELECT COUNT(*) FROM direct_messages 
                         WHERE sender = u.username AND receiver = %s AND is_read = FALSE), 0
                    ) AS unread_count,
                    (SELECT MAX(created_at) FROM direct_messages 
                     WHERE (sender = %s AND receiver = u.username) OR (sender = u.username AND receiver = %s)
                    ) AS last_msg_time
                FROM partners p
                JOIN users u ON p.partner_id = u.username AND u.is_active = TRUE
                LEFT JOIN follows f ON f.follower = %s AND f.following = u.username
                ORDER BY unread_count DESC, last_msg_time DESC;
            """, (user, user, user, user, user, user))
            dm_list = cur.fetchall()

            for item in dm_list:
                count = item.get('unread_count', 0) if isinstance(item, dict) else item[3]
                unread_total += int(count or 0)
        except Exception as e:
            conn.rollback()
            print(f"DM Query Error: {e}")
        
    cur.close()
    conn.close()
    
    return render_template('index.html', 
                           my_rooms=my_rooms, 
                           all_users=all_users, 
                           dm_list=dm_list, 
                           unread_total=unread_total, 
                           user=user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        nickname = request.form.get('nickname', '').strip()
        
        if not username or not password or not nickname:
            return "모든 필드를 입력해주세요.", 400
            
        hashed_password = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 아이디 존재 여부 확인 (탈퇴 여부 포함)
        cur.execute("SELECT username, is_active FROM users WHERE username = %s", (username,))
        existing = cur.fetchone()
        
        if existing:
            is_active = existing.get('is_active') if isinstance(existing, dict) else existing[1]
            if not is_active:
                # 비활성화(탈퇴)된 계정인 경우 계정 재활성화 및 정보 갱신
                cur.execute("""
                    UPDATE users 
                    SET password = %s, nickname = %s, bio = '안녕하세요! ChatClub입니다.', profile_img = 'default.png', is_active = TRUE 
                    WHERE username = %s
                """, (hashed_password, nickname, username))
                conn.commit()
                cur.close()
                conn.close()
                session['user'] = username
                session['role'] = 'USER'
                return redirect(url_for('index'))
            else:
                cur.close()
                conn.close()
                return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>", 400
        
        # 신규 회원가입
        cur.execute("INSERT INTO users (username, password, nickname) VALUES (%s, %s, %s)", (username, hashed_password, nickname))
        conn.commit()
        cur.close()
        conn.close()
        session['user'] = username
        session['role'] = 'USER'
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        # 관리자 계정 직접 로그인 처리
        if username == 'admin' and password == 'admin1234':
            session['user'] = 'admin'
            session['role'] = 'ADMIN'
            return "<script>alert('👑 최고 관리자 모드로 로그인되었습니다.'); location.href='/admin/dashboard';</script>"
            
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s AND is_active = TRUE", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            db_password = user.get('password') if isinstance(user, dict) else user[1]
            db_username = user.get('username') if isinstance(user, dict) else user[0]
            
            # 1) 해시 비밀번호 검증시도 OR 2) 기존 평문 비밀번호 호환 검증
            if check_password_hash(db_password, password) or db_password == password:
                session['user'] = db_username
                session['role'] = 'USER'
                return redirect(url_for('index'))
                
        return "<script>alert('아이디 또는 비밀번호가 잘못되었거나 탈퇴한 회원입니다.'); history.back();</script>", 401
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
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
    
    session.clear()
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
        SELECT id, sender, message, created_at, is_read FROM direct_messages 
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
            room_id = row['id'] if isinstance(row, dict) else row[0]
                
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
    except Exception:
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
        
    cur.execute("SELECT id, sender, message, created_at FROM room_messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
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
    current_user = session.get('user')
    if not current_user:
        flash("로그인이 필요한 서비스입니다.")
        return redirect('/login')
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.room_name, r.created_by, 
               (SELECT COUNT(*) FROM room_members WHERE room_id = r.id) as member_count
        FROM chat_rooms r
        JOIN room_members m ON r.id = m.room_id
        WHERE m.user_id = %s
        ORDER BY r.id DESC
    """, (current_user,))
    my_rooms = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('my_chats.html', rooms=my_rooms, current_user=current_user)

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
        except Exception:
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
# 🌿 오픈채팅 기능 라우팅 (목록 분리 + 닉네임 자동 저장 적용)
# ----------------------------------------------------------------
@app.route('/open_chat_list')
def open_chat_list():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 전체 오픈채팅방 목록
    cur.execute("SELECT id, title, created_by, sub_host, created_at FROM open_rooms ORDER BY created_at DESC")
    all_rooms = cur.fetchall()
    
    # 2. 내가 들어간(메시지를 하나라도 남겼거나 개설한) 오픈채팅방 목록
    cur.execute("""
        SELECT DISTINCT r.id, r.title, r.created_by, r.sub_host, r.created_at
        FROM open_rooms r
        LEFT JOIN open_messages m ON r.id = m.room_id
        WHERE r.created_by = %s OR m.sender_real_id = %s
        ORDER BY r.created_at DESC
    """, (user, user))
    my_open_rooms = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('open_room_list.html', all_rooms=all_rooms, my_open_rooms=my_open_rooms)

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
    
    # 닉네임 설정 POST 요청 시 처리
    if request.method == 'POST':
        custom_name = request.form.get('custom_name', '').strip()
        if custom_name:
            session[f'anon_name_{room_id}'] = custom_name
            cur.close()
            conn.close()
            return redirect(url_for('open_chat_room', room_id=room_id))
            
    # 세션에서 익명 닉네임 확인
    anon_name = session.get(f'anon_name_{room_id}')
    
    # 세션에 없으면 이전 작성 메시지 기록에서 가져오기 (닉네임 재입력 방지)
    if not anon_name:
        cur.execute("SELECT sender_anon FROM open_messages WHERE room_id = %s AND sender_real_id = %s ORDER BY id DESC LIMIT 1", (room_id, user))
        prev_msg = cur.fetchone()
        if prev_msg:
            anon_name = prev_msg['sender_anon']
            session[f'anon_name_{room_id}'] = anon_name
            
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
@app.route('/admin/dashboard')
def admin_dashboard():
    user = session.get('user')
    role = session.get('role', 'USER')
    
    if user != 'admin' and role not in ['ADMIN', 'H_ADMIN']:
        return "관리자 권한이 없습니다.", 403
        
    all_users = []
    open_rooms = []
    group_rooms = []
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT username, nickname, is_active FROM users ORDER BY username ASC")
        all_users = cur.fetchall()
    except Exception as e:
        conn.rollback()
        print(f"User Error: {e}")
        
    try:
        cur.execute("SELECT id, room_name, created_by FROM chat_rooms ORDER BY id DESC")
        group_rooms = cur.fetchall()
    except Exception as e:
        conn.rollback()
        print(f"Group Room Error: {e}")

    try:
        cur.execute("SELECT id, title, created_by FROM open_rooms ORDER BY id DESC")
        open_rooms = cur.fetchall()
    except Exception as e:
        conn.rollback()
        print(f"Open Room Error: {e}")
        
    cur.close()
    conn.close()
    
    return render_template('admin_dashboard.html', 
                           all_users=all_users, 
                           open_rooms=open_rooms, 
                           group_rooms=group_rooms, 
                           user=user, 
                           role=role)

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

# ----------------------------------------------------------------
# 메시지 삭제 API (일반/단톡방/오픈채팅 공용)
# ----------------------------------------------------------------
@app.route('/delete_chat_message/<string:chat_type>/<int:message_id>', methods=['POST'])
def delete_message(chat_type, message_id):
    user = session.get('user')
    
    if not user:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 오픈채팅 메시지 삭제
        if chat_type == 'open':
            cur.execute("SELECT sender_real_id, room_id FROM open_messages WHERE id = %s", (message_id,))
            msg = cur.fetchone()
            if not msg:
                return jsonify({"success": False, "message": "존재하지 않는 메시지입니다."}), 404
                
            msg_sender = msg['sender_real_id']
            room_id = msg['room_id']
            
            cur.execute("SELECT created_by FROM open_rooms WHERE id = %s", (room_id,))
            room = cur.fetchone()
            room_owner = room['created_by'] if room else None
            
            if user == 'admin' or user == room_owner or user == msg_sender:
                cur.execute("DELETE FROM open_messages WHERE id = %s", (message_id,))
                conn.commit()
                return jsonify({"success": True, "message": "메시지가 삭제되었습니다."})
                
        # 2. 일반 DM 메시지 삭제
        elif chat_type == 'general':
            cur.execute("SELECT sender FROM direct_messages WHERE id = %s", (message_id,))
            msg = cur.fetchone()
            if not msg:
                return jsonify({"success": False, "message": "존재하지 않는 메시지입니다."}), 404
                
            msg_sender = msg['sender']
            
            if user == 'admin' or user == msg_sender:
                cur.execute("DELETE FROM direct_messages WHERE id = %s", (message_id,))
                conn.commit()
                return jsonify({"success": True, "message": "메시지가 삭제되었습니다."})

        # 3. 단톡방 메시지 삭제
        elif chat_type == 'group':
            cur.execute("SELECT sender, room_id FROM room_messages WHERE id = %s", (message_id,))
            msg = cur.fetchone()
            if not msg:
                return jsonify({"success": False, "message": "존재하지 않는 메시지입니다."}), 404
                
            msg_sender = msg['sender']
            room_id = msg['room_id']
            
            cur.execute("SELECT created_by FROM chat_rooms WHERE id = %s", (room_id,))
            room = cur.fetchone()
            room_owner = room['created_by'] if room else None
            
            if user == 'admin' or user == room_owner or user == msg_sender:
                cur.execute("DELETE FROM room_messages WHERE id = %s", (message_id,))
                conn.commit()
                return jsonify({"success": True, "message": "메시지가 삭제되었습니다."})
                
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
