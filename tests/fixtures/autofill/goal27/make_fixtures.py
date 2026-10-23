"""GOAL27: writes the synthetic ATS apply pages of the adapter matrix (tests/fixtures/autofill/goal27/sites/*.html).

Each page is modeled on that ATS's public apply form: its field names / ids, label wording, widget kinds (React-select
dropdowns, Yes / No buttons, select2, intl phone boxes, typeaheads, shadow-DOM inputs, iframes, repeaters) and its
sections. They are our own markup (no recorded page, no ATS code) and carry only the fake person's expected values:
every checked control has data-expect (what it must show after one Autofill; "" = must stay empty) and data-type
(the matrix column). Run `python3 tests/fixtures/autofill/goal27/make_fixtures.py` after changing a site below.
"""
import html
from pathlib import Path

OUT = Path(__file__).parent / "sites"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]
STATES = ["Alabama", "Alaska", "Arizona", "California", "Colorado", "Kansas", "Missouri", "New York", "Texas", "Washington"]
YES_NO = ["Yes", "No"]
GENDERS = ["Male", "Female", "Decline To Self Identify"]
RACES = ["American Indian or Alaskan Native", "Asian", "Black or African American", "Hispanic or Latino", "White",
         "Two or More Races", "Decline To Self Identify"]
VETERANS = ["I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"]
DISABILITY = ["Yes, I have a disability (or previously had a disability)", "No, I do not have a disability",
              "I do not want to answer"]
HOW = ["LinkedIn", "Company Website", "Indeed", "Employee Referral", "Other"]
CITIES = ["Springfield, Missouri, United States", "Springfield, Illinois, United States", "Springfield, Massachusetts, United States"]
SCHOOLS = ["Lakeview State University", "University of Missouri", "Missouri State University"]
DEGREES = ["High School", "Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate"]
FIELDS = ["Computer Science", "Data Science", "Electrical Engineering", "Mathematics"]

# what the fake person's profile fills (tests/test_goal27_matrix.py sets it up)
P = {"first": "Zz", "last": "Testcase", "full": "Zz Testcase", "email": "zz.testcase@example.com", "phone": "555-010-0199",
     "digits": "5550100199", "street": "123 Main St", "city": "Springfield", "state": "Missouri", "zip": "65801",
     "linkedin": "https://www.linkedin.com/in/zz-testcase", "github": "https://github.com/zz-testcase",
     "salary": "150000", "company": "Globex Corp", "title": "Senior ML Engineer"}


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def attrs(**kw) -> str:
    return "".join(f' {k.replace("_", "-")}="{esc(v)}"' for k, v in kw.items() if v is not None and v is not False)


def exp(expect, typ):
    return {"data_expect": expect, "data_type": typ}


# ---- plain controls ---------------------------------------------------------------------------------------------
def text(label, id, expect, typ="text", name=None, wrap="field", input_type="text", placeholder=None, autocomplete=None,
         label_tag=True, required=False):
    lab = f'<label for="{esc(id)}">{esc(label)}{" *" if required else ""}</label>' if label_tag else ""
    return (f'<div class="{wrap}">{lab}<input{attrs(id=id, name=name or id, type=input_type, placeholder=placeholder, autocomplete=autocomplete, required=required, **exp(expect, typ))}></div>')


def area(label, id, expect, typ="textarea", name=None, wrap="field"):
    return f'<div class="{wrap}"><label for="{esc(id)}">{esc(label)}</label><textarea{attrs(id=id, name=name or id, **exp(expect, typ))}></textarea></div>'


def select(label, id, options, expect, typ="select", name=None, wrap="field", placeholder="Select..."):
    opts = f'<option value="">{esc(placeholder)}</option>' + "".join(f'<option value="{esc(o)}">{esc(o)}</option>' for o in options)
    return f'<div class="{wrap}"><label for="{esc(id)}">{esc(label)}</label><select{attrs(id=id, name=name or id, **exp(expect, typ))}>{opts}</select></div>'


def radios(label, name, options, expect, typ="radio", wrap="field"):
    items = "".join(f'<label><input type="radio" name="{esc(name)}" value="{esc(o)}"> {esc(o)}</label>' for o in options)
    return f'<fieldset class="{wrap}"{attrs(**exp(expect, typ))}><legend>{esc(label)}</legend>{items}</fieldset>'


def check(label, id, expect, typ="checkbox", wrap="field"):
    return f'<div class="{wrap}"><label><input type="checkbox"{attrs(id=id, name=id, **exp(expect, typ))}> {esc(label)}</label></div>'


