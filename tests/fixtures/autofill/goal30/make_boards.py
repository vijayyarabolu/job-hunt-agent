"""GOAL30 §0 / §0b: one apply page per board, in that board's shape, with what went wrong on NVIDIA: job and school
entries (Greenhouse's Employment / Education, iCIMS' Work Experience / Education with City, State, Country) and the
EEO questions (Greenhouse's and Lever's selects, Ashby's radio survey, iCIMS' selects). Lever's and Ashby's forms have no
job or school entries, so theirs check the EEO part and a top Location (the user's, from the resume) only. Constructed from
each board's field names (no saved page had these sections); run: python make_boards.py (writes boards/*.html)."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent / "boards"
GENDER = ["Male", "Female", "Decline To Self Identify"]
RACE = ["American Indian or Alaskan Native", "Asian", "Black or African American", "Hispanic or Latino", "White",
        "Native Hawaiian or Other Pacific Islander", "Two or More Races", "Decline To Self Identify"]
HISPANIC = ["Yes", "No", "Decline To Self Identify"]
VETERAN = ["I identify as one or more of the classifications of protected veteran", "I am not a protected veteran",
           "I don't wish to answer"]
DISABILITY = ["Yes, I have a disability, or have had one in the past",
              "No, I do not have a disability and have not had one in the past", "I do not want to answer"]


def text(fid, label, kind="text"):
    return f'<div class="field"><label for="{fid}">{escape(label)}</label><input type="{kind}" id="{fid}" name="{fid}"></div>'


def select(fid, label, options):
    opts = "".join(f"<option>{escape(o)}</option>" for o in options)
    return f'<div class="field"><label for="{fid}">{escape(label)}</label><select id="{fid}" name="{fid}"><option value="">Select...</option>{opts}</select></div>'


def check(fid, label):
    return f'<div class="field"><input type="checkbox" id="{fid}" name="{fid}"><label for="{fid}">{escape(label)}</label></div>'


def radios(name, question, options):
    items = "".join(f'<label><input type="radio" name="{name}" value="{escape(o)}"> {escape(o)}</label>' for o in options)
    return f'<fieldset class="field"><legend>{escape(question)}</legend>{items}</fieldset>'


def page(title, form, root='id="application-form"'):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title></head><body>\n'
            f'<!-- GOAL30 §0 / §0b fixture ({escape(title)}): made by make_boards.py, ZZ_TEST person only -->\n'
            f'<h1>{escape(title)}</h1><form {root}>\n{form}\n</form></body></html>\n')


def greenhouse():
    jobs = "".join(f'<div class="employment" role="group" aria-label="Employment {n + 1}"><h4>Employment {n + 1}</h4>'
                   f'{text(f"employment--company-{n}", "Company name")}{text(f"employment--title-{n}", "Title")}'
                   f'{check(f"employment--current-role-{n}", "Current role")}</div>' for n in range(2))
    school = (f'<div class="education" role="group" aria-label="Education 1"><h4>Education 1</h4>'
              f'{text("education--school-0", "School")}{select("education--degree-0", "Degree", ["Bachelor's Degree", "Master's Degree"])}</div>')
    eeo = (select("gender", "Gender", GENDER) + select("hispanic_ethnicity", "Are you Hispanic/Latino?", HISPANIC)
           + select("race", "Please identify your race", RACE) + select("veteran_status", "Veteran Status", VETERAN)
           + select("disability_status", "Disability Status", DISABILITY))
    return page("Apply for ML Engineer", f'{text("first_name", "First Name")}{text("candidate-location", "Location (City)")}'
                f'<h3>Employment</h3>{jobs}<h3>Education</h3>{school}<h3>Voluntary Self-Identification</h3>{eeo}')


def lever():
    eeo = (select("eeo[gender]", "Gender", GENDER) + select("eeo[race]", "Race", RACE)
           + select("eeo[veteran]", "Veteran status", VETERAN))
    return page("ML Engineer", f'{text("name", "Full name")}{text("location", "Current location")}'
                f'<h4>U.S. Equal Employment Opportunity information</h4>{eeo}', 'class="application-form"')


def ashby():
    eeo = (radios("gender", "Gender", GENDER) + radios("race", "Race", RACE) + radios("veteranStatus", "Veteran Status", VETERAN))
    return page("ML Engineer", f'<div class="ashby-application-form-container">{text("_systemfield_name", "Name")}'
                f'{text("_systemfield_location", "Location")}<h3>U.S. Equal Employment Opportunity</h3>{eeo}</div>')


def icims():
    def place(prefix, n):
        return (f'{text(f"{prefix}_{n}_City", "City")}{select(f"{prefix}_{n}_State", "State", ["Kansas", "Missouri", "Illinois", "California"])}'
                f'{select(f"{prefix}_{n}_Country", "Country", ["United States", "India", "Canada"])}')
    jobs = "".join(f'<div class="iCIMS_TableRow" role="group" aria-label="Work Experience {n + 1}"><h4>Work Experience {n + 1}</h4>'
                   f'{text(f"WorkExperience_{n}_Employer", "Employer")}{text(f"WorkExperience_{n}_Title", "Job Title")}'
                   f'{place("WorkExperience", n)}{check(f"WorkExperience_{n}_Current", "This is my current job")}</div>' for n in range(2))
    school = (f'<div class="iCIMS_TableRow" role="group" aria-label="Education 1"><h4>Education 1</h4>'
              f'{text("Education_0_School", "School")}{place("Education", 0)}</div>')
    eeo = (select("EEO_Gender", "Gender", GENDER) + select("EEO_Race", "Race/Ethnicity", RACE)
           + select("EEO_Veteran", "Veteran Status", VETERAN) + select("EEO_Disability", "Disability Status", DISABILITY))
    return page("iCIMS", f'<div id="iCIMS_MainWrapper">{text("FirstName", "First Name")}{text("AddressCity", "City")}'
                f'<h3>Work Experience</h3>{jobs}<h3>Education</h3>{school}<h3>Voluntary Self-Identification</h3>{eeo}</div>', "")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, make in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby), ("icims", icims)):
        (OUT / f"{name}.html").write_text(make())
