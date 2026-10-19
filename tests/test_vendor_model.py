"""GOAL5 §3 (decision 010): each job's resumes scored the way its ATS vendor documents evaluating candidates.
Every feature is off unless a cited fact turns it on; numbers that are ours are labeled; an undocumented vendor keeps
the generic keyword % under an honest label."""
import json
import tempfile
import unittest
from pathlib import Path

from job_hunt_agent import config, keywords, lexicon, vendor_model
from job_hunt_agent.resume import ats
from job_hunt_agent.resumes import Resume, normalized

SRC = "https://vendor.example/docs"
YAML = f"""
literalco:
  label: LiteralCo
  ranking:
  - {{id: lc-exact, text: Recruiter search matches exact keywords., source: {SRC}/exact}}
  parsing:
  - {{id: lc-tables, text: Tables break parsing., source: {SRC}/tables}}
  - {{id: lc-size, text: Files over 2 MB are not parsed., source: {SRC}/size}}
  questions_api: {{exposed: false, how: none, source: {SRC}/api}}
  model:
    detect_hosts: [".literalco.example"]
    channels:
    - {{name: Keyword search, cite: [lc-exact], skills: literal}}
    parse_checks:
      tables: {{cite: [lc-tables], effect: degrade}}
      max_mb: {{value: 2, cite: [lc-size], effect: block}}
smartco:
  label: SmartCo
  ranking:
  - {{id: sc-ai, text: AI matching infers related skills and weighs required skills, titles and years., source: {SRC}/ai}}
  - {{id: sc-grade, text: Grades A to D on basic and preferred qualifications., source: {SRC}/grade}}
  questions_api: {{exposed: false, how: none, source: {SRC}/api}}
  model:
    channels:
    - name: AI matching
      cite: [sc-ai]
      skills: taxonomy
      related: [sc-ai]
      required_weight: [sc-ai]
      title: [sc-ai]
      years: [sc-ai]
      grade: [sc-grade]
needco:
  label: NeedCo
  ranking:
  - {{id: nc-need, text: The AI judges only the skills a job calls necessary., source: {SRC}/need}}
  questions_api: {{exposed: false, how: none, source: {SRC}/api}}
  model:
    channels:
    - {{name: Necessary skills, cite: [nc-need], skills: taxonomy, preferred_zero: [nc-need]}}
quietco:
  label: QuietCo
  ranking: []
  questions_api: {{exposed: false, how: none, source: {SRC}/api}}
"""


def resume(rid: str, text: str, families=("ml_engineer",), years=4.0, risks=(), size=0.02) -> Resume:
    return Resume(rid, rid.upper(), f"{rid}.docx", tuple(families), frozenset(lexicon.find(text)), rid,
                  normalized(text), years, tuple(risks), size)


def row(jd: str, resumes, board="literalco:acme", families=",ml_engineer,", min_years=None, apply_url=None) -> dict:
    terms = ats.extract(jd)
    packed = keywords.pack(terms, tuple(r.terms for r in resumes), tuple(r.text for r in resumes))
    return {"fit_terms": json.dumps(packed), "board": board, "families": families, "min_years": min_years,
            "apply_url": apply_url, "url": None}


