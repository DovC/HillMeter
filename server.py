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


# ============ ROUTES API ============

import hashlib
import json

@app.post("/api/routes")
async def save_route(request: Request):
    """Save a scored route to the user's library."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    try:
        data = await request.json()
        score_data = data.get("score_data")
        gpx_raw = data.get("gpx_raw", "")

        if not score_data:
            return JSONResponse({"error": "Missing score data"}, status_code=400)

        # Hash GPX for deduplication
        gpx_hash = hashlib.sha256(gpx_raw.encode()).hexdigest()[:16] if gpx_raw else None

        # Check if this route already exists (by hash)
        route_id = None
        if gpx_hash:
            existing = db.collection("routes").where("gpx_hash", "==", gpx_hash).limit(1).get()
            existing_list = list(existing)
            if existing_list:
                route_id = existing_list[0].id

        # Create route document if new
        if not route_id:
            route_doc = {
                "gpx_hash": gpx_hash,
                "gpx_raw": gpx_raw,
                "name": score_data.get("name", "Unnamed Route"),
                "date": score_data.get("date", ""),
                "composite": score_data.get("composite", 0),
                "descriptor": score_data.get("descriptor", ""),
                "scoreClass": score_data.get("scoreClass", ""),
                "densityScore": score_data.get("densityScore", 0),
                "intensityScore": score_data.get("intensityScore", 0),
                "continuityScore": score_data.get("continuityScore", 0),
                "totalDist": score_data.get("totalDist", 0),
                "totalGain": score_data.get("totalGain", 0),
                "totalLoss": score_data.get("totalLoss", 0),
                "gainPerKm": score_data.get("gainPerKm", 0),
                "minEle": score_data.get("minEle", 0),
                "maxEle": score_data.get("maxEle", 0),
                "bands": score_data.get("bands", {}),
                "bandColors": score_data.get("bandColors", {}),
                "profile": score_data.get("profile", []),
                "created_at": datetime.utcnow().isoformat(),
            }
            _, route_ref = db.collection("routes").add(route_doc)
            route_id = route_ref.id

        # Check if user already has this route saved
        existing_link = db.collection("user_routes") \
            .where("user_id", "==", user["strava_id"]) \
            .where("route_id", "==", route_id) \
            .limit(1).get()

        if list(existing_link):
            return JSONResponse({"status": "already_saved", "route_id": route_id})

        # Create user-route link
        db.collection("user_routes").add({
            "user_id": user["strava_id"],
            "route_id": route_id,
            "display_name": score_data.get("name", "Unnamed Route"),
            "saved_at": datetime.utcnow().isoformat(),
        })

        return JSONResponse({"status": "saved", "route_id": route_id})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/routes")
async def list_routes(request: Request):
    """List the current user's saved routes."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    try:
        # Get user's route links (sort in Python to avoid requiring Firestore composite index)
        links = db.collection("user_routes") \
            .where("user_id", "==", user["strava_id"]) \
            .get()

        routes = []
        for link in links:
            link_data = link.to_dict()
            route_doc = db.collection("routes").document(link_data["route_id"]).get()
            if route_doc.exists:
                route_data = route_doc.to_dict()
                routes.append({
                    "id": link_data["route_id"],
                    "link_id": link.id,
                    "name": link_data.get("display_name", route_data.get("name", "")),
                    "date": route_data.get("date", ""),
                    "composite": route_data.get("composite", 0),
                    "descriptor": route_data.get("descriptor", ""),
                    "scoreClass": route_data.get("scoreClass", ""),
                    "totalDist": route_data.get("totalDist", 0),
                    "totalGain": route_data.get("totalGain", 0),
                    "saved_at": link_data.get("saved_at", ""),
                })

        # Sort by saved_at descending (newest first)
        routes.sort(key=lambda r: r.get("saved_at", ""), reverse=True)

        return JSONResponse({"routes": routes})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/routes/{route_id}")
async def get_route(route_id: str, request: Request):
    """Get full route data (public — for shared links)."""
    try:
        route_doc = db.collection("routes").document(route_id).get()
        if not route_doc.exists:
            return JSONResponse({"error": "Route not found"}, status_code=404)

        data = route_doc.to_dict()
        # Don't return raw GPX on public endpoint
        data.pop("gpx_raw", None)
        data.pop("gpx_hash", None)
        data["id"] = route_id
        return JSONResponse(data)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/routes/{route_id}")
async def delete_route(route_id: str, request: Request):
    """Remove a route from user's library (doesn't delete the route data)."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    try:
        links = db.collection("user_routes") \
            .where("user_id", "==", user["strava_id"]) \
            .where("route_id", "==", route_id) \
            .get()

        for link in links:
            link.reference.delete()

        return JSONResponse({"status": "deleted"})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
