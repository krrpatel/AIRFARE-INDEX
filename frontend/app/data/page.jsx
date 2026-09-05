"use client";
import { useEffect, useMemo, useState } from "react";
import BarChart from "../components/BarChart";
import TrendChart from "../components/TrendChart";
import { downloadCsv } from "../lib/csv";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";
const csvColumns = ["source", "run_date", "observed_at", "route", "origin", "destination", "travel_date", "lead_window", "airline_code", "airline_name", "flight_number", "fare", "currency", "number_of_stops", "availability_status", "fare_code"];

export default function DataMethod() {
  const [basket, setBasket] = useState(null); const [quality, setQuality] = useState(null); const [sources, setSources] = useState([]); const [mapping, setMapping] = useState(null); const [error, setError] = useState(null); const [catalog, setCatalog] = useState([]); const [airports, setAirports] = useState({});
  const [selectedSource, setSelectedSource] = useState("compareflights"); const [selectedDate, setSelectedDate] = useState(""); const [selectedRoute, setSelectedRoute] = useState(""); const [routeCatalog, setRouteCatalog] = useState([]); const [sourceData, setSourceData] = useState(null); const [sourceLoading, setSourceLoading] = useState(false); const [routeLoading, setRouteLoading] = useState(false); const [exporting, setExporting] = useState(false);

  useEffect(() => {
    Promise.all([fetch(`${API_URL}/api/dgca/basket?top_n=50&direction_mode=bidirectional`), fetch(`${API_URL}/api/data-quality`), fetch(`${API_URL}/api/source-health`), fetch(`${API_URL}/api/source-field-mapping`), fetch(`${API_URL}/api/source-data/dates`), fetch(`${API_URL}/api/airports`)]).then(async responses => {
      const bodies = await Promise.all(responses.map(response => response.ok ? response.json() : Promise.reject(new Error(`Initial data request failed (${response.status})`))));
      setBasket(bodies[0]); setQuality(bodies[1]); setSources(Array.isArray(bodies[2]) ? bodies[2] : []); setMapping(bodies[3]); setCatalog(bodies[4].sources || []); setAirports(Object.fromEntries((bodies[5].airports || []).map(airport => [airport.code, airport])));
    }).catch(e => setError(e.message));
  }, []);

  const availableDates = catalog.find(item => item.source === selectedSource)?.dates || [];
  useEffect(() => { if (!availableDates.some(item => item.run_date === selectedDate)) setSelectedDate(availableDates[0]?.run_date || ""); }, [availableDates, selectedDate]);

  useEffect(() => {
    if (!selectedSource || !selectedDate) { setRouteCatalog([]); setSelectedRoute(""); return; }
    let ignore = false; setRouteLoading(true); setError(null);
    fetch(`${API_URL}/api/source-data/routes?source=${selectedSource}&run_date=${selectedDate}`).then(response => response.ok ? response.json() : response.json().then(body => Promise.reject(new Error(body.detail || "Could not load route catalog")))).then(body => { if (!ignore) { setRouteCatalog(body.routes || []); setSelectedRoute(current => body.routes?.some(route => route.route === current) ? current : body.routes?.[0]?.route || ""); } }).catch(e => { if (!ignore) setError(e.message); }).finally(() => { if (!ignore) setRouteLoading(false); });
    return () => { ignore = true; };
  }, [selectedSource, selectedDate]);

  useEffect(() => {
    if (!selectedSource || !selectedDate || !selectedRoute) { setSourceData(null); return; }
    let ignore = false; setSourceLoading(true);
    fetch(`${API_URL}/api/source-data?source=${selectedSource}&run_date=${selectedDate}&route=${encodeURIComponent(selectedRoute)}&limit=1000`).then(response => response.ok ? response.json() : response.json().then(body => Promise.reject(new Error(body.detail || "Could not load route data")))).then(body => { if (!ignore) setSourceData(body); }).catch(e => { if (!ignore) setError(e.message); }).finally(() => { if (!ignore) setSourceLoading(false); });
    return () => { ignore = true; };
  }, [selectedSource, selectedDate, selectedRoute]);

  async function fetchCsv(route) {
    if (!selectedDate) return;
    setExporting(true);
    try {
      const routeQuery = route ? `&route=${encodeURIComponent(route)}` : "";
      const response = await fetch(`${API_URL}/api/source-data?source=${selectedSource}&run_date=${selectedDate}${routeQuery}&limit=200000`);
      if (!response.ok) throw new Error("The selected source export could not be prepared.");
      const body = await response.json(); downloadCsv(`${selectedSource}-${selectedDate}-${route || "all"}.csv`, csvColumns, body.rows || []);
    } catch (e) { setError(e.message); } finally { setExporting(false); }
  }

  const leadChart = useMemo(() => { const groups = {}; (sourceData?.rows || []).forEach(row => { if (row.fare == null) return; const key = row.lead_window || "Unknown"; groups[key] ||= []; groups[key].push(Number(row.fare)); }); return Object.entries(groups).map(([label, values]) => ({ label, value: values.reduce((sum, value) => sum + value, 0) / values.length })).sort((a, b) => Number(a.label.replace("T+", "")) - Number(b.label.replace("T+", ""))); }, [sourceData]);
  const stopChart = useMemo(() => { const groups = {}; (sourceData?.rows || []).forEach(row => { const key = row.number_of_stops == null ? "Unknown" : `${row.number_of_stops} stop${row.number_of_stops === 1 ? "" : "s"}`; groups[key] = (groups[key] || 0) + 1; }); return Object.entries(groups).map(([label, value]) => ({ label, value })); }, [sourceData]);
  const statuses = quality?.by_quality_status || {};

  return <main className="page">{error && <div className="notice">API error: {error}</div>}<section className="grid">
    <div className="panel full"><div className="section-heading"><div><h2>Source Data Explorer</h2><p className="muted">Choose a dated source run, then a route. Only the selected route observations are loaded into the table.</p></div><div className="button-row"><label>Source<select value={selectedSource} onChange={event => setSelectedSource(event.target.value)}>{catalog.map(item => <option key={item.source} value={item.source}>{item.label}</option>)}</select></label><label>Date<select value={selectedDate} onChange={event => setSelectedDate(event.target.value)}>{availableDates.map(item => <option key={item.run_date} value={item.run_date}>{item.run_date} · {item.data_mode}</option>)}</select></label><label>Route<select value={selectedRoute} onChange={event => setSelectedRoute(event.target.value)} disabled={!routeCatalog.length}><option value="">Select route</option>{routeCatalog.map(route => <option key={route.route} value={route.route}>{route.route}</option>)}</select></label></div></div>
      <div className="button-row table-tools"><button type="button" onClick={() => fetchCsv(selectedRoute)} disabled={!selectedRoute || exporting || sourceLoading}>Export route CSV</button><button type="button" onClick={() => fetchCsv("")} disabled={!selectedDate || exporting || routeLoading}>Export whole date CSV</button><span className="muted">{routeLoading ? "Loading route catalog..." : `${routeCatalog.length} routes available`}</span></div>
      {sourceLoading ? <p className="muted">Loading selected route observations...</p> : sourceData ? <><p className="muted">{sourceData.label} · {sourceData.route} · {sourceData.run_date} · {sourceData.data_mode} · {sourceData.total_rows.toLocaleString()} observations</p><div className="table-scroll"><table><thead><tr><th>Observed</th><th>Travel date</th><th>Lead</th><th>Airline</th><th>Flight</th><th>Fare</th><th>Stops</th><th>Status</th></tr></thead><tbody>{sourceData.rows.map((row, index) => <tr key={`${row.route}-${row.flight_number}-${index}`}><td>{row.observed_at || "n/a"}</td><td>{row.travel_date || "n/a"}</td><td>{row.lead_window || "n/a"}</td><td>{row.airline_name || row.airline_code || "n/a"}</td><td>{row.flight_number || "n/a"}</td><td>{row.fare == null ? "n/a" : `${row.currency || "INR"} ${Number(row.fare).toLocaleString("en-IN")}`}</td><td>{row.number_of_stops == null ? "n/a" : row.number_of_stops}</td><td>{row.availability_status || "UNKNOWN"}</td></tr>)}</tbody></table></div></> : <p className="muted">Select a source date and route to inspect observations.</p>}
    </div>
    <div className="panel wide"><h2>Selected Route Fare Curve</h2>{selectedRoute && <p className="muted">Average payable fare by advance-purchase window for {selectedRoute}. Hover a point for its exact value.</p>}<TrendChart data={leadChart.map(row => ({ date: row.label, value: row.value }))} yLabel="Average fare (Rs)" color="#0f4c81" /></div>
    <div className="panel"><h2>Stops Profile</h2><BarChart data={stopChart} valueLabel="Observations" /></div>
    <div className="panel full"><h2>Route Coverage</h2><div className="table-scroll"><table><thead><tr><th>Route and airports</th><th>Airlines</th><th>Lead windows</th><th>Average fare</th><th>Lowest</th><th>Usable rows</th></tr></thead><tbody>{routeCatalog.map(route => <tr key={route.route} onClick={() => setSelectedRoute(route.route)}><td><strong>{route.route}</strong><br /><span className="muted">{airports[route.origin]?.name || route.origin} to {airports[route.destination]?.name || route.destination}</span></td><td>{route.airlines?.join(", ") || "n/a"}</td><td>{route.lead_windows?.join(", ") || "n/a"}</td><td>{route.average_fare == null ? "n/a" : `Rs ${route.average_fare.toLocaleString("en-IN")}`}</td><td>{route.lowest_fare == null ? "n/a" : `Rs ${route.lowest_fare.toLocaleString("en-IN")}`}</td><td>{route.usable_count.toLocaleString("en-IN")}</td></tr>)}</tbody></table></div></div>
    <div className="panel full"><h2>Data & Methodology</h2><p className="muted">Clean files are date-partitioned, route-complete, normalized JSON artifacts. Raw source snapshots remain the audit trail. Fare components stay null when the source does not expose them separately.</p></div>
    <div className="panel"><h2>Route Basket</h2><div className="metric">{basket?.top_n ?? "..."}</div><div className="muted">DGCA route pairs, {basket?.count ?? "..."} directed routes</div></div><div className="panel"><h2>Valid Rows</h2><div className="metric">{(statuses.valid ?? 0).toLocaleString()}</div><div className="muted">Accepted source observations</div></div><div className="panel"><h2>Flagged Rows</h2><div className="metric">{(statuses.suspicious ?? 0).toLocaleString()}</div><div className="muted">Retained for review</div></div>
    <div className="panel wide"><h2>Source Health</h2><table><thead><tr><th>Source</th><th>Status</th><th>Last success</th></tr></thead><tbody>{sources.length ? sources.map(source => <tr key={source.name}><td>{source.label || source.name}<br /><span className="muted">{source.data_mode || "date-partitioned files"}</span></td><td>{source.status}</td><td>{source.last_success || "N/A"}</td></tr>) : <tr><td colSpan="3">No source status rows are available.</td></tr>}</tbody></table></div>
    <div className="panel wide"><h2>CompareFlights Field Mapping</h2><table><thead><tr><th>Source field</th><th>Standard field</th><th>Transformation</th></tr></thead><tbody>{(mapping?.mapping || []).map(row => <tr key={`${row.source_field}-${row.standard_field}`}><td>{row.source_field}</td><td>{row.standard_field}</td><td>{row.transformation}</td></tr>)}</tbody></table></div>
  </section></main>;
}
