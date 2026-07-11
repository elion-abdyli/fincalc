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
    {"name": "Pavillon Jean-Coutu — Université de Montréal", "short": "UdeM",
     "lat": 45.5003731, "lon": -73.6147689, "color": "blue"},
    {"name": "Pavillon Ferdinand-Vandry — Université Laval", "short": "ULaval",
     "lat": 46.7778727, "lon": -71.2778118, "color": "red"},
]

ETL_SQL = """
SET force_download = true;

CREATE OR REPLACE TABLE bronze AS
SELECT * FROM read_json('https://overpass-api.de/api/interpreter?data=[out:json];area["ISO3166-2"="CA-QC"]->.searchArea;node["amenity"="pharmacy"](area.searchArea);out;');

CREATE OR REPLACE TABLE silver_pharmacies AS
WITH elems AS (
    SELECT unnest(elements, recursive := true) FROM bronze
)
SELECT id AS osm_id, tags, lat, lon
FROM elems
WHERE lat IS NOT NULL AND lon IS NOT NULL;
"""

POINTS_SQL = """
WITH pav(short, plat, plon) AS (
    VALUES
        ('UdeM',   45.5003731, -73.6147689),
        ('ULaval', 46.7778727, -71.2778118)
),
pairs AS (
    SELECT
        s.osm_id,
        p.short,
        ST_Distance_Spheroid(
            ST_Point2D(s.lon, s.lat),
            ST_Point2D(p.plon, p.plat)
        ) / 1000 AS km
    FROM silver_pharmacies s
    CROSS JOIN pav p
),
dist AS (
    SELECT
        osm_id,
        string_agg(
            round(km, 1)::VARCHAR || ' km — ' || short,
            '<br>'
            ORDER BY km
        ) AS dist_html
    FROM pairs
    GROUP BY osm_id
)
SELECT
    s.lat,
    s.lon,
    '<b>' || coalesce(s.tags['name'], 'Pharmacy') || '</b>'
    || coalesce('<br>' || array_to_string(
        list_transform(
            list_filter(
                map_entries(s.tags),
                lambda e: e.key NOT IN (
                    'name', 'amenity', 'healthcare',
                    'brand:wikidata', 'brand:wikipedia'
                )
            ),
            lambda e: '<i>' || e.key || ':</i> ' || e.value
        ),
        '<br>'
    ), '')
    || '<hr style="margin:4px 0">' || d.dist_html AS tooltip_html
FROM silver_pharmacies s
JOIN dist d USING (osm_id)
"""


@st.cache_data
def load_points():
    con = duckdb.connect(DB_PATH)
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        ok = con.execute("""
            SELECT count(*) = 2
            FROM duckdb_columns()
            WHERE table_name = 'silver_pharmacies'
              AND column_name IN ('lat', 'lon')
        """).fetchone()[0]
        if not ok:
            con.execute(ETL_SQL)
        return con.execute(POINTS_SQL).df()
    finally:
        con.close()


with st.spinner("Loading data…"):
    points = load_points()

center_lat = sum(p["lat"] for p in PAVILIONS) / len(PAVILIONS)
center_lon = sum(p["lon"] for p in PAVILIONS) / len(PAVILIONS)

m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

for p in PAVILIONS:
    folium.Marker(
        location=[p["lat"], p["lon"]],
        tooltip=p["name"],
        icon=folium.Icon(color=p["color"], icon="graduation-cap", prefix="fa"),
    ).add_to(m)
    folium.Circle(
        location=[p["lat"], p["lon"]], radius=50_000,
        color=p["color"], weight=2, fill=True, fill_opacity=0.15,
    ).add_to(m)
    folium.Circle(
        location=[p["lat"], p["lon"]], radius=150_000,
        color=p["color"], weight=2, fill=True, fill_opacity=0.08, dash_array="6",
    ).add_to(m)

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