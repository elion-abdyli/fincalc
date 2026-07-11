"""
Streamlit app: map of two university pavilions with 50 km / 150 km radius circles,
plus every OSM-tagged pharmacy in the province of Quebec.

Run with:
    pip install streamlit folium streamlit-folium duckdb
    streamlit run app.py
"""

from datetime import datetime, timezone
from urllib.parse import quote
import urllib.request

import duckdb
import folium
import pandas as pd
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
OVERPASS_BASE = "https://overpass-api.de/api/interpreter"
DB_PATH     = "/tmp/pharmacies.duckdb"
PAYLOAD_PATH = "/tmp/osm_payload.json"
CACHE_TTL_SECONDS = 86_400  

# ---------------------------------------------------------------
# DuckDB helpers
# ---------------------------------------------------------------
@st.cache_resource
def get_db():
    """Open the DuckDB file, seed the pavilions reference table, and load httpfs."""
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("""
        CREATE TABLE IF NOT EXISTS pavilions (
            name  VARCHAR PRIMARY KEY,
            lat   DOUBLE,
            lon   DOUBLE,
            color VARCHAR
        )
    """)
    con.executemany(
        """INSERT INTO pavilions (name, lat, lon, color) VALUES (?, ?, ?, ?)
           ON CONFLICT (name) DO UPDATE
           SET lat = excluded.lat, lon = excluded.lon, color = excluded.color""",
        [(loc["name"], loc["lat"], loc["lon"], loc["color"]) for loc in LOCATIONS],
    )
    return con


def _is_cache_fresh(con):
    try:
        row = con.execute("SELECT fetched_at FROM metadata").fetchone()
    except duckdb.CatalogException:
        return False
    if row is None:
        return False
    fetched_at = row[0]
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() < CACHE_TTL_SECONDS


def _load_pharmacies(con, band_filter) -> pd.DataFrame:
    """Query the appropriate gold view for the selected distance band."""
    view = (
        "vw_50km"       if band_filter.startswith("50")
        else "vw_150km"  if band_filter.startswith("150")
        else "vw_all_quebec"
    )
    return con.execute(
        f"SELECT name, lat, lon, address, operator, hours, "
        f"nearest_pavilion, color, nearest_dist_km, all_dists "
        f"FROM {view} ORDER BY nearest_dist_km"
    ).df()


# ---------------------------------------------------------------
# ETL — Land → Bronze → Silver → Gold → Views
# ---------------------------------------------------------------
def _download_payload():
    """Fetch the Overpass JSON response and write it to disk."""
    overpass_ql = (
        "[out:json][timeout:180];"
        'area["ISO3166-2"="CA-QC"]->.qc;'
        '(nwr["amenity"="pharmacy"](area.qc););'
        "out center tags;"
    )
    url = OVERPASS_BASE + "?data=" + quote(overpass_ql)
    with urllib.request.urlopen(url, timeout=200) as resp:
        with open(PAYLOAD_PATH, "wb") as f:
            f.write(resp.read())


