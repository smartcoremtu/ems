"""Check the MartinH HEMS measurements against the ESB smart meter and the SolarMan portal.

    .venv/Scripts/python martinh_validation.py [--refresh]

Reads ../data/MartinH/*.csv.gz plus ../data/Broomfield Data/ (ESB HDF file, SolarMan workbooks),
writes ../data/MartinH_Validation.xlsx (git-ignored: identifiable household data, and the ESB
file names carry the MPRN).
"""
from __future__ import annotations

import sys

import pandas as pd

import metrics
import validation
from hems_data import load_site
from martinh_report import (BATT, DATA, EXPORT, IMPORT, LOAD, MUTED, PV, SITE, SITE_LABEL, Book, eur, rng)
from reference_data import read_esb_hdf, read_solarman

REF = DATA / "Broomfield Data"
OUT = DATA / f"{SITE}_Validation.xlsx"

STAT_HEADERS = {"n": "Intervals compared", "test_total": "HEMS total", "ref_total": "Reference total",
                "total_diff_pct": "HEMS vs reference", "bias": "Mean error", "mae": "Mean absolute error",
                "rmse": "RMS error", "p95_abs": "95 % of errors below", "r": "Correlation r",
                "slope": "Slope (gain)", "intercept": "Offset"}
STAT_FMTS = {"n": "#,##0", "test_total": "#,##0.0", "ref_total": "#,##0.0", "total_diff_pct": "+0.00%;-0.00%",
             "bias": "+0.0000;-0.0000", "mae": "0.0000", "rmse": "0.0000", "p95_abs": "0.0000", "r": "0.0000",
             "slope": "0.0000", "intercept": "+0.0000;-0.0000"}
PCT = "+0.00%;-0.00%"


def esb_file(kind: str = "calckWh"):
    files = sorted((REF / "ESB Smart Meter Data").glob(f"HDF_{kind}_*.csv"))
    if not files:
        raise SystemExit(f"No ESB HDF_{kind} file in {REF}")
    return files[-1]


def compute(R: dict) -> dict:
    f, s = esb_file(), REF / "SolarMan Data"
    return validation.build(R, read_esb_hdf(f), read_esb_hdf(f, "utc"), read_solarman(s), read_solarman(s, "utc"))


def naive(df: pd.DataFrame, name: str) -> pd.DataFrame:
    df = df.copy()
    df.index = df.index.tz_localize(None)
    df.index.name = name
    return df


