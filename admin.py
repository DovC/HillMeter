"""Admin API endpoints for VertHurt."""

import time
from datetime import datetime, timezone, timedelta
import gzip
import base64
import binascii
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from db import db
from auth import get_current_user
from scoring import compute_score, ALGO_VERSION

# Bootstrap: To create the first admin, set is_admin=True in Firestore console:
# db.collection("users").document("<user_doc_id>").update({"is_admin": True})
# Doc ID is email with @ and . replaced: e.g., "dov_at_tarheelabs_com"


import math

def _sort_key(item: dict, field: str):
    """Return a sortable key — handles numbers, strings, and missing values."""
    val = item.get(field)
    if val is None:
        return ""
    return val


def _sanitize(data):
    """Replace inf/nan float values with None for JSON serialization, recursively."""
    if isinstance(data, dict):
        return {k: _sanitize(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize(v) for v in data]
    elif isinstance(data, float) and (math.isinf(data) or math.isnan(data)):
        return None
    return data


async def _require_admin(request: Request) -> dict | None:
    """Verify user is authenticated AND is_admin in Firestore. Returns user or None."""
    user = get_current_user(request)
    if not user:
        return None

    # Always re-check Firestore (not just JWT) for security
    user_doc = db.collection("users").document(user["user_id"]).get()
    if not user_doc.exists or not user_doc.to_dict().get("is_admin", False):
        return None

    return user


# ============ DASHBOARD ============

async def admin_stats(request: Request):
    """Dashboard summary stats."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    now = time.time()
    seven_days_ago = now - 7 * 86400
    thirty_days_ago = now - 30 * 86400

    # Use aggregation queries to count without fetching documents
    total_users = db.collection("users").count().get()[0][0].value
    new_7d = db.collection("users").where("created_at", ">=", seven_days_ago).count().get()[0][0].value
    new_30d = db.collection("users").where("created_at", ">=", thirty_days_ago).count().get()[0][0].value
    total_routes = db.collection("routes").count().get()[0][0].value
    total_scored = db.collection("scored_routes").count().get()[0][0].value
    total_waitlist = db.collection("waitlist").count().get()[0][0].value

    # Count routes missing algo_version or on an older version
    stale_routes = sum(
        1 for doc in db.collection("routes").select(["algo_version"]).stream()
        if doc.to_dict().get("algo_version") != ALGO_VERSION
    )

    return JSONResponse({
        "total_users": total_users,
        "new_users_7d": new_7d,
        "new_users_30d": new_30d,
        "total_routes": total_routes,
        "total_scored_routes": total_scored,
        "total_waitlist": total_waitlist,
        "stale_routes": stale_routes,
        "current_algo_version": ALGO_VERSION,
    })


# ============ USER MANAGEMENT ============

async def admin_list_users(request: Request):
    """List all users with search, sort, pagination."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    q = request.query_params.get("q", "").lower()
    sort_field = request.query_params.get("sort", "created_at")
    order = request.query_params.get("order", "desc")
    page = int(request.query_params.get("page", 1))
    per_page = int(request.query_params.get("per_page", 25))

    # Fetch all users
    users = []
    for doc in db.collection("users").stream():
        data = doc.to_dict()
        data["doc_id"] = doc.id
        users.append(data)

    # Search filter
    if q:
        users = [u for u in users if
                 q in u.get("email", "").lower() or
                 q in u.get("name", "").lower() or
                 q in u.get("first_name", "").lower() or
                 q in u.get("last_name", "").lower()]

    # Sort
    reverse = order == "desc"
    users.sort(key=lambda u: _sort_key(u, sort_field), reverse=reverse)

    # Paginate
    total = len(users)
    start = (page - 1) * per_page
    users = users[start:start + per_page]

    # Clean up for response
    result = []
    for u in users:
        result.append({
            "doc_id": u.get("doc_id", ""),
            "email": u.get("email", ""),
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
            "name": u.get("name", ""),
            "auth_method": u.get("auth_method", ""),
            "is_admin": u.get("is_admin", False),
            "profile_complete": u.get("profile_complete", False),
            "created_at": u.get("created_at", 0),
            "last_login": u.get("last_login", 0),
        })

    return JSONResponse({
        "users": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


async def admin_get_user(request: Request):
    """Get single user detail."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    user_id = request.path_params["user_id"]
    doc = db.collection("users").document(user_id).get()

    if not doc.exists:
        return JSONResponse({"error": "User not found"}, status_code=404)

    data = doc.to_dict()
    data["doc_id"] = doc.id

    # Get route count for this user
    route_links = list(db.collection("user_routes").where("user_id", "==", user_id).stream())
    data["route_count"] = len(route_links)

    return JSONResponse(data)


async def admin_update_user(request: Request):
    """Update user fields (name, email, is_admin)."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    user_id = request.path_params["user_id"]
    body = await request.json()

    # Self-demotion protection
    if user_id == admin["user_id"] and "is_admin" in body and not body["is_admin"]:
        return JSONResponse({"error": "Cannot remove your own admin access"}, status_code=400)

    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if not doc.exists:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Only update allowed fields
    allowed = {"first_name", "last_name", "email", "is_admin"}
    updates = {k: v for k, v in body.items() if k in allowed}

    if "first_name" in updates or "last_name" in updates:
        existing = doc.to_dict()
        fn = updates.get("first_name", existing.get("first_name", ""))
        ln = updates.get("last_name", existing.get("last_name", ""))
        updates["name"] = f"{fn} {ln}".strip()

    if updates:
        user_ref.update(updates)

    return JSONResponse({"status": "updated", "fields": list(updates.keys())})


async def admin_delete_user(request: Request):
    """Delete user and their route links. Cannot delete yourself."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    user_id = request.path_params["user_id"]

    if user_id == admin["user_id"]:
        return JSONResponse({"error": "Cannot delete your own account from admin"}, status_code=400)

    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Delete user_routes links
    links = db.collection("user_routes").where("user_id", "==", user_id).stream()
    for link in links:
        link.reference.delete()

    # Delete user doc
    db.collection("users").document(user_id).delete()

    return JSONResponse({"status": "deleted"})


# ============ ROUTE MANAGEMENT ============

async def _list_collection(request: Request, collection_name: str, default_sort: str):
    """Generic paginated, searchable, sortable listing for route-like collections."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    q = request.query_params.get("q", "").lower()
    sort_field = request.query_params.get("sort", default_sort)
    order = request.query_params.get("order", "desc")
    page = int(request.query_params.get("page", 1))
    per_page = int(request.query_params.get("per_page", 25))

    # Fetch only needed fields — skip large blobs (gpx_compressed, gpx_raw, profile)
    select_fields = [
        "name", "date", "composite", "descriptor", "fingerprint", "gpx_hash",
        "scored_at", "created_at",
        "scoreClass", "densityScore", "intensityScore", "continuityScore",
        "totalDist", "totalGain", "totalLoss", "gainPerKm",
        "minEle", "maxEle", "bands", "bandColors",
    ]
    routes = []
    for doc in db.collection(collection_name).select(select_fields).stream():
        data = _sanitize(doc.to_dict())
        data["doc_id"] = doc.id
        routes.append(data)

    if q:
        routes = [r for r in routes if q in r.get("name", "").lower()]

    reverse = order == "desc"
    routes.sort(key=lambda r: _sort_key(r, sort_field), reverse=reverse)

    total = len(routes)
    start = (page - 1) * per_page
    routes = routes[start:start + per_page]

    return JSONResponse({
        "routes": routes,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


async def admin_list_routes(request: Request):
    """List all routes with search, sort, pagination."""
    return await _list_collection(request, "routes", "created_at")


async def admin_list_scored_routes(request: Request):
    """List all anonymously scored routes."""
    return await _list_collection(request, "scored_routes", "scored_at")


# ============ GPX DOWNLOAD ============

async def admin_download_gpx(request: Request):
    """Download GPX file for a route."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    collection = request.query_params.get("collection", "routes")
    doc_id = request.path_params["doc_id"]

    if collection not in ("routes", "scored_routes"):
        return JSONResponse({"error": "Invalid collection"}, status_code=400)

    doc = db.collection(collection).document(doc_id).get()
    if not doc.exists:
        return JSONResponse({"error": "Not found"}, status_code=404)

    data = doc.to_dict()
    gpx_xml = None

    if data.get("gpx_compressed"):
        try:
            gpx_xml = gzip.decompress(base64.b64decode(data["gpx_compressed"])).decode()
        except (gzip.BadGzipFile, binascii.Error, UnicodeDecodeError):
            pass

    if not gpx_xml and data.get("gpx_raw"):
        gpx_xml = data["gpx_raw"]

    if not gpx_xml:
        return JSONResponse({"error": "No GPX data available"}, status_code=404)

    name = data.get("name", "route").replace(" ", "_")
    return Response(
        content=gpx_xml,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{name}.gpx"'},
    )


# ============ BATCH RESCORE ============

async def admin_batch_rescore(request: Request):
    """Rescore all saved routes that are on a stale algorithm version."""
    admin = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    rescored = 0
    errors = 0

    for doc in db.collection("routes").stream():
        data = doc.to_dict()
        if data.get("algo_version") == ALGO_VERSION:
            continue  # Already current

        gpx_xml = None
        if data.get("gpx_compressed"):
            try:
                gpx_xml = gzip.decompress(base64.b64decode(data["gpx_compressed"])).decode()
            except (gzip.BadGzipFile, binascii.Error, UnicodeDecodeError):
                pass
        if not gpx_xml and data.get("gpx_raw"):
            gpx_xml = data["gpx_raw"]

        if not gpx_xml:
            errors += 1
            continue

        try:
            result = compute_score(gpx_xml, name=data.get("name"))
            d = result.to_dict()
            doc.reference.update({
                "composite":       d["composite"],
                "descriptor":      d["descriptor"],
                "scoreClass":      d["scoreClass"],
                "densityScore":    d["densityScore"],
                "intensityScore":  d["intensityScore"],
                "continuityScore": d["continuityScore"],
                "totalDist":       d["totalDist"],
                "totalGain":       d["totalGain"],
                "totalLoss":       d["totalLoss"],
                "gainPerKm":       d["gainPerKm"],
                "minEle":          d["minEle"],
                "maxEle":          d["maxEle"],
                "bands":           d["bands"],
                "bandColors":      d["bandColors"],
                "profile":         d["profile"],
                "algo_version":    ALGO_VERSION,
            })
            rescored += 1
        except Exception:
            errors += 1

    return JSONResponse({
        "rescored": rescored,
        "errors": errors,
        "algo_version": ALGO_VERSION,
    })
