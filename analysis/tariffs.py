"""Irish residential electricity plans used for the tariff check.

All unit rates are cent/kWh INCLUDING 9 % VAT, standing charges are EUR/year inc. VAT for an
URBAN smart meter. Researched 2026-09-20 from the pages in SOURCES; rates move often, so every
number here lands in an editable cell of the workbook. Hours are local clock hours (0-24);
a window with start > end wraps past midnight. Band priority: EV > peak > night > day.
"""
from __future__ import annotations

VAT = 1.09
RESEARCH_DATE = "2026-09-20"


def _inc(ex_vat: float) -> float:
    return round(ex_vat * VAT, 2)


NIGHT = (23, 8)
PEAK = (17, 19)

# key: (name, supplier, day, night, night_win, peak, peak_win, ev, ev_win, free_sat, standing, export, note)
PLANS = [
    dict(key="energia_ev_now", name="EV Smart Drive (until 11 Oct 2026)", supplier="Energia",
         day=_inc(40.94), ev=_inc(9.61), ev_win=(2, 6), standing=_inc(243.13), export=18.5,
         note="Standard (undiscounted) rates. Only plan with a 4-hour 02:00-06:00 window - matches "
              "Martin's import pattern, so this is the inferred current plan.", src="energia,sqi_energia"),
    dict(key="energia_ev_oct", name="EV Smart Drive (from 12 Oct 2026)", supplier="Energia",
         day=_inc(40.94), ev=_inc(12.49), ev_win=(2, 6), standing=_inc(311.21), export=18.5,
         note="Announced increase: EV rate +30 %, EV standing charge +28 %. Standing charge assumed "
              "ex-VAT in the source - verify on bill.", src="energia,sqi_energia"),
    dict(key="energia_ev_plus", name="EV Smart Drive Plus (from 12 Oct 2026)", supplier="Energia",
         day=_inc(39.69), night=_inc(24.45), night_win=NIGHT, peak=_inc(52.07), peak_win=PEAK,
         ev=_inc(13.50), ev_win=(2, 6), standing=_inc(311.21), export=18.5,
         note="Standard rates. EV window assumed 02:00-06:00.", src="energia,sqi_energia"),
    dict(key="energia_smart", name="Smart day/night/peak (from 12 Oct 2026)", supplier="Energia",
         day=_inc(39.81), night=_inc(29.00), night_win=NIGHT, peak=_inc(45.57), peak_win=PEAK,
         standing=_inc(255.29), export=18.5, note="Standard rates. Night rate +28 % from 12 Oct.",
         src="sqi_energia"),
    dict(key="ei_nightboost", name="Home Electric+ Night Boost", supplier="Electric Ireland",
         day=37.60, night=18.54, night_win=NIGHT, ev=10.88, ev_win=(2, 4), standing=328.58, export=19.5,
         note="Boost window is only 2 hours (02:00-04:00). Rates effective 1 Jul 2026.", src="sqi_ei"),
    dict(key="ei_sst", name="Home Electric+ SST Saver (20 % disc.)", supplier="Electric Ireland",
         day=32.43, night=17.04, night_win=NIGHT, peak=34.60, peak_win=PEAK, standing=250.77, export=19.5,
         note="Discounted new-customer rates, effective 1 Jul 2026.", src="sqi_ei"),
    dict(key="ei_flat", name="Home Electric+ Saver 24h (20 % disc.)", supplier="Electric Ireland",
         day=29.81, standing=250.77, export=19.5, note="Flat rate, no time bands.", src="sqi_ei"),
    dict(key="ei_weekender", name="Home Electric+ Weekender", supplier="Electric Ireland",
         day=38.65, free_sat=1, standing=250.77, export=19.5,
         note="Free electricity 08:00-23:00 on one weekend day (modelled as Saturday).", src="sqi_ei"),
    dict(key="bge_ev", name="EV Smart plan (from 9 Oct 2026)", supplier="Bord Gais Energy",
         day=35.23, night=26.57, night_win=NIGHT, peak=49.14, peak_win=PEAK, ev=8.98, ev_win=(2, 5),
         standing=364.89, export=18.5, note="3-hour EV window. Rates shown by supplier inc. VAT; "
         "15 % new-customer discount may apply.", src="bge"),
    dict(key="pinergy_ev", name="Lifestyle EV Drive Time (from 1 Aug 2026)", supplier="Pinergy",
         day=37.03, ev=9.99, ev_win=(2, 5), standing=283.47, export=18.5,
         note="EV rate rose 82 % (5.49c -> 9.99c) on 1 Aug 2026. 3-hour window.", src="pinergy"),
    dict(key="sse_ev", name="1 Yr Smart EV Charge (15 % disc.)", supplier="SSE Airtricity",
         day=41.74, night=23.44, night_win=NIGHT, peak=49.73, peak_win=PEAK, ev=9.05, ev_win=(2, 5),
         standing=334.74, export=19.5, note="Tariff sheet V11, rates valid from 20 Oct 2025 - check "
         "for a newer version.", src="sse"),
]
BASELINE_KEY = "energia_ev_now"
FUTURE_KEY = "energia_ev_oct"

