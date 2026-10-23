"""GOAL33 §3d "consistent spacing (8 px grid)": every padding, margin and gap in the app's CSS (the style sheets and the
styles written in JS) is a multiple of 4 px, the half step of the 8 px grid; 1 and 2 px hairlines are allowed. There
were 192 values off it (6, 10, 14 px ...)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "job_hunt_agent" / "static"
PROPS = r"(?:padding|margin)(?:-(?:top|bottom|left|right|block|inline)(?:-(?:start|end))?)?|(?:row-|column-)?gap"
DECL = re.compile(rf"(?<![\w-])(?:{PROPS})\s*:\s*([^;{{}}\"`\n]+)")
HAIRLINE = 2


def off_grid(text: str) -> list[str]:
    return [v for m in DECL.finditer(text) for v in re.findall(r"(-?\d+(?:\.\d+)?)px", m.group(1))
            if abs(float(v)) > HAIRLINE and float(v) % 4]


class GridTest(unittest.TestCase):
    def test_spacing_is_on_the_grid(self):
        files = sorted(STATIC.glob("*.css")) + sorted((STATIC / "js").glob("*.js"))
        for f in files:
            with self.subTest(file=f.name):
                self.assertEqual(off_grid(f.read_text()), [])

    def test_the_check_sees_an_off_grid_value(self):
        self.assertEqual(off_grid(".a { padding: 6px 8px; gap: 2px; margin-top: -14px }"), ["6", "-14"])


if __name__ == "__main__":
    unittest.main()
