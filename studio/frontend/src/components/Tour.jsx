import { router } from "@inertiajs/react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { Button } from "./ui";
import { envHref } from "./Layout";
import { CHAPTERS, STEPS, readTour, writeTour } from "../lib/tour";

const PAD = 8;
const CARD_W = 372;

function place(rect, preferred, cardHeight) {
  const width = window.innerWidth, height = window.innerHeight, gap = 16;
  if (!rect || preferred === "center") return { left: Math.max(16, (width - CARD_W) / 2), top: Math.max(16, (height - cardHeight) / 2), center: true };
  const options = {
    right: { left: rect.right + gap, top: rect.top + rect.height / 2 - cardHeight / 2 },
    left: { left: rect.left - CARD_W - gap, top: rect.top + rect.height / 2 - cardHeight / 2 },
    bottom: { left: rect.left + rect.width / 2 - CARD_W / 2, top: rect.bottom + gap },
    top: { left: rect.left + rect.width / 2 - CARD_W / 2, top: rect.top - cardHeight - gap },
  };
  const fits = (p) => p.left >= 12 && p.left + CARD_W <= width - 12 && p.top >= 12 && p.top + cardHeight <= height - 12;
  const order = [preferred, "right", "bottom", "left", "top"];
  const chosen = order.map((name) => options[name]).find(fits) || options[preferred] || options.bottom;
  return {
    left: Math.min(Math.max(12, chosen.left), width - CARD_W - 12),
    top: Math.min(Math.max(12, chosen.top), height - cardHeight - 12),
  };
}

function Confetti() {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    const ctx = canvas.getContext("2d");
    const resize = () => { canvas.width = window.innerWidth; canvas.height = window.innerHeight; };
    resize();
    const colors = ["#6d5bd0", "#cfc4fa", "#ffd4b8", "#c4ebd9", "#f8e6a8", "#c3dcf6", "#f7cadb"];
    const bits = Array.from({ length: 140 }, () => ({
      x: canvas.width / 2 + (Math.random() - 0.5) * 240, y: canvas.height * 0.45,
      vx: (Math.random() - 0.5) * 11, vy: -Math.random() * 12 - 3, size: 5 + Math.random() * 6,
      color: colors[Math.floor(Math.random() * colors.length)], spin: Math.random() * 6, rot: Math.random() * 6,
    }));
    let frame = 0, raf;
    const tick = () => {
      frame += 1;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const bit of bits) {
        bit.vy += 0.32; bit.x += bit.vx; bit.y += bit.vy; bit.rot += bit.spin * 0.05;
        ctx.save(); ctx.translate(bit.x, bit.y); ctx.rotate(bit.rot);
        ctx.globalAlpha = Math.max(0, 1 - frame / 140);
        ctx.fillStyle = bit.color; ctx.fillRect(-bit.size / 2, -bit.size / 4, bit.size, bit.size / 2);
        ctx.restore();
      }
      if (frame < 140) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className="tour-confetti fixed inset-0 w-full h-full pointer-events-none" aria-hidden="true" />;
}

