import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse
from retrieval.embeddings import generate_embeddings, generate_query_embedding


def database_path() -> str:
    return os.getenv("KNOWLEDGE_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "knowledge.sqlite3"))


_db_initialized = False
_db_init_lock = Lock()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            last_modified TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            links_json TEXT NOT NULL DEFAULT '[]',
            links_scanned INTEGER NOT NULL DEFAULT 0,
            content_snapshot TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS article_change_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL,
            changed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS article_change_history_changed
            ON article_change_history(changed_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            heading TEXT NOT NULL,
            text TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            image_refs_json TEXT NOT NULL DEFAULT '[]',
            embedding_json TEXT
        );
        CREATE INDEX IF NOT EXISTS chunks_source_active
            ON chunks(source_id, active);
        CREATE TABLE IF NOT EXISTS escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            reviewer TEXT,
            answer TEXT
        );
        CREATE TABLE IF NOT EXISTS ingestion_state (
            sitemap_url TEXT PRIMARY KEY,
            cursor_index INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sitemap_schedule TEXT NOT NULL DEFAULT 'manual'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_sitemaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_checked_at TEXT,
            last_sync_at TEXT,
            last_duplicate_urls_skipped INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, url)
        );
        CREATE TABLE IF NOT EXISTS sitemap_articles (
            sitemap_id INTEGER NOT NULL REFERENCES user_sitemaps(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            missing_checks INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(sitemap_id, url)
        );
        CREATE INDEX IF NOT EXISTS sitemap_articles_url_status
            ON sitemap_articles(url, status);
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
    """)
        chunk_columns = {row["name"] for row in connection.execute("PRAGMA table_info(chunks)")}
        if "image_refs_json" not in chunk_columns:
            connection.execute("ALTER TABLE chunks ADD COLUMN image_refs_json TEXT NOT NULL DEFAULT '[]'")
        if "embedding_json" not in chunk_columns:
            connection.execute("ALTER TABLE chunks ADD COLUMN embedding_json TEXT")
        article_columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)")}
        if "links_json" not in article_columns:
            connection.execute("ALTER TABLE articles ADD COLUMN links_json TEXT NOT NULL DEFAULT '[]'")
        if "links_scanned" not in article_columns:
            connection.execute("ALTER TABLE articles ADD COLUMN links_scanned INTEGER NOT NULL DEFAULT 0")
        if "archived_at" not in article_columns:
            connection.execute("ALTER TABLE articles ADD COLUMN archived_at TEXT")
        if "content_snapshot" not in article_columns:
            connection.execute("ALTER TABLE articles ADD COLUMN content_snapshot TEXT NOT NULL DEFAULT '{}'")
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "sitemap_schedule" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN sitemap_schedule TEXT NOT NULL DEFAULT 'manual'")
        if "missing_retention_checks" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN missing_retention_checks INTEGER NOT NULL DEFAULT 1")
        sitemap_columns = {row["name"] for row in connection.execute("PRAGMA table_info(user_sitemaps)")}
        if "last_checked_at" not in sitemap_columns:
            connection.execute("ALTER TABLE user_sitemaps ADD COLUMN last_checked_at TEXT")
        if "last_sync_at" not in sitemap_columns:
            connection.execute("ALTER TABLE user_sitemaps ADD COLUMN last_sync_at TEXT")
        if "last_duplicate_urls_skipped" not in sitemap_columns:
            connection.execute("ALTER TABLE user_sitemaps ADD COLUMN last_duplicate_urls_skipped INTEGER NOT NULL DEFAULT 0")
        _db_initialized = True


def connect() -> sqlite3.Connection:
    path = database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    _ensure_schema(connection)
    return connection


def save_article(
    url: str,
    title: str,
    sections: list[dict],
    last_modified: str | None = None,
    images: list[dict] | None = None,
    links: list[str] | None = None,
) -> bool:
    images = images or []
    links_json = json.dumps(sorted(set(links or [])))
    content = "\n".join(f"{item['heading']}\n{item['text']}" for item in sections)
    content_hash = hashlib.sha256(f"{title}\n{content}\n{json.dumps(images, sort_keys=True)}\n{links_json}".encode("utf-8")).hexdigest()
    content_snapshot = json.dumps({
        "title": title,
        "sections": sections,
        "images": images,
        "links": sorted(set(links or [])),
    }, ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        previous = connection.execute(
            "SELECT title, content_hash, content_snapshot FROM articles WHERE url = ?",
            (url,),
        ).fetchone()
        if previous and previous["content_hash"] == content_hash:
            missing_chunks = connection.execute(
                "SELECT chunk_id, text FROM chunks WHERE source_id = ? AND embedding_json IS NULL",
                (url,),
            ).fetchall()
            if missing_chunks:
                vectors = generate_embeddings([row["text"] for row in missing_chunks])
                if vectors:
                    connection.executemany(
                        "UPDATE chunks SET embedding_json = ? WHERE chunk_id = ?",
                        [(json.dumps(vector), row["chunk_id"]) for row, vector in zip(missing_chunks, vectors)],
                    )
            connection.execute(
                "UPDATE articles SET status = 'active', error = NULL, archived_at = NULL, last_modified = ?, updated_at = ?, links_json = ?, links_scanned = 1, content_snapshot = ? WHERE url = ?",
                (last_modified, now, links_json, content_snapshot, url),
            )
            connection.execute(
                "UPDATE chunks SET active = 1 WHERE source_id = ?",
                (url,),
            )
            connection.commit()
            return False
        connection.execute("""
            INSERT INTO articles(url, title, content_hash, last_modified, status, attempts, error, updated_at, links_json, links_scanned, archived_at, content_snapshot)
            VALUES (?, ?, ?, ?, 'active', 0, NULL, ?, ?, 1, NULL, ?)
            ON CONFLICT(url) DO UPDATE SET title=excluded.title, content_hash=excluded.content_hash,
                last_modified=excluded.last_modified, status='active', error=NULL, updated_at=excluded.updated_at,
                links_json=excluded.links_json, links_scanned=1, archived_at=NULL,
                content_snapshot=excluded.content_snapshot
        """, (url, title, content_hash, last_modified, now, links_json, content_snapshot))
        if previous and previous["content_hash"]:
            connection.execute(
                """INSERT INTO article_change_history(url, old_content, new_content, changed_at)
                   VALUES (?, ?, ?, ?)""",
                (url, previous["content_snapshot"], content_snapshot, now),
            )
        connection.execute("DELETE FROM chunks WHERE source_id = ?", (url,))
        chunk_records = []
        for section in sections:
            remaining = section["text"].strip()
            section_terms = set(re.findall(r"[a-z0-9]+", remaining.lower()))
            section_images = [
                image for image in images
                if not image.get("context") or section_terms.intersection(re.findall(r"[a-z0-9]+", image["context"].lower()))
            ]
            while remaining:
                split_at = min(len(remaining), 1600)
                if split_at < len(remaining):
                    split_at = remaining.rfind(" ", 0, split_at) or split_at
                text = remaining[:split_at].strip()
                remaining = remaining[split_at:].strip()
                chunk_id = hashlib.sha256(f"{url}\0{section['heading']}\0{text}".encode("utf-8")).hexdigest()
                chunk_records.append({
                    "chunk_id": chunk_id,
                    "heading": section["heading"],
                    "text": text,
                    "images": section_images,
                })
        vectors = generate_embeddings([item["text"] for item in chunk_records])
        for index, item in enumerate(chunk_records):
            vector = json.dumps(vectors[index]) if vectors else None
            connection.execute(
                "INSERT OR REPLACE INTO chunks(chunk_id, source_id, title, url, heading, text, active, image_refs_json, embedding_json) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (item["chunk_id"], url, title, url, item["heading"], item["text"], json.dumps(item["images"]), vector),
            )
        connection.commit()
        return True
    finally:
        connection.close()


def record_failure(url: str, error: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.execute("""
            INSERT INTO articles(url, title, content_hash, status, attempts, error, updated_at)
            VALUES (?, ?, '', 'failed', 1, ?, ?)
            ON CONFLICT(url) DO UPDATE SET status='failed', attempts=articles.attempts + 1, error=excluded.error, updated_at=excluded.updated_at
        """, (url, url, error[:1000], now))
        connection.commit()
    finally:
        connection.close()


def record_404(url: str, error: str = "404 Not Found") -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.execute("""
            INSERT INTO articles(url, title, content_hash, status, attempts, error, updated_at)
            VALUES (?, ?, '', '404', 1, ?, ?)
            ON CONFLICT(url) DO UPDATE SET status='404', attempts=articles.attempts + 1, error=excluded.error, updated_at=excluded.updated_at
        """, (url, url, error[:1000], now))
        connection.commit()
    finally:
        connection.close()


def record_301_redirect(old_url: str, new_url: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.execute("""
            INSERT INTO articles(url, title, content_hash, status, attempts, error, updated_at)
            VALUES (?, ?, '', '301', 1, ?, ?)
            ON CONFLICT(url) DO UPDATE SET status='301', error=excluded.error, updated_at=excluded.updated_at
        """, (old_url, old_url, f"301 Moved permanently to {new_url}"[:1000], now))
        connection.commit()
    finally:
        connection.close()


def ensure_pending_articles(urls: list[str]) -> None:
    if not urls:
        return
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.executemany(
            """INSERT OR IGNORE INTO articles(url, title, content_hash, status, updated_at)
               VALUES (?, ?, '', 'pending', ?)""",
            [(url, url, now) for url in set(urls)],
        )
        connection.commit()
    finally:
        connection.close()


def reconcile_sitemap_urls(sitemap_id: int, current_urls: set[str], retention_checks: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    threshold = retention_checks if retention_checks in {1, 2, 15, 30} else 1
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for url in current_urls:
            connection.execute(
                """INSERT INTO sitemap_articles(sitemap_id, url, missing_checks, status, last_seen_at)
                   VALUES (?, ?, 0, 'active', ?)
                   ON CONFLICT(sitemap_id, url) DO UPDATE SET
                       missing_checks=0, status='active', last_seen_at=excluded.last_seen_at""",
                (sitemap_id, url, now),
            )
        previous_urls = connection.execute(
            "SELECT url FROM sitemap_articles WHERE sitemap_id = ? AND status = 'active'",
            (sitemap_id,),
        ).fetchall()
        newly_archived = []
        for row in previous_urls:
            url = row["url"]
            if url in current_urls:
                continue
            connection.execute(
                "UPDATE sitemap_articles SET missing_checks = missing_checks + 1 WHERE sitemap_id = ? AND url = ?",
                (sitemap_id, url),
            )
            missing = connection.execute(
                "SELECT missing_checks FROM sitemap_articles WHERE sitemap_id = ? AND url = ?",
                (sitemap_id, url),
            ).fetchone()["missing_checks"]
            if missing >= threshold:
                connection.execute(
                    "UPDATE sitemap_articles SET status = 'archived' WHERE sitemap_id = ? AND url = ?",
                    (sitemap_id, url),
                )
                still_referenced = connection.execute(
                    "SELECT 1 FROM sitemap_articles WHERE url = ? AND status = 'active' LIMIT 1",
                    (url,),
                ).fetchone()
                if still_referenced is None:
                    newly_archived.append(url)
        for url in newly_archived:
            connection.execute(
                "UPDATE articles SET status = 'archived', archived_at = ?, updated_at = ? WHERE url = ? AND status != 'archived'",
                (now, now, url),
            )
            connection.execute("UPDATE chunks SET active = 0 WHERE source_id = ?", (url,))
        connection.commit()
        return len(newly_archived)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_sitemap_sync(sitemap_id: int, duplicate_urls_skipped: int, checked_at: datetime | None = None) -> None:
    synced_at = (checked_at or datetime.now(timezone.utc)).isoformat()
    connection = connect()
    try:
        connection.execute(
            """UPDATE user_sitemaps
               SET last_checked_at = ?, last_sync_at = ?, last_duplicate_urls_skipped = ?
               WHERE id = ?""",
            (
                synced_at,
                synced_at,
                max(0, duplicate_urls_skipped),
                sitemap_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def list_archived_articles() -> list[dict]:
    connection = connect()
    try:
        rows = connection.execute(
            """SELECT url, title, archived_at FROM articles
               WHERE status = 'archived' ORDER BY archived_at DESC, url"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def restore_archived_article(url: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "UPDATE articles SET status = 'active', archived_at = NULL, updated_at = ? WHERE url = ? AND status = 'archived'",
            (now, url),
        )
        if cursor.rowcount:
            connection.execute("UPDATE chunks SET active = 1 WHERE source_id = ?", (url,))
            connection.execute(
                "UPDATE sitemap_articles SET status = 'active', missing_checks = 0 WHERE url = ?",
                (url,),
            )
        connection.commit()
        return cursor.rowcount > 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def article_needs_fetch(url: str, last_modified: str | None) -> bool:
    connection = connect()
    try:
        row = connection.execute("SELECT last_modified, status, links_scanned FROM articles WHERE url = ?", (url,)).fetchone()
        if row is None or row["status"] != "active" or not row["links_scanned"] or row["last_modified"] != last_modified:
            return True
        return connection.execute(
            "SELECT 1 FROM chunks WHERE source_id = ? AND embedding_json IS NULL LIMIT 1",
            (url,),
        ).fetchone() is not None
    finally:
        connection.close()


