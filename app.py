from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
import sqlite3
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
with app.app_context():
    db.create_all()
app.secret_key = 'asked_platform_secret_key'

# 안전한 C드라이브 경로 설정 (무한 로딩 방지)
db_dir = "C:\\AskedPlatform"
upload_dir = os.path.join(db_dir, "static")
if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)
db_path = os.path.join(db_dir, "asked.db")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT,
            bio TEXT,
            profile_img TEXT DEFAULT 'default_profile.png'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_room (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            msg TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/static/<filename>')
def get_static_file(filename):
    return send_from_directory(upload_dir, filename)

# 완전 힙하고 세련된 인스타/에스크 스타일 UI (CSS)
STYLE = '''
<style>
body { font-family: 'Malgun Gothic', sans-serif; background-color: #f7f9fa; color: #1c1e21; margin: 0; padding: 0; }
.container { max-width: 500px; margin: 40px auto; background-color: #fff; padding: 30px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #eef1f4; }
h1 { text-align: center; color: #ff4b72; font-size: 26px; margin-bottom: 25px; font-weight: bold; }
h2 { color: #333; font-size: 20px; margin-bottom: 20px; }
a { color: #ff4b72; text-decoration: none; font-weight: 500; }
button, .btn { background-color: #ff4b72; color: #fff; border: none; padding: 12px 20px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; font-size: 15px; transition: 0.2s; box-sizing: border-box;}
button:hover { background-color: #e03d60; }
.form-group { margin-bottom: 18px; }
.form-group label { display: block; margin-bottom: 8px; font-weight: bold; font-size: 14px; color: #555; }
input[type="text"], input[type="password"], textarea { width: 100%; padding: 12px; border: 1px solid #dbdbdb; border-radius: 8px; box-sizing: border-box; font-size: 14px; }
input[type="text"]:focus, input[type="password"]:focus, textarea:focus { border-color: #ff4b72; outline: none; }
.profile-area { text-align: center; margin-bottom: 20px; }
.profile-img { width: 90px; height: 90px; border-radius: 50%; object-fit: cover; border: 3px solid #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.msg-list { list-style: none; padding: 0; margin-top: 15px; }
.msg-list li { background-color: #f8f9fa; padding: 15px; border-radius: 10px; margin-bottom: 10px; border-left: 4px solid #ff4b72; position: relative; text-align: left;}
.msg-time { color: #999; font-size: 11px; display: block; margin-top: 5px; }
.search-box { display: flex; gap: 8px; margin-bottom: 20px; }
.search-box input { flex: 1; }
.search-box button { width: auto; }
.user-search-item { background-color: #fff; padding: 12px; border-radius: 10px; border: 1px solid #edeef0; margin-bottom: 10px; display: flex; align-items: center; gap: 12px; text-align: left;}
.chat-box { height: 250px; overflow-y: auto; border: 1px solid #eee; padding: 12px; background: #fafafa; border-radius: 8px; margin-bottom: 15px; text-align: left;}
.chat-msg { margin-bottom: 8px; font-size: 14px; }
.chat-msg b { color: #ff4b72; }
.flex-actions { display: flex; gap: 10px; margin-top: 15px; }
.flex-actions button, .flex-actions a { flex: 1; text-align: center; }
.copy-btn { background-color: #4a5568; font-size: 13px; padding: 6px 12px; width: auto; display: inline-block; margin-left: 8px; border-radius: 6px; color: white;}
</style>
'''

@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return f'''
        {STYLE}
        <div class="container">
            <h1>🚀 ASKED PLATFORM</h1>
            <form action="/search" method="get" class="search-box">
                <input type="text" name="query" placeholder="🔍 친구 아이디 검색...">
                <button type="submit">검색</button>
            </form>
            <hr style="border: 0; height: 1px; background: #eee; margin: 20px 0;">
            <div style="text-align: center; margin-top: 20px;">
                <p><a href="/login" style="font-size: 18px; display: block; padding: 10px; background: #fff; border: 1px solid #ff4b72; border-radius: 8px; margin-bottom: 10px;">🔓 로그인</a></p>
                <p><a href="/register" style="font-size: 18px; display: block; padding: 10px; background: #ff4b72; color: white; border-radius: 8px;">📝 회원가입</a></p>
            </div>
        </div>
    '''

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        bio = request.form['bio']
        
        file = request.files['profile_img']
        profile_img = 'default_profile.png'
        if file and allowed_file(file.filename):
            filename = secure_filename(username + "_" + file.filename)
            file.save(os.path.join(upload_dir, filename))
            profile_img = filename
        
        if not username:
            return "아이디를 입력해주세요."
            
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO users VALUES (?, ?, ?, ?)', (username, password, bio, profile_img))
            conn.commit()
            return '<script>alert("회원가입 완료!"); location.href="/login";</script>'
        except sqlite3.IntegrityError:
            return '<script>alert("이미 존재하는 아이디입니다!"); history.back();</script>'
        finally:
            conn.close()
            
    return f'''
        {STYLE}
        <div class="container">
            <h2>📝 회원가입</h2>
            <form method="post" enctype="multipart/form-data">
                <div class="form-group"><label>아이디</label><input type="text" name="username" placeholder="사용할 아이디 입력" required></div>
                <div class="form-group"><label>비밀번호</label><input type="password" name="password" placeholder="비밀번호 입력" required></div>
                <div class="form-group"><label>상태 소개글</label><input type="text" name="bio" value="익명 질문 언제나 환영! ✨"></div>
                <div class="form-group"><label>프로필 사진 설정</label><input type="file" name="profile_img"></div>
                <button type="submit">가입하기 🚀</button>
            </form>
            <p style="text-align:center; margin-top:15px;"><a href="/">메인으로 돌아가기</a></p>
        </div>
    '''

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username=? AND password=?', (username, password))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            session['username'] = username
            return redirect(url_for('dashboard'))
            
        return '<script>alert("아이디 또는 비밀번호가 틀렸어!"); history.back();</script>'
        
    return f'''
        {STYLE}
        <div class="container">
            <h2>🔒 로그인</h2>
            <form method="post">
                <div class="form-group"><label>아이디</label><input type="text" name="username" required></div>
                <div class="form-group"><label>비밀번호</label><input type="password" name="password" required></div>
                <button type="submit">로그인하기</button>
            </form>
            <p style="text-align:center; margin-top:15px;"><a href="/">메인으로 돌아가기</a></p>
        </div>
    '''

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
        
    my_id = session['username']
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute('SELECT profile_img, bio FROM users WHERE username=?', (my_id,))
    user_data = cursor.fetchone()
    cursor.execute('SELECT content, timestamp FROM messages WHERE receiver=? ORDER BY timestamp DESC', (my_id,))
    my_messages = cursor.fetchall()
    conn.close()
    
    profile_img, bio = user_data
    msg_html = "".join([f"<li>{m[0]}<span class='msg-time'>{m[1]}</span></li>" for m in my_messages]) or "<li>📥 아직 받은 익명 메시지가 없어!</li>"
    
    my_ask_link = f"https://thing-enchilada-drivable.ngrok-free.dev/user/{my_id}"
    
    return f'''
        {STYLE}
        <div class="container">
            <div class="profile-area">
                <img src="/static/{profile_img}" class="profile-img" onerror="this.src='https://cdn-icons-png.flaticon.com/512/149/149071.png'">
                <h2>👋 {my_id}님의 피드</h2>
                <p style="color:#666; font-size:14px;">"{bio}"</p>
            </div>
            
            <div style="background: #fff0f2; padding: 12px; border-radius: 8px; font-size: 13px; word-break: break-all; margin-bottom: 15px; border: 1px solid #ffccd5; text-align: left;">
                🔗 <b>내 에스크 링크:</b> <br><span id="linkStr">{my_ask_link}</span>
                <button class="copy-btn" onclick="copyLink()">복사하기</button>
            </div>
            
            <button onclick="location.href='/chat'" style="background:#4a5568; margin-bottom: 20px;">💬 실시간 단체 채팅방 입장</button>
            
            <hr style="border:0; height:1px; background:#eee;">
            <h3>📥 내가 받은 질문 목록</h3>
            <ul class="msg-list">{msg_html}</ul>
            <hr style="border:0; height:1px; background:#eee; margin-top:20px;">
            <p style="text-align: center;"><a href="/logout" style="color:#999;">로그아웃</a></p>
        </div>
        <script>
            function copyLink() {{
                var t = document.createElement("textarea");
                document.body.appendChild(t);
                t.value = document.getElementById("linkStr").innerText;
                t.select();
                document.execCommand("copy");
                document.body.removeChild(t);
                alert("내 에스크 링크가 복사되었어! 카톡에 공유해봐! 🚀");
            }}
        </script>
    '''

@app.route('/user/<target_user>', methods=['GET', 'POST'])
def user_profile(target_user):
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute('SELECT profile_img, bio FROM users WHERE username=?', (target_user,))
    user_data = cursor.fetchone()
    
    if not user_data:
        conn.close()
        return "존재하지 않는 페이지입니다.", 404
        
    profile_img, bio = user_data
    
    if request.method == 'POST':
        msg_content = request.form['message'].strip()
        if msg_content:
            cursor.execute('INSERT INTO messages (receiver, content) VALUES (?, ?)', (target_user, msg_content))
            conn.commit()
            conn.close()
            return f'<script>alert("{target_user}님에게 익명 배달 완료! 🚀"); location.href="/user/{target_user}";</script>'
            
    conn.close()
    return f'''
        {STYLE}
        <div class="container">
            <div class="profile-area">
                <img src="/static/{profile_img}" class="profile-img" onerror="this.src='https://cdn-icons-png.flaticon.com/512/149/149071.png'">
                <h2>💬 {target_user}님에게 질문하기</h2>
                <p style="color:#666; font-size:14px;">"{bio}"</p>
            </div>
            <hr style="border:0; height:1px; background:#eee; margin-bottom:20px;">
            <form method="post">
                <textarea name="message" placeholder="상처 주는 말 대신 따뜻한 질문을 남겨주세요! (익명 보장 🔒)" rows="4" required></textarea><br><br>
                <button type="submit">익명 질문 전송하기 💌</button>
            </form>
            <p style="text-align:center; margin-top:15px;"><a href="/">나도 에스크 페이지 만들기</a></p>
        </div>
    '''

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute('SELECT username, profile_img, bio FROM users WHERE username LIKE ?', ('%'+query+'%',))
    users = cursor.fetchall()
    conn.close()
    
    users_html = "".join([f'''
        <div class="user-search-item">
            <img src="/static/{user[1]}" class="profile-img" style="width: 45px; height: 45px;" onerror="this.src='https://cdn-icons-png.flaticon.com/512/149/149071.png'">
            <div style="flex:1;">
                <strong style="font-size:15px;">{user[0]}</strong>
                <p style="margin: 3px 0 0 0; font-size:12px; color:#666;">{user[2]}</p>
            </div>
            <a href="/user/{user[0]}" style="background:#ff4b72; color:white; padding:6px 12px; border-radius:6px; font-size:13px; font-weight:bold;">이동</a>
        </div>
    ''' for user in users]) or "<p style='text-align:center; color:#999;'>검색 결과에 맞는 사용자가 없어 🥲</p>"
    
    return f'''
        {STYLE}
        <div class="container">
            <h2>🔍 "{query}" 검색 결과</h2>
            <div style="margin: 20px 0;">{users_html}</div>
            <button onclick="location.href='/'" style="background:#4a5568;">메인으로 돌아가기</button>
        </div>
    '''

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'username' not in session:
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        msg = request.form.get('msg', '').strip()
        if msg:
            cursor.execute('INSERT INTO chat_room (username, msg) VALUES (?, ?)', (session['username'], msg))
            conn.commit()
            
    cursor.execute('SELECT username, msg FROM chat_room ORDER BY timestamp DESC LIMIT 30')
    chats = cursor.fetchall()[::-1]
    conn.close()
    
    chat_html = "".join([f'<div class="chat-msg"><b>{c[0]}:</b> {c[1]}</div>' for c in chats])
    
    return f'''
        {STYLE}
        <div class="container">
            <h2>💬 실시간 단체 대화방</h2>
            <p style="font-size:12px; color:#999; text-align:center;">(글을 쓰고 전송을 누르면 대화가 쌓여!)</p>
            <div class="chat-box" id="cb">{chat_html}</div>
            
            <form method="post" style="display:flex; gap:6px;">
                <input type="text" name="msg" placeholder="메시지를 입력해봐..." autocomplete="off" required style="flex:1;">
                <button type="submit" style="width:auto; padding:0 20px;">전송</button>
            </form>
            
            <div class="flex-actions">
                <button onclick="location.reload()" style="background:#4a5568;">🔄 새로고침</button>
                <button onclick="location.href='/dashboard'" style="background:#718096;">🏠 내 피드로</button>
            </div>
        </div>
        <script>
            var objDiv = document.getElementById("cb");
            objDiv.scrollTop = objDiv.scrollHeight;
        </script>
    '''

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
