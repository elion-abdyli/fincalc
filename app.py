import streamlit as st
import duckdb
import pandas as pd
import seaborn as sns
import plotly.express as px
st.set_page_config(page_title='pagetitle', layout='wide', initial_sidebar_state=None, menu_items=None)

st.write("welcome")

rel = duckdb.sql("""
                 create or replace sequence hsec;
                 select
                 range::date as date,
                 1 as one,
                 row_number () over (order by range) as hsec,
                 1.01**hsec as exp2
                 from range(date '2026-01-01', date '2027-01-01', interval 1 day)
                 """)

c1,c2 = st.columns(2)

df = rel.df().set_index('date')

c1.dataframe(df, height = 600)
# st.data_editor(rel)

c2.scatter_chart(df, size = 5, height = 600)