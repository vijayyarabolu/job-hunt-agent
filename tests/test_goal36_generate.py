"""GOAL36 §5.3 / §5.4: Generate never ends in silence and is offered on a saved answer too (extension/content/answers.js,
run in node). Every way it ends without an answer is words + an icon + Retry: not linked to a job (with Link to a job),
an empty answer (a cache hit with no text included), no reply, the usage limit, no login, a stopped worker. A box the user's
saved answer filled offers "Generate for this job", and the saved answer stays filled. The resumegen half (queued
words, empty rows, no double ask) is tests/test_goal36_answers.py in resumegen."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ANSWERS = Path(__file__).resolve().parent.parent / "extension" / "content" / "answers.js"
# a fake el / btn: the block as a tree of {text | btn}, so the test reads its words and buttons
FAKE_DOM = ("const el = (tag, attrs, ...kids) => ({ cls: (attrs || {}).class || '', kids: kids.flat().filter(Boolean) });"
            "const btn = (label) => ({ btn: label });"
            "const flat = (n) => typeof n === 'string' ? [n] : n.btn ? ['[' + n.btn + ']'] : n.kids.flatMap(flat);"
            "const h = { generate() {}, copy() {}, fill() {}, save() {}, link() {} };")


def node(expr: str, data) -> object:
    script = (f"const a = require({json.dumps(str(ANSWERS))}); const x = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              f"{FAKE_DOM} console.log(JSON.stringify({expr}))")
    out = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=30,
                         check=True)
    return json.loads(out.stdout)


def row(label, kind="textarea", value="", key=None, saved=False, max_chars=None):
    return {"id": label[:12], "label": label, "kind": kind, "status": "filled" if value else "needs", "value": value,
            "key": key, "saved": saved, "max": max_chars}


@unittest.skipUnless(shutil.which("node"), "needs node")
class SavedAnswerTest(unittest.TestCase):
    """§5.4: Generate for this job next to a saved answer."""

    def test_a_box_his_saved_answer_filled_offers_generate(self):
        cases = [
            (row("Why do you want to work here?", value="My saved why.", key="q:why_here", saved=True), True),
            (row("Tell us about a project you are proud of", value="Saved story.", saved=True), True),
            (row("Why do you want to work here?", value="He typed it."), True),                       # GOAL30 §2
            (row("Cover letter", value="Dear team", key="cover_letter", saved=True), False),           # its own tool
            (row("What are your salary expectations?", value="$120,000", key="salary", saved=True), False),
            (row("Earliest start date", value="2 weeks", key="start_date", saved=True), False),
            (row("City", kind="text", value="Austin", key="city", saved=True), False),               # a short box
        ]
        got = node("x.map(a.canWrite)", [c[0] for c in cases])
        for (case, want), result in zip(cases, got):
            with self.subTest(case["label"]):
                self.assertEqual(result, want)

    def test_its_first_button_is_generate_for_this_job_and_the_saved_answer_is_kept(self):
        saved = row("Why do you want to work here?", value="My saved why.", key="q:why_here", saved=True)
        got = node("flat(a.filledBlock(x, null, h, el, btn))", saved)
        self.assertEqual(got[1:3], ["Your saved answer is filled", "[Generate for this job]"])
        self.assertIn("[Save to Answers]", got)                     # the only way the saved answer changes
        self.assertNotIn("[Regenerate for this job]", got)
        typed = row("Why do you want to work here?", value="He typed it.")
        self.assertIn("[Regenerate for this job]", node("flat(a.filledBlock(x, null, h, el, btn))", typed))

    def test_the_answer_written_for_this_job_is_looked_up_for_a_saved_box_too(self):
        rows = [row("Why do you want to work here?", value="My saved why.", key="q:why_here", saved=True),
                row("Tell us about a project you are proud of"), row("Earliest start date", value="2 weeks")]
        self.assertEqual(node("a.toLookUp(x, new Set())", rows),
                         ["Why do you want to work here?", "Tell us about a project you are proud of"])


@unittest.skipUnless(shutil.which("node"), "needs node")
class NeverSilentTest(unittest.TestCase):
    """§5.3: each cause found ends in words, an icon and Retry."""

    def test_an_empty_answer_or_no_reply_is_a_failure_in_words(self):
        got = node("[a.settle({ id: 4, state: 'ready', text: '  ', label: 'Ready' }, 'Q'), a.settle(null, 'Q'),"
                   " a.settle({ state: 'ready', text: 'Fine.' })]", None)
        self.assertEqual((got[0]["state"], got[0]["error"]), ("failed", "Claude wrote an empty answer"))
        self.assertEqual((got[1]["state"], got[1]["error"]), ("failed", "the Resume app sent no answer back"))
        self.assertEqual(got[2]["state"], "ready")

    def test_every_ending_without_an_answer_offers_retry_with_an_icon_and_words(self):
        views = {
            "not linked": {"state": "failed", "error": "this page is not linked to a job yet, so Claude has no JD to "
                                                       "write from: link it, then Retry", "unlinked": True},
            "empty": {"state": "ready", "text": "", "label": "Ready"},
            "job-hunt-agent down": {"state": "failed", "error": "job-hunt-agent did not answer (is it running?)"},
            "too long": {"state": "failed", "error": "Not written after 20 minutes: the Resume app may be stopped or "
                                                     "busy with resumes. Retry asks again"},
            "limit": {"state": "limit", "label": "Claude usage limit, resets at 6 pm"},
            "login": {"state": "login", "label": "Not logged in to Claude"},
            "no worker": {"state": "writing", "stalled": True,
                          "label": "Waiting: the Resume app's worker is not running, so nothing writes it yet"},
        }
        got = node("Object.fromEntries(Object.entries(x).map(([k, v]) => [k, flat(a.block(v, h, el, btn))]))", views)
        for name, parts in got.items():
            with self.subTest(name):
                self.assertIn(parts[0], "✕⚠⏸", parts)                   # an icon
                self.assertGreater(len(parts[1]), 10, parts)              # words
                self.assertIn("[Retry]", parts)
        self.assertIn("[Link to a job]", got["not linked"])
        self.assertIn("No answer: Claude wrote an empty answer", got["empty"])
        self.assertIn("it writes on its own after that", got["limit"][1])

    def test_writing_and_ready_offer_no_retry(self):
        got = node("x.map((v) => a.retries(v))", [{"state": "writing"}, {"state": "writing", "queued": True},
                                                   {"state": "ready", "text": "x"}])
        self.assertEqual(got, [False, False, False])
        self.assertEqual(node("a.block(x, h, el, btn).kids.length", {"state": "writing", "label": "Writing with Claude…"}),
                         1)                                                 # the status line only

    def test_no_dash_punctuation_in_the_words(self):
        strings = [s for pair in re.findall(r'"([^"\n]{12,})"|`([^`\n]{12,})`', ANSWERS.read_text()) for s in pair if s]
        self.assertFalse([s for s in strings if "—" in s or "–" in s or " - " in s], "UI text uses a dash")


if __name__ == "__main__":
    unittest.main()
