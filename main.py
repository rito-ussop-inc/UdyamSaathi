from fastapi import FastAPI, UploadFile, File, Header, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import speech_recognition as sr
import shutil
import os
import json
import random
import re
import sqlite3
import hashlib
import hmac
import secrets
import jwt
import math
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from finance import analyze_dairy_plan

app = FastAPI(title="Saathi 26091 Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Secret key for JWT generation (32+ bytes to meet RFC 7518 recommendation)
SECRET_KEY = "saathi-super-secret-jwt-key-26091-secure-token-32b"
ALGORITHM = "HS256"

# ---------- SQLite user database ----------
DB_FILE = "users.db"


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            username TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            username TEXT PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            locality TEXT,
            district TEXT,
            state TEXT,
            country TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def hash_password(password: str, salt: str = None) -> tuple:
    if salt is None:
        salt = secrets.token_hex(16)
    hash_val = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()
    return hash_val, salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, expected_hash)


def find_user_by_username(username: str):
    conn = get_db()
    u = username.strip()
    row = conn.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
        (u, u)
    ).fetchone()
    conn.close()
    return row


def find_user_by_email(email: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip().lower(),)).fetchone()
    conn.close()
    return row


def user_to_dict(row):
    return {
        "name": row["name"],
        "username": row["username"],
        "email": row["email"],
    }


init_db()

# Seed the demo user (kept so the sample creds still work)
if find_user_by_username("arjun") is None:
    conn = get_db()
    pass_hash, salt = hash_password("password123")
    conn.execute(
        "INSERT INTO users (name, username, email, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
        ("Arjun", "arjun", "arjun@example.com", pass_hash, salt),
    )
    conn.commit()
    conn.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    name: str
    username: str
    email: str
    password: str


def make_token(username: str) -> str:
    return jwt.encode({"sub": username}, SECRET_KEY, algorithm=ALGORITHM)


@app.post("/api/login")
def login(req: LoginRequest):
    user = find_user_by_username(req.username.strip())
    if user and verify_password(req.password, user["salt"], user["password_hash"]):
        token = make_token(user["username"])
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": user_to_dict(user),
        }

    return JSONResponse(status_code=401, content={"error": "Invalid username or password"})


@app.post("/api/register")
def register(req: RegisterRequest):
    name = req.name.strip()
    username = req.username.strip()
    email = req.email.strip().lower()
    password = req.password

    if not name:
        return JSONResponse(status_code=400, content={"error": "Name is required"})
    if len(username) < 3:
        return JSONResponse(status_code=400, content={"error": "Username must be at least 3 characters"})
    if "@" not in email or "." not in email:
        return JSONResponse(status_code=400, content={"error": "Enter a valid email address"})
    if len(password) < 6:
        return JSONResponse(status_code=400, content={"error": "Password must be at least 6 characters"})

    if find_user_by_username(username):
        return JSONResponse(status_code=409, content={"error": "Username already taken"})
    if find_user_by_email(email):
        return JSONResponse(status_code=409, content={"error": "Email already registered"})

    pass_hash, salt = hash_password(password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (name, username, email, password_hash, salt) VALUES (?, ?, ?, ?, ?)",
            (name, username, email, pass_hash, salt),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return JSONResponse(status_code=409, content={"error": "Username or email already taken"})
    conn.close()

    token = make_token(username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"name": name, "username": username, "email": email},
    }

class DairyPlan(BaseModel):
    cows: int = 3
    capital: float = 100000
    existing_shed: bool = False
    fodder_land: bool = True
    family_labour: bool = True
    distributor: bool = True

# ---------- Per-user plan persistence ----------
def get_auth_username(authorization: str = None):
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None


DEFAULT_PLAN = {
    "cows": 3,
    "capital": 100000,
    "existing_shed": False,
    "fodder_land": True,
    "family_labour": True,
    "distributor": True,
}


