{{ config(schema='semantic', materialized='table') }}

select cast(date_day as date) as date_day
from {{ ref('seed_date_spine') }}
