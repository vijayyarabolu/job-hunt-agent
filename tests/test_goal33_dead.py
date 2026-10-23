"""GOAL33 §3c: code of features the user dropped is gone. Draft (GOAL23 §5) was replaced by Generate in GOAL29 (decision
18) and its endpoint was kept only for extension builds older than 1.4; target_pay (the Offer check's Pay row) was
never set in either profile and the user dropped it."""
import unittest
from pathlib import Path

from job_hunt_agent import offer
from job_hunt_agent.autofill import api as afapi, draft

ROOT = Path(__file__).resolve().parent.parent


class DroppedFeaturesTest(unittest.TestCase):
    def test_no_draft_endpoint(self):
        with self.assertRaises(KeyError):
            afapi.post(None, "alex", "/api/autofill/draft", {"question": "Why?"}, None)
        self.assertFalse(hasattr(draft, "draft"))

    def test_no_pay_row_in_the_offer_check(self):
        self.assertFalse(hasattr(offer, "pay"))
        self.assertNotIn("Pay", offer.SHORT)
        self.assertNotIn("target_pay", (ROOT / "job_hunt_agent" / "offer.py").read_text())
