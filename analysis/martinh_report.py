"""Build the single-file Excel energy report for the MartinH HEMS site.

    .venv/Scripts/python martinh_report.py [--refresh]

Reads ../data/MartinH/*.csv.gz, writes ../data/MartinH_Energy_Report.xlsx (git-ignored: this is
identifiable household data - see the Methods sheet before sharing it).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

import metrics
import tariffs as T
from hems_data import load_site

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SITE = "MartinH"
SITE_LABEL = "Martin's house"      # switch to the UHIC code before sharing outside the project
OUT = DATA / f"{SITE}_Energy_Report.xlsx"

# Palette: dataviz reference palette, validated with validate_palette.js (adjacent CVD dE >= 9.1).
LOAD, IMPORT, EXPORT, PV, BATT, EV, PV2 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#4a3aa7", "#e87ba4", "#008300"
INK, INK2, MUTED, GRID, AXIS, SURFACE, TILE, INPUT = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb", "#f3f2ee", "#fff4c2"
NEUTRAL = "#c3c2b7"
FONT = "Segoe UI"
MONTH_COLORS = [LOAD, IMPORT, EXPORT, PV, EV]


class Book:
    def __init__(self, path: Path):
        self.wb = xlsxwriter.Workbook(str(path), {"nan_inf_to_errors": True, "remove_timezone": True})
        self._cache = {}

    def f(self, **kw):
        key = tuple(sorted(kw.items()))
        if key not in self._cache:
            base = {"font_name": FONT, "font_size": 10, "font_color": INK, "valign": "vcenter"}
            self._cache[key] = self.wb.add_format({**base, **kw})
        return self._cache[key]

    def sheet(self, name, title, subtitle, tab=None, width=11, first_col_width=13, ncols=30):
        ws = self.wb.get_worksheet_by_name(name)
        ws.hide_gridlines(2)
        ws.set_column(0, ncols, width, self.f())
        ws.set_column(0, 0, first_col_width, self.f())
        if tab:
            ws.set_tab_color(tab)
        ws.set_row(0, 30)
        ws.write(0, 0, title, self.f(font_size=18, bold=True))
        ws.merge_range(1, 0, 1, 17 if width > 5 else ncols, subtitle, self.f(font_size=11, font_color=INK2, text_wrap=True, valign="top"))
        ws.set_row(1, 50)
        return ws

    def note(self, ws, row, text, height=34, last_col=17, **kw):
        ws.merge_range(row, 0, row, last_col, text, self.f(text_wrap=True, valign="top", font_color=INK2, **kw))
        ws.set_row(row, height)

    def heading(self, ws, row, text, col=0):
        ws.write(row, col, text, self.f(bold=True, font_size=12))

    def table(self, ws, row, col, df, fmts=None, index_fmt=None, headers=None, default="#,##0.0"):
        """Write df (index first) with a header row. Returns (first_data_row, last_data_row)."""
        fmts = fmts or {}
        head = self.f(bold=True, font_color=INK2, bottom=1, bottom_color=AXIS, text_wrap=True, valign="bottom")
        names = [df.index.name or ""] + list(df.columns)
        for j, n in enumerate(names):
            ws.write(row, col + j, (headers or {}).get(n, n), head)
        ifmt = self.f(num_format=index_fmt) if index_fmt else self.f()
        for i, (idx, vals) in enumerate(zip(df.index, df.itertuples(index=False)), start=row + 1):
            ws.write(i, col, idx.to_pydatetime() if isinstance(idx, pd.Timestamp) else idx, ifmt)
            for j, v in enumerate(vals, start=1):
                fmt = self.f(num_format=fmts.get(df.columns[j - 1], default))
                if isinstance(v, pd.Timestamp):
                    ws.write_datetime(i, col + j, v.to_pydatetime().replace(tzinfo=None),
                                      self.f(num_format=fmts.get(df.columns[j - 1], "dd mmm yyyy hh:mm")))
                elif isinstance(v, str):
                    ws.write_string(i, col + j, v, self.f())
                elif v is None or (isinstance(v, float) and not np.isfinite(v)):
                    ws.write_blank(i, col + j, None, fmt)
                else:
                    ws.write_number(i, col + j, float(v), fmt)
        return row + 1, row + len(df)

    def chart(self, ws, cell, kind, title, series, *, size=(720, 340), y_title=None, subtype=None,
              x_interval=None, legend=True, y_fmt=None, reverse=False, x_num_fmt=None, y_max=None):
        """series: dicts with name, cat (sheet,r0,c0,r1,c1), val (same), color, optional points/width."""
        opts = {"type": kind}
        if subtype:
            opts["subtype"] = subtype
        ch = self.wb.add_chart(opts)
        for k, s in enumerate(series):
            d = {"name": s["name"], "categories": list(s["cat"]), "values": list(s["val"])}
            if kind in ("line", "scatter"):
                d["line"] = {"color": s["color"], "width": s.get("width", 2.0)}
                d["marker"] = {"type": "none"}
                if s.get("smooth"):
                    d["smooth"] = True
            elif kind == "area":
                d["fill"] = {"color": s["color"], "transparency": 25}
                d["line"] = {"color": s["color"], "width": 1.0}
            else:
                d["fill"] = {"color": s["color"]}
                d["border"] = {"color": SURFACE, "width": 1.0} if subtype == "stacked" else {"none": True}
                if k == 0:
                    d["gap"] = s.get("gap", 60)
                    if subtype == "stacked":
                        d["overlap"] = 100
                if s.get("points"):
                    d["points"] = s["points"]
                if s.get("labels"):
                    d["data_labels"] = {"value": True, "num_format": s["labels"],
                                        "font": {"name": FONT, "size": 9, "color": INK2}}
            ch.add_series(d)
        font = {"name": FONT, "size": 9, "color": INK2}
        ch.set_title({"name": title, "name_font": {"name": FONT, "size": 11, "bold": True, "color": INK},
                      "overlay": False})
        value_axis = {"num_font": font, "line": {"none": True}, "major_tick_mark": "none",
                      "major_gridlines": {"visible": True, "line": {"color": GRID, "width": 0.75}}}
        if y_title:
            value_axis["name"] = y_title
            value_axis["name_font"] = {**font, "bold": False}
        value_axis["num_format"] = y_fmt or "General"
        if y_max is not None:
            value_axis["max"] = y_max
        cat_axis = {"num_font": font, "line": {"color": AXIS, "width": 0.75}, "major_tick_mark": "none"}
        if x_interval:
            cat_axis["interval_unit"] = x_interval
        if x_num_fmt:
            cat_axis["num_format"] = x_num_fmt
        if reverse:
            cat_axis["reverse"] = True
        if kind == "bar":
            ch.set_x_axis(value_axis)
            ch.set_y_axis(cat_axis)
        else:
            ch.set_x_axis(cat_axis)
            ch.set_y_axis(value_axis)
        ch.set_legend({"position": "bottom", "font": font} if legend and len(series) > 1 else {"none": True})
        ch.set_chartarea({"border": {"none": True}, "fill": {"color": SURFACE}})
        ch.set_plotarea({"fill": {"color": SURFACE}})
        ch.set_size({"width": size[0], "height": size[1]})
        ws.insert_chart(cell, ch, {"object_position": 1})
        return ch

    def tile(self, ws, row, col, label, value, note, num_format="#,##0", span=3, formula=None):
        lab = self.f(font_color=INK2, bg_color=TILE, font_size=10, indent=1, valign="bottom")
        val = self.f(font_size=22, bold=True, bg_color=TILE, indent=1, num_format=num_format, align="left")
        nt = self.f(font_color=INK2, bg_color=TILE, font_size=9, indent=1, text_wrap=True, valign="top")
        ws.merge_range(row, col, row, col + span - 1, label, lab)
        if formula:
            ws.merge_range(row + 1, col, row + 1, col + span - 1, "", val)
            ws.write_formula(row + 1, col, formula, val, value)
        else:
            ws.merge_range(row + 1, col, row + 1, col + span - 1, value, val)
        ws.merge_range(row + 2, col, row + 2, col + span - 1, note, nt)


def rng(sheet, r0, c0, r1=None, c1=None):
    return (sheet, r0, c0, r0 if r1 is None else r1, c0 if c1 is None else c1)


def eur(x, d=0):
    return f"€{x:,.{d}f}"


# ------------------------------------------------------------------------------------------------
def build_workbook(R: dict) -> None:
    k, B = R["k"], Book(OUT)
    base, future, plans = R["base"], R["future"], R["plans"]
    days = k["days"]
    period = f"{k['start']:%d %b %Y} to {k['end']:%d %b %Y} ({days} full days)"
    best = plans[0]
    rank_future = 1 + sum(p["net"] < future["net"] for p in plans if p["key"] not in (T.BASELINE_KEY, T.FUTURE_KEY))
    best_after = min((p for p in plans if p["key"] != T.BASELINE_KEY), key=lambda p: p["net_adapted"])
    switch = (f"{best_after['supplier']} '{best_after['name']}' would then be cheapest: {eur(best_after['net'])} as he charges today, "
              f"{eur(best_after['net_adapted'])} if he moves charging into its {best_after['ev_win'][0]:02d}:00-{best_after['ev_win'][1]:02d}:00 window - "
              f"about {eur((future['net'] - best_after['net_adapted']) * 365 / days)} a year less than staying put")
    year = 365 / days

    # Sheets are created up front in display order, then filled in dependency order (charts on
    # the narrative sheets point at tables on other sheets).
    names = ["Dashboard", "Why an EMS", "Tariff", "Self-consumption", "Day profile", "Daily", "Heatmaps",
             "Battery & EV", "Solar", "Peaks & baseload", "Methods", "Data_15min", "Data_daily",
             "Data_monthly", "Dictionary", "Tariff_calc"]
    for n in names:
        B.wb.add_worksheet(n)

    # ================================ Dashboard =================================================
    ws = B.sheet("Dashboard", f"{SITE_LABEL} - what the energy box saw",
                 f"{period}. Solar PV (~{k['pv_kwp_est']:.1f} kWp) + ~{k['batt_usable_kwh']:.0f} kWh battery + "
                 f"electric car, measured every ~5 seconds by the SmartCORE home energy management (EMS) box. "
                 f"Every chart in this workbook is a live Excel chart: click it to see the numbers behind it.",
                 tab=LOAD, width=10.7, first_col_width=2)
    for r in (3, 7):
        ws.set_row(r, 20); ws.set_row(r + 1, 36); ws.set_row(r + 2, 32)
    ws.set_row(6, 8)
    B.tile(ws, 3, 1, "Electricity used", k["load_kwh"], f"{k['load_kwh']/days:.0f} kWh a day - the car is {k['ev_share_of_load']:.0%} of it", '#,##0" kWh"')
    B.tile(ws, 3, 4, "Solar generated", k["pv_kwh"], f"{k['pv_kwh']/days:.0f} kWh a day, best day {k['best_pv_day'][1]:.0f} kWh", '#,##0" kWh"')
    B.tile(ws, 3, 7, "Bought from the grid", k["import_kwh"], f"{k['window_import_share']:.0%} of it in the cheap 02:00-06:00 window", '#,##0" kWh"')
    B.tile(ws, 3, 10, "Sold to the grid", k["export_kwh"], f"earns {eur(base['export_credit'])} at {base['export']}c/kWh", '#,##0" kWh"')
    B.tile(ws, 7, 1, "Net electricity bill (live from Tariff sheet)", base["net"], f"{eur(base['net']/days, 2)} a day, standing charge and export credit included", '"€"#,##0', formula="=Tariff!T9")
    B.tile(ws, 7, 4, "Saved vs. same house, no solar or battery", k["system_saving"], f"bill would have been {eur(k['bill_without_system'])}", '"€"#,##0')
    B.tile(ws, 7, 7, "Solar used at home (self-consumption)", k["self_consumption"], f"self-sufficiency {k['self_sufficiency']:.0%} - low, and that is fine (see answer 2)", "0%")
    B.tile(ws, 7, 10, "CO₂ avoided by the solar panels", k["co2_avoided_kg"], f"at {T.GRID_CO2_KG_PER_KWH*1000:.0f} g/kWh (SEAI 2026 grid factor)", '#,##0" kg"')

    answers = [
        ("1. Is Martin on the right tariff?",
         f"Today, yes. Priced on his real half-hourly usage, his EV plan (02:00-06:00 cheap window, inferred from the data) is the "
         f"cheapest of the {len(plans)} plans tested: {eur(base['net'])} for the period. But from 12 October 2026 the same plan costs "
         f"{eur(future['net'])} for the same usage (+{future['net']/base['net']-1:.0%}) and drops to rank {rank_future}. "
         f"{switch}. Re-check in October - the Tariff sheet does it in seconds."),
        ("2. Is his self-consumption optimal?",
         f"Physically no, financially yes. Only {k['self_consumption']:.0%} of his solar is used at home (a battery home usually reaches 60-80 %), because the "
         f"battery is filled from the grid at night and is already full at sunrise. But night electricity costs him {base['ev']:.1f}c and exported solar "
         f"earns {base['export']:.1f}c, so selling the solar and buying at night is the cheaper choice. What actually costs money is the "
         f"{k['day_import_kwh']:.0f} kWh bought at the {base['day']:.0f}c day rate: {k['day_import_share_kwh']:.0%} of his units but {k['day_import_share_eur']:.0%} of his energy cost."),
        ("3. What is the biggest saving still on the table?",
         f"Car charging that slips outside the cheap window: {k['ev_out_window_kwh']:.0f} kWh in {days} days cost {eur(k['ev_out_window_extra_eur'])} more than it needed to "
         f"(about {eur(k['ev_out_window_extra_eur']*year)} a year). Next: on {k['batt_to_ev_nights']} nights the house battery emptied itself into the car at 02:00 and then "
         f"recharged from the grid - wear for nothing - and the battery hit its low-voltage alarm {k['batt_low_events']} times. Both are settings, not hardware."),
    ]
    row = 11
    for q, a in answers:
        ws.merge_range(row, 1, row, 12, q, B.f(bold=True, font_size=12, top=1, top_color=GRID))
        ws.merge_range(row + 1, 1, row + 1, 12, a, B.f(text_wrap=True, valign="top", font_size=10.5))
        ws.set_row(row, 24); ws.set_row(row + 1, 74)
        row += 2

    # ================================ Day profile (data first) ==================================
    prof = R["prof"]
    wp = B.sheet("Day profile", "An average day, half-hour by half-hour",
                 f"The signature of this house: a quiet ~{k['median_load_w']:.0f} W for most of the day, solar peaking around 13:00-14:00 and mostly exported, "
                 f"and a ~7 kW car charge in the small hours. With the car removed (top right) a timed ~2.5 kW load shows up at 05:00 every day - most likely water heating, "
                 f"already sitting in the cheap window - and the weekday cooking peak at 18:00 is carried by the battery. Averages over {days} days; kW.", tab=LOAD)
    T0 = 46
    cols = list(prof.columns)
    heads = {"load_kw": "Total use kW", "pv_kw": "Solar kW", "import_kw": "Grid import kW", "export_kw": "Grid export kW",
             "batt_discharge_kw": "Battery discharge kW (est.)", "batt_charge_kw": "Battery charge kW (est.)",
             "house_kw": "House only (no car) kW", "pv1_kw": "Solar string 1 kW", "pv2_kw": "Solar string 2 kW",
             "house_weekday_kw": "House weekday kW", "house_weekend_kw": "House weekend kW"}
    heads.update({f"pv_{mo}": f"Solar {pd.Timestamp(mo + '-01'):%b} kW" for mo in R["months"]})
    prof_x = prof.copy(); prof_x.index.name = "Time"
    prof_x["battery_net_kw"] = prof["batt_discharge_kw"] - prof["batt_charge_kw"]
    heads["battery_net_kw"] = "Battery net kW (+ out, - in)"
    cols = list(prof_x.columns)
    B.heading(wp, T0 - 1, "Data behind the charts")
    wp.set_row(T0, 45)
    r0, r1 = B.table(wp, T0, 0, prof_x, default="0.00", headers=heads)
    pc = lambda name: cols.index(name) + 1
    cat = rng("Day profile", r0, 0, r1, 0)
    sv = lambda name, color, label=None, **kw: {"name": label or heads[name].replace(" kW", ""), "cat": cat,
                                                 "val": rng("Day profile", r0, pc(name), r1, pc(name)), "color": color, **kw}
    avg_day = [sv("load_kw", LOAD, "Total use"), sv("pv_kw", PV, "Solar"), sv("import_kw", IMPORT, "Grid import"), sv("export_kw", EXPORT, "Grid export")]
    B.chart(wp, "A4", "line", "Average day: use, solar, grid import and export (kW)", avg_day, x_interval=4, y_title="kW")
    B.chart(wp, "J4", "line", "The house without the car: weekdays vs weekends (kW)",
            [sv("house_weekday_kw", LOAD, "Weekday"), sv("house_weekend_kw", IMPORT, "Weekend")], x_interval=4, y_title="kW")
    B.chart(wp, "A22", "line", "Solar output through the day, by month (kW)",
            [sv(f"pv_{mo}", MONTH_COLORS[i], f"{pd.Timestamp(mo + '-01'):%B}") for i, mo in enumerate(R["months"])], x_interval=4, y_title="kW")
    B.chart(wp, "J22", "column", "Battery, estimated: discharging (+) and charging (-), average kW",
            [sv("battery_net_kw", BATT, "Battery net", gap=20)], x_interval=4, y_title="kW")
    wp.freeze_panes(T0 + 1, 1)

    # ================================ Data_monthly / Daily ======================================
    monthly = R["monthly"].copy()
    monthly.index = [f"{d:%b %Y}" for d in monthly.index]; monthly.index.name = "Month"
    mh = {"import": "Grid import kWh", "export": "Grid export kWh", "load": "Used kWh", "pv": "Solar kWh", "losses": "Losses kWh",
          "self_used_pv": "Solar used at home kWh", "ev_kwh": "Car kWh (est.)", "house_kwh": "House kWh",
          "batt_charge_grid": "Battery charged from grid kWh (est.)", "batt_charge_pv": "Battery charged from solar kWh (est.)",
          "batt_discharge": "Battery discharged kWh (est.)", "days": "Days", "self_consumption": "Self-consumption",
          "self_sufficiency": "Self-sufficiency", "pv_per_day": "Solar kWh/day", "yield_kwh_per_kwp_day": "Yield kWh/kWp/day"}
    wm = B.sheet("Data_monthly", "Monthly totals", "September is a part month. 'est.' columns are inferred, see Methods.", tab=MUTED, width=13)
    wm.set_row(3, 45)
    m0, m1 = B.table(wm, 3, 0, monthly, headers=mh, default="#,##0",
                     fmts={"self_consumption": "0%", "self_sufficiency": "0%", "pv_per_day": "0.0", "yield_kwh_per_kwp_day": "0.00"})
    mcols = list(monthly.columns)
    mc = lambda n: mcols.index(n) + 1
    mcat = rng("Data_monthly", m0, 0, m1, 0)
    ms = lambda n, color, label, **kw: {"name": label, "cat": mcat, "val": rng("Data_monthly", m0, mc(n), m1, mc(n)), "color": color, **kw}

    daily = R["daily"].copy()
    daily.index = daily.index.tz_localize(None); daily.index.name = "Date"
    dh = {**mh, "peak_load_kw": "Peak use kW", "peak_import_kw": "Peak grid import kW", "v_max": "Max voltage V", "coverage": "Data coverage"}
    wd = B.sheet("Data_daily", "Daily totals", "One row per full day. kWh unless stated.", tab=MUTED, width=13)
    wd.set_row(3, 45)
    d0, d1 = B.table(wd, 3, 0, daily, headers=dh, index_fmt="ddd dd mmm yyyy", default="0.0",
                     fmts={"self_consumption": "0%", "self_sufficiency": "0%", "coverage": "0.0%"})
    wd.set_column(0, 0, 17); wd.freeze_panes(4, 1); wd.autofilter(3, 0, d1, len(daily.columns))
    dcols = list(daily.columns)
    dc = lambda n: dcols.index(n) + 1
    dcat = rng("Data_daily", d0, 0, d1, 0)
    ds = lambda n, color, label, **kw: {"name": label, "cat": dcat, "val": rng("Data_daily", d0, dc(n), d1, dc(n)), "color": color, **kw}

    # Dashboard charts (now that their source ranges exist)
    ws_charts_row = 18
    B.chart(ws, f"B{ws_charts_row}", "line", "An average day (kW): solar by day, car by night", avg_day, size=(470, 300), x_interval=6)
    B.chart(ws, f"H{ws_charts_row}", "column", "Month by month (kWh) - September is a part month",
            [ms("load", LOAD, "Used"), ms("pv", PV, "Solar"), ms("import", IMPORT, "Bought"), ms("export", EXPORT, "Sold")], size=(470, 300))

    # ================================ Tariff ====================================================
    wt = B.sheet("Tariff", "Is Martin on the right tariff?",
                 f"Each plan is priced on Martin's real half-hourly grid import and export for {period}. Today his plan is the cheapest "
                 f"({eur(base['net'])}); after the 12 Oct 2026 increase it costs {eur(future['net'])}. {switch}. "
                 f"Yellow cells are inputs: type the rates from a bill or a new offer and every figure and the chart update.", tab=IMPORT, width=9.5, first_col_width=40)
    wc = B.wb.get_worksheet_by_name("Tariff_calc")
    tp = R["tprof"]
    wt.write(3, 0, "Days in period", B.f(font_color=INK2)); wt.write(3, 1, days, B.f(bg_color=INPUT, num_format="0"))
    wt.write(4, 0, "Grid import kWh (from Tariff_calc)", B.f(font_color=INK2)); wt.write_formula(4, 1, "=SUM(Tariff_calc!$B$2:$B$49)", B.f(num_format="#,##0"), float(tp["import_all"].sum()))
    wt.write(5, 0, "Grid export kWh (from Tariff_calc)", B.f(font_color=INK2)); wt.write_formula(5, 1, "=SUM(Tariff_calc!$D$2:$D$49)", B.f(num_format="#,##0"), float(tp["export_all"].sum()))
    wt.write(3, 3, "PSO levy €/month", B.f(font_color=INK2)); wt.write(3, 5, T.PSO_EUR_PER_MONTH, B.f(bg_color=INPUT, num_format="0.00"))
    wt.write(4, 3, "All rates: cent/kWh inc. 9 % VAT. Hours are clock hours 0-24; a window such as 23 to 8 wraps past midnight. Band priority: EV > peak > night > day.", B.f(font_color=INK2))
    heads_t = ["Plan", "Supplier", "Day c", "Night c", "Night from", "Night to", "Peak c", "Peak from", "Peak to", "EV c", "EV from", "EV to",
               "Free Sat 8-23 (1=yes)", "Extra discount %", "Standing €/yr", "Export c", "Import cost €", "Standing + PSO €",
               "Export credit €", "NET € for period", "Avg import c/kWh", "NET €/yr at this pattern", "NET € if charging re-timed to plan's window", "Notes"]
    HR = 7
    wt.set_row(HR, 88)
    for j, h in enumerate(heads_t):
        wt.write(HR, j, h, B.f(bold=True, font_color=INK2, bottom=1, bottom_color=AXIS, text_wrap=True, valign="bottom"))
    mine = {**base, "name": "MARTIN'S ACTUAL RATES - edit me (pre-filled with the inferred plan)", "supplier": "?", "key": "mine",
            "note": "Type the unit rates, windows, standing charge and export rate from the latest bill."}
    rows_t = [mine] + plans
    inp = lambda nf: B.f(bg_color=INPUT, num_format=nf)
    for i, p in enumerate(rows_t):
        r = HR + 1 + i
        x = r + 1  # Excel row number
        wt.write(r, 0, p["name"], B.f(bold=(p["key"] in ("mine", T.BASELINE_KEY)), text_wrap=True))
        wt.write(r, 1, p["supplier"], B.f())
        vals = [p["day"], p.get("night"), *(p.get("night_win") or (None, None)), p.get("peak"), *(p.get("peak_win") or (None, None)),
                p.get("ev"), *(p.get("ev_win") or (None, None)), p.get("free_sat", 0), 0, p["standing"], p["export"]]
        nfs = ["0.00", "0.00", "0", "0", "0.00", "0", "0", "0.00", "0", "0", "0", "0%", "#,##0.00", "0.00"]
        for j, (v, nf) in enumerate(zip(vals, nfs), start=2):
            if v is None:
                wt.write_blank(r, j, None, inp(nf))
            else:
                wt.write_number(r, j, v, inp(nf))
        c = xl_col_to_name(5 + i)  # this plan's rate column on Tariff_calc
        money = B.f(num_format='"€"#,##0')
        wt.write_formula(r, 16, f"=(SUMPRODUCT(Tariff_calc!${c}$2:${c}$49,Tariff_calc!$B$2:$B$49)-M{x}*SUMPRODUCT(Tariff_calc!${c}$2:${c}$49,Tariff_calc!$C$2:$C$49))/100", money, p["import_cost"])
        wt.write_formula(r, 17, f"=O{x}/365*$B$4+$F$4*$B$4/30.4375", money, p["standing_cost"])
        wt.write_formula(r, 18, f"=P{x}*$B$6/100", money, p["export_credit"])
        wt.write_formula(r, 19, f"=Q{x}+R{x}-S{x}", B.f(num_format='"€"#,##0', bold=True), p["net"])
        wt.write_formula(r, 20, f"=Q{x}/$B$5*100", B.f(num_format="0.0"), p["import_cost"] / k["import_kwh"] * 100)
        wt.write_formula(r, 21, f"=T{x}*365/$B$4", money, p["net"] * year)
        wt.write_number(r, 22, p.get("net_adapted", p["net"]), money)
        wt.write(r, 23, p["note"], B.f(font_color=INK2))
    wt.set_column(1, 1, 15); wt.set_column(16, 22, 11.5); wt.set_column(23, 23, 90)
    t0, t1 = HR + 1, HR + len(rows_t)
    pts = []
    for p in rows_t:
        col_ = LOAD if p["key"] in ("mine", T.BASELINE_KEY) else IMPORT if p["key"] == T.FUTURE_KEY else NEUTRAL
        pts.append({"fill": {"color": col_}})
    B.chart(wt, f"A{t1 + 4}", "bar", f"Net cost of the {days} days on each plan (€) - blue = today's plan, orange = same plan after 12 Oct",
            [{"name": "As he charges today", "cat": rng("Tariff", t0, 0, t1, 0), "val": rng("Tariff", t0, 19, t1, 19), "color": NEUTRAL,
              "points": pts, "gap": 40, "labels": '"€"#,##0'},
             {"name": "If charging is re-timed to the plan's own cheap window", "cat": rng("Tariff", t0, 0, t1, 0), "val": rng("Tariff", t0, 22, t1, 22),
              "color": EXPORT, "labels": '"€"#,##0'}], size=(1100, 560), reverse=True, y_fmt='"€"#,##0')
    nrow = t1 + 33
    for text in [
        "How to read it: NET = import cost + standing charge + PSO levy - export credit. 'Re-timed' assumes he moves his night charging (car + battery, about 9.9 kW) into each plan's own cheap window; "
        "plans with a 2- or 3-hour window cannot fit his bigger charges, so part spills into their ordinary night rate.",
        f"Caveats: {days} summer-weighted days. Winter means less export credit and more import, which favours plans with a low night rate and standing charge even more. "
        "'€/yr at this pattern' is a simple scale-up, not a forecast. Energia rates are standard (undiscounted) - new-customer discounts can be tested in the 'Extra discount %' column. "
        "Urban standing charges; rural is roughly €65-90 a year higher on every plan. Rates researched " + T.RESEARCH_DATE + " - see Methods for sources; always confirm on the supplier's site.",
        "Why comparison websites cannot do this: they price a 'typical' 4,200 kWh home. Martin's home is nothing like typical - half his electricity goes into a car and "
        f"{k['window_import_share']:.0%} of what he buys is bought between 02:00 and 06:00. Only measured half-hourly data gives the real answer.",
    ]:
        B.note(wt, nrow, text, height=48, last_col=22); nrow += 1
    wt.freeze_panes(HR + 1, 1)

    # Tariff_calc helper
    wc.set_column(0, 20, 13, B.f())
    for j, h in enumerate(["Hour of day", "Import kWh (all days)", "Import kWh Sat 08-23", "Export kWh", "Load kWh"] + [f"Rate c/kWh: row {HR + 2 + i}" for i in range(len(rows_t))]):
        wc.write(0, j, h, B.f(bold=True, text_wrap=True, font_color=INK2))
    wc.set_row(0, 45)
    win = lambda s, e: f"IF(Tariff!${s}<Tariff!${e},AND($A{{a}}>=Tariff!${s},$A{{a}}<Tariff!${e}),OR($A{{a}}>=Tariff!${s},$A{{a}}<Tariff!${e}))"
    for h in range(48):
        a = h + 2
        wc.write_number(h + 1, 0, h / 2, B.f(num_format="0.0"))
        for j, cname in enumerate(["import_all", "import_sat_free", "export_all", "load_all"], start=1):
            wc.write_number(h + 1, j, float(tp[cname].iloc[h]), B.f(num_format="0.00"))
        for i, p in enumerate(rows_t):
            x = HR + 2 + i
            fml = (f"=(1-Tariff!$N${x})*IF(AND(Tariff!$J${x}<>\"\",{win(f'K${x}', f'L${x}')}),Tariff!$J${x},"
                   f"IF(AND(Tariff!$G${x}<>\"\",{win(f'H${x}', f'I${x}')}),Tariff!$G${x},"
                   f"IF(AND(Tariff!$D${x}<>\"\",{win(f'E${x}', f'F${x}')}),Tariff!$D${x},Tariff!$C${x})))").replace("{a}", str(a))
            wc.write_formula(h + 1, 5 + i, fml, B.f(num_format="0.00"), T.rate_vector(p)[h])
    wc.set_tab_color(MUTED)

    # Dashboard: plan comparison + expensive imports
    exp = R["expensive"]
    B.chart(ws, f"B{ws_charts_row + 16}", "bar", "Same usage, different plan: net cost for the period (€)",
            [{"name": "Net €", "cat": rng("Tariff", t0 + 1, 0, t1, 0), "val": rng("Tariff", t0 + 1, 19, t1, 19), "color": NEUTRAL,
              "points": pts[1:], "gap": 35}], size=(470, 340), reverse=True, legend=False, y_fmt='"€"#,##0')

    # ================================ Self-consumption ==========================================
    wsc = B.sheet("Self-consumption", "Is Martin using his own solar well?",
                  f"Self-consumption {k['self_consumption']:.0%} (share of solar used at home) and self-sufficiency {k['self_sufficiency']:.0%} (share of use not bought). "
                  f"Low for a home with a battery - studies put PV + battery homes at 60-85 % - but with night power at {base['ev']:.1f}c and export at {base['export']:.1f}c, "
                  "exporting the solar and refilling the battery at night is the cheaper strategy. The numbers to watch are the day-rate imports below.", tab=EXPORT)
    B.chart(wsc, "A4", "column", "Where the solar went each month (kWh)",
            [ms("self_used_pv", PV, "Used at home"), ms("export", EXPORT, "Sold to grid")], subtype="stacked")
    S0 = 46
    val = pd.DataFrame({"c_per_kwh": [base["day"], k["value_pv_charge_c"], base["export"], k["value_grid_charge_c"], base["ev"]]},
                       index=["Buy 1 kWh at the day rate", "1 kWh out of the battery, filled from solar (export income given up, 95 % efficient)",
                              "Sell 1 kWh of solar (export rate)", "1 kWh out of the battery, filled from night grid (90 % round trip)",
                              "Buy 1 kWh in the 02:00-06:00 window"])
    val.index.name = "What 1 kWh is worth on Martin's plan"
    B.heading(wsc, S0 - 1, "Data behind the charts")
    v0, v1 = B.table(wsc, S0, 0, val, default="0.0", headers={"c_per_kwh": "cent/kWh"})
    B.chart(wsc, "F4", "bar", "What 1 kWh is worth on Martin's plan (cent) - why selling solar beats storing it",
            [{"name": "cent/kWh", "cat": rng("Self-consumption", v0, 0, v1, 0), "val": rng("Self-consumption", v0, 1, v1, 1),
              "color": LOAD, "gap": 45, "labels": "0.0"}], reverse=True, legend=False)
    B.chart(wsc, "A22", "line", "Self-consumption and self-sufficiency by month",
            [ms("self_consumption", PV, "Self-consumption (solar used at home)"), ms("self_sufficiency", LOAD, "Self-sufficiency (use not bought)")], y_fmt="0%", y_max=1)
    B.chart(wsc, "F22", "column", "What filled the battery each month (kWh, estimated)",
            [ms("batt_charge_grid", IMPORT, "From night-rate grid"), ms("batt_charge_pv", PV, "From solar")], subtype="stacked")
    wsc.set_column(0, 0, 62)
    ex = exp.copy(); ex.index.name = "Day-rate imports: where they come from"
    e0 = v1 + 3
    wsc.set_row(e0, 62)
    x0, x1 = B.table(wsc, e0, 0, ex, headers={"kwh": "kWh", "eur": f"Cost € at {base['day']:.1f}c", "eur_if_cheap": "Avoidable € if moved to the cheap window"}, default="#,##0.0")
    sc_rows = pd.DataFrame({"eur": [base["net"], future["net"], base["net"] - k["ev_out_window_extra_eur"], k["bill_without_system"], k["flat_plan_net"],
                                    base["net"] + 100 * (base["export"] - base["ev"]) / 100]},
                           index=["Today: what he actually did, on his plan", "Same behaviour after the 12 Oct 2026 price rise",
                                  "Today, with every car charge kept inside 02:00-06:00", "Same house with no solar and no battery (same plan)",
                                  "Same behaviour on a flat 24-hour plan (Electric Ireland Saver)",
                                  "If he moved 100 kWh of car charging from the night window to solar hours (loses export income)"])
    sc_rows.index.name = f"Scenarios: net bill for the {days} days"
    q0, q1 = B.table(wsc, x1 + 3, 0, sc_rows, headers={"eur": "Net €"}, default='"€"#,##0')
    B.chart(ws, f"H{ws_charts_row + 16}", "bar", f"The {k['day_import_kwh']:.0f} kWh bought at the expensive day rate - why? (€)",
            [{"name": "€", "cat": rng("Self-consumption", x0, 0, x1, 0), "val": rng("Self-consumption", x0, 2, x1, 2), "color": IMPORT, "gap": 45, "labels": '"€"#,##0'}],
            size=(470, 340), reverse=True, legend=False, y_fmt='"€"#,##0')
    B.note(wsc, q1 + 2, "Rule of thumb from these numbers: on this plan, solar is worth more sold than stored, and the car should never be charged from solar instead of the night window "
           f"(each kWh moved loses {base['export'] - base['ev']:.1f}c). That flips if the export rate falls below the night rate - one more reason to keep measuring. "
           "Benchmark: JRC, 'Self-consumption of electricity by households' - PV-only 30-60 %, with battery 60-85 %.", height=48)

    # ================================ Daily =====================================================
    wdl = B.sheet("Daily", "Day by day",
                  f"Best solar day {k['best_pv_day'][0]:%d %b}: {k['best_pv_day'][1]:.1f} kWh. Dullest {k['worst_pv_day'][0]:%d %b}: {k['worst_pv_day'][1]:.1f} kWh - an 8x swing. "
                  f"Biggest use {k['max_load_day'][0]:%d %b}: {k['max_load_day'][1]:.0f} kWh (two car charges). The table behind these charts is the Data_daily sheet.", tab=PV)
    big = (1466, 330)
    B.chart(wdl, "A4", "column", "Solar generated each day (kWh)", [ds("pv", PV, "Solar", gap=25)], size=big, x_num_fmt="d mmm", x_interval=7)
    B.chart(wdl, "A21", "column", "Electricity used each day (kWh): house and car", [ds("house_kwh", LOAD, "House", gap=25), ds("ev_kwh", EV, "Car (estimated)")],
            subtype="stacked", size=big, x_num_fmt="d mmm", x_interval=7)
    B.chart(wdl, "A38", "line", "Bought from and sold to the grid each day (kWh)", [ds("import", IMPORT, "Bought", width=1.75), ds("export", EXPORT, "Sold", width=1.75)],
            size=big, x_num_fmt="d mmm", x_interval=7)

    # ================================ Heatmaps ==================================================
    wh = B.sheet("Heatmaps", "Every half-hour of every day",
                 "Each row is a day, each column a half-hour from 00:00 (left) to 23:30 (right); darker = more kWh. Look for the vertical stripe of night charging, "
                 "the solar 'lens' that widens to midsummer, and the odd dark cells where the car was charged in daytime.", tab=BATT, width=2.4, first_col_width=13, ncols=49)
    ramp = {"import": ("Bought from the grid (kWh per half-hour)", "#ffe9df", "#a83c10"), "load": ("Electricity used (kWh per half-hour)", "#e6f0fd", "#0d366b"),
            "pv": ("Solar generated (kWh per half-hour)", "#fff3d1", "#8a5a00"), "export": ("Sold to the grid (kWh per half-hour)", "#ddf5ea", "#0b6b48")}
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

    # ================================ Battery & EV ==============================================
    ses = R["sessions"].copy()
    wb_ = B.sheet("Battery & EV", "The car and the battery",
                  f"Car: {k['ev_sessions']} charges, {k['ev_kwh']:,.0f} kWh = {k['ev_share_of_load']:.0%} of all use, about {k['ev_km_per_week']:.0f} km a week, "
                  f"{eur(k['ev_eur_per_100km'], 2)} per 100 km (petrol: about {eur(metrics.PETROL_EUR_PER_100KM, 2)}). Battery (no sensor, estimated from the inverter): about {k['batt_usable_kwh']:.1f} kWh usable, "
                  f"charges at {k['batt_charge_kw']:.1f} kW, {k['batt_grid_kwh']:.0f} kWh filled from the grid vs {k['batt_pv_kwh']:.0f} kWh from solar.", tab=EV)
    wk = R["weekly_ev"].to_frame("kwh"); wk.index = wk.index.tz_localize(None); wk.index.name = "Week ending"
    starts = ses["start"].dt.hour.value_counts().reindex(range(24), fill_value=0).to_frame("sessions"); starts.index.name = "Hour charge started"
    lowh = R["low_by_hour"].to_frame("alarms"); lowh.index.name = "Hour of day"
    E0 = 46
    B.heading(wb_, E0 - 1, "Data behind the charts")
    w0, w1 = B.table(wb_, E0, 0, wk, index_fmt="dd mmm", headers={"kwh": "Car kWh"})
    s0, s1 = B.table(wb_, E0, 3, starts, default="0", headers={"sessions": "Charges"})
    l0, l1 = B.table(wb_, E0, 6, lowh, default="0", headers={"alarms": "Battery-low alarms"})
    B.chart(wb_, "A4", "column", "Car charging per week (kWh, estimated from house load above 5 kW)",
            [{"name": "Car kWh", "cat": rng("Battery & EV", w0, 0, w1, 0), "val": rng("Battery & EV", w0, 1, w1, 1), "color": EV, "gap": 35}], x_num_fmt="d mmm", legend=False)
    B.chart(wb_, "J4", "column", "When car charging starts (number of charges by hour) - the cheap window opens at 02:00",
            [{"name": "Charges", "cat": rng("Battery & EV", s0, 3, s1, 3), "val": rng("Battery & EV", s0, 4, s1, 4), "color": EV, "gap": 35}], legend=False)
    B.chart(wb_, "A22", "column", f"'Battery low voltage' alarms by hour of day ({k['batt_low_events']} alarms on {k['batt_low_days']} days)",
            [{"name": "Alarms", "cat": rng("Battery & EV", l0, 6, l1, 6), "val": rng("Battery & EV", l0, 7, l1, 7), "color": BATT, "gap": 35}], legend=False)
    B.chart(wb_, "J22", "line", "Battery each day (kWh, estimated): filled from the grid, from solar, and discharged",
            [ds("batt_charge_grid", IMPORT, "Charged from grid", width=1.5), ds("batt_charge_pv", PV, "Charged from solar", width=1.5), ds("batt_discharge", BATT, "Discharged", width=1.5)],
            x_num_fmt="d mmm", x_interval=14)
    B.note(wb_, 40, f"What the 02:00 spike means: when the cheap window opens the car starts pulling ~7 kW, and the inverter answers by emptying what is left of the battery into it "
           f"({k['batt_to_ev_kwh']:.0f} kWh over {k['batt_to_ev_nights']} nights) until the low-voltage protection trips. Two hours later it buys that energy back. It costs little in euro but it is wasted battery life. "
           "Fix: block battery discharge while the car charges (an inverter or charger setting, or an automation on the EMS box).", height=48)
    B.note(wb_, 41, f"Charges outside 02:00-06:00: {k['ev_out_window_kwh']:.0f} kWh, which cost {eur(k['ev_out_window_extra_eur'])} more than in the window. "
           f"Assumptions: car energy = house load above {metrics.EV_LOAD_W/1000:.0f} kW minus a {metrics.HOUSE_BASE_W} W house base; {metrics.EV_KWH_PER_100KM:.0f} kWh/100 km; petrol 6 L/100 km at €1.75.", height=34)
    ses["start"] = ses["start"].dt.tz_localize(None); ses["end"] = ses["end"].dt.tz_localize(None)
    ses.index = range(1, len(ses) + 1); ses.index.name = "#"
    B.heading(wb_, E0 - 1, "Every car charge", col=10)
    wb_.set_row(E0, 45)
    B.table(wb_, E0, 10, ses, default="0.0", fmts={"share_cheap": "0%", "cost_eur": '"€"0.00', "cost_if_all_cheap": '"€"0.00', "start": "ddd dd mmm hh:mm", "end": "hh:mm"},
            headers={"start": "Start", "end": "End", "hours": "Hours", "kwh": "kWh", "kwh_cheap_window": "kWh in cheap window", "share_cheap": "In window",
                     "cost_eur": "Cost", "cost_if_all_cheap": "Cost if all in window"})
    wb_.set_column(11, 11, 17)

    # ================================ Solar =====================================================
    wso = B.sheet("Solar", "How well are the panels doing?",
                  f"{k['pv_kwh']:,.0f} kWh in {days} days = {k['yield_kwh_per_kwp']:.0f} kWh per kWp (array size estimated at {k['pv_kwp_est']} kWp from a {k['pv_peak_kw']:.2f} kW peak). "
                  f"Lifetime on the inverter: {R['lifetime']['pv']:,.0f} kWh generated, {R['lifetime']['export']:,.0f} kWh exported. The two strings are balanced ({k['pv1_kwh']:.0f} vs {k['pv2_kwh']:.0f} kWh); string 2 peaks before noon and string 1 mid-afternoon, so the array is split roughly east/west, which spreads output across the day. "
                  f"Grid voltage peaked at {k['v_max']:.0f} V and never reached the 253 V limit, so the inverter was never forced to throttle.", tab=PV)
    vh = R["vhist"].set_index("volts_from"); vh.index.name = "Volts (2 V bins from)"
    O0 = 46
    B.heading(wso, O0 - 1, "Data behind the charts")
    h0, h1 = B.table(wso, O0, 0, vh, default="0.0%", headers={"share_of_time": "Share of time"})
    facts = pd.DataFrame({"value": [k["pv_peak_kw"], k["clip_minutes"], k["clip_days"], k["loss_share"] * 100, k["v_mean"], k["v_min"], k["v_max"], k["v_over_253_min"],
                                    k["f_mean"], k["f_min"], k["f_max"]]},
                         index=["Peak solar DC power seen, kW", "Minutes with inverter at its ~4 kW AC limit (clipping)", "Days with more than 5 min of clipping",
                                "Inverter + battery losses, % of solar", "Mean grid voltage, V", "Min grid voltage, V", "Max grid voltage, V", "Minutes above 253 V",
                                "Mean frequency, Hz", "Min frequency, Hz", "Max frequency, Hz"])
    facts.index.name = "Solar and power-quality facts"
    B.table(wso, O0, 4, facts, default="#,##0.00", headers={"value": "Value"})
    wso.set_column(4, 4, 52)
    B.chart(wso, "A4", "line", "The two solar strings through an average day (kW)", [sv("pv1_kw", PV, "String 1"), sv("pv2_kw", PV2, "String 2")], x_interval=4, y_title="kW")
    B.chart(wso, "F4", "column", "Average solar per day, by month (kWh/day)",
            [ms("pv_per_day", PV, "Solar kWh per day", gap=45, labels="0.0")], legend=False)
    B.chart(wso, "A22", "column", "Grid voltage at the house: share of time in each 2 V band (limit 253 V)",
            [{"name": "Share of time", "cat": rng("Solar", h0, 0, h1, 0), "val": rng("Solar", h0, 1, h1, 1), "color": LOAD, "gap": 20}], y_fmt="0%", legend=False)
    B.chart(wso, "F22", "line", "Highest voltage each day (V)", [ds("v_max", LOAD, "Max voltage", width=1.5)], x_num_fmt="d mmm", x_interval=14, legend=False)
    ft = R["fault_table"].to_frame("count"); ft.index.name = "Inverter fault messages in the period"
    B.table(wso, O0 + 14, 4, ft, default="0", headers={"count": "Count"})

    # ================================ Peaks & baseload ==========================================
    wpk = B.sheet("Peaks & baseload", "The always-on load and the big peaks",
                  f"Always-on (10th percentile) load is {k['baseload_w']:.0f} W = {k['baseload_kwh_year']:,.0f} kWh a year, about a quarter of a typical Irish home's entire use "
                  f"(CRU: {T.CRU_TYPICAL_KWH_PER_YEAR:,} kWh) and worth up to {eur(k['baseload_eur_year'])} a year at a standard ~30c rate. Peak grid import reached {k['peak_import_kw']:.1f} kW, with "
                  f"{k['minutes_over_limit']} minutes above the standard 12 kVA connection: car (7.4 kW) + battery charging (2.5 kW) + the house at once.", tab=LOAD)
    du = R["duration"].set_index("pct_of_time"); du.index.name = "% of time load is above"
    P0 = 28
    B.heading(wpk, P0 - 1, "Data behind the charts")
    u0, u1 = B.table(wpk, P0, 0, du, default="0.00", index_fmt="0.0", headers={"load_kw": "Total use kW", "house_kw": "House only kW"})
    B.chart(wpk, "A4", "line", "Load duration curve: how much of the time is use above each level? (kW)",
            [{"name": "Total use", "cat": rng("Peaks & baseload", u0, 0, u1, 0), "val": rng("Peaks & baseload", u0, 1, u1, 1), "color": LOAD},
             {"name": "House only (car excluded)", "cat": rng("Peaks & baseload", u0, 0, u1, 0), "val": rng("Peaks & baseload", u0, 2, u1, 2), "color": MUTED}],
            x_interval=10, y_title="kW", x_num_fmt="0")
    B.chart(wpk, "J4", "line", "Highest grid import each day (kW) - standard connection is 12 kVA", [ds("peak_import_kw", IMPORT, "Peak import", width=1.5)],
            x_num_fmt="d mmm", x_interval=14, legend=False)
    if "away" in k:
        a0, a1, kwh_day, watts = k["away"]
        B.note(wpk, 24, f"A natural experiment: no car charging from {a0:%d %b} to {a1:%d %b} (household apparently away). The house still used {kwh_day:.1f} kWh a day "
               f"(an average {watts:.0f} W) - fridge, freezer, broadband, standby and the inverter itself. That is the floor no behaviour change will get under without replacing appliances. "
               "(Note for anyone sharing this data: energy data reveals occupancy - which is why the box keeps it in the home and the project shares it only de-identified and with consent.)", height=50)
    pk = R["peaks"].copy(); pk["when"] = pk["when"].dt.tz_localize(None)
    pk.index = range(1, len(pk) + 1); pk.index.name = "Rank"
    B.heading(wpk, P0 - 1, "Ten highest grid-import minutes (one per day)", col=5)
    B.table(wpk, P0, 5, pk, default="#,##0", fmts={"when": "ddd dd mmm yyyy hh:mm"},
            headers={"when": "When", "import_w": "Grid import W", "load_w": "House + car W", "battery_charge_w": "Battery charging W (est.)"})
    wpk.set_column(6, 6, 22)

    # ================================ Why an EMS ================================================
    we = B.sheet("Why an EMS", "Eight things Martin's electricity bill could not tell him",
                 "A bill gives one number every two months. The EMS box reads the inverter every five seconds and keeps the history in the home. "
                 f"Everything below comes from {days} days of that data - and none of it was visible before.", tab=EXPORT, width=10.7, first_col_width=2)
    cards = [
        (eur(k["system_saving"]), "saved in four and a half months",
         f"The same house with no solar, no battery and no night charging would have paid {eur(k['bill_without_system'])}. Martin paid {eur(base['net'])}. "
         "The bill shows what you paid; only measurement shows what you did not pay - and whether your investment is delivering."),
        (f"{k['avg_import_c']:.1f}c", "average price per kWh bought - the day rate is " + f"{base['day']:.1f}c",
         f"{k['window_import_share']:.0%} of everything he buys is bought in the 02:00-06:00 window at {base['ev']:.1f}c. That routine is worth far more than the panels alone, and "
         "the box proves it is working, night after night."),
        (eur(k["ev_out_window_extra_eur"]), "lost to car charging at the wrong time",
         f"{k['ev_out_window_kwh']:.0f} kWh of car charging slipped outside the cheap window and cost four times the price - about {eur(k['ev_out_window_extra_eur']*year)} a year. "
         "One schedule setting on the charger fixes it. You cannot fix what you cannot see."),
        (f"+{future['net']/base['net']-1:.0%}", "same usage, same plan, from 12 October 2026",
         f"Night and EV rates are rising 25-80 % across suppliers this autumn. Martin's plan goes from cheapest of {len(plans)} to rank {rank_future}. With his real half-hourly profile, "
         f"switching and re-timing the charge would save about {eur((future['net'] - best_after['net_adapted']) * year)} a year. Price-comparison sites assume a 'typical' home; his is nothing like typical."),
        (f"{k['self_consumption']:.0%}", "of his solar is used at home - and that is the right answer",
         f"Everyone says 'use your own solar'. On his plan exported solar earns {base['export']:.1f}c and night power costs {base['ev']:.1f}c, so selling solar and refilling at night wins. "
         f"Charging the car from solar would cost him {base['export'] - base['ev']:.0f}c more per kWh. The right strategy depends on your tariff - the data tells you which."),
        (f"{k['batt_to_ev_nights']} nights", "the house battery emptied itself into the car",
         f"At 02:00 the car starts charging and the inverter drains the battery into it, trips its low-voltage alarm ({k['batt_low_events']} times), then buys the energy back two hours later. "
         "Pointless battery wear, invisible on any bill or app summary, fixed with one setting or a simple automation on the box."),
        (f"{k['baseload_w']:.0f} W", "is always on, day and night",
         f"That is {k['baseload_kwh_year']:,.0f} kWh a year - up to {eur(k['baseload_eur_year'])} at a standard rate - before anyone switches anything on. The box shows it to the watt, so you can "
         "hunt it down appliance by appliance."),
        (f"{k['peak_import_kw']:.1f} kW", "peak draw from the grid",
         f"Car + battery + house together went above the standard 12 kVA home connection for {k['minutes_over_limit']} minutes. Worth knowing before adding a heat pump or a second EV - "
         "and an EMS can stagger the loads automatically."),
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
        "• A small box (Raspberry Pi) beside your inverter or meter. It reads what your equipment already measures - nothing is rewired and nothing in the house is controlled unless you ask for it.",
        "• Your own dashboard: live power, solar, grid and battery, and the full history at 5-second detail. The data is stored on the box in your home.",
        "• A report like this one: is my tariff right, is my solar paying, what is wasting money - answered with your numbers, not averages.",
        "• A path to automation: charge the car and the battery at the cheapest time, keep within your connection limit, and share surplus with neighbours in an energy community.",
        "• Part of SmartCORE, an EU Interreg North-West Europe project with MTU and South Kerry Development Partnership. Your data is only used with your consent and is de-identified before any research use.",
    ]:
        we.merge_range(r, 1, r, 13, line, B.f(text_wrap=True, font_size=10.5, valign="top")); we.set_row(r, 34); r += 1

    # ================================ Methods ===================================================
    wq = B.sheet("Methods", "Data quality, methods, assumptions and sources",
                 "IMPORTANT: this workbook contains identifiable household energy data. Do not circulate it outside the project without the householder's consent; "
                 "for wider sharing replace the site label with the UHIC code (SITE_LABEL in martinh_report.py) as set out in the ethics application.", tab=MUTED, first_col_width=44, width=16)
    lines = [
        f"Source: InfluxDB export of the MartinH HEMS box, 2026-09-20, 36 Home Assistant inverter entities, about 21 million points. Analysis period: {period}, Europe/Dublin time.",
        f"Coverage: the box wrote data in {k['coverage']:.2%} of minutes. Home Assistant stores a value only when it changes, so power signals are held at their last value (time-weighted 1-minute means); "
        f"minutes with no point from any entity for {15} min are treated as gaps.",
        "Energy (kWh) comes from the inverter's own daily counters (0.01 kWh resolution) rebuilt across midnight resets and cut into 15-minute intervals (Data_15min sheet); tariffs and heatmaps sum these to half-hours, the resolution an ESB smart meter bills at. "
        "They agree with the inverter's lifetime counters to about 1-2 % and with integrated power to about 1 % (table below). Expect the supplier's bill to differ by a similar margin.",
        "Sign convention: inverter_grid_power is positive when exporting. Battery power is NOT metered on this site: it is estimated as inverter AC power + inverter losses - solar DC power, "
        "so battery figures include conversion losses and are indicative. Car charging is inferred from house load above 5 kW. All 'est.' figures carry perhaps +/-10 %.",
        f"Tariff: the current plan is inferred from the import pattern (cheap 02:00-06:00 window) - the householder confirmed an EV night plan but not the supplier. Rates were researched on {T.RESEARCH_DATE}, "
        "inc. 9 % VAT, urban standing charges; several suppliers change prices in October 2026. Costs are for the measured period only; they are not an annual forecast.",
        f"Assumptions you can change in metrics.py: array {metrics.PV_KWP_EST} kWp, EV threshold {metrics.EV_LOAD_W} W, house base {metrics.HOUSE_BASE_W} W, EV {metrics.EV_KWH_PER_100KM} kWh/100 km, "
        f"grid CO2 {T.GRID_CO2_KG_PER_KWH} kg/kWh. Phone/tablet sensors were deliberately not exported (personal data).",
    ]
    r = 3
    for ln in lines:
        B.note(wq, r, ln, height=50, last_col=8); r += 1
    rc = R["recon"].copy(); rc.index.name = "Reconciliation, whole export (kWh)"
    rc["integrated_power_vs_counters"] = R["recon_power"].reindex(rc.index)
    r += 1
    wq.set_row(r, 45)
    _, r = B.table(wq, r, 0, rc, default="#,##0.0", fmts={"difference_pct": "0.0%", "integrated_power_vs_counters": "0.0%"},
                   headers={"whole_export_today_counters": "Rebuilt from daily counters (used here)", "whole_export_lifetime_counters": "Lifetime counters", "difference_pct": "Difference",
                            "integrated_power_vs_counters": "Integrated power vs daily counters"})
    gp = R["gap_table"].copy()
    if len(gp):
        gp["from"] = gp["from"].dt.tz_localize(None); gp["to"] = gp["to"].dt.tz_localize(None)
        gp = gp.sort_values("minutes", ascending=False).head(10).set_index("from"); gp.index.name = "Data gaps (longest 10), from"
        r += 2
        _, r = B.table(wq, r, 0, gp, index_fmt="ddd dd mmm yyyy hh:mm", default="0", fmts={"to": "hh:mm"}, headers={"to": "To", "minutes": "Minutes"})
    r += 2
    B.heading(wq, r, "Sources (accessed " + T.RESEARCH_DATE + ")"); r += 1
    extra = {"jrc": ("JRC: Self-consumption of electricity by households, effects of PV system size and battery storage",
                     "https://publications.jrc.ec.europa.eu/repository/handle/JRC89291")}
    for name_, url in list(T.SOURCES.values()) + list(extra.values()):
        wq.write(r, 0, name_, B.f(text_wrap=True)); wq.write_url(r, 1, url, B.f(font_color=LOAD, underline=1), url); r += 1

    # ================================ Data_15min / Dictionary ===================================
    d30 = R["data15"].copy(); d30.index = d30.index.tz_localize(None); d30.index.name = "15 min starting (local)"
    h30 = {"import": "Grid import kWh", "export": "Grid export kWh", "load": "Used kWh", "pv": "Solar kWh", "losses": "Losses kWh", "self_used_pv": "Solar used at home kWh",
           "grid_power": "Grid W (+export)", "load_power": "Load W", "pv_power": "Solar DC W", "power": "Inverter AC W", "pv1_power": "String 1 W", "pv2_power": "String 2 W",
           "power_losses": "Inverter losses W", "l1_voltage": "Voltage V", "l1_current": "Current A", "frequency": "Frequency Hz", "pv1_voltage": "String 1 V", "pv1_current": "String 1 A",
           "pv2_voltage": "String 2 V", "pv2_current": "String 2 A", "radiator_temperature": "Heatsink temp C", "room_temperature": "Inverter room temp C",
           "battery_est": "Battery W est. (+discharge)", "import_w": "Import W", "export_w": "Export W", "l1_voltage_max": "Voltage max V"}
    w30 = B.sheet("Data_15min", "All signals at 15-minute resolution",
                  "The 36 source files condensed to 15-minute intervals: energy per 15 min from the inverter counters (0.01 kWh steps), and the mean of every live signal. "
                  "(The raw 21 million 5-second points exceed Excel's row limit; they stay in the .csv.gz files.)", tab=MUTED, width=12, first_col_width=20, ncols=len(d30.columns))
    w30.set_row(3, 45)
    _, e30 = B.table(w30, 3, 0, d30, headers=h30, index_fmt="yyyy-mm-dd hh:mm", default="0.00",
                     fmts={c: "0" for c in d30.columns if c.endswith(("_power", "_w", "_est"))})
    w30.freeze_panes(4, 1); w30.autofilter(3, 0, e30, len(d30.columns))

    desc = {"grid_power": "Power at the grid connection, W (+ export, - import)", "load_power": "House consumption incl. car, W", "pv_power": "Solar DC power, both strings, W",
            "power": "Inverter AC output, W (negative = charging the battery from the grid)", "power_losses": "Inverter conversion losses, W",
            "today_energy_import": "kWh bought today (resets at midnight)", "today_energy_export": "kWh sold today", "today_load_consumption": "kWh used today",
            "today_production": "kWh solar today", "today_losses": "kWh losses today", "device_state": "Inverter state (On-grid / Waiting / Detection)",
            "device_fault": "Inverter fault text", "update_interval": "Seconds between inverter polls", "l1_voltage": "Grid voltage, V", "frequency": "Grid frequency, Hz"}
    inv = R["inventory"].copy()
    inv["description"] = [desc.get(e.replace("sensor.inverter_", ""), "") for e in inv["entity"]]
    inv["used"] = np.where(inv["points"] > 1000, "yes", "no - input not connected on this single-phase site")
    inv.loc[inv["entity"].str.contains("device_|update_interval"), "used"] = "yes"
    for c in ("first", "last"):
        inv[c] = pd.to_datetime(inv[c]).dt.tz_convert("Europe/Dublin").dt.tz_localize(None)
    inv = inv.set_index("entity")[["description", "points", "first", "last", "min", "max", "used"]]; inv.index.name = "Source entity (one .csv.gz each)"
    wdi = B.sheet("Dictionary", "What was in the 36 source files", "Every Home Assistant entity in the export, how many points it had and whether it is used in this workbook.",
                  tab=MUTED, first_col_width=42, width=18)
    B.table(wdi, 3, 0, inv, default="#,##0.0", fmts={"points": "#,##0", "first": "dd mmm yyyy hh:mm", "last": "dd mmm yyyy hh:mm"},
            headers={"description": "What it is", "points": "Points", "first": "First", "last": "Last", "min": "Min", "max": "Max", "used": "Used?"})
    wdi.set_column(1, 1, 62)

    B.wb.get_worksheet_by_name("Dashboard").activate()
    B.wb.set_properties({"title": f"{SITE_LABEL} - energy report", "subject": "SmartCORE HEMS", "comments": "Generated by ems/analysis/martinh_report.py"})
    B.wb.close()


def main() -> None:
    cache = DATA / f".cache_{SITE}.pkl"
    if "--refresh" in sys.argv and cache.exists():
        cache.unlink()
    R = metrics.build(load_site(DATA / SITE, cache))
    rp = R["recon"]["difference_pct"].drop("losses").abs().max()
    print(f"Counter reconciliation: worst difference {rp:.1%}")
    if rp > 0.03:
        raise SystemExit("Energy counters disagree by more than 3 % - check the export before trusting the report.")
    build_workbook(R)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
