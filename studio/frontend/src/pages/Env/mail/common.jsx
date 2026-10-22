// Small pieces the Mail pages share.

import { useEffect, useRef, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge } from "../../../components/ui";
import { LEVEL_ORDER } from "./editor/checks";

export const CHECK_TONES = { error: "red", warn: "yellow", info: "" };
const LEVEL_LABEL = { error: "Fix", warn: "Check", info: "Note" };

/** Checks, grouped, most serious first. */
export function CheckList({ checks }) {
  if (!checks.length) {
    return (
      <div className="mail-inspector-empty">
        <Icon name="check" size={20} className="text-ok" />
        <p>Nothing to fix. The email reads well, links work and every value has a sample.</p>
      </div>
    );
  }
  const groups = [...new Set(checks.map((c) => c.group))];
  return (
    <div className="mail-checks">
      {groups.map((group) => (
        <div key={group} className="mail-check-group">
          <b>{group}</b>
          {checks.filter((c) => c.group === group).sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]).map((c, i) => (
            <div key={i} className={`mail-check ${c.level}`}>
              <Badge tone={CHECK_TONES[c.level]}>{LEVEL_LABEL[c.level]}</Badge>
              <span>
                {c.message}
                {c.detail && <small>{c.detail}</small>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

const STATUS = {
  sent: ["Sent", "green"], suppressed: ["Suppressed", "yellow"], failed: ["Failed", "red"], scheduled: ["Scheduled", "blue"],
  sending: ["Sending", "blue"], canceled: ["Canceled", ""],
};

export function MailStatus({ value }) {
  const [label, tone] = STATUS[value] || [value, ""];
  return <Badge tone={tone}><span className="dot" />{label}</Badge>;
}

/** A rendered email, scaled down to fit a card. Only drawn once it scrolls into view. */
export function Thumbnail({ html, height = 220 }) {
  const box = useRef(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!box.current) return undefined;
    const observer = new IntersectionObserver(([entry]) => entry.isIntersecting && setSeen(true), { rootMargin: "200px" });
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  return (
    <div className="mail-thumb" ref={box} style={{ height }}>
      {seen && html ? <iframe title="Template preview" sandbox="" srcDoc={html} tabIndex={-1} scrolling="no" /> : <div className="mail-thumb-empty">{html ? "" : "No content yet"}</div>}
    </div>
  );
}
