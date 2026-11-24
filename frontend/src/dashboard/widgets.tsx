import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { categoryColor } from "./palette";

/** Counts up to the target number when it changes. */
export function useCountUp(target: number, ms = 700): number {
  const [value, setValue] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const start = performance.now();
    const origin = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      const v = origin + (target - origin) * eased;
      setValue(v);
      from.current = v;
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

interface KpiProps {
  label: string;
  value: number | null;
  display?: string;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
  icon: string;
  onClick?: () => void;
}

export function Kpi({ label, value, display, hint, tone = "default", icon, onClick }: KpiProps) {
  const n = useCountUp(value ?? 0);
  return (
    <button className={`kpi tone-${tone}`} onClick={onClick} disabled={!onClick} title={onClick ? "Click to see these grievances" : undefined}>
      <span className="kpi-icon" aria-hidden>{icon}</span>
      <span className="kpi-value">{display ?? (value === null ? "-" : Math.round(n).toLocaleString())}</span>
      <span className="kpi-label">{label}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </button>
  );
}

export function Ring({ value, size = 56, color = "#6366f1", children }: { value: number; size?: number; color?: string; children?: ReactNode }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  return (
    <span className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e5e7eb" strokeWidth="6" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${Math.max(0, Math.min(1, value)) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dasharray .8s ease" }}
        />
      </svg>
      <span className="ring-text">{children}</span>
    </span>
  );
}

interface HeatmapProps {
  regions: { id: number; name: string }[];
  categories: string[];
  cells: { region_id: number; category: string; count: number }[];
  onCell: (regionId: number, category: string) => void;
}

export function Heatmap({ regions, categories, cells, onCell }: HeatmapProps) {
  const max = Math.max(1, ...cells.map((c) => c.count));
  const lookup = new Map(cells.map((c) => [`${c.region_id}|${c.category}`, c.count]));
  return (
    <div className="heatmap" style={{ gridTemplateColumns: `130px repeat(${categories.length}, minmax(64px, 1fr))` }}>
      <span />
      {categories.map((c) => (
        <span key={c} className="hm-head" title={c} style={{ color: categoryColor(c) }}>{c}</span>
      ))}
      {regions.map((r) => (
        <Fragment key={r.id}>
          <span className="hm-row">{r.name}</span>
          {categories.map((c) => {
            const n = lookup.get(`${r.id}|${c}`) ?? 0;
            const a = n === 0 ? 0 : 0.12 + 0.88 * (n / max);
            return (
              <button
                key={`${r.id}${c}`} className="hm-cell" disabled={n === 0}
                style={{ background: `rgba(99,102,241,${a})`, color: a > 0.55 ? "#fff" : "#1e293b" }}
                title={`${r.name} · ${c}: ${n}`} onClick={() => onCell(r.id, c)}
              >
                {n || ""}
              </button>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, className = "" }: {
  title?: string; subtitle?: string; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <div><h3>{title}</h3>{subtitle && <p className="muted small">{subtitle}</p>}</div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="skeleton" style={{ height: h }} />;
}
