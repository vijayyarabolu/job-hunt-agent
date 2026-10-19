"""Server HTML moved out of the JavaScript budget: the Stats view (stats_html) and the resume panel's ATS +
diff blocks (rg_html). Every value is escaped; bars carry their scale in data-s (the CSP blocks inline styles)."""
import unittest

from job_hunt_agent import rg_html, stats_html

EVIL = '<img src=x onerror="alert(1)">'


def _report(**extra) -> dict:
    rate = {"label": EVIL, "n": 4, "responses": 1, "rate": 0.25}
    return {"applications": 8, "responses": 2, "rate": 0.25, "this_week": 3,
            "weekly": [{"week": "2026-09-28", "n": 2}, {"week": "2026-10-05", "n": 4}],
            "by_family": [rate, {"label": "ML Engineer", "n": 2, "responses": 0, "rate": 0.0}], "by_size": [],
            "by_days_posted": [], "posting_dated": 5, "median_days_to_response": 2.5, "n_timed_responses": 2,
            "median_days_to_rejection": None, "n_timed_rejections": 0, "status_counts": {"applied": 6, "screen": 2},
            **extra}


class StatsHtmlTest(unittest.TestCase):
    def test_tiles_rates_and_bars(self):
        page = stats_html.render(_report())
        self.assertIn('<div class="tile-value">25%</div>', page)
        self.assertIn("2.5 d", page)
        self.assertIn("Applied 6 · Screen 2", page)
        self.assertIn('data-ax="Y" data-s="0.5000"', page)            # 2 of the busiest week's 4
        self.assertIn('data-ax="X" data-s="1.0000"', page)
        self.assertIn("none timed yet", page)

    def test_every_value_is_escaped(self):
        page = stats_html.render(_report())
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)

    def test_phone_labels_fewer_weeks(self):
        weeks = [{"week": f"2026-{1 + i // 4:02d}-{1 + 7 * (i % 4):02d}", "n": 1} for i in range(26)]
        desk = stats_html.render(_report(weekly=weeks)).count('<span class="wlabel"></span>')
        phone = stats_html.render(_report(weekly=weeks), phone=True).count('<span class="wlabel"></span>')
        self.assertGreater(phone, desk)

    def test_rates_round_half_up_like_the_old_view(self):
        self.assertEqual(stats_html._pct(0.0625), "6.3%")                  # 1 of 16 (Python's round() said 6.2)
        self.assertEqual(stats_html._pct(None), "—")


class ResumePanelHtmlTest(unittest.TestCase):
    def test_odd_data_still_renders(self):
        self.assertIn("Still missing: 3, K8s", rg_html.ats({"base_pct": 1, "tailored_pct": 2, "missing": [3, "K8s"]}))
        html = rg_html.diff({"headline": {"changed": True, "ops": [["equal", "A"], "junk", ["insert", None, 7]]}})
        self.assertIn("<span>A <ins>7</ins></span>", html)

    def test_diff_marks_changes_and_escapes(self):
        diff = {"headline": {"changed": True, "ops": [["equal", "ML", ""], ["replace", "Engineer", EVIL]]},
                "summary": {"changed": False, "ops": []},
                "jobs": [{"company": "Acme", "bullets": [{"changed": True, "ops": [["delete", "old", ""]]}]}],
                "changed_bullets": 1, "total_bullets": None}
        html = rg_html.diff(diff)
        self.assertIn("ML <del>Engineer</del><ins>&lt;img", html)
        self.assertIn('<b class="rg-k">Acme · bullet 1</b><span><del>old</del></span>', html)
        self.assertIn("(1 of ? bullets)", html)
        self.assertNotIn("Summary", html)
        self.assertNotIn("<img", html)

    def test_empty_diff_and_ats(self):
        self.assertEqual(rg_html.diff(None), "")
        self.assertIn("No changes.", rg_html.diff({"changed_bullets": 0, "total_bullets": 3}))
        self.assertEqual(rg_html.ats(None), "")
        ats = rg_html.ats({"base_pct": 61, "tailored_pct": 78, "label": "Good", "missing": ["K8s", EVIL]})
        self.assertIn("<b>61%</b> base → <b>78%</b> tailored", ats)
        self.assertIn("Still missing: K8s, &lt;img", ats)


if __name__ == "__main__":
    unittest.main()
