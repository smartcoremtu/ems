# HEMS data analysis

Turns an InfluxDB export of a HEMS box (one `inverter_*.csv.gz` per Home Assistant entity, see
`F:\Smartcore\CONTEXT.md` → "Device Data Exports") into a single Excel report: dashboard, tariff
check with live formulas, self-consumption, day profiles, heatmaps, battery & EV, solar, peaks.

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python martinh_report.py            # uses data\.cache_MartinH.pkl if present
.venv\Scripts\python martinh_report.py --refresh  # re-read the raw files (~2 min)
```

Output: `..\data\MartinH_Energy_Report.xlsx` (git-ignored).

Stewart House (Sigenergy PV + battery, frient meter reader; export in `..\data\Stewart\influx\`,
Sigen app daily totals in `..\data\Stewart\StewartHouse.xlsx`):

```
.venv\Scripts\python stewart_report.py        # -> ..\data\Stewart_Energy_Report.xlsx
.venv\Scripts\python stewart_validation.py    # -> ..\data\Stewart_Validation.xlsx (vs Sigen app + frient)
```

| File | Role |
|---|---|
| `hems_data.py` | Load + clean: write-on-change signals → time-weighted 1-min means, daily counters → 15-min kWh (summed to 30 min for tariffs) |
| `metrics.py` | KPIs, tariff engine, EV sessions, battery estimate, scenarios. Assumptions are constants at the top |
| `tariffs.py` | Irish plans (inc. VAT) with sources and research date — **re-check before reuse, rates move** |
| `martinh_report.py` | Workbook writer (xlsxwriter, native Excel charts) |
| `reference_data.py` | Loaders for the reference data: ESB Networks smart-meter HDF file, SolarMan portal workbooks (both are on Irish clock time) |
| `validation.py` | HEMS vs ESB meter (import/export) and vs SolarMan (data path, battery); effect on the report's headline figures |
| `sigen_data.py` | Loader for a Sigenergy site: counters (PV de-glitched) + frient import + integrated export → 15-min kWh, load from the energy balance. Station power sensors have a low-power dead band - not used for energy |
| `stewart_metrics.py` / `stewart_report.py` | KPIs and workbook for Stewart House (reuses `Book` from `martinh_report.py`) |
| `stewart_validation.py` | HEMS vs Sigen app daily totals, grid import three ways, sensor dead band |
| `martinh_validation.py` | Writes `..\data\MartinH_Validation.xlsx` from `..\data\Broomfield Data\` (needs `openpyxl` to read the SolarMan files) |

Privacy: the input and the report are identifiable household data. Keep them in `data/` (ignored),
and switch `SITE_LABEL` to the UHIC code before sharing outside the project.
