"use client";
import { useEffect, useState } from "react";
import { downloadCsv } from "../lib/csv";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export default function DataMethod() {
  const [basket, setBasket] = useState(null);
  const [quality, setQuality] = useState(null);
  const [sources, setSources] = useState([]);
  const [mapping, setMapping] = useState(null);
  const [error, setError] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [selectedSource, setSelectedSource] = useState("compareflights");
  const [selectedDate, setSelectedDate] = useState("");
  const [sourceData, setSourceData] = useState(null);
  const [sourceLoading, setSourceLoading] = useState(false);

  useEffect(() => {
    Promise.all([
      fetch(`${API_URL}/api/dgca/basket?top_n=50&direction_mode=bidirectional`).then(r => r.json()),
      fetch(`${API_URL}/api/data-quality`).then(r => r.json()),
      fetch(`${API_URL}/api/source-health`).then(r => r.json()),
      fetch(`${API_URL}/api/source-field-mapping`).then(r => r.json()),
      fetch(`${API_URL}/api/source-data/dates`).then(r => r.json()),
    ]).then(([basketData, qualityData, sourceData, mappingData, catalogData]) => {
      setBasket(basketData);
      setQuality(qualityData);
      setSources(Array.isArray(sourceData) ? sourceData : []);
      setMapping(mappingData);
      setCatalog(catalogData.sources || []);
    }).catch(e => setError(String(e)));
  }, []);

  const availableDates = catalog.find(item => item.source === selectedSource)?.dates || [];

  useEffect(() => {
    if (!availableDates.length) {
      setSelectedDate("");
      setSourceData(null);
      return;
    }
    if (!availableDates.some(item => item.run_date === selectedDate)) setSelectedDate(availableDates[0].run_date);
  }, [selectedSource, catalog]);

  useEffect(() => {
    if (!selectedSource || !selectedDate) return;
    setSourceLoading(true);
    fetch(`${API_URL}/api/source-data?source=${selectedSource}&run_date=${selectedDate}&limit=1000`)
      .then(response => response.ok ? response.json() : response.json().then(body => Promise.reject(new Error(body.detail || "Could not load source data"))))
      .then(setSourceData)
      .catch(e => setError(String(e)))
      .finally(() => setSourceLoading(false));
  }, [selectedSource, selectedDate]);

  async function exportSourceData() {
    if (!selectedDate) return;
    const response = await fetch(`${API_URL}/api/source-data?source=${selectedSource}&run_date=${selectedDate}&limit=200000`);
    if (!response.ok) return;
    const body = await response.json();
    const columns = ["source", "run_date", "observed_at", "route", "origin", "destination", "travel_date", "lead_window", "airline_code", "airline_name", "flight_number", "fare", "currency", "availability_status", "fare_code"];
    downloadCsv(`${selectedSource}-${selectedDate}.csv`, columns, body.rows || []);
  }

  const statuses = quality?.by_quality_status || {};

  return (
    <main className="page">
      {error && <div className="notice">API error: {error}</div>}
      <section className="grid">
        <div className="panel full">
          <div className="section-heading">
            <div><h2>Source Data Explorer</h2><p className="muted">Inspect the date-partitioned source snapshot through one normalized view. Export always includes every row for the selected source and date.</p></div>
            <div className="button-row">
              <label>Source<select value={selectedSource} onChange={event => setSelectedSource(event.target.value)}>{catalog.map(item => <option key={item.source} value={item.source}>{item.label}</option>)}</select></label>
              <label>Date<select value={selectedDate} onChange={event => setSelectedDate(event.target.value)}>{availableDates.map(item => <option key={item.run_date} value={item.run_date}>{item.run_date} · {item.file_count} files</option>)}</select></label>
              <button type="button" onClick={exportSourceData} disabled={!selectedDate || sourceLoading}>Export CSV</button>
            </div>
          </div>
          {sourceLoading ? <p className="muted">Loading source snapshot...</p> : sourceData ? <><p className="muted">{sourceData.label} · {sourceData.run_date} · showing {sourceData.rows.length.toLocaleString()} of {sourceData.total_rows.toLocaleString()} normalized observations</p><div className="table-scroll"><table><thead><tr><th>Observed</th><th>Route</th><th>Travel date</th><th>Lead</th><th>Airline</th><th>Flight</th><th>Fare</th><th>Status</th></tr></thead><tbody>{sourceData.rows.map((row, index) => <tr key={`${row.route}-${row.flight_number}-${index}`}><td>{row.observed_at || "n/a"}</td><td><strong>{row.route}</strong></td><td>{row.travel_date || "n/a"}</td><td>{row.lead_window || "n/a"}</td><td>{row.airline_name || row.airline_code || "n/a"}</td><td>{row.flight_number || "n/a"}</td><td>{row.fare == null ? "n/a" : `${row.currency || "INR"} ${Number(row.fare).toLocaleString("en-IN")}`}</td><td>{row.availability_status || "UNKNOWN"}</td></tr>)}</tbody></table></div></> : <p className="muted">No dated source snapshots are available.</p>}
        </div>

        <div className="panel full">
          <h2>Data & Methodology</h2>
          <p className="muted">
            This page exposes the source trail behind the dashboard: DGCA passenger-traffic
            weights, local observation quality, source status, and the CompareFlights field mapping.
          </p>
        </div>

        <div className="panel">
          <h2>Route Basket</h2>
          <div className="metric">{basket?.top_n ?? "..."}</div>
          <div className="muted">DGCA route pairs, {basket?.count ?? "..."} directed collection routes</div>
        </div>
        <div className="panel">
          <h2>DGCA Window</h2>
          <div className="metric small-metric">{basket?.metadata?.window_start ?? "..."} to {basket?.metadata?.window_end ?? "..."}</div>
          <div className="muted">Latest cached 12 complete months</div>
        </div>
        <div className="panel">
          <h2>Valid Rows</h2>
          <div className="metric">{(statuses.valid ?? 0).toLocaleString()}</div>
          <div className="muted">Rows accepted into local index-ready observations</div>
        </div>
        <div className="panel">
          <h2>Flagged Rows</h2>
          <div className="metric">{(statuses.suspicious ?? 0).toLocaleString()}</div>
          <div className="muted">Kept in the database with quality status</div>
        </div>

        <div className="panel wide">
          <h2>Source Health</h2>
          <table>
            <thead><tr><th>Source</th><th>Status</th><th>Last success</th></tr></thead>
            <tbody>
              {sources.length ? sources.map(source => (
                <tr key={source.name}>
                  <td>{source.name}<br /><span className="muted">{source.data_mode || "observed source"}</span></td>
                  <td>{source.status}</td>
                  <td>{source.last_success ?? "N/A"}</td>
                </tr>
              )) : <tr><td colSpan="3">No source status rows are available.</td></tr>}
            </tbody>
          </table>
        </div>

        <div className="panel wide">
          <h2>Method Choices</h2>
          <ul className="compact-list">
            <li>National aggregation uses DGCA-derived fixed route-pair traffic weights.</li>
            <li>Direction is preserved for observations; bidirectional display splits pair weight evenly.</li>
            <li>Median fare is the representative price where observations are sufficient.</li>
            <li>Unavailable base fare, tax, and fee components are stored as null, never zero.</li>
          </ul>
        </div>

        <div className="panel full">
          <h2>CompareFlights Field Mapping</h2>
          <table>
            <thead><tr><th>Source field</th><th>Standard field</th><th>Transformation</th></tr></thead>
            <tbody>
              {(mapping?.mapping || []).map(row => (
                <tr key={`${row.source_field}-${row.standard_field}`}>
                  <td>{row.source_field}</td>
                  <td>{row.standard_field}</td>
                  <td>{row.transformation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
