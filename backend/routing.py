"""OpenRouteService integration for travel-time checks between destinations."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib import error, request

GEOCODE_API_URL = "https://api.openrouteservice.org/geocode/search"
DIRECTIONS_API_BASE = "https://api.openrouteservice.org/v2/directions"
DEFAULT_TRAVEL_MODE = os.getenv("OPENROUTESERVICE_TRAVEL_MODE", "DRIVE").strip().upper() or "DRIVE"

TRAVEL_MODE_TO_PROFILE = {
    "DRIVE": "driving-car",
    "WALK": "foot-walking",
    "BICYCLE": "cycling-regular",
}


def _format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "unknown"

    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _format_distance(meters: int | float | None) -> str:
    if meters is None:
        return "unknown"
    km = float(meters) / 1000
    if km >= 100:
        return f"{round(km):.0f} km"
    return f"{km:.1f} km"


def route_api_available() -> bool:
    return bool((os.getenv("OPENROUTESERVICE_API_KEY") or "").strip())


def _api_key() -> str:
    return (os.getenv("OPENROUTESERVICE_API_KEY") or "").strip()


def _resolve_profile(travel_mode: str | None) -> str:
    resolved_mode = (travel_mode or DEFAULT_TRAVEL_MODE or "DRIVE").strip().upper()
    return TRAVEL_MODE_TO_PROFILE.get(resolved_mode, "driving-car")


def _resolved_mode_label(travel_mode: str | None) -> str:
    return (travel_mode or DEFAULT_TRAVEL_MODE or "DRIVE").strip().upper()


def _http_json(req: request.Request, timeout: int = 15) -> dict[str, Any]:
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _geocode_place(place: str) -> tuple[list[float] | None, str | None]:
    key = _api_key()
    if not key:
        return None, "OPENROUTESERVICE_API_KEY is not set"

    query = urlencode({"api_key": key, "text": place, "size": 1})
    req = request.Request(f"{GEOCODE_API_URL}?{query}", method="GET")
    try:
        payload = _http_json(req)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {detail[:400]}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    features = payload.get("features") or []
    if not features:
        return None, f"No geocoding result for {place}"

    coordinates = (((features[0] or {}).get("geometry") or {}).get("coordinates"))
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        return None, f"Invalid geocoding result for {place}"
    return coordinates, None


def geocode_places(places: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Return simple marker payloads for places that can be geocoded."""
    markers: list[dict[str, Any]] = []
    errors: list[str] = []

    for place in places:
        coords, geocode_error = _geocode_place(place)
        if geocode_error:
            errors.append(geocode_error)
            continue
        markers.append(
            {
                "label": place,
                "lng": coords[0],
                "lat": coords[1],
            }
        )

    return markers, "; ".join(errors[:3])


def compute_route(origin: str, destination: str, travel_mode: str | None = None) -> dict[str, Any]:
    """
    Compute a route between two places using OpenRouteService geocoding + directions.
    """
    key = _api_key()
    mode_label = _resolved_mode_label(travel_mode)
    if not key:
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": "OPENROUTESERVICE_API_KEY is not set",
        }

    origin_coords, origin_error = _geocode_place(origin)
    if origin_error:
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": origin_error,
        }

    destination_coords, destination_error = _geocode_place(destination)
    if destination_error:
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": destination_error,
        }

    profile = _resolve_profile(travel_mode)
    body = {"coordinates": [origin_coords, destination_coords]}
    req = request.Request(
        f"{DIRECTIONS_API_BASE}/{profile}/json",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": key,
            "Content-Type": "application/json",
        },
    )

    try:
        payload = _http_json(req)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": f"HTTP {exc.code}: {detail[:400]}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": f"{type(exc).__name__}: {exc}",
        }

    routes = payload.get("routes") or []
    if not routes:
        return {
            "ok": False,
            "origin": origin,
            "destination": destination,
            "travel_mode": mode_label,
            "error": "No route returned",
        }

    summary = (routes[0] or {}).get("summary") or {}
    duration_seconds = summary.get("duration")
    distance_meters = summary.get("distance")
    return {
        "ok": True,
        "origin": origin,
        "destination": destination,
        "travel_mode": mode_label,
        "duration_seconds": int(duration_seconds) if duration_seconds is not None else None,
        "duration_text": _format_duration(duration_seconds),
        "distance_meters": int(distance_meters) if distance_meters is not None else None,
        "distance_text": _format_distance(distance_meters),
    }


def compute_adjacent_routes(destinations: list[str], travel_mode: str | None = None) -> list[dict[str, Any]]:
    """Compute route checks for each adjacent destination pair."""
    checks: list[dict[str, Any]] = []
    for idx in range(len(destinations) - 1):
        checks.append(compute_route(destinations[idx], destinations[idx + 1], travel_mode=travel_mode))
    return checks