JD = "Requirements:\n- LLMs in production\n- AWS SageMaker\n- Kubernetes\nNice to have:\n- PyTorch"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ats_profiles.yaml"
        self.path.write_text(YAML)
        self.models = vendor_model.models(str(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    def scored(self, r: dict, resumes, years=None):
        return vendor_model.score(r, resumes, years, self.models)


class ConfigTest(Case):
    def test_a_feature_without_a_cite_is_refused(self):
        self.path.write_text(YAML.replace("cite: [lc-exact], skills", "cite: [], skills"))
        with self.assertRaises(config.ConfigError):
            vendor_model.models(str(self.path))

    def test_a_cite_must_name_a_fact_of_that_vendor(self):
        self.path.write_text(YAML.replace("cite: [lc-tables]", "cite: [sc-ai]"))
        with self.assertRaises(config.ConfigError):
            vendor_model.models(str(self.path))

    def test_unknown_skills_mode_or_effect_is_refused(self):
        self.path.write_text(YAML.replace("skills: literal", "skills: fuzzy"))
        with self.assertRaises(config.ConfigError):
            vendor_model.models(str(self.path))

    def test_vendor_without_model_has_none(self):
        self.assertEqual(set(self.models), {"literalco", "smartco", "needco"})


class LiteralTest(Case):
    def test_only_the_jds_own_wording_counts(self):
        # canonical match on all four skills; literally "LLMs" and "AWS SageMaker" are absent
        rs = (resume("ml", "Built LLM apps on SageMaker and Kubernetes with PyTorch"),)
        got = self.scored(row(JD, rs), rs)
        one = got.scores[0]
        self.assertEqual(got.vendor, "literalco")
        self.assertEqual(one.generic, 100)
        self.assertEqual(one.pct, 50)                       # Kubernetes + PyTorch of 4, every term weighs the same
        self.assertIn("AWS SageMaker", one.missing)          # the JD's exact words, not the canonical name
        self.assertIn("Kubernetes", one.matched)

    def test_case_is_ignored_and_word_bounds_hold(self):
        rs = (resume("ml", "llms, aws sagemaker, KUBERNETES, pytorch"),)
        self.assertEqual(self.scored(row(JD, rs), rs).scores[0].pct, 100)

    def test_a_documented_block_sets_the_score_to_zero(self):
        rs = (resume("ml", "LLMs AWS SageMaker Kubernetes PyTorch", size=3.0),)
        one = self.scored(row(JD, rs), rs).scores[0]
        self.assertEqual(one.pct, 0)
        self.assertIn("max_mb", one.parse_fired)

    def test_a_degrade_check_costs_ten_percent(self):
        rs = (resume("ml", "LLMs AWS SageMaker Kubernetes PyTorch", risks=("tables", "images")),)
        one = self.scored(row(JD, rs), rs).scores[0]
        self.assertEqual(one.pct, 90)                        # images is not a LiteralCo check
        self.assertEqual(one.parse_fired, ("tables",))

    def test_apply_host_picks_the_vendor(self):
        rs = (resume("ml", "Kubernetes"),)
        got = self.scored(row(JD, rs, board="greenhouse:acme", apply_url="https://acme.literalco.example/job/1"), rs)
        self.assertEqual(got.vendor, "literalco")


class TaxonomyTest(Case):
    def test_aliases_related_skills_required_weight_title_and_years(self):
        rs = (resume("ml", "LLM, SageMaker, TensorFlow", years=2.0),)          # no Kubernetes, PyTorch via TensorFlow
        one = self.scored(row(JD, rs, board="smartco:acme", min_years=4), rs).scores[0]
        skills = (2 + 0.5 * 0.5) / 3.5        # required 1+1+0 of 3; preferred PyTorch related 0.5 at weight 0.5
        self.assertEqual(one.pct, round(100 * (0.6 * skills + 0.2 * 1 + 0.2 * 0.5)))
        self.assertIn("PyTorch (~ via TensorFlow)", one.matched)
        self.assertEqual(one.grade, "C")                     # most (2 of 3) basic, not all

    def test_years_drop_out_without_a_minimum(self):
        rs = (resume("ml", "LLM SageMaker Kubernetes PyTorch", families=("data_engineer",)),)
        one = self.scored(row(JD, rs, board="smartco:acme"), rs).scores[0]
        self.assertEqual(one.pct, round(100 * 0.6 / 0.8))    # title 0 (other family), years dropped
        self.assertEqual(one.grade, "A")


class NotApplicableTest(Case):
    def test_a_jd_with_only_preferred_keywords_keeps_the_generic_score_under_a_necessary_only_channel(self):
        rs = (resume("ml", "PyTorch"),)
        got = self.scored(row("Nice to have:\n- PyTorch\n- Kubernetes", rs, board="needco:acme"), rs)
        one = got.scores[0]
        self.assertEqual((one.pct, one.generic), (50, 50))
        text = "\n".join(vendor_model.explain(got, one))
        self.assertIn("not applicable", text)
        self.assertIn(f"{SRC}/need", text)
        self.assertIn("= Estimated NeedCo match 50%", text)

    def test_required_keywords_still_drive_the_channel(self):
        rs = (resume("ml", "PyTorch"),)
        got = self.scored(row("Requirements:\n- Kubernetes\nNice to have:\n- PyTorch", rs, board="needco:acme"), rs)
        self.assertEqual(got.scores[0].pct, 0)


class FallbackTest(Case):
    def test_undocumented_vendor_keeps_the_generic_score_and_says_so(self):
        rs = (resume("ml", "LLM SageMaker"),)
        got = self.scored(row(JD, rs, board="quietco:acme"), rs)
        self.assertIsNone(got.model)
        self.assertEqual(got.scores[0].pct, got.scores[0].generic)
        self.assertIn("not publicly documented", got.label)
        self.assertNotIn("Estimated", got.label)

    def test_label_says_estimated_for_a_modeled_vendor(self):
        rs = (resume("ml", "LLM"),)
        got = self.scored(row(JD, rs), rs)
        self.assertEqual(got.label, "Estimated LiteralCo match (modeled on documented behavior)")

    def test_a_row_built_before_decision_010_has_no_literal_score(self):
        rs = (resume("ml", "Kubernetes"),)
        r = row(JD, rs)
        r["fit_terms"] = json.dumps([t[:6] for t in json.loads(r["fit_terms"])])
        self.assertIsNone(self.scored(r, rs).scores[0].pct)

    def test_best_is_the_highest_vendor_score(self):
        rs = (resume("ai", "LLM SageMaker Kubernetes PyTorch"), resume("ml", "LLMs AWS SageMaker Kubernetes"))
        got = self.scored(row(JD, rs), rs)
        self.assertEqual(got.best.id, "ml")


class ExplainTest(Case):
    def test_steps_go_from_generic_to_vendor_with_cites(self):
        rs = (resume("ml", "Built LLM apps on SageMaker and Kubernetes with PyTorch"),)
        got = self.scored(row(JD, rs), rs)
        text = "\n".join(vendor_model.explain(got, got.scores[0]))
        self.assertIn("generic keyword match 100%", text)
        self.assertIn('JD says "LLMs"', text)
        self.assertIn(f"{SRC}/exact", text)
        self.assertIn("= Estimated LiteralCo match 50%", text)


if __name__ == "__main__":
    unittest.main()
