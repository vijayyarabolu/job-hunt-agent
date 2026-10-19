-- job-hunt-agent schema (decision 001). Timestamps: UTC 'YYYY-MM-DDTHH:MM:SSZ'. Nothing is ever deleted.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS boards (
  board TEXT PRIMARY KEY,                 -- ats:key
  ats TEXT NOT NULL,
  key TEXT NOT NULL,                      -- token, or tenant/wdN/site for Workday
  company TEXT NOT NULL,
  board_name TEXT,
  tier INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  review INTEGER NOT NULL DEFAULT 0,
  baselined_at TEXT,
  baselined_scopes TEXT NOT NULL DEFAULT '[]',
  fail_streak INTEGER NOT NULL DEFAULT 0,
  last_ok_at TEXT,
  last_ok_seen INTEGER NOT NULL DEFAULT 0,
  -- GOAL2 B (decision 005, migration 10): tiered conditional fetching and sticky drops
  etag TEXT,                              -- validators of the last OK full list response
  last_mod TEXT,
  last_fetch TEXT,                        -- last time a fetch of this board finished (drives "due")
  last_full_at TEXT,                      -- last unconditional list GET (one per day)
  terms_at TEXT,                          -- Workday: last daily searchText pass
  dead_streak INTEGER NOT NULL DEFAULT 0, -- consecutive 404/410 answers
  dropped TEXT,                           -- why the board is no longer fetched ('dead: ...', 'robots: ...')
  dropped_at TEXT,
  segment TEXT,                           -- big | startup | public_sector | other | unknown (segments.py)
  source TEXT,                            -- curated | cc | seed
  website TEXT,
  wd_total INTEGER                        -- Workday: first page's total (2000 = capped)
);

CREATE TABLE IF NOT EXISTS jobs (
  uid TEXT PRIMARY KEY,                   -- ats:key:ext_id
  board TEXT NOT NULL REFERENCES boards(board),
  ext_id TEXT NOT NULL,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  families TEXT NOT NULL DEFAULT ',',     -- ',ml_engineer,genai_engineer,'
  location TEXT,
  country TEXT,
  is_us INTEGER,
  workplace TEXT,
  url TEXT,
  apply_url TEXT,
  req_id TEXT,
  has_team INTEGER,
  salary_min REAL,
  salary_max REAL,
  salary_ccy TEXT,
  min_years INTEGER,
  is_talent_pool INTEGER,
  employment_type TEXT,                 -- full_time | contract | contract_to_hire | part_time | internship | NULL
  staffing INTEGER NOT NULL DEFAULT 0,  -- resume-harvesting signal kinds (0-3), signals.staffing_signals
  sponsorship TEXT,                     -- no | citizen | yes | NULL (informational label only, signals.sponsorship)
  posted_at TEXT,                         -- latest observed
  first_posted_at TEXT,                   -- min(observed posted_at), never raised
  precision TEXT NOT NULL CHECK (precision IN ('exact', 'day', 'updated', 'unknown')),
  bumped_at TEXT,
  seed INTEGER NOT NULL DEFAULT 0,
  chain_origin TEXT,                      -- oldest date of chained priors (decision 002): never fresh when set
  chain_n INTEGER NOT NULL DEFAULT 0,      -- number of chained priors (closed <= 90 d, or open with same req_id)
  description BLOB,                       -- zlib-compressed text
  content_hash TEXT NOT NULL,
  repost_key TEXT NOT NULL,
  repost_count INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  miss_count INTEGER NOT NULL DEFAULT 0,
  missing_since TEXT,
  closed_at TEXT,
  stale INTEGER NOT NULL DEFAULT 0,
  raw_fp INTEGER                          -- crc32 of the raw posting JSON (unchanged = fast path)
);
CREATE INDEX IF NOT EXISTS jobs_open ON jobs(closed_at, first_posted_at);
CREATE INDEX IF NOT EXISTS jobs_rk ON jobs(repost_key, first_seen);
CREATE INDEX IF NOT EXISTS jobs_board ON jobs(board, closed_at);
CREATE INDEX IF NOT EXISTS jobs_fresh ON jobs(first_posted_at) WHERE closed_at IS NULL AND bumped_at IS NULL;
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company, closed_at);   -- GOAL7: job detail's company panel
-- GOAL21 W17: the ranking reads ~15k matched jobs of ~1M; this covers the family filter, momentum and the open check,
-- so neither query reads the 1.6 GB of job rows (a cold first page went from ~30 s to ~3 s)
CREATE INDEX IF NOT EXISTS jobs_fam ON jobs(families, closed_at, seed, board, first_posted_at, company);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY,
  run_id INTEGER,
  uid TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('new', 'changed', 'closed', 'reopened', 'repost', 'bumped', 'stale')),
  detail TEXT
);
CREATE INDEX IF NOT EXISTS ev_uid ON job_events(uid, at);

