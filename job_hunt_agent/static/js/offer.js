// GOAL29 §2, lazy from detail.js: the Offer check. Eight areas, each a grade A to F with an icon and a word (never
// colour alone, readable with red-green colour blindness) and one line of evidence; Claude's three (growth, team, mission) are
// asked for after the panel opens, only for jobs at Match 70% or more. It never changes Match % or Priority.
import { api, h, icon } from "./core.js";

const ICON = { A: "star", B: "check", C: "more", D: "down", F: "x" };
const asked = new Set();

function area(a) {
  return h("li", "of-a", h("span", { class: `og og-${a.grade || "u"}`, "aria-label": `${a.area}: ${a.grade || "no grade"}, ${a.word}` },
    icon(ICON[a.grade] || "qmark", "ic sm"), a.grade ? h("b", {}, a.grade) : null, a.word),
  h("span", "of-n", a.area), h("span", "of-e muted small", a.evidence || ""));
}

function draw(slot, offer, busy) {
  slot.replaceChildren(h("section", { class: "side-block offer", "aria-labelledby": "of-h" },
    h("h3", { id: "of-h" }, "Offer check"), h("p", "of-line", offer.line),
    h("ul", "of-list", offer.areas.map(area)),
    busy ? h("p", "muted small", "Checking growth, team and mission with Claude…") : null,
    h("p", "muted small", "Does not change Match % or Priority.")));
}

export function mount(box, job, d) {
  const offer = d?.offer;
  if (!offer) return;
  const run = offer.llm === "ready" && !asked.has(job.uid);
  const slot = h("div");
  box.append(h("link", { rel: "stylesheet", href: "/offer.css" }), slot);
  draw(slot, offer, run);
  if (!run) return;
  asked.add(job.uid);                                    // once per page load: the server keeps it by the JD
  api("/api/offer", { uid: job.uid })
    .then((r) => { if (r.offer) { d.offer = r.offer; if (slot.isConnected) draw(slot, r.offer, false); } })
    .catch(() => { if (slot.isConnected) draw(slot, offer, false); });
}
