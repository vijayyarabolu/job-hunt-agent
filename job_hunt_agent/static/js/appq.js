// GOAL29 §5, lazy from detail.js: Application questions. Paste one, Generate: Claude writes it in resumegen's queue on
// your own login (the JD, your resume for this job, your facts). Only on a click; a written one comes back free.
import { api, copyText, fail, get, h, toast } from "./core.js";

const LOOK = { writing: "⟳", ready: "✓", limit: "⏸", login: "⚠", failed: "✕" };
const WAIT_MS = { writing: 3000, limit: 60000 };
const MAX_POLLS = 400;
const WRITING = { state: "writing", label: "Writing with Claude…" };
const byJob = new Map();                                  // uid -> [{ question, view }] for this page load

function status(view) {
  const words = view.state === "failed" ? `No answer: ${(view.error || "").replace(/^No answer that passes the checks: /, "")}` : view.label;
  return h("p", { class: `aq-st ${view.state}`, role: "status" }, h("span", { "aria-hidden": "true" }, LOOK[view.state] || "·"), words);
}
const small = (word, onclick, title) => h("button", { type: "button", class: "btn sm ghost", onclick, title }, word);

export function mount(box, job) {
  const list = byJob.get(job.uid) || [];
  byJob.set(job.uid, list);
  const out = h("div", "aq-list");
  const ask = h("textarea", { rows: 3, placeholder: "Paste a question from the application", "aria-label": "Application question",
    title: "A question from the application form, pasted as it is" });
  const limit = h("input", { type: "number", min: 1, placeholder: "Character limit (optional)", "aria-label": "Character limit, if the form has one",
    title: "The form's character limit: the answer stays under it with room to spare" });
  const redraw = () => out.replaceChildren(...list.map(entry));

  async function run(question, variant = "", previous) {
    const item = list.find((x) => x.question === question) || { question };
    if (!list.includes(item)) list.unshift(item);
    try {
      if (!variant) {                                     // the cache first: no second call for the same question
        const got = (await api("/api/autofill/answer/lookup", { uid: job.uid, questions: [question] })).answers[question];
        if (got && got.state !== "failed") { item.view = got; redraw(); return poll(item, got); }
      }
      item.view = WRITING;
      redraw();
      item.view = (await api("/api/autofill/answer/generate", { uid: job.uid, question, variant, previous,
        max_chars: Number(limit.value) || null })).answer;
    } catch (err) { item.view = { state: "failed", error: err.message }; }
    redraw();
    poll(item, item.view);
  }
  function poll(item, view, n = 0) {
    const wait = WAIT_MS[view?.state];
    if (!wait || n >= MAX_POLLS) return;
    setTimeout(async () => {
      if (item.view?.id !== view.id || !out.isConnected) return;     // a newer one started, or the job closed
      try { item.view = (await get("/api/autofill/answer/status", { id: view.id })).answer; } catch { return; }
      redraw();
      poll(item, item.view, n + 1);
    }, wait);
  }
  const save = (x, v) => api("/api/autofill/answer/save", { uid: job.uid, question: x.question, text: v.text })
    .then(() => toast("Saved to Answers")).catch(fail);
  function entry(x) {
    const v = x.view || WRITING;
    const ready = v.state === "ready";
    const again = ready || ["failed", "login"].includes(v.state);
    return h("div", "aq-item", h("p", "aq-q", x.question), status(v),
      ready ? h("p", "aq-txt", v.text) : null, ready ? h("p", "muted small", `${v.words} words`) : null,
      h("div", "aq-btns",
        ready ? h("button", { type: "button", class: "btn sm", title: "Copy the answer", onclick: () => copyText(v.text, "Answer copied") }, "Copy") : null,
        again ? small(ready ? "Regenerate" : "Try again", () => run(x.question, "regenerate", v.text), "A different answer from the same facts") : null,
        ready ? small("Shorter", () => run(x.question, "shorter", v.text), "The same answer, shorter") : null,
        ready ? small("Save to Answers", () => save(x, v), "Keep it in Answers for the same question elsewhere") : null));
  }

  const go = () => { const question = ask.value.trim(); if (question) { ask.value = ""; run(question); } };
  box.append(h("link", { rel: "stylesheet", href: "/offer.css" }), h("section", { class: "side-block aq", "aria-labelledby": "aq-h" },
    h("h3", { id: "aq-h" }, "Application questions"),
    h("p", "muted small", "Claude writes the answer from this JD, your resume for it and your facts. Nothing is written until you click."),
    ask, limit, h("button", { type: "button", class: "btn", onclick: go, title: "Claude writes the answer (only now, on this click)" }, "Generate"), out));
  redraw();
}
