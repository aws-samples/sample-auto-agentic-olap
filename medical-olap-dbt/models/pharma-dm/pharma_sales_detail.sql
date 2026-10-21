with typed as (
    select
        cast(yearmonth as integer) as yearmonth,
        cast(quarter as varchar(8)) as quarter,
        cast(country_code as varchar(8)) as country_code,
        cast(brand_code as varchar(32)) as brand_code,
        cast(package_code as varchar(32)) as package_code,
        cast(dist_code as varchar(32)) as distributor_code,
        cast(control_flag as smallint) as control_flag,
        cast(sales_type as varchar(32)) as sales_type,
        cast(sales_qty as bigint) as sales_quantity,
        cast(sales_value as decimal(38, 2)) as sales_value,
        cast(bu_code as varchar(32)) as bu_code,
        cast(un_split_sales_qty as bigint) as unsplit_sales_quantity
    from {{ ref('dm_fact_sales_detail') }}
)

select
    md5(
        cast(yearmonth as varchar) || '|' ||
        coalesce(country_code, '<null>') || '|' ||
        coalesce(brand_code, '<null>') || '|' ||
        coalesce(package_code, '<null>') || '|' ||
        coalesce(distributor_code, '<null>') || '|' ||
        cast(control_flag as varchar) || '|' ||
        coalesce(sales_type, '<null>') || '|' ||
        coalesce(bu_code, '<null>')
    ) as sales_detail_id,
    to_date(cast(yearmonth as varchar), 'YYYYMM') as sales_month,
    case
        when nullif(country_code, '') is not null
            and nullif(brand_code, '') is not null
            and nullif(sales_type, '') is not null
        then cast(yearmonth as varchar) || '|' || country_code || '|' || brand_code || '|' || sales_type
    end as comparable_cell_id,
    yearmonth,
    quarter,
    country_code,
    brand_code,
    package_code,
    distributor_code,
    control_flag,
    sales_type,
    bu_code,
    sales_quantity,
    sales_value,
    unsplit_sales_quantity
from typed
