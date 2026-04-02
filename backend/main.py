"""
FastAPI entrypoint: /health and /chat wired to the trip planning workflow.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

#load .env: repo root first, then backend/ (overrides so backend/.env wins).
_backend_dir = Path(__file__).resolve().parent
_repo_root = _backend_dir.parent
_env_kw = {"override": True, "encoding": "utf-8-sig"} 
load_dotenv(_repo_root / ".env", **_env_kw)
load_dotenv(_backend_dir / ".env", **_env_kw)

try:
    from .workflow import run_workflow
except ImportError:
    from workflow import run_workflow

app = FastAPI(title="Travel planning API", version="0.1.0")

#local dev: explicit origins so allow_credentials=True is valid for browsers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    state: dict = Field(default_factory=dict)


@app.get("/health")
def health():
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return {
        "status": "ok",
        "openai_key_loaded": bool(key),
    }


@app.post("/chat")
def chat(req: ChatRequest):
    trip_state = run_workflow(req.message, state=req.state or None)
    progress = trip_state.progress
    return {
        "state": trip_state.to_dict(),
        "assistant_message": progress.get("final_recommendation", ""),
        "clarifying_questions": progress.get("clarifying_questions", []),
        "workflow_stage": progress.get("workflow_stage"),
        "stage_history": progress.get("stage_history", []),
        "validation_issues": progress.get("validation_issues", []),
        "is_valid": progress.get("is_valid", True),
        "retrieval_query": progress.get("retrieval_query", ""),
        "retrieved_chunks": progress.get("retrieved_chunks", []),
        "retrieval_error": progress.get("retrieval_error", ""),
        "itinerary_structured": progress.get("itinerary_structured"),
        "itinerary_llm_error": progress.get("itinerary_llm_error", ""),
        "awaiting_clarification": progress.get("awaiting_clarification", False),
        "accumulated_context": progress.get("accumulated_context", ""),
    }
