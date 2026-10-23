"""GOAL28 §4: matching by JD content. A Data Scientist (or generic) title stays in a person's set only when its JD's
required / preferred lines name 2+ distinct GenAI terms (profile.yaml jd_gate); the GenAI part of the match sorts and
never blocks; a person without a gate keeps exactly the same rows and hashes; `genai-backfill` re-judges the
existing rows with counts before / after."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, fits, genai, genai_backfill, match, resumes, store, titles
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
GATE = {"families": ["data_scientist"], "generic_titles": True, "min_terms": 2}
GENAI_JD = ("About Acme\nAcme builds software for banks.\nRequirements\n"
            "- 3+ years building RAG systems with LangChain and vector databases in Python.\n"
            "- Experience shipping LLM features to production on AWS.\n")
PLAIN_JD = ("About Acme\nAcme is a generative AI company that loves LLMs and agents.\nRequirements\n"
            "- 3+ years of SQL, Python and A/B testing for marketing analytics.\n"
            "- Build forecasting and regression models with Tableau dashboards.\n"
            "Benefits\n- Agents of change welcome. MCP and RAG lunch talks.\n")
ONE_TERM_JD = "Requirements\n- 3+ years of Python and SQL; experience with LLM evaluation is a plus.\n- Statistics.\n"
GATED = """name: Gate Tester
families: [ai_engineer, genai_engineer, ml_engineer, data_scientist]
years: 4
jd_gate: {families: [data_scientist], generic_titles: true, min_terms: 2}
"""
UNGATED = """name: No Gate
families: [ai_engineer, genai_engineer, ml_engineer, data_scientist, data_engineer]
years: 4
"""


class TermsTest(unittest.TestCase):
    def test_terms_come_from_the_required_lines_only(self):
        self.assertEqual(genai.terms(GENAI_JD), ["LLM", "RAG", "LangChain", "vector database"])
        self.assertEqual(genai.terms(PLAIN_JD), [])          # the blurb and the benefits never count

    def test_spellings_of_one_idea_count_once(self):
        jd = "Requirements\n- Generative AI (GenAI) work with large language models and LLMs.\n"
        self.assertEqual(genai.terms(jd), ["LLM", "generative AI"])

    def test_agents_of_change_is_not_agentic_work(self):
        self.assertEqual(genai.terms("Requirements\n- Be one of our agents of change in Python.\n"), [])
        self.assertEqual(genai.terms("Requirements\n- Build agentic workflows and AI agents in Python.\n"),
                         ["agents"])

    def test_rag_and_mcp_must_be_capitals(self):
        self.assertEqual(genai.terms("Requirements\n- Rag rug and mcp in lower case.\n"), [])
        self.assertEqual(genai.terms("Requirements\n- RAG and MCP servers.\n"), ["RAG", "MCP"])

    def test_every_listed_term_is_found(self):
        jd = ("Requirements\n- LLMs, generative AI, retrieval-augmented generation, agents, prompt engineering, "
              "fine-tuning, LangChain, LangGraph, vector stores, embeddings, Model Context Protocol, tool calling.\n")
        self.assertEqual(genai.terms(jd), list(genai.TERMS))


class TitleTest(unittest.TestCase):
    """GOAL28 §4.2: the AI titles are kept by title alone; Data Scientist and generic titles need the JD."""
    def test_ai_titles_match_a_kept_family(self):
        mine = ("ai_engineer", "genai_engineer", "ml_engineer", "dl_engineer", "data_scientist")
        for title in ("AI Agent Engineer", "LLM Engineer", "Generative AI Engineer", "Applied AI Engineer",
                      "AI/ML Engineer", "Machine Learning Engineer, LLM"):
            with self.subTest(title=title):
                fams = titles.match(title)
                self.assertTrue(set(fams) & set(mine), fams)
                self.assertIsNone(genai.gate_verdict(title, "," + ",".join(fams) + ",", PLAIN_JD, GATE, mine))

    def test_generic_titles(self):
        for title in ("Software Engineer", "Applied Scientist", "Senior Research Engineer",
                      "Member of Technical Staff", "Member of the Technical Staff"):
            self.assertTrue(genai.is_generic_title(title), title)
        for title in ("Software Engineer, Machine Learning", "Applied Scientist, LLM", "AI Research Engineer",
                      "Artificial Intelligence Software Engineer", "Data Scientist", "Machine Learning Engineer"):
            self.assertFalse(genai.is_generic_title(title), title)

    def test_a_marketing_data_scientist_without_genai_work_is_not_kept(self):
        fams = ",data_scientist,"
        self.assertEqual(genai.gate_verdict("Data Scientist, Marketing Analytics", fams, PLAIN_JD, GATE,
                                            ("ai_engineer", "data_scientist")), genai.VERDICT)

    def test_a_data_scientist_with_genai_work_is_kept(self):
        self.assertIsNone(genai.gate_verdict("Data Scientist, GenAI", ",data_scientist,", GENAI_JD, GATE,
                                             ("data_scientist",)))

    def test_one_term_is_not_enough_and_the_verdict_says_which(self):
        got = genai.gate_verdict("Data Scientist", ",data_scientist,", ONE_TERM_JD, GATE, ("data_scientist",))
        self.assertEqual(got, genai.VERDICT + " (only LLM)")
        self.assertTrue(genai.is_gate_verdict(got))

    def test_a_job_also_held_by_an_ungated_family_is_not_gated(self):
        # a title in ml_engineer too: that family alone keeps it
        self.assertIsNone(genai.gate_verdict("Machine Learning Data Scientist", ",data_scientist,ml_engineer,",
                                             PLAIN_JD, GATE, ("ml_engineer", "data_scientist")))

    def test_a_generic_title_is_gated_even_in_an_ungated_family(self):
        self.assertEqual(genai.gate_verdict("Applied Scientist", ",ml_engineer,", PLAIN_JD, GATE, ("ml_engineer",)),
                         genai.VERDICT)
        self.assertIsNone(genai.gate_verdict("Applied Scientist", ",ml_engineer,", GENAI_JD, GATE, ("ml_engineer",)))

    def test_no_gate_never_filters(self):
        self.assertIsNone(genai.gate_verdict("Data Scientist", ",data_scientist,", PLAIN_JD, None, ("data_scientist",)))
        self.assertIsNone(genai.gate_verdict("Software Engineer", ",ml_engineer,", PLAIN_JD, {}, ("ml_engineer",)))


class FileNameTest(unittest.TestCase):
    """GOAL28 §3.1: the user's file names follow the user's three titles; a person without resume_titles is unchanged."""
    ALEX = {"id": "v", "name": "Alex Rivera", "resume_titles": ["AI_Engineer", "AI_ML_Engineer", "ML_Engineer"],
             "resumes": [{"id": "ai_engineer", "file": "Alex_Rivera_AI_Engineer_Resume.docx"}]}
    JORDAN = {"id": "s", "name": "Jordan Lee", "resumes": [{"id": "ai_engineer", "file": "Jordan_Lee_AI_Engineer_Resume.docx"}]}

    def test_titles(self):
        stem = "Alex_Rivera_AI_Engineer_Resume"
        for role, want in (("Senior Data Scientist", stem), ("Data Engineer", stem), ("Software Engineer", stem),
                           ("Machine Learning Engineer", "Alex_Rivera_ML_Engineer_Resume"),
                           ("AI/ML Engineer", "Alex_Rivera_AI_ML_Engineer_Resume")):
            self.assertEqual(resumes.for_role(self.ALEX, stem, role), want, role)
        self.assertEqual(resumes.for_role(self.JORDAN, "Jordan_Lee_AI_Engineer_Resume", "Senior Data Scientist"),
                         "Jordan_Lee_Data_Scientist_Resume")
        self.assertEqual(resumes.role_title("Data Scientist"), "Data_Scientist")


class MatchPartTest(unittest.TestCase):
    def test_the_genai_part_sorts_by_how_many_terms(self):
        self.assertEqual([genai.pct(["x"] * n) for n in range(6)], [30, 55, 75, 90, 100, 100])
        self.assertIsNone(match.genai_part(None).pct)
        self.assertEqual(match.genai_part("").pct, 30)
        part = match.genai_part("LLM,RAG")
        self.assertEqual((part.pct, part.hits), (75, ("LLM", "RAG")))

    def test_more_genai_work_ranks_higher_all_else_equal(self):
        who = match.Who(years=4, families=("ai_engineer",))
        row = {"min_years": 3, "families": ",ai_engineer,", "title": "AI Engineer"}
        low = match.breakdown({**row, "genai": ""}, who, None)["pct"]
        high = match.breakdown({**row, "genai": "LLM,RAG,agents,embeddings"}, who, None)["pct"]
        unknown = match.breakdown(row, who, None)
        self.assertGreater(high, low)
        self.assertEqual(len(unknown["parts"]), 5)
        self.assertIsNone(unknown["parts"][4].pct)            # not read yet: left out of the %, never blocks


class ConfigTest(unittest.TestCase):
    def test_a_bad_gate_is_refused(self):
        for gate in ("yes", {"families": ["astronaut"]}, {"min_terms": 0}, {"min_terms": True}):
            with self.subTest(gate=gate), self.assertRaises(config.ConfigError):
                config._check_gate("x", gate)
        config._check_gate("x", None)
        config._check_gate("x", GATE)


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for pid, text in (("gated", GATED), ("ungated", UNGATED)):
            (root / "profiles" / pid).mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(text)
        self.patches = [mock.patch.object(config, "DB_PATH", root / "t.db"),
                        mock.patch.object(config, "PROFILES", root / "profiles")]
        for p in self.patches:
            p.start()
        self.conn = db.connect()
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        self.uid = {}
        for n, (title, jd) in enumerate((("Data Scientist, Marketing Analytics", PLAIN_JD),
                                         ("Data Scientist, GenAI", GENAI_JD),
                                         ("Applied Scientist", PLAIN_JD),
                                         ("AI Engineer", PLAIN_JD),
                                         ("Data Engineer", PLAIN_JD)), 1):
            self.uid[title] = self.seed(str(n), title, jd)

    def seed(self, ext_id: str, title: str, jd: str) -> str:
        item = make_job(ats="lever", key="acme", ext_id=ext_id, company="Acme", title=title, location="Austin, TX",
                        url=f"https://jobs.lever.co/acme/{ext_id}", posted_at=db.iso(NOW), description=jd)
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run,
                          NOW, [], 5)
        return item["uid"]

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def fits_of(self, pid: str) -> dict:
        rows = self.conn.execute("SELECT j.title, f.dealbreaker, f.genai FROM fits f JOIN jobs j ON j.uid = f.uid "
                                 "WHERE f.profile = ?", (pid,)).fetchall()
        return {r["title"]: (r["dealbreaker"], r["genai"]) for r in rows}


class FitsTest(DbCase):
    def test_migration_33_adds_the_columns(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        cols = {t: {r[1] for r in self.conn.execute(f"PRAGMA table_info({t})")} for t in ("fits", "contacts")}
        self.assertIn("genai", cols["fits"])
        self.assertTrue({"kind", "found_at"} <= cols["contacts"])

    def test_the_gate_filters_only_the_gated_person(self):
        for pid in ("gated", "ungated"):
            fits.refresh(self.conn, config.profile(pid), NOW)
        gated, ungated = self.fits_of("gated"), self.fits_of("ungated")
        self.assertEqual(gated["Data Scientist, Marketing Analytics"][0], genai.VERDICT)
        self.assertEqual(gated["Applied Scientist"][0], genai.VERDICT)
        self.assertIsNone(gated["Data Scientist, GenAI"][0])
        self.assertIsNone(gated["AI Engineer"][0])               # an AI title is never gated
        self.assertNotIn("Data Engineer", gated)                 # data_engineer is not the user's family any more
        self.assertTrue(all(v[0] is None for v in ungated.values()))
        self.assertEqual(ungated["Data Scientist, GenAI"][1], "LLM,RAG,LangChain,vector database")
        self.assertEqual(ungated["AI Engineer"][1], "")

    def test_no_gate_keeps_the_old_dealbreaker_hash(self):
        profile = config.profile("ungated")
        old = fits.sha1(fits.json.dumps(tuple(profile["dealbreakers"])))          # the applied-cards formula
        self.assertEqual(fits.profile_inputs(profile).dealbreaker_hash, old)
        self.assertNotEqual(fits.profile_inputs(config.profile("gated")).dealbreaker_hash,
                            fits.profile_inputs({**config.profile("gated"), "jd_gate": None}).dealbreaker_hash)


class BackfillTest(DbCase):
    def setUp(self):
        super().setUp()
        for pid in ("gated", "ungated"):
            fits.refresh(self.conn, config.profile(pid), NOW)
        # rows written before GOAL28: no genai, no gate verdict
        self.conn.execute("UPDATE fits SET genai = NULL, dealbreaker = NULL")

    def test_a_dry_run_counts_and_writes_nothing(self):
        got = genai_backfill.run(self.conn, config.profile("gated"), dry_run=True)
        self.assertEqual(got["rows"], 4)
        self.assertEqual(got["visible_before"]["data_scientist"], 2)
        self.assertEqual(got["visible_after"]["data_scientist"], 1)
        self.assertEqual({k: v for k, v in got["hidden_by_gate"].items() if v}, {"data_scientist": 1, "ml_engineer": 1})
        self.assertTrue(all(v == (None, None) for v in self.fits_of("gated").values()))
        self.assertIn("dry run, nothing written", genai_backfill.report(got))

    def test_apply_writes_terms_and_verdicts_then_nothing_is_stale(self):
        genai_backfill.main(self.conn, ["gated", "ungated"], dry_run=False)
        gated, ungated = self.fits_of("gated"), self.fits_of("ungated")
        self.assertEqual(gated["Data Scientist, Marketing Analytics"], (genai.VERDICT, ""))
        self.assertEqual(gated["Data Scientist, GenAI"], (None, "LLM,RAG,LangChain,vector database"))
        self.assertTrue(all(v[0] is None for v in ungated.values()))
        self.assertEqual(ungated["Data Engineer"], (None, ""))
        for pid in ("gated", "ungated"):
            self.assertEqual(fits.refresh(self.conn, config.profile(pid), NOW), 0)


if __name__ == "__main__":
    unittest.main()
