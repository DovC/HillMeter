"""
Elevation normalization for VertHurt.

Replaces device-recorded GPS elevation with DEM-based elevation from an
external API provider, giving consistent gain/loss numbers across devices.

Provider abstraction: swap Google → GPXZ via env vars, no code change.
  ELEVATION_PROVIDER     = "google" | "none"   (default: "google")
  GOOGLE_ELEVATION_API_KEY                      (required when provider = google)
  ELEVATION_BASE_URL     = provider endpoint    (default: Google Elevation API)
  ELEVATION_BATCH_SIZE   = points per request   (default: 512)
  ELEVATION_MIN_SPACING_M = min metres between sampled points (default: 10)
"""

import os
import logging
from typing import Optional
import httpx
from scoring import Point, haversine

logger = logging.getLogger(__name__)

# ── Config (read from env, never hardcoded) ────────────────────────────────────
_PROVIDER = os.environ.get("ELEVATION_PROVIDER", "google").lower()
_API_KEY = os.environ.get("GOOGLE_ELEVATION_API_KEY", "")
_BASE_URL = os.environ.get(
    "ELEVATION_BASE_URL",
    "https://maps.googleapis.com/maps/api/elevation/json",
)
_BATCH_SIZE = int(os.environ.get("ELEVATION_BATCH_SIZE", "512"))
_MIN_SPACING_M = float(os.environ.get("ELEVATION_MIN_SPACING_M", "10"))
_TIMEOUT_S = 10  # seconds per batch request


def normalize_elevations(points: list[Point]) -> tuple[list[Point], str]:
    """
    Replace device elevation with DEM-based API elevation.

    Returns (normalized_points, source) where source is:
      "google" — API call succeeded, canonical DEM elevation applied
      "device" — API unavailable or disabled; device elevation used as fallback

    Any trackpoints with no device elevation (None) are filled from the API,
    or set to 0.0 if the API is unavailable.
    """
    if _PROVIDER == "none" or not _API_KEY:
        return _device_fallback(points), "device"

    try:
        sampled_indices = _sample_by_distance(points, _MIN_SPACING_M)
        sampled_pts = [points[i] for i in sampled_indices]

        api_elevations = _fetch_provider(sampled_pts)
        if api_elevations is None:
            raise RuntimeError("API returned no usable results")

        all_elevations = _scatter_elevations(len(points), sampled_indices, api_elevations)
        normalized = [Point(p.lat, p.lon, e) for p, e in zip(points, all_elevations)]
        return normalized, "google"

    except Exception as exc:
        logger.warning("Elevation API failed, falling back to device elevation: %s", exc)
        return _device_fallback(points), "device"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _device_fallback(points: list[Point]) -> list[Point]:
    """Return points with any None elevations replaced by 0.0."""
    return [Point(p.lat, p.lon, p.ele if p.ele is not None else 0.0) for p in points]


def _sample_by_distance(points: list[Point], min_metres: float) -> list[int]:
    """
    Return indices into `points` keeping at least min_metres between consecutive
    retained points. Always includes the first and last point.
    """
    if not points:
        return []

    kept = [0]
    for i in range(1, len(points)):
        dist = haversine(
            points[kept[-1]].lat, points[kept[-1]].lon,
            points[i].lat, points[i].lon,
        )
        if dist >= min_metres:
            kept.append(i)

    last = len(points) - 1
    if kept[-1] != last:
        kept.append(last)
    return kept


def _fetch_provider(points: list[Point]) -> Optional[list[float]]:
    """
    Call the configured elevation provider in batches of _BATCH_SIZE.
    Returns a flat list of elevations matching input order, or None on any error.
    Uses the Google Elevation API response format (also GPXZ-compat).
    """
    elevations: list[float] = []

    for i in range(0, len(points), _BATCH_SIZE):
        batch = points[i : i + _BATCH_SIZE]
        locations = "|".join(f"{p.lat},{p.lon}" for p in batch)
        try:
            resp = httpx.get(
                _BASE_URL,
                params={"locations": locations, "key": _API_KEY},
                timeout=_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(
                "Elevation API request failed (batch %d): %s",
                i // _BATCH_SIZE + 1, exc,
            )
            return None

        if data.get("status") != "OK":
            logger.warning(
                "Elevation API error: %s %s",
                data.get("status"),
                data.get("error_message", ""),
            )
            return None

        results = data.get("results", [])
        if len(results) != len(batch):
            logger.warning(
                "Elevation API result count mismatch: got %d, expected %d",
                len(results), len(batch),
            )
            return None

        elevations.extend(r["elevation"] for r in results)

    return elevations if len(elevations) == len(points) else None


def _scatter_elevations(
    total_points: int,
    sampled_indices: list[int],
    sampled_eles: list[float],
) -> list[float]:
    """
    Map sampled API elevations back to the full point array.
    Un-sampled points receive the elevation of their nearest sampled neighbor
    (nearest-neighbor, not interpolated — matches PRD intent).
    """
    elevations = [0.0] * total_points

    # Assign sampled points directly
    for idx, ele in zip(sampled_indices, sampled_eles):
        elevations[idx] = ele

    if not sampled_indices:
        return elevations

    # Fill gaps between consecutive sampled points
    for k in range(len(sampled_indices) - 1):
        left = sampled_indices[k]
        right = sampled_indices[k + 1]
        mid = (left + right) // 2
        for j in range(left + 1, right):
            elevations[j] = sampled_eles[k] if j <= mid else sampled_eles[k + 1]

    # Fill any points before first sample (index 0 is always kept, so this is a no-op)
    for j in range(0, sampled_indices[0]):
        elevations[j] = sampled_eles[0]

    # Fill any points after last sample (last index is always kept, so this is a no-op)
    for j in range(sampled_indices[-1] + 1, total_points):
        elevations[j] = sampled_eles[-1]

    return elevations
