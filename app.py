from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, jsonify
import psycopg2
import os
import re
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'asked_platform_hip_secret_key'

# Render에 등록한 Neon DB 주소를 자동으로 가져옴
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/asked')

# 프사 전송용 폴더 (프사는 내 프로필에 계속 남아있어야 하므로 유지)
upload_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "static")
if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def convert_youtube_links(text):
    if not text: return text
    text = re.sub(
        r'(https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)[^\s]*)',
        r'<br><iframe width="100%" height="220" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>',
        text
    )
    text = re.sub(
        r'(https?://youtu\.be/([a-zA-Z0-9_-]+)[^\s]*)',
        r'<br><iframe width="100%" height="220" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>',
        text
    )
    return text

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, bio TEXT, profile_img TEXT DEFAULT 'default_profile.png'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY, target_username TEXT, sender_username TEXT, content TEXT, answer TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            follower TEXT, following TEXT, PRIMARY KEY (follower, following)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id SERIAL PRIMARY KEY, room_name TEXT, creator TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS room_members (
            room_id INTEGER, username TEXT, PRIMARY KEY (room_id, username)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY, room_type TEXT, room_identifier TEXT, sender TEXT, message TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

init_db()

@app.route('/')
def index():
    current_user = session.get('user')
    my_rooms, my_dms = [], []
    if current_user:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT r.id, r.room_name FROM chat_rooms r
            JOIN room_members m ON r.id = m.room_id WHERE m.username = %s
        ''', (current_user,))
        my_rooms = cursor.fetchall()
        
        cursor.execute('''
            SELECT DISTINCT room_identifier FROM chat_messages 
            WHERE room_type = 'dm' AND room_identifier LIKE %s
        ''', (f"%{current_user}%",))
        dm_rooms = cursor.fetchall()
        for dm in dm_rooms:
            parts = dm[0].replace("dm_", "").split("_and_")
            if len(parts) == 2 and current_user in parts:
                other_user = parts[1] if parts[0] == current_user else parts[0]
                cursor.execute("SELECT 1 FROM users WHERE username = %s", (other_user,))
                if cursor.fetchone() and other_user not in my_dms:
                    my_dms.append(other_user)
        cursor.close()
        conn.close()
    return render_template('index.html', my_rooms=my_rooms, my_dms=my_dms)

@app.route('/api/check_notifications')
def check_notifications():
    current_user = session.get('user')
    if not current_user: return jsonify({"status": "unauthorized"}), 401
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM chat_messages 
        WHERE room_type = 'dm' AND room_identifier LIKE %s AND sender != %s
        AND timestamp >= NOW() - INTERVAL '4 seconds'
    ''', (f"%{current_user}%", current_user))
    new_dms = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM chat_messages cm
        JOIN room_members rm ON cm.room_identifier = CAST(rm.room_id AS TEXT)
        WHERE cm.room_type = 'group' AND rm.username = %s AND cm.sender != %s
        AND cm.timestamp >= NOW() - INTERVAL '4 seconds'
    ''', (current_user, current_user))
    new_groups = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return jsonify({"new_dms": new_dms, "new_groups": new_groups})

@app.route('/api/chat_history/<room_type>/<identifier>')
def api_chat_history(room_type, identifier):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sender, message, timestamp FROM chat_messages 
        WHERE room_type = %s AND room_identifier = %s ORDER BY timestamp ASC
    ''', (room_type, identifier))
    history = cursor.fetchall()
    cursor.close()
    conn.close()
    chat_list = [{"sender": h[0], "message": convert_youtube_links(h[1]), "time": str(h[2])} for h in history]
    return jsonify(chat_list)

@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
def chat_dm(username):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    users_sorted = sorted([current_user, username])
    room_id = f"dm_{users_sorted[0]}_and_{users_sorted[1]}"
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message) VALUES ('dm', %s, %s, %s)", (room_id, current_user, message))
            conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('chat_dm', username=username))
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_identifier = %s ORDER BY timestamp ASC", (room_id,))
    raw_history = cursor.fetchall()
    cursor.close()
    conn.close()
    chat_history = [(row[0], convert_youtube_links(row[1]), row[2]) for row in raw_history]
    return render_template('chat.html', target_name=f"⚡ {username}님과의 갠톡", chat_history=chat_history, is_group=False, room_id=room_id, room_type="dm")

@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
def chat_group(room_id):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM room_members WHERE room_id = %s AND username = %s", (room_id, current_user))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return "접근 권한 없음", 403
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            cursor.execute("INSERT INTO chat_messages (room_type, room_identifier, sender, message) VALUES ('group', %s, %s, %s)", (str(room_id), current_user, message))
            conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('chat_group', room_id=room_id))
    cursor.execute("SELECT room_name FROM chat_rooms WHERE id = %s", (room_id,))
    room_title = cursor.fetchone()[0]
    cursor.execute("SELECT username FROM room_members WHERE room_id = %s", (room_id,))
    members_list = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT sender, message, timestamp FROM chat_messages WHERE room_identifier = %s ORDER BY timestamp ASC", (str(room_id),))
    raw_history = cursor.fetchall()
    cursor.close()
    conn.close()
    chat_history = [(row[0], convert_youtube_links(row[1]), row[2]) for row in raw_history]
    return render_template('chat.html', target_name=f"👥 {room_title}", chat_history=chat_history, is_group=True, members_list=members_list, room_id=str(room_id), room_type="group")

