"""
Thought experiment: tax "unhealthy" food to subsidize "healthy" food, by province.

For each province/territory and each candidate levy rate, the model estimates:
  * the price rise on unhealthy food and the demand response (price elasticities)
  * levy revenue, plus the knock-on change in existing GST/HST/PST revenue
  * how revenue is split: admin, industry reformulation tax credits
    (manufacturers + restaurant franchisors), and the healthy-food subsidy
  * the healthy-food price cut that pool can fund (solved, revenue-neutral)
  * year-by-year erosion of the levy base as products are reformulated
  * net gain/loss per household by income quintile (regressivity check)

EVERY number in the ASSUMPTIONS block is a placeholder or literature default --
replace with sourced figures before drawing conclusions. Sources to use:
  * Tax rates/food treatment: CRA GST/HST zero-rating (Excise Tax Act Sch. VI
    Part III) and each province's PST/QST/RST rules.
  * Household counts: Statistics Canada Census.
  * Food spending by province and income quintile: Statistics Canada Survey of
    Household Spending (SHS).
  * Elasticities: Andreyeva, Long & Brownell (2010), Am J Public Health -- US data.
  * Regional price levels: prices collected by grocery_catalog_to_sheets.py.

Usage:
    python subsidy_simulation.py                       # defaults, prints tables
    python subsidy_simulation.py grocery_inventory.csv # regional price index from harvest
"""
import sys
from dataclasses import dataclass, field, replace

import pandas as pd

# ---------------------------------------------------------------- tax regimes
# Rates in effect for 2026 to the best of our knowledge -- verify before use.
# pst_unhealthy_grocery / pst_restaurant: share of that spending the provincial
# (non-harmonized) sales tax applies to. VERIFY each against provincial rules.
TAX_REGIMES = pd.DataFrame([
    # prov, regime,      gst,  hst,  pst,    pst_unhealthy_grocery, pst_restaurant, households
    ("BC", "GST + PST", 0.05, 0.0, 0.07,    0.30, 0.0, 2_040_000),  # PST on soft drinks since 2025
    ("AB", "GST only",  0.05, 0.0, 0.0,     0.0,  0.0, 1_630_000),
    ("SK", "GST + PST", 0.05, 0.0, 0.06,    0.0,  1.0,   450_000),
    ("MB", "GST + RST", 0.05, 0.0, 0.07,    0.0,  0.0,   510_000),
    ("ON", "HST",       0.0,  0.13, 0.0,    0.0,  0.0, 5_490_000),
    ("QC", "GST + QST", 0.05, 0.0, 0.09975, 1.0,  1.0, 3_700_000),  # QST mirrors GST food rules
    ("NB", "HST",       0.0,  0.15, 0.0,    0.0,  0.0,   330_000),
    ("NS", "HST",       0.0,  0.14, 0.0,    0.0,  0.0,   420_000),  # 14% since Apr 2025
    ("PE", "HST",       0.0,  0.15, 0.0,    0.0,  0.0,    65_000),
    ("NL", "HST",       0.0,  0.15, 0.0,    0.0,  0.0,   220_000),
    ("YT", "GST only",  0.05, 0.0, 0.0,     0.0,  0.0,    17_000),
    ("NT", "GST only",  0.05, 0.0, 0.0,     0.0,  0.0,    16_000),
    ("NU", "GST only",  0.05, 0.0, 0.0,     0.0,  0.0,    11_000),
], columns=["province", "regime", "gst", "hst", "pst",
            "pst_unhealthy_grocery", "pst_restaurant", "households"]).set_index("province")


