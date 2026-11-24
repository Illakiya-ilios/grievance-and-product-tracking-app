import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Sector, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, download, qs } from "../api";
import { useAuth } from "../auth";
import { categoryColor, hours, pct, SENTIMENT_COLORS, SEVERITY_COLORS } from "../dashboard/palette";
import { Card, Heatmap, Kpi, Ring, Skeleton } from "../dashboard/widgets";
import type { GrievanceItem, Metrics } from "../types";
import { Badge, fmt } from "../ui";

type Count = { name: string; count: number };
interface Summary extends Metrics {
  sla_compliance: number | null;
  urgent_open: number;
  by_category: Count[];
  by_severity: Count[];
  by_sentiment: Count[];
  trend: { date: string; created: number; resolved: number }[];
  recurring_issues: number;
}
type RegionRow = Metrics & { region_id: number; region_name: string; sla_compliance: number | null; urgent_open: number };
type OutletRow = Metrics & { outlet_id: number; outlet_code: string; outlet_name: string; area: string; region_id: number; region_name: string };
interface Heat {
  regions: { id: number; name: string }[];
  categories: string[];
  cells: { region_id: number; category: string; count: number }[];
  severity: ({ region_id: number; region: string } & Record<string, number>)[];
}
interface Recurring {
  id: number; title: string; category: string; region_name: string;
  grievance_count: number; open_count: number; outlet_count: number; last_seen: string;
}
interface Data {
  summary: Summary; regions: RegionRow[]; outlets: OutletRow[]; heat: Heat; insights: string[];
  recurring: Recurring[]; urgent: GrievanceItem[];
}

