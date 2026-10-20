import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "./icons";

export function Button({ variant, size, className = "", ...props }) {
  return <button type="button" className={`btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-brand [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text ${variant || ""} ${size || ""} ${className}`} {...props} />;
}

export function Card({ title, description, icon, actions, children, flush, className = "" }) {
  return (
    <section className={`card shrink-0 flex flex-col bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-[calc(var(--r)_+_4px)] [border:1px_solid_var(--line-2)] shadow-none p-[16px_18px_18px] [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 [&_.card-head_.chips]:gap-0 [&_.card-head_.chips]:flex-nowrap [&_.card-head_.chips]:[border:1px_solid_var(--line-2)] [&_.card-head_.chips]:rounded-sm [&_.card-head_.chips]:overflow-hidden [&_.card-head_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_.card-head_.chip]:p-[0_10px] [&_.card-head_.chip]:border-0 [&_.card-head_.chip]:[border-left:1px_solid_var(--line-2)] [&_.card-head_.chip]:rounded-none [&_.card-head_.chip]:bg-transparent [&_.card-head_.chip]:text-muted [&_.card-head_.chip]:text-xs [&_.card-head_.chip]:font-medium-plus [&_.card-head_.chip:first-child]:border-l-0 [&_.card-head_.chip.active]:bg-press [&_.card-head_.chip.active]:text-text [&_.card-head_.chip.on]:bg-press [&_.card-head_.chip.on]:text-text ${flush ? "flush" : ""} ${className}`}>
      {(title || actions) && (
        <div className={`card-head flex items-center justify-between gap-3 min-h-[54px] p-[0_18px] bg-[color-mix(in_srgb,_var(--surface)_72%,_var(--canvas))] [border-radius:calc(var(--r)_+_4px)_calc(var(--r)_+_4px)_0_0] ${flush ? "" : "[margin:-16px_-18px_14px]"}`}>
          <div className="flex items-center gap-2.5 min-w-0">
            {icon && <Icon name={icon} size={18} className="icon shrink-0 text-brand" />}
            <div className="min-w-0">
              <h2>{title}</h2>
              {description && <p className="m-[2px_0_0] text-ui-sm text-muted">{description}</p>}
            </div>
          </div>
          <div className="row flex items-center gap-2 shrink-0 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">{actions}</div>
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, description, actions }) {
  return (
    <div className="page-head flex justify-between items-end gap-4 mb-6 [&_p]:m-[3px_0_0] [&_p]:text-muted [&_p]:max-w-[760px] max-navigation:flex-col max-navigation:items-start [&_h1]:text-[18px] [&_h1]:font-semibold-plus [&_h1]:tracking-[-0.015em] [&_p]:text-ui [&_.row]:gap-2 [&_.row]:shrink [&_>_div:first-child]:min-w-[220px] [&_>_div:first-child]:[flex:1_1_280px] [&_.row]:flex-wrap [&_.row]:justify-end [&_.row]:min-w-0 [&_.row]:max-w-[100%] [&_.row_>_.row]:flex-wrap">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" data-tour="page-actions">{actions}</div>
    </div>
  );
}

export function Badge({ tone, children }) {
  return <span className={`badge inline-flex items-center gap-1 p-[1px_7px] rounded-xs text-caption font-medium-plus bg-sunken text-muted whitespace-nowrap [&.green]:text-ok [&.green]:bg-ok-soft [&.red]:text-danger [&.red]:bg-danger-soft [&.yellow]:text-warn [&.yellow]:bg-warn-soft [&.blue]:text-info [&.blue]:bg-info-soft [&.brand]:text-brand-strong [&.brand]:bg-brand-soft [&_.dot]:w-1.5 [&_.dot]:h-1.5 [&_.dot]:rounded-full [&_.dot]:bg-[currentColor] ${tone || ""}`}>{children}</span>;
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
  return <div className="spinner w-4 h-4 [border:2px_solid_var(--line-2)] border-t-brand rounded-full animate-[spin_0.7s_linear_infinite]" />;
}

export function Empty({ children }) {
  return <div className="empty p-[40px_16px] text-center text-muted text-ui [&_>_.fw-empty]:p-[4px_0] [&_>_.fw-empty]:text-text">{children}</div>;
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{error.message || String(error)}</div>;
}

export function Loading({ state, children, empty }) {
  if (state.error) return <div style={{ padding: 16 }}><ErrorNote error={state.error} /></div>;
  if (state.loading && !state.data) return <div className="empty p-[40px_16px] text-center text-muted text-ui [&_>_.fw-empty]:p-[4px_0] [&_>_.fw-empty]:text-text"><div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "center" }}><Spinner /> Loading…</div></div>;
  const rows = state.data?.data ?? state.data;
  if (empty && Array.isArray(rows) && rows.length === 0) return <Empty>{empty}</Empty>;
  return children(state.data);
}