# ---------------------------------------------------------------- assumptions
@dataclass
class Assumptions:
    # Annual household food spending, pre-tax, national average (PLACEHOLDER -> SHS)
    grocery_spend: float = 8_000.0
    restaurant_spend: float = 3_000.0
    unhealthy_share_grocery: float = 0.30      # share of grocery $ that is FOP "High in"
    unhealthy_share_restaurant: float = 0.60   # share of restaurant $ the levy covers
    # Share of unhealthy grocery $ already GST/HST-taxable (snacks, candy, pop);
    # the rest (bacon, cheese, frozen pizza...) is zero-rated basic groceries.
    gst_taxable_share_unhealthy_grocery: float = 0.40

    # Own-price elasticities (Andreyeva et al. 2010 means, US data)
    e_unhealthy_grocery: float = -0.45   # blend of soft drinks -0.79, sweets -0.34, fats -0.48
    e_restaurant: float = -0.81          # food away from home
    e_healthy: float = -0.62             # blend of fruit -0.70, veg -0.58, dairy -0.65
    cross_to_healthy: float = 0.10       # % rise in healthy demand per 1% unhealthy price rise

    levy_in_sales_tax_base: bool = True  # GST/HST/PST charged on top of the levy
    pass_through: float = 1.0            # share of levy/subsidy reaching shelf prices

    # Revenue split
    admin_share: float = 0.05
    manufacturer_credit_share: float = 0.15   # reformulation/retooling tax credits
    franchisor_credit_share: float = 0.05     # restaurant menu reformulation credits
    # Pooling: "provincial" keeps revenue where it is raised; "federal" pools
    # nationally and pays the same subsidy rate everywhere.
    pooling: str = "provincial"

    # Share of remaining unhealthy base reformulated out of the levy each year
    reformulation_rate_grocery: float = 0.08
    reformulation_rate_restaurant: float = 0.04
    years: int = 5

    # ILLUSTRATIVE income quintiles -- replace with SHS by income quintile.
    quintiles: pd.DataFrame = field(default_factory=lambda: pd.DataFrame({
        "quintile": ["Q1 (lowest)", "Q2", "Q3", "Q4", "Q5 (highest)"],
        "income": [28_000, 55_000, 85_000, 125_000, 230_000],
        "food_spend_multiplier": [0.60, 0.80, 1.00, 1.20, 1.45],
        "unhealthy_share_multiplier": [1.15, 1.08, 1.00, 0.95, 0.85],
        "restaurant_multiplier": [0.45, 0.70, 1.00, 1.30, 1.80],
    }))

    # Low-income targeting of the subsidy pool.
    # targeted_share: share of the subsidy pool reserved for eligible households;
    #   the rest funds the universal shelf-price cut.
    # targeting_mode:
    #   "rebate"  -- equal cash payment per eligible household via the tax system
    #                (GST/HST-credit style); no condition on what it is spent on.
    #   "voucher" -- extra healthy-food discount at checkout for eligible households
    #                only (benefit card); changes what they buy.
    # targeted_quintiles: which quintiles qualify (0 = Q1 lowest).
    targeted_share: float = 0.0
    targeting_mode: str = "rebate"
    targeted_quintiles: tuple = (0, 1)
    voucher_admin_share: float = 0.08   # extra delivery cost of a benefit-card program
    # Health levy rebate: eligible households (targeted_quintiles) get back the
    # levy their quintile pays on average, plus the sales tax charged on it,
    # paid with the quarterly GST/HST credit. A fixed amount per quintile -- not
    # tied to the household's own purchases, so the price signal is kept.
    # Paid first out of the subsidy pool; the rest is split as above.
    levy_rebate: bool = False

    # Federal income surtax on high earners; revenue tops up the subsidy pool
    # (split universal/targeted like levy revenue).
    # Default design: anyone with total income of $150,000 or more pays 1% of
    # their TOTAL income (T1 line 15000 -- before RRSP or any other deduction),
    # with no shelters, credits or deductions against it (surtax_avoidance=False).
    # surtax_avoidance=True instead models filers reacting anyway: reporting less
    # income (eti) and bunching just under the $150k notch.
    # Variants: surtax_on_all_income=False charges only income above the line;
    # surtax_phase_in phases the charge in to remove the notch.
    # PLACEHOLDERS -> CRA final T1 statistics by income bracket.
    surtax_rate: float = 0.0
    surtax_avoidance: bool = False
    surtax_on_all_income: bool = True
    surtax_threshold: float = 150_000.0
    surtax_filers: float = 1_700_000.0       # filers with total income >= threshold
    surtax_avg_income: float = 280_000.0     # their mean total income
    filers_per_1k_at_threshold: float = 22_000.0  # filer density just above the line
    surtax_phase_in: float = 0.0             # e.g. 0.10 -> charge = min(rate*income, 10% of excess)
    top_marginal_rate: float = 0.47        # combined fed+prov marginal rate around/above $150k
    # Elasticity of taxable income: % drop in reported income per 1% drop in
    # (1 - marginal rate). Canadian high-earner estimates span ~0.2 to ~0.7.
    eti: float = 0.30
    # Share of surtax paid by households in each quintile. ILLUSTRATIVE.
    surtax_quintile_shares: tuple = (0.0, 0.0, 0.0, 0.10, 0.90)
    # Smoothing band below the threshold: from surtax_band_start up to the
    # threshold, filers pay a flat rate on income above the band start, set so
    # the charge reaches rate * threshold exactly at the threshold (no cliff).
    # E.g. 1.25% at $150k with a $100k start -> 3.75% of income above $100k.
    # 0 = no band. PLACEHOLDERS -> CRA final T1 statistics.
    surtax_band_start: float = 0.0
    band_filers: float = 2_600_000.0         # filers with total income in [band start, threshold)
    band_avg_income: float = 122_000.0       # their mean total income
    band_marginal_rate: float = 0.43         # combined rate they already face
    band_quintile_shares: tuple = (0.0, 0.0, 0.25, 0.45, 0.30)   # ILLUSTRATIVE
    # Where the contribution goes: "pool" tops up the healthy-food Fund;
    # "dividend" pays it out as an equal amount to every adult filer with total
    # income under the band start ($100k), with the GST/HST credit.
    surtax_use: str = "pool"
    under_band_filers: float = 25_700_000.0  # adult filers under $100k. PLACEHOLDER -> CRA
    # Share of those filers living in each household income quintile. ILLUSTRATIVE.
    dividend_quintile_shares: tuple = (0.22, 0.24, 0.24, 0.20, 0.10)


