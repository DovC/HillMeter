from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from google.cloud import firestore
from datetime import datetime
import httpx
import os

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

@app.get("/api/waitlist/count")
async def waitlist_count():
    """Quick count for social proof (no PII exposed)."""
    docs = db.collection("waitlist").get()
    return JSONResponse({"count": len(docs)})

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
