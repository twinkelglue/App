import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg
from psycopg.rows import dict_row
from datetime import datetime

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
            is_active BOOLEAN DEFAULT TRUE
        );
    """)
    
    # 2. 팔로우 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            column_id SERIAL PRIMARY KEY, -- 임시 기본키 방지용
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
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE
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

    # 6. [추가] 1:1 개인톡(DM) 메시지 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS direct_messages (
            id SERIAL PRIMARY KEY,
            sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            receiver VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()

# 앱 시작 시 DB 초기화 (새로운 DM 테이블이 자동으로 만들어집니다)
init_db()
# ⏱️ 유저가 활동할 때마다 마지막 접속 시간(last_seen) 갱신
@app.before_request
def update_last_seen():
    user = session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = %s", (user,))
            conn.commit()
            cur.close()
            conn.close()
        except:
            pass
# ✨ 새로 교체할 메인 홈 코드
@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if user:
        # 🔐 모든 단톡방이 아니라, 내가 만든 방이거나 내가 초대받은 방만 가져오기
        query_rooms = """
            SELECT DISTINCT cr.id, cr.room_name 
            FROM chat_rooms cr
            LEFT JOIN room_members rm ON cr.id = rm.room_id
            WHERE cr.created_by = %s OR rm.user_id = %s
            ORDER BY cr.id DESC
        """
        cur.execute(query_rooms, (user, user))
        my_rooms = cur.fetchall()
        
        # 🟢 [교체완료] 내가 팔로우한 유저 리스트 + 온라인 상태 여부 가져오기
        cur.execute("""
            SELECT u.username, u.nickname,
                   CASE WHEN u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes' THEN TRUE ELSE FALSE END as is_online
            FROM users u
            JOIN follows f ON u.username = f.following
            WHERE f.follower = %s AND u.is_active = TRUE
            ORDER BY is_online DESC, u.nickname ASC
        """, (user,))
        all_users = cur.fetchall()
    else:
        # 로그인하지 않은 사람에게는 단톡방을 숨김
        my_rooms = []
        
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

## 💬 1:1 개인톡(DM) 기능 라우팅 (수신 확인 & 1 표시 연동 버전)
@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
def dm_chat(username):
    my_id = session.get('user')
    if not my_id: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    # 대화 상대방 정보 가져오기
    cur.execute("SELECT username, nickname FROM users WHERE username = %s AND is_active = TRUE", (username,))
    receiver = cur.fetchone()
    if not receiver:
        cur.close()
        conn.close()
        return "존재하지 않거나 탈퇴한 회원입니다.", 404
        
    # 📑 [추가] 내가 이 방에 들어왔으므로, 상대방이 나에게 보낸 메시지를 전부 읽음(TRUE) 처리
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
            
    # [수정] 나와 상대방이 주고받은 대화 내역 + 읽음 여부(is_read) 가져오기
    cur.execute("""
        SELECT sender, message, created_at, is_read FROM direct_messages 
        WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s)
        ORDER BY id ASC
    """, (my_id, username, username, my_id))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('dm.html', receiver=receiver, messages=messages)

# 👥 내가 팔로우한 사람만 초대해서 단톡방 생성하는 기능
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        invited_users = request.form.getlist('invited_users') # 체크박스로 선택된 팔로우 목록
        
        if not room_name:
            return "방 이름을 입력해주세요.", 400
            
        try:
            # 1. 단톡방 기본 정보 삽입 (현재 users 테이블 구조상 created_by는 username을 참조함)
            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id", (room_name, user))
            row = cur.fetchone()
            
            if isinstance(row, dict):
                room_id = row.get('id')
            else:
                room_id = row[0]
                
            # 2. 방 만든 사람(나)을 방 멤버로 추가
            cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s)", (room_id, user))
            
            # 3. 체크된 팔로우들을 방 멤버로 추가
            for invited_user in invited_users:
                if invited_user != user: # 나 중복 추가 방지
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
            
    # GET 요청 시: '내가 팔로우한 유저 목록'만 불러와서 보여줌
    try:
        # 현재 DB 구조(follows 테이블의 follower/following 및 users 테이블의 username)에 맞춘 쿼리
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

# ✨ 새로 교체할 단톡방 내부 코드
@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def group_chat(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 🕵️‍♂️ [보안 추가] 이 유저가 방장이거나, 초대된 멤버인지 확인
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

# 기존 에스크/검색/프로필 라우팅 유지
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

@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):
    user = session.get('user')
    if user:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM ask_messages WHERE id = %s AND target_user = %s", (msg_id, user))
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
# 🚪 단톡방 나가기 기능
@app.route('/group/leave/<int:room_id>', methods=['POST'])
def leave_group(room_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 내가 진짜 이 방 멤버인지 먼저 확인
        cur.execute("SELECT 1 FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        is_member = cur.fetchone()
        
        # 방장이 만든 사람인 경우도 고려하여 chat_rooms에서도 확인
        cur.execute("SELECT created_by, room_name FROM chat_rooms WHERE id = %s", (room_id,))
        room_info = cur.fetchone()
        
        if not is_member and (room_info and room_info['created_by'] != user):
            cur.close()
            conn.close()
            return "이 방의 멤버가 아닙니다.", 400
            
        # 2. 퇴장 안내 메시지 먼저 남기기 (센스!)
        # 데이터베이스 u.nickname을 가져오기 위해 유저 정보 조회
        cur.execute("SELECT nickname FROM users WHERE username = %s", (user,))
        my_info = cur.fetchone()
        nickname = my_info['nickname'] if my_info else user
        
        system_msg = f"📢 {nickname}(@{user})님이 퇴장하셨습니다."
        cur.execute("INSERT INTO room_messages (room_id, sender, message) VALUES (%s, %s, %s)", (room_id, user, system_msg))
        
        # 3. room_members 테이블에서 나를 삭제
        cur.execute("DELETE FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        
        # 4. (선택 조건) 만약 방장이 나간 거라면 다음 사람에게 방장을 넘기거나 방을 유지하도록, 
        # 여기서는 created_by를 null이나 시스템 계정으로 바꾸거나 그대로 둡니다. (멤버에서만 빠지므로 방은 유지됨)
        if room_info and room_info['created_by'] == user:
            # 방장 권한을 빈값 처리해서 목록에 안 뜨게 하거나 유지
            cur.execute("UPDATE chat_rooms SET created_by = NULL WHERE id = %s", (room_id,))

        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('index')) # 나가면 메인 화면으로 이동!
        
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return f"방을 나가는 중 오류가 발생했습니다: {str(e)}", 500
        # 🌿 [오픈채팅] 1. 방 목록 보기 화면
@app.route('/open_chat_list')
def open_chat_list():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    # 개설된 모든 오픈채팅방을 최신순으로 가져옴
    cur.execute("SELECT id, title, created_by, created_at FROM open_rooms ORDER BY created_at DESC")
    rooms = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template('open_room_list.html', rooms=rooms)

# 🌿 [오픈채팅] 2. 새로운 방 만들기 처리
@app.route('/create_open_room', methods=['POST'])
def create_open_room():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    room_title = request.form.get('room_title', '').strip()
    if not room_title:
        return "방 제목을 입력해주세요.", 400
        
    conn = get_db_connection()
    cur = conn.cursor()
    # DB에 새 방 저장
    cur.execute("INSERT INTO open_rooms (title, created_by) VALUES (%s, %s) RETURNING id", (room_title, user))
    new_room_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()
    
    # 방을 만들자마자 해당 방으로 바로 입장시킵니다.
    return redirect(url_for('open_chat_room', room_id=new_room_id))

# 🌿 [오픈채팅] 3. 특정 방 입장 & 닉네임 설정 화면
@app.route('/open_chat/room/<int:room_id>', methods=['GET', 'POST'])
def open_chat_room(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 방이 존재하는지 확인
    cur.execute("SELECT id, title FROM open_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    if not room:
        cur.close()
        conn.close()
        return "존재하지 않는 방입니다.", 404
        
    # 유저가 닉네임을 설정해서 POST로 보낸 경우 세션에 저장
    if request.method == 'POST':
        custom_name = request.form.get('custom_name', '').strip()
        if custom_name:
            # 방마다 고유한 닉네임을 가질 수 있도록 세션 키를 방 번호별로 분리합니다.
            session[f'anon_name_{room_id}'] = custom_name
            cur.close()
            conn.close()
            return redirect(url_for('open_chat_room', room_id=room_id))
            
    # 해당 방에서 사용할 닉네임이 세션에 있는지 확인
    anon_name = session.get(f'anon_name_{room_id}')
    
    # 해당 방의 최근 메시지 50개 가져오기
    cur.execute("""
        SELECT id, sender_anon, message, created_at 
        FROM open_messages 
        WHERE room_id = %s 
        ORDER BY created_at ASC LIMIT 50
    """, (room_id,))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('open_chat.html', room=room, messages=messages, anon_name=anon_name)

# 🌿 [오픈채팅] 4. 오픈채팅 메시지 전송 처리
@app.route('/send_open_message/<int:room_id>', methods=['POST'])
def send_open_message(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    message = request.form.get('message', '').strip()
    anon_name = session.get(f'anon_name_{room_id}', '익명의 유저')
    
    if message:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO open_messages (room_id, sender_anon, message) 
            VALUES (%s, %s, %s)
        """, (room_id, anon_name, message))
        conn.commit()
        cur.close()
        conn.close()
        
    return redirect(url_for('open_chat_room', room_id=room_id))
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
