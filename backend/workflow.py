"""Workflow controller for trip planning state transitions."""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_backend_dir = Path(__file__).resolve().parent
_repo_root = _backend_dir.parent
_env_kw = {"override": True, "encoding": "utf-8-sig"}
load_dotenv(_repo_root / ".env", **_env_kw)
load_dotenv(_backend_dir / ".env", **_env_kw)

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

try:
    from .rag import format_retrieved_context, retrieve as rag_retrieve
    from .state import TripState, ensure_trip_state
except ImportError:
    from rag import format_retrieved_context, retrieve as rag_retrieve
    from state import TripState, ensure_trip_state

MONTH_NAMES = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}

EXPENSIVE_DESTINATIONS = {
    "japan",
    "usa",
    "united states",
    "france",
    "canada",
    "australia",
    "italy",
}

INTEREST_KEYWORDS = [
    "beach",
    "food",
    "history",
    "culture",
    "nightlife",
    "hiking",
    "nature",
    "museum",
    "temple", 
    "shopping",
    "adventure",
    "relaxation",
]

DESTINATION_CANONICAL = {
    "australia": "Australia",
    "canada": "Canada",
    "france": "France",
    "greece": "Greece",
    "italy": "Italy",
    "japan": "Japan",
    "mexico": "Mexico",
    "peru": "Peru",
    "spain": "Spain",
    "thailand": "Thailand",
    "usa": "USA",
    "united states": "United States",
    "europe": "Europe",
    "asia": "Asia",
    "tokyo": "Tokyo",
    "kyoto": "Kyoto",
    "osaka": "Osaka",
    "paris": "Paris",
    "rome": "Rome",
}


def _normalized(text: str) -> str:
    return " ".join(text.strip().split())


def _conversation_combined(prev_acc: str, prev_req: str, text: str) -> str:
    """Join prior accumulated text, prior request line, and this turn's message without losing segments."""
    parts: list[str] = []
    for segment in ((prev_acc or "").strip(), (prev_req or "").strip(), (text or "").strip()):
        if not segment:
            continue
        if segment not in parts:
            parts.append(segment)
    return _normalized(" ".join(parts))


def _is_calendar_month_name(name: str) -> bool:
    """True if *name* is only a month (e.g. 'April'), not a place like 'Juneau'."""
    token = name.strip().rstrip(".,!?").lower()
    return bool(token) and token in MONTH_NAMES


