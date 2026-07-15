import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from psycopg2.extras import DictCursor

app = Flask(__name__)
app.secret_key = 'chatclub_ultra_secure_key_100_percent'

# 1. Neon PostgreSQL 연결 함수 (에러 로깅 강화)
def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        # 환경변수가 없을 경우에 대비해 예외를 던지지 않고 None을 리턴해 서버가 켜지게 만듭니다.
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor, connect_timeout=3)
        return conn
    except Exception as e:
        print(f"[DB Connection Error] Neon DB 연결 실패: {e}")
        return None

# 2. 데이터베이스 테이블 초기화 (최초 1회 안전하게 실행)
def init_db():
    conn = get_db_connection()
    if not conn:
        print("[DB Init Warning] DB 연결이 설정되지 않아 테이블 초기화를 건너뜁니다.")
        return
    try:
        cur = conn.cursor()
        
        # 회원 테이블
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
        
        # 채팅 메시지 테이블 (영구 보존)
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
    except Exception as e:
        print(f"[DB Init Error] 테이블 생성 중 예외 발생: {e}")
    finally:
        conn.close()

# 앱 실행 시 에러가 나더라도 백그라운드에서만 경고를 띄우고 앱은 계속 작동시킵니다.
try:
    init_db()
except Exception as e:
    print(f"[DB Auto-Init Failed] {e}")

# 유튜브 변환 헬퍼
def convert_youtube_links(text):
    if not text:
        return ""
    pattern = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11}))'
    replacement = r'<br><iframe width="100%" height="200" src="https://www.youtube.com/embed/\2" frameborder="0" allowfullscreen></iframe><br>'
    return re.sub(pattern, replacement, text)


# --- [ 라우팅 로직 ] ---

# 1. 홈 화면
@app.route('/')
def home():
    user = session.get('user')
    if not user:
        # 로그인 정보가 없으면 에러를 뿜지 않고 무조건 안전하게 로그인 페이지로 유도합니다.
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if not conn:
        # DB 연결 실패 시 에러 페이지 대신 빈 데이터로 index.html을 안전하게 열어줍니다.
        return render_template('index.html', my_dms=[], my_rooms=[], user=user, me_info=None)
        
    try:
        cur = conn.cursor()
        
        # 내 1:1 대화 상대방 목록
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
                    
        # 그룹 단톡방 목록
        cur.execute('SELECT room_id, room_name FROM group_rooms')
        my_rooms = cur.fetchall()
        
        # 로그인한 내 정보 추출 (index.html 렌더링에 필수)
        cur.execute('SELECT * FROM users WHERE username = %s', (user,))
        me_info = cur.fetchone()
        
        cur.close()
        conn.close()
        return render_template('index.html', my_dms=my_dms, my_rooms=my_rooms, user=user, me_info=me_info)
    except Exception as e:
        print(f"[Home Route Error] {e}")
        return render_template('index.html', my_dms=[], my_rooms=[], user=user, me_info=None)


# 2. 로그인 화면 (로그인 창이 안 뜨는 비상 상황을 완전 복구한 안전 장치)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        conn = get_db_connection()
        if not conn:
            return "<script>alert('데이터베이스 연결에 실패했습니다. Render 환경 변수(DATABASE_URL)를 확인해 주세요.'); history.back();</script>"
            
        try:
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
        except Exception as e:
            print(f"[Login Action Error] {e}")
            return f"<script>alert('로그인 처리 중 오류가 발생했습니다: {e}'); history.back();</script>"
            
    # GET 요청 시 무조건 안전하게 login.html 렌더링!
    try:
        return render_template('login.html')
    except Exception as e:
        return f"<h1>로그인 페이지 렌더링 실패: templates/login.html 파일이 존재하지 않거나 문법 에러가 있습니다.</h1><p>{e}</p>"


# 3. 회원가입 (register.html)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        conn = get_db_connection()
        if not conn:
            return "<script>alert('데이터베이스 연결 실패'); history.back();</script>"
            
        try:
            cur = conn.cursor()
            cur.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (username, password))
            conn.commit()
            session['user'] = username
            cur.close()
            conn.close()
            return redirect(url_for('home'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>"
        except Exception as e:
            return f"<script>alert('오류 발생: {e}'); history.back();</script>"
            
    return render_template('register.html')


# 4. 로그아웃
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))


# 5. 에스크 프로필 홈 (500 에러 완전 차단 예외 처리 추가)
@app.route('/user/<username>')
def profile(username):
    conn = get_db_connection()
    if not conn:
        return "<h1>데이터베이스 연결 실패. Neon DB 설정을 확인해 주세요.</h1>"
        
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username = %s', (username,))
        target_user = cur.fetchone()
        
        # 타겟 유저가 없는 경우 500 대신 404 에러 화면을 직접 제어
        if not target_user:
            cur.close()
            conn.close()
            return f"<h1>유저 '{username}'을(를) 찾을 수 없습니다.</h1><p><a href='/'>홈으로 돌아가기</a></p>", 404
            
        cur.execute('SELECT * FROM asked WHERE target_user = %s ORDER BY id DESC', (username,))
        questions = cur.fetchall()
        
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
            
        # user.html로 안전하게 렌더링
        return render_template('user.html', target_user=target_user, questions=processed_questions, 
                               followers_count=followers_count, following_count=following_count, is_following=is_following)
                               
    except Exception as e:
        print(f"[Profile Route Error] {e}")
        return f"<h1>프로필 로딩 중 오류가 발생했습니다: {e}</h1>"


