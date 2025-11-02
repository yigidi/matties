# app.py — Flask mini sosyal ağ (Py3.9 uyumlu)
import os, uuid
from typing import Optional
from flask import (
    Flask, request, jsonify, render_template_string,
    send_from_directory, session, redirect, url_for, Response, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------- FLASK APP (route'lardan ÖNCE!) --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "DEGISTIR_ILK_CALISTIRMADA")

# -------------------- YÜKLEME KLASÖRLERİ & LİMİTLER --------------------
BASE_DIR   = os.path.abspath(os.getcwd())
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
AVATAR_DIR = os.path.join(UPLOAD_DIR, "avatars")
MEDIA_DIR  = os.path.join(UPLOAD_DIR, "media")     # video & ses

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR,  exist_ok=True)

# Maksimum yükleme boyutu (örn. 200 MB)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".ogg", ".m4v", ".mov"}  # MP4 dahil
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg"}

def is_image(path): return os.path.splitext(path)[1].lower() in IMAGE_EXT
def is_video(path): return os.path.splitext(path)[1].lower() in VIDEO_EXT
def is_audio(path): return os.path.splitext(path)[1].lower() in AUDIO_EXT

# -------------------- VERİ YAPILARI --------------------
USERS   = {}   # {"user": {"pw": "<hash>", "bio": "...", "avatar": "dosya.jpg" or None, "privacy": "friends"|"public"}}
POSTS   = []   # {"id":int,"user":str,"html":str,"likes":int}
FRIENDS = {}   # {"user": set([...])}
REQ_SENT= {}   # {"user": set([...])}
REQ_RECV= {}   # {"user": set([...])}
DMS     = []   # {"from":str,"to":str,"html":str}
COMMENTS= {}   # {post_id: [{"user": "berke", "html": "..."}]}
NEXT_ID = 1

def ensure_user_struct(u: Optional[str]):
    if not u: return
    FRIENDS.setdefault(u, set())
    REQ_SENT.setdefault(u, set())
    REQ_RECV.setdefault(u, set())

def can_view_posts(owner: str, viewer: Optional[str]) -> bool:
    """owner'ın gönderilerini viewer görebilir mi? (Py3.9 Optional kullanımı)"""
    if owner not in USERS:
        return False
    privacy = USERS[owner].get("privacy", "friends")
    if privacy == "public":
        return True
    if viewer is None:
        return False
    if viewer == owner:
        return True
    return viewer in FRIENDS.get(owner, set())

# -------------------- HTML ŞABLON --------------------
PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
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
      {% if 'user' in session %}
        {% set me = session['user'] %}
        {% set me_avatar = USERS.get(me, {}).get('avatar') %}
        {% if me_avatar %}
          <img class="avatar" src="/avatar/{{me_avatar}}" alt="">
        {% endif %}
        Giriş: <a href="/user/{{me}}">@{{me}}</a> |
        <a href="/inbox">Mesajlar</a> |
        <a href="/requests">İstekler</a> |
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
      {% if 'user' in session %}
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
          {% set uavatar = USERS.get(p.user, {}).get('avatar') %}
          {% if uavatar %}
            <img class="avatar" src="/avatar/{{uavatar}}" alt="">
          {% endif %}
          <b><a href="/user/{{p.user}}">{{p.user}}</a></b> · <span class="muted">#{{p.id}}</span>
          <div style="margin-top:6px;">{{p.html|safe}}</div>
          <div style="margin-top:8px;">
            <form action="/like/{{p.id}}" method="post" class="inline">
              <button>❤️ Beğen ({{p.likes}})</button>
            </form>
            {% if 'user' in session and session['user'] != p.user %}
              {% if p.user in friends_of_current %}
                <span class="muted" style="margin-left:8px;">✅ Arkadaş</span>
              {% elif p.user in req_sent_of_current %}
                <form action="/cancel_request/{{p.user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>↩️ İsteği geri al</button>
                </form>
              {% elif p.user in req_recv_of_current %}
                <form action="/accept_request/{{p.user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>✅ Kabul</button>
                </form>
                <form action="/decline_request/{{p.user}}" method="post" class="inline" style="margin-left:6px;">
                  <button>❌ Reddet</button>
                </form>
              {% else %}
                <form action="/request_friend/{{p.user}}" method="post" class="inline" style="margin-left:8px;">
                  <button>🤝 İstek Gönder</button>
                </form>
              {% endif %}
              <form action="/dm/{{p.user}}" method="get" class="inline" style="margin-left:8px;">
                <button>💬 Mesaj</button>
              </form>
            {% endif %}
          </div>

          <!-- YORUMLAR -->
          <div style="margin-top:10px; border-top:1px solid #e5e7eb; padding-top:8px;">
            <div class="muted" style="margin-bottom:6px;">Yorumlar</div>
            {% set clist = COMMENTS.get(p.id, []) %}
            {% if clist %}
              {% for c in clist %}
                <div style="margin-bottom:6px;">
                  {% set cav = USERS.get(c.user, {}).get('avatar') %}
                  {% if cav %}
                    <img class="avatar" src="/avatar/{{cav}}" alt="">
                  {% endif %}
                  <b><a href="/user/{{c.user}}">{{c.user}}</a>:</b>
                  <span>{{c.html|safe}}</span>
                </div>
              {% endfor %}
            {% else %}
              <div class="muted">Henüz yorum yok.</div>
            {% endif %}

            {% if 'user' in session %}
              <form action="/comment/{{p.id}}" method="post" enctype="multipart/form-data" style="margin-top:8px;">
                <textarea name="text" rows="2" placeholder="Yorum yaz..." style="width:100%;"></textarea>
                <input type="file" name="media" accept="video/*,audio/*,image/*">
                <button>Yorum Gönder</button>
              </form>
            {% else %}
              <div class="muted" style="margin-top:6px;">Yorum yapmak için giriş yap.</div>
            {% endif %}
          </div>
          <!-- /YORUMLAR -->
        </div>
      {% endfor %}
    {% endif %}
  </div>
</body>
</html>
"""

# -------------------- Range (Partial Content) Sunucu --------------------
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
        end   = int(start_end[1]) if len(start_end) > 1 and start_end[1] else file_size - 1
        start = max(0, start); end = min(end, file_size - 1)
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

# -------------------- ANA SAYFA (gizlilik filtreli) --------------------
@app.route("/")
def index():
    me = session.get("user")
    ensure_user_struct(me) if me else None
    visible_posts = [p for p in POSTS if can_view_posts(p["user"], me)]
    return render_template_string(
        PAGE,
        posts=visible_posts[::-1],
        COMMENTS=COMMENTS,
        USERS=USERS,
        friends_of_current=(FRIENDS.get(me) if me else set()),
        req_sent_of_current=(REQ_SENT.get(me) if me else set()),
        req_recv_of_current=(REQ_RECV.get(me) if me else set()),
    )

# -------------------- KAYIT & GİRİŞ --------------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        priv = (request.form.get("privacy") or "friends").strip().lower()
        if priv not in {"friends","public"}: priv = "friends"
        if not username or not password:
            return "Kullanıcı adı ve şifre gerekli. <a href='/register'>&larr; Geri</a>", 400
        if username in USERS:
            return "Bu kullanıcı adı alınmış. <a href='/register'>&larr; Geri</a>", 400
        USERS[username] = {"pw": generate_password_hash(password), "bio": bio, "avatar": None, "privacy": priv}
        ensure_user_struct(username)
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

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        u = USERS.get(username)
        if not u or not check_password_hash(u["pw"], password):
            return "Geçersiz kimlik. <a href='/login'>&larr; Geri</a>", 401
        ensure_user_struct(username)
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

# -------------------- PROFİL & AVATAR (gizlilik kontrollü) --------------------
@app.route("/user/<username>", methods=["GET", "POST"])
def profile(username):
    if username not in USERS:
        return "Kullanıcı bulunamadı.", 404

    me = session.get("user")
    ensure_user_struct(me) if me else None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "avatar":
            if "user" not in session or session["user"] != username:
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
            old = USERS[username].get("avatar")
            if old and old != unique:
                try: os.remove(os.path.join(AVATAR_DIR, old))
                except OSError: pass
            USERS[username]["avatar"] = unique
            return redirect(url_for("profile", username=username))
        elif action == "privacy":
            if "user" not in session or session["user"] != username:
                return "Yetkisiz işlem.", 403
            priv = (request.form.get("privacy") or "friends").strip().lower()
            if priv in {"friends","public"}:
                USERS[username]["privacy"] = priv
            return redirect(url_for("profile", username=username))

    user_posts = [p for p in POSTS if p["user"] == username]
    bio = USERS.get(username, {}).get("bio","")
    uav = USERS.get(username, {}).get("avatar")
    av_html = f"<img src='/avatar/{uav}' style='width:120px;height:120px;border-radius:50%;object-fit:cover;border:2px solid #e5e7eb;'>" if uav else "<div class='muted' style='margin:8px 0;'>Profil fotoğrafı yok</div>"
    privacy = USERS[username].get("privacy","friends")

    sent = me in REQ_SENT and username in REQ_SENT.get(me,set())
    recv = me in REQ_RECV and username in REQ_RECV.get(me,set())
    is_friend = me in FRIENDS and username in FRIENDS.get(me, set()) if me else False

    html = f"""
    <h2>{username} — Profil</h2>
    <div style="margin:8px 0;">{av_html}</div>
    <p><i>Bio:</i> {bio or '(bio yok)'}</p>
    <p><i>Gizlilik:</i> {"Sadece arkadaşlar" if privacy=="friends" else "Herkese açık"}</p>
    <p><a href='/'>Geri</a></p>
    """

    if me and me == username:
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
              <option value="friends" {"selected" if privacy=="friends" else ""}>Sadece arkadaşlar</option>
              <option value="public" {"selected" if privacy=="public" else ""}>Herkese açık</option>
            </select>
          </label>
          <button>Kaydet</button>
        </form>
        """

    if me and me != username:
        if is_friend:
            html += "<p>✅ Arkadaşsınız</p>"
        elif sent:
            html += f"<form action='/cancel_request/{username}' method='post'><button>↩️ İsteği geri al</button></form>"
        elif recv:
            html += f"<form action='/accept_request/{username}' method='post' style='display:inline;'><button>✅ Kabul</button></form>"
            html += f"<form action='/decline_request/{username}' method='post' style='display:inline;margin-left:6px;'><button>❌ Reddet</button></form>"
        else:
            html += f"<form action='/request_friend/{username}' method='post'><button>🤝 İstek Gönder</button></form>"
        html += f"<p><a href='/dm/{username}'>💬 Mesaj Gönder</a></p>"

    html += "<hr>"

    if not can_view_posts(username, me):
        html += "<p><b>Bu kullanıcı gönderilerini sadece arkadaşlarıyla paylaşıyor.</b></p>"
        return html

    if not user_posts:
        html += "<p>Henüz gönderi yok.</p>"
    else:
        for p in reversed(user_posts):
            html += f"<div><b>{p['user']}</b>: {p['html']}</div><br>"
    return html

@app.route("/avatar/<filename>")
def serve_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)

