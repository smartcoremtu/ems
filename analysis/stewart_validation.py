"""Check the Stewart House HEMS measurements against the Sigenergy app and the frient meter reader.

    .venv/Scripts/python stewart_validation.py [--refresh]

References (there is no ESB smart-meter file for this site yet):
  Sigen app export (../data/Stewart/StewartHouse.xlsx) - daily totals from the vendor cloud: the
      SAME system's meters through a different data path. Checks the box's polling, storage and
      energy method, not the sensors themselves.
  frient EMIZB-141 on the ESB meter's LED - independent hardware, grid IMPORT only.
Writes ../data/Stewart_Validation.xlsx (git-ignored: identifiable household data).
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd

import stewart_metrics as SM
from martinh_report import BATT, EXPORT, IMPORT, LOAD, MUTED, PV, Book, eur, rng
from martinh_validation import PCT, STAT_FMTS, STAT_HEADERS
from sigen_data import load_site, prefixes, read_entity
from stewart_report import DATA, SITE, SITE_LABEL
from validation import agreement

APP = DATA / SITE / "StewartHouse.xlsx"
OUT = DATA / f"{SITE}_Validation.xlsx"
QTY = {"pv": "Solar", "load": "House use", "batt_charge": "Battery charge", "batt_discharge": "Battery discharge",
       "import": "Grid import", "export": "Grid export"}


def read_app(path=APP) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        a = pd.read_excel(path)
    a = a[a["Date"] != "total"]
    a.index = pd.to_datetime(a.pop("Date"))
    a = a.astype(float)
    a.columns = ["pv", "load", "batt_charge", "batt_discharge", "import", "export", "revenue"]
    return a.drop(columns="revenue")


def compute(R: dict) -> dict:
    k, m, e15 = R["k"], R["m"], R["e15"]
    app = read_app()
    hd = R["daily"].copy(); hd.index = hd.index.tz_localize(None)
    days = hd.index[hd["load"].notna()].intersection(app.index)
    days = days[days < app.index.max()]                        # the app's last day is today, incomplete
    h, a = hd.loc[days], app.loc[days]
    V = {"days": days, "app": a, "hems": h}

    # ---- HEMS (as used in the report) vs the app, day by day ----------------------------------------
    V["stats"] = pd.DataFrame({QTY[c]: agreement(h[c], a[c]) for c in QTY}).T

    # ---- the alternative: integrate the Sigenergy power sensors ----------------------------------------
    mm = m.copy(); mm.index = mm.index.tz_localize(None)
    folder = DATA / SITE / "influx"
    STATION, INVERTER = prefixes(folder)
    stn_batt = read_entity(folder, STATION + "battery_power").tz_convert("Europe/Dublin").tz_localize(None) * 1000
    grid = pd.date_range(mm.index[0], mm.index[-1], freq="1min")
    stn_batt_m = stn_batt.reindex(stn_batt.index.union(grid)).ffill().reindex(grid).where(mm["alive"].reindex(grid).fillna(False))
    integ = pd.DataFrame({
        "pv": mm["pv_power"] / 60000, "load": mm["load_power"] / 60000,
        "batt_charge": stn_batt_m.clip(lower=0) / 60000, "batt_discharge": (-stn_batt_m).clip(lower=0) / 60000,
        "import": (-mm["grid_power"]).clip(lower=0) / 60000, "export": mm["grid_power"].clip(lower=0) / 60000,
    }).resample("1D").sum(min_count=1).reindex(days)
    V["integ"] = integ
    V["stats_integ"] = pd.DataFrame({QTY[c]: agreement(integ[c], a[c]) for c in QTY}).T

    # ---- which day does the app use? local days vs UTC days -------------------------------------------
    rows = []
    # Solar is zero at midnight, so it cannot tell day definitions apart; battery discharge (the house
    # runs on the battery across midnight) can.
    for label, shift in (("Irish clock days (used)", 0), ("UTC days (1 h earlier)", 60), ("Days shifted 1 h later", -60)):
        x = e15["batt_discharge"].copy(); x.index = x.index.tz_localize(None) - pd.Timedelta(minutes=shift)
        d = x.resample("1D").sum(min_count=90).reindex(days)
        rows.append({"day definition": label, "mae_kwh": float((d - a["batt_discharge"]).abs().mean()), "r": float(d.corr(a["batt_discharge"]))})
    V["daycheck"] = pd.DataFrame(rows).set_index("day definition")

    # ---- grid import three ways ------------------------------------------------------------------------
    imp = pd.DataFrame({
        "frient_counter": h["import"],                                           # used in the report
        "frient_demand": (mm["meter_demand_w"] / 60000).resample("1D").sum(min_count=1).reindex(days),
        "sigen_hems": h["import_sigen"], "sigen_app": a["import"]})
    V["import"] = imp
    V["import_stats"] = pd.DataFrame({
        "frient counter (report) vs app": agreement(imp["frient_counter"], imp["sigen_app"]),
        "frient instantaneous W, integrated, vs app": agreement(imp["frient_demand"], imp["sigen_app"]),
        "Sigenergy grid power in HEMS vs app": agreement(imp["sigen_hems"], imp["sigen_app"]),
        "frient counter vs Sigenergy grid power in HEMS": agreement(imp["frient_counter"], imp["sigen_hems"])}).T

    # ---- sensor resolution: station battery power vs the inverter's own battery sensor, same poll ------------
    inv_b = read_entity(folder, INVERTER + "battery_power")
    stn_b = read_entity(folder, STATION + "battery_power")
    pair = pd.merge_asof(stn_b.rename("stn").to_frame(), inv_b.rename("inv").to_frame(), left_index=True, right_index=True,
                         tolerance=pd.Timedelta("2s"), direction="nearest").dropna()          # written by the same poll
    bands = [-5, -1, -0.5, -0.3, -0.2, -0.1, -0.02, 0.02, 0.1, 0.2, 0.5, 1, 5]
    labels = ["discharge > 1 kW", "discharge 0.5-1", "discharge 0.3-0.5", "discharge 0.2-0.3", "discharge 0.1-0.2", "discharge < 0.1",
              "~0", "charge < 0.1", "charge 0.1-0.2", "charge 0.2-0.5", "charge 0.5-1", "charge > 1 kW"]
    g = pair.groupby(pd.cut(pair["inv"], bands, labels=labels), observed=True)
    band = pd.DataFrame({"readings": g.size(), "inverter_kw": g["inv"].mean(), "station_kw": g["stn"].mean()})
    band["station_vs_inverter"] = band["station_kw"] / band["inverter_kw"].where(band["inverter_kw"].abs() > 0.02) - 1
    V["bands"] = band
    V["pairs"] = len(pair)
    hour = e15.groupby(e15.index.hour)[["load", "load_sensor"]].sum()
    V["load_by_hour"] = (hour["load_sensor"] / hour["load"] - 1).rename("load sensor vs balance")

    # ---- app's own energy balance --------------------------------------------------------------------
    bal = a["pv"] + a["import"] - a["export"] + a["batt_discharge"] - a["batt_charge"]
    V["app_balance"] = (float(bal.sum()), float(a["load"].sum()), float((bal - a["load"]).abs().max()))

    # ---- what it does to the report's headline numbers (same days) ------------------------------------------
    flat = R["flat"]
    def kpis(d):
        n = len(d)
        return {"Solar, kWh": d["pv"].sum(), "House use, kWh": d["load"].sum(), "Bought, kWh": d["import"].sum(),
                "Sold, kWh": d["export"].sum(), "Self-sufficiency (1 - bought / use)": 1 - d["import"].sum() / d["load"].sum(),
                "Self-consumption (1 - sold / solar)": 1 - d["export"].sum() / d["pv"].sum(),
                "Battery full cycles": d["batt_discharge"].sum() / SM.BATTERY_KWH,
                "Battery round trip (out / in)": d["batt_discharge"].sum() / d["batt_charge"].sum(),
                "Net bill on a flat plan, EUR": (d["import"].sum() * flat["day"] - d["export"].sum() * flat["export"]) / 100
                + flat["standing"] / 365 * n + 1.59 * n / 30.4375}
    imp_t = pd.DataFrame({"hems": kpis(h), "app": kpis(a)})
    imp_t["difference"] = imp_t["hems"] - imp_t["app"]
    imp_t["difference_pct"] = imp_t["hems"] / imp_t["app"] - 1
    V["impact"] = imp_t

    mo = pd.concat({"hems": h[list(QTY)].resample("MS").sum(), "app": a[list(QTY)].resample("MS").sum()}, axis=1)
    V["monthly"] = pd.DataFrame({f"{c}_diff_pct": mo[("hems", c)] / mo[("app", c)] - 1 for c in QTY})
    V["monthly"]["days"] = h["load"].resample("MS").count()
    V["worst"] = (h[list(QTY)] - a[list(QTY)]).abs().sum(axis=1).sort_values(ascending=False).head(8)
    return V


def build_workbook(R: dict, V: dict) -> None:
    k, B = R["k"], Book(OUT)
    st, si, imp, im = V["stats"], V["stats_integ"], V["impact"], V["import_stats"]
    days = V["days"]; n = len(days)
    period = f"{days[0]:%d %b %Y} to {days[-1]:%d %b %Y} ({n} days; " + ", ".join(f"{d:%d %b}" for d in k["bad_days"]) + " left out for HEMS data gaps)"
    names = ["Verdict", "App vs HEMS", "Grid import", "Sensor resolution", "Daily", "Method"]
    for nm in names:
        B.wb.add_worksheet(nm)

    # ================================ Daily (data first) ==========================================
    d = pd.concat({"hems": V["hems"][list(QTY)], "app": V["app"][list(QTY)], "sensors": V["integ"][list(QTY)]}, axis=1)
    d.columns = [f"{c}_{src}" for src, c in d.columns]
    order = [f"{c}_{s}" for c in QTY for s in ("hems", "app", "sensors")]
    d = d[order]
    d["frient_demand"] = V["import"]["frient_demand"]
    d.index.name = "Date"
    dh = {f"{c}_{s}": f"{QTY[c]} kWh - {lab}" for c in QTY for s, lab in (("hems", "HEMS (report)"), ("app", "Sigen app"), ("sensors", "power sensors integrated"))}
    dh["frient_demand"] = "Grid import kWh - frient W integrated"
    wd = B.sheet("Daily", "Day by day: the box, the app and the raw power sensors", "kWh per Irish calendar day. 'HEMS (report)' is what the energy report uses.", tab=MUTED, width=12)
    wd.set_row(3, 58)
    d0, d1 = B.table(wd, 3, 0, d, headers=dh, index_fmt="ddd dd mmm", default="0.00")
    wd.freeze_panes(4, 1)
    cols = list(d.columns)
    cat = rng("Daily", d0, 0, d1, 0)
    ser = lambda c, color, label, **kw: {"name": label, "cat": cat, "val": rng("Daily", d0, cols.index(c) + 1, d1, cols.index(c) + 1), "color": color, **kw}

    # ================================ Verdict ===================================================
    ws = B.sheet("Verdict", f"{SITE_LABEL} - how accurate is the energy box?",
                 f"{period}. The figures in {SITE}_Energy_Report.xlsx checked against the daily totals in the Sigenergy app (the same system's meters, through the "
                 "vendor's cloud) and, for grid import, against the frient reader on the ESB meter's LED (independent hardware). There is no ESB half-hourly file "
                 "for this house yet.", tab=LOAD, width=10.7, first_col_width=2)
    ws.set_row(3, 20); ws.set_row(4, 36); ws.set_row(5, 32)
    B.tile(ws, 3, 1, "Solar, box vs app", st.loc["Solar", "total_diff_pct"], f"{st.loc['Solar', 'test_total']:,.1f} vs {st.loc['Solar', 'ref_total']:,.1f} kWh", PCT)
    B.tile(ws, 3, 4, "House use, box vs app", st.loc["House use", "total_diff_pct"], f"{st.loc['House use', 'test_total']:,.0f} vs {st.loc['House use', 'ref_total']:,.0f} kWh", PCT)
    B.tile(ws, 3, 7, "Grid export, box vs app", st.loc["Grid export", "total_diff_pct"], f"{st.loc['Grid export', 'test_total']:,.0f} vs {st.loc['Grid export', 'ref_total']:,.0f} kWh", PCT)
    B.tile(ws, 3, 10, "Grid import, meter reader vs app", st.loc["Grid import", "total_diff_pct"],
           f"{st.loc['Grid import', 'test_total']:,.1f} vs {st.loc['Grid import', 'ref_total']:,.1f} kWh - a {st.loc['Grid import', 'ref_total'] - st.loc['Grid import', 'test_total']:.1f} kWh gap", PCT)
    worst_c = st["total_diff_pct"].drop("Grid import").abs().idxmax()
    bill = imp.loc["Net bill on a flat plan, EUR"]
    lines = [
        ("Verdict", f"The energy report's figures agree with the Sigenergy app. Over {n} days solar, battery charge and battery discharge match to within "
         f"{st.loc[['Solar', 'Battery charge', 'Battery discharge'], 'total_diff_pct'].abs().max():.1%}, house use to {abs(st.loc['House use', 'total_diff_pct']):.1%} and export to "
         f"{abs(st.loc['Grid export', 'total_diff_pct']):.1%}. Day by day the typical error is {st.loc['House use', 'mae']:.2f} kWh on use and {st.loc['Grid export', 'mae']:.2f} kWh on export. "
         f"Recomputed from the app, self-sufficiency is {imp.loc['Self-sufficiency (1 - bought / use)', 'app']:.1%} (report {imp.loc['Self-sufficiency (1 - bought / use)', 'hems']:.1%}) and the "
         f"flat-plan bill differs by {eur(abs(bill['difference']), 2)}. No conclusion in the energy report changes."),
        ("Grid import: two meters, 1 kWh apart", f"The report takes import from the frient reader on the ESB meter, which is independent of Sigenergy. It reads "
         f"{abs(st.loc['Grid import', 'total_diff_pct']):.1%} less than the app ({st.loc['Grid import', 'test_total']:.1f} vs {st.loc['Grid import', 'ref_total']:.1f} kWh), and the Sigenergy "
         f"grid reading stored by the box is {im.loc['Sigenergy grid power in HEMS vs app', 'total_diff_pct']:+.1%}. On {st.loc['Grid import', 'ref_total'] / n:.2f} kWh a day that is about "
         f"{(st.loc['Grid import', 'ref_total'] - st.loc['Grid import', 'test_total']) / n * 1000:.0f} Wh a day - below what matters for any figure in the report. Which of the two is right "
         "can only be settled by the ESB meter's own half-hourly file (request it from ESB Networks with the MPRN, as for MartinH)."),
        ("Why counters, not power sensors", f"Integrating the Sigenergy power sensors instead gives house use {si.loc['House use', 'total_diff_pct']:+.1%} and battery discharge "
         f"{si.loc['Battery discharge', 'total_diff_pct']:+.1%} against the app. The station-level sensors have a dead band at low power: a 0.17 kW battery discharge reads as "
         f"{-V['bands'].loc['discharge 0.1-0.2', 'station_kw']:.2f} kW and 0.26 kW as {-V['bands'].loc['discharge 0.2-0.3', 'station_kw']:.2f} kW, while above 0.5 kW they agree within ~5 % (see Sensor resolution). "
         "At night the house runs on ~0.3 kW, right in that band. The report therefore uses the energy counters and the energy balance, and the power sensors only for shapes and peaks."),
        ("Two data glitches handled", "The Sigenergy PV day counter returned 58 single readings at about 1/100 of the true value (32.48 -> 0.33 -> 32.82) and, on 3 Sep at 03:34, "
         "yesterday's total twice. A 5-reading rolling median removes them; without it the counter method would have been badly wrong on those days."),
        ("Days line up", f"The app's days are Irish calendar days: battery discharge (which runs across midnight) agrees to {V['daycheck'].iloc[0]['mae_kwh']:.2f} kWh a day on local days "
         f"against {V['daycheck'].iloc[1]['mae_kwh']:.2f} kWh on UTC days. The app's own figures balance exactly (use = solar + import - export + battery out - battery in, "
         f"worst day {V['app_balance'][2]:.2f} kWh), so the app's 'use' is itself a balance - the same definition the report uses."),
        ("What this check cannot do", "The app reads the same Sigenergy meters, so it confirms the box's data path and methods, not the sensors' absolute accuracy. "
         "Only grid import has an independent reading (frient). Two days with HEMS gaps (5 Aug, box/Home Assistant outage 04:27-15:52; 7 Sep, ~2 h of gaps) are not compared."),
    ]
    r = 7
    for head, body in lines:
        ws.merge_range(r, 1, r, 3, head, B.f(bold=True, text_wrap=True, valign="top"))
        ws.merge_range(r, 4, r, 15, body, B.f(text_wrap=True, valign="top"))
        ws.set_row(r, 15 * max(2, len(body) // 105 + 1)); r += 1
    r += 1
    B.heading(ws, r, "Headline figures of the energy report, recomputed from the Sigen app (same days)", col=1); r += 1
    head = B.f(bold=True, font_color="#52514e", bottom=1, bottom_color="#c3c2b7")
    ws.merge_range(r, 1, r, 5, "Measure", head)
    for j, hname in enumerate(["Energy box", "Sigen app", "Difference", "Difference %"]):
        ws.merge_range(r, 6 + 2 * j, r, 7 + 2 * j, hname, head)
    for meas, row_ in imp.iterrows():
        r += 1
        pct = "Self" in meas or "round trip" in meas
        nf = "0.0%" if pct else ('"€"#,##0.00' if "EUR" in meas else "#,##0.0")
        ws.merge_range(r, 1, r, 5, meas, B.f())
        for j, v in enumerate([row_["hems"], row_["app"], row_["difference"]]):
            ws.merge_range(r, 6 + 2 * j, r, 7 + 2 * j, float(v), B.f(num_format=("+0.0%;-0.0%" if (pct and j == 2) else nf)))
        ws.merge_range(r, 12, r, 13, float(row_["difference_pct"]) if not pct else "", B.f(num_format=PCT))

    # ================================ App vs HEMS ================================================
    wa = B.sheet("App vs HEMS", "Every quantity, day by day",
                 f"Top table: what the report uses. Bottom table: what you would get by integrating the Sigenergy power sensors. {n} days.", tab=PV, width=11, first_col_width=26)
    t = st.copy(); t.index.name = "Report method vs Sigen app (daily kWh)"
    wa.set_row(3, 45)
    _, r1 = B.table(wa, 3, 0, t, fmts=STAT_FMTS, headers=STAT_HEADERS)
    t2 = si.copy(); t2.index.name = "Power sensors integrated vs Sigen app"
    wa.set_row(r1 + 2, 45)
    _, r2 = B.table(wa, r1 + 2, 0, t2, fmts=STAT_FMTS, headers=STAT_HEADERS)
    mo = V["monthly"].copy(); mo.index = [f"{x:%b %Y}" for x in mo.index]; mo.index.name = "Month (report vs app)"
    wa.set_row(r2 + 2, 45)
    _, r3 = B.table(wa, r2 + 2, 0, mo, default="+0.0%;-0.0%", fmts={"days": "0"}, headers={**{f"{c}_diff_pct": QTY[c] for c in QTY}, "days": "Days"})
    c0 = r3 + 3
    size = (720, 300)
    B.chart(wa, f"A{c0}", "line", "Solar each day (kWh): box vs app", [ser("pv_hems", PV, "Box"), ser("pv_app", MUTED, "App", width=1.25)], size=size, x_num_fmt="d mmm", x_interval=7)
    B.chart(wa, f"H{c0}", "line", "House use each day (kWh): box (balance), app, and load sensor",
            [ser("load_hems", LOAD, "Box (energy balance)"), ser("load_app", MUTED, "App", width=1.25), ser("load_sensors", IMPORT, "load_power sensor", width=1.25)],
            size=size, x_num_fmt="d mmm", x_interval=7)
    B.chart(wa, f"A{c0 + 16}", "line", "Grid export each day (kWh): box vs app", [ser("export_hems", EXPORT, "Box"), ser("export_app", MUTED, "App", width=1.25)],
            size=size, x_num_fmt="d mmm", x_interval=7)
    B.chart(wa, f"H{c0 + 16}", "line", "Battery discharge each day (kWh): counter, app, station power sensor",
            [ser("batt_discharge_hems", BATT, "Box (counter)"), ser("batt_discharge_app", MUTED, "App", width=1.25), ser("batt_discharge_sensors", IMPORT, "Station power sensor", width=1.25)],
            size=size, x_num_fmt="d mmm", x_interval=7)

    # ================================ Grid import ================================================
    wg = B.sheet("Grid import", "Grid import, measured three ways",
                 "The frient reader counts the ESB meter's LED pulses (independent of Sigenergy); the Sigenergy system measures grid flow with its own meter; the app reports that meter's "
                 f"daily total. All are tiny here: {V['import']['sigen_app'].sum():.1f} kWh in {n} days, most of it on the few nights the battery ran empty.", tab=IMPORT, width=12, first_col_width=40)
    gi = im.copy(); gi.index.name = "Daily import compared"
    wg.set_row(3, 45)
    _, g1 = B.table(wg, 3, 0, gi, fmts=STAT_FMTS, headers=STAT_HEADERS)
    B.chart(wg, f"A{g1 + 3}", "column", "Grid import each day (kWh)",
            [ser("import_hems", IMPORT, "frient meter reader (report)", gap=40), ser("import_app", MUTED, "Sigen app"), ser("import_sensors", LOAD, "Sigenergy grid power (box)")],
            size=(1200, 340), x_num_fmt="d mmm", x_interval=7)
    B.note(wg, g1 + 22, "Reading it: on days with real import all three agree closely; the app is consistently a little higher. With about 0.3 kWh a day the gap is worth a few cent a month. "
           "To know which meter is right, compare both with the ESB half-hourly file for this MPRN.", height=40, last_col=12)

    # ================================ Sensor resolution ===========================================
    wr = B.sheet("Sensor resolution", "Why the Sigenergy power sensors under-read",
                 f"The same poll writes two battery readings: the station's and the inverter's own sensor. Compared over {V['pairs']:,} polls, the station value shrinks small flows towards zero - "
                 "a dead band below about 0.3 kW - and agrees within ~5 % above 0.5 kW. The station load sensor behaves the same way: it reads 20-38 % low at night (house on ~0.3 kW) and "
                 "about right in the day. Integrated over the summer that is ~8 % of house use and ~16 % of battery discharge.", tab=BATT, width=13, first_col_width=26)
    bd = V["bands"].copy(); bd.index = bd.index.astype(str); bd.index.name = "Inverter sensor reads"
    wr.set_row(3, 45)
    b0, b1 = B.table(wr, 3, 0, bd, default="0.000", fmts={"readings": "#,##0", "station_vs_inverter": "+0%;-0%"},
                     headers={"readings": "Polls", "inverter_kw": "Inverter sensor kW (mean)", "station_kw": "Station sensor kW (mean)", "station_vs_inverter": "Station vs inverter"})
    lh = V["load_by_hour"].to_frame("diff"); lh.index.name = "Hour of day"
    wr.set_row(b1 + 2, 45)
    l0, l1 = B.table(wr, b1 + 2, 0, lh, default="+0%;-0%", headers={"diff": "Load sensor vs energy balance"})
    B.chart(wr, "G4", "column", "Load sensor vs energy balance, by hour of day (under-reads at night)",
            [{"name": "Difference", "cat": rng("Sensor resolution", l0, 0, l1, 0), "val": rng("Sensor resolution", l0, 1, l1, 1), "color": LOAD, "gap": 30}],
            y_fmt="0%", legend=False)

    # ================================ Method ======================================================
    wm = B.sheet("Method", "How the comparison was made", "Code: ems/analysis/stewart_validation.py (reuses sigen_data.py and stewart_metrics.py).", tab=MUTED, width=16, first_col_width=48)
    rows = [
        "Days: Irish calendar days present in both the app export (1 Aug - 24 Sep 2026; the last, incomplete day dropped) and the HEMS data with at least 95 % coverage.",
        "HEMS (report) method: solar and battery from the Sigenergy daily counters; import from the frient summation counter; export by integrating Sigenergy grid power (1-min hold-last-value); "
        "house use = solar + import - export + battery out - battery in, per 15 min, with dips absorbed by a running maximum of the cumulative balance.",
        "Statistics: 'Mean error' = HEMS - reference, per day; 'Slope' from a least-squares fit of HEMS on reference. Correlation is low for quantities that barely vary from day to day (house use) even when the error is small.",
        "Bill check: flat plan (Electric Ireland Saver 24h rates) computed from daily totals, which is exact for a flat unit rate.",
    ]
    for i, text in enumerate(rows):
        B.note(wm, 3 + i, text, height=46, last_col=8)
    dc = V["daycheck"].copy()
    B.table(wm, 9, 0, dc, default="0.000", headers={"mae_kwh": "Battery discharge MAE kWh/day", "r": "Correlation"})

    B.wb.get_worksheet_by_name("Verdict").activate()
    B.wb.set_properties({"title": f"{SITE_LABEL} - HEMS validation", "subject": "SmartCORE HEMS", "comments": "Generated by ems/analysis/stewart_validation.py"})
    B.wb.close()


def main() -> None:
    cache = DATA / f".cache_{SITE}.pkl"
    if "--refresh" in sys.argv and cache.exists():
        cache.unlink()
    R = SM.build(load_site(DATA / SITE / "influx", cache))
    V = compute(R)
    print(V["stats"][["test_total", "ref_total", "total_diff_pct", "mae"]].round(3))
    build_workbook(R, V)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
