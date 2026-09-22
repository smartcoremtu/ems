"""Load the independent reference data for a site: the ESB Networks smart-meter file (HDF) and
the SolarMan portal exports. Both are used only to check the HEMS measurements against.

ESB HDF  : one row per register per half-hour, `Read Date and End Time` = END of the interval.
SolarMan : one workbook per month, 5-minute power snapshots (W), battery power and SoC.
Both files turn out to be on Irish clock time (the SolarMan "Time Zone: UTCZ" column is wrong);
validation.clock_check() proves that from the data, so `clock` stays a parameter.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

TZ = "Europe/Dublin"

SOLARMAN_COLS = {
    "Production Power(W)": "pv_power", "Consumption Power(W)": "load_power",
    "Grid Power(W)": "grid_power", "Battery Power(W)": "battery_power", "SoC(%)": "soc",
}


def read_esb_hdf(path: Path, clock: str = "local") -> pd.DataFrame:
    """Half-hourly import/export (kWh, or kW for the kW file), labelled by interval START, Europe/Dublin.

    clock: "utc" = file times are UTC all year; "local" = Irish clock time (ambiguous hour dropped).
    """
    d = pd.read_csv(path, usecols=["Read Value", "Read Type", "Read Date and End Time"])
    d["end"] = pd.to_datetime(d["Read Date and End Time"], format="%d-%m-%Y %H:%M")
    d["reg"] = d["Read Type"].str.extract(r"Active (Import|Export)")[0].str.lower()
    wide = d.pivot_table(index="end", columns="reg", values="Read Value", aggfunc="first").sort_index()
    if clock == "utc":
        wide.index = wide.index.tz_localize("UTC").tz_convert(TZ)
    else:
        wide.index = wide.index.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
        wide = wide[wide.index.notna()]
    wide.index = wide.index - pd.Timedelta(minutes=30)
    wide.index.name = None
    wide.columns.name = None
    return wide[["import", "export"]]


def read_solarman(folder: Path, clock: str = "local") -> pd.DataFrame:
    """All monthly workbooks as one 5-min frame (W, %), Europe/Dublin index, in the HEMS sign
    conventions: grid_power + = export, battery_power + = discharging. SolarMan's own battery sign
    is the opposite (negative while the SoC falls), so it is flipped here."""
    frames = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)      # "Workbook contains no default style"
        for p in sorted(folder.glob("*.xlsx")):
            if p.name.startswith("~$"):
                continue
            frames.append(pd.read_excel(p, usecols=["Updated Time", *SOLARMAN_COLS]))
    d = pd.concat(frames, ignore_index=True).rename(columns=SOLARMAN_COLS)
    idx = pd.to_datetime(d.pop("Updated Time"), format="%Y/%m/%d %H:%M")
    if clock == "utc":
        d.index = idx.dt.tz_localize("UTC").dt.tz_convert(TZ)
    else:
        d.index = pd.DatetimeIndex(idx).tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
        d = d[d.index.notna()]
    d.index.name = None
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.astype("float64")
    d["battery_power"] = -d["battery_power"]
    return d
