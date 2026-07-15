import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from psycopg2.extras import DictCursor

app = Flask(__name__)
# 세션 유지를 위한 키 (세션이 유지되어야 로그인 상태 및 초록색 테마가 정상 작동합니다)
app.secret_key = 'chatclub_ultra_secure_key_100_percent'

# 1. Neon PostgreSQL 연결 (환경변수 DATABASE_URL 기준)
def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        # DB 주소가 없을 때 서버가 죽는 것을 방지하기 위한 안전장치
        raise ValueError("Render 설정에 DATABASE_URL 환경 변수가 누락되었습니다. Neon 연결 주소를 입력해주세요.")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

# 2. 데이터베이스 테이블 초기화 (데이터가 절대 지워지지 않고 누적되는 핵심 저장소)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 회원 테이블 (인스타 스타일 프로필 필드 포함)
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
    
    # 에스크 질문 테이블 (익명 질문 및 답변 영구 보존)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS asked (
            id SERIAL PRIMARY KEY,
            target_user TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 채팅 메시지 테이블 (채팅 내용 영구 보존)
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
    
    # 그룹방 목록 테이블
    cur.execute('''
        CREATE TABLE IF NOT EXISTS group_rooms (
            room_id TEXT PRIMARY KEY,
            room_name TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

# 앱 기동 시 자동으로 테이블 구조 세팅
try:
    init_db()
except Exception as e:
    print(f"[DB Init Warning] Neon 테이블 초기화 실패: {e}")

# 유튜브 변환 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)


# --- [ 핵심 라우터 및 템플릿 연동 ] ---

# 1. 홈 화면 (index.html)
@app.route('/')
def home():
    user = session.get('user')
    # 비로그인 상태일 때는 빈 리스트를 전달하여 화면이 깨지거나 뻗는 문제를 방지합니다.
    if not user:
        return render_template('index.html', my_dms=[], my_rooms=[])
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 나와 대화한 이력이 있는 1:1 대화 상대방 목록 (index.html에서 초록색 테마 활성화 대상)
    cur.execute('''
        SELECT DISTINCT room_id FROM messages 
        WHERE room_type = 'dm' AND room_id LIKE %s
    ''', (f'%{user}%',))
    dm_rooms = cur.fetchall()
    
    my_dms = []
    for r in dm_rooms:
        parts = r['room_id'].split('-')
        if user in parts:
            other = parts[1] if parts[0] == user else parts[0]
            if other not in my_dms:
                my_dms.append(other)
                
    # 개설된 모든 그룹 단톡방 목록 추출
    cur.execute('SELECT room_id, room_name FROM group_rooms')
    my_rooms = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # 템플릿에 정상 데이터를 전달해야 비로소 '초록색 렌더링 블록'들이 켜집니다!
    return render_template('index.html', my_dms=my_dms, my_rooms=my_rooms)


# 2. 로그인 처리 (★ index.html 및 login.html과 정확히 연동되며 로그인 후 user.html로 이동)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
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
            # 로그인 성공 후 유저님의 파일인 'user.html' 컨트롤러인 profile 라우트로 이동시킵니다.
            return redirect(url_for('profile', username=username))
        else:
            return "<script>alert('아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"
            
    return render_template('login.html')


# 3. 회원가입 (register.html)
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
            session['user'] = username  # 가입과 동시에 세션 로그인 처리
            return redirect(url_for('home'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>"
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')


# 4. 로그아웃
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))


# 5. 에스크 프로필 홈 (★ 중요: templates/user.html 파일과 100% 매칭 완료!)
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
        
    # 작성된 질문 가져오기 (절대 지워지지 않고 보존됨)
    cur.execute('SELECT * FROM asked WHERE target_user = %s ORDER BY id DESC', (username,))
    questions = cur.fetchall()
    
    # 팔로워 및 팔로잉 카운트
    cur.execute('SELECT COUNT(*) FROM follows WHERE following_id = %s', (target_user['id'],))
    followers_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM follows WHERE follower_id = %s', (target_user['id'],))
    following_count = cur.fetchone()[0]
    
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
    
    processed_questions = []
    for q in questions:
        q_dict = dict(q)
        q_dict['question'] = convert_youtube_links(q_dict['question'])
        q_dict['answer'] = convert_youtube_links(q_dict['answer'])
        processed_questions.append(q_dict)
        
    # 유저님의 실제 파일인 'user.html'을 렌더링하여 500 에러를 원천 차단합니다.
    return render_template('user.html', target_user=target_user, questions=processed_questions, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)


# 6. 프로필 바이오 및 사진 업데이트
@app.route('/update_profile', methods=['POST'])
def update_profile():
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
    bio = request.form.get('bio', '').strip()
    
    file = request.files.get('profile_pic')
    profile_pic_url = ""
    
    conn = get_db_connection()
    cur = conn.cursor()
    if file and file.filename != '':
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


# 7. 친구 검색 (search_results.html)
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


# 8. 팔로우 토글
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


# 9. 에스크 익명 질문 등록 (asked 테이블에 영구 저장)
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


# 10. 익명 질문에 답변 달기 (Neon DB에 영구 보존)
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


# 11. 질문 삭제 기능
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


# 12. 새 그룹 단톡방 개설 (create_group.html)
@app.route('/create_group', methods=['GET', 'POST'])
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "<script>alert('방 이름을 입력해주세요.'); history.back();</script>"
            
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


# 13. 1:1 개인 DM 톡방 렌더링 & 메시지 전송 처리 (messages 테이블 영구 누적)
@app.route('/chat/dm/<target_username>', methods=['GET', 'POST'])
def chat_dm(target_username):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    room_id = "-".join(sorted([me, target_username]))
    
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


# 14. 그룹 단톡방 렌더링 & 메시지 전송 처리 (messages 테이블 영구 누적)
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


# 15. 실시간 비동기 채팅 내역 로드 API (chat.html 요구 규격 준수)
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
            'message': convert_youtube_links(r['message'])
        })
    return jsonify(messages)

if __name__ == '__main__':
    app.run(debug=True)
