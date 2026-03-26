"""
VertHurt Scoring Engine — Server-side GPX hilliness analysis.

This module contains the proprietary scoring algorithm. It is never
sent to the client.
"""

import math
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


# ============ DATA STRUCTURES ============

@dataclass
class Point:
    lat: float
    lon: float
    ele: float


@dataclass
class Segment:
    dist: float
    ele_change: float
    gradient: float
    cum_dist: float
    start_ele: float
    end_ele: float


@dataclass
class Climb:
    dist: float
    gain: float
    gradient_sum: float = 0.0  # sum of abs gradients × segment dist (for weighted avg)


@dataclass
class GradientBand:
    min_pct: float
    max_pct: float
    dist: float
    label: str


@dataclass
class ScoringResult:
    name: str
    date: Optional[str]
    composite: int
    descriptor: str
    score_class: str
    density_score: int
    intensity_score: int
    continuity_score: int
    total_dist_km: float
    total_gain: float
    total_loss: float
    min_ele: float
    max_ele: float
    gain_per_km: float
    bands: dict
    band_colors: dict
    climb_dist: float
    profile: list  # [{dist, ele}, ...]
    segments: list

    def to_dict(self):
        return {
            "name": self.name,
            "date": self.date,
            "composite": self.composite,
            "descriptor": self.descriptor,
            "scoreClass": self.score_class,
            "densityScore": self.density_score,
            "intensityScore": self.intensity_score,
            "continuityScore": self.continuity_score,
            "totalDist": self.total_dist_km,
            "totalGain": self.total_gain,
            "totalLoss": self.total_loss,
            "minEle": self.min_ele,
            "maxEle": self.max_ele,
            "gainPerKm": self.gain_per_km,
            "bands": {
                k: {"dist": v["dist"], "label": v["label"]}
                for k, v in self.bands.items()
            },
            "bandColors": self.band_colors,
            "climbDist": self.climb_dist,
            "profile": self.profile,
        }


# ============ CONSTANTS ============

# Scoring weights
WEIGHT_DENSITY = 0.40
WEIGHT_INTENSITY = 0.35
WEIGHT_CONTINUITY = 0.25

# Ceilings (calibrated from real GPX data)
DENSITY_CEILING = 50       # m/km (~264 ft/mi)
INTENSITY_CEILING = 25     # calibrated: Arlington=8.3, Lake Lure=10.4, mountain=25+
CONTINUITY_CEILING = 50    # calibrated with gradient-weighted metric: Wilmington=6.5, Arlington=31, Lake Lure=39
CONTINUITY_EXPONENT = 1.3  # power-sum exponent for climb length weighting

# Pre-processing
NORMALIZE_INTERVAL = 10    # meters — fixed point density before smoothing (Option A)
MEDIAN_WINDOW = 7
SMOOTH_WINDOW = 5
DEAD_BAND_BASE = 3.0       # meters — base dead-band threshold (clean data)
DEAD_BAND_MAX = 5.0        # meters — maximum dead-band threshold (noisy data)
DEAD_BAND_NOISE_FLOOR = 0.5  # MAD below this → clean data, keep base threshold
DEAD_BAND_NOISE_CAP = 2.0    # MAD above this → fully noisy, use max threshold
RESAMPLE_INTERVAL = 25     # meters — gradient calculation interval
CLIMB_GRADIENT_THRESHOLD = 0.5  # % — minimum to count as climbing


# ============ GPX PARSING ============

def parse_gpx(xml_string: str) -> dict:
    """Parse GPX XML string into name, date, and points."""
    # Handle namespace
    root = ET.fromstring(xml_string)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    # Extract track name
    name_el = root.find(f".//{ns}trk/{ns}name")
    name = name_el.text if name_el is not None else "Unnamed Route"

    # Extract date from first trackpoint time or metadata time
    date = None
    time_el = root.find(f".//{ns}trkpt/{ns}time")
    if time_el is None:
        time_el = root.find(f".//{ns}metadata/{ns}time")
    if time_el is not None and time_el.text:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            date = dt.strftime("%b %d, %Y")
        except (ValueError, TypeError):
            pass

    # Extract trackpoints
    points = []
    for trkpt in root.iter(f"{ns}trkpt"):
        lat = float(trkpt.get("lat"))
        lon = float(trkpt.get("lon"))
        ele_el = trkpt.find(f"{ns}ele")
        ele = float(ele_el.text) if ele_el is not None else 0.0
        points.append(Point(lat, lon, ele))

    return {"name": name, "date": date, "points": points}


