from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, flash
import sqlite3
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'asked_platform_hip_secret_key'

base_dir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(base_dir, "asked.db")
upload_dir = os.path.join(base_dir, "static")

if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    
    # 1. 회원 테이블
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
    
    # 3. 팔로우 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            follower TEXT,
            following TEXT,
            PRIMARY KEY (follower, following)
        )
    ''')
    
    # 4. 단톡방/그룹방 목록 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_name TEXT,
            creator TEXT
        )
    ''')
    
    # 5. 단톡방 멤버 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_members (
            room_id INTEGER,
            username TEXT,
            PRIMARY KEY (room_id, username)
        )
    ''')
    
    # 6. 1:1 디엠 & 단톡방 통합 메시지 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_type TEXT,
            room_identifier TEXT,
            sender TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
        
        # 내가 참여 중인 단톡방 목록 불러오기
        cursor.execute('''
            SELECT r.id, r.room_name FROM chat_rooms r
            JOIN room_members m ON r.id = m.room_id
            WHERE m.username = ?
        ''', (current_user,))
        my_rooms = cursor.fetchall()
        
        # [기능 고도화] 내가 대화한 적 있는 1:1 갠톡방 상대방 목록 추출하기
        # room_identifier가 'dm_유저1_유저2' 형태이므로 내가 포함된 방 검색
        cursor.execute('''
            SELECT DISTINCT room_identifier FROM chat_messages 
            WHERE room_type = 'dm' AND room_identifier LIKE ?
        ''', (f"%{current_user}%",))
        dm_rooms = cursor.fetchall()
        
        # 갠톡 상대방 이름만 깔끔하게 발라내기
        for dm in dm_rooms:
            parts = dm[0].replace("dm_", "").split("_")
            if current_user in parts:
                other_user = parts[1] if parts[0] == current_user else parts[0]
                # 상대방 존재 여부 더블체크
                cursor.execute("SELECT profile_img FROM users WHERE username = ?", (other_user,))
                user_info = cursor.fetchone()
                if user_info and other_user not in my_dms:
                    my_dms.append(other_user)
                    
        conn.close()
        
    return render_template('index.html', my_rooms=my_rooms, my_dms=my_dms)

@app.route('/search', methods=['GET'])
def search_user():
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('index'))
    
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
            if cursor.fetchone():
                is_following = True
        results.append((u[0], u[1], u[2], is_following))
        
    conn.close()
    return render_template('search_results.html', query=query, results=results)

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
                       (username, password, "안녕하세요! 힙한 제 에스크에 오신 걸 환영합니다."))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))
        
    return render_template('register.html')

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

@app.route('/user/<username>')
def user_profile(username):
    current_user = session.get('user')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username = ?", (username,))
    profile_user = cursor.fetchone()
    
    if not profile_user:
        conn.close()
        return "존재하지 않는 유저입니다.", 404
        
    # [기능 개선] 나를 팔로우한 사람 수 (나를 target으로 삼은 사람들)
    cursor.execute("SELECT COUNT(*) FROM follows WHERE following = ?", (username,))
    followers_count = cursor.fetchone()[0]
    
    # [기능 추가] 내가 팔로우한 사람 수 (내가 주체가 되어 저장한 사람들)
    cursor.execute("SELECT COUNT(*) FROM follows WHERE follower = ?", (username,))
    following_count = cursor.fetchone()[0]
    
    is_following = False
    if current_user:
        cursor.execute("SELECT 1 FROM follows WHERE follower = ? AND following = ?", (current_user, username))
        if cursor.fetchone():
            is_following = True
            
    cursor.execute("SELECT id, sender_username, content, answer, timestamp FROM messages WHERE target_username = ? ORDER BY id DESC", (username,))
    messages = cursor.fetchall()
    conn.close()
    return render_template('user.html', profile_user=profile_user, messages=messages, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)

@app.route('/follow/<username>', methods=['POST'])
def follow_user(username):
    if 'user' not in session:
        return redirect(url_for('login'))
    current_user = session['user']
    if current_user == username:
        return "자기 자신은 팔로우할 수 없습니다.", 400
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM follows WHERE follower = ? AND following = ?", (current_user, username))
    already = cursor.fetchone()
    
    if already:
        cursor.execute("DELETE FROM follows WHERE follower = ? AND following = ?", (current_user, username))
    else:
        cursor.execute("INSERT INTO follows (follower, following) VALUES (?, ?)", (current_user, username))
        
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('user_profile', username=username))

@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
def chat_dm(username):
    if 'user' not in session:
        return redirect(url_for('login'))
        
    current_user = session['user']
    # 항상 유저 알파벳 순서대로 방 고유 key 생성하여 서로 엇갈리지 않게 함
    room_id = f"dm_{min(current_user, username)}_{max(current_user, username)}"
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message) VALUES ('dm', ?, ?, ?)",
                           (room_id, current_user, message))
            conn.commit()
        return redirect(url_for('chat_dm', username=username))
    
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_identifier = ? ORDER BY timestamp ASC", (room_id,))
    chat_history = cursor.fetchall()
    conn.close()
    return render_template('chat.html', target_name=f"⚡ {username}님과의 갠톡", chat_history=chat_history, is_group=False)

@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if 'user' not in session:
        return redirect(url_for('login'))
    current_user = session['user']
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        invited_members = request.form.getlist('members')
        
        if not room_name:
            return "단톡방 이름을 입력해주세요.", 400
            
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

@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def chat_group(room_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    current_user = session['user']
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM room_members WHERE room_id = ? AND username = ?", (room_id, current_user))
    if not cursor.fetchone():
        conn.close()
        return "접근 권한이 없는 단톡방입니다.", 403
        
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message) VALUES ('group', ?, ?, ?)",
                           (str(room_id), current_user, message))
            conn.commit()
        return redirect(url_for('chat_group', room_id=room_id))
        
    cursor.execute("SELECT room_name FROM chat_rooms WHERE id = ?", (room_id,))
    room_title = cursor.fetchone()[0]
    
    cursor.execute("SELECT username FROM room_members WHERE room_id = ?", (room_id,))
    members_list = [row[0] for row in cursor.fetchall()]
    
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_identifier = ? ORDER BY timestamp ASC", (str(room_id),))
    chat_history = cursor.fetchall()
    conn.close()
    return render_template('chat.html', target_name=f"👥 {room_title}", chat_history=chat_history, is_group=True, members_list=members_list)

@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content', '').strip()
    if not content:
        return "질문 내용을 입력해주세요.", 400
    sender = session.get('user', '익명')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (target_username, sender_username, content) VALUES (?, ?, ?)", (username, sender, content))
    conn.commit()
    conn.close()
    return redirect(url_for('user_profile', username=username))

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

@app.route('/static/<filename>')
def uploaded_file(filename):
    return send_from_directory(upload_dir, filename)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
