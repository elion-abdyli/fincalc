import urllib.request
from urllib.parse import quote

import duckdb
import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

st.set_page_config(page_title="Pavilion map", layout="wide")

st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {padding: 0 !important; margin: 0 !important; max-width: 100% !important;}
      [data-testid="stAppViewContainer"] > .main {overflow: hidden;}
      iframe[title="streamlit_folium.st_folium"] {width: 100vw !important; height: 100vh !important;}
      div[data-testid="stVerticalBlock"] {gap: 0rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

OVERPASS_BASE = "https://overpass-api.de/api/interpreter"
DB_PATH = "/tmp/pharmacies.duckdb"
PAYLOAD_PATH = "/tmp/osm_payload.json"

@st.cache_resource
def get_db():
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial;")
    
    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_pavilions (
            id VARCHAR PRIMARY KEY,
            name VARCHAR,
            address VARCHAR,
            lat DOUBLE,
            lon DOUBLE,
            color VARCHAR
        );
        INSERT OR IGNORE INTO dim_pavilions VALUES
        ('udem', 'Pavillon Jean-Coutu — Université de Montréal', '2940 Chemin de Polytechnique, Montréal, QC', 45.5003731, -73.6147689, 'blue'),
        ('ulaval', 'Pavillon Ferdinand-Vandry — Université Laval', '1600 Avenue des Sciences-de-la-Vie, Québec City, QC', 46.7778727, -71.2778118, 'red');
    """)
    return con

def execute_pipeline(con):
    overpass_ql = '[out:json][timeout:180];area["ISO3166-2"="CA-QC"]->.qc;(nwr["amenity"="pharmacy"](area.qc););out center tags;'
    url = OVERPASS_BASE + "?data=" + quote(overpass_ql)
    with urllib.request.urlopen(url, timeout=200) as resp:
        with open(PAYLOAD_PATH, "wb") as f:
            f.write(resp.read())

    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_osm AS
        SELECT element 
        FROM read_json('{PAYLOAD_PATH}', columns={{'elements': 'JSON[]'}}), UNNEST(elements) AS element
        WHERE element->>'type' IN ('node', 'way', 'relation')
    """)

    con.execute("""
        CREATE OR REPLACE TABLE silver_pharmacies AS
        SELECT DISTINCT ON (element->>'type', element->>'id')
            (element->>'id')::BIGINT AS osm_id,
            COALESCE(NULLIF(element->>'$.tags.name', ''), 'Pharmacy (unnamed)') AS name,
            COALESCE((element->>'$.lat')::DOUBLE, (element->>'$.center.lat')::DOUBLE) AS lat,
            COALESCE((element->>'$.lon')::DOUBLE, (element->>'$.center.lon')::DOUBLE) AS lon,
            CONCAT_WS(', ', 
                NULLIF(TRIM(CONCAT_WS(' ', element->>'$.tags."addr:housenumber"', element->>'$.tags."addr:street"')), ''), 
                element->>'$.tags."addr:city"'
            ) AS address,
            COALESCE(element->>'$.tags.operator', '') AS operator,
            COALESCE(element->>'$.tags.opening_hours', '') AS hours,
            ST_Point(
                COALESCE((element->>'$.lon')::DOUBLE, (element->>'$.center.lon')::DOUBLE),
                COALESCE((element->>'$.lat')::DOUBLE, (element->>'$.center.lat')::DOUBLE)
            ) AS geom
        FROM bronze_osm
        WHERE lat IS NOT NULL AND lon IS NOT NULL
    """)

    con.execute("""
        CREATE OR REPLACE TABLE gold_pharmacies AS
        WITH distances AS (
            SELECT
                s.osm_id, s.name, s.lat, s.lon, s.address, s.operator, s.hours,
                p.name AS pavilion_name, p.color AS pavilion_color,
                ST_Distance_Spheroid(s.geom, ST_Point(p.lon, p.lat)) / 1000.0 AS dist_km
            FROM silver_pharmacies s
            CROSS JOIN dim_pavilions p
        ),
        ranked_distances AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY osm_id ORDER BY dist_km ASC) as rnk
            FROM distances
        ),
        html_components AS (
            SELECT
                r.osm_id, r.lat, r.lon, r.pavilion_color AS color, r.dist_km AS nearest_dist_km, r.name,
                CONCAT('<b>', r.name, '</b>',
                    CASE WHEN r.address != '' THEN CONCAT('<br>', r.address) ELSE '' END,
                    CASE WHEN r.operator != '' THEN CONCAT('<br>', r.operator) ELSE '' END,
                    CASE WHEN r.hours != '' THEN CONCAT('<br>', r.hours) ELSE '' END
                ) AS html_details,
                STRING_AGG(CONCAT('<br>', ROUND(d.dist_km, 1)::VARCHAR, ' km — ', SPLIT_PART(d.pavilion_name, '—', 1)), '' ORDER BY d.dist_km ASC) AS html_distances
            FROM ranked_distances r
            JOIN distances d ON r.osm_id = d.osm_id
            WHERE r.rnk = 1
            GROUP BY r.osm_id, r.lat, r.lon, r.pavilion_color, r.dist_km, r.name, r.address, r.operator, r.hours
        )
        SELECT
            lat, lon, color, nearest_dist_km,
            CONCAT(html_details, '<hr style="margin:4px 0">', html_distances) AS popup_html,
            CONCAT(name, ' — ', ROUND(nearest_dist_km, 1)::VARCHAR, ' km') AS tooltip_text
        FROM html_components
    """)

    con.execute("""
        CREATE OR REPLACE VIEW gold_ui_config AS
        SELECT AVG(lat) AS center_lat, AVG(lon) AS center_lon FROM dim_pavilions
    """)

