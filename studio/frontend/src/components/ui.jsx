import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
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
      <div className="row" data-tour="page-actions">{actions}</div>
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

/** Tabs across one record's view (a function's overview, runs, code): an underlined row, with room on the right for actions. */
export function PageTabs({ tabs, value, onChange, actions }) {
  return (
    <div className="page-tabs">
      <div className="page-tabs-list" role="tablist">
        {tabs.map((t) => (
          <button key={t.value} type="button" role="tab" aria-selected={value === t.value} className={value === t.value ? "active" : ""} onClick={() => onChange(t.value)}>
            {t.label}{t.count != null && <span className="page-tabs-count">{t.count}</span>}
          </button>
        ))}
      </div>
      {actions && <div className="page-tabs-actions">{actions}</div>}
    </div>
  );
}

/** A vertical menu of sections: the secondary sidebar's look, for a sheet or a pane inside a page. ``items``: ``[{ value, label, sub? }]``. */
export function SideMenu({ items, value, onChange, className = "", label = "Sections" }) {
  return (
    <nav className={`side-menu-list ${className}`} aria-label={label}>
      {items.map((t) => (
        <button key={t.value} type="button" className={`subnav-item ${value === t.value ? "active" : ""}`} aria-current={value === t.value ? "page" : undefined} onClick={() => onChange(t.value)}>
          <span className="subnav-text"><span>{t.label}</span>{t.sub && <small>{t.sub}</small>}</span>
        </button>
      ))}
    </nav>
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
 *  side or top tabs, a scrolling body of form sections, and a sticky footer. */
export function Sheet({ title, subtitle, icon, tone = "lavender", tabs, tab, onTab, tabsPlacement = "side", onClose, footer, children }) {
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
        {tabs && tabsPlacement === "top" && <PageTabs tabs={tabs} value={tab} onChange={onTab} />}
        {tabs && tabsPlacement === "side" ? (
          <div className="sheet-split">
            <SideMenu className="sheet-nav" items={tabs} value={tab} onChange={onTab} />
            <div className="sheet-body">{children}</div>
          </div>
        ) : (
          <div className="sheet-body">{children}</div>
        )}
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

/**
 * The one popover every menu in Studio uses: anchored under its trigger, kept inside the viewport,
 * closed by Escape or a click outside, and returning focus to the trigger when it closes.
 *
 * `trigger` is a render function `(props) => element` that receives the props the button needs.
 */
export function Popover({ trigger, children, align = "start", width = 320, label, className = "" }) {
  const [open, setOpen] = useState(false);
  const [shift, setShift] = useState(0);
  const wrap = useRef(null);
  const panel = useRef(null);
  const button = useRef(null);
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus?.();
  };
  useEffect(() => {
    if (!open) return undefined;
    const key = (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } };
    const down = (event) => { if (wrap.current && !wrap.current.contains(event.target)) close(false); };
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", down);
    return () => { window.removeEventListener("keydown", key, true); window.removeEventListener("pointerdown", down); };
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !panel.current) { setShift(0); return; }
    const rect = panel.current.getBoundingClientRect();
    const gap = 8;
    if (rect.right > window.innerWidth - gap) setShift(window.innerWidth - gap - rect.right);
    else if (rect.left < gap) setShift(gap - rect.left);
    else setShift(0);
    // Focus the first field (a search box) or the first item, so the menu works from the keyboard.
    const first = panel.current.querySelector("input, [role=menuitem], [role=option], a, button");
    first?.focus?.({ preventScroll: true });
  }, [open]);
  const onKeyDown = (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || !panel.current) return;
    const items = [...panel.current.querySelectorAll("[role=menuitem], [role=option]")].filter((el) => !el.disabled);
    if (!items.length) return;
    event.preventDefault();
    const at = items.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? (at + 1) % items.length : (at <= 0 ? items.length - 1 : at - 1);
    items[next].focus();
  };
  return (
    <div className={`popover-wrap ${className}`} ref={wrap}>
      {trigger({ ref: button, "aria-haspopup": "menu", "aria-expanded": open, onClick: () => setOpen((value) => !value) })}
      {open && (
        <div
          ref={panel}
          className={`popover align-${align}`}
          role="menu"
          aria-label={label}
          style={{ width, transform: shift ? `translateX(${shift}px)` : undefined }}
          onKeyDown={onKeyDown}
        >
          {typeof children === "function" ? children({ close }) : children}
        </div>
      )}
    </div>
  );
}

/** A value worth copying (an ID, a URL), shown truncated in a bordered chip with a copy icon. */
export function IdChip({ value, label, title }) {
  const toast = useToast();
  if (!value) return null;
  const copy = async () => toast((await copyToClipboard(value)) ? `${label || "Value"} copied` : "Could not copy. Select it and copy by hand.", "ok");
  return (
    <span className="id-chip" title={title || `${label ? `${label}: ` : ""}${value}`}>
      {label && <span className="id-chip-label">{label}</span>}
      <code className="id-chip-value">{value}</code>
      <button type="button" className="id-chip-copy" onClick={copy} aria-label={`Copy ${label || "value"}`}><Icon name="copy" size={13} /></button>
    </span>
  );
}
