// Settings > Setup and Settings > Profiles. Setup lists every account and key with its state (connected, missing,
// failing), a Test button, why it is a sign-in or a key, what it costs and what works without it. Everything typed
// here is saved on this computer (<home>/config), never in the repo; keys are never sent back to the page.
import { B, S, api, extLink, h, toast } from "./core.js";

const STATE = { connected: "Connected", missing: "Missing", failing: "Failing", demo: "Demo", optional: "Optional" };
const pill = (state) => h("span", { class: `st-pill st-${state}` }, STATE[state] || state);
const note = (text, cls = "hint") => h("p", cls, text);

async function post(path, body, done) {
  try {
    const out = await api(path, body);
    if (done) await done(out);
    return out;
  } catch (err) { toast(err.message, { error: true }); return null; }
}

function testBtn(item, repaint) {
  return B({ class: "btn sm", title: "Check it works now (spends no credits)", onclick: async (e) => {
    e.currentTarget.disabled = true;
    const out = await post("/api/setup/test", { item: item.id });
    if (out) toast(`${out.ok ? "Works" : "Failed"}: ${out.detail}`, { error: !out.ok });
    repaint();
  } }, "Test");
}

function statusPanel(items, repaint) {
  return h("section", "set-card st-panel", h("h3", {}, "Status"),
    h("table", "st-table", h("tbody", {}, items.map((it) => h("tr", {},
      h("th", { scope: "row" }, it.name), h("td", {}, pill(it.state)),
      h("td", "st-detail", it.last_test && !it.last_test.ok ? `${it.detail}. Last test: ${it.last_test.detail}` : it.detail),
      h("td", {}, it.state === "missing" ? null : testBtn(it, repaint)))))));
}

function about(it) {
  return [note(it.why, "st-why"), h("p", "hint", h("b", {}, "Without it: "), it.without),
    it.link ? extLink(it.link, "Where to get it ↗", "lnk", it.link) : null];
}

function keyForm(it, name, label, repaint) {
  const input = h("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: it.detail === "No key" ? "Paste the key" : it.detail,
    "aria-label": label });
  const save = (value) => post("/api/setup/key", { name, value }, () => { toast(value ? `${label} saved` : `${label} removed`); repaint(); });
  return h("div", "set-in", input,
    B({ class: "btn primary sm", onclick: () => (input.value.trim() ? save(input.value.trim()) : toast("Paste the key first", { error: true })) }, "Save"),
    it.state !== "missing" ? B({ class: "btn ghost sm", onclick: () => save("") }, "Remove") : null);
}

function googleClientCard(it, repaint) {
  const id = h("input", { type: "text", autocomplete: "off", placeholder: "123456-abc.apps.googleusercontent.com", "aria-label": "Client ID" });
  const secret = h("input", { type: "password", autocomplete: "off", placeholder: "Client secret", "aria-label": "Client secret" });
  const json = h("textarea", { rows: 3, placeholder: "…or paste the whole downloaded JSON file here", "aria-label": "Client JSON" });
  const save = () => post("/api/setup/google_client", json.value.trim() ? { json: json.value } : { client_id: id.value, client_secret: secret.value },
    () => { toast("Google OAuth client saved"); repaint(); });
  return h("section", "set-card", h("h3", {}, it.name, " ", pill(it.state)), ...about(it),
    h("ol", "set-list st-steps", it.steps.map((s) => h("li", {}, s.text, s.link ? [" ", extLink(s.link, "Open ↗", "lnk", s.link)] : null))),
    h("label", "field set-field", h("span", {}, "Client ID"), h("span", "set-in", id)),
    h("label", "field set-field", h("span", {}, "Client secret"), h("span", "set-in", secret)), json,
    B({ class: "btn primary", onclick: save }, it.state === "missing" ? "Save client" : "Replace client"));
}

function googleCard(it, repaint) {
  const connect = B({ class: "btn primary", disabled: !it.can_connect, title: it.can_connect ? "Opens Google's consent screen" : "Save the OAuth client first",
    onclick: () => post("/api/setup/google_connect", {}, (out) => { location.href = out.url; }) }, it.state === "connected" ? "Reconnect Google" : "Connect Google");
  const off = it.state === "connected" || it.state === "failing"
    ? B({ class: "btn ghost", onclick: () => post("/api/setup/google_disconnect", {}, () => { toast("Disconnected; the token was revoked"); repaint(); }) }, "Disconnect")
    : null;
  return h("section", "set-card", h("h3", {}, it.name, " ", pill(it.state)), note(it.detail, "st-detail"), ...about(it),
    h("div", "row-btns", connect, off),
    it.redirect_uri ? note(`Google sends you back to ${it.redirect_uri} (this app's own port). A Desktop OAuth client accepts it as is.`) : null,
    h("p", "hint", "Each profile can also use its own Google account and its own tracker sheet: Settings › Accounts."));
}

function keyCard(it, name, label, repaint) {
  return h("section", "set-card", h("h3", {}, it.name, " ", pill(it.state)), ...about(it), keyForm(it, name, label, repaint),
    it.model ? note(`Model: ${it.model} (settings.yaml anthropic_model changes it).`) : null);
}

