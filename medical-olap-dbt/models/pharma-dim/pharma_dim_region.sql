select
    cast(country_code as varchar(8)) as country_code,
    cast(region_code as varchar(32)) as region_code,
    cast(region_name as varchar(100)) as region_name,
    cast(seq_id as integer) as region_sequence
from {{ ref('dw_dim_region') }}