def _extract_destination(text: str) -> str | None:
    lowered = text.lower()
    for raw in sorted(DESTINATION_CANONICAL, key=len, reverse=True):
        if re.search(rf"\b{re.escape(raw)}\b", lowered):
            return DESTINATION_CANONICAL[raw]

    patterns = [
        r"(?:to|visit|in|for)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)",
        r"destination\s*[:\-]\s*([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            first_word = candidate.split()[0]
            if _is_calendar_month_name(first_word):
                continue
            return candidate
    return None


def _extract_destinations(text: str) -> list[str]:
    lowered = text.lower()
    matches: list[tuple[int, str]] = []

    for raw in sorted(DESTINATION_CANONICAL, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(raw)}\b", lowered):
            matches.append((match.start(), DESTINATION_CANONICAL[raw]))

    if not matches:
        single = _extract_destination(text)
        return [single] if single else []

    seen: set[str] = set()
    ordered: list[str] = []
    for _, canonical in sorted(matches, key=lambda item: item[0]):
        if canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(canonical)
    return ordered


def _extract_duration_days(text: str) -> int | None:
    match = re.search(r"\b(\d+)\s*(day|days|week|weeks|month|months)\b", text, re.IGNORECASE)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("day"):
        return value
    if unit.startswith("week"):
        return value * 7
    if unit.startswith("month"):
        return value * 30
    return None


def _mentions_total_trip_duration(text: str) -> bool:
    return bool(
        re.search(
            r"(?:whole trip|entire trip|total trip|trip duration|overall trip|for the trip)\D{0,15}\d+\s*(?:day|days|week|weeks|month|months)\b",
            text,
            re.IGNORECASE,
        )
        or re.search(
            r"\btrip\D{0,12}(?:is|to|at|for)\D{0,10}\d+\s*(?:day|days|week|weeks|month|months)\b",
            text,
            re.IGNORECASE,
        )
    )


def _looks_like_destination_split_only(text: str, destinations: list[str]) -> bool:
    if not text or len(destinations) < 2:
        return False

    lowered = text.lower()
    if _mentions_total_trip_duration(text):
        return False
    if any(token in lowered for token in ("total trip", "trip duration", "overall trip", "entire trip")):
        return False

    mentioned = 0
    for destination in destinations:
        escaped = re.escape(destination.lower())
        if re.search(rf"\b{escaped}\b", lowered):
            mentioned += 1

    if mentioned < 2:
        return False

    duration_mentions = re.findall(r"\b\d+\s*(?:day|days|week|weeks|month|months)\b", lowered)
    return len(duration_mentions) >= 2


def _duration_to_days(value: int, unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("week"):
        return value * 7
    if unit.startswith("month"):
        return value * 30
    return value


def _extract_destination_day_allocations(text: str, destinations: list[str]) -> dict[str, int]:
    if not text or not destinations:
        return {}

    allocations: dict[str, int] = {}
    for destination in destinations:
        escaped = re.escape(destination)
        patterns = [
            rf"(?:stay in|time in|visit|in)\s+\b{escaped}\b\s+(?:for|at|around)\s+(\d+)\s*(day|days|week|weeks|month|months)\b",
            rf"\b{escaped}\b\s+(?:for|at|around)\s+(\d+)\s*(day|days|week|weeks|month|months)\b",
            rf"(\d+)\s*(day|days|week|weeks|month|months)\s+(?:in\s+)?\b{escaped}\b",
            rf"\b{escaped}\b\s+(\d+)\s*(day|days|week|weeks|month|months)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                allocations[destination] = _duration_to_days(int(match.group(1)), match.group(2))
                break
    return allocations


def _extract_budget_total(text: str) -> int | None:
    explicit_update_patterns = [
        r"(?:budget|total budget|new budget)\D{0,20}(?:is|to|at|of)\D{0,10}\$?\s*(\d{1,3}(?:,\d{3})+|\d{2,6})\b",
        r"(?:change|make|set|update|raise|lower)\D{0,20}(?:budget|it)\D{0,20}(?:to|at)\D{0,10}\$?\s*(\d{1,3}(?:,\d{3})+|\d{2,6})\b",
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:CAD|C\$)\s*(?:budget|total)?\b",
    ]
    for pattern in explicit_update_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))

    range_match = re.search(r"\$?(\d{2,6})\s*(?:-|to)\s*\$?(\d{2,6})", text, re.IGNORECASE)
    if range_match:
        return int(range_match.group(2))

    budget_match = re.search(
        r"(?:budget|under|max(?:imum)?|spend|up to)\D{0,15}\$?\s*(\d{2,6})",
        text,
        re.IGNORECASE,
    )
    if budget_match:
        return int(budget_match.group(1))

    standalone = re.search(r"\$\s*(\d{2,6})", text)
    if standalone:
        return int(standalone.group(1))

    currency_amount = re.search(
        r"\b(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:CAD|C\$)\b",
        text,
        re.IGNORECASE,
    )
    if currency_amount:
        return int(currency_amount.group(1).replace(",", ""))

    trip_budget = re.search(
        r"(?:trip|total|budget)\D{0,12}(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:CAD|C\$)\b",
        text,
        re.IGNORECASE,
    )
    if trip_budget:
        return int(trip_budget.group(1).replace(",", ""))
    return None