export function Table({ columns, rows, onRowClick, empty = "Nothing here yet." }) {
  if (!rows || rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap overflow-auto p-0! m-0!">
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
  if (value === null || value === undefined || value === "") return <span className="faint text-faint">—</span>;
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
    <label className={`field [label&]:flex [label&]:flex-col [label&]:gap-1.5 [label&]:text-ui-sm [label&]:font-medium-plus [label&]:text-text [label&]:min-w-0 [label&_>_.label-row]:flex [label&_>_.label-row]:justify-between [label&_>_.label-row]:gap-2 [label&_.optional]:text-faint [label&_.optional]:font-medium ${className}`}>
      {label && <span className="label-row"><span>{label}</span>{optional && <span className="optional">Optional</span>}</span>}
      {children}
      {hint && <span className="hint text-xs font-book text-muted leading-[1.45]">{hint}</span>}
      {error && <span className="error-text text-danger text-ui-sm font-medium">{error}</span>}
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
      {invalid && <span className="error-text text-danger text-ui-sm font-medium">Not valid JSON yet.</span>}
    </>
  );
}

export function Json({ value }) {
  return <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{JSON.stringify(value, null, 2)}</pre>;
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs flex gap-5 p-0 bg-transparent rounded-none mb-[18px] max-w-none overflow-auto [&_button]:bg-transparent! [&_button]:border-0 [&_button]:text-muted [&_button]:p-[0_2px] [&_button]:font-inherit [&_button]:font-medium [&_button]:text-body [&_button]:cursor-pointer [&_button]:rounded-none [&_button]:whitespace-nowrap [&_button:hover]:text-text [&_button.active]:text-text [&_button.active]:bg-surface [&_button.active]:shadow-none items-stretch w-full m-[0_0_16px] border-0 [border-bottom:1px_solid_var(--line)] overflow-x-auto [&_button]:relative [&_button]:h-[44px] [&_button]:m-0 [&_button]:shadow-none! [&_button.active]:font-semibold">
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
    <div className="page-tabs flex items-end gap-3 [border-bottom:1px_solid_var(--line)] mb-[18px]">
      <div className="page-tabs-list flex gap-[22px] flex-1 min-w-0 overflow-x-auto [&_button]:relative [&_button]:bg-none [&_button]:border-0 [&_button]:p-[10px_0_11px] [&_button]:font-inherit [&_button]:text-body [&_button]:font-medium-plus [&_button]:text-muted [&_button]:cursor-pointer [&_button]:whitespace-nowrap [&_button]:inline-flex [&_button]:items-center [&_button]:gap-1.5 [&_button:hover]:text-text [&_button.active]:text-text [&_button.active]:font-semibold-plus" role="tablist">
        {tabs.map((t) => (
          <button key={t.value} type="button" role="tab" aria-selected={value === t.value} className={value === t.value ? "active" : ""} onClick={() => onChange(t.value)}>
            {t.label}{t.count != null && <span className="page-tabs-count text-2xs font-semibold text-faint bg-sunken rounded-xs p-[0_6px]">{t.count}</span>}
          </button>
        ))}
      </div>
      {actions && <div className="page-tabs-actions flex gap-2 pb-2">{actions}</div>}
    </div>
  );
}

