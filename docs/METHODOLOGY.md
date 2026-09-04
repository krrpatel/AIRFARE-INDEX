# Airfare Price Index — Methodology

**Status: PROTOTYPE METHODOLOGY. This is not an official MoSPI/CPI index.**
Every design choice below is labeled FACT (externally verifiable), ASSUMPTION
(reasonable but unverified), or PROPOSED DESIGN (our engineering decision).

---

## 1. The Core Problem

A price index needs to compare "the price of the same thing" across time. For
groceries, that's easy — a 1kg bag of rice today is the same item as a 1kg
bag of rice last month. Airfare does not have this property:

- The same route on the same day has many different prices simultaneously
  (cabin class, fare type, days-to-departure, seat inventory, dynamic pricing).
- A ticket priced today for a flight in 3 weeks is not "the same product" as
  a ticket priced today for a flight tomorrow.

This is a documented, long-standing problem in official statistics, not
something invented for this project. The U.S. Bureau of Labor Statistics
built an *experimental* Air Travel Price Index specifically because matching
individual airline itineraries across time periods is the primary
methodological obstacle — itineraries are customized per passenger, so
period-over-period item matching (the standard CPI technique) breaks down.
[FACT — BLS Monthly Labor Review, "Air-Travel Transaction Index", 2005]

Our design responds to this by never trying to match individual tickets.
Instead we match **route + cabin class + a fixed days-to-departure bucket**
as the "item," and treat each day's observations for that item as a small
sample, not a single price.

---

## 2. Item Definition (Elementary Aggregate)

**PROPOSED DESIGN.** An elementary item = one **(origin, destination, cabin
class, departure-bucket)** cell, e.g. `(DEL, BOM, Economy, 14-21 days out)`.

We fix departure-bucket because price rises mechanically as departure
approaches — without controlling for this, the index would conflate genuine
inflation with normal booking-curve dynamics. Buckets:

| Bucket | Days to departure |
|---|---|
| B1 | 0–3 |
| B2 | 4–7 |
| B3 | 8–14 |
| B4 | 15–30 |
| B5 | 31–60 |
| B6 | 61+ |

Each day, for each route, we compute one **representative price per bucket**
as the **median total fare** of that day's valid observations in that cell
(median, not mean, to reduce single-outlier sensitivity before formal
outlier handling runs). A route's daily "price" for indexing purposes is
then the observation-count-weighted average across buckets that have data
that day — this smooths gaps when a bucket has no fresh scrape on a given
day.

## 3. Elementary Aggregation — Price Relatives

For each route, each day *t*:

```
Price Relative (route, t) = Price(route, t) / Price(route, base_period)
```

Where multiple items (bucket cells) exist for a route, we combine their
individual price relatives using the **geometric mean**, not the arithmetic
mean:

```
Route Price Relative(t) = ( Π PriceRelative_i(t) )^(1/n)
```

**Why geometric mean (PROPOSED DESIGN, but this is standard real-world
practice, not invented):** international CPI guidance (ILO/IMF Consumer
Price Index Manual) recommends geometric-mean (Jevons-type) elementary
aggregation over simple arithmetic averaging because it does not suffer from
upward bias when relative prices diverge, and it's consistent under
time-reversal. This is the same reasoning applied here at the route level.

## 4. Higher-Level Aggregation — Route Weights → National Index

Above the elementary level, we use a **fixed-weight (Laspeyres-style)
arithmetic aggregation**:

```
National Index(t) = Σ [ Weight(route) × RoutePriceRelative(t) ] × 100
```

**Why Laspeyres-style here, not Paasche or Fisher (PROPOSED DESIGN):**

| Method | Needs | Verdict for us |
|---|---|---|
| Laspeyres | Base-period weights only | ✅ Feasible — we only need one weight set, held fixed |
| Paasche | Current-period expenditure weights every period | ❌ We have no real-time expenditure/traffic-share data — would have to fabricate weights every period, which is worse than fixing them |
| Fisher (geometric mean of Laspeyres & Paasche) | Both of the above | ❌ Same blocker as Paasche |

A fixed-weight Laspeyres-style index is the only one of the three we can
compute honestly with the data actually available to a prototype. This
mirrors how most national CPIs actually operate in practice — weights are
revised periodically (e.g. annually) from expenditure surveys, not
recomputed every period.

## 5. Weights — Prototype vs Production

