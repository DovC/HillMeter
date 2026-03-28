# How To: Access the Scored Routes Data Store

Every GPX file scored by HillMeter is saved anonymously to a Firestore collection called `scored_routes` (no user data stored). This guide covers how to access that data for analysis.

---

## Google Cloud Console (no code required)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and select project **hilliness-analyzer**
2. Navigate to **Firestore** → **Data**
3. Click the `scored_routes` collection in the left panel
4. Browse documents, filter by field, or click any document to inspect its full contents

To filter (e.g. routes over 20km):
- Click **Filter** above the document list
- Field: `total_dist_km`, Operator: `>=`, Value: `20`

---

## Python (ad hoc analysis)

Install the Firestore client if needed:

```bash
pip install google-cloud-firestore
```

Authenticate with your Google account:

```bash
gcloud auth application-default login
```

### Fetch all records into a list of dicts

```python
from google.cloud import firestore

db = firestore.Client(project="hilliness-analyzer")
docs = db.collection("scored_routes").stream()
rows = [d.to_dict() for d in docs]

print(f"{len(rows)} routes")
```

### Load into a pandas DataFrame

```python
import pandas as pd
from google.cloud import firestore

db = firestore.Client(project="hilliness-analyzer")
docs = db.collection("scored_routes").stream()

df = pd.DataFrame([d.to_dict() for d in docs])

# Drop large fields not needed for numeric analysis
df = df.drop(columns=["gpx_raw", "profile", "bands", "band_colors"], errors="ignore")

print(df[["name", "composite", "descriptor", "total_dist_km", "total_gain", "scored_at"]].head(20))
```

### Filter by score or distance

```python
# Routes scored 70+ (Very Hilly / Mountainous)
hilly = db.collection("scored_routes").where("composite", ">=", 70).stream()
rows = [d.to_dict() for d in hilly]
```

### Export to JSONL for offline use

```python
import json
from google.cloud import firestore

db = firestore.Client(project="hilliness-analyzer")
docs = db.collection("scored_routes").stream()

with open("scored_routes.jsonl", "w") as f:
    for doc in docs:
        f.write(json.dumps(doc.to_dict()) + "\n")

print("Export complete")
```

---

## gcloud CLI (bulk export to GCS)

For large-scale exports, use Firestore's managed export to a Cloud Storage bucket:

```bash
gcloud firestore export gs://YOUR_BUCKET/exports/scored_routes \
  --collection-ids=scored_routes \
  --project=hilliness-analyzer
```

Once exported, download locally:

```bash
gsutil -m cp -r gs://YOUR_BUCKET/exports/scored_routes ./scored_routes_export
```

The export is in Firestore's LevelDB format. To convert to JSON/CSV, load it back into a Firestore emulator or use the Python client against the exported files.

---

## Document Schema

| Field | Type | Description |
|---|---|---|
| `fingerprint` | string | Fuzzy dedup key: `{start_lat}_{start_lon}_{end_lat}_{end_lon}_{dist_km}` |
| `gpx_hash` | string | SHA256 of raw GPX (first 16 chars) |
| `gpx_raw` | string | Full GPX XML |
| `name` | string | Route name from GPX file |
| `date` | string | Timestamp from GPX (ISO 8601) |
| `scored_at` | string | Server time when scored (ISO 8601 UTC) |
| `composite` | int | Final hilliness score 0–100 |
| `descriptor` | string | e.g. "Flat", "Rolling", "Very Hilly" |
| `score_class` | string | CSS class (e.g. "score-hilly") |
| `density_score` | int | Climb density component (0–100) |
| `intensity_score` | int | Gradient intensity component (0–100) |
| `continuity_score` | int | Climb continuity component (0–100) |
| `total_dist_km` | float | Route distance in km |
| `total_gain` | float | Total elevation gain in meters |
| `total_loss` | float | Total elevation loss in meters |
| `min_ele` | float | Minimum elevation in meters |
| `max_ele` | float | Maximum elevation in meters |
| `gain_per_km` | float | Elevation gain per km |
| `climb_dist` | float | Distance spent climbing in km |
| `bands` | map | Gradient distribution (easy/moderate/hard/severe) |
| `band_colors` | map | Hex colors for each gradient band |
| `profile` | array | 500-point elevation profile `[{dist, ele}, ...]` |

---

## Firestore Index

The deduplication query filters on `fingerprint`. Firestore will automatically prompt you to create the required index the first time a scored route is saved — follow the link in the Cloud Console error log or Firestore UI to create it with one click.

Alternatively, create it manually:
- **Collection**: `scored_routes`
- **Field**: `fingerprint` (Ascending)
- **Query scope**: Collection
