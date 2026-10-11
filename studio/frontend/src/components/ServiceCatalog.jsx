import { Link } from "@inertiajs/react";
import Hair from "./Hair";
import { CATEGORIES, SERVICES } from "../lib/services";

const LABEL = Object.fromEntries(CATEGORIES.map((category) => [category.id, category.label]));

/** The home of an environment: every service on one hairline grid. */
export default function ServiceCatalog({ hrefFor }) {
  return (
    <section className="catalog" aria-label="Services">
      <header className="catalog-head">
        <h2>Services</h2>
        <p>Independent parts that share one account, one set of access rules and one stream of events.</p>
      </header>
      <div className="catalog-grid">
        {SERVICES.map((service) => (
          <article key={service.key} className="service-cell">
            <span className="service-cell-eyebrow">{LABEL[service.category]}</span>
            <Link href={hrefFor(service.pages[0][0])} className="service-cell-head">
              <Hair name={service.icon} size={26} />
              <b>{service.label}</b>
            </Link>
            <p>{service.description}</p>
            <nav className="service-pages" aria-label={`${service.label} pages`}>
              {service.pages.map(([key, label]) => <Link key={key} href={hrefFor(key)}>{label}</Link>)}
            </nav>
          </article>
        ))}
      </div>
    </section>
  );
}
