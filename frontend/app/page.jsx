"use client";
import { useEffect, useState } from "react";
import TrendChart from "./components/TrendChart";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

function fmtPct(v) {
  return v == null ? "n/a" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
}

export default function Home() {
  const [current, setCurrent] = useState(null);
  const [history, setHistory] = useState([]);
  const [dq, setDq] = useState(null);
  const [dgca, setDgca] = useState(null);
  const [lead, setLead] = useState(null);
  const [sourceHealth, setSourceHealth] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [movers, setMovers] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      fetch(`${API_URL}/api/index/current`).then(r => r.json()),
      fetch(`${API_URL}/api/index/history`).then(r => r.json()),
      fetch(`${API_URL}/api/data-quality`).then(r => r.json()),
      fetch(`${API_URL}/api/dgca/basket?top_n=50&direction_mode=bidirectional`).then(r => r.json()),
      fetch(`${API_URL}/api/lead-time`).then(r => r.json()),
      fetch(`${API_URL}/api/source-health`).then(r => r.json()),
      fetch(`${API_URL}/api/alerts?limit=5`).then(r => r.json()),
      fetch(`${API_URL}/api/index/top-movers?limit=5`).then(r => r.json()),
    ]).then(([cur, hist, quality, basket, leadTime, sources, alertData, moverData]) => {
      setCurrent(cur);
      setHistory(hist.series || []);
      setDq(quality);
      setDgca(basket);
      setLead(leadTime);
      setSourceHealth(Array.isArray(sources) ? sources : []);
      setAlerts(alertData.alerts || []);
      setMovers(moverData);
    }).catch(e => setError(String(e)));
  }, []);

  const leadRows = lead?.series || [];
  const cheapestLead = leadRows.length
    ? leadRows.reduce((best, row) => row.median_fare < best.median_fare ? row : best, leadRows[0])
    : null;
  const sourceFreshness = sourceHealth[0]?.last_success || "N/A";

  return (
    <main className="page">
      <div className="notice">Research dashboard. It is not an official MoSPI/CPI statistic.</div>
      {error && <div className="notice">API error: {error}</div>}

      <section className="hero">
        <div>
          <div className="eyebrow">Smart Automation</div>
          <h1>India Airfare Price Index</h1>
          <p>
            A high-frequency research indicator built from Indian domestic airfare observations,
            DGCA passenger-traffic route weights, and transparent validation. The dashboard keeps
            simulated backcast data visibly separate from imported scraped observations.
          </p>
        </div>
        <div className="hero-facts">
          <span>Top 50 DGCA route-pair basket</span>
          <span>T+1, T+7, T+15, T+30, T+45 lead-time windows</span>
          <span>Nullable fare components when the source does not expose them</span>
        </div>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>Current Index</h2>
          <div className="metric">{current?.index_value?.toFixed(2) ?? "..."}</div>
          <div className="muted">As of {current?.index_date ?? "loading"} | {current?.data_mode ?? ""}</div>
        </div>
        <div className="panel">
          <h2>Weekly Change</h2>
          <div className="metric">{fmtPct(current?.wow_change_pct)}</div>
          <div className="muted">Compared with previous available week</div>
        </div>
        <div className="panel">
          <h2>Monthly Change</h2>
          <div className="metric">{fmtPct(current?.mom_change_pct)}</div>
          <div className="muted">Computed from stored index history</div>
        </div>
        <div className="panel">
          <h2>Observations</h2>
          <div className="metric">{dq?.total_observations?.toLocaleString() ?? "..."}</div>
          <div className="muted">Valid plus flagged observations in local DB</div>
        </div>
        <div className="panel">
          <h2>DGCA Basket</h2>
          <div className="metric">{dgca?.top_n ?? "..."}</div>
          <div className="muted">Route pairs expanded to {dgca?.count ?? "..."} directed routes</div>
        </div>
        <div className="panel">
          <h2>Lead-Time Low</h2>
          <div className="metric">{cheapestLead ? `T+${cheapestLead.advance_purchase_days}` : "N/A"}</div>
          <div className="muted">{cheapestLead ? `Median Rs ${cheapestLead.median_fare.toLocaleString()}` : "Insufficient data"}</div>
        </div>
        <div className="panel">
          <h2>Data Freshness</h2>
          <div className="metric small-metric">{sourceFreshness}</div>
          <div className="muted">Latest successful source run recorded locally</div>
        </div>
        <div className="panel">
          <h2>Quality Flags</h2>
          <div className="metric">{dq?.anomaly_count?.toLocaleString() ?? "..."}</div>
          <div className="muted">Anomalies are flagged for review, not deleted</div>
        </div>

        <div className="panel wide">
          <h2>Index Time Series</h2>
          <TrendChart data={history.map(h => ({ date: h.index_date, value: h.index_value }))} yLabel="Index, base=100" color="#0f4c81" />
        </div>

        <div className="panel wide">
          <h2>Index Monitoring</h2>
          <div className="monitor-grid">
            <div><span className="metric-label">Source status</span><strong>{sourceHealth.length ? sourceHealth.filter(s => s.status === "ONLINE").length + "/" + sourceHealth.length + " online" : "No source status"}</strong></div>
            <div><span className="metric-label">Quality events</span><strong>{dq?.anomaly_count?.toLocaleString() ?? "n/a"}</strong></div>
            <div><span className="metric-label">Latest index date</span><strong>{current?.index_date ?? "n/a"}</strong></div>
          </div>
          <p className="muted">Monitor freshness, source availability, observation coverage, quality events, and day-over-day route movement before publishing a series.</p>
          {alerts.length ? <table><thead><tr><th>Severity</th><th>Date</th><th>Alert</th></tr></thead><tbody>{alerts.map(a => <tr key={a.alert_id}><td>{a.severity}</td><td>{a.index_date}</td><td>{a.message}</td></tr>)}</tbody></table> : <p className="muted">No active alerts recorded.</p>}
          <h3 className="subheading">Source health</h3>
          <table><thead><tr><th>Source</th><th>Status</th><th>Last success</th></tr></thead><tbody>{sourceHealth.map(source => <tr key={source.name}><td>{source.label || source.name}</td><td>{source.status}</td><td>{source.last_success || "Configured; no run recorded"}</td></tr>)}</tbody></table>
        </div>

        <div className="panel wide">
          <h2>Route Movement Watch</h2>
          <table><thead><tr><th>Route</th><th>Current index</th><th>Change</th></tr></thead><tbody>{(movers?.top_increasing || []).slice(0, 5).map(r => <tr key={`up-${r.origin}-${r.destination}`}><td>{r.origin}-{r.destination}</td><td>{r.current_value?.toFixed(2) ?? "n/a"}</td><td className="positive">+{r.change_pct?.toFixed(2)}%</td></tr>)}{(movers?.top_decreasing || []).slice(0, 5).map(r => <tr key={`down-${r.origin}-${r.destination}`}><td>{r.origin}-{r.destination}</td><td>{r.current_value?.toFixed(2) ?? "n/a"}</td><td className="negative">{r.change_pct?.toFixed(2)}%</td></tr>)}</tbody></table>
        </div>
        <div className="panel wide">
          <h2>Lead-Time Analysis</h2>
          <table>
            <thead><tr><th>Advance days</th><th>Median fare</th><th>Observations</th><th>Routes</th></tr></thead>
            <tbody>
              {(lead?.series || []).map(row => (
                <tr key={row.advance_purchase_days}>
                  <td>T+{row.advance_purchase_days}</td>
                  <td>Rs {row.median_fare.toLocaleString()}</td>
                  <td>{row.observation_count.toLocaleString()}</td>
                  <td>{row.route_coverage}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">{lead?.note}</p>
        </div>

        <div className="panel full">
          <h2>DGCA Route Basket</h2>
          <p className="muted">
            Window {dgca?.metadata?.window_start ?? "..."} to {dgca?.metadata?.window_end ?? "..."}.
            Showing {dgca?.count ?? 0} directed routes from Top {dgca?.top_n ?? 50} route pairs.
          </p>
          <table>
            <thead><tr><th>Rank</th><th>Route</th><th>Direction</th><th>Directional weight</th><th>12-month passengers</th></tr></thead>
            <tbody>
              {(dgca?.routes || []).slice(0, 12).map((r, i) => (
                <tr key={`${r.origin}-${r.destination}-${i}`}>
                  <td>{r.rank}</td>
                  <td>{r.pair_route}</td>
                  <td>{r.origin}-{r.destination}</td>
                  <td>{(r.directional_weight * 100).toFixed(3)}%</td>
                  <td>{r.total_pax_12_months.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
