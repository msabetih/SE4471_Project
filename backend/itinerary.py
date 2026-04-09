"""
Structured itinerary generation: model returns JSON, then we render canonical Markdown.
This keeps day-by-day sections, citations, and budget blocks consistent for the rubric.
"""

from __future__ import annotations
import json
import logging
import os
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

# Full chunk text in TripState + again in "Retrieved context" can exceed model limits; strip from JSON.
_MAX_CONTEXT_CHARS = 32000

try:
    from .rag import format_retrieved_context
    from .state import TripState
    from .workflow import CITY_TO_DESTINATION
except ImportError:
    from rag import format_retrieved_context
    from state import TripState
    from workflow import CITY_TO_DESTINATION


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output (handles optional ```json fences)."""
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def itinerary_json_to_markdown(data: dict[str, Any]) -> str:
    """Turn validated itinerary JSON into stable Markdown sections."""
    lines: list[str] = []

    title = (data.get("trip_title") or "Trip itinerary").strip()
    lines.append(f"# {title}")
    lines.append("")

    summary = data.get("trip_summary_bullets") or []
    if summary:
        lines.append("## Trip summary")
        for item in summary:
            lines.append(f"- {item}")
        lines.append("")

    notes = data.get("planning_notes")
    if notes:
        lines.append("## Planning notes")
        for item in notes:
            lines.append(f"- {item}")
        lines.append("")

    days = data.get("days") or []
    if days:
        lines.append("## Day-by-day plan")
        lines.append("")
        for d in days:
            if not isinstance(d, dict):
                continue
            n = d.get("day_number", "?")
            sub = (d.get("subtitle") or "").strip()
            heading = f"### Day {n}"
            if sub:
                heading += f" — {sub}"
            lines.append(heading)
            lines.append("")
            for part, key in (
                ("Morning", "morning"),
                ("Afternoon", "afternoon"),
                ("Evening", "evening"),
            ):
                block = (d.get(key) or "").strip()
                if block:
                    lines.append(f"**{part}**")
                    lines.append("")
                    lines.append(block)
                    lines.append("")
            costs = d.get("estimated_costs_cad")
            if costs:
                lines.append(f"**Estimated costs (this day):** {costs}")
                lines.append("")
            cites = d.get("citations") or []
            if isinstance(cites, list) and cites:
                lines.append("**Sources cited this day:**")
                for c in cites:
                    lines.append(f"- {c}")
                lines.append("")

    budget = data.get("budget_overview_bullets") or []
    if budget:
        lines.append("## Budget overview")
        for item in budget:
            lines.append(f"- {item}")
        lines.append("")

    sources = data.get("sources_used") or []
    if isinstance(sources, list) and sources:
        lines.append("## Sources used")
        for s in sources:
            lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _state_for_llm_prompt(state: TripState) -> dict[str, Any]:
    """TripState without full RAG chunk text (that lives only under Retrieved context)."""
    payload = deepcopy(state.to_dict())
    overview = payload.get("trip_overview") or {}
    prog = payload.get("progress") or {}
    chunks = prog.get("retrieved_chunks") or []
    prog["retrieved_chunks"] = [
        {
            "source": c.get("source"),
            "title": c.get("title"),
            "chunk_index": c.get("chunk_index"),
            "distance": c.get("distance"),
        }
        for c in chunks
        if isinstance(c, dict)
    ]
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    interests = overview.get("interests") or []
    constraints = payload.get("constraints") or {}
    budget_total = constraints.get("budget_total_cad")
    duration = overview.get("duration_days")

    summary_parts: list[str] = []
    if destinations:
        summary_parts.append(f"Destinations: {', '.join(destinations)}.")
    elif overview.get("destination"):
        summary_parts.append(f"Destination: {overview.get('destination')}.")
    if duration:
        summary_parts.append(f"Total trip length: {duration} days.")
    if allocations:
        split = ", ".join(f"{dest}={days} days" for dest, days in allocations.items())
        summary_parts.append(f"Current destination split: {split}.")
    if budget_total:
        summary_parts.append(f"Budget total: ${budget_total} CAD.")
    if interests:
        summary_parts.append(f"Interests: {', '.join(interests)}.")

    # Keep the prompt focused on the current structured state, not stale turn history.
    if summary_parts:
        overview["request_text"] = " ".join(summary_parts)
    payload["trip_overview"] = overview
    payload["progress"] = prog
    return payload


def _weather_prompt_block(state: TripState) -> str:
    summary = (state.progress.get("weather_summary") or "").strip()
    err = (state.progress.get("weather_error") or "").strip()
    meta = state.progress.get("weather_meta") or {}
    mode = str(meta.get("mode") or "").strip()
    if summary:
        if mode == "climate_normals":
            return (
                summary
                + "\n\nTreat this as seasonal trend guidance only. "
                "Do not present it as exact day-by-day weather."
            )
        return summary
    if err and not err.startswith("skipped_"):
        return f"(Weather tool error: {err})"
    return "(No weather forecast available — add trip dates or check connectivity.)"


def _clip_context(context: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    if len(context) <= max_chars:
        return context
    return context[: max_chars - 40] + "\n\n[… retrieved context truncated for model limits …]\n"


def _destination_day_range_instructions(overview: dict[str, Any]) -> str:
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    if not destinations or not allocations:
        return ""

    ranges: list[str] = []
    day_start = 1
    for destination in destinations:
        days = allocations.get(destination)
        if not isinstance(days, int) or days <= 0:
            continue
        day_end = day_start + days - 1
        if day_start == day_end:
            ranges.append(f"Day {day_start}: {destination}")
        else:
            ranges.append(f"Days {day_start}-{day_end}: {destination}")
        day_start = day_end + 1

    if not ranges:
        return ""

    return "Use this exact destination-to-day assignment: " + "; ".join(ranges) + "."


def _expected_destination_by_day(overview: dict[str, Any]) -> dict[int, str]:
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    expected: dict[int, str] = {}
    day_number = 1
    for destination in destinations:
        days = allocations.get(destination)
        if not isinstance(days, int) or days <= 0:
            continue
        for _ in range(days):
            expected[day_number] = destination
            day_number += 1
    return expected


def _infer_destination_for_day(day: dict[str, Any], destinations: list[str]) -> str | None:
    haystack = " ".join(
        str(day.get(key) or "")
        for key in ("subtitle", "morning", "afternoon", "evening")
    ).lower()
    for destination in destinations:
        if destination.lower() in haystack:
            return destination
    return None


def _destination_split_issues(parsed: dict[str, Any], overview: dict[str, Any]) -> list[str]:
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    if len(destinations) < 2 or not allocations:
        return []

    expected = _expected_destination_by_day(overview)
    days = parsed.get("days") or []
    issues: list[str] = []

    for day in days:
        if not isinstance(day, dict):
            continue
        day_number = day.get("day_number")
        if not isinstance(day_number, int):
            continue
        expected_destination = expected.get(day_number)
        if not expected_destination:
            continue
        actual_destination = _infer_destination_for_day(day, destinations)
        if actual_destination is None:
            issues.append(
                f"Day {day_number} does not clearly identify its destination; expected {expected_destination}."
            )
            continue
        if actual_destination != expected_destination:
            issues.append(
                f"Day {day_number} is assigned to {actual_destination}, but expected {expected_destination}."
            )

    return issues


_DESTINATION_GUIDE_SOURCES = {
    "Australia": "australia_guide.md",
    "Canada": "canada_guide.md",
    "France": "france_guide.md",
    "Greece": "greece_guide.md",
    "Italy": "italy_guide.md",
    "Japan": "japan_guide.md",
    "Mexico": "mexico_guide.md",
    "Peru": "peru_guide.md",
    "Spain": "spain_guide.md",
    "Thailand": "thailand_guide.md",
    "USA": "usa_guide.md",
    "United States": "usa_guide.md",
}


def _destination_cities(overview: dict[str, Any]) -> dict[str, list[str]]:
    destinations = overview.get("destinations") or []
    cities = overview.get("cities") or []
    city_map: dict[str, list[str]] = {destination: [] for destination in destinations}
    for city in cities:
        parent = CITY_TO_DESTINATION.get(city)
        if parent in city_map:
            city_map[parent].append(city)
    return city_map


def _distribute_days(total_days: int, slot_count: int) -> list[int]:
    if total_days <= 0 or slot_count <= 0:
        return []
    base = total_days // slot_count
    remainder = total_days % slot_count
    return [base + (1 if idx < remainder else 0) for idx in range(slot_count)]


def _city_sequence_for_destination(destination: str, cities: list[str], days: int) -> list[str]:
    if days <= 0:
        return []
    if not cities:
        return [destination] * days
    if len(cities) == 1:
        return [cities[0]] * days

    allocation = _distribute_days(days, len(cities))
    sequence: list[str] = []
    for city, city_days in zip(cities, allocation):
        sequence.extend([city] * city_days)
    return sequence[:days] or [cities[0]] * days


def _destination_sources(state: TripState, destination: str, city_names: list[str]) -> list[str]:
    retrieved = state.progress.get("retrieved_chunks") or []
    picked: list[str] = []
    wanted_tokens = [destination.lower(), *(city.lower() for city in city_names)]
    for chunk in retrieved:
        source = (chunk.get("source") or "").strip()
        if not source or source in picked:
            continue
        source_l = source.lower()
        if any(token and token.replace(" ", "_") in source_l for token in wanted_tokens):
            picked.append(source)

    guide = _DESTINATION_GUIDE_SOURCES.get(destination)
    if guide and guide not in picked:
        picked.append(guide)

    if "travel_budgeting_guide.md" not in picked:
        for chunk in retrieved:
            source = (chunk.get("source") or "").strip()
            if source == "travel_budgeting_guide.md":
                picked.append(source)
                break

    return picked[:2]


def _activity_snippets(interests: list[str], city: str, destination: str) -> tuple[str, str, str]:
    lowered = {item.lower() for item in interests}
    if "food" in lowered and "nature" in lowered:
        return (
            f"Start in {city} with a local breakfast spot and an easy orientation walk to settle into {destination}.",
            f"Spend the afternoon balancing signature food stops with a scenic outdoor area in or near {city}.",
            f"Wrap up with a relaxed dinner and a low-key neighborhood stroll so the pace stays manageable.",
        )
    if "food" in lowered:
        return (
            f"Begin with a neighborhood cafe or market in {city} to get an early taste of the local food scene.",
            f"Use the afternoon for a mix of landmark sightseeing and a well-known lunch stop in {city}.",
            f"Keep the evening centered on a memorable dinner and a walk through one of {city}'s livelier districts.",
        )
    if "nature" in lowered:
        return (
            f"Ease into the day with a park, garden, or waterfront visit in {city}.",
            f"Dedicate the afternoon to a scenic viewpoint, trail, or nature-focused excursion tied to {destination}.",
            f"Spend the evening on a calmer local activity and an easy dinner near your accommodation.",
        )
    return (
        f"Use the morning to get oriented in {city} with one high-priority attraction and nearby local exploration.",
        f"Keep the afternoon flexible for a second major sight plus time to explore the surrounding district.",
        f"Finish with dinner and a relaxed evening that leaves enough energy for the next day of travel.",
    )


def _deterministic_split_itinerary(
    state: TripState,
    validation_issues: list[str],
) -> tuple[str, dict[str, Any]]:
    overview = state.trip_overview
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    duration = int(overview.get("duration_days") or 0)
    interests = overview.get("interests") or []
    budget_total = state.constraints.get("budget_total_usd")
    city_map = _destination_cities(overview)

    summary: list[str] = []
    if len(destinations) > 1 and allocations:
        split_text = ", ".join(f"{dest} for {allocations.get(dest, 0)} days" for dest in destinations)
        summary.append(f"Trip split: {split_text}.")
    if interests:
        summary.append(f"Main focus: {', '.join(interests)}.")
    if overview.get("cities"):
        summary.append(f"Planned city stops: {', '.join(overview.get('cities') or [])}.")

    daily_budget = None
    if budget_total and duration > 0:
        daily_budget = max(50, round(budget_total / duration))

    days: list[dict[str, Any]] = []
    sources_used: list[str] = []
    day_number = 1

    for destination in destinations:
        allocated_days = int(allocations.get(destination) or 0)
        if allocated_days <= 0:
            continue

        dest_cities = city_map.get(destination) or []
        city_sequence = _city_sequence_for_destination(destination, dest_cities, allocated_days)
        dest_sources = _destination_sources(state, destination, dest_cities)
        for source in dest_sources:
            if source not in sources_used:
                sources_used.append(source)

        for offset in range(allocated_days):
            city = city_sequence[offset] if offset < len(city_sequence) else destination
            subtitle = f"{city}, {destination}" if city != destination else destination
            morning, afternoon, evening = _activity_snippets(interests, city, destination)

            prev_city = city_sequence[offset - 1] if offset > 0 and offset - 1 < len(city_sequence) else None
            next_destination = None
            current_index = destinations.index(destination)
            if offset == allocated_days - 1 and current_index + 1 < len(destinations):
                next_destination = destinations[current_index + 1]

            if prev_city and prev_city != city:
                morning = f"Travel from {prev_city} to {city} early, then settle in and reset your pace for the new base."
            if next_destination:
                evening = (
                    f"Use the evening to prepare for the transfer to {next_destination} on the next day and keep plans light."
                )

            citations = dest_sources or [_DESTINATION_GUIDE_SOURCES.get(destination, "travel_budgeting_guide.md")]
            days.append(
                {
                    "day_number": day_number,
                    "subtitle": subtitle,
                    "morning": f"{morning} (Source: {citations[0]})",
                    "afternoon": f"{afternoon} (Source: {citations[0]})",
                    "evening": f"{evening} (Source: {citations[min(1, len(citations) - 1)]})",
                    "estimated_costs_usd": str(daily_budget) if daily_budget else None,
                    "citations": citations,
                }
            )
            day_number += 1

    structured = {
        "trip_title": " and ".join(destinations) + " Adventure" if destinations else "Trip itinerary",
        "trip_summary_bullets": summary,
        "planning_notes": validation_issues or None,
        "days": days,
        "budget_overview_bullets": [
            f"Total budget: ${budget_total} USD." if budget_total else "Budget not specified.",
            f"Average daily budget: about ${daily_budget} USD." if daily_budget else "Daily budget depends on final bookings.",
        ],
        "sources_used": sources_used,
    }
    return itinerary_json_to_markdown(structured), structured


def _json_correction_prompt(
    state: TripState,
    context: str,
    validation_issues: list[str],
    bad_json: dict[str, Any],
    split_issues: list[str],
) -> str:
    base = _json_generation_prompt(state, context, validation_issues)
    return (
        base
        + "\n\nYour previous JSON violated the required destination day split.\n"
        + "Fix it and return a single corrected JSON object only.\n"
        + f"Split violations: {json.dumps(split_issues)}\n"
        + f"Previous invalid JSON: {json.dumps(bad_json, ensure_ascii=True)}"
    )


def _json_generation_prompt(state: TripState, context: str, validation_issues: list[str]) -> str:
    overview = state.trip_overview
    duration = overview.get("duration_days")
    destinations = overview.get("destinations") or []
    allocations = overview.get("destination_day_allocations") or {}
    dest = ", ".join(destinations) or overview.get("destination") or "unknown destination"
    allocation_ranges = _destination_day_range_instructions(overview)

    day_hint = ""
    if isinstance(duration, int) and duration > 0:
        day_hint = f'Include exactly {duration} objects in the "days" array (Day 1 … Day {duration}).'
    else:
        day_hint = 'Include at least 2 objects in "days" unless the trip is clearly a single day.'

    return f"""
You are a travel planning API. Output a single JSON object only — no markdown, no prose before or after.

Schema (all keys required unless noted nullable):
{{
  "trip_title": string,
  "trip_summary_bullets": string[],
  "planning_notes": string[] | null,
  "days": [
    {{
      "day_number": number,
      "subtitle": string,
      "morning": string,
      "afternoon": string,
      "evening": string,
      "estimated_costs_cad": string | null,
      "citations": string[]
    }}
  ],
  "budget_overview_bullets": string[],
  "sources_used": string[]
}}

Rules:
- Ground factual claims in the retrieved context below. Each day should include inline citations in the text using (Source: filename.md) matching the context blocks.
- For **sources_used**, per-day **citations**, and every **(Source: …)** tag, use **exactly** the corpus filenames as they appear in the Retrieved context (e.g. `thailand_guide.md`, `travel_budgeting_guide.md`). Do not invent, rename, or substitute different filenames; do not turn titles into new file names.
- {day_hint}
- "citations" per day lists filenames or titles referenced that day.
- "sources_used" is the deduplicated list of all corpus sources cited.
- Express all budget and estimated cost amounts in CAD.
- If validation issues exist, put them in planning_notes or trip_summary; use this list: {json.dumps(validation_issues)}.
- Destination focus: {dest}
- If multiple destinations are requested, distribute the itinerary across them in a sensible order and make the day subtitles/location choices clearly reflect those stops.
- Honor this destination day split when present: {json.dumps(allocations)}.
- The destination day split is authoritative. If it says Japan=7 and Canada=3, the itinerary must assign exactly 7 days to Japan and exactly 3 days to Canada.
- Do not reuse or infer an older split from prior conversation turns when a newer split is present in TripState.
- In trip_summary_bullets, explicitly mention the destination split when multiple destinations are present.
- {allocation_ranges}
- Each day subtitle should include the active destination for that day.
- Do not place travel-to-the-next-country days before the final allocated day for the current destination unless the split explicitly leaves room for that transfer.
- Use the **External tool — weather** section below for same-day outdoor vs indoor balance and packing hints. Do not invent temperatures or precipitation; only use what appears there.

External tool — weather (Open-Meteo forecast; not from the markdown corpus):
{_weather_prompt_block(state)}

TripState (metadata only — full doc text is under Retrieved context):
{json.dumps(_state_for_llm_prompt(state), indent=2)}

Retrieved context:
{context}
""".strip()


def _markdown_fallback_prompt(state: TripState, context: str, validation_issues: list[str]) -> str:
    """If JSON parsing fails, one freeform Markdown generation (same spirit as before)."""
    return f"""
You are an expert travel planner. Produce a single Markdown document.

Use TripState and retrieved context. Cite sources as (Source: filename.md) using **exact** filenames from the Retrieved context blocks only — never invent file names.
Express all budget and estimated cost amounts in CAD.

If multiple destinations are requested and TripState includes `destination_day_allocations`, that split is authoritative and must be followed exactly. Do not keep an older split from earlier turns.

External tool — weather:
{_weather_prompt_block(state)}

TripState (metadata only):
{json.dumps(_state_for_llm_prompt(state), indent=2)}

Validation issues:
{json.dumps(validation_issues)}

Retrieved context:
{context}

Structure with: # title, ## Trip summary, ## Day-by-day plan with ### Day N and **Morning**/**Afternoon**/**Evening**, ## Budget overview, ## Sources used.
""".strip()


def _resolve_model(explicit: str | None) -> str:
    return (explicit or os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()


def _chat_message(
    client: Any,
    model: str,
    prompt: str,
    *,
    json_mode: bool,
) -> tuple[str, str | None]:
    """Returns (assistant_text, error_message_if_failed)."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        r = client.chat.completions.create(**kwargs)
        text = (r.choices[0].message.content or "").strip()
        return text, None
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("OpenAI chat.completions failed (%s): %s", model, err)
        if json_mode:
            return _chat_message(client, model, prompt, json_mode=False)
        return "", err


def generate_itinerary(
    state: TripState,
    model: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """
    Generate a structured itinerary: JSON via Chat Completions → canonical Markdown.

    Returns
    -------
    (markdown_for_user, parsed_json_or_none)
    """
    state.progress["itinerary_llm_error"] = ""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        state.progress["itinerary_llm_error"] = "OPENAI_API_KEY is not set"
        return "", None

    try:
        from openai import OpenAI
    except ImportError:
        state.progress["itinerary_llm_error"] = "openai package not installed"
        return "", None

    resolved = _resolve_model(model)
    context = _clip_context(
        format_retrieved_context(state.progress.get("retrieved_chunks", []))
    )
    validation_issues = list(state.progress.get("validation_issues") or [])

    client = OpenAI(api_key=api_key)

    json_prompt = _json_generation_prompt(state, context, validation_issues)
    json_prompt = (
        json_prompt
        + "\n\nRespond with a single valid JSON object only (no markdown fences, no commentary)."
    )
    raw, err = _chat_message(client, resolved, json_prompt, json_mode=True)

    parsed = _extract_json_object(raw) if raw else None
    if parsed:
        split_issues = _destination_split_issues(parsed, state.trip_overview)
        if split_issues:
            correction_prompt = _json_correction_prompt(
                state,
                context,
                validation_issues,
                parsed,
                split_issues,
            )
            corrected_raw, corrected_err = _chat_message(
                client,
                resolved,
                correction_prompt,
                json_mode=True,
            )
            corrected = _extract_json_object(corrected_raw) if corrected_raw else None
            if corrected:
                corrected_issues = _destination_split_issues(corrected, state.trip_overview)
                if not corrected_issues:
                    md = itinerary_json_to_markdown(corrected)
                    if md.strip():
                        return md, corrected
            state.progress["itinerary_llm_error"] = (
                "Generated itinerary did not follow the required destination day split; using deterministic split fallback. "
                + " | ".join(split_issues)
            )[:2000]
            return _deterministic_split_itinerary(state, validation_issues)
        md = itinerary_json_to_markdown(parsed)
        if md.strip():
            return md, parsed

    fb_prompt = _markdown_fallback_prompt(state, context, validation_issues)
    md, err_fb = _chat_message(client, resolved, fb_prompt, json_mode=False)
    if md:
        return md, None

    if len(state.trip_overview.get("destinations") or []) > 1 and state.trip_overview.get("destination_day_allocations"):
        state.progress["itinerary_llm_error"] = (
            (err_fb or err or "Empty model response")
            + " | Using deterministic split fallback."
        )[:2000]
        return _deterministic_split_itinerary(state, validation_issues)

    state.progress["itinerary_llm_error"] = (err_fb or err or "Empty model response")[:2000]
    return "", None
