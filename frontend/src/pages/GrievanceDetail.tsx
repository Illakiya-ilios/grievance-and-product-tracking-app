import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { GrievanceDetail } from "../types";
import { Badge, fmt } from "../ui";

interface Assignable { id: number; name: string; role: string }
interface Meta { categories: string[]; severities: string[]; sentiments: string[] }

export default function GrievanceDetailPage() {
  const { id } = useParams();
  const [g, setG] = useState<GrievanceDetail | null>(null);
  const [error, setError] = useState("");
  const [remarks, setRemarks] = useState("");
  const [assignee, setAssignee] = useState("");
  const [people, setPeople] = useState<Assignable[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [override, setOverride] = useState({ category: "", severity: "", sentiment: "" });
  const [dupOf, setDupOf] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api<GrievanceDetail>(`/grievances/${id}`);
      setG(d);
      setError("");
      if (d.permissions.assign) api<Assignable[]>(`/users/assignable?region_id=${d.region_id}`).then(setPeople).catch(() => undefined);
      if (d.permissions.review) api<Meta>("/grievances/meta/filters").then(setMeta).catch(() => undefined);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const act = async (path: string, body: unknown) => {
    try {
      await api(`/grievances/${id}/${path}`, "POST", body);
      setRemarks("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error && !g) return <p className="error">{error}</p>;
  if (!g) return <p className="muted">Loading…</p>;
  const a = g.analysis;
  const p = g.permissions;

  return (
    <div className="detail">
      <p><Link to="/staff/grievances">← All grievances</Link></p>
      <h2>{g.reference} <Badge kind="st" value={g.status} /> {g.escalated && <span className="tag warn">escalated</span>}</h2>
      <p className="muted">{g.region_name} · {g.outlet_name ?? "General regional issue"} · submitted {fmt(g.created_at)} · due <span className={g.overdue ? "overdue" : ""}>{fmt(g.due_at)}</span> · assigned to {g.assigned_user_name ?? "nobody"}</p>
      {error && <p className="error">{error}</p>}
      {g.review_reason && g.status === "PENDING_REVIEW" && <p className="notice">Needs review: {g.review_reason}</p>}

      <div className="grid2">
        <section className="card">
          <h3>Original complaint</h3>
          <p className="quote">{g.description}</p>
          {g.contact_email && <p className="muted small">Contact: {g.contact_email}</p>}
        </section>

        <section className="card">
          <h3>AI analysis {a && <span className="muted small">({a.source === "human" ? "human reviewed" : a.model} · prompt {a.prompt_version} · confidence {Math.round(a.confidence * 100)}%)</span>}</h3>
          {a ? (
            <>
              <p><strong>{a.summary}</strong></p>
              <dl className="kv">
                <dt>Category</dt><dd>{a.category}{a.related_categories.map((c) => <span className="tag" key={c}>{c}</span>)}</dd>
                <dt>Severity</dt><dd><Badge kind="sev" value={a.severity} /></dd>
                <dt>Sentiment</dt><dd><Badge kind="sent" value={a.sentiment} /></dd>
                <dt>Investigation</dt><dd>{a.requires_investigation ? "Recommended" : "Not required"}</dd>
                <dt>Entities</dt><dd>{[a.entities.outlet_id && `Outlet ${a.entities.outlet_id}`, a.entities.location].filter(Boolean).join(" · ") || "-"}</dd>
              </dl>
              <p className="small muted">Alleged issues (reported by the complainant, not verified):</p>
              <ul>{a.issues.map((i) => <li key={i}>{i}</li>)}</ul>
              <p><strong>Suggested action:</strong> {a.recommended_action}</p>
              {a.error && <p className="error small">{a.error}</p>}
            </>
          ) : <p className="muted">Analysis pending…</p>}
        </section>
      </div>

      {g.recurring_issue && (
        <section className="card">
          <h3>Recurring issue</h3>
          <p>{g.recurring_issue.title} · {g.recurring_issue.grievance_count} complaints · <Link to={`/staff/grievances?recurring_issue_id=${g.recurring_issue.id}`}>View all</Link></p>
        </section>
      )}

      {g.related.length > 0 && (
        <section className="card">
          <h3>Duplicate and related complaints</h3>
          <table>
            <thead><tr><th>Reference</th><th>Type</th><th>Similarity</th><th>Summary</th><th>Outlet</th><th>Status</th></tr></thead>
            <tbody>{g.related.map((r) => (
              <tr key={r.grievance_id}>
                <td><Link to={`/staff/grievances/${r.grievance_id}`}>{r.reference}</Link></td>
                <td><span className={`tag ${r.relationship_type === "duplicate" ? "warn" : ""}`}>{r.relationship_type}</span></td>
                <td>{r.score.toFixed(2)}{r.rerank_score !== null && ` / rerank ${r.rerank_score.toFixed(2)}`}</td>
                <td>{r.summary}</td><td>{r.outlet_name ?? "General"}</td><td>{r.status}</td>
              </tr>))}
            </tbody>
          </table>
        </section>
      )}

      {p.review && meta && (
        <section className="card">
          <h3>Review</h3>
          <div className="row">
            <select value={override.category} onChange={(e) => setOverride({ ...override, category: e.target.value })}>
              <option value="">Keep category</option>{meta.categories.map((c) => <option key={c}>{c}</option>)}
            </select>
            <select value={override.severity} onChange={(e) => setOverride({ ...override, severity: e.target.value })}>
              <option value="">Keep severity</option>{meta.severities.map((c) => <option key={c}>{c}</option>)}
            </select>
            <select value={override.sentiment} onChange={(e) => setOverride({ ...override, sentiment: e.target.value })}>
              <option value="">Keep sentiment</option>{meta.sentiments.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <textarea rows={2} placeholder="Remarks" value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          <div className="row">
            <button onClick={() => act("review", { decision: "approve", ...Object.fromEntries(Object.entries(override).filter(([, v]) => v)), remarks })}>Approve and route</button>
            <select value={dupOf} onChange={(e) => setDupOf(e.target.value)}>
              <option value="">Duplicate of…</option>
              {g.related.map((r) => <option key={r.grievance_id} value={r.grievance_id}>{r.reference}</option>)}
            </select>
            <button className="secondary" disabled={!dupOf} onClick={() => act("review", { decision: "duplicate", duplicate_of_id: Number(dupOf), remarks })}>Mark as duplicate</button>
          </div>
        </section>
      )}

      {(p.assign || p.update_status || p.reopen || p.add_note || p.reanalyze) && (
        <section className="card">
          <h3>Actions</h3>
          {p.assign && (
            <div className="row">
              <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="">Assign to…</option>{people.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
              <button disabled={!assignee} onClick={() => act("assign", { user_id: Number(assignee) })}>Assign</button>
            </div>
          )}
          <textarea rows={2} placeholder="Remarks (required to resolve)" value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          <div className="row">
            {p.update_status && g.status !== "IN_PROGRESS" && <button onClick={() => act("status", { status: "IN_PROGRESS", remarks })}>Start work</button>}
            {p.update_status && <button onClick={() => act("status", { status: "RESOLVED", remarks })}>Resolve</button>}
            {p.reopen && <button className="secondary" disabled={remarks.trim().length < 3} onClick={() => act("reopen", { remarks })}>Reopen</button>}
            {p.add_note && <button className="secondary" disabled={!remarks.trim()} onClick={() => act("notes", { remarks })}>Add note</button>}
            {p.reanalyze && <button className="secondary" onClick={async () => { await act("reanalyze", {}); setTimeout(load, 1500); }}>Re-run AI analysis</button>}
          </div>
        </section>
      )}

      <section className="card">
        <h3>Action history</h3>
        <ul className="timeline">
          {g.actions.map((x, i) => (
            <li key={i}><strong>{x.action.replace(/_/g, " ")}</strong> by {x.performed_by} <span className="muted">{fmt(x.created_at)}</span>{x.remarks && <div>{x.remarks}</div>}</li>
          ))}
        </ul>
        {g.analysis_history.length > 0 && <p className="muted small">{g.analysis_history.length} earlier analysis version(s) retained for audit.</p>}
      </section>
    </div>
  );
}