LEVY_RATES = [0.05, 0.10, 0.15, 0.20, 0.25]


# ---------------------------------------------------------------- price index
def regional_price_index(catalog_csv):
    """Relative price level per province from harvested prices (same UPC across capitals)."""
    df = pd.read_csv(catalog_csv, dtype={"product_number": str})
    df = df[df["regular_price"].notna() & (df["product_number"] != "")]
    df["rel"] = df["regular_price"] / df.groupby("product_number")["regular_price"].transform("mean")
    return df.groupby("province")["rel"].mean().to_dict()


# ---------------------------------------------------------------- core model
def _existing_rates(row, a):
    """Effective existing sales-tax rate on each spending category."""
    federal = row.gst + row.hst
    unhealthy_grocery = federal * a.gst_taxable_share_unhealthy_grocery + row.pst * row.pst_unhealthy_grocery
    restaurant = federal + row.pst * row.pst_restaurant
    return unhealthy_grocery, restaurant


def _qty(price_change, elasticity):
    return max(0.0, (1 + price_change)) ** elasticity


def _solve_subsidy(pool, healthy_base, a, cross_qty):
    """Subsidy rate s such that s * spend at subsidized demand == pool (bisection)."""
    if pool <= 0 or healthy_base <= 0:
        return 0.0
    lo, hi = 0.0, 0.9
    for _ in range(60):
        s = (lo + hi) / 2
        cost = s * healthy_base * _qty(-s * a.pass_through, a.e_healthy) * cross_qty
        lo, hi = (s, hi) if cost < pool else (lo, s)
    return (lo + hi) / 2