CREATE TABLE IF NOT EXISTS jd_versions (
  uid TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (uid, content_hash)
);

CREATE TABLE IF NOT EXISTS profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  last_visit TEXT
);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL REFERENCES profiles(id),
  job_uid TEXT,
  import_key TEXT UNIQUE,
  company TEXT, role_title TEXT, date_applied TEXT, jd TEXT, resume TEXT,
  target_name TEXT, target_role TEXT, linkedin_request TEXT, email_address TEXT, email_sent TEXT,
  gemini_chat TEXT, follow_up_1 TEXT, follow_up_2 TEXT, status_ar TEXT,
  applied_on TEXT,                        -- ISO date parsed from date_applied
  status TEXT NOT NULL DEFAULT 'saved' CHECK (status IN
    ('saved', 'tailored', 'applied', 'screen', 'interview', 'offer', 'rejected', 'ghosted')),
  coverage REAL,
  folder TEXT,
  raw_json TEXT,                          -- original xlsx cells with types
  edited TEXT NOT NULL DEFAULT '[]',      -- tracker fields changed in the app
  xlsx_row INTEGER,
  sheet_status TEXT,                      -- record: Status(A/R) as last exported/imported ('' = empty)
  followups_suppressed INTEGER NOT NULL DEFAULT 0,
  apply_clicked_at TEXT,
  updated_at TEXT,
  notes TEXT,                             -- the Sheet's Notes column
  field_at TEXT NOT NULL DEFAULT '{}',    -- {field: time} of the last app-side edit (Sheet last-writer-wins)
  resume_manual TEXT,                     -- what the user typed/pasted into the Sheet's Resume cell (kept)
  downloaded_at TEXT,                     -- last PDF/DOCX download from the web UI (decision 006 condition 4)
  -- GOAL3 tracker (migration 11): the job's facts copied in when the row is made (the posting may close), the row's
  -- origin, and Jordan's extra columns as real fields (her workbook's Address / Team Name)
  job_url TEXT,
  apply_url TEXT,
  location TEXT,
  workplace TEXT,
  salary TEXT,                            -- display text, e.g. '$150k–$190k'
  employment_type TEXT,                   -- signals.EMPLOYMENT_LABELS keys
  source TEXT,                            -- feed | manual | import
  address TEXT,
  team_name TEXT,
  resume_used TEXT,                       -- GOAL4 §1: id of the resume used (profile.yaml resumes:)
  match_pct INTEGER,                      -- that resume's keyword % when marked applied
  resume_missing INTEGER NOT NULL DEFAULT 0,  -- GOAL6 §1: marked applied without uploading the resume file
  jd_text TEXT                               -- GOAL6: the full JD; the JD cell holds the linked title ("JD Text" column)
);
CREATE UNIQUE INDEX IF NOT EXISTS app_profile_job ON applications(profile, job_uid) WHERE job_uid IS NOT NULL;

-- GOAL3: several contacts per application, each with its own sent / replied marks. Rows with origin 'legacy' are
-- derived from the tracker's packed cells ("A, B, C" / "y,y,y") and re-derived on import; once the user edits an
-- application's contacts in the app they become 'app' rows and the packed cells are written from them.
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  pos INTEGER NOT NULL DEFAULT 0,
  name TEXT, role TEXT, email TEXT, linkedin TEXT,
  linkedin_sent TEXT,                     -- '' / the tracker's text ('Sent', 'Y') / the date it was ticked
  email_sent TEXT,
  replied TEXT,
  notes TEXT,
  origin TEXT NOT NULL DEFAULT 'app' CHECK (origin IN ('legacy', 'app')),
  updated_at TEXT,
  source TEXT                             -- GOAL6 §4: where the person was found (JD, Apollo, LinkedIn-manual, ...)
);
CREATE INDEX IF NOT EXISTS contacts_app ON contacts(app_id, pos);

-- GOAL3: Jordan's vendor / recruiter list (her workbook's Sheet2), one row per vendor contact.
CREATE TABLE IF NOT EXISTS vendors (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL REFERENCES profiles(id),
  pos INTEGER NOT NULL DEFAULT 0,
  company TEXT, target_name TEXT, target_role TEXT, linkedin TEXT, email TEXT, email_sent TEXT, notes TEXT,
  origin TEXT NOT NULL DEFAULT 'import' CHECK (origin IN ('import', 'app')),   -- 'app' once edited in the UI
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS vendors_profile ON vendors(profile, pos);

CREATE TABLE IF NOT EXISTS application_events (
  id INTEGER PRIMARY KEY,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  at TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  source TEXT NOT NULL CHECK (source IN ('ui', 'cli', 'import', 'sheet', 'google', 'email')),
  snapshot TEXT
);

CREATE TABLE IF NOT EXISTS triage_events (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  uid TEXT NOT NULL,
  at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('saved', 'dismissed', 'restored'))
);
CREATE VIEW IF NOT EXISTS triage_now AS
  SELECT * FROM triage_events WHERE id IN (SELECT max(id) FROM triage_events GROUP BY profile, uid);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  trigger TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  host_alerts TEXT,
  cpu_ms INTEGER,                         -- process CPU (user+sys) for the whole run
  cpu_sweep_ms INTEGER,                   -- CPU after the board loop (fits, notify, backup)
  n_req INTEGER, n_304 INTEGER, bytes INTEGER,
  budget_exhausted INTEGER NOT NULL DEFAULT 0,
  n_due INTEGER, n_taken INTEGER
);

