"""Every resume file is named like the person's real resumes (user, 2026-10-07), and the title in the name follows the
job's role, not the base it was tailored from (user, 2026-10-15)."""
import unittest

from job_hunt_agent import resume_upload, resumes

ALEX = {"id": "alex", "name": "Alex Rivera", "resume_file_pattern": "{name}_{title}_Resume({company}).pdf",
         "resumes": [{"id": "ai_engineer", "label": "AI Engineer", "file": "Alex_Rivera_AI_Engineer_Resume.docx"},
                     {"id": "ml_engineer", "label": "ML Engineer", "file": "Alex_Rivera_ML_Engineer_Resume.docx"},
                     {"id": "data_scientist", "label": "Data Scientist",
                      "file": "Alex_Rivera_Data_Scientist_Resume.docx"},
                     {"id": "data_engineer", "label": "Data Engineer",
                      "file": "Alex_Rivera_Data_Engineer_Resume.docx"}]}


class BaseFileNameTest(unittest.TestCase):
    def test_each_base_id_maps_to_its_own_file_name(self):
        for rid, want in [("ai_engineer", "Alex_Rivera_AI_Engineer_Resume.pdf"),
                          ("ml_engineer", "Alex_Rivera_ML_Engineer_Resume.pdf"),
                          ("data_scientist", "Alex_Rivera_Data_Scientist_Resume.pdf"),
                          ("data_engineer", "Alex_Rivera_Data_Engineer_Resume.pdf")]:
            with self.subTest(rid=rid):
                self.assertEqual(resumes.base_file_name(ALEX, rid), want)

    def test_unknown_id_with_several_bases_gives_none(self):
        self.assertIsNone(resumes.base_file_name(ALEX, "nope"))

    def test_tracker_copy_takes_the_roles_title_and_the_company(self):
        app = {"role_title": "Senior Associate, Product Management - Applied AI", "company": "Capital One",
               "resume_used": "ml_engineer"}
        self.assertEqual(resume_upload.file_name(ALEX, app, ".pdf", 1), "Alex_Rivera_AI_Engineer_Resume(Capital One).pdf")
        self.assertEqual(resume_upload.file_name(ALEX, app, ".pdf", 2),
                         "Alex_Rivera_AI_Engineer_Resume(Capital One)_v2.pdf")


JORDAN = {"id": "jordan", "name": "Jordan Lee",
         "resumes": [{"id": "ai_engineer", "label": "AI Engineer", "file": "Jordan_Lee_AI_Engineer_Resume.docx"}]}


class UploadNameTest(unittest.TestCase):
    """The file a job site gets is the plain base name; only the tracker / Drive copy names the company (user
    2026-10-14)."""

    def test_the_job_site_gets_the_plain_name_and_the_tracker_keeps_the_company(self):
        for profile, rid, plain in [(ALEX, "data_scientist", "Alex_Rivera_Data_Scientist_Resume.pdf"),
                                    (JORDAN, "ai_engineer", "Jordan_Lee_Data_Scientist_Resume.pdf")]:
            app = {"role_title": "Senior Data Scientist", "company": "Acme (Labs)", "resume_used": rid}
            with self.subTest(profile["id"]):
                uploaded = resume_upload.upload_name(profile, app, ".pdf")
                self.assertEqual(uploaded, plain)
                self.assertNotIn("(", uploaded)
                self.assertNotIn("_v", uploaded)
                self.assertIn("(Acme (Labs))", resume_upload.file_name(profile, app, ".pdf", 1))
                self.assertTrue(resume_upload.file_name(profile, app, ".pdf", 2).endswith(")_v2.pdf"))   # the tracker copy only

    def test_without_a_known_base_the_name_is_built_without_the_company(self):
        nobody = {"id": "zz", "name": "Zz Tester"}
        got = resume_upload.upload_name(nobody, {"role_title": "ML Engineer", "company": "Acme"}, ".pdf")
        self.assertEqual(got, "Zz_Tester_ML_Engineer_Resume.pdf")


class RoleTitleTest(unittest.TestCase):
    """Waymo "Senior Machine Learning Engineer, Growth" was tailored from the Data Scientist base (best ATS match,
    kept) but every name said Data_Scientist (user, 2026-10-15)."""

    def test_ml_role_on_the_data_scientist_base_is_named_ml_engineer_everywhere(self):
        app = {"role_title": "Senior Machine Learning Engineer, Growth", "company": "Waymo",
               "resume_used": "data_scientist"}
        self.assertEqual(resume_upload.upload_name(ALEX, app, ".pdf"), "Alex_Rivera_ML_Engineer_Resume.pdf")
        self.assertEqual(resume_upload.file_name(ALEX, app, ".pdf", 1), "Alex_Rivera_ML_Engineer_Resume(Waymo).pdf")
        self.assertEqual(resumes.base_file_name(ALEX, "data_scientist", role="Machine Learning Engineer"),
                         "Alex_Rivera_ML_Engineer_Resume.pdf")    # resumegen's headline works as the role too

    def test_each_family(self):
        for role, want in [("Data Scientist, AI/ML", "Data_Scientist"), ("Analytics Engineer", "Data_Engineer"),
                           ("AI/ML Engineer", "AI_ML_Engineer"), ("Applied Scientist", "ML_Engineer"),
                           ("GenAI Engineer", "AI_Engineer"), ("Software Engineer", None), ("", None)]:
            with self.subTest(role=role):
                self.assertEqual(resumes.role_title(role), want)

    def test_unknown_role_keeps_the_bases_own_name(self):
        self.assertEqual(resumes.base_file_name(ALEX, "data_engineer", role="Software Engineer, Backend"),
                         "Alex_Rivera_Data_Engineer_Resume.pdf")
        self.assertEqual(resumes.base_file_name(ALEX, "data_engineer"), "Alex_Rivera_Data_Engineer_Resume.pdf")

    def test_a_title_the_person_has_no_base_for_follows_their_naming_style(self):
        self.assertEqual(resumes.base_file_name(ALEX, "ml_engineer", role="AI/ML Engineer"),
                         "Alex_Rivera_AI_ML_Engineer_Resume.pdf")
        self.assertEqual(resumes.base_file_name(JORDAN, "ai_engineer", role="Machine Learning Engineer"),
                         "Jordan_Lee_ML_Engineer_Resume.pdf")

    def test_ml_engineer_never_matches_an_ai_ml_engineer_base(self):
        both = {**ALEX, "resumes": [{"id": "x", "file": "Alex_Rivera_AI_ML_Engineer_Resume.docx"},
                                     {"id": "y", "file": "Alex_Rivera_Data_Scientist_Resume.docx"}]}
        self.assertEqual(resumes.base_file_name(both, "y", role="ML Engineer"), "Alex_Rivera_ML_Engineer_Resume.pdf")

    def test_panel_label_says_which_title_and_which_base(self):
        self.assertEqual(resumes.title_label("Senior Machine Learning Engineer"), "ML Engineer")
        self.assertEqual(resumes.title_label("AI/ML Engineer"), "AI/ML Engineer")


if __name__ == "__main__":
    unittest.main()