# -------------------- GÖNDERİLER (POST) --------------------
def save_media(file_storage, target_dir):
    safe = secure_filename(file_storage.filename)
    stem, ext = os.path.splitext(safe)
    unique = f"{stem}_{uuid.uuid4().hex[:8]}{ext.lower()}"
    file_storage.save(os.path.join(target_dir, unique))
    return unique

@app.route("/post", methods=["POST"])
def post():
    if "user" not in session:
        return "Giriş yapmanız gerekiyor.", 401
    global NEXT_ID

    text = (request.form.get("text") or "").strip()
    photo = request.files.get("photo")
    media = request.files.get("media")

    parts = []
    if text:
        parts.append(text.replace("\n", "<br>"))

    if photo and photo.filename:
        if not is_image(photo.filename):
            return "Sadece resim yükleyin (jpg, png, webp...).", 400
        img_name = save_media(photo, UPLOAD_DIR)
        parts.append(f"<img class='media' src='/uploads/{img_name}' alt=''>")

    if media and media.filename:
        ext = os.path.splitext(media.filename)[1].lower()
        if ext not in VIDEO_EXT.union(AUDIO_EXT):
            return "Desteklenmeyen medya biçimi.", 400
        media_name = save_media(media, MEDIA_DIR)
        if ext in VIDEO_EXT:
            parts.append(f"<video controls preload='metadata' src='/media/{media_name}'></video>")
        else:
            parts.append(f"<audio controls src='/media/{media_name}'></audio>")

    if not parts:
        return "Boş gönderi olmaz.", 400

    POSTS.append({"id": NEXT_ID, "user": session["user"], "html": "<br>".join(parts), "likes": 0})
    COMMENTS.setdefault(NEXT_ID, [])
    NEXT_ID += 1
    return redirect(url_for("index"))

