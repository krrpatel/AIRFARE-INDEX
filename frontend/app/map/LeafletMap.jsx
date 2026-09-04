"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

function FitIndia({ boundary }) {
  const map = useMap();

  useEffect(() => {
    if (!boundary) return;

    map.fitBounds(
      [
        [6, 68],
        [38, 98],
      ],
      {
        padding: [18, 18],
      }
    );
  }, [boundary, map]);

  return null;
}

function color(value) {
  if (value >= 105) return "#b42318";
  if (value <= 95) return "#138a36";
  return "#0f4c81";
}

export default function LeafletMap({
  routes = [],
  boundary,
  filter = "all",
}) {
  const [selected, setSelected] = useState(null);

  const airports = useMemo(() => {
    const result = {};

    routes.forEach((route) => {
      if (route.origin_coords) {
        result[route.origin] = route.origin_coords;
      }

      if (route.destination_coords) {
        result[route.destination] = route.destination_coords;
      }
    });

    return result;
  }, [routes]);

  const shown = useMemo(() => {
    return routes.filter(
      (route) =>
        filter === "all" ||
        route.origin === filter ||
        route.destination === filter
    );
  }, [routes, filter]);

  return (
    <div className="leaflet-shell">
      <MapContainer
        center={[22.5, 79]}
        zoom={4.5}
        scrollWheelZoom={true}
        className="leaflet-map"
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <FitIndia boundary={boundary} />

        {boundary && (
          <GeoJSON
            data={boundary}
            style={{
              color: "#547b69",
              weight: 1.2,
              fillColor: "#dcece2",
              fillOpacity: 0.62,
            }}
          />
        )}

        {shown.map((route) => {
          const positions = [
            [
              route.origin_coords?.lat,
              route.origin_coords?.lon,
            ],
            [
              route.destination_coords?.lat,
              route.destination_coords?.lon,
            ],
          ];

          const invalid = positions.some((point) =>
            point.some((value) => value == null)
          );

          if (invalid) return null;

          return (
            <Polyline
              key={`${route.origin}-${route.destination}`}
              positions={positions}
              pathOptions={{
                color: color(route.latest_index_value),
                weight:
                  2 + Math.min(4, (route.weight || 0) * 30),
                opacity: 0.72,
              }}
              eventHandlers={{
                click: () => setSelected(route),
              }}
            >
              <Tooltip sticky>
                {route.origin}-{route.destination} | index{" "}
                {route.latest_index_value?.toFixed(2) || "n/a"}
              </Tooltip>
            </Polyline>
          );
        })}

        {Object.entries(airports).map(([code, airport]) => {
          if (airport?.lat == null || airport?.lon == null) {
            return null;
          }

          return (
            <CircleMarker
              key={code}
              center={[airport.lat, airport.lon]}
              radius={6}
              pathOptions={{
                color: "#09365f",
                fillColor: "#1672b8",
                fillOpacity: 0.9,
              }}
              eventHandlers={{
                click: () =>
                  setSelected({
                    airport,
                    code,
                  }),
              }}
            >
              <Tooltip direction="right">
                {airport.name || code} ({code})
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>

      {selected && (
        <div className="map-info">
          <button
            className="map-close"
            type="button"
            onClick={() => setSelected(null)}
            aria-label="Close map details"
          >
            ×
          </button>

          {selected.airport ? (
            <>
              <strong>
                {selected.airport.name || selected.code} (
                {selected.code})
              </strong>

              <span>
                {selected.airport.airport_name ||
                  "Airport details unavailable"}
              </span>

              <span>
                Coordinates: {selected.airport.lat},{" "}
                {selected.airport.lon}
              </span>
            </>
          ) : (
            <>
              <strong>
                {selected.origin}-{selected.destination}
              </strong>

              <span>
                {selected.label || "Route details unavailable"}
              </span>

              <span>
                Latest route index:{" "}
                {selected.latest_index_value?.toFixed(2) ||
                  "Unavailable"}
              </span>

              <span>
                DGCA weight:{" "}
                {selected.weight != null
                  ? `${(selected.weight * 100).toFixed(3)}%`
                  : "Unavailable"}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}