/** A skippable walk through Studio: a spotlight on one part at a time, with the page opened behind it. */
export default function Tour({ env, section }) {
  const [tour, setTour] = useState(() => readTour());
  const [rect, setRect] = useState(null);
  const [cardHeight, setCardHeight] = useState(240);
  const [celebrate, setCelebrate] = useState(0);
  const card = useRef(null);
  const step = STEPS[Math.min(tour.step, STEPS.length - 1)];
  const running = tour.status === "running" && !!env;

  const update = useCallback((next) => {
    setTour((current) => {
      const value = typeof next === "function" ? next(current) : next;
      writeTour(value);
      return value;
    });
  }, []);

  // First time anyone opens Studio: begin, once the page has settled.
  useEffect(() => {
    if (!env || tour.status !== "new") return undefined;
    const timer = setTimeout(() => update({ status: "running", step: 0 }), 900);
    return () => clearTimeout(timer);
  }, [env, tour.status, update]);

  useEffect(() => {
    const onStart = (event) => {
      const { chapter, step: at } = event.detail || {};
      const first = typeof at === "number" ? at : chapter ? STEPS.findIndex((item) => item.chapter === chapter) : 0;
      update({ status: "running", step: Math.max(0, first) });
    };
    window.addEventListener("pawabase:tour", onStart);
    return () => window.removeEventListener("pawabase:tour", onStart);
  }, [update]);

  // Open the page a step is about, so there is something real behind the spotlight.
  useEffect(() => {
    if (running && step.section && step.section !== section) router.visit(envHref(env, step.section), { preserveScroll: true });
  }, [running, tour.step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Follow the highlighted element: it moves when the page loads, scrolls or resizes.
  useEffect(() => {
    if (!running) return undefined;
    setRect(null);
    let scrolled = false;
    const measure = () => {
      const element = step.target ? document.querySelector(`[data-tour="${step.target}"]`) : null;
      if (!element) { setRect((current) => (current ? null : current)); return; }
      const box = element.getBoundingClientRect();
      if (!scrolled && (box.top < 0 || box.bottom > window.innerHeight)) { element.scrollIntoView({ block: "center" }); scrolled = true; }
      setRect((current) => {
        const next = { left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height };
        return current && Math.abs(current.left - next.left) + Math.abs(current.top - next.top) + Math.abs(current.width - next.width) + Math.abs(current.height - next.height) < 0.5 ? current : next;
      });
    };
    measure();
    const timer = setInterval(measure, 140);
    window.addEventListener("resize", measure);
    return () => { clearInterval(timer); window.removeEventListener("resize", measure); };
  }, [running, tour.step, section]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (card.current) setCardHeight(card.current.offsetHeight);
  }, [tour.step, rect === null]);

  const go = useCallback((to) => {
    const next = Math.max(0, Math.min(STEPS.length - 1, to));
    update({ status: "running", step: next });
    if (STEPS[next].last) setCelebrate((n) => n + 1);
  }, [update]);
  const next = () => (tour.step >= STEPS.length - 1 ? update({ status: "done", step: 0 }) : go(tour.step + 1));
  const back = () => go(tour.step - 1);
  const skip = () => update({ status: tour.step === 0 ? "skipped" : "paused", step: tour.step });

  // Steps the person can do themselves move on when they do.
  useEffect(() => {
    if (!running || !step.advance) return undefined;
    const advance = () => setTimeout(() => go(tour.step + 1), step.advance.on === "click" ? 420 : 650);
    if (step.advance.on === "click") {
      const element = document.querySelector(`[data-tour="${step.target}"]`);
      if (!element) return undefined;
      element.addEventListener("click", advance, { once: true, capture: true });
      return () => element.removeEventListener("click", advance, { capture: true });
    }
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === step.advance.key) advance();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, tour.step]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!running) return undefined;
    const onKey = (event) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (event.key === "Escape") skip();
      else if (!typing && event.key === "ArrowRight") next();
      else if (!typing && event.key === "ArrowLeft") back();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, tour.step]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!running) return null;
  const chapter = CHAPTERS.find((item) => item.id === step.chapter);
  const spot = rect && step.place !== "center" ? { left: rect.left - PAD, top: rect.top - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 } : null;
  const position = place(spot ? { ...rect, left: spot.left, top: spot.top, right: spot.left + spot.width, bottom: spot.top + spot.height, width: spot.width, height: spot.height } : null, step.place, cardHeight);
  const veil = spot ? [
    { left: 0, top: 0, width: "100%", height: Math.max(0, spot.top) },
    { left: 0, top: spot.top + spot.height, width: "100%", height: `calc(100% - ${spot.top + spot.height}px)` },
    { left: 0, top: spot.top, width: Math.max(0, spot.left), height: spot.height },
    { left: spot.left + spot.width, top: spot.top, width: `calc(100% - ${spot.left + spot.width}px)`, height: spot.height },
  ] : [{ left: 0, top: 0, width: "100%", height: "100%" }];
  const index = tour.step;
  const welcome = step.chapter === "welcome";

  return (
    <div className="tour fixed inset-0 z-[200] pointer-events-none" role="dialog" aria-modal="false" aria-label={`Studio tour, step ${index + 1} of ${STEPS.length}: ${step.title}`}>
      {veil.map((style, i) => <div key={i} className="tour-veil fixed bg-[rgba(20,_16,_36,_0.58)] backdrop-filter-none pointer-events-auto transition-[left_.38s_cubic-bezier(.2,.8,.2,1),_top_.38s_cubic-bezier(.2,.8,.2,1),_width_.38s_cubic-bezier(.2,.8,.2,1),_height_.38s_cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-[none] motion-reduce:animate-[none]" style={style} onClick={(event) => event.stopPropagation()} />)}
      {spot && <div className="tour-ring fixed [border:2px_solid_var(--brand)] rounded-sm pointer-events-none shadow-none transition-[left_.38s_cubic-bezier(.2,.8,.2,1),_top_.38s_cubic-bezier(.2,.8,.2,1),_width_.38s_cubic-bezier(.2,.8,.2,1),_height_.38s_cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-[none] motion-reduce:animate-[none]" style={spot} />}
      {celebrate > 0 && step.last && <Confetti key={celebrate} />}
      <div ref={card} className={`tour-card fixed pointer-events-auto bg-surface text-text rounded-sm p-[18px_20px_16px] shadow-none [border:1px_solid_var(--line)] flex flex-col gap-2.5 transition-[left_.38s_cubic-bezier(.2,.8,.2,1),_top_.38s_cubic-bezier(.2,.8,.2,1)] animate-[tour-in_.32s_cubic-bezier(.2,.8,.2,1)] [&.centered]:p-[24px_26px_20px] [&.centered_h2]:text-display-sm [&_h2]:m-0 [&_h2]:text-[17px] [&_h2]:tracking-[-0.01em] [&_p]:m-0 [&_p]:text-muted [&_p]:text-body [&_p]:leading-[1.55] motion-reduce:transition-[none] motion-reduce:animate-[none] max-phone:left-3! max-phone:right-3 max-phone:w-auto! tone-${chapter.tone} ${position.center ? "centered" : ""}`} style={{ left: position.left, top: position.top, width: CARD_W }}>
        <div className="tour-progress flex gap-1 m-[-4px_0_4px]" aria-hidden="true">
          {CHAPTERS.map((item) => {
            const all = STEPS.map((s, i) => [s, i]).filter(([s]) => s.chapter === item.id);
            const done = all.filter(([, i]) => i < index).length + (all.some(([, i]) => i === index) ? 0.5 : 0);
            return <span key={item.id} className="tour-seg h-1 rounded-xs bg-sunken overflow-hidden min-w-1.5 [&_i]:block [&_i]:h-full [&_i]:bg-brand [&_i]:rounded-xs [&_i]:transition-[width_.4s_ease] motion-reduce:[&_i]:transition-[none] motion-reduce:[&_i]:animate-[none]" style={{ flexGrow: all.length }}><i style={{ width: `${(done / all.length) * 100}%` }} /></span>;
          })}
        </div>
        <div className="tour-meta flex items-center gap-2">
          <span className={`tour-badge w-[30px] h-[30px] rounded-sm inline-flex items-center justify-center pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${chapter.tone}`}><Icon name={step.icon} size={16} /></span>
          <span className="tour-chapter text-caption font-semibold-plus tracking-[.06em] uppercase text-muted">{chapter.label}</span>
          <span className="tour-count ml-auto [font:600_12px_var(--mono)] text-faint">{index + 1} / {STEPS.length}</span>
        </div>
        <h2>{step.title}</h2>
        <p>{step.body}</p>
        {step.hint && <div className="tour-hint flex items-center gap-2 text-ui-sm font-semibold text-brand-strong bg-brand-soft rounded-sm p-[8px_10px] [&_.faint]:font-book [&_.faint]:text-muted"><span className="tour-pulse w-2 h-2 rounded-full bg-brand animate-[tour-ping_1.4s_ease-out_infinite] shrink-0 motion-reduce:animate-[none]" />{step.hint}<span className="faint text-faint"> or just continue</span></div>}
        {welcome && (
          <div className="tour-chapters flex flex-col gap-1.5">
            <span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Jump to</span>
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
              {CHAPTERS.filter((c) => !["welcome", "finish"].includes(c.id)).map((c) => (
                <button key={c.id} type="button" className={`tour-chip border-0 rounded-sm p-[6px_12px] [font:600_12.5px_var(--sans)] cursor-pointer text-pastel-ink transition-[transform_.15s_ease] [&:hover]:transform-[none]! pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${c.tone}`} onClick={() => go(STEPS.findIndex((s) => s.chapter === c.id))}>{c.label}</button>
              ))}
            </div>
          </div>
        )}
        <div className="tour-actions flex items-center gap-2 mt-1">
          {step.last ? (
            <>
              <Button onClick={() => { update({ status: "done", step: 0 }); router.visit(envHref(env, "resources")); }}>Create a resource</Button>
              <Button variant="primary" onClick={next}>Finish</Button>
            </>
          ) : (
            <>
              <button type="button" className="tour-skip bg-none border-0 text-muted [font:600_12.5px_var(--sans)] cursor-pointer p-[6px_4px] [&:hover]:text-text" onClick={() => update({ status: "skipped", step: 0 })}>{welcome ? "Not now" : "Skip tour"}</button>
              <span className="grow flex-1 min-w-0" />
              {index > 0 && <Button size="sm" onClick={back}>Back</Button>}
              <Button size="sm" variant="primary" onClick={next}>{welcome ? "Start the tour" : "Next"} <Icon name="chevronRight" size={14} /></Button>
            </>
          )}
        </div>
        {!welcome && !step.last && <div className="tour-keys text-caption text-faint flex items-center gap-1 [&_kbd]:[font:600_11px_var(--mono)] [&_kbd]:bg-sunken [&_kbd]:rounded-sm [&_kbd]:p-[2px_6px] [&_kbd]:text-muted"><kbd>←</kbd><kbd>→</kbd> move · <kbd>Esc</kbd> pause</div>}
      </div>
    </div>
  );
}
