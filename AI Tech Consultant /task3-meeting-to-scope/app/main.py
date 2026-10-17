import json
import os
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from agents.scope_agent import generate_scope
from scope_diff.diff import compare_scopes
from scope_store import approve_proposal, get_approved_baseline, project_history, save_proposal

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8081", "http://localhost:8081"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health():
    return {"status": "ok", "database": "sqlite"}


class ScopeRequest(BaseModel):
    project_id: str = Field(default="default", min_length=1, max_length=100)
    transcript: str = Field(min_length=1, max_length=200000)


class ApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)


def query_knowledge(question: str) -> dict:
    base_url = os.getenv("KNOWLEDGE_BRAIN_URL", "http://127.0.0.1:8010").rstrip("/")
    url = f"{base_url}/ask?{urlencode({'q': question})}"
    try:
        with urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, ValueError):
        return {"status": "unavailable", "sources": [], "needs_human": True}


@app.post("/scope")
def scope_from_meeting(request: ScopeRequest):
    requirements, estimate, proposal = generate_scope(request.transcript, query_knowledge)
    baseline = get_approved_baseline(request.project_id)
    changes = compare_scopes(baseline, requirements) if baseline is not None else None
    proposal_id = save_proposal(request.project_id, request.transcript, requirements, estimate, proposal)
    return {
        "proposal_id": proposal_id,
        "project_id": request.project_id,
        "requirements": requirements,
        "scope": [item["description"] for item in requirements],
        "estimate": estimate,
        "proposal": proposal,
        "changes_from_approved_baseline": changes,
        "approval_required": True,
        "approved_for_sending": False,
    }


@app.post("/scope/{proposal_id}/approve")
def approve_scope(proposal_id: str, request: ApprovalRequest):
    result = approve_proposal(proposal_id, request.reviewer)
    if result is None:
        raise HTTPException(status_code=404, detail="Pending proposal not found")
    return result


@app.get("/scope/projects/{project_id}/history")
def scope_history(project_id: str):
    return {"project_id": project_id, "proposals": project_history(project_id)}
