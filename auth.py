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
        "profile_complete": user_data.get("profile_complete", False),
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
            "profile_complete": payload.get("profile_complete", False),
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
        user_data["profile_complete"] = existing.to_dict().get("profile_complete", False)
    else:
        user_data["created_at"] = time.time()
        user_data["profile_complete"] = False
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

    # Fetch first/last name from Firestore for profile form
    user_ref = db.collection("users").document(user["strava_id"]).get()
    if user_ref.exists:
        fs_data = user_ref.to_dict()
        user["first_name"] = fs_data.get("first_name", "")
        user["last_name"] = fs_data.get("last_name", "")
        user["email"] = fs_data.get("email", "")
        # If no first/last stored yet, split from full name
        if not user["first_name"] and user.get("name"):
            parts = user["name"].split(" ", 1)
            user["first_name"] = parts[0]
            user["last_name"] = parts[1] if len(parts) > 1 else ""

    return JSONResponse({"authenticated": True, "user": user})


async def update_profile(request: Request):
    """Update user profile (name, email) and mark profile as complete."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    data = await request.json()
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    email = data.get("email", "").strip().lower()

    if not email or "@" not in email:
        return JSONResponse({"error": "Valid email is required"}, status_code=400)
    if not first_name:
        return JSONResponse({"error": "First name is required"}, status_code=400)

    name = f"{first_name} {last_name}".strip()

    # Update Firestore
    user_ref = db.collection("users").document(user["strava_id"])
    user_ref.update({
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "profile_complete": True,
    })

    # Re-issue JWT with updated info
    updated_user = {
        "strava_id": user["strava_id"],
        "name": name,
        "avatar": user.get("avatar", ""),
        "profile_complete": True,
    }
    token = _make_jwt(updated_user)

    response = JSONResponse({"status": "ok", "user": updated_user})
    response.set_cookie(
        key="verthurt_session",
        value=token,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


async def delete_account(request: Request):
    """Delete user account, user_routes links, but NOT the route data itself."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    strava_id = user["strava_id"]

    # Delete all user_routes links for this user
    links = db.collection("user_routes").where("user_id", "==", strava_id).get()
    for link in links:
        link.reference.delete()

    # Delete the user document
    db.collection("users").document(strava_id).delete()

    # Clear session
    response = JSONResponse({"status": "deleted"})
    response.delete_cookie("verthurt_session")
    return response


async def logout(request: Request):
    """Clear session cookie."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("verthurt_session")
    return response
