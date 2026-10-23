// Job Hunt Agent Autofill, content script 2/n: which of a form's choices means the saved answer (pure: unit-tested in Node).
// Exact text first (the board's own wording saved from "Needs you"), then Yes/No by meaning, the same idea in other
// words (decline to answer, a state's code or name, the US, a degree level, how you heard), then close wording.
// No confident choice -> null: the field becomes "Needs you" instead of a guess.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const STATES = { AL: "alabama", AK: "alaska", AZ: "arizona", AR: "arkansas", CA: "california", CO: "colorado", CT: "connecticut",
    DE: "delaware", DC: "district of columbia", FL: "florida", GA: "georgia", HI: "hawaii", ID: "idaho", IL: "illinois", IN: "indiana",
    IA: "iowa", KS: "kansas", KY: "kentucky", LA: "louisiana", ME: "maine", MD: "maryland", MA: "massachusetts", MI: "michigan",
    MN: "minnesota", MS: "mississippi", MO: "missouri", MT: "montana", NE: "nebraska", NV: "nevada", NH: "new hampshire",
    NJ: "new jersey", NM: "new mexico", NY: "new york", NC: "north carolina", ND: "north dakota", OH: "ohio", OK: "oklahoma",
    OR: "oregon", PA: "pennsylvania", RI: "rhode island", SC: "south carolina", SD: "south dakota", TN: "tennessee", TX: "texas",
    UT: "utah", VT: "vermont", VA: "virginia", WA: "washington", WV: "west virginia", WI: "wisconsin", WY: "wyoming" };
  const GROUPS = [
    ["decline", /\b(decline|prefer not|do not wish|don t wish|dont wish|choose not|not wish to|rather not|do not want to (answer|disclose|self identify)|i don t want to answer|not to (disclose|self identify|answer)|not specified|undisclosed)\b/],
    ["us", /^(the )?(united states( of america)?|usa|us|u s a?|america)( \+1| \(?\+1\)?)?$/],
    ["company_site", /\b(company|corporate|employer|career|careers) (web ?site|site|page|portal)\b|^(web ?site|career site|careers page|company web ?site)$/],
    ["linkedin", /\blinked ?in\b/],
    ["job_board", /\b(job board|online job|job site|internet job|job posting site)\b/],
    ["master", /\bmaster|\bm ?s\b|\bmsc\b|\bm sc\b|graduate degree|\bm tech\b|\bmeng\b/],
    ["bachelor", /\bbachelor|\bb ?s\b|\bbsc\b|\bb tech\b|\bb e\b|undergraduate degree/],
    ["doctor", /\bdoctor|\bph ?d\b|doctorate/],
    ["male", /^(male|man|he him)\b/],
    ["female", /^(female|woman|she her)\b/],
    ["not_veteran", /\b(not a (protected )?veteran|i am not a (protected )?veteran|non veteran|no military service|not a veteran)\b/],
    // GOAL30 §0b: race / ethnicity families ("Asian / South Asian" is the form's "Asian (Not Hispanic or Latino)")
    ["two_or_more", /\b(two or more|multi ?racial|more than one race|mixed race)\b/],
    ["race_asian", /^(?!.*\b(two or more|caucasian)\b).*\b(asian|south asian|east asian|indian american)\b/],
    ["race_white", /^(?!.*\btwo or more\b).*\b(white|caucasian)\b/],
    ["race_black", /^(?!.*\btwo or more\b).*\b(black|african american)\b/],
    ["race_native", /^(?!.*\btwo or more\b).*\b(american indian|alaska(n)? native|native american)\b/],
    ["race_pacific", /^(?!.*\btwo or more\b).*\b(native hawaiian|pacific islander)\b/],
    ["no_disability", /\b(no,? i (do not|don t) have a disability|i do not have a disability|no disability)\b/],
    ["mobile", /^(mobile|cell|cellular|cell phone|mobile phone)\b/],
  ];
  const PLACEHOLDER = /^(select|please select|choose|select one|select an option|none selected|no selection|pick one|\-+)$/;
  const NEG = /\b(no|not|never|n t|dont|don t|cannot|can t|won t|will not|do not|am not|have not|haven t|isn t|aren t)\b/;

  const norm = (s) => String(s == null ? "" : s).toLowerCase().replace(/[’']/g, " ").replace(/&/g, " and ")
    .replace(/[^a-z0-9+]+/g, " ").trim();
  const stem = (w) => (w.length > 4 && w.endsWith("s") ? w.slice(0, -1) : w);
  const words = (s) => new Set(norm(s).split(" ").filter((w) => w.length > 1 || /\d/.test(w)).map(stem));

  // statements ("I am not authorized ...") carry a yes / no only as a choice, never as the saved answer
  function polarity(text, statements = true) {
    const n = norm(text);
    if (/^(yes|y|true|i agree|agree|i accept|accept|i consent|consent|i acknowledge|acknowledge)\b/.test(n)) return "yes";
    if (/^(no|n|false)\b/.test(n)) return "no";
    if (/^not (?!applicable|specified|listed|declared|disclosed|sure|selected)\w/.test(n)) return "no";   // "Not a protected veteran"
    if (statements && /^i\b/.test(n)) return NEG.test(n) ? "no" : "yes";
    return null;
  }

  function group(text) {
    const n = norm(text);
    const g = GROUPS.find(([, rx]) => rx.test(n));
    return g ? g[0] : null;
  }

  function stateOf(text) {
    const raw = String(text || "").trim();
    if (/^[A-Za-z]{2}$/.test(raw) && STATES[raw.toUpperCase()]) return STATES[raw.toUpperCase()];
    const n = norm(text).replace(/^(us|usa|united states) /, "");
    return Object.values(STATES).includes(n) ? n : null;
  }

  function jaccard(a, b) {
    const x = words(a), y = words(b);
    if (!x.size || !y.size) return 0;
    let both = 0;
    for (const w of x) if (y.has(w)) both++;
    return both / (x.size + y.size - both);
  }

  // A start date said as a wait ("2 weeks after offer acceptance", "Within 2 weeks", "Less than 1 month", "1-3 months",
  // "Immediately"): {lo, hi} in days, or null when the text is no wait at all.
  const NUMS = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, twelve: 12 };
  const UNIT_DAYS = { day: 1, week: 7, month: 30 };
  const N = "(\\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve)";
  const U = "(day|week|month)s?";
  const num = (w) => (/^\d+$/.test(w) ? Number(w) : NUMS[w]);
  function wait(text) {
    const s = String(text == null ? "" : text).toLowerCase().replace(/[’']/g, "'");
    if (/^\s*\d{1,4}\s*[\/.-]\s*\d{1,2}(\s*[\/.-]\s*\d{1,4})?\s*$/.test(s)) return null;   // a date, not a wait
    if (/\b(immediately|immediate|asap|as soon as possible|right away)\b/.test(s)) return { lo: 0, hi: 0 };
    let m = new RegExp(`\\b${N}\\s*(?:-|–|to)\\s*${N}\\s*${U}\\b`).exec(s);
    if (m) return { lo: num(m[1]) * UNIT_DAYS[m[3]], hi: num(m[2]) * UNIT_DAYS[m[3]] };
    m = new RegExp(`\\b(less than|within|up to|under|no more than|no later than)\\s*${N}\\s*${U}\\b`).exec(s);
    if (m) return { lo: 0, hi: num(m[2]) * UNIT_DAYS[m[3]] };
    m = new RegExp(`\\b(more than|over|at least|longer than)\\s*${N}\\s*${U}\\b`).exec(s);
    if (m) return { lo: num(m[2]) * UNIT_DAYS[m[3]], hi: Infinity };
    m = new RegExp(`\\b${N}\\s*${U}\\b`).exec(s);
    if (m) { const d = num(m[1]) * UNIT_DAYS[m[2]]; return { lo: d, hi: d }; }
    return null;
  }

  // the choice that fits a wait: the same wait ("2 weeks"), else the narrowest range holding it ("Within 2 weeks"
  // before "Less than 1 month"); -1: none fits
  function waitChoice(texts, answer) {
    const want = wait(answer);
    if (!want || want.hi === Infinity) return -1;
    const days = want.hi;
    const ranges = texts.map((t, i) => ({ i, w: wait(t) })).filter((x) => x.w);
    const same = ranges.find((x) => x.w.lo === days && x.w.hi === days);
    if (same) return same.i;
    const holding = ranges.filter((x) => x.w.lo <= days && days <= x.w.hi).sort((a, b) => (a.w.hi - a.w.lo) - (b.w.hi - b.w.lo));
    return holding.length ? holding[0].i : -1;
  }

  // a wait as the date it ends on, from today (at fill time): {mm, dd, yyyy} or null
  function waitDate(answer, now = new Date()) {
    const w = wait(answer);
    if (!w || w.hi === Infinity) return null;
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + w.hi);
    return { mm: String(d.getMonth() + 1).padStart(2, "0"), dd: String(d.getDate()).padStart(2, "0"), yyyy: String(d.getFullYear()) };
  }

  // options: list of strings; answers: the board's exact text first, then the saved answer.
  function best(options, answers) {
    const opts = options.map((t, i) => ({ i, t: String(t), n: norm(t) })).filter((o) => o.n && !PLACEHOLDER.test(o.n));
    const wanted = answers.filter((a) => a != null && String(a).trim() !== "").map(String);
    if (!opts.length || !wanted.length) return null;
    const found = (o) => (o ? { index: o.i, text: o.t } : null);
    for (const a of wanted) {
      const exact = opts.find((o) => o.t.trim().toLowerCase() === a.trim().toLowerCase()) || opts.find((o) => o.n === norm(a));
      if (exact) return found(exact);
    }
    const answer = wanted[wanted.length - 1];
    // 1.3.1: "I don't wish to answer" means the form's own decline choice ("Decline to self-identify")
    if (group(answer) === "decline") {
      const declines = opts.filter((o) => group(o.t) === "decline");
      if (declines.length) return found(declines.sort((a, b) => a.n.length - b.n.length)[0]);
    }
    // a statement ("I am not a protected veteran") answers a plain Yes / No question by its meaning
    const plain = opts.filter((o) => /^(yes|no)$/.test(o.n));
    const pol = polarity(answer, false) || (plain.length === 2 && group(answer) !== "decline" ? polarity(answer, true) : null);
    if (pol) {
      // "I don't wish to answer" is never a "no"; several "Yes, I am …" choices are his to pick (Needs you, once)
      const same = opts.filter((o) => polarity(o.t) === pol && group(o.t) !== "decline");
      if (same.length === 1) return found(same[0]);
      // GOAL30 §0b: several "no" statements ("I identify as a veteran, just not a protected veteran" / "I am not a
      // veteran"): a plain No is the one that denies the whole thing, never one that says "I identify as"
      const flat = same.filter((o) => !/\bidentify as (a|an|one)\b/.test(o.n));
      if (same.length > 1 && pol === "no" && flat.length === 1) return found(flat[0]);
      if (same.length > 1) return found(same.find((o) => o.n === pol) || null);
      if (opts.some((o) => polarity(o.t))) return null;
    }
    if (wait(answer)) {                                  // "2 weeks after offer acceptance" -> "Within 2 weeks"
      const i = waitChoice(opts.map((o) => o.t), answer);
      if (i >= 0) return found(opts[i]);
      if (opts.some((o) => wait(o.t))) return null;
    }
    const st = stateOf(answer);
    if (st) {
      const hit = opts.find((o) => stateOf(o.t) === st || o.n === st || o.n.startsWith(st + " "));
      if (hit) return found(hit);
    }
    const g = group(answer);
    if (g) {
      const hits = opts.filter((o) => group(o.t) === g);
      if (hits.length) return found(hits.sort((a, b) => a.n.length - b.n.length)[0]);
    }
    const an = norm(answer);
    if (an.length >= 2) {
      const starts = opts.filter((o) => o.n.startsWith(an + " ") || an.startsWith(o.n + " "));
      if (starts.length) return found(starts.sort((a, b) => Math.abs(a.n.length - an.length) - Math.abs(b.n.length - an.length))[0]);
      const inside = opts.filter((o) => new RegExp(`\\b${an.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(o.n));
      if (inside.length === 1) return found(inside[0]);
    }
    const scored = opts.map((o) => ({ o, s: jaccard(o.t, answer) })).sort((a, b) => b.s - a.s);
    if (scored[0].s >= 0.5 && (scored.length === 1 || scored[0].s > scored[1].s)) return found(scored[0].o);
    return null;
  }

  // A checkbox's answer: is it a yes?
  const truthy = (answer) => polarity(answer, false) === "yes" || /^(checked|true|on|1|x)$/i.test(String(answer || "").trim());

  // skills: the list entry that is this skill (case / punctuation do not matter): the same words, else
  // its abbreviation in parentheses ("AWS" -> "Amazon Web Services (AWS)"), else the shortest entry holding every
  // word of it as a whole word ("S3" -> "Amazon S3"; never "Java" -> "JavaScript"). -1: none.
  const skillNorm = (s) => String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9+#]+/g, " ").trim();
  const bare = (s) => skillNorm(String(s).replace(/\([^)]*\)/g, " "));
  const abbr = (s) => { const m = /\(([^)]+)\)\s*$/.exec(String(s)); return m ? skillNorm(m[1]) : ""; };
  function skill(options, want) {
    const w = skillNorm(want), wb = bare(want), wa = abbr(want);
    if (!w) return -1;
    const opts = options.map((t, i) => ({ i, t: String(t), n: skillNorm(t) })).filter((o) => o.n && !/^no (items|results|matches)/.test(o.n));
    const hit = (f) => { const o = opts.find(f); return o ? o.i : -1; };
    for (const f of [(o) => o.n === w, (o) => bare(o.t) === wb || (wa && (o.n === wa || abbr(o.t) === wa)), (o) => abbr(o.t) === w]) {
      const i = hit(f);
      if (i >= 0) return i;
    }
    const words = wb.split(" ").filter(Boolean);
    const holding = opts.filter((o) => { const ow = new Set(o.n.split(" ")); return words.every((x) => ow.has(x)); })
      .sort((a, b) => a.t.length - b.t.length);
    return holding.length ? holding[0].i : -1;
  }
  const creates = (text, want) => /^(create|add|use)\b/i.test(String(text).trim()) && skillNorm(text).includes(skillNorm(want));

  JH.pick = { best, polarity, group, stateOf, norm, truthy, STATES, skill, creates, skillNorm, wait, waitChoice, waitDate };
  if (typeof module !== "undefined") module.exports = JH.pick;
})();
