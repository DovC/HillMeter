# VertHurt — Ideas & Future Improvements

## GPS Data Normalization

### Problem
Same course, same day, different watches → different scores. NYC Marathon scored 28 (AB's watch) vs 34 (Hana's watch) — a 52% difference in recorded elevation gain (559ft vs 849ft).

### Root Causes
- **GPS chipset quality**: Barometric altimeters (higher-end Garmin/Apple Watch Ultra) are more accurate than pure GPS elevation
- **Recording frequency**: Every-second recording captures more noise than smart-recording
- **GPS multipath**: Tall buildings (Manhattan) cause signal reflection, varying by chipset and wrist position

### Implemented (March 2026)
- **Option A**: Standardized point density resampling (10m intervals) before smoothing
- **Option B**: Adaptive dead-band threshold (3-5m) based on noise detection (MAD of elevation deltas)

### Future — Not Yet Implemented
- **Option C: DEM-based elevation correction (Gold Standard)** — Replace watch elevation data with terrain-based elevation from Digital Elevation Model databases (SRTM, Google Elevation API). This is what Strava's "elevation correction" does. Completely eliminates watch-to-watch variance. Makes the score about *the route*, not *the watch*. This is the industry standard approach.
- **Option D: Known course profiles** — For major races, cross-reference against published official elevation profiles.

---

## Route Length Consideration

### Problem
A 5K with 200ft of gain and a marathon with 200ft of gain are very different experiences, but score similarly on gain/mile.

### Recommendation
Don't bake distance into the VertHurt score — keep it as a pure hilliness-per-mile metric. Instead, add **contextual percentiles**: "Hillier than 84% of half marathons." Requires saved route data to build percentile distributions by distance bucket.

### Alternative
A separate "Difficulty" score that combines VertHurt × distance. Keep VertHurt pure, add difficulty as a second metric.

---

## Downhill Factor / Vertical Difficulty Score

### Problem
Western States 100 has 20,624 ft of descent (more than its 15,677 ft of gain). Steep downhills destroy quads (running) and require heavy braking (cycling). Currently scored as zero.

### Philosophical Question
Are we measuring *hilliness* or *difficulty*? Boston Marathon's downhills are brutal but not "hilly" in the traditional sense.

### Recommendation
Stay "VertHurt" (hilliness) for the main score. Add a **Downhill Impact** sub-score that captures:
- Steep descents (>8% grade downhill)
- Sustained descents (continuity logic applied to downhill)
- Net elevation change (point-to-point courses)

Display as a separate metric on the card, not folded into the main score.

---

## Cycling Model

### Key Differences from Running
- Descents matter differently (coasting = recovery, not difficulty)
- Sustained grades hit differently (drafting, gearing)
- Speed means gradient *duration* matters more than gradient *distance*
- Category climbs (HC, Cat 1-4) are established benchmarks to calibrate against

### Architecture
`mode` parameter already exists in `compute_score()`. Build running model solid first, then add cycling-specific weights and thresholds.

---

## Altitude Effects

### Problem
Running at 8,000+ ft is physiologically brutal. Western States ranges 586–8,681 ft. The algorithm doesn't consider altitude.

### Potential Approach
Altitude modifier that increases perceived difficulty above ~5,000 ft. Could be a separate metric or a multiplier on the difficulty score.

---

## Algorithm Protection

### Current State
Scoring logic moved server-side (Python/FastAPI). Client can no longer view the algorithm.

### Future Consideration
If algorithm becomes a competitive advantage, consider additional obfuscation or compilation to binary (Cython, PyInstaller).
