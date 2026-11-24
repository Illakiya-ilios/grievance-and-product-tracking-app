import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs } from "../api";
import { useAuth } from "../auth";
import type { GrievanceItem } from "../types";
import { Badge, fmt } from "../ui";

interface Meta { categories: string[]; severities: string[]; statuses: string[] }
interface Page { total: number; page: number; page_size: number; items: GrievanceItem[] }

export default function GrievanceList() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [data, setData] = useState<Page | null>(null);
  const [error, setError] = useState("");

  const filters = {
    status: params.get("status") ?? "",
    category: params.get("category") ?? "",
    severity: params.get("severity") ?? "",
    q: params.get("q") ?? "",
    assigned_to_me: params.get("assigned_to_me") === "true",
    overdue: params.get("overdue") === "true",
    escalated: params.get("escalated") === "true",
    is_open: params.get("is_open") === "true",
    urgent: params.get("urgent") === "true",
    sentiment: params.get("sentiment") ?? "",
    outlet_id: params.get("outlet_id") ?? "",
    region_id: params.get("region_id") ?? "",
    days: params.get("days") ?? "",
    recurring_issue_id: params.get("recurring_issue_id") ?? "",
    page: Number(params.get("page") ?? 1),
  };

  useEffect(() => { api<Meta>("/grievances/meta/filters").then(setMeta).catch(() => undefined); }, []);
  useEffect(() => {
    api<Page>(`/grievances${qs(filters)}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const set = (key: string, value: string | boolean) => {
    const next = new URLSearchParams(params);
    if (value === "" || value === false) next.delete(key); else next.set(key, String(value));
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const global = user?.role === "admin" || user?.role === "management";

  return (
    <div>
      <h2>Grievances {!global && <span className="muted small">your region only</span>}</h2>
      <div className="filters">
        <input placeholder="Search text or reference" defaultValue={filters.q} onKeyDown={(e) => { if (e.key === "Enter") set("q", e.currentTarget.value); }} />
        <select value={filters.status} onChange={(e) => set("status", e.target.value)}>
          <option value="">All statuses</option>{meta?.statuses.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={filters.category} onChange={(e) => set("category", e.target.value)}>
          <option value="">All categories</option>{meta?.categories.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={filters.severity} onChange={(e) => set("severity", e.target.value)}>
          <option value="">All severities</option>{meta?.severities.map((s) => <option key={s}>{s}</option>)}
        </select>
        <label className="inline"><input type="checkbox" checked={filters.assigned_to_me} onChange={(e) => set("assigned_to_me", e.target.checked)} /> Mine</label>
        <label className="inline"><input type="checkbox" checked={filters.overdue} onChange={(e) => set("overdue", e.target.checked)} /> Overdue</label>
        <label className="inline"><input type="checkbox" checked={filters.escalated} onChange={(e) => set("escalated", e.target.checked)} /> Escalated</label>
      </div>
      {(filters.region_id || filters.outlet_id || filters.sentiment || filters.days || filters.is_open || filters.urgent || filters.recurring_issue_id) && (
        <div className="chips active-filters">
          {([["region_id", "Region"], ["outlet_id", "Outlet"], ["sentiment", "Sentiment"], ["days", "Last days"], ["is_open", "Open only"], ["urgent", "Urgent only"], ["recurring_issue_id", "Recurring issue"]] as const)
            .filter(([k]) => params.get(k)).map(([k, label]) => (
              <button key={k} className="chip removable" onClick={() => set(k, "")}>{label}{["is_open", "urgent"].includes(k) ? "" : `: ${params.get(k)}`} ?</button>
            ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>Reference</th><th>Summary</th><th>Region / outlet</th><th>Category</th><th>Severity</th><th>Status</th><th>Assigned</th><th>Due</th></tr></thead>
        <tbody>
          {data?.items.map((g) => (
            <tr key={g.id}>
              <td><Link to={`/staff/grievances/${g.id}`}>{g.reference}</Link></td>
              <td>{g.summary ?? <span className="muted">Analysing…</span>}{g.recurring_issue_id && <span className="tag">recurring</span>}</td>
              <td>{g.region_name}<div className="muted small">{g.outlet_name ?? "General"}</div></td>
              <td>{g.category ?? "-"}</td>
              <td><Badge kind="sev" value={g.severity} /></td>
              <td><Badge kind="st" value={g.status} />{g.escalated && <span className="tag warn">escalated</span>}</td>
              <td>{g.assigned_user_name ?? "-"}</td>
              <td className={g.overdue ? "overdue" : ""}>{fmt(g.due_at)}</td>
            </tr>
          ))}
          {data && !data.items.length && <tr><td colSpan={8} className="muted">No grievances match.</td></tr>}
        </tbody>
      </table>
      <div className="pager">
        <button disabled={filters.page <= 1} onClick={() => set("page", String(filters.page - 1))}>Previous</button>
        <span>Page {filters.page} of {pages} · {data?.total ?? 0} total</span>
        <button disabled={filters.page >= pages} onClick={() => set("page", String(filters.page + 1))}>Next</button>
      </div>
    </div>
  );
}
