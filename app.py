import os
import time
import json
import hmac
from functools import wraps

from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg
from psycopg.rows import dict_row

# ----------------------------------------------------------------
# 기본 설정
# ----------------------------------------------------------------
os.environ['TZ'] = 'Asia/Seoul'
try:
    time.tzset()
except AttributeError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-this-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL 환경 변수가 설정되지 않았습니다.')

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin1234')
DEFAULT_BIO = '안녕하세요! ChatClub입니다.'
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_DIR = os.path.join(app.root_path, 'static')
MAX_MESSAGE_LEN = 2000
LAST_SEEN_INTERVAL_SEC = 60


# ----------------------------------------------------------------
# 공통 헬퍼
# ----------------------------------------------------------------
def get_db_connection():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'Asia/Seoul'")
    return conn


def is_admin():
    return session.get('user') == ADMIN_USERNAME and session.get('role') == 'ADMIN'


def wants_json():
    if request.is_json:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.headers.get('Accept', '')
    return 'application/json' in accept and 'text/html' not in accept


def get_field(name, default=''):
    if request.is_json:
        data = request.get_json(silent=True) or {}
        value = data.get(name, default)
    else:
        value = request.form.get(name, default)
    if isinstance(value, str):
        return value.strip()
    return value


def js_alert(message, url=None, status=200):
    target = f"location.href={json.dumps(url)};" if url else "history.back();"
    return f"<script>alert({json.dumps(message)}); {target}</script>", status


def json_or_alert(success, message, url=None, status=200):
    if wants_json():
        return jsonify({'success': success, 'message': message}), status
    return js_alert(message, url, status)


def fmt_time(value):
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else None


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            if wants_json():
                return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return '관리자 권한이 없습니다.', 403
        return view(*args, **kwargs)
    return wrapper


# ----------------------------------------------------------------
# 데이터베이스 초기화
# ----------------------------------------------------------------
def init_db():
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(732914)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(50) PRIMARY KEY,
                password VARCHAR(255) NOT NULL,
                nickname VARCHAR(50) NOT NULL,
                bio VARCHAR(255) DEFAULT '안녕하세요! ChatClub입니다.',
                profile_img VARCHAR(255) DEFAULT 'default.png',
                is_active BOOLEAN DEFAULT TRUE,
                last_seen TIMESTAMP
            );
        """)
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS follows (
                column_id SERIAL PRIMARY KEY,
                follower VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                following VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                UNIQUE (follower, following)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS direct_messages (
                id SERIAL PRIMARY KEY,
                sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                receiver VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                message TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS deleted_by_sender BOOLEAN DEFAULT FALSE;")
        cur.execute("ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS deleted_by_receiver BOOLEAN DEFAULT FALSE;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_rooms (
                id SERIAL PRIMARY KEY,
                room_name VARCHAR(100) NOT NULL,
                created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS room_members (
                id SERIAL PRIMARY KEY,
                room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
                user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                UNIQUE (room_id, user_id)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS room_messages (
                id SERIAL PRIMARY KEY,
                room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
                sender VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS open_rooms (
                id SERIAL PRIMARY KEY,
                title VARCHAR(100) NOT NULL,
                created_by VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
                sub_host VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS open_messages (
                id SERIAL PRIMARY KEY,
                room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
                sender_anon VARCHAR(50) NOT NULL,
                sender_real_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS open_banned_users (
                id SERIAL PRIMARY KEY,
                room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
                username VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                UNIQUE (room_id, username)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ask_messages (
                id SERIAL PRIMARY KEY,
                target_user VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                content TEXT NOT NULL,
                answer TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_dm_pair ON direct_messages (sender, receiver);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dm_unread ON direct_messages (receiver, is_read);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_room_messages_room ON room_messages (room_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_open_messages_room ON open_messages (room_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ask_target ON ask_messages (target_user);")

        cur.execute("""
            INSERT INTO users (username, password, nickname, is_active, is_banned)
            VALUES (%s, %s, %s, TRUE, FALSE)
            ON CONFLICT (username) DO UPDATE
            SET password = EXCLUDED.password, is_active = TRUE, is_banned = FALSE
        """, (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), '최고관리자'))


init_db()


# ----------------------------------------------------------------
# 접속 상태 갱신 (요청마다 DB를 치지 않도록 60초 간격)
# ----------------------------------------------------------------
@app.before_request
def update_last_seen():
    if request.endpoint in (None, 'static'):
        return
    user = session.get('user')
    if not user or is_admin():
        return
    now = time.time()
    if now - session.get('_last_seen_ping', 0) < LAST_SEEN_INTERVAL_SEC:
        return
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET last_seen = CURRENT_TIMESTAMP
                WHERE username = %s AND is_active = TRUE AND COALESCE(is_banned, FALSE) = FALSE
                RETURNING username
            """, (user,))
            if cur.fetchone() is None:
                session.clear()
                return
        session['_last_seen_ping'] = now
    except psycopg.Error as e:
        app.logger.warning(f'last_seen 갱신 실패: {e}')


@app.errorhandler(413)
def too_large(_e):
    return js_alert('업로드 파일은 5MB 이하만 가능합니다.', status=413)


