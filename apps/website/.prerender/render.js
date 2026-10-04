import { jsx, jsxs, Fragment } from "react/jsx-runtime";
import { renderToString } from "react-dom/server";
import { createRootRoute, Outlet, createRoute, createRouter, createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { useState, useRef, useEffect } from "react";
const paths = {
  database: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("ellipse", { cx: "12", cy: "5", rx: "8", ry: "3" }),
    /* @__PURE__ */ jsx("path", { d: "M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0" })
  ] }),
  shield: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z" }),
    /* @__PURE__ */ jsx("path", { d: "m8 12 3 3 5-6" })
  ] }),
  folder: /* @__PURE__ */ jsx("path", { d: "M3 7V4h7l3 3h8v13H3z" }),
  radio: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("circle", { cx: "12", cy: "12", r: "2" }),
    /* @__PURE__ */ jsx("path", { d: "M7 7a7 7 0 0 0 0 10M17 7a7 7 0 0 1 0 10M4 4a11 11 0 0 0 0 16M20 4a11 11 0 0 1 0 16" })
  ] }),
  flow: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("rect", { x: "2", y: "3", width: "6", height: "6", rx: "1" }),
    /* @__PURE__ */ jsx("rect", { x: "16", y: "15", width: "6", height: "6", rx: "1" }),
    /* @__PURE__ */ jsx("path", { d: "M8 6h7a4 4 0 0 1 4 4v5M5 9v9h11" })
  ] }),
  stack: /* @__PURE__ */ jsx(Fragment, { children: /* @__PURE__ */ jsx("path", { d: "m3 7 9-4 9 4-9 4zM3 12l9 4 9-4M3 17l9 4 9-4" }) }),
  code: /* @__PURE__ */ jsx("path", { d: "m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" }),
  activity: /* @__PURE__ */ jsx("path", { d: "M2 12h5l3-8 4 16 3-8h5" }),
  arrow: /* @__PURE__ */ jsx("path", { d: "M4 12h16m-6-6 6 6-6 6" }),
  globe: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("circle", { cx: "12", cy: "12", r: "9" }),
    /* @__PURE__ */ jsx("ellipse", { cx: "12", cy: "12", rx: "4", ry: "9" }),
    /* @__PURE__ */ jsx("path", { d: "M3 12h18" })
  ] })
};
function Icon({
  name,
  className = ""
}) {
  return /* @__PURE__ */ jsx(
    "svg",
    {
      className: `icon ${className}`,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "1.5",
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
      children: paths[name] || paths.code
    }
  );
}
function Brand() {
  return /* @__PURE__ */ jsxs("a", { className: "brand", href: "/", "aria-label": "Pawabase home", children: [
    /* @__PURE__ */ jsx("img", { src: "/favicon.svg", width: "36", height: "36", alt: "" }),
    "pawabase"
  ] });
}
const github = "https://github.com/sillohq/pawabase";
const docs = (page = "quickstart") => `${github}/blob/main/apps/docs/${page}.mdx`;
const products = [
  {
    name: "Database",
    detail: "Your data, with an API.",
    path: "data/resources",
    tone: "lavender",
    icon: "database"
  },
  {
    name: "Authentication",
    detail: "Identity meets access control.",
    path: "auth/overview",
    tone: "peach",
    icon: "shield"
  },
  {
    name: "Storage",
    detail: "Files with signed access.",
    path: "storage/overview",
    tone: "butter",
    icon: "folder"
  },
  {
    name: "Realtime",
    detail: "Broadcast. Presence. History.",
    path: "realtime/overview",
    tone: "mint",
    icon: "radio"
  },
  {
    name: "Flows",
    detail: "Backend logic you can follow.",
    path: "flows/overview",
    tone: "lavender",
    icon: "flow"
  },
  {
    name: "Jobs & queues",
    detail: "Work beyond the request.",
    path: "jobs/queues",
    tone: "sky",
    icon: "stack"
  }
];
function Navigation() {
  const [open, setOpen] = useState(false);
  const [mobile, setMobile] = useState(false);
  const nav = useRef(null);
  const productButton = useRef(null);
  const mobileButton = useRef(null);
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const saved = localStorage.getItem("pawabase.website.theme");
    const next = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
  }, []);
  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    localStorage.setItem("pawabase.website.theme", next ? "dark" : "light");
  };
  useEffect(() => {
    const close = (e) => {
      var _a, _b;
      if (e.key === "Escape") {
        if (open) (_a = productButton.current) == null ? void 0 : _a.focus();
        else if (mobile) (_b = mobileButton.current) == null ? void 0 : _b.focus();
        setOpen(false);
        setMobile(false);
      }
    };
    const outside = (e) => {
      var _a;
      if (!((_a = nav.current) == null ? void 0 : _a.contains(e.target))) {
        setOpen(false);
        setMobile(false);
      }
    };
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", outside);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open, mobile]);
  return /* @__PURE__ */ jsx("header", { className: "site-header", children: /* @__PURE__ */ jsxs("nav", { ref: nav, className: "navigation", "aria-label": "Main navigation", children: [
    /* @__PURE__ */ jsx(Brand, {}),
    /* @__PURE__ */ jsxs(
      "button",
      {
        ref: mobileButton,
        className: "menu-toggle",
        "aria-expanded": mobile,
        "aria-controls": "nav-links",
        onClick: () => setMobile(!mobile),
        children: [
          mobile ? "Close" : "Menu",
          " ",
          /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: mobile ? "×" : "☰" })
        ]
      }
    ),
    /* @__PURE__ */ jsxs(
      "div",
      {
        id: "nav-links",
        className: `nav-links ${mobile ? "mobile-open" : ""}`,
        children: [
          /* @__PURE__ */ jsxs(
            "button",
            {
              ref: productButton,
              className: "nav-product",
              "aria-expanded": open,
              "aria-controls": "product-menu",
              onClick: () => setOpen(!open),
              children: [
                "Product ",
                /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: "⌄" })
              ]
            }
          ),
          /* @__PURE__ */ jsx("a", { href: docs("clients/overview"), onClick: () => setMobile(false), children: "Developers" }),
          /* @__PURE__ */ jsx("a", { href: github, onClick: () => setMobile(false), children: "Open source" }),
          /* @__PURE__ */ jsxs("a", { href: docs(), children: [
            "Docs ",
            /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: "↗" })
          ] }),
          /* @__PURE__ */ jsxs("a", { className: "nav-github", href: github, children: [
            "GitHub ",
            /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: "↗" })
          ] }),
          /* @__PURE__ */ jsx("button", { className: "theme-toggle", onClick: toggleTheme, "aria-label": `Switch to ${dark ? "light" : "dark"} mode`, title: `Switch to ${dark ? "light" : "dark"} mode`, children: /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: dark ? "☀" : "◐" }) }),
          /* @__PURE__ */ jsxs("a", { className: "button small", href: docs("installation/docker"), children: [
            "Run Pawabase ",
            /* @__PURE__ */ jsx("span", { "aria-hidden": "true", children: "↗" })
          ] })
        ]
      }
    ),
    open && /* @__PURE__ */ jsxs("div", { className: "mega-menu", id: "product-menu", children: [
      /* @__PURE__ */ jsxs("div", { className: "mega-intro", children: [
        /* @__PURE__ */ jsx("p", { className: "eyebrow", children: "THE CONNECTED BACKEND" }),
        /* @__PURE__ */ jsxs("h2", { children: [
          "One platform.",
          /* @__PURE__ */ jsx("br", {}),
          "More ways to build."
        ] }),
        /* @__PURE__ */ jsx("p", { children: "Explore the building blocks in the documentation." }),
        /* @__PURE__ */ jsx(
          "a",
          {
            href: docs(),
            onClick: () => {
              setOpen(false);
              setMobile(false);
            },
            children: "See the platform ↓"
          }
        )
      ] }),
      /* @__PURE__ */ jsx("div", { className: "mega-products", children: products.map((p) => /* @__PURE__ */ jsxs("a", { href: docs(p.path), children: [
        /* @__PURE__ */ jsx("span", { className: `feature-icon ${p.tone}`, children: /* @__PURE__ */ jsx(Icon, { name: p.icon }) }),
        /* @__PURE__ */ jsxs("span", { children: [
          /* @__PURE__ */ jsx("b", { children: p.name }),
          /* @__PURE__ */ jsx("small", { children: p.detail })
        ] })
      ] }, p.name)) })
    ] })
  ] }) });
}
function StudioFilm() {
  const video = useRef(null);
  const [motion, setMotion] = useState(false);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setMotion(!preference.matches);
    sync();
    preference.addEventListener("change", sync);
    return () => preference.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    var _a, _b;
    if (motion) (_a = video.current) == null ? void 0 : _a.play().catch(() => {
    });
    else (_b = video.current) == null ? void 0 : _b.pause();
  }, [motion]);
  return /* @__PURE__ */ jsx("div", { className: "studio-film", children: /* @__PURE__ */ jsx("video", { ref: video, src: motion ? "/media/overview.mp4" : void 0, poster: "/media/overview.webp", width: "1440", height: "900", muted: true, loop: true, playsInline: true, preload: "metadata", "aria-label": "Pawabase Studio showing application traffic, latency, and Flow activity" }) });
}
function FeaturePanel({ index }) {
  if (index === 0) return /* @__PURE__ */ jsxs("div", { className: "studio-panel table-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "panel-bar", children: [
      /* @__PURE__ */ jsx("span", { children: "orders" }),
      /* @__PURE__ */ jsx("b", { children: "12 rows" })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "mini-grid", children: [
      /* @__PURE__ */ jsx("span", { children: "id" }),
      /* @__PURE__ */ jsx("span", { children: "status" }),
      /* @__PURE__ */ jsx("span", { children: "total" }),
      /* @__PURE__ */ jsx("span", { children: "8294…" }),
      /* @__PURE__ */ jsxs("span", { children: [
        /* @__PURE__ */ jsx("i", {}),
        " paid"
      ] }),
      /* @__PURE__ */ jsx("span", { children: "$84.00" }),
      /* @__PURE__ */ jsx("span", { children: "8295…" }),
      /* @__PURE__ */ jsxs("span", { children: [
        /* @__PURE__ */ jsx("i", {}),
        " paid"
      ] }),
      /* @__PURE__ */ jsx("span", { children: "$130.00" }),
      /* @__PURE__ */ jsx("span", { children: "8296…" }),
      /* @__PURE__ */ jsxs("span", { children: [
        /* @__PURE__ */ jsx("i", { className: "pending" }),
        " pending"
      ] }),
      /* @__PURE__ */ jsx("span", { children: "$32.00" })
    ] })
  ] });
  if (index === 1) return /* @__PURE__ */ jsxs("div", { className: "studio-panel auth-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "panel-bar", children: [
      /* @__PURE__ */ jsx("span", { children: "sign in" }),
      /* @__PURE__ */ jsx("b", { children: "auth" })
    ] }),
    /* @__PURE__ */ jsxs("label", { children: [
      "Email ",
      /* @__PURE__ */ jsx("em", { children: "you@example.com" })
    ] }),
    /* @__PURE__ */ jsxs("label", { children: [
      "Password ",
      /* @__PURE__ */ jsx("em", { children: "••••••••••••" })
    ] }),
    /* @__PURE__ */ jsx("button", { children: "Continue" })
  ] });
  if (index === 2) return /* @__PURE__ */ jsxs("div", { className: "studio-panel storage-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "panel-bar", children: [
      /* @__PURE__ */ jsx("span", { children: "assets" }),
      /* @__PURE__ */ jsx("b", { children: "private" })
    ] }),
    /* @__PURE__ */ jsx("div", { className: "dropzone", children: "Drop files to upload" }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " product-brief.pdf ",
      /* @__PURE__ */ jsx("b", { children: "2.4 MB" })
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " storefront.png ",
      /* @__PURE__ */ jsx("b", { children: "844 KB" })
    ] })
  ] });
  if (index === 3) return /* @__PURE__ */ jsxs("div", { className: "studio-panel realtime-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "panel-bar", children: [
      /* @__PURE__ */ jsx("span", { children: "presence: workspace" }),
      /* @__PURE__ */ jsxs("b", { children: [
        /* @__PURE__ */ jsx("i", {}),
        " live"
      ] })
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " Maya joined"
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " Project updated"
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " 3 members online"
    ] })
  ] });
  if (index === 4) return /* @__PURE__ */ jsxs("div", { className: "studio-panel flow-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "flow-canvas", children: [
      /* @__PURE__ */ jsx("span", { children: "Request" }),
      /* @__PURE__ */ jsx("i", {}),
      /* @__PURE__ */ jsx("span", { children: "Check policy" }),
      /* @__PURE__ */ jsx("i", {}),
      /* @__PURE__ */ jsx("span", { children: "Queue work" })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "run-line", children: [
      /* @__PURE__ */ jsx("i", {}),
      " Run #1248 ",
      /* @__PURE__ */ jsx("b", { children: "Complete" })
    ] })
  ] });
  return /* @__PURE__ */ jsxs("div", { className: "studio-panel jobs-panel", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsxs("div", { className: "panel-bar", children: [
      /* @__PURE__ */ jsx("span", { children: "scheduled jobs" }),
      /* @__PURE__ */ jsx("b", { children: "3 active" })
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " Send daily digest ",
      /* @__PURE__ */ jsx("b", { children: "09:00" })
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " Clean sessions ",
      /* @__PURE__ */ jsx("b", { children: "Hourly" })
    ] }),
    /* @__PURE__ */ jsxs("p", { children: [
      /* @__PURE__ */ jsx("i", {}),
      " Sync invoices ",
      /* @__PURE__ */ jsx("b", { children: "Every 15m" })
    ] })
  ] });
}
function Home() {
  return /* @__PURE__ */ jsxs("main", { id: "main", className: "isolate", children: [
    /* @__PURE__ */ jsx(Navigation, {}),
    /* @__PURE__ */ jsxs("section", { className: "hero", "aria-labelledby": "hero-title", children: [
      /* @__PURE__ */ jsx("div", { className: "hero-current", "aria-hidden": "true", children: /* @__PURE__ */ jsxs("svg", { viewBox: "0 0 1440 900", preserveAspectRatio: "none", children: [
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-one", d: "M-90 700C210 455 330 755 605 492S1062 198 1510 -34" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-two", d: "M-130 818C165 565 378 830 650 566S1124 250 1560 38" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-three", d: "M-80 564C200 330 366 591 618 362S1018 118 1498 -88" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-four", d: "M-140 876C150 630 380 900 672 626S1100 318 1570 90" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-five", d: "M-115 506C168 264 394 534 652 292S1077 65 1555 -136" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-six", d: "M-130 445C174 210 404 465 675 228S1091 4 1552 -162" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-seven", d: "M-85 760C226 512 364 804 635 536S1099 226 1510 22" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-eight", d: "M-100 640C192 402 346 672 622 425S1044 152 1518 -58" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-nine", d: "M-106 934C196 675 392 940 692 688S1115 382 1546 162" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-ten", d: "M-100 385C174 155 419 402 685 174S1097 -34 1545 -190" }),
        /* @__PURE__ */ jsx("path", { className: "current-line current-line-eleven", d: "M-128 992C150 742 412 1010 717 742S1132 440 1560 210" })
      ] }) }),
      /* @__PURE__ */ jsxs("div", { className: "hero-shell", children: [
        /* @__PURE__ */ jsxs("div", { className: "hero-copy max-w-[420px]", children: [
          /* @__PURE__ */ jsxs("h1", { id: "hero-title", children: [
            "The backend,",
            /* @__PURE__ */ jsx("br", {}),
            "already ",
            /* @__PURE__ */ jsx("em", { children: "connected." })
          ] }),
          /* @__PURE__ */ jsx("p", { className: "hero-lede", children: "Data, auth, APIs, flows and realtime. One workspace. More time for your product." }),
          /* @__PURE__ */ jsx("div", { className: "hero-actions", children: /* @__PURE__ */ jsxs("a", { className: "button", href: docs("installation/docker"), children: [
            "Start building",
            " ",
            /* @__PURE__ */ jsx(Icon, { name: "arrow" })
          ] }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "hero-product", children: /* @__PURE__ */ jsx(StudioFilm, {}) })
      ] })
    ] }),
    /* @__PURE__ */ jsx("section", { className: "platform-section", "aria-labelledby": "platform-title", children: /* @__PURE__ */ jsxs("div", { className: "platform-frame", children: [
      /* @__PURE__ */ jsxs("div", { className: "platform-intro", children: [
        /* @__PURE__ */ jsxs("div", { children: [
          /* @__PURE__ */ jsx("h2", { id: "platform-title", children: "One platform for your entire backend." }),
          /* @__PURE__ */ jsx("p", { children: "Build, automate, and scale without touching backend code." })
        ] }),
        /* @__PURE__ */ jsxs("a", { className: "button platform-button", href: docs("quickstart"), children: [
          "Get started ",
          /* @__PURE__ */ jsx(Icon, { name: "arrow" })
        ] })
      ] }),
      /* @__PURE__ */ jsx("div", { className: "platform-bento", children: products.map((product, index) => /* @__PURE__ */ jsxs("article", { className: `platform-card platform-card-${index + 1}`, children: [
        /* @__PURE__ */ jsx(FeaturePanel, { index }),
        /* @__PURE__ */ jsxs("div", { children: [
          /* @__PURE__ */ jsx("h3", { children: product.name }),
          /* @__PURE__ */ jsx("p", { children: product.detail })
        ] })
      ] }, product.name)) })
    ] }) })
  ] });
}
function makeRouter(server = false) {
  const root = createRootRoute({
    component: () => /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("a", { className: "skip", href: "#main", children: "Skip to content" }),
      /* @__PURE__ */ jsx(Outlet, {})
    ] }),
    notFoundComponent: () => /* @__PURE__ */ jsxs("main", { id: "main", className: "section", children: [
      /* @__PURE__ */ jsx("h1", { children: "Page not found." }),
      /* @__PURE__ */ jsx("a", { href: "/", children: "Back to Pawabase →" })
    ] })
  });
  const home = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: Home
  });
  return createRouter({
    routeTree: root.addChildren([home]),
    ...server ? { history: createMemoryHistory({ initialEntries: ["/"] }) } : {}
  });
}
async function render() {
  const router = makeRouter(true);
  await router.load();
  return renderToString(/* @__PURE__ */ jsx(RouterProvider, { router }));
}
export {
  render
};
