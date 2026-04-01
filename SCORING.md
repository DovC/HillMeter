# VertHurt Scoring Algorithm

The hilliness score combines three dimensions of what makes a route feel hilly. Each component uses square root scaling, which expands differences between gentle and moderate routes while preserving meaningful separation at the top end — unlike logarithmic scaling, which compresses hard routes into an indistinguishable band near 100.

## 1. Climb Density (Volume) — 40% weight

Total elevation gained divided by total distance. The most intuitive, universal metric — a marathon with 5,000ft of gain is objectively hillier than one with 1,200ft. Sqrt-scaled with a ceiling of ~264 ft/mi (50 m/km).

```
score = sqrt(gain_per_km / 50) x 100
```

## 2. Gradient-Weighted Intensity — 35% weight

For each climbing segment, the gradient is raised to the power of 1.5 and multiplied by segment distance. The 1.5 exponent captures that steep grades are disproportionately hard without over-penalizing moderate grades. Sqrt-scaled with a ceiling of 25.

```
raw = sum(distance_i x gradient_i^1.5) / total_distance
score = sqrt(raw / 25) x 100
```

## 3. Climb Continuity — 25% weight

Measures how sustained and steep the climbs are. A route with one long 2km climb feels much hillier than one with twenty 100m bumps, even if total climbing distance is the same. Uses a gradient-weighted power-sum formula: each climb's length is raised to p=1.3 and multiplied by the climb's average gradient, naturally rewarding longer *and steeper* climbs disproportionately. Sqrt-scaled with a ceiling of 50.

```
metric = sum(climb_length^1.3 x avg_gradient) / total_climb_distance
score = sqrt(metric / 50) x 100
```

Continuity is further dampened when there's negligible climbing overall — continuity is meaningless if there's nothing to be continuous about:

```
density_dampen = min(1.0, (density_score / 40)^0.5)
continuity_score = continuity_score x density_dampen
```

## Noise Confidence Dampener

On extremely flat routes, GPS noise that survives smoothing produces many segments barely above the 0.5% gradient threshold (just 12.5cm rise per 25m segment). These phantom "climbs" inflate all three score components. The dead-band filter is our best estimate of real elevation gain — when segment-based scoring gain vastly exceeds dead-band gain, most of the signal is noise.

A dampener is applied to all three component scores before computing the composite:

```
dampener = min(1.0, sqrt(dead_band_gain / scoring_gain))
```

This has no effect on genuinely hilly routes where dead-band gain and scoring gain converge (~1.0 ratio), but suppresses noise-inflated scores on flat routes (e.g., a route with 3m real gain but 27m phantom scoring gain gets dampener ≈ 0.33).

## Why Square Root Scaling?

Linear scaling compresses gentle-to-moderate routes into a narrow band at the bottom. Logarithmic scaling fixes the bottom but over-compresses the top — routes with 130 and 260 ft/mi gain both hit the ceiling. Square root scaling strikes the right balance: the jump from flat to rolling is still amplified, while genuinely harder routes maintain meaningful separation (210 ft/mi scores 89, not 100).

## Pre-processing

GPS elevation data goes through four filtering passes:

1. **Point Density Normalization** — Raw GPS points are resampled to uniform 10-meter spacing via linear interpolation, ensuring files with different recording frequencies (1-second vs 5-second watches) enter the pipeline with consistent point density.

2. **Median Filter** (window size 7) — Removes sharp elevation spikes caused by GPS drift during watch pauses.

3. **Moving Average** (window size 5) — Smooths remaining noise.

4. **Adaptive Dead-Band Threshold** — Eliminates GPS wobble: small oscillations that aren't real terrain changes but accumulate into phantom elevation gain. The dead-band adapts to signal quality: 3 meters for clean data (e.g., barometric altimeter), ramping up to 5 meters for noisy data (GPS-only altitude), based on the Median Absolute Deviation (MAD) of elevation changes. This approach mirrors Strava's elevation correction and produces gain figures consistent with corrected industry values.

Only uphill segments exceeding 0.5% grade contribute to the score.

## Score Ranges

| Range | Descriptor |
|-------|-----------|
| 0-15 | Flat |
| 15-30 | Nearly Flat |
| 30-45 | Gently Rolling |
| 45-60 | Rolling |
| 60-70 | Hilly |
| 70-82 | Very Hilly |
| 82-100 | Mountainous |