@app.route("/like/<int:post_id>", methods=["POST"])
def like_post(post_id):
    me = session.get("user")
    for p in POSTS:
        if p["id"] == post_id:
            if not can_view_posts(p["user"], me):
                break
            p["likes"] += 1
            break
    return redirect(url_for("index"))

# -------------------- YORUMLAR --------------------
@app.route("/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id):
    if "user" not in session:
        return redirect(url_for("login"))
    target = None
    for p in POSTS:
        if p["id"] == post_id:
            target = p
            break
    if not target:
        return "Gönderi bulunamadı.", 404
    if not can_view_posts(target["user"], session.get("user")):
        return "İzniniz yok.", 403

    text = (request.form.get("text") or "").strip()
    media = request.files.get("media")
    parts = []
    if text:
        parts.append(text.replace("\n", "<br>"))
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

    if not parts:
        return redirect(request.referrer or url_for("index"))

    COMMENTS.setdefault(post_id, []).append({"user": session["user"], "html": "<br>".join(parts)})
    return redirect(request.referrer or url_for("index"))

# -------------------- ARKADAŞLIK --------------------
@app.route("/request_friend/<username>", methods=["POST"])
def request_friend(username):
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]
    if username == me:
        return redirect(url_for("profile", username=me))
    ensure_user_struct(me); ensure_user_struct(username)
    if username in FRIENDS[me]: return redirect(request.referrer or url_for("profile", username=username))
    if username in REQ_SENT[me]: return redirect(request.referrer or url_for("profile", username=username))
    if username in REQ_RECV[me]:
        REQ_RECV[me].discard(username); REQ_SENT[username].discard(me)
        FRIENDS[me].add(username); FRIENDS[username].add(me)
        return redirect(request.referrer or url_for("profile", username=username))
    REQ_SENT[me].add(username); REQ_RECV[username].add(me)
    return redirect(request.referrer or url_for("profile", username=username))

