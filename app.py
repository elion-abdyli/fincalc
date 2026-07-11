"""
Streamlit app: map of two university pavilions with 50 km / 150 km radius circles,
plus every OSM-tagged pharmacy inside those radii.

Run with:
    pip install streamlit folium streamlit-folium requests
    streamlit run pavilion_radius_map.py
"""

from math import asin, cos, radians, sin, sqrt

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

# ---------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


@st.cache_data(ttl=86_400, show_spinner="Querying OpenStreetMap for pharmacies…")
def fetch_pharmacies(locations, radius_km):
    """One Overpass query covering all pavilions. nwr + `out center` so that
    pharmacies mapped as buildings (ways) get a centroid, not dropped."""
    clauses = "".join(
        f'nwr["amenity"="pharmacy"](around:{radius_km * 1000},{loc["lat"]},{loc["lon"]});'
        for loc in locations
    )
    query = f"[out:json][timeout:90];({clauses});out center tags;"

    r = requests.post(OVERPASS_URL, data={"data": query}, timeout=120)
    r.raise_for_status()

    seen, results = set(), []
    for el in r.json().get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        key = (el["type"], el["id"])
        if key in seen:  # a pharmacy in both 150 km circles is returned twice
            continue
        seen.add(key)

        tags = el.get("tags", {})
        street = " ".join(
            p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
        )
        results.append(
            {
                "name": tags.get("name", "Pharmacy (unnamed)"),
                "lat": lat,
                "lon": lon,
                "address": ", ".join(p for p in (street, tags.get("addr:city")) if p),
                "operator": tags.get("operator", ""),
                "hours": tags.get("opening_hours", ""),
            }
        )
    return results


def classify(pharm, locations):
    """Nearest pavilion + which radius band the pharmacy falls in."""
    dists = {
        loc["name"]: haversine_km(pharm["lat"], pharm["lon"], loc["lat"], loc["lon"])
        for loc in locations
    }
    nearest = min(dists, key=dists.get)
    d = dists[nearest]
    band = 50 if d <= 50 else (150 if d <= 150 else None)
    color = next(l["color"] for l in locations if l["name"] == nearest)
    return nearest, d, band, color, dists


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
        ["50 km only", "150 km (all)"],
        index=1,
        disabled=not show_pharmacies,
    )
    cluster = st.checkbox(
        "Cluster markers", value=True, disabled=not show_pharmacies,
        help="Uncheck to see every pin individually — slow above ~500 points.",
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
    try:
        pharmacies = fetch_pharmacies(LOCATIONS, max(RADII_KM))
    except Exception as e:
        pharmacies = []
        st.sidebar.error(f"Overpass query failed: {e}")

    max_band = 50 if band_filter.startswith("50") else 150

    layer = folium.FeatureGroup(name="Pharmacies").add_to(m)
    target = MarkerCluster().add_to(layer) if cluster else layer

    counts = {"50": 0, "150": 0}
    for p in pharmacies:
        nearest, dist, band, color, dists = classify(p, LOCATIONS)
        if band is None or band > max_band:
            continue
        counts["50" if band == 50 else "150"] += 1

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
            fill_opacity=0.9 if band == 50 else 0.45,
            popup=folium.Popup(
                f"<b>{p['name']}</b>{detail}<hr style='margin:4px 0'>{dist_lines}",
                max_width=280,
            ),
            tooltip=f"{p['name']} — {dist:.1f} km",
        ).add_to(target)

    st.sidebar.metric(
        "Pharmacies shown",
        counts["50"] + counts["150"],
        f"{counts['50']} within 50 km",
    )

st_folium(m, use_container_width=True, height=1200, returned_objects=[])