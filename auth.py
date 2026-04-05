"""Authentication for VertHurt — Magic Link (primary) + Strava OAuth (feature-flagged)."""

import os
import time
import secrets
import hashlib
import logging
import httpx

logger = logging.getLogger(__name__)
from jose import jwt
from fastapi import Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from db import db

# Config from environment
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 30 * 24 * 3600  # 30 days

# Magic link config
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
MAGIC_LINK_TTL = 15 * 60  # 15 minutes
FROM_EMAIL = os.getenv("FROM_EMAIL", "VertHurt <noreply@verthurt.com>")

# Detect environment for callback URL
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def _make_jwt(user_data: dict) -> str:
    """Create a signed JWT with user info."""
    payload = {
        "sub": str(user_data["user_id"]),
        "name": user_data.get("name", ""),
        "avatar": user_data.get("avatar", ""),
        "profile_complete": user_data.get("profile_complete", False),
        "auth_method": user_data.get("auth_method", "magic_link"),
        "is_admin": user_data.get("is_admin", False),
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
            "user_id": payload["sub"],
            "name": payload.get("name", ""),
            "avatar": payload.get("avatar", ""),
            "profile_complete": payload.get("profile_complete", False),
            "auth_method": payload.get("auth_method", "strava"),
            "is_admin": payload.get("is_admin", False),
        }
    except (jwt.JWTError, KeyError):
        return None


# ============ MAGIC LINK AUTH ============

async def send_magic_link(request: Request):
    """Send a magic link email for passwordless login."""
    # Rate limit: 3 magic link requests per IP per 5 minutes
    from server import _rate_limit
    if _rate_limit(request, "magic_link", 3, 300):
        return JSONResponse({"error": "Too many requests. Please wait a few minutes."}, status_code=429)
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()

        if not email or "@" not in email:
            return JSONResponse({"error": "Valid email is required"}, status_code=400)

        # Generate a secure token
        token = secrets.token_urlsafe(32)

        # Store in Firestore with TTL
        db.collection("magic_links").document(token).set({
            "email": email,
            "created_at": time.time(),
            "expires_at": time.time() + MAGIC_LINK_TTL,
            "used": False,
        })

        # Build magic link URL
        verify_url = f"{BASE_URL}/api/auth/verify?token={token}"

        # Send email via Resend
        if RESEND_API_KEY:
            import resend
            resend.api_key = RESEND_API_KEY
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": [email],
                "subject": "Your VertHurt login link",
                "html": f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
                    <h1 style="color: #1F2937; font-size: 24px; margin-bottom: 8px;">VertHurt</h1>
                    <p style="color: #6B7280; font-size: 16px; margin-bottom: 24px;">Click the link below to sign in. It expires in 15 minutes.</p>
                    <a href="{verify_url}" style="display: inline-block; background: #2563EB; color: white; padding: 12px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">Sign in to VertHurt</a>
                    <p style="color: #9CA3AF; font-size: 13px; margin-top: 24px;">If you didn't request this, you can safely ignore this email.</p>
                </div>
                """,
            })
        else:
            # Local dev fallback — print link to console
            print(f"\n{'='*60}")
            print(f"MAGIC LINK (no Resend API key configured):")
            print(f"{verify_url}")
            print(f"{'='*60}\n")

        return JSONResponse({"status": "sent", "message": "Check your email for a login link."})

    except Exception as e:
        print(f"Magic link error: {e}")
        return JSONResponse({"error": "Failed to send login link. Please try again."}, status_code=500)


async def verify_magic_link(request: Request):
    """Verify a magic link token and log the user in."""
    token = request.query_params.get("token", "")

    if not token:
        return JSONResponse({"error": "Missing token"}, status_code=400)

    # Look up token in Firestore
    token_ref = db.collection("magic_links").document(token)
    token_doc = token_ref.get()

    if not token_doc.exists:
        return _magic_link_error("This link is invalid or has expired.")

    token_data = token_doc.to_dict()

    # Check expiry
    if time.time() > token_data.get("expires_at", 0):
        token_ref.delete()
        return _magic_link_error("This link has expired. Please request a new one.")

    # Check if already used
    if token_data.get("used"):
        return _magic_link_error("This link has already been used.")

    # Mark as used
    token_ref.update({"used": True})

    email = token_data["email"]

    # Create or update user in Firestore (keyed by email hash for safe doc IDs)
    user_doc_id = _email_to_doc_id(email)
    user_ref = db.collection("users").document(user_doc_id)
    existing = user_ref.get()

    if existing.exists:
        fs_data = existing.to_dict()
        # Collision guard: verify stored email matches the authenticating email
        if fs_data.get("email", "").lower() != email.lower():
            logger.error(
                "Doc ID collision: existing email=%s, new email=%s, doc_id=%s",
                fs_data.get("email"), email, user_doc_id
            )
            return _magic_link_error(
                "Unable to create your account due to a system conflict. "
                "Please contact support."
            )
        user_ref.update({"last_login": time.time()})
        user_data = {
            "user_id": user_doc_id,
            "email": email,
            "name": fs_data.get("name", ""),
            "avatar": "",
            "profile_complete": fs_data.get("profile_complete", False),
            "auth_method": "magic_link",
            "is_admin": fs_data.get("is_admin", False),
        }
    else:
        user_data = {
            "user_id": user_doc_id,
            "email": email,
            "name": "",
            "avatar": "",
            "profile_complete": False,
            "auth_method": "magic_link",
            "created_at": time.time(),
            "last_login": time.time(),
        }
        user_ref.set(user_data)

    # Create JWT and set cookie
    jwt_token = _make_jwt(user_data)

    response = RedirectResponse(url="/app.html", status_code=302)
    response.set_cookie(
        key="verthurt_session",
        value=jwt_token,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


def _email_to_doc_id(email: str) -> str:
    """Convert email to a safe Firestore document ID."""
    # Use a short hash prefix + sanitized email for readability
    return email.replace("@", "_at_").replace(".", "_")


def _magic_link_error(message: str):
    """Return a user-friendly error page for magic link issues."""
    import html as html_mod
    safe_message = html_mod.escape(message)
    html = f"""
    <html>
    <head><title>VertHurt — Link Error</title></head>
    <body style="font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #F8F9FA;">
        <div style="text-align: center; max-width: 400px; padding: 40px;">
            <h2 style="color: #1F2937;">Oops!</h2>
            <p style="color: #6B7280; margin-bottom: 24px;">{safe_message}</p>
            <a href="/app.html" style="display: inline-block; background: #2563EB; color: white; padding: 10px 24px; border-radius: 8px; text-decoration: none;">Back to VertHurt</a>
        </div>
    </body>
    </html>
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


