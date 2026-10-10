import os
import time
import zoneinfo
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2

app = Flask(__name__)
app.secret_key = 'super_secret_chat_club_key'

UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def get_kst_now():
    return datetime.now(zoneinfo.ZoneInfo("Asia/Seoul"))

def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        db_url = 'postgresql://postgres:password@localhost:5432/postgres'
    conn = psycopg2.connect(db_url)
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(50) PRIMARY KEY,
            password VARCHAR(255) NOT NULL,
            nickname VARCHAR(50) NOT NULL,
            bio TEXT DEFAULT '안녕하세요! ChatClub입니다.',
            profile_img VARCHAR(255) DEFAULT 'default.png',
            is_active BOOLEAN DEFAULT TRUE,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_img VARCHAR(255) DEFAULT 'default.png';")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT DEFAULT '안녕하세요! ChatClub입니다.';")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        conn.commit()
    except Exception:
        conn.rollback()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            id SERIAL PRIMARY KEY,
            follower VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            following VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(follower, following)
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id SERIAL PRIMARY KEY,
            room_name VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS room_members (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES chat_rooms(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE
        );
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
            room_name VARCHAR(100) NOT NULL,
            created_by VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS open_room_members (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            role VARCHAR(20) DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(room_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS open_room_banned (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE(room_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS open_messages (
            id SERIAL PRIMARY KEY,
            room_id INT REFERENCES open_rooms(id) ON DELETE CASCADE,
            sender_anon VARCHAR(50) NOT NULL,
            sender_real_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            message TEXT,
            image_url VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    try:
        cur.execute("ALTER TABLE open_room_members ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cur.execute("ALTER TABLE open_messages ADD COLUMN IF NOT EXISTS image_url VARCHAR(255);")
        conn.commit()
    except Exception:
        conn.rollback()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ask_messages (
            id SERIAL PRIMARY KEY,
            target_user VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            sender_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            content TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS community_likes (
            id SERIAL PRIMARY KEY,
            post_id INT REFERENCES community_posts(id) ON DELETE CASCADE,
            user_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            UNIQUE(post_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS community_comments (
            id SERIAL PRIMARY KEY,
            post_id INT REFERENCES community_posts(id) ON DELETE CASCADE,
            author_id VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        nickname = request.form.get('nickname', '').strip()

        if not username or not password or not nickname:
            return "<script>alert('모든 필드를 입력해 주세요.'); history.back();</script>", 400

        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT username FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s))", (username,))
            if cur.fetchone() is not None:
                cur.close()
                conn.close()
                return "<script>alert('이미 존재하는 아이디입니다.'); history.back();</script>", 400

            hashed_pw = generate_password_hash(password)
            cur.execute("""
                INSERT INTO users (username, password, nickname, bio, profile_img, is_active, last_seen) 
                VALUES (%s, %s, %s, '안녕하세요!', 'default.png', TRUE, %s)
            """, (username, hashed_pw, nickname, get_kst_now()))
            
            conn.commit()
            cur.close()
            conn.close()
            return "<script>alert('회원가입 성공! 로그인해 주세요.'); location.href='/login';</script>"

        except Exception as e:
            return f"<script>alert('회원가입 처리 실패: {str(e)}'); history.back();</script>", 500

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            return "<script>alert('아이디와 비밀번호를 입력해 주세요.'); history.back();</script>", 400

        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT username, password FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s)) AND is_active = TRUE", (username,))
            user_row = cur.fetchone()

            if user_row is not None:
                real_username = user_row[0]
                real_password = user_row[1]

                if check_password_hash(real_password, password):
                    session['user'] = real_username
                    cur.execute("UPDATE users SET last_seen = %s WHERE username = %s", (get_kst_now(), real_username))
                    conn.commit()
                    cur.close()
                    conn.close()
                    return redirect(url_for('index'))

            cur.close()
            conn.close()
            return "<script>alert('아이디 또는 비밀번호가 올바르지 않습니다.'); history.back();</script>", 400

        except Exception as e:
            return f"<script>alert('로그인 처리 실패: {str(e)}'); history.back();</script>", 500

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    my_rooms, open_rooms, all_users, dm_list = [], [], [], []
    my_asks, community_posts = [], []
    unread_total = 0
    current_user_info = None

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("UPDATE users SET last_seen = %s WHERE username = %s", (get_kst_now(), user))
        cur.execute("SELECT username, nickname, bio, profile_img FROM users WHERE username = %s", (user,))
        user_data = cur.fetchone()
        if user_data:
            current_user_info = {
                'username': user_data[0],
                'nickname': user_data[1],
                'bio': user_data[2],
                'profile_img': user_data[3]
            }

        try:
            cur.execute("""
                SELECT DISTINCT cr.id, cr.room_name 
                FROM chat_rooms cr
                LEFT JOIN room_members rm ON cr.id = rm.room_id
                WHERE cr.created_by = %s OR rm.user_id = %s
                ORDER BY cr.id DESC
            """, (user, user))
            my_rooms = [{'id': r[0], 'room_name': r[1]} for r in cur.fetchall()]
        except Exception:
            conn.rollback()

        try:
            cur.execute("SELECT id, room_name FROM open_rooms ORDER BY id DESC")
            open_rooms = [{'id': r[0], 'room_name': r[1]} for r in cur.fetchall()]
        except Exception:
            conn.rollback()

        try:
            cur.execute("""
                SELECT u.username, u.nickname, u.profile_img,
                       CASE WHEN u.last_seen >= CURRENT_TIMESTAMP - INTERVAL '3 minutes' THEN TRUE ELSE FALSE END as is_online
                FROM users u
                JOIN follows f ON u.username = f.following
                WHERE f.follower = %s AND u.is_active = TRUE
                ORDER BY is_online DESC, u.nickname ASC
            """, (user,))
            all_users = [{
                'username': u[0], 'nickname': u[1], 'profile_img': u[2], 'is_online': u[3]
            } for u in cur.fetchall()]
        except Exception:
            conn.rollback()

        try:
            cur.execute("""
                WITH partners AS (
                    SELECT sender AS partner_id FROM direct_messages WHERE receiver = %s
                    UNION
                    SELECT receiver AS partner_id FROM direct_messages WHERE sender = %s
                )
                SELECT 
                    u.username, u.nickname, u.profile_img,
                    CASE WHEN f.id IS NOT NULL THEN TRUE ELSE FALSE END AS is_following,
                    COALESCE((SELECT COUNT(*) FROM direct_messages WHERE sender = u.username AND receiver = %s AND is_read = FALSE), 0) AS unread_count,
                    (SELECT MAX(created_at) FROM direct_messages WHERE (sender = %s AND receiver = u.username) OR (sender = u.username AND receiver = %s)) AS last_msg_time,
                    (SELECT message FROM direct_messages WHERE (sender = %s AND receiver = u.username) OR (sender = u.username AND receiver = %s) ORDER BY id DESC LIMIT 1) AS last_msg
                FROM partners p
                JOIN users u ON p.partner_id = u.username AND u.is_active = TRUE
                LEFT JOIN follows f ON f.follower = %s AND f.following = u.username
                ORDER BY last_msg_time DESC;
            """, (user, user, user, user, user, user, user, user))
            raw_dm_list = cur.fetchall()
            dm_list = []
            for item in raw_dm_list:
                un_cnt = item[4] or 0
                unread_total += int(un_cnt)
                dm_list.append({
                    'username': item[0], 'nickname': item[1], 'profile_img': item[2],
                    'is_following': item[3], 'unread_count': un_cnt, 'last_msg_time': item[5], 'last_msg': item[6]
                })
        except Exception:
            conn.rollback()

        try:
            cur.execute("SELECT id, content, created_at FROM ask_messages WHERE target_user = %s ORDER BY id DESC", (user,))
            my_asks = [{'id': a[0], 'content': a[1], 'created_at': a[2]} for a in cur.fetchall()]
        except Exception:
            conn.rollback()

        try:
            cur.execute("""
                SELECT 
                    p.id, p.content, p.image_url, p.created_at,
                    (SELECT COUNT(*) FROM community_likes WHERE post_id = p.id) AS like_count,
                    EXISTS(SELECT 1 FROM community_likes WHERE post_id = p.id AND user_id = %s) AS is_liked,
                    (SELECT COUNT(*) FROM community_comments WHERE post_id = p.id) AS comment_count
                FROM community_posts p
                ORDER BY p.id DESC LIMIT 30
            """, (user,))
            posts = cur.fetchall()
            community_posts = []
            for p in posts:
                post_id = p[0]
                cur.execute("SELECT comment, created_at FROM community_comments WHERE post_id = %s ORDER BY id ASC", (post_id,))
                comments_data = [{'comment': c[0], 'created_at': c[1]} for c in cur.fetchall()]
                community_posts.append({
                    'id': p[0], 'content': p[1], 'image_url': p[2], 'created_at': p[3],
                    'like_count': p[4], 'is_liked': p[5], 'comment_count': p[6],
                    'comments': comments_data
                })
        except Exception:
            conn.rollback()

        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return render_template('index.html', 
                           user=user, 
                           current_user_info=current_user_info,
                           my_rooms=my_rooms, 
                           open_rooms=open_rooms,
                           all_users=all_users, 
                           dm_list=dm_list, 
                           unread_total=unread_total,
                           my_asks=my_asks, 
                           community_posts=community_posts)

@app.route('/profile/update', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    nickname = request.form.get('nickname', '').strip()
    bio = request.form.get('bio', '').strip()
    img_file = request.files.get('profile_img')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if img_file and img_file.filename != '':
            filename = f"prof_{int(time.time())}_{secure_filename(img_file.filename)}"
            img_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            cur.execute("UPDATE users SET profile_img = %s WHERE username = %s", (filename, user))

        if nickname:
            cur.execute("UPDATE users SET nickname = %s, bio = %s WHERE username = %s", (nickname, bio, user))

        conn.commit()
    except Exception as e:
        conn.rollback()
        return f"<script>alert('프로필 수정 오류: {str(e)}'); history.back();</script>", 500
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('index'))

@app.route('/follow/search', methods=['POST'])
def follow_by_search():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    target_username = request.form.get('target_username', '').strip()

    if not target_username:
        return "<script>alert('아이디를 입력해 주세요.'); history.back();</script>", 400

    if target_username.lower() == user.lower():
        return "<script>alert('자기 자신은 팔로우할 수 없습니다.'); history.back();</script>", 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username, nickname FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s)) AND is_active = TRUE", (target_username,))
        target_user = cur.fetchone()

        if not target_user:
            cur.close()
            conn.close()
            return "<script>alert('존재하지 않는 아이디입니다.'); history.back();</script>", 400

        real_target_username = target_user[0]
        nickname = target_user[1]

        cur.execute("""
            INSERT INTO follows (follower, following, created_at) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (follower, following) DO NOTHING
        """, (user, real_target_username, get_kst_now()))
        conn.commit()

        return f"<script>alert('{nickname}(@{real_target_username})님을 팔로우했습니다!'); location.href='/';</script>"

    except Exception as e:
        conn.rollback()
        return f"<script>alert('팔로우 처리 오류: {str(e)}'); history.back();</script>", 500
    finally:
        cur.close()
        conn.close()

@app.route('/follow/<username>', methods=['POST'])
def follow_user(username):
    user = session.get('user')
    if not user:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO follows (follower, following, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (user, username, get_kst_now()))
        conn.commit()
        success = True
    except Exception:
        conn.rollback()
        success = False
    finally:
        cur.close()
        conn.close()
        
    return jsonify({"success": success})

@app.route('/dm/<username>')
def dm_chat(username):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT username, nickname, profile_img FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s))", (username,))
        partner_row = cur.fetchone()

        if not partner_row:
            cur.close()
            conn.close()
            return "<script>alert('존재하지 않는 유저입니다.'); history.back();</script>", 404

        partner = {'username': partner_row[0], 'nickname': partner_row[1], 'profile_img': partner_row[2]}
        real_partner_name = partner['username']

        cur.execute("SELECT id FROM follows WHERE follower = %s AND following = %s", (user, real_partner_name))
        is_following = cur.fetchone() is not None

        cur.execute("UPDATE direct_messages SET is_read = TRUE WHERE sender = %s AND receiver = %s", (real_partner_name, user))
        conn.commit()

        cur.execute("""
            SELECT id, sender, receiver, message, created_at 
            FROM direct_messages 
            WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s)
            ORDER BY id ASC
        """, (user, real_partner_name, real_partner_name, user))
        messages = [{'id': m[0], 'sender': m[1], 'receiver': m[2], 'message': m[3], 'created_at': m[4]} for m in cur.fetchall()]

    except Exception as e:
        conn.rollback()
        return f"<script>alert('대화 내역 오류: {str(e)}'); history.back();</script>", 500
    finally:
        cur.close()
        conn.close()

    return render_template('dm_chat.html', user=user, partner=partner, is_following=is_following, messages=messages)

@app.route('/dm/<username>/send', methods=['POST'])
def send_dm(username):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    msg = request.form.get('message', '').strip()
    if msg:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO direct_messages (sender, receiver, message, created_at) VALUES (%s, %s, %s, %s)",
                        (user, username, msg, get_kst_now()))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()
            
    return redirect(url_for('dm_chat', username=username))

@app.route('/dm/message/<int:msg_id>/delete', methods=['POST'])
def delete_dm_message(msg_id):
    user = session.get('user')
    if not user: return jsonify({"success": False}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM direct_messages WHERE id = %s AND sender = %s", (msg_id, user))
        conn.commit()
        success = True
    except Exception:
        conn.rollback()
        success = False
    finally:
        cur.close()
        conn.close()
        
    return jsonify({"success": success})

@app.route('/open_chat/create', methods=['POST'])
def create_open_room():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    room_name = request.form.get('room_name', '').strip()
    if room_name:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO open_rooms (room_name, created_by, created_at) VALUES (%s, %s, %s) RETURNING id",
                        (room_name, user, get_kst_now()))
            res = cur.fetchone()
            room_id = res[0]
            
            cur.execute("INSERT INTO open_room_members (room_id, user_id, role, last_read_at) VALUES (%s, %s, 'owner', %s)", (room_id, user, get_kst_now()))
            conn.commit()
            return redirect(url_for('open_chat', room_id=room_id))
        except Exception as e:
            conn.rollback()
            return f"<script>alert('오픈채팅방 생성 오류: {str(e)}'); history.back();</script>", 500
        finally:
            cur.close()
            conn.close()
            
    return redirect(url_for('index'))

@app.route('/open_chat/<int:room_id>')
def open_chat(room_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM open_room_banned WHERE room_id = %s AND user_id = %s", (room_id, user))
        if cur.fetchone():
            cur.close()
            conn.close()
            return "<script>alert('강퇴된 오픈채팅방입니다.'); location.href='/';</script>", 403

        cur.execute("SELECT id, room_name, created_by FROM open_rooms WHERE id = %s", (room_id,))
        room_row = cur.fetchone()

        if not room_row:
            cur.close()
            conn.close()
            return "<script>alert('존재하지 않는 방입니다.'); history.back();</script>", 404

        room = {'id': room_row[0], 'room_name': room_row[1], 'created_by': room_row[2]}
        room_owner = room['created_by']
        my_role = 'owner' if room_owner == user else 'member'
        
        cur.execute("""
            INSERT INTO open_room_members (room_id, user_id, role, last_read_at) 
            VALUES (%s, %s, %s, %s) 
            ON CONFLICT (room_id, user_id) 
            DO UPDATE SET last_read_at = %s
        """, (room_id, user, my_role, get_kst_now(), get_kst_now()))
        conn.commit()

        cur.execute("SELECT role FROM open_room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        role_res = cur.fetchone()
        current_role = role_res[0] if role_res else 'member'

        cur.execute("""
            SELECT m.user_id, m.role, u.nickname, u.profile_img
            FROM open_room_members m
            JOIN users u ON m.user_id = u.username
            WHERE m.room_id = %s
            ORDER BY CASE WHEN m.role = 'owner' THEN 1 WHEN m.role = 'sub_owner' THEN 2 ELSE 3 END, u.nickname ASC
        """, (room_id,))
        members = [{'user_id': m[0], 'role': m[1], 'nickname': m[2], 'profile_img': m[3]} for m in cur.fetchall()]
        total_member_cnt = len(members)

        cur.execute("""
            SELECT 
                om.id, om.sender_anon, om.sender_real_id, om.message, om.image_url, om.created_at,
                (%s - (
                    SELECT COUNT(*) 
                    FROM open_room_members 
                    WHERE room_id = %s AND last_read_at >= om.created_at
                )) AS unread_cnt
            FROM open_messages om
            WHERE om.room_id = %s 
            ORDER BY om.id ASC
        """, (total_member_cnt, room_id, room_id))
        messages = [{
            'id': msg[0], 'sender_anon': msg[1], 'sender_real_id': msg[2],
            'message': msg[3], 'image_url': msg[4], 'created_at': msg[5], 'unread_cnt': msg[6]
        } for msg in cur.fetchall()]

    except Exception as e:
        conn.rollback()
        return f"<script>alert('오픈채팅 로딩 오류: {str(e)}'); history.back();</script>", 500
    finally:
        cur.close()
        conn.close()

    return render_template('open_chat.html', 
                           user=user, 
                           room=room, 
                           messages=messages, 
                           members=members, 
                           current_role=current_role)

@app.route('/open_chat/<int:room_id>/send', methods=['POST'])
def send_open_message(room_id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
        
    anon_name = request.form.get('anon_name', '').strip() or '익명'
    msg = request.form.get('message', '').strip()
    image = request.files.get('image')
    img_name = None

    if image and image.filename != '':
        img_name = f"open_{int(time.time())}_{secure_filename(image.filename)}"
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], img_name))

    if msg or img_name:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM open_room_banned WHERE room_id = %s AND user_id = %s", (room_id, user))
            if cur.fetchone():
                return "<script>alert('강퇴된 사용자입니다.'); location.href='/';</script>", 403

            cur.execute("""
                INSERT INTO open_messages (room_id, sender_anon, sender_real_id, message, image_url, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (room_id, anon_name, user, msg, img_name, get_kst_now()))
            
            cur.execute("UPDATE open_room_members SET last_read_at = %s WHERE room_id = %s AND user_id = %s", 
                        (get_kst_now(), room_id, user))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()
            
    return redirect(url_for('open_chat', room_id=room_id))

@app.route('/open_chat/<int:room_id>/role', methods=['POST'])
def set_open_member_role(room_id):
    user = session.get('user')
    if not user: return jsonify({"success": False}), 401
    
    target_user = request.form.get('target_user')
    new_role = request.form.get('role')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT role FROM open_room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        res = cur.fetchone()
        my_role = res[0] if res else None

        if my_role != 'owner':
            return jsonify({"success": False, "message": "방장 전용"}), 403

        cur.execute("UPDATE open_room_members SET role = %s WHERE room_id = %s AND user_id = %s", (new_role, room_id, target_user))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/open_chat/<int:room_id>/kick', methods=['POST'])
def kick_open_member(room_id):
    user = session.get('user')
    if not user: return jsonify({"success": False}), 401
    
    target_user = request.form.get('target_user')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT role FROM open_room_members WHERE room_id = %s AND user_id = %s", (room_id, user))
        res = cur.fetchone()
        my_role = res[0] if res else None

        if my_role not in ['owner', 'sub_owner']:
            return jsonify({"success": False, "message": "권한 없음"}), 403

        cur.execute("DELETE FROM open_room_members WHERE room_id = %s AND user_id = %s", (room_id, target_user))
        cur.execute("INSERT INTO open_room_banned (room_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (room_id, target_user))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/group/create', methods=['POST'])
def create_group_room():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    room_name = request.form.get('room_name', '').strip()
    if room_name:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO chat_rooms (room_name, created_by) VALUES (%s, %s) RETURNING id", (room_name, user))
            res = cur.fetchone()
            room_id = res[0]
            cur.execute("INSERT INTO room_members (room_id, user_id) VALUES (%s, %s)", (room_id, user))
            conn.commit()
            return redirect(url_for('group_chat', room_id=room_id))
        except Exception as e:
            conn.rollback()
            return f"<script>alert('단톡방 생성 오류: {str(e)}'); history.back();</script>", 500
        finally:
            cur.close()
            conn.close()
    return redirect(url_for('index'))

@app.route('/group/<int:room_id>')
def group_chat(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, room_name FROM chat_rooms WHERE id = %s", (room_id,))
        r = cur.fetchone()
        room = {'id': r[0], 'room_name': r[1]} if r else None
        
        cur.execute("""
            SELECT rm.id, rm.sender, rm.message, rm.created_at, u.nickname 
            FROM room_messages rm
            LEFT JOIN users u ON rm.sender = u.username
            WHERE rm.room_id = %s ORDER BY rm.id ASC
        """, (room_id,))
        messages = [{
            'id': m[0], 'sender': m[1], 'message': m[2], 'created_at': m[3], 'nickname': m[4]
        } for m in cur.fetchall()]
    except Exception as e:
        conn.rollback()
        return f"<script>alert('단톡방 로딩 오류: {str(e)}'); history.back();</script>", 500
    finally:
        cur.close()
        conn.close()
    return render_template('group_chat.html', user=user, room=room, messages=messages)

@app.route('/group/<int:room_id>/send', methods=['POST'])
def send_group_message(room_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    msg = request.form.get('message', '').strip()
    if msg:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO room_messages (room_id, sender, message, created_at) VALUES (%s, %s, %s, %s)",
                        (room_id, user, msg, get_kst_now()))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()
    return redirect(url_for('group_chat', room_id=room_id))

@app.route('/community/ask', methods=['POST'])
def send_anonymous_ask():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    target_id = request.form.get('target_user', '').strip()
    content = request.form.get('content', '').strip()
    if target_id and content:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT username FROM users WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s)) AND is_active = TRUE", (target_id,))
            target_row = cur.fetchone()
            if target_row:
                real_target_id = target_row[0]
                cur.execute("INSERT INTO ask_messages (target_user, sender_id, content, created_at) VALUES (%s, %s, %s, %s)",
                            (real_target_id, user, content, get_kst_now()))
                conn.commit()
                return "<script>alert('익명 메시지를 보냈습니다!'); location.href='/';</script>"
            else:
                return "<script>alert('존재하지 않는 유저입니다.'); history.back();</script>", 400
        except Exception as e:
            conn.rollback()
            return f"<script>alert('전송 오류: {str(e)}'); history.back();</script>", 500
        finally:
            cur.close()
            conn.close()
    return "<script>alert('모든 입력란을 작성해 주세요.'); history.back();</script>", 400

@app.route('/community/post', methods=['POST'])
def create_community_post():
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    content = request.form.get('content', '').strip()
    image = request.files.get('image')
    img_name = None
    if image and image.filename != '':
        img_name = f"cloud_{int(time.time())}_{secure_filename(image.filename)}"
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], img_name))
    if content or img_name:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO community_posts (author_id, content, image_url, created_at) VALUES (%s, %s, %s, %s)",
                        (user, content, img_name, get_kst_now()))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()
    return redirect(url_for('index'))

@app.route('/community/post/<int:post_id>/like', methods=['POST'])
def toggle_post_like(post_id):
    user = session.get('user')
    if not user: return jsonify({"success": False}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM community_likes WHERE post_id = %s AND user_id = %s", (post_id, user))
        if cur.fetchone():
            cur.execute("DELETE FROM community_likes WHERE post_id = %s AND user_id = %s", (post_id, user))
            is_liked = False
        else:
            cur.execute("INSERT INTO community_likes (post_id, user_id) VALUES (%s, %s)", (post_id, user))
            is_liked = True
        cur.execute("SELECT COUNT(*) AS cnt FROM community_likes WHERE post_id = %s", (post_id,))
        res = cur.fetchone()
        cnt = res[0] if res else 0
        conn.commit()
    except Exception:
        conn.rollback()
        return jsonify({"success": False}), 500
    finally:
        cur.close()
        conn.close()
    return jsonify({"success": True, "is_liked": is_liked, "like_count": cnt})

@app.route('/community/post/<int:post_id>/comment', methods=['POST'])
def add_post_comment(post_id):
    user = session.get('user')
    if not user: return redirect(url_for('login'))
    comment = request.form.get('comment', '').strip()
    if comment:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO community_comments (post_id, author_id, comment, created_at) VALUES (%s, %s, %s, %s)",
                        (post_id, user, comment, get_kst_now()))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
