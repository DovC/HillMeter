# Route Hilliness Analyzer

A single-page app that scores GPX running routes for "hilliness" using logarithmic scaling across three dimensions: climb density, gradient-weighted intensity, and steep proportion.

## Deploy to Google Cloud

### Option A: Cloud Run (recommended)

```bash
# Build and deploy in one command
gcloud run deploy hilliness-analyzer \
  --source . \
  --region us-east1 \
  --allow-unauthenticated
```

### Option B: App Engine

```bash
gcloud app deploy app.yaml
```

### Option C: Just run locally

```bash
# Python
python3 -m http.server 8080

# Or Docker
docker build -t hilliness . && docker run -p 8080:8080 hilliness
```

## How It Works

Drop a GPX file onto the page. The algorithm:

1. Smooths GPS elevation noise (5-point moving average)
2. Segments the route into climbs by gradient
3. Scores three components (log-scaled 0–100):
   - **Climb Density** (20%) — meters gained per km
   - **Gradient-Weighted Intensity** (35%) — steeper grades weighted quadratically
   - **Steep Proportion** (45%) — fraction of climbing above 8% grade
4. Produces a composite 0–100 hilliness score

All processing happens client-side in the browser. No server, no API calls, no data leaves the user's machine.