def file(label, id, expect="re:_Resume\\.pdf$", typ="file", name=None, wrap="field", accept=".pdf,.doc,.docx"):
    return f'<div class="{wrap}"><label for="{esc(id)}">{esc(label)}</label><input{attrs(type="file", id=id, name=name or id, accept=accept, **exp(expect, typ))}></div>'


# ---- simulated widgets (ats_sim.js) -------------------------------------------------------------------------------
def w(kind, label, expect=None, typ=None, **kw):
    d = {"data_w": kind, "data_label": label, **{f"data_{k}": v for k, v in kw.items()}}
    if expect is not None:
        d.update(exp(expect, typ))
    return f"<div{attrs(**d)}></div>"


def rs(label, options, expect, typ="dropdown", **kw):
    return w("rs", label, expect, typ, options="|".join(options), **kw)


def yesno(label, expect, typ="yesno", **kw):
    return w("yesno", label, expect, typ, **kw)


def head(t):
    return f"<h3>{esc(t)}</h3>"


def shadow(label, name, expect, typ="text"):
    """A web-component input (SmartRecruiters spl-input, ADP sdf-input): label and input in an open shadow root."""
    return f'<x-input{attrs(data_label=label, data_name=name, **exp(expect, typ))}></x-input>'


SHADOW_JS = """<script>
customElements.define("x-input", class extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({ mode: "open" });
    const id = "in-" + this.dataset.name;
    r.innerHTML = `<style>label{display:block;font-weight:600}input{width:300px}</style><label for="${id}">${this.dataset.label}</label><input id="${id}" name="${this.dataset.name}" type="text">`;
    this.__simValue = () => r.querySelector("input").value;
  }
});
</script>"""


def jobs_repeater(style="generic"):
    """Work history entries: Title, Company, Location, Start / End month + year selects, current box, description."""
    mo = "".join(f'<option value="{i}">{m}</option>' for i, m in enumerate(MONTHS, 1))
    yr = "".join(f'<option value="{y}">{y}</option>' for y in range(2026, 2009, -1))
    first = P["title"] + "|" + P["company"]
    del first
    tpl = (f'<div class="field"><label for="title-{{i}}">Job Title</label><input id="title-{{i}}" name="jobs[{{i}}][title]"></div>'
           f'<div class="field"><label for="company-{{i}}">Company</label><input id="company-{{i}}" name="jobs[{{i}}][company]"></div>'
           f'<div class="field"><label for="sm-{{i}}">Start Month</label><select id="sm-{{i}}" name="jobs[{{i}}][start_month]"><option value="">Month</option>{mo}</select></div>'
           f'<div class="field"><label for="sy-{{i}}">Start Year</label><select id="sy-{{i}}" name="jobs[{{i}}][start_year]"><option value="">Year</option>{yr}</select></div>'
           f'<div class="field"><label for="em-{{i}}">End Month</label><select id="em-{{i}}" name="jobs[{{i}}][end_month]"><option value="">Month</option>{mo}</select></div>'
           f'<div class="field"><label for="ey-{{i}}">End Year</label><select id="ey-{{i}}" name="jobs[{{i}}][end_year]"><option value="">Year</option>{yr}</select></div>'
           f'<div class="field"><label><input type="checkbox" id="cur-{{i}}" name="jobs[{{i}}][current]"> I currently work here</label></div>'
           f'<div class="field"><label for="desc-{{i}}">Description</label><textarea id="desc-{{i}}" name="jobs[{{i}}][description]"></textarea></div>')
    return (f'<h3>Work Experience</h3><div data-w="repeater" data-add="Add another job" data-entry="entry job" data-start="1">'
            f'<template>{tpl}</template></div>'
            f'<div hidden data-check-jobs data-type="repeater"></div>')


def schools_repeater():
    yr = "".join(f'<option value="{y}">{y}</option>' for y in range(2026, 2009, -1))
    tpl = (f'<div class="field"><label for="school-{{i}}">School</label><input id="school-{{i}}" name="educations[{{i}}][school]"></div>'
           f'<div class="field"><label for="degree-{{i}}">Degree</label><select id="degree-{{i}}" name="educations[{{i}}][degree]"><option value="">Select</option>'
           + "".join(f'<option>{d}</option>' for d in DEGREES) + '</select></div>'
           f'<div class="field"><label for="major-{{i}}">Field of Study</label><input id="major-{{i}}" name="educations[{{i}}][field]"></div>'
           f'<div class="field"><label for="gy-{{i}}">End Year</label><select id="gy-{{i}}" name="educations[{{i}}][end_year]"><option value="">Year</option>{yr}</select></div>')
    return (f'<h3>Education</h3><div data-w="repeater" data-add="Add another school" data-entry="entry school" data-start="1">'
            f'<template>{tpl}</template></div><div hidden data-check-schools data-type="repeater"></div>')


