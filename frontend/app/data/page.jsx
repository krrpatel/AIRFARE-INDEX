"use client";
import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export default function DataMethod() {
  const [basket, setBasket] = useState(null);
  const [quality, setQuality] = useState(null);
  const [sources, setSources] = useState([]);
  const [mapping, setMapping] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      fetch(`${API_URL}/api/dgca/basket?top_n=50&direction_mode=bidirectional`).then(r => r.json()),
      fetch(`${API_URL}/api/data-quality`).then(r => r.json()),
      fetch(`${API_URL}/api/source-health`).then(r => r.json()),
      fetch(`${API_URL}/api/source-field-mapping`).then(r => r.json()),
    ]).then(([basketData, qualityData, sourceData, mappingData]) => {
      setBasket(basketData);
      setQuality(qualityData);
      setSources(Array.isArray(sourceData) ? sourceData : []);
      setMapping(mappingData);
    }).catch(e => setError(String(e)));
  }, []);

  const statuses = quality?.by_quality_status || {};

  return (
    <main className="page">
      {error && <div className="notice">API error: {error}</div>}
      <section className="grid">
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
