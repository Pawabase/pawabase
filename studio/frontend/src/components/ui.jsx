import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Icon } from "./icons";

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

// Every dialog in Studio is the same right-docked side sheet — Modal is kept
// as a thin alias over Sheet (rather than rewritten at each call site) so the
// whole app stays uniform from one definition. `wide` is accepted for
// backwards compatibility with existing call sites but no longer does
// anything: Sheet has one generous width for everyone.
export function Modal({ title, onClose, children, footer }) {
  return (
    <Sheet title={title} onClose={onClose} footer={footer}>
      {children}
    </Sheet>
  );
}

export function Field({ label, hint, error, optional, children, className = "" }) {
  return (
    <label className={`field ${className}`}>
      {label && <span className="label-row"><span>{label}</span>{optional && <span className="optional">Optional</span>}</span>}
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

/** Copy text to the clipboard. The async API needs a secure context and a focused page, and
 *  rejects otherwise; fall back to a hidden textarea so a copy button never silently does nothing. */
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the legacy path */ }
  const holder = document.activeElement?.closest?.("[role=dialog], dialog") || document.body;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
  holder.appendChild(area);
  const previous = document.activeElement;
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  holder.removeChild(area);
  previous?.focus?.();
  return ok;
}

export function CopyText({ text, label }) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();
  const copy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } else {
      toast("Could not copy. Select the text and copy it manually.", "error");
    }
  };
  return (
    <span className="row" style={{ gap: 6 }}>
      <code style={{ overflowWrap: "anywhere" }}>{text}</code>
      <Button size="sm" variant="ghost" onClick={copy} aria-label={label ? `Copy ${label}` : "Copy"} title={copied ? "Copied" : "Copy"}>
        <Icon name={copied ? "check" : "copy"} />
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
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}><span className="toast-dot"><Icon name={t.tone === "error" ? "x" : "check"} /></span>{t.message}</div>)}
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

/** A right-hand panel for creating and editing things: a header, optional
 *  tabs, a scrolling body of form sections, and a sticky footer. */
export function Sheet({ title, subtitle, icon, tone = "lavender", tabs, tab, onTab, onClose, footer, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="sheet-overlay" onMouseDown={onClose} />
      <div className="sheet" role="dialog" aria-label={typeof title === "string" ? title : undefined}>
        <div className="sheet-head">
          {icon && <span className={`tile-icon pastel ${tone}`}><Icon name={icon} size={20} /></span>}
          <div className="grow">
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {tabs && <div className="sheet-tabs"><Tabs tabs={tabs} value={tab} onChange={onTab} /></div>}
        <div className="sheet-body">{children}</div>
        {footer && <div className="sheet-foot">{footer}</div>}
      </div>
    </>
  );
}

export function Section({ title, description, actions, children }) {
  return (
    <section className="form-section">
      {(title || actions) && (
        <div className="form-section-head">
          <div>
            <h3>{title}</h3>
            {description && <p>{description}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Switch({ checked, onChange, label, hint, size }) {
  return (
    <label className={`switch ${size || ""}`}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      {(label || hint) && (
        <span className="switch-text">
          {label}
          {hint && <span className="hint">{hint}</span>}
        </span>
      )}
    </label>
  );
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, o];
        return (
          <button key={String(v)} type="button" role="radio" aria-checked={value === v} className={value === v ? "active" : ""} onClick={() => onChange(v)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

/** A list of short strings edited as chips. Enter or comma adds one. */
export function TagInput({ value = [], onChange, placeholder = "Type and press Enter", suggestions = [] }) {
  const [draft, setDraft] = useState("");
  const input = useRef(null);
  const add = (text) => {
    const items = text.split(",").map((t) => t.trim()).filter(Boolean).filter((t) => !value.includes(t));
    if (items.length) onChange([...value, ...items]);
    setDraft("");
  };
  const open = suggestions.filter((s) => !value.includes(s));
  return (
    <div className="stack sm">
      <div className="tag-input" onClick={() => input.current?.focus()}>
        {value.map((t) => (
          <span key={t} className="tag">
            {t}
            <button type="button" onClick={() => onChange(value.filter((x) => x !== t))} aria-label={`Remove ${t}`}><Icon name="x" size={12} /></button>
          </span>
        ))}
        <input
          ref={input}
          value={draft}
          placeholder={value.length ? "" : placeholder}
          onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value) : setDraft(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(draft); }
            if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
          }}
          onBlur={() => draft && add(draft)}
        />
      </div>
      {open.length > 0 && (
        <div className="chips">
          {open.map((s) => <button type="button" key={s} className="chip" onClick={() => onChange([...value, s])}>+ {s}</button>)}
        </div>
      )}
    </div>
  );
}

/** A string → string map edited as rows. */
export function KeyValue({ value = {}, onChange, keyLabel = "Key", valueLabel = "Value", addLabel = "Add row" }) {
  const [rows, setRows] = useState(() => Object.entries(value || {}));
  const commit = (next) => {
    setRows(next);
    onChange(Object.fromEntries(next.filter(([k]) => k.trim())));
  };
  return (
    <div className="list-rows">
      {rows.map(([k, v], i) => (
        <div key={i} className="list-row">
          <input value={k} placeholder={keyLabel} onChange={(e) => commit(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <input value={typeof v === "string" ? v : JSON.stringify(v)} placeholder={valueLabel} onChange={(e) => commit(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <IconButton icon="trash" label="Remove" onClick={() => commit(rows.filter((_, j) => j !== i))} />
        </div>
      ))}
      <button type="button" className="add-row" onClick={() => setRows([...rows, ["", ""]])}><Icon name="plus" />{addLabel}</button>
    </div>
  );
}

export function IconButton({ icon, label, onClick, disabled }) {
  return (
    <button type="button" className="btn ghost icon-only" title={label} aria-label={label} onClick={onClick} disabled={disabled}>
      <Icon name={icon} />
    </button>
  );
}

export function EmptyState({ icon = "layers", tone = "lavender", title, children, action }) {
  return (
    <div className="empty-state">
      <span className={`tile-icon pastel ${tone}`}><Icon name={icon} /></span>
      {title && <h2>{title}</h2>}
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Tile({ tone = "lavender", icon, value, label, i = 0 }) {
  return (
    <div className={`tile pastel ${tone} rise`} style={{ "--i": i }}>
      {icon && <span className="tile-icon"><Icon name={icon} /></span>}
      <div className="stack" style={{ gap: 2 }}>
        <b>{value}</b>
        <span>{label}</span>
      </div>
    </div>
  );
}
