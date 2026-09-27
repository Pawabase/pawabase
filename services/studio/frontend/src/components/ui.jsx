import { createContext, useContext, useEffect, useState } from "react";

export function Button({ variant, size, className = "", ...props }) {
  return <button type="button" className={`btn ${variant || ""} ${size || ""} ${className}`} {...props} />;
}

export function Card({ title, actions, children, flush, className = "" }) {
  return (
    <section className={`card ${flush ? "flush" : ""} ${className}`}>
      {(title || actions) && (
        <div className={flush ? "card-head" : "spread"} style={flush ? undefined : { marginBottom: 12 }}>
          <h2>{title}</h2>
          <div className="row">{actions}</div>
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, description, actions }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="row">{actions}</div>
    </div>
  );
}

export function Badge({ tone, children }) {
  return <span className={`badge ${tone || ""}`}>{children}</span>;
}

const STATUS_TONES = {
  succeeded: "green", completed: "green", delivered: "green", ok: "green", sent: "green", active: "blue",
  running: "blue", queued: "blue", retrying: "yellow", pending: "yellow", suppressed: "yellow",
  failed: "red", error: "red", dead: "red", revoked: "red",
};

export function Status({ value }) {
  return <Badge tone={STATUS_TONES[value] || ""}>{value ?? "—"}</Badge>;
}

export function Spinner() {
  return <div className="spinner" />;
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return <div className="alert error">{error.message || String(error)}</div>;
}

export function Loading({ state, children, empty }) {
  if (state.error) return <div style={{ padding: 16 }}><ErrorNote error={state.error} /></div>;
  if (state.loading && !state.data) return <div className="empty"><div className="row" style={{ justifyContent: "center" }}><Spinner /> Loading…</div></div>;
  const rows = state.data?.data ?? state.data;
  if (empty && Array.isArray(rows) && rows.length === 0) return <Empty>{empty}</Empty>;
  return children(state.data);
}

export function Table({ columns, rows, onRowClick, empty = "Nothing here yet." }) {
  if (!rows || rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key || c.label}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.id ?? row.name ?? i} className={onRowClick ? "clickable" : ""} onClick={onRowClick ? () => onRowClick(row) : undefined}>
              {columns.map((c) => (
                <td key={c.key || c.label}>{c.render ? c.render(row) : formatCell(row[c.key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function formatCell(value) {
  if (value === null || value === undefined || value === "") return <span className="faint">—</span>;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return <code>{truncate(JSON.stringify(value), 80)}</code>;
  return String(value);
}

export function truncate(text, n) {
  return text.length > n ? text.slice(0, n - 1) + "…" : text;
}

export function when(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (Math.abs(seconds) < 60) return "just now";
  if (Math.abs(seconds) < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (Math.abs(seconds) < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return date.toLocaleString();
}

export function Modal({ title, onClose, children, footer, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`}>
        <div className="modal-head">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>✕</Button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, error, children }) {
  return (
    <label className="field">
      {label}
      {children}
      {hint && <span className="hint">{hint}</span>}
      {error && <span className="error-text">{error}</span>}
    </label>
  );
}

/** A JSON text area that reports whether its content parses. */
export function JsonInput({ value, onChange, rows = 10, placeholder }) {
  const [text, setText] = useState(() => (value === undefined ? "" : JSON.stringify(value, null, 2)));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    try {
      if (JSON.stringify(JSON.parse(text || "null")) !== JSON.stringify(value ?? null)) {
        setText(value === undefined ? "" : JSON.stringify(value, null, 2));
      }
    } catch {
      /* keep the user's half-typed text */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value)]);
  return (
    <>
      <textarea
        rows={rows}
        className={invalid ? "invalid" : ""}
        value={text}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed = e.target.value.trim() ? JSON.parse(e.target.value) : null;
            setInvalid(false);
            onChange(parsed);
          } catch {
            setInvalid(true);
          }
        }}
      />
      {invalid && <span className="error-text">Not valid JSON yet.</span>}
    </>
  );
}

export function Json({ value }) {
  return <pre className="code-block">{JSON.stringify(value, null, 2)}</pre>;
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.value} className={value === t.value ? "active" : ""} onClick={() => onChange(t.value)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function CopyText({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="row" style={{ gap: 6 }}>
      <code style={{ overflowWrap: "anywhere" }}>{text}</code>
      <Button size="sm" variant="ghost" onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
        {copied ? "copied" : "copy"}
      </Button>
    </span>
  );
}

const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = (message, tone = "ok") => {
    const id = Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  };
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.message}</div>)}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/** Run an async action, toast its outcome, and report whether it succeeded. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async (fn, success) => {
    setBusy(true);
    try {
      const result = await fn();
      if (success) toast(success);
      return result ?? true;
    } catch (error) {
      toast(error.message || "Something went wrong", "error");
      return false;
    } finally {
      setBusy(false);
    }
  };
  return [run, busy];
}
