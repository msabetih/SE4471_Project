"""Shared workflow state for the travel recommendation pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

WORKFLOW_STAGES = ("intake", "clarify", "retrieve", "validate", "generate")


def _normalize_client_state_payload(data: dict[str, Any] | None) -> dict[str, Any]:
    """
    Accept either a flat TripState dict or a full /chat response body where the
    payload lives under `state` (common when clients forward the entire JSON response).
    """
    if not data:
        return {}
    if "user_profile" in data:
        return data
    for key in ("state", "data"):
        inner = data.get(key)
        if isinstance(inner, dict) and "user_profile" in inner:
            return inner
    return data


def _default_user_profile() -> dict[str, Any]:
    return {
        "traveler_type": None,  # e.g., solo, couple, family, friends
        "group_size": None,
        "home_airport": None,
        "preferences": [],
    }


def _default_trip_overview() -> dict[str, Any]:
    return {
        "request_text": "",
        "destination": None,
        "destinations": [],
        "destination_day_allocations": {},
        "duration_days": None,
        "start_date": None,  # YYYY-MM-DD
        "end_date": None,  # YYYY-MM-DD
        "start_month": None,  # e.g., "april"
        "interests": [],
    }


def _default_constraints() -> dict[str, Any]:
    return {
        "budget_total_cad": None,
        "budget_per_day_cad": None,
        "must_avoid": [],
        "accessibility_needs": [],
    }


def _default_progress() -> dict[str, Any]:
    return {
        "workflow_stage": "intake",
        "stage_history": [],
        "clarifying_questions": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "retrieval_error": "",
        "validation_issues": [],
        "is_valid": True,
        "final_recommendation": "",
        "itinerary_structured": None,  
        "itinerary_llm_error": "", 
        "awaiting_clarification": False,  
        "raw_user_input": "",
        "accumulated_context": "",  
    }


@dataclass
class TripState:
    """Workflow state container used across all planning steps."""

    user_profile: dict[str, Any] = field(default_factory=_default_user_profile)
    trip_overview: dict[str, Any] = field(default_factory=_default_trip_overview)
    constraints: dict[str, Any] = field(default_factory=_default_constraints)
    progress: dict[str, Any] = field(default_factory=_default_progress)

    def set_stage(self, stage: str) -> None:
        """Update current workflow stage and append to history."""
        if stage not in WORKFLOW_STAGES:
            raise ValueError(f"Unsupported workflow stage: {stage}")

        self.progress["workflow_stage"] = stage
        history = self.progress.setdefault("stage_history", [])
        if not history or history[-1] != stage:
            history.append(stage)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TripState":
        data = _normalize_client_state_payload(data)
        state = cls()

        def merge_skip_none(target: dict[str, Any], incoming: dict[str, Any] | None) -> None:
            if not incoming:
                return
            for key, val in incoming.items():
                if val is not None:
                    target[key] = val

        def merge_trip_overview(target: dict[str, Any], incoming: dict[str, Any] | None) -> None:
            """Merge API JSON into trip_overview; do not let `interests: []` wipe prior session."""
            if not incoming:
                return
            for key, val in incoming.items():
                if val is None:
                    continue
                if key == "interests" and val == []:
                    continue
                if key == "request_text" and isinstance(val, str):
                    old = target.get("request_text") or ""
                    #keep a longer prior message if the client sends only the latest line (multi-turn).
                    if (
                        len(old) > len(val) + 20
                        and any(x in old for x in ("CAD", "budget", "trip to", "days"))
                        and not any(x in val for x in ("CAD", "budget"))
                    ):
                        continue
                target[key] = val

        def merge_progress(target: dict[str, Any], incoming: dict[str, Any] | None) -> None:
            if not incoming:
                return
            for key, val in incoming.items():
                if val is None:
                    continue
                #never let the client wipe conversation history with an empty string.
                if key == "accumulated_context" and val == "":
                    continue
                target[key] = val

        merge_skip_none(state.user_profile, data.get("user_profile"))
        merge_trip_overview(state.trip_overview, data.get("trip_overview"))
        merge_skip_none(state.constraints, data.get("constraints"))
        merge_progress(state.progress, data.get("progress"))
        return state


def ensure_trip_state(state: TripState | dict[str, Any] | None) -> TripState:
    """Coerce dict/None to TripState."""
    if state is None:
        return TripState()
    if isinstance(state, TripState):
        return state
    if isinstance(state, dict):
        return TripState.from_dict(state)
    raise TypeError("state must be TripState, dict, or None")