PSO_EUR_PER_MONTH = 1.59          # inc. VAT; drops to 0.51 from 1 Oct 2026
GRID_CO2_KG_PER_KWH = 0.2241      # SEAI 2026 electricity emission factor
CRU_TYPICAL_KWH_PER_YEAR = 4200   # CRU typical domestic consumption (no EV)
EXPORT_TAX_FREE_EUR = 400         # micro-generation income disregard, extended to end 2028

SOURCES = {
    "energia": ("Energia - Our tariffs", "https://www.energia.ie/about-energia/our-tariffs"),
    "sqi_energia": ("Energia price increase 12 Oct 2026 (SolarQuotesIreland)",
                    "https://solarquotesireland.ie/energia-price-increase-october-2026/"),
    "sqi_ei": ("Electric Ireland rates & plans, effective 1 Jul 2026 (SolarQuotesIreland)",
               "https://solarquotesireland.ie/electric-ireland-rates-plans-2026/"),
    "bge": ("Bord Gais Energy - EV plan comparison", "https://www.bordgaisenergy.ie/home/ev-plan-comparison"),
    "pinergy": ("Pinergy overnight rate +82 % (Newstalk) / Lifestyle tariffs",
                "https://www.newstalk.com/news/energy-2272436"),
    "sse": ("SSE Airtricity 1 Year Smart EV Charge tariff sheet",
            "https://www.sseairtricity.com/assets/Tariffs/ROI/Current/1YR-ELEC-15-EVCharge.pdf"),
    "ceg": ("Clean Export Guarantee rates by supplier 2026",
            "https://solarquotesireland.ie/clean-export-guarantee-rates-ireland/"),
    "tax": ("Budget 2026: EUR 400 export income disregard extended to end 2028",
            "https://energyefficiency.ie/blog/budget-2026-e400-tax-threshold-for-selling-excess-solar-extended-by-3-years/"),
    "co2": ("SEAI electricity emission factor 2026 (224.1 gCO2/kWh)",
            "https://www.utilityfair.ie/business-energy-insights/carbon-emission-factors-irish-business-guide"),
    "cru": ("CRU typical consumption 4,200 kWh/yr (Electric Ireland EAB explainer)",
            "https://www.electricireland.ie/residential/help/detail/what-is-the-estimated-annual-bill-and-how-is-it-calculated"),
    "evplans": ("Best EV electricity plans Ireland 2026 (Volteire)",
                "https://volteire.ie/blog/best-ev-electricity-plans-ireland"),
}


def in_window(t: float, win) -> bool:
    if not win:
        return False
    s, e = win
    return (s <= t < e) if s < e else (t >= s or t < e)


def rate_vector(plan: dict) -> list[float]:
    """Cent/kWh for each of the 48 half-hours of a day (same logic as the workbook formula)."""
    out = []
    for i in range(48):
        t = i / 2
        if plan.get("ev") is not None and in_window(t, plan.get("ev_win")):
            r = plan["ev"]
        elif plan.get("peak") is not None and in_window(t, plan.get("peak_win")):
            r = plan["peak"]
        elif plan.get("night") is not None and in_window(t, plan.get("night_win")):
            r = plan["night"]
        else:
            r = plan["day"]
        out.append(r * (1 - plan.get("discount", 0.0)))
    return out
