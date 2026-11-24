import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, qs } from "../api";
import { fmt } from "../ui";

interface Tracking {
  reference: string;
  status: string;
  region: string;
  outlet: string | null;
  submitted_at: string;
  can_reopen: boolean;
  timeline: { action: string; remarks: string; at: string }[];
}

export default function Track() {
  const [params] = useSearchParams();
  const [ref, setRef] = useState(params.get("ref") ?? "");
  const [token, setToken] = useState(params.get("token") ?? "");
  const [data, setData] = useState<Tracking | null>(null);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");

  const load = async () => {
    setError("");
    try {
      setData(await api<Tracking>(`/public/grievances/${encodeURIComponent(ref.trim())}${qs({ token: token.trim() })}`));
    } catch (err) {
      setData(null);
      setError((err as Error).message === "Grievance not found" ? "No grievance matches that reference and tracking code." : (err as Error).message);
    }
  };

  useEffect(() => {
    if (params.get("ref") && params.get("token")) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void load();
  };

  const reopen = async () => {
    try {
      await api(`/public/grievances/${encodeURIComponent(ref.trim())}/reopen${qs({ token: token.trim() })}`, "POST", { reason });
      setReason("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="card narrow">
      <h2>Track a grievance</h2>
      <form onSubmit={onSubmit}>
        <label>Reference<input value={ref} onChange={(e) => setRef(e.target.value)} required placeholder="TSM-2026-000001" /></label>
        <label>Tracking code<input value={token} onChange={(e) => setToken(e.target.value)} required minLength={8} /></label>
        <button>Check status</button>
      </form>
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <hr />
          <p><span className={`badge st-${data.status}`}>{data.status.replace("_", " ")}</span> {data.region}{data.outlet ? ` · ${data.outlet}` : ""}</p>
          <ul className="timeline">
            {data.timeline.map((t, i) => (
              <li key={i}><strong>{t.action.replace("_", " ")}</strong> <span className="muted">{fmt(t.at)}</span>
                {t.remarks && <div>{t.remarks}</div>}</li>
            ))}
          </ul>
          {data.can_reopen && (
            <div>
              <h3>Problem not solved?</h3>
              <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Tell us what is still wrong" />
              <button disabled={reason.trim().length < 5} onClick={reopen}>Reopen grievance</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
