"""Ephemeral fragmented images limited to one viewing session per IP address."""

from __future__ import annotations

from io import BytesIO
import datetime
import hashlib
import hmac
import ipaddress
import math
import os
import secrets
import sqlite3
import time
import uuid

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, url_for
from PIL import Image, UnidentifiedImageError
from translations import TRANSLATIONS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data.db"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
ALLOWED_SLICE_COUNTS = {10, 15, 30, 60}
GRID_DIMENSIONS = {
    10: (5, 2),
    15: (5, 3),
    30: (6, 5),
    60: (10, 6),
}
MIN_DURATION_MS = 2_000
MAX_DURATION_MS = 5 * 60 * 1_000
MAX_IMAGE_PIXELS = 25_000_000
SESSION_GRACE_SECONDS = 60

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

app = Flask(__name__)
app.config.update(
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)

trusted_proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
if trusted_proxy_count > 0:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=trusted_proxy_count,
        x_proto=trusted_proxy_count,
    )


def load_ip_hash_secret() -> bytes:
    configured_secret = os.environ.get("IP_HASH_SECRET")
    if configured_secret:
        return configured_secret.encode("utf-8")

    secret_path = os.path.join(BASE_DIR, ".ip_hash_secret")
    try:
        with open(secret_path, "rb") as secret_file:
            secret = secret_file.read().strip()
            if not secret:
                raise RuntimeError(".ip_hash_secret est vide")
            return secret
    except FileNotFoundError:
        generated_secret = secrets.token_bytes(32)
        try:
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as secret_file:
                secret_file.write(generated_secret)
            return generated_secret
        except FileExistsError:
            with open(secret_path, "rb") as secret_file:
                return secret_file.read().strip()


