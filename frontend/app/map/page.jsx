"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "/api/backend";

const LeafletMap = dynamic(
  () => import("./LeafletMap"),
  {
    ssr: false,
    loading: () => (
      <p className="muted">
        Loading geographic layer...
      </p>
    ),
  }
);

export default function IndiaMap() {
  const [mounted, setMounted] = useState(false);
  const [routes, setRoutes] = useState([]);
  const [boundary, setBoundary] = useState(null);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    // Wait until the browser has mounted the page.
    setMounted(true);

    fetch(`${API_URL}/api/map`)
      .then((r) => r.json())
      .then((data) => {
        setRoutes(data.routes || []);
      })
      .catch((error) => {
        console.error("Failed to load map routes:", error);
        setRoutes([]);
      });

    fetch("/india-composite.geojson")
      .then((r) => r.json())
      .then(setBoundary)
      .catch(() => setBoundary(null));
  }, []);

  const airports = useMemo(
    () =>
      [
        ...new Set(
          routes.flatMap((route) => [
            route.origin,
            route.destination,
          ])
        ),
      ].sort(),
    [routes]
  );

  const shown = useMemo(
    () =>
      routes.filter(
        (route) =>
          filter === "all" ||
          route.origin === filter ||
          route.destination === filter
      ),
    [routes, filter]
  );

  return (
    <main className="page">
      <section className="grid">
        <div className="panel full">
          <h2>India Domestic Route Map</h2>

          <div className="button-row">
            <label htmlFor="airport">
              Airport filter
            </label>

            <select
              id="airport"
              value={filter}
              onChange={(event) =>
                setFilter(event.target.value)
              }
            >
              <option value="all">
                All airports
              </option>

              {airports.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </div>

          <p className="muted">
            DGCA-weighted domestic route network. Hover a
            route or airport for a quick overview; click for
            persistent details. Map tiles and boundaries are
            loaded from open geographic sources.
          </p>
        </div>

        <div className="panel full">
          {mounted ? (
            <LeafletMap
              routes={routes}
              boundary={boundary}
              filter={filter}
            />
          ) : (
            <p className="muted">
              Loading geographic layer...
            </p>
          )}
        </div>

        <div className="panel full">
          <h2>Displayed Routes</h2>

          <table>
            <thead>
              <tr>
                <th>Route</th>
                <th>Latest index</th>
                <th>Weight</th>
              </tr>
            </thead>

            <tbody>
              {shown.slice(0, 50).map((route) => (
                <tr
                  key={`${route.origin}-${route.destination}`}
                >
                  <td>
                    {route.origin}-{route.destination}
                  </td>

                  <td>
                    {route.latest_index_value?.toFixed(2) ||
                      "n/a"}
                  </td>

                  <td>
                    {route.weight != null
                      ? `${(route.weight * 100).toFixed(3)}%`
                      : "n/a"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}