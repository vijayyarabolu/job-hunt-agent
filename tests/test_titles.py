"""Title matcher against real titles seen on live boards (Greenhouse, Lever, Ashby, Workday)."""
import unittest

from job_hunt_agent.titles import match

# (title, expected families): all titles copied from live boards on 2026-10-02.
MATCHES = [
    # verifier 1: AVP is an IC grade at banks (Citi, Deloitte); "MD" in a title is usually Maryland
    ("Agentic AI Engineer, AVP", ["ai_engineer"]),
    ("Gen AI - Python Engineer - Assistant Vice President", ["ai_engineer", "genai_engineer"]),
    ("Machine Learning Engineer - Baltimore, MD", ["ml_engineer"]),
    ("Applied AI Engineer", ["ai_engineer"]),
    ("Applied AI Engineer, Enterprise", ["ai_engineer"]),
    ("AI Engineer – Forward Deployed Engineering (AI FDE), U.S. Public Sector", ["ai_engineer"]),
    ("Senior AI Engineer - FDE (Forward Deployed Engineer)", ["ai_engineer"]),
    ("Software Engineer, Enterprise AI", ["ai_engineer"]),
    ("Software Engineer - AI Product", ["ai_engineer"]),
    ("Sr Full Stack Developer (AI Agents)", ["ai_engineer"]),
    ("AI/ML Engineer – Agentic Private Bank", ["ai_engineer", "ml_engineer"]),
    ("Infrastructure Software Engineer, Enterprise GenAI", ["genai_engineer"]),
    ("Senior LLM Engineer", ["genai_engineer"]),
    ("NLP Engineer", ["genai_engineer"]),
    ("Generative AI Engineer", ["ai_engineer", "genai_engineer"]),
    ("Machine Learning Engineer, Public Sector", ["ml_engineer"]),
    ("Senior Machine Learning Engineer, Accelerated Apache Spark", ["ml_engineer"]),
    ("Machine Learning Engineer - Data Pipelines", ["ml_engineer"]),
    ("Software Engineer, ML Systems & Training Architecture", ["ml_engineer"]),
    ("MLOps Engineer", ["ml_engineer"]),
    ("AI Applied Scientist", ["ml_engineer"]),
    ("Applied Scientist", ["ml_engineer"]),
    ("Senior Machine Learning Engineer, Perception - Autonomous Driving", ["ml_engineer", "dl_engineer"]),
    ("Computer Vision Engineer", ["dl_engineer"]),
    ("Deep Learning Engineer, Inference", ["dl_engineer"]),
    ("Data Scientist, Core Experimentation", ["data_scientist"]),
    ("Machine Learning Data Scientist, Forecasting", ["data_scientist"]),
    ("Data Engineer, GTM", ["data_engineer"]),
    ("Senior Data Engineer, Public Sector", ["data_engineer"]),
    ("Analytics Engineer", ["data_engineer"]),
    ("Member of Technical Staff, Machine Learning Engineer", ["ml_engineer"]),
    ("Senior Software Engineer, Agentic AI", ["ai_engineer"]),        # NVIDIA 8+ yrs: years filter, not the title
    ("Data Scientist Senior", ["data_scientist"]),
    ("Software Engineer II (GenAI)", ["genai_engineer"]),
    ("AI Engineer (Contract)", ["ai_engineer"]),
    ("Machine Learning Engineer - Lead Scoring Models", ["ml_engineer"]),
    ("Machine Learning Engineer, Customer Engineering", ["ml_engineer"]),   # an MLE on that team, not presales
    ("AI Engineer – Forward Deployed Engineering (AI FDE), U.S. Public Sector", ["ai_engineer"]),
]

EXCLUDED = [
    "Staff Data Scientist - Trust and Safety",
    "Staff Applied AI Engineer",
    "Data Science Intern (2027)",
    "Applied Machine Learning Engineer, AI for VLSI Design - New College Grad 2026",
    "Engineering Manager, Agent Oversight",
    "Director, Data Science",
    "Head of Data Science, Strategic Finance",
    "AI Support Engineer - San Francisco",
    "Account Executive, AI Native",
    "Specialist Solutions Architect - AI/ML",
    "Senior Solutions Engineer (Technical, Presales, Data & AI, DNB)",
    "Recruiter, AI/ML Research",
    "Technical Program Manager, Gen AI Operations Planning",
    "Senior AI Product Manager, Finance Agents",
    "Sr. Director, (Applied AI)  Forward Deployed Engineering",
    "Principal Machine Learning Engineer",
    "VP, Machine Learning",
    "AI Trainer - Coding",
    "AI Tutor, Mathematics",
    "Data Annotator (AI)",
    "Applied AI Architect, Partner",
    "Hardware Engineer, AI Chips",
    "Firmware Engineer - AI Accelerators",
    # ranked high in the live UI but wrong for a 4-year IC
    "ED, Agentic Core IT Processes",
    "Senior Product Designer - Okta for AI Agents",
    "Lead Full-Stack Engineer -- Advanced AI",
    "Agentic AI Senior Lead Data Scientist",
    "Lead Engineer - AI Security",
    "Lead Data Scientist, Platform Product",
    "Executive Director, Machine Learning Engineering",
    "SVP, AI Engineering",
    "AVP, Engineering Manager - Machine Learning",            # AVP-as-manager
    "Managing Director, AI Engineering",
    "UX Designer, Generative AI",
    "Visual Designer - AI Products",
    "AI Architect",
    "Head, Applied AI",
    "Chief AI Officer",
    "Machine Learning Engineer, People Manager",
    "Senior Experience Designer, Generative and Agentic Workflows",
    "Data Annotation Specialist, Data Science",
    # GOAL3 §2 leaks (live DB 2026-10-05) and their kin: customer-facing roles and managers by another name
    "Sr. Tech CSM - Emerging Products, AWS Applied AI Solutions GTM",
    "Managing Engineer, Data Engineering",
    "Managing Consultant, Data Engineer",
    "AI/ML Customer Engineer",
    "Senior Customer Engineer, Enterprise AI  - Bay Area",
    "Agentic Solutions Consultant (Insurance SaaS)",
    "Technical Account Manager, GenAI",
    "Developer Advocate – Agentic AI",
    "DevRel Engineer, Agentic Infrastructure",
    "Data Science Supervisor",
]