def household_flows(t, a, row, price_index=1.0, unhealthy_base_left=(1.0, 1.0),
                    spend_mult=1.0, unhealthy_mult=1.0, restaurant_mult=1.0):
    """Per-household levy paid, existing-tax change and spending bases for one levy rate."""
    left_g, left_r = unhealthy_base_left
    grocery = a.grocery_spend * spend_mult * price_index
    restaurant = a.restaurant_spend * restaurant_mult * price_index
    ug = grocery * min(1.0, a.unhealthy_share_grocery * unhealthy_mult) * left_g
    ur = restaurant * a.unhealthy_share_restaurant * left_r
    healthy = grocery - ug                       # reformulated products join the healthy base

    tax_g, tax_r = _existing_rates(row, a)
    dp = t * a.pass_through
    if not a.levy_in_sales_tax_base:
        dp_g, dp_r = dp / (1 + tax_g), dp / (1 + tax_r)
    else:
        dp_g = dp_r = dp
    qg, qr = _qty(dp_g, a.e_unhealthy_grocery), _qty(dp_r, a.e_restaurant)

    levy = t * (ug * qg + ur * qr)
    if a.levy_in_sales_tax_base:
        new_tax = tax_g * ug * (1 + t) * qg + tax_r * ur * (1 + t) * qr
    else:
        new_tax = tax_g * ug * qg + tax_r * ur * qr
    sales_tax_change = new_tax - (tax_g * ug + tax_r * ur)

    unhealthy_share_of_levy = ug * qg / max(ug * qg + ur * qr, 1e-9)
    cross_qty = (1 + dp_g) ** a.cross_to_healthy
    return {"levy": levy, "sales_tax_change": sales_tax_change, "healthy_base": healthy,
            "cross_qty": cross_qty, "grocery_share_of_levy": unhealthy_share_of_levy,
            "unhealthy_qty_change": (ug * qg + ur * qr) / max(ug + ur, 1e-9) - 1}


def simulate(a=Assumptions(), levy_rates=LEVY_RATES, price_index=None):
    """Province x levy rate x year results."""
    price_index = price_index or {}
    credit_share = a.manufacturer_credit_share + a.franchisor_credit_share
    subsidy_share = 1 - a.admin_share - credit_share
    rows = []
    for t in levy_rates:
        for year in range(1, a.years + 1):
            left = ((1 - a.reformulation_rate_grocery) ** (year - 1),
                    (1 - a.reformulation_rate_restaurant) ** (year - 1))
            prov = {}
            for p, row in TAX_REGIMES.iterrows():
                f = household_flows(t, a, row, price_index.get(p, 1.0), left)
                prov[p] = (row, f)

            if a.pooling == "federal":
                total_pool = sum(f["levy"] * subsidy_share * r.households for r, f in prov.values())
                total_base = sum(f["healthy_base"] * f["cross_qty"] * r.households for r, f in prov.values())
                avg_cross = total_base / sum(f["healthy_base"] * r.households for r, f in prov.values())
                national_s = _solve_subsidy(total_pool, total_base / avg_cross, a, avg_cross)

            for p, (row, f) in prov.items():
                pool = f["levy"] * subsidy_share
                s = national_s if a.pooling == "federal" else _solve_subsidy(pool, f["healthy_base"], a, f["cross_qty"])
                subsidy = s * f["healthy_base"] * _qty(-s * a.pass_through, a.e_healthy) * f["cross_qty"]
                hh = row.households
                rows.append({
                    "levy_rate": t, "year": year, "province": p, "regime": row.regime,
                    "healthy_price_cut": s * a.pass_through,
                    "unhealthy_qty_change": f["unhealthy_qty_change"],
                    "levy_revenue_m": f["levy"] * hh / 1e6,
                    "existing_sales_tax_change_m": f["sales_tax_change"] * hh / 1e6,
                    "manufacturer_credits_m": f["levy"] * a.manufacturer_credit_share * hh / 1e6,
                    "franchisor_credits_m": f["levy"] * a.franchisor_credit_share * hh / 1e6,
                    "subsidy_paid_m": subsidy * hh / 1e6,
                    "net_per_household": subsidy - f["levy"] - f["sales_tax_change"],
                })
    return pd.DataFrame(rows)