@app.route("/cancel_request/<username>", methods=["POST"])
def cancel_request(username):
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]; ensure_user_struct(me); ensure_user_struct(username)
    REQ_SENT[me].discard(username); REQ_RECV[username].discard(me)
    return redirect(request.referrer or url_for("profile", username=username))

@app.route("/accept_request/<username>", methods=["POST"])
def accept_request(username):
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]; ensure_user_struct(me); ensure_user_struct(username)
    if username in REQ_RECV[me]:
        REQ_RECV[me].discard(username); REQ_SENT[username].discard(me)
        FRIENDS[me].add(username); FRIENDS[username].add(me)
    return redirect(request.referrer or url_for("profile", username=username))

@app.route("/decline_request/<username>", methods=["POST"])
def decline_request(username):
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]; ensure_user_struct(me); ensure_user_struct(username)
    REQ_RECV[me].discard(username); REQ_SENT[username].discard(me)
    return redirect(request.referrer or url_for("profile", username=username))

@app.route("/requests")
def requests_box():
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]; ensure_user_struct(me)
    incoming = sorted(REQ_RECV[me]); outgoing = sorted(REQ_SENT[me])
    html = "<h2>İstek Kutusu</h2><p><a href='/'>Geri</a></p><hr><h3>Gelen</h3>"
    if not incoming: html += "<p>Yok.</p>"
    else:
        for u in incoming:
            html += f"<div><b>{u}</b> <form action='/accept_request/{u}' method='post' style='display:inline;'><button>✅</button></form> <form action='/decline_request/{u}' method='post' style='display:inline;margin-left:6px;'><button>❌</button></form></div><br>"
    html += "<h3>Gönderilen</h3>"
    if not outgoing: html += "<p>Yok.</p>"
    else:
        for u in outgoing:
            html += f"<div><b>{u}</b> <form action='/cancel_request/{u}' method='post' style='display:inline;margin-left:8px;'><button>↩️ Geri al</button></form></div><br>"
    return html

# -------------------- DM (medya destekli) --------------------
@app.route("/inbox")
def inbox():
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]
    users = sorted({m["from"] if m["from"]!=me else m["to"] for m in DMS if me in (m["from"], m["to"])})
    html = "<h2>Mesajlar</h2><p><a href='/'>Geri</a></p><hr>"
    if not users: html += "<p>Henüz konuşma yok.</p>"
    else:
        for u in users:
            html += f"<div><a href='/dm/{u}'>@{u}</a></div>"
    return html

