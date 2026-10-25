"""GOAL36 §5.2: writes the pages the extension could not read (tests/fixtures/autofill/goal36/sites/*.html), each the
shape of a real case seen on outside and inside jobs: a form in a frame from an address Autofill does not know, a form
drawn seconds after load, a form inside a web component's shadow root, a form written into an about:blank frame, a
two-step form whose second step comes after Save and Continue, a company site whose job job-hunt-agent knows only by company
and title (JSON-LD), and a posting with Apply but no form. Our own markup, synthetic ZZ_TEST names and the fake person's
labels only; nothing is ever submitted (the forms have no action). Run this file after changing a page below."""
import html
import json
from pathlib import Path

OUT = Path(__file__).parent / "sites"
COMPANY = "ZZ_TEST Prairie Robotics"
TITLE = "Machine Learning Engineer"
STYLE = ("<style>body{font:14px sans-serif;padding:20px 24px 120px;max-width:760px}label{display:block;font-weight:600;"
         "margin-top:10px}input,textarea{width:340px;padding:4px}</style>")


def box(fid: str, label: str, tag: str = "input", kind: str = "text", name: str = "") -> str:
    attrs = f'id="{fid}" name="{name or fid}"'
    control = f'<textarea {attrs} rows="5"></textarea>' if tag == "textarea" else f'<input {attrs} type="{kind}">'
    return f'<label for="{fid}">{html.escape(label)}</label>{control}'


def fields(p: str) -> str:
    """The application's own fields: name, email, phone, resume, an open question and a saved-answer question."""
    return "".join([box(f"{p}first", "First name", name="first_name"), box(f"{p}last", "Last name", name="last_name"),
                    box(f"{p}email", "Email", kind="email", name="email"), box(f"{p}phone", "Phone", kind="tel", name="phone"),
                    box(f"{p}resume", "Resume", kind="file", name="resume"),
                    box(f"{p}why", f"Why do you want to work at {COMPANY}?", "textarea"),
                    box(f"{p}proud", "Tell us about a project you are proud of", "textarea")])


def form(p: str) -> str:
    return f'<form onsubmit="return false">{fields(p)}</form>'


def page(title: str, body: str, head: str = "") -> str:
    return (f'<!doctype html>\n<!-- GOAL36 §5.2 fixture (synthetic, written by make_fixtures.py): {html.escape(title)} -->\n'
            f'<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>{STYLE}{head}</head>'
            f'<body><main>{body}</main><script>window.__ready = true;</script></body></html>\n')


def js(text: str) -> str:
    """A string for an inline script."""
    return json.dumps(text).replace("</", "<\\/")


LD = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": TITLE,
                 "hiringOrganization": {"@type": "Organization", "name": COMPANY},
                 "jobLocation": {"@type": "Place", "address": {"addressLocality": "Lawrence", "addressRegion": "KS"}}})
STEP_1 = ("<h2>Step 1 of 2: Contact</h2>" + box("t-first", "First name", name="first_name")
          + box("t-email", "Email", kind="email", name="email") + box("t-resume", "Resume", kind="file", name="resume")
          + '<button type="button" id="next">Save and Continue</button>')
STEP_2 = ("<h2>Step 2 of 2: Questions</h2>" + box("t-years", "How many years of Python have you used?")
          + box("t-ship", "Describe a time you shipped a model to production", "textarea"))

PAGES = {
    # 1. a form in a frame from an address that is no known ATS (KNOWN_FRAMES): only the frame's own script sees it
    "iframe_top": page("Careers: Machine Learning Engineer",
                       f"<h1>{TITLE}</h1><p>Apply below.</p>"
                       '<iframe id="apply-frame" src="https://forms.zztest-embed.net/embed/apply?id=36001" '
                       'style="width:720px;height:900px;border:1px solid #ccc"></iframe>'),
    "iframe_inner": page("Apply", form("f-")),
    # 2. a form drawn 6 seconds after load (a slow single-page app), after the 4 second settle the panel waited for
    "late": page("Apply: Machine Learning Engineer",
                 f'<h1>{TITLE}</h1><div id="app">Loading the application…</div>'
                 f'<script>setTimeout(() => {{ document.getElementById("app").innerHTML = {js(form("l-"))};'
                 " window.__drawn = true; }, 6000);</script>"),
    # 3. a web component whose open shadow root holds the form, drawn 1.5 seconds after it attaches
    "shadow": page("Apply: Machine Learning Engineer",
                   f"<h1>{TITLE}</h1><zz-apply-form></zz-apply-form>"
                   '<script>customElements.define("zz-apply-form", class extends HTMLElement { connectedCallback() {'
                   ' const root = this.attachShadow({ mode: "open" });'
                   f" setTimeout(() => {{ root.innerHTML = {js(STYLE + form('s-'))}; window.__drawn = true; }}, 1500);"
                   " } });</script>"),
    # 4. a frame with no address (about:blank) whose form the page writes in
    "blank_frame": page("Apply: Machine Learning Engineer",
                        f'<h1>{TITLE}</h1><iframe id="blank" style="width:720px;height:900px"></iframe>'
                        '<script>const d = document.getElementById("blank").contentDocument; d.open();'
                        f" d.write({js('<!doctype html><html><body>' + STYLE + form('b-') + '</body></html>')});"
                        " d.close(); window.__drawn = true;</script>"),
    # 5. two steps: Save and Continue (the user's click) swaps in step 2 a second later, with no field typed in
    "steps": page("Apply: Machine Learning Engineer",
                  f'<h1>{TITLE}</h1><form id="step" onsubmit="return false">{STEP_1}</form>'
                  '<script>window.__clicks = []; document.getElementById("next").addEventListener("click", (e) => {'
                  ' window.__clicks.push(e.isTrusted); setTimeout(() => {'
                  f' document.getElementById("step").innerHTML = {js(STEP_2)}; window.__step = 2; }}, 1000); }});</script>'),
    # 6. the company's own site: its URL is unknown to job-hunt-agent, its JSON-LD names the company and the title
    "unknown_job": page(f"{TITLE} | Careers", f"<h1>{TITLE}</h1><p>Lawrence, KS</p>{form('u-')}",
                        f'<script type="application/ld+json">{LD}</script>'),
    # 7. a posting with Apply and no form at all
    "no_form": page(TITLE, f"<h1>{TITLE}</h1><p>{COMPANY} · Lawrence, KS</p><p>Responsibilities, requirements and "
                           'benefits are listed here.</p><a href="#apply" id="apply">Apply now</a>'),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in PAGES.items():
        (OUT / f"{name}.html").write_text(text)
    print(f"wrote {len(PAGES)} pages to {OUT}")


if __name__ == "__main__":
    main()