function tailorCard(it, repaint) {
  const url = h("input", { type: "url", placeholder: "https://resume-tailor.example.com (empty: built in)", value: it.url || "", "aria-label": "Service URL" });
  return h("section", "set-card", h("h3", {}, it.name, " ", pill(it.state)), note(it.detail, "st-detail"), ...about(it),
    h("label", "field set-field", h("span", {}, "Service URL (optional)"), h("span", "set-in", url,
      B({ class: "btn sm", onclick: () => post("/api/setup/resume_service", { url: url.value }, () => { toast(url.value ? "Service saved" : "Using the built-in tailor"); repaint(); }) }, "Save"))),
    h("label", "field set-field", h("span", {}, "Service token (optional)"), keyForm({ ...it, detail: it.token || "No key", state: it.token ? "connected" : "missing" },
      "resume_tailor_token", "Service token", repaint)));
}

function telegramCard(it, repaint) {
  return h("section", "set-card", h("h3", {}, it.name, " ", pill(it.state)), ...about(it),
    h("label", "field set-field", h("span", {}, "Bot token"), keyForm(it, "telegram_token", "Bot token", repaint)),
    h("label", "field set-field", h("span", {}, "Chat id"), keyForm({ ...it, detail: "Chat id" }, "telegram_chat", "Chat id", repaint)));
}

export async function setupPart(repaint) {
  let s;
  try { s = await api("/api/setup" + (S.profile ? `?profile=${encodeURIComponent(S.profile)}` : "")); } catch (err) { return [note(`Setup could not load: ${err.message}`, "bad")]; }
  const by = Object.fromEntries(s.items.map((it) => [it.id, it]));
  return [
    s.demo ? note("Demo mode: fixture jobs and fake services. Nothing here is saved or sent. Run `job-hunt-agent serve` for the real app.", "st-demo") : null,
    note(`Saved on this computer in ${s.home}/config (keys in secrets.yaml, readable only by you). Nothing goes into the repo, and keys are never shown again after saving.`),
    statusPanel(s.items, repaint),
    googleClientCard(by.google_client, repaint), googleCard(by.google, repaint),
    keyCard(by.anthropic, "anthropic_api_key", "Anthropic key", repaint),
    keyCard(by.apollo, "apollo_api_key", "Apollo key", repaint), keyCard(by.hunter, "hunter_api_key", "Hunter key", repaint),
    tailorCard(by.resume_tailor, repaint), telegramCard(by.telegram, repaint)];
}

function uploadBtn(pid, repaint) {
  const file = h("input", { type: "file", accept: ".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document", "aria-label": "Resume (.docx)" });
  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    const out = await api(`/api/setup/resume?profile=${encodeURIComponent(pid)}&name=${encodeURIComponent(f.name)}`, null, f).catch((err) => { toast(err.message, { error: true }); return null; });
    if (out) { toast(`Read ${out.jobs} jobs and ${out.skills} skills from ${out.file}` + (out.warnings?.length ? ` (check: ${out.warnings.join(", ")})` : "")); repaint(); }
  });
  return h("label", "btn sm", "Upload resume (.docx)", file);
}

export async function profilesPart(repaint) {
  let s;
  try { s = await api("/api/setup"); } catch (err) { return [note(`Profiles could not load: ${err.message}`, "bad")]; }
  const people = s.profiles.map((p) => h("li", "st-person", h("b", {}, p.name), h("span", "muted", ` (${p.id})`),
    h("span", p.has_baseline ? "chip-note ok" : "chip-note warn", p.has_baseline ? " resume loaded" : " no resume yet"), " ", uploadBtn(p.id, repaint)));
  const field = (name, label, attrs = {}) => h("label", "field set-field", h("span", {}, label), h("span", "set-in", h("input", { name, type: "text", autocomplete: "off", ...attrs })));
  const fams = h("div", "set-check", s.families.map((f) => h("label", "check", h("input", { type: "checkbox", name: "families", value: f, checked: f === "ml_engineer" }), " ", f.replace(/_/g, " "))));
  const form = h("form", { class: "set-group", onsubmit: async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    data.families = [...e.currentTarget.querySelectorAll("input[name=families]:checked")].map((x) => x.value);
    const out = await post("/api/setup/profile", data);
    if (out) { toast(`Added ${out.name}. Upload a resume next.`); setTimeout(() => { location.hash = "settings/profiles"; location.reload(); }, 900); }
  } }, field("name", "Name", { required: true, placeholder: "Your name" }), field("years", "Years of experience", { inputmode: "numeric", placeholder: "3" }),
  field("location", "City", { placeholder: "Austin, TX" }), field("email", "Email (goes on the tailored resume)"), field("phone", "Phone"),
  field("linkedin", "LinkedIn"), field("github", "GitHub"), h("p", "hint", "Roles to look for:"), fams,
  h("button", { type: "submit", class: "btn primary" }, "Add profile"));
  return [note("One person or several (a partner, a friend). Each profile has its own resume, feed, tracker and settings."),
    s.profiles.length ? h("section", "set-card", h("h3", {}, "Profiles"), h("ul", "set-list", people),
      note("A resume is a Word .docx with headings Summary, Skills and Work Experience. It is used for matching and for tailoring; it never leaves this computer except in Claude calls you start.")) : null,
    h("section", "set-card", h("h3", {}, s.profiles.length ? "Add another profile" : "Add your profile to start"), form),
    s.profiles.length ? null : note("Or try the app first with sample data: `job-hunt-agent demo`.")];
}