def _extract_group_size(text: str) -> int | None:
    lowered = text.lower()
    if any(token in lowered for token in ("solo", "alone", "by myself")):
        return 1

    patterns = [
        r"family of (\d+)",
        r"group of (\d+)",
        r"(\d+)\s*(?:people|persons|travelers|travellers|adults|kids)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    return None


def _extract_month(text: str) -> str | None:
    lowered = text.lower()
    for month in MONTH_NAMES:
        if month in lowered:
            return month
    return None


def _extract_dates(text: str) -> tuple[str | None, str | None]:
    matches = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if len(matches) >= 2:
        return matches[0], matches[1]
    if len(matches) == 1:
        return matches[0], None
    return None, None


def _extract_interests(text: str) -> list[str]:
    lowered = text.lower()
    hits = [keyword for keyword in INTEREST_KEYWORDS if keyword in lowered]
    return sorted(set(hits))


def _coalesce_preferred(*values):
    """Return the first non-empty parsed value."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set)) and not value:
            continue
        return value
    return None


def _get_budget_total(constraints: dict[str, Any]) -> int | float | None:
    return constraints.get("budget_total_cad")


def _get_budget_per_day(constraints: dict[str, Any]) -> int | float | None:
    return constraints.get("budget_per_day_cad")


def _set_budget_total(constraints: dict[str, Any], value: int | float) -> None:
    constraints["budget_total_cad"] = value


def _set_budget_per_day(constraints: dict[str, Any], value: int | float) -> None:
    constraints["budget_per_day_cad"] = value


def intake(user_input: str, state: TripState | dict[str, Any] | None = None) -> TripState:
    """Parse free-form user input into state fields."""
    trip_state = ensure_trip_state(state)
    trip_state.set_stage("intake")

    text = _normalized(user_input)
    prev_acc = (trip_state.progress.get("accumulated_context") or "").strip()
    prev_req = (trip_state.trip_overview.get("request_text") or "").strip()
    combined = _conversation_combined(prev_acc, prev_req, text)

    trip_state.progress["accumulated_context"] = combined[:15000]
    trip_state.progress["raw_user_input"] = text
    #full conversation for API round-trips (not just this turn's line).
    trip_state.trip_overview["request_text"] = combined

    latest_start_date, latest_end_date = _extract_dates(text)
    combined_start_date, combined_end_date = _extract_dates(combined)

    latest_destinations = _extract_destinations(text)
    combined_destinations = _extract_destinations(combined)
    destinations = _coalesce_preferred(
        latest_destinations,
        trip_state.trip_overview.get("destinations"),
        combined_destinations,
    )
    if destinations is not None:
        trip_state.trip_overview["destinations"] = list(destinations)
        existing_allocations = dict(trip_state.trip_overview.get("destination_day_allocations") or {})
        trip_state.trip_overview["destination_day_allocations"] = {
            key: val for key, val in existing_allocations.items() if key in trip_state.trip_overview["destinations"]
        }
    destination = _coalesce_preferred(
        (trip_state.trip_overview.get("destinations") or [None])[0],
        _extract_destination(text),
        trip_state.trip_overview.get("destination"),
        _extract_destination(combined),
    )
    if destination:
        trip_state.trip_overview["destination"] = destination
    elif trip_state.trip_overview.get("destinations"):
        trip_state.trip_overview["destination"] = trip_state.trip_overview["destinations"][0]

    current_destinations = trip_state.trip_overview.get("destinations") or []
    latest_allocations = _extract_destination_day_allocations(text, current_destinations)
    latest_duration_days = _extract_duration_days(text)
    split_only_reply = bool(
        latest_allocations
        and len(current_destinations) > 1
        and (
            not _mentions_total_trip_duration(text)
            or _looks_like_destination_split_only(text, current_destinations)
        )
    )
    if split_only_reply:
        latest_duration_days = None

    combined_duration_days = None if split_only_reply else _extract_duration_days(combined)

    duration_days = _coalesce_preferred(
        latest_duration_days,
        trip_state.trip_overview.get("duration_days"),
        combined_duration_days,
    )
    if duration_days is None and latest_allocations:
        duration_days = sum(int(days) for days in latest_allocations.values())
    if duration_days is not None:
        trip_state.trip_overview["duration_days"] = duration_days

    if latest_allocations:
        merged_allocations = dict(trip_state.trip_overview.get("destination_day_allocations") or {})
        merged_allocations.update(latest_allocations)
        trip_state.trip_overview["destination_day_allocations"] = merged_allocations

    start_month = _coalesce_preferred(
        _extract_month(text),
        trip_state.trip_overview.get("start_month"),
        _extract_month(combined),
    )
    if start_month:
        trip_state.trip_overview["start_month"] = start_month

    start_date = _coalesce_preferred(
        latest_start_date,
        trip_state.trip_overview.get("start_date"),
        combined_start_date,
    )
    if start_date:
        trip_state.trip_overview["start_date"] = start_date

    # End date is derived when start date + duration are available.
    end_date: str | None = None
    if latest_end_date:
        end_date = latest_end_date
    elif start_date and trip_state.trip_overview.get("duration_days"):
        try:
            start_dt = date.fromisoformat(start_date)
            trip_days = int(trip_state.trip_overview["duration_days"])
            if trip_days >= 1:
                end_date = (start_dt + timedelta(days=trip_days - 1)).isoformat()
        except (ValueError, TypeError):
            end_date = None
    elif trip_state.trip_overview.get("end_date"):
        end_date = trip_state.trip_overview.get("end_date")
    elif combined_end_date:
        end_date = combined_end_date

    if end_date:
        trip_state.trip_overview["end_date"] = end_date

    latest_interests = _extract_interests(text)
    combined_interests = _extract_interests(combined)
    interests = _coalesce_preferred(
        latest_interests,
        trip_state.trip_overview.get("interests"),
        combined_interests,
    )
    if interests is not None:
        trip_state.trip_overview["interests"] = list(interests)

    budget_total = _coalesce_preferred(
        _extract_budget_total(text),
        _get_budget_total(trip_state.constraints),
        _extract_budget_total(combined),
    )
    if budget_total:
        _set_budget_total(trip_state.constraints, budget_total)

    if trip_state.trip_overview.get("duration_days") and _get_budget_total(trip_state.constraints):
        duration_days = max(1, int(trip_state.trip_overview["duration_days"]))
        _set_budget_per_day(
            trip_state.constraints,
            round(
                float(_get_budget_total(trip_state.constraints)) / duration_days,
                2,
            ),
        )

    group_size = _coalesce_preferred(
        _extract_group_size(text),
        trip_state.user_profile.get("group_size"),
        _extract_group_size(combined),
    )
    if group_size:
        trip_state.user_profile["group_size"] = group_size

    lowered = text.lower()
    if not any(token in lowered for token in ("family", "couple", "friends", "group", "solo", "alone", "by myself")):
        lowered = combined.lower()
    if "family" in lowered:
        trip_state.user_profile["traveler_type"] = "family"
    elif "couple" in lowered:
        trip_state.user_profile["traveler_type"] = "couple"
    elif "friends" in lowered or "group" in lowered:
        trip_state.user_profile["traveler_type"] = "friends"
    elif group_size == 1:
        trip_state.user_profile["traveler_type"] = "solo"

    return trip_state


def _local_clarifying_questions(state: TripState) -> list[str]:
    questions: list[str] = []
    if not (state.trip_overview.get("destination") or state.trip_overview.get("destinations")):
        questions.append("Which country or city do you want to visit? You can include multiple destinations.")
    if not state.trip_overview.get("duration_days"):
        questions.append("How many days is your trip?")
    destinations = state.trip_overview.get("destinations") or []
    allocations = state.trip_overview.get("destination_day_allocations") or {}
    if len(destinations) > 1 and any(dest not in allocations for dest in destinations):
        total = state.trip_overview.get("duration_days")
        if total:
            questions.append(
                f"How would you like to split your {total}-day trip across {', '.join(destinations)}?"
            )
        else:
            questions.append(f"How many days do you want to spend in each destination: {', '.join(destinations)}?")
    if not _get_budget_total(state.constraints):
        questions.append("What is your total budget in CAD?")
    if not state.trip_overview.get("interests"):
        questions.append("What are your top interests (food, beaches, museums, nightlife, hiking)?")
    return questions[:3]


def _openai_clarifying_questions(state: TripState, model: str) -> list[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    missing_fields = []
    if not (state.trip_overview.get("destination") or state.trip_overview.get("destinations")):
        missing_fields.append("destination")
    if not state.trip_overview.get("duration_days"):
        missing_fields.append("duration")
    destinations = state.trip_overview.get("destinations") or []
    allocations = state.trip_overview.get("destination_day_allocations") or {}
    if len(destinations) > 1 and any(dest not in allocations for dest in destinations):
        missing_fields.append("days per destination")
    if not _get_budget_total(state.constraints):
        missing_fields.append("budget")
    if not state.trip_overview.get("interests"):
        missing_fields.append("interests")

    if not missing_fields:
        return []

    prompt = f"""
You are a travel planner assistant.
Given this user request, ask up to 3 short follow-up questions to fill missing details.
Missing fields: {", ".join(missing_fields)}
Return only a numbered list of questions.

User request:
{state.trip_overview.get("request_text", "")}
""".strip()

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        return []

    if not raw:
        return []

    lines = []
    for line in raw.splitlines():
        cleaned = re.sub(r"^\s*\d+[\).\s-]*", "", line).strip()
        if cleaned:
            if not cleaned.endswith("?"):
                cleaned = f"{cleaned}?"
            lines.append(cleaned)
    return lines[:3]


def clarify(state: TripState | dict[str, Any], model: str = DEFAULT_OPENAI_MODEL) -> TripState:
    """Generate follow-up questions for missing planning details."""
    trip_state = ensure_trip_state(state)
    trip_state.set_stage("clarify")

    questions = _openai_clarifying_questions(trip_state, model=model)
    if not questions:
        questions = _local_clarifying_questions(trip_state)

    trip_state.progress["clarifying_questions"] = questions
    return trip_state


def retrieve(state: TripState | dict[str, Any], top_k: int = 3) -> TripState:
    """Retrieve semantically relevant corpus chunks using RAG."""
    trip_state = ensure_trip_state(state)
    trip_state.set_stage("retrieve")

    query_parts = [
        " ".join(trip_state.trip_overview.get("destinations") or [])
        or trip_state.trip_overview.get("destination"),
        "budget travel" if _get_budget_total(trip_state.constraints) else None,
        " ".join(trip_state.trip_overview.get("interests", []))
        if trip_state.trip_overview.get("interests")
        else None,
        trip_state.progress.get("raw_user_input") or trip_state.trip_overview.get("request_text"),
    ]
    query = " ".join(str(part).strip() for part in query_parts if part).strip()
    if not query:
        query = "general international travel tips"

    try:
        results = rag_retrieve(query=query, top_k=top_k)
        trip_state.progress["retrieval_error"] = ""
    except Exception as exc:
        results = []
        trip_state.progress["retrieval_error"] = str(exc)

    trip_state.progress["retrieval_query"] = query
    trip_state.progress["retrieved_chunks"] = results
    return trip_state


def validate(state: TripState | dict[str, Any]) -> TripState:
    """Check high-level budget/timing conflicts."""
    trip_state = ensure_trip_state(state)
    trip_state.set_stage("validate")

    issues: list[str] = []

    destinations = trip_state.trip_overview.get("destinations") or []
    destination_text = " ".join(destinations) or (trip_state.trip_overview.get("destination") or "")
    destination = destination_text.lower()
    duration_days = trip_state.trip_overview.get("duration_days")
    allocations = trip_state.trip_overview.get("destination_day_allocations") or {}
    budget_total = _get_budget_total(trip_state.constraints)
    budget_per_day = _get_budget_per_day(trip_state.constraints)
    start_date = trip_state.trip_overview.get("start_date")
    end_date = trip_state.trip_overview.get("end_date")

    if duration_days is not None and duration_days <= 0:
        issues.append("Trip duration must be at least 1 day.")

    if start_date and end_date:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
            if end < start:
                issues.append("End date is earlier than start date.")
            elif (end - start).days + 1 <= 0:
                issues.append("Date range is invalid.")
        except ValueError:
            issues.append("Date format should be YYYY-MM-DD.")

    if duration_days and budget_total and budget_total < max(200, duration_days * 20):
        issues.append("Total budget is likely too low for the requested trip length.")

    if len(destinations) > 1:
        missing_allocation_dests = [dest for dest in destinations if dest not in allocations]
        if missing_allocation_dests:
            issues.append(
                f"Please specify how many days to spend in each destination: {', '.join(missing_allocation_dests)}."
            )
        elif duration_days:
            allocated_total = sum(int(v) for v in allocations.values())
            if allocated_total != int(duration_days):
                issues.append(
                    f"Destination day split totals {allocated_total} days, but the trip length is {duration_days} days."
                )

    if any(tag in destination for tag in EXPENSIVE_DESTINATIONS) and budget_per_day is not None and budget_per_day < 60:
        expensive_hits = [d for d in destinations if d.lower() in EXPENSIVE_DESTINATIONS]
        if expensive_hits:
            issues.append(f"{', '.join(expensive_hits)} usually need a higher daily budget.")
        else:
            issues.append(f"{trip_state.trip_overview.get('destination')} usually needs a higher daily budget.")

    trip_state.progress["validation_issues"] = issues
    trip_state.progress["is_valid"] = len(issues) == 0
    return trip_state


def _local_generate(state: TripState) -> str:
    destinations = state.trip_overview.get("destinations") or []
    destination = ", ".join(destinations) or state.trip_overview.get("destination") or "your selected destination"
    duration = state.trip_overview.get("duration_days")
    allocations = state.trip_overview.get("destination_day_allocations") or {}
    budget_total = _get_budget_total(state.constraints)
    interests = ", ".join(state.trip_overview.get("interests", [])) or "general sightseeing"
    context = format_retrieved_context(state.progress.get("retrieved_chunks", []))
    issues = state.progress.get("validation_issues", [])
    retrieval_error = state.progress.get("retrieval_error")

    lines = [
        f"Trip recommendation for {destination}:",
        f"- Suggested focus: {interests}.",
    ]
    if duration:
        lines.append(f"- Suggested trip length: {duration} days.")
    if allocations:
        split = ", ".join(f"{dest}: {days} days" for dest, days in allocations.items())
        lines.append(f"- Destination split: {split}.")
    if budget_total:
        lines.append(f"- Budget target: about ${budget_total} CAD total.")
    if issues:
        lines.append("- Validation notes: " + " | ".join(issues))
    if retrieval_error:
        lines.append(f"- Retrieval note: context lookup failed ({retrieval_error}).")
    lines.append("")
    lines.append("Retrieved context:")
    lines.append(context)
    return "\n".join(lines)


def _openai_generate(state: TripState, model: str) -> str:
    """Delegates to itinerary.generate_itinerary (JSON → Markdown + optional structured dict)."""
    try:
        from .itinerary import generate_itinerary
    except ImportError:
        from itinerary import generate_itinerary

    markdown, structured = generate_itinerary(state, model=model)
    state.progress["itinerary_structured"] = structured
    return markdown


def generate(state: TripState | dict[str, Any], model: str = DEFAULT_OPENAI_MODEL) -> TripState:
    """Create a final recommendation using retrieved context and validated state."""
    trip_state = ensure_trip_state(state)
    trip_state.set_stage("generate")

    recommendation = _openai_generate(trip_state, model=model)
    if not recommendation:
        recommendation = _local_generate(trip_state)

    trip_state.progress["final_recommendation"] = recommendation
    return trip_state


def _core_trip_fields_complete(state: TripState) -> bool:
    """Minimum fields needed before generating an itinerary (matches clarify missing-field logic)."""
    if not (state.trip_overview.get("destination") or state.trip_overview.get("destinations")):
        return False
    duration = state.trip_overview.get("duration_days")
    if duration is None or (isinstance(duration, int) and duration < 1):
        return False
    destinations = state.trip_overview.get("destinations") or []
    allocations = state.trip_overview.get("destination_day_allocations") or {}
    if len(destinations) > 1:
        if any(dest not in allocations for dest in destinations):
            return False
        if sum(int(v) for v in allocations.values()) != int(duration):
            return False
    if not _get_budget_total(state.constraints):
        return False
    if not state.trip_overview.get("interests"):
        return False
    return True


def _clarification_hold_message(state: TripState) -> str:
    """Shown when we skip generation until the user sends another message with missing details."""
    qs = state.progress.get("clarifying_questions") or []
    lines = [
        "## More information needed",
        "",
        "Reply in your **next message** with answers (your trip details are kept in `state`).",
        "",
    ]
    if qs:
        for i, q in enumerate(qs, 1):
            lines.append(f"{i}. {q}")
    else:
        lines.append(
            "Please add: destination, trip length (days), total budget (CAD), interests, and if you have multiple destinations, how many days in each "
            "(e.g. food, temples, beaches)."
        )
    lines.extend(
        [
            "",
            "*No full itinerary has been generated yet. After you answer, send another request with the same "
            "`state` from this response so we can build the plan.*",
        ]
    )
    return "\n".join(lines)


def run_workflow(
    user_input: str,
    state: TripState | dict[str, Any] | None = None,
    top_k: int = 3,
    model: str = DEFAULT_OPENAI_MODEL,
) -> TripState:
    """Run full workflow: intake -> clarify -> retrieve -> validate -> generate (if details complete)."""
    trip_state = intake(user_input, state=state)
    trip_state = clarify(trip_state, model=model)
    trip_state = retrieve(trip_state, top_k=top_k)
    trip_state = validate(trip_state)

    trip_state.progress["awaiting_clarification"] = False
    if not _core_trip_fields_complete(trip_state):
        trip_state.progress["awaiting_clarification"] = True
        trip_state.progress["final_recommendation"] = _clarification_hold_message(trip_state)
        trip_state.progress["itinerary_structured"] = None
        trip_state.progress["itinerary_llm_error"] = ""
        trip_state.set_stage("clarify")
        return trip_state

    trip_state = generate(trip_state, model=model)
    return trip_state


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the travel workflow pipeline")
    parser.add_argument("query", nargs="+", help="User trip request")
    parser.add_argument("--top-k", type=int, default=3, help="Number of retrieval chunks")
    parser.add_argument(
        "--model",
        default=DEFAULT_OPENAI_MODEL,
        help="OpenAI model for clarify/generate (or set OPENAI_MODEL env)",
    )
    args = parser.parse_args()

    request = " ".join(args.query)
    final_state = run_workflow(request, top_k=args.top_k, model=args.model)
    print(json.dumps(final_state.to_dict(), indent=2))
