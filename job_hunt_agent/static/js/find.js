// GOAL35 §5.3: textsearch.py's twin (same rules; aliases and dates are the server's) for on-page boxes and highlights.
// No imports: tests/test_goal35_search.py runs it under node.

const TYPO_MIN = 5, TYPO_TWO = 9, SHORT = 2;     // SHORT: matches whole words (and joined pairs), not as a prefix

export function norm(text) {
  return String(text ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/&/g, " and ")
    .match(/[a-z0-9]+/g)?.join(" ") || "";
}
export const words = (q) => [...new Set(norm(q).split(" ").filter(Boolean))];

// optimal string alignment distance, stopped once it passes `limit`
export function edits(a, b, limit) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let prev2 = null, prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1]));
      if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
    }
    if (Math.min(...cur) > limit) return limit + 1;
    [prev2, prev] = [prev, cur];
  }
  return prev[b.length];
}
const allowed = (w) => (w.length >= TYPO_TWO ? 2 : w.length >= TYPO_MIN ? 1 : 0);

// the record's tokens: its words and each word joined to the next one or two ("ai ml" is also "aiml")
function tokens(text) {
  const ws = norm(text).split(" ").filter(Boolean);
  const out = ws.flatMap((w, k) => [w, w + (ws[k + 1] || ""), w + (ws[k + 1] || "") + (ws[k + 2] || "")]);
  out.lead = new Map(ws.flatMap((w, k) => [[w + (ws[k + 1] || ""), w], [w + (ws[k + 1] || "") + (ws[k + 2] || ""), w]]));
  return out;
}

// the record tokens a query word stands for: those it starts, else those it is a typo of
function hits(word, toks) {
  const found = toks.filter((t) => t.startsWith(word) && (word.length > SHORT || t === word || toks.lead.get(t) === word));
  const limit = allowed(word);
  if (found.length || !limit) return found;
  return toks.filter((t) => Math.abs(t.length - word.length) <= limit && edits(word, t, limit) <= limit);
}

// every query word matches somewhere in the texts: the matched tokens (to highlight), or null
export function match(q, ...texts) {
  const ws = Array.isArray(q) ? q : words(q);
  const toks = tokens(texts.join(" "));
  const out = [];
  for (const w of ws) {
    const got = hits(w, toks);
    if (!got.length) return null;
    out.push(...got);
  }
  return out;
}

// the text with each word a search term starts in <mark> (terms: what the search matched, normalized)
export function marked(text, terms) {
  const s = String(text ?? "");
  const want = (terms || []).filter(Boolean);
  if (!s || !want.length) return [s];
  const out = [];
  let at = 0;
  for (const m of s.matchAll(/[\p{L}\p{N}]+/gu)) {
    const w = norm(m[0]);
    if (!w || !want.some((t) => t === w || (t.startsWith(w) && w.length > SHORT) || (w.startsWith(t) && t.length > SHORT))) continue;
    const mark = document.createElement("mark");
    mark.textContent = m[0];
    out.push(s.slice(at, m.index), mark);
    at = m.index + m[0].length;
  }
  out.push(s.slice(at));
  return out.filter((x) => x !== "");
}

// the CSP blocks <style>; app.css is at its cap
if (typeof CSSStyleSheet !== "undefined" && typeof document !== "undefined" && document.adoptedStyleSheets) {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(`mark { background: color-mix(in oklab, var(--signal) 26%, transparent); color: inherit; border-radius: 3px; padding: 0 1px }
.t-found, .f-found { margin: 8px 0 0 } .t-in { margin: 2px 0 0 } .f-search { display: block; width: 100%; height: 34px; margin: 8px 0 0; padding: 0 12px; border: var(--line); border-radius: var(--r-pill); background: var(--raised); color: var(--ink); font: inherit }`);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}
