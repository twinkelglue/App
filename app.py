from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, jsonify
import sqlite3
import os
import re
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'asked_platform_hip_secret_key'

# [데이터 유실 방지] Render 영구 디스크 경로(/data) 매핑
if os.path.exists('/data'):
    db_path = "/data/asked.db"
    upload_dir = "/data/static"
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(base_dir, "asked.db")
    upload_dir = os.path.join(base_dir, "static")

if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 유튜브 링크를 iframe 임베드 태그로 변환해주는 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return text
    # 일반 주소 형식 매칭 (youtube.com/watch?v=...)
    text = re.sub(
        r'(https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)[^\s]*)',
        r'<br><iframe width="100%" height="220" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>',
        text
    )
    # 단축 주소 형식 매칭 (youtu.be/...)
    text = re.sub(
        r'(https?://youtu\.be/([a-zA-Z0-9_-]+)[^\s]*)',
        r'<br><iframe width="100%" height="220" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>',
        text
    )
    return text

def init_db():
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    # 테이블 구조 보존
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, bio TEXT, profile_img TEXT DEFAULT 'default_profile.png'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_username TEXT, sender_username TEXT, content TEXT, answer TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            follower TEXT, following TEXT, PRIMARY KEY (follower, following)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room_name TEXT, creator TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_members (
            room_id INTEGER, username TEXT, PRIMARY KEY (room_id, username)
        )
    ''')
    # 이미지/파일 공유를 위해 file_path 컬럼이 없다면 자동 생성 로직 추가
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room_type TEXT, room_identifier TEXT, sender TEXT, message TEXT, file_path TEXT DEFAULT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    current_user = session.get('user')
    my_rooms = []
    my_dms = []
    
    if current_user:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 단톡방
        cursor.execute('''
            SELECT DISTINCT r.id, r.room_name FROM chat_rooms r
            JOIN room_members m ON r.id = m.room_id
            WHERE m.username = ?
        ''', (current_user,))
        my_rooms = cursor.fetchall()
        
        # 갠톡방 추적
        cursor.execute('''
            SELECT DISTINCT room_identifier FROM chat_messages 
            WHERE room_type = 'dm' AND room_identifier LIKE ?
        ''', (f"%{current_user}%",))
        dm_rooms = cursor.fetchall()
        
        for dm in dm_rooms:
            parts = dm[0].replace("dm_", "").split("_and_")
            if len(parts) == 2 and current_user in parts:
                other_user = parts[1] if parts[0] == current_user else parts[0]
                cursor.execute("SELECT 1 FROM users WHERE username = ?", (other_user,))
                if cursor.fetchone() and other_user not in my_dms:
                    my_dms.append(other_user)
                    
        conn.close()
        
    return render_template('index.html', my_rooms=my_rooms, my_dms=my_dms)

@app.route('/api/check_notifications')
def check_notifications():
    current_user = session.get('user')
    if not current_user:
        return jsonify({"status": "unauthorized"}), 401
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) FROM chat_messages 
        WHERE room_type = 'dm' AND room_identifier LIKE ? AND sender != ?
        AND timestamp >= datetime('now', '-4 seconds')
    ''', (f"%{current_user}%", current_user))
    new_dms = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM chat_messages cm
        JOIN room_members rm ON cm.room_identifier = CAST(rm.room_id AS TEXT)
        WHERE cm.room_type = 'group' AND rm.username = ? AND cm.sender != ?
        AND cm.timestamp >= datetime('now', '-4 seconds')
    ''', (current_user, current_user))
    new_groups = cursor.fetchone()[0]
    
    conn.close()
    return jsonify({"new_dms": new_dms, "new_groups": new_groups})

@app.route('/api/chat_history/<room_type>/<identifier>')
def api_chat_history(room_type, identifier):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sender, message, file_path, timestamp FROM chat_messages 
        WHERE room_type = ? AND room_identifier = ? 
        ORDER BY timestamp ASC
    ''', (room_type, identifier))
    history = cursor.fetchall()
    conn.close()
    
    chat_list = []
    for h in history:
        # 대화 텍스트 내 유튜브 링크 실시간 렌더링용 변환
        converted_msg = convert_youtube_links(h[1])
        chat_list.append({
            "sender": h[0],
            "message": converted_msg,
            "file_path": h[2] if h[2] else "",
            "time": h[3]
        })
    return jsonify(chat_list)

@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
def chat_dm(username):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    
    users_sorted = sorted([current_user, username])
    room_id = f"dm_{users_sorted[0]}_and_{users_sorted[1]}"
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        file = request.files.get('chat_img')
        saved_filename = None
        
        # 이미지 파일 업로드 처리
        if file and allowed_file(file.filename):
            saved_filename = secure_filename(f"chat_{current_user}_{file.filename}")
            file.save(os.path.join(upload_dir, saved_filename))
            
        if message or saved_filename:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message, file_path) VALUES ('dm', ?, ?, ?, ?)", (room_id, current_user, message, saved_filename))
            conn.commit()
        return redirect(url_for('chat_dm', username=username))
        
    cursor.execute("SELECT sender, message, file_path, timestamp FROM chat_messages WHERE room_identifier = ? ORDER BY timestamp ASC", (room_id,))
    raw_history = cursor.fetchall()
    conn.close()
    
    chat_history = []
    for row in raw_history:
        chat_history.append((row[0], convert_youtube_links(row[1]), row[2], row[3]))
    
    return render_template('chat.html', target_name=f"⚡ {username}님과의 갠톡", chat_history=chat_history, is_group=False, room_id=room_id, room_type="dm")

