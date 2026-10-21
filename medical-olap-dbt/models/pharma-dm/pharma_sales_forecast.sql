with typed as (
    select
        cast(yearmonth as integer) as yearmonth,
        cast(brand_code as varchar(32)) as brand_code,
        cast(package_name as varchar(200)) as package_name,
        cast(country_code as varchar(8)) as country_code,
        cast(fcst_qty as bigint) as planned_quantity,
        cast(fcst_value as decimal(38, 2)) as planned_value,
        cast(sales_type as varchar(32)) as sales_type,
        cast(data_type as varchar(16)) as data_type
    from {{ ref('dm_fact_sales_forecast') }}
)

select
    md5(
        cast(yearmonth as varchar) || '|' ||
        coalesce(brand_code, '<null>') || '|' ||
        coalesce(package_name, '<null>') || '|' ||
        coalesce(country_code, '<null>') || '|' ||
        coalesce(sales_type, '<null>') || '|' ||
        coalesce(data_type, '<null>')
    ) as sales_forecast_id,
    to_date(cast(yearmonth as varchar), 'YYYYMM') as forecast_month,
    case
        when nullif(country_code, '') is not null
            and nullif(brand_code, '') is not null
            and nullif(sales_type, '') is not null
        then cast(yearmonth as varchar) || '|' || country_code || '|' || brand_code || '|' || sales_type
    end as comparable_cell_id,
    yearmonth,
    brand_code,
    package_name,
    country_code,
    sales_type,
    data_type,
    planned_quantity,
    planned_value
from typed
