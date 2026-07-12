from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, flash
import sqlite3
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'asked_platform_secret_key'

# Render 리눅스 서버 환경용 안전한 절대 경로
base_dir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(base_dir, "asked.db")
upload_dir = os.path.join(base_dir, "static")

if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 데이터베이스 초기화 및 테이블 생성
def init_db():
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    
    # 1. 회원 테이블 (아이디, 비밀번호, 상메, 프사)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT,
            bio TEXT,
            profile_img TEXT DEFAULT 'default_profile.png'
        )
    ''')
    
    # 2. 에스크 질문/답변 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_username TEXT,
            sender_username TEXT,
            content TEXT,
            answer TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 3. 1:1 비밀 디엠 & 그룹 채팅 통합 테이블
    # room_name이 'global_group'이면 전체 그룹 채팅, 유저 아이디 조합이면 1:1 디엠이 됩니다.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT,
            sender TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# [기능 1] 메인 화면
@app.route('/')
def index():
    return render_template('index.html')

# [기능 2] 사용자 아이디 검색
@app.route('/search', methods=['GET'])
def search_user():
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('index'))
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username LIKE ?", (f"%{query}%",))
    results = cursor.fetchall()
    conn.close()
    return render_template('search_results.html', query=query, results=results)

# [기능 3] 회원가입 (로직 100% 탑재)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        
        if not username or not password:
            return "아이디와 비밀번호를 모두 입력해주세요.", 400
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            conn.close()
            return "이미 사용 중인 아이디입니다.", 400
            
        cursor.execute("INSERT INTO users (username, password, bio) VALUES (?, ?, ?)", 
                       (username, password, "안녕하세요! 제 에스크에 오신 것을 환영합니다."))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
        
    return render_template('register.html')

# [기능 4] 로그인
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and user[0] == password:
            session['user'] = username
            return redirect(url_for('user_profile', username=username))
        else:
            return "아이디 또는 비밀번호가 틀렸습니다.", 400
            
    return render_template('login.html')

# [기능 5] 에스크 프로필 홈 & 피드
@app.route('/user/<username>')
def user_profile(username):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username = ?", (username,))
    profile_user = cursor.fetchone()
    
    if not profile_user:
        conn.close()
        return "존재하지 않는 유저의 페이지입니다.", 404
        
    cursor.execute("SELECT id, sender_username, content, answer, timestamp FROM messages WHERE target_username = ? ORDER BY id DESC", (username,))
    messages = cursor.fetchall()
    conn.close()
    return render_template('user.html', profile_user=profile_user, messages=messages)

# [기능 6] 에스크 질문 전송
@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content', '').strip()
    if not content:
        return "질문 내용을 입력해주세요.", 400
        
    sender = session.get('user', '익명')
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (target_username, sender_username, content) VALUES (?, ?, ?)", 
                   (username, sender, content))
    conn.commit()
    conn.close()
    return redirect(url_for('user_profile', username=username))

# [기능 7] 에스크 답변 달기
@app.route('/answer/<int:msg_id>', methods=['POST'])
def answer(msg_id):
    if 'user' not in session:
        return redirect(url_for('login'))
        
    answer_text = request.form.get('answer', '').strip()
    current_user = session['user']
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT target_username FROM messages WHERE id = ?", (msg_id,))
    msg = cursor.fetchone()
    
    if msg and msg[0] == current_user:
        cursor.execute("UPDATE messages SET answer = ? WHERE id = ?", (answer_text, msg_id))
        conn.commit()
        
    conn.close()
    return redirect(url_for('user_profile', username=current_user))

# [기능 8] 1:1 비밀 디엠방 (서로 주고받은 대화만 필터링)
@app.route('/chat/<username>', methods=['GET', 'POST'])
def chat(username):
    if 'user' not in session:
        return redirect(url_for('login'))
        
    current_user = session['user']
    # 알파벳 순으로 방 이름을 묶어서 A-B방과 B-A방이 같은 방을 보게 설계
    room_name = f"dm_{min(current_user, username)}_{max(current_user, username)}"
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_name, sender, message) VALUES (?, ?, ?)",
                           (room_name, current_user, message))
            conn.commit()
        return redirect(url_for('chat', username=username))
    
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_name = ? ORDER BY timestamp ASC", (room_name,))
    chat_history = cursor.fetchall()
    conn.close()
    return render_template('chat.html', target_user=username, chat_history=chat_history, chat_type="dm")

# [기능 9] 다 함께 떠드는 전체 그룹 채팅방
@app.route('/group-chat', methods=['GET', 'POST'])
def group_chat():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    current_user = session['user']
    room_name = "global_group"
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_name, sender, message) VALUES (?, ?, ?)",
                           (room_name, current_user, message))
            conn.commit()
        return redirect(url_for('group_chat'))
    
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_name = ? ORDER BY timestamp ASC", (room_name,))
    chat_history = cursor.fetchall()
    conn.close()
    return render_template('chat.html', target_user="전체 그룹 단톡방", chat_history=chat_history, chat_type="group")

# [기능 10] 질문 삭제
@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):
    if 'user' not in session:
        return redirect(url_for('login'))
        
    current_user = session['user']
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT target_username FROM messages WHERE id = ?", (msg_id,))
    msg = cursor.fetchone()
    
    if msg and msg[0] == current_user:
        cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
        
    conn.close()
    return redirect(url_for('user_profile', username=current_user))

# [기능 11] 프사 업로드 및 상태메시지 변경
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    current_user = session['user']
    bio = request.form.get('bio', '').strip()
    file = request.files.get('profile_img')
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{current_user}_{file.filename}")
        file.save(os.path.join(upload_dir, filename))
        cursor.execute("UPDATE users SET bio = ?, profile_img = ? WHERE username = ?", (bio, filename, current_user))
    else:
        cursor.execute("UPDATE users SET bio = ? WHERE username = ?", (bio, current_user))
        
    conn.commit()
    conn.close()
    return redirect(url_for('user_profile', username=current_user))

# [기능 12] 정적 파일 전송 경로
@app.route('/static/<filename>')
def uploaded_file(filename):
    return send_from_directory(upload_dir, filename)

# [기능 13] 로그아웃
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