def get_article_links(url: str) -> list[str]:
    connection = connect()
    try:
        row = connection.execute("SELECT links_json FROM articles WHERE url = ? AND status = 'active'", (url,)).fetchone()
        return json.loads(row["links_json"]) if row else []
    finally:
        connection.close()


def get_pending_article_links() -> list[str]:
    connection = connect()
    try:
        rows = connection.execute("SELECT url, links_json FROM articles WHERE status = 'active'").fetchall()
        active_urls = {row["url"] for row in rows}
        links = {
            link
            for row in rows
            for link in json.loads(row["links_json"])
            if link not in active_urls
        }
        open_questions = [
            row["question"]
            for row in connection.execute("SELECT question FROM escalations WHERE status = 'open'").fetchall()
        ]
        stop_words = {"about", "and", "are", "can", "from", "how", "into", "the", "what", "where", "with", "your"}
        question_terms = [
            {word for word in re.findall(r"[a-z0-9]+", question.lower()) if len(word) > 2 and word not in stop_words}
            for question in open_questions
        ]

        def priority(link: str) -> tuple[int, int, str]:
            link_terms = set(re.findall(r"[a-z0-9]+", link.lower()))
            relevance = max(
                (len(terms & link_terms) for terms in question_terms),
                default=0,
            )
            article_rank = 0 if "/articles/" in urlparse(link).path else 1 if "/folders/" in urlparse(link).path else 2
            return (-relevance, article_rank, link)

        return sorted(
            links,
            key=priority,
        )
    finally:
        connection.close()


