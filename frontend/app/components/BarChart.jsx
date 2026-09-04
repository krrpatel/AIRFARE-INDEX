"use client";
import { Bar, BarChart as RechartsBarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COLORS = ["#0f4c81", "#1672b8", "#138a36", "#9a6700", "#b42318", "#007c91"];
export default function BarChart({ data = [], labelKey = "label", valueKey = "value", valueLabel = "Value" }) {
  const values = data.filter(row => row && Number.isFinite(Number(row[valueKey]))).map(row => ({ ...row, [valueKey]: Number(row[valueKey]) }));
  if (!values.length) return null;
  return <div className="chart-shell chart-recharts" role="img" aria-label={valueLabel}><ResponsiveContainer width="100%" height={Math.max(280, values.length * 32)}><RechartsBarChart data={values} layout="vertical" margin={{ top: 6, right: 18, left: 10, bottom: 6 }}><CartesianGrid stroke="#dfe7ee" strokeDasharray="3 3" horizontal={false} /><XAxis type="number" tick={{ fontSize: 11, fill: "#5d6978" }} tickFormatter={value => Number(value).toLocaleString("en-IN")} /><YAxis type="category" dataKey={labelKey} width={145} tick={{ fontSize: 11, fill: "#344457" }} tickFormatter={value => String(value).length > 22 ? `${String(value).slice(0, 22)}...` : value} /><Tooltip formatter={value => [Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 }), valueLabel]} contentStyle={{ borderRadius: 8, border: "1px solid #cfd7df", boxShadow: "0 6px 18px rgba(9,54,95,.12)" }} /><Bar dataKey={valueKey} name={valueLabel} radius={[0, 5, 5, 0]} isAnimationActive animationDuration={650}>{values.map((row, index) => <Cell key={`${row[labelKey]}-${index}`} fill={COLORS[index % COLORS.length]} />)}</Bar></RechartsBarChart></ResponsiveContainer></div>;
}
