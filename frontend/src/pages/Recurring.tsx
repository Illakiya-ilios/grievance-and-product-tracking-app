import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmt } from "../ui";

interface Issue {
  id: number;
  title: string;
  category: string;
  region_name: string;
  grievance_count: number;
  open_count: number;
  outlet_count: number;
  first_seen: string;
  last_seen: string;
}

export default function Recurring() {
  const [items, setItems] = useState<Issue[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<Issue[]>("/dashboard/recurring").then(setItems).catch((e) => setError(e.message)); }, []);
  return (
    <div>
      <h2>Recurring issues</h2>
      <p className="muted">Groups of related complaints from different people in the same region.</p>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>Issue</th><th>Category</th><th>Region</th><th>Complaints</th><th>Open</th><th>Outlets</th><th>First seen</th><th>Last seen</th></tr></thead>
        <tbody>
          {items?.map((i) => (
            <tr key={i.id}>
              <td><Link to={`/staff/grievances?recurring_issue_id=${i.id}`}>{i.title}</Link></td>
              <td>{i.category}</td><td>{i.region_name}</td><td>{i.grievance_count}</td><td>{i.open_count}</td>
              <td>{i.outlet_count}</td><td>{fmt(i.first_seen)}</td><td>{fmt(i.last_seen)}</td>
            </tr>
          ))}
          {items && !items.length && <tr><td colSpan={8} className="muted">No recurring issues detected yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
