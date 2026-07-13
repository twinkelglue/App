import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from psycopg2.extras import DictCursor
import requests

app = Flask(__name__)
app.secret_key = 'your_very_secret_key_here'

# 데이터베이스 연결 함수 (Neon Postgres)
def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

# 데이터베이스 테이블 초기화 (채팅방 이미지 보존용 image_url 필드 완벽 반영)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 유저 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            profile_pic TEXT DEFAULT ''
        )
    ''')
    
    # 2. 팔로우 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            id SERIAL PRIMARY KEY,
            follower_id INT NOT NULL,
            following_id INT NOT NULL,
            UNIQUE(follower_id, following_id)
        )
    ''')
    
    # 3. 에스크 질문 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS asked (
            id SERIAL PRIMARY KEY,
            target_user TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 4. 채팅 메시지 테이블 (사진 영구 저장 공간 image_url 완벽 포함)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            room_type TEXT NOT NULL, 
            room_id TEXT NOT NULL,     
            sender TEXT NOT NULL,
            message TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 5. 그룹 단톡방 정보 테이블 (누락방지 추가)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS group_rooms (
            id SERIAL PRIMARY KEY,
            room_name TEXT NOT NULL,
            created_by TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

# 유튜브 링크 자동 변환 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)

# Imgur 외부 이미지 금고 업로드 함수 (사진 터짐 방지)
def upload_to_imgur(file_storage):
    try:
        headers = {"Authorization": "Client-ID 54401259d9975e9"}
        files = {"image": file_storage.read()}
        response = requests.post("https://api.imgur.com/3/image", headers=headers, files=files)
        data = response.json()
        if data.get("success"):
            return data["data"]["link"]
    except Exception as e:
        print("Imgur 업로드 오류:", e)
    return None

@app.route('/')
def home():
    if 'user' in session:
        # 메인 홈에서 내가 참여중인 갠톡방/단톡방 리스트를 index.html에 뿌려주기 위한 데이터 전송
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 내가 대화한 적 있는 DM 상대방들 가져오기
        cur.execute('''
            SELECT DISTINCT room_id FROM messages 
            WHERE room_type = 'dm' AND room_id LIKE %s
        ''', (f"%{session['user']}%",))
        dm_rooms = cur.fetchall()
        my_dms = []
        for r in dm_rooms:
            parts = r['room_id'].split('-')
            if session['user'] in parts:
                other = parts[1] if parts[0] == session['user'] else parts[0]
                if other not in my_dms:
                    my_dms.append(other)
                    
        # 모든 그룹 단톡방 리스트 가져오기
        cur.execute('SELECT id, room_name FROM group_rooms')
        my_rooms = cur.fetchall()
        
        cur.close()
        conn.close()
        return render_template('index.html', my_dms=my_dms, my_rooms=my_rooms)
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        if not username or not password:
            return "아이디와 비밀번호를 입력해주세요."
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (username, password))
            conn.commit()
            session['user'] = username
            return redirect(url_for('profile', username=username))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "이미 존재하는 아이디입니다."
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            session['user'] = username
            return redirect(url_for('profile', username=username))
        else:
            return "아이디 또는 비밀번호가 틀렸습니다."
    return render_template('index.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))

@app.route('/user/<username>')
def profile(username):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT * FROM users WHERE username = %s', (username,))
    target_user = cur.fetchone()
    if not target_user:
        cur.close()
        conn.close()
        return "존재하지 않는 유저입니다.", 404
        
    cur.execute('SELECT * FROM asked WHERE target_user = %s ORDER BY id DESC', (username,))
    questions = cur.fetchall()
    
    cur.execute('SELECT COUNT(*) FROM follows WHERE following_id = %s', (target_user['id'],))
    followers_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM follows WHERE follower_id = %s', (target_user['id'],))
    following_count = cur.fetchone()[0]
    
    is_following = False
    if 'user' in session and session['user'] != username:
        cur.execute('SELECT id FROM users WHERE username = %s', (session['user'],))
        me = cur.fetchone()
        if me:
            cur.execute('SELECT * FROM follows WHERE follower_id = %s AND following_id = %s', (me['id'], target_user['id']))
            if cur.fetchone():
                is_following = True
                
    cur.close()
    conn.close()
    
    processed_questions = []
    for q in questions:
        q_dict = dict(q)
        q_dict['question'] = convert_youtube_links(q_dict['question'])
        q_dict['answer'] = convert_youtube_links(q_dict['answer'])
        processed_questions.append(q_dict)
        
    return render_template('user.html', target_user=target_user, questions=processed_questions, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user' not in session:
        return redirect(url_for('home'))
    bio = request.form.get('bio', '').strip()
    file = request.files.get('profile_pic')
    
    profile_pic_url = None
    if file and file.filename != '':
        profile_pic_url = upload_to_imgur(file)
        
    conn = get_db_connection()
    cur = conn.cursor()
    if profile_pic_url:
        cur.execute('UPDATE users SET bio = %s, profile_pic = %s WHERE username = %s', (bio, profile_pic_url, session['user']))
    else:
        cur.execute('UPDATE users SET bio = %s WHERE username = %s', (bio, session['user']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['user']))

@app.route('/follow/<username>', methods=['POST'])
def follow(username):
    if 'user' not in session:
        return redirect(url_for('home'))
    if session['user'] == username:
        return "자기 자신을 팔로우할 수 없습니다."
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id FROM users WHERE username = %s', (session['user'],))
    me = cur.fetchone()
    cur.execute('SELECT id FROM users WHERE username = %s', (username,))
    target = cur.fetchone()
    
    if me and target:
        try:
            cur.execute('INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)', (me['id'], target['id']))
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur.execute('DELETE FROM follows WHERE follower_id = %s AND following_id = %s', (me['id'], target['id']))
            conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=username))

