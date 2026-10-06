"""Check the HEMS measurements against two references that do not pass through the HEMS box:

  ESB smart meter (HDF file)  - the revenue meter. Independent hardware, half-hourly kWh.
                                The only true accuracy reference, but it only sees import/export.
  SolarMan portal export      - the SAME inverter sensors, logged by the vendor cloud every 5 min.
                                Not independent for sensor accuracy; it checks the HEMS data path
                                (polling, write-on-change storage, resampling) and it carries the
                                battery power and SoC that the HEMS export lacks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import tariffs as T

SM_STEP_MIN = 5
SM_MAX_GAP_SLOTS = 3        # SolarMan gaps up to 15 min are interpolated, longer ones are left out
IMPORT_BINS = [0, 0.025, 0.1, 0.5, 1.5, 3.0, 99]   # kWh per half-hour, by the ESB reading


def _hh(index) -> np.ndarray:
    return index.hour * 2 + index.minute // 30


def agreement(test: pd.Series, ref: pd.Series) -> dict:
    """How well `test` reproduces `ref` over the intervals where both exist."""
    j = pd.concat([test.rename("t"), ref.rename("r")], axis=1).dropna()
    d = j["t"] - j["r"]
    slope, intercept = np.polyfit(j["r"], j["t"], 1)
    return {"n": len(j), "test_total": j["t"].sum(), "ref_total": j["r"].sum(),
            "total_diff_pct": j["t"].sum() / j["r"].sum() - 1, "bias": d.mean(), "mae": d.abs().mean(),
            "rmse": float(np.sqrt((d ** 2).mean())), "p95_abs": d.abs().quantile(0.95),
            "r": j["t"].corr(j["r"]), "slope": slope, "intercept": intercept}


def bill(imp30: pd.Series, exp30: pd.Series, plan: dict, days: int) -> dict:
    """Same arithmetic as metrics.build().cost(), on any half-hourly import/export pair."""
    r = np.array(T.rate_vector(plan))
    prof = imp30.groupby(_hh(imp30.index)).sum().reindex(range(48), fill_value=0.0)
    imp_cost = float((r * prof.values).sum()) / 100
    if plan.get("free_sat"):
        free = (imp30.index.dayofweek == 5) & (imp30.index.hour >= 8) & (imp30.index.hour < 23)
        fprof = imp30.where(free, 0).groupby(_hh(imp30.index)).sum().reindex(range(48), fill_value=0.0)
        imp_cost -= float((r * fprof.values).sum()) / 100
    standing = plan["standing"] / 365 * days + T.PSO_EUR_PER_MONTH * days / 30.4375
    credit = float(exp30.sum()) * plan["export"] / 100
    return {"import_cost": imp_cost, "standing_cost": standing, "export_credit": credit,
            "net": imp_cost + standing - credit}


def solar_centroid(power: pd.Series) -> float:
    """Power-weighted mean clock hour. Solar noon at 9.7 W is ~12:40 UTC, so ~12.6 on a UTC/GMT
    clock and ~13.6 on a UTC+1 clock - which tells a winter file's clock apart."""
    p = power.clip(lower=0).dropna()
    h = p.index.hour + p.index.minute / 60
    return float((h * p).sum() / p.sum())