EEO_SELECTS = lambda wrap="field": "".join([  # noqa: E731
    head("Voluntary Self-Identification"),
    select("Gender", "gender", GENDERS, "Male", "eeo", wrap=wrap),
    select("Are you Hispanic/Latino?", "hispanic_ethnicity", YES_NO + ["Decline To Self Identify"], "No", "eeo", wrap=wrap),
    select("Race", "race", RACES, "Asian", "eeo", wrap=wrap),
    select("Veteran Status", "veteran_status", VETERANS, "I am not a protected veteran", "eeo", wrap=wrap),
    select("Disability Status", "disability_status", DISABILITY, "No, I do not have a disability", "eeo", wrap=wrap)])

SENSITIVE = (head("Additional information") + text("Social Security Number", "ssn", "", "sensitive") +
             text("Date of Birth", "dob", "", "sensitive", placeholder="MM/DD/YYYY"))
YEARS_Q = text("How many years of Python experience do you have?", "q_years", "5", "resume-answer", required=True)
SUBMIT = '<div class="actions"><button type="submit" class="btn-submit">Submit Application</button></div>'


SITES = {
    "greenhouse": ("job-boards.greenhouse.io", "/zztest/jobs/27001", "Apply for ML Engineer", [
        text("First Name", "first_name", P["first"], autocomplete="given-name", required=True),
        text("Last Name", "last_name", P["last"], autocomplete="family-name", required=True),
        text("Email", "email", P["email"], "email", input_type="email", required=True),
        head("Phone"), rs("Country", ["Canada +1", "India +91", "United States +1"], "United States +1", id="country"),
        text("Phone", "phone", P["phone"], "phone", input_type="tel", required=True),
        w("typeahead", "Location (City)", "Springfield, Missouri, United States", "typeahead", id="candidate-location", options="|".join(CITIES)),
        file("Resume/CV", "resume"), file("Cover Letter", "cover_letter", "", "file"),
        rs("Are you legally authorized to work in the United States?", YES_NO, "Yes", "legal", id="question_1001", required=True),
        rs("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B visa status)?", YES_NO, "No", "legal", id="question_1002", required=True),
        text("LinkedIn Profile", "question_1003", P["linkedin"], name="question_1003"),
        YEARS_Q,
        rs("How did you hear about this job?", HOW, "Company Website", "dropdown", id="question_1004", **{"async": ""}),
        head("Education"),
        rs("School", SCHOOLS, "Lakeview State University", "dropdown", id="school--0", **{"async": ""}),
        rs("Degree", DEGREES, "Master's Degree", "dropdown", id="degree--0"),
        rs("Discipline", FIELDS, "Computer Science", "dropdown", id="discipline--0"),
        head("Voluntary Self-Identification"),
        rs("Gender", GENDERS, "Male", "eeo", id="gender"), rs("Are you Hispanic/Latino?", YES_NO + ["Decline To Self Identify"], "No", "eeo", id="hispanic_ethnicity"),
        rs("Veteran Status", VETERANS, "I am not a protected veteran", "eeo", id="veteran_status"),
        rs("Disability Status", DISABILITY, "No, I do not have a disability", "eeo", id="disability_status"),
    ]),
    "lever": ("jobs.lever.co", "/zztest/27002/apply", "Lever application", [
        head("Submit your application"),
        file("Resume/CV", "resume-upload-input", name="resume"),
        text("Full name", "name", P["full"], name="name", wrap="application-question"),
        text("Email", "email", P["email"], "email", wrap="application-question"),
        text("Phone", "phone", P["phone"], "phone", wrap="application-question"),
        w("typeahead", "Current location", "Springfield, Missouri, United States", "typeahead", id="location-input", name="location", options="|".join(CITIES), field="application-question"),
        text("Current company", "org", P["company"], name="org", wrap="application-question"),
        head("Links"),
        text("LinkedIn URL", "urls-linkedin", P["linkedin"], name="urls[LinkedIn]", wrap="application-question"),
        text("GitHub URL", "urls-github", P["github"], name="urls[GitHub]", wrap="application-question"),
        head("Work authorization"),
        radios("Are you authorized to work in the United States?", "cards[a1][field0]", YES_NO, "Yes", "legal", wrap="application-question"),
        radios("Will you require sponsorship for employment now or in the future?", "cards[a1][field1]", YES_NO, "No", "legal", wrap="application-question"),
        area("If yes, what type of sponsorship?", "cards-a1-field2", "", "textarea", name="cards[a1][field2]", wrap="application-question"),
        YEARS_Q,
        head("U.S. Equal Employment Opportunity information"),
        select("Gender", "eeo-gender", GENDERS, "Male", "eeo", name="eeo[gender]"),
        select("Race", "eeo-race", RACES, "Asian", "eeo", name="eeo[race]"),
        select("Veteran status", "eeo-veteran", VETERANS, "I am not a protected veteran", "eeo", name="eeo[veteran]"),
        area("Additional information", "comments", "", "textarea", name="comments"),
    ]),
    "ashby": ("jobs.ashbyhq.com", "/zztest/27003/application", "Ashby application", [
        text("Name", "_systemfield_name", P["full"], name="_systemfield_name", wrap="_fieldEntry", required=True),
        text("Email", "_systemfield_email", P["email"], "email", input_type="email", name="_systemfield_email", wrap="_fieldEntry"),
        text("Phone", "phone-ashby", P["phone"], "phone", input_type="tel", name="a58c-phone", wrap="_fieldEntry"),
        file("Resume", "_systemfield_resume", wrap="_fieldEntry"),
        w("typeahead", "Location", "Springfield, Missouri, United States", "typeahead", id="ashby-location", options="|".join(CITIES), field="_fieldEntry"),
        text("LinkedIn Profile", "q-linkedin", P["linkedin"], name="question_2001", wrap="_fieldEntry"),
        yesno("Are you legally authorized to work in the United States?", "Yes", "legal"),
        yesno("Will you now or in the future require visa sponsorship?", "No", "legal"),
        yesno("Are you willing to relocate?", "Yes", "yesno"),
        area("Why are you interested in this role?", "why", "", "textarea", name="question_2002", wrap="_fieldEntry"),
        YEARS_Q,
        rs("Gender", GENDERS, "Male", "eeo", id="ashby-gender"), rs("Race", RACES, "Asian", "eeo", id="ashby-race"),
    ]),
    "smartrecruiters": ("jobs.smartrecruiters.com", "/oneclick-ui/company/zztest/publication/27004", "SmartRecruiters", [
        shadow("First name", "firstName", P["first"]), shadow("Last name", "lastName", P["last"]),
        shadow("Email", "email", P["email"], "email"), shadow("Confirm your email", "confirmEmail", P["email"], "email"),
        shadow("Phone number", "phoneNumber", P["phone"], "phone"),
        w("typeahead", "City", "Springfield, Missouri, United States", "typeahead", id="sr-location", options="|".join(CITIES)),
        shadow("LinkedIn profile", "linkedin", P["linkedin"]),
        file("Resume", "sr-resume", name="resume"),
        select("Are you legally authorized to work in the United States?", "sr-q1", YES_NO, "Yes", "legal"),
        select("Will you now or in the future require sponsorship?", "sr-q2", YES_NO, "No", "legal"),
        area("Message to the hiring team", "sr-msg", "", "textarea"),
    ]),
    "icims": ("careers-zztest.icims.com", "/jobs/27005/ml-engineer/candidate", "iCIMS", "iframe"),
    "icims_form": ("careers-zztest.icims.com", "/jobs/27005/ml-engineer/candidate/form", "iCIMS form", [
        head("Contact Information"),
        text("First Name", "PersonProfileFields.FirstName", P["first"], wrap="iCIMS_InfoField"),
        text("Last Name", "PersonProfileFields.LastName", P["last"], wrap="iCIMS_InfoField"),
        text("Email", "PersonProfileFields.Email", P["email"], "email", wrap="iCIMS_InfoField"),
        text("Phone Number", "PersonProfileFields.PhoneNumber", P["phone"], "phone", wrap="iCIMS_InfoField"),
        text("Address", "PersonProfileFields.AddressStreet1", P["street"], wrap="iCIMS_InfoField"),
        text("City", "PersonProfileFields.AddressCity", P["city"], wrap="iCIMS_InfoField"),
        select("State", "PersonProfileFields.AddressState", STATES, P["state"], wrap="iCIMS_InfoField"),
        text("Zip/Postal Code", "PersonProfileFields.AddressZip", P["zip"], wrap="iCIMS_InfoField"),
        file("Upload Resume", "PortalProfileFields.Resume"),
        head("Questions"),
        select("Are you legally authorized to work in the United States?", "rcf3048", YES_NO, "Yes", "legal", wrap="iCIMS_InfoField"),
        select("Will you require sponsorship now or in the future?", "rcf3049", YES_NO, "No", "legal", wrap="iCIMS_InfoField"),
        select("Are you at least 18 years of age?", "rcf3050", YES_NO, "Yes", "legal", wrap="iCIMS_InfoField"),
        EEO_SELECTS("iCIMS_InfoField"),
    ]),
    "taleo": ("zztest.taleo.net", "/careersection/2/jobapply.ftl", "Taleo", [
        head("Personal Information"),
        text("First Name", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_FirstName", P["first"]),
        text("Last Name", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_LastName", P["last"]),
        text("Email Address", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_EmailAddress", P["email"], "email"),
        text("Cellular Phone", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_MobilePhone", P["phone"], "phone"),
        text("Address (line 1)", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_Address", P["street"]),
        text("City", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_City", P["city"]),
        text("Zip/Postal Code", "et-ef-content-ftf-gp-j_id_id16pc9-page_0-cpi-cfrmsub-frm-dv_cs_candidate_personal_info_ZipCode", P["zip"]),
        file("Attach a resume", "taleo-attach"),
        head("Questions"),
        select("Are you at least 18 years of age?", "taleo-q1", YES_NO, "Yes", "legal", placeholder="Select an answer"),
        select("Are you legally authorized to work in the United States?", "taleo-q2", YES_NO, "Yes", "legal", placeholder="Select an answer"),
        select("Will you now or in the future require sponsorship for employment visa status?", "taleo-q3", YES_NO, "No", "legal", placeholder="Select an answer"),
        text("Date Available", "taleo-date", "re:^\\d{2}/\\d{2}/\\d{4}$", "date", placeholder="MM/DD/YYYY"),
    ]),
    "successfactors": ("career5.successfactors.com", "/careers?company=zztest&career_job_req_id=27007", "SuccessFactors", [
        text("First Name", "fbclc_fName", P["first"]), text("Last Name", "fbclc_lName", P["last"]),
        text("Email", "fbclc_email", P["email"], "email"), text("Contact Phone", "fbclc_phone", P["phone"], "phone"),
        text("City", "fbclc_city", P["city"]),
        select("Country", "fbclc_country", ["Canada", "India", "United States"], "United States"),
        select("State", "fbclc_state", STATES, P["state"]),
        text("Zip Code", "fbclc_zip", P["zip"]),
        file("Resume", "fbclc_resume"),
        select("Are you legally authorized to work in the United States?", "fbclc_auth", YES_NO, "Yes", "legal"),
        select("Will you now or in the future require sponsorship?", "fbclc_spons", YES_NO, "No", "legal"),
        text("Available start date", "fbclc_availableDate", "re:^\\d{2}/\\d{2}/\\d{4}$", "date", placeholder="MM/DD/YYYY"),
        SENSITIVE,
    ]),
    "oracle": ("zztest.fa.us2.oraclecloud.com", "/hcmUI/CandidateExperience/en/sites/CX/job/27008/apply", "Oracle Cloud HCM", [
        head("Contact Information"),
        text("First Name", "firstName-1", P["first"], name="firstName"), text("Last Name", "lastName-2", P["last"], name="lastName"),
        text("Email Address", "email-3", P["email"], "email", name="email"),
        select("Country Code", "phoneCountry-4", ["United States (+1)", "India (+91)"], "United States (+1)", name="phoneCountryCode"),
        text("Phone Number", "phoneNumber-5", P["phone"], "phone", name="phoneNumber"),
        head("Address"),
        text("Address Line 1", "addressLine1-6", P["street"], name="addressLine1"), text("City", "city-7", P["city"], name="city"),
        text("ZIP Code", "postalCode-8", P["zip"], name="postalCode"),
        head("Application Questions"),
        yesno("Are you legally authorized to work in the United States?", "Yes", "legal", field="cx-select-pills"),
        yesno("Will you now or in the future require sponsorship?", "No", "legal", field="cx-select-pills"),
        file("Resume", "oracle-resume"),
        head("E-Signature"),
        text("Full Name", "fullName-9", P["full"], name="fullName"),
    ]),
    "jobvite": ("jobs.jobvite.com", "/zztest/job/o27009/apply", "Jobvite", [
        text("First Name", "jv-firstName", P["first"], name="firstName"), text("Last Name", "jv-lastName", P["last"], name="lastName"),
        text("Email", "jv-email", P["email"], "email", name="email"), text("Phone", "jv-phone", P["phone"], "phone", name="phone"),
        text("Address", "jv-address", P["street"], name="address"), text("City", "jv-city", P["city"], name="city"),
        select("State", "jv-state", STATES, P["state"], name="state"), text("Postal Code", "jv-zip", P["zip"], name="postalCode"),
        text("LinkedIn", "jv-linkedin", P["linkedin"], name="linkedin"),
        file("Resume", "jv-resume"),
        radios("Are you legally authorized to work in the U.S.?", "jv-q1", YES_NO, "Yes", "legal"),
        radios("Will you require sponsorship?", "jv-q2", YES_NO, "No", "legal"),
        EEO_SELECTS(),
    ]),
    "workable": ("apply.workable.com", "/zztest/j/27010/apply", "Workable", [
        head("Personal information"),
        text("First name", "firstname", P["first"]), text("Last name", "lastname", P["last"]),
        text("Email", "wk-email", P["email"], "email", name="email", input_type="email"),
        w("iti", "Phone", P["digits"], "phone", id="wk-phone"),
        text("Address", "wk-address", P["street"], name="address"),
        head("Profile"),
        file("Resume", "wk-resume", name="resume"),
        area("Summary", "wk-summary", "re:^ML engineer who ships", "textarea", name="summary"),
        jobs_repeater(), schools_repeater(),
        head("Details"),
        text("Expected salary", "ca_10626", P["salary"], name="ca_10626"),
        radios("Are you eligible to work in the country you are applying?", "ca_10628", YES_NO, "Yes", "legal"),
    ]),
    "bamboohr": ("zztest.bamboohr.com", "/careers/27011", "BambooHR", [
        text("First Name", "firstName", P["first"]), text("Last Name", "lastName", P["last"]),
        text("Email", "bh-email", P["email"], "email", name="email"), text("Phone", "bh-phone", P["phone"], "phone", name="phone"),
        text("Address", "streetAddress", P["street"]), text("City", "bh-city", P["city"], name="city"),
        select("State", "bh-state", STATES, P["state"], name="state"), text("ZIP", "bh-zip", P["zip"], name="zip"),
        file("Resume", "bh-resume", name="resumeFileId"),
        text("Date Available", "dateAvailable", "re:^\\d{2}/\\d{2}/\\d{4}$", "date", placeholder="mm/dd/yyyy"),
        text("Desired Pay", "desiredPay", P["salary"]),
        text("LinkedIn URL", "linkedinUrl", P["linkedin"]),
        radios("Are you legally authorized to work in the U.S. for any employer?", "customQuestionAnswers.yes_no_183", YES_NO, "Yes", "legal"),
        radios("Will you now or in the future require employment visa sponsorship?", "customQuestionAnswers.yes_no_184", YES_NO, "No", "legal"),
        EEO_SELECTS(),
    ]),
    "breezy": ("zztest.breezy.hr", "/p/27012-ml-engineer/apply", "Breezy", [
        text("Full Name", "bz-name", P["full"], name="name"), text("Email Address", "bz-email", P["email"], "email", name="email_address"),
        text("Phone Number", "bz-phone", P["phone"], "phone", name="phone_number"),
        file("Resume", "bz-resume", name="resume"),
        jobs_repeater(), schools_repeater(),
        area("Summary", "bz-summary", "re:^ML engineer who ships", "textarea", name="summary"),
        area("Cover Letter", "bz-cover", "", "textarea", name="cover_letter"),
        w("select2", "Are you legally authorized to work in the US?", "Yes", "legal", options="Yes|No", id="bz-q1"),
    ]),
    "jazzhr": ("zztest.applytojob.com", "/apply/27013/ML-Engineer", "JazzHR", [
        text("First Name", "resumator-firstname-value", P["first"]), text("Last Name", "resumator-lastname-value", P["last"]),
        text("Email Address", "resumator-email-value", P["email"], "email"), text("Phone", "resumator-phone-value", P["phone"], "phone"),
        text("Address", "resumator-address-value", P["street"]), text("City", "resumator-city-value", P["city"]),
        text("State/Province", "resumator-state-value", "re:^(Missouri|MO)$"), text("Postal", "resumator-postal-value", P["zip"]),
        file("Resume", "resumator-resume-value"),
        text("Desired Salary", "resumator-salary-value", P["salary"]),
        select("Are you legally authorized to work in the United States?", "resumator-questionnaire-1", YES_NO, "Yes", "legal"),
        text("LinkedIn Profile URL", "resumator-linkedin-value", P["linkedin"]),
        EEO_SELECTS(),
    ]),
    "paylocity": ("recruiting.paylocity.com", "/Recruiting/Jobs/Apply/27014", "Paylocity", [
        text("First Name", "pl-first", P["first"], name="FirstName"), text("Last Name", "pl-last", P["last"], name="LastName"),
        text("Email", "pl-email", P["email"], "email", name="Email"), text("Phone", "pl-phone", P["phone"], "phone", name="Phone"),
        text("Street Address", "pl-addr", P["street"], name="Street"), text("Locality", "pl-loc", P["city"], name="Locality"),
        text("Administrative Area", "pl-aa", P["state"], name="AdministrativeArea"), text("Postal Code", "pl-pc", P["zip"], name="PostalCode"),
        file("Resume", "pl-resume", name="Resume"),
        select("Are you legally authorized to work in the United States?", "pl-q1", YES_NO, "Yes", "legal"),
        select("Will you now or in the future require sponsorship?", "pl-q2", YES_NO, "No", "legal"),
        area("Cover Letter", "pl-cover", "", "textarea", name="CoverLetter"),
    ]),
    "adp": ("workforcenow.adp.com", "/mascsr/default/mdf/recruitment/recruitment.html", "ADP", [
        shadow("First Name", "firstName", P["first"]), shadow("Last Name", "lastName", P["last"]),
        shadow("Email", "email", P["email"], "email"), shadow("Mobile Phone", "mobilePhone", P["phone"], "phone"),
        select("Country", "adp-country", ["Canada", "United States"], "United States"),
        text("Zip code", "adp-zip", P["zip"]), text("City", "adp-city", P["city"]),
        select("State", "adp-state", STATES, P["state"]),
        file("Upload Resume", "adp-resume"),
        radios("Are you legally authorized to work in the United States?", "adp-q1", YES_NO, "Yes", "legal"),
        radios("Will you now or in the future require sponsorship?", "adp-q2", YES_NO, "No", "legal"),
        check("I have read and agree to the privacy policy", "adp-terms", "checked", "checkbox"),
    ]),
    "rippling": ("ats.rippling.com", "/zztest/jobs/27016/apply", "Rippling", [
        w("react", "First name", P["first"], "text", name="firstName"), w("react", "Last name", P["last"], "text", name="lastName"),
        w("react", "Email", P["email"], "email", name="email", itype="email"),
        w("react", "Phone number", P["phone"], "phone", name="phoneNumber", itype="tel"),
        w("react", "LinkedIn URL", P["linkedin"], "text", name="linkedInUrl"),
        w("typeahead", "Location", "Springfield, Missouri, United States", "typeahead", id="rp-location", options="|".join(CITIES)),
        file("Resume", "rp-resume"),
        yesno("Are you legally authorized to work in the United States?", "Yes", "legal"),
        yesno("Will you now or in the future require visa sponsorship?", "No", "legal"),
        w("react", "What are your salary expectations?", P["salary"], "text", name="salary"),
        rs("Gender", GENDERS, "Male", "eeo", id="rp-gender"),
        rs("Veteran status", VETERANS, "I am not a protected veteran", "eeo", id="rp-veteran"),
    ]),
    "dover": ("app.dover.com", "/apply/zztest/27017", "Dover", [
        w("react", "First Name", P["first"], "text", name="firstName"), w("react", "Last Name", P["last"], "text", name="lastName"),
        w("react", "Email", P["email"], "email", name="email", itype="email"),
        w("react", "Phone Number", P["phone"], "phone", name="phoneNumber", itype="tel"),
        w("react", "LinkedIn Profile", P["linkedin"], "text", name="linkedinUrl"),
        file("Resume", "dover-resume"),
        yesno("Are you authorized to work in the US?", "Yes", "legal"),
        yesno("Do you require visa sponsorship?", "No", "legal"),
        YEARS_Q,
    ]),
    "wellfound": ("wellfound.com", "/jobs/27018-ml-engineer/apply", "Wellfound", [
        yesno("Are you willing to relocate?", "Yes", "yesno"),
        yesno("Do you now or will you in the future require sponsorship for employment visa status?", "No", "legal"),
        select("When can you start?", "wf-start", ["Immediately", "In 2 weeks", "In 1 month", "In 2+ months"], "In 2 weeks", "select"),
        file("Resume", "wf-resume"),
        area("Write a note to ZZ Test Corp: why are you interested?", "wf-note", "", "textarea"),
    ]),
    "generic": ("careers.zz-unknown.example", "/jobs/27019/apply", "An unknown site", [
        '<div class="row"><input id="g-first" placeholder="First name" data-expect="Zz" data-type="text"></div>',
        '<div class="row"><input id="g-last" placeholder="Last name" data-expect="Testcase" data-type="text"></div>',
        '<div class="row"><span class="q">Your e-mail</span><input id="g-mail" type="email" data-expect="zz.testcase@example.com" data-type="email"></div>',
        w("iti", "Mobile", P["digits"], "phone", id="g-phone"),
        w("select2", "State", P["state"], "select", options="|".join(STATES), id="g-state"),
        text("Postcode", "g-zip", P["zip"]),
        w("react", "LinkedIn", P["linkedin"], "text", name="profile_url_linkedin"),
        file("Upload your CV", "g-cv", name="cv"),
        yesno("Do you have the right to work in the United States?", "Yes", "legal"),
        yesno("Would you need sponsorship (now or in the future)?", "No", "legal"),
        jobs_repeater(), schools_repeater(),
        area("About you", "g-about", "re:^ML engineer who ships", "textarea"),
        YEARS_Q,
        SENSITIVE,
        check("I agree to the terms and conditions", "g-terms", "checked", "checkbox"),
    ]),
}

PAGE = """<!doctype html>
<!-- GOAL27 matrix fixture: {name}. Synthetic: modeled on {name}'s public apply form (its field names, label wording and
     widget kinds); our own markup, the fake person's values only. Regenerate with make_fixtures.py. -->
<html lang="en"><head><meta charset="utf-8"><title>{title}: ML Engineer</title>
<meta property="og:title" content="ML Engineer"><meta property="og:site_name" content="ZZ Test Corp">
<style>body{{font:14px sans-serif;padding:20px 24px 120px;max-width:760px}}.field,.application-question,._fieldEntry,.iCIMS_InfoField,.row,fieldset{{margin:8px 0}}
label,legend,._label{{display:block;font-weight:600}}input[type=text],input:not([type]),input[type=email],input[type=tel],textarea,select{{width:340px;padding:4px}}
.select__control{{border:1px solid #888;width:340px;min-height:28px;position:relative;display:flex}}.select__value-container{{flex:1;display:flex}}
.select__input{{border:0;width:100%}}.select__menu{{position:absolute;background:#fff;border:1px solid #888;width:340px;z-index:9}}.select__option{{padding:3px 6px;cursor:pointer}}
.typeahead{{position:relative}}.typeahead-results{{position:absolute;background:#fff;border:1px solid #888;list-style:none;margin:0;padding:0;width:340px;z-index:9}}
.typeahead-results li{{padding:3px 6px;cursor:pointer}}._yesno button{{margin-right:6px;padding:4px 14px}}._active{{background:#1f5fbf;color:#fff}}
.entry{{border:1px solid #ccc;padding:8px;margin:8px 0}}.select2-selection{{display:inline-block;border:1px solid #888;width:340px;padding:4px}}</style></head>
<body><main><h1>ML Engineer</h1><p>ZZ Test Corp · Springfield, MO</p>
<form id="application-form" action="#" onsubmit="return false">
{body}
{submit}
</form></main>
{shadow}<script src="/__sim/ats_sim.js"></script></body></html>
"""

IFRAME = """<!doctype html>
<!-- GOAL27 matrix fixture: iCIMS. The career page holds the application in iCIMS's own frame (icims_content_iframe). -->
<html lang="en"><head><meta charset="utf-8"><title>ML Engineer - Careers</title></head>
<body><h1>ML Engineer</h1><iframe id="icims_content_iframe" src="/jobs/27005/ml-engineer/candidate/form?in_iframe=1"
style="width:820px;height:2400px;border:0"></iframe></body></html>
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (host, path, title, body) in SITES.items():
        if body == "iframe":
            (OUT / f"{name}.html").write_text(IFRAME)
            continue
        parts = "\n".join(body)
        (OUT / f"{name}.html").write_text(PAGE.format(name=title, title=esc(title), body=parts, submit=SUBMIT,
                                                      shadow=SHADOW_JS if "<x-input" in parts else ""))
    print(f"{len(SITES)} pages in {OUT}")


if __name__ == "__main__":
    main()
