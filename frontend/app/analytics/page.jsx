"use client";
import { useEffect, useState } from "react";
import BarChart from "../components/BarChart";
import TrendChart from "../components/TrendChart";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/backend";

export default function AnalyticsPage() {
  const [data, setData] = useState(null);
  const [routeData, setRouteData] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState("");
  const [error, setError] = useState(null);
  useEffect(() => { Promise.all([fetch(`${API_URL}/api/analytics`), fetch(`${API_URL}/api/routes/analytics`)]).then(async ([analyticsResponse, routesResponse]) => { if (!analyticsResponse.ok || !routesResponse.ok) throw new Error("Analytics data could not be loaded"); const analytics = await analyticsResponse.json(); const routes = await routesResponse.json(); setData(analytics); setRouteData(routes.routes || []); }).catch(e => setError(e.message)); }, []);
  const ranking = data?.route_ranking || [];
  const routeOptions = [...new Set(routeData.map(row => `${row.origin}-${row.destination}`))].sort();
  const activeRoute = selectedRoute || routeOptions[0] || "";
  const selectedAirlines = routeData.filter(row => `${row.origin}-${row.destination}` === activeRoute).sort((a, b) => b.average_fare - a.average_fare);
  return <main className="page"><section className="hero"><div><div className="eyebrow">Decision support</div><h1>Fare Market Analytics</h1><p>Route concentration, fare distribution, movement, and seasonal context for review of the Airfare Price Index basket.</p></div></section>{error && <div className="notice">{error}</div>}<section className="grid">
    <div className="panel wide"><div className="section-heading"><div><h2>Average Fare Ranking</h2><p className="muted">Compare all available airlines for one selected route.</p></div><label>Route<select value={activeRoute} onChange={event => setSelectedRoute(event.target.value)}><option value="">Select route</option>{routeOptions.map(route => <option key={route} value={route}>{route}</option>)}</select></label></div><BarChart data={selectedAirlines.map(row => ({ label: row.airline_code, value: row.average_fare }))} valueLabel="Average fare (Rs)" /></div>
    <div className="panel full"><h2>{activeRoute || "Selected route"} Airline Fare Detail</h2><table><thead><tr><th>Airline</th><th>Average fare</th><th>Median fare</th><th>Lowest</th><th>Highest</th><th>Observations</th></tr></thead><tbody>{selectedAirlines.map(row => <tr key={`${row.origin}-${row.destination}-${row.airline_code}`}><td><strong>{row.airline_code}</strong></td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td><td>Rs {row.median_fare.toLocaleString("en-IN")}</td><td>Rs {row.lowest_fare.toLocaleString("en-IN")}</td><td>Rs {row.highest_fare.toLocaleString("en-IN")}</td><td>{row.observation_count.toLocaleString("en-IN")}</td></tr>)}</tbody></table>{!selectedAirlines.length && <p className="muted">Select a route with available airline observations.</p>}</div>
    <div className="panel"><h2>Cheapest Sectors</h2><table><thead><tr><th>Route</th><th>Average fare</th></tr></thead><tbody>{ranking.slice(0, 8).map(row => <tr key={`low-${row.route}`}><td>{row.route}</td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td></tr>)}</tbody></table></div>
    <div className="panel"><h2>Highest Sectors</h2><table><thead><tr><th>Route</th><th>Average fare</th></tr></thead><tbody>{[...ranking].reverse().slice(0, 8).map(row => <tr key={`high-${row.route}`}><td>{row.route}</td><td>Rs {row.average_fare.toLocaleString("en-IN")}</td></tr>)}</tbody></table></div>
    <div className="panel wide"><h2>Fare Distribution</h2><BarChart data={(data?.fare_distribution || []).map(row => ({ label: row.bucket, value: row.count }))} valueLabel="Valid observations" /></div>
    <div className="panel wide"><h2>Monthly Seasonality</h2><TrendChart data={(data?.seasonality || []).map(row => ({ date: row.month, value: row.average_fare }))} yLabel="Average fare (Rs)" color="#b42318" /></div>
  </section></main>;
}