# ----------------------------------------------------------------
# 메인 대시보드
# ----------------------------------------------------------------
@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    dm_list = []
    unread_total = 0

    if not user:
        return render_template('index.html', my_rooms=my_rooms, all_users=all_users,
                               dm_list=dm_list, unread_total=unread_total, user=user)

    with get_db_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute("""
                SELECT cr.id, cr.room_name, cr.created_by,
                       (SELECT COUNT(*) FROM room_members WHERE room_id = cr.id) AS member_count
                FROM chat_rooms cr
                JOIN room_members rm ON cr.id = rm.room_id
                WHERE rm.user_id = %s
                ORDER BY cr.id DESC
            """, (user,))
            my_rooms = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'단톡방 목록 오류: {e}')

        try:
            cur.execute("""
                SELECT u.username, u.nickname, u.profile_img, u.bio,
                       COALESCE(u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes', FALSE) AS is_online
                FROM follows f
                JOIN users u ON u.username = f.following
                WHERE f.follower = %s AND u.is_active = TRUE
                ORDER BY is_online DESC, u.nickname ASC
            """, (user,))
            all_users = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'친구 목록 오류: {e}')

        try:
            cur.execute("""
                WITH my_dm AS (
                    SELECT id, sender, receiver, is_read, created_at,
                           CASE WHEN sender = %(me)s THEN receiver ELSE sender END AS partner_id
                    FROM direct_messages
                    WHERE (sender = %(me)s AND COALESCE(deleted_by_sender, FALSE) = FALSE)
                       OR (receiver = %(me)s AND COALESCE(deleted_by_receiver, FALSE) = FALSE)
                ),
                agg AS (
                    SELECT partner_id,
                           COUNT(*) FILTER (WHERE receiver = %(me)s AND is_read = FALSE) AS unread_count,
                           MAX(created_at) AS last_msg_time,
                           MAX(id) AS last_msg_id
                    FROM my_dm
                    GROUP BY partner_id
                )
                SELECT u.username, u.nickname, u.profile_img,
                       (f.column_id IS NOT NULL) AS is_following,
                       COALESCE(u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes', FALSE) AS is_online,
                       a.unread_count, a.last_msg_time,
                       (SELECT message FROM direct_messages WHERE id = a.last_msg_id) AS last_message
                FROM agg a
                JOIN users u ON u.username = a.partner_id AND u.is_active = TRUE
                LEFT JOIN follows f ON f.follower = %(me)s AND f.following = u.username
                ORDER BY a.unread_count DESC, a.last_msg_time DESC
            """, {'me': user})
            dm_list = cur.fetchall()
            unread_total = sum(int(item['unread_count'] or 0) for item in dm_list)
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'DM 목록 오류: {e}')

    return render_template('index.html', my_rooms=my_rooms, all_users=all_users,
                           dm_list=dm_list, unread_total=unread_total, user=user)