def income_surtax(a=Assumptions()):
    """National annual revenue ($) from the high-earner surtax, after behavioural response."""
    zero = {"static": 0.0, "surtax_collected": 0.0, "existing_tax_lost": 0.0,
            "bunching_filers": 0.0, "band_collected": 0.0, "net": 0.0}
    if a.surtax_rate <= 0:
        return zero
    rate, T, n = a.surtax_rate, a.surtax_threshold, a.surtax_filers
    excess = a.surtax_avg_income - T

    # Notch: with an all-income charge and no phase-in, anyone whose income is
    # within the "dominated band" above T keeps more after tax by reporting T.
    bunching = 0.0
    smoothed = a.surtax_band_start > 0 and a.surtax_on_all_income
    if a.surtax_avoidance and a.surtax_on_all_income and a.surtax_phase_in <= 0 and not smoothed:
        band = rate * T / (1 - a.top_marginal_rate - rate)   # $ of income
        bunching = min(n, a.filers_per_1k_at_threshold * band / 1_000)

    def charge(income):
        if not a.surtax_on_all_income:
            return rate * max(0.0, income - T) if income >= T else 0.0
        if a.surtax_phase_in > 0:
            return min(rate * income, a.surtax_phase_in * max(0.0, income - T))
        return rate * income if income >= T else 0.0

    static = charge(a.surtax_avg_income) * n
    # Everyone left above the line faces +rate at the margin and reports less income.
    payers = n - bunching
    shrink = a.eti * rate / (1 - a.top_marginal_rate) if a.surtax_avoidance else 0.0
    new_income = a.surtax_avg_income - shrink * excess
    collected = charge(new_income) * payers
    lost_income = shrink * excess * payers
    if bunching:
        lost_income += bunching * band / 2   # bunchers drop to T, ~half the band each
    existing_lost = a.top_marginal_rate * lost_income

    band_collected = band_static = 0.0
    if smoothed:
        band_rate = rate * T / (T - a.surtax_band_start)
        band_excess = a.band_avg_income - a.surtax_band_start
        band_static = band_rate * band_excess * a.band_filers
        band_shrink = (a.eti * band_rate / (1 - a.band_marginal_rate)) if a.surtax_avoidance else 0.0
        band_lost = band_shrink * band_excess * a.band_filers
        band_collected = band_rate * (band_excess - band_shrink * band_excess) * a.band_filers
        existing_lost += a.band_marginal_rate * band_lost
        static += band_static
        collected += band_collected
    return {"static": static, "surtax_collected": collected, "existing_tax_lost": existing_lost,
            "bunching_filers": bunching, "band_collected": band_collected,
            "net": collected - existing_lost}


def quintile_impact(t, a=Assumptions(), province="ON", price_index=1.0):
    """
    Year-1 net gain(+)/loss(-) per household by income quintile, provincial pooling.
    Each quintile is 20% of households; the subsidy pool is split between a
    universal shelf-price cut and, if a.targeted_share > 0, a low-income program.
    """
    row = TAX_REGIMES.loc[province]
    flows = [household_flows(t, a, row, price_index, spend_mult=q.food_spend_multiplier,
                             unhealthy_mult=q.unhealthy_share_multiplier,
                             restaurant_mult=q.restaurant_multiplier)
             for q in a.quintiles.itertuples()]
    subsidy_share = 1 - a.admin_share - a.manufacturer_credit_share - a.franchisor_credit_share
    pool = sum(f["levy"] for f in flows) / len(flows) * subsidy_share   # per average household
    # Surtax: collected through the existing tax system, so no extra admin cut.
    households = TAX_REGIMES["households"].sum()
    surtax = income_surtax(a)
    dividend = [0.0] * len(flows)
    if a.surtax_use == "dividend":
        dividend = [surtax["net"] * share / (households / len(flows))
                    for share in a.dividend_quintile_shares]
    else:
        pool += surtax["net"] / households
    n_q = len(flows)
    top_part = surtax["surtax_collected"] - surtax["band_collected"]
    surtax_paid = [(top_part * top + surtax["band_collected"] * band) / (households / n_q)
                   for top, band in zip(a.surtax_quintile_shares, a.band_quintile_shares)]

    # Health levy rebate comes off the top of the pool.
    eligible = [i in a.targeted_quintiles for i in range(len(flows))]
    rebates = [f["levy"] + f["sales_tax_change"] if (a.levy_rebate and e) else 0.0
               for f, e in zip(flows, eligible)]
    pool -= sum(rebates) / n_q

    # Universal shelf-price cut funded by the untargeted part of the pool.
    mean_base = sum(f["healthy_base"] for f in flows) / len(flows)
    mean_cross = sum(f["healthy_base"] * f["cross_qty"] for f in flows) / len(flows) / mean_base
    s = _solve_subsidy(pool * (1 - a.targeted_share), mean_base, a, mean_cross)

    # Targeted program: budget per eligible household.
    per_eligible = pool * a.targeted_share * len(flows) / max(sum(eligible), 1)

    def universal(f):
        return s * f["healthy_base"] * _qty(-s * a.pass_through, a.e_healthy) * f["cross_qty"]

    def voucher_cost(v, f):
        total = (s + v) * f["healthy_base"] * _qty(-(s + v) * a.pass_through, a.e_healthy) * f["cross_qty"]
        return total - universal(f)

    v = 0.0
    if a.targeting_mode == "voucher" and per_eligible > 0:
        budget = per_eligible * (1 - a.voucher_admin_share)
        elig_flows = [f for f, e in zip(flows, eligible) if e]
        lo, hi = 0.0, 0.9 - s
        for _ in range(60):
            v = (lo + hi) / 2
            cost = sum(voucher_cost(v, f) for f in elig_flows) / len(elig_flows)
            lo, hi = (v, hi) if cost < budget else (lo, v)
        v = (lo + hi) / 2

    out = []
    for q, f, is_eligible in zip(a.quintiles.itertuples(), flows, eligible):
        benefit, extra_cut = 0.0, 0.0
        if is_eligible and a.targeted_share > 0:
            if a.targeting_mode == "voucher":
                benefit, extra_cut = voucher_cost(v, f), v
            else:
                benefit = per_eligible
        cut = s + extra_cut
        paid = surtax_paid[len(out)]
        rebate = rebates[len(out)]
        div = dividend[len(out)]
        net = universal(f) + benefit + rebate + div - f["levy"] - f["sales_tax_change"] - paid
        out.append({"quintile": q.quintile, "levy_paid": f["levy"], "surtax_paid": paid,
                    "contribution_dividend": div,
                    "levy_rebate": rebate,
                    "universal_subsidy": universal(f), "targeted_benefit": benefit,
                    "healthy_price_cut": cut * a.pass_through,
                    "healthy_qty_change": _qty(-cut * a.pass_through, a.e_healthy) * f["cross_qty"] - 1,
                    "net_per_household": net, "net_pct_income": net / q.income})
    return pd.DataFrame(out)


