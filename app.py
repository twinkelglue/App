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

# 데이터베이스 테이블 초기화 (기존 데이터 구조 100% 영구화)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 유저 테이블 (소개글, 프사 기능 포함)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            profile_pic TEXT DEFAULT ''
        )
    ''')
    
    # 2. 팔로우 테이블 (팔로워/팔로잉 숫자 연동)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            id SERIAL PRIMARY KEY,
            follower_id INT NOT NULL,
            following_id INT NOT NULL,
            UNIQUE(follower_id, following_id)
        )
    ''')
    
    # 3. 에스크 질문 테이블 (익명 질문 및 답변 피드용)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS asked (
            id SERIAL PRIMARY KEY,
            target_user TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 4. 채팅 메시지 테이블 (1:1 DM 및 그룹 단톡방 + Imgur 사진 링크 통합)
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
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

# 유튜브 링크 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)

# Imgur 외부 이미지 금고 업로드 함수 (절대 안 깨짐)
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
    if 'username' in session:
        return redirect(url_for('profile', username=session['username']))
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
            session['username'] = username
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
            session['username'] = username
            return redirect(url_for('profile', username=username))
        else:
            return "아이디 또는 비밀번호가 틀렸습니다."
    return render_template('index.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
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
    if 'username' in session and session['username'] != username:
        cur.execute('SELECT id FROM users WHERE username = %s', (session['username'],))
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
        
    return render_template('profile.html', target_user=target_user, questions=processed_questions, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'username' not in session:
        return redirect(url_for('home'))
    bio = request.form.get('bio', '').strip()
    file = request.files.get('profile_pic')
    
    profile_pic_url = None
    if file and file.filename != '':
        profile_pic_url = upload_to_imgur(file)
        
    conn = get_db_connection()
    cur = conn.cursor()
    if profile_pic_url:
        cur.execute('UPDATE users SET bio = %s, profile_pic = %s WHERE username = %s', (bio, profile_pic_url, session['username']))
    else:
        cur.execute('UPDATE users SET bio = %s WHERE username = %s', (bio, session['username']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['username']))

@app.route('/follow/<username>', methods=['POST'])
def follow(username):
    if 'username' not in session:
        return redirect(url_for('home'))
    if session['username'] == username:
        return "자기 자신을 팔로우할 수 없습니다."
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id FROM users WHERE username = %s', (session['username'],))
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
    if 'username' not in session:
        return redirect(url_for('home'))
    answer_text = request.form.get('answer', '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE asked SET answer = %s WHERE id = %s AND target_user = %s', (answer_text, q_id, session['username']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['username']))

@app.route('/delete_ask/<int:q_id>', methods=['POST'])
def delete_ask(q_id):
    if 'username' not in session:
        return redirect(url_for('home'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM asked WHERE id = %s AND target_user = %s', (q_id, session['username']))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=session['username']))

# --- 🚀 실시간 1:1 DM, 그룹 단톡방, 4초 실시간 폴링 알림 기능 완벽 지원 ---

@app.route('/chat/dm/<target_username>')
def chat_dm(target_username):
    if 'username' not in session:
        return redirect(url_for('home'))
    me = session['username']
    room_id = "-".join(sorted([me, target_username]))
    return render_template('chat.html', room_type='dm', room_id=room_id, target_name=target_username)

@app.route('/chat/group/<room_name>')
def chat_group(room_name):
    if 'username' not in session:
        return redirect(url_for('home'))
    return render_template('chat.html', room_type='group', room_id=room_name, target_name=room_name)

# 프론트엔드에서 4초마다 대화 기록을 쏙쏙 빼오는 실시간 알림 동기화 API
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
        # 사진 주소가 있을 경우, 메인 대화창에 영구 보존된 주소로 이미지 띄우기
        if r['image_url']:
            msg_html += f'<br><img src="{r["image_url"]}" style="max-width:230px; border-radius:10px; margin-top:6px; box-shadow: 0px 2px 5px rgba(0,0,0,0.1);">'
        messages.append({'sender': r['sender'], 'message': msg_html})
    return jsonify(messages)

# 메시지 및 이미지 업로드 통합 처리 API
@app.route('/api/send_message', methods=['POST'])
def send_message():
    if 'username' not in session:
        return jsonify({'status': 'fail'}), 403
        
    room_type = request.form.get('room_type')
    room_id = request.form.get('room_id')
    message = request.form.get('message', '').strip()
    file = request.files.get('image') # 파일 선택창(HTML)에서 날아온 이미지 감지
    
    image_url = ''
    if file and file.filename != '':
        image_url = upload_to_imgur(file) or '' # 외부 이미지 금고로 안전하게 우회 저장
        
    if not message and not image_url:
        return jsonify({'status': 'fail', 'reason': 'empty'}), 400
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO messages (room_type, room_id, sender, message, image_url) VALUES (%s, %s, %s, %s, %s)',
                (room_type, room_id, session['username'], message, image_url))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'success'})

if __name__ == '__main__':
    app.run(debug=True)
