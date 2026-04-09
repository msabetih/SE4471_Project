# SE4471 Project: Travel Planning Assistant (RAG + Workflow + API)

This repository contains a travel-planning backend that combines:

1. Retrieval-Augmented Generation (RAG) over a curated travel corpus.
2. A multi-step planning workflow (`intake -> clarify -> retrieve -> validate -> generate`).
3. A FastAPI server (`/health`, `/chat`) for frontend or API testing.

The backend supports multi-turn chat by passing a `state` object between requests.

## Repository Layout

- `backend/main.py`: FastAPI entrypoint (`GET /health`, `POST /chat`)
- `backend/workflow.py`: Workflow controller and stage orchestration
- `backend/state.py`: `TripState` schema + state merge logic
- `backend/rag.py`: ChromaDB embedding + retrieval
- `backend/itinerary.py`: Structured itinerary generation (JSON -> Markdown)
- `backend/corpus/*.md`: Travel knowledge base (13 markdown documents)
- `requirements.txt`: Python dependencies
- `.env.example`: Environment variable template

## Dependencies

Installed from `requirements.txt`:

## Setup

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate     # macOS/Linux
# .venv\Scripts\activate      # Windows PowerShell

pip install -r requirements.txt
```

## Environment Configuration

1. Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

2. Set your OpenAI key:

```env
OPENAI_API_KEY=sk-...
```

Environment file loading behavior:

- The app loads both:
  - repo root: `.env`
  - backend folder: `backend/.env`
- If both exist, `backend/.env` overrides duplicate keys.

## Run the API

From repo root:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- Swagger UI: `http://127.0.0.1:8000/docs`

## Quick Verification (2-minute test)

### 1) Health check

```bash
curl http://127.0.0.1:8000/health
```

Expected:

- `"status": "ok"`
- `"openai_key_loaded": true` (if key is configured)

### 2) First chat turn (underspecified input)

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"i want a vacation","state":{}}'
```

Expected in response:

- `workflow_stage: "clarify"`
- `awaiting_clarification: true`
- `clarifying_questions` populated
- `state` object returned

### 3) Second chat turn (provide missing details)

Run another request (same endpoint), and paste the exact `state` object returned in Step 2:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message":"Japan, 10 days, budget 1800 CAD, interests food and culture",
    "state": <PASTE_STATE_OBJECT_FROM_STEP_2_HERE>
  }'
```

Expected:

- `workflow_stage: "generate"`
- `awaiting_clarification: false`
- `assistant_message` contains itinerary/recommendation content

## Important API Usage Notes

- `POST /chat` body:

```json
{
  "message": "user text",
  "state": {}
}
```

- First turn: use empty `{}` for `state`.
- Later turns: pass back the full `state` from prior response.
- Top-level response fields (`assistant_message`, `workflow_stage`, etc.) are convenience mirrors; canonical session memory lives in `state`.

## Optional CLI Test (without HTTP)

```bash
python backend/workflow.py "planning a 10 day trip to japan with a $1800 CAD budget focused on food and culture"

## Frontend (React UI)

A React frontend was implemented to provide a user-friendly interface for interacting with the backend travel planning system.

### Features

- Chat-style interface for multi-turn interaction
- Trip parameters form including:
  - destination
  - duration
  - start date (date picker; end date is auto-calculated from duration)
  - budget
  - group size
  - dietary restrictions
  - interests
- Structured itinerary rendering using Markdown
- Displays workflow stages, validation issues, and retrieved sources
- Maintains conversation state across turns

---

## Frontend Setup

From the repository root:

```bash
cd frontend
npm install
npm run dev