@app.route("/dm/<username>", methods=["GET","POST"])
def dm(username):
    if "user" not in session:
        return redirect(url_for("login"))
    me = session["user"]
    if request.method == "POST":
        msg = (request.form.get("text") or "").strip()
        media = request.files.get("media")
        parts = []
        if msg:
            parts.append(msg.replace("\n","<br>"))
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
            DMS.append({"from": me, "to": username, "html": "<br>".join(parts)})
        return redirect(url_for("dm", username=username))

    conv = [m for m in DMS if (m["from"]==me and m["to"]==username) or (m["from"]==username and m["to"]==me)]
    html = f"<h2>{username} ile yazışma</h2><p><a href='/'>Geri</a></p><hr>"
    for m in conv:
        sender = "Ben" if m["from"]==me else m["from"]
        html += f"<p><b>{sender}:</b><br>{m['html']}</p><hr>"
    html += """<form method='post' enctype='multipart/form-data'>
      <textarea name='text' rows='2' placeholder='Mesaj.'></textarea><br>
      <input type='file' name='media' accept='image/*,video/*,audio/*'><br>
      <button>Gönder</button>
    </form>"""
    return html

# -------------------- DOSYA SERVİSİ --------------------
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/media/<path:filename>")
def serve_media(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in VIDEO_EXT:
        mimetype = "video/mp4" if ext in {".mp4", ".m4v"} else ("video/webm" if ext==".webm" else "video/ogg")
    elif ext in AUDIO_EXT:
        if ext==".mp3": mimetype="audio/mpeg"
        elif ext==".wav": mimetype="audio/wav"
        elif ext==".m4a": mimetype="audio/mp4"
        else: mimetype="audio/ogg"
    else:
        mimetype = "application/octet-stream"
    return partial_response(os.path.join(MEDIA_DIR, filename), mimetype)

# -------------------- ARAMA (gizlilik filtreli) --------------------
@app.route("/search")
def search():
    me = session.get("user")
    q = (request.args.get("q") or "").strip().lower()
    if not q: return redirect(url_for("index"))
    results = [p for p in POSTS if can_view_posts(p["user"], me) and (q in p["html"].lower() or q in p["user"].lower())]
    html = f"<h2>Arama: {q}</h2><p><a href='/'>Geri</a></p><hr>"
    if not results: html += "<p>Sonuç yok ya da görebileceğin gönderi yok.</p>"
    else:
        for p in results[::-1]:
            html += f"<div><b>{p['user']}</b>: {p['html']}</div><br>"
    return html

@app.route("/find_friend")
def find_friend():
    q = (request.args.get("name") or "").strip().lower()
    if not q: return redirect(url_for("index"))
    me = session.get("user"); ensure_user_struct(me) if me else None
    matches = [u for u in USERS.keys() if q in u.lower()]
    html = f"<h2>Kullanıcı Ara: {q}</h2><p><a href='/'>Geri</a></p><hr>"
    if not matches: html += "<p>Yok.</p>"
    else:
        for u in matches:
            html += f"<div><b><a href='/user/{u}'>{u}</a></b>"
            if me and me!=u:
                ensure_user_struct(u)
                if u in FRIENDS[me]:
                    html += " <span class='muted'>✅ Arkadaş</span>"
                elif u in REQ_SENT[me]:
                    html += f" <form action='/cancel_request/{u}' method='post' style='display:inline;margin-left:8px;'><button>↩️ Geri al</button></form>"
                elif u in REQ_RECV[me]:
                    html += f" <form action='/accept_request/{u}' method='post' style='display:inline;margin-left:8px;'><button>✅</button></form>"
                    html += f" <form action='/decline_request/{u}' method='post' style='display:inline;margin-left:6px;'><button>❌</button></form>"
                else:
                    html += f" <form action='/request_friend/{u}' method='post' style='display:inline;margin-left:8px;'><button>🤝 İstek</button></form>"
            html += "</div><br>"
    return html

# -------------------- API (gizlilik filtreli) --------------------
@app.route("/api/posts")
def api_posts():
    me = session.get("user")
    visible = [p for p in POSTS if can_view_posts(p["user"], me)]
    return jsonify(visible)

@app.route("/api/users")
def api_users():
    return jsonify(list(USERS.keys()))

@app.route("/api/comments/<int:post_id>")
def api_comments(post_id):
    me = session.get("user")
    owner = None
    for p in POSTS:
        if p["id"] == post_id:
            owner = p["user"]; break
    if owner and not can_view_posts(owner, me):
        return jsonify([])
    return jsonify(COMMENTS.get(post_id, []))

# -------------------- ÇALIŞTIR --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"Çalışıyor: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
