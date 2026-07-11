import duckdb
import folium
import streamlit as st
from streamlit_folium import st_folium

DB_PATH = "osm.duckdb"

st.set_page_config(page_title="Pavilion map", layout="wide")

st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {padding: 0 !important; margin: 0 !important; max-width: 100% !important;}
      iframe[title="streamlit_folium.st_folium"] {width: 100vw !important; height: 100vh !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

PAVILIONS = [
    ("Pavillon Jean-Coutu — Université de Montréal", 45.5003731, -73.6147689, "blue"),
    ("Pavillon Ferdinand-Vandry — Université Laval", 46.7778727, -71.2778118, "red"),
]

ETL_SQL = """
INSTALL spatial; LOAD spatial;
SET force_download = true;

CREATE OR REPLACE TABLE bronze AS
SELECT * FROM read_json('https://overpass-api.de/api/interpreter?data=[out:json];area["ISO3166-2"="CA-QC"]->.searchArea;node["amenity"="pharmacy"](area.searchArea);out;');

CREATE OR REPLACE TABLE silver_pharmacies AS
WITH elems AS (
    SELECT unnest(elements, recursive := true) FROM bronze
)
SELECT
    id AS osm_id,
    tags,
    ST_SetCRS(ST_Point(lon, lat), 'OGC:CRS84') AS geom
FROM elems
WHERE lat IS NOT NULL AND lon IS NOT NULL;
"""


def run_etl():
    con = duckdb.connect(DB_PATH)
    try:
        con.execute(ETL_SQL)
    finally:
        con.close()


@st.cache_data
def load_points():
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        return con.execute("""
            SELECT
                ST_Y(geom) AS lat,
                ST_X(geom) AS lon,
                concat_ws('<br>',
                    '<b>' || coalesce(tags['name'], 'Pharmacy') || '</b>',
                    nullif(trim(concat_ws(' ', tags['addr:housenumber'], tags['addr:street'])), ''),
                    tags['addr:city'],
                    tags['operator'],
                    tags['opening_hours']
                ) AS tooltip_html
            FROM silver_pharmacies
        """).df()
    finally:
        con.close()


def table_exists():
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        return bool(con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'silver_pharmacies'"
        ).fetchone()[0])
    except duckdb.Error:
        return False
    finally:
        con.close()


with st.sidebar:
    if st.button("Refresh data (run ETL)"):
        with st.spinner("Fetching from Overpass…"):
            run_etl()
        load_points.clear()

if not table_exists():
    with st.spinner("First run: fetching data from Overpass…"):
        run_etl()

points = load_points()
st.sidebar.metric("Pharmacies", len(points))

center_lat = sum(p[1] for p in PAVILIONS) / len(PAVILIONS)
center_lon = sum(p[2] for p in PAVILIONS) / len(PAVILIONS)

m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

for name, lat, lon, color in PAVILIONS:
    folium.Marker(
        location=[lat, lon],
        tooltip=name,
        icon=folium.Icon(color=color, icon="graduation-cap", prefix="fa"),
    ).add_to(m)
    folium.Circle(location=[lat, lon], radius=50_000, color=color, weight=2, fill=True, fill_opacity=0.15).add_to(m)
    folium.Circle(location=[lat, lon], radius=150_000, color=color, weight=2, fill=True, fill_opacity=0.08, dash_array="6").add_to(m)

for row in points.itertuples():
    folium.CircleMarker(
        location=[row.lat, row.lon],
        radius=4,
        color="green",
        weight=1,
        fill=True,
        fill_opacity=0.75,
        tooltip=row.tooltip_html,
    ).add_to(m)

st_folium(m, use_container_width=True, height=1200, returned_objects=[])