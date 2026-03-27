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

---

## Strava API — Legal Constraints & Architecture

### Key Legal Finding (March 2026)
Anything touching the Strava API — even a user's own GPX-derived activity data pulled via API — becomes "Strava Data" under their agreement and is subject to all restrictions (seven-day cache, no cross-user display, no aggregation, no AI training). Strava enforced this aggressively in fall 2024, forcing TrailForks to delete ~60M activities.

### Critical Architecture Decision: Two Data Pipelines

**Pipeline 1 — User-uploaded GPX (WE OWN THIS DATA)**
- User manually exports GPX from Strava/Garmin/etc., drags into VertHurt
- File never touches Strava API → not "Strava Data"
- We can store forever, aggregate, build percentiles, train models, share freely
- This is our strongest legal asset

**Pipeline 2 — Strava API (RESTRICTED, minimal use)**
- OAuth for authentication ONLY (sign in with Strava)
- Read profile (name, avatar, city) for display
- NO activity pulls, NO route sync, NO data storage beyond profile
- P0.5: `activity:write` to post VertHurt score back to Strava activity description — this is us *writing to* Strava, not reading data. Should be fine but needs legal review.

### Permanently Removed from Roadmap
- Strava auto-sync / activity import via API
- Any feature that pulls activities or routes through Strava API
- Commingling of API-sourced data with user-uploaded data

### Garmin Attribution (Section 2.4)
If any API-synced data originated from a Garmin device, Garmin attribution must be displayed. Another reason to avoid API data pipeline entirely.

### Reference
DC Rainmaker coverage of TrailForks/Strava enforcement action, fall 2024.

---

## Mobile Strategy

### Current State
Mobile-responsive web is low priority for beta. Downloading and uploading a GPX on mobile is painful — not a realistic user flow. Mobile web should be functional but not polished.

### Future: Native Mobile App
A mobile app is the right path for mobile users. It can integrate directly with on-device file storage, making GPX upload much easier.

### Architecture Implication
All server-side code must follow a **services architecture with clean API endpoints**. The web UI is just one client consuming `/api/*` routes. A future mobile app (React Native, Flutter, or native) hits the same endpoints. Every feature must be built API-first, with the web frontend as a thin client.

Current API endpoints:
- `POST /api/score` — GPX upload + scoring
- `POST /api/waitlist` — email capture
- `GET /api/waitlist/count` — waitlist count

Planned endpoints (P0):
- `GET /api/auth/strava` — initiate OAuth
- `GET /api/auth/strava/callback` — OAuth callback
- `GET /api/auth/me` — current user profile
- `POST /api/auth/logout` — logout
- `GET /api/routes` — user's saved routes
- `POST /api/routes` — save a scored route
- `GET /api/routes/{id}` — single route detail
- `DELETE /api/routes/{id}` — remove route from library

---

## Anonymous Gate / Conversion Strategy

### Gate Logic (Confirmed March 2026)

| Action | Anonymous | Authenticated |
|---|---|---|
| Score 1st route | Free | Free |
| Score 2nd route | Free | Free |
| Score 3rd route | Free | Free |
| Score 4th+ route | **Gate** | Free |
| Compare any 2 routes | **Gate** | Free |
| Save route | **Gate** | Free |
| Share route | **Gate** | Free |

### Implementation
- Track score count in **localStorage** (persists across visits, prevents new-tab bypass)
- Gate modal appears with "You're hooked. We knew it." + "Continue with Strava" CTA
- Store pending action in sessionStorage so post-auth resumes exactly where user left off
- Gate modal explains value: compare routes, save library, share Score Cards

### Rationale
3 free scores lets users see enough value before converting. They've likely already thought "I want to compare these" — natural conversion moment. Less friction than gating on second upload.

---

## Strava OAuth Configuration

### Setup
- **Client ID**: 157764
- **Credentials**: Stored as environment variables (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`), never in code
- **Scopes**: `read` (P0), add `activity:write` for P0.5
- **Callback domains**: localhost, hilliness-analyzer-1077888722357.us-east1.run.app, verthurt.com
- **Developer Program**: Currently on personal dev account. Need to apply for full Dev Program to scale beyond personal testing. Up to 15 users can auth before approval.

### What Strava Auth Provides
- User authentication (sign in)
- Profile data: name, avatar, city
- Nothing else — no activity reads, no route sync (see Strava Legal Constraints section)
