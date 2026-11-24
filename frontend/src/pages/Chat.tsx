import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";

interface Msg { role: "bot" | "user"; text: string }
interface Turn {
  session_id: string;
  stage: string;
  replies: string[];
  quick_replies: string[];
  submitted: { reference: string; tracking_token: string } | null;
}
interface Transcript { session_id: string; stage: string; messages: Msg[] }

const KEY = "tasmac_chat_session";
const CONFIRM_BUTTONS = ["Yes, submit", "No, change something"];

export default function Chat() {
  const [sid, setSid] = useState<string | null>(sessionStorage.getItem(KEY));
  const [messages, setMessages] = useState<Msg[]>([]);
  const [quick, setQuick] = useState<string[]>([]);
  const [submitted, setSubmitted] = useState<Turn["submitted"]>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const begin = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const t = await api<Turn>("/public/chat", "POST", {});
      sessionStorage.setItem(KEY, t.session_id);
      setSid(t.session_id);
      setMessages(t.replies.map((text) => ({ role: "bot", text })));
      setQuick(t.quick_replies);
      setSubmitted(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!sid) {
      void begin();
      return;
    }
    api<Transcript>(`/public/chat/${sid}`)
      .then((t) => {
        setMessages(t.messages);
        setQuick(t.stage === "confirming" ? CONFIRM_BUTTONS : []);
        setBusy(false);
      })
      .catch(() => {
        sessionStorage.removeItem(KEY);
        setSid(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  const send = async (text: string) => {
    const t = text.trim();
    if (!t || busy || !sid) return;
    setMessages((m) => [...m, { role: "user", text: t }]);
    setInput("");
    setQuick([]);
    setBusy(true);
    setError("");
    try {
      const r = await api<Turn>("/public/chat", "POST", { session_id: sid, message: t });
      setMessages((m) => [...m, ...r.replies.map((text) => ({ role: "bot" as const, text }))]);
      setQuick(r.quick_replies);
      if (r.submitted) setSubmitted(r.submitted);
      else if (r.stage !== "submitted") setSubmitted(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        sessionStorage.removeItem(KEY);
        setSid(null);
        setMessages([]);
        void begin();
        setError("That chat expired, so I started a new one.");
      } else {
        setError((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  return (
    <div className="chat card narrow">
      <div className="row between">
        <h2>TASMAC grievance assistant</h2>
        <button className="link" disabled={busy} onClick={() => void send("start over")}>Start over</button>
      </div>
      <div className="chat-log" aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.text}</div>
        ))}
        {busy && <div className="bubble bot muted">Typing…</div>}
        <div ref={endRef} />
      </div>
      {submitted && (
        <p className="notice">
          Saved as <code>{submitted.reference}</code>.{" "}
          <Link to={`/track?ref=${submitted.reference}&token=${submitted.tracking_token}`}>Track this grievance</Link>
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {quick.length > 0 && (
        <div className="quick">
          {quick.map((q) => <button key={q} className="secondary" disabled={busy} onClick={() => void send(q)}>{q}</button>)}
        </div>
      )}
      <form className="chat-input" onSubmit={onSubmit}>
        <input value={input} onChange={(e) => setInput(e.target.value)} maxLength={1000} placeholder="Type your message…" disabled={busy || !sid} autoFocus />
        <button disabled={busy || !input.trim()}>Send</button>
      </form>
    </div>
  );
}