# 6. 프로필 정보 업데이트
@app.route('/update_profile', methods=['POST'])
def update_profile():
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
    bio = request.form.get('bio', '').strip()
    
    file = request.files.get('profile_pic')
    profile_pic_url = ""
    
    conn = get_db_connection()
    if not conn:
        return "<script>alert('DB 연결 실패'); history.back();</script>"
        
    try:
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
    except Exception as e:
        print(f"[Update Profile Error] {e}")
        
    return redirect(url_for('profile', username=me))


# 7. 친구 ID 검색 기능
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', '').strip()
    conn = get_db_connection()
    if not conn:
        return render_template('search_results.html', users=[], query=query)
        
    try:
        cur = conn.cursor()
        cur.execute('SELECT username FROM users WHERE username LIKE %s', (f'%{query}%',))
        users = cur.fetchall()
        cur.close()
        conn.close()
        return render_template('search_results.html', users=users, query=query)
    except Exception as e:
        print(f"[Search Error] {e}")
        return render_template('search_results.html', users=[], query=query)


# 8. 팔로우 / 팔로우 취소 토글
@app.route('/follow/<username>', methods=['POST'])
def follow(username):
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if not conn:
        return redirect(url_for('profile', username=username))
        
    try:
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
    except Exception as e:
        print(f"[Follow Error] {e}")
        
    return redirect(url_for('profile', username=username))


# 9. 익명 질문 보내기
@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    question = request.form.get('question', '').strip()
    if not question:
        return "<script>alert('질문을 입력하세요.'); history.back();</script>"
        
    conn = get_db_connection()
    if not conn:
        return "<script>alert('DB 연결 실패'); history.back();</script>"
        
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO asked (target_user, question) VALUES (%s, %s)', (username, question))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Ask Error] {e}")
        
    return redirect(url_for('profile', username=username))


# 10. 익명 질문 답변하기
@app.route('/answer/<int:q_id>', methods=['POST'])
def answer(q_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
    answer_text = request.form.get('answer', '').strip()
    
    conn = get_db_connection()
    if not conn:
        return redirect(url_for('profile', username=me))
        
    try:
        cur = conn.cursor()
        cur.execute('UPDATE asked SET answer = %s WHERE id = %s AND target_user = %s', (answer_text, q_id, me))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Answer Error] {e}")
        
    return redirect(url_for('profile', username=me))


# 11. 익명 질문 삭제하기
@app.route('/delete_ask/<int:q_id>', methods=['POST'])
def delete_ask(q_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if not conn:
        return redirect(url_for('profile', username=me))
        
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM asked WHERE id = %s AND target_user = %s', (q_id, me))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Delete Ask Error] {e}")
        
    return redirect(url_for('profile', username=me))


# 12. 새 그룹 단톡방 만들기
@app.route('/create_group', methods=['GET', 'POST'])
@app.route('/group/create', methods=['GET', 'POST'])
def create_group():
    if request.method == 'POST':
        room_name = request.form.get('room_name', '').strip()
        if not room_name:
            return "<script>alert('방 이름을 입력해주세요.'); history.back();</script>"
            
        room_id = f"group_{room_name.replace(' ', '_')}"
        
        conn = get_db_connection()
        if not conn:
            return "<script>alert('DB 연결 실패'); history.back();</script>"
            
        try:
            cur = conn.cursor()
            cur.execute('INSERT INTO group_rooms (room_id, room_name) VALUES (%s, %s) ON CONFLICT DO NOTHING', (room_id, room_name))
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('chat_group', room_id=room_id))
        except Exception as e:
            print(f"[Create Group Error] {e}")
            return f"<script>alert('오류 발생: {e}'); history.back();</script>"
            
    return render_template('create_group.html')


# 13. 1:1 개인 DM 톡방 렌더링 & POST 처리
@app.route('/chat/dm/<target_username>', methods=['GET', 'POST'])
def chat_dm(target_username):
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
        
    room_id = "-".join(sorted([me, target_username]))
    
    if request.method == 'POST':
        msg_txt = request.form.get('message', '').strip()
        if msg_txt:
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute('INSERT INTO messages (room_type, room_id, sender, message) VALUES (%s, %s, %s, %s)',
                                ('dm', room_id, me, msg_txt))
                    conn.commit()
                    cur.close()
                    conn.close()
                except Exception as e:
                    print(f"[DM Post Error] {e}")
        return jsonify({'status': 'success'})
        
    return render_template('chat.html', room_type='dm', room_id=room_id, target_name=target_username)


# 14. 그룹 단톡방 렌더링 & POST 처리
@app.route('/group/chat/<room_id>', methods=['GET', 'POST'])
def chat_group(room_id):
    me = session.get('user')
    if not me:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    room_name = room_id
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('SELECT room_name FROM group_rooms WHERE room_id = %s', (room_id,))
            room_row = cur.fetchone()
            if room_row:
                room_name = room_row['room_name']
                
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
        except Exception as e:
            print(f"[Group Chat Error] {e}")
            
    return render_template('chat.html', room_type='group', room_id=room_id, target_name=room_name)


# 15. 비동기 실시간 채팅 데이터 송신 API
@app.route('/api/chat_history/<room_type>/<room_id>')
def get_chat_history(room_type, room_id):
    conn = get_db_connection()
    if not conn:
        return jsonify([])
        
    try:
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
    except Exception as e:
        print(f"[Chat History Api Error] {e}")
        return jsonify([])

if __name__ == '__main__':
    app.run(debug=True)
