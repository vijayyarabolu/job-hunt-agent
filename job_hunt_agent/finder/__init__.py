"""GOAL6 §4 Find contacts (decision 012): free signals first, paid providers only on a click.

signals  people named in the JD, the company's team page, LinkedIn people-search links, the "Open in Apollo" link,
         the company domain guess and the email-pattern guess (no network except the team page, on a click)
apollo   organization enrich + organization top people (per-person key, documented cost shown first)
hunter   domain search + email finder (off until hunter.<person>_api_key exists)
rank     likely role (recruiter / hiring manager / leader), why they match, confidence
ledger   the cache (a lookup is never paid twice) and the per-person daily credit cap
service  the panel, a lookup, Add contact

Nothing here is imported by the scheduled fetch: credits are only ever spent from the web UI's button.
"""
