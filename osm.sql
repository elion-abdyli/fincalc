.echo on
.timer on

INSTALL spatial; LOAD spatial;

set force_download=true;

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

describe silver_pharmacies;
from silver_pharmacies limit 5;