// Stats: stats_html.py; bars scale from data-s (CSP).
import { $, S, get, h, phone } from "./core.js";

const TILES = 5;
const skeleton = () => h("div", { class: "stats", "aria-busy": "true" },
  h("header", "t-head", h("h1", {}, "Stats"), h("p", "muted small", "What gets callbacks")),
  h("div", "tiles", Array.from({ length: TILES }, () => h("div", { class: "tile", "aria-hidden": "true" }, h("span", "sk-line w40"), h("span", "sk-line w70")))));

export async function loadStats() {
  const view = $("#view");
  if (!view.querySelector(".stats")) view.replaceChildren(skeleton());   // the page's shape, not a bare "Loading…"
  try {
    const s = await get("/api/tracker/stats", { phone: phone.matches ? "1" : "" });
    if (S.tab !== "stats") return;
    view.innerHTML = s.html;
    for (const bar of view.querySelectorAll("[data-s]")) bar.style.transform = `scale${bar.dataset.ax}(${bar.dataset.s})`;
  } catch (err) { view.replaceChildren(h("p", "pad", `Could not load: ${err.message}`)); }
}
