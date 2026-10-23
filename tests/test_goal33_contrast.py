"""GOAL33 §3d: both themes pass WCAG AA for text (a headless audit of every page found the Tracker's links at 2.5:1 in
dark, the "resume missing" tag at 4.0:1 in light). The links follow the theme; the light warning colour is darker."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "job_hunt_agent" / "static"
AA = 4.5


def luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block: str) -> dict:
    return dict(re.findall(r"--([\w-]+): (#[0-9a-f]{6})", block))


class ContrastTest(unittest.TestCase):
    def setUp(self):
        css = (STATIC / "ds.css").read_text()
        self.light = tokens(css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")])
        self.dark = tokens(css[css.index(':root[data-theme="dark"]'):])

    def test_the_warning_colour_reads_on_paper_and_on_raised(self):
        for theme in (self.light, self.dark):
            for ground in ("paper", "raised"):
                with self.subTest(signal=theme["signal"], ground=ground):
                    self.assertGreaterEqual(ratio(theme["signal"], theme[ground]), AA + 1)   # the tag's 16% tint costs about 1

    def test_tracker_links_follow_the_theme(self):
        rule = re.search(r"\.g \.g-link \{ color: light-dark\((#\w{6}), (#\w{6})\)", (STATIC / "js" / "gridcss.js").read_text())
        self.assertIsNotNone(rule, "the grid's link colour is one colour for both themes")
        light, dark = rule.groups()
        self.assertGreaterEqual(ratio(light, "#ffffff"), AA)
        self.assertGreaterEqual(ratio(dark, self.dark["raised"]), AA)


if __name__ == "__main__":
    unittest.main()


class StatusShapeTest(unittest.TestCase):
    """For colour-blind users: every status chip (Tracker cards and grid) shows a shape beside its word; the shape is CSS
    (::before), so copying a cell or reading its text gives the word only."""
    def test_every_status_has_a_shape(self):
        labels = re.search(r"export const STATUS_LABELS = \{([^}]*)\}", (STATIC / "js" / "core.js").read_text()).group(1)
        css = (STATIC / "app.css").read_text()
        shaped = {m for rule in re.findall(r"([^{}]+)\{ content: \"[^\"]+\" \}", css) for m in re.findall(r"\.st-(\w+)::before", rule)}
        for status in re.findall(r"(\w+): \"", labels):
            with self.subTest(status=status):
                self.assertIn(status, shaped)
