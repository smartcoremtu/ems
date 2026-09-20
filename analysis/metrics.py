"""Turn the cleaned HEMS frames into the tables and headline numbers the report shows."""
from __future__ import annotations

import numpy as np
import pandas as pd

import tariffs as T

EV_LOAD_W = 5000        # house load above this = the 7.4 kW EV charger is running
EV_GAP_MIN = 10         # merge EV minutes closer than this into one session
HOUSE_BASE_W = 350      # typical non-EV load, subtracted to estimate EV-only energy
PV_KWP_EST = 4.4        # estimated array size (peak DC seen: 4.3 kW) - edit if known
INVERTER_AC_LIMIT_W = 3950
EV_KWH_PER_100KM = 17.0
PETROL_EUR_PER_100KM = 6.0 * 1.75   # 6 L/100 km at EUR 1.75/L
IMPORT_LIMIT_KW = 12.0  # standard domestic MIC is 12 kVA
CHARGE_KW = 9.9         # EV charger 7.4 kW + battery 2.5 kW: most he can pull into a cheap window


def _hh(index) -> np.ndarray:
    return index.hour * 2 + index.minute // 30


def build(site: dict) -> dict:
    minute, e_all = site["minute"], site["energy30"]

    # Whole local days only.
    first = (minute.index[0] + pd.Timedelta(days=1)).normalize()
    last = minute.index[-1].normalize()
    m = minute[(minute.index >= first) & (minute.index < last)].copy()
    num = m.columns.drop("alive")
    m[num] = m[num].astype("float64")
    e = e_all[(e_all.index >= first) & (e_all.index < last)].astype("float64")   # labelled by half-hour start
    days = int((last - first).days)

    # Battery power is not metered: infer it from the inverter's AC output, its losses and PV DC input.
    m["battery_est"] = m["power"] + m["power_losses"].fillna(0) - m["pv_power"]   # + = discharging
    m["import_w"] = (-m["grid_power"]).clip(lower=0)
    m["export_w"] = m["grid_power"].clip(lower=0)
    m["ev_on"] = (m["load_power"] > EV_LOAD_W).fillna(False).astype(bool)

    e["self_used_pv"] = (e["pv"] - e["export"]).clip(lower=0)
    half = m.resample("30min").mean(numeric_only=True)
    half["l1_voltage_max"] = m["l1_voltage"].resample("30min").max()
    data30 = e.join(half.drop(columns=["alive", "ev_on"]), how="left")

    tot = e.sum()
    k = {"days": days, "start": first, "end": last - pd.Timedelta(days=1)}
    k.update({f"{c}_kwh": float(tot[c]) for c in ["import", "export", "load", "pv", "losses"]})
    k["self_consumption"] = 1 - tot["export"] / tot["pv"]
    k["self_sufficiency"] = 1 - tot["import"] / tot["load"]
    k["co2_avoided_kg"] = tot["pv"] * T.GRID_CO2_KG_PER_KWH

    # ---- daily / monthly ------------------------------------------------------------------
    daily = e.resample("1D").sum()
    daily["self_consumption"] = 1 - daily["export"] / daily["pv"].where(daily["pv"] > 0.2)
    daily["self_sufficiency"] = 1 - daily["import"] / daily["load"]
    daily["peak_load_kw"] = m["load_power"].resample("1D").max() / 1000
    daily["peak_import_kw"] = m["import_w"].resample("1D").max() / 1000
    daily["ev_kwh"] = ((m["load_power"] - HOUSE_BASE_W).where(m["ev_on"], 0)).resample("1D").sum() / 60000
    daily["house_kwh"] = daily["load"] - daily["ev_kwh"]
    daily["batt_charge_grid"] = (-m["battery_est"].where(m["pv_power"] < 5).clip(upper=0)).resample("1D").sum() / 60000
    daily["batt_charge_pv"] = (-m["battery_est"].where(m["pv_power"] >= 5).clip(upper=0)).resample("1D").sum() / 60000
    daily["batt_discharge"] = m["battery_est"].clip(lower=0).resample("1D").sum() / 60000
    daily["v_max"] = m["l1_voltage"].resample("1D").max()
    daily["coverage"] = m["alive"].resample("1D").mean()

    monthly = daily[["import", "export", "load", "pv", "losses", "self_used_pv", "ev_kwh", "house_kwh",
                     "batt_charge_grid", "batt_charge_pv", "batt_discharge"]].resample("MS").sum()
    monthly["days"] = daily["load"].resample("MS").count()
    monthly["self_consumption"] = 1 - monthly["export"] / monthly["pv"]
    monthly["self_sufficiency"] = 1 - monthly["import"] / monthly["load"]
    monthly["pv_per_day"] = monthly["pv"] / monthly["days"]
    monthly["yield_kwh_per_kwp_day"] = monthly["pv_per_day"] / PV_KWP_EST

    k["ev_kwh"] = float(daily["ev_kwh"].sum())
    k["house_kwh"] = k["load_kwh"] - k["ev_kwh"]
    k["best_pv_day"] = (daily["pv"].idxmax(), float(daily["pv"].max()))
    k["worst_pv_day"] = (daily["pv"].idxmin(), float(daily["pv"].min()))
    k["max_load_day"] = (daily["load"].idxmax(), float(daily["load"].max()))

    # ---- average day (48 half-hours) --------------------------------------------------------
    g = _hh(m.index)
    prof = pd.DataFrame({
        "load_kw": m["load_power"].groupby(g).mean() / 1000,
        "pv_kw": m["pv_power"].groupby(g).mean() / 1000,
        "import_kw": m["import_w"].groupby(g).mean() / 1000,
        "export_kw": m["export_w"].groupby(g).mean() / 1000,
        "batt_discharge_kw": m["battery_est"].clip(lower=0).groupby(g).mean() / 1000,
        "batt_charge_kw": (-m["battery_est"].clip(upper=0)).groupby(g).mean() / 1000,
        "house_kw": m["load_power"].where(~m["ev_on"]).groupby(g).mean() / 1000,
        "pv1_kw": m["pv1_power"].groupby(g).mean() / 1000,
        "pv2_kw": m["pv2_power"].groupby(g).mean() / 1000,
    })
    wk = m.index.dayofweek >= 5
    prof["house_weekday_kw"] = m["load_power"].where(~m["ev_on"])[~wk].groupby(g[~wk]).mean() / 1000
    prof["house_weekend_kw"] = m["load_power"].where(~m["ev_on"])[wk].groupby(g[wk]).mean() / 1000
    months = sorted(set(m.index.strftime("%Y-%m")))
    for mo in months:
        sel = m.index.strftime("%Y-%m") == mo
        prof[f"pv_{mo}"] = m["pv_power"][sel].groupby(g[sel]).mean() / 1000
    prof.index = [f"{i // 2:02d}:{(i % 2) * 30:02d}" for i in prof.index]

    # ---- tariff profile: kWh per half-hour of day -------------------------------------------
    hh = _hh(e.index)
    sat_free = (e.index.dayofweek == 5) & (e.index.hour >= 8) & (e.index.hour < 23)
    tprof = pd.DataFrame({
        "import_all": e["import"].groupby(hh).sum(),
        "import_sat_free": e["import"].where(sat_free, 0).groupby(hh).sum(),
        "export_all": e["export"].groupby(hh).sum(),
        "load_all": e["load"].groupby(hh).sum(),
    }).reindex(range(48), fill_value=0.0)

    def cost(plan, imp=tprof["import_all"], exp_kwh=k["export_kwh"]):
        r = np.array(T.rate_vector(plan))
        imp_cost = float((r * imp.values).sum()) / 100
        if plan.get("free_sat"):
            imp_cost -= float((r * tprof["import_sat_free"].values).sum()) / 100
        standing = plan["standing"] / 365 * days + T.PSO_EUR_PER_MONTH * days / 30.4375
        credit = exp_kwh * plan["export"] / 100
        return {"import_cost": imp_cost, "standing_cost": standing, "export_credit": credit,
                "net": imp_cost + standing - credit}

    # What if Martin re-timed his night charging (EV + battery, ~9.9 kW) into each plan's own window?
    night = e["import"][(e.index.hour >= 2) & (e.index.hour < 6)].resample("1D").sum()

    def adapted_net(plan, c):
        if plan.get("ev") is None:
            return c["net"]
        r = np.array(T.rate_vector(plan))
        hours = (plan["ev_win"][1] - plan["ev_win"][0]) % 24
        cheap = night.clip(upper=hours * CHARGE_KW)
        as_is = float((r[4:12] * tprof["import_all"].values[4:12]).sum()) / 100
        retimed = float((cheap * plan["ev"] + (night - cheap) * r[11]).sum()) / 100
        return c["net"] - as_is + retimed

    plans = []
    for p in T.PLANS:
        c = cost(p)
        plans.append({**p, **c, "annual": c["net"] * 365 / days, "net_adapted": adapted_net(p, c)})
    plans.sort(key=lambda p: p["net"])
    base = next(p for p in plans if p["key"] == T.BASELINE_KEY)
    future = next(p for p in plans if p["key"] == T.FUTURE_KEY)
    k["bill_net"] = base["net"]
    k["bill_import"] = base["import_cost"]
    k["avg_import_c"] = base["import_cost"] / k["import_kwh"] * 100
    # Same house with no PV, no battery: every kWh of load bought when it was used, nothing exported.
    no_pv = cost(base, imp=tprof["load_all"], exp_kwh=0.0)
    k["bill_without_system"] = no_pv["net"]
    k["system_saving"] = no_pv["net"] - base["net"]
    flat = next(p for p in plans if p["key"] == "ei_flat")
    k["flat_plan_net"] = flat["net"]

    # ---- where the expensive (day-rate) imports come from -------------------------------------
    in_win = (m.index.hour >= base["ev_win"][0]) & (m.index.hour < base["ev_win"][1])
    out = m[~in_win]
    h = out.index.hour
    cause = np.select(
        [out["ev_on"].to_numpy(), np.asarray((h >= 17) | (h < 2)), np.asarray(h < 10)],
        ["EV charging outside the cheap window", "Evening / night after the battery ran flat",
         "Morning before the sun is up"], "Daytime: big appliance, more than sun + battery")
    by_cause = (out["import_w"].groupby(cause).sum() / 60000).sort_values(ascending=False)
    day_rate = base["day"]
    expensive = pd.DataFrame({"kwh": by_cause, "eur": by_cause * day_rate / 100,
                              "eur_if_cheap": by_cause * (day_rate - base["ev"]) / 100})
    k["day_import_kwh"] = float(by_cause.sum())
    k["day_import_eur"] = float(by_cause.sum() * day_rate / 100)
    k["day_import_share_kwh"] = k["day_import_kwh"] / (m["import_w"].sum() / 60000)
    k["day_import_share_eur"] = k["day_import_eur"] / base["import_cost"]
    k["window_import_share"] = 1 - k["day_import_share_kwh"]

    # ---- battery ---------------------------------------------------------------------------
    night_charge = daily["batt_charge_grid"]
    k["batt_usable_kwh"] = float(night_charge.quantile(0.95))
    k["batt_charge_kw"] = float((-m["battery_est"]).quantile(0.999) / 1000)
    k["batt_grid_kwh"] = float(daily["batt_charge_grid"].sum())
    k["batt_pv_kwh"] = float(daily["batt_charge_pv"].sum())
    k["batt_discharge_kwh"] = float(daily["batt_discharge"].sum())
    dump = m["battery_est"].clip(lower=0).where(in_win & m["ev_on"], 0)
    k["batt_to_ev_kwh"] = float(dump.sum() / 60000)
    k["batt_to_ev_nights"] = int((dump.resample("1D").sum() / 60000 > 0.2).sum())
    k["value_grid_charge_c"] = base["ev"] / 0.90           # cost per kWh delivered, 90 % round trip
    k["value_pv_charge_c"] = base["export"] / 0.95         # export income given up per kWh delivered
    faults = site["states"]["device_fault"]
    faults = faults[(faults.index >= first) & (faults.index < last)]
    low = faults[faults.str.contains("Battery low", na=False)]
    k["batt_low_events"] = int(len(low))
    k["batt_low_days"] = int(low.index.normalize().nunique())
    low_by_hour = low.groupby(low.index.hour).size().reindex(range(24), fill_value=0)
    fault_table = faults[faults != "OK"].groupby(faults[faults != "OK"]).size().sort_values(ascending=False)

    # ---- EV sessions -------------------------------------------------------------------------
    on = m["ev_on"].fillna(False)
    filled = on.rolling(EV_GAP_MIN, center=True, min_periods=1).max().astype(bool)
    sid = (filled != filled.shift()).cumsum()[filled]
    ev_w = (m["load_power"] - HOUSE_BASE_W).where(on, 0)
    rows = []
    for _, idx in sid.groupby(sid).groups.items():
        idx = idx[on[idx].to_numpy()]          # the gap-filling window pads each end; trim it off
        if len(idx) == 0:
            continue
        idx = m.index[(m.index >= idx[0]) & (m.index <= idx[-1])]
        kwh = ev_w[idx].sum() / 60000
        if kwh < 1:
            continue
        cheap = ev_w[idx][in_win[m.index.get_indexer(idx)]].sum() / 60000
        rows.append({"start": idx[0], "end": idx[-1], "hours": len(idx) / 60, "kwh": kwh,
                     "kwh_cheap_window": cheap, "share_cheap": cheap / kwh,
                     "cost_eur": (cheap * base["ev"] + (kwh - cheap) * day_rate) / 100,
                     "cost_if_all_cheap": kwh * base["ev"] / 100})
    sessions = pd.DataFrame(rows)
    weekly_ev = daily["ev_kwh"].resample("W-SUN").sum()
    k["ev_sessions"] = len(sessions)
    k["ev_kwh_per_week"] = k["ev_kwh"] / days * 7
    k["ev_km_per_week"] = k["ev_kwh_per_week"] / EV_KWH_PER_100KM * 100
    k["ev_cost_eur"] = float(sessions["cost_eur"].sum())
    k["ev_eur_per_100km"] = k["ev_cost_eur"] / (k["ev_kwh"] / EV_KWH_PER_100KM)
    # Headline "wrong-time charging" figures use metered grid import (daytime charges are partly solar).
    k["ev_out_window_kwh"] = float(expensive["kwh"].get("EV charging outside the cheap window", 0.0))
    k["ev_out_window_extra_eur"] = float(expensive["eur_if_cheap"].get("EV charging outside the cheap window", 0.0))
    # Longest stretch with no car charging = household away; its use is the true standby of the house.
    gaps_ev = sessions["start"].diff().dt.days
    if len(sessions) > 1 and gaps_ev.max() >= 7:
        j = gaps_ev.idxmax()
        a0 = sessions["end"].iloc[j - 1].normalize() + pd.Timedelta(days=2)
        a1 = sessions["start"].iloc[j].normalize() - pd.Timedelta(days=2)
        away = daily.loc[a0:a1]
        k["away"] = (a0, a1, float(away["load"].mean()), float(away["load"].mean() / 24 * 1000))
    k["ev_share_of_load"] = k["ev_kwh"] / k["load_kwh"]

    # ---- baseload, peaks, load duration ------------------------------------------------------
    load = m["load_power"].dropna()
    k["baseload_w"] = float(load.quantile(0.10))
    k["baseload_kwh_year"] = k["baseload_w"] * 8.76
    k["baseload_eur_year"] = k["baseload_kwh_year"] * flat["day"] / 100
    k["median_load_w"] = float(load.median())
    k["peak_load_kw"] = float(load.max() / 1000)
    k["peak_import_kw"] = float(m["import_w"].max() / 1000)
    k["minutes_over_limit"] = int((m["import_w"] > IMPORT_LIMIT_KW * 1000).sum())
    pct = np.r_[0, 0.1, 0.25, 0.5, np.arange(1, 100, 1), 100]
    duration = pd.DataFrame({"pct_of_time": pct,
                             "load_kw": np.percentile(load, 100 - pct) / 1000,
                             "house_kw": np.percentile(m["load_power"][~m["ev_on"]].dropna(), 100 - pct) / 1000})
    peaks = (m["import_w"].resample("1D").agg(["idxmax", "max"]).dropna().sort_values("max", ascending=False)
             .head(10).rename(columns={"idxmax": "when", "max": "import_w"}))
    peaks["load_w"] = m["load_power"].reindex(peaks["when"]).values
    peaks["battery_charge_w"] = (-m["battery_est"].clip(upper=0)).reindex(peaks["when"]).values

    # ---- solar & power quality ---------------------------------------------------------------
    k["pv_peak_kw"] = float(m["pv_power"].max() / 1000)
    k["pv_kwp_est"] = PV_KWP_EST
    k["yield_kwh_per_kwp"] = k["pv_kwh"] / PV_KWP_EST
    k["clip_minutes"] = int((m["power"] >= INVERTER_AC_LIMIT_W).sum())
    k["clip_days"] = int(((m["power"] >= INVERTER_AC_LIMIT_W).resample("1D").sum() > 5).sum())
    k["pv1_kwh"] = float(m["pv1_power"].sum() / 60000)
    k["pv2_kwh"] = float(m["pv2_power"].sum() / 60000)
    k["loss_share"] = k["losses_kwh"] / k["pv_kwh"]
    v = m["l1_voltage"].dropna()
    k["v_mean"], k["v_max"], k["v_min"] = float(v.mean()), float(v.max()), float(v.min())
    k["v_over_253_min"] = int((v > 253).sum())
    vbins = np.arange(224, 256, 2)
    vhist = pd.DataFrame({"volts_from": vbins[:-1],
                          "share_of_time": np.histogram(v, bins=vbins)[0] / len(v)})
    f = m["frequency"].dropna()
    k["f_mean"], k["f_min"], k["f_max"] = float(f.mean()), float(f.min()), float(f.max())
    states = site["states"]["device_state"].value_counts()

    # ---- heatmaps: day x half-hour -------------------------------------------------------------
    def heat(col):
        t = e[col].to_frame("v")
        t["d"], t["h"] = t.index.normalize().tz_localize(None), _hh(t.index)
        return t.pivot_table(index="d", columns="h", values="v", aggfunc="sum").reindex(columns=range(48))
    heatmaps = {c: heat(c) for c in ["import", "load", "pv", "export"]}

    # ---- data quality --------------------------------------------------------------------------
    gaps = (~minute["alive"]).astype(int)
    grp = (gaps != gaps.shift()).cumsum()[gaps == 1]
    gap_table = pd.DataFrame([{"from": i[0], "to": i[-1], "minutes": len(i)}
                              for i in grp.groupby(grp).groups.values()])
    integ = {"import": m["import_w"].sum() / 60000, "export": m["export_w"].sum() / 60000,
             "load": m["load_power"].sum() / 60000, "pv": m["pv_power"].sum() / 60000}
    recon = pd.DataFrame({
        "whole_export_today_counters": site["energy30"].sum(),
        "whole_export_lifetime_counters": pd.Series(site["totals"]),
    })
    recon["difference_pct"] = recon.iloc[:, 0] / recon.iloc[:, 1] - 1
    recon_power = pd.Series(integ) / tot[list(integ)] - 1
    k["coverage"] = float(m["alive"].mean())

    return dict(k=k, m=m, e=e, data30=data30, daily=daily, monthly=monthly, prof=prof, tprof=tprof,
                plans=plans, base=base, future=future, flat=flat, expensive=expensive,
                low_by_hour=low_by_hour, fault_table=fault_table, sessions=sessions,
                weekly_ev=weekly_ev, duration=duration, peaks=peaks, vhist=vhist, states=states,
                heatmaps=heatmaps, gap_table=gap_table, recon=recon, recon_power=recon_power,
                months=months, lifetime=site["lifetime"], inventory=site["inventory"])
