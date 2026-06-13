import streamlit as st
import duckdb
import pandas as pd
import seaborn as sns
import plotly.express as px
st.set_page_config(page_title='fincalc', layout='wide', initial_sidebar_state=None, menu_items=None)

st.write("welcome")

interest = st.slider(
    label = 'interest',
    value = 1.0,
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