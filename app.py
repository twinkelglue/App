import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from psycopg2.extras import DictCursor

app = Flask(__name__)
# 세션을 유지하고 로그인 상태를 기록하기 위해 반드시 필요한 고유 키입니다.
app.secret_key = 'chatclub_ultra_secure_key_100_percent'

# 1. Neon PostgreSQL 연결 함수 (환경변수 DATABASE_URL 기준)
def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        # 로컬 테스트용 백업 (Neon 연결이 없을 때 에러 방지)
        raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다. Render 환경 설정을 확인하세요.")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn

# 2. 데이터베이스 테이블 초기화 (DB가 비어있어도 무조건 자동 생성)
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
    
    # 에스크 질문 테이블 (질문 삭제, 답변 여부 포함)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS asked (
            id SERIAL PRIMARY KEY,
            target_user TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 채팅 메시지 테이블 (유저님 chat.html 스펙에 맞춤)
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
    
    # 그룹방 목록 테이블 (index.html에서 방 목록을 그리기 위함)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS group_rooms (
            room_id TEXT PRIMARY KEY,
            room_name TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

# 앱 실행 시 자동으로 테이블들을 세팅합니다.
try:
    init_db()
except Exception as e:
    print(f"[DB Init Warning] 테이블 초기화 실패 (DATABASE_URL 확인 필요): {e}")

# 유튜브 변환 헬퍼 함수
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)


# --- [ 라우팅 핵심 로직 ] ---

# 1. 홈 화면 (index.html 연동)
@app.route('/')
def home():
    user = session.get('user')
    # 로그인 세션이 없으면 로그인 폼이 있는 메인화면을 보여줍니다.
    if not user:
        return render_template('index.html', my_dms=[], my_rooms=[])
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 내가 대화한 이력이 있는 1:1 상대방 목록 추출 (my_dms 전달)
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
                
    # 개설된 그룹 단톡방 전체 목록 추출 (my_rooms 전달)
    cur.execute('SELECT room_id, room_name FROM group_rooms')
    my_rooms = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # 이 변수들을 넘겨주어야 index.html의 '개인 톡방 목록', '그룹 단톡방 목록'이 초록색으로 활성화됩니다!
    return render_template('index.html', my_dms=my_dms, my_rooms=my_rooms)


# 2. 회원가입 처리 (register.html 연동)
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
            session['user'] = username  # 가입 즉시 자동 로그인 성공 상태로 세션 부여
            return redirect(url_for('home'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>"
        finally:
            cur.close()
            conn.close()
    return render_template('register.html')


# 3. 로그인 처리 (login.html 및 index.html 내 폼 연동)
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
        session['user'] = username  # 세션에 확실하게 'user' 기록! (초록색 활성화 조건)
        return redirect(url_for('home'))
    else:
        return "<script>alert('아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"


# 4. 로그아웃
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))


# 5. 에스크 프로필 홈 (profile.html 연동)
@app.route('/user/<username>')
def profile(username):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 타겟 유저 정보 가져오기
    cur.execute('SELECT * FROM users WHERE username = %s', (username,))
    target_user = cur.fetchone()
    if not target_user:
        cur.close()
        conn.close()
        return "<script>alert('존재하지 않는 유저입니다.'); history.back();</script>", 404
        
    # 질문 리스트 조회
    cur.execute('SELECT * FROM asked WHERE target_user = %s ORDER BY id DESC', (username,))
    questions = cur.fetchall()
    
    # 팔로우 및 팔로잉 수 계산
    cur.execute('SELECT COUNT(*) FROM follows WHERE following_id = %s', (target_user['id'],))
    followers_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM follows WHERE follower_id = %s', (target_user['id'],))
    following_count = cur.fetchone()[0]
    
    # 로그인한 본인이 이 타겟 유저를 팔로우 중인지 확인
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
    
    # 질문과 답변 내용 내의 유튜브 링크 파싱 변환
    processed_questions = []
    for q in questions:
        q_dict = dict(q)
        q_dict['question'] = convert_youtube_links(q_dict['question'])
        q_dict['answer'] = convert_youtube_links(q_dict['answer'])
        processed_questions.append(q_dict)
        
    return render_template('profile.html', target_user=target_user, questions=processed_questions, 
                           followers_count=followers_count, following_count=following_count, is_following=is_following)


# 6. 프로필 정보 업데이트 (소개글, 사진)
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


# 7. 친구 ID 검색 기능
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


# 8. 팔로우 / 팔로우 취소 토글
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


# 9. 익명 질문 보내기 (Anonymous Question)
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


# 10. 익명 질문 답변하기
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


# 11. 익명 질문 삭제하기
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


# 12. 새 그룹 단톡방 만들기 (두 주소 규격 모두 완벽 수용)
@app.route('/create_group', methods=['GET', 'POST'])
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "<script>alert('방 이름을 입력해주세요.'); history.back();</script>"
            
        # 방 ID 규격화 처리
        room_id = f"group_{room_name.replace(' ', '_')}"
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO group_rooms (room_id, room_name) VALUES (%s, %s) ON CONFLICT DO NOTHING', (room_id, room_name))
            conn.commit()
        finally:
            cur.close()
            conn.close()
            
        # 개설 즉시 해당 그룹 채팅방으로 이동
        return redirect(url_for('chat_group', room_id=room_id))
    return render_template('create_group.html')


# 13. 1:1 개인 DM 톡방 렌더링 & POST 처리
@app.route('/chat/dm/<target_username>', methods=['GET', 'POST'])
def chat_dm(target_username):
    me = session.get('user')
    if not me:
        return redirect(url_for('home'))
        
    # 두 사람의 이름을 알파벳 순으로 정렬해 유일한 룸 ID 빌드
    room_id = "-".join(sorted([me, target_username]))
    
    # fetch POST 요청 발생 시 메시지 즉시 DB 저장
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


# 14. 그룹 단톡방 렌더링 & POST 처리
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
    
    # fetch POST 요청 발생 시 메시지 DB 저장
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


# 15. 비동기 실시간 채팅 데이터 송신 API (chat.html 요구 규격 준수)
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
