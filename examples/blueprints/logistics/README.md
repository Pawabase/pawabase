# SwiftLine Mobility & Logistics

SwiftLine is a production-shaped Pawabase blueprint for a multi-tenant ride,
courier and business logistics operation. It models the operational system as
one connected backend: driver onboarding and document expiry feed availability;
availability feeds matching; matching feeds rides and deliveries; jobs feed
payments, earnings, webhooks, notifications, support and analytics.

The generated artifact is [`swiftline.blueprint.json`](swiftline.blueprint.json).
Generate it with:

```bash
python3 build.py
```

Then import it in Studio through **Projects → New project → From blueprint**.
The import creates fresh inbound-hook secrets. Configure the payment and
verification providers with those secrets before enabling external traffic;
outbound webhooks are intentionally imported disabled until their destinations
are configured.

## What is included

- 56 resources covering markets, zones, services, pricing, customers, drivers,
  fleets, businesses, rides, deliveries, multi-stop jobs, matching offers and
  claims, payments, earnings, payouts, support, incidents, audit timelines,
  live locations, webhooks and analytics.
- 36 flows for estimation, booking, delivery processing, matching races, expiring offers,
  reassignment, driver state/location, trip lifecycle, delivery proof and
  in-job chat.
- 26 custom routes, 30 policies, 30 input schemas, realtime channels,
  scheduled workers, inbound hooks, retryable outbound webhooks and private
  storage buckets for operational documents and proof.
- Lagos-ready configuration is available through the `sample_data()` builder;
  the portable blueprint intentionally imports without sample rows so retries
  cannot collide on fixed unique slugs.

All money uses integer minor units and all driver availability is represented
by lifecycle states rather than a boolean. Concurrent driver accepts are
arbitrated by claim rows, while timeline events and resource events preserve an
explainable operational history.

The Python files are definition builders only. Runtime business logic lives in
Pawabase resources, policies, flows, events, queues, storage, realtime and
scheduled operations after import.
