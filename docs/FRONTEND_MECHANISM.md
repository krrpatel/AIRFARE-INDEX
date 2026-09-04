# Frontend Mechanism

## Stack

The dashboard is a Next.js App Router application. Recharts provides responsive animated trend, area, line, and bar visualizations. React Leaflet renders the India GeoJSON boundary, OpenStreetMap tiles, route lines, airport markers, hover tooltips, and click details.

## Views

- Overview: national index, movement, freshness, quality, alerts, source health, and DGCA basket.
- Routes: route selection, fare components, available offers, airline/lead-time lines, forecast, route ranking, pagination, CSV export, and DGCA Top 50.
- Airlines: sortable airline summary, weekly source overview, and configurable Top N route mix.
- Analytics: fare distribution, route ranking, cheapest/highest sectors, and seasonality.
- India Map: geographic route and airport inspection.
- Settings: DGCA cycle, daily source schedule, route scope, source links, and background job status.

## Data Loading

The frontend calls `/api/backend/...`. The same-origin catch-all route proxies GET, POST, and PUT requests to FastAPI, avoiding browser cross-origin loopback issues. Dynamic route parameters are awaited for compatibility with current Next.js versions.

## Interaction Rules

Charts use data-driven domains, smooth interpolation, responsive containers, labelled axes, legends, and custom tooltips. Dense series reduce marker size automatically. Route exports include all filtered rows, not only the current 10-row page. Scrape jobs are polled by status and remain visible after a page change when the job id is retained.

## Commands

```powershell
cd frontend
npm install
npm run dev
npm run build
npm run start
```
