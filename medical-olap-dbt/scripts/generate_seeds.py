"""Generate the synthetic pharma seed data (seeds/pharma-dim, seeds/pharma-dm,
seeds/seed_date_spine.csv).

Every value here is invented. Brands, packages, distributors, regions and
amounts do not describe any real company. The output is deterministic (fixed
RNG seed), so re-running the script reproduces the same CSV files.

The data deliberately keeps the quirks the semantic layer is built to handle:

* negative sales rows with no distributor (returns and reversals);
* control rows: ``sales_type = 对照`` with ``control_flag = 1``, never budgeted;
* ``un_split_sales_qty`` that differs from ``sales_qty``;
* a handful of rows with an empty ``sales_type`` or ``brand_code``;
* brand ``PX14`` mapped to two brand names in the product dimension;
* forecast ``package_name`` sometimes holding a package spec and sometimes a
  brand name;
* both ``bgt`` (budget, frozen per year) and ``fcst`` (rolling forecast) plans.

Usage:
    python scripts/generate_seeds.py
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIM_DIR = ROOT / "seeds" / "pharma-dim"
DM_DIR = ROOT / "seeds" / "pharma-dm"

RNG = random.Random(20261015)

FIRST_ACTUAL = (2022, 1)
LAST_ACTUAL = (2026, 9)
PLAN_YEARS = (2025, 2026)

BUS = {
    "BU01": "肿瘤事业部",
    "BU02": "自身免疫事业部",
    "BU03": "慢病事业部",
}

# code, brand name, BU, launch month, base monthly value (CNY), monthly growth,
# share of sales outside China, packages (name, unit price)
BRANDS = [
    ("PX01", "肿瘤-单抗A", "BU01", (2022, 1), 9_000_000, 0.004, 0.10, [("100mg/10ml/瓶", 1450), ("500mg/50ml/瓶", 6900)]),
    ("PX02", "肿瘤-单抗B", "BU01", (2022, 1), 26_000_000, 0.009, 0.25, [("150mg/瓶", 1680), ("440mg/瓶", 4700), ("60mg/瓶", 720)]),
    ("PX03", "肿瘤-PD-1 C", "BU01", (2022, 6), 14_000_000, 0.022, 0.30, [("100mg(10ml)/瓶", 2980)]),
    ("PX04", "肿瘤-ADC D", "BU01", (2024, 9), 4_000_000, 0.045, 0.05, [("100mg/瓶", 8800), ("20mg/瓶", 1900)]),
    ("PX05", "肿瘤-小分子E", "BU01", (2023, 3), 3_500_000, 0.012, 0.00, [("40mg*28片/盒", 2600), ("80mg*28片/盒", 4900)]),
    ("PX06", "自免-单抗F", "BU02", (2022, 1), 7_500_000, 0.006, 0.35, [("40mg/0.8ml/支", 1290), ("40mg（组合包装）", 2480)]),
    ("PX07", "自免-单抗G", "BU02", (2023, 7), 3_000_000, 0.030, 0.10, [("150mg/ml/支", 1980)]),
    ("PX08", "自免-融合蛋白H", "BU02", (2022, 1), 5_000_000, -0.004, 0.00, [("25mg/瓶", 480), ("50mg/瓶", 890)]),
    ("PX09", "慢病-胰岛素I", "BU03", (2022, 1), 6_000_000, 0.002, 0.15, [("300IU/3ml/支", 68), ("300IU*5支/盒", 320)]),
    ("PX10", "慢病-降脂J", "BU03", (2022, 4), 2_200_000, 0.015, 0.00, [("10mg*30片/盒", 96), ("20mg*30片/盒", 168)]),
    ("PX11", "慢病-骨质K", "BU03", (2024, 2), 1_500_000, 0.035, 0.20, [("60mg/1ml/支", 1180)]),
    ("PX12", "慢病-眼科L", "BU03", (2025, 1), 1_200_000, 0.050, 0.00, [("2mg/0.05ml/支", 3600)]),
    # One brand code, two brand names: the product dimension is not 1:1.
    ("PX14", "肿瘤-单抗M", "BU01", (2024, 4), 2_800_000, 0.020, 0.60, [("120mg/瓶", 3200), ("420mg/瓶", 9800)]),
]
PX14_ALT_NAME = "肿瘤-单抗M（海外）"

# Countries outside China with an ISO code that exists in dw_dim_country.
EXPORT_COUNTRIES = {
    "US": 0.18, "GB": 0.08, "DE": 0.08, "FR": 0.06, "PL": 0.06, "ES": 0.05,
    "IT": 0.05, "BE": 0.03, "NL": 0.03, "BR": 0.05, "AR": 0.04, "MX": 0.04,
    "IN": 0.05, "ID": 0.04, "TH": 0.03, "MY": 0.02, "PH": 0.02, "SA": 0.03,
    "AE": 0.02, "EG": 0.02, "TR": 0.01, "AU": 0.01,
}

REGIONS = [
    ("CN-01", "中国大陆", {"CN", "HK", "MO", "TW"}),
    ("NA-01", "北美", {"US", "CA"}),
    ("EU-01", "欧洲", {"GB", "DE", "FR", "PL", "ES", "IT", "BE", "NL", "CH", "AT", "SE", "NO", "DK", "FI", "IE", "PT", "CZ", "HU", "RO", "GR"}),
    ("LA-01", "拉丁美洲", {"BR", "AR", "MX", "CL", "CO", "PE"}),
    ("AP-01", "亚太", {"IN", "ID", "TH", "MY", "PH", "VN", "SG", "JP", "KR", "AU", "NZ", "PK", "BD"}),
    ("ME-01", "中东及非洲", {"SA", "AE", "EG", "TR", "QA", "KW", "IL", "ZA", "NG", "MA"}),
]
OTHER_REGION = ("OT-01", "其他地区")


def months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def index_of(ym: tuple[int, int]) -> int:
    return (ym[0] - FIRST_ACTUAL[0]) * 12 + ym[1] - FIRST_ACTUAL[1]


def seasonality(month: int) -> float:
    # Slow January/February (holiday), strong quarter-ends.
    base = {1: 0.82, 2: 0.78, 3: 1.08, 6: 1.10, 9: 1.06, 12: 1.18}.get(month, 1.0)
    return base


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"{path.relative_to(ROOT)}: {len(rows)} rows")


def build_products():
    rows, packages = [], []
    code = 910_000_001
    for brand, name, bu, *_rest, pkgs in BRANDS:
        for i, (pkg_name, price) in enumerate(pkgs):
            brand_name = PX14_ALT_NAME if brand == "PX14" and i == 1 else name
            rows.append([code, pkg_name, brand, brand_name, bu, BUS[bu]])
            packages.append((code, brand, pkg_name, price))
            code += 1
        # Discontinued / sample-pack codes that rarely or never sell.
        rows.append([code, pkgs[0][0] + "（样品）", brand, name, bu, BUS[bu]])
        code += 1
    return rows, packages


def build_regions(countries: list[str]):
    rows = []
    for c in countries:
        for seq, (rcode, rname, members) in enumerate(REGIONS, start=1):
            if c in members:
                rows.append([c, rcode, rname, seq])
                break
        else:
            rows.append([c, OTHER_REGION[0], OTHER_REGION[1], len(REGIONS) + 1])
    return rows


def build_sales(packages):
    by_brand: dict[str, list[tuple[int, str, int]]] = {}
    for code, brand, pkg_name, price in packages:
        by_brand.setdefault(brand, []).append((code, pkg_name, price))

    cn_dists = list(range(10_001, 10_181))  # 180 domestic distributors
    ex_dists = list(range(20_001, 20_061))  # 60 overseas partners
    rows = []
    cells: dict[tuple, float] = {}  # (ym, country, brand, sales_type) -> value

    for brand, _name, bu, launch, base, growth, export_share, _pkgs in BRANDS:
        brand_dists = RNG.sample(cn_dists, k=RNG.randint(18, 40))
        export_countries = [c for c in EXPORT_COUNTRIES if RNG.random() < 0.55] if export_share else []
        for y, m in months(max(launch, FIRST_ACTUAL), LAST_ACTUAL):
            age = index_of((y, m)) - index_of(max(launch, FIRST_ACTUAL))
            ramp = 1 - math.exp(-age / 6) if launch > FIRST_ACTUAL else 1.0
            total = base * (1 + growth) ** age * seasonality(m) * max(ramp, 0.08)
            total *= RNG.uniform(0.9, 1.1)
            # A supply disruption in one brand, for anomaly questions.
            if brand == "PX06" and (y, m) in {(2025, 4), (2025, 5)}:
                total *= 0.45

            quarter = f"{y}Q{(m - 1) // 3 + 1}"
            ym = y * 100 + m
            pkgs = [p for p in by_brand[brand] if "样品" not in p[1]]

            # Domestic self-operated sales, split across distributors and packages.
            cn_value = total * (1 - export_share)
            active = RNG.sample(brand_dists, k=max(3, len(brand_dists) // RNG.randint(2, 4)))
            weights = [RNG.random() ** 2 + 0.05 for _ in active]
            wsum = sum(weights)
            for dist, w in zip(active, weights):
                code, _pkg, price = RNG.choice(pkgs)
                value = cn_value * w / wsum
                qty = max(1, round(value / price))
                unsplit = qty if RNG.random() < 0.08 else qty + RNG.randint(0, max(1, qty // 10))
                rows.append([ym, quarter, "CN", brand, code, dist, 0, "自营", qty, round(qty * price, 2), bu, unsplit])
                key = (ym, "CN", brand, "自营")
                cells[key] = cells.get(key, 0) + qty * price

            # Overseas: licensed partners, plus self-operated in a few markets.
            ex_value = total * export_share
            if export_countries:
                shares = [EXPORT_COUNTRIES[c] * RNG.uniform(0.6, 1.4) for c in export_countries]
                ssum = sum(shares)
                for c, s in zip(export_countries, shares):
                    if RNG.random() < 0.2:
                        continue  # not every market ships every month
                    sales_type = "自营" if c in {"US", "GB", "DE"} and brand in {"PX02", "PX03", "PX14"} else "授权"
                    code, _pkg, price = RNG.choice(pkgs)
                    unit = price * (2.4 if c in {"US", "GB", "DE", "FR"} else 1.3)
                    qty = max(1, round(ex_value * s / ssum / unit))
                    rows.append([ym, quarter, c, brand, code, RNG.choice(ex_dists), 0, sales_type, qty, round(qty * unit, 2), bu, qty])
                    key = (ym, c, brand, sales_type)
                    cells[key] = cells.get(key, 0) + qty * unit

            # Returns / reversals: negative, no distributor.
            if RNG.random() < 0.18:
                code, _pkg, price = RNG.choice(pkgs)
                qty = -RNG.randint(1, 40)
                rows.append([ym, quarter, "CN", brand, code, "", 0, "自营", qty, round(qty * price, 2), bu, qty])
            # Control (comparator) supply for clinical studies: flag = 1, never budgeted.
            if bu == "BU01" and RNG.random() < 0.12:
                code, _pkg, price = RNG.choice(pkgs)
                qty = RNG.randint(5, 120)
                rows.append([ym, quarter, "CN", brand, code, RNG.choice(cn_dists), 1, "对照", qty, round(qty * price * 0.5, 2), bu, qty])

    # A few incomplete rows, which the null-row metrics are there to surface.
    for _ in range(12):
        r = list(RNG.choice(rows))
        if RNG.random() < 0.5:
            r[7] = ""
        else:
            r[3] = ""
        rows.append(r)

    rows.sort(key=lambda r: (r[0], r[2], r[3], str(r[5])))
    return rows, cells


def build_plans(cells, packages):
    pkg_names = {}
    for _code, brand, pkg_name, _price in packages:
        if "样品" not in pkg_name:
            pkg_names.setdefault(brand, []).append(pkg_name)
    brand_names = {b[0]: b[1] for b in BRANDS}

    # Aggregate the planning cells: plans are set per month x country x brand x type.
    plan_cells: dict[tuple, float] = {}
    for (ym, c, brand, st), value in cells.items():
        y = ym // 100
        if y not in PLAN_YEARS and not (y == PLAN_YEARS[0] - 1):
            continue
        plan_cells[(ym, c, brand, st)] = plan_cells.get((ym, c, brand, st), 0) + value

    # Only plan the larger country cells; small markets are left unbudgeted.
    yearly = {}
    for (ym, c, brand, st), v in plan_cells.items():
        yearly[(c, brand, st)] = yearly.get((c, brand, st), 0) + v
    planned_keys = {k for k, v in yearly.items() if k[0] == "CN" or v > 2_000_000}

    rows = []
    for c, brand, st in sorted(planned_keys):
        label = RNG.choice(pkg_names[brand]) if RNG.random() < 0.7 else brand_names[brand]
        avg_price = 1500
        for year in PLAN_YEARS:
            budget_bias = RNG.uniform(0.95, 1.20)  # budgets tend to be optimistic
            for m in range(1, 13):
                ym = year * 100 + m
                prior = plan_cells.get((ym - 100, c, brand, st))
                actual = plan_cells.get((ym, c, brand, st))
                anchor = prior * 1.12 if prior else actual
                if not anchor:
                    continue
                bgt = anchor * budget_bias
                rows.append([ym, brand, label, c, round(bgt / avg_price), round(bgt, 2), st, "bgt"])
                # Rolling forecast: close to actuals that exist, extrapolated after.
                fc_base = actual if actual else anchor
                fcst = fc_base * RNG.uniform(0.93, 1.07)
                rows.append([ym, brand, label, c, round(fcst / avg_price), round(fcst, 2), st, "fcst"])
    rows.sort(key=lambda r: (r[0], r[3], r[1], r[6], r[7]))
    return rows


def write_date_spine() -> None:
    import datetime as dt

    day, last = dt.date(2021, 1, 1), dt.date(2027, 12, 31)
    rows = []
    while day <= last:
        rows.append([day.isoformat()])
        day += dt.timedelta(days=1)
    write_csv(ROOT / "seeds" / "seed_date_spine.csv", ["date_day"], rows)


def main() -> int:
    write_date_spine()
    countries = []
    with (DIM_DIR / "dw_dim_country.csv").open(encoding="utf-8") as fh:
        countries = [row["country_code"] for row in csv.DictReader(fh)]

    product_rows, packages = build_products()
    write_csv(DIM_DIR / "dw_dim_product.csv",
              ["package_code", "package_name", "brand_code", "brand_name", "bu_code", "bu_name"], product_rows)
    write_csv(DIM_DIR / "dw_dim_region.csv",
              ["country_code", "region_code", "region_name", "seq_id"], build_regions(countries))

    sales_rows, cells = build_sales(packages)
    write_csv(DM_DIR / "dm_fact_sales_detail.csv",
              ["yearmonth", "quarter", "country_code", "brand_code", "package_code", "dist_code",
               "control_flag", "sales_type", "sales_qty", "sales_value", "bu_code", "un_split_sales_qty"],
              sales_rows)
    write_csv(DM_DIR / "dm_fact_sales_forecast.csv",
              ["yearmonth", "brand_code", "package_name", "country_code", "fcst_qty", "fcst_value",
               "sales_type", "data_type"],
              build_plans(cells, packages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