def solarman_halfhours(sm: pd.DataFrame) -> pd.DataFrame:
    """Half-hourly kWh from the 5-min power snapshots (mean power x 0.5 h). The portal often skips
    a 5-min slot; gaps up to SM_MAX_GAP_SLOTS are interpolated, half-hours still incomplete are NaN."""
    w = pd.DataFrame({"import": (-sm["grid_power"]).clip(lower=0), "export": sm["grid_power"].clip(lower=0),
                      "pv": sm["pv_power"], "load": sm["load_power"],
                      "batt_discharge": sm["battery_power"].clip(lower=0),
                      "batt_charge": (-sm["battery_power"]).clip(lower=0)})
    w = w.resample(f"{SM_STEP_MIN}min").mean()
    gap = w["load"].isna()
    run = gap.groupby((~gap).cumsum()).transform("sum")          # length of the gap each slot sits in
    w = w.interpolate(method="time", limit_area="inside").where(~gap | (run <= SM_MAX_GAP_SLOTS))
    g = w.resample("30min")
    return (g.mean() * 0.5 / 1000).where(g["load"].count() == 30 // SM_STEP_MIN)


def battery_capacity(sm: pd.DataFrame) -> dict:
    """Battery size from SoC swing vs metered battery energy, on unbroken 5-min runs."""
    step = sm.index.to_series().diff().dt.total_seconds().div(60)
    ok = step == SM_STEP_MIN
    kwh = (sm["battery_power"].shift() + sm["battery_power"]) / 2 * SM_STEP_MIN / 60000    # + = out of battery
    dsoc = sm["soc"].diff()
    hour = pd.DataFrame({"kwh": kwh.where(ok), "dsoc": dsoc.where(ok)}).resample("1h").agg(["sum", "count"])
    full = hour[hour[("kwh", "count")] == 12]
    e, s = full[("kwh", "sum")], full[("dsoc", "sum")]
    dis, chg = (e > 0.3) & (s <= -3), (e < -0.3) & (s >= 3)
    cap_dis = float(e[dis].sum() / -s[dis].sum() * 100)      # kWh delivered per 100 % SoC
    cap_chg = float(-e[chg].sum() / s[chg].sum() * 100)      # kWh absorbed per 100 % SoC
    return {"kwh_per_100pct_discharging": cap_dis, "kwh_per_100pct_charging": cap_chg,
            "round_trip_efficiency": cap_dis / cap_chg, "soc_min": float(sm["soc"].min()),
            "soc_max": float(sm["soc"].max()), "soc_floor_typical": float(sm["soc"].quantile(0.02)),
            "usable_kwh": cap_dis * (sm["soc"].max() - sm["soc"].quantile(0.02)) / 100,
            "max_charge_w": float(-sm["battery_power"].min()), "max_discharge_w": float(sm["battery_power"].max()),
            "hours_used": int(dis.sum() + chg.sum())}


def build(R: dict, esb: pd.DataFrame, esb_utc: pd.DataFrame, sm: pd.DataFrame, sm_utc: pd.DataFrame) -> dict:
    """R = metrics.build() output. esb / sm = reference data read as Irish clock time; *_utc = the
    same files read as UTC, used only to show which clock they are on."""
    m, e, k, base = R["m"], R["e"], R["k"], R["base"]
    first, last = e.index[0], e.index[-1] + pd.Timedelta(minutes=30)
    V = {"period": (first, last)}

    # ---------------------------------------------------------------- clocks
    e_full = e[["import", "export"]]
    m5 = m[["pv_power", "load_power", "grid_power"]].resample("5min").mean()
    rows = []
    for shift in (-60, -30, 0, 30, 60):
        row = {"shift_min": shift}
        for label, ref in (("ESB read as Irish clock", esb), ("ESB read as UTC", esb_utc)):
            s = ref["import"].copy(); s.index = s.index + pd.Timedelta(minutes=shift)
            row[label] = e_full["import"].corr(s.reindex(e_full.index))
        for label, ref in (("SolarMan read as Irish clock", sm), ("SolarMan read as UTC", sm_utc)):
            s = ref["grid_power"].copy(); s.index = s.index + pd.Timedelta(minutes=shift)
            row[label] = m5["grid_power"].corr(s.reindex(m5.index))
        rows.append(row)
    V["clock"] = pd.DataFrame(rows).set_index("shift_min")
    winter = (sm.index.month == 12) | (sm.index.month == 1)
    summer = (sm.index.month == 6) | (sm.index.month == 7)
    ew, es = (esb.index.month == 12) | (esb.index.month == 1), (esb.index.month == 6) | (esb.index.month == 7)
    V["centroid"] = pd.DataFrame({
        "Dec-Jan (clock = GMT)": [solar_centroid(sm["pv_power"][winter]), solar_centroid(esb["export"][ew])],
        "Jun-Jul (clock = GMT+1)": [solar_centroid(sm["pv_power"][summer]), solar_centroid(esb["export"][es])]},
        index=["SolarMan solar power", "ESB export"])

    # ---------------------------------------------------------------- HEMS vs ESB, half-hourly
    ref = esb.reindex(e.index)
    alive30 = m["alive"].resample("30min").mean().reindex(e.index)
    integ = pd.DataFrame({"import": m["import_w"].resample("30min").sum() / 60000,
                          "export": m["export_w"].resample("30min").sum() / 60000}).reindex(e.index)
    hh = pd.DataFrame({"import_hems": e["import"], "import_esb": ref["import"], "export_hems": e["export"],
                       "export_esb": ref["export"], "import_hems_power": integ["import"],
                       "export_hems_power": integ["export"], "hems_alive": alive30})
    hh["import_diff"] = hh["import_hems"] - hh["import_esb"]
    hh["export_diff"] = hh["export_hems"] - hh["export_esb"]
    V["halfhour"] = hh
    V["esb_missing"] = int(ref["import"].isna().sum())

    stats = {}
    for reg in ("import", "export"):
        stats[f"{reg}: HEMS daily counters (used in report)"] = agreement(hh[f"{reg}_hems"], hh[f"{reg}_esb"])
        stats[f"{reg}: HEMS integrated power"] = agreement(hh[f"{reg}_hems_power"], hh[f"{reg}_esb"])
        good = hh["hems_alive"] == 1
        stats[f"{reg}: counters, half-hours with no HEMS gap"] = agreement(hh[f"{reg}_hems"][good], hh[f"{reg}_esb"][good])
    V["stats_halfhour"] = pd.DataFrame(stats).T

    daily = hh.drop(columns="hems_alive").resample("1D").sum(min_count=48)
    daily["hems_coverage"] = alive30.resample("1D").mean()
    daily["import_diff_pct"] = daily["import_hems"] / daily["import_esb"] - 1
    daily["export_diff_pct"] = daily["export_hems"] / daily["export_esb"].where(daily["export_esb"] > 1) - 1
    V["daily"] = daily
    V["stats_daily"] = pd.DataFrame({reg: agreement(daily[f"{reg}_hems"], daily[f"{reg}_esb"])
                                     for reg in ("import", "export")}).T

    monthly = hh[["import_hems", "import_esb", "export_hems", "export_esb"]].resample("MS").sum()
    monthly["import_diff_pct"] = monthly["import_hems"] / monthly["import_esb"] - 1
    monthly["export_diff_pct"] = monthly["export_hems"] / monthly["export_esb"] - 1
    V["monthly"] = monthly

    g = _hh(hh.index)
    prof = hh[["import_hems", "import_esb", "export_hems", "export_esb"]].groupby(g).sum()
    prof["import_diff"] = prof["import_hems"] - prof["import_esb"]
    prof["export_diff"] = prof["export_hems"] - prof["export_esb"]
    prof.index = [f"{i // 2:02d}:{(i % 2) * 30:02d}" for i in prof.index]
    V["profile"] = prof

    # Where does the difference come from: small standby flows or big charging flows?
    labels = [f"{a:g}-{b:g}" if b < 99 else f"over {a:g}" for a, b in zip(IMPORT_BINS[:-1], IMPORT_BINS[1:])]
    bins = {}
    for reg in ("import", "export"):
        cut = pd.cut(hh[f"{reg}_esb"], IMPORT_BINS, labels=labels, include_lowest=True)
        t = hh.groupby(cut, observed=False)[[f"{reg}_hems", f"{reg}_esb"]].agg(["sum", "count"])
        b = pd.DataFrame({"half_hours": t[(f"{reg}_esb", "count")], "hems_kwh": t[(f"{reg}_hems", "sum")],
                          "esb_kwh": t[(f"{reg}_esb", "sum")]})
        b["diff_kwh"] = b["hems_kwh"] - b["esb_kwh"]
        b["diff_pct"] = b["hems_kwh"] / b["esb_kwh"] - 1
        b["share_of_total_diff"] = b["diff_kwh"] / b["diff_kwh"].sum()
        bins[reg] = b
    V["bins"] = bins

    worst = hh.assign(abs_diff=hh["import_diff"].abs().fillna(0) + hh["export_diff"].abs().fillna(0))
    V["worst"] = worst.sort_values("abs_diff", ascending=False).head(15).drop(columns="abs_diff")

    # ---------------------------------------------------------------- what it does to the report
    days = k["days"]
    paired = hh.dropna(subset=["import_esb", "export_esb"])
    in_win = (paired.index.hour >= base["ev_win"][0]) & (paired.index.hour < base["ev_win"][1])
    b_h = bill(paired["import_hems"], paired["export_hems"], base, days)
    b_e = bill(paired["import_esb"], paired["export_esb"], base, days)
    pv, load = float(e["pv"].sum()), float(e["load"].sum())
    imp_h, imp_e = paired["import_hems"].sum(), paired["import_esb"].sum()
    exp_h, exp_e = paired["export_hems"].sum(), paired["export_esb"].sum()
    # The meter cannot see load or PV. Keeping the HEMS solar figure, the energy balance gives the
    # load the meter implies: what came in + what the roof made - what went out (battery ~net zero).
    rows = [
        ("Bought from the grid, kWh", imp_h, imp_e),
        ("Sold to the grid, kWh", exp_h, exp_e),
        ("Net grid energy (import - export), kWh", imp_h - exp_h, imp_e - exp_e),
        ("Share of import in the 02:00-06:00 window", paired["import_hems"][in_win].sum() / imp_h,
         paired["import_esb"][in_win].sum() / imp_e),
        ("Import cost on current plan, EUR", b_h["import_cost"], b_e["import_cost"]),
        ("Export credit, EUR", b_h["export_credit"], b_e["export_credit"]),
        ("Net electricity bill, EUR", b_h["net"], b_e["net"]),
        ("Self-consumption (1 - export / solar)", 1 - exp_h / pv, 1 - exp_e / pv),
        ("Self-sufficiency (1 - import / load)", 1 - imp_h / load, 1 - imp_e / load),
        ("Highest half-hour import, kW", paired["import_hems"].max() * 2, paired["import_esb"].max() * 2),
        ("Half-hours importing above 12 kW", float((paired["import_hems"] * 2 > 12).sum()),
         float((paired["import_esb"] * 2 > 12).sum())),
    ]
    impact = pd.DataFrame(rows, columns=["measure", "hems", "esb"]).set_index("measure")
    impact["difference"] = impact["hems"] - impact["esb"]
    impact["difference_pct"] = impact["hems"] / impact["esb"] - 1
    V["impact"] = impact
    V["bill_check"] = (b_h["net"], k["bill_net"])

    # ---------------------------------------------------------------- HEMS vs SolarMan (same sensors)
    s5 = sm[(sm.index >= first) & (sm.index < last)]
    # A SolarMan row is a snapshot; the HEMS 5-min mean starting at the same stamp fits it best.
    m5all = m[["pv_power", "power", "load_power", "grid_power", "battery_est"]].resample("5min").mean()
    pairs = [("Solar power vs HEMS solar DC (pv_power)", "pv_power", "pv_power"),
             ("Solar power vs HEMS inverter AC (power)", "power", "pv_power"),
             ("House load", "load_power", "load_power"), ("Grid power (+ export)", "grid_power", "grid_power"),
             ("Battery: HEMS ESTIMATE vs SolarMan metered", "battery_est", "battery_power")]
    V["stats_solarman_5min"] = pd.DataFrame(
        {name: agreement(m5all[h].reindex(s5.index), s5[c]) for name, h, c in pairs}).T

    sm30 = solarman_halfhours(sm)
    V["sm30"] = sm30
    h30 = e[["pv", "load", "import", "export"]].copy()
    h30["batt_discharge"] = m["battery_est"].clip(lower=0).resample("30min").sum() / 60000
    h30["batt_charge"] = (-m["battery_est"].clip(upper=0)).resample("30min").sum() / 60000
    cols = ["pv", "load", "import", "export", "batt_discharge", "batt_charge"]
    both = h30.join(sm30[cols], rsuffix="_solarman", how="inner").dropna()      # matched half-hours only
    V["solarman_halfhours"] = (len(both), len(h30))
    V["stats_solarman_energy"] = pd.DataFrame({c: agreement(both[c], both[f"{c}_solarman"]) for c in cols}).T
    dcmp = both.resample("1D").sum(min_count=48).dropna()                       # days with all 48 matched
    V["solarman_daily"] = dcmp
    V["stats_solarman_daily"] = pd.DataFrame({c: agreement(dcmp[c], dcmp[f"{c}_solarman"]) for c in cols}).T

    cap = battery_capacity(sm)
    cap["report_usable_kwh_estimate"] = k["batt_usable_kwh"]
    cap["report_charge_kw_estimate"] = k["batt_charge_kw"]
    V["battery"] = pd.Series(cap)

    # ---------------------------------------------------------------- inverter CT vs ESB, whole year
    yr = sm30[["import", "export"]].join(esb, rsuffix="_esb", how="inner").dropna()
    ym = yr.resample("MS").sum()
    ym["half_hours"] = yr["import"].resample("MS").count()
    ym["coverage"] = ym["half_hours"] / (ym.index.days_in_month * 48)
    ym["import_diff_pct"] = ym["import"] / ym["import_esb"] - 1
    ym["export_diff_pct"] = ym["export"] / ym["export_esb"].where(ym["export_esb"] > 5) - 1
    V["year_monthly"] = ym
    V["stats_year"] = pd.DataFrame({reg: agreement(yr[reg], yr[f"{reg}_esb"]) for reg in ("import", "export")}).T

    # ---------------------------------------------------------------- the meter's own two years
    em = esb.resample("MS").sum()
    em["half_hours"] = esb["import"].resample("MS").count()
    V["esb_monthly"] = em
    return V
