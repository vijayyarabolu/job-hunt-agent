// The grid's CSS, adopted as a constructed sheet (the CSP blocks <style>; app.css and the lazy CSS are at their caps).
const CSS = `
.g-wrap { position: relative; flex: 1; min-height: 0; overflow: auto; margin: 0 var(--gut); border: var(--line); border-bottom: 0; background: var(--raised); outline: none; overscroll-behavior: contain }
.g-wrap:focus-visible { box-shadow: inset 0 0 0 2px color-mix(in oklab, #1a73e8 40%, transparent) }
.g { border-collapse: separate; border-spacing: 0; table-layout: fixed; width: 1px; font-size: 13px; user-select: none }
.g col.g-rnc { width: 46px }
.g th, .g td { height: 25px; padding: 0 8px; line-height: 24px; white-space: nowrap; overflow: hidden; text-overflow: clip; text-align: left; border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); box-sizing: border-box; font-weight: 400 }
.g thead th { position: sticky; top: 0; z-index: 3; height: 30px; background: var(--paper); font-weight: 650; font-size: 12px; color: var(--ink); padding-right: 32px }
.g thead th.app { background: color-mix(in oklab, var(--signal) 9%, var(--paper)) }
.g .fz { position: sticky; z-index: 1; background: var(--raised) }
.g thead .fz { z-index: 4 }
.g .g-rn { position: sticky; left: 0; z-index: 2; background: var(--paper); color: var(--quiet); font-size: 11px; text-align: center; padding: 0 2px; cursor: pointer }
.g thead .g-rn { z-index: 5; top: 0 }
.g .g-rn .g-x { display: none; position: absolute; right: 1px; top: 3px; width: 16px; height: 18px; padding: 0; border: 0; border-radius: 4px; background: var(--paper); color: var(--bad); font-size: 14px; line-height: 1; cursor: pointer }
.g tr:hover .g-rn .g-x, .g .g-rn .g-x:focus-visible { display: block }
.g .g-rn.g-dup { color: var(--bad); font-weight: 650; box-shadow: inset 3px 0 var(--bad) }
.g tr:has(td) { background: var(--raised) }
.g .hs { background: color-mix(in oklab, #1a73e8 18%, var(--paper)); color: var(--ink) }
.g thead th.hs { box-shadow: inset 0 -3px #1a73e8 }
.g td.sl { background-image: linear-gradient(color-mix(in oklab, #1a73e8 14%, transparent), color-mix(in oklab, #1a73e8 14%, transparent)) }
.g td.cur { box-shadow: inset 0 0 0 2px #1a73e8 }
.g td.hit { background-image: linear-gradient(#fbbc0466, #fbbc0466) }
.g td.ro { color: var(--quiet) }
.g td.g-cf { box-shadow: inset -6px 6px 0 -3px #e37400 }
.g td.g-cf.cur { box-shadow: inset 0 0 0 2px #1a73e8, inset -6px 6px 0 -3px #e37400 }
.g .g-chip { display: inline-block; padding: 0 8px; border-radius: 10px; line-height: 19px; color: #202124; font-size: 12px }
.g .st { height: 20px; font-size: 11.5px }
.g .g-co { display: flex; align-items: center; gap: 4px; min-width: 0 }
.g .g-co>span { overflow: hidden; text-overflow: ellipsis; min-width: 5em }
.g .g-co>.sb { flex: 0 1 auto; min-width: 22px }
.g .g-link { color: light-dark(#1155cc, #8ab4f8); text-decoration: underline }
.g .g-file { display: inline-flex; align-items: center; gap: 4px; padding: 0 8px; border: var(--line); border-radius: 10px; line-height: 19px; background: var(--paper) }
.g .g-file .ic { width: 12px; height: 12px }
.g td.c-resume .mini, .g td.c-resume .tag { height: 20px; min-height: 0; line-height: 18px; padding: 0 8px; margin-left: 4px; font-size: 11px }
@media (hover: hover) { .g td.c-resume .rm { opacity: 0 } .g tr:hover td.c-resume .rm, .g td.c-resume .rm:focus-visible { opacity: 1 } }
.g tr.g-sp td { height: auto; padding: 0; border: 0 }
.g tr.g-day th { position: sticky; left: 0; text-align: left; background: var(--paper); font-weight: 700; color: var(--ink); height: 28px }
.g-hl { overflow: hidden; text-overflow: ellipsis }
.g-fi { margin-left: 4px; color: #1a73e8 }
.g-hm { position: absolute; right: 8px; top: 5px; width: 20px; height: 20px; border: 0; border-radius: 4px; background: transparent; opacity: 0; display: grid; place-items: center; padding: 0; color: inherit }
.g thead th:hover .g-hm, .g-hm:focus-visible, .g-hm[aria-expanded=true] { opacity: 1; background: color-mix(in oklab, var(--ink) 10%, transparent) }
.g-rz { position: absolute; top: 0; right: 0; bottom: 0; width: 6px; cursor: col-resize; touch-action: none }
.g-rz:hover { background: #1a73e8 }
.g-fh { position: absolute; width: 7px; height: 7px; background: #1a73e8; border: 1px solid #fff; cursor: crosshair; z-index: 4 }
.g-fh[hidden] { display: none }
.g-fill td.sl { outline: 1px dashed #1a73e8; outline-offset: -1px }
.g-ed { position: absolute; z-index: 8; display: grid; background: var(--raised); box-shadow: 0 0 0 2px #1a73e8, 0 8px 24px -8px rgb(0 0 0 / .35) }
.g-ed input, .g-ed textarea { height: 24px; border: 0; border-radius: 0; padding: 0 4px; font: inherit; font-size: 13px; background: var(--raised); outline: none; width: 100% }
.g-ed textarea { height: 140px; padding: 4px; resize: both; line-height: 1.4 }
.g-ed .g-list { max-height: 220px; overflow: auto; border-top: var(--line); padding: 4px }
.g-ed .g-opt { display: flex; width: 100%; border: 0; background: none; padding: 4px 4px; border-radius: 4px; text-align: left }
.g-ed .g-opt[aria-selected=true] { background: color-mix(in oklab, #1a73e8 16%, transparent) }
.g-ed .g-date { display: flex; gap: 8px; align-items: center; padding: 8px; border-top: var(--line) }
.g-ed .g-date input { width: auto; height: 28px; border: var(--line); border-radius: 6px }
.g-ed .g-hint { padding: 2px 8px 4px; font-size: 11px; color: var(--quiet) }
.g-state { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; font-size: 12.5px; color: var(--quiet) }
.g-state:empty { display: none }
.g-find { display: flex; align-items: center; gap: 8px }
.g-find input { height: 30px; width: 200px }
.g-saved { font-size: 12px; color: var(--quiet); min-width: 64px }
.g-saved.err { color: var(--bad) }
.g-vals { display: grid; gap: 8px; padding: 8px; min-width: 240px }
.g-vals .g-boxes { max-height: 240px; overflow: auto; display: grid; gap: 2px }
.g-vals label { display: flex; gap: 8px; align-items: center; font-size: 13px }
.g-vals input[type=checkbox] { height: auto }
.g-row2 { display: flex; gap: 8px; justify-content: flex-end }
.g-empty { position: sticky; left: 0 }
`;
let done = false;
export function css() {
  if (done) return;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(CSS);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  done = true;
}
