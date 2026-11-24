import "leaflet/dist/leaflet.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";
import { api } from "../api";
import { fmt } from "../ui";

interface Product { id: number; name: string; category: string; size_ml: number; price: number; status: "in_stock" | "low_stock" }
interface Store {
  id: number; code: string; name: string; address: string; area: string; district: string; lat: number; lng: number;
  distance_km: number | null; directions_url: string | null; stock_updated_at: string | null; stock_is_stale: boolean; products: Product[];
}
interface Result {
  located: boolean; radius_km: number; stores: Store[]; total_in_radius: number;
  nearest_outside_radius: { name: string; area: string; district: string; distance_km: number } | null;
  distance_note: string | null; notice: string | null; attribution: string | null;
}
interface Origin { lat: number; lng: number }
interface District { name: string; shops: number }

const RADII = [5, 10, 25, 50];

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 14);
    else if (points.length > 1) map.fitBounds(points, { padding: [40, 40], maxZoom: 15 });
  }, [map, points]);
  return null;
}

function geoMessage(code: number): string {
  if (code === 1) return "Location permission was denied. You can allow it in your browser settings, or pick your district below.";
  if (code === 3) return "Finding your location took too long. Try again, or pick your district below.";
  return "Your location is not available right now. Pick your district below instead.";
}

