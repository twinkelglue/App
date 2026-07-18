import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chatclub_secret_key_1234")

# Neon DB 연동 설정 (환경 변수 사용 권장)
DATABASE_URL = os.environ.get("DATABASE_URL", "your_neon_db_connection_string_here")

def get_db_connection():
    # DictCursor를 사용하여 HTML 템플릿에서 컬럼명(속성)으로 접근 가능하게 합니다.
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

# 데이터베이스 초기화 및 영구 테이블 구성
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 유저 테이블 (탈퇴 처리를 위해 is_active 컬럼 도입)
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
            follower VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            following VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            PRIMARY KEY (follower, following)
        );
    """)
    
    # 3. 익명 에스크 메시지 테이블 (읽음 표시 기능 추가)
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
    
    # 5. 단톡방 메시지 테이블 (읽음 표시 및 추적용 카운트 간소화)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS room_messages (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    if user:
        conn = get_db_connection()
        cur = conn.cursor()
        # 사용자가 만든 방 목록 가져오기
        cur.execute("SELECT id, room_name FROM chat_rooms WHERE created_by = %s ORDER BY id DESC", (user,))
        my_rooms = cur.fetchall()
        cur.close()
        conn.close()
    return render_template('index.html', my_rooms=my_rooms)

@app.route('/register', models=['GET', 'POST'])
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
        
        # 탈퇴 이력이 있는 유저가 재가입하려는 경우 체크
        cur.execute("SELECT is_active FROM users WHERE username = %s", (username,))
        existing = cur.fetchone()
        
        if existing:
            if existing['is_active'] == False:
                # 7번 조건: 재가입 시 계정 상태 활성화 및 데이터 리셋
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
        # 활성화(is_active=True)된 회원만 로그인 가능
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

# 7번 조건: 회원 탈퇴 라우터 구현
@app.route('/delete_account', methods=['POST'])
def delete_account():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    # 소프트 딜리트를 처리하여 향후 동일 ID 재가입이 매끄럽게 처리되도록 비활성화
    cur.execute("UPDATE users SET is_active = FALSE WHERE username = %s", (user,))
    # 연관된 대화방 기록 정리 필요 시 처리
    cur.execute("DELETE FROM follows WHERE follower = %s OR following = %s", (user, user))
    conn.commit()
    cur.close()
    conn.close()
    
    session.pop('user', None)
    return redirect(url_for('index'))

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
        
    # 에스크 질문 읽음 처리 (내 프로필을 내가 직접 조회할 때)
    if session.get('user') == username:
        cur.execute("UPDATE ask_messages SET is_read = TRUE WHERE target_user = %s", (username,))
        conn.commit()

    # 팔로워 카운트
    cur.execute("SELECT COUNT(*) FROM follows WHERE following = %s", (username,))
    followers_count = cur.fetchone()[0]
    
    # 나를 팔로우 중인지 여부
    is_following = False
    if session.get('user'):
        cur.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (session['user'], username))
        is_following = cur.fetchone() is not None
        
    # 익명 메일 피드 리스트 가져오기
    cur.execute("SELECT * FROM ask_messages WHERE target_user = %s ORDER BY id DESC", (username,))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('user.html', profile_user=profile_user, followers_count=followers_count, is_following=is_following, messages=messages)

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

@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    if request.method == 'POST':
        room_name = request.form.get('room_name').strip()
        if room_name:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id", (room_name, user))
            room_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('group_chat', room_id=room_id))
    return render_template('create_group.html')

# 5, 6번 조건: 읽음 유무를 포함하는 매끄러운 그룹 단톡방 구현
@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def group_chat(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        message = request.form.get('message').strip()
        if message:
            cur.execute("INSERT INTO room_messages (room_id, sender, message) VALUES (%s, %s, %s)", (room_id, user, message))
            conn.commit()
            
    cur.execute("SELECT room_name FROM chat_rooms WHERE id = %s", (room_id,))
    room = cur.fetchone()
    
    cur.execute("SELECT sender, message, created_at FROM room_messages WHERE room_id = %s ORDER BY id ASC", (room_id,))
    messages = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template('chat.html', room=room, room_id=room_id, messages=messages)

if __name__ == '__main__':
    app.run(debug=True)