# ============ HAVERSINE DISTANCE ============

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lon points."""
    R = 6371000
    to_rad = math.radians
    d_lat = to_rad(lat2 - lat1)
    d_lon = to_rad(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + \
        math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ============ OPTION A: NORMALIZE POINT DENSITY ============

def normalize_point_density(points: list[Point], interval_m: float = NORMALIZE_INTERVAL) -> list[Point]:
    """
    Resample raw GPS track to one point every `interval_m` meters of horizontal
    distance using linear interpolation. This ensures files with different
    recording frequencies enter the smoothing pipeline with uniform point density.
    """
    if len(points) < 2:
        return list(points)

    # Build cumulative horizontal distances
    cum_dists = [0.0]
    for i in range(1, len(points)):
        cum_dists.append(
            cum_dists[-1] + haversine(points[i - 1].lat, points[i - 1].lon,
                                       points[i].lat, points[i].lon)
        )
    total_dist = cum_dists[-1]

    if total_dist < interval_m:
        return list(points)

    result = [Point(points[0].lat, points[0].lon, points[0].ele)]
    pt_idx = 0
    d = interval_m

    while d <= total_dist:
        # Advance to the bracketing pair
        while pt_idx < len(cum_dists) - 1 and cum_dists[pt_idx + 1] < d:
            pt_idx += 1

        next_idx = min(pt_idx + 1, len(points) - 1)
        span = cum_dists[next_idx] - cum_dists[pt_idx]
        frac = (d - cum_dists[pt_idx]) / span if span > 0 else 0.0

        lat = points[pt_idx].lat + frac * (points[next_idx].lat - points[pt_idx].lat)
        lon = points[pt_idx].lon + frac * (points[next_idx].lon - points[pt_idx].lon)
        ele = points[pt_idx].ele + frac * (points[next_idx].ele - points[pt_idx].ele)

        result.append(Point(lat, lon, ele))
        d += interval_m

    # Always include the final point to preserve total distance
    last = points[-1]
    if len(result) > 0:
        tail_dist = haversine(result[-1].lat, result[-1].lon, last.lat, last.lon)
        if tail_dist > 1.0:  # avoid duplicate if already very close
            result.append(Point(last.lat, last.lon, last.ele))

    return result


# ============ SPIKE REMOVAL (Median Filter) ============

def median_filter(points: list[Point], window_size: int = MEDIAN_WINDOW) -> list[Point]:
    """Remove GPS spike noise while preserving terrain shape."""
    half = window_size // 2
    result = []
    for i, p in enumerate(points):
        window = sorted(
            points[j].ele
            for j in range(max(0, i - half), min(len(points), i + half + 1))
        )
        median = window[len(window) // 2]
        result.append(Point(p.lat, p.lon, median))
    return result


# ============ SMOOTHING ============

def smooth_elevation(points: list[Point], window_size: int = SMOOTH_WINDOW) -> list[Point]:
    """Two-pass smoothing: median filter then moving average."""
    despiked = median_filter(points, MEDIAN_WINDOW)
    half = window_size // 2
    result = []
    for i, p in enumerate(despiked):
        start = max(0, i - half)
        end = min(len(despiked), i + half + 1)
        avg_ele = sum(despiked[j].ele for j in range(start, end)) / (end - start)
        result.append(Point(p.lat, p.lon, avg_ele))
    return result


# ============ BUILD SEGMENTS ============

def build_segments(points: list[Point], interval_m: float = RESAMPLE_INTERVAL) -> list[Segment]:
    """Resample elevation at fixed distance intervals for gradient calculation."""
    # Build cumulative distances
    cum_dists = [0.0]
    for i in range(1, len(points)):
        cum_dists.append(
            cum_dists[-1] + haversine(points[i - 1].lat, points[i - 1].lon,
                                       points[i].lat, points[i].lon)
        )
    total_dist = cum_dists[-1]

    segments = []
    prev_ele = points[0].ele
    pt_idx = 0

    d = interval_m
    while d <= total_dist:
        # Find bracketing points
        while pt_idx < len(cum_dists) - 1 and cum_dists[pt_idx + 1] < d:
            pt_idx += 1

        # Interpolate elevation
        span = cum_dists[pt_idx + 1] - cum_dists[pt_idx]
        frac = (d - cum_dists[pt_idx]) / span if span > 0 else 0
        next_idx = min(pt_idx + 1, len(points) - 1)
        ele = points[pt_idx].ele + frac * (points[next_idx].ele - points[pt_idx].ele)

        ele_change = ele - prev_ele
        gradient = (ele_change / interval_m) * 100

        segments.append(Segment(
            dist=interval_m,
            ele_change=ele_change,
            gradient=gradient,
            cum_dist=d,
            start_ele=prev_ele,
            end_ele=ele
        ))
        prev_ele = ele
        d += interval_m

    # Handle remaining distance
    remain_dist = total_dist - len(segments) * interval_m
    if remain_dist > 5:
        last_ele = points[-1].ele
        ele_change = last_ele - prev_ele
        gradient = (ele_change / remain_dist) * 100
        segments.append(Segment(
            dist=remain_dist,
            ele_change=ele_change,
            gradient=gradient,
            cum_dist=total_dist,
            start_ele=prev_ele,
            end_ele=last_ele
        ))

    return segments


# ============ OPTION B: ADAPTIVE DEAD-BAND ============

def compute_adaptive_dead_band(points: list[Point]) -> float:
    """
    Analyze signal-to-noise ratio of smoothed elevation data and return an
    adaptive dead-band threshold. Uses Median Absolute Deviation (MAD) of
    point-to-point elevation changes as a robust noise estimator.

    - Clean data (low MAD) → base threshold (3m)
    - Noisy data (high MAD) → up to max threshold (5m)
    - Linear interpolation between floor and cap
    """
    if len(points) < 3:
        return DEAD_BAND_BASE

    # Compute point-to-point elevation deltas
    deltas = [abs(points[i].ele - points[i - 1].ele) for i in range(1, len(points))]

    # MAD = median(|delta_i - median(deltas)|)
    med = statistics.median(deltas)
    mad = statistics.median([abs(d - med) for d in deltas])

    # Linear ramp between floor and cap
    if mad <= DEAD_BAND_NOISE_FLOOR:
        return DEAD_BAND_BASE
    if mad >= DEAD_BAND_NOISE_CAP:
        return DEAD_BAND_MAX

    t = (mad - DEAD_BAND_NOISE_FLOOR) / (DEAD_BAND_NOISE_CAP - DEAD_BAND_NOISE_FLOOR)
    return DEAD_BAND_BASE + t * (DEAD_BAND_MAX - DEAD_BAND_BASE)


# ============ DEAD-BAND GAIN/LOSS ============

def compute_dead_band_gain(points: list[Point], threshold: float = None) -> tuple[float, float]:
    """
    Strava-style elevation correction: only register changes exceeding threshold.
    Eliminates GPS wobble that accumulates into phantom gain.

    If threshold is None, the adaptive dead-band is computed automatically
    from the noise characteristics of the data (Option B).
    """
    if not points:
        return 0.0, 0.0

    if threshold is None:
        threshold = compute_adaptive_dead_band(points)

    gain = 0.0
    loss = 0.0
    ref_ele = points[0].ele
    for p in points[1:]:
        diff = p.ele - ref_ele
        if diff >= threshold:
            gain += diff
            ref_ele = p.ele
        elif diff <= -threshold:
            loss += abs(diff)
            ref_ele = p.ele
    return gain, loss


# ============ SCORE COMPUTATION ============

def compute_score(gpx_xml: str, name: str = None, mode: str = "running") -> ScoringResult:
    """
    Compute the VertHurt hilliness score for a GPX file.

    Args:
        gpx_xml: Raw GPX XML string
        name: Override route name (e.g., from filename)
        mode: "running" or "cycling" (future)

    Returns:
        ScoringResult with all scores, stats, and profile data
    """
    gpx_data = parse_gpx(gpx_xml)
    if name:
        gpx_data["name"] = name

    points = gpx_data["points"]
    if len(points) < 2:
        raise ValueError("GPX file must contain at least 2 trackpoints")

    # Pre-processing pipeline
    normalized = normalize_point_density(points)   # Option A: uniform 10m spacing
    smoothed = smooth_elevation(normalized)
    segments = build_segments(smoothed)

    total_dist = sum(seg.dist for seg in segments)
    total_dist_km = total_dist / 1000

    # Dead-band elevation gain/loss (Option B: adaptive threshold)
    gain, loss = compute_dead_band_gain(smoothed)  # threshold=None → auto-detect

    # Initialize tracking
    min_ele = float("inf")
    max_ele = float("-inf")

    bands = {
        "easy": {"min": 0, "max": 4, "dist": 0.0, "label": "0–4%"},
        "moderate": {"min": 4, "max": 8, "dist": 0.0, "label": "4–8%"},
        "hard": {"min": 8, "max": 12, "dist": 0.0, "label": "8–12%"},
        "severe": {"min": 12, "max": float("inf"), "dist": 0.0, "label": "12%+"},
    }
    band_colors = {
        "easy": "#059669",
        "moderate": "#0891B2",
        "hard": "#D97706",
        "severe": "#DC2626",
    }

    intensity_sum = 0.0
    climb_dist = 0.0
    climbs = []
    current_climb = None

    for seg in segments:
        # Track elevation range
        for ele in (seg.start_ele, seg.end_ele):
            if ele < min_ele:
                min_ele = ele
            if ele > max_ele:
                max_ele = ele

        # Only climbing segments contribute to score
        if seg.gradient > CLIMB_GRADIENT_THRESHOLD:
            abs_grad = abs(seg.gradient)
            climb_dist += seg.dist

            # Gradient-weighted intensity: dist × gradient^1.5
            intensity_sum += seg.dist * (abs_grad ** 1.5)

            # Band classification
            if abs_grad < 4:
                bands["easy"]["dist"] += seg.dist
            elif abs_grad < 8:
                bands["moderate"]["dist"] += seg.dist
            elif abs_grad < 12:
                bands["hard"]["dist"] += seg.dist
            else:
                bands["severe"]["dist"] += seg.dist

            # Track climb continuity
            if current_climb is None:
                current_climb = Climb(dist=seg.dist, gain=seg.ele_change, gradient_sum=abs_grad * seg.dist)
            else:
                current_climb.dist += seg.dist
                current_climb.gain += seg.ele_change
                current_climb.gradient_sum += abs_grad * seg.dist
        else:
            if current_climb and current_climb.dist > 0:
                climbs.append(current_climb)
            current_climb = None

    # Don't forget last climb
    if current_climb and current_climb.dist > 0:
        climbs.append(current_climb)

    # ---- SQUARE ROOT SCALING ----
    # score = sqrt(value / ceiling) × 100

    # Component 1: Climb Density (40%)
    gain_per_km = gain / total_dist_km if total_dist_km > 0 else 0
    density_score = min(100, math.sqrt(gain_per_km / DENSITY_CEILING) * 100)

    # Component 2: Gradient Intensity (35%)
    raw_intensity = intensity_sum / total_dist if total_dist > 0 else 0
    intensity_score = min(100, math.sqrt(raw_intensity / INTENSITY_CEILING) * 100)

    # Component 3: Climb Continuity (25%)
    # Gradient-weighted: long gentle rises contribute much less than long steep climbs.
    # Each climb's contribution = climb_length^p × avg_gradient_of_climb
    continuity_score = 0.0
    if climbs:
        total_climb_dist = sum(c.dist for c in climbs)
        power_sum = sum(
            (c.dist ** CONTINUITY_EXPONENT) * (c.gradient_sum / c.dist if c.dist > 0 else 0)
            for c in climbs
        )
        continuity_metric = power_sum / total_climb_dist if total_climb_dist > 0 else 0
        continuity_score = min(100, math.sqrt(continuity_metric / CONTINUITY_CEILING) * 100)

    # Dampen continuity when there's negligible climbing — continuity is
    # meaningless if there's nothing to be continuous about.
    density_dampen = min(1.0, (density_score / 40) ** 0.5)
    continuity_score = continuity_score * density_dampen

    # Composite
    composite = round(
        density_score * WEIGHT_DENSITY
        + intensity_score * WEIGHT_INTENSITY
        + continuity_score * WEIGHT_CONTINUITY
    )

    # Descriptor
    if composite < 15:
        descriptor, score_class = "Flat", "score-flat"
    elif composite < 30:
        descriptor, score_class = "Nearly Flat", "score-flat"
    elif composite < 45:
        descriptor, score_class = "Gently Rolling", "score-rolling"
    elif composite < 60:
        descriptor, score_class = "Rolling", "score-rolling"
    elif composite < 70:
        descriptor, score_class = "Hilly", "score-hilly"
    elif composite < 82:
        descriptor, score_class = "Very Hilly", "score-hilly"
    else:
        descriptor, score_class = "Mountainous", "score-mountainous"

    # Build elevation profile (subsampled for client rendering)
    profile_points = []
    cd = 0.0
    for i, p in enumerate(smoothed):
        if i > 0:
            cd += haversine(smoothed[i - 1].lat, smoothed[i - 1].lon, p.lat, p.lon)
        profile_points.append({"dist": cd, "ele": p.ele})

    step = max(1, len(profile_points) // 500)
    sampled_profile = [profile_points[i] for i in range(0, len(profile_points), step)]
    if sampled_profile[-1] != profile_points[-1]:
        sampled_profile.append(profile_points[-1])

    return ScoringResult(
        name=gpx_data["name"],
        date=gpx_data["date"],
        composite=composite,
        descriptor=descriptor,
        score_class=score_class,
        density_score=round(density_score),
        intensity_score=round(intensity_score),
        continuity_score=round(continuity_score),
        total_dist_km=total_dist_km,
        total_gain=gain,
        total_loss=loss,
        min_ele=min_ele,
        max_ele=max_ele,
        gain_per_km=gain_per_km,
        bands=bands,
        band_colors=band_colors,
        climb_dist=climb_dist,
        profile=sampled_profile,
        segments=[],  # Don't send raw segments to client
    )
