// save a public apply page's form as a fixture for the nightly canary and the browser tests. The page is
// rendered in headless Chrome (no extension, nothing typed, never submitted), "Apply" is clicked when the form sits
// behind it, then scripts are removed and every element the site's CSS hides gets an inline display:none, so the saved
// copy shows exactly the fields a person sees (a honeypot stays hidden).
// usage: node scripts/autofill/capture.mjs <chrome> <url> <out.html>
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch, sleep, tab } from "../../tests/ui/extpipe.mjs";

const [CHROME, URL_IN, OUT] = process.argv.slice(2);
const FORM = "document.querySelectorAll('input:not([type=hidden]), select, textarea').length >= 3";
const APPLY = /^(apply( now| for this (job|position))?|i'?m interested)$/i;

const b = await launch(CHROME, mkdtempSync(join(tmpdir(), "jh-capture-")));
try {
  const t = await tab(b);
  await t.send("Page.navigate", { url: URL_IN });
  await t.until("document.readyState === 'complete'", 20000);
  if (!(await t.until(FORM, 12000))) {
    await t.js(`const b = [...document.querySelectorAll("a, button")].find((x) => ${APPLY}.test((x.innerText || "").trim())); if (b) b.click(); return !!b;`);
    await t.until(FORM, 15000);
  }
  await sleep(1500);
  const html = await t.js(`
    for (const el of document.querySelectorAll("body *")) {
      const st = getComputedStyle(el);
      if (st.display === "none") el.setAttribute("style", "display:none");
      else if (st.visibility === "hidden") el.setAttribute("style", "visibility:hidden");
    }
    const copy = document.documentElement.cloneNode(true);
    for (const s of copy.querySelectorAll("script, noscript, link[rel=preload], link[rel=modulepreload], iframe")) s.remove();
    for (const s of copy.querySelectorAll("style")) if (s.textContent.length > 100000) s.remove();   // a framework's whole CSS
    return "<!doctype html>\\n" + copy.outerHTML.replace(/data:[^)"'\\s]{200,}/g, "data:,");`);   // inlined fonts and images
  const fields = await t.js(`return document.querySelectorAll('input:not([type=hidden]), select, textarea').length`);
  writeFileSync(OUT, html);
  console.log(JSON.stringify({ ok: true, url: URL_IN, out: OUT, fields, bytes: html.length }));
} catch (err) {
  console.log(JSON.stringify({ ok: false, url: URL_IN, error: String(err && err.message) }));
} finally {
  b.close();
}