# ----------------------------------------------------------------
# 회원 관리 & 인증
# ----------------------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    nickname = request.form.get('nickname', '').strip()

    if not username or not password or not nickname:
        return js_alert('모든 필드를 입력해주세요.', status=400)
    if len(username) > 50 or len(nickname) > 50:
        return js_alert('아이디와 닉네임은 50자 이하로 입력해주세요.', status=400)
    if username.lower() == ADMIN_USERNAME:
        return js_alert('사용할 수 없는 아이디입니다.', status=400)
    if len(password) < 4:
        return js_alert('비밀번호는 4자 이상이어야 합니다.', status=400)

    hashed_password = generate_password_hash(password)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT username, is_active, COALESCE(is_banned, FALSE) AS is_banned
            FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s))
        """, (username,))
        existing = cur.fetchone()

        if existing:
            if existing['is_banned']:
                return js_alert('관리자에 의해 영구 차단된 아이디입니다.', status=403)
            if existing['is_active']:
                return js_alert('이미 존재하는 아이디입니다.', status=400)
            cur.execute("""
                UPDATE users
                SET password = %s, nickname = %s, bio = DEFAULT, profile_img = 'default.png',
                    is_active = TRUE, last_seen = CURRENT_TIMESTAMP
                WHERE username = %s
            """, (hashed_password, nickname, existing['username']))
            username = existing['username']
        else:
            cur.execute("""
                INSERT INTO users (username, password, nickname, last_seen)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            """, (username, hashed_password, nickname))

    session.clear()
    session['user'] = username
    session['role'] = 'USER'
    return redirect(url_for('index'))


def verify_password(stored, given):
    try:
        if check_password_hash(stored, given):
            return True, False
    except (ValueError, TypeError):
        pass
    if stored and hmac.compare_digest(stored.encode('utf-8'), given.encode('utf-8')):
        return True, True
    return False, False


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    fail_msg = '아이디 또는 비밀번호가 잘못되었거나 탈퇴한 회원입니다.'

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT username, password, is_active, COALESCE(is_banned, FALSE) AS is_banned
            FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s))
        """, (username,))
        row = cur.fetchone()

        if not row:
            return js_alert(fail_msg, status=401)

        ok, is_plain = verify_password(row['password'], password)
        if not ok:
            return js_alert(fail_msg, status=401)

        if row['username'] == ADMIN_USERNAME:
            session.clear()
            session['user'] = ADMIN_USERNAME
            session['role'] = 'ADMIN'
            return js_alert('👑 최고 관리자 모드로 로그인되었습니다.', url_for('admin_dashboard'))

        if row['is_banned']:
            return js_alert('관리자에 의해 영구 차단된 계정입니다.', status=403)
        if not row['is_active']:
            return js_alert(fail_msg, status=401)

        if is_plain:
            cur.execute("UPDATE users SET password = %s WHERE username = %s",
                        (generate_password_hash(password), row['username']))
        cur.execute("UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = %s", (row['username'],))

    session.clear()
    session['user'] = row['username']
    session['role'] = 'USER'
    session['_last_seen_ping'] = time.time()
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    user = session['user']
    if user == ADMIN_USERNAME:
        return js_alert('관리자 계정은 탈퇴할 수 없습니다.', status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET is_active = FALSE WHERE username = %s", (user,))
        cur.execute("DELETE FROM follows WHERE follower = %s OR following = %s", (user, user))
        cur.execute("DELETE FROM room_members WHERE user_id = %s", (user,))
        cur.execute("UPDATE open_rooms SET sub_host = NULL WHERE sub_host = %s", (user,))
        cur.execute("""
            DELETE FROM chat_rooms cr
            WHERE NOT EXISTS (SELECT 1 FROM room_members rm WHERE rm.room_id = cr.id)
        """)

    session.clear()
    return js_alert('회원 탈퇴가 완료되었습니다.', url_for('index'))


# ----------------------------------------------------------------
# 1:1 DM
# ----------------------------------------------------------------
@app.route('/chat/dm/<username>', methods=['GET', 'POST'])
@login_required
def dm_chat(username):
    my_id = session['user']
    if username == my_id:
        return js_alert('자기 자신에게는 메시지를 보낼 수 없습니다.', url_for('index'))

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT username, nickname, profile_img, last_seen,
                   COALESCE(last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes', FALSE) AS is_online
            FROM users WHERE username = %s AND is_active = TRUE
        """, (username,))
        receiver = cur.fetchone()
        if not receiver:
            return '존재하지 않거나 탈퇴한 회원입니다.', 404

        if request.method == 'POST':
            message = get_field('message')
            if not message:
                if wants_json():
                    return jsonify({'success': False, 'message': '메시지를 입력해주세요.'}), 400
                return redirect(url_for('dm_chat', username=username))
            if len(message) > MAX_MESSAGE_LEN:
                return json_or_alert(False, f'메시지는 {MAX_MESSAGE_LEN}자 이하로 입력해주세요.', status=400)

            cur.execute("""
                INSERT INTO direct_messages (sender, receiver, message)
                VALUES (%s, %s, %s) RETURNING id, created_at
            """, (my_id, username, message))
            row = cur.fetchone()
            if wants_json():
                return jsonify({'success': True, 'id': row['id'], 'sender': my_id,
                                'message': message, 'created_at': fmt_time(row['created_at'])})
            return redirect(url_for('dm_chat', username=username))

        cur.execute("""
            UPDATE direct_messages SET is_read = TRUE
            WHERE sender = %s AND receiver = %s AND is_read = FALSE
        """, (username, my_id))

        cur.execute("""
            SELECT id, sender, receiver, message, created_at, is_read
            FROM direct_messages
            WHERE (sender = %(me)s AND receiver = %(other)s AND COALESCE(deleted_by_sender, FALSE) = FALSE)
               OR (sender = %(other)s AND receiver = %(me)s AND COALESCE(deleted_by_receiver, FALSE) = FALSE)
            ORDER BY id ASC
        """, {'me': my_id, 'other': username})
        messages = cur.fetchall()

    return render_template('dm.html', receiver=receiver, messages=messages, my_id=my_id)


@app.route('/chat/dm/<username>/delete/<int:message_id>', methods=['POST'])
@login_required
def dm_delete_message(username, message_id):
    my_id = session['user']
    mode = get_field('mode', 'me') or 'me'
    redirect_url = url_for('dm_chat', username=username)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, sender, receiver FROM direct_messages
            WHERE id = %s AND ((sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s))
        """, (message_id, my_id, username, username, my_id))
        msg = cur.fetchone()
        if not msg:
            return json_or_alert(False, '존재하지 않는 메시지입니다.', redirect_url, 404)

        if mode == 'all':
            if msg['sender'] != my_id and not is_admin():
                return json_or_alert(False, '내가 보낸 메시지만 모두에게서 삭제할 수 있습니다.', redirect_url, 403)
            cur.execute("DELETE FROM direct_messages WHERE id = %s", (message_id,))
        else:
            if msg['sender'] == my_id:
                cur.execute("UPDATE direct_messages SET deleted_by_sender = TRUE WHERE id = %s", (message_id,))
            else:
                cur.execute("UPDATE direct_messages SET deleted_by_receiver = TRUE WHERE id = %s", (message_id,))
            cur.execute("""
                DELETE FROM direct_messages
                WHERE id = %s AND deleted_by_sender = TRUE AND deleted_by_receiver = TRUE
            """, (message_id,))

    if wants_json():
        return jsonify({'success': True, 'message': '메시지가 삭제되었습니다.', 'mode': mode})
    return redirect(redirect_url)


@app.route('/chat/dm/<username>/clear', methods=['POST'])
@login_required
def dm_clear_conversation(username):
    my_id = session['user']
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE direct_messages SET deleted_by_sender = TRUE
            WHERE sender = %s AND receiver = %s
        """, (my_id, username))
        cur.execute("""
            UPDATE direct_messages SET deleted_by_receiver = TRUE, is_read = TRUE
            WHERE sender = %s AND receiver = %s
        """, (username, my_id))
        cur.execute("""
            DELETE FROM direct_messages
            WHERE ((sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s))
              AND deleted_by_sender = TRUE AND deleted_by_receiver = TRUE
        """, (my_id, username, username, my_id))

    if wants_json():
        return jsonify({'success': True, 'message': '대화방이 내 목록에서 삭제되었습니다.'})
    return redirect(url_for('index'))


# ----------------------------------------------------------------
# 일반 단톡방
# ----------------------------------------------------------------
@app.route('/group/create', methods=['GET', 'POST'])
@login_required
def create_group():
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        if request.method == 'POST':
            room_name = request.form.get('room_name', '').strip()
            invited_users = request.form.getlist('invited_users')

            if not room_name:
                return js_alert('방 이름을 입력해주세요.', status=400)
            if len(room_name) > 100:
                return js_alert('방 이름은 100자 이하로 입력해주세요.', status=400)

            cur.execute("""
                SELECT u.username FROM follows f
                JOIN users u ON u.username = f.following
                WHERE f.follower = %s AND u.is_active = TRUE AND u.username = ANY(%s)
            """, (user, invited_users))
            valid_invites = [r['username'] for r in cur.fetchall() if r['username'] != user]

            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id",
                        (room_name, user))
            room_id = cur.fetchone()['id']

            cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (room_id, user))
            for invited in valid_invites:
                cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (room_id, invited))

            return redirect(url_for('group_chat', room_id=room_id))

        cur.execute("""
            SELECT u.username, u.nickname, u.profile_img
            FROM follows f
            JOIN users u ON f.following = u.username
            WHERE f.follower = %s AND u.is_active = TRUE
            ORDER BY u.nickname ASC
        """, (user,))
        user_list = cur.fetchall()

    return render_template('create_group.html', user_list=user_list)


@app.route('/group/chat/<int:room_id>', methods=['GET', 'POST'])
@login_required
def group_chat(room_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, room_name, created_by FROM chat_rooms WHERE id = %s", (room_id,))
        room = cur.fetchone()
        if not room:
            return '존재하지 않는 단톡방입니다.', 404

        cur.execute("SELECT 1 FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        is_member = cur.fetchone() is not None
        if not is_member and not is_admin():
            return '❌ 이 단톡방에 초대받지 않았습니다. 입장 권한이 없습니다!', 403

        if request.method == 'POST':
            if not is_member:
                return json_or_alert(False, '멤버만 메시지를 보낼 수 있습니다.', status=403)
            message = get_field('message')
            if message:
                if len(message) > MAX_MESSAGE_LEN:
                    return json_or_alert(False, f'메시지는 {MAX_MESSAGE_LEN}자 이하로 입력해주세요.', status=400)
                cur.execute("""
                    INSERT INTO room_messages (room_id, sender, message)
                    VALUES (%s, %s, %s) RETURNING id, created_at
                """, (room_id, user, message))
                row = cur.fetchone()
                if wants_json():
                    return jsonify({'success': True, 'id': row['id'], 'sender': user,
                                    'message': message, 'created_at': fmt_time(row['created_at'])})
            return redirect(url_for('group_chat', room_id=room_id))

        cur.execute("""
            SELECT m.id, m.sender, u.nickname, u.profile_img, m.message, m.created_at
            FROM room_messages m
            LEFT JOIN users u ON u.username = m.sender
            WHERE m.room_id = %s
            ORDER BY m.id ASC
        """, (room_id,))
        messages = cur.fetchall()

        cur.execute("""
            SELECT u.username, u.nickname, u.profile_img
            FROM room_members rm
            JOIN users u ON u.username = rm.user_id
            WHERE rm.room_id = %s
            ORDER BY rm.id ASC
        """, (room_id,))
        members = cur.fetchall()

    return render_template('chat.html', room=room, room_id=room_id, messages=messages,
                           members=members, my_id=user, is_owner=(room['created_by'] == user))


@app.route('/group/leave/<int:room_id>', methods=['POST'])
@login_required
def leave_group(room_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, created_by FROM chat_rooms WHERE id = %s FOR UPDATE", (room_id,))
        room = cur.fetchone()
        if not room:
            return js_alert('존재하지 않는 단톡방입니다.', url_for('index'), 404)

        cur.execute("SELECT 1 FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        if not cur.fetchone():
            return js_alert('이 방의 멤버가 아닙니다.', url_for('index'), 400)

        cur.execute("SELECT nickname FROM users WHERE username = %s", (user,))
        info = cur.fetchone()
        nickname = info['nickname'] if info else user

        cur.execute("DELETE FROM room_members WHERE room_id = %s AND user_id = %s", (room_id, user))

        cur.execute("SELECT user_id FROM room_members WHERE room_id = %s ORDER BY id ASC LIMIT 1", (room_id,))
        next_member = cur.fetchone()

        if not next_member:
            cur.execute("DELETE FROM chat_rooms WHERE id = %s", (room_id,))
        else:
            cur.execute("INSERT INTO room_messages (room_id, sender, message) VALUES (%s, %s, %s)",
                        (room_id, user, f'📢 {nickname}님이 퇴장하셨습니다.'))
            if room['created_by'] == user:
                cur.execute("UPDATE chat_rooms SET created_by = %s WHERE id = %s",
                            (next_member['user_id'], room_id))

    return redirect(url_for('index'))


@app.route('/my_chats')
def my_joined_rooms():
    current_user = session.get('user')
    if not current_user:
        flash('로그인이 필요한 서비스입니다.')
        return redirect(url_for('login'))

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT r.id, r.room_name, r.created_by,
                   (SELECT COUNT(*) FROM room_members WHERE room_id = r.id) AS member_count
            FROM chat_rooms r
            JOIN room_members m ON r.id = m.room_id
            WHERE m.user_id = %s
            ORDER BY r.id DESC
        """, (current_user,))
        my_rooms = cur.fetchall()

    return render_template('my_chats.html', rooms=my_rooms, current_user=current_user)