# ============ STRAVA AUTH (kept for future feature flag) ============

async def strava_login(request: Request):
    """Redirect to Strava OAuth authorization page."""
    return_to = request.query_params.get("return_to", "/app.html")
    callback_url = f"{BASE_URL}/api/auth/strava/callback"

    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "read",
        "state": return_to,
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
        "user_id": strava_id,
        "name": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "avatar": athlete.get("profile_medium", ""),
        "city": athlete.get("city", ""),
        "state": athlete.get("state", ""),
        "country": athlete.get("country", ""),
        "auth_method": "strava",
        "last_login": time.time(),
    }

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


# ============ SHARED ENDPOINTS ============

async def get_me(request: Request):
    """Return current authenticated user or 401."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)

    user_ref = db.collection("users").document(user["user_id"]).get()
    if user_ref.exists:
        fs_data = user_ref.to_dict()
        user["first_name"] = fs_data.get("first_name", "")
        user["last_name"] = fs_data.get("last_name", "")
        user["email"] = fs_data.get("email", "")
        user["is_admin"] = fs_data.get("is_admin", False)
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

    user_ref = db.collection("users").document(user["user_id"])
    user_ref.update({
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "profile_complete": True,
    })

    updated_user = {
        "user_id": user["user_id"],
        "name": name,
        "avatar": user.get("avatar", ""),
        "profile_complete": True,
        "auth_method": user.get("auth_method", "magic_link"),
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

    user_id = user["user_id"]

    links = db.collection("user_routes").where("user_id", "==", user_id).get()
    for link in links:
        link.reference.delete()

    db.collection("users").document(user_id).delete()

    response = JSONResponse({"status": "deleted"})
    response.delete_cookie("verthurt_session")
    return response


async def logout(request: Request):
    """Clear session cookie."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("verthurt_session")
    return response
