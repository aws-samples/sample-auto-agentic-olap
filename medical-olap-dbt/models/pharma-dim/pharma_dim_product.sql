select
    cast(package_code as varchar(32)) as package_code,
    cast(package_name as varchar(200)) as package_name,
    cast(brand_code as varchar(32)) as brand_code,
    cast(brand_name as varchar(200)) as brand_name,
    cast(bu_code as varchar(32)) as bu_code,
    cast(bu_name as varchar(100)) as bu_name
from {{ ref('dw_dim_product') }}
