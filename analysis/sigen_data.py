"""Load and clean a HEMS InfluxDB export from a Sigenergy site (Stewart House).

Same export format as hems_data.py (`_time, field, unit, value`, one .csv.gz per entity), but the
sources differ from the Sofar/SolarMan site:

  <station>_*, <station>_inverter_<id>_*
                                       Sigenergy station + inverter sensors, polled about every
                                       5 min, written on change. Power in kW. HA names them after
                                       the Sigenergy station, which carries the household's name
                                       and address, so the prefixes are read from the export
                                       (prefixes()) and never written into the code.
  frient_a_s_emizb_141_*               frient meter-LED reader on the ESB meter: grid IMPORT only
                                       (summation_delivered kWh, instantaneous_demand W). Independent
                                       of the Sigenergy system.

Findings that shape the choices below (checked against the Sigen app, see stewart_validation.py):
  * Station-level power (`load_power`, `battery_power`) reads small flows far too low - a dead
    band: a 0.17 kW battery discharge (inverter sensor) shows as ~0, 0.26 kW as 0.14; above 0.5 kW
    the two agree within ~5 %. The house draws ~0.3 kW at night, so integrated they under-read house
    load by ~7 % and battery discharge by ~16 %. Energy therefore comes from counters and the energy
    balance, not from them.
  * Battery power sign: + = charging. Grid power sign: + = export.
  * Phase B/C, EV-charger and heat-pump entities hold placeholders (655.35 V, 0 kW): not used.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from hems_data import _counter_to_cumulative

TZ = "Europe/Dublin"
FRIENT = "frient_a_s_emizb_141_"


def prefixes(folder: Path) -> tuple[str, str]:
    """(station, inverter) entity prefixes, e.g. ('<station>_', '<station>_inverter_<id>_')."""
    name = next(folder.glob("*_inverter_*_battery_power.csv.gz")).name
    inverter = name[:-len("battery_power.csv.gz")]
    return inverter[:inverter.index("inverter_")], inverter


def entities(folder: Path) -> dict:
    """Signal name -> entity, per group. kW entities are converted to W on load."""
    S, I = prefixes(folder)
    return {
        # 1-min signals
        "power_kw": {"pv_power": S + "pv_power", "load_power": S + "load_power", "grid_power": S + "grid_power",
                     "battery_power": I + "battery_power", "ac_power": I + "active_power"},
        "other": {"soc": S + "battery_state_of_charge", "stored_kwh": S + "battery_stored_energy",
                  "voltage": I + "phase_a_voltage", "current": I + "phase_a_current",
                  "frequency": I + "grid_frequency", "meter_demand_w": FRIENT + "instantaneous_demand"},
        # Daily-resetting counters (kWh, 0.01 resolution, reset a few minutes after local midnight).
        "today": {"pv": S + "daily_pv_generation", "batt_charge": I + "battery_charging_today",
                  "batt_discharge": I + "battery_discharging_today"},
        "lifetime": {"pv": S + "lifetime_pv_generation", "batt_discharge": I + "battery_discharging_total",
                     "import_meter": FRIENT + "summation_delivered"},
        "mode": S + "operating_mode",
    }

ALIVE_WINDOW_MIN = 30   # Sigenergy writes every ~5 min; no point from any Sigen entity for 30 min = gap


def read_entity(folder: Path, entity: str, field: str = "value") -> pd.Series:
    d = pd.read_csv(folder / f"{entity}.csv.gz", usecols=["_time", "field", "value"], dtype={"value": "string"})
    d = d[d["field"] == field]
    s = pd.to_numeric(d["value"], errors="coerce").astype("float64") if field == "value" else d["value"].copy()
    s.index = pd.to_datetime(d["_time"], format="ISO8601", utc=True)
    s = s.dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _clean_daily_counter(s: pd.Series) -> pd.Series:
    """Centred rolling median over 5 readings. The Sigenergy PV day counter has 1-reading dips to
    about 1/100 of its value (32.48 -> 0.33 -> 32.82, 58 times) and 2-reading spikes (3 Sep 2026
    03:34: yesterday's 16.97 kWh twice, then 0); a median of 5 removes both. On a rising counter
    the median is the reading itself; at the midnight reset it moves at most a 0.01-0.02 kWh tick.
    Used on the PV counter only: the battery counters have no glitches and do move at midnight."""
    return s.rolling(5, center=True, min_periods=1).median()


def _held(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Hold-last-value at 1 min (the polls are 5 min apart, so a finer grid adds nothing)."""
    return s.reindex(s.index.union(grid)).ffill().reindex(grid)


def load_site(folder: Path, cache: Path | None = None) -> dict:
    """{'minute', 'energy15', 'energy30', 'lifetime', 'inventory'}; indexes Europe/Dublin.

    energy15 columns (kWh): pv, batt_charge, batt_discharge (Sigenergy counters), import (frient
    meter reader), export (integrated grid power), load (energy balance), plus import_sigen and
    load_sensor (the Sigenergy power readings, kept for comparison)."""
    if cache and cache.exists():
        with open(cache, "rb") as f:
            return pickle.load(f)

    ent = entities(folder)
    station, _ = prefixes(folder)
    raw, inventory = {}, []
    for p in sorted(folder.glob("*.csv.gz")):
        name = p.name[:-len(".csv.gz")]
        s = read_entity(folder, name)
        if not len(s):
            s = read_entity(folder, name, "state")
        raw[name] = s
        num = pd.api.types.is_numeric_dtype(s)
        inventory.append({"entity": f"sensor.{name}", "points": len(s), "first": s.index.min(), "last": s.index.max(),
                          "min": s.min() if num and len(s) else np.nan, "max": s.max() if num and len(s) else np.nan})

    sig = [s for e, s in raw.items() if e.startswith(station) and pd.api.types.is_numeric_dtype(s) and len(s) > 1000]
    start = min(s.index.min() for s in sig).ceil("1min")
    end = max(s.index.max() for s in sig).floor("1min")
    grid = pd.date_range(start, end, freq="1min")

    stamps = np.concatenate([s.index.values for s in sig])
    beats = pd.Series(1, index=pd.DatetimeIndex(stamps, tz="UTC")).resample("1min").sum().reindex(grid).fillna(0)
    alive = beats.rolling(ALIVE_WINDOW_MIN, min_periods=1).sum() > 0

    minute = pd.DataFrame(index=grid)
    for col, e in ent["power_kw"].items():
        minute[col] = _held(raw[e], grid) * 1000
    for col, e in ent["other"].items():
        minute[col] = _held(raw[e], grid)
    minute = minute.where(alive, np.nan)
    minute["alive"] = alive
    minute.index = minute.index.tz_convert(TZ)

    cum = pd.DataFrame({k: _held(_counter_to_cumulative(
        _clean_daily_counter(raw[v]) if k == "pv" else raw[v]), grid) for k, v in ent["today"].items()})
    cum["import"] = _held(raw[ent["lifetime"]["import_meter"]], grid)
    cum.index = cum.index.tz_convert(TZ)
    e15 = cum.resample("15min").last().diff().clip(lower=0)
    m = minute
    e15["export"] = (m["grid_power"].clip(lower=0) / 60000).resample("15min").sum(min_count=1)
    e15["import_sigen"] = ((-m["grid_power"]).clip(lower=0) / 60000).resample("15min").sum(min_count=1)
    e15["load_sensor"] = (m["load_power"] / 60000).resample("15min").sum(min_count=1)
    # A quarter-hour only counts if the box was alive throughout (counters would lump a gap's energy
    # into the first quarter after it).
    ok = m["alive"].resample("15min").mean() == 1
    e15 = e15.where(ok).iloc[1:]
    # Load = energy balance. Per quarter-hour it dips below zero now and then, because the counters
    # tick in 0.01 kWh steps a few minutes behind the power readings. Clipping those dips would bias
    # load upwards, so the running maximum of the cumulative balance is used instead: dips are
    # absorbed by the next quarter-hours and the total stays the true balance.
    bal = e15["pv"] + e15["import"] - e15["export"] + e15["batt_discharge"] - e15["batt_charge"]
    e15["load"] = bal.fillna(0).cumsum().cummax().diff().fillna(0).where(bal.notna())
    e15 = e15[["import", "export", "load", "pv", "batt_charge", "batt_discharge", "import_sigen", "load_sensor"]]
    e30 = e15.resample("30min").sum(min_count=2)

    lifetime = {k: float(raw[v].iloc[-1]) for k, v in ent["lifetime"].items()}
    out = {"minute": minute, "energy15": e15, "energy30": e30, "lifetime": lifetime,
           "inventory": pd.DataFrame(inventory), "mode": raw[ent["mode"]].value_counts(), "prefixes": prefixes(folder)}
    if cache:
        with open(cache, "wb") as f:
            pickle.dump(out, f)
    return out
