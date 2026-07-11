.echo on
.timer on

set force_download=true;
CREATE OR REPLACE TABLE bronze AS SELECT * FROM read_json('https://overpass-api.de/api/interpreter?data=[out:json];area["ISO3166-2"="CA-QC"]->.searchArea;node["amenity"="pharmacy"](area.searchArea);out;');
from bronze;
select unnest(elements, recursive:=true) from bronze;
describe select unnest(elements, recursive:=true) from bronze;

