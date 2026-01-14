from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "data" / "blog.db"
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["INITIALIZED"] = False


def is_private_ip(ip_address: str) -> bool:
    if ip_address.startswith("127."):
        return True
    if ip_address.startswith("10."):
        return True
    if ip_address.startswith("192.168."):
        return True
    if ip_address.startswith("172."):
        try:
            second_octet = int(ip_address.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second_octet <= 31
    return False


@app.before_request
def limit_to_local_network() -> None:
    remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    if remote_addr and not is_private_ip(remote_addr):
        abort(403)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            image_filename TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(post_id) REFERENCES posts(id)
        )
        """
    )
    db.commit()


@app.before_request
def setup() -> None:
    if app.config["INITIALIZED"]:
        return
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.config["INITIALIZED"] = True


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index() -> str:
    db = get_db()
    selected_date = request.args.get("date")
    if selected_date:
        posts = db.execute(
            "SELECT * FROM posts WHERE date(created_at) = ? ORDER BY datetime(created_at) DESC",
            (selected_date,),
        ).fetchall()
    else:
        posts = db.execute(
            "SELECT * FROM posts ORDER BY datetime(created_at) DESC"
        ).fetchall()

    post_dates = {
        row["post_date"]
        for row in db.execute(
            "SELECT DISTINCT date(created_at) AS post_date FROM posts ORDER BY post_date"
        ).fetchall()
        if row["post_date"]
    }

    comments_by_post: dict[int, list[sqlite3.Row]] = {}
    post_ids = [post["id"] for post in posts]
    if post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        comments = db.execute(
            f"SELECT * FROM comments WHERE post_id IN ({placeholders}) ORDER BY datetime(created_at) ASC",
            post_ids,
        ).fetchall()
        for comment in comments:
            comments_by_post.setdefault(comment["post_id"], []).append(comment)

    return render_template(
        "index.html",
        posts=posts,
        comments_by_post=comments_by_post,
        post_dates=sorted(post_dates),
        selected_date=selected_date,
    )


@app.route("/post", methods=["POST"])
def create_post() -> str:
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not title or not body:
        abort(400, "Title and body are required.")

    filename = None
    file = request.files.get("image")
    if file and file.filename:
        if not allowed_file(file.filename):
            abort(400, "Unsupported image type.")
        filename = f"{datetime.utcnow().timestamp()}_{secure_filename(file.filename)}"
        file.save(UPLOAD_DIR / filename)

    db = get_db()
    db.execute(
        "INSERT INTO posts (title, body, image_filename, created_at) VALUES (?, ?, ?, ?)",
        (title, body, filename, datetime.utcnow().isoformat(timespec="seconds")),
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id: int) -> str:
    author = request.form.get("author", "Anonymous").strip() or "Anonymous"
    body = request.form.get("body", "").strip()
    if not body:
        abort(400, "Comment body is required.")

    db = get_db()
    post = db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        abort(404)

    db.execute(
        "INSERT INTO comments (post_id, author, body, created_at) VALUES (?, ?, ?, ?)",
        (post_id, author, body, datetime.utcnow().isoformat(timespec="seconds")),
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/delete", methods=["POST"])
def delete_post(post_id: int) -> str:
    db = get_db()
    post = db.execute(
        "SELECT image_filename FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if not post:
        abort(404)

    db.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()

    if post["image_filename"]:
        image_path = UPLOAD_DIR / post["image_filename"]
        if image_path.exists():
            image_path.unlink()

    return redirect(url_for("index"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
