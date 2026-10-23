"""GOAL31 §2: Lever's and Breezy's EEO sections in each board's markup, with the race choices EXACTLY as the user's
autofill_choices stored them (Lever 2026-10-15T17:55Z, Breezy 2026-10-15T05:02Z); the other questions use the US
federal (OFCCP) wording these boards show, since no list of theirs was stored. ashby_unify.html beside this file is the
real Ashby page itself. Run: python make_eeo.py (writes lever.html, breezy.html)."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
LEVER_RACE = ["Select ...", "Hispanic or Latino", "White (Not Hispanic or Latino)", "Black or African American (Not Hispanic or Latino)",
              "Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino)", "Asian (Not Hispanic or Latino)",
              "American Indian or Alaska Native (Not Hispanic or Latino)", "Two or More Races (Not Hispanic or Latino)",
              "Decline to self-identify"]
BREEZY_RACE = ["White (not Hispanic or Latino)", "Black or African-American (not Hispanic or Latino)", "Asian (not Hispanic or Latino)",
               "American Indian or Alaskan Native (not Hispanic or Latino)",
               "Native Hawaiian or other Pacific islander (not Hispanic or Latino)",
               "Two or more races/ethnicities (not Hispanic or Latino)",
               "Hispanic or Latino (including Black individuals whose origins are Hispanic)", "I don't wish to answer"]
GENDER = ["Male", "Female", "Decline to self-identify"]
VETERAN = ["I identify as one or more of the classifications of protected veteran", "I am not a protected veteran",
           "I decline to self-identify for protected veteran status"]
DISABILITY = ["Yes, I have a disability (or previously had a disability)", "No, I do not have a disability",
              "I do not want to answer"]


def page(title, body):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title></head><body>\n'
            f'<!-- GOAL31 §2 fixture: made by make_eeo.py, ZZ_TEST only -->\n{body}\n</body></html>\n')


def lever_q(name, label, options):
    opts = "".join(f'<option value="{"" if o.startswith("Select") else escape(o)}">{escape(o)}</option>' for o in options)
    return (f'<li class="application-question"><div class="application-label"><div class="text">{escape(label)}</div></div>'
            f'<div class="application-field"><select name="{name}">{opts}</select></div></li>')


def lever():
    eeo = (lever_q("eeo[gender]", "Gender", ["Select ...", *GENDER]) + lever_q("eeo[race]", "Race", LEVER_RACE)
           + lever_q("eeo[veteran]", "Veteran status", ["Select ...", *VETERAN])
           + lever_q("eeo[disability]", "Disability status", ["Select ...", *DISABILITY]))
    return page("ZZ Test Corp - ML Engineer", '<form class="application-form" id="application-form"><div class="section application-form">'
                '<ul><li class="application-question"><div class="application-label">Full name</div><div class="application-field">'
                '<input type="text" name="name"></div></li></ul></div><div class="section eeo-section">'
                f'<h4>U.S. Equal Employment Opportunity information</h4><ul>{eeo}</ul></div></form>')


def breezy_q(fid, label, options):
    opts = "".join(f"<option>{escape(o)}</option>" for o in options)
    return (f'<div class="form-group"><label for="{fid}">{escape(label)}</label>'
            f'<select id="{fid}" name="{fid}" class="form-control"><option value="">Select an option</option>{opts}</select></div>')


def breezy():
    eeo = (breezy_q("eeo_gender", "Gender", GENDER) + breezy_q("eeo_race", "Race / Ethnicity", BREEZY_RACE)
           + breezy_q("eeo_veteran", "Veteran Status", VETERAN) + breezy_q("eeo_disability", "Disability Status", DISABILITY))
    return page("ML Engineer | ZZ Test Corp", '<form id="application"><div class="form-group"><label for="name">Full Name</label>'
                f'<input type="text" id="name" name="name" class="form-control"></div><h3>Equal Employment Opportunity</h3>{eeo}</form>')


if __name__ == "__main__":
    (OUT / "lever.html").write_text(lever())
    (OUT / "breezy.html").write_text(breezy())