export default function Stores() {
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [districts, setDistricts] = useState<District[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [district, setDistrict] = useState("");
  const [q, setQ] = useState("");
  const [radius, setRadius] = useState(10);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState("");
  const [showFallback, setShowFallback] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    api<District[]>("/public/stores/districts").then(setDistricts).catch(() => undefined);
    api<{ categories: string[] }>("/public/stores/products").then((d) => setCategories(d.categories)).catch(() => undefined);
  }, []);

  const search = useCallback(async (where: Origin | { district: string }, query: string, radiusKm: number) => {
    setBusy(true);
    setError("");
    try {
      setResult(await api<Result>("/public/stores/nearby", "POST", { ...where, q: query.trim() || null, radius_km: radiusKm, limit: 5 }));
      setSelected(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const locate = () => {
    if (!("geolocation" in navigator)) {
      setError("This browser cannot share a location. Pick your district below instead.");
      setShowFallback(true);
      return;
    }
    setLocating(true);
    setError("");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        setOrigin(here);
        setLocating(false);
        void search(here, q, radius);
      },
      (err) => {
        setLocating(false);
        setError(geoMessage(err.code));
        setShowFallback(true);
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 60000 },
    );
  };

  const rerun = (nextQ: string, nextRadius: number) => {
    if (origin) void search(origin, nextQ, nextRadius);
    else if (district) void search({ district }, nextQ, nextRadius);
  };

  const points = useMemo<[number, number][]>(() => {
    const p: [number, number][] = [];
    if (origin) p.push([origin.lat, origin.lng]);
    result?.stores.forEach((s) => p.push([s.lat, s.lng]));
    return p;
  }, [origin, result]);

  const hasPlace = !!origin || !!district;

  return (
    <div className="stores">
      <div className="hero small-hero">
        <div>
          <h2>Find stock near you</h2>
          <p>See nearby shops and what they have in stock, then go and pick it up.</p>
        </div>
      </div>

      <section className="panel">
        <div className="find-row">
          <button onClick={locate} disabled={locating || busy}>{locating ? "Finding you…" : origin ? "Update my location" : "📍 Use my location"}</button>
          <input className="grow" placeholder="Search a product, for example beer, whisky or brandy" value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") rerun(q, radius); }} />
          <select value={radius} onChange={(e) => { const r = Number(e.target.value); setRadius(r); rerun(q, r); }} aria-label="Search radius">
            {RADII.map((r) => <option key={r} value={r}>within {r} km</option>)}
          </select>
          <button className="secondary" onClick={() => rerun(q, radius)} disabled={!hasPlace || busy}>Search</button>
        </div>
        {categories.length > 0 && (
          <div className="chips">
            {categories.map((c) => (
              <button key={c} className={`chip pick${q.toLowerCase() === c.toLowerCase() ? " on" : ""}`}
                onClick={() => { const next = q.toLowerCase() === c.toLowerCase() ? "" : c; setQ(next); rerun(next, radius); }}>{c}</button>
            ))}
          </div>
        )}
        <p className="muted small">Your location is used only for this search. It is not saved or shared.</p>
        {error && <p className="error">{error}</p>}
        {(showFallback || !origin) && (
          <div className="fallback">
            <span className="muted small">No location? Choose your district:</span>
            <select value={district} onChange={(e) => { setDistrict(e.target.value); setOrigin(null); if (e.target.value) void search({ district: e.target.value }, q, radius); }}>
              <option value="">Select district…</option>
              {districts.map((d) => <option key={d.name} value={d.name}>{d.name} ({d.shops})</option>)}
            </select>
          </div>
        )}
      </section>

      {result?.notice && <p className="notice">{result.notice}</p>}
      {result?.attribution && <p className="muted small">{result.attribution}</p>}

      {busy && !result && <p className="muted">Searching…</p>}
      {result && (
        <div className="stores-grid">
          <div className="store-list">
            <p className="muted small">
              {result.located
                ? `${result.stores.length} of ${result.total_in_radius} shops within ${result.radius_km} km, nearest first. ${result.distance_note ?? ""}`
                : `${result.stores.length} of ${result.total_in_radius} shops in ${district}${q ? ` that stock "${q}"` : ""}.`}
            </p>
            {result.stores.map((s, i) => (
              <article key={s.id} className={`store-card${selected === s.id ? " on" : ""}`} onClick={() => setSelected(s.id)}>
                <header>
                  <span className="rank">{i + 1}</span>
                  <div>
                    <h3>{s.name}</h3>
                    <p className="muted small">{s.address || [s.area, s.district].filter(Boolean).join(", ")}{!s.code.startsWith("osm-") && ` · Shop ${s.code}`}</p>
                  </div>
                  {s.distance_km !== null && <span className="dist">{s.distance_km} km</span>}
                </header>
                {s.products.length ? (
                  <ul className="prods">
                    {s.products.map((p) => (
                      <li key={p.id} className={p.status}>
                        <span>{p.name} <span className="muted">{p.size_ml} ml</span></span>
                        <span>₹{p.price % 1 ? p.price.toFixed(2) : p.price}{p.status === "low_stock" && <b className="pill warn-pill">few left</b>}</span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted small">Nothing in stock listed.</p>}
                <footer>
                  <span className={`muted small${s.stock_is_stale ? " stale" : ""}`}>
                    {s.stock_updated_at ? `Stock updated ${fmt(s.stock_updated_at)}${s.stock_is_stale ? " (may be out of date)" : ""}` : "Stock not reported"}
                  </span>
                  {s.directions_url && <a className="btn-link" href={s.directions_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>Directions</a>}
                </footer>
              </article>
            ))}
            {!result.stores.length && (
              <div className="card">
                <p>{q ? `No shops within ${result.radius_km} km list "${q}" in stock.` : `No shops within ${result.radius_km} km of you.`}</p>
                {result.nearest_outside_radius && (
                  <p className="muted">The nearest listed shop is {result.nearest_outside_radius.name} ({result.nearest_outside_radius.area}),
                    {" "}{result.nearest_outside_radius.distance_km} km away. Try a larger radius.</p>
                )}
              </div>
            )}
          </div>

          <div className="map-wrap">
            <MapContainer center={[11.1271, 78.6569]} zoom={7} scrollWheelZoom className="map">
              <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
              <FitBounds points={points} />
              {origin && (
                <CircleMarker center={[origin.lat, origin.lng]} radius={9} pathOptions={{ color: "#fff", weight: 3, fillColor: "#2563eb", fillOpacity: 1 }}>
                  <Popup>You are here</Popup>
                </CircleMarker>
              )}
              {result.stores.map((s, i) => s.lat !== null && s.lng !== null && (
                <CircleMarker key={s.id} center={[s.lat, s.lng]} radius={selected === s.id ? 14 : 11}
                  pathOptions={{ color: "#fff", weight: 2, fillColor: selected === s.id ? "#7c3aed" : "#ef4444", fillOpacity: 0.95 }}
                  eventHandlers={{ click: () => setSelected(s.id) }}>
                  <Popup><strong>{i + 1}. {s.name}</strong><br />{s.products.length} products in stock{s.distance_km !== null && <><br />{s.distance_km} km away</>}</Popup>
                </CircleMarker>
              ))}
            </MapContainer>
          </div>
        </div>
      )}
    </div>
  );
}
