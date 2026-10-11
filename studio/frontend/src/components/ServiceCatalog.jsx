import { Link } from "@inertiajs/react";
import Hair from "./Hair";
import { startTour } from "../lib/tour";
import { CATEGORIES, SERVICES, readRecent } from "../lib/services";

const LABEL = Object.fromEntries(CATEGORIES.map((category) => [category.id, category.label]));

const openTerminal = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", ctrlKey: true, bubbles: true }));

const QUICK = [
  ["data", "Create a collection", "resources"],
  ["access", "Invite someone", "users"],
  ["automation", "Build a flow", "flows"],
  ["secrets", "Create an API key", "keys"],
  ["functions", "Try your API", "explorer"],
];

/** The home of an environment: where to start, what you used last, and every service on one hairline grid. */
export default function ServiceCatalog({ env, hrefFor }) {
  const recent = readRecent().map((key) => SERVICES.find((service) => service.key === key)).filter(Boolean);
  return (
    <section className="catalog" aria-label="Services">
      <header className="catalog-hero">
        <div>
          <span className="eyebrow">Environment</span>
          <h1>{env}</h1>
          <p>Everything this backend does, as independent services that share one account, one set of access rules and one stream of events.</p>
        </div>
        <button type="button" className="hero-link" onClick={() => startTour()}><Hair name="events" size={16} /> Take the tour</button>
      </header>

      <div className="quick">
        <span className="quick-label">Start with</span>
        {QUICK.map(([icon, label, page]) => (
          <Link key={label} href={hrefFor(page)} className="quick-action"><Hair name={icon} size={17} />{label}</Link>
        ))}
      </div>

      {recent.length > 0 && (
        <div className="quick">
          <span className="quick-label">Recent</span>
          {recent.map((service) => (
            <Link key={service.key} href={hrefFor(service.pages[0][0])} className="quick-action quiet"><Hair name={service.icon} size={17} />{service.label}</Link>
          ))}
        </div>
      )}

      <div className="catalog-grid">
        {SERVICES.map((service) => (
          <article key={service.key} className="service-cell">
            <span className="service-cell-eyebrow">{LABEL[service.category]}</span>
            <Link href={hrefFor(service.pages[0][0])} className="service-cell-head">
              <Hair name={service.icon} size={24} />
              <b>{service.label}</b>
            </Link>
            <p>{service.description}</p>
            <nav className="service-pages" aria-label={`${service.label} pages`}>
              {service.pages.map(([key, label]) => <Link key={key} href={hrefFor(key)}>{label}</Link>)}
            </nav>
          </article>
        ))}
        <article className="service-cell">
          <span className="service-cell-eyebrow">Tools</span>
          <button type="button" className="service-cell-head as-button" onClick={openTerminal}>
            <Hair name="terminal" size={24} />
            <b>Terminal</b>
          </button>
          <p>Run commands against this environment, or test an endpoint with <code>http get /health</code>.</p>
          <nav className="service-pages" aria-label="Terminal shortcuts">
            <button type="button" onClick={openTerminal}>Open <kbd>⌘J</kbd></button>
            <button type="button" onClick={() => startTour()}>Guided tour</button>
          </nav>
        </article>
      </div>
    </section>
  );
}
