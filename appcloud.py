# app.py — Flask mini sosyal ağ (Py3.9 uyumlu)
import os, uuid
from typing import Optional
from flask import (
    Flask, request, jsonify, render_template_string,
    send_from_directory, session, redirect, url_for, Response, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room, leave_room, send

# YENİ EKLENTİLER: Veritabanı için
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_

# -------------------- FLASK APP (route'lardan ÖNCE!) --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "DEGISTIR_ILK_CALISTIRMADA")

# YENİ: SQLite veritabanı yapılandırması
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)  # SQLAlchemy nesnesi oluştur

# SocketIO'yu başlat
socketio = SocketIO(app, cors_allowed_origins="*")

# -------------------- YÜKLEME KLASÖRLERİ & LİMİTLER --------------------
BASE_DIR = os.path.abspath(os.getcwd())
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
AVATAR_DIR = os.path.join(UPLOAD_DIR, "avatars")
MEDIA_DIR = os.path.join(UPLOAD_DIR, "media")  # video & ses

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)

# Maksimum yükleme boyutu (örn. 200 MB)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".ogg", ".m4v", ".mov"}  # MP4 dahil
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg"}


def is_image(path): return os.path.splitext(path)[1].lower() in IMAGE_EXT


def is_video(path): return os.path.splitext(path)[1].lower() in VIDEO_EXT


def is_audio(path): return os.path.splitext(path)[1].lower() in AUDIO_EXT


# -------------------- VERİ YAPILARI (SQLAlchemy Modelleri) --------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    bio = db.Column(db.String(500))
    avatar = db.Column(db.String(100))
    privacy = db.Column(db.String(10), default='friends')  # 'friends' veya 'public'

    # İlişkiler (Back-references)
    posts = db.relationship('Post', backref='author', lazy='dynamic')
    comments = db.relationship('Comment', backref='commenter', lazy='dynamic')

    def __repr__(self): return f'<User {self.username}>'

    # Şablonda kullanım kolaylığı için
    def get_avatar_path(self):
        return f"/avatar/{self.avatar}" if self.avatar else None


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    likes = db.Column(db.Integer, default=0)

    comments = db.relationship('Comment', backref='parent_post', lazy='dynamic')

    # API için sözlük formatına çevirme
    def to_dict(self):
        return {
            'id': self.id,
            'user': self.author.username,
            'html': self.html_content,
            'likes': self.likes
        }


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    html_content = db.Column(db.Text, nullable=False)

    def to_dict(self):
        return {"user": self.commenter.username, "html": self.html_content}


class Friendship(db.Model):
    __tablename__ = 'friendship'
    # Bu tabloda kullanıcılar arasındaki tüm ilişkiler tutulur: istekler ve onaylanmış arkadaşlar
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)  # İsteği gönderen/arkadaş
    friend_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)  # İsteği alan/arkadaş
    status = db.Column(db.String(10), default='pending')  # 'pending' (bekliyor) veya 'accepted' (kabul)

    # Aynı yönde iki kez istek olmasını engeller
    __table_args__ = (db.UniqueConstraint('user_id', 'friend_id', name='_user_friend_uc'),)