# ----------------------------------------------------------------
# 검색 / 프로필 / 팔로우 / 익명 질문
# ----------------------------------------------------------------
@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    results = []
    if query:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT username, nickname, profile_img, bio FROM users
                WHERE (username ILIKE %s OR nickname ILIKE %s)
                  AND is_active = TRUE AND username <> %s
                ORDER BY username ASC
                LIMIT 50
            """, (f'%{query}%', f'%{query}%', ADMIN_USERNAME))
            results = cur.fetchall()
    return render_template('search_results.html', query=query, results=results)


@app.route('/user/<username>')
def user_profile(username):
    me = session.get('user')

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT username, nickname, bio, profile_img, last_seen,
                   COALESCE(last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes', FALSE) AS is_online
            FROM users WHERE username = %s AND is_active = TRUE
        """, (username,))
        profile_user = cur.fetchone()
        if not profile_user:
            return '존재하지 않거나 탈퇴한 유저입니다.', 404

        if me == username:
            cur.execute("UPDATE ask_messages SET is_read = TRUE WHERE target_user = %s AND is_read = FALSE",
                        (username,))

        cur.execute("SELECT COUNT(*) AS cnt FROM follows WHERE following = %s", (username,))
        followers_count = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM follows WHERE follower = %s", (username,))
        following_count = cur.fetchone()['cnt']

        is_following = False
        if me:
            cur.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (me, username))
            is_following = cur.fetchone() is not None

        cur.execute("""
            SELECT id, target_user, content, answer, is_read, created_at
            FROM ask_messages WHERE target_user = %s ORDER BY id DESC
        """, (username,))
        messages = cur.fetchall()

    return render_template('user.html', profile_user=profile_user, followers_count=followers_count,
                           following_count=following_count, is_following=is_following,
                           messages=messages, is_me=(me == username))


