import os
import time
import json
import hmac
import io
import secrets
from functools import wraps

from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify, Response, abort
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row
from PIL import Image, ImageOps, UnidentifiedImageError

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
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL 환경 변수가 설정되지 않았습니다.')

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin1234')
DEFAULT_BIO = '안녕하세요! ChatClub입니다.'
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


# ----------------------------------------------------------------
# 사진 저장 (Render 서버 디스크는 재시작 시 지워지므로 DB에 저장)
# ----------------------------------------------------------------
Image.MAX_IMAGE_PIXELS = 50_000_000
CHAT_IMAGE_MAX_SIDE = 1280
PROFILE_IMAGE_MAX_SIDE = 400
DEFAULT_AVATAR = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<rect width='100' height='100' fill='%231e293b'/><circle cx='50' cy='38' r='18' fill='%2394a3b8'/>"
    "<path d='M18 88c4-18 18-28 32-28s28 10 32 28z' fill='%2394a3b8'/></svg>"
)


def get_uploaded_image(field_name='image'):
    file = request.files.get(field_name)
    if file and file.filename:
        return file
    return None


def process_image(file_storage, max_side):
    raw = file_storage.read()
    if not raw:
        raise ValueError('빈 파일입니다.')
    try:
        img = Image.open(io.BytesIO(raw))
        img.draft('RGB', (max_side * 2, max_side * 2))
        img = ImageOps.exif_transpose(img)
        if img.mode in ('RGBA', 'LA', 'P', 'PA'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, 'WEBP', quality=80, method=4)
        return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise ValueError('사진 파일(jpg, png, gif, webp)만 올릴 수 있습니다.')


def save_image(cur, image_bytes, owner):
    key = secrets.token_hex(16)
    cur.execute("INSERT INTO images (key, owner, mime, data) VALUES (%s, %s, %s, %s)",
                (key, owner, 'image/webp', image_bytes))
    return f'/img/{key}'


def delete_image(cur, url):
    if url and url.startswith('/img/'):
        cur.execute("DELETE FROM images WHERE key = %s", (url[len('/img/'):],))


