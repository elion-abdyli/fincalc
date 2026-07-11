"""
Streamlit app: map of two university pavilions with 50 km and 150 km radius circles.

Run with:
    pip install streamlit folium streamlit-folium
    streamlit run pavilion_radius_map.py
"""

import folium
import streamlit as st
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

# ---------------------------------------------------------------
# Sidebar controls (title/legend moved here to free the canvas)
# ---------------------------------------------------------------
with st.sidebar:
    st.header("Pavilion coverage map")
    show_50 = st.checkbox("Show 50 km circles", value=True)
    show_150 = st.checkbox("Show 150 km circles", value=True)
    st.caption(
        "Blue = Pavillon Jean-Coutu (UdeM, Montréal)\n\n"
        "Red = Pavillon Ferdinand-Vandry (ULaval, Québec City)\n\n"
        "Solid = 50 km · dashed = 150 km. True geodesic radii."
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

st_folium(m, use_container_width=True, height=1200, returned_objects=[])