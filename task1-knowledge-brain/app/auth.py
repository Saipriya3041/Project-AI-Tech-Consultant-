import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from retrieval.store import connect

PASSWORD_ITERATIONS = 310_000
SESSION_DURATION = timedelta(days=7)
PASSWORD_RESET_DURATION = timedelta(minutes=30)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_user(email: str, password: str) -> dict | None:
    normalized_email = normalize_email(email)
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        cursor = connection.execute(
            "INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
            (normalized_email, hash_password(password), now),
        )
        connection.commit()
        return {"id": cursor.lastrowid, "email": normalized_email}
    except sqlite3.IntegrityError as error:
        if "users.email" in str(error):
            return None
        raise
    finally:
        connection.close()


def authenticate_user(email: str, password: str) -> dict | None:
    connection = connect()
    try:
        user = connection.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            return None
        return {"id": user["id"], "email": user["email"]}
    finally:
        connection.close()


def reset_local_account_password(email: str, password: str) -> bool:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    normalized_email = normalize_email(email)
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT id FROM users WHERE email = ?",
            (normalized_email,),
        ).fetchone()
        if user is None:
            connection.rollback()
            return False
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user["id"]),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        connection.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        connection.commit()
        return True
    finally:
        connection.close()


def create_password_reset(email: str) -> tuple[str, str] | None:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + PASSWORD_RESET_DURATION).isoformat()
    connection = connect()
    try:
        user = connection.execute(
            "SELECT id, email FROM users WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
        if user is None:
            return None
        connection.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        connection.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user["id"], expires_at),
        )
        connection.commit()
        return user["email"], token
    finally:
        connection.close()


def reset_password(token: str, password: str) -> bool:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        reset = connection.execute(
            "SELECT user_id FROM password_resets WHERE token_hash = ? AND expires_at > ?",
            (token_hash, now),
        ).fetchone()
        if reset is None:
            connection.execute("DELETE FROM password_resets WHERE token_hash = ?", (token_hash,))
            connection.commit()
            return False
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), reset["user_id"]),
        )
        connection.execute("DELETE FROM password_resets WHERE user_id = ?", (reset["user_id"],))
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (reset["user_id"],))
        connection.commit()
        return True
    finally:
        connection.close()


def create_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + SESSION_DURATION).isoformat()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )
        connection.commit()
    finally:
        connection.close()
    return token, expires_at


def get_session_user(token: str | None) -> dict | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        user = connection.execute(
            """SELECT users.id, users.email FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (token_hash, now),
        ).fetchone()
        if user is None:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            connection.commit()
            return None
        return {"id": user["id"], "email": user["email"]}
    finally:
        connection.close()


def delete_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = connect()
    try:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        connection.commit()
    finally:
        connection.close()


def add_sitemap(user_id: int, url: str) -> dict | None:
    normalized_url = url.strip()
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        cursor = connection.execute(
            "INSERT INTO user_sitemaps(user_id, url, created_at) VALUES (?, ?, ?)",
            (user_id, normalized_url, now),
        )
        connection.commit()
        return {"id": cursor.lastrowid, "url": normalized_url, "created_at": now}
    except sqlite3.IntegrityError as error:
        if "user_sitemaps.user_id, user_sitemaps.url" in str(error):
            return None
        raise
    finally:
        connection.close()


def list_sitemaps(user_id: int) -> list[dict]:
    connection = connect()
    try:
        rows = connection.execute(
            """SELECT id, url, created_at, last_sync_at, last_duplicate_urls_skipped
               FROM user_sitemaps WHERE user_id = ? ORDER BY id""",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_sitemap_schedule(user_id: int) -> str:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT sitemap_schedule FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"User {user_id} does not exist")
        return row["sitemap_schedule"]
    finally:
        connection.close()


def set_sitemap_schedule(user_id: int, schedule: str) -> str | None:
    connection = connect()
    try:
        cursor = connection.execute(
            "UPDATE users SET sitemap_schedule = ? WHERE id = ?",
            (schedule, user_id),
        )
        connection.commit()
        return schedule if cursor.rowcount else None
    finally:
        connection.close()


def get_missing_retention_checks(user_id: int) -> int:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT missing_retention_checks FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"User {user_id} does not exist")
        return int(row["missing_retention_checks"])
    finally:
        connection.close()


def set_missing_retention_checks(user_id: int, checks: int) -> int | None:
    connection = connect()
    try:
        cursor = connection.execute(
            "UPDATE users SET missing_retention_checks = ? WHERE id = ?",
            (checks, user_id),
        )
        connection.commit()
        return checks if cursor.rowcount else None
    finally:
        connection.close()


def list_due_sitemaps(now: datetime | None = None) -> list[dict]:
    current_time = now or datetime.now(timezone.utc)
    intervals = {
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "every_15_days": timedelta(days=15),
        "every_30_days": timedelta(days=30),
        "monthly": timedelta(days=30),
    }
    connection = connect()
    try:
        rows = connection.execute(
            """SELECT user_sitemaps.id, user_sitemaps.url, user_sitemaps.last_checked_at,
                      users.id AS user_id, users.sitemap_schedule, users.missing_retention_checks
               FROM user_sitemaps
               JOIN users ON users.id = user_sitemaps.user_id
               WHERE users.sitemap_schedule != 'manual'
               ORDER BY user_sitemaps.id"""
        ).fetchall()
        due = []
        for row in rows:
            interval = intervals[row["sitemap_schedule"]]
            last_checked = row["last_checked_at"]
            if last_checked is None:
                due.append({
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "url": row["url"],
                    "schedule": row["sitemap_schedule"],
                    "retention_checks": int(row["missing_retention_checks"]),
                })
                continue
            checked_at = datetime.fromisoformat(last_checked)
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if current_time - checked_at >= interval:
                due.append({
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "url": row["url"],
                    "schedule": row["sitemap_schedule"],
                    "retention_checks": int(row["missing_retention_checks"]),
                })
        return due
    finally:
        connection.close()


def mark_sitemap_checked(sitemap_id: int, checked_at: datetime | None = None) -> None:
    connection = connect()
    try:
        connection.execute(
            "UPDATE user_sitemaps SET last_checked_at = ? WHERE id = ?",
            ((checked_at or datetime.now(timezone.utc)).isoformat(), sitemap_id),
        )
        connection.commit()
    finally:
        connection.close()


def remove_sitemap(user_id: int, sitemap_id: int) -> bool:
    connection = connect()
    try:
        cursor = connection.execute(
            "DELETE FROM user_sitemaps WHERE id = ? AND user_id = ?",
            (sitemap_id, user_id),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()
