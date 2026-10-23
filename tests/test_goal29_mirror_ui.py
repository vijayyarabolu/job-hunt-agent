"""GOAL29 §1 in job-hunt-agent's resume panel: each bullet resumegen mirrored from the JD carries a tag with an icon and the
words "Mirrored from JD: <responsibility>", and a line above the diff says "JD responsibilities covered: 3 of 5" with
the uncovered ones as gaps in plain words. Every value is escaped; no mirror data, no line and no tags."""
import unittest

from job_hunt_agent import rg_html

DIFF = {"headline": {"changed": False}, "summary": {"changed": False}, "skills": [],
        "jobs": [{"company": "Globex", "bullets": [{"changed": True, "ops": [["equal", "Built"], ["insert", None, "eval pipelines"]]},
                                                {"changed": True, "ops": [["replace", "a", "b"]]}]},
                 {"company": "Deloitte", "bullets": [{"changed": True, "ops": [["insert", None, "partnering"]]}]}],
        "changed_bullets": 3, "total_bullets": 3}
MIRROR = {"covered": 2, "total": 5, "unmatched": ["mentor <juniors>", "on-call rotation", "work in a regulated environment"],
          "mirrored": [{"responsibility": "build evaluation pipelines", "at": [0, 0]},
                       {"responsibility": "partner with product & data science", "at": [1, 0]}]}


class MirrorTags(unittest.TestCase):
    def test_tags_sit_on_the_mirrored_bullets(self):
        html = rg_html.diff(DIFF, MIRROR)
        self.assertEqual(html.count('class="rg-mirror"'), 2)
        self.assertIn("Mirrored from JD: build evaluation pipelines</span></li>", html)
        self.assertIn("Mirrored from JD: partner with product &amp; data science", html)
        self.assertEqual(html.count('href="#i-mirror"'), 3)                     # an icon on each tag + the line

    def test_coverage_line_and_gaps_in_plain_words(self):
        html = rg_html.diff(DIFF, MIRROR)
        self.assertIn("JD responsibilities covered: <b>2 of 5</b>", html)
        self.assertIn("Not covered, no bullet does this work: mentor &lt;juniors&gt;; on-call rotation; work in a "
                      "regulated environment", html)
        self.assertLess(html.index("rg-cover"), html.index("<ul>"))

    def test_without_mirror_data_nothing_changes(self):
        self.assertEqual(rg_html.diff(DIFF), rg_html.diff(DIFF, None))
        self.assertNotIn("rg-mirror", rg_html.diff(DIFF))
        self.assertNotIn("covered", rg_html.diff(DIFF, {"total": 0}))

    def test_ui_words_have_no_dashes(self):
        text = rg_html.diff(DIFF, MIRROR).replace("on-call", "")              # the JD's own word, escaped as is
        self.assertNotRegex(text, "[—–]| - ")
