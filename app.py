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

@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 모든 단톡방 리스트 가져오기
    cur.execute("SELECT id, room_name FROM chat_rooms ORDER BY id DESC")
    my_rooms = cur.fetchall()
    if user:
        cur.execute("""
            SELECT u.username, u.nickname 
            FROM users u
            JOIN follows f ON u.username = f.following
            WHERE f.follower = %s AND u.is_active = TRUE
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

# 💬 [새로 추가] 1:1 개인톡(DM) 기능 라우팅
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
        
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cur.execute("INSERT INTO direct_messages (sender, receiver, message) VALUES (%s, %s, %s)", (my_id, username, message))
            conn.commit()
            return redirect(url_for('dm_chat', username=username))
            
    # 나와 상대방이 주고받은 대화 내역 전체 가져오기
    cur.execute("""
        SELECT sender, message, created_at FROM direct_messages 
        WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s)
        ORDER BY id ASC
    """, (my_id, username, username, my_id))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('dm.html', receiver=receiver, messages=messages)

# 👥 단톡방 생성 기능
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
  user = session.get('user')
    if not user: return redirect(url_for('login'))
        
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "방 이름을 입력해주세요.", 400
            
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id", (room_name, user))
            row = cur.fetchone()
            
            if isinstance(row, dict):
                room_id = row.get('id')
            else:
                room_id = row[0]
                
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('group_chat', room_id=room_id))
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return f"단톡방 생성 중 오류가 발생했습니다: {str(e)}", 500
            
    return render_template('create_group.html')

# 🏛️ 단톡방 채팅 내부 기능
@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def group_chat(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
        
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