@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def chat_group(room_id):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM room_members WHERE room_id = ? AND username = ?", (room_id, current_user))
    if not cursor.fetchone():
        conn.close()
        return "접근 권한 없음", 403
        
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        file = request.files.get('chat_img')
        saved_filename = None
        
        if file and allowed_file(file.filename):
            saved_filename = secure_filename(f"chat_{current_user}_{file.filename}")
            file.save(os.path.join(upload_dir, saved_filename))
            
        if message or saved_filename:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message, file_path) VALUES ('group', ?, ?, ?, ?)", (str(room_id), current_user, message, saved_filename))
            conn.commit()
        return redirect(url_for('chat_group', room_id=room_id))
        
    cursor.execute("SELECT room_name FROM chat_rooms WHERE id = ?", (room_id,))
    room_title = cursor.fetchone()[0]
    cursor.execute("SELECT username FROM room_members WHERE room_id = ?", (room_id,))
    members_list = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT sender, message, file_path, timestamp FROM chat_messages WHERE room_identifier = ? ORDER BY timestamp ASC", (str(room_id),))
    raw_history = cursor.fetchall()
    conn.close()
    
    chat_history = []
    for row in raw_history:
        chat_history.append((row[0], convert_youtube_links(row[1]), row[2], row[3]))
        
    return render_template('chat.html', target_name=f"👥 {room_title}", chat_history=chat_history, is_group=True, members_list=members_list, room_id=str(room_id), room_type="group")

# 🔍 유저 검색 (기능 보존)
@app.route('/search', methods=['GET'])
def search_user():
    query = request.args.get('query', '').strip()
    if not query: return redirect(url_for('index'))
    current_user = session.get('user')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username LIKE ?", (f"%{query}%",))
    users = cursor.fetchall()
    results = []
    for u in users:
        is_following = False
        if current_user:
            cursor.execute("SELECT 1 FROM follows WHERE follower = ? AND following = ?", (current_user, u[0]))
            if cursor.fetchone(): is_following = True
        results.append((u[0], u[1], u[2], is_following))
    conn.close()
    return render_template('search_results.html', query=query, results=results)

# 회원가입 (기능 보존)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        if not username or not password: return "정보 누락", 400
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            conn.close()
            return "중복 아이디", 400
        cursor.execute("INSERT INTO users (username, password, bio) VALUES (?, ?, ?)", (username, password, "힙한 에스크!"))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

# 로그인 (기능 보존)
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
        return "정보 불일치", 400
    return render_template('login.html')

# 📷 유저 프로필 홈 및 에스크 질문 내역 (기능 완전 보존)
@app.route('/user/<username>')
def user_profile(username):
    current_user = session.get('user')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username = ?", (username,))
    profile_user = cursor.fetchone()
    if not profile_user:
        conn.close()
        return "유저 없음", 404
    cursor.execute("SELECT COUNT(*) FROM follows WHERE following = ?", (username,))
    followers_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM follows WHERE follower = ?", (username,))
    following_count = cursor.fetchone()[0]
    is_following = False
    if current_user:
        cursor.execute("SELECT 1 FROM follows WHERE follower = ? AND following = ?", (current_user, username))
        if cursor.fetchone(): is_following = True
    cursor.execute("SELECT id, sender_username, content, answer, timestamp FROM messages WHERE target_username = ? ORDER BY id DESC", (username,))
    messages = cursor.fetchall()
    conn.close()
    return render_template('user.html', profile_user=profile_user, messages=messages, followers_count=followers_count, following_count=following_count, is_following=is_following)

# 👥 팔로우 (기능 보존)
@app.route('/follow/<username>', methods=['POST'])
def follow_user(username):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    if current_user == username: return "본인 불가", 400
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM follows WHERE follower = ? AND following = ?", (current_user, username))
    if cursor.fetchone():
        cursor.execute("DELETE FROM follows WHERE follower = ? AND following = ?", (current_user, username))
    else:
        cursor.execute("INSERT INTO follows (follower, following) VALUES (?, ?)", (current_user, username))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('user_profile', username=username))

# 단톡방 개설 (기능 보존)
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        invited_members = request.form.getlist('members')
        if not room_name: return "방이름 공백 불가", 400
        cursor.execute("INSERT INTO chat_rooms (room_name, creator) VALUES (?, ?)", (room_name, current_user))
        new_room_id = cursor.lastrowid
        cursor.execute("INSERT INTO room_members (room_id, username) VALUES (?, ?)", (new_room_id, current_user))
        for member in invited_members:
            cursor.execute("INSERT INTO room_members (room_id, username) VALUES (?, ?)", (new_room_id, member))
        conn.commit()
        conn.close()
        return redirect(url_for('chat_group', room_id=new_room_id))
    cursor.execute("SELECT following FROM follows WHERE follower = ?", (current_user,))
    my_friends = [row[0] for row in cursor.fetchall()]
    conn.close()
    return render_template('create_group.html', my_friends=my_friends)

# ❓ 에스크 질문 던지기 (기능 보존)
@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content', '').strip()
    sender = session.get('user', '익명')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (target_username, sender_username, content) VALUES (?, ?, ?)", (username, sender, content))
    conn.commit()
    conn.close()
    return redirect(url_for('user_profile', username=username))

# ✍️ 에스크 답변 달기 (기능 보존)
@app.route('/answer/<int:msg_id>', methods=['POST'])
def answer(msg_id):
    if 'user' not in session: return redirect(url_for('login'))
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

# 에스크 삭제 (기능 보존)
@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):
    if 'user' not in session: return redirect(url_for('login'))
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

# 📷 프사 및 Bio 수정 기능 (기능 완전 보존)
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user' not in session: return redirect(url_for('login'))
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

@app.route('/static/<filename>')
def uploaded_file(filename):
    return send_from_directory(upload_dir, filename)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
