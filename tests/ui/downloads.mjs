// The harness's downloads land in the test's temp dir and are deleted on close (never ~/Downloads).
// node downloads.mjs <chrome> <profileDir>
import { existsSync, readdirSync } from "node:fs";
import { sleep, start } from "./cdp.mjs";

const [CHROME, PROFILE_DIR] = process.argv.slice(2);
const NAME = "ZZ_TEST_download.txt";
const { send, js, until, checks, check, downloads, close } = await start(CHROME, PROFILE_DIR);
try {
  await send("Page.navigate", { url: `data:text/html,<a id="a" download="${NAME}" href="data:text/plain,zz">x</a>` });
  await until("document.getElementById('a')");
  await js("document.getElementById('a').click(); return true");
  let landed = false;
  for (let t = 0; t < 50 && !landed; t++) { landed = existsSync(downloads) && readdirSync(downloads).includes(NAME); await sleep(100); }
  check("a download lands in the test's temp folder", landed, downloads);
} catch (err) {
  check("runner", false, err.stack || String(err));
}
close();
check("the temp download folder is deleted on close", !existsSync(downloads), downloads);
console.log(JSON.stringify({ checks, name: NAME, downloads }));
