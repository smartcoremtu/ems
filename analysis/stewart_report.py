"""Build the single-file Excel energy report for the Stewart House HEMS site (Sigenergy PV + battery).

    .venv/Scripts/python stewart_report.py [--refresh]

Reads ../data/Stewart/influx/*.csv.gz, writes ../data/Stewart_Energy_Report.xlsx (git-ignored:
identifiable household data - see the Methods sheet before sharing it). Layout and styling follow
martinh_report.py, whose Book helper it reuses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from xlsxwriter.utility import xl_col_to_name

import stewart_metrics as SM
import tariffs as T
from martinh_report import (AXIS, BATT, EXPORT, GRID, IMPORT, INK, INK2, INPUT, LOAD, MONTH_COLORS, MUTED, NEUTRAL, PV,
                            SURFACE, TILE, Book, eur, rng)
from sigen_data import load_site

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SITE = "Stewart"
SITE_LABEL = "Stewart House"       # switch to the UHIC code before sharing outside the project
OUT = DATA / f"{SITE}_Energy_Report.xlsx"


def build_workbook(R: dict) -> None:
    k, B = R["k"], Book(OUT)
    plans, flat, best = R["plans"], R["flat"], R["best"]
    days = k["days"]
    bad = ", ".join(f"{d:%d %b}" for d in k["bad_days"])
    period = f"{k['start']:%d %b %Y} to {k['end']:%d %b %Y} ({days} full days; {bad} left out for data gaps)"
    year = 365 / days

    names = ["Dashboard", "Why an EMS", "Tariff", "Self-consumption", "Day profile", "Daily", "Heatmaps",
             "Battery", "Solar", "Peaks & baseload", "Methods", "Data_15min", "Data_daily", "Data_monthly",
             "Dictionary", "Tariff_calc"]
    for n in names:
        B.wb.add_worksheet(n)

    # ================================ Dashboard =================================================
    ws = B.sheet("Dashboard", f"{SITE_LABEL} - what the energy box saw",
                 f"{period}. Solar PV (peak output {k['pv_peak_kw']:.1f} kW) + {k['batt_kwh']:.0f} kWh Sigenergy battery, read about every "
                 "5 minutes by the SmartCORE home energy management (EMS) box, with a frient reader on the electricity meter. "
                 "Every chart is a live Excel chart: click it to see the numbers behind it.", tab=LOAD, width=10.7, first_col_width=2)
    for r in (3, 7):
        ws.set_row(r, 20); ws.set_row(r + 1, 36); ws.set_row(r + 2, 32)
    ws.set_row(6, 8)
    B.tile(ws, 3, 1, "Electricity used", k["load_kwh"], f"{k['load_kwh']/days:.1f} kWh a day", '#,##0" kWh"')
    B.tile(ws, 3, 4, "Solar generated", k["pv_kwh"], f"{k['pv_kwh']/days:.0f} kWh a day, best day {k['best_pv_day'][1]:.0f} kWh", '#,##0" kWh"')
    B.tile(ws, 3, 7, "Bought from the grid", k["import_kwh"], f"{k['import_kwh']/days:.2f} kWh a day; nothing at all on {k['days_zero_import']} of {days} days", '#,##0" kWh"')
    B.tile(ws, 3, 10, "Sold to the grid", k["export_kwh"], f"worth {eur(k['export_credit_flat'])} at {flat['export']}c/kWh", '#,##0" kWh"')
    B.tile(ws, 7, 1, "Net bill on a flat plan (live from Tariff sheet)", flat["net"], "negative = the supplier owes the house; standing charge included", '"€"#,##0', formula="=Tariff!T9")
    B.tile(ws, 7, 4, "Saved vs. same house, no solar or battery", k["system_saving_flat"], f"bill would have been {eur(k['bill_flat_no_system'])}", '"€"#,##0')
    B.tile(ws, 7, 7, "Self-sufficiency (use not bought)", k["self_sufficiency"], f"self-consumption {k['self_consumption']:.0%} of the solar", "0%")
    B.tile(ws, 7, 10, "CO₂ avoided by the solar panels", k["co2_avoided_kg"], f"at {T.GRID_CO2_KG_PER_KWH*1000:.0f} g/kWh (SEAI 2026 grid factor)", '#,##0" kg"')

    answers = [
        ("1. How independent of the grid is the house?",
         f"Almost completely, this summer. Solar and battery covered {k['self_sufficiency']:.0%} of everything the house used; it bought {k['import_kwh']:.0f} kWh in {days} days "
         f"({k['import_kwh']/days*1000:.0f} Wh a day) and nothing at all on {k['days_zero_import']} days. {k['import_while_empty_share']:.0%} of what it did buy came in while the battery was "
         f"empty, on {k['batt_days_empty']} nights in late August and September. That will grow fast as the days shorten: September already needed {R['monthly']['import'].iloc[-1]:.0f} kWh."),
        ("2. Does the tariff matter?",
         f"Much less than for most homes, and in a different way. With so little bought, the bill is the standing charge minus the export credit, so the right plan is the one with the "
         f"lowest standing charge and the highest export rate - not the cheapest unit rate. On this summer's data the {len(plans)} plans tested are {eur(k['plan_spread_eur'])} apart "
         f"for the period; the best is {best['supplier']} '{best['name']}'. Winter will change the order: re-run the Tariff sheet with winter data before switching."),
        ("3. What is left to improve?",
         f"(a) On {k['curtail_days']} sunny days the battery was full by late morning and the inverter sat at its 5.5 kW output ceiling ({k['curtail_hours']:.0f} hours in all), so solar above "
         "that was lost. Extra house load cannot win it back - the ceiling is on the inverter's output - but filling the battery later (export in the morning, charge during the midday peak) can: "
         f"a setting or an EMS automation. (b) Grid voltage climbs to {k['v_max']:.0f} V when the house exports hard, close to the 253 V limit. (c) A ~3.3 kW load, most likely the immersion "
         f"on a timer, runs at 13:35 on {k['timer_days']} of {days} days - already well placed in solar hours - and the {k['baseload_w']:.0f} W always-on load is low."),
    ]
    row = 11
    for q, a in answers:
        ws.merge_range(row, 1, row, 12, q, B.f(bold=True, font_size=12, top=1, top_color=GRID))
        ws.merge_range(row + 1, 1, row + 1, 12, a, B.f(text_wrap=True, valign="top", font_size=10.5))
        ws.set_row(row, 24); ws.set_row(row + 1, 74)
        row += 2

    # ================================ Day profile =================================================
    prof = R["prof"]
    wp = B.sheet("Day profile", "An average day, half-hour by half-hour",
                 f"The signature of this house: solar from about 07:00 to 20:00, a ~3.3 kW load switching on at about 13:35 for 1-1.5 hours on {k['timer_days']} of {days} days (most likely the immersion on a timer, "
                 "already in solar hours), the battery filling in the morning and carrying the house from evening to the next morning "
                 f"(median charge at 07:00: {k['soc_07_median']:.0f} %), and almost no grid import at any hour. Typical use is ~{k['median_load_w']:.0f} W. "
                 f"Averages over {days} days, kW (battery in %).", tab=LOAD)
    T0 = 46
    heads = {"load_kw": "Total use kW", "pv_kw": "Solar kW", "import_kw": "Grid import kW", "export_kw": "Grid export kW",
             "batt_charge_kw": "Battery charge kW", "batt_discharge_kw": "Battery discharge kW", "battery_net_kw": "Battery net kW (+ out, - in)",
             "soc_pct": "Battery charge level %", "load_weekday_kw": "Use weekday kW", "load_weekend_kw": "Use weekend kW"}
    heads.update({f"pv_{mo}": f"Solar {pd.Timestamp(mo + '-01'):%b} kW" for mo in R["months"]})
    prof_x = prof.copy(); prof_x.index.name = "Time"
    cols = list(prof_x.columns)
    B.heading(wp, T0 - 1, "Data behind the charts")
    wp.set_row(T0, 45)
    r0, r1 = B.table(wp, T0, 0, prof_x, default="0.00", headers=heads, fmts={"soc_pct": "0"})
    pc = lambda name: cols.index(name) + 1
    cat = rng("Day profile", r0, 0, r1, 0)
    sv = lambda name, color, label=None, **kw: {"name": label or heads[name].replace(" kW", ""), "cat": cat,
                                                 "val": rng("Day profile", r0, pc(name), r1, pc(name)), "color": color, **kw}
    avg_day = [sv("load_kw", LOAD, "Total use"), sv("pv_kw", PV, "Solar"), sv("import_kw", IMPORT, "Grid import"), sv("export_kw", EXPORT, "Grid export")]
    B.chart(wp, "A4", "line", "Average day: use, solar, grid import and export (kW)", avg_day, x_interval=4, y_title="kW")
    B.chart(wp, "J4", "line", "Battery charge level through an average day (%)", [sv("soc_pct", BATT, "Battery %")], x_interval=4, y_title="%", legend=False, y_max=100)
    B.chart(wp, "A22", "line", "Solar output through the day, by month (kW)",
            [sv(f"pv_{mo}", MONTH_COLORS[i], f"{pd.Timestamp(mo + '-01'):%B}") for i, mo in enumerate(R["months"])], x_interval=4, y_title="kW")
    B.chart(wp, "J22", "column", "Battery: discharging (+) and charging (-), average kW",
            [sv("battery_net_kw", BATT, "Battery net", gap=20)], x_interval=4, y_title="kW")
    wp.freeze_panes(T0 + 1, 1)

    # ================================ Data_monthly / Data_daily ====================================
    monthly = R["monthly"].copy()
    monthly.index = [f"{d:%b %Y}" for d in monthly.index]; monthly.index.name = "Month"
    mh = {"import": "Grid import kWh", "export": "Grid export kWh", "load": "Used kWh", "pv": "Solar kWh",
          "batt_charge": "Battery charged kWh", "batt_discharge": "Battery discharged kWh", "self_used_pv": "Solar used at home kWh",
          "days": "Days", "self_consumption": "Self-consumption", "self_sufficiency": "Self-sufficiency", "pv_per_day": "Solar kWh/day",
          "load_per_day": "Use kWh/day"}
    wm = B.sheet("Data_monthly", "Monthly totals", "July and September are part months (data starts 16 Jul). Days with data gaps are left out.", tab=MUTED, width=13)
    wm.set_row(3, 45)
    m0, m1 = B.table(wm, 3, 0, monthly, headers=mh, default="#,##0.0",
                     fmts={"self_consumption": "0%", "self_sufficiency": "0.0%", "pv_per_day": "0.0", "load_per_day": "0.0", "days": "0"})
    mcols = list(monthly.columns)
    mcat = rng("Data_monthly", m0, 0, m1, 0)
    ms = lambda n, color, label, **kw: {"name": label, "cat": mcat, "val": rng("Data_monthly", m0, mcols.index(n) + 1, m1, mcols.index(n) + 1), "color": color, **kw}

    daily = R["daily"].copy()
    daily.index = daily.index.tz_localize(None); daily.index.name = "Date"
    daily = daily.drop(columns=["import_sigen", "load_sensor"])
    dh = {**mh, "coverage": "Data coverage", "soc_min": "Battery min %", "soc_max": "Battery max %", "hours_full": "Hours battery full",
          "hours_empty": "Hours battery empty", "peak_load_kw": "Peak use kW (sensor)", "peak_import_kw": "Peak grid import kW (meter)",
          "v_max": "Max voltage V", "hours_at_ac_ceiling": "Hours inverter at 5.5 kW ceiling"}
    wd = B.sheet("Data_daily", "Daily totals", "One row per day. kWh unless stated. Blank rows = day left out for a data gap.", tab=MUTED, width=13)
    wd.set_row(3, 45)
    d0, d1 = B.table(wd, 3, 0, daily, headers=dh, index_fmt="ddd dd mmm yyyy", default="0.0",
                     fmts={"self_consumption": "0%", "self_sufficiency": "0%", "coverage": "0.0%", "soc_min": "0", "soc_max": "0", "import": "0.00"})
    wd.set_column(0, 0, 17); wd.freeze_panes(4, 1); wd.autofilter(3, 0, d1, len(daily.columns))
    dcols = list(daily.columns)
    dcat = rng("Data_daily", d0, 0, d1, 0)
    ds = lambda n, color, label, **kw: {"name": label, "cat": dcat, "val": rng("Data_daily", d0, dcols.index(n) + 1, d1, dcols.index(n) + 1), "color": color, **kw}

    ws_charts_row = 18
    B.chart(ws, f"B{ws_charts_row}", "line", "An average day (kW): the battery bridges the night", avg_day, size=(470, 300), x_interval=6)
    B.chart(ws, f"H{ws_charts_row}", "column", "Month by month (kWh) - July and September are part months",
            [ms("load", LOAD, "Used"), ms("pv", PV, "Solar"), ms("import", IMPORT, "Bought"), ms("export", EXPORT, "Sold")], size=(470, 300))

    # ================================ Tariff ====================================================
    wt = B.sheet("Tariff", "Which tariff suits a house that hardly buys electricity?",
                 f"Each plan is priced on the house's real half-hourly grid import and export for {period}. The current plan is not known. "
                 f"With {k['import_kwh']:.0f} kWh bought, import costs are a few euro on any plan: the bill is the standing charge minus the export credit. "
                 "Yellow cells are inputs: type the rates from a bill and every figure and chart updates.", tab=IMPORT, width=9.5, first_col_width=40)
    wc = B.wb.get_worksheet_by_name("Tariff_calc")
    tp = R["tprof"]
    wt.write(3, 0, "Days in period", B.f(font_color=INK2)); wt.write(3, 1, days, B.f(bg_color=INPUT, num_format="0"))
    wt.write(4, 0, "Grid import kWh (from Tariff_calc)", B.f(font_color=INK2)); wt.write_formula(4, 1, "=SUM(Tariff_calc!$B$2:$B$49)", B.f(num_format="#,##0.0"), float(tp["import_all"].sum()))
    wt.write(5, 0, "Grid export kWh (from Tariff_calc)", B.f(font_color=INK2)); wt.write_formula(5, 1, "=SUM(Tariff_calc!$D$2:$D$49)", B.f(num_format="#,##0"), float(tp["export_all"].sum()))
    wt.write(3, 3, "PSO levy €/month", B.f(font_color=INK2)); wt.write(3, 5, T.PSO_EUR_PER_MONTH, B.f(bg_color=INPUT, num_format="0.00"))
    wt.write(4, 3, "All rates: cent/kWh inc. 9 % VAT. Hours are clock hours 0-24; a window such as 23 to 8 wraps past midnight. Band priority: EV > peak > night > day.", B.f(font_color=INK2))
    heads_t = ["Plan", "Supplier", "Day c", "Night c", "Night from", "Night to", "Peak c", "Peak from", "Peak to", "EV c", "EV from", "EV to",
               "Free Sat 8-23 (1=yes)", "Extra discount %", "Standing €/yr", "Export c", "Import cost €", "Standing + PSO €",
               "Export credit €", "NET € for period", "Avg import c/kWh", "NET €/yr at this (summer) pattern", "NET € same house, no solar or battery", "Notes"]
    HR = 7
    wt.set_row(HR, 88)
    for j, h in enumerate(heads_t):
        wt.write(HR, j, h, B.f(bold=True, font_color=INK2, bottom=1, bottom_color=AXIS, text_wrap=True, valign="bottom"))
    mine = {**flat, "name": "STEWART HOUSE ACTUAL RATES - edit me (pre-filled with a flat plan)", "supplier": "?", "key": "mine",
            "note": "Plan not known. Type the unit rates, standing charge and export rate from the latest bill."}
    rows_t = [mine] + plans
    inp = lambda nf: B.f(bg_color=INPUT, num_format=nf)
    for i, p in enumerate(rows_t):
        r = HR + 1 + i
        x = r + 1
        wt.write(r, 0, p["name"], B.f(bold=(p["key"] == "mine"), text_wrap=True))
        wt.write(r, 1, p["supplier"], B.f())
        vals = [p["day"], p.get("night"), *(p.get("night_win") or (None, None)), p.get("peak"), *(p.get("peak_win") or (None, None)),
                p.get("ev"), *(p.get("ev_win") or (None, None)), p.get("free_sat", 0), 0, p["standing"], p["export"]]
        nfs = ["0.00", "0.00", "0", "0", "0.00", "0", "0", "0.00", "0", "0", "0", "0%", "#,##0.00", "0.00"]
        for j, (v, nf) in enumerate(zip(vals, nfs), start=2):
            if v is None:
                wt.write_blank(r, j, None, inp(nf))
            else:
                wt.write_number(r, j, v, inp(nf))
        c = xl_col_to_name(5 + i)
        money = B.f(num_format='"€"#,##0')
        wt.write_formula(r, 16, f"=(SUMPRODUCT(Tariff_calc!${c}$2:${c}$49,Tariff_calc!$B$2:$B$49)-M{x}*SUMPRODUCT(Tariff_calc!${c}$2:${c}$49,Tariff_calc!$C$2:$C$49))/100", money, p["import_cost"])
        wt.write_formula(r, 17, f"=O{x}/365*$B$4+$F$4*$B$4/30.4375", money, p["standing_cost"])
        wt.write_formula(r, 18, f"=P{x}*$B$6/100", money, p["export_credit"])
        wt.write_formula(r, 19, f"=Q{x}+R{x}-S{x}", B.f(num_format='"€"#,##0', bold=True), p["net"])
        wt.write_formula(r, 20, f"=IF($B$5>0,Q{x}/$B$5*100,0)", B.f(num_format="0.0"), p["import_cost"] / k["import_kwh"] * 100)
        wt.write_formula(r, 21, f"=T{x}*365/$B$4", money, p["net"] * year)
        wt.write_number(r, 22, p["net_without_system"], money)      # static: includes the free-Saturday rule
        wt.write(r, 23, p["note"], B.f(font_color=INK2))
    wt.set_column(1, 1, 15); wt.set_column(16, 22, 11.5); wt.set_column(23, 23, 90)
    t0, t1 = HR + 1, HR + len(rows_t)
    pts = [{"fill": {"color": LOAD if p["key"] == "mine" else NEUTRAL}} for p in rows_t]
    B.chart(wt, f"A{t1 + 4}", "bar", f"Net cost of the {days} days on each plan (€; below zero = credit)",
            [{"name": "Net €", "cat": rng("Tariff", t0, 0, t1, 0), "val": rng("Tariff", t0, 19, t1, 19), "color": NEUTRAL,
              "points": pts, "gap": 40, "labels": '"€"#,##0'}], size=(1100, 520), reverse=True, legend=False, y_fmt='"€"#,##0', cat_label_low=True)
    nrow = t1 + 31
    for text in [
        "How to read it: NET = import cost + standing charge + PSO levy - export credit. A negative NET means the export credit was larger than the charges. "
        "'No solar or battery' prices every kWh the house used at the time it was used, with nothing exported.",
        f"Caveats: {days} summer days, when this house is nearly self-sufficient. From October it will buy more and export less, so unit rates start to matter again; "
        "'€/yr' is a simple scale-up of summer and overstates the annual credit. Urban standing charges; rural is roughly €65-90 a year higher on every plan. "
        f"Rates researched {T.RESEARCH_DATE} - see Methods; always confirm on the supplier's site.",
        f"Tax: export income up to €{T.EXPORT_TAX_FREE_EUR} a year is tax-free (micro-generation disregard, to end 2028). At the summer rate this house would export about "
        f"{k['export_kwh_year_summer_rate']:,.0f} kWh a year (~{eur(k['export_income_year'])}); the real annual figure will be lower once winter is in the data - worth watching.",
    ]:
        B.note(wt, nrow, text, height=48, last_col=22); nrow += 1
    wt.freeze_panes(HR + 1, 1)

    wc.set_column(0, 20, 13, B.f())
    for j, h in enumerate(["Hour of day", "Import kWh (all days)", "Import kWh Sat 08-23", "Export kWh", "Load kWh"] + [f"Rate c/kWh: row {HR + 2 + i}" for i in range(len(rows_t))]):
        wc.write(0, j, h, B.f(bold=True, text_wrap=True, font_color=INK2))
    wc.set_row(0, 45)
    win = lambda s, e: f"IF(Tariff!${s}<Tariff!${e},AND($A{{a}}>=Tariff!${s},$A{{a}}<Tariff!${e}),OR($A{{a}}>=Tariff!${s},$A{{a}}<Tariff!${e}))"
    for h in range(48):
        a = h + 2
        wc.write_number(h + 1, 0, h / 2, B.f(num_format="0.0"))
        for j, cname in enumerate(["import_all", "import_sat_free", "export_all", "load_all"], start=1):
            wc.write_number(h + 1, j, float(tp[cname].iloc[h]), B.f(num_format="0.000"))
        for i, p in enumerate(rows_t):
            x = HR + 2 + i
            fml = (f"=(1-Tariff!$N${x})*IF(AND(Tariff!$J${x}<>\"\",{win(f'K${x}', f'L${x}')}),Tariff!$J${x},"
                   f"IF(AND(Tariff!$G${x}<>\"\",{win(f'H${x}', f'I${x}')}),Tariff!$G${x},"
                   f"IF(AND(Tariff!$D${x}<>\"\",{win(f'E${x}', f'F${x}')}),Tariff!$D${x},Tariff!$C${x})))").replace("{a}", str(a))
            wc.write_formula(h + 1, 5 + i, fml, B.f(num_format="0.00"), T.rate_vector(p)[h])
    wc.set_tab_color(MUTED)
    B.chart(ws, f"B{ws_charts_row + 16}", "bar", "Same usage, different plan: net cost for the period (€)",
            [{"name": "Net €", "cat": rng("Tariff", t0 + 1, 0, t1, 0), "val": rng("Tariff", t0 + 1, 19, t1, 19), "color": NEUTRAL,
              "gap": 35}], size=(470, 340), reverse=True, legend=False, y_fmt='"€"#,##0', cat_label_low=True)

    # ================================ Self-consumption ============================================
    wsc = B.sheet("Self-consumption", "Where the solar goes",
                  f"Self-sufficiency {k['self_sufficiency']:.0%} (share of use not bought) and self-consumption {k['self_consumption']:.0%} (share of solar used at home or stored). "
                  f"The array makes about twice what the house uses in summer, so half of it has to be exported however well it is managed. As the days shorten, "
                  f"self-consumption rises (July {R['monthly']['self_consumption'].iloc[0]:.0%}, September {R['monthly']['self_consumption'].iloc[-1]:.0%}) and self-sufficiency starts to slip.", tab=EXPORT)
    B.chart(wsc, "A4", "column", "Where the solar went each month (kWh)",
            [ms("self_used_pv", PV, "Used at home or stored"), ms("export", EXPORT, "Sold to grid")], subtype="stacked")
    B.chart(wsc, "F4", "line", "Self-consumption and self-sufficiency by month",
            [ms("self_consumption", PV, "Self-consumption (solar used at home)"), ms("self_sufficiency", LOAD, "Self-sufficiency (use not bought)")], y_fmt="0%", y_max=1)
    S0 = 40
    ih = R["imp_by_hour"].to_frame("kwh"); ih.index.name = "Hour of day"
    B.heading(wsc, S0 - 1, "Data behind the charts")
    i0, i1 = B.table(wsc, S0, 0, ih, default="0.00", headers={"kwh": f"Grid import kWh, all {days} days"})
    B.chart(wsc, "A22", "column", f"When the {k['import_kwh']:.0f} kWh were bought: by hour of day (kWh)",
            [{"name": "kWh", "cat": rng("Self-consumption", i0, 0, i1, 0), "val": rng("Self-consumption", i0, 1, i1, 1), "color": IMPORT, "gap": 35}], legend=False)
    B.chart(wsc, "F22", "line", "Bought from the grid each day (kWh)", [ds("import", IMPORT, "Bought", width=1.5)], x_num_fmt="d mmm", x_interval=14, legend=False)
    wsc.set_column(0, 0, 14)

    # ================================ Daily =====================================================
    wdl = B.sheet("Daily", "Day by day",
                  f"Best solar day {k['best_pv_day'][0]:%d %b}: {k['best_pv_day'][1]:.1f} kWh. Dullest {k['worst_pv_day'][0]:%d %b}: {k['worst_pv_day'][1]:.1f} kWh. "
                  f"Biggest use {k['max_load_day'][0]:%d %b}: {k['max_load_day'][1]:.0f} kWh. Solar fell short of the day's use on only {k['days_pv_below_load']} days. "
                  "The table behind these charts is the Data_daily sheet.", tab=PV)
    big = (1466, 330)
    B.chart(wdl, "A4", "column", "Solar generated and electricity used each day (kWh)", [ds("pv", PV, "Solar", gap=25), ds("load", LOAD, "Used")],
            size=big, x_num_fmt="d mmm", x_interval=7)
    B.chart(wdl, "A21", "line", "Sold to and bought from the grid each day (kWh)", [ds("export", EXPORT, "Sold", width=1.75), ds("import", IMPORT, "Bought", width=1.75)],
            size=big, x_num_fmt="d mmm", x_interval=7)
    B.chart(wdl, "A38", "line", "Battery each day: lowest and highest charge level (%)", [ds("soc_min", IMPORT, "Lowest", width=1.75), ds("soc_max", BATT, "Highest", width=1.75)],
            size=big, x_num_fmt="d mmm", x_interval=7, y_max=100)

    # ================================ Heatmaps ==================================================
    wh = B.sheet("Heatmaps", "Every half-hour of every day",
                 "Each row is a day, each column a half-hour from 00:00 (left) to 23:30 (right); darker = more. Look at the battery map: dark (full) from late morning, "
                 "fading through the night, and the pale cells in late August and September where it ran empty before sunrise - the only times the house bought power.",
                 tab=BATT, width=2.4, first_col_width=13, ncols=49)
    ramp = {"soc": ("Battery charge level (% average per half-hour)", "#ebe8f7", "#2d2170"),
            "load": ("Electricity used (kWh per half-hour)", "#e6f0fd", "#0d366b"),
            "pv": ("Solar generated (kWh per half-hour)", "#fff3d1", "#8a5a00"),
            "export": ("Sold to the grid (kWh per half-hour)", "#ddf5ea", "#0b6b48"),
            "import": ("Bought from the grid (kWh per half-hour)", "#ffe9df", "#a83c10")}
    hr = 3
    for key, (title, lo, hi) in ramp.items():
        hm = R["heatmaps"][key]
        B.heading(wh, hr, title)
        for c in range(48):
            if c % 2 == 0:
                wh.write(hr + 1, c + 1, f"{c // 2:02d}", B.f(font_size=7, font_color=MUTED, align="left"))
        for i, (d, vals) in enumerate(hm.iterrows()):
            wh.write_datetime(hr + 2 + i, 0, d.to_pydatetime(), B.f(num_format="ddd dd mmm", font_size=8, font_color=INK2))
            wh.set_row(hr + 2 + i, 9)
            for c, v in enumerate(vals.values):
                if np.isfinite(v):
                    wh.write_number(hr + 2 + i, c + 1, round(float(v), 3), B.f(font_size=1, font_color=lo, num_format=";;;"))
        wh.conditional_format(hr + 2, 1, hr + 1 + len(hm), 48, {"type": "2_color_scale", "min_color": SURFACE, "max_color": hi,
                                                                  "min_type": "num", "min_value": 0, "max_type": "percentile", "max_value": 99.5})
        hr += len(hm) + 5

    # ================================ Battery ===================================================
    wb_ = B.sheet("Battery", "The battery",
                  f"{k['batt_kwh']:.2f} kWh at 100 % (read from the system). {k['batt_charge_kwh']:.0f} kWh in, {k['batt_discharge_kwh']:.0f} kWh out = {k['batt_cycles']:.0f} full cycles in {days} days "
                  f"({k['batt_cycles_per_day']:.2f} a day), round-trip {k['batt_rte']:.0%}. Charging peaked at {k['batt_max_charge_kw']:.1f} kW, discharging at {k['batt_max_discharge_kw']:.1f} kW. "
                  f"It reached 100 % on {k['batt_days_full']} days and sat full for {k['batt_hours_full_per_day']:.1f} hours a day on average; it ran empty on {k['batt_days_empty']} days. "
                  "It is charged from solar only - there is no night grid charging.", tab=BATT)
    E0 = 40
    B.heading(wb_, E0 - 1, "Data behind the charts")
    sh = R["soc_hist"].copy(); sh.index.name = "Charge level"
    h0, h1 = B.table(wb_, E0, 0, sh, default="0.0%", headers={"share_of_time": "Share of time"})
    eb = R["empty_by_hour"].to_frame("share"); eb.index.name = "Hour of day"
    b0, b1 = B.table(wb_, E0, 3, eb, default="0.0%", headers={"share": "Share of time empty"})
    B.chart(wb_, "A4", "column", "How full the battery is: share of time in each 10 % band",
            [{"name": "Share of time", "cat": rng("Battery", h0, 0, h1, 0), "val": rng("Battery", h0, 1, h1, 1), "color": BATT, "gap": 25}], y_fmt="0%", legend=False)
    B.chart(wb_, "J4", "column", "When the battery is empty: share of time, by hour of day",
            [{"name": "Empty", "cat": rng("Battery", b0, 3, b1, 3), "val": rng("Battery", b0, 4, b1, 4), "color": IMPORT, "gap": 25}], y_fmt="0%", legend=False)
    B.chart(wb_, "A22", "line", "Battery energy each day (kWh): charged and discharged",
            [ds("batt_charge", PV, "Charged", width=1.5), ds("batt_discharge", BATT, "Discharged", width=1.5)], x_num_fmt="d mmm", x_interval=14)
    B.chart(wb_, "J22", "column", "Hours per day the battery sat full (100 %) vs empty",
            [ds("hours_full", BATT, "Full", gap=30), ds("hours_empty", IMPORT, "Empty")], x_num_fmt="d mmm", x_interval=14)
    B.note(wb_, 38, f"Is it the right size? In July it was full by late morning and never ran out, so a bigger battery would have changed nothing. On the {k['batt_days_empty']} days it did run out, "
           f"the house bought {k['import_while_empty_share']:.0%} of its (small) import while the battery was empty. A bigger battery would save a few euro in autumn, not a winter's worth: "
           "in winter the problem is too little solar to fill the one it has. Round-trip efficiency is from the system's own charge/discharge counters and includes the inverter.", height=48)

    # ================================ Solar =====================================================
    wso = B.sheet("Solar", "How well are the panels doing, and what does export do to the grid?",
                  f"{k['pv_kwh']:,.0f} kWh in {days} days, best day {k['best_pv_day'][1]:.0f} kWh; lifetime on the system {R['lifetime']['pv']:,.0f} kWh. Peak solar output seen {k['pv_peak_kw']:.1f} kW "
                  f"(array size not known). The inverter's AC output tops out at {k['ac_peak_kw']:.1f} kW: it sat at that ceiling for {k['ac_ceiling_hours']:.0f} hours, {k['curtail_hours']:.0f} of them with the "
                  f"battery already full - on those {k['curtail_days']} days some solar was probably curtailed (the sensors cannot say how much). Grid voltage rises with export, from "
                  f"{R['v_by_export']['mean_voltage'].iloc[0]:.0f} V when importing to {R['v_by_export']['mean_voltage'].iloc[-1]:.0f} V when exporting over 4 kW; max {k['v_max']:.0f} V, limit 253 V.", tab=PV)
    O0 = 46
    B.heading(wso, O0 - 1, "Data behind the charts")
    vh = R["vhist"].set_index("volts_from"); vh.index.name = "Volts (2 V bins from)"
    vh0, vh1 = B.table(wso, O0, 0, vh, default="0.0%", headers={"share_of_time": "Share of time"})
    ve = R["v_by_export"].copy(); ve.index = ve.index.astype(str); ve.index.name = "Grid flow"
    ve0, ve1 = B.table(wso, O0, 3, ve, default="0.0", headers={"mean_voltage": "Mean voltage V"})
    facts = pd.DataFrame({"value": [k["pv_peak_kw"], k["ac_peak_kw"], k["export_peak_kw"], k["ac_ceiling_hours"], k["curtail_hours"], k["curtail_days"],
                                    k["v_mean"], k["v_min"], k["v_max"], k["v_over_248_min"], k["v_over_253_min"], k["f_mean"], k["f_min"], k["f_max"]]},
                         index=["Peak solar power seen, kW", "Peak inverter AC output, kW", "Peak export, kW", "Hours with inverter at its AC ceiling",
                                "... of which battery full (likely curtailment), hours", "Days with >15 min of likely curtailment", "Mean grid voltage, V",
                                "Min grid voltage, V", "Max grid voltage, V", "Minutes above 248 V", "Minutes above 253 V", "Mean frequency, Hz", "Min frequency, Hz", "Max frequency, Hz"])
    facts.index.name = "Solar and power-quality facts"
    B.table(wso, O0, 6, facts, default="#,##0.00", headers={"value": "Value"})
    wso.set_column(6, 6, 50)
    B.chart(wso, "A4", "column", "Average solar per day, by month (kWh/day)", [ms("pv_per_day", PV, "Solar kWh per day", gap=45, labels="0.0")], legend=False)
    B.chart(wso, "J4", "column", "Hours per day the inverter sat at its 5.5 kW ceiling", [ds("hours_at_ac_ceiling", PV, "Hours", gap=30)], x_num_fmt="d mmm", x_interval=14, legend=False)
    B.chart(wso, "A22", "column", "Grid voltage at the house: share of time in each 2 V band (limit 253 V)",
            [{"name": "Share of time", "cat": rng("Solar", vh0, 0, vh1, 0), "val": rng("Solar", vh0, 1, vh1, 1), "color": LOAD, "gap": 20}], y_fmt="0%", legend=False)
    B.chart(wso, "J22", "column", "Export pushes the local voltage up: mean voltage by grid flow (V)",
            [{"name": "Mean voltage", "cat": rng("Solar", ve0, 3, ve1, 3), "val": rng("Solar", ve0, 4, ve1, 4), "color": EXPORT, "gap": 30, "labels": "0.0"}], legend=False, y_fmt="0")

    # ================================ Peaks & baseload ==========================================
    wpk = B.sheet("Peaks & baseload", "The always-on load and the big peaks",
                  f"Always-on (10th percentile) load is about {k['baseload_w']:.0f} W = {k['baseload_kwh_year']:,.0f} kWh a year - low (a typical Irish home uses {T.CRU_TYPICAL_KWH_PER_YEAR:,} kWh a year in total). "
                  f"Highest use seen {k['peak_load_kw']:.1f} kW; highest grid import {k['peak_import_kw']:.1f} kW, far below the 12 kVA connection. The battery (4.8 kW) covers most "
                  "short peaks, so the grid rarely sees them.", tab=LOAD)
    du = R["duration"].set_index("pct_of_time"); du.index.name = "% of time use is above"
    P0 = 26
    B.heading(wpk, P0 - 1, "Data behind the charts")
    u0, u1 = B.table(wpk, P0, 0, du, default="0.00", index_fmt="0.0", headers={"load_kw": "Use kW (15-min average)"})
    B.chart(wpk, "A4", "line", "Load duration curve: how much of the time is use above each level? (kW, 15-min averages)",
            [{"name": "Use", "cat": rng("Peaks & baseload", u0, 0, u1, 0), "val": rng("Peaks & baseload", u0, 1, u1, 1), "color": LOAD}],
            x_interval=10, y_title="kW", x_num_fmt="0", legend=False)
    B.chart(wpk, "J4", "line", "Highest grid import each day (kW, from the meter reader)", [ds("peak_import_kw", IMPORT, "Peak import", width=1.5)],
            x_num_fmt="d mmm", x_interval=14, legend=False)
    pk = R["peaks"].copy(); pk["when"] = pk["when"].dt.tz_localize(None)
    pk.index = range(1, len(pk) + 1); pk.index.name = "Rank"
    B.heading(wpk, P0 - 1, "Ten highest grid-import moments (one per day)", col=4)
    B.table(wpk, P0, 4, pk, default="#,##0", fmts={"when": "ddd dd mmm yyyy hh:mm"},
            headers={"when": "When", "import_w": "Grid import W (meter)", "soc_pct": "Battery %", "pv_w": "Solar W", "load_w_sensor": "Use W (sensor)"})
    wpk.set_column(5, 5, 22)
    B.note(wpk, 23, "Reading the table: the biggest imports happen with the battery at 0 %, after dark or on dull mornings - the battery simply had nothing left. "
           "The afternoon rows with a full battery are single meter readings when a 3-5 kW appliance switched on, before the battery ramped up; averaged over a few minutes "
           "they are only 250-450 W.", height=40)

    # ================================ Why an EMS ================================================
    we = B.sheet("Why an EMS", "Six things the electricity bill could not tell this household",
                 "A bill gives one number every two months; the Sigenergy app gives daily totals. The EMS box keeps every reading, in the home, "
                 f"and puts the solar, the battery, the meter and the grid voltage side by side. Everything below comes from {days} days of that data.", tab=EXPORT, width=10.7, first_col_width=2)
    cards = [
        (f"{k['self_sufficiency']:.0%}", "of the house's electricity came from its own roof and battery",
         f"{k['import_kwh']:.0f} kWh bought in {days} days - nothing at all on {k['days_zero_import']} of them. The box shows exactly when the rest was bought: "
         f"{k['import_while_empty_share']:.0%} of it while the battery was empty, before sunrise."),
        (eur(k["system_saving_flat"]), f"saved in {days} summer days",
         f"On a flat plan the same house with no solar or battery would have paid {eur(k['bill_flat_no_system'])}; with them it is owed {eur(-flat['net'])}. "
         "The bill shows what you paid; only measurement shows what you did not pay."),
        (f"{k['curtail_hours']:.0f} h", "the inverter was at full output with nowhere to put more",
         f"On {k['curtail_days']} sunny days the battery was full by late morning and the inverter was at its 5.5 kW output ceiling, so extra solar was likely lost. More load in the house "
         "cannot fix that (the limit is the inverter's output); charging the battery later in the day can - exactly the kind of rule an EMS automates."),
        (f"{k['v_max']:.0f} V", "the highest grid voltage at the house",
         f"The voltage climbs by about {R['v_by_export']['mean_voltage'].iloc[-1] - R['v_by_export']['mean_voltage'].iloc[0]:.0f} V when the house exports hard. The limit is 253 V; above it the inverter "
         "must cut output. As more neighbours add solar this will bite - using the power locally, in the house or in an energy community, is the fix."),
        (f"{k['batt_cycles']:.0f}", "full battery cycles in the period",
         f"{k['batt_cycles_per_day']:.2f} a day, {k['batt_rte']:.0%} round trip, full for {k['batt_hours_full_per_day']:.0f} hours a day in summer. The box tracks the battery's real use, "
         "which is what its warranty and its payback depend on."),
        (f"{-R['recon']['difference_pct'].iloc[1]:.0%}", "how far the Sigenergy load sensor under-counts",
         "The Sigenergy power readings all but ignore small flows - a 0.2 kW draw can show as zero - so they quietly under-count the house at night. The box cross-checks against the system's own energy "
         "counters and an independent reader on the electricity meter - see the validation workbook."),
    ]
    r = 3
    for big_, small, body in cards:
        we.merge_range(r, 1, r + 1, 3, big_, B.f(font_size=24, bold=True, bg_color=TILE, align="center", font_color=INK))
        we.merge_range(r + 2, 1, r + 2, 3, small, B.f(font_size=9, bg_color=TILE, align="center", valign="top", text_wrap=True, font_color=INK2))
        we.merge_range(r, 4, r + 2, 13, body, B.f(font_size=11, text_wrap=True, valign="vcenter", indent=1))
        we.set_row(r, 24); we.set_row(r + 1, 24); we.set_row(r + 2, 30); we.set_row(r + 3, 8)
        r += 4
    B.heading(we, r, "What you get with the box", col=1); r += 1
    for line in [
        "• A small box (Raspberry Pi) that reads what your equipment already measures - nothing is rewired and nothing in the house is controlled unless you ask for it.",
        "• Your own dashboard and the full history, stored on the box in your home.",
        "• A report like this one: is my tariff right, is my solar paying, what is wasting money - answered with your numbers, not averages.",
        "• A path to automation: use surplus solar in the house instead of losing it, and share it with neighbours in an energy community.",
        "• Part of SmartCORE, an EU Interreg North-West Europe project with MTU and South Kerry Development Partnership. Your data is only used with your consent and is de-identified before any research use.",
    ]:
        we.merge_range(r, 1, r, 13, line, B.f(text_wrap=True, font_size=10.5, valign="top")); we.set_row(r, 34); r += 1

    # ================================ Methods ===================================================
    wq = B.sheet("Methods", "Data quality, methods, assumptions and sources",
                 "IMPORTANT: this workbook contains identifiable household energy data. Do not circulate it outside the project without the householder's consent; "
                 "for wider sharing replace the site label with the UHIC code (SITE_LABEL in stewart_report.py) as set out in the ethics application.", tab=MUTED, first_col_width=48, width=16)
    lines = [
        f"Source: InfluxDB export of the Stewart House HEMS box, 2026-09-24: 35 Home Assistant entities from the Sigenergy integration (polled about every 5 minutes, written on change) and a frient "
        f"EMIZB-141 reader on the electricity meter's LED. Analysis period: {period}, Europe/Dublin time.",
        f"Coverage: the box wrote data in {k['coverage']:.1%} of minutes. Longest gap: 5 Aug 04:27-15:52 (HA/box outage). Days with less than 95 % coverage ({bad}) are left out of all totals.",
        "Energy (kWh): solar and battery from the Sigenergy daily counters (0.01 kWh); grid import from the frient meter reader's counter (0.001 kWh, independent of Sigenergy); grid export by "
        "integrating Sigenergy grid power; house use from the energy balance (solar + import - export + battery out - battery in). Checked day by day against the Sigenergy app: solar and battery "
        "agree to within 0.3 %, use 0.6 %, export 1.2 %; import is 1.2 kWh (7 %) lower on the meter reader than in the app - see Stewart_Validation.xlsx.",
        "Why not the power sensors: the Sigenergy station values (load, battery power) read small flows far too low - a 0.17 kW battery discharge shows as ~0, 0.26 kW as 0.14 kW (above 0.5 kW they "
        "agree with the inverter's own sensor within ~5 %). At night the house draws ~0.3 kW, so integrated they under-read house use by ~8 % and battery discharge by ~16 %. They are used only for shapes, peaks and the 15-min data sheet. The PV day counter has 1- and 2-reading glitches (58 dips to 1/100 of the value, one "
        "repeat of the previous day's total on 3 Sep), removed with a 5-reading rolling median.",
        "Sign conventions: grid power + = export; battery power + = charging. The EV-charger and heat-pump entities are always 0 (nothing connected to the Sigenergy system) and phase B/C "
        "hold a 655.35 V placeholder (single-phase site): not used. 'Likely curtailment' = inverter AC output at 5.4-5.5 kW with the battery at 100 %.",
        f"Tariff: the household's plan is not known; every plan is priced on the measured half-hourly import and export. Rates researched {T.RESEARCH_DATE}, inc. 9 % VAT, urban standing charges. "
        "Summer only - not an annual forecast.",
    ]
    r = 3
    for ln in lines:
        B.note(wq, r, ln, height=58, last_col=8); r += 1
    rc = R["recon"].copy(); rc.index.name = "Cross-checks, whole period (kWh)"
    r += 1
    wq.set_row(r, 45)
    _, r = B.table(wq, r, 0, rc, default="#,##0.0", fmts={"difference_pct": "0.0%"},
                   headers={"used_in_report": "Used in this report", "sigenergy_power_sensor": "Sigenergy power sensor, integrated", "difference_pct": "Difference"})
    gp = R["gap_table"].copy()
    if len(gp):
        gp["from"] = gp["from"].dt.tz_localize(None); gp["to"] = gp["to"].dt.tz_localize(None)
        gp = gp.sort_values("minutes", ascending=False).head(10).set_index("from"); gp.index.name = "Data gaps (longest 10), from"
        r += 2
        _, r = B.table(wq, r, 0, gp, index_fmt="ddd dd mmm yyyy hh:mm", default="0", fmts={"to": "hh:mm"}, headers={"to": "To", "minutes": "Minutes"})
    r += 2
    B.heading(wq, r, "Sources (accessed " + T.RESEARCH_DATE + ")"); r += 1
    for name_, url in T.SOURCES.values():
        wq.write(r, 0, name_, B.f(text_wrap=True)); wq.write_url(r, 1, url, B.f(font_color=LOAD, underline=1), url); r += 1

    # ================================ Data_15min / Dictionary ===================================
    d15 = R["data15"].copy(); d15.index = d15.index.tz_localize(None); d15.index.name = "15 min starting (local)"
    h15 = {"import": "Grid import kWh (meter)", "export": "Grid export kWh", "load": "Used kWh (balance)", "pv": "Solar kWh", "batt_charge": "Battery in kWh",
           "batt_discharge": "Battery out kWh", "import_sigen": "Grid import kWh (Sigenergy)", "load_sensor": "Used kWh (load sensor)", "self_used_pv": "Solar used at home kWh",
           "pv_power": "Solar W", "load_power": "Load W (sensor)", "grid_power": "Grid W (+export)", "battery_power": "Battery W (+charge)", "ac_power": "Inverter AC W",
           "soc": "Battery %", "stored_kwh": "Battery stored kWh", "voltage": "Voltage V", "current": "Current A", "frequency": "Frequency Hz", "meter_demand_w": "Meter import W"}
    w15 = B.sheet("Data_15min", "All signals at 15-minute resolution",
                  "Energy per 15 min (see Methods for the source of each column) and the mean of every live signal. Blank = data gap.", tab=MUTED, width=12, first_col_width=20, ncols=len(d15.columns))
    w15.set_row(3, 45)
    _, e15r = B.table(w15, 3, 0, d15, headers=h15, index_fmt="yyyy-mm-dd hh:mm", default="0.000",
                      fmts={c: "0" for c in d15.columns if c.endswith(("_power", "_w"))} | {"soc": "0.0", "voltage": "0.0", "frequency": "0.00", "current": "0.0", "stored_kwh": "0.00"})
    w15.freeze_panes(4, 1); w15.autofilter(3, 0, e15r, len(d15.columns))

    inv = R["inventory"].copy()
    used = {"pv_power", "load_power", "grid_power", "inverter_battery_power", "inverter_active_power", "battery_state_of_charge", "battery_stored_energy",
            "inverter_phase_a_voltage", "inverter_phase_a_current", "inverter_grid_frequency", "daily_pv_generation",
            "inverter_battery_charging_today", "inverter_battery_discharging_today", "lifetime_pv_generation", "inverter_battery_discharging_total",
            "instantaneous_demand", "summation_delivered"}
    station, inverter = R["prefixes"]
    short = (inv["entity"].str.replace("sensor." + inverter, "inverter_", regex=False)
             .str.replace("sensor." + station, "", regex=False).str.replace("sensor.frient_a_s_emizb_141_", "", regex=False))
    inv["used"] = np.where(short.isin(used), "yes", "no")
    inv.loc[short.str.contains("phase_b|phase_c|ev_charger|heat_pump"), "used"] = "no - placeholder / nothing connected"
    for c in ("first", "last"):
        inv[c] = pd.to_datetime(inv[c]).dt.tz_convert("Europe/Dublin").dt.tz_localize(None)
    inv = inv.set_index("entity")[["points", "first", "last", "min", "max", "used"]]; inv.index.name = "Source entity (one .csv.gz each)"
    wdi = B.sheet("Dictionary", f"What was in the {len(inv)} source files", "Every Home Assistant entity in the export, how many points it had and whether it is used in this workbook.",
                  tab=MUTED, first_col_width=62, width=18)
    B.table(wdi, 3, 0, inv, default="#,##0.00", fmts={"points": "#,##0", "first": "dd mmm yyyy hh:mm", "last": "dd mmm yyyy hh:mm"},
            headers={"points": "Points", "first": "First", "last": "Last", "min": "Min", "max": "Max", "used": "Used?"})

    B.wb.get_worksheet_by_name("Dashboard").activate()
    B.wb.set_properties({"title": f"{SITE_LABEL} - energy report", "subject": "SmartCORE HEMS", "comments": "Generated by ems/analysis/stewart_report.py"})
    B.wb.close()


def main() -> None:
    cache = DATA / f".cache_{SITE}.pkl"
    if "--refresh" in sys.argv and cache.exists():
        cache.unlink()
    R = SM.build(load_site(DATA / SITE / "influx", cache))
    build_workbook(R)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
