// Facade: rowedit.js, panels.js load on first use.
import { $, S, fail, get } from "./core.js";

export let reloadTracker = () => {};
export const setTrackerReload = (fn) => { reloadTracker = fn; };
const call = (load, name) => (...args) => load().then((m) => m[name](...args)).catch(fail);
const editor = () => import("./rowedit.js");
const panels = () => import("./panels.js");
export const openRowEditor = call(editor, "openRowEditor");
export const loadFollowups = call(panels, "loadFollowups");
export const openAddJob = call(panels, "openAddJob");
export const openVendors = call(panels, "openVendors");
const repaintEditor = call(editor, "refreshEditor");
export const refreshEditor = (row) => { if (S.detail?.panel) repaintEditor(row); };

export async function refreshFollowups() {
  try { setFollowupCount((await get("/api/tracker/followups", { count: 1 })).due_count); } catch { /* best effort */ }
}

export function setFollowupCount(n) {
  S.followups = n;
  const asks = S.snoozed || 0, tip = `${n} follow-up${n === 1 ? "" : "s"} due${asks ? ` + ${asks} “Did you apply?”` : ""}`;
  n += asks;
  const badge = $('#tabs [data-tab="followups"] .n');
  if (badge) { badge.textContent = n ? String(n) : ""; badge.title = n ? tip : ""; }
  const apps = $('#btabs [data-tab="tracker"] .n');           // the phone's Applications shows the follow-ups due
  if (apps) { apps.textContent = n ? String(n) : ""; apps.title = n ? tip : ""; }
}
