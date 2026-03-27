"""Strava OAuth and session management for VertHurt."""

import os
import time
import secrets
import httpx
from jose import jwt
from fastapi import Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from google.cloud import firestore

# Config from environment
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 30 * 24 * 3600  # 30 days

# Detect environment for callback URL
BASE_URL = os.getenv("BASE_URL", "http://localhost:8181")

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"

db = firestore.Client(project="hilliness-analyzer")


def _make_jwt(user_data: dict) -> str:
    """Create a signed JWT with user info."""
    payload = {
        "sub": str(user_data["strava_id"]),
        "name": user_data.get("name", ""),
        "avatar": user_data.get("avatar", ""),
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(request: Request) -> dict | None:
    """Extract user from JWT cookie. Returns None if not authenticated."""
    token = request.cookies.get("verthurt_session")
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "strava_id": payload["sub"],
            "name": payload.get("name", ""),
            "avatar": payload.get("avatar", ""),
        }
    except Exception:
        return None


async def strava_login(request: Request):
    """Redirect to Strava OAuth authorization page."""
    # Store the return-to URL so we can resume after auth
    return_to = request.query_params.get("return_to", "/app.html")

    callback_url = f"{BASE_URL}/api/auth/strava/callback"

    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "read",
        "state": return_to,  # pass return URL through state param
    }

    url = f"{STRAVA_AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return RedirectResponse(url)


async def strava_callback(request: Request):
    """Handle Strava OAuth callback — exchange code for token, create/update user."""
    code = request.query_params.get("code")
    state = request.query_params.get("state", "/app.html")

    if not code:
        return JSONResponse({"error": "No authorization code received"}, status_code=400)

    callback_url = f"{BASE_URL}/api/auth/strava/callback"

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        })

    if resp.status_code != 200:
        return JSONResponse({"error": "Failed to exchange token", "detail": resp.text}, status_code=400)

    data = resp.json()
    athlete = data.get("athlete", {})

    strava_id = str(athlete.get("id", ""))
    if not strava_id:
        return JSONResponse({"error": "No athlete ID in response"}, status_code=400)

    user_data = {
        "strava_id": strava_id,
        "name": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "avatar": athlete.get("profile_medium", ""),
        "city": athlete.get("city", ""),
        "state": athlete.get("state", ""),
        "country": athlete.get("country", ""),
        "last_login": time.time(),
    }

    # Create or update user in Firestore
    user_ref = db.collection("users").document(strava_id)
    existing = user_ref.get()
    if existing.exists:
        user_ref.update({
            "name": user_data["name"],
            "avatar": user_data["avatar"],
            "last_login": user_data["last_login"],
        })
    else:
        user_data["created_at"] = time.time()
        user_ref.set(user_data)

    # Create JWT and set cookie
    token = _make_jwt(user_data)

    response = RedirectResponse(url=state, status_code=302)
    response.set_cookie(
        key="verthurt_session",
        value=token,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


async def get_me(request: Request):
    """Return current authenticated user or 401."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True, "user": user})


async def logout(request: Request):
    """Clear session cookie."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("verthurt_session")
    return response
