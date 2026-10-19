// GOAL23 Autofill extension, the real extension in headless Chrome against a real job-hunt-agent server: its settings page,
// then a whole Workday application on fixture pages (real saved Create Account / Sign In pages, Workday-shaped steps
// after them): account, My Information, My Experience (upload, wrong parsed rows fixed), questions, the legal ones
// only from saved answers, auto-fill on/off, a hand-typed answer kept, Review, Submit by the person -> Applied.
// usage: node goal23_extension.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir>
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX] = process.argv.slice(2);
const HOST = "zztest.wd5.myworkdayjobs.com";
const JOB = `https://${HOST}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001`;
const APPLY = `${JOB}/apply/applyManually`;
const GENERIC = "careers.zztest-generic.example";
const PASSWORD = "Zz&Test\"Pass-9!";                // & and " : caught HTML-escaped too
const EMAIL = "zz.testcase@example.com";
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const api = async (path, body) => {
  const r = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}profile=zztest`, { method: body ? "POST" : "GET",
    headers: { "X-Jobhunt-Token": TOKEN, "X-Jobhunt": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify({ profile: "zztest", ...body }) : undefined });
  return r.json();
};
const b = await launch(CHROME, PROFILE_DIR);
let t;
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  check("the extension loads with its fixed id", id === "kmlpnmhllggacfjfpmigaigohedjcmaa", id);

  // ---- settings page ----
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    document.querySelector("input[name=profile][value=zztest]") || document.querySelector(".seg").append(Object.assign(document.createElement("input"), {type: "radio", name: "profile", value: "zztest"}));
    document.querySelector("input[name=profile][value=zztest]").checked = true;
    $("email").value = ${JSON.stringify(EMAIL)}; $("password").value = ${JSON.stringify(PASSWORD)}; $("test").click()`);
  check("Test connection reaches job-hunt-agent", await o.until("document.getElementById('test-out').textContent.startsWith('Connected as zztest')"),
    await o.js("return document.getElementById('test-out').textContent"));
  check("the password box is masked", await o.js("return document.getElementById('password').type === 'password' && !document.getElementById('masked').textContent.includes(" + JSON.stringify(PASSWORD) + ")"));
  check("the account is kept in chrome.storage.local", await o.js(`const s = await chrome.storage.local.get("account"); return s.account.password === ${JSON.stringify(PASSWORD)}`));
  check("a request carrying the password is refused", await o.js(`const r = await chrome.runtime.sendMessage({type: "api", method: "POST", path: "/api/autofill/report", body: {company: ${JSON.stringify(PASSWORD)}}}); return !r.ok && /password/.test(r.error)`));

  // ---- the job site ----
  t = await tab(b);
  await serveFixtures(t, b, [HOST, GENERIC], (u) => {
    if (u.hostname === GENERIC) return { file: join(FIX, "generic_skills.html"), type: "text/html" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go2 = async (url) => { await t.send("Page.navigate", { url }); await t.until("document.readyState === 'complete'", 10000); await sleep(1500); };
  const go = async (url) => { await t.send("Page.navigate", { url }); await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000); };
  const panelHas = (text, ms = 8000) => (async () => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; })();
  const val = (sel) => t.js(`return document.querySelector(${JSON.stringify(sel)})?.value ?? null`);
  const btnText = (field) => t.js(`return document.querySelector('[data-automation-id="formField-${field}"] button[aria-haspopup]')?.textContent`);
  const chosen = (field) => t.js(`return [...document.querySelectorAll('[data-automation-id="formField-${field}"] [data-automation-id=selectedItem]')].map((x) => x.title)`);
  const untrusted = () => t.js("return window.__wd.clicks.filter((c) => !c.trusted).map((c) => c.id)");

  await go(`${JOB}?fx=job`);
  check("the panel shows the matched job", await panelHas("ZZ Test Corp · AI Engineer"), await t.panelText());
  check("the panel shows the tailored resume", await panelHas("Tailored resume"), await t.panelText());
  check("the panel shows the address for the resume's city", await panelHas("808 Comet Dr, Foster City, CA 94404"), await t.panelText());
  await t.panelClick("Autofill");
  check("the posting says click Apply", await panelHas("Click Apply on the posting"), await t.panelText());

  // an apply page whose address says nothing: the posting this tab matched; "Not this job?" -> the picker
  await go(`https://${HOST}/en-US/External/userHome?fx=auth_signin`);
  check("an apply page keeps the posting this tab opened", await panelHas("The posting you opened in this tab") && await panelHas("ZZ Test Corp · AI Engineer"), await t.panelText());
  await t.panelClick("Not this job?");
  check("Not this job? shows the picker", await panelHas("Which job is this?") && await panelHas("ZZ Test Corp · AI Engineer ✓"), await t.panelText());
  await t.panelClick("ZZ Test Corp · AI Engineer");
  check("a pick is matched", await panelHas("Matched by your pick"), await t.panelText());
  await go(`https://${HOST}/en-US/External/userHome?fx=auth_signin`);
  check("the pick is remembered for the page", await panelHas("Matched by your pick"), await t.panelText());

  // Create Account (Autodesk's real page: terms box, robots-only box)
  await go(`${APPLY}?fx=auth_create`);
  check("Create Account is filled on its own", await t.until(`document.querySelector('[data-automation-id=verifyPassword]').value === ${JSON.stringify(PASSWORD)}`),
    await val("[data-automation-id=email]"));
  check("the email is the job-site email", (await val("[data-automation-id=email]")) === EMAIL);
  check("the password is filled", (await val("[data-automation-id=password]")) === PASSWORD);
  check("the terms box is ticked", await t.js("return document.querySelector('[data-automation-id=createAccountCheckbox]').checked"));
  check("the robots-only box stays empty", (await val("[data-automation-id=beecatcher]")) === "");
  check("it says click Create Account", await panelHas("Click Create Account"), await t.panelText());
  check("Create Account is never clicked", (await untrusted()).length === 0, await untrusted());
  await t.js("const p = document.querySelector('[data-automation-id=password]'); p.type = 'text'; p.setAttribute('value', p.value); return true");   // "Show password"
  await t.panelClick("Send page snapshot");
  check("a page snapshot is sent to job-hunt-agent", await panelHas("Page snapshot sent to job-hunt-agent"), await t.panelText());

  // "account exists" -> Sign In, filled; no loop
  await go(`${APPLY}?fx=auth_create&exists=1`);
  await t.panelClick("Autofill");
  check("an existing account switches to Sign In", await t.until("!document.querySelector('[data-automation-id=verifyPassword]') && document.querySelector('[data-automation-id=password]').value.length > 0"));
  check("Sign In gets the email and password", (await val("[data-automation-id=email]")) === EMAIL && (await val("[data-automation-id=password]")) === PASSWORD);
  check("it says click Sign In", await panelHas("Click Sign In"), await t.panelText());
  check("only the Sign In link was clicked, never Sign In itself", JSON.stringify(await untrusted()) === '["signInLink"]', await untrusted());

  await go(`${APPLY}?fx=auth_signin`);
  check("Capital One's real Sign In page is filled", await t.until(`document.querySelector('[data-automation-id=password]').value === ${JSON.stringify(PASSWORD)}`));
  check("Sign In is never clicked", (await untrusted()).length === 0, await untrusted());

  await go(`${APPLY}?fx=verify`);
  await t.panelClick("Autofill");
  check("the email-check step says check your email", await panelHas("Check your email, then click Autofill again"), await t.panelText());
  check("the email-check step clicks nothing", (await untrusted()).length === 0, await untrusted());

  await go(`${APPLY}?fx=auth_chooser`);
  check("NVIDIA's real Sign In chooser: email chosen, then filled", await t.until(`document.querySelector('[data-automation-id=password]')?.value === ${JSON.stringify(PASSWORD)}`) &&
    (await val("[data-automation-id=email]")) === EMAIL, await t.panelText());
  check("only Sign in with email was chosen, never Sign In", JSON.stringify(await untrusted()) === '["SignInWithEmailButton"]', await untrusted());

  await go(`${APPLY}?fx=auth_create_nobox&captcha=challenge`);
  await t.panelClick("Autofill");
  check("a human check pauses it", await panelHas("Solve the check"), await t.panelText());
  check("nothing is filled under a check", (await val("[data-automation-id=email]")) === "");
  await go(`${APPLY}?fx=auth_create_nobox&captcha=box`);
  await t.panelClick("Autofill");
  check("a robot box is left for the user, the form filled", await panelHas("Tick “I'm not a robot” yourself") && (await val("[data-automation-id=email]")) === EMAIL, await t.panelText());

  // ---- My Information (auto-filled when the step appears) ----
  await go(`${APPLY}?fx=flow&step=info`);
  check("My Information fills on its own", await t.until("document.getElementById('phoneNumber--phoneNumber')?.value && document.querySelector('[data-automation-id=formField-countryPhoneCode] [data-automation-id=selectedItem]')", 20000), await val("#address--postalCode"));
  check("How did you hear: Company Website", JSON.stringify(await chosen("source")) === '["Company Website"]', await chosen("source"));
  check("previously worked here: No", await t.js("return [...document.querySelectorAll('input[name=candidateIsPreviousWorker]')].find((i) => i.checked)?.value === 'No'"));
  check("legal name", (await val("#name--legalName--firstName")) === "Zz" && (await val("#name--legalName--lastName")) === "Testcase",
    [await val("#name--legalName--firstName"), await val("#name--legalName--lastName")]);
  check("address for the resume's city, street only", (await val("#address--addressLine1")) === "808 Comet Dr" && (await val("#address--addressLine2")) === "" &&
    (await val("#address--city")) === "Foster City" && (await btnText("countryRegion")) === "California", [await val("#address--addressLine1"), await val("#address--city"), await btnText("countryRegion")]);
  check("phone type, country code and number", (await btnText("phoneType")) === "Mobile" && JSON.stringify(await chosen("countryPhoneCode")) === '["United States of America (+1)"]' &&
    /555.?010.?0199/.test(await val("#phoneNumber--phoneNumber")), [await btnText("phoneType"), await chosen("countryPhoneCode"), await val("#phoneNumber--phoneNumber")]);
  check("the panel counts what it filled", await panelHas("Filled "), await t.panelText());

  // ---- My Experience: the person clicks Next ----
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  check("My Experience fills on its own after the upload", await t.until(`window.__wd.parsed && document.querySelectorAll('[data-sec=work] > [role=group]').length === 2 &&
    document.getElementById('workExperience-2--companyName')?.value === 'Testworks' && [...document.querySelectorAll('[data-sec=websites] input')].filter((i) => i.value).length === 2 &&
    [...document.querySelectorAll('[data-automation-id=formField-skills] [data-automation-id=selectedItem]')].some((x) => x.title === 'AWS Glue')`, 90000),
    await t.js("return [...document.querySelectorAll('[data-sec=work] input[id$=companyName]')].map((i) => i.value)"));
  check("the tailored resume was uploaded", await t.js("return window.__wd.uploaded?.name === 'Zz_Testcase_AI_Engineer_Resume.pdf' && window.__wd.uploaded.type === 'application/pdf'"),
    await t.js("return window.__wd.uploaded"));
  const work = await t.js(`return [1, 2].map((i) => { const p = document.querySelector('[data-automation-id=workExperience-' + i + ']');
    const d = (f) => [...p.querySelectorAll('[data-automation-id=formField-' + f + '] input')].map((x) => x.value).join('/');
    return [p.querySelector('[id$=jobTitle]').value, p.querySelector('[id$=companyName]').value, p.querySelector('[id$=location]').value,
      p.querySelector('[data-automation-id=currentlyWorkHere]').checked, d('startDate'), d('endDate')]; })`);
  check("work history is exactly the user's: wrong rows fixed, junk removed", JSON.stringify(work) ===
    JSON.stringify([["AI Engineer", "Examplecorp", "USA", true, "07/2024", "/"], ["ML Engineer", "Testworks", "India", false, "06/2020", "07/2022"]]), work);
  const edu = await t.js(`const p = [...document.querySelectorAll('[data-sec=education] > [role=group]')];
    return [p.length, [...p[0].querySelectorAll('[data-automation-id=formField-school] [data-automation-id=selectedItem]')].map((x) => x.title).join(),
      p[0].querySelector('[data-automation-id=formField-degree] button').textContent,
      [...p[0].querySelectorAll('[data-automation-id=formField-fieldOfStudy] [data-automation-id=selectedItem]')].map((x) => x.title).join(),
      p[0].querySelector('[data-automation-id=formField-firstYearAttended] input').value, p[0].querySelector('[data-automation-id=formField-lastYearAttended] input').value]`);
  check("education is exactly the user's", JSON.stringify(edu) === JSON.stringify([1, "University of Central Missouri", "Master's Degree", "Computer Science", "", "2024"]), edu);
  check("websites: exactly LinkedIn and GitHub (junk parsed rows fixed or removed)", await t.js("return [...document.querySelectorAll('[data-sec=websites] input')].map((i) => i.value).sort().join() === 'https://github.com/zz-testcase-example,https://www.linkedin.com/in/zz-testcase-example'"),
    await t.js("return [...document.querySelectorAll('[data-sec=websites] input')].map((i) => i.value)"));
  const skills = await chosen("skills");
  check("every skill of the tailored resume, one at a time, closest match, no duplicates", JSON.stringify(skills) === JSON.stringify(["Amazon Web Services (AWS)",
    "Python", "PySpark", "PyTorch", "LangChain", "Retrieval Augmented Generation (RAG)", "Amazon S3", "AWS Glue"]), skills);   // AWS was parsed in: kept once
  check("a skill Workday does not list is named in the panel", await panelHas("not in Workday's list: LLM evaluation"), await t.panelText());
  check("Next was never clicked by the extension", (await untrusted()).length === 0, await untrusted());

  // ---- Application Questions 1: one it cannot answer ----
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  check("saved answers fill the questions", await t.until("document.querySelector('[data-automation-id=formField-q3] button')?.textContent === 'Yes'", 15000) &&
    (await btnText("q1")) === "Yes" && (await btnText("q2")) === "No", [await btnText("q1"), await btnText("q2"), await btnText("q3")]);
  const inTwoWeeks = (() => { const d = new Date(); d.setDate(d.getDate() + 14); return [d.getMonth() + 1, d.getDate(), d.getFullYear()]; })();
  const pad2 = (n) => String(n).padStart(2, "0");
  check("a start date saved as \"2 weeks after offer acceptance\" fills Workday's date parts with today + 2 weeks",
    await t.until(`[...document.querySelectorAll('[data-automation-id=formField-availableDate] input')].map((i) => Number(i.value)).join('/') === ${JSON.stringify(inTwoWeeks.join("/"))}`, 8000),
    await t.js("return [...document.querySelectorAll('[data-automation-id=formField-availableDate] input')].map((i) => i.value)"));
  check("and its dropdown picks the closest choice (Within 2 weeks)", (await btnText("qStart")) === "Within 2 weeks", await btnText("qStart"));
  check("an unknown question is left for the user", await t.until("document.querySelector('[data-automation-id=formField-q4][data-jh-need]')") && (await btnText("q4")) === "Select One");
  check("the panel lists it under Needs you", await panelHas("Kubernetes operators"), await t.panelText());
  await t.js("window.scrollTo(0, 0); document.activeElement?.blur()");
  await t.panelClick("Do you have production experience with Kubernetes operators?");
  check("clicking a Needs-you item goes to it", await t.until("document.activeElement === document.querySelector('[data-automation-id=formField-q4] button')"),
    await t.js("return document.activeElement?.outerHTML?.slice(0, 120)"));
  const needs = await api("/api/autofill/answers");
  const k8s = needs.needs.find((n) => /Kubernetes operators/.test(n.text));
  check("job-hunt-agent has it in Needs you, with its choices", k8s && JSON.stringify(k8s.options) === '["Yes","No"]', needs.needs.map((n) => n.text));
  await api("/api/autofill/needs/answer", { id: k8s.id, answer: "No" });
  await t.panelClick("Autofill");
  check("answered once in job-hunt-agent, it fills", await t.until("document.querySelector('[data-automation-id=formField-q4] button').textContent === 'No'"), await btnText("q4"));

  // ---- Application Questions 2: auto-fill off, a typed answer is kept ----
  const toggled = await t.panelClick("Auto-fill each new page");
  const before = await t.js("return window.__wd.step");
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  const moved = await t.until("document.querySelector('[data-automation-id=formField-q5] textarea')");
  check("the user moves to the next step", moved, [before, await t.js("return window.__wd.step + ' popups=' + document.querySelectorAll('[data-automation-id=activeListContainer]').length")]);
  await sleep(3000);
  check("with auto-fill off a new step waits", (await val("[data-automation-id=formField-q5] textarea")) === "", await val("[data-automation-id=formField-q5] textarea"));
  await t.clickSel("[data-automation-id=formField-q5] textarea");
  await t.send("Input.insertText", { text: "My own words" });
  await t.panelClick("Autofill");
  await sleep(2500);
  check("a free-text question offers Draft", await panelHas("Why are you interested in this role?") && await t.panelClick("Draft"), await t.panelText());
  check("Draft puts Claude's text in the box, marked as a draft", await t.until("document.querySelector('[data-automation-id=formField-q6] textarea').value.startsWith('I build retrieval') && document.querySelector('[data-automation-id=formField-q6] textarea').hasAttribute('data-jh-draft')"),
    await val("[data-automation-id=formField-q6] textarea"));
  check("a field the user typed in is never overwritten", (await val("[data-automation-id=formField-q5] textarea")) === "My own words", await val("[data-automation-id=formField-q5] textarea"));
  check("the auto-fill toggle can be turned off", toggled);
  await t.panelClick("Auto-fill each new page");
  check("and on again", await t.js("return true") && (await (async () => { const v = await o.js("return (await chrome.storage.local.get('auto')).auto"); return v === true; })()));

  // ---- Voluntary Disclosures: legal answers only from saved ones ----
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  await t.until("document.querySelector('[data-automation-id=formField-gender]')");
  await t.panelClick("Autofill");
  await panelHas("Needs you");
  await sleep(1500);
  check("EEO questions are never guessed", (await btnText("gender")) === "Select One" && (await btnText("ethnicity")) === "Select One" &&
    (await btnText("veteranStatus")) === "Select One" && !(await t.js("return document.querySelector('[data-automation-id=agreementCheckbox]').checked")),
    [await btnText("gender"), await btnText("ethnicity"), await btnText("veteranStatus")]);
  for (const [key, answer] of [["gender", "I do not wish to self-identify"], ["race", "I do not wish to self-identify"], ["veteran", "I am not a protected veteran"], ["terms", "Yes"],
    ["signature_name", "Zz Testcase"], ["disability", "I do not want to answer"]]) await api("/api/autofill/answer", { key, answer });
  await t.panelClick("Autofill");
  check("saved EEO answers fill", await t.until("document.querySelector('[data-automation-id=agreementCheckbox]').checked") &&
    (await btnText("gender")) === "I do not wish to self-identify" && /do not wish/.test(await btnText("ethnicity")) &&
    await t.js("return document.querySelector('[data-automation-id=agreementCheckbox]').checked"), [await btnText("gender"), await btnText("ethnicity"), await btnText("veteranStatus")]);

  // ---- Self Identify: name + today's date from saved answers ----
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  await t.until("document.querySelector('[data-automation-id=formField-name]')");
  await t.panelClick("Autofill");
  const today = new Date();
  const want = [String(today.getMonth() + 1).padStart(2, "0"), String(today.getDate()).padStart(2, "0"), String(today.getFullYear())].join("/");
  check("Self Identify: signature name, today's date, disability", await t.until(`[...document.querySelectorAll('[data-automation-id=formField-dateSignedOn] input')].map((i) => i.value).join('/') === ${JSON.stringify(want)} && document.querySelector('input[name=disabilityStatus]:checked')`) &&
    (await val("#selfIdentifiedDisabilityData--name")) === "Zz Testcase" && await t.js("return [...document.querySelectorAll('input[name=disabilityStatus]')].find((i) => i.checked)?.nextSibling.textContent === 'I do not want to answer'"),
    [await val("#selfIdentifiedDisabilityData--name"), await t.js("return [...document.querySelectorAll('[data-automation-id=formField-dateSignedOn] input')].map((i) => i.value).join('/')")]);

  // ---- Review: nothing filled; the user clicks Submit; the confirmation marks it Applied ----
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  await t.until("document.querySelector('[data-automation-id=applyFlowReviewPage]')");
  await t.panelClick("Autofill");
  check("Review says review, then click Submit", await panelHas("Review, then click Submit"), await t.panelText());
  check("the extension never clicked Next or Submit", (await untrusted()).length === 0, await untrusted());
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  check("the confirmation marks it Applied", await panelHas("marked Applied in job-hunt-agent", 15000), await t.panelText());
  check("with the address it used", await panelHas("Address: 808 Comet Dr, Foster City, CA 94404"), await t.panelText());
  // ---- any other board: its Skills typeahead gets the same skills, one at a time ----
  await go2(`https://${GENERIC}/jobs/ai-engineer/apply`);
  await sleep(8000);                                    // the old code filled it (and uploaded that resume) by now
  check("another site opened in the same tab is not auto-filled with the last job", (await val("#fn")) === "" && (await val("#em")) === "",
    [await val("#fn"), await val("#em")]);
  await t.panelClick("Autofill");
  check("a generic site's Skills typeahead gets every skill it lists, one at a time", await t.until("window.__skills.length === 7", 30000) &&
    JSON.stringify(await t.js("return window.__skills")) === JSON.stringify(["Python", "PySpark", "PyTorch", "Retrieval Augmented Generation (RAG)",
      "Amazon Web Services (AWS)", "Amazon S3", "AWS Glue"]), await t.js("return window.__skills"));
  check("its missing skill is named, nothing typed is left behind", await panelHas("Skills not in the list: LLM evaluation") && (await val("#sk")) === "", await t.panelText());
  const twoWeeks = `${pad2(inTwoWeeks[0])}/${pad2(inTwoWeeks[1])}/${inTwoWeeks[2]}`;
  check("a mm/dd/yyyy box gets today + 2 weeks", (await val("#sd")) === twoWeeks, await val("#sd"));
  check("a date input gets today + 2 weeks", (await val("#da")) === `${inTwoWeeks[2]}-${pad2(inTwoWeeks[0])}-${pad2(inTwoWeeks[1])}`, await val("#da"));
  check("a start dropdown gets the narrowest choice holding 2 weeks", (await val("#nt")) === "Less than 1 month", await val("#nt"));
  check("the generic form is never submitted", (await t.js("return window.__submitted")) === 0);
  check("no page errors", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  if (process.env.JH_DEBUG && t) {
    const { writeFileSync } = await import("node:fs");
    writeFileSync(process.env.JH_DEBUG, JSON.stringify({ logs: t.logs, panel: await t.panelText().catch(() => ""),
      wd: await t.js("return {clicks: window.__wd.clicks, steps: window.__wd.steps, step: window.__wd.step}").catch(() => null) }, null, 1));
  }
  b.close();
  console.log(JSON.stringify({ checks }));
}
