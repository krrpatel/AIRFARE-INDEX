"use client";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const money = value => Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });

export default function TrendChart({ data = [], yLabel = "Index value", color = "#2563eb" }) {
  const clean = data.filter(point => point && point.value != null && Number.isFinite(Number(point.value))).map(point => ({ ...point, value: Number(point.value) }));
  if (!clean.length) return <p className="muted">No data to chart yet.</p>;
  const indexChart = yLabel.toLowerCase().includes("index");
  const values = clean.map(point => point.value);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const spread = maxValue - minValue || Math.max(1, Math.abs(maxValue) * 0.02);
  const padding = Math.max(spread * 0.12, indexChart ? 1.5 : spread * 0.05);
  const yDomain = [Math.floor(minValue - padding), Math.ceil(maxValue + padding)];
  return <div className="chart-shell chart-recharts" role="img" aria-label={yLabel}>
    <ResponsiveContainer width="100%" height={310}>
      <AreaChart data={clean} margin={{ top: 12, right: 20, left: 4, bottom: 22 }}>
        <defs><linearGradient id={`trend-fill-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.28} /><stop offset="100%" stopColor={color} stopOpacity={0.03} /></linearGradient></defs>
        <CartesianGrid stroke="#dfe7ee" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" type="category" tick={{ fontSize: 11, fill: "#5d6978" }} minTickGap={28} tickMargin={8} />
        <YAxis domain={yDomain} allowDataOverflow={false} tick={{ fontSize: 11, fill: "#5d6978" }} tickFormatter={value => indexChart ? Number(value).toFixed(0) : money(value)} width={58} label={{ value: yLabel, angle: -90, position: "insideLeft", fill: "#5d6978", fontSize: 11 }} />
        <Tooltip content={({ active, payload, label }) => active && payload?.length ? <div className="recharts-tooltip"><strong>{label}</strong><span>{yLabel}: {money(payload[0].value)}</span></div> : null} />
        {indexChart && <ReferenceLine y={100} stroke="#9aa8b6" strokeDasharray="5 5" label={{ value: "Base 100", position: "insideTopRight", fill: "#5d6978", fontSize: 11 }} />}
        <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} fill={`url(#trend-fill-${color.replace("#", "")})`} dot={{ r: clean.length > 80 ? 2 : 3, strokeWidth: 1.5, fill: color }} activeDot={{ r: 6, stroke: "#fff", strokeWidth: 2 }} isAnimationActive animationDuration={650} />
      </AreaChart>
    </ResponsiveContainer>
  </div>;
}
