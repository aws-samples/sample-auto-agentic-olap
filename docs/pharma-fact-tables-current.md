# Current state of the two Pharma fact tables

## 1. Actual sales: dm_fact_sales_detail

| Raw field | Meaning | Key conventions we need to pin down |
| --- | --- | --- |
| `yearmonth` | Sales month | |
| `quarter` | Sales quarter | |
| `country_code` | Sales country code | |
| `brand_code` | Brand code | |
| `package_code` | Package / SKU code | |
| `dist_code` | Distributor code | |
| `control_flag` | Comparator flag (tentative) | What exactly do 0 and 1 mean? Do they decide whether a row counts toward total sales? |
| `sales_type` | Sales business type | How are 自营 (self-operated), 授权 (licensed) and 对照 (comparator) defined? Are they mutually exclusive? What business model does 授权 refer to? |
| `sales_qty` | Sales quantity | What is the unit? Is it already net of returns, allocated or converted? |
| `sales_value` | Sales value | Shipped amount or recognized revenue? CNY or original currency? Tax-inclusive? Net of discounts, rebates, returns? |
| `bu_code` | Business-unit code | Is it the accounting org, the sales-responsibility org, or the product-owning BU? |
| `un_split_sales_qty` | Unsplit sales quantity | What does "split" mean exactly? How does it relate to `sales_qty`? Which one is used for performance review? |

The fields you cannot judge from the name alone are: sales value, the two
quantities, 授权/对照, and BU.

## 2. Budget/forecast: dm_fact_sales_forecast

Although the table name contains `forecast`, the data holds both `bgt` and
`fcst`, so in business discussion it is better called the "budget/forecast table".

| Raw field | Meaning | Key conventions we need to pin down |
| --- | --- | --- |
| `yearmonth` | Plan target month | |
| `brand_code` | Brand code | Does it use exactly the same brand coding and business scope as the sales table? |
| `package_name` | Plan product / spec label | Why do some rows hold a spec and others a brand name? Do different records have different plan granularity? |
| `country_code` | Plan sales country code | Does the country mean the same as in the sales table? Are there cases where one country stands for a whole region? |
| `fcst_qty` | Plan quantity | Split into budget vs forecast quantity by `data_type`; is the unit consistent with actual quantity? |
| `fcst_value` | Plan value | Split into budget vs forecast value by `data_type`; are currency and tax conventions consistent with actuals? |
| `sales_type` | Plan sales business type | Are 自营 and 授权 defined the same as in the sales table? Is "no plan for 对照" a business rule? |
| `data_type` | Plan scenario | The current code defines `bgt` as budget and `fcst` as forecast; still need to confirm whether budget is frozen and whether forecast is refreshed on a rolling basis. |
