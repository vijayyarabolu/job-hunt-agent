"""GOAL29 §5: which fields of a form the panel offers Generate for (extension/content/answers.js, run in node): open
questions only (a textarea, or a text box taking 200+ characters, with no saved answer, not salary / date / link /
yes-no), the cache lookup asks each question once, polling stops when the answer is done, and every state has an icon
and words."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ANSWERS = Path(__file__).resolve().parent.parent / "extension" / "content" / "answers.js"


def node(expr: str, data) -> object:
    script = f"const a = require({json.dumps(str(ANSWERS))}); const x = JSON.parse(require('fs').readFileSync(0, 'utf8'));" \
             f"console.log(JSON.stringify({expr}))"
    out = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=30,
                         check=True)
    return json.loads(out.stdout)


def row(label, kind="textarea", status="needs", value="", key=None, max_chars=None):
    return {"id": label[:12], "label": label, "kind": kind, "status": status, "value": value, "key": key, "max": max_chars}


@unittest.skipUnless(shutil.which("node"), "needs node")
class OpenQuestionTest(unittest.TestCase):
    def test_open_questions_are_big_empty_unknown_boxes(self):
        cases = [
            (row("Why do you want to work at Acme?"), True),
            (row("Tell us about a project you are proud of", status="skipped"), True),
            (row("Describe a time you disagreed with a teammate", kind="text", max_chars=1000), True),
            (row("Do you have experience with Kubernetes? Please describe it."), True),
            (row("Why do you want to work at Acme?", kind="text", max_chars=120), False),     # a short text box
            (row("Why do you want to work at Acme?", kind="text"), False),                    # no limit: a short box
            # GOAL30 §2: an open question holding text is still one (Regenerate for this job); only the lookup skips it
            (row("Why do you want to work at Acme?", value="Mine."), True),
            (row("Why do you want to work at Acme?", status="filled", value="Saved."), True),
            (row("Cover letter", key="cover_letter"), False),                                 # its own tool
            (row("What are your salary expectations?"), False),
            (row("Earliest start date"), False),
            (row("Link to your portfolio or GitHub"), False),
            (row("Are you willing to relocate?"), False),                                     # yes / no
            (row("Notes"), False),                                                            # too short to be a question
            (row("Why this role?", kind="select"), False),
        ]
        got = node("x.map(a.isOpen)", [c[0] for c in cases])
        for (case, want), result in zip(cases, got):
            with self.subTest(case["label"], kind=case["kind"]):
                self.assertEqual(result, want)

    def test_each_open_question_is_looked_up_once(self):
        rows = [row("Why do you want to work at Acme?"), row("Why do you want to work at Acme?"),
                row("Tell us about a project you are proud of"), row("Earliest start date")]
        got = node("[a.toLookUp(x, new Set()), a.toLookUp(x, new Set(['Why do you want to work at Acme?']))]", rows)
        self.assertEqual(got, [["Why do you want to work at Acme?", "Tell us about a project you are proud of"],
                               ["Tell us about a project you are proud of"]])

    def test_a_filled_open_question_is_checked_for_whose_job_it_was_written_once(self):
        rows = [row("Why do you want to work at Acme?", value="I loved Globex."), row("Tell us about a project you are proud of"),
                row("Earliest start date", value="2 weeks")]
        got = node("[a.filledToCheck(x, new Set()), a.filledToCheck(x, new Set(['Why do you want to work at Acme?\\u0000I loved Globex.']))]", rows)
        self.assertEqual(got, [{"Why do you want to work at Acme?": "I loved Globex."}, {}])

    def test_polling_runs_while_writing_slows_down_while_paused_and_stops_when_done(self):
        states = ["writing", "limit", "ready", "failed", "login"]
        got = node("x.map((s) => a.waitFor({ state: s }))", states)
        self.assertEqual(got, [1000, 60000, 0, 0, 0])                 # GOAL30 §5: every second while writing

    def test_every_state_has_an_icon_and_words(self):
        views = [{"state": "writing", "label": "Writing with Claude…"}, {"state": "ready", "label": "Ready"},
                 {"state": "limit", "label": "Claude usage limit, resets at 6 pm"},
                 {"state": "login", "label": "Not logged in to Claude"},
                 {"state": "failed", "label": "No answer", "error": "No answer that passes the checks: 210 words; write 80 to 150"}]
        got = node("x.map((v) => [a.LOOK[v.state], a.words(v)])", views)
        self.assertEqual(got, [["⟳", "Writing with Claude…"], ["✓", "Ready"], ["⏸", "Claude usage limit, resets at 6 pm"],
                               ["⚠", "Not logged in to Claude"], ["✕", "No answer: 210 words; write 80 to 150"]])


if __name__ == "__main__":
    unittest.main()
