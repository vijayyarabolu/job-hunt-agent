// Match ring: one arc in lists, four quarters in the detail.
import { MATCH_TIP, h, sv, tier } from "./core.js";

const QUAD_GAP_DEG = 7;

function ringSvg(size, r, pct, cls = "") {
  const c = size / 2;
  const s = sv("svg", { viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" });
  s.append(sv("circle", { cx: c, cy: c, r, class: "r-track" }),
    sv("circle", { cx: c, cy: c, r, class: `r-fill ${cls}`, pathLength: 100, "stroke-dasharray": `${pct ?? 0} 100`,
      transform: `rotate(-90 ${c} ${c})` }));
  return s;
}

export const tailoredLine = (job) => (job.tailored ? `${job.match_base}% → ${job.match}% with your tailored resume` : "");
// GOAL33 §3d: a job with no JD text says "No JD" in words, not a bare "?"; one being scored says so
const RING_WORDS = { nojd: ["No JD", "No JD text to match against: paste the JD in the Resume app or open the posting", "No JD, no match"],
  scoring: ["…", "Scoring this JD against your resume: it shows on the next look", "Match being scored"] };
export const miniRing = (pct, size = "", tip = "", state = null) => (RING_WORDS[state]
  ? h("span", { class: `ring no-jd ${size}`, title: RING_WORDS[state][1], role: "img", "aria-label": RING_WORDS[state][2] },
    ringSvg(36, 15.5, null), h("b", { "aria-hidden": "true" }, RING_WORDS[state][0]))
  : h("span", { class: `ring ${size} t-${tier(pct)}`, title: tip ? `${tip}. ${MATCH_TIP}` : MATCH_TIP,
    role: "img", "aria-label": pct == null ? "Match not scored yet" : `Match ${pct}%${tip ? ` (${tip})` : ""}` },
  ringSvg(36, 15.5, pct), h("b", { "aria-hidden": "true" }, pct ?? "?")));

function arcPath(r, a0, a1) {
  const pt = (a) => [60 + r * Math.cos((a * Math.PI) / 180), 60 + r * Math.sin((a * Math.PI) / 180)]
    .map((n) => n.toFixed(2)).join(" ");
  return `M${pt(a0)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${pt(a1)}`;
}
const quad = (i) => [-90 + i * 90 + QUAD_GAP_DEG / 2, i * 90 - QUAD_GAP_DEG / 2];

export function bigRing(job) {
  const s = ringSvg(120, 41, job.match, `t-${tier(job.match)}`);
  (job.parts || []).slice(0, 4).forEach((p, i) => {
    const [a0, a1] = quad(i);
    s.append(sv("path", { d: arcPath(53, a0, a1), class: `q-track${p.pct === null ? " q-na" : ""}` }));
    if (p.pct) s.append(sv("path", { d: arcPath(53, a0, a0 + ((a1 - a0) * p.pct) / 100), class: `q-fill t-${tier(p.pct)}` }));
  });
  const words = (job.parts || []).map((p) => `${p.label} ${p.pct ?? "n/a"}`).join(", ");
  return h("div", { class: "big-ring", role: "img", "aria-label": `Match ${job.match ?? "not scored"}%. ${words}.`, title: MATCH_TIP },
    s, h("div", { class: "big-num", "aria-hidden": "true" }, h("b", {}, job.match ?? "?"), h("span", "pct", "%"),
      h("small", {}, "match")));
}

export function quadGlyph(i, pct) {
  const s = sv("svg", { viewBox: "0 0 120 120", class: "qg", "aria-hidden": "true" });
  s.append(sv("circle", { cx: 60, cy: 60, r: 52, class: "qg-o" }),
    sv("path", { d: `M60 60L${arcPath(52, -90 + i * 90, i * 90).slice(1)}Z`, class: `qg-w t-${tier(pct)}` }));
  return s;
}
