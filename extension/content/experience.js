// Job Hunt Agent Autofill, content script: Work history, Education and Summary on ANY form (user 2026-10-14). A section is
// found by its heading ("Work Experience", "Employment History", "Education", ...) and its "Add" button; each job of
// the resume being uploaded gets its own entry, in the resume's order: Title, Company, Location, Start / End month
// and year (or "I currently work here"), and the job's bullets as the description, one "• bullet" per line. Entries
// the site already made (it parsed the resume) are corrected, never duplicated. Education: School, Degree, Field of
// study, graduation date (a start date only when the form requires one). A Summary / About you box gets the resume's
// summary word for word. What worked on a site (its Add button, which label means what) is remembered for next time.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const LAYOUTS = "jh.layouts";
  const ADD_WAIT_MS = 3000;
  const MAX_ENTRIES = 12;
  const MAX_HEADING = 60;
  const HEADINGS = "h1, h2, h3, h4, h5, h6, legend, [role=heading], [class*=heading i], [class*=title i], [class*=header i], strong, b, p, span, div, label";
  const KINDS = {
    exp: { heading: /^(\+\s*)?((work|professional|employment|relevant|previous|past) )?(experience|employment|employers?|positions?|work history|employment history|job history)( history| details| information)?\s*[:*]?$/i,
      add: /\b(position|experience|job|employ|employer|work|company)\b/i },
    edu: { heading: /^(\+\s*)?(education(al)?|schools?|academic|degrees?)( history| background| details| information)?\s*[:*]?$/i,
      add: /\b(education|school|degree|university|college)\b/i },
  };
  const ADD = /^\s*(\+\s*)?add\b/i;
  const ROLES = {
    exp: [["current", /current(ly)?|present|still (work|employed)|i work here/i], ["desc", /description|responsibilit|duties|achievements|accomplishments|what (did|do) you do|summary of (your )?(role|work)|^summary$/i],
      ["company", /company|employer|organi[sz]ation|business/i], ["title", /title|position|role|designation/i], ["location", /location|city|where/i],
      ["startMonth", /(start|from|begin)\w*.*month|month.*(start|from)/i], ["startYear", /(start|from|begin)\w*.*year|year.*(start|from)/i],
      ["endMonth", /(end|to|until|finish)\w*.*month|month.*(end|to\b)/i], ["endYear", /(end|to|until|finish)\w*.*year|year.*(end|to\b)/i],
      ["start", /start|from\b|begin/i], ["end", /\bend\b|\bto\b|until|finish/i]],
    edu: [["school", /school|university|college|institution/i], ["degree", /degree|qualification|diploma/i], ["field", /field|major|discipline|area of study|concentration|subject/i],
      ["startMonth", /(start|from|begin)\w*.*month/i], ["startYear", /(start|from|begin)\w*.*year/i],
      ["endMonth", /(end|to|graduat|complet)\w*.*month/i], ["endYear", /(end|to|graduat|complet)\w*.*year|^year$/i],
      ["start", /start|from\b|begin/i], ["end", /\bend\b|\bto\b|graduat|complet|finish/i]],
  };
  const SUMMARY = /^(professional |experience |career |profile |personal |executive )?summary\b|about (you|yourself|me)\b|tell us about yourself|^profile$/i;
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const before = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  // ---- finding the sections -------------------------------------------------------------------------------------
  function headings(kind, root) {
    return JH.all(HEADINGS, root).filter((h) => {
      if (h.closest("[data-jh-panel]") || h.querySelector("input, select, textarea")) return false;
      const t = JH.text(h);
      if (!t || t.length > MAX_HEADING || !KINDS[kind].heading.test(t)) return false;
      return ![...h.children].some((c) => JH.text(c) === t && c.matches(HEADINGS));   // the innermost element saying it
    });
  }

  function sections(root, saved) {
    // every Experience / Education section: its heading, and the part of the page it covers (to the next heading)
    const found = [];
    for (const kind of Object.keys(KINDS)) {
      const hs = headings(kind, root);
      const learned = saved && saved[kind] && saved[kind].heading;
      const own = learned ? hs.find((h) => norm(JH.text(h)) === learned) : null;
      for (const h of own ? [own] : hs.slice(0, 1)) found.push({ kind, heading: h });
    }
    const all = found.map((s) => s.heading).sort((a, b) => (before(a, b) ? -1 : 1));
    for (const s of found) {                             // it ends at the next section, or the next heading like its own (an h3 after an h3)
      const other = all.find((h) => h !== s.heading && before(s.heading, h)) || null;
      const tag = s.heading.tagName;
      const same = /^(H[1-6]|LEGEND)$/.test(tag) ? JH.all(tag.toLowerCase(), root).find((h) => before(s.heading, h) && !h.closest("[data-jh-panel]")) || null : null;
      s.next = [other, same].filter(Boolean).sort((a, b) => (before(a, b) ? -1 : 1))[0] || null;
    }
    return found;
  }

  const inSection = (s, el) => before(s.heading, el) && (!s.next || before(el, s.next));

  function addButton(s, root, saved) {
    const learned = saved && saved[s.kind] && saved[s.kind].add;
    const buttons = JH.all("button, [role=button], a", root).filter((b) => !b.closest("[data-jh-panel]") && JH.text(b).length <= 40 && ADD.test(JH.text(b)));
    const mine = buttons.filter((b) => inSection(s, b));
    return (learned && buttons.find((b) => norm(JH.text(b)) === learned)) || mine.find((b) => KINDS[s.kind].add.test(JH.text(b)))
      || buttons.find((b) => KINDS[s.kind].add.test(JH.text(b)) && !KINDS[s.kind === "exp" ? "edu" : "exp"].add.test(JH.text(b))) || mine[0] || null;
  }

  function roleOf(kind, f, saved) {
    const learned = saved && saved[kind] && saved[kind].roles && saved[kind].roles[norm(f.label)];
    if (learned) return learned;
    const hints = (f.hints || []).join(" ");
    for (const [role, re] of ROLES[kind]) {                // its label first ("Summary" alone), then its id / name
      const said = re.test(f.label) || re.test(hints);
      if (role === "current" ? f.kind === "checkbox" && said : f.kind !== "checkbox" && f.kind !== "radio" && said) return role;
    }
    return null;
  }

  function entries(s, root, saved) {
    // the section's fields split into entries: a role seen again starts the next entry
    const out = [];
    let cur = null;
    for (const f of JH.fields.collect(root).filter((x) => inSection(s, x.el) && x.kind !== "file")) {
      const role = roleOf(s.kind, f, saved);
      if (!role) continue;
      if (!cur || cur.has(role)) { cur = new Map(); out.push(cur); }
      cur.set(role, f);
    }
    return out;
  }

  // ---- filling one entry ----------------------------------------------------------------------------------------
  function parts(mmYyyy) {
    const [mm, yyyy] = String(mmYyyy || "").split("/");
    const m = Number(mm);
    return m >= 1 && m <= 12 && /^\d{4}$/.test(yyyy || "") ? { mm: String(m).padStart(2, "0"), m, yyyy } : null;
  }

  async function put(f, value, option) {
    if (!f || value == null || value === "") return false;
    if (f.kind === "date") {                             // <input type=month> / <input type=date>
      const p = parts(value);
      if (!p) return false;
      JH.setValue(f.el, f.el.type === "month" ? `${p.yyyy}-${p.mm}` : `${p.yyyy}-${p.mm}-01`);
      JH.mark(f.el);
      return true;
    }
    const got = await JH.fill.field(f, { value, option: option || "" }, { overwrite: true });
    return got.status === "filled";
  }

  async function putDate(e, which, mmYyyy, out) {
    const p = parts(mmYyyy);
    if (!p) return;
    const month = e.get(`${which}Month`), year = e.get(`${which}Year`), whole = e.get(which);
    if (month || year) {
      if (month && (await put(month, month.kind === "select" || month.kind === "combo" || month.kind === "listbox" ? String(p.m) : p.mm, MONTHS[p.m - 1]))) out.filled++;
      if (year && (await put(year, p.yyyy))) out.filled++;
      return;
    }
    if (!whole) return;
    const shape = `${whole.el.placeholder || ""} ${whole.label}`;
    const text = /yyyy-mm/i.test(shape) ? `${p.yyyy}-${p.mm}` : /^\s*yyyy\s*$/i.test(whole.el.placeholder || "") ? p.yyyy : `${p.mm}/${p.yyyy}`;
    if (await put(whole, whole.kind === "date" ? mmYyyy : text)) out.filled++;
  }

  async function fillJob(e, job, out) {
    for (const [role, value] of [["title", job.title], ["company", job.company], ["location", job.location]]) {
      if (await put(e.get(role), value)) out.filled++;
    }
    const cur = e.get("current");
    if (cur && cur.el.checked !== Boolean(job.current) && !JH.byHand(cur.el)) { JH.click(cur.el); JH.mark(cur.el); out.filled++; await JH.settle(150, 1200); }
    await putDate(e, "start", job.start, out);
    if (!job.current) await putDate(e, "end", job.end, out);
    const desc = e.get("desc");
    if (desc && job.description && !JH.byHand(desc.el)) { JH.setValue(desc.el, job.description); JH.mark(desc.el); out.filled++; }
  }

  async function fillSchool(e, school, out) {
    for (const [role, value] of [["school", school.school], ["degree", school.degree], ["field", school.field]]) {
      if (await put(e.get(role), value)) out.filled++;
    }
    const startRequired = ["start", "startMonth", "startYear"].some((r) => e.get(r) && e.get(r).required);
    if (startRequired) await putDate(e, "start", school.start, out);       // the user's rule: no start date unless it is required
    await putDate(e, "end", school.end, out);
  }

  // ---- a section: enough entries (Add), each filled, the layout remembered ----------------------------------------
  async function fillSection(s, root, items, saved, out) {
    let list = entries(s, root, saved);
    const add = addButton(s, root, saved);
    for (let guard = 0; list.length < Math.min(items.length, MAX_ENTRIES) && add && guard < MAX_ENTRIES; guard++) {
      const had = list.length;
      JH.click(add);
      if (!(await JH.waitFor(() => entries(s, root, saved).length > had, ADD_WAIT_MS))) break;
      await JH.settle(200, 1500);
      list = entries(s, root, saved);
    }
    for (let i = 0; i < Math.min(list.length, items.length); i++) {
      if (s.kind === "exp") await fillJob(list[i], items[i], out); else await fillSchool(list[i], items[i], out);
    }
    const roles = {};
    for (const e of list) for (const [role, f] of e) { roles[norm(f.label)] = role; out.handled.add(f.el); }
    return { heading: norm(JH.text(s.heading)), add: add ? norm(JH.text(add)) : null, roles };
  }

  async function layoutsFor(host) {
    const got = await JH.store([LAYOUTS]);
    return { all: got[LAYOUTS] || {}, mine: (got[LAYOUTS] || {})[host] || null };
  }

  JH.experience = {
    // the page has a section or a summary box to fill (so the resume's data is worth asking Job Hunt Agent for)
    wanted(root) {
      return Object.keys(KINDS).some((k) => headings(k, root).length) || JH.fields.collect(root).some((f) => /^(textarea|text)$/.test(f.kind) && SUMMARY.test(f.label));
    },
    // fills what it finds; returns { filled, handled: Set of elements (not questions for Job Hunt Agent), notes }
    async fill(root, data) {
      const out = { filled: 0, handled: new Set(), notes: [] };
      if (!data) return out;
      const host = location.host;
      const { all, mine } = await layoutsFor(host);
      const learned = {};
      for (const s of sections(root, mine)) {
        const items = s.kind === "exp" ? data.jobs || [] : data.schools || [];
        if (!items.length) continue;
        learned[s.kind] = await fillSection(s, root, items, mine, out);
      }
      const summary = String(data.summary || "").trim();
      if (summary) {
        for (const f of JH.fields.collect(root)) {
          if (out.handled.has(f.el) || !/^(textarea|text)$/.test(f.kind) || !SUMMARY.test(f.label) || JH.byHand(f.el)) continue;
          if (f.el.value.trim() !== summary) { JH.setValue(f.el, summary); out.filled++; }
          JH.mark(f.el);
          out.handled.add(f.el);
        }
      }
      if (Object.keys(learned).length) {
        try { await chrome.storage.local.set({ [LAYOUTS]: { ...all, [host]: { ...(mine || {}), ...learned, at: Date.now() } } }); } catch { /* storage full: learned next time */ }
      }
      return out;
    },
  };
})();