@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    question = request.form.get('question', '').strip()
    if not question:
        return "질문을 입력해주세요."
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO asked (target_user, question) VALUES (%s, %s)', (username, question))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=username))

@app.route('/answer/<int:q_id>', methods=['POST'])
def answer(q_id):
    if 'user' not in session:
        return redirect(url_for('home'))
    answer_text = request.form.get('answer', '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE asked SET answer = %s WHERE id = %s AND target_user = %s', (answer_text, q_id, session['user']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['user']))

@app.route('/delete_ask/<int:q_id>', methods=['POST'])
def delete_ask(q_id):
    if 'user' not in session:
        return redirect(url_for('home'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM asked WHERE id = %s AND target_user = %s', (q_id, session['user']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['user']))

# --- 🔍 저격 유저 검색 라우터 (누락방지 복구!) ---
@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT username, bio FROM users WHERE username LIKE %s', (f"%{query}%",))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('search_results.html', results=results, query=query)

# --- 👥 그룹 단톡방 생성 및 오픈 입장 라우터 (누락방지 복구!) ---
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if 'user' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "방 이름을 입력해주세요."
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO group_rooms (room_name, created_by) VALUES (%s, %s)', (room_name, session['user']))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('home'))
    return render_template('create_group.html')

@app.route('/group/chat/<int:room_id>')
def group_chat(room_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT room_name FROM group_rooms WHERE id = %s', (room_id,))
    room = cur.fetchone()
    cur.close()
    conn.close()
    if not room:
        return "존재하지 않는 단톡방입니다.", 404
    return render_template('chat.html', room_type='group', room_id=str(room_id), target_name=room['room_name'])

# --- 🚀 실시간 메시지 API 및 자동 알림 라우터 ---
@app.route('/chat/dm/<target_username>')
def chat_dm(target_username):
    if 'user' not in session:
        return redirect(url_for('home'))
    me = session['user']
    room_id = "-".join(sorted([me, target_username]))
    return render_template('chat.html', room_type='dm', room_id=room_id, target_name=target_username)

@app.route('/api/messages/<room_type>/<room_id>')
def get_messages(room_type, room_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT sender, message, image_url FROM messages WHERE room_type = %s AND room_id = %s ORDER BY id ASC', (room_type, room_id))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    messages = []
    for r in rows:
        msg_html = convert_youtube_links(r['message'])
        if r['image_url']:
            msg_html += f'<br><img src="{r["image_url"]}" style="max-width:200px; border-radius:10px; margin-top:5px;">'
        messages.append({'sender': r['sender'], 'message': msg_html})
    return jsonify(messages)

@app.route('/api/send_message', methods=['POST'])
def send_message():
    if 'user' not in session:
        return jsonify({'status': 'fail'}), 403
        
    room_type = request.form.get('room_type')
    room_id = request.form.get('room_id')
    message = request.form.get('message', '').strip()
    file = request.files.get('image')
    
    image_url = ''
    if file and file.filename != '':
        image_url = upload_to_imgur(file) or ''
        
    if not message and not image_url:
        return jsonify({'status': 'fail', 'reason': 'empty'}), 400
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO messages (room_type, room_id, sender, message, image_url) VALUES (%s, %s, %s, %s, %s)',
                (room_type, room_id, session['user'], message, image_url))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/api/check_notifications')
def check_notifications():
    # 실시간 알림 새로고침 감지용 빈 라우터 에러 방지용 추가
    return jsonify({'new_dms': 0, 'new_groups': 0})

if __name__ == '__main__':
    app.run(debug=True)
