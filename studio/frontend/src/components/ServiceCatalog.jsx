import { Link } from "@inertiajs/react";
import { Icon } from "./icons";
import { CATEGORIES, SERVICES } from "../lib/services";

/** The home of an environment: every service as a card, grouped the way a cloud console does. */
export default function ServiceCatalog({ hrefFor }) {
  return (
    <section className="catalog" aria-label="Services">
      <div className="catalog-head">
        <h2>Services</h2>
        <p>Everything in this environment, as independent parts that share one account, one set of access rules and one event stream.</p>
      </div>
      {CATEGORIES.map((category) => (
        <div key={category.id} className="catalog-category">
          <div className="catalog-category-head"><b>{category.label}</b><span>{category.blurb}</span></div>
          <div className="catalog-grid">
            {SERVICES.filter((service) => service.category === category.id).map((service) => (
              <div key={service.key} className="service-card">
                <Link href={hrefFor(service.pages[0][0])} className="service-card-head">
                  <span className="service-icon"><Icon name={service.icon} size={20} /></span>
                  <b>{service.label}</b>
                </Link>
                <p>{service.description}</p>
                <div className="service-pages">
                  {service.pages.map(([key, label]) => <Link key={key} href={hrefFor(key)}>{label}</Link>)}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
