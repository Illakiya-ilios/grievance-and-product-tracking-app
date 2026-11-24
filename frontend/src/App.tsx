import { lazy, Suspense, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import GrievanceDetailPage from "./pages/GrievanceDetail";
import GrievanceList from "./pages/GrievanceList";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import Recurring from "./pages/Recurring";

import Track from "./pages/Track";

const Stores = lazy(() => import("./pages/Stores")); // Leaflet only loads on this page
const Dashboard = lazy(() => import("./pages/Dashboard")); // keeps the chart library out of the main bundle

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="muted">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">TASMAC Grievances</Link>
        <nav>
          <NavLink to="/" end>File a complaint</NavLink>
          <NavLink to="/stores">Find stores</NavLink>
          <NavLink to="/track">Track</NavLink>
          {user && (
            <>
              <NavLink to="/staff/dashboard">Dashboard</NavLink>
              <NavLink to="/staff/grievances">Grievances</NavLink>
              <NavLink to="/staff/recurring">Recurring issues</NavLink>
            </>
          )}
        </nav>
        <div className="spacer" />
        {user ? (
          <span>
            {user.name} <span className="muted">({user.role.replace("_", " ")})</span>{" "}
            <button className="link" onClick={() => { logout(); nav("/"); }}>Sign out</button>
          </span>
        ) : (
          <NavLink to="/login">Staff login</NavLink>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/stores" element={<Suspense fallback={<p className="muted">Loading…</p>}><Stores /></Suspense>} />
          <Route path="/track" element={<Track />} />
          <Route path="/login" element={<Login />} />
          <Route path="/staff/dashboard" element={<Protected><Suspense fallback={<p className="muted">Loading dashboard…</p>}><Dashboard /></Suspense></Protected>} />
          <Route path="/staff/grievances" element={<Protected><GrievanceList /></Protected>} />
          <Route path="/staff/grievances/:id" element={<Protected><GrievanceDetailPage /></Protected>} />
          <Route path="/staff/recurring" element={<Protected><Recurring /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