@app.get("/api/plan")
def get_plan(authorization: str = Header(None)):
    username = get_auth_username(authorization)
    if not username:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    conn = get_db()
    row = conn.execute("SELECT data FROM plans WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    conn.close()

    if row:
        try:
            return {"plan": json.loads(row["data"])}
        except Exception:
            return {"plan": DEFAULT_PLAN}
    return {"plan": DEFAULT_PLAN}


class PlanData(BaseModel):
    cows: int = 3
    capital: float = 100000
    existing_shed: bool = False
    fodder_land: bool = True
    family_labour: bool = True
    distributor: bool = True
    lang: str = "en"


@app.post("/api/plan")
def save_plan(req: PlanData, authorization: str = Header(None)):
    username = get_auth_username(authorization)
    if not username:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    data = req.model_dump()
    conn = get_db()
    u_row = conn.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    canonical_user = u_row["username"] if u_row else username

    conn.execute(
        """INSERT INTO plans (username, data, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(username) DO UPDATE SET
             data = excluded.data,
             updated_at = excluded.updated_at""",
        (canonical_user, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    return {"status": "saved", "plan": data}

# ---------- Location & nearby dairy ecosystem ----------
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
OVERPASS_API = "https://overpass-api.de/api/interpreter"
# Public mirrors, raced in parallel. Note: overpass-api.de often times out
# from some Indian networks, so we fail fast (6s) and fall back to Nominatim
# city search which is reliable here.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "http://overpass-api.de/api/interpreter",
]
APP_USER_AGENT = "UdyamSaathi/1.0 (SIH26091 prototype; contact: local-demo)"


_SSL_CTX = None


def _get_ssl_ctx():
    """Lazy-create an SSL context that tolerates expired/invalid certs on public Overpass mirrors."""
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        _SSL_CTX = ssl.create_default_context()
        _SSL_CTX.check_hostname = False
        _SSL_CTX.verify_mode = ssl.CERT_NONE
    return _SSL_CTX


def http_get_json(url, headers=None, timeout=15):
    """Thin stdlib helper for JSON HTTP GETs (no external dependencies).

    Uses a relaxed SSL context so expired certificates on public Overpass
    mirrors don't cause failures.
    """
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def reverse_geocode(lat, lng):
    """Convert coordinates to locality/district/state/country via Nominatim (OpenStreetMap)."""
    params = {"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 16, "addressdetails": 1}
    qs = urllib.parse.urlencode(params)
    try:
        data = http_get_json(f"{NOMINATIM_URL}?{qs}", headers={"User-Agent": APP_USER_AGENT})
    except Exception:
        return None

    if not data:
        return None

    addr = data.get("address", {}) or {}
    locality = (
        addr.get("village") or addr.get("town") or addr.get("city_district")
        or addr.get("city") or addr.get("suburb") or addr.get("hamlet")
        or addr.get("locality") or addr.get("municipality") or ""
    )
    district = addr.get("county") or addr.get("district") or addr.get("state_district") or ""
    state = addr.get("state") or addr.get("province") or ""
    country = addr.get("country") or ""

    return {
        "latitude": float(lat),
        "longitude": float(lng),
        "locality": locality,
        "district": district,
        "state": state,
        "country": country,
    }


GEOCODE_SEARCH_URL = "https://nominatim.openstreetmap.org/search"


def geocode_search(query: str, limit: int = 6):
    """Search for places by name via Nominatim and return lightweight matches."""
    query = (query or "").strip()
    if not query:
        return []
    params = {
        "format": "jsonv2",
        "q": query,
        "limit": str(max(1, min(int(limit), 10))),
        "addressdetails": 1,
    }
    qs = urllib.parse.urlencode(params)
    try:
        data = http_get_json(f"{GEOCODE_SEARCH_URL}?{qs}", headers={"User-Agent": APP_USER_AGENT}, timeout=12)
    except Exception:
        return []

    out = []
    for r in (data or []):
        lat = r.get("lat")
        lon = r.get("lon")
        if lat is None or lon is None:
            continue
        addr = r.get("address", {}) or {}
        # Build a nice display label from the returned display_name.
        display = r.get("display_name") or (r.get("name") or "")
        # Short label: name + state/country for cleaner dropdown rows.
        name = r.get("name") or ""
        state = addr.get("state") or addr.get("province") or ""
        country = addr.get("country") or ""
        short = (name + (", " + state if state else "") + (", " + country if not state and country else "")).strip(", ")
        out.append({
            "label": display,
            "short": short or display,
            "lat": float(lat),
            "lon": float(lon),
            "locality": (addr.get("village") or addr.get("town") or addr.get("city_district")
                         or addr.get("city") or addr.get("suburb") or addr.get("hamlet")
                         or addr.get("locality") or addr.get("municipality") or name or ""),
            "district": addr.get("county") or addr.get("district") or addr.get("state_district") or "",
            "state": state,
            "country": country,
        })
    return out


@app.get("/api/geocode")
def geocode_endpoint(q: str, limit: int = 6):
    """Forward a place-name search to Nominatim for the location picker."""
    return {"results": geocode_search(q, limit)}


class LocationData(BaseModel):
    latitude: float
    longitude: float


@app.post("/api/location")
def save_location(req: LocationData, authorization: str = Header(None)):
    username = get_auth_username(authorization)
    if not username:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    geocoded = reverse_geocode(req.latitude, req.longitude)
    if geocoded is None:
        # Reverse geocoding failed; still store the raw coordinates.
        geocoded = {
            "latitude": req.latitude,
            "longitude": req.longitude,
            "locality": "",
            "district": "",
            "state": "",
            "country": "",
        }
        geocoded_ok = False
    else:
        geocoded_ok = True

    conn = get_db()
    u_row = conn.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    canonical_user = u_row["username"] if u_row else username

    conn.execute(
        """INSERT INTO locations
           (username, latitude, longitude, locality, district, state, country, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(username) DO UPDATE SET
             latitude = excluded.latitude,
             longitude = excluded.longitude,
             locality = excluded.locality,
             district = excluded.district,
             state = excluded.state,
             country = excluded.country,
             updated_at = excluded.updated_at""",
        (
            canonical_user,
            geocoded["latitude"],
            geocoded["longitude"],
            geocoded["locality"],
            geocoded["district"],
            geocoded["state"],
            geocoded["country"],
        ),
    )
    conn.commit()
    conn.close()

    return {"status": "saved", "geocoded": geocoded_ok, "location": geocoded}


@app.get("/api/location")
def get_location(authorization: str = Header(None)):
    username = get_auth_username(authorization)
    if not username:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    conn = get_db()
    row = conn.execute(
        "SELECT latitude, longitude, locality, district, state, country, updated_at "
        "FROM locations WHERE LOWER(username) = LOWER(?)",
        (username,),
    ).fetchone()
    conn.close()

    if not row:
        return JSONResponse(status_code=404, content={"error": "No location saved for this user"})

    return {
        "location": {
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "locality": row["locality"],
            "district": row["district"],
            "state": row["state"],
            "country": row["country"],
        }
    }


def haversine_km(lat1, lon1, lat2, lon2):
    """Approximate great-circle distance in kilometres between two coordinates."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Nearby POI categories for the Dairy business.
DAIRY_POI_CATEGORIES = [
    "Milk Collection Centre",
    "Dairy Farm",
    "Dairy Market",
    "Feed / Fodder Supplier",
    "Veterinary Service",
    "Other Dairy Business",
]


def _safe_tag(v):
    return v if v else ""


def _poi_address(tags):
    """Build a short human-friendly address string from OSM addr:* tags (best-effort)."""
    a = tags.get("addr:street") or ""
    house = tags.get("addr:housenumber") or ""
    place = (tags.get("addr:village") or tags.get("addr:city") or tags.get("addr:town")
             or tags.get("addr:suburb") or tags.get("is_in") or "")
    parts = []
    if a:
        parts.append((house + " " + a).strip())
    if place:
        parts.append(place)
    return ", ".join(parts)


def _classify_poi(tags):
    """Map OSM tags to a friendly Dairy category label (best-effort, based on real OSM tags)."""
    amenity = tags.get("amenity")
    shop = tags.get("shop")
    man_made = tags.get("man_made")
    landuse = tags.get("landuse")
    healthcare = tags.get("healthcare")
    building = tags.get("building")

    if amenity == "veterinary" or healthcare == "veterinary":
        return "Veterinary Service"
    room = shop or amenity or ""
    if room and re.search(r"dairy|creamery|milk|cheese|butter|ice[_ -]?cream", room, re.I):
        return "Milk Collection Centre" if re.search(r"dairy|creamery|milk", room, re.I) else "Other Dairy Business"
    if (shop in ("agrarian", "agrarian_shop", "farming_equipment", "agricultural_supplies",
                 "animal_feed", "fodder", "feed", "grain", "country_store", "agriculture")
            or amenity in ("animal_feed", "feed")):
        return "Feed / Fodder Supplier"
    if amenity == "marketplace" or shop == "farm":
        return "Dairy Market"
    if shop == "dairy_farm" or landuse == "farmyard" or building == "farm":
        return "Dairy Farm"
    if landuse == "greenhouse_horticulture":
        return "Feed / Fodder Supplier"
    # Only label something a Dairy business when the OSM tags explicitly say it is one.
    # Returning None means "not dairy-relevant" and the object is dropped, so unrelated
    # businesses (restaurants, sweets/hotel, generic grocery, poultry, etc.) never appear.
    if shop in ("cheese", "butcher", "wholesale") or amenity in ("milk", "butchery", "dairy"):
        return "Other Dairy Business"
    if re.search(r"dairy|creamery|milk|cheese|butter|ice[_ -]?cream", room or "", re.I):
        return "Other Dairy Business"
    return None


# -- category -> Overpass query fragment. Only Dairy categories are wired up for now.
# Queries are intentionally a bit broad to catch real POIs in rural/semi-urban OSM.
# We avoid over-restrictive name filters on the common POI tags so unnamed places still match.
DAIRY_QUERIES = [
    # Milk buyers / collection centres (dairy shops, creameries, milk retailers, pure-milk booths)
    {
        "category": "Milk Collection Centre",
        "query": (
            "(node[\"shop\"~\"dairy|creamery|milk|cheese\"](around:{R},{LAT},{LON});"
            "way[\"shop\"~\"dairy|creamery|milk|cheese\"](around:{R},{LAT},{LON});"
            "node[\"amenity\"~\"dairy|milk\"](around:{R},{LAT},{LON});"
            "way[\"amenity\"~\"dairy|milk\"](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
    # Dairy farms (dairy_farm shops, farmyards, farm buildings)
    {
        "category": "Dairy Farm",
        "query": (
            "(node[\"shop\"=\"dairy_farm\"](around:{R},{LAT},{LON});"
            "node[\"landuse\"=\"farmyard\"](around:{R},{LAT},{LON});"
            "way[\"landuse\"=\"farmyard\"](around:{R},{LAT},{LON});"
            "node[\"building\"=\"farm\"](around:{R},{LAT},{LON});"
            "way[\"building\"=\"farm\"](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
    # Dairy-related markets (marketplaces, farm shops)
    {
        "category": "Dairy Market",
        "query": (
            "(node[\"amenity\"=\"marketplace\"](around:{R},{LAT},{LON});"
            "way[\"amenity\"=\"marketplace\"](around:{R},{LAT},{LON});"
            "node[\"shop\"=\"farm\"](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
    # Feed / fodder suppliers (agri & animal-feed shops, grain/country stores, horticulture)
    {
        "category": "Feed / Fodder Supplier",
        "query": (
            "(node[\"shop\"~\"agrarian|agrarian_shop|farming_equipment|agricultural_supplies|animal_feed|fodder|feed|grain|country_store|agriculture\"](around:{R},{LAT},{LON});"
            "way[\"shop\"~\"agrarian|agrarian_shop|farming_equipment|agricultural_supplies|animal_feed|fodder|feed|grain|country_store|agriculture\"](around:{R},{LAT},{LON});"
            "node[\"amenity\"~\"animal_feed|feed\"](around:{R},{LAT},{LON});"
            "way[\"landuse\"=\"greenhouse_horticulture\"](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
    # Veterinary services
    {
        "category": "Veterinary Service",
        "query": (
            "(node[\"amenity\"=\"veterinary\"](around:{R},{LAT},{LON});"
            "way[\"amenity\"=\"veterinary\"](around:{R},{LAT},{LON});"
            "node[\"healthcare\"=\"veterinary\"](around:{R},{LAT},{LON});"
            "way[\"healthcare\"=\"veterinary\"](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
    # Other relevant dairy / milk businesses (retail, processing, butchers, dairy creameries)
    {
        "category": "Other Dairy Business",
        "query": (
            "(node[\"shop\"~\"cheese|ice_cream|butcher|wholesale\"](around:{R},{LAT},{LON});"
            "way[\"shop\"~\"cheese|ice_cream|butcher|wholesale\"](around:{R},{LAT},{LON});"
            "node[\"amenity\"=\"butchery\"](around:{R},{LAT},{LON});"
            "node[\"man_made\"=\"works\"][\"name\"~\"dairy|milk|cream|cheese\",i](around:{R},{LAT},{LON});"
            "way[\"man_made\"=\"works\"][\"name\"~\"dairy|milk|cream|cheese\",i](around:{R},{LAT},{LON}););out center 25;"
        ),
    },
]


def _race_fetch(urls, timeout, post_data=None):
    """Hit all mirrors concurrently; return the first response within the hard timeout."""
    ex = ThreadPoolExecutor(max_workers=max(2, len(urls)))
    futs = [
        ex.submit(http_get_json, url, {"User-Agent": APP_USER_AGENT}, timeout)
        for url in urls
    ]
    try:
        for fut in as_completed(futs, timeout=timeout):
            try:
                return fut.result(timeout=1)
            except Exception:
                # failed mirror; try the next one that finishes
                continue
        return None
    except Exception:
        return None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _overpass_post(query_str, timeout=6):
    """POST query to the primary Overpass endpoint. Fails fast so UI never hangs."""
    url = OVERPASS_MIRRORS[0]
    data = urllib.parse.urlencode({"data": query_str}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": APP_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _query_category(spec, clat, clon, radius):
    """Legacy per-category query (kept for compat). Prefer _fetch_overpass_combined."""
    q = spec["query"].format(R=radius, LAT=clat, LON=clon)
    overpass_q = f"[out:json][timeout:10];{q}"

    # Try POST first (separate rate-limit bucket, more reliable)
    data = _overpass_post(overpass_q)
    if data is None:
        # Fallback to GET race across mirrors
        urls = [
            m + "?" + urllib.parse.urlencode({"data": overpass_q})
            for m in OVERPASS_MIRRORS
        ]
        data = _race_fetch(urls, 8)
    if data is None:
        return spec["category"], None

    found = []
    for el in data.get("elements", []):
        if "lat" not in el or "lon" not in el:
            continue
        lat2, lon2 = el["lat"], el["lon"]
        dist = round(haversine_km(clat, clon, lat2, lon2), 1)
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("operator") or ""
        kind = _classify_poi(tags)
        if not kind:
            # Object matched a query tag pattern but is not actually dairy-relevant.
            continue
        found.append({
            "name": name,
            "lat": lat2,
            "lon": lon2,
            "type": kind,
            "category": spec["category"],
            "distance_km": dist,
            "address": _poi_address(tags),
        })
    return spec["category"], found


# ---------- Single-query Overpass + cache (fixes rate-limit / slow map) ----------
_NEARBY_CACHE = {}
_NEARBY_CACHE_TTL = 600  # seconds


def _nearby_cache_key(clat, clon, radius):
    # Round to ~500m grid so nearby map clicks reuse the same upstream result.
    return (round(float(clat), 3), round(float(clon), 3), int(radius))


def _build_combined_overpass_query(clat, clon, radius):
    inner = (
        f'node["shop"~"dairy|creamery|milk|cheese"](around:{radius},{clat},{clon});'
        f'way["shop"~"dairy|creamery|milk|cheese"](around:{radius},{clat},{clon});'
        f'node["amenity"~"dairy|milk"](around:{radius},{clat},{clon});'
        f'way["amenity"~"dairy|milk"](around:{radius},{clat},{clon});'
        f'node["shop"="dairy_farm"](around:{radius},{clat},{clon});'
        f'node["landuse"="farmyard"](around:{radius},{clat},{clon});'
        f'way["landuse"="farmyard"](around:{radius},{clat},{clon});'
        f'node["amenity"="marketplace"](around:{radius},{clat},{clon});'
        f'way["amenity"="marketplace"](around:{radius},{clat},{clon});'
        f'node["shop"="farm"](around:{radius},{clat},{clon});'
        f'node["shop"~"agrarian|animal_feed|fodder|feed|grain|country_store|agriculture"](around:{radius},{clat},{clon});'
        f'way["shop"~"agrarian|animal_feed|fodder|feed|grain|country_store|agriculture"](around:{radius},{clat},{clon});'
        f'node["amenity"="veterinary"](around:{radius},{clat},{clon});'
        f'way["amenity"="veterinary"](around:{radius},{clat},{clon});'
        f'node["healthcare"="veterinary"](around:{radius},{clat},{clon});'
        f'way["healthcare"="veterinary"](around:{radius},{clat},{clon});'
        f'node["shop"~"cheese|ice_cream|butcher|wholesale"](around:{radius},{clat},{clon});'
        f'way["shop"~"cheese|ice_cream|butcher|wholesale"](around:{radius},{clat},{clon});'
        f'node["amenity"="butchery"](around:{radius},{clat},{clon});'
    )
    return f"[out:json][timeout:12];({inner});out center 40;"


def _fetch_overpass_combined(clat, clon, radius):
    """One Overpass round-trip for all dairy categories. Hard-fails in ~7s.

    Overpass trickles headers then stalls, so a blocking POST can hang past
    its socket timeout. We race GET mirrors with a hard as_completed deadline
    and skip the POST entirely for speed.
    """
    overpass_q = _build_combined_overpass_query(clat, clon, radius)
    urls = [m + "?" + urllib.parse.urlencode({"data": overpass_q}) for m in OVERPASS_MIRRORS]
    data = _race_fetch(urls, 7)
    if data is None:
        return None
    found = []
    for el in data.get("elements", []):
        # 'out center' puts way coords under 'center', node coords under lat/lon.
        if "lat" in el and "lon" in el:
            lat2, lon2 = el["lat"], el["lon"]
        elif "center" in el and isinstance(el["center"], dict):
            lat2 = el["center"].get("lat")
            lon2 = el["center"].get("lon")
            if lat2 is None or lon2 is None:
                continue
        else:
            continue
        dist = round(haversine_km(clat, clon, lat2, lon2), 1)
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("operator") or ""
        kind = _classify_poi(tags)
        if not kind:
            continue
        found.append({
            "name": name,
            "lat": lat2,
            "lon": lon2,
            "type": kind,
            "category": kind,
            "distance_km": dist,
            "address": _poi_address(tags),
        })
    return found


def _nominatim_search_once(query, limit=8, timeout=6):
    params = {"format": "jsonv2", "q": query, "limit": str(limit)}
    qs = urllib.parse.urlencode(params)
    try:
        return http_get_json(f"{GEOCODE_SEARCH_URL}?{qs}", headers={"User-Agent": APP_USER_AGENT}, timeout=timeout)
    except Exception:
        return []


PHOTON_URL = "https://photon.komoot.io/api/"

# Curated West Bengal dairy ecosystem — all coords verified via Nominatim/Photon
# (no hallucinated entries). Used as guaranteed baseline when OSM live search
# is sparse, e.g. Madhyamgram/Barasat where small shops aren't mapped.
CURATED_WB_DAIRY = [
    {"name": "Mother Dairy, New Alipore", "lat": 22.5069965, "lon": 88.3373235,
     "type": "Milk Collection Centre", "address": "Ustad Amir Khan Sarani, Kolkata"},
    {"name": "Hotel and Pure Milk Emporium", "lat": 22.5531582, "lon": 88.3565078,
     "type": "Milk Collection Centre", "address": "Rafi Ahmad Kidwai Rd, Kolkata"},
    {"name": "Sur Milk Centre", "lat": 22.6292108, "lon": 88.3811326,
     "type": "Milk Collection Centre", "address": "KC Ghosh Rd, Cossipore, Kolkata"},
    {"name": "Chaina Ghosh (Metro Milk)", "lat": 22.6291507, "lon": 88.381024,
     "type": "Milk Collection Centre", "address": "KC Ghosh Rd, Cossipore, Kolkata"},
    {"name": "Haringhata Dairy Farm, Mohanpur", "lat": 22.9539161, "lon": 88.5253613,
     "type": "Dairy Farm", "address": "Mohanpur, Nadia"},
]


def _curated_nearby(clat, clon, radius_km=30):
    out = []
    for c in CURATED_WB_DAIRY:
        d = haversine_km(clat, clon, c["lat"], c["lon"])
        if d <= radius_km:
            out.append({
                "name": c["name"],
                "lat": c["lat"],
                "lon": c["lon"],
                "type": c["type"],
                "category": c["type"],
                "distance_km": round(d, 1),
                "address": c["address"],
                "source": "curated",
            })
    out.sort(key=lambda p: p["distance_km"])
    return out


def _photon_bbox_search(clat, clon, radius_m=10000, limit=20):
    """Photon (OSM-based) bbox search — fast (~0.7s) and works where Overpass times out."""
    delta = max(radius_m, 15000) / 111000.0  # search at least 15km for suburbs
    bbox = f"{clon - delta},{clat - delta},{clon + delta},{clat + delta}"
    found = []
    for q in ["milk", "dairy"]:
        params = {"q": q, "bbox": bbox, "limit": str(limit)}
        qs = urllib.parse.urlencode(params)
        try:
            data = http_get_json(f"{PHOTON_URL}?{qs}", headers={"User-Agent": APP_USER_AGENT}, timeout=7)
        except Exception:
            continue
        for f in (data.get("features", []) if data else []):
            props = f.get("properties", {}) or {}
            geom = f.get("geometry", {}) or {}
            coords = geom.get("coordinates", [None, None])
            if not coords or len(coords) < 2 or coords[0] is None:
                continue
            lon2, lat2 = float(coords[0]), float(coords[1])
            dist = haversine_km(clat, clon, lat2, lon2)
            if dist > max(radius_m / 1000.0, 30):
                continue
            osm_key = (props.get("osm_key") or "")
            osm_val = (props.get("osm_value") or "")
            name = (props.get("name") or "").strip()
            # Keep only genuinely dairy-ish OSM objects; drop roads/parks/mills.
            tags = {"shop": props.get("osm_value") if osm_key == "shop" else None,
                    "amenity": props.get("osm_value") if osm_key == "amenity" else None}
            kind = None
            if osm_key == "shop" and osm_val in ("dairy", "cheese"):
                kind = "Milk Collection Centre"
            elif osm_key == "amenity" and osm_val in ("dairy", "milk"):
                kind = "Milk Collection Centre"
            elif name and ("dairy" in name.lower() or "milk" in name.lower() or "doodh" in name.lower()):
                # Named dairy/milk place inside bbox (e.g. Hotel and Pure Milk Emporium).
                if osm_key in ("highway", "leisure", "landuse", "place") and "mill" in name.lower().replace("milk", ""):
                    continue  # jute/cotton mill false positives
                kind = "Other Dairy Business"
                if "mother dairy" in name.lower() or "milk centre" in name.lower() or "milk center" in name.lower():
                    kind = "Milk Collection Centre"
            if not kind or not name:
                continue
            city = props.get("city") or props.get("county") or props.get("state") or ""
            found.append({
                "name": name[:80],
                "lat": lat2,
                "lon": lon2,
                "type": kind,
                "category": kind,
                "distance_km": round(dist, 1),
                "address": city[:80],
                "source": "photon",
            })
    return found


def _merge_places(*lists):
    seen = set()
    merged = []
    for lst in lists:
        for p in (lst or []):
            try:
                key = (round(float(p["lat"]), 4), round(float(p["lon"]), 4))
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            if "source" not in p:
                p["source"] = "live"
            merged.append(p)
    merged.sort(key=lambda p: p.get("distance_km", 999))
    return merged


def _nominatim_poi_fallback(clat, clon, radius, geo=None):
    """City-aware Nominatim fallback (Overpass is blocked on some networks).

    Uses 'dairy <city>' style queries which actually return results, instead
    of generic bounded-box queries which return 0. Respects 1 req/sec policy.
    """
    if geo is None:
        try:
            geo = reverse_geocode(clat, clon)
        except Exception:
            geo = None
    locality = (geo or {}).get("locality") or ""
    district = (geo or {}).get("district") or ""
    state = (geo or {}).get("state") or ""
    areas = []
    for a in [locality, district, state]:
        a = (a or "").strip()
        if a and a not in areas:
            areas.append(a)
    if not areas:
        areas = ["West Bengal"]

    templates = [
        ("Milk Collection Centre", "dairy {area}"),
        ("Other Dairy Business", "milk {area}"),
        ("Veterinary Service", "veterinary {area}"),
    ]
    found = []
    seen_coords = set()
    search_radius_km = max(radius / 1000.0, 20)
    for area in areas[:2]:
        for category, tmpl in templates:
            q = tmpl.format(area=area)
            data = _nominatim_search_once(q, limit=8, timeout=6)
            for r in (data or []):
                try:
                    lat2 = float(r.get("lat"))
                    lon2 = float(r.get("lon"))
                except Exception:
                    continue
                dist = haversine_km(clat, clon, lat2, lon2)
                if dist > search_radius_km:
                    continue
                key = (round(lat2, 4), round(lon2, 4))
                if key in seen_coords:
                    continue
                seen_coords.add(key)
                name = r.get("name") or (r.get("display_name", "").split(",")[0] or category)
                disp = r.get("display_name", "") or ""
                addr = ", ".join(disp.split(", ")[1:3]) if ", " in disp else ""
                found.append({
                    "name": name[:80],
                    "lat": lat2,
                    "lon": lon2,
                    "type": category,
                    "category": category,
                    "distance_km": round(dist, 1),
                    "address": addr[:120],
                })
            time.sleep(1.0)
        found.sort(key=lambda p: p["distance_km"])
        if len(found) >= 3:
            break
    found.sort(key=lambda p: p["distance_km"])
    return found


@app.get("/api/nearby")
def nearby_places(lat: float, lng: float, radius: int = 5000, business: str = "dairy"):
    """Multi-source dairy POI search for accurate results.

    Priority (fastest reliable first):
      1. Photon bbox (OSM, ~0.7s, works where Overpass times out)
      2. Curated WB baseline (verified coords, guarantees useful nearest)
      3. Nominatim city-aware (only if still <3 places)
      4. Overpass combined (only if still <3 places)
    Merged + deduped, nearest-first. Cached 10 min.
    """
    max_radius = max(500, min(int(radius), 20000))
    clat, clon = float(lat), float(lng)

    # --- Server cache (10 min, ~500m grid) ---
    ckey = _nearby_cache_key(clat, clon, max_radius)
    now = time.time()
    cached = _NEARBY_CACHE.get(ckey)
    if cached and (now - cached[0]) < _NEARBY_CACHE_TTL:
        data = dict(cached[1])
        data["cached"] = True
        return data

    from_cache = False
    sources_used = []

    # 1) Photon bbox — primary live source (fast + bbox-accurate).
    try:
        photon_results = _photon_bbox_search(clat, clon, radius_m=max_radius)
    except Exception:
        photon_results = []
    if photon_results:
        sources_used.append("photon")
    for p in photon_results:
        p["source"] = "photon"

    # 2) Curated baseline — always merged (filtered to 30km).
    curated_results = _curated_nearby(clat, clon, radius_km=30)
    if curated_results:
        sources_used.append("curated")

    merged = _merge_places(photon_results, curated_results)
    # Keep only places within a sensible display radius: requested radius,
    # widened to 30km if that is what it takes to show the nearest mapped ones.
    within_requested = [p for p in merged if p["distance_km"] <= max_radius / 1000.0]
    if len(within_requested) >= 3:
        deduped = within_requested
        widened = False
    else:
        deduped = [p for p in merged if p["distance_km"] <= 30]
        widened = len(deduped) > len(within_requested)

    # 3) Nominatim city-aware — only if still sparse.
    used_fallback = False
    if len(deduped) < 3:
        try:
            geo_for_fallback = reverse_geocode(clat, clon)
        except Exception:
            geo_for_fallback = None
        try:
            nomi_results = _nominatim_poi_fallback(clat, clon, max_radius, geo=geo_for_fallback)
        except Exception:
            nomi_results = []
        if nomi_results:
            sources_used.append("nominatim")
            used_fallback = True
        deduped = _merge_places(deduped, nomi_results)
        within_requested = [p for p in deduped if p["distance_km"] <= max_radius / 1000.0]
        if len(within_requested) >= 3:
            deduped = within_requested
            widened = False
        else:
            deduped = [p for p in deduped if p["distance_km"] <= 30]
            widened = True

    # 4) Overpass — last resort (often blocked, slow).
    if len(deduped) < 3:
        try:
            over_results = _fetch_overpass_combined(clat, clon, max_radius) or []
        except Exception:
            over_results = []
        if over_results:
            sources_used.append("overpass")
        deduped = _merge_places(deduped, over_results)

    # Sort by actual distance (nearest first).
    deduped.sort(key=lambda p: p["distance_km"])

    # Progressive display radius: tightest radius with >=3, else widen to 30km
    # so suburbs like Madhyamgram show nearest mapped dairies instead of 0.
    progressive_radii = [1, 2, 5, max_radius / 1000]
    used_radius_m = max_radius
    display_widened = bool(locals().get("widened", False))
    for r_km in progressive_radii:
        within = [p for p in deduped if p["distance_km"] <= r_km]
        if len(within) >= 3:
            used_radius_m = int(r_km * 1000)
            deduped = within
            display_widened = False
            break
    else:
        if deduped:
            farthest = max(p["distance_km"] for p in deduped)
            used_radius_m = int(min(max(farthest, max_radius / 1000.0), 30) * 1000)
            display_widened = farthest > max_radius / 1000.0

    by_category = {}
    for p in deduped:
        by_category[p.get("type", "?")] = by_category.get(p.get("type", "?"), 0) + 1

    out = {
        "count": len(deduped),
        "by_category": by_category,
        "business": business,
        "center": {"lat": clat, "lng": clon},
        "radius_m": used_radius_m,
        "places": deduped[:50],
        "categories": DAIRY_POI_CATEGORIES,
        "partial_failures": [],
        "cached": from_cache,
        "fallback": used_fallback or display_widened,
        "source": "+".join(sources_used) if sources_used else "none",
        "widened": display_widened,
    }
    if len(deduped) > 0:
        _NEARBY_CACHE[ckey] = (now, dict(out))
        if len(_NEARBY_CACHE) > 200:
            oldest = min(_NEARBY_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _NEARBY_CACHE.pop(oldest, None)
    return out

# ---------- Optimize my plan (risk-aware, leverage-constrained, param-unique) ----------
def _risk_rank(risk: str) -> int:
    return {"LOW": 2, "MEDIUM": 1, "HIGH": 0}.get(risk, 0)


def _structural_tips(base: dict, ref_analysis: dict):
    """What single starting-situation fix would help most? Unique per params."""
    tips = []
    checks = [
        ("fodder_land", True, "secure fodder land / grow green fodder"),
        ("family_labour", True, "use family labour for daily care"),
        ("distributor", True, "lock an assured buyer before scaling"),
        ("existing_shed", True, "use / repair the existing shed first"),
    ]
    for key, better_val, label in checks:
        if base.get(key) == better_val:
            continue
        trial = dict(base)
        trial[key] = better_val
        try:
            r = analyze_dairy_plan(trial)
        except Exception:
            continue
        gain = r["after_emi"] - ref_analysis["after_emi"]
        if gain > 500:
            tips.append({"fix": label, "gain": round(gain, 2), "after_emi": r["after_emi"]})
    tips.sort(key=lambda t: t["gain"], reverse=True)
    return tips[:2]


@app.post("/api/optimize")
def optimize_plan(req: PlanData):
    base = req.model_dump()
    current_cows = max(1, int(base["cows"]))

    current = analyze_dairy_plan(base)
    current_surplus = current["after_emi"]
    current_risk = current["risk"]

    own_capital = max(0, float(base["capital"]))
    leverage_cap = max(4 * own_capital, 150000)

    candidates = []
    for c in range(1, 13):
        trial = dict(base)
        trial["cows"] = c
        r = analyze_dairy_plan(trial)
        over_cap = r["loan_needed"] > leverage_cap + 1
        # Risk-adjusted score: heavily prefer LOW > MEDIUM > HIGH, then surplus,
        # then smaller loan (capital efficiency). Prevents generic 12-cow answer.
        # Loan penalty ~₹5/month per ₹1,000 borrowed prices in debt stress.
        score = _risk_rank(r["risk"]) * 50000 + r["after_emi"] - r["loan_needed"] * 0.005
        if over_cap:
            score -= 30000 + (r["loan_needed"] - leverage_cap) * 0.02
        candidates.append({"plan": trial, "analysis": r, "score": score, "over_cap": over_cap})

    # Score-based pick across ALL herd sizes (risk first, then surplus,
    # then loan efficiency). Soft cap penalty lets a profitable LOW that is
    # slightly over the guideline still win over a poorer feasible MEDIUM —
    # but a losing HIGH can never outscore a smaller losing HIGH with less debt.
    best = max(candidates, key=lambda x: x["score"])
    chosen = best
    status = "optimized"
    if best["over_cap"] and _risk_rank(best["analysis"]["risk"]) < 2:
        # Best is over the guideline and not LOW — is there a feasible non-HIGH close behind?
        feasible_good = [x for x in candidates
                         if not x["over_cap"] and _risk_rank(x["analysis"]["risk"]) >= 1]
        if feasible_good:
            alt = max(feasible_good, key=lambda x: x["score"])
            # Switch to the feasible option if best's edge is only debt-fuelled (<₹2k gain).
            if best["analysis"]["after_emi"] - alt["analysis"]["after_emi"] < 2000:
                chosen = alt
    # Status from chosen + current context.
    if all(x["over_cap"] for x in candidates):
        chosen = min(candidates, key=lambda x: x["analysis"]["loan_needed"])
        status = "over_leveraged"
    elif chosen["analysis"]["risk"] == "HIGH":
        status = "no_viable"
    # Aggressive betterment: only stay put for dust-level gains (<₹100/month
    # with no risk improvement). Small-but-real gains (₹100-1,000) are still
    # shown with a "small difference" note per user request.
    small_gain_note = False
    if chosen["plan"]["cows"] != current_cows:
        gain = chosen["analysis"]["after_emi"] - current_surplus
        risk_improves = _risk_rank(chosen["analysis"]["risk"]) > _risk_rank(current_risk)
        if (not risk_improves) and gain < 100:
            # Stay with current if it is not HIGH and not wildly over cap.
            if _risk_rank(current_risk) >= 1 and current["loan_needed"] <= leverage_cap * 1.75:
                chosen = {"plan": base, "analysis": current,
                          "score": 0, "over_cap": current["loan_needed"] > leverage_cap + 1}
                status = "already_optimal"
        elif (not risk_improves) and gain < 1000:
            small_gain_note = True
    if chosen["plan"]["cows"] == current_cows and abs(chosen["analysis"]["after_emi"] - current_surplus) < 1:
        if status == "optimized" and current_risk in ("LOW", "MEDIUM"):
            status = "already_optimal"
    # Aggressive MEDIUM betterment: if current is MEDIUM and a same-risk,
    # higher-surplus option exists within a stretched cap, show it even for
    # small gains (user explicitly wants to see it, with a honesty note).
    if current_risk == "MEDIUM" and status == "already_optimal":
        same_risk_better = [x for x in candidates
                            if x["analysis"]["risk"] == "MEDIUM"
                            and x["analysis"]["after_emi"] > current_surplus + 100
                            and x["analysis"]["loan_needed"] <= leverage_cap * 1.25]
        if same_risk_better:
            alt = max(same_risk_better, key=lambda x: x["analysis"]["after_emi"])
            chosen = alt
            status = "optimized"
            small_gain_note = (alt["analysis"]["after_emi"] - current_surplus) < 1000
    # Hard guard: HIGH risk must never report "already optimal".
    if current_risk == "HIGH" and status == "already_optimal":
        status = "no_viable"

    # When nothing viable at current facts, search one-fix structural variants
    # (family labour / fodder / buyer / shed) so Apply actually changes something
    # instead of showing identical 3-vs-3 columns with "keep my plan".
    fix_applied = None
    if status in ("no_viable", "over_leveraged") or chosen["analysis"]["risk"] == "HIGH":
        fix_keys = [k for k, v in
                    [("fodder_land", True), ("family_labour", True),
                     ("distributor", True), ("existing_shed", True)]
                    if base.get(k) != v]
        best_fix = None
        for fk in fix_keys:
            fbase = dict(base)
            fbase[fk] = True
            for c in range(1, 13):
                trial = dict(fbase)
                trial["cows"] = c
                try:
                    r = analyze_dairy_plan(trial)
                except Exception:
                    continue
                over = r["loan_needed"] > leverage_cap + 1
                sc = _risk_rank(r["risk"]) * 50000 + r["after_emi"] - r["loan_needed"] * 0.005
                if over:
                    sc -= 30000 + (r["loan_needed"] - leverage_cap) * 0.02
                if best_fix is None or sc > best_fix["score"]:
                    best_fix = {"plan": trial, "analysis": r, "score": sc,
                                "over_cap": over, "fix": fk}
        if best_fix is not None:
            improves_risk = _risk_rank(best_fix["analysis"]["risk"]) > _risk_rank(chosen["analysis"]["risk"])
            improves_cash = best_fix["analysis"]["after_emi"] - chosen["analysis"]["after_emi"] >= 2000
            differs = best_fix["plan"] != chosen["plan"]
            if differs and (improves_risk or improves_cash):
                chosen = best_fix
                fix_applied = best_fix["fix"]
                status = "fixable"

    opt = chosen["analysis"]
    opt_cows = chosen["plan"]["cows"]
    tips = _structural_tips(dict(base, cows=opt_cows), opt)
    olang = (getattr(req, "lang", "en") or "en").lower()[:2]
    if olang not in ("hi", "bn"):
        olang = "en"

    def _money(n):
        return f"₹{_inr_grouped(n)}"

    _FACT_L = {"new shed cost": {"hi": "नए शेड की लागत", "bn": "নতুন শেডের খরচ"},
                 "purchased fodder": {"hi": "खरीदा चारा", "bn": "কেনা খাদ্য"},
                 "hired labour": {"hi": "किराए का श्रम", "bn": "ভাড়া শ্রম"},
                 "no assured buyer (~10% lower realization)": {"hi": "कोई पक्का खरीदार नहीं (~10% कम भाव)", "bn": "কোনো পাকা ক্রেতা নেই (~10% কম দাম)"}}
    facts = []
    if not base["existing_shed"]:
        facts.append("new shed cost")
    if not base["fodder_land"]:
        facts.append("purchased fodder")
    if not base["family_labour"]:
        facts.append("hired labour")
    if not base.get("distributor", True):
        facts.append("no assured buyer (~10% lower realization)")
    if olang != "en":
        facts = [_FACT_L.get(f, {}).get(olang, f) for f in facts]
    fact_str = _L(olang, (", ".join(facts) + " weigh on costs. ") if facts else "",
        (", ".join(facts) + " लागत बढ़ाते हैं। ") if facts else "",
        (", ".join(facts) + " খরচ বাড়ায়। ") if facts else "")

    over_note = ""
    if chosen.get("over_cap"):
        over_note = _L(olang,
            f" Note: loan {_money(opt['loan_needed'])} is above the ~{_money(leverage_cap)} guideline — add own capital to be safer.",
            f" नोट: ऋण {_money(opt['loan_needed'])} ~{_money(leverage_cap)} सीमा से ऊपर है — अपनी पूँजी बढ़ाना सुरक्षित रहेगा।",
            f" নোট: ঋণ {_money(opt['loan_needed'])} ~{_money(leverage_cap)} সীমার উপরে — নিজের মূলধন বাড়ানো নিরাপদ হবে।")
    fix_labels = {"fodder_land": _L(olang, "securing fodder land", "चारा ज़मीन पक्की करना", "খাদ্যের জমি ঠিক করা"),
                  "family_labour": _L(olang, "using family labour", "पारिवारिक श्रम लगाना", "পারিবারিক শ্রম লাগানো"),
                  "distributor": _L(olang, "locking an assured buyer", "पक्का खरीदार बाँधना", "পাকা ক্রেতা বাঁধা"),
                  "existing_shed": _L(olang, "using the existing shed", "मौजूदा शेड इस्तेमाल करना", "আগের শেড ব্যবহার করা")}
    tip0 = tips[0]['fix'] if tips else _L(olang, "cut setup costs", "सेटअप लागत घटाएँ", "সেটআপ খরচ কমান")
    cow_w = _L(olang, "cow(s)", "गाय", "গরু")
    if status == "fixable" and fix_applied:
        reason = _L(olang,
            f"Keeping {current_cows} cows as-is stays HIGH ({_money(current_surplus)}/month) — don't keep it unchanged. "
            f"With {fix_labels.get(fix_applied, fix_applied)} applied, {opt_cows} {cow_w} reaches "
            f"{_money(opt['after_emi'])}/month ({opt['risk']} risk, loan {_money(opt['loan_needed'])}). "
            f"{fact_str}Apply this fix to move out of HIGH.",
            f"{current_cows} गायें ऐसे ही रखने पर HIGH रहेगा ({_money(current_surplus)}/माह) — ऐसे न रखें। "
            f"{fix_labels.get(fix_applied, fix_applied)} से {opt_cows} {cow_w} पर "
            f"{_money(opt['after_emi'])}/माह ({opt['risk']} जोखिम, ऋण {_money(opt['loan_needed'])}) मिलेगा। "
            f"{fact_str}HIGH से निकलने के लिए यह सुधार अपनाएँ।",
            f"{current_cows} গরু এভাবে রাখলে HIGH থাকবে ({_money(current_surplus)}/মাস) — এভাবে রাখবেন না। "
            f"{fix_labels.get(fix_applied, fix_applied)} করলে {opt_cows} {cow_w}-তে "
            f"{_money(opt['after_emi'])}/মাস ({opt['risk']} ঝুঁকি, ঋণ {_money(opt['loan_needed'])}) হবে। "
            f"{fact_str}HIGH থেকে বেরোতে এই সংশোধন নিন।")
    elif status == "over_leveraged":
        need = max(0, opt["loan_needed"] - leverage_cap)
        reason = _L(olang,
            f"No herd size fits a safe loan on {_money(own_capital)} own capital "
            f"(safe limit ~{_money(leverage_cap)}; even 1 cow needs {_money(opt['loan_needed'])}). "
            f"{fact_str}Add about {_money(need)} more own capital, or {tip0} first — "
            f"borrowing more now keeps you HIGH risk.",
            f"{_money(own_capital)} पूँजी पर कोई पशु संख्या सुरक्षित ऋण में नहीं आती "
            f"(सीमा ~{_money(leverage_cap)}; 1 गाय को भी {_money(opt['loan_needed'])} चाहिए)। "
            f"{fact_str}लगभग {_money(need)} पूँजी और जोड़ें, या पहले {tip0} — "
            f"अभी और उधार HIGH जोखिम रखेगा।",
            f"{_money(own_capital)} মূলধনে কোনো পশু সংখ্যাই নিরাপদ ঋণে আসে না "
            f"(সীমা ~{_money(leverage_cap)}; 1 গরুতেও {_money(opt['loan_needed'])} লাগে)। "
            f"{fact_str}প্রায় {_money(need)} মূলধন আরও যোগ করুন, বা আগে {tip0} — "
            f"এখন আরও ধার HIGH ঝুঁকি রাখবে।")
    elif status == "no_viable":
        reason = _L(olang,
            f"No scale reaches break-even on these costs — {fact_str}"
            f"least-bad is {opt_cows} {cow_w} at {_money(opt['after_emi'])}/month "
            f"(loan {_money(opt['loan_needed'])}). Scaling to 12 only grows a loss-making loan.",
            f"इन लागतों पर कोई पैमाना फायदे में नहीं — {fact_str}"
            f"सबसे कम खराब {opt_cows} {cow_w} पर {_money(opt['after_emi'])}/माह है "
            f"(ऋण {_money(opt['loan_needed'])})। 12 तक बढ़ाना सिर्फ घाटे वाला ऋण बढ़ाएगा।",
            f"এই খরচে কোনো আকারই লাভে আসে না — {fact_str}"
            f"সবচেয়ে কম খারাপ {opt_cows} {cow_w}-তে {_money(opt['after_emi'])}/মাস "
            f"(ঋণ {_money(opt['loan_needed'])})। 12-তে বাড়ালে শুধু লোকসানি ঋণ বাড়বে।")
    elif opt_cows > current_cows:
        gain_txt = f"{_money(current_surplus)} → {_money(opt['after_emi'])}/month"
        gain_txt_l = _L(olang, gain_txt, gain_txt.replace("/month", "/माह"), gain_txt.replace("/month", "/মাস"))
        reason = _L(olang,
            f"Raising {current_cows} → {opt_cows} cows moves surplus {gain_txt}, "
            f"loan {_money(opt['loan_needed'])} (guideline ~{_money(leverage_cap)}). {fact_str}"
            f"Risk: {current_risk} → {opt['risk']}.{over_note}",
            f"{current_cows} → {opt_cows} गायें करने से बचत {gain_txt_l} होगी, "
            f"ऋण {_money(opt['loan_needed'])} (सीमा ~{_money(leverage_cap)})। {fact_str}"
            f"जोखिम: {current_risk} → {opt['risk']}।{over_note}",
            f"{current_cows} → {opt_cows} গরু করলে উদ্বৃত্ত {gain_txt_l} হবে, "
            f"ঋণ {_money(opt['loan_needed'])} (সীমা ~{_money(leverage_cap)})। {fact_str}"
            f"ঝুঁকি: {current_risk} → {opt['risk']}।{over_note}")
        if small_gain_note:
            reason += _L(olang, " This doesn't pose much difference month-to-month, but it is the slightly better scale.",
                " महीने-दर-महीने ज़्यादा फर्क नहीं, पर यही थोड़ा बेहतर पैमाना है।",
                " মাসে মাসে বেশি ফারাক নেই, তবে এটাই একটু ভালো আকার।")
    elif opt_cows < current_cows:
        if opt["after_emi"] >= current_surplus:
            reason = _L(olang,
                f"Cutting {current_cows} → {opt_cows} cows improves surplus {_money(current_surplus)} → {_money(opt['after_emi'])}/month "
                f"and drops the loan to {_money(opt['loan_needed'])} (guideline ~{_money(leverage_cap)}). {fact_str}"
                f"Risk: {current_risk} → {opt['risk']}.",
                f"{current_cows} → {opt_cows} गायें घटाने से बचत {_money(current_surplus)} → {_money(opt['after_emi'])}/माह सुधरती है "
                f"और ऋण {_money(opt['loan_needed'])} रह जाता है (सीमा ~{_money(leverage_cap)})। {fact_str}"
                f"जोखिम: {current_risk} → {opt['risk']}।",
                f"{current_cows} → {opt_cows} গরু কমালে উদ্বৃত্ত {_money(current_surplus)} → {_money(opt['after_emi'])}/মাস ভালো হয় "
                f"এবং ঋণ {_money(opt['loan_needed'])} থাকে (সীমা ~{_money(leverage_cap)})। {fact_str}"
                f"ঝুঁকি: {current_risk} → {opt['risk']}।")
        else:
            reason = _L(olang,
                f"Trimming {current_cows} → {opt_cows} cows lowers the loan {_money(current['loan_needed'])} → {_money(opt['loan_needed'])} "
                f"(guideline ~{_money(leverage_cap)}), at a surplus of {_money(opt['after_emi'])}/month vs {_money(current_surplus)} now. {fact_str}"
                f"Risk: {current_risk} → {opt['risk']}. Smaller debt, easier to service.",
                f"{current_cows} → {opt_cows} गायें छाँटने से ऋण {_money(current['loan_needed'])} → {_money(opt['loan_needed'])} घटता है "
                f"(सीमा ~{_money(leverage_cap)}), बचत अभी {_money(current_surplus)} बनाम {_money(opt['after_emi'])}/माह। {fact_str}"
                f"जोखिम: {current_risk} → {opt['risk']}। छोटा कर्ज़, आसान चुकौती।",
                f"{current_cows} → {opt_cows} গরু ছাঁটলে ঋণ {_money(current['loan_needed'])} → {_money(opt['loan_needed'])} কমে "
                f"(সীমা ~{_money(leverage_cap)}), উদ্বৃত্ত এখন {_money(current_surplus)} বনাম {_money(opt['after_emi'])}/মাস। {fact_str}"
                f"ঝুঁকি: {current_risk} → {opt['risk']}। ছোট ঋণ, সহজ শোধ।")
        if small_gain_note:
            reason += _L(olang, " This doesn't pose much difference month-to-month, but it is the slightly better scale.",
                " महीने-दर-महीने ज़्यादा फर्क नहीं, पर यही थोड़ा बेहतर पैमाना है।",
                " মাসে মাসে বেশি ফারাক নেই, তবে এটাই একটু ভালো আকার।")
    else:
        reason = _L(olang,
            f"{current_cows} cow(s) is already your sweet spot for {_money(own_capital)} capital — "
            f"surplus {_money(opt['after_emi'])}/month, {opt['risk']} risk, loan {_money(opt['loan_needed'])}. We kept it.{over_note}",
            f"{current_cows} गायें {_money(own_capital)} पूँजी के लिए पहले से सही हैं — "
            f"बचत {_money(opt['after_emi'])}/माह, {opt['risk']} जोखिम, ऋण {_money(opt['loan_needed'])}। ऐसे ही रखा।{over_note}",
            f"{current_cows} গরু {_money(own_capital)} মূলধনের জন্য আগেই সঠিক — "
            f"উদ্বৃত্ত {_money(opt['after_emi'])}/মাস, {opt['risk']} ঝুঁকি, ঋণ {_money(opt['loan_needed'])}। এভাবেই রাখা হলো।{over_note}")
    if tips and status in ("optimized", "no_viable", "fixable"):
        reason += _L(olang, f" Next: {tips[0]['fix']} could add ≈{_money(tips[0]['gain'])}/month.",
            f" आगे: {tips[0]['fix']} से ≈{_money(tips[0]['gain'])}/माह बढ़ सकता है।",
            f" এরপর: {tips[0]['fix']} থেকে ≈{_money(tips[0]['gain'])}/মাস বাড়তে পারে।")

    return {
        "current": {"plan": base, "analysis": current},
        "optimal": {"plan": chosen["plan"], "analysis": opt},
        "reason": _loc_text(reason, olang),
        "status": status,
        "leverage_cap": leverage_cap,
        "tips": tips,
        "fix_applied": fix_applied,
    }

# ---------- 24-month scenario projection engine ----------
@app.post("/api/simulate")
def simulate_plan(req: PlanData):
    base = req.model_dump()
    months = 24

    a = analyze_dairy_plan(base)
    os = a["operating_surplus"]
    after_emi = a["after_emi"]
    monthly_cost = a["monthly_cost"]

    # Deterministic scenario monthly cash positions (per the model formulas):
    #   Expected   = surplus after the loan repayment  (Operating Surplus - EMI)
    #   Opportunity= operating surplus boosted +30%     (Operating Surplus x 1.30)
    #   Stress     = after-EMI surplus less 20% of monthly costs
    expected_month = after_emi
    opportunity_month = os * 1.30
    stress_month = after_emi - (0.20 * monthly_cost)

    def cumulative(slope):
        return [round(slope * t, 2) for t in range(months + 1)]

    expected = cumulative(expected_month)
    opportunity = cumulative(opportunity_month)
    stress = cumulative(stress_month)

    all_vals = opportunity + stress
    return {
        "months": months + 1,
        "expected": expected,
        "opportunity": opportunity,
        "stress": stress,
        "months_listed": 24,
        "final": {
            "opportunity": round(opportunity_month * months, 2),
            "expected": round(expected_month * months, 2),
            "stress": round(stress_month * months, 2),
        },
        "min_y": round(min(all_vals), 2),
        "max_y": round(max(all_vals), 2),
        "monthly_surplus": after_emi,
        "risk": a["risk"],
        "loan_needed": a["loan_needed"],
    }

# ---------- Debate Agent: what-if analysis ----------
class AgentQuestion(BaseModel):
    question: str
    plan: dict
    agent: str = "advocate"
    lang: str = "en"


def _L(lang, en, hi=None, bn=None):
    """Pick a template by language (default English). Placeholders use .format()."""
    if lang == "hi" and hi is not None:
        return hi
    if lang == "bn" and bn is not None:
        return bn
    return en


def _inr_grouped(n):
    """Indian digit grouping (lakh/crore): 2009000 -> 20,09,000."""
    neg = n < 0
    s = str(abs(round(n)))
    if len(s) <= 3:
        g = s
    else:
        tail = s[-3:]
        head = s[:-3]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        g = ",".join(parts) + "," + tail
    return ("-" if neg else "") + g


def _money(n):
    return f"₹{_inr_grouped(n)}"


_NATIVE_DIGITS = {"hi": "०१२३४५६७८९", "bn": "০১২৩৪৫৬৭৮৯"}
_NATIVE_RISK = {"hi": {"LOW": "कम", "MEDIUM": "मध्यम", "HIGH": "उच्च"},
                "bn": {"LOW": "কম", "MEDIUM": "মাঝারি", "HIGH": "উচ্চ"}}
_NATIVE_COW = {"hi": ("गायें", "गाय"), "bn": ("গরু", "গরু")}


def _loc_text(s, lang):
    """Localize digits + risk tiers + cow nouns for hi/bn chat/optimizer text."""
    if lang not in ("hi", "bn") or not s:
        return s
    plu, sing = _NATIVE_COW[lang]
    s = s.replace("cow(s)", sing)
    s = re.sub(r"(\d+)-cow\b", r"\1-" + sing, s)
    s = re.sub(r"\bcows\b", plu, s)
    s = re.sub(r"\bcow\b", sing, s)
    d = _NATIVE_DIGITS[lang]
    s = "".join(d[ord(c) - 48] if "0" <= c <= "9" else c for c in s)
    for k, v in _NATIVE_RISK[lang].items():
        s = re.sub(r"\b" + k + r"\b", v, s)
    return s


def _infer_yes_no(text, base):
    """Guess whether the user is turning a feature ON or OFF from phrasing."""
    t = text.lower()
    # Explicit OFF: no / no longer / without / lose / quit / stop / remove...
    if re.search(r"\b(no longer|no more|without|lose|losing|lost|quit|quitting|leave|leaving|stop|stopped|stopping|cancel|give up|gone|remove|don'?t (have|use)|do not (have|use)|none|no existing)\b", t):
        # "no longer have land" / "lose buyers" / "quit buyer" -> OFF
        # Guard: "no problem" shouldn't count — require a feature word nearby is checked by caller.
        return False
    # Explicit ON: gain / get / secure / arrange / add / have / with...
    if re.search(r"\b(gain|gaining|gained|get|getting|got|find|found|secure|secured|securing|arrange|arranged|onboard|tie up|tied up|add|adding|include|including|have|has|having|with|yes|use|using|i have|we have|put|keep|keeping|take|taking)\b", t):
        return True
    return base  # no clear intent -> keep as-is


def _parse_cow_change(q, base_cows):
    """Detect cow-count changes in the question. Returns new cow count or None."""
    # "reduce cows to N" / "increase to N" / "to N cows" / "N cows" / "cows to N"
    # verb ... (cows/them/it) ... to N   (number AFTER the word cow)
    m = re.search(r"\b(?:reduce|decrease|cut|increase|raise|bring|set|drop|lower)\b.*?\b(?:cows?|animals?|cattle|herd|them|it|that)\b.*?\bto\s+(\d+)\b", q)
    if m:
        return max(1, min(12, int(m.group(1))))
    # "add/increase by N" / "add N more"
    m = re.search(r"\b(?:add|increase|raise)\s+(?:by\s+)?(\d+)\s+(?:more\s+)?(?:cows?|animals?|cattle)s?", q)
    if m:
        return max(1, min(12, base_cows + int(m.group(1))))
    # "remove/reduce N cows"
    m = re.search(r"\b(?:remove|reduce|decrease|cut)\s+(?:by\s+)?(\d+)\s+(?:cows?|animals?|cattle)s?", q)
    if m:
        return max(1, min(12, base_cows - int(m.group(1))))
    # "N cows", "N cattle", "N animals" (plain statement)
    m = re.search(r"\b(\d+)\s+(?:cows?|animals?|cattle|herd)s?\b", q)
    if m:
        return max(1, min(12, int(m.group(1))))
    return None


def _parse_capital_change(q, base_capital=0):
    """Detect adding/losing an amount of capital.
    Returns (delta, label). If a phrase like 'more capital' is found without a
    number, uses ₹100,000 as a concrete example labelled as such."""
    # explicit amount with add/lose intent
    m = re.search(r"\b(?:add|put|invest|bring|increase|raise)\s+(?:in\s+)?[₹]?\s*(\d{3,})|(?:lose|remove|reduce|take)\s+(?:out\s+)?[₹]?\s*(\d{3,})", q)
    if m:
        amt = int(m.group(1) or m.group(2))
        neg = m.group(1) is None
        return (-amt if neg else amt), None
    # phrase without a number -> example bump
    if re.search(r"\b(?:more|extra|add|increase|put in)\s+(?:capital|money|investment)|more\s+capital\b", q):
        return 100000, "Example: adding ₹1,00,000 of own capital"
    return 0, None


def _parse_pct(q, keywords):
    """Detect a percent change near one of 'keywords'. Returns pct (+/- as float in 1.0 terms) or None."""
    for kw in keywords:
        idx = q.find(kw)
        if idx == -1:
            continue
        window = q[max(0, idx - 45):idx + 35]
        # "10%", "drop 10 percent", "decrease by 5%", "up 8 percent", "by 12 %"
        m = re.search(r'([+\-]?\d{1,3})\s*(?:%|percent|per\s*cent)', window)
        if not m:
            continue
        val = float(m.group(1))
        # negative if phrased with drop/decrease/fall/reduce/lower/cut/less
        neg = any(w in window for w in ["drop", "decrease", "fall", "reduce", "lower", "cut", "less", "down", "minus", "decline", "fall"])
        if neg and val > 0:
            val = -val
        return val / 100.0
    return None


_APPLIED_L = {
    "family labour": {"hi": "पारिवारिक श्रम", "bn": "পারিবারিক শ্রম"},
    "hired labour": {"hi": "किराए का श्रम", "bn": "ভাড়া করা শ্রম"},
    "your own shed": {"hi": "अपना शेड", "bn": "নিজের শেড"},
    "shed built": {"hi": "शेड बनवाना", "bn": "শেড বানানো"},
    "fodder land": {"hi": "चारे की ज़मीन", "bn": "খাদ্যের জমি"},
    "no fodder land": {"hi": "चारे की ज़मीन नहीं", "bn": "খাদ্যের জমি নেই"},
    "a distributor/buyer": {"hi": "वितरक/खरीदार", "bn": "পরিবেশক/ক্রেতা"},
    "no fixed buyer": {"hi": "कोई पक्का खरीदार नहीं", "bn": "কোনো পাকা ক্রেতা নেই"},
}


def _local_applied(applied, lang):
    if lang == "en":
        return list(applied)
    out = []
    for a in applied:
        m = re.match(r"^(\d+)\s+cows?$", a)
        if m:
            out.append(f"{m.group(1)} " + ("गायें" if lang == "hi" else "গরু"))
            continue
        t = _APPLIED_L.get(a, {}).get(lang)
        out.append(t if t else a)
    return out


def _asks_zero_cows(q):
    """Detect exit intent: 0 / zero / no / none / without any cows."""
    return bool(re.search(
        r"\b(0|zero|no|none|without any|sell all|quit|shut|close|stop (keeping|having|rearing))\b[^.]{0,20}\b(cows?|cattle|animals?|buffalo|herd|dairy)\b"
        r"|\b(cows?|cattle|animals?|buffalo|herd)\b[^.]{0,10}\b(0|zero)\b", q))


def _modal_answer(plan, d, d_after):
    """Generic explanation of impact between two analyses."""
    return {
        "loan_unchanged": abs(d["loan_needed"] - d_after["loan_needed"]) < 1,
        "loan_change": d_after["loan_needed"] - d["loan_needed"],
        "surplus_change": d_after["after_emi"] - d["after_emi"],
        "cost_change": d_after["monthly_cost"] - d["monthly_cost"],
        "new_risk": d_after["risk"],
        "old_risk": d["risk"],
    }


TOGGLE_TOPICS = [
    ("family_labour", ["family labour", "family labor", "family member", "family help", "hired labour", "hired labor", "labour", "labor"],
     "family labour", "hired labour"),
    ("existing_shed", ["shed", "cattle shed", "cow shed", "barn", "shelter"],
     "your own shed", "shed built"),
    ("fodder_land", ["fodder land", "fodder", "land for fodder", "green fodder", "land"],
     "fodder land", "no fodder land"),
    ("distributor", ["distributor", "buyer", "buyers", "market access", "customer", "customers", "supplier", "suppliers", "sell to", "selling"],
     "a distributor/buyer", "no fixed buyer"),
]


def _topic_gains(plan, cur):
    """Value of each toggle ON vs OFF at current cows/capital (for explainers)."""
    out = {}
    for key, _, _, _ in TOGGLE_TOPICS:
        on = dict(plan)
        on[key] = True
        off = dict(plan)
        off[key] = False
        try:
            a_on = analyze_dairy_plan(on)
            a_off = analyze_dairy_plan(off)
        except Exception:
            continue
        out[key] = {"on": a_on, "off": a_off, "gain_on": a_on["after_emi"] - a_off["after_emi"]}
    return out


def _risk_drivers(plan, cur):
    """Ranked risk drivers for THIS plan with numbers (challenger fuel)."""
    drivers = []
    emi_share = (cur["estimated_emi"] / cur["operating_surplus"] * 100) if cur["operating_surplus"] > 0 else 999
    drivers.append(("emi", f"EMI eats {emi_share:.0f}% of operating surplus ({_money(cur['estimated_emi'])}/month on {_money(cur['operating_surplus'])} surplus)", emi_share))
    drivers.append(("feed", f"Monthly running cost {_money(cur['monthly_cost'])}/month leaves {_money(cur['after_emi'])}/month after EMI", -cur["after_emi"] if cur["after_emi"] < 5000 else 0))
    lev = cur["loan_needed"] / max(plan.get("capital", 1), 1)
    drivers.append(("leverage", f"Borrowing {_money(cur['loan_needed'])} on {_money(plan.get('capital', 0))} own capital ({lev:.1f}x leverage)", lev * 1000))
    if not plan.get("distributor", True):
        drivers.append(("buyer", "No assured buyer: ~10% lower milk realization", 5000))
    if not plan.get("family_labour", True):
        drivers.append(("labour", f"Hired labour is a fixed {_money(9000)}/month wage", 3000))
    if not plan.get("fodder_land", True):
        drivers.append(("fodder", "Purchased green fodder on every cow", 2000))
    if not plan.get("existing_shed", False):
        drivers.append(("shed", "New shed inflates the one-time project cost", 1000))
    return drivers


@app.post("/api/agent/ask")
def agent_ask(req: AgentQuestion):
    q = req.question.lower().strip()
    plan = dict(req.plan)
    for k in ("cows", "capital", "existing_shed", "fodder_land", "family_labour", "distributor"):
        plan.setdefault(k, {"cows": 3, "capital": 100000, "existing_shed": False, "fodder_land": True,
                            "family_labour": True, "distributor": True}[k])

    cur = analyze_dairy_plan(plan)
    lang = (getattr(req, "lang", "en") or "en").lower()[:2]
    if lang not in ("hi", "bn"):
        lang = "en"
    zero_cows = _asks_zero_cows(q)
    trial = dict(plan)
    applied = []
    mentioned = {}  # toggle key -> True when the question is about it (even if no change)

    for key, kw_group, on_label, off_label in TOGGLE_TOPICS:
        if any(kw in q for kw in kw_group):
            mentioned[key] = True
            desired = _infer_yes_no(q, bool(trial[key]))
            if desired != bool(trial[key]):
                trial[key] = desired
                applied.append(f"{on_label if desired else off_label}")

    # --- Cow count ---
    new_cows = _parse_cow_change(q, int(plan["cows"]))
    if new_cows is not None and new_cows != int(plan["cows"]):
        trial["cows"] = new_cows
        applied.append(f"{new_cows} cows")

    # --- Capital change ---
    cap_delta, cap_label = _parse_capital_change(q, int(plan.get("capital", 0)))
    if cap_delta != 0:
        trial["capital"] = max(0, int(plan["capital"]) + cap_delta)
        applied.append((cap_label if cap_label else
                        ("adding" if cap_delta > 0 else "reducing") + f" capital by ₹{abs(cap_delta):,}"))

    # --- Price / feed percentage scenarios (scaled on top of base analysis) ---
    milk_pct = _parse_pct(q, ["milk price", "price", "realization", "selling price", "rate"])
    feed_pct = _parse_pct(q, ["feed cost", "feed price", "fodder cost", "input cost", "cost of feed"])

    def slant(d_, mp, fp):
        d2 = dict(d_)
        d2["estimated_monthly_revenue"] = d_["estimated_monthly_revenue"] * (1 + (mp or 0))
        d2["monthly_cost"] = d_["monthly_cost"] * (1 + (fp or 0))
        d2["operating_surplus"] = d2["estimated_monthly_revenue"] - d2["monthly_cost"]
        d2["after_emi"] = d2["operating_surplus"] - d_["estimated_emi"]
        d2["risk"] = "LOW" if d2["after_emi"] >= 5000 else ("MEDIUM" if d2["after_emi"] >= 0 else "HIGH")
        return d2

    cur_slanted = slant(cur, milk_pct, feed_pct) if (milk_pct or feed_pct) else cur
    after = analyze_dairy_plan(trial)
    if milk_pct or feed_pct:
        after = slant(after, milk_pct, feed_pct)

    imp = _modal_answer(plan, cur_slanted, after)

    # ========== Build a question-specific answer (no generic dumps) ==========
    gains = _topic_gains(plan, cur)
    lines = []
    if applied:
        change_human = ", ".join(_local_applied(applied, lang))
        lines.append(_L(lang,
            f"If you change to: **{change_human}** — here's the real effect on your numbers.",
            f"अगर आप **{change_human}** पर बदलते हैं — आपकी संख्याओं पर असली असर यह है।",
            f"আপনি **{change_human}**-এ বদলালে — আপনার সংখ্যায় আসল প্রভাব এই।"))

    ask_about_loan = any(w in q for w in ["loan", "borrow", "emi", "debt", "principal", "repay"])
    ask_about_surplus = any(w in q for w in ["surplus", "profit", "profitable", "earn", "save", "saving", "margin", "money", "income"])
    ask_about_cost = any(w in q for w in ["cost", "cheaper", "reduce cost", "expense", "spend", "expenditure"])
    ask_about_risk = any(w in q for w in ["risk", "safe", "safety", "danger", "wrong", "fail", "loss", "lose money", "worry", "concern"])
    ask_about_cows = any(w in q for w in ["cow", "cattle", "herd", "animal", "buffalo", "scale", "expand", "reduce cows", "more cows", "fewer cows"])
    ask_about_capital = any(w in q for w in ["capital", "own money", "down payment", "investment", "invest"])
    ask_about_breakdown = any(w in q for w in ["breakdown", "break up", "project cost", "setup cost", "one-time", "how much will it cost"])
    ask_about_milk_nopct = ("milk" in q or "price" in q or "rate" in q or "realization" in q) and milk_pct is None
    ask_about_feed_nopct = ("feed" in q or "fodder" in q or "concentrate" in q) and feed_pct is None
    ask_greeting = bool(re.search(r"^(hi|hello|hey|namaste|good (morning|evening|afternoon))\b", q))
    ask_thanks = any(w in q for w in ["thank", "thanks", "thx", "great", "nice", "okay", "ok "])

    answer_lines = []
    # 0) Exit intent: 0 cows is not a herd size — answer it directly.
    if zero_cows:
        m = analyze_dairy_plan.__globals__["MODEL"]["dairy"]
        fixed = m["utilities_month"] + m["misc_month"]
        one = analyze_dairy_plan({**plan, "cows": 1})
        answer_lines.append(_L(lang,
            f"With **0 cows there's no dairy business**: milk revenue drops to **₹0/month**, "
            f"but fixed costs (~{_money(fixed)}/month utilities + misc) and any already-taken EMI "
            f"(~{_money(cur['estimated_emi'])}/month on your {_money(cur['loan_needed'])} loan) don't disappear — "
            f"you'd sit at about **-{_money(fixed + cur['estimated_emi'])}/month** with zero income. "
            f"Minimum viable here is **1 cow** ({_money(one['after_emi'])}/month, "
            f"{one['risk']}). If you mean pausing, keep {_money(plan.get('capital', 0))} capital unborrowed; "
            f"if you mean quitting dairy, ask me what else to compare.",
            f"**0 गायों पर कोई डेयरी व्यवसाय नहीं**: दूध आय **₹0/माह** होगी, "
            f"पर तय लागत (~{_money(fixed)}/माह) और पहले से लिया EMI "
            f"(आपके {_money(cur['loan_needed'])} ऋण पर ~{_money(cur['estimated_emi'])}/माह) नहीं हटेंगे — "
            f"बिना आय के लगभग **-{_money(fixed + cur['estimated_emi'])}/माह** पर रहेंगे। "
            f"यहाँ न्यूनतम **1 गाय** ठीक है ({_money(one['after_emi'])}/माह, {one['risk']})।",
            f"**0 গরুতে কোনো ডেয়ারি ব্যবসা নেই**: দুধের আয় **₹0/মাস** হবে, "
            f"কিন্তু নির্দিষ্ট খরচ (~{_money(fixed)}/মাস) ও আগে নেওয়া EMI "
            f"(আপনার {_money(cur['loan_needed'])} ঋণে ~{_money(cur['estimated_emi'])}/মাস) যাবে না — "
            f"আয় ছাড়াই প্রায় **-{_money(fixed + cur['estimated_emi'])}/মাস**-এ থাকবেন। "
            f"এখানে সর্বনিম্ন **1 গরু** ঠিক আছে ({_money(one['after_emi'])}/মাস, {one['risk']})।"))
    structural_change = bool(applied) or abs(imp["surplus_change"]) >= 1 or abs(imp["loan_change"]) >= 1
    pct_only = (milk_pct is not None or feed_pct is not None) and not applied

    _TOPIC_L = {
        "family_labour": {"label": {"hi": "पारिवारिक श्रम", "bn": "পারিবারিক শ্রম"},
                          "what": {"hi": "₹9,000/माह किराए की मज़दूरी", "bn": "₹9,000/মাস ভাড়া মজুরি"}},
        "existing_shed": {"label": {"hi": "अपना शेड", "bn": "নিজের শেড"},
                          "what": {"hi": "ऋण में शेड बनाने की लागत", "bn": "ঋণে শেড বানানোর খরচ"}},
        "fodder_land": {"label": {"hi": "चारे की ज़मीन", "bn": "খাদ্যের জমি"},
                        "what": {"hi": "खरीदे गए हरे चारे का बिल", "bn": "কেনা সবুজ খাদ্যের বিল"}},
        "distributor": {"label": {"hi": "पक्का खरीदार", "bn": "পাকা ক্রেতা"},
                        "what": {"hi": "~10% बेहतर दूध भाव", "bn": "~10% ভালো দুধের দাম"}},
    }

    def _topic_explainer(key):
        g = gains.get(key)
        if not g:
            return None
        has_it = bool(plan.get(key))
        on, off = g["on"], g["off"]
        delta = g["gain_on"]  # surplus gain of having it ON vs OFF
        names = {"family_labour": ("family labour", f"{_money(9000)}/month hired wage"),
                 "existing_shed": ("own shed", "shed build cost in the loan"),
                 "fodder_land": ("fodder land", "purchased green fodder bill"),
                 "distributor": ("assured buyer", "~10% better milk realization")}
        label, what = names.get(key, (key, ""))
        if lang != "en":
            label = _TOPIC_L.get(key, {}).get("label", {}).get(lang, label)
            what = _TOPIC_L.get(key, {}).get("what", {}).get(lang, what)
        if has_it:
            return _L(lang,
                f"You already have {label} — it is worth about **{_money(abs(delta))}/month** "
                f"vs going without ({_money(on['after_emi'])} vs {_money(off['after_emi'])}/month surplus; "
                f"risk {on['risk']} vs {off['risk']}). Losing it would cost you {_money(abs(delta))}/month, "
                f"so protect it ({what}).",
                f"आपके पास पहले से {label} है — इसके बिना की तुलना में यह लगभग **{_money(abs(delta))}/माह** का है "
                f"({_money(on['after_emi'])} बनाम {_money(off['after_emi'])}/माह बचत; जोखिम {on['risk']} बनाम {off['risk']})। "
                f"इसे खोने पर {_money(abs(delta))}/माह जाएगा, इसलिए इसे बचाएँ ({what})।",
                f"আপনার আগেই {label} আছে — এটা ছাড়া থাকার চেয়ে প্রায় **{_money(abs(delta))}/মাস** মূল্যবান "
                f"({_money(on['after_emi'])} বনাম {_money(off['after_emi'])}/মাস উদ্বৃত্ত; ঝুঁকি {on['risk']} বনাম {off['risk']})। "
                f"এটা হারালে {_money(abs(delta))}/মাস যাবে, তাই রক্ষা করুন ({what})।")
        else:
            return _L(lang,
                f"You don't have {label} right now ({what}). Adding it would move your surplus "
                f"**{_money(off['after_emi'])} → {_money(on['after_emi'])}/month** "
                f"(risk {off['risk']} → {on['risk']}) — a gain of about {_money(abs(delta))}/month.",
                f"आपके पास अभी {label} नहीं है ({what})। इसे जोड़ने पर बचत "
                f"**{_money(off['after_emi'])} → {_money(on['after_emi'])}/माह** होगी "
                f"(जोखिम {off['risk']} → {on['risk']}) — लगभग {_money(abs(delta))}/माह का फायदा।",
                f"আপনার এখন {label} নেই ({what})। এটা যোগ করলে উদ্বৃত্ত "
                f"**{_money(off['after_emi'])} → {_money(on['after_emi'])}/মাস** হবে "
                f"(ঝুঁকি {off['risk']} → {on['risk']}) — প্রায় {_money(abs(delta))}/মাস লাভ।")

    # 1) Real what-if change detected -> specific delta (existing behaviour, kept).
    if applied and not zero_cows:
        loan_delta = imp["loan_change"]
        up_txt = _L(lang, "up", "बढ़ता", "বাড়ে")
        down_txt = _L(lang, "down", "घटता", "কমে")
        if abs(loan_delta) >= 1:
            answer_lines.append(_L(lang,
                f"Your loan changes by {_money(loan_delta)} to **{_money(after['loan_needed'])}**, "
                f"EMI about {_money(after['estimated_emi'])}/month.",
                f"आपका ऋण {_money(loan_delta)} बदलकर **{_money(after['loan_needed'])}** होगा, EMI लगभग {_money(after['estimated_emi'])}/माह।",
                f"আপনার ঋণ {_money(loan_delta)} বদলে **{_money(after['loan_needed'])}** হবে, EMI প্রায় {_money(after['estimated_emi'])}/মাস।"))
        else:
            answer_lines.append(_L(lang,
                f"Loan stays at **{_money(cur['loan_needed'])}** (EMI ~{_money(cur['estimated_emi'])}/month) — "
                f"this change affects running costs, not the one-time setup.",
                f"ऋण **{_money(cur['loan_needed'])}** ही रहेगा (EMI ~{_money(cur['estimated_emi'])}/माह) — "
                f"यह बदलाव रोज़ की लागत पर असर डालता है, एकमुश्त लागत पर नहीं।",
                f"ঋণ **{_money(cur['loan_needed'])}**-ই থাকবে (EMI ~{_money(cur['estimated_emi'])}/মাস) — "
                f"এই বদল দৈনিক খরচে প্রভাব ফেলে, এককালীন খরচে নয়।"))
        cost_delta = imp["cost_change"]
        if abs(cost_delta) >= 1:
            answer_lines.append(_L(lang,
                f"Monthly running cost **{_money(cur['monthly_cost'])} → {_money(after['monthly_cost'])}/month**.",
                f"मासिक चालू लागत **{_money(cur['monthly_cost'])} → {_money(after['monthly_cost'])}/माह**।",
                f"মাসিক চলতি খরচ **{_money(cur['monthly_cost'])} → {_money(after['monthly_cost'])}/মাস**।"))
        surplus_delta = imp["surplus_change"]
        arrow = up_txt if surplus_delta >= 0 else down_txt
        answer_lines.append(_L(lang,
            f"Monthly surplus after the loan goes **{arrow} by {_money(abs(surplus_delta))}** "
            f"— {_money(cur_slanted['after_emi'])} → {_money(after['after_emi'])}/month. "
            f"Risk: {imp['old_risk']} → {imp['new_risk']}.",
            f"ऋण के बाद मासिक बचत **{_money(abs(surplus_delta))} {arrow} है** "
            f"— {_money(cur_slanted['after_emi'])} → {_money(after['after_emi'])}/माह। "
            f"जोखिम: {imp['old_risk']} → {imp['new_risk']}।",
            f"ঋণের পর মাসিক উদ্বৃত্ত **{_money(abs(surplus_delta))} {arrow}** "
            f"— {_money(cur_slanted['after_emi'])} → {_money(after['after_emi'])}/মাস। "
            f"ঝুঁকি: {imp['old_risk']} → {imp['new_risk']}।"))
    # 2) Topic mentioned but already in that state -> explain THAT topic only.
    elif not zero_cows and mentioned and not pct_only and new_cows is None and cap_delta == 0:
        for key in ("distributor", "fodder_land", "family_labour", "existing_shed"):
            if mentioned.get(key):
                txt = _topic_explainer(key)
                if txt:
                    answer_lines.append(txt)
                break
    # 3) Focused intents (no plan change) — answer only what was asked.
    if not answer_lines and not pct_only:
        if ask_about_risk and not ask_about_loan:
            ops = cur["operating_surplus"]
            emi = cur["estimated_emi"]
            share = (emi / ops * 100) if ops > 0 else 999
            top = []
            if share >= 60:
                top.append(_L(lang,
                    f"EMI burden: {_money(emi)}/month is {share:.0f}% of your {_money(ops)} operating surplus — one bad month flips you negative",
                    f"EMI बोझ: {_money(emi)}/माह आपके {_money(ops)} परिचालन अधिशेष का {share:.0f}% है — एक खराब महीना घाटे में डाल देगा",
                    f"EMI বোঝা: {_money(emi)}/মাস আপনার {_money(ops)} পরিচালন উদ্বৃত্তের {share:.0f}% — একটা খারাপ মাসেই লোকসান"))
            if cur["after_emi"] < 5000:
                top.append(_L(lang,
                    f"Thin cushion: only {_money(cur['after_emi'])}/month left after EMI (rated {cur['risk']})",
                    f"पतली बचत: EMI के बाद सिर्फ {_money(cur['after_emi'])}/माह बचता है ({cur['risk']})",
                    f"পাতলা সঞ্চয়: EMI-এর পর মাত্র {_money(cur['after_emi'])}/মাস থাকে ({cur['risk']})"))
            if not plan.get("distributor", True):
                top.append(_L(lang, "No assured buyer: a 10% price dip comes straight out of that thin surplus",
                    "कोई पक्का खरीदार नहीं: 10% भाव गिरते ही पतली बचत से कटेगा",
                    "কোনো পাকা ক্রেতা নেই: 10% দাম কমলেই পাতলা উদ্বৃত্ত থেকে যাবে"))
            if not plan.get("family_labour", True):
                top.append(_L(lang,
                    f"Hired labour locks in {_money(9000)}/month whether milk flows or not",
                    f"किराए का श्रम दूध हो या न हो {_money(9000)}/माह पक्का खर्च है",
                    f"ভাড়া শ্রম দুধ হোক বা না হোক {_money(9000)}/মাস পাকা খরচ"))
            if not plan.get("fodder_land", True):
                top.append(_L(lang, "Purchased fodder rises with every cow — feed inflation hits you fully",
                    "खरीदा चारा हर गाय के साथ बढ़ता है — चारा महँगाई पूरी लगेगी",
                    "কেনা খাদ্য প্রতি গরুর সঙ্গে বাড়ে — খাদ্য মূল্যবৃদ্ধি পুরো লাগবে"))
            if cur["loan_needed"] > max(4 * float(plan.get("capital", 0)), 150000):
                top.append(_L(lang,
                    f"High leverage: {_money(cur['loan_needed'])} borrowed on {_money(plan.get('capital', 0))} own capital",
                    f"अधिक कर्ज़: {_money(plan.get('capital', 0))} पूँजी पर {_money(cur['loan_needed'])} उधार",
                    f"বেশি ঋণ: {_money(plan.get('capital', 0))} মূলধনে {_money(cur['loan_needed'])} ধার"))
            if not top:
                top.append(_L(lang,
                    f"Cushion is {_money(cur['after_emi'])}/month ({cur['risk']}) — watch milk price and feed costs",
                    f"बचत {_money(cur['after_emi'])}/माह है ({cur['risk']}) — दूध भाव और चारा लागत पर नज़र रखें",
                    f"সঞ্চয় {_money(cur['after_emi'])}/মাস ({cur['risk']}) — দুধের দাম ও খাদ্য খরচে নজর রাখুন"))
            answer_lines.append(_L(lang,
                "Biggest risks for YOUR plan: " + "; ".join([f"{i+1}) {t}" for i, t in enumerate(top[:3])]) + ".",
                "आपकी योजना के सबसे बड़े जोखिम: " + "; ".join([f"{i+1}) {t}" for i, t in enumerate(top[:3])]) + "।",
                "আপনার পরিকল্পনার সবচেয়ে বড় ঝুঁকি: " + "; ".join([f"{i+1}) {t}" for i, t in enumerate(top[:3])]) + "।"))
        elif ask_about_loan:
            ops = cur["operating_surplus"]
            emi = cur["estimated_emi"]
            lev = cur["loan_needed"] / max(float(plan.get("capital", 0)), 1)
            verdict = _L(lang,
                "workable" if cur["risk"] == "LOW" else ("tight but serviceable" if cur["risk"] == "MEDIUM" else "unsafe at these costs"),
                "चलने योग्य" if cur["risk"] == "LOW" else ("तंग पर चल सकता" if cur["risk"] == "MEDIUM" else "इन लागतों पर असुरक्षित"),
                "চলার মতো" if cur["risk"] == "LOW" else ("টানটান কিন্তু চলতে পারে" if cur["risk"] == "MEDIUM" else "এই খরচে অনিরাপদ"))
            answer_lines.append(_L(lang,
                f"Your loan: **{_money(cur['loan_needed'])}** (project {_money(cur['project_cost'])} − capital {_money(plan.get('capital', 0))}), "
                f"EMI ~{_money(emi)}/month over 7 years @8%. That leaves **{_money(cur['after_emi'])}/month** — {verdict} "
                f"({lev:.1f}x leverage, EMI {((emi/ops*100) if ops>0 else 999):.0f}% of operating surplus).",
                f"आपका ऋण: **{_money(cur['loan_needed'])}** (लागत {_money(cur['project_cost'])} − पूँजी {_money(plan.get('capital', 0))}), "
                f"7 साल @8% पर EMI ~{_money(emi)}/माह। बचता है **{_money(cur['after_emi'])}/माह** — {verdict} "
                f"({lev:.1f}x कर्ज़, EMI परिचालन अधिशेष का {((emi/ops*100) if ops>0 else 999):.0f}%)।",
                f"আপনার ঋণ: **{_money(cur['loan_needed'])}** (খরচ {_money(cur['project_cost'])} − মূলধন {_money(plan.get('capital', 0))}), "
                f"7 বছর @8%-এ EMI ~{_money(emi)}/মাস। থাকে **{_money(cur['after_emi'])}/মাস** — {verdict} "
                f"({lev:.1f}x ঋণ, EMI পরিচালন উদ্বৃত্তের {((emi/ops*100) if ops>0 else 999):.0f}%)।"))
        elif ask_about_cows and new_cows is None:
            best_c, best_r = plan["cows"], cur
            for c in range(1, 13):
                tc = dict(plan)
                tc["cows"] = c
                r = analyze_dairy_plan(tc)
                if (_risk_rank(r["risk"]), r["after_emi"]) > (_risk_rank(best_r["risk"]), best_r["after_emi"]):
                    best_c, best_r = c, r
            if best_c == plan["cows"]:
                answer_lines.append(_L(lang,
                    f"At {plan['cows']} cows you are already at your sweet spot ({_money(cur['after_emi'])}/month, {cur['risk']}). "
                    f"Adding cows adds ~{_money(13212)}/month revenue each but also feed + EMI — beyond ~5 cows you need hired help, so it backfires.",
                    f"{plan['cows']} गायों पर आप पहले से सही जगह हैं ({_money(cur['after_emi'])}/माह, {cur['risk']})। "
                    f"हर गाय ~{_money(13212)}/माह आय जोड़ती है पर चारा + EMI भी — ~5 से ज़्यादा पर किराए की मदद चाहिए, उल्टा पड़ेगा।",
                    f"{plan['cows']} গরুতে আপনি আগেই সঠিক জায়গায় আছেন ({_money(cur['after_emi'])}/মাস, {cur['risk']})। "
                    f"প্রতি গরু ~{_money(13212)}/মাস আয় বাড়ায় কিন্তু খাদ্য + EMI-ও — ~5-এর বেশি হলে ভাড়া সাহায্য লাগে, উল্টো হবে।"))
            else:
                answer_lines.append(_L(lang,
                    f"For YOUR costs, **{best_c} cows** beats {plan['cows']} ({_money(best_r['after_emi'])} vs {_money(cur['after_emi'])}/month, "
                    f"{best_r['risk']} vs {cur['risk']}). Tap Optimize to apply it.",
                    f"आपकी लागतों पर **{best_c} गायें** {plan['cows']} से बेहतर हैं ({_money(cur['after_emi'])} बनाम {_money(best_r['after_emi'])}/माह, "
                    f"{cur['risk']} बनाम {best_r['risk']})। अपनाने के लिए Optimize दबाएँ।",
                    f"আপনার খরচে **{best_c} গরু** {plan['cows']}-এর চেয়ে ভালো ({_money(cur['after_emi'])} বনাম {_money(best_r['after_emi'])}/মাস, "
                    f"{cur['risk']} বনাম {best_r['risk']})। নিতে Optimize চাপুন।"))
        elif ask_about_capital and cap_delta == 0:
            need_low = None
            for c in range(1, 13):
                tc = dict(plan)
                tc["cows"] = c
                r = analyze_dairy_plan(tc)
                if r["risk"] == "LOW" and r["loan_needed"] <= max(4 * float(plan.get("capital", 0)), 150000):
                    need_low = (c, r)
                    break
            if need_low:
                answer_lines.append(_L(lang,
                    f"With {_money(plan.get('capital', 0))} own capital your plan is {cur['risk']} ({_money(cur['after_emi'])}/month). "
                    f"No extra capital strictly needed — {need_low[0]} cows already reaches LOW within a safe loan.",
                    f"{_money(plan.get('capital', 0))} पूँजी पर आपकी योजना {cur['risk']} है ({_money(cur['after_emi'])}/माह)। "
                    f"अतिरिक्त पूँजी ज़रूरी नहीं — {need_low[0]} गायें सुरक्षित ऋण में LOW तक पहुँचती हैं।",
                    f"{_money(plan.get('capital', 0))} মূলধনে আপনার পরিকল্পনা {cur['risk']} ({_money(cur['after_emi'])}/মাস)। "
                    f"বাড়তি মূলধন দরকার নেই — {need_low[0]} গরুতেই নিরাপদ ঋণে LOW হয়।"))
            else:
                answer_lines.append(_L(lang,
                    f"With {_money(plan.get('capital', 0))} capital you borrow {_money(cur['loan_needed'])} leaving {_money(cur['after_emi'])}/month ({cur['risk']}). "
                    f"Each extra {_money(50000)} of own capital cuts EMI ~{_money(780)}/month and straight adds to surplus.",
                    f"{_money(plan.get('capital', 0))} पूँजी पर {_money(cur['loan_needed'])} उधार लेकर {_money(cur['after_emi'])}/माह बचता है ({cur['risk']})। "
                    f"हर अतिरिक्त {_money(50000)} पूँजी EMI ~{_money(780)}/माह घटाकर सीधे बचत बढ़ाती है।",
                    f"{_money(plan.get('capital', 0))} মূলধনে {_money(cur['loan_needed'])} ধার নিয়ে {_money(cur['after_emi'])}/মাস থাকে ({cur['risk']})। "
                    f"প্রতি বাড়তি {_money(50000)} মূলধন EMI ~{_money(780)}/মাস কমিয়ে সরাসরি উদ্বৃত্ত বাড়ায়।"))
        elif ask_about_breakdown:
            m = analyze_dairy_plan.__globals__["MODEL"]["dairy"]
            cows = int(plan["cows"])
            animal = cows * m["cow_cost"]
            shed = 0 if plan["existing_shed"] else cows * m["shed_cost_per_cow"]
            if plan["existing_shed"]:
                shed = max(0, cows - int(m.get("shed_free_capacity", 3))) * m["shed_cost_per_cow"]
            answer_lines.append(_L(lang,
                f"Your {_money(cur['project_cost'])} setup: animals {_money(animal)}, shed {_money(shed)}, "
                f"equipment + transport + insurance make up the rest. You fund {_money(plan.get('capital', 0))}, borrow {_money(cur['loan_needed'])}.",
                f"आपकी {_money(cur['project_cost'])} लागत: पशु {_money(animal)}, शेड {_money(shed)}, "
                f"बाकी उपकरण + परिवहन + बीमा। आप {_money(plan.get('capital', 0))} लगाते हैं, {_money(cur['loan_needed'])} उधार।",
                f"আপনার {_money(cur['project_cost'])} খরচ: পশু {_money(animal)}, শেড {_money(shed)}, "
                f"বাকি যন্ত্র + পরিবহন + বীমা। আপনি {_money(plan.get('capital', 0))} দেন, {_money(cur['loan_needed'])} ধার।"))
        elif ask_about_milk_nopct:
            sens = cur["estimated_monthly_revenue"] * 0.10
            buyer_txt = _L(lang,
                "An assured buyer protects this — you have one.",
                "पक्का खरीदार इसे बचाता है — आपके पास है।",
                "পাকা ক্রেতা এটা রক্ষা করে — আপনার আছে।") if plan.get("distributor") else _L(lang,
                "You have NO assured buyer, so you already realize ~10% less — lock one first.",
                "आपके पास कोई पक्का खरीदार नहीं, इसलिए ~10% कम मिलता है — पहले खरीदार पक्का करें।",
                "আপনার কোনো পাকা ক্রেতা নেই, তাই ~10% কম পান — আগে ক্রেতা ঠিক করুন।")
            answer_lines.append(_L(lang,
                f"At {plan['cows']} cows you sell ~{plan['cows']*12*30} L/month @₹36.7 ≈ {_money(cur['estimated_monthly_revenue'])}/month. "
                f"Every 10% price move swings surplus by ≈{_money(sens)}/month (now {_money(cur['after_emi'])}, {cur['risk']}). " + buyer_txt,
                f"{plan['cows']} गायों पर ~{plan['cows']*12*30} लीटर/माह @₹36.7 ≈ {_money(cur['estimated_monthly_revenue'])}/माह। "
                f"भाव में हर 10% बदलाव बचत ≈{_money(sens)}/माह हिलाता है (अभी {_money(cur['after_emi'])}, {cur['risk']})। " + buyer_txt,
                f"{plan['cows']} গরুতে ~{plan['cows']*12*30} লিটার/মাস @₹36.7 ≈ {_money(cur['estimated_monthly_revenue'])}/মাস। "
                f"দামে প্রতি 10% বদল উদ্বৃত্ত ≈{_money(sens)}/মাস নাড়ায় (এখন {_money(cur['after_emi'])}, {cur['risk']})। " + buyer_txt))
        elif ask_about_feed_nopct:
            fodder_txt = _L(lang,
                "Your own fodder land already saves the green-fodder bill up to 4 cows.",
                "आपकी अपनी चारा ज़मीन 4 गायों तक हरे चारे का बिल बचाती है।",
                "আপনার নিজের খাদ্যের জমি 4 গরু পর্যন্ত সবুজ খাদ্যের বিল বাঁচায়।") if plan.get("fodder_land") else _L(lang,
                "Without fodder land you buy green fodder for every cow — securing land is your biggest lever.",
                "चारा ज़मीन बिना हर गाय के लिए हरा चारा खरीदना पड़ता है — ज़मीन ही सबसे बड़ा सहारा है।",
                "খাদ্যের জমি ছাড়া প্রতি গরুর জন্য সবুজ খাদ্য কিনতে হয় — জমিই সবচেয়ে বড় হাতিয়ার।")
            answer_lines.append(_L(lang,
                f"Feed is your biggest running cost inside {_money(cur['monthly_cost'])}/month total. " + fodder_txt +
                f" Surplus after EMI is {_money(cur['after_emi'])}/month ({cur['risk']}).",
                f"कुल {_money(cur['monthly_cost'])}/माह में चारा सबसे बड़ी चालू लागत है। " + fodder_txt +
                f" EMI के बाद बचत {_money(cur['after_emi'])}/माह है ({cur['risk']})।",
                f"মোট {_money(cur['monthly_cost'])}/মাসে খাদ্যই সবচেয়ে বড় চলতি খরচ। " + fodder_txt +
                f" EMI-এর পর উদ্বৃত্ত {_money(cur['after_emi'])}/মাস ({cur['risk']})।"))
        elif ask_about_surplus and not ask_about_loan:
            answer_lines.append(_L(lang,
                f"You net **{_money(cur['after_emi'])}/month** after all costs + EMI (operating {_money(cur['operating_surplus'])} − EMI {_money(cur['estimated_emi'])}), rated {cur['risk']}.",
                f"सभी लागत + EMI के बाद आप **{_money(cur['after_emi'])}/माह** पाते हैं (परिचालन {_money(cur['operating_surplus'])} − EMI {_money(cur['estimated_emi'])}), {cur['risk']} रेटिंग।",
                f"সব খরচ + EMI-এর পর আপনি **{_money(cur['after_emi'])}/মাস** পান (পরিচালন {_money(cur['operating_surplus'])} − EMI {_money(cur['estimated_emi'])}), {cur['risk']} রেটিং।"))
        elif ask_about_cost and not ask_about_loan:
            shed_w = _L(lang, "owned" if plan.get("existing_shed") else "new", "अपना" if plan.get("existing_shed") else "नया", "নিজের" if plan.get("existing_shed") else "নতুন")
            fod_w = _L(lang, "own land" if plan.get("fodder_land") else "purchased", "अपनी ज़मीन" if plan.get("fodder_land") else "खरीदा", "নিজের জমি" if plan.get("fodder_land") else "কেনা")
            lab_w = _L(lang, "family" if plan.get("family_labour") else "hired", "पारिवारिक" if plan.get("family_labour") else "किराए का", "পারিবারিক" if plan.get("family_labour") else "ভাড়া")
            answer_lines.append(_L(lang,
                f"Running cost is **{_money(cur['monthly_cost'])}/month** for {plan['cows']} cows at your settings "
                f"(shed: {shed_w}, fodder: {fod_w}, labour: {lab_w}). Surplus after EMI: {_money(cur['after_emi'])}/month.",
                f"{plan['cows']} गायों पर चालू लागत **{_money(cur['monthly_cost'])}/माह** है "
                f"(शेड: {shed_w}, चारा: {fod_w}, श्रम: {lab_w})। EMI के बाद बचत: {_money(cur['after_emi'])}/माह।",
                f"{plan['cows']} গরুতে চলতি খরচ **{_money(cur['monthly_cost'])}/মাস** "
                f"(শেড: {shed_w}, খাদ্য: {fod_w}, শ্রম: {lab_w})। EMI-এর পর উদ্বৃত্ত: {_money(cur['after_emi'])}/মাস।"))
        elif ask_greeting:
            answer_lines.append(_L(lang,
                f"Namaste! Your {plan['cows']}-cow plan nets {_money(cur['after_emi'])}/month ({cur['risk']}). Ask me about loan, profit, feed, milk price, or herd size.",
                f"नमस्ते! आपकी {plan['cows']} गायों वाली योजना {_money(cur['after_emi'])}/माह देती है ({cur['risk']})। ऋण, लाभ, चारा, दूध भाव या पशु संख्या पूछें।",
                f"নমস্কার! আপনার {plan['cows']} গরুর পরিকল্পনা {_money(cur['after_emi'])}/মাস দেয় ({cur['risk']})। ঋণ, লাভ, খাদ্য, দুধের দাম বা পশু নিয়ে জিজ্ঞাসা করুন।"))
        elif ask_thanks:
            answer_lines.append(_L(lang, "You're welcome — test one more what-if before you borrow.",
                "आपका स्वागत है — उधार से पहले एक और अगर-मगर परख लें।",
                "স্বাগতম — ধার নেওয়ার আগে আরও একটা কী-হলে যাচাই করুন।"))

    # Dedicated milk-price / feed-cost scenario line (absolute new values).
    if pct_only:
        if milk_pct is not None:
            direction = _L(lang, "up" if milk_pct > 0 else "down", "बढ़ता" if milk_pct > 0 else "घटता", "বাড়ে" if milk_pct > 0 else "কমে")
            pv = abs(milk_pct) * 100
            answer_lines.append(_L(lang,
                f"If the milk price moves {direction} {pv:.0f}%, your **monthly revenue becomes "
                f"{_money(after['estimated_monthly_revenue'])}/month** (from {_money(cur['estimated_monthly_revenue'])}) "
                f"and your **surplus after the loan is {_money(after['after_emi'])}/month** "
                f"(rated {after['risk']} risk).",
                f"दूध भाव {pv:.0f}% {direction} हो तो **मासिक आय "
                f"{_money(after['estimated_monthly_revenue'])}/माह** होगी ({_money(cur['estimated_monthly_revenue'])} से) "
                f"और **ऋण के बाद बचत {_money(after['after_emi'])}/माह** होगी ({after['risk']} जोखिम)।",
                f"দুধের দাম {pv:.0f}% {direction} হলে **মাসিক আয় "
                f"{_money(after['estimated_monthly_revenue'])}/মাস** হবে ({_money(cur['estimated_monthly_revenue'])} থেকে) "
                f"এবং **ঋণের পর উদ্বৃত্ত {_money(after['after_emi'])}/মাস** হবে ({after['risk']} ঝুঁকি)।"))
        if feed_pct is not None:
            fdirection = _L(lang, "up" if feed_pct > 0 else "down", "बढ़ती" if feed_pct > 0 else "घटती", "বাড়ে" if feed_pct > 0 else "কমে")
            fv = abs(feed_pct) * 100
            answer_lines.append(_L(lang,
                f"If feed costs move {fdirection} {fv:.0f}%, your **monthly cost becomes "
                f"{_money(after['monthly_cost'])}/month** (from {_money(cur['monthly_cost'])}) and your "
                f"**surplus after the loan is {_money(after['after_emi'])}/month** "
                f"(rated {after['risk']} risk).",
                f"चारा लागत {fv:.0f}% {fdirection} हो तो **मासिक लागत "
                f"{_money(after['monthly_cost'])}/माह** होगी ({_money(cur['monthly_cost'])} से) और "
                f"**ऋण के बाद बचत {_money(after['after_emi'])}/माह** होगी ({after['risk']} जोखिम)।",
                f"খাদ্য খরচ {fv:.0f}% {fdirection} হলে **মাসিক খরচ "
                f"{_money(after['monthly_cost'])}/মাস** হবে ({_money(cur['monthly_cost'])} থেকে) এবং "
                f"**ঋণের পর উদ্বৃত্ত {_money(after['after_emi'])}/মাস** হবে ({after['risk']} ঝুঁকি)।"))

    if not answer_lines:
        # Last resort: weakest-point pointer, not a full dump.
        tips = _structural_tips(plan, cur)
        tip_fix = tips[0]['fix'] if tips else ""
        tip_txt = _L(lang,
            f" Weakest point to probe: {tip_fix} (≈+{_money(tips[0]['gain'])}/month)." if tips else "",
            f" सबसे कमज़ोर कड़ी: {tip_fix} (≈+{_money(tips[0]['gain'])}/माह)।" if tips else "",
            f" সবচেয়ে দুর্বল জায়গা: {tip_fix} (≈+{_money(tips[0]['gain'])}/মাস)।" if tips else "")
        answer_lines.append(_L(lang,
            f"For YOUR {plan['cows']}-cow plan: surplus {_money(cur['after_emi'])}/month ({cur['risk']}), loan {_money(cur['loan_needed'])}."
            f"{tip_txt} Ask me e.g. 'what if I lose fodder land?' or 'is my loan safe?'",
            f"आपकी {plan['cows']} गायों वाली योजना: बचत {_money(cur['after_emi'])}/माह ({cur['risk']}), ऋण {_money(cur['loan_needed'])}।"
            f"{tip_txt} पूछें, जैसे 'चारा ज़मीन गई तो?' या 'क्या ऋण सुरक्षित है?'",
            f"আপনার {plan['cows']} গরুর পরিকল্পনা: উদ্বৃত্ত {_money(cur['after_emi'])}/মাস ({cur['risk']}), ঋণ {_money(cur['loan_needed'])}।"
            f"{tip_txt} জিজ্ঞাসা করুন, যেমন 'খাদ্যের জমি গেলে?' বা 'ঋণ কি নিরাপদ?'"))

    # Agent-specific outlook: same numbers, opposite jobs.
    # Advocate hunts upside (growth + best fix); challenger stress-tests (threat + buffer).
    try:
        tips_all = _structural_tips(plan, cur)
        top_tip = tips_all[0] if tips_all else None
        best_c, best_r = int(plan["cows"]), cur
        for c in range(1, 13):
            t = dict(plan)
            t["cows"] = c
            r = analyze_dairy_plan(t)
            if (r["after_emi"] > best_r["after_emi"] and _risk_rank(r["risk"]) >= _risk_rank(best_r["risk"])) or \
               (_risk_rank(r["risk"]) > _risk_rank(best_r["risk"])):
                best_c, best_r = c, r
        stress = cur["estimated_monthly_revenue"] * 0.10  # 10% milk dip ≈ this much surplus lost
        if req.agent == "challenger":
            if not pct_only:
                answer_lines.append(_L(lang,
                    f"Stress test: a 10% milk-price dip costs ≈{_money(stress)}/month → surplus ≈{_money(cur['after_emi'] - stress)}/month. "
                    f"Keep 2–3 months of {_money(cur['monthly_cost'])} costs as buffer.",
                    f"दबाव परीक्षण: दूध भाव 10% गिरे तो ≈{_money(stress)}/माह जाएगा → बचत ≈{_money(cur['after_emi'] - stress)}/माह। "
                    f"{_money(cur['monthly_cost'])} लागत का 2–3 महीने का भंडार रखें।",
                    f"চাপ পরীক্ষা: দুধের দাম 10% কমলে ≈{_money(stress)}/মাস যাবে → উদ্বৃত্ত ≈{_money(cur['after_emi'] - stress)}/মাস। "
                    f"{_money(cur['monthly_cost'])} খরচের 2–3 মাসের মজুত রাখুন।"))
        else:
            if top_tip and top_tip["gain"] >= 500:
                answer_lines.append(_L(lang,
                    f"Best upside from here: {top_tip['fix']} (≈+{_money(top_tip['gain'])}/month).",
                    f"यहाँ से सबसे बड़ा फायदा: {top_tip['fix']} (≈+{_money(top_tip['gain'])}/माह)।",
                    f"এখান থেকে সবচেয়ে বড় লাভ: {top_tip['fix']} (≈+{_money(top_tip['gain'])}/মাস)।"))
            if best_c != int(plan["cows"]) and best_r["after_emi"] > cur["after_emi"]:
                answer_lines.append(_L(lang,
                    f"Growth path: **{best_c} cows** reaches {_money(best_r['after_emi'])}/month ({best_r['risk']}) vs {_money(cur['after_emi'])} now — tap Optimize to apply it.",
                    f"बढ़त की राह: **{best_c} गायें** {_money(best_r['after_emi'])}/माह ({best_r['risk']}) तक पहुँचाती हैं, अभी {_money(cur['after_emi'])} से — अपनाने के लिए Optimize दबाएँ।",
                    f"বৃদ্ধির পথ: **{best_c} গরু** {_money(best_r['after_emi'])}/মাস ({best_r['risk']}) দেয়, এখন {_money(cur['after_emi'])} থেকে — নিতে Optimize চাপুন।"))
    except Exception:
        pass

    heading = "\n".join(lines)
    body = "\n".join(answer_lines)

    # Personality framing so the two debate agents keep distinct voices.
    if req.agent == "challenger":
        framing = _L(lang, "I'll play devil's advocate and flag what you should watch out for. ",
            "मैं आलोचक बनकर बताता हूँ कि किन बातों से सावधान रहें। ",
            "আমি সমালোচক হয়ে বলছি কোন বিষয়ে সাবধান থাকবেন। ")
    else:
        framing = _L(lang, "I'll find the opportunity here and push for the upside. ",
            "मैं यहाँ अवसर खोजकर फायदे की ओर धकेलूँगा। ",
            "আমি এখানে সুযোগ খুঁজে লাভের দিকে ঠেলব। ")

    full = _loc_text((heading + "\n" + framing + body).strip(), lang)

    return {
        "answer": full,
        "changed": applied,
        "current": {
            "loan_needed": cur["loan_needed"],
            "after_emi": cur["after_emi"],
            "risk": cur["risk"],
        },
        "after": {
            "loan_needed": after["loan_needed"],
            "after_emi": after["after_emi"],
            "risk": after["risk"],
        },
    }

@app.get("/")
def root():
    return {"message": "Saathi backend is running"}

recognizer = sr.Recognizer()

@app.post("/analyze")
def analyze(plan: DairyPlan):
    return analyze_dairy_plan(plan.model_dump())

SPEECH_LANGS = {"hi": "hi-IN", "bn": "bn-IN", "en": "en-IN"}


@app.post("/api/transcribe")
async def transcribe_audio(audio_file: UploadFile = File(...), lang: str = Form("en")):
    temp_file_path = f"temp_{audio_file.filename}"
    speech_lang = SPEECH_LANGS.get((lang or "en").lower()[:2], "en-IN")

    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)

    try:
        with sr.AudioFile(temp_file_path) as source:
            recognizer.adjust_for_ambient_noise(source)
            audio_data = recognizer.record(source)
            try:
                text = recognizer.recognize_google(audio_data, language=speech_lang)
            except sr.UnknownValueError:
                # Retry in English once (covers Hinglish/Banglish mixed speech).
                text = recognizer.recognize_google(audio_data, language="en-IN")

        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return JSONResponse(content={"transcript": text, "status": "success"})

    except sr.UnknownValueError:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        msg = {"hi": "ऑडियो समझ नहीं आया।", "bn": "অডিও বোঝা যায়নি।"}.get((lang or "en").lower()[:2], "Could not understand the audio.")
        return JSONResponse(content={"error": msg, "status": "failed"}, status_code=400)
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return JSONResponse(content={"error": str(e), "status": "failed"}, status_code=500)


# ---------- Serve frontend static files ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return FileResponse(os.path.join(BASE_DIR, "login.html"))


@app.get("/business", response_class=HTMLResponse)
def business_page():
    return FileResponse(os.path.join(BASE_DIR, "business.html"))


@app.get("/index", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
def index_page():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


app.mount("/assets", StaticFiles(directory=os.path.join(BASE_DIR, "assets")), name="assets")
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")
