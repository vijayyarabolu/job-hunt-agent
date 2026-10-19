// Job Hunt Agent Autofill: the resume Autofill uploads for a job, opened from the panel's "View". The PDF comes
// from job_hunt_agent through the service worker (which holds the address and token) and is shown in this tab only.
"use strict";
(async () => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const query = { uid: params.get("uid") || "", app: params.get("app") || "" };
  const res = await chrome.runtime.sendMessage({ type: "api", path: "/api/autofill/resume", query, binary: true });
  if (!res || !res.ok) {
    $("name").textContent = "No resume";
    $("msg").hidden = false;
    $("msg").textContent = (res && res.error) || "Job Hunt Agent did not answer";
    return;
  }
  const bin = atob(res.data.b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: res.data.type || "application/pdf" }));
  document.title = `${res.data.name} · Job Hunt Agent Autofill`;
  $("name").textContent = res.data.name;
  Object.assign($("save"), { href: url, download: res.data.name, hidden: false });
  Object.assign($("pdf"), { src: url, hidden: false });
})();
