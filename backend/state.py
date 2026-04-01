"""Shared workflow state for the travel recommendation pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

WORKFLOW_STAGES = ("intake", "clarify", "retrieve", "validate", "generate")


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
        "duration_days": None,
        "start_date": None,  # YYYY-MM-DD
        "end_date": None,  # YYYY-MM-DD
        "start_month": None,  # e.g., "april"
        "interests": [],
    }


def _default_constraints() -> dict[str, Any]:
    return {
        "budget_total_usd": None,
        "budget_per_day_usd": None,
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
        "raw_user_input": "",
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
        state = cls()
        state.user_profile.update(data.get("user_profile", {}))
        state.trip_overview.update(data.get("trip_overview", {}))
        state.constraints.update(data.get("constraints", {}))
        state.progress.update(data.get("progress", {}))
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
