import type { ReactNode } from "react";

export const fmt = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "-";

export const Badge = ({ kind, value }: { kind: "sev" | "st" | "sent"; value: string | null }) =>
  value ? <span className={`badge ${kind}-${value}`}>{value.replace("_", " ")}</span> : <span className="muted">-</span>;

export function Bars({ data, max }: { data: { name: string; count: number }[]; max?: number }) {
  const top = max ?? Math.max(1, ...data.map((d) => d.count));
  if (!data.length) return <p className="muted">No data</p>;
  return (
    <div className="bars">
      {data.map((d) => (
        <div className="bar-row" key={d.name}>
          <span className="bar-label">{d.name}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(100 * d.count) / top}%` }} /></span>
          <span className="bar-count">{d.count}</span>
        </div>
      ))}
    </div>
  );
}

export const Stat = ({ label, value, warn }: { label: string; value: ReactNode; warn?: boolean }) => (
  <div className={`stat${warn ? " warn" : ""}`}>
    <div className="stat-value">{value}</div>
    <div className="muted small">{label}</div>
  </div>
);