@app.template_filter('avatar')
def avatar_filter(value):
    if value and value.startswith('/img/'):
        return value
    return DEFAULT_AVATAR


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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS community_posts (
                id SERIAL PRIMARY KEY,
                author_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                content TEXT NOT NULL,
                image_url VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS community_likes (
                id SERIAL PRIMARY KEY,
                post_id INT REFERENCES community_posts(id) ON DELETE CASCADE,
                user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                UNIQUE (post_id, user_id)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS community_comments (
                id SERIAL PRIMARY KEY,
                post_id INT REFERENCES community_posts(id) ON DELETE CASCADE,
                author_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS images (
                key VARCHAR(32) PRIMARY KEY,
                owner VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
                mime VARCHAR(30) NOT NULL,
                data BYTEA NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 예전 버전 app.py가 만든 테이블에 빠진 컬럼 자동 보강
        for sql in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(50)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio VARCHAR(255) DEFAULT '안녕하세요! ChatClub입니다.'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_img VARCHAR(255) DEFAULT 'default.png'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP",
            "UPDATE users SET nickname = username WHERE nickname IS NULL",
            "UPDATE users SET is_active = TRUE WHERE is_active IS NULL",
            "UPDATE users SET is_banned = FALSE WHERE is_banned IS NULL",
            "ALTER TABLE follows ADD COLUMN IF NOT EXISTS column_id SERIAL",
            "ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE",
            "ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE room_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE open_rooms ADD COLUMN IF NOT EXISTS title VARCHAR(100)",
            "ALTER TABLE open_rooms ADD COLUMN IF NOT EXISTS sub_host VARCHAR(50) REFERENCES users(username) ON DELETE SET NULL",
            "ALTER TABLE open_rooms ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE open_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE ask_messages ADD COLUMN IF NOT EXISTS answer TEXT",
            "ALTER TABLE ask_messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE",
            "ALTER TABLE ask_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS image_url VARCHAR(255)",
            "ALTER TABLE room_messages ADD COLUMN IF NOT EXISTS image_url VARCHAR(255)",
            "ALTER TABLE open_messages ADD COLUMN IF NOT EXISTS image_url VARCHAR(255)",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS image_url VARCHAR(255)",
        ]:
            cur.execute(sql)

        cur.execute("""
            DO $$
            DECLARE
                legacy RECORD;
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema = current_schema() AND table_name = 'open_rooms'
                             AND column_name = 'room_name') THEN
                    EXECUTE 'UPDATE open_rooms SET title = room_name WHERE title IS NULL';
                END IF;
                UPDATE open_rooms SET title = '제목 없는 방' WHERE title IS NULL;

                FOR legacy IN
                    SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = current_schema() AND is_nullable = 'NO' AND column_default IS NULL
                      AND (table_name, column_name) IN (('open_rooms', 'room_name'), ('ask_messages', 'sender_id'),
                                                        ('follows', 'created_at'), ('open_messages', 'image_url'))
                LOOP
                    EXECUTE format('ALTER TABLE %I ALTER COLUMN %I DROP NOT NULL', legacy.table_name, legacy.column_name);
                END LOOP;
            END $$;
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_community_likes_post ON community_likes (post_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_community_comments_post ON community_comments (post_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dm_pair ON direct_messages (sender, receiver);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dm_unread ON direct_messages (receiver, is_read);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_room_messages_room ON room_messages (room_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_open_messages_room ON open_messages (room_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ask_target ON ask_messages (target_user);")

        cur.execute("""
            DELETE FROM images i
            WHERE i.created_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
              AND NOT EXISTS (SELECT 1 FROM direct_messages WHERE image_url = '/img/' || i.key)
              AND NOT EXISTS (SELECT 1 FROM room_messages WHERE image_url = '/img/' || i.key)
              AND NOT EXISTS (SELECT 1 FROM open_messages WHERE image_url = '/img/' || i.key)
              AND NOT EXISTS (SELECT 1 FROM community_posts WHERE image_url = '/img/' || i.key)
              AND NOT EXISTS (SELECT 1 FROM users WHERE profile_img = '/img/' || i.key)
        """)

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
    return js_alert('사진은 15MB 이하만 올릴 수 있습니다.', status=413)


@app.route('/img/<key>')
def serve_image(key):
    if len(key) != 32 or not all(ch in '0123456789abcdef' for ch in key):
        abort(404)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT mime, data FROM images WHERE key = %s", (key,))
        row = cur.fetchone()
    if not row:
        abort(404)
    resp = Response(bytes(row['data']), mimetype=row['mime'])
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# ----------------------------------------------------------------
# 메인 대시보드
# ----------------------------------------------------------------
@app.route('/')
def index():
    user = session.get('user')
    my_rooms = []
    all_users = []
    dm_list = []
    posts = []
    unread_total = 0

    if not user:
        return render_template('index.html', my_rooms=my_rooms, all_users=all_users,
                               dm_list=dm_list, unread_total=unread_total, user=user, posts=posts)

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
                       (SELECT CASE WHEN COALESCE(message, '') = '' AND image_url IS NOT NULL THEN '📷 사진' ELSE message END FROM direct_messages WHERE id = a.last_msg_id) AS last_message
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

        try:
            cur.execute("""
                SELECT p.id, p.author_id, u.nickname, u.profile_img, p.content, p.image_url, p.created_at,
                       (SELECT COUNT(*) FROM community_likes l WHERE l.post_id = p.id) AS like_count,
                       (SELECT COUNT(*) FROM community_comments c WHERE c.post_id = p.id) AS comment_count,
                       EXISTS (SELECT 1 FROM community_likes l WHERE l.post_id = p.id AND l.user_id = %s) AS is_liked
                FROM community_posts p
                JOIN users u ON u.username = p.author_id AND u.is_active = TRUE
                ORDER BY p.id DESC
                LIMIT 30
            """, (user,))
            posts = cur.fetchall()
        except psycopg.Error as e:
            conn.rollback()
            app.logger.error(f'커뮤니티 목록 오류: {e}')

    return render_template('index.html', my_rooms=my_rooms, all_users=all_users,
                           dm_list=dm_list, unread_total=unread_total, user=user, posts=posts)


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
            message = get_field('message') or ''
            upload = get_uploaded_image()
            if not message and not upload:
                if wants_json():
                    return jsonify({'success': False, 'message': '메시지를 입력해주세요.'}), 400
                return redirect(url_for('dm_chat', username=username))
            if len(message) > MAX_MESSAGE_LEN:
                return json_or_alert(False, f'메시지는 {MAX_MESSAGE_LEN}자 이하로 입력해주세요.', status=400)

            image_url = None
            if upload:
                try:
                    image_url = save_image(cur, process_image(upload, CHAT_IMAGE_MAX_SIDE), my_id)
                except ValueError as e:
                    return json_or_alert(False, str(e), status=400)

            cur.execute("""
                INSERT INTO direct_messages (sender, receiver, message, image_url)
                VALUES (%s, %s, %s, %s) RETURNING id, created_at
            """, (my_id, username, message, image_url))
            row = cur.fetchone()
            if wants_json():
                return jsonify({'success': True, 'id': row['id'], 'sender': my_id, 'image_url': image_url,
                                'message': message, 'created_at': fmt_time(row['created_at'])})
            return redirect(url_for('dm_chat', username=username))

        cur.execute("""
            UPDATE direct_messages SET is_read = TRUE
            WHERE sender = %s AND receiver = %s AND is_read = FALSE
        """, (username, my_id))

        cur.execute("""
            SELECT id, sender, receiver, message, image_url, created_at, is_read
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
            cur.execute("DELETE FROM direct_messages WHERE id = %s RETURNING image_url", (message_id,))
            gone = cur.fetchone()
            if gone:
                delete_image(cur, gone['image_url'])
        else:
            if msg['sender'] == my_id:
                cur.execute("UPDATE direct_messages SET deleted_by_sender = TRUE WHERE id = %s", (message_id,))
            else:
                cur.execute("UPDATE direct_messages SET deleted_by_receiver = TRUE WHERE id = %s", (message_id,))
            cur.execute("""
                DELETE FROM direct_messages
                WHERE id = %s AND deleted_by_sender = TRUE AND deleted_by_receiver = TRUE
                RETURNING image_url
            """, (message_id,))
            gone = cur.fetchone()
            if gone:
                delete_image(cur, gone['image_url'])

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
            RETURNING image_url
        """, (my_id, username, username, my_id))
        for gone in cur.fetchall():
            delete_image(cur, gone['image_url'])

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
            message = get_field('message') or ''
            upload = get_uploaded_image()
            if message or upload:
                if len(message) > MAX_MESSAGE_LEN:
                    return json_or_alert(False, f'메시지는 {MAX_MESSAGE_LEN}자 이하로 입력해주세요.', status=400)
                image_url = None
                if upload:
                    try:
                        image_url = save_image(cur, process_image(upload, CHAT_IMAGE_MAX_SIDE), user)
                    except ValueError as e:
                        return json_or_alert(False, str(e), status=400)
                cur.execute("""
                    INSERT INTO room_messages (room_id, sender, message, image_url)
                    VALUES (%s, %s, %s, %s) RETURNING id, created_at
                """, (room_id, user, message, image_url))
                row = cur.fetchone()
                if wants_json():
                    return jsonify({'success': True, 'id': row['id'], 'sender': user, 'image_url': image_url,
                                    'message': message, 'created_at': fmt_time(row['created_at'])})
            return redirect(url_for('group_chat', room_id=room_id))

        cur.execute("""
            SELECT m.id, m.sender, u.nickname, u.profile_img, m.message, m.image_url, m.created_at
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
    if not session.get('user'):
        flash('로그인이 필요한 서비스입니다.')
        return redirect(url_for('login'))
    return redirect(url_for('index'))


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

    image_bytes = None
    if profile_img and profile_img.filename:
        try:
            image_bytes = process_image(profile_img, PROFILE_IMAGE_MAX_SIDE)
        except ValueError as e:
            return js_alert(str(e), status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET bio = %s WHERE username = %s", (bio, user))
        if nickname:
            cur.execute("UPDATE users SET nickname = %s WHERE username = %s", (nickname, user))
        if image_bytes:
            cur.execute("SELECT profile_img FROM users WHERE username = %s", (user,))
            old = cur.fetchone()
            new_url = save_image(cur, image_bytes, user)
            cur.execute("UPDATE users SET profile_img = %s WHERE username = %s", (new_url, user))
            if old:
                delete_image(cur, old['profile_img'])

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


@app.route('/delete_message/<int:msg_id>', methods=['POST'])
@app.route('/delete_ask/<int:msg_id>', methods=['POST'])
@login_required
def delete_ask(msg_id):
    user = session['user']
    with get_db_connection() as conn, conn.cursor() as cur:
        if is_admin():
            cur.execute("DELETE FROM ask_messages WHERE id = %s RETURNING target_user", (msg_id,))
        else:
            cur.execute("DELETE FROM ask_messages WHERE id = %s AND target_user = %s RETURNING target_user",
                        (msg_id, user))
        row = cur.fetchone()
    if not row:
        return js_alert('삭제할 수 없는 질문입니다.', status=403)
    return redirect(url_for('user_profile', username=row['target_user']))


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
# 커뮤니티 게시판 (게시글 / 좋아요 / 댓글)
# ----------------------------------------------------------------
MAX_POST_LEN = 2000
MAX_COMMENT_LEN = 500


@app.route('/community/post', methods=['POST'])
@login_required
def create_community_post():
    user = session['user']
    content = request.form.get('content', '').strip()
    upload = get_uploaded_image()
    if not content and not upload:
        return js_alert('내용을 입력해주세요.', status=400)
    if len(content) > MAX_POST_LEN:
        return js_alert(f'게시글은 {MAX_POST_LEN}자 이하로 작성해주세요.', status=400)

    image_bytes = None
    if upload:
        try:
            image_bytes = process_image(upload, CHAT_IMAGE_MAX_SIDE)
        except ValueError as e:
            return js_alert(str(e), status=400)

    with get_db_connection() as conn, conn.cursor() as cur:
        image_url = save_image(cur, image_bytes, user) if image_bytes else None
        cur.execute("INSERT INTO community_posts (author_id, content, image_url) VALUES (%s, %s, %s)",
                    (user, content, image_url))

    return redirect(url_for('index', tab='community'))


@app.route('/community/post/<int:post_id>')
@login_required
def community_post(post_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.author_id, u.nickname, u.profile_img, p.content, p.image_url, p.created_at,
                   (SELECT COUNT(*) FROM community_likes l WHERE l.post_id = p.id) AS like_count,
                   EXISTS (SELECT 1 FROM community_likes l WHERE l.post_id = p.id AND l.user_id = %s) AS is_liked
            FROM community_posts p
            JOIN users u ON u.username = p.author_id AND u.is_active = TRUE
            WHERE p.id = %s
        """, (user, post_id))
        post = cur.fetchone()
        if not post:
            return js_alert('삭제되었거나 존재하지 않는 게시글입니다.', url_for('index', tab='community'), 404)

        cur.execute("""
            SELECT c.id, c.author_id, u.nickname, u.profile_img, c.comment, c.created_at
            FROM community_comments c
            JOIN users u ON u.username = c.author_id AND u.is_active = TRUE
            WHERE c.post_id = %s
            ORDER BY c.id ASC
        """, (post_id,))
        comments = cur.fetchall()

    return render_template('community_post.html', post=post, comments=comments,
                           my_id=user, is_admin=is_admin())


@app.route('/community/post/<int:post_id>/like', methods=['POST'])
def toggle_post_like(post_id):
    user = session.get('user')
    if not user:
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM community_posts WHERE id = %s", (post_id,))
            if not cur.fetchone():
                return jsonify({'success': False, 'message': '존재하지 않는 게시글입니다.'}), 404

            cur.execute("DELETE FROM community_likes WHERE post_id = %s AND user_id = %s RETURNING id",
                        (post_id, user))
            if cur.fetchone():
                is_liked = False
            else:
                cur.execute("""
                    INSERT INTO community_likes (post_id, user_id) VALUES (%s, %s)
                    ON CONFLICT (post_id, user_id) DO NOTHING
                """, (post_id, user))
                is_liked = True

            cur.execute("SELECT COUNT(*) AS cnt FROM community_likes WHERE post_id = %s", (post_id,))
            like_count = cur.fetchone()['cnt']
    except psycopg.Error as e:
        app.logger.error(f'좋아요 오류: {e}')
        return jsonify({'success': False, 'message': '서버 오류가 발생했습니다.'}), 500

    return jsonify({'success': True, 'is_liked': is_liked, 'like_count': like_count})


@app.route('/community/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_post_comment(post_id):
    user = session['user']
    comment = request.form.get('comment', '').strip()
    post_url = url_for('community_post', post_id=post_id)

    if not comment:
        return redirect(post_url)
    if len(comment) > MAX_COMMENT_LEN:
        return js_alert(f'댓글은 {MAX_COMMENT_LEN}자 이하로 작성해주세요.', post_url, 400)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM community_posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            return js_alert('삭제되었거나 존재하지 않는 게시글입니다.', url_for('index', tab='community'), 404)
        cur.execute("INSERT INTO community_comments (post_id, author_id, comment) VALUES (%s, %s, %s)",
                    (post_id, user, comment))

    return redirect(post_url)


@app.route('/community/post/<int:post_id>/delete', methods=['POST'])
@login_required
def delete_community_post(post_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT author_id FROM community_posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if not post:
            return js_alert('이미 삭제된 게시글입니다.', url_for('index', tab='community'), 404)
        if post['author_id'] != user and not is_admin():
            return js_alert('삭제 권한이 없습니다.', status=403)
        cur.execute("DELETE FROM community_posts WHERE id = %s RETURNING image_url", (post_id,))
        gone = cur.fetchone()
        if gone:
            delete_image(cur, gone['image_url'])

    return js_alert('게시글이 삭제되었습니다.', url_for('index', tab='community'))


@app.route('/community/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_community_comment(comment_id):
    user = session['user']

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.post_id, c.author_id, p.author_id AS post_author
            FROM community_comments c
            JOIN community_posts p ON p.id = c.post_id
            WHERE c.id = %s
        """, (comment_id,))
        row = cur.fetchone()
        if not row:
            return js_alert('이미 삭제된 댓글입니다.', url_for('index', tab='community'), 404)
        if user not in (row['author_id'], row['post_author']) and not is_admin():
            return js_alert('삭제 권한이 없습니다.', status=403)
        cur.execute("DELETE FROM community_comments WHERE id = %s", (comment_id,))

    return redirect(url_for('community_post', post_id=row['post_id']))


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
            SELECT id, sender_anon, sender_real_id, message, image_url, created_at FROM (
                SELECT id, sender_anon, sender_real_id, message, image_url, created_at
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
    message = get_field('message') or ''
    upload = get_uploaded_image()
    anon_name = session.get(f'anon_name_{room_id}')
    room_url = url_for('open_chat_room', room_id=room_id)

    if not anon_name:
        return json_or_alert(False, '먼저 닉네임을 설정해주세요.', room_url, 400)
    if not message and not upload:
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

        image_url = None
        if upload:
            try:
                image_url = save_image(cur, process_image(upload, CHAT_IMAGE_MAX_SIDE), user)
            except ValueError as e:
                return json_or_alert(False, str(e), room_url, 400)

        cur.execute("""
            INSERT INTO open_messages (room_id, sender_anon, sender_real_id, message, image_url)
            VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at
        """, (room_id, anon_name, user, message, image_url))
        row = cur.fetchone()

    if wants_json():
        return jsonify({'success': True, 'id': row['id'], 'sender_anon': anon_name, 'image_url': image_url,
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
                SELECT username, nickname, bio, is_active, COALESCE(is_banned, FALSE) AS is_banned, last_seen,
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
                    cur.execute("DELETE FROM open_messages WHERE id = %s RETURNING image_url", (message_id,))
                    gone = cur.fetchone()
                    if gone:
                        delete_image(cur, gone['image_url'])

            elif chat_type == 'general':
                cur.execute("SELECT sender FROM direct_messages WHERE id = %s", (message_id,))
                msg = cur.fetchone()
                if not msg:
                    return jsonify({'success': False, 'message': '존재하지 않는 메시지입니다.'}), 404
                allowed = admin or user == msg['sender']
                if allowed:
                    cur.execute("DELETE FROM direct_messages WHERE id = %s RETURNING image_url", (message_id,))
                    gone = cur.fetchone()
                    if gone:
                        delete_image(cur, gone['image_url'])

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
                    cur.execute("DELETE FROM room_messages WHERE id = %s RETURNING image_url", (message_id,))
                    gone = cur.fetchone()
                    if gone:
                        delete_image(cur, gone['image_url'])

        if allowed:
            return jsonify({'success': True, 'message': '메시지가 삭제되었습니다.'})
        return jsonify({'success': False, 'message': '삭제 권한이 없습니다.'}), 403

    except psycopg.Error as e:
        app.logger.error(f'메시지 삭제 오류: {e}')
        return jsonify({'success': False, 'message': '서버 오류가 발생했습니다.'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
