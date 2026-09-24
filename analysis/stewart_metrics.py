"""Tables and headline numbers for the Stewart House (Sigenergy) report.

The MartinH equivalent is metrics.py; this site has a metered battery, no EV and almost no grid
import, so the questions (and the code) differ. Energy comes from sigen_data.energy15 (counters and
energy balance); the 1-min power signals are only used for shapes, peaks and power quality.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import tariffs as T
from validation import bill

BATTERY_KWH = 9.04          # battery_stored_energy at 100 % SoC (constant 9.02-9.06 kWh in the data)
SOC_EMPTY = 1.0             # SoC at or below this = battery empty (the system runs it down to 0 %)
SOC_FULL = 99.0
AC_LIMIT_W = 5400           # inverter AC output piles up at 5.4-5.5 kW (58 h): its ceiling
MIN_COVERAGE = 0.95         # days with less HEMS data than this are left out of daily figures


def _hh(index) -> np.ndarray:
    return index.hour * 2 + index.minute // 30


def build(site: dict) -> dict:
    minute, e15_all = site["minute"], site["energy15"]
    first = (minute.index[0] + pd.Timedelta(days=1)).normalize()
    last = minute.index[-1].normalize()
    m = minute[(minute.index >= first) & (minute.index < last)].copy()
    m[m.columns.drop("alive")] = m[m.columns.drop("alive")].astype("float64")
    e15 = e15_all[(e15_all.index >= first) & (e15_all.index < last)].astype("float64")
    e15["self_used_pv"] = (e15["pv"] - e15["export"]).clip(lower=0)
    e = e15.resample("30min").sum(min_count=2)
    days_all = int((last - first).days)

    # ---- daily / monthly ------------------------------------------------------------------------
    daily = e15.resample("1D").sum(min_count=1)
    daily["coverage"] = m["alive"].resample("1D").mean()
    good = daily["coverage"] >= MIN_COVERAGE
    daily.loc[~good, daily.columns.drop("coverage")] = np.nan
    daily["self_consumption"] = 1 - daily["export"] / daily["pv"].where(daily["pv"] > 0.2)
    daily["self_sufficiency"] = 1 - daily["import"] / daily["load"]
    soc = m["soc"]
    daily["soc_min"] = soc.resample("1D").min()
    daily["soc_max"] = soc.resample("1D").max()
    daily["hours_full"] = (soc >= SOC_FULL).resample("1D").sum() / 60
    daily["hours_empty"] = (soc <= SOC_EMPTY).resample("1D").sum() / 60
    daily["peak_load_kw"] = m["load_power"].resample("1D").max() / 1000
    daily["peak_import_kw"] = m["meter_demand_w"].resample("1D").max() / 1000
    daily["v_max"] = m["voltage"].resample("1D").max()
    daily.loc[~good, ["soc_min", "soc_max", "hours_full", "hours_empty", "peak_load_kw", "peak_import_kw", "v_max"]] = np.nan
    days = int(good.sum())

    # Totals: whole good days only, so every figure below is over the same days.
    eg = e15[good.reindex(e15.index.normalize()).fillna(False).to_numpy()]
    tot = eg.sum()
    k = {"days": days, "days_all": days_all, "start": first, "end": last - pd.Timedelta(days=1),
         "bad_days": [d for d in daily.index[~good]]}
    for c in ["import", "export", "load", "pv", "batt_charge", "batt_discharge", "self_used_pv", "import_sigen", "load_sensor"]:
        k[f"{c}_kwh"] = float(tot[c])
    k["self_consumption"] = 1 - tot["export"] / tot["pv"]
    k["self_sufficiency"] = 1 - tot["import"] / tot["load"]
    k["co2_avoided_kg"] = tot["pv"] * T.GRID_CO2_KG_PER_KWH
    k["best_pv_day"] = (daily["pv"].idxmax(), float(daily["pv"].max()))
    k["worst_pv_day"] = (daily["pv"].idxmin(), float(daily["pv"].min()))
    k["max_load_day"] = (daily["load"].idxmax(), float(daily["load"].max()))
    k["max_import_day"] = (daily["import"].idxmax(), float(daily["import"].max()))
    k["days_zero_import"] = int((daily["import"] < 0.05).sum())
    k["days_pv_below_load"] = int((daily["pv"] < daily["load"]).sum())
    k["coverage"] = float(m["alive"].mean())

    monthly = daily[["import", "export", "load", "pv", "batt_charge", "batt_discharge", "self_used_pv"]].resample("MS").sum()
    monthly["days"] = daily["load"].resample("MS").count()
    monthly["self_consumption"] = 1 - monthly["export"] / monthly["pv"]
    monthly["self_sufficiency"] = 1 - monthly["import"] / monthly["load"]
    monthly["pv_per_day"] = monthly["pv"] / monthly["days"]
    monthly["load_per_day"] = monthly["load"] / monthly["days"]

    # ---- average day (48 half-hours), from the energy series so it adds up to the totals ------------
    eh = e[good.reindex(e.index.normalize()).fillna(False).to_numpy()]
    g = _hh(eh.index)
    prof = pd.DataFrame({c + "_kw": eh[c].groupby(g).mean() * 2 for c in
                         ["load", "pv", "import", "export", "batt_charge", "batt_discharge"]})
    prof["battery_net_kw"] = prof["batt_discharge_kw"] - prof["batt_charge_kw"]
    mg = m[m["alive"]]
    prof["soc_pct"] = mg["soc"].groupby(_hh(mg.index)).mean()
    wk = eh.index.dayofweek >= 5
    prof["load_weekday_kw"] = eh["load"][~wk].groupby(g[~wk]).mean() * 2
    prof["load_weekend_kw"] = eh["load"][wk].groupby(g[wk]).mean() * 2
    months = sorted(set(eh.index.strftime("%Y-%m")))
    for mo in months:
        sel = eh.index.strftime("%Y-%m") == mo
        prof[f"pv_{mo}"] = eh["pv"][sel].groupby(g[sel]).mean() * 2
    prof.index = [f"{i // 2:02d}:{(i % 2) * 30:02d}" for i in prof.index]

    # ---- battery -------------------------------------------------------------------------------------
    k["batt_kwh"] = BATTERY_KWH
    k["batt_cycles"] = k["batt_discharge_kwh"] / BATTERY_KWH
    k["batt_cycles_per_day"] = k["batt_cycles"] / days
    k["batt_rte"] = k["batt_discharge_kwh"] / k["batt_charge_kwh"]
    k["batt_max_charge_kw"] = float(m["battery_power"].max() / 1000)
    k["batt_max_discharge_kw"] = float(-m["battery_power"].min() / 1000)
    k["batt_days_full"] = int((daily["soc_max"] >= SOC_FULL).sum())
    k["batt_days_empty"] = int((daily["hours_empty"] > 0).sum())
    k["batt_hours_empty_per_day"] = float(daily["hours_empty"].mean())
    k["batt_hours_full_per_day"] = float(daily["hours_full"].mean())
    # Morning state: SoC at 07:00 local, the typical end of the night.
    at7 = soc[(soc.index.hour == 7) & (soc.index.minute == 0)]
    k["soc_07_median"] = float(at7.median())
    # When the battery ran empty, how much was imported while empty?
    empty = (soc <= SOC_EMPTY)
    imp_min = (m["meter_demand_w"] / 60000)
    k["import_while_empty_kwh"] = float(imp_min[empty].sum())
    k["import_while_empty_share"] = k["import_while_empty_kwh"] / float(imp_min.sum())
    # Would a bigger battery have helped? Energy still exported on days that later ran empty.
    runs_empty = daily["hours_empty"] > 0
    k["export_on_empty_days_kwh"] = float(daily["export"][runs_empty].sum())
    soc_hist = pd.DataFrame({"share_of_time": np.histogram(soc.dropna(), bins=np.arange(0, 110, 10))[0] / soc.notna().sum()},
                            index=[f"{a}-{a + 10} %" for a in range(0, 100, 10)])
    empty_by_hour = (soc <= SOC_EMPTY).groupby(soc.index.hour).mean().reindex(range(24), fill_value=0)

    # ---- tariffs -------------------------------------------------------------------------------------
    # Plan not known. Import is so small that the bill is standing charge minus export credit.
    tprof = pd.DataFrame({
        "import_all": eh["import"].groupby(_hh(eh.index)).sum(),
        "import_sat_free": eh["import"].where((eh.index.dayofweek == 5) & (eh.index.hour >= 8) & (eh.index.hour < 23), 0).groupby(_hh(eh.index)).sum(),
        "export_all": eh["export"].groupby(_hh(eh.index)).sum(),
        "load_all": eh["load"].groupby(_hh(eh.index)).sum(),
    }).reindex(range(48), fill_value=0.0)
    plans = []
    for p in T.PLANS:
        c = bill(eh["import"].dropna(), eh["export"].dropna(), p, days)
        no_sys = bill(eh["load"].dropna(), eh["export"].dropna() * 0, p, days)
        plans.append({**p, **c, "annual": c["net"] * 365 / days, "net_without_system": no_sys["net"]})
    plans.sort(key=lambda p: p["net"])
    best, worst = plans[0], plans[-1]
    flat = next(p for p in plans if p["key"] == "ei_flat")
    k["best_plan"] = best
    k["plan_spread_eur"] = worst["net"] - best["net"]
    k["bill_flat"] = flat["net"]
    k["bill_flat_no_system"] = flat["net_without_system"]
    k["system_saving_flat"] = flat["net_without_system"] - flat["net"]
    k["import_cost_flat"] = flat["import_cost"]
    k["export_credit_flat"] = flat["export_credit"]
    k["export_income_year"] = k["export_kwh"] / days * 365 * flat["export"] / 100
    k["export_kwh_year_summer_rate"] = k["export_kwh"] / days * 365

    # ---- baseload, peaks ---------------------------------------------------------------------------
    load15_kw = eg["load"] * 4
    k["baseload_w"] = float(load15_kw.quantile(0.10) * 1000)
    k["median_load_w"] = float(load15_kw.median() * 1000)
    k["baseload_kwh_year"] = k["baseload_w"] * 8.76
    k["baseload_eur_year"] = k["baseload_kwh_year"] * flat["day"] / 100
    k["peak_load_kw"] = float(m["load_power"].max() / 1000)
    k["peak_import_kw"] = float(m["meter_demand_w"].max() / 1000)
    pct = np.r_[0, 0.1, 0.25, 0.5, np.arange(1, 100, 1), 100]
    duration = pd.DataFrame({"pct_of_time": pct, "load_kw": np.percentile(load15_kw.dropna(), 100 - pct)})
    imp = m["meter_demand_w"]
    peaks = (imp.resample("1D").agg(["idxmax", "max"]).dropna().sort_values("max", ascending=False)
             .head(10).rename(columns={"idxmax": "when", "max": "import_w"}))
    peaks["soc_pct"] = soc.reindex(peaks["when"]).values
    peaks["pv_w"] = m["pv_power"].reindex(peaks["when"]).values
    peaks["load_w_sensor"] = m["load_power"].reindex(peaks["when"]).values
    # Import by hour of day: when does this house still buy?
    imp_by_hour = (eh["import"].groupby(eh.index.hour).sum()).reindex(range(24), fill_value=0)

    # A ~3.3 kW load switches on at ~13:35 most days (immersion on a timer, most likely).
    big = m["load_power"] > 2500
    k["timer_days"] = int((big.between_time("13:30", "15:30").resample("1D").sum()[good] > 30).sum())

    # ---- solar & power quality -------------------------------------------------------------------
    k["pv_peak_kw"] = float(m["pv_power"].max() / 1000)
    k["ac_peak_kw"] = float(m["ac_power"].max() / 1000)
    k["export_peak_kw"] = float(m["grid_power"].max() / 1000)
    # At the AC ceiling with the battery full, any extra solar has nowhere to go: likely curtailed.
    at_ceiling = m["ac_power"] >= AC_LIMIT_W
    k["ac_ceiling_hours"] = float(at_ceiling.sum() / 60)
    k["curtail_hours"] = float((at_ceiling & (m["soc"] >= SOC_FULL)).sum() / 60)
    k["curtail_days"] = int(((at_ceiling & (m["soc"] >= SOC_FULL)).resample("1D").sum() > 15).sum())
    daily["hours_at_ac_ceiling"] = at_ceiling.resample("1D").sum() / 60
    v = m["voltage"].dropna()
    k["v_mean"], k["v_max"], k["v_min"] = float(v.mean()), float(v.max()), float(v.min())
    k["v_over_253_min"] = int((v > 253).sum())
    k["v_over_248_min"] = int((v > 248).sum())
    vbins = np.arange(228, 256, 2)
    vhist = pd.DataFrame({"volts_from": vbins[:-1], "share_of_time": np.histogram(v, bins=vbins)[0] / len(v)})
    f = m["frequency"].dropna()
    k["f_mean"], k["f_min"], k["f_max"] = float(f.mean()), float(f.min()), float(f.max())
    # Voltage rises with export: mean voltage by export band.
    vb = pd.cut(m["grid_power"] / 1000, [-6, -0.05, 0.05, 1, 2, 3, 4, 6],
                labels=["importing", "~0", "0-1 kW", "1-2 kW", "2-3 kW", "3-4 kW", "4+ kW"])
    v_by_export = m.groupby(vb, observed=False)["voltage"].mean().to_frame("mean_voltage")

    # ---- heatmaps --------------------------------------------------------------------------------
    def heat(series, agg="sum"):
        t = series.to_frame("v")
        t["d"], t["h"] = t.index.normalize().tz_localize(None), _hh(t.index)
        return t.pivot_table(index="d", columns="h", values="v", aggfunc=agg).reindex(columns=range(48))
    heatmaps = {c: heat(e[c]) for c in ["load", "pv", "export", "import"]}
    heatmaps["soc"] = heat(soc.resample("30min").mean(), "mean")

    # ---- data quality ------------------------------------------------------------------------------
    gaps = (~minute["alive"]).astype(int)
    grp = (gaps != gaps.shift()).cumsum()[gaps == 1]
    gap_table = pd.DataFrame([{"from": i[0], "to": i[-1], "minutes": len(i)} for i in grp.groupby(grp).groups.values()])
    recon = pd.DataFrame({
        "used_in_report": [k["import_kwh"], k["load_kwh"], k["batt_discharge_kwh"]],
        "sigenergy_power_sensor": [k["import_sigen_kwh"], k["load_sensor_kwh"],
                                   float((-m["battery_power"]).clip(lower=0).sum() / 60000)],
    }, index=["Grid import (frient meter reader vs Sigenergy grid power)", "House load (energy balance vs load_power sensor)",
              "Battery discharge (counter vs integrated inverter battery power)"])
    recon["difference_pct"] = recon["sigenergy_power_sensor"] / recon["used_in_report"] - 1

    data15 = e15.join(m.drop(columns="alive").resample("15min").mean(), how="left")
    return dict(k=k, m=m, e=e, e15=e15, data15=data15, daily=daily, monthly=monthly, prof=prof, tprof=tprof,
                plans=plans, flat=flat, best=best, soc_hist=soc_hist, empty_by_hour=empty_by_hour,
                duration=duration, peaks=peaks, imp_by_hour=imp_by_hour, vhist=vhist, v_by_export=v_by_export,
                heatmaps=heatmaps, gap_table=gap_table, recon=recon, months=months,
                lifetime=site["lifetime"], inventory=site["inventory"], mode=site["mode"],
                prefixes=site["prefixes"])
