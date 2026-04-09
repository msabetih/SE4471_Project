# Backend Guide (RAG + Workflow + API)

The backend has four layers:

1. **`rag.py`** — Embeds the markdown corpus into ChromaDB and retrieves top semantic chunks for a query.
2. **`state.py`** — Defines `TripState` (user profile, trip overview, constraints, progress) and JSON merge rules for multi-turn chat.
3. **`workflow.py`** — Runs the pipeline: `intake → clarify → retrieve → validate → generate` (generation is skipped until core trip fields are complete).
4. **`main.py`** — FastAPI app: `GET /health`, `POST /chat` (runs `run_workflow`).
5. **`itinerary.py`** — Builds a structured itinerary (JSON from the model → canonical Markdown) using `TripState`, validation notes, and **retrieved context**; prompts require corpus citations by filename.
6. **`weather_tool.py`** — **External tool (Tier 2):** Open-Meteo geocoding + daily forecast; runs **after** required trip fields are complete and **before** `generate`. No API key. Results live in `progress.weather_summary`, `progress.weather_error`, `progress.weather_meta`.

---

## File map

| File / directory | Role |
|------------------|------|
| `main.py` | FastAPI, CORS for local frontends, `/health`, `/chat` |
| `workflow.py` | Stage functions + `run_workflow`, intake parsing, clarify/retrieve/validate/generate |
| `state.py` | `TripState`, `from_dict` / `to_dict`, merge rules (nested `state` payload, `accumulated_context`, interests) |
| `rag.py` | Chroma + sentence-transformers, `embed_corpus`, `retrieve`, `format_retrieved_context` |
| `weather_tool.py` | Open-Meteo forecast for trip dates; injected into itinerary prompts |
| `itinerary.py` | `generate_itinerary`: JSON itinerary + Markdown; grounded-in-corpus citation rules |
| `corpus/*.md` | Knowledge base (13 destination/topic guides) |
| `.chromadb/` | Persisted vector index (created on first retrieval or via `embed_corpus`) |

---

## Setup

From the **repository root** (recommended):

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

### Environment variables

Copy `.env.example` to `.env` in the **repo root** and/or **`backend/.env`**. Both paths are loaded (`backend/.env` wins on duplicate keys). Use UTF-8; a BOM is fine on Windows.

| Variable | Required | Notes |
|----------|----------|--------|
| `OPENAI_API_KEY` | Yes, for LLM clarify + itinerary | Without it, clarify uses local rules; generation returns empty structured itinerary (see `itinerary_llm_error`) |
| `OPENAI_MODEL` | No | Defaults to `gpt-4o-mini` |

See `.env.example` for the exact format.

---

## Run the HTTP API

From the **repository root**:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Health:** `GET /health` — returns `openai_key_loaded` so you can confirm the API key is visible to the server.

### `POST /chat`

**Body (JSON):**

```json
{
  "message": "User message for this turn",
  "state": {}
}
```

- **First turn:** send `"state": {}` or omit `state`.
- **Later turns:** send the **`state` object from the previous response** (the full `state` field inside the JSON body). The client may also send the **entire previous JSON response** as `state`; the backend unwraps a nested `state` or `data` field when present.

**Important:** Do not drop `constraints`, `trip_overview`, or `progress` when round-tripping; budget and interests must survive merge + intake on the next turn.

**Response (top-level):** Includes `state` (full `TripState`), `assistant_message`, `clarifying_questions`, `workflow_stage`, `stage_history`, retrieval and validation fields, `itinerary_structured` (JSON or `null`), `itinerary_llm_error`, `awaiting_clarification`, and `accumulated_context` for debugging.

---

## End-to-end workflow

### 1) Intake

Parses the user message and merged state into `TripState`: destination, duration, budget, interests, traveler hints. When `start_date` and `duration_days` are present, `end_date` is auto-calculated. Builds **`trip_overview.request_text`** and **`progress.accumulated_context`** as the **full conversation text** so later turns can re-parse budget and interests.

### 2) Clarify

If destination, duration, budget, or interests are still missing, OpenAI may propose follow-up questions (or local rules if no API key). If details are incomplete, **`generate` is not run**; the assistant message explains what to send next (`awaiting_clarification: true`).

### 3) Retrieve

Builds a query from destination, budget, interests, and request text, then runs **`rag.retrieve`**. Results are stored in **`progress.retrieved_chunks`** (used by validation and itinerary generation).

### 4) Validate

Light checks (dates, budget vs duration, expensive destinations). Issues go to **`progress.validation_issues`** and **`progress.is_valid`**.

### 5) Weather tool (before generate)

When core fields are complete, **`weather_tool.apply_weather_to_progress`** geocodes **`trip_overview.destination`** and requests a **daily forecast** from Open-Meteo for the trip window. You need **`start_date`** plus **`end_date`** *or* **`start_date`** + **`duration_days`**. If dates are missing, a short skip message is stored in **`progress.weather_summary`**. Forecasts are limited to roughly the **next 16 days** from “today”; trips further out get a guidance message instead of live rows.

### 6) Generate

**`itinerary.generate_itinerary`** receives the weather block in the prompt (outdoor/indoor balance, packing). Output is Markdown + optional parsed JSON, grounded in retrieved chunks and citing corpus **filenames**. If the LLM fails, see **`itinerary_llm_error`** and fallback behavior in code.

---

## TripState JSON shape

Top-level keys:

- `user_profile`
- `trip_overview`
- `constraints`
- `progress`

Example (abbreviated; your app should round-trip the real object returned by `/chat`):

```json
{
  "user_profile": {
    "traveler_type": "solo",
    "group_size": 1,
    "home_airport": null,
    "preferences": []
  },
  "trip_overview": {
    "request_text": "… full combined user text …",
    "destination": "Japan",
    "duration_days": 10,
    "start_date": null,
    "end_date": null,
    "start_month": null,
    "interests": ["food", "culture"]
  },
  "constraints": {
    "budget_total_cad": 1800,
    "budget_per_day_cad": 180.0,
    "must_avoid": [],
    "accessibility_needs": []
  },
  "progress": {
    "workflow_stage": "generate",
    "stage_history": ["intake", "clarify", "retrieve", "validate", "generate"],
    "clarifying_questions": [],
    "retrieval_query": "…",
    "retrieved_chunks": [],
    "retrieval_error": "",
    "validation_issues": [],
    "is_valid": true,
    "final_recommendation": "… Markdown itinerary …",
    "itinerary_structured": {},
    "itinerary_llm_error": "",
    "awaiting_clarification": false,
    "raw_user_input": "…",
    "accumulated_context": "…"
  }
}
```

---

## Optional: CLI workflow (no HTTP)

From the repo root, you can run the pipeline and print final JSON:

```bash
python backend/workflow.py "planning a 10 day trip to japan with a $1800 CAD budget focused on food and culture"
```

Underspecified input may stop at clarify (no full itinerary):

```bash
python backend/workflow.py "i want a vacation"
```

---

## RAG corpus and index

- **Corpus:** `backend/corpus/*.md` (13 markdown files).
- **First query:** If Chroma has no vectors yet, **`retrieve`** calls **`embed_corpus()`** automatically (may take a minute while the embedding model downloads).

To force a full re-embed, use `rag.embed_corpus(force_reload=True)` from Python or the `rag.py` CLI patterns in that file.
