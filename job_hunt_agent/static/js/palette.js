// ⌘K palette: tabs, profiles, companies, jobs. Lazy.
import { $, S, TABS, closePop, h, tabName } from "./core.js";
import { select } from "./feed.js";
import { openJob } from "./detail.js";

const PALETTE_MAX = 40;
let app = {};
let palItems = [];
let palCur = 0;
let wired = false;

function paletteEntries() {
  const tabs = TABS.map((t, i) => ({ kind: "Tab", label: tabName(t), hint: String(i + 1), run: () => app.switchTab(t) }));
  const people = S.profiles.filter((p) => p.id !== S.profile)
    .map((p) => ({ kind: "Profile", label: `Switch to ${p.name}`, run: () => app.setProfile(p.id) }));
  const seen = new Set();
  const companies = S.items.flatMap((j, i) => (seen.has(j.company) ? [] : (seen.add(j.company),
    [{ kind: "Company", label: `${j.company} · ${j.title}`, run: () => pickJob(i) }])));
  return [...tabs, ...people, ...companies];
}

function pickJob(i) {
  select(i, { open: false });
  openJob(S.items[i].uid, { sheet: true });
}

function paintPalette() {
  const words = $("#pal-q").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  palItems = paletteEntries().filter((x) => words.every((w) => x.label.toLowerCase().includes(w))).slice(0, PALETTE_MAX);
  palCur = Math.min(palCur, Math.max(0, palItems.length - 1));
  $("#pal-list").replaceChildren(...(palItems.length ? palItems.map((x, i) => h("li", { role: "option", id: `pal-${i}`,
    "aria-selected": String(i === palCur), title: `${x.kind}: ${x.label}${x.hint ? ` (${x.hint})` : ""}`, onclick: () => runPalette(i) },
  h("span", "pal-kind", x.kind), h("span", "pal-label", x.label), x.hint ? h("kbd", {}, x.hint) : null))
    : [h("li", "pal-empty", "Nothing matches")]));
  $("#pal-q").setAttribute("aria-activedescendant", palItems.length ? `pal-${palCur}` : "");
  $(`#pal-${palCur}`)?.scrollIntoView({ block: "nearest" });
}

function runPalette(i) {
  const item = palItems[i];
  $("#palette").close();
  item?.run();
}

function wirePalette() {
  const input = $("#pal-q");
  input.addEventListener("input", () => { palCur = 0; paintPalette(); });
  input.addEventListener("keydown", (e) => {
    const moves = { ArrowDown: 1, ArrowUp: -1 };
    if (moves[e.key]) {
      e.preventDefault();
      palCur = (palCur + moves[e.key] + palItems.length) % Math.max(1, palItems.length);
      paintPalette();
    } else if (e.key === "Enter") { e.preventDefault(); runPalette(palCur); }
  });
  $("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") $("#palette").close(); });
  wired = true;
}

export function openPalette(given) {
  app = given;
  if (!wired) wirePalette();
  const dlg = $("#palette");
  if (dlg.open) return;
  closePop();
  $("#pal-q").value = "";
  palCur = 0;
  paintPalette();
  dlg.showModal();
  $("#pal-q").focus();
}