@app.route('/search')
def search_user():
    query = request.args.get('query', '').strip()
    if not query: return redirect(url_for('index'))
    current_user = session.get('user')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username LIKE %s", (f"%{query}%",))
    users = cursor.fetchall()
    results = []
    for u in users:
        is_following = False
        if current_user:
            cursor.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (current_user, u[0]))
            if cursor.fetchone(): is_following = True
        results.append((u[0], u[1], u[2], is_following))
    cursor.close()
    conn.close()
    return render_template('search_results.html', query=query, results=results)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        if not username or not password: return "정보 누락", 400
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return "중복 아이디", 400
        cursor.execute("INSERT INTO users (username, password, bio) VALUES (%s, %s, %s)", (username, password, "힙한 에스크!"))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user and user[0] == password:
            session['user'] = username
            return redirect(url_for('user_profile', username=username))
        return "정보 불일치", 400
    return render_template('login.html')

@app.route('/user/<username>')
def user_profile(username):
    current_user = session.get('user')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, bio, profile_img FROM users WHERE username = %s", (username,))
    profile_user = cursor.fetchone()
    if not profile_user:
        cursor.close()
        conn.close()
        return "유저 없음", 404
    cursor.execute("SELECT COUNT(*) FROM follows WHERE following = %s", (username,))
    followers_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM follows WHERE follower = %s", (username,))
    following_count = cursor.fetchone()[0]
    is_following = False
    if current_user:
        cursor.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (current_user, username))
        if cursor.fetchone(): is_following = True
    cursor.execute("SELECT id, sender_username, content, answer, timestamp FROM messages WHERE target_username = %s ORDER BY id DESC", (username,))
    messages = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('user.html', profile_user=profile_user, messages=messages, followers_count=followers_count, following_count=following_count, is_following=is_following)

@app.route('/follow/<username>', methods=['POST'])
def follow_user(username):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    if current_user == username: return "본인 불가", 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (current_user, username))
    if cursor.fetchone():
        cursor.execute("DELETE FROM follows WHERE follower = %s AND following = %s", (current_user, username))
    else:
        cursor.execute("INSERT INTO follows (follower, following) VALUES (%s, %s)", (current_user, username))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(request.referrer or url_for('user_profile', username=username))

@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        invited_members = request.form.getlist('members')
        if not room_name: return "방이름 공백 불가", 400
        cursor.execute("INSERT INTO chat_rooms (room_name, creator) VALUES (%s, %s) RETURNING id", (room_name, current_user))
        new_room_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO room_members (room_id, username) VALUES (%s, %s)", (new_room_id, current_user))
        for member in invited_members:
            cursor.execute("INSERT INTO room_members (room_id, username) VALUES (%s, %s)", (new_room_id, member))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('chat_group', room_id=new_room_id))
    cursor.execute("SELECT following FROM follows WHERE follower = %s", (current_user,))
    my_friends = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return render_template('create_group.html', my_friends=my_friends)

@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content', '').strip()
    sender = session.get('user', '익명')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (target_username, sender_username, content) VALUES (%s, %s, %s)", (username, sender, content))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('user_profile', username=username))

@app.route('/answer/<int:msg_id>', methods=['POST'])
def answer(msg_id):
    if 'user' not in session: return redirect(url_for('login'))
    answer_text = request.form.get('answer', '').strip()
    current_user = session['user']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT target_username FROM messages WHERE id = %s", (msg_id,))
    msg = cursor.fetchone()
    if msg and msg[0] == current_user:
        cursor.execute("UPDATE messages SET answer = %s WHERE id = %s", (answer_text, msg_id))
        conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('user_profile', username=current_user))

@app.route('/delete_message/<int:msg_id>', methods=['POST'])
def delete_message(msg_id):
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT target_username FROM messages WHERE id = %s", (msg_id,))
    msg = cursor.fetchone()
    if msg and msg[0] == current_user:
        cursor.execute("DELETE FROM messages WHERE id = %s", (msg_id,))
        conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('user_profile', username=current_user))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user' not in session: return redirect(url_for('login'))
    current_user = session['user']
    bio = request.form.get('bio', '').strip()
    file = request.files.get('profile_img')
    conn = get_db_connection()
    cursor = conn.cursor()
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{current_user}_{file.filename}")
        file.save(os.path.join(upload_dir, filename))
        cursor.execute("UPDATE users SET bio = %s, profile_img = %s WHERE username = %s", (bio, filename, current_user))
    else:
        cursor.execute("UPDATE users SET bio = %s WHERE username = %s", (bio, current_user))
    conn.commit()
    cursor.close()
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