class DirectMessage(db.Model):
    __tablename__ = 'direct_message'
    id = db.Column(db.Integer, primary_key=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    html_content = db.Column(db.Text, nullable=False)

    sender = db.relationship('User', foreign_keys=[from_user_id], backref='sent_dms')
    recipient = db.relationship('User', foreign_keys=[to_user_id], backref='received_dms')


# YENİ EKLENTİ: Aktif canlı yayınları takip etmek için (in-memory kalır)
LIVE_STREAMS = {}  # {"username": "socketio_room_id"}


# -------------------- YARDIMCI VERİTABANI FONKSİYONLARI --------------------

def get_user_by_username(username: Optional[str]) -> Optional[User]:
    if not username: return None
    return User.query.filter_by(username=username).first()


def get_user_by_id(user_id: Optional[int]) -> Optional[User]:
    """
    UYARI GİDERİLDİ: User.query.get(user_id) yerine db.session.get(User, user_id) kullanılır.
    """
    if not user_id: return None
    return db.session.get(User, user_id)


def get_user_id_by_username(username: Optional[str]) -> Optional[int]:
    user = get_user_by_username(username)
    return user.id if user else None


def get_username_by_id(user_id: Optional[int]) -> Optional[str]:
    user = get_user_by_id(user_id)
    return user.username if user else None


def get_friendship_status(user_id, target_id):
    """
    user_id'nin target_id ile ilişkisini döndürür:
    'friend', 'sent', 'received', 'none'
    """
    if user_id == target_id: return 'self'

    # Zaten arkadaş mı? (Çift yönlü kontrol)
    is_friend = Friendship.query.filter(
        or_(
            (Friendship.user_id == user_id) & (Friendship.friend_id == target_id) & (Friendship.status == 'accepted'),
            (Friendship.user_id == target_id) & (Friendship.friend_id == user_id) & (Friendship.status == 'accepted')
        )
    ).first()
    if is_friend: return 'friend'

    # İstek gönderildi mi? (user_id -> target_id, pending)
    sent_req = Friendship.query.filter_by(user_id=user_id, friend_id=target_id, status='pending').first()
    if sent_req: return 'sent'

    # İstek alındı mı? (target_id -> user_id, pending)
    recv_req = Friendship.query.filter_by(user_id=target_id, friend_id=user_id, status='pending').first()
    if recv_req: return 'received'

    return 'none'


def can_view_posts(owner_id: int, viewer_id: Optional[int]) -> bool:
    """owner'ın gönderilerini viewer görebilir mi?"""
    owner = get_user_by_id(owner_id)
    if not owner: return False

    privacy = owner.privacy
    if privacy == "public": return True
    if viewer_id is None: return False
    if viewer_id == owner_id: return True

    # 'friends' ise, kabul edilmiş arkadaşlık var mı kontrol et
    return get_friendship_status(viewer_id, owner_id) == 'friend'


# -------------------- HTML ŞABLON (Aynı kaldı) --------------------
PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9564931444103239"
     crossorigin="anonymous"></script>
<meta charset="utf-8">
<title>MATTIES</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {font-family:system-ui, -apple-system, Roboto, Arial; background:#b6ffb6; margin:0;}
  #header {background:#fff; color:#000; padding:12px 16px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #e5e7eb;}
  #container {max-width:900px;margin:18px auto;padding:0 12px;}
  .card {background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px;margin-bottom:12px;}
  input, textarea, select {width:100%;padding:8px;border-radius:6px;border:1px solid #d1d5db;box-sizing:border-box;margin:6px 0;}
  button {background:#2563eb;color:#fff;border:none;padding:8px 12px;border-radius:6px;cursor:pointer;}
  button:hover {background:#1d4ed8;}
  a {color:#2563eb;text-decoration:none;}
  .muted {color:#6b7280;font-size:0.9rem;}
  .inline {display:inline;}
  .avatar {width:28px;height:28px;border-radius:50%;object-fit:cover;vertical-align:middle;border:1px solid #e5e7eb;margin-right:6px;}
  video, audio, img.media {max-width:100%;border-radius:8px;margin-top:6px;display:block}
</style>
</head>
<body>
  <div id="header">
    <div><strong>MATTIES</strong><div class="muted">Local Wi-Fi demo</div></div>
    <div>
      {% if current_user %}
        {% set me = current_user.username %}
        {% if current_user.avatar %}
          <img class="avatar" src="/avatar/{{current_user.avatar}}" alt="">
        {% endif %}
        Giriş: <a href="/user/{{me}}">@{{me}}</a> |
        <a href="/inbox">Mesajlar</a> |
        <a href="/requests">İstekler</a> |
        <a href="/go_live" style="color:red; font-weight:bold;">🔴 Canlı Yayın Aç</a> | 
        <a href="/logout">Çıkış</a>
      {% else %}
        <a href="/register">Kayıt</a> | <a href="/login">Giriş</a>
      {% endif %}
    </div>
  </div>

  <div id="container">
    <div class="card">
      <form action="/search" method="get" style="display:flex;gap:8px;">
        <input name="q" placeholder="Gönderilerde ara...">
        <button>Ara</button>
      </form>
      <div style="height:8px"></div>
      <form action="/find_friend" method="get" style="display:flex;gap:8px;">
        <input name="name" placeholder="Kullanıcı ara...">
        <button>Bul</button>
      </form>
    </div>

    <div class="card">
      {% if current_user %}
        <form action="/post" method="post" enctype="multipart/form-data">
          <textarea name="text" rows="3" placeholder="Ne düşünüyorsun?"></textarea>
          <div class="muted">Medya yükle (opsiyonel):</div>
          <input type="file" name="photo" accept="image/*">
          <input type="file" name="media" accept="video/*,audio/*">
          <button>Paylaş</button>
        </form>
      {% else %}
        <p>Gönderi paylaşmak için <a href="/login">giriş yap</a> veya <a href="/register">kayıt ol</a>.</p>
      {% endif %}
    </div>

    {% if posts|length == 0 %}
      <div class="card">Henüz gönderi yok veya bu gönderileri görme iznin yok.</div>
    {% else %}
      {% for p in posts %}
        <div class="card">
          {% set author_user = p.author %}
          {% if author_user.avatar %}
            <img class="avatar" src="/avatar/{{author_user.avatar}}" alt="">
          {% endif %}
          <b><a href="/user/{{author_user.username}}">{{author_user.username}}</a></b>
          {% if author_user.username in LIVE_STREAMS %}
             <a href="/live_stream/{{author_user.username}}" style="color:red; font-size:0.8rem; font-weight:bold; margin-left:8px;">🔴 CANLI İZLE</a>
          {% endif %}
          · <span class="muted">#{{p.id}}</span>
          <div style="margin-top:6px;">{{p.html_content|safe}}</div>
          <div style="margin-top:8px;">
            <form action="/like/{{p.id}}" method="post" class="inline">
              <button>❤️ Beğen ({{p.likes}})</button>
            </form>

            {% set target_user = author_user.username %}
            {% if current_user and current_user.username != target_user %}
              {% if target_user in friends_of_current %}
                <span class="muted" style="margin-left:8px;">✅ Arkadaş</span>
              {% elif target_user in req_sent_of_current %}
                <form action="/cancel_request/{{target_user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>↩️ İsteği geri al</button>
                </form>
              {% elif target_user in req_recv_of_current %}
                <form action="/accept_request/{{target_user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>✅ Kabul</button>
                </form>
                <form action="/decline_request/{{target_user}}" method="post" class="inline" style="margin-left:6px;">
                  <button>❌ Reddet</button>
                </form>
              {% else %}
                <form action="/request_friend/{{target_user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>🤝 İstek Gönder</button>
                </form>
              {% endif %}
              <form action="/dm/{{target_user}}" method="get" class="inline" style="margin-left:8px;">
                <button>💬 Mesaj</button>
              </form>
            {% endif %}
          </div>

          <div style="margin-top:10px; border-top:1px solid #e5e7eb; padding-top:8px;">
            <div class="muted" style="margin-bottom:6px;">Yorumlar</div>
            {% set clist = p.comments.all() %}
            {% if clist %}
              {% for c in clist %}
                <div style="margin-bottom:6px;">
                  {% set commenter_user = c.commenter %}
                  {% if commenter_user.avatar %}
                    <img class="avatar" src="/avatar/{{commenter_user.avatar}}" alt="">
                  {% endif %}
                  <b><a href="/user/{{commenter_user.username}}">{{commenter_user.username}}</a>:</b>
                  <span>{{c.html_content|safe}}</span>
                </div>
              {% endfor %}
            {% else %}
              <div class="muted">Henüz yorum yok.</div>
            {% endif %}

            {% if current_user %}
              <form action="/comment/{{p.id}}" method="post" enctype="multipart/form-data" style="margin-top:8px;">
                <textarea name="text" rows="2" placeholder="Yorum yaz..." style="width:100%;"></textarea>
                <input type="file" name="media" accept="video/*,audio/*,image/*">
                <button>Yorum Gönder</button>
              </form>
            {% else %}
              <div class="muted" style="margin-top:6px;">Yorum yapmak için giriş yap.</div>
            {% endif %}
          </div>
          </div>
      {% endfor %}
    {% endif %}
  </div>
</body>
</html>
"""


# -------------------- Range (Partial Content) Sunucu (Aynı kaldı) --------------------
def partial_response(path, mimetype):
    if not os.path.exists(path): abort(404)
    file_size = os.path.getsize(path)
    range_header = request.headers.get('Range', None)
    if not range_header:
        with open(path, 'rb') as f:
            data = f.read()
        return Response(data, 200, mimetype=mimetype, direct_passthrough=True)

    try:
        _, rng = range_header.split('=')
        start_end = rng.split('-')
        start = int(start_end[0]) if start_end[0] else 0
        end = int(start_end[1]) if len(start_end) > 1 and start_end[1] else file_size - 1
        start = max(0, start);
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
    except Exception:
        with open(path, 'rb') as f:
            return Response(f.read(), 200, mimetype=mimetype, direct_passthrough=True)

    length = end - start + 1

    def generate():
        with open(path, 'rb') as f:
            f.seek(start)
            remaining = length
            chunk = 64 * 1024
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data: break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length)
    }
    return Response(generate(), 206, mimetype=mimetype, headers=headers)


# -------------------- ANA SAYFA (gizlilik filtreli) (Aynı kaldı) --------------------
@app.route("/")
def index():
    me_username = session.get("user")
    current_user = get_user_by_username(me_username)
    me_id = current_user.id if current_user else None

    # Gizlilik filtresi: Görüntülenebilecek tüm gönderileri çek
    all_posts = Post.query.order_by(Post.id.desc()).all()
    visible_posts = [p for p in all_posts if can_view_posts(p.user_id, me_id)]

    # Arkadaşlık durumlarını şablon için hazırla
    friends_of_current = set()
    req_sent_of_current = set()
    req_recv_of_current = set()

    if current_user:
        # Benim kabul ettiğim veya bana kabul edilenler
        friends_q = Friendship.query.filter(
            or_(Friendship.user_id == me_id, Friendship.friend_id == me_id),
            Friendship.status == 'accepted'
        ).all()
        for f in friends_q:
            friend_id = f.friend_id if f.user_id == me_id else f.user_id
            friends_of_current.add(get_username_by_id(friend_id))

        # Benim gönderdiğim bekleyen istekler
        sent_q = Friendship.query.filter_by(user_id=me_id, status='pending').all()
        req_sent_of_current = {get_username_by_id(f.friend_id) for f in sent_q}

        # Bana gelen bekleyen istekler
        recv_q = Friendship.query.filter_by(friend_id=me_id, status='pending').all()
        req_recv_of_current = {get_username_by_id(f.user_id) for f in recv_q}

    return render_template_string(
        PAGE,
        posts=visible_posts,
        LIVE_STREAMS=LIVE_STREAMS,
        current_user=current_user,
        friends_of_current=friends_of_current,
        req_sent_of_current=req_sent_of_current,
        req_recv_of_current=req_recv_of_current,
    )


# -------------------- KAYIT & GİRİŞ (Aynı kaldı) --------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        priv = (request.form.get("privacy") or "friends").strip().lower()
        if priv not in {"friends", "public"}: priv = "friends"
        if not username or not password:
            return "Kullanıcı adı ve şifre gerekli. <a href='/register'>&larr; Geri</a>", 400

        if User.query.filter_by(username=username).first():
            return "Bu kullanıcı adı alınmış. <a href='/register'>&larr; Geri</a>", 400

        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),
            bio=bio,
            avatar=None,
            privacy=priv
        )
        db.session.add(new_user)
        db.session.commit()

        session["user"] = username
        return redirect(url_for("index"))
    return """
    <h2>Kayıt Ol</h2>
    <form method="post">
      <input name="username" placeholder="Kullanıcı adı"><br>
      <input name="password" type="password" placeholder="Şifre"><br>
      <textarea name="bio" rows="2" placeholder="Bio (opsiyonel)"></textarea><br>
      <label>Gizlilik:
        <select name="privacy">
          <option value="friends" selected>Sadece arkadaşlar</option>
          <option value="public">Herkese açık</option>
        </select>
      </label><br>
      <button>Kayıt ol</button>
    </form>
    <p><a href='/'>Geri</a></p>
    """


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        u = User.query.filter_by(username=username).first()
        if not u or not check_password_hash(u.password_hash, password):
            return "Geçersiz kimlik. <a href='/login'>&larr; Geri</a>", 401

        session["user"] = username
        return redirect(url_for("index"))
    return """
    <h2>Giriş Yap</h2>
    <form method="post">
      <input name="username" placeholder="Kullanıcı adı"><br>
      <input name="password" type="password" placeholder="Şifre"><br>
      <button>Giriş</button>
    </form>
    <p><a href='/register'>Kayıt ol</a></p>
    """


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))


# -------------------- PROFİL & AVATAR (Aynı kaldı) --------------------
@app.route("/user/<username>", methods=["GET", "POST"])
def profile(username):
    user = get_user_by_username(username)
    if not user: return "Kullanıcı bulunamadı.", 404

    me_username = session.get("user")
    current_user = get_user_by_username(me_username)
    me_id = current_user.id if current_user else None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "avatar":
            if not current_user or current_user.username != username:
                return "Yetkisiz işlem.", 403
            file = request.files.get("avatar")
            if not file or not file.filename:
                return redirect(url_for("profile", username=username))
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in IMAGE_EXT:
                return "Sadece resim dosyası yükleyin.", 400
            safe = secure_filename(file.filename)
            stem, _ = os.path.splitext(safe)
            unique = f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
            file.save(os.path.join(AVATAR_DIR, unique))

            old = user.avatar
            if old and old != unique:
                try:
                    os.remove(os.path.join(AVATAR_DIR, old))
                except OSError:
                    pass

            user.avatar = unique
            db.session.commit()
            return redirect(url_for("profile", username=username))

        elif action == "privacy":
            if not current_user or current_user.username != username:
                return "Yetkisiz işlem.", 403
            priv = (request.form.get("privacy") or "friends").strip().lower()
            if priv in {"friends", "public"}:
                user.privacy = priv
                db.session.commit()
            return redirect(url_for("profile", username=username))

    user_posts = user.posts.order_by(Post.id.desc()).all()
    bio = user.bio or ''
    uav = user.avatar
    av_html = f"<img src='/avatar/{uav}' style='width:120px;height:120px;border-radius:50%;object-fit:cover;border:2px solid #e5e7eb;'>" if uav else "<div class='muted' style='margin:8px 0;'>Profil fotoğrafı yok</div>"
    privacy = user.privacy

    status = get_friendship_status(me_id, user.id) if current_user else 'none'

    html = f"""
    <h2>{username} — Profil</h2>
    <div style="margin:8px 0;">{av_html}</div>
    <p><i>Bio:</i> {bio or '(bio yok)'}</p>
    <p><i>Gizlilik:</i> {"Sadece arkadaşlar" if privacy == "friends" else "Herkese açık"}</p>
    <p><a href='/'>Geri</a></p>
    """

    # YENİ EKLENTİ: CANLI YAYIN DURUMU
    if username in LIVE_STREAMS:
        html += f"<p><a href='/live_stream/{username}' style='color:red; font-weight:bold;'>🔴 CANLI YAYINDA! İzle</a></p>"

    if current_user and current_user.username == username:
        html += f"""
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="avatar">
          <input type="file" name="avatar" accept="image/*" required>
          <button>Profil fotoğrafını güncelle</button>
        </form>
        <form method="post" style="margin-top:8px;">
          <input type="hidden" name="action" value="privacy">
          <label>Gizlilik:
            <select name="privacy">
              <option value="friends" {"selected" if privacy == "friends" else ""}>Sadece arkadaşlar</option>
              <option value="public" {"selected" if privacy == "public" else ""}>Herkese açık</option>
            </select>
          </label>
          <button>Kaydet</button>
        </form>
        """

    if current_user and current_user.username != username:
        if status == 'friend':
            html += "<p>✅ Arkadaşsınız</p>"
        elif status == 'sent':
            html += f"<form action='/cancel_request/{username}' method='post'><button>↩️ İsteği geri al</button></form>"
        elif status == 'received':
            html += f"<form action='/accept_request/{username}' method='post' style='display:inline;'><button>✅ Kabul</button></form>"
            html += f"<form action='/decline_request/{username}' method='post' style='display:inline;margin-left:6px;'><button>❌ Reddet</button></form>"
        else:  # 'none'
            html += f"<form action='/request_friend/{username}' method='post'><button>🤝 İstek Gönder</button></form>"
        html += f"<p><a href='/dm/{username}'>💬 Mesaj Gönder</a></p>"

    html += "<hr>"

    if not can_view_posts(user.id, me_id):
        html += "<p><b>Bu kullanıcı gönderilerini sadece arkadaşlarıyla paylaşıyor.</b></p>"
        return html

    if not user_posts:
        html += "<p>Henüz gönderi yok.</p>"
    else:
        for p in user_posts:
            html += f"<div><b>{p.author.username}</b>: {p.html_content}</div><br>"
    return html


@app.route("/avatar/<filename>")
def serve_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)


# -------------------- GÖNDERİLER (POST) (Aynı kaldı) --------------------
def save_media(file_storage, target_dir):
    safe = secure_filename(file_storage.filename)
    stem, ext = os.path.splitext(safe)
    unique = f"{stem}_{uuid.uuid4().hex[:8]}{ext.lower()}"
    file_storage.save(os.path.join(target_dir, unique))
    return unique


@app.route("/post", methods=["POST"])
def post():
    if "user" not in session: return "Giriş yapmanız gerekiyor.", 401
    current_user = get_user_by_username(session["user"])
    if not current_user: return "Kullanıcı bulunamadı.", 404

    text = (request.form.get("text") or "").strip()
    photo = request.files.get("photo")
    media = request.files.get("media")

    parts = []
    if text:
        parts.append(text.replace("\n", "<br>"))

    # ... (Medya kaydetme kısmı aynı kalır) ...
    if photo and photo.filename:
        if not is_image(photo.filename): return "Sadece resim yükleyin (jpg, png, webp...).", 400
        img_name = save_media(photo, UPLOAD_DIR)
        parts.append(f"<img class='media' src='/uploads/{img_name}' alt=''>")

    if media and media.filename:
        ext = os.path.splitext(media.filename)[1].lower()
        if ext not in VIDEO_EXT.union(AUDIO_EXT): return "Desteklenmeyen medya biçimi.", 400
        media_name = save_media(media, MEDIA_DIR)
        if ext in VIDEO_EXT:
            parts.append(f"<video controls preload='metadata' src='/media/{media_name}'></video>")
        else:
            parts.append(f"<audio controls src='/media/{media_name}'></audio>")

    if not parts: return "Boş gönderi olmaz.", 400

    new_post = Post(user_id=current_user.id, html_content="<br>".join(parts), likes=0)
    db.session.add(new_post)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/like/<int:post_id>", methods=["POST"])
def like_post(post_id):
    me = session.get("user")
    current_user = get_user_by_username(me)
    me_id = current_user.id if current_user else None

    # UYARI GİDERİLDİ: Post.query.get yerine db.session.get kullanıldı
    post = db.session.get(Post, post_id)
    if post and can_view_posts(post.user_id, me_id):
        post.likes += 1
        db.session.commit()

    return redirect(url_for("index"))


# -------------------- YORUMLAR (Aynı kaldı) --------------------
@app.route("/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id):
    if "user" not in session: return redirect(url_for("login"))
    current_user = get_user_by_username(session["user"])
    if not current_user: return redirect(url_for("login"))

    # UYARI GİDERİLDİ: Post.query.get yerine db.session.get kullanıldı
    target_post = db.session.get(Post, post_id)
    if not target_post: return "Gönderi bulunamadı.", 404
    if not can_view_posts(target_post.user_id, current_user.id): return "İzniniz yok.", 403

    text = (request.form.get("text") or "").strip()
    media = request.files.get("media")
    # ... (Medya işleme ve parts oluşturma kısmı aynı kalır) ...
    parts = []
    if text: parts.append(text.replace("\n", "<br>"))
    if media and media.filename:
        ext = os.path.splitext(media.filename)[1].lower()
        if ext in IMAGE_EXT:
            name = save_media(media, UPLOAD_DIR)
            parts.append(f"<img class='media' src='/uploads/{name}' alt=''>")
        elif ext in VIDEO_EXT:
            name = save_media(media, MEDIA_DIR)
            parts.append(f"<video controls preload='metadata' src='/media/{name}'></video>")
        elif ext in AUDIO_EXT:
            name = save_media(media, MEDIA_DIR)
            parts.append(f"<audio controls src='/media/{name}'></audio>")
        else:
            return "Desteklenmeyen medya tipi.", 400

    if not parts: return redirect(request.referrer or url_for("index"))

    new_comment = Comment(post_id=post_id, user_id=current_user.id, html_content="<br>".join(parts))
    db.session.add(new_comment)
    db.session.commit()
    return redirect(request.referrer or url_for("index"))


# -------------------- ARKADAŞLIK (Aynı kaldı) --------------------

@app.route("/request_friend/<username>", methods=["POST"])
def request_friend(username):
    me = get_user_by_username(session.get("user"))
    target = get_user_by_username(username)
    if not me or not target: return redirect(url_for("login"))
    if me.id == target.id: return redirect(url_for("profile", username=me.username))

    status = get_friendship_status(me.id, target.id)

    if status == 'friend' or status == 'sent':
        return redirect(request.referrer or url_for("profile", username=username))

    # Gelen istek varsa, direkt kabul et (eski davranış)
    if status == 'received':
        req = Friendship.query.filter_by(user_id=target.id, friend_id=me.id, status='pending').first()
        if req:
            req.status = 'accepted'
            db.session.commit()
            return redirect(request.referrer or url_for("profile", username=username))

    # Yeni istek gönder
    new_req = Friendship(user_id=me.id, friend_id=target.id, status='pending')
    db.session.add(new_req)
    db.session.commit()
    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/cancel_request/<username>", methods=["POST"])
def cancel_request(username):
    me = get_user_by_username(session.get("user"))
    target = get_user_by_username(username)
    if not me or not target: return redirect(url_for("login"))

    # Benim gönderdiğim bekleyen isteği bul ve sil
    req = Friendship.query.filter_by(user_id=me.id, friend_id=target.id, status='pending').first()
    if req:
        db.session.delete(req)
        db.session.commit()

    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/accept_request/<username>", methods=["POST"])
def accept_request(username):
    me = get_user_by_username(session.get("user"))
    target = get_user_by_username(username)
    if not me or not target: return redirect(url_for("login"))

    # Target'ın bana gönderdiği bekleyen isteği bul ve 'accepted' yap
    req = Friendship.query.filter_by(user_id=target.id, friend_id=me.id, status='pending').first()
    if req:
        req.status = 'accepted'
        db.session.commit()

    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/decline_request/<username>", methods=["POST"])
def decline_request(username):
    me = get_user_by_username(session.get("user"))
    target = get_user_by_username(username)
    if not me or not target: return redirect(url_for("login"))

    # Target'ın bana gönderdiği bekleyen isteği bul ve sil
    req = Friendship.query.filter_by(user_id=target.id, friend_id=me.id, status='pending').first()
    if req:
        db.session.delete(req)
        db.session.commit()

    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/requests")
def requests_box():
    me = get_user_by_username(session.get("user"))
    if not me: return redirect(url_for("login"))
    me_id = me.id

    # Gelen istekler (başka biri bana gönderdi)
    incoming_reqs = Friendship.query.filter_by(friend_id=me_id, status='pending').all()
    incoming = sorted([get_username_by_id(r.user_id) for r in incoming_reqs])

    # Giden istekler (ben başkasına gönderdim)
    outgoing_reqs = Friendship.query.filter_by(user_id=me_id, status='pending').all()
    outgoing = sorted([get_username_by_id(r.friend_id) for r in outgoing_reqs])

    html = "<h2>İstek Kutusu</h2><p><a href='/'>Geri</a></p><hr><h3>Gelen</h3>"
    if not incoming:
        html += "<p>Yok.</p>"
    else:
        for u in incoming:
            html += f"<div><b>{u}</b> <form action='/accept_request/{u}' method='post' style='display:inline;'><button>✅</button></form> <form action='/decline_request/{u}' method='post' style='display:inline;margin-left:6px;'><button>❌</button></form></div><br>"
    html += "<h3>Gönderilen</h3>"
    if not outgoing:
        html += "<p>Yok.</p>"
    else:
        for u in outgoing:
            html += f"<div><b>{u}</b> <form action='/cancel_request/{u}' method='post' style='display:inline;margin-left:8px;'><button>↩️ Geri al</button></form></div><br>"
    return html


# -------------------- DM (Aynı kaldı) --------------------
@app.route("/inbox")
def inbox():
    me = get_user_by_username(session.get("user"))
    if not me: return redirect(url_for("login"))
    me_id = me.id

    # Benim gönderdiğim ve aldığım tüm mesajları çeker
    dms = DirectMessage.query.filter(or_(DirectMessage.from_user_id == me_id, DirectMessage.to_user_id == me_id)).all()

    # Konuşulan kullanıcı adlarını bul
    users = set()
    for m in dms:
        if m.from_user_id != me_id:
            users.add(m.sender.username)
        if m.to_user_id != me_id:
            users.add(m.recipient.username)

    sorted_users = sorted(list(users))

    html = "<h2>Mesajlar</h2><p><a href='/'>Geri</a></p><hr>"
    if not sorted_users:
        html += "<p>Henüz konuşma yok.</p>"
    else:
        for u in sorted_users:
            html += f"<div><a href='/dm/{u}'>@{u}</a></div>"
    return html


@app.route("/dm/<username>", methods=["GET", "POST"])
def dm(username):
    me = get_user_by_username(session.get("user"))
    target = get_user_by_username(username)
    if not me or not target: return redirect(url_for("login"))

    if request.method == "POST":
        msg = (request.form.get("text") or "").strip()
        media = request.files.get("media")
        parts = []
        # ... (Medya işleme ve parts oluşturma kısmı aynı kalır) ...
        if msg: parts.append(msg.replace("\n", "<br>"))
        if media and media.filename:
            ext = os.path.splitext(media.filename)[1].lower()
            if ext in IMAGE_EXT:
                name = save_media(media, UPLOAD_DIR)
                parts.append(f"<img class='media' src='/uploads/{name}' alt=''>")
            elif ext in VIDEO_EXT:
                name = save_media(media, MEDIA_DIR)
                parts.append(f"<video controls preload='metadata' src='/media/{name}'></video>")
            elif ext in AUDIO_EXT:
                name = save_media(media, MEDIA_DIR)
                parts.append(f"<audio controls src='/media/{name}'></audio>")
            else:
                return "Desteklenmeyen medya tipi.", 400

        if parts:
            new_dm = DirectMessage(from_user_id=me.id, to_user_id=target.id, html_content="<br>".join(parts))
            db.session.add(new_dm)
            db.session.commit()

        return redirect(url_for("dm", username=username))

    # Konuşmayı çek (Gönderen veya alıcı benim/target olduğu mesajlar)
    conv = DirectMessage.query.filter(
        or_(
            (DirectMessage.from_user_id == me.id) & (DirectMessage.to_user_id == target.id),
            (DirectMessage.from_user_id == target.id) & (DirectMessage.to_user_id == me.id)
        )
    ).order_by(DirectMessage.id).all()

    html = f"<h2>{username} ile yazışma</h2><p><a href='/'>Geri</a></p><hr>"
    for m in conv:
        sender = "Ben" if m.from_user_id == me.id else m.sender.username
        html += f"<p><b>{sender}:</b><br>{m.html_content}</p><hr>"

    html += """<form method='post' enctype='multipart/form-data'>
      <textarea name='text' rows='2' placeholder='Mesaj.'></textarea><br>
      <input type='file' name='media' accept='image/*,video/*,audio/*'><br>
      <button>Gönder</button>
    </form>"""
    return html


# -------------------- DOSYA SERVİSİ (Aynı kaldı) --------------------
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/media/<path:filename>")
def serve_media(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in VIDEO_EXT:
        mimetype = "video/mp4" if ext in {".mp4", ".m4v"} else ("video/webm" if ext == ".webm" else "video/ogg")
    elif ext in AUDIO_EXT:
        if ext == ".mp3":
            mimetype = "audio/mpeg"
        elif ext == ".wav":
            mimetype = "audio/wav"
        elif ext == ".m4a":
            mimetype = "audio/mp4"
        else:
            mimetype = "audio/ogg"
    else:
        mimetype = "application/octet-stream"
    return partial_response(os.path.join(MEDIA_DIR, filename), mimetype)


# -------------------- ARAMA (Aynı kaldı) --------------------
@app.route("/search")
def search():
    me_user = get_user_by_username(session.get("user"))
    me_id = me_user.id if me_user else None
    q = (request.args.get("q") or "").strip().lower()
    if not q: return redirect(url_for("index"))

    # Arama: Gizlilik kurallarına uyan ve içeriği veya kullanıcı adı aranan metni içeren gönderileri bul
    # (SQLAlchemy'de LIKE/ilike kullanarak)
    results_q = Post.query.join(User).filter(
        or_(
            Post.html_content.ilike(f'%{q}%'),
            User.username.ilike(f'%{q}%')
        )
    ).order_by(Post.id.desc()).all()

    results = [p for p in results_q if can_view_posts(p.user_id, me_id)]

    html = f"<h2>Arama: {q}</h2><p><a href='/'>Geri</a></p><hr>"
    if not results:
        html += "<p>Sonuç yok ya da görebileceğin gönderi yok.</p>"
    else:
        for p in results:
            html += f"<div><b>{p.author.username}</b>: {p.html_content}</div><br>"
    return html


@app.route("/find_friend")
def find_friend():
    q = (request.args.get("name") or "").strip().lower()
    if not q: return redirect(url_for("index"))

    me_user = get_user_by_username(session.get("user"))
    me_id = me_user.id if me_user else None

    # Kullanıcı adında arama terimini içeren kullanıcıları bul
    matches = User.query.filter(User.username.ilike(f'%{q}%')).all()

    html = f"<h2>Kullanıcı Ara: {q}</h2><p><a href='/'>Geri</a></p><hr>"
    if not matches:
        html += "<p>Yok.</p>"
    else:
        for u in matches:
            html += f"<div><b><a href='/user/{u.username}'>{u.username}</a></b>"
            if me_user and me_user.id != u.id:
                status = get_friendship_status(me_id, u.id)
                if status == 'friend':
                    html += " <span class='muted'>✅ Arkadaş</span>"
                elif status == 'sent':
                    html += f" <form action='/cancel_request/{u.username}' method='post' style='display:inline;margin-left:8px;'><button>↩️ Geri al</button></form>"
                elif status == 'received':
                    html += f" <form action='/accept_request/{u.username}' method='post' style='display:inline;margin-left:8px;'><button>✅</button></form>"
                    html += f" <form action='/decline_request/{u.username}' method='post' style='display:inline;margin-left:6px;'><button>❌</button></form>"
                else:
                    html += f" <form action='/request_friend/{u.username}' method='post' style='display:inline;margin-left:8px;'><button>🤝 İstek</button></form>"
            html += "</div><br>"
    return html


# -------------------- CANLI YAYIN (WEBRTC Sinyalleşme) (Aynı kaldı) --------------------

@app.route("/go_live")
def go_live_page():
    """Yayıncının kamera/ekran paylaşımını başlattığı sayfa."""
    me = session.get("user")
    if not me: return redirect(url_for('login'))
    return render_template_string(LIVE_STREAM_PAGE_TEMPLATE, streamer_user=me)


@app.route("/live_stream/<string:username>")
def live_stream_page(username):
    """İzleyicilerin yayını izlediği sayfa."""
    if username not in LIVE_STREAMS:
        return redirect(url_for('index'))
    return render_template_string(LIVE_VIEWER_PAGE_TEMPLATE, streamer_user=username, viewer_user=session.get("user"))


# SocketIO Olay Yöneticileri (Aynı kaldı)
@socketio.on('join_live_room')
def handle_join_live_room(data):
    """Yayıncı veya izleyici odaya katılır."""
    username = data.get('username')
    streamer = data.get('streamer')
    me = session.get("user")

    if get_user_by_username(streamer):
        room_id = f"live_{streamer}"
        join_room(room_id)
        print(f"User {username} joined live room {room_id} (SID: {request.sid})")

        if username == streamer:
            # Yayıncı odaya katıldı
            LIVE_STREAMS[streamer] = room_id
            print(f"Streamer {streamer} is now active.")
        elif streamer in LIVE_STREAMS:
            # İzleyici odaya katıldı, yayıncıya haber ver
            emit('new_viewer', {'viewer_id': request.sid, 'viewer_user': me}, room=f"live_{streamer}",
                 include_self=False)


@socketio.on('disconnect')
def handle_disconnect():
    """Kullanıcı ayrıldığında yayını kontrol et."""
    # Yayıncının ayrılıp ayrılmadığını kontrol et
    disconnected_user = None
    for user, room in LIVE_STREAMS.items():
        if room == f"live_{user}":
            disconnected_user = user
            break

    if disconnected_user:
        LIVE_STREAMS.pop(disconnected_user, None)
        room_id = f"live_{disconnected_user}"
        emit('stream_status', {'status': 'stopped'}, room=room_id, broadcast=True)
        print(f"Streamer {disconnected_user} disconnected. Live stream stopped.")
    else:
        # Ayrılan bir izleyiciyse, yayıncıya haber ver
        for streamer in LIVE_STREAMS.keys():
            emit('viewer_left', {'viewer_id': request.sid}, room=f"live_{streamer}")


@socketio.on('webrtc_signal')
def handle_webrtc_signal(data):
    """WebRTC Sinyalleşmesini (SDP/ICE) ilgili tarafa yönlendir."""
    target_sid = data.get('target_sid')  # Hedef SocketID (izleyici/yayıncı)
    signal_data = data.get('signal')

    if target_sid:
        emit('webrtc_signal', {'signal': signal_data, 'sender_sid': request.sid}, room=target_sid)


# -------------------- Canlı Yayın HTML ve JS Şablonları (Aynı kaldı) --------------------
LIVE_STREAM_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9564931444103239"
     crossorigin="anonymous"></script>
<meta charset="utf-8">
<title>CANLI YAYIN: {{ streamer_user }}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
<style>
  body {font-family:system-ui, -apple-system, Roboto, Arial; background:#000; color:#fff; text-align:center;}
  #localVideo {max-width:80%; max-height:80vh; margin:20px auto; border: 4px solid red; display:block; background:#333;}
  .controls button {padding: 10px 20px; font-size: 16px; cursor: pointer; margin: 5px; background: #2563eb; color:#fff; border:none; border-radius:6px;}
  .controls button:disabled {background:#6b7280; cursor:not-allowed;}
  a {color:#2563eb;text-decoration:none;}
</style>
</head>
<body>
  <h1>🔴 CANLI YAYIN: {{ streamer_user }}</h1>
  <p><a href="/">Ana Sayfaya Dön</a></p>

  {% if streamer_user in LIVE_STREAMS %}
      <p style="color:red;">Zaten canlı yayındasınız. Lütfen izleyicilerin sizi <a href="/live_stream/{{ streamer_user }}" style="color:lightgray;">buradan</a> izlemesini sağlayın.</p>
      <video id="localVideo" autoplay muted></video>
      <div class="controls">
        <button id="stopBtn">Yayını Durdur</button>
        <p id="status">Aktif Yayın...</p>
      </div>
  {% else %}
      <video id="localVideo" autoplay muted style="display:none;"></video>
      <div class="controls">
        <button id="startBtn">Yayını Başlat (Kamera/Ekran İzni İste)</button>
        <button id="stopBtn" disabled>Yayını Durdur</button>
        <p id="status">Hazır. Yayın tipini seçin.</p>
      </div>
  {% endif %}


<script>
    // -------------------- JAVASCRIPT / WEBRTC BAŞLANGIÇ --------------------
    const socket = io();
    const localVideo = document.getElementById('localVideo');
    const startBtn = document.getElementById('startBtn'); // YENİ: Tek başlangıç butonu
    const stopBtn = document.getElementById('stopBtn');
    const statusDiv = document.getElementById('status');
    const streamerUser = "{{ streamer_user }}";

    let localStream = null;
    let peerConnections = {}; // İzleyiciler için PeerConnection objeleri

    // Yayıncı odaya katılır (Socket.io)
    socket.on('connect', () => {
        socket.emit('join_live_room', { username: streamerUser, streamer: streamerUser });
    });

    async function startStream() {
        if (!socket.connected) {
             statusDiv.textContent = 'Socket bağlantısı yok. Sayfayı yenileyin.';
             return;
        }

        statusDiv.textContent = 'Kamera erişimi bekleniyor. Tarayıcınızdan izin verin...';

        // YENİ DÜZENLEME: İlk olarak kamera izni iste. Ekran paylaşımı için ek bir butona ihtiyaç var.
        try {
            // Kamera yayını izni istenir (Tarayıcı bu noktada izin penceresini gösterir)
            localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });

            localVideo.srcObject = localStream;
            localVideo.style.display = 'block';

            startBtn.disabled = true;
            stopBtn.disabled = false;
            statusDiv.textContent = 'Canlı yayın başladı. İzleyiciler bekleniyor... Ekran paylaşmak için yayını durdurup tekrar başlayın.';

            // Medya akışını durdurma olayını dinle (Ekran paylaşımında kullanıcı durdurursa)
            localStream.getVideoTracks()[0].onended = stopLiveStream;

        } catch (error) {
            console.warn("Kamera izni reddedildi. Ekran paylaşımı denenecek: ", error);
            statusDiv.textContent = 'Kamera izni reddedildi. Ekran paylaşımı izni bekleniyor...';

            // İzin verilmezse, ekran paylaşımı izni istenir
            try {
                localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
                localVideo.srcObject = localStream;
                localVideo.style.display = 'block';

                startBtn.disabled = true;
                stopBtn.disabled = false;
                statusDiv.textContent = 'Ekran Yayını başladı. İzleyiciler bekleniyor...';

                localStream.getVideoTracks()[0].onended = stopLiveStream;

            } catch (screenError) {
                console.error("Yayın başlatılamadı: Kamera ve Ekran izni reddedildi.", screenError);
                statusDiv.textContent = 'Yayın başlatılamadı. Hem Kamera hem de Ekran izinleri reddedildi.';
                return;
            }
        }
    }

    function stopLiveStream() {
        if (localStream) {
            localStream.getTracks().forEach(track => track.stop());
            localStream = null;
        }

        // Tüm PeerConnection'ları kapat
        for (const sid in peerConnections) {
            peerConnections[sid].close();
        }
        peerConnections = {};

        // Sadece Yayıncı Sayfasında butonları yeniden aktif et
        if (startBtn) startBtn.disabled = false; // YENİ
        if (stopBtn) stopBtn.disabled = true;

        if (localVideo) localVideo.style.display = 'none';

        statusDiv.textContent = 'Yayın durduruldu.';

        // Sunucuya yayını durdurduğunu bildir (Gerekli değil, disconnect halleder)
        socket.disconnect();
        setTimeout(() => socket.connect(), 100);
    }

    // İzleyicilerden Gelen İstekleri Yönetme
    socket.on('new_viewer', async (data) => {
        if (!localStream) { return; } // Yayın başlamadıysa ignore et

        const viewerSid = data.viewer_id;
        console.log('Yeni izleyici bağlandı:', data.viewer_user, viewerSid);

        const pc = createPeerConnection(viewerSid);
        peerConnections[viewerSid] = pc;

        // Local stream'deki tüm track'leri PeerConnection'a ekle
        localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

        try {
            // SDP Offer oluştur ve izleyiciye gönder
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            socket.emit('webrtc_signal', {
                target_sid: viewerSid,
                signal: {
                    type: 'offer',
                    sdp: pc.localDescription.sdp
                }
            });
        } catch (e) {
            console.error("Offer oluşturulamadı:", e);
        }
    });

    // Sinyalleşme Verilerini Yönetme (Answer, ICE Candidate)
    socket.on('webrtc_signal', async (data) => {
        const signal = data.signal;
        const senderSid = data.sender_sid;

        if (signal.type === 'answer' && peerConnections[senderSid]) {
            try {
                // İzleyiciden Answer geldi, PeerConnection'a ayarla
                await peerConnections[senderSid].setRemoteDescription(new RTCSessionDescription(signal));
            } catch (e) { console.error("Set remote description failed (Answer):", e); }
        } else if (signal.type === 'candidate' && peerConnections[senderSid]) {
            try {
                // ICE Candidate geldi, PeerConnection'a ekle
                await peerConnections[senderSid].addIceCandidate(new RTCIceCandidate(signal.candidate));
            } catch (e) { console.warn("Add ICE candidate failed:", e); }
        }
    });

    // PeerConnection Oluşturma Fonksiyonu
    function createPeerConnection(targetSid) {
        const pc = new RTCPeerConnection({
            iceServers: [ { urls: 'stun:stun.l.google.com:19302' } ] // STUN sunucusu
        });

        // Kendi ICE Candidate'lerimizi izleyiciye gönder
        pc.onicecandidate = (event) => {
            if (event.candidate) {
                socket.emit('webrtc_signal', {
                    target_sid: targetSid,
                    signal: {
                        type: 'candidate',
                        candidate: event.candidate
                    }
                });
            }
        };

        return pc;
    }

    // İzleyici ayrıldı
    socket.on('viewer_left', (data) => {
        const viewerSid = data.viewer_id;
        if (peerConnections[viewerSid]) {
            peerConnections[viewerSid].close();
            delete peerConnections[viewerSid];
            console.log('İzleyici bağlantısı kapandı:', viewerSid);
        }
    });


    if (startBtn) startBtn.onclick = startStream; // YENİ
    if (stopBtn) stopBtn.onclick = stopLiveStream;
    // -------------------- JAVASCRIPT / WEBRTC BİTİŞ --------------------
</script>
</body>
</html>
"""

LIVE_VIEWER_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9564931444103239"
     crossorigin="anonymous"></script>
<meta charset="utf-8">
<title>CANLI YAYIN İZLE: {{ streamer_user }}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
<style>
  body {font-family:system-ui, -apple-system, Roboto, Arial; background:#000; color:#fff; text-align:center;}
  #remoteVideo {max-width:90%; max-height:90vh; margin:20px auto; border: 4px solid white; display:block; background:#333;}
  a {color:#2563eb;text-decoration:none;}
</style>
</head>
<body>
  <h1>🔴 Canlı Yayın: {{ streamer_user }}</h1>
  <p><a href="/">Ana Sayfaya Dön</a></p>
  <video id="remoteVideo" autoplay controls></video>
  <p id="status">Yayına bağlanılıyor...</p>

<script>
    // -------------------- JAVASCRIPT / WEBRTC İZLEYİCİ BAŞLANGIÇ --------------------
    const socket = io();
    const remoteVideo = document.getElementById('remoteVideo');
    const statusDiv = document.getElementById('status');
    const streamerUser = "{{ streamer_user }}";
    const viewerUser = "{{ viewer_user or 'viewer' }}";

    let peerConnection = null;
    let streamerSid = null; // Yayıncının socket ID'si
    let isConnected = false;

    // 1. Odaya Katıl
    socket.on('connect', () => {
        socket.emit('join_live_room', { username: viewerUser, streamer: streamerUser });
    });

    // 2. PeerConnection Oluşturma Fonksiyonu
    function createPeerConnection() {
        const pc = new RTCPeerConnection({
            iceServers: [ { urls: 'stun:stun.l.google.com:19302' } ]
        });

        // Uzak stream'i (yayıncının videosu) aldığımızda video elementine ata
        pc.ontrack = (event) => {
            if (remoteVideo.srcObject !== event.streams[0]) {
                remoteVideo.srcObject = event.streams[0];
                statusDiv.textContent = 'Canlı yayın izleniyor!';
                isConnected = true;
            }
        };

        pc.oniceconnectionstatechange = () => {
             console.log("ICE state:", pc.iceConnectionState);
             if (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected') {
                 statusDiv.textContent = 'Bağlantı kesildi. Tekrar deneniyor...';
                 // Tekrar bağlanmayı dene
                 setTimeout(() => socket.emit('join_live_room', { username: viewerUser, streamer: streamerUser }), 3000);
             }
        };


        // Kendi ICE Candidate'lerimizi yayıncıya gönder
        pc.onicecandidate = (event) => {
            if (event.candidate && streamerSid) {
                // Sinyali yayıncıya geri gönder
                socket.emit('webrtc_signal', {
                    target_sid: streamerSid, // Yayıncının Socket ID'si
                    signal: {
                        type: 'candidate',
                        candidate: event.candidate
                    }
                });
            }
        };

        return pc;
    }

    // 3. Sinyalleşme Verilerini Yönetme (Offer, Answer, ICE Candidate)
    socket.on('webrtc_signal', async (data) => {
        const signal = data.signal;

        if (signal.type === 'offer') {
            // Yayıncıdan Offer (Teklif) geldi.
            streamerSid = data.sender_sid; // Yayıncının Socket ID'sini kaydet

            if (!peerConnection) {
                peerConnection = createPeerConnection();
            }

            try {
                await peerConnection.setRemoteDescription(new RTCSessionDescription(signal));

                // Answer oluştur ve yayıncıya gönder
                const answer = await peerConnection.createAnswer();
                await peerConnection.setLocalDescription(answer);

                socket.emit('webrtc_signal', {
                    target_sid: streamerSid,
                    signal: {
                        type: 'answer',
                        sdp: peerConnection.localDescription.sdp
                    }
                });
                statusDiv.textContent = 'Bağlantı kuruluyor...';
            } catch (e) {
                console.error("WebRTC Offer/Answer hatası:", e);
                statusDiv.textContent = 'Bağlantı hatası oluştu.';
            }

        } else if (signal.type === 'candidate' && peerConnection) {
            // ICE Candidate geldi, PeerConnection'a ekle
            try {
                await peerConnection.addIceCandidate(new RTCIceCandidate(signal.candidate));
            } catch (e) { console.warn("Add ICE candidate failed:", e); }
        }
    });

    // 4. Yayın Durumu
    socket.on('stream_status', (data) => {
        if (data.status === 'stopped') {
            statusDiv.textContent = '🔴 Canlı Yayın sona erdi. Ana sayfaya yönlendiriliyorsunuz...';
            if (peerConnection) {
                peerConnection.close();
                peerConnection = null;
                remoteVideo.srcObject = null;
            }
             setTimeout(() => window.location.href = '/', 3000);
        }
    });

    // -------------------- JAVASCRIPT / WEBRTC İZLEYİCİ BİTİŞ --------------------
</script>
</body>
</html>
"""


# -------------------- API (SQLAlchemy'ye Uyarlandı ve Düzeltildi) --------------------
@app.route("/api/posts")
def api_posts():
    """Tüm görünür gönderileri JSON olarak döndürür."""
    me_user = get_user_by_username(session.get("user"))
    me_id = me_user.id if me_user else None

    all_posts = Post.query.order_by(Post.id.desc()).all()
    # Gönderileri filtrele ve to_dict() metodu ile JSON'a uygun hale getir
    visible_posts = [p.to_dict() for p in all_posts if can_view_posts(p.user_id, me_id)]
    return jsonify(visible_posts)


@app.route("/api/users")
def api_users():
    """Tüm kullanıcı adlarını JSON olarak döndürür."""
    users = User.query.with_entities(User.username).all()
    # Sonuç bir tuple listesi olduğu için [u[0]] ile sadece kullanıcı adlarını al
    return jsonify([u[0] for u in users])


@app.route("/api/comments/<int:post_id>")
def api_comments(post_id):
    """Belirli bir gönderinin yorumlarını JSON olarak döndürür."""
    me_user = get_user_by_username(session.get("user"))
    me_id = me_user.id if me_user else None

    # UYARI GİDERİLDİ: Post.query.get yerine db.session.get kullanıldı
    target_post = db.session.get(Post, post_id)
    if not target_post: return jsonify([])

    # Gizlilik kontrolü
    if not can_view_posts(target_post.user_id, me_id):
        return jsonify([])

    comments = [c.to_dict() for c in target_post.comments.all()]
    return jsonify(comments)


# -------------------- ÇALIŞTIR & VERİTABANI BAŞLATMA (Aynı kaldı) --------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("Veritabanı başlatılıyor (site.db)...")

    # Uygulama bağlamında veritabanını oluştur
    with app.app_context():
        db.create_all()

    print(f"Çalışıyor: http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)