**PROTOTYPE BASKET.** ~25 domestic routes (see `basket.py`), chosen for
being high-traffic, geographically spread across India, and covering all
major carriers. This is explicitly **not** the official MoSPI/DGCA basket —
it is a representative stand-in for demonstration purposes.

**PROTOTYPE WEIGHTS.** Derived as each route's share of total **DGCA
city-pair-wise domestic passenger traffic** [FACT — DGCA publishes monthly
carrier-wise and quarterly city-pair-wise domestic passenger traffic
statistics on its public portal] among the routes in our basket, normalized
to sum to 100%. This is a passenger-count proxy for expenditure share, not
actual expenditure share (we don't have route-level fare-expenditure survey
data) — stated explicitly as a limitation.

**PRODUCTION DESIGN (not built, documented only).** A real MoSPI system
would derive weights from: (a) route-level expenditure data possibly sourced
from airline/OTA transaction volumes under an MoU, (b) DGCA's full
route-traffic dataset (not just our basket subset), (c) periodic weight
revision (e.g. annual), consistent with how CPI basket weights are
periodically rebased.

## 6. Base Period & Chain-Linking

**Base period: January 2026 = 100.**

**Problem:** live scraping only begins during this project's build window
(mid-to-late August 2026) — we cannot have genuinely observed January 2026
prices.

**PROPOSED DESIGN — Chain-linking with two labeled tracks:**

- **Track A (`data_mode = 'simulated_backcast'`)** — a statistically
  generated, deterministic, clearly-labeled series from Jan 2026 to the
  scraper's live-launch date, built by the mock data generator using
  realistic seasonality/holiday/volatility parameters. This is explicitly
  **not real data** and is never presented as such.
- **Track B (`data_mode = 'live'`)** — real (or, if unavailable, current
  mock-mode) observations from launch date onward.

At the splice date, Track B's index is rescaled so its first value equals
Track A's index value on that date — the same mechanic statistical agencies
use whenever a data source changes mid-series, so the time series has no
artificial break. The dashboard always shows which segment of the chart is
simulated vs. live with a visible boundary marker. This is disclosed in the
UI and in `LIMITATIONS.md`, not hidden.

## 7. Missing Data

- A route with zero observations on a given day: **carry forward** the
  previous valid route index value, flagged `imputed=true`. Never
  interpolated forward more than 3 consecutive days without a data-quality
  warning being raised.
- A route below a minimum weekly observation-count threshold is excluded
  from that day's national index computation and its weight is
  redistributed proportionally among the remaining routes (documented,
  reproducible re-normalization — not silent).

## 8. Outliers vs. Promotions vs. Invalid Data

Three different things get confused if handled with one filter:

1. **Clearly invalid** (negative fare, travel date before booking date,
   impossible duration, unknown airport code) → **rejected**, never enters
   index calculation, logged to `data_quality_events`.
2. **Statistically suspicious** (outside route+bucket IQR bounds, or
   isolation-forest flagged) → **flagged, not deleted.** Included in index
   only with a `suspicious=true` annotation visible in the data-quality
   dashboard; excluded from the *median* calculation used for the
   representative price (medians are naturally robust to this) but retained
   in the raw table for audit.
3. **Genuine unusual price** (promotion, sudden demand spike) → not
   filtered; this is real price movement the index is supposed to capture.
   Distinguished from (2) by being corroborated across multiple sources or
   persisting over multiple observations, rather than a single-source
   single-observation spike.

We do not claim a fully automated way to separate (2) from (3) with
certainty — this is stated as a known limitation. Isolation Forest is used
as a secondary corroborating signal, not a sole deletion trigger.

## 9. Duplicates

An observation is a duplicate if it shares (source, route, travel_date,
cabin, flight_number-or-null, scraped_at rounded to the collection interval)
with an existing row — deduplicated before entering the clean table,
keeping the most complete record.

## 10. Revision Policy

Late-arriving valid data for a past date triggers **recomputation of that
date's route and national index values only** (not the whole series), and
the revision is logged in `index_values` with a `revised_at` timestamp and
the prior value retained in `index_revision_history`. This mirrors standard
statistical-agency practice of publishing provisional then revised figures.

## 11. What This Prototype Explicitly Does NOT Claim

- This is **not** the official CPI, nor an MoSPI-endorsed methodology.
- Weights are a **traffic-share proxy**, not expenditure-share data.
- The basket is a **representative subset**, not the official route basket.
- ML forecasts/anomaly explanations are **supplementary signals**, not the
  index calculation itself, and never described as causal ("potential
  contributing factor," never "this caused").
