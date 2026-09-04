# Source Field Mapping

## CompareFlights Offline Import

Source files:

- `data/raw_airfare/compareflights/2026-08-26/*.json`

Adapter:

- `airfare/sources/compareflights/offline_adapter.py`

Parser version:

- `compareflights-normalized-json-v1`

| Source field | Standard field | Transformation |
|---|---|---|
| route file `origin` / `destination` | `origin` / `destination` | Upper-case IATA. The searched route is preserved for index routing. |
| itinerary `origin` / `destination` | `source_payload.reported_origin` / `reported_destination` | Preserved for audit, including alternate-airport returns such as `NMI`. |
| offset `departure_date` | `travel_date` | ISO date parsing. |
| itinerary or offset `days_before_departure` | `advance_purchase_days` | Integer lead-time value. |
| itinerary `cabin` | `cabin_class` | Preserved as source label. |
| `segments[0].airline_code` | `airline_code` | Primary segment airline. |
| `segments[0].airline_name` | `airline_name` | Primary segment airline name. |
| `segments[*].flight_number` | `flight_number` | Joined with `|` for multi-leg itineraries. |
| first segment `departure_datetime` | `departure_datetime` | ISO datetime parsing. |
| last segment `arrival_datetime` | `arrival_datetime` | ISO datetime parsing. |
| itinerary `total_duration_minutes` | `duration_minutes` | Integer. |
| itinerary `stops` | `stops` | Integer. |
| offer `fare_code` | `fare_code` | Preserved. |
| offer `price` | `components.offered_fare`, `components.total_payable_fare` | Numeric. |
| offer `currency` or pricing `currency` | `currency` | Defaults to `INR` only when source omits currency. |
| offer baggage fields | `baggage` | Preserved nullable. |
| `pricing` and full offer object | `components.source_native` | Preserved for audit. |
| base fare, taxes, UDF, PSF/ASF, airport charges, fuel surcharge, service/convenience fee | nullable fare component fields | `NULL`; current source output does not expose these separately. |

Do not infer unavailable fare components from total fare. Unknown means `NULL`, not zero.

