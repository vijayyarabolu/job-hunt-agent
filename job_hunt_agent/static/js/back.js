// "Did you apply?" after Apply ↗. Lazy. (Settings is its own page since settings.js)
import { btn, JOB_TABS, S, api, exactAge, fail, get, h, muted, toast } from "./core.js";
import { loadFeed } from "./feed.js";
import { loadFollowups, setFollowupCount } from "./editor.js";

const SETTLE_MS = 700; // focus + visibilitychange arrive together: ask once
const DROPPED_MS = 12000;
let busy = false;
let timer = 0;
let dlg = null;

function dialog() {
  if (!dlg) {
    dlg = h("dialog", { class: "form-dialog back-dlg", "aria-labelledby": "bk-h" });
    dlg.addEventListener("close", () => { busy = false; if (history.state?.dlg) history.back(); });
    const out = ({ target: t, offsetX: x, offsetY: y }) => t === dlg && (x < 0 || y < 0 || x > t.offsetWidth || y > t.offsetHeight);
    let down; // a drag out of a field is no click outside
    dlg.onpointerdown = (e) => { down = out(e); };
    dlg.onclick = (e) => down && out(e) && dlg.close();
    addEventListener("popstate", () => dlg.open && dlg.close());
    document.body.append(dlg);
  }
  return dlg;
}
function show(...nodes) {
  const d = dialog();
  d.replaceChildren(...nodes.flat().filter(Boolean));
  if (!d.open) { d.showModal(); history.pushState({ dlg: 1 }, ""); }
  (d.querySelector("input") || d.querySelector(".btn.primary"))?.focus();
}

export function initBack() {
  const soon = () => { clearTimeout(timer); timer = setTimeout(() => check(false), SETTLE_MS); };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) soon(); });
  addEventListener("focus", soon);
  addEventListener("pageshow", (e) => { if (e.persisted) soon(); });
  check(true);
}

export async function check(boot) {
  if (busy || document.hidden || !S.profile) return;
  busy = true; // before the request: one dialog
  const pid = S.profile;
  let d;
  try { d = await get("/api/pending", boot ? { boot: 1 } : {}); } catch { busy = false; return; }
  busy = false;
  if (pid !== S.profile) return;
  S.snoozed = d.snoozed;
  setFollowupCount(S.followups);
  if (d.dropped.length) {
    toast(`Dropped ${d.dropped.length} Apply click${d.dropped.length === 1 ? "" : "s"} older than 7 days, not marked applied: ` +
      d.dropped.map((x) => `${x.company} · ${x.role}`).join("; "), { ms: DROPPED_MS });
  }
  const rest = S.g8?.inline ? S.g8.inline(d.ask) : d.ask;
  if (rest.length && !busy) { busy = true; ask(rest, 0); }
}

function ask(list, i) {
  const e = list[i];
  if (!e) { dialog().close(); refresh(); return; }
  const reply = async (answer) => {
    btns.forEach((b) => { b.disabled = true; });
    if (answer === "yes") show(h("h2", { id: "bk-h" }, `Marking applied: ${e.company}`), muted("Attaching the resume…"));
    try {
      const r = await api("/api/pending/answer", { id: e.id, answer });
      if (answer === "yes") { applied(r, () => ask(list, i + 1)); return; }
      if (answer === "later" && e.state !== "snoozed") S.snoozed = (S.snoozed || 0) + 1;
      if (answer !== "later" && e.state === "snoozed") S.snoozed = Math.max(0, (S.snoozed || 0) - 1);
      setFollowupCount(S.followups);
      ask(list, i + 1);
    } catch (err) { fail(err); ask(list, err.status === 400 || err.status === 404 ? i + 1 : i); }
  };
  const btns = [btn("primary", "Yes, I applied: mark it applied and attach the resume", () => reply("yes"), "Yes, I applied"),
    btn("", "Not yet", () => reply("no"), "No"),
    btn("ghost", "Ask me later (next time the app opens)", () => reply("later"), "Later")];
  show(h("h2", { id: "bk-h" }, `Did you apply to ${e.company} · ${e.role}?`),
    muted(`You opened Apply ${exactAge(e.opened_at)}${list.length > 1 ? ` · ${i + 1} of ${list.length}` : ""}`, "muted small"),
    h("div", "row-btns", btns));
}

const ATTACHED = { tailored: (a) => `Tailored resume attached: ${a.name}`,
  base: (a) => `Base resume attached: ${a.name}${a.note ? ` (${a.note})` : ""}`,
  existing: () => "Resume already attached", none: (a) => `Resume not attached (${a.error}); upload it from the tracker row` };

export function applied(r, next) {
  const a = r.attached || { kind: "none", error: "unknown" };
  const inputs = r.ask.map((f) => h("input", { type: "text", name: f.field, autocomplete: "off", title: `${f.header}: still empty in your sheet row` }));
  const done = () => { next(); refresh(); };
  const save = async () => {
    const changes = Object.fromEntries(inputs.filter((x) => x.value.trim()).map((x) => [x.name, x.value.trim()]));
    if (Object.keys(changes).length) {
      try { await api("/api/tracker/update", { app: r.app, changes }); } catch (err) { fail(err); return; }
      toast(`Saved to your sheet row: ${r.ask.filter((f) => changes[f.field]).map((f) => f.header).join(", ")}`);
    }
    done();
  };
  show(h("h2", { id: "bk-h" }, `Applied: ${r.company} · ${r.role}`),
    h("p", "small", `Marked applied today. ${ATTACHED[a.kind](a)}. Your sheet row fills in within a minute.`),
    r.ask.length ? h("p", {}, "Still empty in your sheet:") : muted("Nothing else is empty in your sheet row.", "muted small"),
    r.ask.map((f, k) => h("label", "field", h("span", {}, f.header), inputs[k])),
    h("div", "row-btns", r.ask.length ? btn("ghost", "Skip: leave them empty for now", done, "Skip") : null,
      btn("primary", r.ask.length ? "Save these fields to the Sheet" : "Done", r.ask.length ? save : done, r.ask.length ? "Save" : "Done")));
}

function refresh({ quiet = true } = {}) {
  if (JOB_TABS.includes(S.tab)) loadFeed({ quiet });
  if (S.tab === "followups") loadFollowups();
}

export async function confirmOutside(row) {
  busy = true;
  show(h("h2", { id: "bk-h" }, `Marking applied: ${row.company}`), muted("Attaching the resume…"));
  try { applied(await api("/api/outside/applied", { app: row.id }), () => dialog().close()); }
  catch (err) { dialog().close(); fail(err); }
}

export function followupsTop() {
  const box = h("section", "fu-group back-later");
  box.hidden = true; // an empty box left a gap above Overdue
  get("/api/pending", { boot: 1, peek: 1 }).then((d) => {
    const later = d.ask.filter((x) => x.state === "snoozed");
    if (later.length !== (S.snoozed || 0)) { S.snoozed = later.length; setFollowupCount(S.followups); }
    if (!later.length) return;
    box.hidden = false;
    box.replaceChildren(h("h2", {}, "Did you apply? ", muted(String(later.length))),
      h("ul", "fu-list", later.map((x) => h("li", "fu-row", h("p", {}, h("b", {}, x.company), ` · ${x.role}`),
        muted(`opened ${exactAge(x.opened_at)}`, "muted small")))),
      btn("", "Answer now: did you apply?", () => { if (!busy) { busy = true; ask(later, 0); } }, "Answer now"));
  }).catch(() => {});
  return box;
}