def get_sitemap_cursor(sitemap_url: str, page_count: int) -> int:
    if page_count <= 0:
        return 0
    connection = connect()
    try:
        row = connection.execute("SELECT cursor_index FROM ingestion_state WHERE sitemap_url = ?", (sitemap_url,)).fetchone()
        return int(row["cursor_index"]) % page_count if row else 0
    finally:
        connection.close()


def save_sitemap_cursor(sitemap_url: str, cursor_index: int) -> None:
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO ingestion_state(sitemap_url, cursor_index, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(sitemap_url) DO UPDATE SET cursor_index=excluded.cursor_index, updated_at=excluded.updated_at",
            (sitemap_url, max(0, cursor_index), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def deactivate_removed_urls(current_urls: set[str]) -> int:
    connection = connect()
    try:
        rows = connection.execute("SELECT url FROM articles WHERE status = 'active'").fetchall()
        removed = [row["url"] for row in rows if row["url"] not in current_urls]
        for url in removed:
            connection.execute("UPDATE articles SET status = 'removed' WHERE url = ?", (url,))
            connection.execute("UPDATE chunks SET active = 0 WHERE source_id = ?", (url,))
        connection.commit()
        return len(removed)
    finally:
        connection.close()


def search_chunks(query: str, limit: int = 3, user_id: int | None = None) -> list[dict]:
    stop_words = {
        "about", "after", "again", "also", "and", "are", "can", "change", "could", "does", "for", "from",
        "help", "how", "into", "member", "product", "service", "site", "support", "the", "this", "that",
        "update", "website", "what", "when", "where", "which", "with", "your", "would",
    }
    terms = {word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) > 2 and word not in stop_words}
    if "invoice" in terms or "invoices" in terms:
        terms.update({"billing", "invoice"})
    if "billing" in terms:
        terms.add("invoice")
    if not terms:
        return []
    connection = connect()
    try:
        if user_id is not None:
            # First try matching chunks from URLs that belong to this user's sitemaps
            rows = connection.execute(
                """
                SELECT DISTINCT c.title, c.url, c.heading, c.text, c.image_refs_json, c.embedding_json
                FROM chunks c
                JOIN sitemap_articles sa ON sa.url = c.url AND sa.status = 'active'
                JOIN user_sitemaps us ON us.id = sa.sitemap_id AND us.user_id = ?
                WHERE c.active = 1
                """,
                (user_id,),
            ).fetchall()
            if not rows:
                # If sitemap_articles hasn't linked the URLs yet, match chunks by domain of user's sitemaps
                user_sources = [r["url"] for r in connection.execute("SELECT url FROM user_sitemaps WHERE user_id = ?", (user_id,)).fetchall()]
                if user_sources:
                    from urllib.parse import urlparse
                    domains = {urlparse(u).netloc for u in user_sources if urlparse(u).netloc}
                    all_active = connection.execute(
                        "SELECT title, url, heading, text, image_refs_json, embedding_json FROM chunks WHERE active = 1"
                    ).fetchall()
                    rows = [r for r in all_active if urlparse(r["url"]).netloc in domains]
                if not rows:
                    rows = connection.execute(
                        "SELECT title, url, heading, text, image_refs_json, embedding_json FROM chunks WHERE active = 1"
                    ).fetchall()
        else:
            rows = connection.execute(
                "SELECT title, url, heading, text, image_refs_json, embedding_json FROM chunks WHERE active = 1"
            ).fetchall()
    finally:
        connection.close()
    query_vector = generate_query_embedding(query)
    requires_article = bool(re.search(r"\b(how|steps?|configure|update|change|enable|disable|setup|set up|fix|troubleshoot)\b", query, re.I))
    matches = []
    for row in rows:
        image_references = json.loads(row["image_refs_json"])
        image_text = " ".join(item.get("description") or item.get("alt", "") for item in image_references)
        content = f"{row['title']} {row['heading']} {row['text']} {image_text}".lower()
        if re.search(r"\b(?:site|website|homepage|home page)\b.{0,30}\blogos?\b", query, re.I):
            member_logo_context = re.search(r"\b(?:member|profile|listing)\b.{0,80}\blogos?\b", content)
            site_logo_context = re.search(r"\b(?:site|website|homepage|home page)\b.{0,40}\blogos?\b", content)
            if member_logo_context and not site_logo_context:
                continue
        matched = {term for term in terms if term in content}
        lexical_relevance = len(matched) / len(terms)
        vector_relevance = 0.0
        if query_vector and row["embedding_json"]:
            document_vector = json.loads(row["embedding_json"])
            if len(query_vector) == len(document_vector):
                dot = sum(left * right for left, right in zip(query_vector, document_vector))
                query_norm = sum(value * value for value in query_vector) ** 0.5
                document_norm = sum(value * value for value in document_vector) ** 0.5
                if query_norm and document_norm:
                    vector_relevance = max(0.0, dot / (query_norm * document_norm))
        relevance = (
            0.7 * lexical_relevance + 0.3 * vector_relevance
            if query_vector and lexical_relevance >= 0.3
            else vector_relevance if query_vector else lexical_relevance
        )
        if lexical_relevance >= 0.3 or vector_relevance >= 0.78:
            item = dict(row)
            item["image_references"] = json.loads(item.pop("image_refs_json"))
            item.pop("embedding_json", None)
            item["retrieval_method"] = "hybrid" if query_vector and lexical_relevance >= 0.3 else "semantic" if query_vector else "keyword"
            matches.append({**item, "relevance": relevance})
    return sorted(matches, key=lambda item: item["relevance"], reverse=True)[:limit]


def create_escalation(question: str, reason: str) -> int:
    connection = connect()
    try:
        existing = connection.execute(
            "SELECT id FROM escalations WHERE question = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (question.strip(),),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = connection.execute(
            "INSERT INTO escalations(question, reason, created_at) VALUES (?, ?, ?)",
            (question.strip(), reason, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def resolve_escalation(escalation_id: int, answer: str, reviewer: str) -> dict | None:
    connection = connect()
    try:
        escalation = connection.execute(
            "SELECT * FROM escalations WHERE id = ? AND status = 'open'", (escalation_id,)
        ).fetchone()
        if escalation is None:
            return None
        timestamp = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE escalations SET status='resolved', resolved_at=?, reviewer=?, answer=? WHERE id=?",
            (timestamp, reviewer, answer, escalation_id),
        )
        title = f"Human-reviewed answer for: {escalation['question'][:100]}"
        url = f"expert://escalations/{escalation_id}"
        chunk_id = hashlib.sha256(f"{url}\0{answer}".encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT OR REPLACE INTO chunks(chunk_id, source_id, title, url, heading, text, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (chunk_id, url, title, url, "Expert answer", answer),
        )
        connection.commit()
        return {"id": escalation_id, "status": "resolved", "reviewer": reviewer, "knowledge_source": url}
    finally:
        connection.close()


def get_article_change_history(user_id: int | None = None, limit: int = 25) -> list[dict]:
    connection = connect()
    try:
        query = """
            SELECT history.url, history.old_content, history.new_content, history.changed_at
            FROM article_change_history AS history
        """
        parameters: tuple = ()
        if user_id is not None:
            query += """
                WHERE EXISTS (
                    SELECT 1
                    FROM sitemap_articles AS mapping
                    JOIN user_sitemaps AS sitemap ON sitemap.id = mapping.sitemap_id
                    WHERE mapping.url = history.url AND sitemap.user_id = ?
                )
            """
            parameters = (user_id, max(1, min(limit, 100)))
        else:
            parameters = (max(1, min(limit, 100)),)
        query += " ORDER BY history.changed_at DESC, history.id DESC LIMIT ?"
        rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_ingestion_status(user_id: int | None = None) -> dict:
    connection = connect()
    try:
        rows = connection.execute("SELECT status, COUNT(*) AS count FROM articles GROUP BY status").fetchall()
        embedding_counts = connection.execute(
            """SELECT
                   SUM(CASE WHEN embedding_json IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                   SUM(CASE WHEN embedding_json IS NULL THEN 1 ELSE 0 END) AS pending
               FROM chunks WHERE active = 1"""
        ).fetchone()
        page_counts = connection.execute(
            """SELECT
                   COUNT(CASE WHEN articles.status != 'pending' THEN 1 END) AS processed,
                   COUNT(CASE WHEN articles.status = 'failed' THEN 1 END) AS failed,
                   COUNT(CASE WHEN articles.status = 'archived' THEN 1 END) AS archived,
                   COUNT(CASE WHEN articles.status = 'pending' OR (
                       articles.status = 'active' AND (
                           NOT EXISTS (SELECT 1 FROM chunks WHERE chunks.source_id = articles.url AND chunks.active = 1)
                           OR EXISTS (SELECT 1 FROM chunks WHERE chunks.source_id = articles.url AND chunks.active = 1 AND chunks.embedding_json IS NULL)
                       )
                   ) THEN 1 END) AS pending,
                   COUNT(CASE WHEN articles.status = 'active' AND
                       EXISTS (SELECT 1 FROM chunks WHERE chunks.source_id = articles.url AND chunks.active = 1) AND
                       NOT EXISTS (SELECT 1 FROM chunks WHERE chunks.source_id = articles.url AND chunks.active = 1 AND chunks.embedding_json IS NULL)
                   THEN 1 END) AS embedded
               FROM articles"""
        ).fetchone()
        sitemap_query = """SELECT MAX(last_sync_at) AS last_sync_at,
                                  COALESCE(SUM(last_duplicate_urls_skipped), 0) AS duplicate_urls_skipped
                           FROM user_sitemaps"""
        parameters: tuple = ()
        if user_id is not None:
            sitemap_query += " WHERE user_id = ?"
            parameters = (user_id,)
        sitemap_counts = connection.execute(sitemap_query, parameters).fetchone()
        status_map = {row["status"]: row["count"] for row in rows}
        return {
            **status_map,
            "total": sum(row["count"] for row in rows),
            "processed_pages": page_counts["processed"] or 0,
            "embedded_pages": page_counts["embedded"] or 0,
            "failed_pages": (page_counts["failed"] or 0) + status_map.get("404", 0),
            "active_pages": status_map.get("active", 0),
            "pages_301": status_map.get("301", 0),
            "pages_404": status_map.get("404", 0),
            "pending_pages": page_counts["pending"] or 0,
            "archived_pages": page_counts["archived"] or 0,
            "duplicate_urls_skipped": sitemap_counts["duplicate_urls_skipped"] or 0,
            "last_sync_at": sitemap_counts["last_sync_at"],
            "embedding_count": embedding_counts["embedded"] or 0,
            "embedding_pending": embedding_counts["pending"] or 0,
        }
    finally:
        connection.close()