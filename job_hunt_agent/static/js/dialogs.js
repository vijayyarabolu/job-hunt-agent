// Mark applied (#apply-dlg): resume, optional file.
import { $, B, api, ask, closePop, fail, get, h, menu, mini, openPop, q, toast } from "./core.js";

const MB = 1024 * 1024;

async function sendResumeFile(appId, file, replace) {
  if (!/\.(pdf|docx)$/i.test(file.name)) throw new Error("Choose a PDF or a .docx file");
  return api(`/api/tracker/resume?${q({ app: appId, name: file.name, replace: replace || "" })}`, null, file);
}

// GOAL19 §3: × Remove (REMOVE_TIP)
export const REMOVE_TIP = "Remove: a duplicate or a job you never want to see. It leaves every list, Archived too; Undo for 8 s (Shift+X)";
export const REMOVE_UNDO_MS = 8000;
export const xBtn = (what, run) => B({ class: "x-rm", title: REMOVE_TIP, "aria-label": `Remove ${what}`,
  onclick: (e) => { e.stopPropagation(); run(); } }, "×");
// a tracker row (Archived, phone card): asks first, then gone here and from the Sheet
export async function removeApp(app, gone, back) {
  const what = `${app.company || "(company)"} · ${app.role_title || "(role)"}`;
  if (!(await ask(`Delete ${what}?`, "It is removed here and from your Google Sheet. You can Undo.", "Delete"))) return;
  try {
    const r = await api("/api/remove", { app: app.id, why: app.dup_of ? "duplicate" : "unwanted" });
    gone();
    toast(`Deleted ${what}`, { ms: REMOVE_UNDO_MS, undo: () => api("/api/remove/undo", { id: r.removed }).then(back, fail) });
  } catch (err) { fail(err); }
}

const uploaded = (up) => toast(`Uploaded ${up.name} · Sheet: ${up.sheet}`);

export async function openApplyDialog(opts) {
  const target = opts.uid ? { uid: opts.uid } : { app: opts.appId };
  let info;
  try { info = await get("/api/apply_options", target); } catch (err) { fail(err); return; }
  const [dlg, sel, file, upload, status] = ["#apply-dlg", "#ad-resume", "#ad-file", "#ad-upload", "#ad-status"].map((s) => $(s)); // not .map($): the index would become the root
  const maxMb = info.upload_max_mb || 10;
  const who = info.app || (info.job ? { company: info.job.company, role_title: info.job.title } : null);
  $("#ad-who").textContent = who ? `${who.company || ""} · ${who.role_title || ""}` : "this job";
  $("#ad-vendor").textContent = info.vendor_label || "";
  $("#ad-note").textContent = `PDF or DOCX, up to ${maxMb} MB. Saved to your Google Drive (job-hunt-agent/Resumes), never over ` +
    "an older file, and linked in your sheet's Resume cell.";
  sel.replaceChildren(...info.resumes.map((r) => h("option", { value: r.id }, r.pct == null ? r.label : `${r.label} (${r.pct}%)`)));
  sel.value = opts.resume || info.best || info.resumes[0]?.id || "";
  file.value = "";
  status.textContent = "";
  const controls = [upload, $("#ad-without"), $("#ad-cancel"), sel, file];
  const busy = (on) => controls.forEach((c) => { c.disabled = on; });
  busy(false);
  upload.disabled = true;
  file.onchange = () => {
    const f = file.files[0];
    const tooBig = f && f.size > maxMb * MB;
    status.textContent = tooBig ? `That file is over ${maxMb} MB` : f ? f.name : "";
    status.classList.toggle("bad", !!tooBig);
    upload.disabled = !f || tooBig;
  };
  const finish = async (withFile) => {
    busy(true);
    status.textContent = "Marking applied…";
    let appId;
    try { appId = (await api("/api/applied", { resume: sel.value, with_file: withFile, ...target })).app; }
    catch (err) { busy(false); status.textContent = err.message; status.classList.add("bad"); return; }
    if (withFile) {
      status.textContent = `Uploading ${file.files[0].name}…`;
      try { uploaded(await sendResumeFile(appId, file.files[0])); }
      catch (err) { toast(`Marked applied, but the upload failed: ${err.message}. Use "Upload resume" on the row.`, { error: true }); }
    }
    dlg.close();
    opts.onDone?.(appId, withFile);
  };
  upload.onclick = () => finish(true);
  $("#ad-without").onclick = () => finish(false);
  $("#ad-cancel").onclick = () => dlg.close();
  dlg.showModal();
  sel.focus();
}

export function pickResumeFile(appId, onDone, replace) {
  const input = h("input", { type: "file", accept: ".pdf,.docx", hidden: true });
  input.addEventListener("change", async () => {
    const f = input.files[0];
    input.remove();
    if (!f) return;
    toast(`Uploading ${f.name}…`);
    try { const up = await sendResumeFile(appId, f, replace); up.undo ? said(appId, onDone)(up) : uploaded(up); onDone?.(up.row); } catch (err) { fail(err); }
  });
  document.body.append(input);
  input.click();
}

// × / Replace ▾ (menu: resume_swap.py), Undo each
const swap = (app, a) => api("/api/resume/act", { app, ...a });
const said = (app, done) => (r) => toast(r.msg || `Uploaded ${r.name}`, r.undo && { undo: () => swap(app, { act: "undo", id: r.undo }).then((u) => { toast(u.msg); done?.(u.row); }, fail) });
export function resumeBtns(app, has, done, rmLabel = "×") {
  const go = (a) => swap(app, a).then((r) => { r.row && done?.(r.row); r.msg && said(app, done)(r); return r; }, fail);
  const m = menu();
  m.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; closePop(); b.value ? go({ act: "use", id: b.value }) : b.dataset.r === "upload" ? pickResumeFile(app, done, 1) : go({ act: b.dataset.r }); };
  return [has ? mini(rmLabel, "Remove this resume from the row (Drive keeps the file, in Replaced)", () => go({ act: "remove" }), "rm") : null,
    mini("Replace ▾", "Replace this row's resume", async (e) => { openPop(m, e.currentTarget, "Replace"); m.innerHTML = (await go({ act: "menu" }))?.html || ""; })];
}
