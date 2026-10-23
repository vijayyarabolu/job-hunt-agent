// GOAL27: the classifier on recorded apply pages (tests/fixtures/autofill/boards). Each page is opened from disk in
// headless Chrome with the extension's field scripts injected (no extension, no network); prints every field's label
// and canonical key. usage: node goal27_classify_pages.mjs <chrome> <profile dir> <extension dir> <page.html>...
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { launch, sleep, tab } from "./extpipe.mjs";

const [CHROME, PROFILE_DIR, EXT_DIR, ...FILES] = process.argv.slice(2);
const SCRIPTS = ["core.js", "pick.js", "classify.js", "fields.js"].map((f) => readFileSync(join(EXT_DIR, "content", f), "utf8"));
const b = await launch(CHROME, PROFILE_DIR);
const out = {};
try {
  for (const file of FILES) {
    const t = await tab(b, `file://${file}`);
    await sleep(500);
    for (const s of SCRIPTS) await t.js(`${s}\nreturn 1`);
    out[file.split("/").pop()] = await t.js("return JH.fields.collect().map((f) => [f.label.slice(0, 120), f.cls ? f.cls.key : null])");
    await b.send("Target.closeTarget", { targetId: t.targetId });
  }
} finally {
  b.close();
  console.log(JSON.stringify(out));
}
