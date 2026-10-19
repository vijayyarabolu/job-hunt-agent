// the Settings page for the browser tests. Open it (from the person menu, as a person would), pick a section,
// change one field and wait until it says "Saved ✓". Every field saves on its own; there is no Save button.
export function settingsPage({ js, until, clickText }) {
  const open = async () => {
    if (await js("return document.body.dataset.view === 'settings' && !!document.querySelector('.set-page h1')")) return true;
    await js("document.querySelector('#who').click(); return true");
    await clickText("#pop .opt", "Settings");
    return until("document.body.dataset.view === 'settings' && document.querySelector('.set-nav')", 6000);
  };
  const section = async (sec) => {
    await open();
    await js(`document.querySelector('.set-nav [data-sec=${JSON.stringify(sec)}]').click(); return true`);
    return until(`document.querySelector('.set-nav [data-sec=${JSON.stringify(sec)}][aria-current=page]') && document.querySelector('.set-body .set-field, .set-body .set-card')`, 4000);
  };
  const field = (label) => `[...document.querySelectorAll('.set-body label')].find((l) => l.textContent.trim().startsWith(${JSON.stringify(label)}))`;
  // value: a string for a text / number field, true / false for a checkbox; returns true once it says Saved
  const set = async (sec, label, value) => {
    await section(sec);
    await js(`const l = ${field(label)}; const i = l.querySelector('input, textarea');
      if (typeof ${JSON.stringify(value)} === 'boolean') i.checked = ${JSON.stringify(value)}; else i.value = ${JSON.stringify(value)};
      i.dispatchEvent(new Event('change', { bubbles: true })); return true`);
    return until(`${field(label)}?.querySelector('.saved-state')?.textContent === 'Saved ✓'`, 8000);
  };
  const read = (sec, label) => section(sec).then(() => js(`const i = ${field(label)}?.querySelector('input, textarea'); return i ? (i.type === 'checkbox' ? i.checked : i.value) : null`));
  const close = async () => { await js("document.querySelector('.set-back')?.click(); return true"); return until("document.body.dataset.view !== 'settings'", 4000); };
  return { open, section, set, read, close };
}
