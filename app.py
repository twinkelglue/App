import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from psycopg2.extras import DictCursor

app = Flask(__name__)
app.secret_key = 'chatclub_ultra_secret_key'

# 1. Neon PostgreSQL 연결 함수
def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

# 2. 데이터베이스 테이블 초기화 (유저님이 쓰시던 데이터 스펙 복원)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 유저 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            profile_pic TEXT DEFAULT ''
        )
    ''')
    
    # 팔로우 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            id SERIAL PRIMARY KEY,
            follower_id INT NOT NULL,
            following_id INT NOT NULL,
            UNIQUE(follower_id, following_id)
        )
    ''')
    
    # 에스크 질문 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS asked (
            id SERIAL PRIMARY KEY,
            target_user TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 채팅 메시지 테이블 (유저님 HTML의 api 명세 반영)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            room_type TEXT NOT NULL, 
            room_id TEXT NOT NULL,     
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 샘플 대화방 정보 저장을 위한 그룹방 테이블 생성
    cur.execute('''
        CREATE TABLE IF NOT EXISTS group_rooms (
            room_id TEXT PRIMARY KEY,
            room_name TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

# 유튜브 변환 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)


# --- [ 라우팅 핵심 로직 ] ---

# 홈 화면 (유저님 index.html 연동)
@app.route('/')
def home():
    user = session.get('user')
    if not user:
        return render_template('index.html')
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 내가 속한 개인 톡방 목록 추출 (메시지 보낸 이력 기반)
    cur.execute('''
        SELECT DISTINCT room_id FROM messages 
        WHERE room_type = 'dm' AND room_id LIKE %s
    ''', (f'%{user}%',))
    dm_rooms = cur.fetchall()
    my_dms = []
    for r in dm_rooms:
        # room_id가 "user1-user2" 형태이므로 상대방 이름만 파싱
        parts = r['room_id'].split('-')
        if user in parts:
            other = parts[1] if parts[0] == user else parts[0]
            if other not in my_dms:
                my_dms.append(other)
                
    # 2. 전체 그룹 단톡방 목록 추출
    cur.execute('SELECT room_id, room_name FROM group_rooms')
    my_rooms = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('index.html', my_dms=my_dms, my_rooms=my_rooms)


# 회원가입 처리
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (username, password))
            conn.commit()
            session['user'] = username  # 유저님 HTML 세션 규격 매칭
            return redirect(url_for('home'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>"
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')


# 로그인 처리
@app.route('/login', methods=['POST'])
def login():
    username = request.form['username'].strip()
    password = request.form['password'].strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
    user_row = cur.fetchone()
    cur.close()
    conn.close()
    
    if user_row:
        session['user'] = username
        return redirect(url_for('home'))
    else:
        return "<script>alert('아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"


# 로그아웃
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))


# 프로필 / 에스크 홈 (유저님 profile.html 연동)
@app.route('/user/<username>')
def profile(username):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT * FROM users WHERE username = %s', (username,))
    target_user = cur.fetchone()
    if not target_user:
        cur.close()
        conn.close()
        return "<script>alert('존재하지 않는 유저입니다.'); history.back();</script>", 404
        
    # 질문 리스트업
    cur.execute('SELECT * FROM asked WHERE target_user = %s ORDER BY id DESC', (username,))
    questions = cur.fetchall()
    
    # 팔로우/팔로잉 카운트
    cur.execute('SELECT COUNT(*) FROM follows WHERE following_id = %s', (target_user['id'],))
    followers_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM follows WHERE follower_id = %s', (target_user['id'],))
    following_count = cur.fetchone()[0]
    
    # 내가 팔로우 중인지 여부
    is_following = False
    me = session.get('user')
    if me and me != username:
        cur.execute('SELECT id FROM users WHERE username = %s', (me,))
        me_row = cur.fetchone()
        if me_row:
            cur.execute('SELECT * FROM follows WHERE follower_id = %s AND following_id = %s', (me_row['id'], target_user['id']))
            if cur.fetchone():
                is_following = True
                
    cur.close()
    conn.close()
    
    # 유튜브 링크 안전 치환 변환
    processed_questions = []
    for q in questions:
        q_dict = dict(q)
        q_dict['question'] = convert_youtube_links(q_dict['question'])
        q_dict['answer'] = convert_youtube_links(q_dict['answer'])
        processed_questions.append(q_dict)
        
    return render_template('profile.html', target_user=target_user, questions=processed_questions, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)


# 프로필 소개글 수정 (정보 업데이트)
@app.route('/update_profile', methods=['POST'])
def update_profile():
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
    bio = request.form.get('bio', '').strip()
    
    # 파일 업로드는 간단 저장을 위해 폼이 있을 때 처리 (없으면 기본값 유지)
    file = request.files.get('profile_pic')
    profile_pic_url = ""
    
    conn = get_db_connection()
    cur = conn.cursor()
    if file and file.filename != '':
        # 실무나 테스트 환경에 맞게 서버 내부 static 폴더 등에 임시 저장하거나 주소 처리
        filename = f"static/uploads/{me}_{file.filename}"
        os.makedirs("static/uploads", exist_ok=True)
        file.save(filename)
        profile_pic_url = "/" + filename
        cur.execute('UPDATE users SET bio = %s, profile_pic = %s WHERE username = %s', (bio, profile_pic_url, me))
    else:
        cur.execute('UPDATE users SET bio = %s WHERE username = %s', (bio, me))
        
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=me))


# 유저 검색 (index.html에서 진입)
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT username FROM users WHERE username LIKE %s', (f'%{query}%',))
    users = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('search_results.html', users=users, query=query)


# 팔로우 / 팔로우 취소 처리
@app.route('/follow/<username>', methods=['POST'])
def follow(username):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id FROM users WHERE username = %s', (me,))
    me_id = cur.fetchone()['id']
    cur.execute('SELECT id FROM users WHERE username = %s', (username,))
    target_id = cur.fetchone()['id']
    
    cur.execute('SELECT * FROM follows WHERE follower_id = %s AND following_id = %s', (me_id, target_id))
    already = cur.fetchone()
    
    if already:
        cur.execute('DELETE FROM follows WHERE follower_id = %s AND following_id = %s', (me_id, target_id))
    else:
        cur.execute('INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)', (me_id, target_id))
        
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=username))


# 에스크 질문 전송
@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    question = request.form.get('question', '').strip()
    if not question:
        return "<script>alert('질문을 입력하세요.'); history.back();</script>"
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO asked (target_user, question) VALUES (%s, %s)', (username, question))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=username))


# 에스크 답변 등록
@app.route('/answer/<int:q_id>', methods=['POST'])
def answer(q_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
    answer_text = request.form.get('answer', '').strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE asked SET answer = %s WHERE id = %s AND target_user = %s', (answer_text, q_id, me))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=me))


# 에스크 질문 삭제
@app.route('/delete_ask/<int:q_id>', methods=['POST'])
def delete_ask(q_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM asked WHERE id = %s AND target_user = %s', (q_id, me))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('profile', username=me))


# 그룹 단톡방 생성페이지 로딩 & 액션 처리 (create_group.html 연동)
@app.route('/create_group', methods=['GET', 'POST'])
@app.route('/group/create', methods=['GET', 'POST'])  # 두 URL 주소 규격 모두 바인딩
def create_group():
    if request.method == 'POST':
        # form name="room_name" 대응
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "<script>alert('방 이름을 입력해주세요.'); history.back();</script>"
            
        # 고유 방 ID 규격화 생성
        room_id = f"group_{room_name.replace(' ', '_')}"
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO group_rooms (room_id, room_name) VALUES (%s, %s) ON CONFLICT DO NOTHING', (room_id, room_name))
            conn.commit()
        finally:
            cur.close()
            conn.close()
            
        return redirect(url_for('chat_group', room_id=room_id))
    return render_template('create_group.html')


# 1:1 개인 DM 톡방 렌더링
@app.route('/chat/dm/<target_username>', methods=['GET', 'POST'])
def chat_dm(target_username):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    room_id = "-".join(sorted([me, target_username]))
    
    # 만약 유저가 채팅 전송창에서 바로 비동기 POST를 보낸 경우 처리 (fetch(window.location.pathname) 대응)
    if request.method == 'POST':
        msg_txt = request.form.get('message', '').strip()
        if msg_txt:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('INSERT INTO messages (room_type, room_id, sender, message) VALUES (%s, %s, %s, %s)',
                        ('dm', room_id, me, msg_txt))
            conn.commit()
            cur.close()
            conn.close()
        return jsonify({'status': 'success'})
        
    return render_template('chat.html', room_type='dm', room_id=room_id, target_name=target_username)


# 그룹 단톡방 렌더링
@app.route('/group/chat/<room_id>', methods=['GET', 'POST'])
def chat_group(room_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT room_name FROM group_rooms WHERE room_id = %s', (room_id,))
    room_row = cur.fetchone()
    room_name = room_row['room_name'] if room_row else room_id
    
    # 비동기 POST 처리
    if request.method == 'POST':
        msg_txt = request.form.get('message', '').strip()
        if msg_txt:
            cur.execute('INSERT INTO messages (room_type, room_id, sender, message) VALUES (%s, %s, %s, %s)',
                        ('group', room_id, me, msg_txt))
            conn.commit()
        cur.close()
        conn.close()
        return jsonify({'status': 'success'})
        
    cur.close()
    conn.close()
    return render_template('chat.html', room_type='group', room_id=room_id, target_name=room_name)


# 채팅 데이터 갱신 API (유저님 chat.html의 /api/chat_history/${roomType}/${roomId} 구현)
@app.route('/api/chat_history/<room_type>/<room_id>')
def get_chat_history(room_type, room_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT sender, message FROM messages WHERE room_type = %s AND room_id = %s ORDER BY id ASC', (room_type, room_id))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    messages = []
    for r in rows:
        messages.append({
            'sender': r['sender'],
            'message': convert_youtube_links(r['message']) # 유튜브 임베드 자동 파싱 적용
        })
    return jsonify(messages)

if __name__ == '__main__':
    app.run(debug=True)