/** A vertical menu of sections: the secondary sidebar's look, for a sheet or a pane inside a page. ``items``: ``[{ value, label, sub? }]``. */
export function SideMenu({ items, value, onChange, className = "", label = "Sections" }) {
  return (
    <nav className={`side-menu-list flex flex-col gap-0.5 ${className}`} aria-label={label}>
      {items.map((t) => (
        <button key={t.value} type="button" className={`subnav-item [&.active_.subnav-icon]:text-brand-strong relative flex items-center gap-2 w-full min-h-8 p-[6px_10px] border-0 rounded-sm bg-none text-muted font-inherit text-body font-medium text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&.active]:bg-brand-soft [&.active]:text-brand-strong [&.active]:font-semibold h-auto [&.active]:shadow-none [&.active::before]:hidden [&.active]:border-brand-line [&.active_.icon]:text-brand ${value === t.value ? "active" : ""}`} aria-current={value === t.value ? "page" : undefined} onClick={() => onChange(t.value)}>
          <span className="subnav-text flex-1 min-w-0 grid gap-px [&_>_span]:overflow-hidden [&_>_span]:text-ellipsis [&_>_span]:whitespace-nowrap [&_small]:text-caption [&_small]:font-book [&_small]:text-faint [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap"><span>{t.label}</span>{t.sub && <small>{t.sub}</small>}</span>
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
    <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>
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
      <div className="toast-stack fixed bottom-14 right-4 flex flex-col gap-2 z-[60]">
        {toasts.map((t) => <div key={t.id} className={`toast flex items-center gap-2.5 bg-surface text-text p-[10px_14px_10px_10px] rounded-sm min-w-[220px] max-w-[460px] shadow-none font-medium-plus text-body animate-[pop_0.2s_ease] [&_.toast-dot]:shrink-0 [&_.toast-dot]:text-ok [&.error_.toast-dot]:text-danger [&_.toast-dot_.icon]:w-[17px] [&_.toast-dot_.icon]:h-[17px] [border:1px_solid_var(--line-2)] ${t.tone}`}><span className="toast-dot"><Icon name={t.tone === "error" ? "x" : "check"} /></span>{t.message}</div>)}
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
      <div className="sheet-overlay fixed inset-0 bg-[rgba(6,_5,_10,_0.55)] backdrop-filter-none z-[50] animate-[fade_0.15s_ease]" onMouseDown={onClose} />
      <div className="sheet fixed top-0 right-0 bottom-0 w-[min(720px,_100vw)] bg-canvas rounded-none shadow-none z-[51] flex flex-col overflow-hidden animate-[slide-in_0.18s_ease-out] [border-left:1px_solid_var(--line-2)] max-navigation:w-[100vw] max-navigation:border-l-0 border-0 [&_>_.page-tabs]:flex-none [&_>_.page-tabs]:m-0 [&_>_.page-tabs]:p-[0_20px]" role="dialog" aria-label={typeof title === "string" ? title : undefined}>
        <div className="sheet-head flex items-center gap-2.5 p-[0_12px_0_20px] [&_.tile-icon]:shrink-0 [&_h2]:text-heading [&_p]:m-[1px_0_0] [&_p]:text-ui-sm [&_p]:text-muted h-[var(--bar)] box-border [border-bottom:1px_solid_var(--line)] bg-canvas  shrink-0 [&_h2]:font-semibold [&_h2]:overflow-hidden [&_h2]:text-ellipsis [&_h2]:whitespace-nowrap [&_p]:hidden [&_.tile-icon_.icon]:w-[22px] [&_.tile-icon_.icon]:h-[22px]">
          {icon && <span className={`tile-icon pastel text-text pastel-contrast [&.lavender]:text-lavender [&.peach]:text-peach [&.mint]:text-mint [&.butter]:text-butter [&.sky]:text-sky [&.rose]:text-rose ${tone}`}><Icon name={icon} size={20} /></span>}
          <div className="grow flex-1 min-w-0">
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {tabs && tabsPlacement === "top" && <PageTabs tabs={tabs} value={tab} onChange={onTab} />}
        {tabs && tabsPlacement === "side" ? (
          <div className="sheet-split flex-1 flex min-h-0 [&_.sheet-body]:min-w-0 [&_.sheet-body]:pt-[18px] max-tablet:flex-col">
            <SideMenu className="sheet-nav w-[188px] flex-none flex flex-col gap-0.5 p-[14px_10px] [border-right:1px_solid_var(--line-2)] overflow-auto bg-canvas max-tablet:w-auto max-tablet:flex-row max-tablet:border-r-0 max-tablet:[border-bottom:1px_solid_var(--line-2)] max-tablet:p-[8px_12px] max-tablet:[&_.subnav-item]:whitespace-nowrap" items={tabs} value={tab} onChange={onTab} />
            <div className="sheet-body flex-1 overflow-auto p-[20px_20px_24px] flex flex-col gap-3.5">{children}</div>
          </div>
        ) : (
          <div className="sheet-body flex-1 overflow-auto p-[20px_20px_24px] flex flex-col gap-3.5">{children}</div>
        )}
        {footer && <div className="sheet-foot shrink-0 flex items-center gap-2 p-[12px_20px] bg-canvas [border-top:1px_solid_var(--line)] min-h-[var(--dock)] box-border justify-end [&_.btn.primary]:bg-brand [&_.btn.primary]:border-brand [&_.btn.primary]:text-[#15121f] [&_.btn.primary:hover]:bg-[var(--brand-strong)] [&_.btn.primary:hover]:border-brand-strong">{footer}</div>}
      </div>
    </>
  );
}

export function Section({ title, description, icon, actions, children }) {
  return (
    <section className="form-section shrink-0 flex flex-col overflow-hidden rounded-[calc(var(--r)_+_2px)] bg-[color-mix(in_srgb,_var(--surface)_40%,_var(--canvas))] [border:1px_solid_var(--line)] [&+&]:mt-3">
      {(title || actions) && (
        <div className="form-section-head flex items-center justify-between gap-3 min-h-[48px] p-[9px_16px] bg-[color-mix(in_srgb,_var(--surface)_66%,_var(--canvas))] [border-bottom:1px_solid_var(--line)]">
          <div className="flex items-center gap-2.5 min-w-0">
            {icon && <Icon name={icon} size={17} className="icon shrink-0 text-brand" />}
            <div className="min-w-0">
              <h3 className="text-[13.5px] font-semibold tracking-[-0.01em]">{title}</h3>
              {description && <p className="m-[2px_0_0] text-ui-sm text-muted">{description}</p>}
            </div>
          </div>
          {actions && <div className="row flex items-center gap-2 shrink-0 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">{actions}</div>}
        </div>
      )}
      <div className="flex flex-col gap-3 p-[16px] min-w-0">{children}</div>
    </section>
  );
}

export function Switch({ checked, onChange, label, hint, size }) {
  return (
    <label className={`switch flex items-start gap-3 cursor-pointer [&_input]:absolute [&_input]:opacity-0 [&_input]:pointer-events-none [&_.track]:w-[38px] [&_.track]:h-[22px] [&_.track]:rounded-[99px] [&_.track]:bg-[var(--line-2)] [&_.track]:relative [&_.track]:shrink-0 [&_.track]:transition-[background_0.15s] [&_.track]:mt-px [&_input:checked_+_.track]:bg-ink [&_input:checked_+_.track::after]:transform-[translateX(16px)] [&_input:checked_+_.track::after]:bg-[var(--on-ink)] [&_input:focus-visible_+_.track]:shadow-none [&_input:focus-visible_+_.track]:outline-[1px_solid_var(--brand)] [&_.switch-text]:flex [&_.switch-text]:flex-col [&_.switch-text]:gap-px [&_.switch-text]:text-body [&_.switch-text]:font-semibold [&_.switch-text_.hint]:font-book [&.sm_.track]:w-8 [&.sm_.track]:h-[19px] [&.sm_.track::after]:w-[13px] [&.sm_.track::after]:h-[13px] [&.sm_input:checked_+_.track::after]:transform-[translateX(13px)] ${size || ""}`}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      {(label || hint) && (
        <span className="switch-text">
          {label}
          {hint && <span className="hint text-xs font-book text-muted leading-[1.45]">{hint}</span>}
        </span>
      )}
    </label>
  );
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="segmented self-start w-fit max-w-[100%] inline-flex gap-0 p-0 bg-sunken rounded-sm flex-nowrap [&_button]:border-0 [&_button]:bg-transparent [&_button]:text-muted [&_button]:font-inherit [&_button]:text-ui-sm [&_button]:font-medium-plus [&_button]:p-[0_12px] [&_button]:rounded-none [&_button]:cursor-pointer [&_button.active]:bg-surface [&_button.active]:text-text [&_button.active]:shadow-none! [&_button:disabled]:opacity-50 [&_button:disabled]:cursor-not-allowed [border:1px_solid_var(--line-2)] overflow-hidden [&_button]:h-[calc(var(--control)_-_2px)] [&_button]:[border-left:1px_solid_var(--line-2)] [&_button]:shadow-none! [&_button:first-child]:border-l-0 [&_button:hover]:bg-hover [&_button:hover]:text-text [&_button.active]:font-semibold" role="radiogroup">
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
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
      <div className="tag-input flex flex-wrap items-center gap-1.5 [border:1px_solid_var(--line-2)] rounded-sm p-[3px_6px] bg-canvas min-h-control cursor-text [&:focus-within]:border-brand [&:focus-within]:shadow-none [&_input]:border-0 [&_input]:p-[3px_4px] [&_input]:flex-1 [&_input]:min-w-[120px] [&_input]:w-auto [&_input]:shadow-none [&_input]:bg-transparent" onClick={() => input.current?.focus()}>
        {value.map((t) => (
          <span key={t} className="tag inline-flex items-center gap-1 bg-brand-soft text-brand-strong rounded-xs p-[2px_4px_2px_10px] text-ui-sm font-semibold font-mono [&_button]:border-0 [&_button]:bg-none [&_button]:text-inherit [&_button]:cursor-pointer [&_button]:w-[18px] [&_button]:h-[18px] [&_button]:rounded-full [&_button]:grid [&_button]:place-items-center [&_button]:p-0 [&_button]:opacity-70 [&_button:hover]:opacity-100 [&_button:hover]:bg-[rgba(0,_0,_0,_0.06)]">
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
        <div className="chips flex flex-wrap gap-1.5">
          {open.map((s) => <button type="button" key={s} className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" onClick={() => onChange([...value, s])}>+ {s}</button>)}
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
    <div className="list-rows flex flex-col gap-2">
      {rows.map(([k, v], i) => (
        <div key={i} className="list-row flex items-center gap-2 [&_>_input]:flex-1 [&_>_input]:min-w-0 [&_>_select]:flex-1 [&_>_select]:min-w-0">
          <input value={k} placeholder={keyLabel} onChange={(e) => commit(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <input value={typeof v === "string" ? v : JSON.stringify(v)} placeholder={valueLabel} onChange={(e) => commit(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <IconButton icon="trash" label="Remove" onClick={() => commit(rows.filter((_, j) => j !== i))} />
        </div>
      ))}
      <button type="button" className="add-row [border:1.5px_dashed_var(--line-2)] bg-none rounded-sm p-2.5 text-muted font-inherit font-semibold text-ui cursor-pointer flex items-center justify-center gap-1.5 [&:hover]:border-brand [&:hover]:text-brand-strong [&:hover]:bg-brand-soft [&_.icon]:w-4 [&_.icon]:h-4" onClick={() => setRows([...rows, ["", ""]])}><Icon name="plus" />{addLabel}</button>
    </div>
  );
}

export function IconButton({ icon, label, onClick, disabled }) {
  return (
    <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text ghost icon-only" title={label} aria-label={label} onClick={onClick} disabled={disabled}>
      <Icon name={icon} />
    </button>
  );
}

export function EmptyState({ icon = "layers", tone = "lavender", title, children, action }) {
  return (
    <div className="empty-state flex flex-col items-center gap-2 p-[56px_20px] text-center [&_.tile-icon]:mb-1 [&_.tile-icon]:grid [&_.tile-icon]:place-items-center [&_.tile-icon_.icon]:w-[34px] [&_.tile-icon_.icon]:h-[34px] [&_p]:m-0 [&_p]:text-muted [&_p]:max-w-[420px] [&_.tile-icon]:border-0 [&_h2]:text-heading [&_h2]:font-semibold">
      <span className={`tile-icon pastel text-text pastel-contrast [&.lavender]:text-lavender [&.peach]:text-peach [&.mint]:text-mint [&.butter]:text-butter [&.sky]:text-sky [&.rose]:text-rose ${tone}`}><Icon name={icon} /></span>
      {title && <h2>{title}</h2>}
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Tile({ tone = "lavender", icon, value, label, i = 0 }) {
  return (
    <div className={`tile rounded-none p-[14px_16px] flex flex-col gap-1 min-h-0 [&_.tile-icon]:grid [&_.tile-icon]:place-items-center [&_.tile-icon_.icon]:w-[26px] [&_.tile-icon_.icon]:h-[26px] [&.compact]:min-h-0 [&.compact]:flex-row [&.compact]:items-center [&.compact]:gap-3.5 [&.compact]:p-[16px_18px] [&.compact_b]:text-[24px] [&_b]:text-display-sm [&_b]:font-semibold [&_b]:tracking-[-0.02em] [&_b]:leading-[1.1] [&_span]:text-ui-sm [&_span]:font-medium [&_span]:text-muted relative m-0 border-0 [border-right:1px_solid_var(--line)] bg-transparent! text-text shadow-none! text-left cursor-default [&:last-child]:border-r-0 [&_b]:text-text [&_b]:tabular-nums [&_.tile-icon]:text-brand-strong [&.pastel]:bg-transparent [&_.tile-icon]:border-0 pastel text-text pastel-contrast [&.lavender]:text-lavender [&.peach]:text-peach [&.mint]:text-mint [&.butter]:text-butter [&.sky]:text-sky [&.rose]:text-rose ${tone} rise animate-[rise_0.45s_cubic-bezier(0.2,_1,_0.3,_1)_both] [animation-delay:calc(var(--i,_0)_*_60ms)]`} style={{ "--i": i }}>
      {icon && <span className="tile-icon"><Icon name={icon} /></span>}
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 2 }}>
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
    <div className={`popover-wrap relative inline-flex ${className}`} ref={wrap}>
      {trigger({ ref: button, "aria-haspopup": "menu", "aria-expanded": open, onClick: () => setOpen((value) => !value) })}
      {open && (
        <div
          ref={panel}
          className={`popover absolute top-[calc(100%_+_6px)] z-[70] flex flex-col max-h-[min(560px,_calc(100vh_-_80px))] bg-surface [border:1px_solid_var(--line-2)] rounded-sm shadow-none overflow-hidden animate-[pop-in_0.12s_ease-out] [&.align-start]:left-0 [&.align-end]:right-0 [&_.pop-list[role=listbox]]:border-0 [&_.pop-list[role=listbox]]:rounded-none [&_.pop-list[role=listbox]]:bg-transparent [&_.pop-list[role=listbox]]:shadow-none align-${align}`}
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
    <span className="id-chip inline-flex items-center h-control-sm max-w-[min(300px,_100%)] [border:1px_solid_var(--line-2)] rounded-sm overflow-hidden text-xs" title={title || `${label ? `${label}: ` : ""}${value}`}>
      {label && <span className="id-chip-label inline-flex items-center h-full p-[0_8px] [border-right:1px_solid_var(--line-2)] text-muted whitespace-nowrap font-medium">{label}</span>}
      <code className="id-chip-value p-[0_8px] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-xs text-text">{value}</code>
      <button type="button" className="id-chip-copy grid place-items-center w-[28px] h-full shrink-0 border-0 [border-left:1px_solid_var(--line-2)] bg-none text-muted cursor-pointer [&:hover]:bg-hover [&:hover]:text-text" onClick={copy} aria-label={`Copy ${label || "value"}`}><Icon name="copy" size={13} /></button>
    </span>
  );
}