type SortKey = "total" | "open" | "overdue" | "avg_severity" | "negative_share";
const PERIODS = [7, 30, 90];

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const global = user?.role === "admin" || user?.role === "management";
  const [days, setDays] = useState(30);
  const [regionId, setRegionId] = useState<number | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  const [slice, setSlice] = useState<number | undefined>();
  const [series, setSeries] = useState({ created: true, resolved: true });
  const [sort, setSort] = useState<SortKey>("total");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const q = qs({ days, region_id: regionId });
    try {
      const [summary, regions, outlets, heat, insights, recurring, urgent] = await Promise.all([
        api<Summary>(`/dashboard/summary${q}`),
        api<RegionRow[]>(`/dashboard/regions${qs({ days })}`),
        api<OutletRow[]>(`/dashboard/outlets${q}`),
        api<Heat>(`/dashboard/heatmap${q}`),
        api<{ insights: string[] }>(`/dashboard/insights${q}`),
        api<Recurring[]>(`/dashboard/recurring${qs({ region_id: regionId })}`),
        api<{ items: GrievanceItem[] }>(`/grievances${qs({ urgent: true, region_id: regionId, page_size: 6 })}`),
      ]);
      setData({ summary, regions, outlets, heat, insights: insights.insights, recurring, urgent: urgent.items });
      setUpdated(new Date());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [days, regionId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  const go = (filters: Record<string, string | number | boolean | null | undefined>) =>
    nav(`/staff/grievances${qs({ days, region_id: regionId, ...filters })}`);

  const outlets = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.outlets
      .filter((o) => !q || `${o.outlet_code} ${o.outlet_name} ${o.area} ${o.region_name}`.toLowerCase().includes(q))
      .sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0));
  }, [data, search, sort]);

  const s = data?.summary;
  const maxOutlet = Math.max(1, ...(data?.outlets.map((o) => o.total) ?? [1]));
  const scopeName = regionId ? data?.regions.find((r) => r.region_id === regionId)?.region_name : global ? "All regions" : "Your region";
  const catTotal = s?.by_category.reduce((n, c) => n + c.count, 0) ?? 0;

  return (
    <div className="dash">
      <div className="hero">
        <div>
          <h2>Grievance intelligence</h2>
          <p>{scopeName} · last {days} days{updated && <> · updated {updated.toLocaleTimeString()}</>}</p>
        </div>
        <div className="hero-actions">
          <div className="seg" role="group" aria-label="Period">
            {PERIODS.map((p) => (
              <button key={p} className={p === days ? "on" : ""} onClick={() => setDays(p)}>{p}d</button>
            ))}
          </div>
          <button className="ghost" onClick={() => void load()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
          <button className="ghost" onClick={() => download(`/dashboard/export.csv${qs({ days, region_id: regionId })}`, "grievances.csv")}>Export CSV</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      {global && data && (
        <div className="region-strip">
          <button className={`region-card${regionId === null ? " on" : ""}`} onClick={() => setRegionId(null)}>
            <strong>All regions</strong>
            <span className="muted small">{data.regions.reduce((n, r) => n + r.total, 0)} complaints</span>
          </button>
          {data.regions.map((r) => (
            <button key={r.region_id} className={`region-card${regionId === r.region_id ? " on" : ""}`}
              onClick={() => setRegionId(regionId === r.region_id ? null : r.region_id)}>
              <Ring value={r.resolution_rate} size={46} color={r.overdue ? "#f59e0b" : "#10b981"}>
                <small>{pct(r.resolution_rate)}</small>
              </Ring>
              <span className="region-meta">
                <strong>{r.region_name}</strong>
                <span className="muted small">{r.total} total · {r.open} open</span>
                {r.overdue > 0 && <span className="pill bad">{r.overdue} overdue</span>}
              </span>
            </button>
          ))}
        </div>
      )}

      {!s ? (
        <div className="kpis">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} h={96} />)}</div>
      ) : (
        <>
          <div className="kpis">
            <Kpi icon="📥" label="Received" value={s.total} onClick={() => go({})} />
            <Kpi icon="🛠️" label="Open" value={s.open} hint={`${s.pending_review} awaiting review`} onClick={() => go({ is_open: true })} />
            <Kpi icon="⏰" label="Overdue" value={s.overdue} tone={s.overdue ? "bad" : "good"} onClick={() => go({ overdue: true })} />
            <Kpi icon="🚨" label="Urgent open" value={s.urgent_open} tone={s.urgent_open ? "warn" : "good"} hint="High or Critical" onClick={() => go({ urgent: true })} />
            <Kpi icon="⬆️" label="Escalated" value={s.escalated} tone={s.escalated ? "warn" : "good"} onClick={() => go({ escalated: true })} />
            <Kpi icon="✅" label="Resolution rate" value={Math.round(s.resolution_rate * 100)} display={pct(s.resolution_rate)} tone="good" hint={`${s.resolved} resolved`} onClick={() => go({ status: "RESOLVED" })} />
            <Kpi icon="🎯" label="Within SLA" value={s.sla_compliance === null ? null : Math.round(s.sla_compliance * 100)} display={pct(s.sla_compliance)}
              tone={s.sla_compliance !== null && s.sla_compliance < 0.8 ? "warn" : "good"} hint="of resolved cases" />
            <Kpi icon="⏱️" label="Avg resolution" value={s.avg_resolution_hours} display={hours(s.avg_resolution_hours)} />
          </div>

          <Card title="AI insights" subtitle="Generated from the AI analysis of recent complaints">
            {data!.insights.length ? (
              <ul className="insights">{data!.insights.map((i) => <li key={i}>{i}</li>)}</ul>
            ) : <p className="muted">Nothing notable in this period.</p>}
          </Card>

          <div className="grid-main">
            <Card title="Complaints over time" subtitle="Click a legend item to show or hide it"
              actions={<span className="muted small">{s.recurring_issues} recurring issues</span>}>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={s.trend} margin={{ left: -18, right: 8, top: 8 }}>
                  <defs>
                    <linearGradient id="gc" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6366f1" stopOpacity={0.5} /><stop offset="100%" stopColor="#6366f1" stopOpacity={0} /></linearGradient>
                    <linearGradient id="gr" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#10b981" stopOpacity={0.45} /><stop offset="100%" stopColor="#10b981" stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend onClick={(e) => { const k = e.dataKey as "created" | "resolved"; setSeries((v) => ({ ...v, [k]: !v[k] })); }} />
                  {series.created && <Area type="monotone" dataKey="created" name="Received" stroke="#6366f1" strokeWidth={2} fill="url(#gc)" />}
                  {series.resolved && <Area type="monotone" dataKey="resolved" name="Resolved" stroke="#10b981" strokeWidth={2} fill="url(#gr)" />}
                </AreaChart>
              </ResponsiveContainer>
            </Card>

            <Card title="By category" subtitle="Click a slice to open the list">
              <div className="donut">
                <ResponsiveContainer width="100%" height={230}>
                  <PieChart>
                    <Pie data={s.by_category} dataKey="count" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}
                      shape={(p: any, i?: string | number) => <Sector {...p} outerRadius={p.outerRadius + (Number(i) === slice ? 6 : 0)} />}
                      onMouseEnter={(_, i) => setSlice(i)} onMouseLeave={() => setSlice(undefined)}
                      onClick={(d) => go({ category: (d as unknown as Count).name })}>
                      {s.by_category.map((c) => <Cell key={c.name} fill={categoryColor(c.name)} style={{ cursor: "pointer" }} />)}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-center"><strong>{slice !== undefined ? s.by_category[slice]?.count : catTotal}</strong>
                  <span className="muted small">{slice !== undefined ? s.by_category[slice]?.name : "complaints"}</span></div>
              </div>
              <ul className="legend">
                {s.by_category.map((c) => (
                  <li key={c.name}><button className="link" onClick={() => go({ category: c.name })}>
                    <i style={{ background: categoryColor(c.name) }} />{c.name}<b>{c.count}</b></button></li>
                ))}
              </ul>
            </Card>
          </div>

          <div className="grid-3">
            <Card title="Severity by region" subtitle="Click a bar to filter by severity">
              <ResponsiveContainer width="100%" height={Math.max(180, data!.heat.severity.length * 38 + 40)}>
                <BarChart data={data!.heat.severity} layout="vertical" margin={{ left: 30, right: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e5e7eb" />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="region" width={96} tick={{ fontSize: 11 }} />
                  <Tooltip cursor={{ fill: "rgba(99,102,241,.06)" }} />
                  {["Low", "Medium", "High", "Critical"].map((sev) => (
                    <Bar key={sev} dataKey={sev} stackId="a" fill={SEVERITY_COLORS[sev]} cursor="pointer"
                      onClick={(d) => go({ severity: sev, region_id: (d as unknown as { region_id: number }).region_id })} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
              <div className="chips">
                {Object.entries(SEVERITY_COLORS).map(([k, c]) => <span key={k} className="chip"><i style={{ background: c }} />{k}</span>)}
              </div>
            </Card>

            <Card title="Sentiment" subtitle="Tone of the complaints">
              <div className="sent">
                {s.by_sentiment.map((x) => (
                  <button key={x.name} className="sent-row" onClick={() => go({ sentiment: x.name })}>
                    <span>{x.name}</span>
                    <span className="bar-track"><span style={{ width: `${(100 * x.count) / Math.max(1, s.total)}%`, background: SENTIMENT_COLORS[x.name] }} /></span>
                    <b>{pct(x.count / Math.max(1, s.total))}</b>
                  </button>
                ))}
              </div>
              <div className="neg-ring">
                <Ring value={s.negative_share} size={96} color="#ef4444"><strong>{pct(s.negative_share)}</strong></Ring>
                <span className="muted small">negative</span>
              </div>
            </Card>

            <Card title="Needs attention" subtitle="Open High and Critical complaints" actions={<Link to="/staff/grievances?urgent=true">View all</Link>}>
              <ul className="feed">
                {data!.urgent.map((g) => (
                  <li key={g.id}>
                    <Link to={`/staff/grievances/${g.id}`}>
                      <span className="feed-top"><Badge kind="sev" value={g.severity} /><span className="muted small">{g.outlet_name ?? g.region_name}</span></span>
                      <span>{g.summary}</span>
                      <span className="muted small">{g.reference} · {fmt(g.created_at)}{g.overdue && <b className="overdue"> · overdue</b>}</span>
                    </Link>
                  </li>
                ))}
                {!data!.urgent.length && <p className="muted">No urgent open complaints.</p>}
              </ul>
            </Card>
          </div>

          <Card title="Region × category" subtitle="Where each kind of complaint concentrates. Click a cell to open it.">
            <Heatmap regions={data!.heat.regions} categories={data!.heat.categories} cells={data!.heat.cells}
              onCell={(rid, category) => go({ region_id: rid, category })} />
          </Card>

          <Card title="Outlet leaderboard" subtitle="Click a row to open its complaints"
            actions={
              <div className="row">
                <input className="mini" placeholder="Search outlet or area" value={search} onChange={(e) => setSearch(e.target.value)} />
                <select className="mini" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                  <option value="total">Sort: most complaints</option><option value="open">Sort: most open</option>
                  <option value="overdue">Sort: most overdue</option><option value="avg_severity">Sort: highest severity</option>
                  <option value="negative_share">Sort: most negative</option>
                </select>
              </div>
            }>
            <table className="lb">
              <thead><tr><th>Outlet</th><th>Region</th><th style={{ width: "26%" }}>Complaints</th><th>Open</th><th>Overdue</th><th>Avg severity</th><th>Top issue</th></tr></thead>
              <tbody>
                {outlets.slice(0, 15).map((o) => (
                  <tr key={o.outlet_id} className="click" onClick={() => go({ outlet_id: o.outlet_id, region_id: o.region_id })}>
                    <td><strong>{o.area || o.outlet_name}</strong><div className="muted small">{o.outlet_code}</div></td>
                    <td>{o.region_name}</td>
                    <td><span className="bar-track"><span style={{ width: `${(100 * o.total) / maxOutlet}%`, background: categoryColor(o.top_category ?? "") }} /></span><b className="bar-num">{o.total}</b></td>
                    <td>{o.open}</td>
                    <td>{o.overdue ? <span className="pill bad">{o.overdue}</span> : <span className="muted">0</span>}</td>
                    <td>{o.avg_severity ? <span className="sev-dot" style={{ background: o.avg_severity >= 3 ? SEVERITY_COLORS.High : o.avg_severity >= 2 ? SEVERITY_COLORS.Medium : SEVERITY_COLORS.Low }} /> : null}{o.avg_severity ?? "-"}</td>
                    <td>{o.top_category ?? "-"}</td>
                  </tr>
                ))}
                {!outlets.length && <tr><td colSpan={7} className="muted">No outlets match.</td></tr>}
              </tbody>
            </table>
          </Card>

          <Card title="Recurring issues" subtitle="The same problem reported by different people" actions={<Link to="/staff/recurring">See all</Link>}>
            <div className="cards">
              {data!.recurring.slice(0, 6).map((r) => (
                <button key={r.id} className="issue" onClick={() => nav(`/staff/grievances?recurring_issue_id=${r.id}`)}>
                  <i className="issue-bar" style={{ background: categoryColor(r.category) }} />
                  <strong>{r.title}</strong>
                  <span className="muted small">{r.region_name} · {r.outlet_count} outlets</span>
                  <span><span className="pill">{r.grievance_count} complaints</span> {r.open_count > 0 && <span className="pill bad">{r.open_count} open</span>}</span>
                </button>
              ))}
              {!data!.recurring.length && <p className="muted">No recurring issues detected.</p>}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
