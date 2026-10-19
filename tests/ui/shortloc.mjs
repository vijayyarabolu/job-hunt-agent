// core.js's shortLoc() in node with a stubbed DOM, for tests/test_goal17_shortloc.py.
// usage: node shortloc.mjs '<json list of location strings>' -> prints {location: short text}
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], documentElement: { dataset: {} },
  addEventListener() {}, createElement: () => ({ style: {}, setAttribute() {}, append() {} }) };
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
const { shortLoc } = await import(new URL("../../job_hunt_agent/static/js/core.js", import.meta.url));
console.log(JSON.stringify(Object.fromEntries(JSON.parse(process.argv[2]).map((l) => [l, shortLoc(l)]))));
