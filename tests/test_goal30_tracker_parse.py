"""GOAL30-app §1 #6: the Tracker list parsed a row's stored sheet cells (raw_json) and its edited-fields list again for
every cell: 54,000 json.loads per call for 1,022 rows (p50 435 ms). Each text is now parsed once."""
import json
import unittest
from unittest import mock

from job_hunt_agent import tracker
from job_hunt_agent.tracker import FIELDS


def _app(company="Acme", edited=("role_title",)):
    raw = [tracker.encode(None) for _ in FIELDS]
    raw[FIELDS.index("company")] = tracker.encode(company)
    raw[FIELDS.index("role_title")] = tracker.encode("Typed Title")
    return {**{f: None for f in FIELDS}, "company": "App Company", "role_title": "App Title",
            "raw_json": json.dumps(raw), "edited": json.dumps(list(edited)), "xlsx_row": 2}


class StoredCellsTest(unittest.TestCase):
    def setUp(self):
        tracker.stored_cells.cache_clear()
        tracker.edited_fields.cache_clear()

    def test_a_row_is_parsed_once_for_all_its_cells(self):
        app = _app()
        real = json.loads
        with mock.patch.object(tracker.json, "loads", side_effect=real) as parse:
            for _ in range(3):
                [tracker._cell_value(app, i) for i in range(len(FIELDS))]
        self.assertEqual(parse.call_count, 2)                     # raw_json once, edited once

    def test_values_are_as_before(self):
        app = _app()
        self.assertEqual(tracker._cell_value(app, FIELDS.index("company")), ("Acme", False))       # typed cell
        self.assertEqual(tracker._cell_value(app, FIELDS.index("role_title")), ("App Title", False))  # edited in app
        self.assertIsNone(tracker._cell_link(app, FIELDS.index("role_title"), {}))

    def test_a_changed_row_is_read_again(self):
        tracker._cell_value(_app("Acme"), FIELDS.index("company"))
        self.assertEqual(tracker._cell_value(_app("Globex"), FIELDS.index("company"))[0], "Globex")



def _compact_before(row: dict) -> dict:                 # trackapi.compact as it was (two _clip calls per value)
    from job_hunt_agent.trackapi import _clip
    cut = [k for k, v in row.items() if v != _clip(v)]
    out = {k: _clip(v) for k, v in row.items()}
    if row.get("sheet"):
        cells = row["sheet"]["cells"]
        cut += [f"sheet:{i}" for i, v in enumerate(cells) if v != _clip(v)]
        out["sheet"] = {**row["sheet"], "cells": [_clip(v) for v in cells]}
    if row.get("extras"):
        out["extras"] = {k: _clip(v) for k, v in row["extras"].items()}
    return {**out, "clipped": cut}


class CompactOnceTest(unittest.TestCase):
    """GOAL30-app second pass: the Tracker list (2.5 MB, 1,022 rows, p95 202 ms) clipped every value twice."""

    def test_the_rows_are_cut_exactly_as_before(self):
        from job_hunt_agent import trackapi
        long, short = "x" * 300, "short"
        rows = [{"id": 1, "notes": long, "company": short, "salary": None, "n": 3.5},
                {"id": 2, "jd_text": long, "sheet": {"tab": 0, "cells": [short, long, None, 7]}, "extras": {"Team": long}},
                {"id": 3, "sheet": {"tab": 1, "cells": []}, "extras": {}}]
        for row in rows:
            self.assertEqual(trackapi.compact(row), _compact_before(row))

    def test_each_value_is_clipped_once(self):
        from job_hunt_agent import trackapi
        row = {"a": "x" * 300, "b": "y", "sheet": {"cells": ["z" * 300, "w"]}}
        with mock.patch.object(trackapi, "_clip", side_effect=trackapi._clip) as clip:
            trackapi.compact(row)
        self.assertEqual(clip.call_count, len(row) + 2)


if __name__ == "__main__":
    unittest.main()
