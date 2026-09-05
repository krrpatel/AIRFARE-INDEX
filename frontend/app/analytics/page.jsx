"use client";
import { useEffect, useState } from "react";
import BarChart from "../components/BarChart";
import TrendChart from "../components/TrendChart";
import { downloadCsv } from "../lib/csv";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export default function AnalyticsPage() {
  const [data, setData] = useState(null);
  const [routeData, setRouteData] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState("");
  const [dateRange, setDateRange] = useState(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  useEffect(() => {
    // `ignore` guards against out-of-order responses: startDate and endDate
    // are two separate state updates, so a quick filter change can fire this
    // effect twice before the first request resolves. Without this guard,
    // an older (slower) request finishing after a newer one would clobber
    // the fresh, correct state with stale data or a stale error -- which is
    // exactly what caused the error banner to appear after a successful
    // load, and the seasonality chart to appear "stuck" on old data.
    let ignore = false;
    const params = new URLSearchParams();
    if (startDate) params.set("start", startDate);
    if (endDate) params.set("end", endDate);
    params.set("limit", "500");
    const qs = params.toString();
    setIsLoading(true);
    setError(null);
    Promise.all([fetch(`${API_URL}/api/analytics?${qs}`), fetch(`${API_URL}/api/routes/analytics?${qs}`)]).then(async ([analyticsResponse, routesResponse]) => {
      if (!analyticsResponse.ok) throw new Error(`Analytics request failed (${analyticsResponse.status})`);
      if (!routesResponse.ok) throw new Error(`Route analytics request failed (${routesResponse.status})`);
      const analytics = await analyticsResponse.json();
      const routes = await routesResponse.json();
      if (ignore) return;
      setData(analytics);
      setRouteData(routes.routes || []);
      setDateRange(analytics.date_range || routes.date_range || null);
    }).catch(e => { if (!ignore) setError(e instanceof TypeError ? "Network error: could not reach the server. Please check your connection and try again." : e.message); })
      .finally(() => { if (!ignore) setIsLoading(false); });
    return () => { ignore = true; };
  }, [startDate, endDate]);
  const ranking = data?.route_ranking || [];
  const routeOptions = [...new Set(routeData.map(row => `${row.origin}-${row.destination}`))].sort();
  const activeRoute = selectedRoute || routeOptions[0] || "";
  const selectedAirlines = routeData.filter(row => `${row.origin}-${row.destination}` === activeRoute).sort((a, b) => b.average_fare - a.average_fare);
  const exportAirlineDetail = () => {
    const columns = ["route", "airline_code", "average_fare", "median_fare", "lowest_fare", "highest_fare", "observation_count"];
    const rows = selectedAirlines.map(row => ({ route: `${row.origin}-${row.destination}`, airline_code: row.airline_code, average_fare: row.average_fare, median_fare: row.median_fare, lowest_fare: row.lowest_fare, highest_fare: row.highest_fare, observation_count: row.observation_count }));
    downloadCsv(`airfare-analytics-${activeRoute || "route"}-${new Date().toISOString().slice(0, 10)}.csv`, columns, rows);
  };
  return <main className="page"><section className="hero"><div><div className="eyebrow">Decision support</div><h1>Fare Market Analytics</h1><p>Route concentration, fare distribution, movement, and seasonal context for review of the Airfare Price Index basket.</p></div></section>{error && <div className="notice">{error}</div>}<section className="grid">
    <div className="panel wide"><div className="section-heading"><div><h2>Average Fare Ranking</h2><p className="muted">Compare all available airlines for one selected route.</p></div><label>Route<select value={activeRoute} onChange={event => setSelectedRoute(event.target.value)}><option value="">Select route</option>{routeOptions.map(route => <option key={route} value={route}>{route}</option>)}</select></label></div><div className="button-row table-tools"><label htmlFor="analytics-start-date">From</label><input id="analytics-start-date" type="date" value={startDate} min={dateRange?.min} max={dateRange?.max} onChange={e => setStartDate(e.target.value)} /><label htmlFor="analytics-end-date">To</label><input id="analytics-end-date" type="date" value={endDate} min={dateRange?.min} max={dateRange?.max} onChange={e => setEndDate(e.target.value)} /></div><p className="muted">{dateRange?.min && dateRange?.max ? `Observations exist from ${dateRange.min} to ${dateRange.max}. Leave blank to include the full range.` : "Loading available date range..."}</p>{error ? null : isLoading ? <p className="muted">Loading data...</p> : <BarChart data={selectedAirlines.map(row => ({ label: row.airline_code, value: row.average_fare }))} valueLabel="Average fare (Rs)" />}</div>
    <div className="panel full"><div className="section-heading"><h2>{activeRoute || "Selected route"} Airline Fare Detail</h2><button type="button" onClick={exportAirlineDetail} disabled={isLoading || !selectedAirlines.length}>Export all filtered CSV</button></div>{error ? null : isLoading ? <p className="muted">Loading data...</p> : <><table><thead><tr><th>Airline</th><th>Average fare</th><th>Median fare</th><th>Lowest</th><th>Highest</th><th>Observations</th></tr></thead><tbody>{selectedAirlines.map(row => <tr key={`${row.origin}-${row.destination}-${row.airline_code}`}><td><strong>{row.airline_code}</strong></td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td><td>Rs {row.median_fare.toLocaleString("en-IN")}</td><td>Rs {row.lowest_fare.toLocaleString("en-IN")}</td><td>Rs {row.highest_fare.toLocaleString("en-IN")}</td><td>{row.observation_count.toLocaleString("en-IN")}</td></tr>)}</tbody></table>{!selectedAirlines.length && <p className="muted">{activeRoute ? "No data available for the selected filters." : "Select a route with available airline observations."}</p>}</>}</div>
    <div className="panel"><h2>Cheapest Sectors</h2>{error ? null : isLoading ? <p className="muted">Loading data...</p> : ranking.length ? <table><thead><tr><th>Route</th><th>Average fare</th></tr></thead><tbody>{ranking.slice(0, 8).map(row => <tr key={`low-${row.route}`}><td>{row.route}</td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td></tr>)}</tbody></table> : <p className="muted">No data available for the selected filters.</p>}</div>
    <div className="panel"><h2>Highest Sectors</h2>{error ? null : isLoading ? <p className="muted">Loading data...</p> : ranking.length ? <table><thead><tr><th>Route</th><th>Average fare</th></tr></thead><tbody>{[...ranking].reverse().slice(0, 8).map(row => <tr key={`high-${row.route}`}><td>{row.route}</td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td></tr>)}</tbody></table> : <p className="muted">No data available for the selected filters.</p>}</div>
    <div className="panel wide"><h2>Fare Distribution</h2>{error ? null : isLoading ? <p className="muted">Loading data...</p> : <BarChart data={(data?.fare_distribution || []).map(row => ({ label: row.bucket, value: row.count }))} valueLabel="Valid observations" />}</div>
    <div className="panel wide"><h2>Monthly Seasonality</h2>{error ? null : isLoading ? <p className="muted">Loading data...</p> : <TrendChart data={(data?.seasonality || []).map(row => ({ date: row.month, value: row.average_fare }))} yLabel="Average fare (Rs)" color="#b42318" />}</div>
  </section></main>;
}