IP_HASH_SECRET = load_ip_hash_secret()


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    connection = connect_db()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            slice_count INTEGER NOT NULL DEFAULT 60
        );

        CREATE TABLE IF NOT EXISTS view_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id TEXT,
            seen_at TEXT,
            remote_addr TEXT
        );

        CREATE TABLE IF NOT EXISTS blocked_viewers (
            image_id TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (image_id, ip_hash),
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS view_sessions (
            token_hash TEXT PRIMARY KEY,
            image_id TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            next_slice INTEGER NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(images)").fetchall()
    }
    if "slice_count" not in columns:
        connection.execute(
            "ALTER TABLE images ADD COLUMN slice_count INTEGER NOT NULL DEFAULT 15"
        )
    connection.commit()
    connection.close()


def db_execute(query, params=(), *, fetch=False, one=False):
    connection = connect_db()
    cursor = connection.execute(query, params)
    data = cursor.fetchall() if fetch else None
    connection.commit()
    connection.close()
    if fetch:
        return data[0] if one and data else data
    return None


init_db()


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_image(file) -> tuple[int, int]:
    try:
        with Image.open(file.stream) as image:
            width, height = image.size
            image.verify()
        file.stream.seek(0)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        file.stream.seek(0)
        raise ValueError("invalid_image") from error
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("image_too_large")
    return width, height


def client_ip() -> str:
    try:
        return ipaddress.ip_address(request.remote_addr).compressed
    except (TypeError, ValueError):
        abort(400, description="Adresse IP client invalide")


def hash_ip(ip: str | None = None) -> str:
    normalized_ip = ip or client_ip()
    return hmac.new(
        IP_HASH_SECRET,
        normalized_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def is_blocked(image_id: str) -> bool:
    return bool(
        db_execute(
            "SELECT 1 FROM blocked_viewers WHERE image_id=? AND ip_hash=?",
            (image_id, hash_ip()),
            fetch=True,
            one=True,
        )
    )


def create_view_session(image_id: str):
    """Atomically block the IP and create a single-use fragment session."""
    ip_hash = hash_ip()
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    seen_at = utc_now()
    now = time.time()

    connection = connect_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM view_sessions WHERE expires_at < ?", (now,))
        image = connection.execute(
            "SELECT filename, duration_ms, slice_count FROM images WHERE id=?",
            (image_id,),
        ).fetchone()
        if not image:
            connection.rollback()
            return None, "not_found"
        try:
            connection.execute(
                "INSERT INTO blocked_viewers (image_id, ip_hash, first_seen_at) VALUES (?, ?, ?)",
                (image_id, ip_hash, seen_at),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return None, "already_seen"

        filename, duration_ms, slice_count = image
        expires_at = now + (duration_ms / 1000) + SESSION_GRACE_SECONDS
        connection.execute(
            "INSERT INTO view_sessions "
            "(token_hash, image_id, ip_hash, next_slice, expires_at) VALUES (?, ?, ?, 0, ?)",
            (token_hash, image_id, ip_hash, expires_at),
        )
        connection.execute("UPDATE images SET views = views + 1 WHERE id=?", (image_id,))
        connection.execute(
            "INSERT INTO view_logs (image_id, seen_at, remote_addr) VALUES (?, ?, ?)",
            (image_id, seen_at, ip_hash),
        )
        connection.commit()
        return {
            "token": token,
            "filename": filename,
            "duration_ms": duration_ms,
            "slice_count": slice_count,
        }, None
    finally:
        connection.close()


def image_dimensions(filename: str) -> tuple[int, int]:
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    try:
        with Image.open(path) as image:
            return image.size
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        abort(404)


def build_slice(filename: str, index: int, slice_count: int):
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    try:
        with Image.open(path) as image:
            image.seek(0)
            source = image.convert("RGBA")
            width, height = source.size
            column_count, row_count = GRID_DIMENSIONS[slice_count]
            column = index % column_count
            row = index // column_count
            x_start = (width * column) // column_count
            x_end = (width * (column + 1)) // column_count
            y_start = (height * row) // row_count
            y_end = (height * (row + 1)) // row_count
            fragment = source.crop((x_start, y_start, x_end, y_end))
            output = BytesIO()
            fragment.save(output, format="PNG", optimize=True)
            output.seek(0)
            return output, x_start, y_start, x_end - x_start, y_end - y_start
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        abort(404)


@app.after_request
def secure_response(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.before_request
def detect_language() -> None:
    saved_language = request.cookies.get("language")
    if saved_language in TRANSLATIONS:
        g.language = saved_language
    else:
        g.language = request.accept_languages.best_match(TRANSLATIONS.keys()) or "fr"
    g.translations = TRANSLATIONS[g.language]


@app.context_processor
def inject_translations():
    return {
        "lang": g.language,
        "t": g.translations,
        "language_next": request.path if request.method == "GET" else url_for("index"),
    }


@app.get("/language/<language>")
def set_language(language: str):
    if language not in TRANSLATIONS:
        abort(404)
    destination = request.args.get("next", url_for("index"))
    if not destination.startswith("/") or destination.startswith("//"):
        destination = url_for("index")
    response = redirect(destination)
    response.set_cookie(
        "language",
        language,
        max_age=365 * 24 * 60 * 60,
        secure=request.is_secure,
        httponly=True,
        samesite="Lax",
    )
    return response


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/upload")
def upload():
    if "image" not in request.files:
        return render_template("error.html", message=g.translations["no_file"]), 400
    file = request.files["image"]
    if not file.filename:
        return render_template("error.html", message=g.translations["no_selection"]), 400
    if not allowed_file(file.filename):
        return render_template("error.html", message=g.translations["format_not_allowed"]), 400
    try:
        validate_image(file)
        duration_ms = int(request.form.get("duration_ms", "5000"))
        slice_count = int(request.form.get("slice_count", "60"))
        if not MIN_DURATION_MS <= duration_ms <= MAX_DURATION_MS:
            raise ValueError("invalid_duration")
        if slice_count not in ALLOWED_SLICE_COUNTS:
            raise ValueError("invalid_slices")
    except (TypeError, ValueError) as error:
        message_key = str(error)
        message = g.translations.get(message_key, g.translations["invalid_image"])
        return render_template("error.html", message=message), 400

    image_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    stored_name = f"{image_id}__{filename}"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], stored_name))
    db_execute(
        "INSERT INTO images "
        "(id, filename, duration_ms, created_at, views, slice_count) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (image_id, stored_name, duration_ms, utc_now(), slice_count),
    )
    link = url_for("view_image", image_id=image_id, _external=True)
    return render_template("success.html", link=link)


@app.get("/i/<image_id>")
def view_image(image_id: str):
    image = db_execute(
        "SELECT duration_ms, slice_count FROM images WHERE id=?",
        (image_id,),
        fetch=True,
        one=True,
    )
    if not image:
        abort(404)
    if is_blocked(image_id):
        return render_template("gone.html"), 410
    duration_ms, slice_count = image
    return no_store(
        app.make_response(
            render_template(
                "viewer.html",
                session_url=url_for("start_session", image_id=image_id),
                duration_ms=duration_ms,
                slice_count=slice_count,
            )
        )
    )


@app.post("/api/session/<image_id>")
def start_session(image_id: str):
    payload = request.get_json(silent=True) or {}
    if payload.get("pledge_accepted") is not True:
        return jsonify(error="pledge_required"), 400
    session, error = create_view_session(image_id)
    if error == "not_found":
        return jsonify(error="not_found"), 404
    if error == "already_seen":
        return jsonify(error="already_seen"), 410
    width, height = image_dimensions(session["filename"])
    # Fast target cadence; the client display still imposes its physical refresh limit.
    frame_ms = 4
    return no_store(
        jsonify(
            token=session["token"],
            width=width,
            height=height,
            slice_count=session["slice_count"],
            max_visible=max(5, math.ceil(session["slice_count"] / 3)),
            duration_ms=session["duration_ms"],
            frame_ms=frame_ms,
            frame_url=url_for("image_slice", index=0).rsplit("/", 1)[0],
        )
    )


@app.get("/api/frame/<int:index>")
def image_slice(index: int):
    if request.method == "HEAD":
        return "", 405, {"Allow": "GET"}

    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        abort(404)
    token_hash = hash_token(token)
    ip_hash = hash_ip()
    now = time.time()
    connection = connect_db()
    session = connection.execute(
        "SELECT s.next_slice, s.expires_at, i.filename, i.slice_count "
        "FROM view_sessions s JOIN images i ON i.id=s.image_id "
        "WHERE s.token_hash=? AND s.ip_hash=?",
        (token_hash, ip_hash),
    ).fetchone()
    connection.close()
    if not session:
        abort(404)
    next_slice, expires_at, filename, slice_count = session
    if expires_at < now or index != next_slice or not 0 <= index < slice_count:
        return jsonify(error="fragment_unavailable"), 410

    fragment, x_offset, y_offset, fragment_width, fragment_height = build_slice(
        filename, index, slice_count
    )

    connection = connect_db()
    cursor = connection.execute(
        "UPDATE view_sessions SET next_slice=next_slice + 1 "
        "WHERE token_hash=? AND ip_hash=? AND next_slice=? AND expires_at>=?",
        (token_hash, ip_hash, index, now),
    )
    connection.commit()
    delivered = cursor.rowcount == 1
    connection.close()
    if not delivered:
        return jsonify(error="fragment_unavailable"), 410

    response = send_file(fragment, mimetype="image/png", conditional=False, max_age=0)
    response.headers["X-Fragment-X"] = str(x_offset)
    response.headers["X-Fragment-Y"] = str(y_offset)
    response.headers["X-Fragment-Width"] = str(fragment_width)
    response.headers["X-Fragment-Height"] = str(fragment_height)
    return no_store(response)


@app.get("/stats")
def stats():
    admin_token = os.environ.get("ADMIN_TOKEN")
    supplied_token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not admin_token or not hmac.compare_digest(supplied_token, admin_token):
        abort(404)
    rows = db_execute(
        "SELECT id, filename, duration_ms, slice_count, created_at, views "
        "FROM images ORDER BY created_at DESC",
        fetch=True,
    )
    return render_template("stats.html", images=rows)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
