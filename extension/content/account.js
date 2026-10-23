// Job Hunt Agent Autofill 1.3.1 (AUTOFILL-BUGS #6): Sign In / Create Account forms on any site. The job-site email and
// password come from the extension's settings (chrome.storage.local only): they are never sent to Job Hunt Agent, never
// logged, never in a snapshot. Create Account gets email, password, the confirm-password box and its terms box; Sign In
// gets email and password. Autofill never clicks Sign In / Create Account / Register: the user does. When the site says the
// account already exists, the panel says to open Sign In (a site's own link is not followed for the user).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const EXISTS = /already (exists|in use|registered|associated|have an account)|account (with this e-?mail )?already|email (address )?is already/i;
  const CREATE = /\b(create (an )?account|register|sign ?up|new (user|candidate|account)|join)\b/i;
  const TERMS = /\b(terms|privacy|agree|consent|accept|policy|conditions)\b/i;
  const USER = /\b(e ?mail|user ?name|user ?id|login|log ?in id)\b/i;

  const shown = (el) => JH.visible(el) && !el.disabled && !el.readOnly && !el.closest("[data-jh-panel]");
  const words = (el) => [el.name, el.id, el.getAttribute("autocomplete"), el.getAttribute("aria-label"), el.placeholder,
    el.getAttribute("data-automation-id"), JH.labelOf ? JH.labelOf(el) : ""].join(" ");

  // the account form on this page: its password boxes (one = Sign In, two = Create Account) and the email / user box
  function find(root = document) {
    const passwords = JH.all("input[type=password]", root).filter(shown);
    if (!passwords.length) return null;
    const form = passwords[0].closest("form, [role=dialog], [data-automation-id=signInContent], [data-automation-id=createAccountContent], main") || document.body;
    const inputs = JH.all("input", form).filter((i) => shown(i) && /^(email|text|)$/.test(i.type));
    const user = inputs.find((i) => i.type === "email") || inputs.find((i) => USER.test(words(i)));
    const pw = passwords.filter((p) => form.contains(p));
    const heading = JH.text(form.querySelector("h1, h2, h3, legend, [role=heading]")) || document.title;
    const creating = pw.length >= 2 || (CREATE.test(heading) && !/\bsign ?in\b|\blog ?in\b/i.test(heading));
    return { form, user, passwords: pw, creating };
  }

  async function set(el, value) {
    if (!el || JH.byHand(el)) return false;              // what the user typed for this site stays
    if (el.value !== value) await JH.fill.putText(el, value);
    JH.mark(el);
    if (JH.marks) JH.marks.set(el, el.value === value ? "filled" : "failed", "");
    return el.value === value;
  }

  JH.account = {
    find,
    // { filled, message } or null when the page has no account form
    async fill(root = document) {
      const got = find(root);
      if (!got) return null;
      const { account } = await JH.store(["account"]);
      if (!account || !account.email || !account.password) {
        return { filled: 0, message: "This page asks you to sign in: add your job-site email and password in the extension's settings, then click Autofill" };
      }
      let filled = 0;
      if (await set(got.user, account.email)) filled++;
      for (const p of got.passwords) if (await set(p, account.password)) filled++;
      if (got.creating) {
        const box = JH.all("input[type=checkbox]", got.form)
          .find((c) => JH.shown(c) && TERMS.test(`${JH.labelOf(c)} ${c.name} ${c.id}`) && !JH.isHoneypot(c, JH.labelOf(c)));
        if (box && !box.checked && !JH.byHand(box)) { JH.click(box); await JH.sleep(150); }
        if (box && box.checked) filled++;
      }
      const errors = JH.text(got.form).slice(0, 3000);
      const message = got.creating && EXISTS.test(errors) ? "This email already has an account here: open Sign In, then click Autofill"
        : got.creating ? "Click Create Account (Autofill never clicks it)" : "Click Sign In (Autofill never clicks it)";
      return { filled, message, creating: got.creating };
    },
  };
})();
