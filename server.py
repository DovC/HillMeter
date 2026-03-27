from dotenv import load_dotenv
load_dotenv()  # Load .env BEFORE other imports that read env vars

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from google.cloud import firestore
from datetime import datetime
from scoring import compute_score
from auth import strava_login, strava_callback, get_me, logout, get_current_user
import httpx
import os
import re

app = FastAPI()

# Firestore client (auto-authenticates on Cloud Run via service account)
db = firestore.Client(project="hilliness-analyzer")

# PostHog proxy client
posthog_client = httpx.AsyncClient(base_url="https://us.i.posthog.com", timeout=10.0)
posthog_assets_client = httpx.AsyncClient(base_url="https://us-assets.i.posthog.com", timeout=10.0)

@app.post("/api/waitlist")
async def join_waitlist(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()

        if not email or "@" not in email:
            return JSONResponse({"error": "Invalid email"}, status_code=400)

        # Check for duplicate
        existing = db.collection("waitlist").where("email", "==", email).limit(1).get()
        if len(list(existing)) > 0:
            return JSONResponse({"status": "already_registered", "message": "You're already on the list!"})

        # Save to Firestore
        db.collection("waitlist").add({
            "email": email,
            "signed_up_at": datetime.utcnow().isoformat(),
            "source": data.get("source", "landing_page"),
            "user_agent": request.headers.get("user-agent", ""),
        })

        return JSONResponse({"status": "success", "message": "You're on the list!"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

WAITLIST_BASE_COUNT = 1742  # Starting base for social proof

@app.get("/api/waitlist/count")
async def waitlist_count():
    """Count with base offset for social proof (no PII exposed)."""
    docs = db.collection("waitlist").get()
    return JSONResponse({"count": WAITLIST_BASE_COUNT + len(docs)})

# ============ AUTH API ============

app.add_api_route("/api/auth/strava", strava_login, methods=["GET"])
app.add_api_route("/api/auth/strava/callback", strava_callback, methods=["GET"])
app.add_api_route("/api/auth/me", get_me, methods=["GET"])
app.add_api_route("/api/auth/logout", logout, methods=["POST"])

# ============ SCORING API ============

@app.post("/api/score")
async def score_route(file: UploadFile = File(...)):
    """
    Score a GPX file and return hilliness analysis.

    Accepts a GPX file upload, processes it server-side, and returns
    the full scoring result including profile data for rendering.
    """
    try:
        if not file.filename.lower().endswith(".gpx"):
            return JSONResponse({"error": "File must be a .gpx file"}, status_code=400)

        content = await file.read()
        gpx_xml = content.decode("utf-8")

        # Use filename as route name
        name = re.sub(r"\.gpx$", "", file.filename, flags=re.IGNORECASE)
        name = name.replace("_", " ")

        result = compute_score(gpx_xml, name=name, mode="running")

        return JSONResponse(result.to_dict())

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Failed to process GPX: {str(e)}"}, status_code=500)


# PostHog reverse proxy — serves JS assets and forwards events
@app.api_route("/ingest/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def posthog_proxy(path: str, request: Request):
    """Proxy PostHog requests through our domain to bypass ad blockers."""
    try:
        headers = {
            "content-type": request.headers.get("content-type", "application/json"),
            "user-agent": request.headers.get("user-agent", ""),
        }

        # Static assets come from the assets CDN
        if path.startswith("static/"):
            resp = await posthog_assets_client.get(f"/{path}", headers=headers)
        elif request.method == "GET":
            resp = await posthog_client.get(f"/{path}", params=dict(request.query_params), headers=headers)
        else:
            body = await request.body()
            resp = await posthog_client.post(f"/{path}", content=body, headers=headers, params=dict(request.query_params))

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json")
        )
    except Exception:
        return Response(status_code=502)

# Serve static files — app.html for alpha testers, index.html for public
app.mount("/", StaticFiles(directory="static", html=True), name="static")
