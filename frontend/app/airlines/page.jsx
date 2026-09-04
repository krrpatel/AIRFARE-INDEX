"use client";
import { useEffect, useMemo, useState } from "react";
import MultiLineChart from "../components/MultiLineChart";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export default function AirlineAnalysis() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [sort, setSort] = useState("fare");
  const [selectedAirline, setSelectedAirline] = useState("");
  const [topN, setTopN] = useState(5);
  const [routeData, setRouteData] = useState(null);
  const [scrape, setScrape] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/api/airlines`).then(r => r.json()).then(setData).catch(e => setError(String(e)));
    fetch(`${API_URL}/api/scrape-overview`).then(r => r.json()).then(setScrape).catch(() => setScrape(null));
  }, []);

  useEffect(() => {
    const first = data?.airlines?.[0]?.airline_code;
    if (!selectedAirline && first) setSelectedAirline(first);
  }, [data, selectedAirline]);

  useEffect(() => {
    if (!selectedAirline) return;
    fetch(`${API_URL}/api/airlines/${selectedAirline}/routes?top_n=${topN}`).then(r => r.ok ? r.json() : null).then(setRouteData).catch(() => setRouteData(null));
  }, [selectedAirline, topN]);

  const airlines = useMemo(() => [...(data?.airlines || [])].sort((a, b) => {
    if (sort === "index") return (b.latest_index_value ?? -Infinity) - (a.latest_index_value ?? -Infinity);
    if (sort === "coverage") return (b.route_coverage ?? 0) - (a.route_coverage ?? 0);
    if (sort === "volatility") return (a.fare_volatility_coeff ?? Infinity) - (b.fare_volatility_coeff ?? Infinity);
    return (a.latest_avg_fare ?? Infinity) - (b.latest_avg_fare ?? Infinity);
  }), [data, sort]);

  return (
    <main className="page">
      <section className="grid">
        <div className="panel full">
          <h2>Airline Analysis</h2>
          <p className="muted">Airline figures are computed from stored fare observations and are supplementary to the national index.</p>
          {error && <div className="notice">{error}</div>}
          <div className="button-row table-tools"><label htmlFor="airline-sort">Sort airlines</label><select id="airline-sort" value={sort} onChange={e => setSort(e.target.value)}><option value="fare">Average fare</option><option value="index">Index</option><option value="coverage">Route coverage</option><option value="volatility">Fare volatility</option></select></div>
          <table>
            <thead>
              <tr><th>Airline</th><th>Latest avg fare</th><th>Index</th><th>Route coverage</th><th>Fare volatility</th></tr>
            </thead>
            <tbody>
              {airlines.map(a => (
                <tr key={a.airline_code}>
                  <td>{a.airline_code}</td>
                  <td>{a.latest_avg_fare != null ? `Rs ${a.latest_avg_fare.toLocaleString()}` : "n/a"}</td>
                  <td>{a.latest_index_value?.toFixed(2) ?? "n/a"}</td>
                  <td>{a.route_coverage}</td>
                  <td>{a.fare_volatility_coeff ?? "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">{data?.disclaimer}</p>
        </div>
        <div className="panel full">
          <h2>Latest OTA Scrape</h2>
          <div className="metric-grid"><div><span className="metric-label">Source</span><strong>CompareFlights OTA</strong></div><div><span className="metric-label">Latest import run</span><strong>{scrape?.latest_import_run || "Unavailable"}</strong></div><div><span className="metric-label">Latest indexed date</span><strong>{scrape?.latest_database_date || "Unavailable"}</strong></div><div><span className="metric-label">Weekly observations</span><strong>{scrape?.observation_count?.toLocaleString() || "Unavailable"}</strong></div></div>
          <p className="muted">Scraped/imported dates are shown exactly as available from the OTA collection pipeline. A missing day is not filled with an estimated fare.</p>
        </div>
        <div className="panel full">
          <h2>Weekly Airfare Overview</h2>
          <MultiLineChart series={scrape?.airlines || []} yLabel="Average fare (Rs)" />
          <p className="muted">Each line is an airline average across the latest seven available observation dates. Hover points for the exact date, fare, and threshold status.</p>
        </div>
        <div className="panel full">
          <h2>Airline Route Mix</h2>
          <div className="button-row table-tools"><label htmlFor="airline-select">Airline</label><select id="airline-select" value={selectedAirline} onChange={e => setSelectedAirline(e.target.value)}>{(data?.airlines || []).map(a => <option key={a.airline_code} value={a.airline_code}>{a.airline_code}</option>)}</select><label htmlFor="route-top-n">Top routes</label><select id="route-top-n" value={topN} onChange={e => setTopN(Number(e.target.value))}><option value="3">Top 3</option><option value="5">Top 5</option><option value="10">Top 10</option><option value="15">Top 15</option></select></div>
          <MultiLineChart series={routeData?.routes || []} yLabel="Average fare (Rs)" />
          <p className="muted">Each line is one of the selected airline's highest-observation routes. Compare route-level fare movement and market concentration before interpreting the airline index.</p>
        </div>
      </section>
    </main>
  );
}