UNRELATED = [
    "Data Center Mechanical Engineer",
    "Software Engineer - Data Infrastructure",
    "Product Designer, AI Models",
    "Customer Enablement Architect (Capability Engineering & AI Adoption)",
    "Research Scientist, Frontier Risk Evaluations",
]


class TitleMatchTest(unittest.TestCase):
    def test_real_titles_match_expected_families(self):
        for title, expected in MATCHES:
            with self.subTest(title=title):
                self.assertEqual(sorted(match(title)), sorted(expected))

    def test_excluded_titles_never_match(self):
        for title in EXCLUDED:
            with self.subTest(title=title):
                self.assertEqual(match(title), [])

    def test_unrelated_titles_do_not_match(self):
        for title in UNRELATED:
            with self.subTest(title=title):
                self.assertEqual(match(title), [])

    def test_profile_subset_restricts_families(self):
        self.assertEqual(match("Data Engineer, GTM", ["ai_engineer"]), [])
        self.assertEqual(match("Applied AI Engineer", ["ai_engineer", "data_engineer"]), ["ai_engineer"])

    def test_has_at_least_30_real_titles(self):
        self.assertGreaterEqual(len(MATCHES) + len(EXCLUDED) + len(UNRELATED), 30)


if __name__ == "__main__":
    unittest.main()


class AcademicTitlesTests(unittest.TestCase):
    """resumegen GOAL3 §9: teaching / academic jobs never match; look-alikes that are software roles still do."""

    EXCLUDED = [   # the open academic jobs found in both feeds on 2026-10-08, plus the GOAL's list
        "Education Assistant Professor - Biostatistics & Data Science",
        "HPE Labs - Postdoctoral Research Scientist: Generative AI for Photonic and Analog Computing",
        "Postdoctoral Researcher – Integrative Genetics and Multi-Omics Data Science",
        "Dept of Surgery, Division of Research, Data Science Institute – Assistant Professor",
        "Data Science, Department of Information Technology- Adjunct Faculty",
        "Faculty Position - Department of Epidemiology and Cancer Control, Data Science",
        "Full Time, Tenure-track Faculty, Mathematics, Statistics or Data Science",
        "Adjunct Pool (2025/2026)/Mathematics and Data Science",
        "Adjunct Instructor, Data Science",
        "Assistant Professor / Associate Professor of Journalism in Data Science and Quantitative Reporting",
        "Lecturer in Machine Learning Engineering",
        "Data Science Teacher",
        "Teaching Professor, Data Science",
        "Graduate Teaching Assistant - Machine Learning",
        "Postdoc, AI Engineer for Climate Models",
        "Dean of Data Science",
        "Research Fellow, Machine Learning Engineering",
        "Machine Learning Tutor",
    ]
    STILL_MATCH = [
        ("AI Engineer, Education Technology", ["ai_engineer"]),
        ("ML Engineer – Teaching Assistant tools", ["ml_engineer"]),
        ("Machine Learning Engineer, EdTech Platform", ["ml_engineer"]),
        ("Data Scientist, Learning Analytics", ["data_scientist"]),
    ]

    def test_academic_titles_match_no_family(self):
        for title in self.EXCLUDED:
            with self.subTest(title=title):
                self.assertEqual(match(title), [])

    def test_look_alikes_still_match(self):
        for title, families in self.STILL_MATCH:
            with self.subTest(title=title):
                self.assertEqual(match(title), families)

    def test_a_jd_that_says_academic_or_faculty_matches_nothing(self):
        from job_hunt_agent.titles import academic_jd, match_job
        kumc = "Department: Biostatistics\nJob Family Group:\nAcademic\nPosition Requirements: PhD"
        self.assertTrue(academic_jd(kumc))
        self.assertTrue(academic_jd("Position Type: Faculty (9-month)"))
        self.assertFalse(academic_jd("We build learning tools for faculty and students."))
        self.assertEqual(match_job("Data Scientist", kumc), [])
        self.assertEqual(match_job("Data Scientist", "We build learning tools for faculty."), ["data_scientist"])
