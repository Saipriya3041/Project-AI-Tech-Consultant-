from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ingestion.crawler import run_ingestion
from retrieval.search import query_knowledge
from retrieval.store import create_escalation, get_ingestion_status, resolve_escalation

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8081", "http://localhost:8081"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class IngestionRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=1000)


class EscalationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=1000)


class ResolutionRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)
    reviewer: str = Field(min_length=1, max_length=200)


@app.get("/health")
def health():
    return {"status": "ok", "database": "sqlite"}


@app.get("/ask")
def ask_question(q: str):
    if not q.strip():
        raise HTTPException(status_code=422, detail="Question must not be empty")
    answer, sources, needs_human = query_knowledge(q)
    escalation_id = create_escalation(q, "Insufficient relevant indexed evidence") if needs_human else None
    return {
        "query": q,
        "answer": answer,
        "status": "insufficient_evidence" if needs_human else "answered",
        "sources": sources,
        "needs_human": needs_human,
        "escalation_id": escalation_id,
    }


@app.post("/ingestion/run")
def ingestion_run(request: IngestionRequest):
    return run_ingestion(limit=request.limit)


@app.get("/ingestion/status")
def ingestion_status():
    return get_ingestion_status()


@app.post("/escalations")
def create_escalation_task(request: EscalationRequest):
    escalation_id = create_escalation(request.question, request.reason)
    return {"id": escalation_id, "status": "open"}


@app.post("/escalations/{escalation_id}/resolve")
def resolve_escalation_task(escalation_id: int, request: ResolutionRequest):
    result = resolve_escalation(escalation_id, request.answer, request.reviewer)
    if result is None:
        raise HTTPException(status_code=404, detail="Open escalation not found")
    return result