def compare_targeting(t, province="ON", base=Assumptions()):
    """Year-1 quintile results for universal vs. low-income-targeted designs."""
    designs = {
        "Universal price cut": replace(base, targeted_share=0.0),
        "50% rebate to Q1-Q2": replace(base, targeted_share=0.5, targeting_mode="rebate"),
        "100% rebate to Q1-Q2": replace(base, targeted_share=1.0, targeting_mode="rebate"),
        "50% voucher to Q1-Q2": replace(base, targeted_share=0.5, targeting_mode="voucher"),
        "100% voucher to Q1-Q2": replace(base, targeted_share=1.0, targeting_mode="voucher"),
    }
    frames = []
    for name, a in designs.items():
        df = quintile_impact(t, a, province)
        df.insert(0, "design", name)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    pd.set_option("display.width", 160, "display.max_columns", 20)
    idx = regional_price_index(sys.argv[1]) if len(sys.argv) > 1 else None
    a = Assumptions()
    results = simulate(a, price_index=idx)
    results.to_csv("subsidy_scenarios.csv", index=False)

    y1 = results[results.year == 1]
    print("\nYear 1, all provinces -- national totals by levy rate ($ millions)")
    print(y1.groupby("levy_rate")[["levy_revenue_m", "existing_sales_tax_change_m",
                                   "manufacturer_credits_m", "franchisor_credits_m",
                                   "subsidy_paid_m"]].sum().round(0))

    print("\nYear 1 at 15% levy, by province")
    cols = ["regime", "healthy_price_cut", "unhealthy_qty_change", "levy_revenue_m", "net_per_household"]
    print(y1[y1.levy_rate == 0.15].set_index("province")[cols].round(3))

    print("\nBase erosion from reformulation at 15% levy (national, $ millions)")
    print(results[results.levy_rate == 0.15].groupby("year")[["levy_revenue_m", "subsidy_paid_m"]].sum().round(0))

    print("\nYear 1 at 15% levy, Ontario -- universal vs low-income targeting (ILLUSTRATIVE quintile inputs)")
    cmp = compare_targeting(0.15, "ON", a)
    cmp.to_csv("targeting_comparison.csv", index=False)
    order = cmp["design"].unique()
    print("Net gain(+)/loss(-) as % of household income:")
    print((cmp.pivot(index="design", columns="quintile", values="net_pct_income").reindex(order) * 100).round(2))
    print("Net $ per household per year:")
    print(cmp.pivot(index="design", columns="quintile", values="net_per_household").reindex(order).round(0))
    print("Healthy-food purchases, change in quantity:")
    print((cmp.pivot(index="design", columns="quintile", values="healthy_qty_change").reindex(order) * 100).round(1))

    st = income_surtax(replace(a, surtax_rate=0.01))
    print("\n1% of total income for anyone earning $150,000 or more, no deductions (national, $ millions/yr)")
    print(f"  surtax collected       {st['surtax_collected'] / 1e6:8.0f}")
    print(f"  net to subsidy pool    {st['net'] / 1e6:8.0f}")
    av = income_surtax(replace(a, surtax_rate=0.01, surtax_avoidance=True))
    print(f"  sensitivity -- if filers still react (eti {a.eti}, notch bunching):")
    print(f"    net {av['net'] / 1e6:6.0f}, filers bunching under $150k {av['bunching_filers']:,.0f}")
    print("\nYear 1 at 15% levy, Ontario -- with vs without the surtax (% of income)")
    with_surtax = compare_targeting(0.15, "ON", replace(a, surtax_rate=0.01))
    both = pd.concat([cmp.assign(surtax="no surtax"), with_surtax.assign(surtax="1% of income >=$150k")])
    both.to_csv("targeting_comparison.csv", index=False)
    table = both.pivot_table(index=["design", "surtax"], columns="quintile", values="net_pct_income",
                             sort=False) * 100
    print(table.round(2))
    print("Healthy-food purchases, change in quantity, Q1 (lowest):")
    q1 = both[both.quintile == "Q1 (lowest)"].pivot_table(index="design", columns="surtax",
                                                          values="healthy_qty_change", sort=False) * 100
    print(q1.round(1))

    print("\nBill design, year 1 at 15% levy, Ontario: 50% healthy food benefit to Q1-Q2")
    designs = {
        "1% contribution, no rebate": replace(a, surtax_rate=0.01, targeted_share=0.5, targeting_mode="voucher"),
        "1.25% contribution + levy rebate": replace(a, surtax_rate=0.0125, targeted_share=0.5,
                                                   targeting_mode="voucher", levy_rebate=True),
        "+ smoothed from $100k": replace(a, surtax_rate=0.0125, targeted_share=0.5, targeting_mode="voucher",
                                         levy_rebate=True, surtax_band_start=100_000),
        "+ contribution paid out under $100k": replace(a, surtax_rate=0.0125, targeted_share=0.5,
                                                       targeting_mode="voucher", levy_rebate=True,
                                                       surtax_band_start=100_000, surtax_use="dividend"),
    }
    households = TAX_REGIMES["households"].sum()
    for name, d in designs.items():
        q = quintile_impact(0.15, d, "ON")
        print(f"  {name}")
        print("    net % of income:  " + "  ".join(f"{v * 100:+.2f}" for v in q.net_pct_income))
        print("    net $/household:  " + "  ".join(f"{v:+.0f}" for v in q.net_per_household))
        print(f"    levy rebate, Q1/Q2 per household: {q.levy_rebate.iloc[0]:.0f} / {q.levy_rebate.iloc[1]:.0f};"
              f" national cost ~{q.levy_rebate.mean() * households / 1e6:,.0f} M")
        print(f"    healthy purchases Q1: {q.healthy_qty_change.iloc[0] * 100:+.1f}%,"
              f" healthy price cut Q1/Q3: {q.healthy_price_cut.iloc[0] * 100:.1f}% / {q.healthy_price_cut.iloc[2] * 100:.1f}%")
        st = income_surtax(d)
        print(f"    contribution revenue: {st['net'] / 1e6:,.0f} M (band $100k-150k: {st['band_collected'] / 1e6:,.0f} M)")
        av = income_surtax(replace(d, surtax_avoidance=True))
        print(f"    if people still avoid it: {av['net'] / 1e6:,.0f} M")
        if d.surtax_use == "dividend":
            print(f"    dividend per adult under $100k: {st['net'] / d.under_band_filers:,.0f}/yr;"
                  f" per household Q1..Q5: " + " ".join(f"{v:.0f}" for v in q.contribution_dividend))
