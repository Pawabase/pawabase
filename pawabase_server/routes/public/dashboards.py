"""Serving a published dashboard: a plain HTML page whose own client-side code reads and writes through the
normal ``/rest/v1`` API, under the environment's sealed-in publishable key — so every resource's existing
policies, not anything here, decide what a viewer can see or change. See ``pawabase_server.dashboards.engine``
for what a widget binding looks like, and ``pawabase_server.routes.platform.dashboards`` for publishing one.
"""

from __future__ import annotations

import json
from html import escape

from sillo import HttpContext, SilloApp, html
from sillo.exceptions import HTTPException

from pawabase_server.core.crypto import SecretBox
from pawabase_server.db.models import Dashboard, Environment
from pawabase_server.settings import Settings

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="pd-head"><h1>{title}</h1><nav id="pd-nav" class="pd-nav"></nav></header>
<main id="pd-main" class="pd-main"></main>
<script>window.__DASHBOARD__ = {config};</script>
<script>{js}</script>
</body>
</html>"""

CSS = """
:root { color-scheme: light dark; }
body { margin: 0; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f6f5; color: #1c1a20; }
@media (prefers-color-scheme: dark) { body { background: #15141a; color: #e9e7ee; } }
.pd-head { display: flex; align-items: center; justify-content: space-between; padding: 16px 28px; border-bottom: 1px solid rgba(0,0,0,.08); }
.pd-head h1 { font-size: 16px; margin: 0; }
.pd-nav { display: flex; gap: 6px; }
.pd-nav-item { border: 1px solid rgba(0,0,0,.14); background: transparent; color: inherit; padding: 6px 12px; border-radius: 8px; cursor: pointer; font: inherit; }
.pd-main { padding: 20px 28px 60px; display: flex; flex-wrap: wrap; gap: 16px; }
.pd-widget { border: 1px solid rgba(0,0,0,.1); border-radius: 10px; padding: 16px; background: rgba(255,255,255,.6); flex: 1 1 100%; min-width: 0; box-sizing: border-box; }
@media (prefers-color-scheme: dark) { .pd-widget { background: rgba(255,255,255,.04); border-color: rgba(255,255,255,.1); } }
.pd-w-half { flex-basis: calc(50% - 8px); }
.pd-w-third { flex-basis: calc(33.333% - 11px); }
.pd-widget-title { font-size: 13px; font-weight: 600; margin: 0 0 10px; opacity: .75; }
.pd-metric-value { font-size: 32px; font-weight: 700; letter-spacing: -0.02em; }
.pd-metric-label { font-size: 11px; opacity: .6; text-transform: uppercase; letter-spacing: .04em; }
.pd-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.pd-table th, .pd-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid rgba(0,0,0,.08); }
.pd-empty, .pd-error { opacity: .6; font-size: 12.5px; margin: 8px 0 0; }
.pd-error { color: #c0392b; }
.pd-chart-svg { width: 100%; height: 160px; }
.pd-bar { fill: #6f62dd; }
.pd-line { fill: none; stroke: #6f62dd; stroke-width: 2; }
.pd-dot { fill: #6f62dd; }
.pd-kanban { display: flex; gap: 10px; overflow-x: auto; }
.pd-kanban-col { flex: 0 0 220px; background: rgba(0,0,0,.03); border-radius: 8px; padding: 8px; min-height: 60px; }
.pd-kanban-col-head { font-size: 11px; font-weight: 600; opacity: .7; margin-bottom: 8px; text-transform: uppercase; }
.pd-kanban-card { background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 6px; padding: 8px; margin-bottom: 6px; cursor: grab; font-size: 12.5px; }
@media (prefers-color-scheme: dark) { .pd-kanban-card { background: #1f1e26; } }
.pd-form { display: flex; flex-direction: column; gap: 10px; max-width: 420px; }
.pd-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
.pd-field input { padding: 8px; border-radius: 6px; border: 1px solid rgba(0,0,0,.15); font: inherit; background: transparent; color: inherit; }
.pd-form button { align-self: flex-start; padding: 8px 16px; border-radius: 6px; border: 1px solid rgba(0,0,0,.15); background: #6f62dd; color: #fff; cursor: pointer; font: inherit; }
.pd-form-status.ok { color: #258053; } .pd-form-status.error { color: #c0392b; }
"""

JS = r"""
(function () {
  var CFG = window.__DASHBOARD__;
  var API = CFG.gatewayUrl.replace(/\/$/, "");

  function qs(obj) {
    return Object.keys(obj).filter(function (k) { return obj[k] !== null && obj[k] !== undefined && obj[k] !== ""; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(obj[k]); }).join("&");
  }
  function filterParams(filters) {
    var params = {};
    (filters || []).forEach(function (f) { params["filter[" + f.field + "]"] = (f.op || "eq") + "." + f.value; });
    return params;
  }
  function restList(resource, opts) {
    opts = opts || {};
    var params = filterParams(opts.filters);
    if (opts.select && opts.select.length) params.select = opts.select.join(",");
    if (opts.sort) params.sort = opts.sort;
    params.per_page = opts.limit || 100;
    return fetch(API + "/rest/v1/" + resource + "?" + qs(params), { headers: { apikey: CFG.apiKey } })
      .then(function (res) { if (!res.ok) throw new Error(resource + ": " + res.status); return res.json(); });
  }
  function restWrite(method, resource, id, body) {
    var url = API + "/rest/v1/" + resource + (id ? "/" + id : "");
    return fetch(url, { method: method, headers: { apikey: CFG.apiKey, "content-type": "application/json" }, body: JSON.stringify(body) })
      .then(function (res) { if (!res.ok) return res.text().then(function (t) { throw new Error(t || (res.status + "")); }); return res.json(); });
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "class") node.className = v;
      else if (k.indexOf("on") === 0) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
    (children || []).forEach(function (c) { node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }

  function aggregate(kind, values) {
    if (kind === "count") return values.length;
    if (kind === "sum") return values.reduce(function (a, c) { return a + c; }, 0);
    if (kind === "avg") return values.length ? values.reduce(function (a, c) { return a + c; }, 0) / values.length : 0;
    if (kind === "min") return values.length ? Math.min.apply(null, values) : 0;
    return values.length ? Math.max.apply(null, values) : 0;
  }

  function renderMetric(widget, root) {
    var b = widget.binding;
    var value = el("div", { class: "pd-metric-value" }, ["…"]);
    root.appendChild(value);
    var p = b.aggregate === "count"
      ? restList(b.resource, { filters: b.filters, limit: 1 }).then(function (data) { return data.total != null ? data.total : data.data.length; })
      : restList(b.resource, { select: [b.field], filters: b.filters, limit: 1000 }).then(function (data) {
          var nums = data.data.map(function (r) { return Number(r[b.field]); }).filter(function (n) { return !isNaN(n); });
          return aggregate(b.aggregate, nums);
        });
    p.then(function (v) { value.textContent = Math.round(v * 100) / 100; })
     .catch(function (err) { value.textContent = "—"; value.title = err.message; });
  }

  function renderTable(widget, root) {
    var b = widget.binding;
    var table = el("table", { class: "pd-table" });
    root.appendChild(table);
    restList(b.resource, { select: b.fields, filters: b.filters, sort: b.sort, limit: b.limit || 50 }).then(function (data) {
      var fields = (b.fields && b.fields.length) ? b.fields : Object.keys(data.data[0] || {});
      table.appendChild(el("thead", {}, [el("tr", {}, fields.map(function (f) { return el("th", {}, [f]); }))]));
      var body = el("tbody");
      data.data.forEach(function (row) {
        body.appendChild(el("tr", {}, fields.map(function (f) { return el("td", {}, [String(row[f] == null ? "" : row[f])]); })));
      });
      table.appendChild(body);
      if (!data.data.length) root.appendChild(el("p", { class: "pd-empty" }, ["No rows yet."]));
    }).catch(function (err) { root.appendChild(el("p", { class: "pd-error" }, [err.message])); });
  }

  function drawChart(points, kind) {
    var w = 480, h = 160, pad = 20;
    var max = Math.max.apply(null, [1].concat(points.map(function (p) { return p[1]; })));
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("class", "pd-chart-svg");
    var n = points.length || 1;
    if (kind === "line") {
      var step = (w - pad * 2) / Math.max(1, n - 1);
      var coords = points.map(function (p, i) { return [pad + i * step, h - pad - (p[1] / max) * (h - pad * 2)]; });
      var line = document.createElementNS(svg.namespaceURI, "polyline");
      line.setAttribute("points", coords.map(function (c) { return c.join(","); }).join(" "));
      line.setAttribute("class", "pd-line");
      svg.appendChild(line);
      coords.forEach(function (c) {
        var dot = document.createElementNS(svg.namespaceURI, "circle");
        dot.setAttribute("cx", c[0]); dot.setAttribute("cy", c[1]); dot.setAttribute("r", 3); dot.setAttribute("class", "pd-dot");
        svg.appendChild(dot);
      });
    } else {
      var bw = (w - pad * 2) / n * 0.6;
      points.forEach(function (p, i) {
        var x = pad + (i + 0.2) * (w - pad * 2) / n;
        var barH = (p[1] / max) * (h - pad * 2);
        var rect = document.createElementNS(svg.namespaceURI, "rect");
        rect.setAttribute("x", x); rect.setAttribute("y", h - pad - barH);
        rect.setAttribute("width", bw); rect.setAttribute("height", barH); rect.setAttribute("class", "pd-bar");
        var title = document.createElementNS(svg.namespaceURI, "title"); title.textContent = p[0] + ": " + p[1];
        rect.appendChild(title);
        svg.appendChild(rect);
      });
    }
    return svg;
  }

  function renderChart(widget, root) {
    var b = widget.binding;
    var holder = el("div", { class: "pd-chart" });
    root.appendChild(holder);
    restList(b.resource, { select: [b.group_by, b.field].filter(Boolean), filters: b.filters, limit: 1000 }).then(function (data) {
      var groups = {};
      data.data.forEach(function (row) {
        var key = String(row[b.group_by] == null ? "—" : row[b.group_by]);
        var value = b.aggregate === "count" ? 1 : (Number(row[b.field]) || 0);
        (groups[key] = groups[key] || []).push(value);
      });
      var points = Object.keys(groups).map(function (key) { return [key, aggregate(b.aggregate, groups[key])]; });
      holder.appendChild(drawChart(points, b.chart_type || "bar"));
    }).catch(function (err) { root.appendChild(el("p", { class: "pd-error" }, [err.message])); });
  }

  function renderKanban(widget, root) {
    var b = widget.binding;
    var idField = b.id_field || "id";
    var board = el("div", { class: "pd-kanban" });
    root.appendChild(board);
    function load() {
      board.innerHTML = "";
      restList(b.resource, { filters: b.filters, limit: 500 }).then(function (data) {
        var columns = {};
        data.data.forEach(function (row) {
          var key = String(row[b.group_field] == null ? "—" : row[b.group_field]);
          (columns[key] = columns[key] || []).push(row);
        });
        Object.keys(columns).forEach(function (col) {
          var rows = columns[col];
          var colEl = el("div", {
            class: "pd-kanban-col",
            ondragover: function (e) { e.preventDefault(); },
            ondrop: function (e) {
              e.preventDefault();
              var id = e.dataTransfer.getData("text/plain");
              var patch = {}; patch[b.group_field] = col;
              restWrite("PATCH", b.resource, id, patch).then(load).catch(function (err) { alert(err.message); });
            },
          });
          colEl.appendChild(el("div", { class: "pd-kanban-col-head" }, [col + " · " + rows.length]));
          rows.forEach(function (row) {
            var card = el("div", {
              class: "pd-kanban-card", draggable: "true",
              ondragstart: function (e) { e.dataTransfer.setData("text/plain", row[idField]); },
            }, [String(b.card_title_field ? row[b.card_title_field] : row[idField])]);
            colEl.appendChild(card);
          });
          board.appendChild(colEl);
        });
      }).catch(function (err) { board.appendChild(el("p", { class: "pd-error" }, [err.message])); });
    }
    load();
    widget._reload = load;
  }

  function renderForm(widget, root) {
    var b = widget.binding;
    var form = el("form", { class: "pd-form" });
    var inputs = {};
    (b.fields || []).forEach(function (f) {
      var input = el("input", { name: f.name, placeholder: f.label || f.name, type: f.type === "number" ? "number" : "text" });
      inputs[f.name] = input;
      form.appendChild(el("label", { class: "pd-field" }, [f.label || f.name, input]));
    });
    var status = el("p", { class: "pd-form-status" });
    form.appendChild(el("button", { type: "submit" }, [b.mode === "update" ? "Save" : "Create"]));
    form.appendChild(status);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var body = {};
      Object.keys(inputs).forEach(function (name) { if (inputs[name].value !== "") body[name] = inputs[name].value; });
      var write = b.mode === "update"
        ? restWrite("PATCH", b.resource, new URLSearchParams(location.search).get(b.id_param), body)
        : restWrite("POST", b.resource, null, body).then(function (r) { form.reset(); return r; });
      write.then(function () { status.textContent = "Saved."; status.className = "pd-form-status ok"; })
        .catch(function (err) { status.textContent = err.message; status.className = "pd-form-status error"; });
    });
    root.appendChild(form);
  }

  var RENDERERS = { metric: renderMetric, table: renderTable, chart: renderChart, kanban: renderKanban, form: renderForm };
  var sources = [];

  function renderPage(page) {
    sources.forEach(function (s) { s.close(); });
    sources = [];
    var main = document.getElementById("pd-main");
    main.innerHTML = "";
    var resources = {};
    page.widgets.forEach(function (widget) {
      var card = el("section", { class: "pd-widget pd-w-" + (widget.width || "full") });
      if (widget.title) card.appendChild(el("h2", { class: "pd-widget-title" }, [widget.title]));
      main.appendChild(card);
      (RENDERERS[widget.type] || function () {})(widget, card);
      if (widget.binding && widget.binding.resource) resources[widget.binding.resource] = true;
    });
    Object.keys(resources).forEach(function (name) {
      var source = new EventSource(API + "/realtime/v1/sse?channels=resource:" + encodeURIComponent(name) + "&apikey=" + encodeURIComponent(CFG.apiKey));
      source.onmessage = function () { renderPage(page); };
      sources.push(source);
    });
  }

  function currentPage() {
    var path = location.hash.slice(1) || CFG.definition.pages[0].path;
    var match = CFG.definition.pages.filter(function (p) { return p.path === path; })[0];
    return match || CFG.definition.pages[0];
  }

  var nav = document.getElementById("pd-nav");
  if (CFG.definition.pages.length > 1) {
    CFG.definition.pages.forEach(function (page) {
      nav.appendChild(el("button", { class: "pd-nav-item", onclick: function () { location.hash = page.path; } }, [page.name]));
    });
  }
  renderPage(currentPage());
  window.addEventListener("hashchange", function () { renderPage(currentPage()); });
})();
"""


def register(app: SilloApp, settings: Settings) -> None:
    box = SecretBox(settings.master_key)

    @app.get("/dashboards/v1/{env}/{slug}", exclude_from_schema=True)
    async def published_dashboard(ctx: HttpContext, env: str, slug: str):
        environment = await Environment.filter(name=env).first()
        if environment is None:
            raise HTTPException(status_code=404, detail="no such environment")
        row = await Dashboard.get_or_none(environment=environment, name=slug)
        if row is None or not row.enabled or not row.public or not row.publishable_key_ciphertext:
            raise HTTPException(status_code=404, detail="no published dashboard at this address")
        api_key = box.open(row.publishable_key_ciphertext, env)
        config = {
            "env": env,
            "definition": row.definition,
            "apiKey": api_key,
            "gatewayUrl": settings.public_gateway_url,
        }
        title = escape(row.title or row.name)
        page = PAGE.format(
            title=title,
            css=CSS,
            js=JS,
            config=json.dumps(config).replace("</", "<\\/"),
        )
        return html(page)