@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    user = session['user']
    bio = request.form.get('bio', '').strip()[:255]
    nickname = request.form.get('nickname', '').strip()[:50]
    profile_img = request.files.get('profile_img')

    new_filename = None
    if profile_img and profile_img.filename:
        if not allowed_image(profile_img.filename):
            return js_alert('이미지 파일(png, jpg, jpeg, gif, webp)만 업로드할 수 있습니다.', status=400)
        safe_name = secure_filename(profile_img.filename) or 'profile.png'
        ext = safe_name.rsplit('.', 1)[-1].lower()
        new_filename = f"{secure_filename(user) or 'user'}_{int(time.time())}.{ext}"
        try:
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            profile_img.save(os.path.join(UPLOAD_DIR, new_filename))
        except OSError as e:
            app.logger.error(f'프로필 이미지 저장 실패: {e}')
            new_filename = None

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET bio = %s WHERE username = %s", (bio, user))
        if nickname:
            cur.execute("UPDATE users SET nickname = %s WHERE username = %s", (nickname, user))
        if new_filename:
            cur.execute("UPDATE users SET profile_img = %s WHERE username = %s", (new_filename, user))

    return redirect(url_for('user_profile', username=user))


@app.route('/ask/<username>', methods=['POST'])
def ask(username):
    content = request.form.get('content', '').strip()
    if not content:
        return redirect(url_for('user_profile', username=username))
    if len(content) > 1000:
        return js_alert('질문은 1000자 이하로 입력해주세요.', status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username = %s AND is_active = TRUE", (username,))
        if not cur.fetchone():
            return '존재하지 않거나 탈퇴한 유저입니다.', 404
        cur.execute("INSERT INTO ask_messages (target_user, content) VALUES (%s, %s)", (username, content))

    return js_alert('익명 질문이 전송되었습니다.', url_for('user_profile', username=username))


@app.route('/answer/<int:msg_id>', methods=['POST'])
@login_required
def answer(msg_id):
    user = session['user']
    answer_text = request.form.get('answer', '').strip()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE ask_messages SET answer = %s WHERE id = %s AND target_user = %s",
                    (answer_text or None, msg_id, user))

    return redirect(url_for('user_profile', username=user))