def build_workbook(R: dict, V: dict) -> None:
    k, B = R["k"], Book(OUT)
    sh, sd, imp = V["stats_halfhour"], V["stats_daily"], V["impact"]
    period = f"{k['start']:%d %b %Y} to {k['end']:%d %b %Y} ({k['days']} full days)"
    i_pct = sh.loc["import: HEMS daily counters (used in report)", "total_diff_pct"]
    x_pct = sh.loc["export: HEMS daily counters (used in report)", "total_diff_pct"]
    i_r = sh.loc["import: HEMS daily counters (used in report)", "r"]
    bill_d = imp.loc["Net electricity bill, EUR", "difference"]
    se, bat, sy = V["stats_solarman_energy"], V["battery"], V["stats_year"]

    for n in ["Verdict", "ESB vs HEMS", "Daily", "SolarMan & battery", "Whole year", "Method", "Data_halfhour"]:
        B.wb.add_worksheet(n)

    # ================================ Verdict ===================================================
    ws = B.sheet("Verdict", f"{SITE_LABEL} - how accurate is the energy box?",
                 f"{period}. The HEMS figures in {SITE}_Energy_Report.xlsx checked against the ESB Networks smart meter "
                 f"(the meter the bill is made from - independent hardware) and against the inverter maker's own SolarMan portal "
                 f"(same sensors, different data path; it also has the battery readings the box does not collect).",
                 tab=LOAD, width=10.7, first_col_width=2)
    ws.set_row(3, 20); ws.set_row(4, 36); ws.set_row(5, 32)
    B.tile(ws, 3, 1, "Grid import, box vs ESB meter", i_pct, f"{imp.iloc[0, 0]:,.0f} vs {imp.iloc[0, 1]:,.0f} kWh", PCT)
    B.tile(ws, 3, 4, "Grid export, box vs ESB meter", x_pct, f"{imp.iloc[1, 0]:,.0f} vs {imp.iloc[1, 1]:,.0f} kWh", PCT)
    B.tile(ws, 3, 7, "Net bill, box vs ESB meter", bill_d, f"{eur(imp.loc['Net electricity bill, EUR', 'hems'], 2)} vs "
           f"{eur(imp.loc['Net electricity bill, EUR', 'esb'], 2)} on the current plan", '+"€"0.00;-"€"0.00')
    B.tile(ws, 3, 10, "Half-hour by half-hour match", i_r, f"correlation over {int(sh.iloc[0]['n']):,} half-hours (1 = identical)", "0.0000")

    lines = [
        ("Verdict", f"The grid measurements in the report are accurate. Over {k['days']} days the box read {abs(i_pct):.2%} "
         f"{'less' if i_pct < 0 else 'more'} import and {abs(x_pct):.2%} {'less' if x_pct < 0 else 'more'} export than the ESB meter; a domestic "
         f"revenue meter is itself only guaranteed to about 1-2 %. Every month is within {V['monthly']['import_diff_pct'].abs().max():.1%} on import and "
         f"{V['monthly']['export_diff_pct'].abs().max():.1%} on export, the worst single day is {V['daily']['import_diff_pct'].abs().max():.1%}, and the bill computed from the "
         f"box is within {eur(abs(bill_d), 2)} of the bill computed from the meter. No conclusion in the energy report changes."),
        ("What the small error is", f"A gain error, not an offset: the inverter's grid sensor (a clip-on CT) reads about "
         f"{abs(V['bins']['import'].loc['over 3', 'diff_pct']):.1%} low at car-charging currents, and that one band is "
         f"{V['bins']['import'].loc['over 3', 'share_of_total_diff']:.0%} of the whole import difference. Export (lower currents) has no measurable gain error. "
         f"The box also registers energy about half a minute late (the counter is polled every ~30 s), which moves a little import from the 02:00 half-hour "
         f"into later ones; over a day it cancels."),
        ("Counters beat integrated power", f"The report builds kWh from the inverter's daily counters. The alternative - integrating the 5-second power signal - "
         f"reads {sh.loc['import: HEMS integrated power', 'total_diff_pct']:+.1%} import and {sh.loc['export: HEMS integrated power', 'total_diff_pct']:+.1%} export against the meter. "
         f"The counters are the right choice and stay."),
        ("One bug found and fixed", "On 25 May 2026 at 16:01 the inverter returned one corrupt reading (export counter 10.73 -> 12.09 -> 10.75 kWh). The loader counted the jump as "
         "1.34 kWh of export. hems_data.py now drops such single-point spikes; the energy report has been regenerated (export -1.5 kWh, bill +EUR 0.27)."),
        ("Peaks are slightly under-read", f"Highest half-hour import: box {imp.loc['Highest half-hour import, kW', 'hems']:.2f} kW, meter {imp.loc['Highest half-hour import, kW', 'esb']:.2f} kW. "
         f"The meter has {imp.loc['Half-hours importing above 12 kW', 'esb']:.0f} half-hours above 12 kW, the box {imp.loc['Half-hours importing above 12 kW', 'hems']:.0f} - same CT gain error, "
         f"sitting right on the threshold. The report's warning about the 12 kVA connection limit is, if anything, understated."),
        ("Battery estimate confirmed", f"The box has no battery sensor; the report estimates battery power from the inverter's AC, DC and loss figures. SolarMan meters it: the estimate is "
         f"{se.loc['batt_charge', 'total_diff_pct']:+.1%} on charge and {se.loc['batt_discharge', 'total_diff_pct']:+.1%} on discharge energy. Battery size from SolarMan's state of charge: "
         f"{bat['kwh_per_100pct_discharging']:.1f} kWh per 100 %, {bat['usable_kwh']:.1f} kWh usable between the {bat['soc_floor_typical']:.0f} % floor and full - the report had estimated "
         f"{bat['report_usable_kwh_estimate']:.1f} kWh. Round trip at the battery terminals {bat['round_trip_efficiency']:.0%}; the report's 90 % also covers the inverter, so it stands."),
        ("Solar and house load: not independently checked", f"The ESB meter cannot see them. Two readings of the same inverter differ: box solar is {se.loc['pv', 'total_diff_pct']:+.1%} "
         f"and load {se.loc['load', 'total_diff_pct']:+.1%} against SolarMan, but SolarMan reports solar in 100 W steps every 5-10 min, so treat solar as good to about +/-4 % "
         f"and self-consumption / self-sufficiency to a couple of points."),
        ("Winter can be trusted too", f"The inverter's grid sensor tracked the ESB meter across all 13 months of SolarMan data ({sy.loc['import', 'total_diff_pct']:+.2%} import, "
         f"{sy.loc['export', 'total_diff_pct']:+.2%} export, Sep 2025 - Sep 2026), so the agreement is not a summer artefact."),
    ]
    r = 7
    for head, body in lines:
        ws.merge_range(r, 1, r, 3, head, B.f(bold=True, text_wrap=True, valign="top"))
        ws.merge_range(r, 4, r, 15, body, B.f(text_wrap=True, valign="top"))
        ws.set_row(r, 15 * max(2, len(body) // 105 + 1)); r += 1
    r += 1
    B.heading(ws, r, "Headline figures of the energy report, recomputed from the ESB meter", col=1); r += 1
    t = imp.copy(); t.index.name = "Measure"
    ws.set_column(16, 22, 13)
    # measure labels need room: write the table from a merged label column
    head = B.f(bold=True, font_color="#52514e", bottom=1, bottom_color="#c3c2b7")
    ws.merge_range(r, 1, r, 5, "Measure", head)
    for j, h in enumerate(["Energy box", "ESB meter", "Difference", "Difference %"]):
        ws.merge_range(r, 6 + 2 * j, r, 7 + 2 * j, h, head)
    for name, row in t.iterrows():
        r += 1
        share = "Share" in name or "Self-" in name
        ws.merge_range(r, 1, r, 5, name, B.f())
        num = "0.0%" if share else ("#,##0.00" if name.endswith(("EUR", ", kW")) else "#,##0")
        dnum = '+0.0" pt";-0.0" pt"' if share else num.replace("#,##0", "+#,##0;-#,##0")
        for j, (v, f) in enumerate([(row["hems"], num), (row["esb"], num),
                                    (row["difference"] * (100 if share else 1), dnum), (row["difference_pct"], PCT)]):
            ws.merge_range(r, 6 + 2 * j, r, 7 + 2 * j, float(v), B.f(num_format=f))
    chart_row = r + 2

    # ================================ Daily =====================================================
    d = naive(V["daily"][["import_hems", "import_esb", "import_diff_pct", "export_hems", "export_esb",
                          "export_diff_pct", "import_hems_power", "export_hems_power", "hems_coverage"]], "Day")
    wd = B.sheet("Daily", "Day by day: energy box vs ESB meter",
                 "kWh per day. Export difference % is blank on days with under 1 kWh of export.", tab=IMPORT, first_col_width=14, width=12)
    wd.set_row(3, 45)
    d0, d1 = B.table(wd, 3, 0, d, index_fmt="ddd dd mmm yyyy", default="0.00",
                     fmts={"import_diff_pct": PCT, "export_diff_pct": PCT, "hems_coverage": "0.0%"},
                     headers={"import_hems": "Import, box", "import_esb": "Import, ESB", "import_diff_pct": "Import difference",
                              "export_hems": "Export, box", "export_esb": "Export, ESB", "export_diff_pct": "Export difference",
                              "import_hems_power": "Import, box (integrated power)", "export_hems_power": "Export, box (integrated power)",
                              "hems_coverage": "Box data coverage"})
    wd.freeze_panes(4, 1)
    cat = rng("Daily", d0, 0, d1, 0)
    B.chart(ws, f"B{chart_row + 1}", "line", "Grid import per day: the two lines should be one", [
        {"name": "ESB meter", "cat": cat, "val": rng("Daily", d0, 2, d1, 2), "color": MUTED, "width": 3.5},
        {"name": "Energy box", "cat": cat, "val": rng("Daily", d0, 1, d1, 1), "color": IMPORT, "width": 1.25}],
        size=(560, 300), y_title="kWh", y_fmt="0", x_interval=14, x_num_fmt="d mmm")
    B.chart(ws, f"I{chart_row + 1}", "line", "Grid export per day", [
        {"name": "ESB meter", "cat": cat, "val": rng("Daily", d0, 5, d1, 5), "color": MUTED, "width": 3.5},
        {"name": "Energy box", "cat": cat, "val": rng("Daily", d0, 4, d1, 4), "color": EXPORT, "width": 1.25}],
        size=(560, 300), y_title="kWh", y_fmt="0", x_interval=14, x_num_fmt="d mmm")
    B.chart(wd, "L4", "column", "Daily import difference, box vs ESB meter", [
        {"name": "Import difference", "cat": cat, "val": rng("Daily", d0, 3, d1, 3), "color": IMPORT, "gap": 30}],
        size=(720, 320), y_fmt="0%", x_interval=14, x_num_fmt="d mmm")
    B.chart(wd, "L21", "column", "Daily export difference, box vs ESB meter", [
        {"name": "Export difference", "cat": cat, "val": rng("Daily", d0, 6, d1, 6), "color": EXPORT, "gap": 30}],
        size=(720, 320), y_fmt="0%", x_interval=14, x_num_fmt="d mmm")

    # ================================ ESB vs HEMS ===============================================
    we = B.sheet("ESB vs HEMS", "Energy box vs ESB smart meter, in detail",
                 f"All {int(sh.iloc[0]['n']):,} half-hours of the report period; the ESB file had {V['esb_missing']} missing. kWh per interval. "
                 "'Slope' below 1 means the box under-reads in proportion to the flow (sensor gain); 'offset' is a constant error.",
                 tab=IMPORT, first_col_width=46, width=13)
    r = 3
    B.heading(we, r, "Half-hourly agreement"); r += 1
    we.set_row(r, 32)
    t = sh.copy(); t.index.name = "HEMS series vs ESB meter"
    _, r = B.table(we, r, 0, t, fmts=STAT_FMTS, headers=STAT_HEADERS)
    r += 2; B.heading(we, r, "Daily agreement"); r += 1
    we.set_row(r, 32)
    t = sd.copy(); t.index = [f"{i}: HEMS daily counters" for i in t.index]; t.index.name = "kWh per day"
    _, r = B.table(we, r, 0, t, fmts=STAT_FMTS, headers=STAT_HEADERS)
    r += 2; B.heading(we, r, "By month"); r += 1
    mo = naive(V["monthly"], "Month")[["import_hems", "import_esb", "import_diff_pct", "export_hems", "export_esb", "export_diff_pct"]]
    _, r = B.table(we, r, 0, mo, index_fmt="mmmm yyyy", default="#,##0.0", fmts={"import_diff_pct": PCT, "export_diff_pct": PCT},
                   headers={"import_hems": "Import, box", "import_esb": "Import, ESB", "import_diff_pct": "Difference",
                            "export_hems": "Export, box", "export_esb": "Export, ESB", "export_diff_pct": "Difference"})
    for reg in ("import", "export"):
        r += 2; B.heading(we, r, f"Where the {reg} difference comes from: half-hours grouped by the size of the ESB reading"); r += 1
        we.set_row(r, 32)
        t = V["bins"][reg].copy(); t.index = t.index.astype(str); t.index.name = "ESB kWh in the half-hour"
        _, r = B.table(we, r, 0, t, default="#,##0.0", fmts={"half_hours": "#,##0", "diff_pct": PCT, "share_of_total_diff": "0%"},
                       headers={"half_hours": "Half-hours", "hems_kwh": "Box kWh", "esb_kwh": "ESB kWh", "diff_kwh": "Difference kWh",
                                "diff_pct": "Difference", "share_of_total_diff": "Share of total difference"})
    r += 1
    B.note(we, r, "Reading the tables: below 0.025 kWh per half-hour (under 50 W) the box counter's 0.01 kWh steps and the meter's 0.0005 kWh steps disagree by a large percentage "
                  "but only a few kWh. The ESB meter also registers ~1.5 Wh of import AND export in the same idle half-hour, which the inverter nets to zero.", height=48, last_col=9)
    r += 2; B.heading(we, r, "The 15 half-hours that disagree most"); r += 1
    we.set_row(r, 32)
    w15 = naive(V["worst"], "Half-hour starting")[["import_hems", "import_esb", "import_diff", "export_hems", "export_esb", "export_diff", "hems_alive"]]
    _, r = B.table(we, r, 0, w15, index_fmt="ddd dd mmm yyyy hh:mm", default="0.000", fmts={"hems_alive": "0%"},
                   headers={"import_hems": "Import, box", "import_esb": "Import, ESB", "import_diff": "Difference", "export_hems": "Export, box",
                            "export_esb": "Export, ESB", "export_diff": "Difference", "hems_alive": "Box data coverage"})
    r += 1
    B.note(we, r, "Most are the 02:00 / 05:00-05:30 edges of the car-charging window (the half-minute counter delay), or follow a box restart (coverage below 100 %).", last_col=9)
    r += 2; B.heading(we, r, "Total kWh by half-hour of day (whole period)"); r += 1
    pr = V["profile"].copy(); pr.index.name = "Half-hour starting"
    p0, p1 = B.table(we, r, 0, pr, default="#,##0.00",
                     headers={"import_hems": "Import, box", "import_esb": "Import, ESB", "export_hems": "Export, box", "export_esb": "Export, ESB",
                              "import_diff": "Import difference", "export_diff": "Export difference"})
    B.chart(we, f"I{r + 1}", "column", "Box minus meter, total kWh by half-hour of day", [
        {"name": "Import difference", "cat": rng("ESB vs HEMS", p0, 0, p1, 0), "val": rng("ESB vs HEMS", p0, 5, p1, 5), "color": IMPORT, "gap": 40},
        {"name": "Export difference", "cat": rng("ESB vs HEMS", p0, 0, p1, 0), "val": rng("ESB vs HEMS", p0, 6, p1, 6), "color": EXPORT}],
        size=(760, 340), y_title="kWh over the period", x_interval=4)

    # ================================ SolarMan & battery ========================================
    n_hh, n_all = V["solarman_halfhours"]
    wsm = B.sheet("SolarMan & battery", "Energy box vs the SolarMan portal (same inverter, different route)",
                  "SolarMan is the inverter maker's cloud: 5-minute snapshots of the same sensors the box polls every ~5 s. It cannot prove the sensors right, but it shows "
                  "whether the box loses or distorts data, and it meters the battery, which the box does not. "
                  f"{n_hh:,} of {n_all:,} half-hours had complete SolarMan data (gaps up to 15 min interpolated).", tab=BATT, first_col_width=46, width=13)
    r = 3
    B.heading(wsm, r, "Energy per half-hour, matched half-hours only (kWh)"); r += 1
    wsm.set_row(r, 32)
    names = {"pv": "Solar", "load": "House load", "import": "Grid import", "export": "Grid export",
             "batt_discharge": "Battery discharge - box ESTIMATE vs SolarMan metered", "batt_charge": "Battery charge - box ESTIMATE vs SolarMan metered"}
    t = se.rename(index=names); t.index.name = "Box vs SolarMan"
    _, r = B.table(wsm, r, 0, t, fmts=STAT_FMTS, headers=STAT_HEADERS)
    r += 2; B.heading(wsm, r, "Power, 5-minute values (W): box 5-min mean vs SolarMan snapshot"); r += 1
    wsm.set_row(r, 32)
    t = V["stats_solarman_5min"].copy(); t.index.name = "Signal"
    pf = {**STAT_FMTS, "test_total": "#,##0", "ref_total": "#,##0", "bias": "+0.0;-0.0", "mae": "0.0", "rmse": "0.0", "p95_abs": "0.0", "intercept": "+0.0;-0.0"}
    _, r = B.table(wsm, r, 0, t.drop(columns=["test_total", "ref_total", "total_diff_pct"]), fmts=pf, headers=STAT_HEADERS)
    r += 1
    B.note(wsm, r, "A snapshot is compared with a mean, and SolarMan rounds to 10 W (solar: 100 W) and often repeats a value for 10 min, so the scatter here is SolarMan's, not the box's. "
                   "The inverter's AC output (second row) is not the solar figure SolarMan shows - the box's DC solar power is.", height=48, last_col=9)
    r += 2; B.heading(wsm, r, "Battery, from SolarMan battery power and state of charge (whole 13 months)"); r += 1
    bt = pd.DataFrame({"value": [bat["kwh_per_100pct_discharging"], bat["kwh_per_100pct_charging"], bat["round_trip_efficiency"] * 100, bat["soc_floor_typical"],
                                 bat["soc_min"], bat["usable_kwh"], bat["report_usable_kwh_estimate"], bat["max_charge_w"] / 1000,
                                 bat["report_charge_kw_estimate"], bat["max_discharge_w"] / 1000, bat["hours_used"]]},
                      index=["kWh delivered per 100 % state of charge", "kWh absorbed per 100 % state of charge", "Round-trip efficiency, %",
                             "Usual state-of-charge floor, %", "Lowest state of charge seen, %", "Usable capacity (floor to full), kWh",
                             "  energy report's estimate of usable capacity, kWh", "Highest charge power, kW", "  energy report's estimate of charge power, kW",
                             "Highest discharge power, kW", "Hours of clean charge/discharge data used"])
    bt.index.name = "Battery"
    _, r = B.table(wsm, r, 0, bt, default="#,##0.0")
    r += 2; B.heading(wsm, r, "Per day (days with all 48 half-hours matched), kWh"); r += 1
    sdly = naive(V["solarman_daily"], "Day")[["batt_charge", "batt_charge_solarman", "batt_discharge", "batt_discharge_solarman",
                                               "pv", "pv_solarman", "load", "load_solarman"]]
    wsm.set_row(r, 45)
    s0, s1 = B.table(wsm, r, 0, sdly, index_fmt="ddd dd mmm yyyy", default="0.00",
                     headers={"batt_charge": "Battery charge, box est.", "batt_charge_solarman": "Battery charge, SolarMan",
                              "batt_discharge": "Battery discharge, box est.", "batt_discharge_solarman": "Battery discharge, SolarMan",
                              "pv": "Solar, box", "pv_solarman": "Solar, SolarMan", "load": "Load, box", "load_solarman": "Load, SolarMan"})
    cat = rng("SolarMan & battery", s0, 0, s1, 0)
    B.chart(wsm, f"K{r + 1}", "line", "Battery charge per day: the box's estimate vs SolarMan's meter", [
        {"name": "SolarMan (metered)", "cat": cat, "val": rng("SolarMan & battery", s0, 2, s1, 2), "color": MUTED, "width": 3.5},
        {"name": "Energy box (estimated)", "cat": cat, "val": rng("SolarMan & battery", s0, 1, s1, 1), "color": BATT, "width": 1.25}],
        size=(720, 320), y_title="kWh", y_fmt="0", x_interval=14, x_num_fmt="d mmm")
    B.chart(wsm, f"K{r + 18}", "line", "Solar per day: box counter vs SolarMan (100 W steps)", [
        {"name": "SolarMan", "cat": cat, "val": rng("SolarMan & battery", s0, 6, s1, 6), "color": MUTED, "width": 3.5},
        {"name": "Energy box", "cat": cat, "val": rng("SolarMan & battery", s0, 5, s1, 5), "color": PV, "width": 1.25}],
        size=(720, 320), y_title="kWh", y_fmt="0", x_interval=14, x_num_fmt="d mmm")

    # ================================ Whole year ================================================
    wy = B.sheet("Whole year", "The inverter's grid sensor vs the ESB meter across 13 months",
                 "The box only has data from May 2026. SolarMan has the same grid sensor since September 2025, so it shows whether the sensor also holds in winter, when import is "
                 "high and export is tiny. Only half-hours with complete SolarMan data are compared (coverage column).", tab=EXPORT, first_col_width=16, width=13)
    r = 3
    wy.set_row(r, 32)
    t = sy.copy(); t.index = [f"Grid {i}: SolarMan half-hours vs ESB" for i in t.index]; t.index.name = "All matched half-hours"
    wy.set_column(0, 0, 40)
    _, r = B.table(wy, r, 0, t, fmts=STAT_FMTS, headers={**STAT_HEADERS, "test_total": "SolarMan total"})
    r += 2
    ym = naive(V["year_monthly"], "Month")[["import", "import_esb", "import_diff_pct", "export", "export_esb", "export_diff_pct", "coverage"]]
    wy.set_row(r, 32)
    y0, y1 = B.table(wy, r, 0, ym, index_fmt="mmm yyyy", default="#,##0.0", fmts={"import_diff_pct": PCT, "export_diff_pct": PCT, "coverage": "0%"},
                     headers={"import": "Import, inverter sensor", "import_esb": "Import, ESB", "import_diff_pct": "Difference", "export": "Export, inverter sensor",
                              "export_esb": "Export, ESB", "export_diff_pct": "Difference", "coverage": "Half-hours compared"})
    B.chart(wy, f"J{r + 1}", "column", "Inverter grid sensor vs ESB meter, by month", [
        {"name": "Import difference", "cat": rng("Whole year", y0, 0, y1, 0), "val": rng("Whole year", y0, 3, y1, 3), "color": IMPORT, "gap": 60},
        {"name": "Export difference", "cat": rng("Whole year", y0, 0, y1, 0), "val": rng("Whole year", y0, 6, y1, 6), "color": EXPORT}],
        size=(720, 320), y_fmt="0.0%", x_num_fmt="mmm yy")
    r = y1 + 3
    B.heading(wy, r, "The ESB meter's own record: every month in the file (kWh) - the full-year picture the box cannot give yet"); r += 1
    em = naive(V["esb_monthly"], "Month"); em["net"] = em["import"] - em["export"]
    em = em[["import", "export", "net", "half_hours"]]
    m0, m1 = B.table(wy, r, 0, em, index_fmt="mmm yyyy", default="#,##0",
                     headers={"import": "Import", "export": "Export", "net": "Net import", "half_hours": "Half-hours in file"})
    B.chart(wy, f"J{r + 1}", "column", "ESB meter: grid import and export by month", [
        {"name": "Import", "cat": rng("Whole year", m0, 0, m1, 0), "val": rng("Whole year", m0, 1, m1, 1), "color": IMPORT, "gap": 40},
        {"name": "Export", "cat": rng("Whole year", m0, 0, m1, 0), "val": rng("Whole year", m0, 2, m1, 2), "color": EXPORT}],
        size=(720, 340), y_title="kWh", y_fmt="#,##0", x_num_fmt="mmm yy", x_interval=2)

    # ================================ Method ====================================================
    wm = B.sheet("Method", "How the comparison was made",
                 "IMPORTANT: identifiable household data (the ESB file names carry the MPRN). Same handling rules as the energy report: consent or UHIC relabel before sharing.",
                 tab=MUTED, first_col_width=30, width=22)
    notes = [
        "Energy box: InfluxDB export of 2026-09-20, processed exactly as in the energy report (hems_data.py + metrics.py): inverter daily counters cut into half-hours, Europe/Dublin time, whole days only.",
        "ESB meter: HDF 'calckWh' file downloaded from the ESB Networks portal on 21 Sep 2026 - 30-minute import and export kWh, 21 Sep 2024 to 20 Sep 2026. 'Read Date and End Time' is the END of the interval. "
        "The HDF kW file is the same data (kW = 2 x kWh, checked to the last digit).",
        "SolarMan: 13 monthly 'Detailed Data' workbooks, Sep 2025 - 21 Sep 2026: 5-minute snapshots (many slots skipped, values often repeated for 10 min). Half-hour energy = mean power x 0.5 h, "
        "gaps up to 15 min interpolated, longer gaps left out. SolarMan's battery sign is flipped to the box convention (+ = discharging).",
        "Clocks were not taken from the file labels. Both files are on Irish clock time: shifted against the box (whose stamps are true UTC) the match collapses (table below); the ESB file skips exactly the two "
        "half-hours of each spring clock change; and SolarMan's winter solar peak sits at GMT solar noon. The SolarMan column 'Time Zone: UTCZ' is simply wrong.",
        "What this does not prove: solar production and house load have no independent meter. The house load the box reports is computed by the inverter from the same grid sensor, so the import/export result "
        "supports it indirectly, but solar rests on the inverter's own DC measurement alone.",
        "Reference accuracy: domestic revenue meters are certified to MID class A or B (about 2 % or 1 %). Differences smaller than that are not meaningful.",
    ]
    r = 3
    for ln in notes:
        B.note(wm, r, ln, height=52, last_col=6); r += 1
    r += 1; B.heading(wm, r, "Correlation with the box when the reference file is shifted in time"); r += 1
    ck = V["clock"].copy(); ck.index.name = "Reference shifted by (min)"
    wm.set_row(r, 32)
    _, r = B.table(wm, r, 0, ck, default="0.0000")
    r += 2; B.heading(wm, r, "Power-weighted mean clock hour of solar output (solar noon here is ~12:40 GMT)"); r += 1
    ct = V["centroid"].copy(); ct.index.name = "Series"
    _, r = B.table(wm, r, 0, ct, default="0.00")

    # ================================ Data ======================================================
    hh = naive(V["halfhour"], "Half-hour starting (local)")
    wh = B.sheet("Data_halfhour", "Every half-hour: energy box and ESB meter", "kWh per half-hour. 'integrated power' = the box's 5-second power signal summed, for comparison with the counters.",
                 tab=MUTED, first_col_width=22, width=13)
    wh.set_row(3, 45)
    _, h1 = B.table(wh, 3, 0, hh, index_fmt="yyyy-mm-dd hh:mm", default="0.0000", fmts={"hems_alive": "0%"},
                    headers={"import_hems": "Import, box", "import_esb": "Import, ESB", "export_hems": "Export, box", "export_esb": "Export, ESB",
                             "import_hems_power": "Import, box (integrated power)", "export_hems_power": "Export, box (integrated power)",
                             "hems_alive": "Box data coverage", "import_diff": "Import difference", "export_diff": "Export difference"})
    wh.freeze_panes(4, 1); wh.autofilter(3, 0, h1, len(hh.columns))

    ws.activate()
    B.wb.set_properties({"title": f"{SITE_LABEL} - measurement validation", "subject": "SmartCORE HEMS",
                         "comments": "Generated by ems/analysis/martinh_validation.py"})
    B.wb.close()


def main() -> None:
    cache = DATA / f".cache_{SITE}.pkl"
    if "--refresh" in sys.argv and cache.exists():
        cache.unlink()
    R = metrics.build(load_site(DATA / SITE, cache))
    V = compute(R)
    s = V["stats_halfhour"]
    print(f"Import vs ESB: {s.iloc[0]['total_diff_pct']:+.2%}   export vs ESB: {s.iloc[3]['total_diff_pct']:+.2%}   "
          f"bill difference: EUR {V['impact'].loc['Net electricity bill, EUR', 'difference']:+.2f}")
    build_workbook(R, V)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