def _etl_bronze(con):
    """Bronze: SELECT * from the raw JSON file on disk."""
    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_osm_elements AS
        SELECT
            element->>'type'       AS osm_type,
            (element->>'id')::BIGINT AS osm_id,
            element                AS raw
        FROM (
            SELECT UNNEST(elements) AS element
            FROM read_json('{PAYLOAD_PATH}', columns={{'elements': 'JSON[]'}})
        )
        WHERE element->>'type' IN ('node', 'way', 'relation')
    """)


def _etl_silver(con):
    """Silver: parse and clean bronze into typed columns."""
    con.execute("""
        CREATE OR REPLACE TABLE silver_pharmacies AS
        WITH staged AS (
            SELECT DISTINCT ON (osm_type, osm_id)
                osm_type,
                osm_id,
                COALESCE(
                    NULLIF(json_extract_string(raw, '$.tags.name'), ''),
                    'Pharmacy (unnamed)'
                ) AS name,
                COALESCE(
                    TRY_CAST(json_extract_string(raw, '$.lat')        AS DOUBLE),
                    TRY_CAST(json_extract_string(raw, '$.center.lat') AS DOUBLE)
                ) AS lat,
                COALESCE(
                    TRY_CAST(json_extract_string(raw, '$.lon')        AS DOUBLE),
                    TRY_CAST(json_extract_string(raw, '$.center.lon') AS DOUBLE)
                ) AS lon,
                CONCAT_WS(', ',
                    NULLIF(TRIM(CONCAT_WS(' ',
                        json_extract_string(raw, '$.tags[''addr:housenumber'']'),
                        json_extract_string(raw, '$.tags[''addr:street'']')
                    )), ''),
                    json_extract_string(raw, '$.tags[''addr:city'']')
                ) AS address,
                COALESCE(json_extract_string(raw, '$.tags.operator'), '')      AS operator,
                COALESCE(json_extract_string(raw, '$.tags.opening_hours'), '') AS hours
            FROM bronze_osm_elements
            ORDER BY osm_type, osm_id
        )
        SELECT * FROM staged
        WHERE lat IS NOT NULL AND lon IS NOT NULL
    """)


def _etl_gold(con):
    """Gold: denormalized master table with pre-computed distances, then three views."""
    con.execute("""
        CREATE OR REPLACE TABLE gold_pharmacies AS
        WITH all_dists AS (
            SELECT
                s.osm_type, s.osm_id,
                s.name, s.lat, s.lon, s.address, s.operator, s.hours,
                pav.name  AS pavilion_name,
                pav.color AS color,
                ST_Distance_Spheroid(
                    ST_Point(s.lon, s.lat),
                    ST_Point(pav.lon, pav.lat)
                ) / 1000.0 AS dist_km
            FROM silver_pharmacies s
            CROSS JOIN pavilions pav
        ),
        nearest AS (
            SELECT DISTINCT ON (osm_type, osm_id)
                osm_type, osm_id,
                name, lat, lon, address, operator, hours,
                pavilion_name AS nearest_pavilion,
                color,
                dist_km       AS nearest_dist_km
            FROM all_dists
            ORDER BY osm_type, osm_id, dist_km
        ),
        dists_agg AS (
            SELECT
                osm_type, osm_id,
                LIST({'pavilion': pavilion_name, 'dist_km': dist_km}
                     ORDER BY dist_km) AS all_dists
            FROM all_dists
            GROUP BY osm_type, osm_id
        )
        SELECT
            n.osm_type, n.osm_id,
            n.name, n.lat, n.lon, n.address, n.operator, n.hours,
            n.nearest_pavilion, n.color, n.nearest_dist_km,
            d.all_dists
        FROM nearest n
        JOIN dists_agg d USING (osm_type, osm_id)
    """)
    
    con.execute("CREATE OR REPLACE VIEW vw_50km        AS SELECT * FROM gold_pharmacies WHERE nearest_dist_km <= 50")
    con.execute("CREATE OR REPLACE VIEW vw_150km       AS SELECT * FROM gold_pharmacies WHERE nearest_dist_km <= 150")
    con.execute("CREATE OR REPLACE VIEW vw_all_quebec  AS SELECT * FROM gold_pharmacies")


def fetch_pharmacies(force_refresh=False):
    """Run the full Land → Bronze → Silver → Gold pipeline if the cache is stale."""
    con = get_db()
    if not force_refresh and _is_cache_fresh(con):
        return

    _download_payload()
    _etl_bronze(con)
    _etl_silver(con)
    _etl_gold(con)

    count = con.execute("SELECT COUNT(*) FROM gold_pharmacies").fetchone()[0]
    if count == 0:
        raise ValueError("ETL produced 0 pharmacies — aborting.")

    con.execute("""
        CREATE OR REPLACE TABLE metadata AS
        SELECT now()::TIMESTAMPTZ AS fetched_at
    """)


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
            fetch_pharmacies(force_refresh=force_refresh)
        except Exception as e:
            st.sidebar.error(f"Pipeline failed: {e}")

    try:
        pharmacies = _load_pharmacies(get_db(), band_filter)
    except Exception as e:
        pharmacies = pd.DataFrame()
        st.sidebar.error(f"Database query failed: {e}")

    layer = folium.FeatureGroup(name="Pharmacies").add_to(m)
    target = MarkerCluster().add_to(layer) if cluster else layer

    def _add_marker(row):
        detail = "".join(
            f"<br>{lbl}" for lbl in (row.address, row.operator, row.hours) if lbl
        )
        dist_lines = "".join(
            f"<br>{d['dist_km']:.1f} km — {d['pavilion'].split('—')[0].strip()}"
            for d in row.all_dists
        )
        folium.CircleMarker(
            location=[row.lat, row.lon],
            radius=4,
            color=row.color,
            weight=1,
            fill=True,
            fill_color=row.color,
            fill_opacity=0.75,
            popup=folium.Popup(
                f"<b>{row['name']}</b>{detail}<hr style='margin:4px 0'>{dist_lines}",
                max_width=280,
            ),
            tooltip=f"{row['name']} — {row.nearest_dist_km:.1f} km",
        ).add_to(target)

    pharmacies.apply(_add_marker, axis=1)

    st.sidebar.metric("Pharmacies shown", len(pharmacies))

st_folium(m, use_container_width=True, height=1200, returned_objects=[])