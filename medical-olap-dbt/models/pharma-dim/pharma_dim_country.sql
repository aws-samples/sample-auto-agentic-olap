select
    cast(country.country_code as varchar(8)) as country_code,
    cast(country.country_cn_short as varchar(100)) as country_cn_short,
    cast(country.country_cn_full as varchar(200)) as country_cn_full,
    cast(country.countryname_en as varchar(200)) as country_name_en,
    cast(country.currency_code as varchar(8)) as currency_code,
    cast(country.language_code as varchar(16)) as language_code,
    cast(country.timezone as varchar(32)) as timezone,
    region.region_code,
    region.region_name,
    region.region_sequence
from {{ ref('dw_dim_country') }} as country
left join {{ ref('pharma_dim_region') }} as region
    on country.country_code = region.country_code
