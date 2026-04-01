# Backend Guide (RAG + Workflow)

This backend currently has two core parts:

1. `rag.py` = retrieves relevant travel knowledge from the markdown corpus.
2. `workflow.py` + `state.py` = controls the trip-planning pipeline and state.

---

## File Map

- `rag.py`
  - Builds/loads ChromaDB vector store from `backend/corpus/*.md`
  - Retrieves top semantic matches for a user query
- `state.py`
  - Defines `TripState` and workflow stage tracking
- `workflow.py`
  - Runs: `intake -> clarify -> retrieve -> validate -> generate`
- `corpus/*.md`
  - Knowledge base used by RAG retrieval
- `.chromadb/`
  - Local persisted vector DB artifacts (runtime data)

---

## End-to-End Flow

### 1) Intake
Parses raw user text into structured fields in `TripState`:
- destination
- duration
- budget
- interests
- traveler hints (solo/family/etc.)

### 2) Clarify
Generates follow-up questions if important fields are missing.
- Uses OpenAI if configured (`OPENAI_API_KEY` + `openai` package).
- Falls back to local rule-based questions if OpenAI is unavailable.

Important: in current CLI mode, questions are stored in state (`progress.clarifying_questions`) but the script does **not** pause and ask interactively.

### 3) Retrieve
Builds a retrieval query from parsed state and calls `rag.retrieve(...)`.
Stores results under `progress.retrieved_chunks`.

### 4) Validate
Checks basic planning conflicts:
- invalid date order
- unrealistic low budget for duration
- low daily budget for expensive destinations

Writes findings to `progress.validation_issues` and `progress.is_valid`.

### 5) Generate
Produces final recommendation text using:
- structured state
- retrieved context
- validation notes

If OpenAI is unavailable, falls back to local template generation.

---

## TripState JSON Structure

Top-level required fields:
- `user_profile`
- `trip_overview`
- `constraints`
- `progress`

Example:

```json
{
  "user_profile": {
    "traveler_type": "solo",
    "group_size": 1,
    "home_airport": null,
    "preferences": []
  },
  "trip_overview": {
    "request_text": "10 day trip to japan with $1800 for food and culture",
    "destination": "Japan",
    "duration_days": 10,
    "start_date": null,
    "end_date": null,
    "start_month": null,
    "interests": ["food", "culture"]
  },
  "constraints": {
    "budget_total_usd": 1800,
    "budget_per_day_usd": 180.0,
    "must_avoid": [],
    "accessibility_needs": []
  },
  "progress": {
    "workflow_stage": "generate",
    "stage_history": ["intake", "clarify", "retrieve", "validate", "generate"],
    "clarifying_questions": [],
    "retrieval_query": "...",
    "retrieved_chunks": [],
    "retrieval_error": "",
    "validation_issues": [],
    "is_valid": true,
    "final_recommendation": "...",
    "raw_user_input": "..."
  }
}
```

---

## How to Run

From repo root:

```bash
python backend/workflow.py "planning a 10 day trip to japan with a $1800 budget focused on food and culture"
```

Or test with missing details:

```bash
python backend/workflow.py "i want a vacation"
```

Expected:
- Pipeline reaches `progress.workflow_stage = "generate"`
- `stage_history` shows all 5 stages in order
- `clarifying_questions` contains questions if input is underspecified

---

