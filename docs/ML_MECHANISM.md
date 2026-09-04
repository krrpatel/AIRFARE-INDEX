# ML Mechanism

## Purpose

The ML layer supports two research tasks: anomaly corroboration and route-level fare forecasting. It does not silently remove observations or replace missing source data.

## Anomaly Detection

`ml/anomaly_detection.py` applies an Isolation Forest to route groups using `total_fare`, `days_to_departure`, and `stops`. Its boolean signal is combined with the pipeline's IQR signal as `anomaly_flag`. Rows remain in the dataset and are available for audit.

The API exposes flagged records through `/api/anomalies` and quality counts through `/api/data-quality`.

## Forecasting

`ml/forecasting.py` builds features from travel date, lead time, stops, day of week, month, and holiday-window status. It compares Linear Regression with Random Forest using a chronological train/validation split. Random shuffling is avoided because it would leak future pricing behavior into training.

The route forecast endpoint is `/api/forecast/{origin}/{destination}`. It uses valid stored observations, returns predicted fare ranges, and caches the response for the configured TTL.

## Operational Rules

- A model result is a signal, not an index input by itself.
- Fewer than the required observations returns an honest insufficient-data response.
- Validation MAE, RMSE, and MAPE are preferred over an assumed model choice.
- Holiday data can be refreshed from `scraper.ota.ixigo_holiday`; the current feature builder retains an explicit fallback when a holiday table is not connected.

## Useful Commands

```powershell
python -c "from ml.forecasting import FEATURES; print(FEATURES)"
pytest tests/test_pipeline.py tests/test_alerts.py -q
```