CREATE TABLE IF NOT EXISTS fetch_runs (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  board TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'drift', 'http_error', 'skipped')),
  http_status INTEGER,
  n_raw INTEGER, n_jobs INTEGER, n_match INTEGER, n_parse_err INTEGER,
  n_new INTEGER, n_closed INTEGER,
  latency_ms INTEGER,
  error TEXT,
  PRIMARY KEY (run_id, board)
);
CREATE INDEX IF NOT EXISTS fetch_runs_board ON fetch_runs(board, run_id DESC);

CREATE TABLE IF NOT EXISTS notifications (
  profile TEXT NOT NULL,
  uid TEXT NOT NULL,
  channel TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  PRIMARY KEY (profile, uid, channel)
);

CREATE TABLE IF NOT EXISTS discover_probes (
  ats TEXT NOT NULL,
  slug TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  hit INTEGER NOT NULL,
  n_jobs INTEGER,
  board_name TEXT,
  website TEXT,
  source TEXT,                            -- cc | seed | tracker | refresh
  error TEXT,                             -- last transient failure (never cached as a miss)
  next_try TEXT,                          -- retry after a transient failure
  us60 INTEGER,                           -- US postings in the last 60 days at verification
  newest_us TEXT,                         -- newest US posting date seen
  name_src TEXT,                          -- where board_name came from (ats | page | og | list | slug)
  segment TEXT,
  verified_at TEXT,
  PRIMARY KEY (ats, slug)
);

CREATE TABLE IF NOT EXISTS robots (           -- robots.txt cache for the per-request gate (decision 005 run 2)
  host TEXT PRIMARY KEY,
  status INTEGER,                         -- HTTP status of robots.txt (4xx = no rules, RFC 9309)
  fetched_at TEXT NOT NULL,
  sha TEXT,
  body TEXT
);

CREATE TABLE IF NOT EXISTS run_buckets (      -- per rate bucket per fetch run (decision 005 run 2, conditions 1-3)
  run_id INTEGER NOT NULL,
  bucket TEXT NOT NULL,                   -- boards-api.greenhouse.io, wd5.myworkdayjobs.com, ...
  n_req INTEGER, n_304 INTEGER,
  busy_s REAL,                            -- seconds the bucket had a board in flight
  budget_s REAL,                          -- the run's per-bucket deadline
  est_need_s REAL,                        -- seconds the due boards were estimated to need (EWMA cost / in-flight)
  t1_due INTEGER, t1_done INTEGER,
  taken INTEGER, carried INTEGER,
  PRIMARY KEY (run_id, bucket)
);

