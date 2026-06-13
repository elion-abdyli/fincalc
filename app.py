import streamlit as st
import duckdb
import pandas as pd
import seaborn as sns
import plotly.express as px
st.set_page_config(page_title='fincalc', layout='wide', initial_sidebar_state=None, menu_items=None)

st.write("welcome")

import duckdb


def forecast_by_city(city: str) -> duckdb.DuckDBPyRelation:
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
    geo = duckdb.sql(f"""
        SELECT results[1].latitude AS lat, results[1].longitude AS lon
        FROM read_json('{geo_url}')
    """).fetchone()
    lat, lon = geo

    fc_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&hourly=temperature_2m"
    )
    return duckdb.sql(f"""
        SELECT
            unnest(hourly.time)::TIMESTAMP AS time,
            unnest(hourly.temperature_2m) AS temp_c
        FROM read_json('{fc_url}')
    """)

# forecast_by_city("Montreal").show()


interest = st.slider(
    label = 'interest',
    value = 1.02,
    min_value = 1.0,
    max_value = 1.25,
    step = 0.01,
    )

rel = duckdb.sql(f"""
                 select
                 range::date as date,
                 {interest}**(row_number() over (order by range) - 1) as compound
                 from range(date '2026-01-01', date '2036-01-01', interval 1 month)
                 """)

c1,c2 = st.columns(2)

df = rel.df().set_index('date')

c1.dataframe(df, height = 'content')
# st.data_editor(rel)

c2.scatter_chart(df, size = 50, height = 600)