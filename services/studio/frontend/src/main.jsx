import { createInertiaApp } from "@inertiajs/react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles.css";

const pages = import.meta.glob("./pages/**/*.jsx", { eager: true });

createInertiaApp({
  title: (title) => (title ? `${title} · Pawabase Studio` : "Pawabase Studio"),
  resolve: (name) => {
    const page = pages[`./pages/${name}.jsx`];
    if (!page) throw new Error(`Studio has no page ${name}`);
    return page;
  },
  setup({ el, App, props }) {
    createRoot(el).render(<App {...props} />);
  },
  progress: { color: "#22c55e" },
});