CREATE TABLE IF NOT EXISTS host_state (       -- per-host backoff after 403/429 runs (decision 005)
  host TEXT PRIMARY KEY,
  until TEXT,
  strikes INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS fits (            -- fetch-time fit cache per profile (decision 002)
  profile TEXT NOT NULL,
  uid TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  lexicon_hash TEXT NOT NULL,
  baseline_hash TEXT NOT NULL,
  dealbreaker_hash TEXT NOT NULL,
  jd_state TEXT NOT NULL CHECK (jd_state IN ('ok', 'thin', 'none', 'unreadable')),
  jd_len INTEGER NOT NULL DEFAULT 0,
  terms TEXT NOT NULL DEFAULT '[]',       -- [[canonical, weight, hitmask, need, count, first, forms, litmask], ...]
  dealbreaker TEXT,                       -- matched pattern, or NULL
  computed_at TEXT NOT NULL,
  PRIMARY KEY (profile, uid)
);

CREATE VIEW IF NOT EXISTS followups_due AS
  SELECT a.*,
         date(a.applied_on, '+3 days') AS due_1,
         date(a.applied_on, '+6 days') AS due_2
    FROM applications a
   WHERE a.status = 'applied'
     AND a.followups_suppressed = 0
     AND a.applied_on >= date('now', 'localtime', '-21 days')
     AND a.applied_on <= date('now', 'localtime')
     AND ((coalesce(a.follow_up_1, '') = '' AND date(a.applied_on, '+3 days') <= date('now', 'localtime'))
       OR (coalesce(a.follow_up_2, '') = '' AND date(a.applied_on, '+6 days') <= date('now', 'localtime')));

-- GOAL2 C2 (decision 006): Google Sheets tracker + resumes in Drive. The DB stays the source of truth.
CREATE TABLE IF NOT EXISTS google_state (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sheet_sync (       -- each row exactly as last pushed to / pulled from the Sheet
  app_id INTEGER PRIMARY KEY REFERENCES applications(id),
  row_key TEXT NOT NULL,                      -- the Job UID cell: job_uid, or '#<app id>' for imported rows
  row_json TEXT NOT NULL,                     -- {header: shown text}
  pushed_at TEXT NOT NULL
);

-- GOAL4 §4 (decision 008): two-way sync with each person's EXISTING Google Sheet
CREATE TABLE IF NOT EXISTS gsync_rows (       -- a Sheet row linked to an application (its hidden key: 'jh:<app id>')
  profile TEXT NOT NULL REFERENCES profiles(id),
  tab TEXT NOT NULL,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  cells TEXT NOT NULL,                        -- {header: [Sheet value, app value]} as canonical text at the last sync
  synced_at TEXT NOT NULL,
  missing_since TEXT,                         -- its row is gone from the Sheet: reported, never re-added or deleted
  PRIMARY KEY (profile, app_id)
);

CREATE TABLE IF NOT EXISTS gsync_log (        -- every pull, push, conflict (and its winner), new and missing row
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  profile TEXT NOT NULL,
  tab TEXT,
  app_id INTEGER,
  kind TEXT NOT NULL,
  column_name TEXT,
  sheet_value TEXT,
  app_value TEXT,
  winner TEXT
);
CREATE INDEX IF NOT EXISTS gsync_log_at ON gsync_log(profile, at);

CREATE TABLE IF NOT EXISTS gfiles (           -- Drive folders this app created (idempotent creation)
  key TEXT PRIMARY KEY,
  file_id TEXT NOT NULL,
  link TEXT,
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (  -- one per accepted /tailor finish with new content (re-tailor = new)
  app_id INTEGER NOT NULL REFERENCES applications(id),
  version INTEGER NOT NULL,
  content_sha TEXT NOT NULL,                  -- resume.json + template
  engine TEXT NOT NULL CHECK (engine IN ('google', 'offline')),
  stem TEXT NOT NULL,                         -- recruiter-facing file name, the same in every version
  outbox TEXT,                                -- local copy waiting for upload (data/drive_outbox/...)
  gdoc_id TEXT,
  folder_id TEXT, folder_link TEXT,
  pdf_id TEXT, pdf_link TEXT,
  docx_id TEXT, docx_link TEXT,
  jd_id TEXT, jd_link TEXT,
  g_attempts INTEGER NOT NULL DEFAULT 0,      -- Google re-render attempts of an offline render (max 3)
  flag TEXT,                                  -- e.g. 're-tailor: Google layout differs'
  created_at TEXT NOT NULL,
  uploaded_at TEXT,
  PRIMARY KEY (app_id, version),
  UNIQUE (app_id, content_sha)
);

CREATE TABLE IF NOT EXISTS resume_uploads (   -- GOAL4 §4: tracker "Upload resume"; one row per upload, never replaced
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  version INTEGER NOT NULL,                   -- 1, 2, ... per application (a re-upload is a new Drive file)
  name TEXT NOT NULL,                         -- Drive file name, e.g. Alex_Rivera_ML_Engineer_Resume(Acme).pdf
  original_name TEXT,
  mime TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  md5 TEXT NOT NULL,
  file_id TEXT NOT NULL,
  link TEXT NOT NULL,                         -- webViewLink
  folder_id TEXT,
  sheet TEXT,                                 -- where the Sheet's Resume cell was linked, or why not
  uploaded_at TEXT NOT NULL,
  removed_at TEXT,                            -- GOAL15 §4: taken off the row (× or a replacement); the file is kept
  removed_batch TEXT,                         -- the remove / replace it belonged to (Undo)
  UNIQUE (app_id, version)
);

CREATE TABLE IF NOT EXISTS edit_history (     -- GOAL6 §2: every tracker cell change (app, Sheet, undo): who, when, old -> new
  id INTEGER PRIMARY KEY,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  profile TEXT NOT NULL,
  field TEXT NOT NULL,                        -- app field: 'jd', 'address', 'status', 'contacts' (JSON list), ...
  old_value TEXT,
  new_value TEXT,
  at TEXT NOT NULL,
  who TEXT NOT NULL,                          -- the person (app edit), 'Google Sheet' (pulled), 'job-hunt-agent' (CLI)
  source TEXT NOT NULL CHECK (source IN ('app', 'sheet', 'undo')),
  batch TEXT NOT NULL,                        -- one user action (an undo reverts the whole batch)
  undone_by TEXT                              -- the batch of the undo that reverted it
);
CREATE INDEX IF NOT EXISTS edit_history_app ON edit_history(app_id, id);
CREATE INDEX IF NOT EXISTS edit_history_profile ON edit_history(profile, id);

CREATE TABLE IF NOT EXISTS jd_snapshots (     -- GOAL6 §3: the full JD kept after the company takes the posting down
  app_id INTEGER PRIMARY KEY REFERENCES applications(id),
  url TEXT,                                   -- the posting URL ("Original posting")
  text TEXT,                                  -- full JD text (the Sheet cell may be cut at 50,000 characters)
  source TEXT,                                -- feed | fetch | sheet
  state TEXT NOT NULL CHECK (state IN ('saved', 'gone', 'no_url', 'unreadable')),
  fetched_at TEXT,
  checked_at TEXT,
  detail TEXT                                 -- why gone / unreadable (HTTP status, robots, ...)
);

CREATE TABLE IF NOT EXISTS contact_lookups (  -- GOAL6 §4: one Find-contacts call, cached (never paid twice) + credit ledger
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  app_id INTEGER REFERENCES applications(id),
  provider TEXT NOT NULL,                     -- free | apollo | hunter
  call TEXT NOT NULL,                         -- e.g. organizations/enrich
  cache_key TEXT NOT NULL,                    -- e.g. the company domain
  at TEXT NOT NULL,
  credits REAL NOT NULL DEFAULT 0,            -- credits the call cost (documented price; 0 for free calls)
  ok INTEGER NOT NULL,
  result TEXT,                                -- JSON answer, never the key
  UNIQUE (profile, provider, call, cache_key)
);

CREATE TABLE IF NOT EXISTS pending_applies (  -- GOAL8 §2: Apply ↗ clicked; "Did you apply?" asked on return
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,                               -- a feed job, or
  app_id INTEGER REFERENCES applications(id), -- a tracker row with no feed job (outside jobs)
  company TEXT,
  role TEXT,
  opened_at TEXT NOT NULL,                    -- the last Apply ↗ click (UTC ISO)
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'snoozed', 'yes', 'no', 'expired')),
  answered_at TEXT,
  noted INTEGER NOT NULL DEFAULT 0            -- an expired entry was reported once ("dropped after 7 days")
);
CREATE INDEX IF NOT EXISTS pending_applies_open ON pending_applies(profile, state, opened_at);

CREATE TABLE IF NOT EXISTS rg_links (         -- GOAL8 §3: a resumegen job from a pasted JD -> its outside tracker row
  rg_id INTEGER PRIMARY KEY,                  -- resumegen's job id
  profile TEXT NOT NULL,
  app_id INTEGER NOT NULL REFERENCES applications(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tailored_scores (  -- resumegen GOAL3 §4: the match with the job's ready tailored resume
  profile TEXT NOT NULL,
  uid TEXT NOT NULL,                          -- the feed job
  rg_id INTEGER NOT NULL,                     -- resumegen's job id whose resume was scored
  stamp TEXT NOT NULL,                        -- rg id + made / updated times: a new resume gives a new stamp
  pct INTEGER,                                -- job-hunt-agent's match % with the tailored resume (NULL: unreadable file)
  base_pct INTEGER,                           -- the base match % when it was scored (the card shows the live one)
  computed_at TEXT NOT NULL,
  PRIMARY KEY (profile, uid)
);

CREATE TABLE IF NOT EXISTS mail_messages (    -- GOAL12: one job email read (never its body): what it said, what it did
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  msg_id TEXT NOT NULL,                       -- Gmail message id (hex), also the link's id
  sender TEXT,
  subject TEXT,
  sent_at TEXT NOT NULL,                      -- the email's own time (UTC ISO)
  snippet TEXT,                               -- at most 300 characters of its text (the body is never stored)
  link TEXT,                                  -- https://mail.google.com/mail/u/0/#all/<msg_id>
  kind TEXT NOT NULL,                         -- rejected / screen / interview / offer / received / other
  method TEXT NOT NULL,                       -- rule / llm / none / person; GOAL14: claude / prefilter
  company_guess TEXT,
  app_id INTEGER REFERENCES applications(id), -- the matched (or picked) application
  to_status TEXT,                             -- the status this email asks for (NULL: no change)
  state TEXT NOT NULL CHECK (state IN ('applied', 'recorded', 'needs', 'confirmed', 'dismissed', 'undone',
                                       'ignored')),
  reason TEXT,                                -- why it waits for the person / why it changed nothing
  from_status TEXT,                           -- the row's status before this email moved it (Undo puts it back)
  batch TEXT,                                 -- edit_history batch of the change
  read_at TEXT NOT NULL,
  acted_at TEXT,
  rule_kind TEXT,                             -- GOAL14: what the rules alone said (the cross-check)
  claude_reason TEXT,                         -- GOAL14: Claude's one sentence, quoting the deciding words
  confidence TEXT,                            -- GOAL14: high / medium / low
  UNIQUE (profile, msg_id)
);
CREATE INDEX IF NOT EXISTS mail_messages_day ON mail_messages(profile, sent_at);
CREATE TABLE IF NOT EXISTS jd_docs (          -- GOAL11 §1: the full JD as a Google Doc, one per application
  app_id INTEGER PRIMARY KEY REFERENCES applications(id),
  profile TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('saved', 'gone')),   -- gone: no JD text anywhere ("posting gone, no JD saved")
  file_id TEXT,                               -- the Doc (re-saving updates it in place, never a second one)
  link TEXT,                                  -- its webViewLink (the Sheet's JD cell and the app link to it)
  name TEXT,                                  -- "<Company> - <Role Title> - JD"
  sha TEXT,                                   -- sha256 of the HTML last written (unchanged -> no Drive call)
  source TEXT,                                -- feed | jd_text | snapshot (...) | sheet | fetch
  detail TEXT,
  saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS google_moves (     -- GOAL11 §5: a person's files re-made in their own Drive (resumable)
  old_id TEXT PRIMARY KEY,                    -- the copy in the owner's Drive (parked in "job-hunt-agent/Old - <Name> (moved)")
  profile TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('upload', 'approved', 'doc', 'jd_doc')),
  name TEXT,
  new_id TEXT NOT NULL,                       -- the copy in the person's Drive
  new_link TEXT,
  moved_at TEXT NOT NULL,
  parked_at TEXT                              -- when the old copy went to the Old folder (never deleted)
);

CREATE TABLE IF NOT EXISTS resume_places (     -- GOAL22: the contact-line city a person picked for a job at Generate
  profile TEXT NOT NULL,
  uid TEXT NOT NULL,                          -- the feed job
  location TEXT,                              -- "City, ST"; NULL = Remote (the person's own city)
  picked_at TEXT NOT NULL,
  PRIMARY KEY (profile, uid)
);

-- GOAL23: the Autofill extension. The answer bank (per person), the questions it could not answer ("Needs you"),
-- pages matched to a job by hand, one line per fill (counts only, never a field's value), page snapshots sent from
-- the panel, and the short answers Claude drafted on a click. The job-site password is never stored here.
CREATE TABLE IF NOT EXISTS autofill_answers (
  profile TEXT NOT NULL,
  key TEXT NOT NULL,                          -- a built-in key (email, sponsor_now, gender, ...) or 'q:<question>'
  label TEXT NOT NULL,                        -- what the Answers page shows
  category TEXT NOT NULL,                     -- contact | links | work | education | legal | eeo | history | other
  question TEXT,                              -- custom entries: the question as the form asked it
  answer TEXT NOT NULL DEFAULT '',            -- '' = not given yet: a form asking it gets "Needs you"
  options TEXT NOT NULL DEFAULT '{}',         -- JSON {board: the exact option text that board's dropdown needs}
  updated_at TEXT NOT NULL,
  PRIMARY KEY (profile, key)
);
CREATE TABLE IF NOT EXISTS autofill_questions (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  qnorm TEXT NOT NULL,                        -- the question, normalized: identical questions are one entry
  key TEXT,                                   -- the bank key it belongs to when known (zip, sponsor_now, ...)
  text TEXT NOT NULL,                         -- as the form asked it (latest)
  kind TEXT NOT NULL,                         -- text | textarea | select | radio | checkbox | checkboxes | date
  options TEXT NOT NULL DEFAULT '[]',         -- JSON, the form's choices (latest)
  boards TEXT NOT NULL DEFAULT '[]',          -- JSON, where it was asked
  companies TEXT NOT NULL DEFAULT '[]',
  seen INTEGER NOT NULL DEFAULT 1,
  first_at TEXT NOT NULL,
  last_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'answered', 'dismissed')),
  UNIQUE (profile, qnorm)
);
CREATE TABLE IF NOT EXISTS autofill_urls (
  profile TEXT NOT NULL,
  url_key TEXT NOT NULL,                      -- host + path of an apply page, lower case, no query / trailing slash
  job_uid TEXT,                               -- the feed job, or
  app_id INTEGER,                             -- the tracker row (outside job)
  picked_at TEXT NOT NULL,
  PRIMARY KEY (profile, url_key)
);
CREATE TABLE IF NOT EXISTS autofill_log (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,
  app_id INTEGER,
  board TEXT,
  step TEXT,
  filled INTEGER NOT NULL DEFAULT 0,
  needs INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autofill_snapshots (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  url TEXT NOT NULL,
  board TEXT,
  html BLOB NOT NULL,                         -- zlib; password fields have no value
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autofill_drafts (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,
  app_id INTEGER,
  question TEXT NOT NULL,
  text TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ext ON jobs(ext_id);   -- GOAL23: an apply page's id -> the feed job
-- GOAL23 §7: the address the forms get must match the city on the resume used for the job. One saved street
-- address per person per City+State (street only: never an apartment / unit number), confirmed by the person; the
-- OpenStreetMap suggestions for a city are fetched once and cached (osm_places), and which one is offered now is
-- kept per person (autofill_address_offers, "Pick another").
CREATE TABLE IF NOT EXISTS autofill_addresses (
  profile TEXT NOT NULL,
  city_state TEXT NOT NULL,                   -- "McLean, VA"
  street TEXT NOT NULL,                       -- "7480 Birdwood Ave"
  zip TEXT NOT NULL,                          -- "22102"
  source TEXT NOT NULL,                       -- osm | typed
  saved_at TEXT NOT NULL,
  PRIMARY KEY (profile, city_state)
);
CREATE TABLE IF NOT EXISTS osm_places (
  city_state TEXT PRIMARY KEY,
  candidates TEXT NOT NULL,                   -- JSON [{street, zip, name}], best first
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autofill_address_offers (
  profile TEXT NOT NULL,
  city_state TEXT NOT NULL,
  idx INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (profile, city_state)
);

-- GOAL24: which companies sponsor H-1B now, from the public DOL filings (data/h1b/*.xlsx, refreshed monthly by
-- `job-hunt-agent h1b refresh`). Never in the jobs row: per filing employer (one per FEIN, else per name), its filings per
-- month (certified H-1B LCAs, certified PERM green-card cases; counts only, no worker data), rolled up per company
-- under the same name key job-hunt-agent's companies get (h1b/names.py). Companies learned to auto-reject people who need
-- sponsorship are read from the tracker and its emails (h1b/label.py), not stored here.
CREATE TABLE IF NOT EXISTS h1b_files (
  name TEXT PRIMARY KEY,                      -- LCA_Disclosure_Data_FY2025_Q4.xlsx
  program TEXT NOT NULL,                      -- lca | perm
  url TEXT,
  bytes INTEGER,
  rows INTEGER,                               -- filings kept from it
  first_date TEXT,
  last_date TEXT,
  imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS h1b_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS h1b_employers (
  id INTEGER PRIMARY KEY,
  ident TEXT NOT NULL UNIQUE,                 -- 'fein:061506026' or 'name:<key>'
  name TEXT NOT NULL,                         -- the name it files under most
  dba TEXT,
  key TEXT NOT NULL,                          -- names.key(name)
  naics TEXT,
  state TEXT,                                 -- head office state
  domain TEXT,                                -- its contact email's domain (the career site is found from it)
  cap_exempt TEXT                             -- why it is cap-exempt (university, research, ...) or NULL
);
CREATE INDEX IF NOT EXISTS h1b_employers_key ON h1b_employers(key);
CREATE TABLE IF NOT EXISTS h1b_months (
  employer_id INTEGER NOT NULL REFERENCES h1b_employers(id),
  program TEXT NOT NULL,                      -- lca | perm
  month TEXT NOT NULL,                        -- YYYY-MM of the decision
  n_all INTEGER NOT NULL,
  n_data INTEGER NOT NULL,                    -- data / AI / ML / analytics / data-engineering roles (SOC or title)
  levels TEXT NOT NULL DEFAULT '{}',          -- JSON {wage level: n} of the data roles (LCA)
  states TEXT NOT NULL DEFAULT '{}',          -- JSON {worksite state: n}
  titles TEXT NOT NULL DEFAULT '{}',          -- JSON {job title: n} of the data roles
  PRIMARY KEY (employer_id, program, month)
);
CREATE TABLE IF NOT EXISTS h1b_companies (
  key TEXT PRIMARY KEY,                       -- names.key(): the same key a job's company gets
  name TEXT NOT NULL,                         -- the biggest filer's name
  employers TEXT NOT NULL,                    -- JSON [[name, filings in 12 mo], ...] biggest first
  status TEXT,                                -- sponsors | cap_exempt | maybe
  recent_all INTEGER NOT NULL, recent_data INTEGER NOT NULL,     -- LCAs in the last 12 months of the data
  prior_all INTEGER NOT NULL, prior_data INTEGER NOT NULL,       -- LCAs in the 12 months before
  perm_all INTEGER NOT NULL, perm_data INTEGER NOT NULL,         -- PERM cases in the 24 months
  level TEXT,                                 -- typical wage level of its data-role LCAs (I..IV)
  states TEXT NOT NULL DEFAULT '[]',          -- JSON top worksite states
  titles TEXT NOT NULL DEFAULT '[]',          -- JSON top data-role titles
  last_month TEXT,
  cap_exempt TEXT,
  domain TEXT
);
-- GOAL25 §1: Autofill learns what the person fills or changes by hand. The answer itself goes into the bank
-- (autofill_answers) or, for a legal / EEO question, into that company's override; every learned change is one
-- autofill_learned row with the answer it replaced, so Undo puts it back. Long free text is a snippet kept with the
-- application (offered in the panel, never filled on its own). autofill_choices: the real choices a board's form
-- showed for a question (the Answers page offers them as a dropdown). autofill_hand: fields filled by hand per step
-- (Stats: how much Autofill filled by itself).
CREATE TABLE IF NOT EXISTS autofill_learned (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  scope TEXT NOT NULL,                        -- global | company | snippet
  key TEXT NOT NULL,                          -- the bank key ('q:<question>' for his own questions); snippets: ''
  question TEXT NOT NULL,                     -- as the form asked it
  answer TEXT NOT NULL,
  prev TEXT,                                  -- the answer it replaced (NULL: there was none)
  how TEXT NOT NULL,                          -- hand (typed / picked on the page) | override (changed what Autofill put) | panel
  board TEXT,
  company TEXT,
  job_uid TEXT,
  app_id INTEGER,
  ref INTEGER,                                -- snippet: autofill_snippets.id
  at TEXT NOT NULL,
  undone_at TEXT
);
CREATE INDEX IF NOT EXISTS autofill_learned_p ON autofill_learned(profile, at);
CREATE TABLE IF NOT EXISTS autofill_overrides (
  profile TEXT NOT NULL,
  company TEXT NOT NULL,                      -- normalized company name
  company_name TEXT NOT NULL,                 -- as shown
  key TEXT NOT NULL,                          -- a legal / EEO bank key, or 'q:<question>'
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (profile, company, key)
);
CREATE TABLE IF NOT EXISTS autofill_snippets (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,
  app_id INTEGER,
  company TEXT,
  question TEXT NOT NULL,
  qnorm TEXT NOT NULL,
  text TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autofill_choices (
  profile TEXT NOT NULL,
  key TEXT NOT NULL,
  board TEXT NOT NULL,
  options TEXT NOT NULL,                      -- JSON list, as the form showed them
  at TEXT NOT NULL,
  PRIMARY KEY (profile, key, board)
);
CREATE TABLE IF NOT EXISTS autofill_hand (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,
  app_id INTEGER,
  board TEXT,
  step TEXT,
  hand INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL
);
-- GOAL25 §4: no silent failures. A fill error or a layout no adapter knows is reported with the form's HTML
-- (no password / ID values) and grouped by board + step on Settings › Autofill › Health. The nightly canary fills
-- saved fixtures and a few live public pages with the fake person; a drop against the last good run is a notice.
CREATE TABLE IF NOT EXISTS autofill_errors (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  board TEXT NOT NULL,
  step TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,                         -- error | unknown_layout | timeout
  message TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  html BLOB,                                  -- zlib, form HTML without password / ID values
  version TEXT,                               -- the extension's version
  at TEXT NOT NULL,
  fixture TEXT                                -- the regression fixture made from it
);
CREATE INDEX IF NOT EXISTS autofill_errors_at ON autofill_errors(at);
CREATE TABLE IF NOT EXISTS autofill_canary (
  id INTEGER PRIMARY KEY,
  run_at TEXT NOT NULL,                       -- one nightly run = one run_at
  name TEXT NOT NULL,                         -- fixture name or live page
  board TEXT NOT NULL,
  source TEXT NOT NULL,                       -- fixture | live
  step TEXT NOT NULL DEFAULT '',
  fields INTEGER NOT NULL DEFAULT 0,
  filled INTEGER NOT NULL DEFAULT 0,
  expected INTEGER,                           -- a fixture's expected fills
  ok INTEGER NOT NULL DEFAULT 1,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS app_notices (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                         -- canary
  text TEXT NOT NULL,
  at TEXT NOT NULL,
  closed_at TEXT
);
-- GOAL25 §4: the Autofill version each person's Chrome last used (job-hunt-agent says when a newer one is ready)
CREATE TABLE IF NOT EXISTS autofill_seen (
  profile TEXT PRIMARY KEY,
  version TEXT NOT NULL,                      -- '1.0.0' for an extension too old to say
  at TEXT NOT NULL
);
-- GOAL25 §5: "Apply on laptop": jobs queued on the phone, opened on the laptop (Open next 5); done once applied.
CREATE TABLE IF NOT EXISTS apply_queue (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  job_uid TEXT,
  app_id INTEGER,
  added_at TEXT NOT NULL,
  opened_at TEXT,
  done_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS apply_queue_job ON apply_queue(profile, coalesce(job_uid, ''), coalesce(app_id, 0));
