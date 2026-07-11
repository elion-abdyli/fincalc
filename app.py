"""
Streamlit app: map of two university pavilions with 50 km / 150 km radius circles,
plus every OSM-tagged pharmacy in the province of Quebec.

Run with:
    pip install streamlit folium streamlit-folium requests duckdb
    streamlit run app.py
"""

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

import duckdb
import folium
import requests
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

st.set_page_config(page_title="Pavilion radius map", layout="wide")

# ---------------------------------------------------------------
# Full-screen CSS: strip Streamlit padding/header, let map fill viewport
# ---------------------------------------------------------------
st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {
          padding: 0 !important;
          margin: 0 !important;
          max-width: 100% !important;
      }
      [data-testid="stAppViewContainer"] > .main {
          overflow: hidden;
      }
      iframe[title="streamlit_folium.st_folium"] {
          width: 100vw !important;
          height: 100vh !important;
      }
      div[data-testid="stVerticalBlock"] {gap: 0rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------
# Locations (exact coordinates)
# ---------------------------------------------------------------
LOCATIONS = [
    {
        "name": "Pavillon Jean-Coutu — Université de Montréal",
        "address": "2940 Chemin de Polytechnique, Montréal, QC",
        "lat": 45.5003731,
        "lon": -73.6147689,
        "color": "blue",
    },
    {
        "name": "Pavillon Ferdinand-Vandry — Université Laval",
        "address": "1600 Avenue des Sciences-de-la-Vie, Québec City, QC",
        "lat": 46.7778727,
        "lon": -71.2778118,
        "color": "red",
    },
]

RADII_KM = [50, 150]
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DB_PATH = "pharmacies.duckdb"
CACHE_TTL_SECONDS = 86_400  # 24 hours

# ---------------------------------------------------------------
# DuckDB helpers
# ---------------------------------------------------------------
@st.cache_resource
def get_db():
    """Return a single shared DuckDB connection, creating the schema if needed."""
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pharmacies (
            osm_type VARCHAR,
            osm_id   BIGINT,
            name     VARCHAR,
            lat      DOUBLE,
            lon      DOUBLE,
            address  VARCHAR,
            operator VARCHAR,
            hours    VARCHAR,
            PRIMARY KEY (osm_type, osm_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR
        )
    """)
    return con


def _is_cache_fresh(con):
    row = con.execute(
        "SELECT value FROM metadata WHERE key = 'fetched_at'"
    ).fetchone()
    if row is None:
        return False
    fetched_at = datetime.fromisoformat(row[0])
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() < CACHE_TTL_SECONDS


def _load_pharmacies(con):
    rows = con.execute(
        "SELECT name, lat, lon, address, operator, hours FROM pharmacies"
    ).fetchall()
    keys = ("name", "lat", "lon", "address", "operator", "hours")
    return [dict(zip(keys, row)) for row in rows]


def _save_pharmacies(con, records):
    con.execute("DELETE FROM pharmacies")
    con.executemany(
        "INSERT INTO pharmacies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (r["osm_type"], r["osm_id"], r["name"], r["lat"],
             r["lon"], r["address"], r["operator"], r["hours"])
            for r in records
        ],
    )
    con.execute(
        """INSERT INTO metadata (key, value) VALUES ('fetched_at', ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
        [datetime.now(timezone.utc).isoformat()],
    )


# ---------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def fetch_pharmacies(force_refresh=False):
    """Load pharmacies from DuckDB cache, or fetch from Overpass if stale/forced."""
    con = get_db()
    if not force_refresh and _is_cache_fresh(con):
        return _load_pharmacies(con)

    query = (
        "[out:json][timeout:180];"
        'area["ISO3166-2"="CA-QC"]->.qc;'
        '(nwr["amenity"="pharmacy"](area.qc););'
        "out center tags;"
    )
    headers = {"User-Agent": "fincalc/0.1 (educational project)"}
    r = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=120)
    r.raise_for_status()

    seen, records = set(), []
    for el in r.json().get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        key = (el["type"], el["id"])
        if key in seen:
            continue
        seen.add(key)
        tags = el.get("tags", {})
        street = " ".join(
            p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
        )
        records.append({
            "osm_type": el["type"],
            "osm_id":   el["id"],
            "name":     tags.get("name", "Pharmacy (unnamed)"),
            "lat":      lat,
            "lon":      lon,
            "address":  ", ".join(p for p in (street, tags.get("addr:city")) if p),
            "operator": tags.get("operator", ""),
            "hours":    tags.get("opening_hours", ""),
        })

    _save_pharmacies(con, records)
    return _load_pharmacies(con)


def classify(pharm, locations):
    """Nearest pavilion and its distance, colored by that pavilion."""
    dists = {
        loc["name"]: haversine_km(pharm["lat"], pharm["lon"], loc["lat"], loc["lon"])
        for loc in locations
    }
    nearest = min(dists, key=dists.get)
    d = dists[nearest]
    color = next(l["color"] for l in locations if l["name"] == nearest)
    return nearest, d, color, dists


# ---------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------
with st.sidebar:
    st.header("Pavilion coverage map")

    show_50 = st.checkbox("Show 50 km circles", value=True)
    show_150 = st.checkbox("Show 150 km circles", value=True)

    st.divider()
    st.subheader("Pharmacies (OSM)")
    show_pharmacies = st.checkbox("Show pharmacies", value=True)
    band_filter = st.radio(
        "Include pharmacies within",
        ["50 km only", "150 km only", "All Quebec"],
        index=2,
        disabled=not show_pharmacies,
    )
    cluster = st.checkbox(
        "Cluster markers", value=True, disabled=not show_pharmacies,
        help="Uncheck to see every pin individually — slow above ~500 points.",
    )
    force_refresh = st.button(
        "Refresh OSM data",
        disabled=not show_pharmacies,
        help="Re-fetch pharmacy data from OpenStreetMap (ignores the 24 h cache).",
    )

    st.caption(
        "Blue = Pavillon Jean-Coutu (UdeM, Montréal)\n\n"
        "Red = Pavillon Ferdinand-Vandry (ULaval, Québec City)\n\n"
        "Solid = 50 km · dashed = 150 km. True geodesic radii.\n\n"
        "Pharmacy dots are colored by their nearest pavilion."
    )

# ---------------------------------------------------------------
# Build the map (centered between the two campuses)
# ---------------------------------------------------------------
center_lat = sum(loc["lat"] for loc in LOCATIONS) / len(LOCATIONS)
center_lon = sum(loc["lon"] for loc in LOCATIONS) / len(LOCATIONS)

m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

for loc in LOCATIONS:
    folium.Marker(
        location=[loc["lat"], loc["lon"]],
        popup=folium.Popup(f"<b>{loc['name']}</b><br>{loc['address']}", max_width=280),
        tooltip=loc["name"],
        icon=folium.Icon(color=loc["color"], icon="graduation-cap", prefix="fa"),
    ).add_to(m)

    for radius_km in RADII_KM:
        if radius_km == 50 and not show_50:
            continue
        if radius_km == 150 and not show_150:
            continue
        folium.Circle(
            location=[loc["lat"], loc["lon"]],
            radius=radius_km * 1000,
            color=loc["color"],
            weight=2,
            fill=True,
            fill_opacity=0.08 if radius_km == 150 else 0.15,
            dash_array="6" if radius_km == 150 else None,
            tooltip=f"{radius_km} km around {loc['name']}",
        ).add_to(m)

# ---------------------------------------------------------------
# Pharmacies
# ---------------------------------------------------------------
if show_pharmacies:
    with st.spinner("Loading pharmacy data…"):
        try:
            pharmacies = fetch_pharmacies(force_refresh=force_refresh)
        except Exception as e:
            pharmacies = []
            st.sidebar.error(f"Overpass query failed: {e}")

    max_dist = 50.0 if band_filter.startswith("50") else (150.0 if band_filter.startswith("150") else float("inf"))

    layer = folium.FeatureGroup(name="Pharmacies").add_to(m)
    target = MarkerCluster().add_to(layer) if cluster else layer

    shown = 0
    for p in pharmacies:
        nearest, dist, color, dists = classify(p, LOCATIONS)
        if dist > max_dist:
            continue
        shown += 1

        detail = "".join(
            f"<br>{lbl}" for lbl in (p["address"], p["operator"], p["hours"]) if lbl
        )
        dist_lines = "".join(
            f"<br>{d:.1f} km — {n.split('—')[0].strip()}" for n, d in sorted(dists.items(), key=lambda kv: kv[1])
        )

        folium.CircleMarker(
            location=[p["lat"], p["lon"]],
            radius=4,
            color=color,
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            popup=folium.Popup(
                f"<b>{p['name']}</b>{detail}<hr style='margin:4px 0'>{dist_lines}",
                max_width=280,
            ),
            tooltip=f"{p['name']} — {dist:.1f} km",
        ).add_to(target)

    st.sidebar.metric("Pharmacies shown", shown)

st_folium(m, use_container_width=True, height=1200, returned_objects=[])