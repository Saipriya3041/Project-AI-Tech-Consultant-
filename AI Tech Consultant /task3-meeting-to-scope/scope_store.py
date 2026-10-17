import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def database_path() -> str:
    return os.getenv("SCOPE_DB_PATH", str(Path(__file__).resolve().parent / "data" / "scope.sqlite3"))


def connect() -> sqlite3.Connection:
    path = database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            approved_proposal_id TEXT NOT NULL,
            approved_requirements_json TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            reviewer TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            transcript TEXT NOT NULL,
            requirements_json TEXT NOT NULL,
            estimate_json TEXT NOT NULL,
            proposal_text TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewer TEXT
        );
    """)
    return connection


def get_approved_baseline(project_id: str) -> list[dict] | None:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT approved_requirements_json FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return json.loads(row["approved_requirements_json"]) if row else None
    finally:
        connection.close()


def save_proposal(
    project_id: str,
    transcript: str,
    requirements: list[dict],
    estimate: dict,
    proposal_text: str,
) -> str:
    proposal_id = str(uuid4())
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?, 'pending_review', ?, NULL, NULL)",
            (
                proposal_id,
                project_id,
                transcript,
                json.dumps(requirements),
                json.dumps(estimate),
                proposal_text,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        return proposal_id
    finally:
        connection.close()


def approve_proposal(proposal_id: str, reviewer: str) -> dict | None:
    connection = connect()
    try:
        proposal = connection.execute(
            "SELECT * FROM proposals WHERE proposal_id = ? AND status = 'pending_review'", (proposal_id,)
        ).fetchone()
        if proposal is None:
            return None
        timestamp = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE proposals SET status='approved', reviewed_at=?, reviewer=? WHERE proposal_id=?",
            (timestamp, reviewer, proposal_id),
        )
        connection.execute("""
            INSERT INTO projects(project_id, approved_proposal_id, approved_requirements_json, approved_at, reviewer)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET approved_proposal_id=excluded.approved_proposal_id,
                approved_requirements_json=excluded.approved_requirements_json,
                approved_at=excluded.approved_at, reviewer=excluded.reviewer
        """, (proposal["project_id"], proposal_id, proposal["requirements_json"], timestamp, reviewer))
        connection.commit()
        return {"proposal_id": proposal_id, "project_id": proposal["project_id"], "status": "approved", "reviewer": reviewer, "approved_at": timestamp}
    finally:
        connection.close()


def reject_proposal(proposal_id: str, reviewer: str, reason: str) -> dict | None:
    connection = connect()
    try:
        cursor = connection.execute(
            "UPDATE proposals SET status='rejected', reviewed_at=?, reviewer=? "
            "WHERE proposal_id=? AND status='pending_review'",
            (datetime.now(timezone.utc).isoformat(), reviewer, proposal_id),
        )
        if cursor.rowcount == 0:
            return None
        connection.commit()
        return {"proposal_id": proposal_id, "status": "rejected", "reviewer": reviewer, "reason": reason}
    finally:
        connection.close()


def project_history(project_id: str) -> list[dict]:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT proposal_id, status, created_at, reviewed_at, reviewer FROM proposals WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()