con = get_db()

with st.sidebar:
    if st.button("Run ETL Pipeline"):
        execute_pipeline(con)
    
    st.divider()
    band_filter = st.radio("Distance filter", ["50", "150", "All"], index=2)
    cluster = st.checkbox("Cluster markers", value=True)
    show_50 = st.checkbox("Show 50 km radii", value=True)
    show_150 = st.checkbox("Show 150 km radii", value=True)

try:
    ui_config = con.execute("SELECT center_lat, center_lon FROM gold_ui_config").fetchone()
    center_lat, center_lon = ui_config[0], ui_config[1]
except duckdb.CatalogException:
    center_lat, center_lon = 46.0, -72.0 

m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

pavilions = con.execute("SELECT name, address, lat, lon, color FROM dim_pavilions").df()
for row in pavilions.itertuples():
    folium.Marker(
        location=[row.lat, row.lon],
        popup=folium.Popup(f"<b>{row.name}</b><br>{row.address}", max_width=280),
        tooltip=row.name,
        icon=folium.Icon(color=row.color, icon="graduation-cap", prefix="fa")
    ).add_to(m)

    if show_50:
        folium.Circle(location=[row.lat, row.lon], radius=50000, color=row.color, weight=2, fill=True, fill_opacity=0.15).add_to(m)
    if show_150:
        folium.Circle(location=[row.lat, row.lon], radius=150000, color=row.color, weight=2, fill=True, fill_opacity=0.08, dash_array="6").add_to(m)

try:
    where_clause = ""
    if band_filter == "50":
        where_clause = "WHERE nearest_dist_km <= 50"
    elif band_filter == "150":
        where_clause = "WHERE nearest_dist_km <= 150"
        
    pharmacies = con.execute(f"SELECT lat, lon, color, popup_html, tooltip_text FROM gold_pharmacies {where_clause}").df()
    
    layer = folium.FeatureGroup(name="Pharmacies").add_to(m)
    target = MarkerCluster().add_to(layer) if cluster else layer

    for row in pharmacies.itertuples():
        folium.CircleMarker(
            location=[row.lat, row.lon],
            radius=4,
            color=row.color,
            weight=1,
            fill=True,
            fill_color=row.color,
            fill_opacity=0.75,
            popup=folium.Popup(row.popup_html, max_width=280),
            tooltip=row.tooltip_text
        ).add_to(target)
        
    st.sidebar.metric("Pharmacies", len(pharmacies))
except duckdb.CatalogException:
    st.sidebar.warning("Pipeline has not been executed.")

st_folium(m, use_container_width=True, height=1200, returned_objects=[])