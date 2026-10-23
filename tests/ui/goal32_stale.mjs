// GOAL32 §3: extension/content/core.js in a bare VM with a stub chrome, first alive, then as Chrome leaves an old tab's
// content script after the extension is reloaded (runtime.id gone, every call throws "Extension context invalidated").
// Prints one JSON line of results for tests/test_goal32_extension.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const [, , corePath] = process.argv;
const invalid = () => { throw new Error("Extension context invalidated."); };
const listeners = {};
let alive = true;
const store = { base: "http://127.0.0.1:8765" };
const chrome = {
  runtime: {
    get id() { return alive ? "ext-id" : undefined; },
    lastError: undefined,
    getManifest: () => ({ version: "1.6.4" }),
    sendMessage: (msg, cb) => (alive ? cb({ ok: true, data: msg.type }) : invalid()),
  },
  storage: { local: {
    get: (keys, cb) => (alive ? cb({ base: store.base }) : invalid()),
    set: (patch, cb) => (alive ? (Object.assign(store, patch), cb()) : invalid()),
  } },
};
const win = { addEventListener: (type, fn) => { listeners[type] = fn; } };
win.top = win;
const doc = { addEventListener: () => {} };
const sandbox = { chrome, window: win, document: doc, Element: class {}, setTimeout, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(readFileSync(corePath, "utf8"), sandbox, { filename: corePath });
const JH = sandbox.JH;
const notices = [];
JH.panel = { set: (patch) => notices.push(patch) };

const out = { version: JH.version };
out.aliveSend = await JH.send({ type: "ping" });
out.aliveStore = await JH.store("base");
out.aliveSave = await JH.save({ auto: true });
alive = false;
const settle = (p) => p.then((v) => ({ value: v }), (e) => ({ threw: String(e && e.message) }));
out.deadSend = await settle(JH.send({ type: "ping" }));
out.deadApi = await settle(JH.api("GET", "/api/health"));
out.deadStore = await settle(JH.store("base"));
out.deadSave = await settle(JH.save({ auto: false }));
let prevented = false;
listeners.unhandledrejection({ reason: new Error("Extension context invalidated."), preventDefault: () => { prevented = true; } });
out.rejectionPrevented = prevented;
out.notices = notices;
console.log(JSON.stringify(out));
