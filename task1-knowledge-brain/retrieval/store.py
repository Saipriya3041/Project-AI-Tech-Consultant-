import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from retrieval.embeddings import generate_embeddings, generate_query_embedding


def database_path() -> str:
    return os.getenv("KNOWLEDGE_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "knowledge.sqlite3"))


def connect() -> sqlite3.Connection:
    path = database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
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
            links_json TEXT NOT NULL DEFAULT '[]',
            links_scanned INTEGER NOT NULL DEFAULT 0
        );
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
    now = datetime.now(timezone.utc).isoformat()
    connection = connect()
    try:
        previous = connection.execute("SELECT content_hash FROM articles WHERE url = ?", (url,)).fetchone()
        if previous and previous["content_hash"] == content_hash:
            connection.execute(
                "UPDATE articles SET status = 'active', error = NULL, last_modified = ?, updated_at = ?, links_json = ?, links_scanned = 1 WHERE url = ?",
                (last_modified, now, links_json, url),
            )
            connection.commit()
            return False
        connection.execute("""
            INSERT INTO articles(url, title, content_hash, last_modified, status, attempts, error, updated_at, links_json, links_scanned)
            VALUES (?, ?, ?, ?, 'active', 0, NULL, ?, ?, 1)
            ON CONFLICT(url) DO UPDATE SET title=excluded.title, content_hash=excluded.content_hash,
                last_modified=excluded.last_modified, status='active', error=NULL, updated_at=excluded.updated_at,
                links_json=excluded.links_json, links_scanned=1
        """, (url, title, content_hash, last_modified, now, links_json))
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


def article_needs_fetch(url: str, last_modified: str | None) -> bool:
    if not last_modified:
        return True
    connection = connect()
    try:
        row = connection.execute("SELECT last_modified, status, links_scanned FROM articles WHERE url = ?", (url,)).fetchone()
        return row is None or row["status"] != "active" or not row["links_scanned"] or row["last_modified"] != last_modified
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


def search_chunks(query: str, limit: int = 3) -> list[dict]:
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
        rows = connection.execute("SELECT title, url, heading, text, image_refs_json, embedding_json FROM chunks WHERE active = 1").fetchall()
    finally:
        connection.close()
    query_vector = generate_query_embedding(query)
    requires_article = bool(re.search(r"\b(how|steps?|configure|update|change|enable|disable|setup|set up|fix|troubleshoot)\b", query, re.I))
    matches = []
    for row in rows:
        source = urlparse(row["url"])
        if (
            requires_article
            and source.hostname == "support.brilliantdirectories.com"
            and source.path.startswith("/support/solutions")
            and "/articles/" not in source.path
        ):
            continue
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
        relevance = max(lexical_relevance, vector_relevance) if query_vector else lexical_relevance
        if lexical_relevance >= 0.3 or vector_relevance >= 0.72:
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


def get_ingestion_status() -> dict:
    connection = connect()
    try:
        rows = connection.execute("SELECT status, COUNT(*) AS count FROM articles GROUP BY status").fetchall()
        return {row["status"]: row["count"] for row in rows} | {"total": sum(row["count"] for row in rows)}
    finally:
        connection.close()