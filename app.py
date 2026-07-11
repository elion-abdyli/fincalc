"""
Streamlit app: map of two university pavilions with 50 km / 150 km radius circles,
plus every OSM-tagged pharmacy in the province of Quebec.

Run with:
    pip install streamlit folium streamlit-folium duckdb
    streamlit run app.py
"""

from datetime import datetime, timezone
from urllib.parse import quote

import duckdb
import folium
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
DB_PATH = "/tmp/pharmacies.duckdb"
CACHE_TTL_SECONDS = 86_400  # 24 hours

# ---------------------------------------------------------------
# DuckDB helpers
# ---------------------------------------------------------------
@st.cache_resource
def get_db():
    """Return a single shared DuckDB connection, creating the schema if needed."""
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze_osm_elements (
            fetched_at TIMESTAMPTZ,
            osm_type   VARCHAR,
            osm_id     BIGINT,
            raw        JSON
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS silver_pharmacies (
            osm_type  VARCHAR,
            osm_id    BIGINT,
            name      VARCHAR,
            lat       DOUBLE,
            lon       DOUBLE,
            address   VARCHAR,
            operator  VARCHAR,
            hours     VARCHAR,
            loaded_at TIMESTAMPTZ,
            PRIMARY KEY (osm_type, osm_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR
        )
    """)
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
    con.execute("LOAD httpfs")
    con.execute("SET http_timeout = 200000")  # ms
    con.execute("""
        CREATE OR REPLACE VIEW vw_all_dists AS
        SELECT
            p.osm_type, p.osm_id,
            p.name, p.lat, p.lon, p.address, p.operator, p.hours,
            pav.name  AS pavilion_name,
            pav.color AS color,
            2 * 6371.0 * asin(sqrt(
                pow(sin(radians((p.lat - pav.lat) / 2.0)), 2) +
                cos(radians(pav.lat)) * cos(radians(p.lat)) *
                pow(sin(radians((p.lon - pav.lon) / 2.0)), 2)
            )) AS dist_km
        FROM silver_pharmacies p
        CROSS JOIN pavilions pav
    """)
    con.execute("""
        CREATE OR REPLACE VIEW vw_nearest AS
        SELECT DISTINCT ON (osm_type, osm_id)
            osm_type, osm_id,
            name, lat, lon, address, operator, hours,
            pavilion_name AS nearest_pavilion,
            color,
            dist_km      AS nearest_dist_km
        FROM vw_all_dists
        ORDER BY osm_type, osm_id, dist_km
    """)
    con.execute("""
        CREATE OR REPLACE VIEW vw_dists_agg AS
        SELECT
            osm_type, osm_id,
            LIST({'pavilion': pavilion_name, 'dist_km': dist_km}
                 ORDER BY dist_km) AS all_dists
        FROM vw_all_dists
        GROUP BY osm_type, osm_id
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


def _load_pharmacies(con, max_dist_km):
    """Query pre-classified pharmacies from DuckDB views, filtered by distance."""
    rows = con.execute("""
        SELECT
            n.name, n.lat, n.lon, n.address, n.operator, n.hours,
            n.nearest_pavilion, n.color, n.nearest_dist_km,
            d.all_dists
        FROM vw_nearest n
        JOIN vw_dists_agg d USING (osm_type, osm_id)
        WHERE n.nearest_dist_km <= ?
        ORDER BY n.nearest_dist_km
    """, [max_dist_km]).fetchall()
    keys = ("name", "lat", "lon", "address", "operator", "hours",
            "nearest_pavilion", "color", "nearest_dist_km", "all_dists")
    return [dict(zip(keys, row)) for row in rows]


# ---------------------------------------------------------------
# ETL — Bronze → Silver → Gold
# ---------------------------------------------------------------
def _etl_bronze(con):
    """Load raw OSM elements from Overpass into the bronze table via DuckDB httpfs."""
    overpass_ql = (
        "[out:json][timeout:180];"
        'area["ISO3166-2"="CA-QC"]->.qc;'
        '(nwr["amenity"="pharmacy"](area.qc););'
        "out center tags;"
    )
    url = OVERPASS_BASE + "?data=" + quote(overpass_ql)

    con.execute("DELETE FROM bronze_osm_elements")
    con.execute("""
        INSERT INTO bronze_osm_elements (fetched_at, osm_type, osm_id, raw)
        SELECT
            now()::TIMESTAMPTZ,
            element->>'type',
            (element->>'id')::BIGINT,
            element
        FROM (
            SELECT UNNEST(elements) AS element
            FROM read_json(?, columns={'elements': 'JSON[]'})
        )
        WHERE element->>'type' IN ('node', 'way', 'relation')
    """, [url])


def _etl_silver(con):
    """Parse and clean bronze elements into the silver pharmacies table."""
    con.execute("DELETE FROM silver_pharmacies")
    con.execute("""
        INSERT INTO silver_pharmacies
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
                COALESCE(json_extract_string(raw, '$.tags.opening_hours'), '') AS hours,
                now()::TIMESTAMPTZ AS loaded_at
            FROM bronze_osm_elements
            ORDER BY osm_type, osm_id, fetched_at DESC
        )
        SELECT osm_type, osm_id, name, lat, lon, address, operator, hours, loaded_at
        FROM staged
        WHERE lat IS NOT NULL AND lon IS NOT NULL
    """)


def fetch_pharmacies(force_refresh=False):
    """Orchestrate the full Bronze → Silver ETL inside a single transaction."""
    con = get_db()
    if not force_refresh and _is_cache_fresh(con):
        return

    con.execute("BEGIN")
    try:
        _etl_bronze(con)
        _etl_silver(con)

        count = con.execute("SELECT COUNT(*) FROM silver_pharmacies").fetchone()[0]
        if count == 0:
            raise ValueError("ETL produced 0 pharmacies — aborting to preserve existing data.")

        con.execute(
            """INSERT INTO metadata (key, value) VALUES ('fetched_at', ?)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
            [datetime.now(timezone.utc).isoformat()],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


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
    max_dist_km = 50.0 if band_filter.startswith("50") else (150.0 if band_filter.startswith("150") else 1_000_000.0)

    with st.spinner("Loading pharmacy data…"):
        try:
            fetch_pharmacies(force_refresh=force_refresh)
        except Exception as e:
            st.sidebar.error(f"Overpass query failed: {e}")

    try:
        pharmacies = _load_pharmacies(get_db(), max_dist_km)
    except Exception as e:
        pharmacies = []
        st.sidebar.error(f"Database query failed: {e}")

    layer = folium.FeatureGroup(name="Pharmacies").add_to(m)
    target = MarkerCluster().add_to(layer) if cluster else layer

    for p in pharmacies:
        detail = "".join(
            f"<br>{lbl}" for lbl in (p["address"], p["operator"], p["hours"]) if lbl
        )
        dist_lines = "".join(
            f"<br>{d['dist_km']:.1f} km — {d['pavilion'].split('—')[0].strip()}"
            for d in p["all_dists"]
        )

        folium.CircleMarker(
            location=[p["lat"], p["lon"]],
            radius=4,
            color=p["color"],
            weight=1,
            fill=True,
            fill_color=p["color"],
            fill_opacity=0.75,
            popup=folium.Popup(
                f"<b>{p['name']}</b>{detail}<hr style='margin:4px 0'>{dist_lines}",
                max_width=280,
            ),
            tooltip=f"{p['name']} — {p['nearest_dist_km']:.1f} km",
        ).add_to(target)

    st.sidebar.metric("Pharmacies shown", len(pharmacies))

st_folium(m, use_container_width=True, height=1200, returned_objects=[])