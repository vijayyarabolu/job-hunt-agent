// the H-1B badge (icon + word, never colour alone, for colour-blind users) and the Recommended switch.
import { B, S, h, icon, store } from "./core.js";

const ICON = { sponsors: "check", cap_exempt: "star", maybe: "qmark", no: "x" };
const VIEWS = [["friendly", "Sponsor-friendly", "Sponsors, cap-exempt and sponsored before"],
  ["no", "No sponsorship", "The post or record says no sponsorship"], ["", "All", "Every job, also unknown"]];
const n = (x) => Number(x || 0).toLocaleString();

export function evidenceLines(sp) {
  const ev = sp?.evidence || {};
  const lines = [];
  if (ev.jd) lines.push(`The post says: “${ev.jd}”`);
  if (ev.recent != null) {
    const trend = ev.trend === "up" ? "more than" : ev.trend === "down" ? "fewer than" : "as many as";
    lines.push(`${n(ev.recent)} H-1B filings in the last 12 months (${n(ev.recent_data)} data / AI), ${trend} the 12 before (${n(ev.prior)})`);
    if (ev.level) lines.push(`Typical wage level ${ev.level} for data / AI roles`);
    if (ev.perm) lines.push(`${n(ev.perm)} green-card (PERM) cases in 2 years`);
    if (ev.titles?.length) lines.push(`Filed for: ${ev.titles.slice(0, 3).join(", ")}`);
    if (ev.states?.length) lines.push(`Where: ${ev.states.join(", ")}`);
    if (ev.cap_exempt) lines.push(`Cap-exempt (${ev.cap_exempt}): no H-1B lottery`);
    if (ev.filed_as?.length) lines.push(`Filed as ${ev.filed_as.join(", ")}`);
  }
  if (ev.learned) {
    const d = ev.learned.days;
    lines.push(`${ev.learned.profile === S.profile ? "You" : "Your partner"} applied ${ev.learned.applied_on} and got a rejection ${d} day${d === 1 ? "" : "s"} later`);
  }
  if (ev.through) lines.push(`DOL filing data through ${ev.through}`);
  return lines;
}
const extra = (sp) => evidenceLines(sp).filter((l) => !sp.why.includes(l));

export function sponsorBadge(sp, { compact = false } = {}) {
  if (!sp) return null;
  const text = compact ? sp.text.replace(/ · .*| \(.*\)$/, "") : sp.text;
  return h("span", { class: `sb sb-${sp.kind}${compact ? " sb-sm" : ""}`, title: [sp.text, sp.why, ...extra(sp)].join("\n"),
    role: "note", "aria-label": `${sp.text}. ${sp.why}` }, icon(ICON[sp.kind] || "qmark", "ic sb-ic"), h("span", "sb-t", text));
}

export function sponsorBlock(sp, company) {
  if (!sp) return null;
  const f = sp.source === "filings";
  return h("section", { class: "side-block sb-block", "aria-labelledby": "sb-h" }, h("h3", { id: "sb-h" }, `Visa sponsorship at ${company}`),
    sponsorBadge(sp), f ? null : h("p", "small", sp.why), h("ul", "sb-ev small", (f ? evidenceLines(sp) : extra(sp)).map((l) => h("li", {}, l))));
}

const KEY = () => `jh.spons.${S.profile}`;
export const sponsView = () => store.get(KEY(), "") || "";
export function setSponsView(v, after) { store.set(KEY(), v); after(); }

export function sponsSwitch(counts, active, onChange) {
  if (!counts) return null;
  const by = { friendly: counts.friendly, no: counts.no, "": counts.all };
  return h("div", { class: "seg sb-seg", role: "group", "aria-label": "Visa sponsorship" }, VIEWS.map(([v, label, tip]) =>
    B({ "aria-pressed": String((active || "") === v), title: tip, "data-spons": v, onclick: () => setSponsView(v, onChange) },
      h("span", "sb-l", v === "friendly" ? icon("check", "ic sm") : v === "no" ? icon("x", "ic sm") : null, label), " ",
      h("span", "sb-n", n(by[v])))));
}
