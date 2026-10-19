"""The Autofill Chrome extension's side of the app.

The extension (extension/ in this repo) fills job applications with the person's tailored resume and their saved
answers. job-hunt-agent tells it which job a page is (match), which resume to upload (resume), what to answer (bank: the
answer bank, one per person) and keeps what it could not answer as "Needs you" questions (questions) for the
Answers page. Applied pages go through the same code as Mark applied (applyback.confirm). Routes: api.py.

Hard lines kept here: a legal / government / EEO question is answered only from a saved answer (never a default),
nothing about the person's job-site account (email, password) ever reaches job-hunt-agent, and the fill log keeps counts,
never a field's value.
"""