@app.route('/follow/<username>', methods=['POST'])
@login_required
def follow(username):
    user = session['user']
    if username == user or username == ADMIN_USERNAME:
        return js_alert('팔로우할 수 없는 대상입니다.', status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username = %s AND is_active = TRUE", (username,))
        if not cur.fetchone():
            return '존재하지 않거나 탈퇴한 유저입니다.', 404

        cur.execute("SELECT 1 FROM follows WHERE follower = %s AND following = %s", (user, username))
        if cur.fetchone():
            cur.execute("DELETE FROM follows WHERE follower = %s AND following = %s", (user, username))
            following_now = False
        else:
            cur.execute("INSERT INTO follows (follower, following) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (user, username))
            following_now = True

    if wants_json():
        return jsonify({'success': True, 'is_following': following_now})
    return redirect(url_for('user_profile', username=username))


# ----------------------------------------------------------------
# 오픈채팅
# ----------------------------------------------------------------
def get_open_room(cur, room_id):
    cur.execute("SELECT id, title, created_by, sub_host, created_at FROM open_rooms WHERE id = %s", (room_id,))
    return cur.fetchone()


def is_open_banned(cur, room_id, username):
    cur.execute("SELECT 1 FROM open_banned_users WHERE room_id = %s AND username = %s", (room_id, username))
    return cur.fetchone() is not None


@app.route('/open_chat_list')
@login_required
def open_chat_list():
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT r.id, r.title, r.created_by, r.sub_host, r.created_at,
                   (SELECT COUNT(DISTINCT sender_real_id) FROM open_messages WHERE room_id = r.id) AS participant_count
            FROM open_rooms r
            ORDER BY r.created_at DESC
        """)
        all_rooms = cur.fetchall()

        cur.execute("""
            SELECT r.id, r.title, r.created_by, r.sub_host, r.created_at
            FROM open_rooms r
            WHERE (r.created_by = %(me)s
                   OR EXISTS (SELECT 1 FROM open_messages m WHERE m.room_id = r.id AND m.sender_real_id = %(me)s))
              AND NOT EXISTS (SELECT 1 FROM open_banned_users b WHERE b.room_id = r.id AND b.username = %(me)s)
            ORDER BY r.created_at DESC
        """, {'me': user})
        my_open_rooms = cur.fetchall()

    return render_template('open_room_list.html', all_rooms=all_rooms, my_open_rooms=my_open_rooms)


@app.route('/create_open_room', methods=['POST'])
@login_required
def create_open_room():
    user = session['user']
    room_title = request.form.get('room_title', '').strip()

    if not room_title:
        return js_alert('방 제목을 입력해주세요.', status=400)
    if len(room_title) > 100:
        return js_alert('방 제목은 100자 이하로 입력해주세요.', status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO open_rooms (title, created_by) VALUES (%s, %s) RETURNING id", (room_title, user))
        new_room_id = cur.fetchone()['id']

    return redirect(url_for('open_chat_room', room_id=new_room_id))


@app.route('/open_chat/room/<int:room_id>', methods=['GET', 'POST'])
@login_required
def open_chat_room(room_id):
    user = session['user']
    session_key = f'anon_name_{room_id}'

    with get_db_connection() as conn, conn.cursor() as cur:
        room = get_open_room(cur, room_id)
        if not room:
            return '존재하지 않는 방입니다.', 404

        if not is_admin() and is_open_banned(cur, room_id, user):
            return js_alert('해당 방장 또는 부방장에 의해 강퇴 처리되어 입장할 수 없습니다.', url_for('open_chat_list'))

        is_host = (room['created_by'] == user or is_admin())
        is_sub_host = (room['sub_host'] == user)

        if request.method == 'POST':
            custom_name = request.form.get('custom_name', '').strip()
            if not custom_name:
                return js_alert('닉네임을 입력해주세요.', status=400)
            if len(custom_name) > 50:
                return js_alert('닉네임은 50자 이하로 입력해주세요.', status=400)
            cur.execute("""
                SELECT 1 FROM open_messages
                WHERE room_id = %s AND sender_anon = %s AND sender_real_id <> %s
                LIMIT 1
            """, (room_id, custom_name, user))
            if cur.fetchone():
                return js_alert('이 방에서 이미 다른 사람이 사용 중인 닉네임입니다.', status=400)
            session[session_key] = custom_name
            return redirect(url_for('open_chat_room', room_id=room_id))

        anon_name = session.get(session_key)
        if not anon_name:
            cur.execute("""
                SELECT sender_anon FROM open_messages
                WHERE room_id = %s AND sender_real_id = %s
                ORDER BY id DESC LIMIT 1
            """, (room_id, user))
            prev_msg = cur.fetchone()
            if prev_msg:
                anon_name = prev_msg['sender_anon']
                session[session_key] = anon_name

        if not anon_name:
            return render_template('open_chat.html', room=room, messages=[], anon_name=None,
                                   is_host=is_host, is_sub_host=is_sub_host,
                                   banned_users=[], participants=[], my_id=user)

        cur.execute("""
            SELECT id, sender_anon, sender_real_id, message, created_at FROM (
                SELECT id, sender_anon, sender_real_id, message, created_at
                FROM open_messages WHERE room_id = %s
                ORDER BY id DESC LIMIT 100
            ) recent
            ORDER BY id ASC
        """, (room_id,))
        messages = cur.fetchall()

        banned_users = []
        participants = []
        if is_host or is_sub_host:
            cur.execute("""
                SELECT b.username, u.nickname FROM open_banned_users b
                LEFT JOIN users u ON u.username = b.username
                WHERE b.room_id = %s ORDER BY b.id DESC
            """, (room_id,))
            banned_users = cur.fetchall()

            cur.execute("""
                SELECT DISTINCT ON (sender_real_id) sender_real_id, sender_anon
                FROM open_messages
                WHERE room_id = %s AND sender_real_id IS NOT NULL
                ORDER BY sender_real_id, id DESC
            """, (room_id,))
            participants = cur.fetchall()

    return render_template('open_chat.html', room=room, messages=messages, anon_name=anon_name,
                           is_host=is_host, is_sub_host=is_sub_host,
                           banned_users=banned_users, participants=participants, my_id=user)


@app.route('/send_open_message/<int:room_id>', methods=['POST'])
@login_required
def send_open_message(room_id):
    user = session['user']
    message = get_field('message')
    anon_name = session.get(f'anon_name_{room_id}')
    room_url = url_for('open_chat_room', room_id=room_id)

    if not anon_name:
        return json_or_alert(False, '먼저 닉네임을 설정해주세요.', room_url, 400)
    if not message:
        if wants_json():
            return jsonify({'success': False, 'message': '메시지를 입력해주세요.'}), 400
        return redirect(room_url)
    if len(message) > MAX_MESSAGE_LEN:
        return json_or_alert(False, f'메시지는 {MAX_MESSAGE_LEN}자 이하로 입력해주세요.', room_url, 400)

    with get_db_connection() as conn, conn.cursor() as cur:
        if not get_open_room(cur, room_id):
            return json_or_alert(False, '존재하지 않는 방입니다.', url_for('open_chat_list'), 404)
        if not is_admin() and is_open_banned(cur, room_id, user):
            return json_or_alert(False, '채팅 권한이 없습니다.', url_for('open_chat_list'), 403)

        cur.execute("""
            INSERT INTO open_messages (room_id, sender_anon, sender_real_id, message)
            VALUES (%s, %s, %s, %s) RETURNING id, created_at
        """, (room_id, anon_name, user, message))
        row = cur.fetchone()

    if wants_json():
        return jsonify({'success': True, 'id': row['id'], 'sender_anon': anon_name,
                        'message': message, 'created_at': fmt_time(row['created_at'])})
    return redirect(room_url)


@app.route('/open_chat/room/<int:room_id>/set_sub', methods=['POST'])
@login_required
def open_chat_set_sub(room_id):
    user = session['user']
    target_user = request.form.get('target_user', '').strip()
    action = request.form.get('action', '').strip()
    room_url = url_for('open_chat_room', room_id=room_id)

    with get_db_connection() as conn, conn.cursor() as cur:
        room = get_open_room(cur, room_id)
        if not room:
            return '존재하지 않는 방입니다.', 404
        if room['created_by'] != user and not is_admin():
            return js_alert('방장만 부방장을 지정할 수 있습니다.', room_url, 403)

        if action == 'appoint':
            if not target_user or target_user == room['created_by']:
                return js_alert('부방장으로 지정할 수 없는 대상입니다.', room_url, 400)
            cur.execute("SELECT 1 FROM users WHERE username = %s AND is_active = TRUE", (target_user,))
            if not cur.fetchone():
                return js_alert('존재하지 않는 유저입니다.', room_url, 404)
            if is_open_banned(cur, room_id, target_user):
                return js_alert('차단된 유저는 부방장이 될 수 없습니다.', room_url, 400)
            cur.execute("UPDATE open_rooms SET sub_host = %s WHERE id = %s", (target_user, room_id))
            return js_alert('부방장이 지정되었습니다.', room_url)

        if action == 'dismiss':
            cur.execute("UPDATE open_rooms SET sub_host = NULL WHERE id = %s", (room_id,))
            return js_alert('부방장이 해제되었습니다.', room_url)

    return js_alert('잘못된 요청입니다.', room_url, 400)


@app.route('/open_chat/room/<int:room_id>/ban', methods=['POST'])
@login_required
def open_chat_ban_user(room_id):
    user = session['user']
    target_user = request.form.get('target_user', '').strip()
    room_url = url_for('open_chat_room', room_id=room_id)

    with get_db_connection() as conn, conn.cursor() as cur:
        room = get_open_room(cur, room_id)
        if not room:
            return '방이 존재하지 않습니다.', 404

        is_host = (room['created_by'] == user or is_admin())
        is_sub_host = (room['sub_host'] == user)

        if not (is_host or is_sub_host):
            return js_alert('강퇴 권한이 없습니다.', room_url, 403)
        if not target_user or target_user == user:
            return js_alert('강퇴할 수 없는 대상입니다.', room_url, 400)
        if target_user == ADMIN_USERNAME:
            return js_alert('관리자는 강퇴할 수 없습니다.', room_url, 403)
        if target_user == room['created_by']:
            return js_alert('방장은 강퇴할 수 없습니다.', room_url, 403)

        cur.execute("SELECT 1 FROM users WHERE username = %s", (target_user,))
        if not cur.fetchone():
            return js_alert('존재하지 않는 유저입니다.', room_url, 404)

        cur.execute("""
            INSERT INTO open_banned_users (room_id, username) VALUES (%s, %s)
            ON CONFLICT (room_id, username) DO NOTHING
        """, (room_id, target_user))
        cur.execute("DELETE FROM open_messages WHERE room_id = %s AND sender_real_id = %s", (room_id, target_user))
        if room['sub_host'] == target_user:
            cur.execute("UPDATE open_rooms SET sub_host = NULL WHERE id = %s", (room_id,))

    return js_alert('해당 유저를 강퇴 및 차단했습니다.', room_url)


@app.route('/open_chat/room/<int:room_id>/unban', methods=['POST'])
@login_required
def open_chat_unban_user(room_id):
    user = session['user']
    target_user = request.form.get('target_user', '').strip()
    room_url = url_for('open_chat_room', room_id=room_id)

    with get_db_connection() as conn, conn.cursor() as cur:
        room = get_open_room(cur, room_id)
        if not room:
            return '방이 존재하지 않습니다.', 404
        if room['created_by'] != user and not is_admin():
            return js_alert('방장만 차단을 해제할 수 있습니다.', room_url, 403)
        cur.execute("DELETE FROM open_banned_users WHERE room_id = %s AND username = %s", (room_id, target_user))

    return js_alert('차단이 해제되었습니다.', room_url)


@app.route('/open_chat/room/<int:room_id>/delete', methods=['POST'])
@login_required
def delete_open_room(room_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        room = get_open_room(cur, room_id)
        if not room:
            return '존재하지 않는 방입니다.', 404
        if not (is_admin() or room['created_by'] == user):
            return js_alert('삭제 권한이 없습니다.', status=403)
        cur.execute("DELETE FROM open_rooms WHERE id = %s", (room_id,))

    redirect_url = url_for('admin_dashboard') if is_admin() else url_for('open_chat_list')
    return js_alert('오픈채팅방이 성공적으로 삭제되었습니다.', redirect_url)


# ----------------------------------------------------------------
# 최고 관리자 MASTER PANEL
# ----------------------------------------------------------------
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    user = session.get('user')
    role = session.get('role')
    all_users = []
    open_rooms = []
    group_rooms = []
    stats = {}

    with get_db_connection() as conn, conn.cursor() as cur:
        try:
            cur.execute("""
                SELECT username, nickname, is_active, COALESCE(is_banned, FALSE) AS is_banned, last_seen,
                       COALESCE(last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes', FALSE) AS is_online
                FROM users WHERE username <> %s
                ORDER BY username ASC
            """, (ADMIN_USERNAME,))
            all_users = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'관리자 유저 목록 오류: {e}')

        try:
            cur.execute("""
                SELECT cr.id, cr.room_name, cr.created_by,
                       (SELECT COUNT(*) FROM room_members WHERE room_id = cr.id) AS member_count,
                       (SELECT COUNT(*) FROM room_messages WHERE room_id = cr.id) AS message_count
                FROM chat_rooms cr ORDER BY cr.id DESC
            """)
            group_rooms = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'관리자 단톡방 목록 오류: {e}')

        try:
            cur.execute("""
                SELECT r.id, r.title, r.created_by, r.sub_host, r.created_at,
                       (SELECT COUNT(*) FROM open_messages WHERE room_id = r.id) AS message_count
                FROM open_rooms r ORDER BY r.id DESC
            """)
            open_rooms = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'관리자 오픈채팅 목록 오류: {e}')

        stats = {
            'total_users': sum(1 for u in all_users if u['is_active']),
            'banned_users': sum(1 for u in all_users if u['is_banned']),
            'online_users': sum(1 for u in all_users if u['is_online'] and u['is_active']),
            'group_rooms': len(group_rooms),
            'open_rooms': len(open_rooms),
        }

    return render_template('admin_dashboard.html', all_users=all_users, open_rooms=open_rooms,
                           group_rooms=group_rooms, user=user, role=role, stats=stats)


@app.route('/admin/ban_user/<username>', methods=['POST'])
@admin_required
def admin_ban_user(username):
    if username == ADMIN_USERNAME:
        return js_alert('관리자 계정은 차단할 수 없습니다.', url_for('admin_dashboard'), 400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET is_active = FALSE, is_banned = TRUE WHERE username = %s RETURNING username",
                    (username,))
        if not cur.fetchone():
            return js_alert('존재하지 않는 유저입니다.', url_for('admin_dashboard'), 404)
        cur.execute("DELETE FROM follows WHERE follower = %s OR following = %s", (username, username))
        cur.execute("DELETE FROM room_members WHERE user_id = %s", (username,))
        cur.execute("UPDATE open_rooms SET sub_host = NULL WHERE sub_host = %s", (username,))
        cur.execute("""
            DELETE FROM chat_rooms cr
            WHERE NOT EXISTS (SELECT 1 FROM room_members rm WHERE rm.room_id = cr.id)
        """)

    return js_alert('해당 유저가 영구 차단되었습니다.', url_for('admin_dashboard'))


@app.route('/admin/unban_user/<username>', methods=['POST'])
@admin_required
def admin_unban_user(username):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET is_banned = FALSE, is_active = TRUE WHERE username = %s", (username,))
    return js_alert('차단이 해제되었습니다.', url_for('admin_dashboard'))


@app.route('/admin/delete_group/<int:room_id>', methods=['POST'])
@admin_required
def admin_delete_group(room_id):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chat_rooms WHERE id = %s RETURNING id", (room_id,))
        deleted = cur.fetchone()
    if not deleted:
        return js_alert('존재하지 않는 단톡방입니다.', url_for('admin_dashboard'), 404)
    return js_alert('일반 단톡방이 강제 삭제되었습니다.', url_for('admin_dashboard'))


# ----------------------------------------------------------------
# 통합 메시지 삭제 API (open / general / group)
# ----------------------------------------------------------------
@app.route('/delete_chat_message/<string:chat_type>/<int:message_id>', methods=['POST'])
def delete_message(chat_type, message_id):
    user = session.get('user')
    if not user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
    if chat_type not in ('open', 'general', 'group'):
        return jsonify({'success': False, 'message': '잘못된 요청입니다.'}), 400

    admin = is_admin()

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            if chat_type == 'open':
                cur.execute("""
                    SELECT m.sender_real_id, r.created_by, r.sub_host
                    FROM open_messages m
                    JOIN open_rooms r ON r.id = m.room_id
                    WHERE m.id = %s
                """, (message_id,))
                msg = cur.fetchone()
                if not msg:
                    return jsonify({'success': False, 'message': '존재하지 않는 메시지입니다.'}), 404
                allowed = admin or user in (msg['sender_real_id'], msg['created_by'], msg['sub_host'])
                if allowed:
                    cur.execute("DELETE FROM open_messages WHERE id = %s", (message_id,))

            elif chat_type == 'general':
                cur.execute("SELECT sender FROM direct_messages WHERE id = %s", (message_id,))
                msg = cur.fetchone()
                if not msg:
                    return jsonify({'success': False, 'message': '존재하지 않는 메시지입니다.'}), 404
                allowed = admin or user == msg['sender']
                if allowed:
                    cur.execute("DELETE FROM direct_messages WHERE id = %s", (message_id,))

            else:
                cur.execute("""
                    SELECT m.sender, r.created_by
                    FROM room_messages m
                    JOIN chat_rooms r ON r.id = m.room_id
                    WHERE m.id = %s
                """, (message_id,))
                msg = cur.fetchone()
                if not msg:
                    return jsonify({'success': False, 'message': '존재하지 않는 메시지입니다.'}), 404
                allowed = admin or user in (msg['sender'], msg['created_by'])
                if allowed:
                    cur.execute("DELETE FROM room_messages WHERE id = %s", (message_id,))

        if allowed:
            return jsonify({'success': True, 'message': '메시지가 삭제되었습니다.'})
        return jsonify({'success': False, 'message': '삭제 권한이 없습니다.'}), 403

    except psycopg.Error as e:
        app.logger.error(f'메시지 삭제 오류: {e}')
        return jsonify({'success': False, 'message': '서버 오류가 발생했습니다.'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
