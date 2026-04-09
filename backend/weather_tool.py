"""
Trip weather via Open-Meteo (no API key). Geocodes destination, fetches daily forecast
for the trip window, writes human-readable summary into TripState.progress.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

# WMO Weather interpretation codes (subset)
_WMO_LABELS: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _wmo_label(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _WMO_LABELS.get(int(code), f"Code {code}")


def _http_get_json(url: str, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "SE4471-TravelPlanner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode_destination(name: str) -> dict[str, Any] | None:
    """Return first Open-Meteo geocoding hit: latitude, longitude, name, country."""
    if not (name or "").strip():
        return None
    q = urllib.parse.urlencode({"name": name.strip(), "count": 1, "language": "en", "format": "json"})
    data = _http_get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return {
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "name": r.get("name", name),
        "country": r.get("country", ""),
        "admin1": r.get("admin1", ""),
    }


def _trip_date_range(trip_overview: dict[str, Any]) -> tuple[date, date] | None:
    """Resolve (start, end) inclusive from trip_overview, or None."""
    start_s = trip_overview.get("start_date")
    end_s = trip_overview.get("end_date")
    duration = trip_overview.get("duration_days")

    if start_s:
        try:
            start = date.fromisoformat(str(start_s))
        except ValueError:
            return None
        if end_s:
            try:
                end = date.fromisoformat(str(end_s))
            except ValueError:
                return None
            if end < start:
                return None
            return start, end
        if duration is not None and int(duration) >= 1:
            end = start + timedelta(days=int(duration) - 1)
            return start, end
        return None

    return None


def _forecast_intersection(start: date, end: date) -> tuple[date, date] | None:
    """Intersect trip window with Open-Meteo ~16-day forecast horizon from today."""
    today = date.today()
    horizon_end = today + timedelta(days=15)
    a = max(start, today)
    b = min(end, horizon_end)
    if a > b:
        return None
    return a, b


def _iter_month_numbers(start: date, end: date) -> list[int]:
    """Return month numbers spanned by start..end (inclusive), deduplicated in order."""
    months: list[int] = []
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        if cursor.month not in months:
            months.append(cursor.month)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _month_name(month: int) -> str:
    names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    if month < 1 or month > 12:
        return f"Month {month}"
    return names[month]


def fetch_climate_normals_summary(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    *,
    location_label: str,
    years_back: int = 10,
) -> str:
    """
    Build seasonal guidance using Open-Meteo archive data.
    Aggregates recent historical rows by trip month.
    """
    today = date.today()
    hist_end = today - timedelta(days=1)
    start_year = max(1940, hist_end.year - max(1, years_back) + 1)
    hist_start = date(start_year, 1, 1)

    params = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "start_date": hist_start.isoformat(),
            "end_date": hist_end.isoformat(),
            "timezone": "auto",
        }
    )
    data = _http_get_json(f"https://archive-api.open-meteo.com/v1/archive?{params}")
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return f"Seasonal weather guidance unavailable for **{location_label}** (no historical rows returned)."

    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    precip_sum = daily.get("precipitation_sum") or []

    acc: dict[int, dict[str, float]] = defaultdict(
        lambda: {
            "sum_hi": 0.0,
            "sum_lo": 0.0,
            "sum_precip": 0.0,
            "wet_days": 0.0,
            "count": 0.0,
        }
    )

    for i, day_s in enumerate(times):
        try:
            d = date.fromisoformat(day_s)
        except ValueError:
            continue

        if i >= len(tmax) or i >= len(tmin):
            continue
        hi = tmax[i]
        lo = tmin[i]
        if hi is None or lo is None:
            continue

        pr = precip_sum[i] if i < len(precip_sum) and precip_sum[i] is not None else 0.0
        bucket = acc[d.month]
        bucket["sum_hi"] += float(hi)
        bucket["sum_lo"] += float(lo)
        bucket["sum_precip"] += float(pr)
        bucket["wet_days"] += 1.0 if float(pr) >= 1.0 else 0.0
        bucket["count"] += 1.0

    trip_months = _iter_month_numbers(start, end)
    lines = [
        f"Seasonal weather guidance for **{location_label}** "
        f"(trip **{start.isoformat()}** -> **{end.isoformat()}**):",
        "",
    ]

    had_any = False
    for month in trip_months:
        b = acc.get(month)
        if not b or b["count"] <= 0:
            continue
        had_any = True
        avg_hi = b["sum_hi"] / b["count"]
        avg_lo = b["sum_lo"] / b["count"]
        avg_precip_day = b["sum_precip"] / b["count"]
        wet_pct = (b["wet_days"] / b["count"]) * 100.0
        lines.append(
            f"- **{_month_name(month)}**: typical high ~{avg_hi:.1f} C, low ~{avg_lo:.1f} C; "
            f"avg precip ~{avg_precip_day:.1f} mm/day; wet-day frequency ~{wet_pct:.0f}%."
        )

    if not had_any:
        return (
            f"Seasonal weather guidance unavailable for **{location_label}** "
            "(insufficient historical data for trip months)."
        )

    lines.append("")
    lines.append(
        "This is historical seasonal guidance (not a day-by-day forecast). "
        "Use it for packing and indoor/outdoor balance."
    )
    return "\n".join(lines)


def fetch_forecast_summary(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    *,
    location_label: str,
) -> str:
    """Build a short bullet summary for the LLM (daily max/min, precip, condition)."""
    window = _forecast_intersection(start, end)
    if window is None:
        return fetch_climate_normals_summary(
            latitude,
            longitude,
            start,
            end,
            location_label=location_label,
        )

    f_start, f_end = window
    params = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "start_date": f_start.isoformat(),
            "end_date": f_end.isoformat(),
            "timezone": "auto",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    data = _http_get_json(url)
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return "No daily forecast rows returned for this window."

    lines = [
        f"Weather tool (Open-Meteo) for **{location_label}** "
        f"(forecast slice **{f_start.isoformat()}** -> **{f_end.isoformat()}**; trip **{start.isoformat()}** -> **{end.isoformat()}**):",
        "",
    ]
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_probability_max") or []
    codes = daily.get("weathercode") or []

    for i, day in enumerate(times):
        hi = tmax[i] if i < len(tmax) else "?"
        lo = tmin[i] if i < len(tmin) else "?"
        pr = precip[i] if i < len(precip) else "?"
        code = codes[i] if i < len(codes) else None
        label = _wmo_label(int(code) if code is not None else None)
        lines.append(
            f"- **{day}**: {label}; high ~{hi} C, low ~{lo} C; max rain chance ~{pr}%."
        )

    lines.append("")
    lines.append(
        "Use this for packing and indoor/outdoor balance; it is a forecast, not a guarantee."
    )
    return "\n".join(lines)


def apply_weather_to_progress(trip_overview: dict[str, Any], progress: dict[str, Any]) -> None:
    """
    Fills progress['weather_summary'] and progress['weather_error'].
    Fail-open: errors do not raise; itinerary can still run without weather.
    """
    progress["weather_summary"] = ""
    progress["weather_error"] = ""

    dest = trip_overview.get("destination")
    if not dest:
        progress["weather_error"] = "skipped_no_destination"
        return

    dr = _trip_date_range(trip_overview)
    if not dr:
        progress["weather_error"] = "skipped_no_date_range"
        progress["weather_summary"] = (
            "No trip start/end dates in state — add **start_date** (YYYY-MM-DD) and **end_date** for a forecast."
        )
        return

    start, end = dr

    try:
        geo = geocode_destination(str(dest))
        if not geo:
            progress["weather_error"] = f"geocode_not_found:{dest}"
            return

        label = f"{geo['name']}, {geo['country']}".strip().rstrip(",")
        summary = fetch_forecast_summary(
            float(geo["latitude"]),
            float(geo["longitude"]),
            start,
            end,
            location_label=label,
        )
        mode = "forecast" if _forecast_intersection(start, end) is not None else "climate_normals"
        progress["weather_summary"] = summary
        progress["weather_meta"] = {
            "provider": "open-meteo",
            "mode": mode,
            "latitude": geo["latitude"],
            "longitude": geo["longitude"],
            "label": label,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
    except urllib.error.URLError as exc:
        progress["weather_error"] = f"network:{exc}"
    except Exception as exc:
        progress["weather_error"] = f"{type(exc).__name__}:{exc}"[:500]
