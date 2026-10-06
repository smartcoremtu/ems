"""Load and clean a HEMS InfluxDB export (one gzipped CSV per Home Assistant entity).

Export format (see CONTEXT.md, "Device Data Exports"): columns `_time, field, unit, value`,
UTC timestamps, `field == "value"` rows are numeric. Home Assistant only writes a point when
a value *changes*, so a long run without points is normally "value unchanged", not "no data".
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Europe/Dublin"
PREFIX = "inverter_"

# Instantaneous signals, resampled by time-weighted mean.
POWER = ["grid_power", "load_power", "pv_power", "power", "pv1_power", "pv2_power", "power_losses"]
ELECTRICAL = ["l1_voltage", "l1_current", "frequency", "pv1_voltage", "pv1_current",
              "pv2_voltage", "pv2_current", "insulation_resistance"]
THERMAL = ["temperature", "module_temperature", "radiator_temperature", "room_temperature"]
# Daily-resetting energy counters (0.01 kWh resolution) and their lifetime twins (0.1 kWh).
TODAY = {"import": "today_energy_import", "export": "today_energy_export",
         "load": "today_load_consumption", "pv": "today_production", "losses": "today_losses"}
TOTAL = {"import": "total_energy_import", "export": "total_energy_export",
         "load": "total_load_consumption", "pv": "total_production", "losses": "total_losses"}

ALIVE_WINDOW_MIN = 15   # no point from *any* entity for this long = real data gap
MIN_POINTS = 1000       # entities with fewer points are unused inputs (L2/L3 on a 1-phase site)


def read_entity(folder: Path, name: str) -> pd.Series:
    """Numeric series for one entity, UTC index, duplicates dropped."""
    d = pd.read_csv(folder / f"{PREFIX}{name}.csv.gz", usecols=["_time", "field", "value"],
                    dtype={"value": "string"})
    d = d[d["field"] == "value"]
    s = pd.to_numeric(d["value"], errors="coerce").astype("float64")
    s.index = pd.to_datetime(d["_time"], format="ISO8601", utc=True)
    s = s.dropna()
    return s[~s.index.duplicated(keep="last")].sort_index().rename(name)


def read_states(folder: Path, name: str) -> pd.Series:
    """Text state series (device_state / device_fault)."""
    d = pd.read_csv(folder / f"{PREFIX}{name}.csv.gz", usecols=["_time", "field", "value"],
                    dtype={"value": "string"})
    d = d[d["field"] == "state"]
    s = d["value"].copy()
    s.index = pd.to_datetime(d["_time"], format="ISO8601", utc=True).dt.tz_convert(TZ)
    return s.sort_index().rename(name)


def _minute_mean(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Time-weighted 1-min mean of a write-on-change series (hold last value, 5 s steps)."""
    fine = pd.date_range(grid[0], grid[-1] + pd.Timedelta(minutes=1), freq="5s", inclusive="left")
    held = s.reindex(s.index.union(fine)).ffill().reindex(fine)
    return held.resample("1min").mean().reindex(grid)


def _despike(s: pd.Series) -> pd.Series:
    """Drop single bad reads of a counter: a point that jumps away from its neighbours while the
    next point carries on from the previous one (seen on 25 May 2026: 10.73 -> 12.09 -> 10.75,
    which would otherwise count as 1.34 kWh of export). A midnight reset is not a spike because
    the point after it stays low."""
    prev, nxt = s.shift(), s.shift(-1)
    spike = (nxt >= prev) & ((s > nxt) | (s < prev))
    return s[~spike]


def _counter_to_cumulative(s: pd.Series) -> pd.Series:
    """Turn a daily-resetting counter into a monotonic cumulative series.

    A drop to below half the previous value is the midnight reset (the new value is the energy
    since the reset); any other negative step is a glitch and counts as zero.
    """
    s = _despike(s)
    step = s.diff()
    reset = (step < 0) & (s < 0.5 * s.shift())
    inc = step.where(step > 0, 0.0)
    inc[reset] = s[reset]
    inc.iloc[0] = 0.0
    return inc.cumsum()


def load_site(folder: Path, cache: Path | None = None) -> dict:
    """Return {'minute': 1-min frame, 'energy15' / 'energy30': 15- and 30-min kWh frames, 'totals': counter deltas,
    'states': dict, 'inventory': per-entity point counts}. All indexes are Europe/Dublin."""
    if cache and cache.exists():
        with open(cache, "rb") as f:
            return pickle.load(f)

    inventory, raw = [], {}
    for p in sorted(folder.glob(f"{PREFIX}*.csv.gz")):
        name = p.name[len(PREFIX):-len(".csv.gz")]
        s = read_entity(folder, name)
        inventory.append({"entity": f"sensor.{PREFIX}{name}", "points": len(s),
                          "first": s.index.min(), "last": s.index.max(),
                          "min": s.min() if len(s) else np.nan, "max": s.max() if len(s) else np.nan})
        raw[name] = s

    start = min(s.index.min() for s in raw.values() if len(s) > MIN_POINTS).floor("1min")
    end = max(s.index.max() for s in raw.values() if len(s) > MIN_POINTS).floor("1min")
    grid = pd.date_range(start, end, freq="1min")

    # Heartbeat: the box is "alive" in a minute if any entity wrote within the last 15 min.
    stamps = np.concatenate([s.index.values for s in raw.values()])
    beats = pd.Series(1, index=pd.DatetimeIndex(stamps, tz="UTC")).resample("1min").sum().reindex(grid).fillna(0)
    alive = beats.rolling(ALIVE_WINDOW_MIN, min_periods=1).sum() > 0

    minute = pd.DataFrame(index=grid)
    for name in POWER + ELECTRICAL + THERMAL:
        if len(raw.get(name, [])) > MIN_POINTS:
            minute[name] = _minute_mean(raw[name], grid).where(alive)
    minute["alive"] = alive
    minute.index = minute.index.tz_convert(TZ)

    cum = pd.DataFrame({k: _counter_to_cumulative(raw[v]).reindex(
        raw[v].index.union(grid)).ffill().reindex(grid) for k, v in TODAY.items()})
    cum.index = cum.index.tz_convert(TZ)
    energy15 = cum.resample("15min").last().diff().iloc[1:].clip(lower=0)
    energy30 = energy15.resample("30min").sum()          # smart-meter resolution, used for tariffs

    totals = {k: float(raw[v].iloc[-1] - raw[v].iloc[0]) for k, v in TOTAL.items()}
    lifetime = {k: float(raw[v].iloc[-1]) for k, v in TOTAL.items()}
    out = {"minute": minute, "energy15": energy15, "energy30": energy30, "totals": totals, "lifetime": lifetime,
           "states": {n: read_states(folder, n) for n in ("device_state", "device_fault")},
           "inventory": pd.DataFrame(inventory)}
    if cache:
        with open(cache, "wb") as f:
            pickle.dump(out, f)
    return out
