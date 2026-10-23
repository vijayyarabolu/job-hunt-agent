"""Several contacts per application: name, role, email, LinkedIn, and each one's own sent / replied marks.

The trackers pack contacts into single cells ("CJ Sheppard, Jesse Stiff, Aaron Pence" / "y,y,y"). Those cells stay
the record: contacts derived from them are origin 'legacy' and re-derived whenever the cells change (import), so an
export writes the original cells untouched. Once the user edits an application's contacts in the app, its contacts
become origin 'app' and the packed cells are written from them (marked edited, so export uses the new text).
"""
import re
import unicodedata
from datetime import date

from . import db, history, tracker

FIELDS = ("name", "role", "email", "linkedin", "linkedin_sent", "email_sent", "replied", "notes", "source", "kind",
          "found_at")                                         # GOAL28 §5: the type of contact and when it was found
KINDS = ("recruiter", "manager", "peer")
FLAGS = ("linkedin_sent", "email_sent", "replied")
LEGACY = ("target_name", "target_role", "email_address", "linkedin_request", "email_sent")
MAX_CONTACTS = 20
MAX_FIELD = 500
SQL_BATCH = 500
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_NAME_SPLIT = re.compile(r"\s*(?:,|;|\n|&|\band\b)\s*")
_LIST_SPLIT = re.compile(r"\s*[,;\n]\s*")


def _parts(text: str | None, pattern: re.Pattern = _LIST_SPLIT) -> list[str]:
    return [p for p in pattern.split((text or "").strip()) if p]


def _spread(text: str | None, n: int) -> list[str]:
    """Per-contact values of a packed cell: one each when the count matches, the single value for everyone, else the
    whole text on the first contact (never lost: the cell itself is kept)."""
    parts = _parts(text)
    if len(parts) == n:
        return parts
    if len(parts) == 1:
        return parts * n
    return [(text or "").strip()] + [""] * (n - 1) if n else []


def _roles(text: str | None, n: int) -> list[str]:
    """Roles hold commas themselves ("Director, Data Services"): split only when the count matches the names."""
    parts = _parts(text)
    if n > 1 and len(parts) == n:
        return parts
    return [(text or "").strip()] + [""] * (n - 1)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _owner(email: str, names: list[str], taken: set[int]) -> int | None:
    """The name an email belongs to: a free name with a first or last name (3+ letters) in the email's local part."""
    local = _fold(email.split("@", 1)[0])
    for i, name in enumerate(names):
        tokens = [t for t in re.split(r"[^a-z]+", _fold(name)) if len(t) >= 3]
        if i not in taken and tokens and (tokens[0] in local or tokens[-1] in local):
            return i
    return None


def _emails_by_name(emails: list[str], names: list[str], n: int) -> list[str]:
    """Emails in the order of their names: matched by name when fewer emails than names, else as written."""
    if len(emails) >= n or not names:
        return (emails + [""] * n)[:n]
    slots: list[str] = [""] * n
    taken: set[int] = set()
    rest = []
    for email in emails:
        i = _owner(email, names, taken)
        if i is None:
            rest.append(email)
        else:
            slots[i], taken = email, taken | {i}
    for email in rest:                                          # no name matched: next free slot, in order
        i = next(k for k in range(n) if k not in taken)
        slots[i], taken = email, taken | {i}
    return slots


def split_legacy(app: dict) -> list[dict]:
    """The contacts packed into an application's five tracker cells (best effort; pure)."""
    names = _parts(app.get("target_name"), _NAME_SPLIT)
    emails = _EMAIL.findall(app.get("email_address") or "")
    n = max(len(names), len(emails))
    if n == 0 and any((app.get(f) or "").strip() for f in ("target_role", "linkedin_request")):
        n = 1                                                   # a role or a LinkedIn note without a name
    if n == 0:
        return []
    names += [""] * (n - len(names))
    mails = _emails_by_name(emails, names, n)
    roles, linked, sent = _roles(app.get("target_role"), n), _spread(app.get("linkedin_request"), n), \
        _spread(app.get("email_sent"), n)
    return [{"name": names[i], "role": roles[i], "email": mails[i], "linkedin": "", "linkedin_sent": linked[i],
             "email_sent": sent[i], "replied": "", "notes": "", "source": ""} for i in range(n)]


def packed(contacts: list[dict]) -> dict[str, str]:
    """The five tracker cells written from the app's contacts (inverse of split_legacy for app-edited rows)."""
    def join(field: str, sep: str = ", ") -> str:
        values = [(c.get(field) or "").strip() for c in contacts]
        return sep.join(values) if any(values) else ""
    return {"target_name": join("name"), "target_role": join("role"), "email_address": join("email"),
            "linkedin_request": join("linkedin_sent", ","), "email_sent": join("email_sent", ",")}


def _insert(conn, app_id: int, rows: list[dict], origin: str, now: str) -> None:
    for pos, c in enumerate(rows):
        conn.execute(f"INSERT INTO contacts(app_id, pos, {', '.join(FIELDS)}, origin, updated_at) "
                     f"VALUES (?,?,{','.join('?' * len(FIELDS))},?,?)",
                     (app_id, pos, *[c.get(f) or "" for f in FIELDS], origin, now))


def refresh_legacy(conn, app_ids: list[int] | None = None) -> int:
    """Re-derive the 'legacy' contacts of every application (or the given ones) whose contacts the user has not
    edited in the app. Returns how many applications were refreshed. Idempotent."""
    where, params = ("", []) if app_ids is None else (f"AND a.id IN ({','.join('?' * len(app_ids))})", app_ids)
    rows = conn.execute(f"SELECT a.id, {', '.join('a.' + f for f in LEGACY)} FROM applications a "
                        f"WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.app_id = a.id AND c.origin = 'app') "
                        f"{where}", params).fetchall()
    now = db.iso(db.utcnow())
    with db.tx(conn):
        for row in rows:
            conn.execute("DELETE FROM contacts WHERE app_id = ? AND origin = 'legacy'", (row["id"],))
            _insert(conn, row["id"], split_legacy(dict(row)), "legacy", now)
    return len(rows)


def for_apps(conn, app_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in app_ids}
    for i in range(0, len(app_ids), SQL_BATCH):
        part = app_ids[i:i + SQL_BATCH]
        for r in conn.execute(f"SELECT * FROM contacts WHERE app_id IN ({','.join('?' * len(part))}) "
                              "ORDER BY app_id, pos, id", part):
            out[r["app_id"]].append(dict(r))
    return out


def _clean(contact: dict) -> dict:
    if not isinstance(contact, dict):
        raise ValueError("each contact must be an object")
    out = {f: str(contact.get(f) or "").strip()[:MAX_FIELD] for f in FIELDS}
    if out["email"] and not _EMAIL.fullmatch(out["email"]):
        raise ValueError(f"not an email address: {out['email']!r}")
    if out["linkedin"] and not re.match(r"^https?://", out["linkedin"]):
        raise ValueError("LinkedIn must be a link (https://...)")
    if out["kind"] and out["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return out


def save(conn, app_id: int, contacts: list[dict], keep_empty: bool = False) -> list[dict]:
    """Replace the application's contacts with these (the UI sends the whole list) and write the packed cells.
    Only a cell whose contact field the edit changed is rewritten: the others keep the tracker's original text
    (legacy contacts are a best-effort split of those cells, so re-packing them could lose notes in a cell)."""
    if not isinstance(contacts, list) or len(contacts) > MAX_CONTACTS:
        raise ValueError(f"contacts must be a list of at most {MAX_CONTACTS}")
    rows = [c for c in (_clean(c) for c in contacts) if keep_empty or any(c.values())]
    old_people = for_apps(conn, [app_id])[app_id]
    before = packed(old_people)
    now = db.iso(db.utcnow())
    with db.tx(conn):
        conn.execute("DELETE FROM contacts WHERE app_id = ?", (app_id,))
        _insert(conn, app_id, rows, "app", now)
        history.record(conn, app_id, "contacts", plain(old_people) or None, plain(rows) or None)
    current = dict(conn.execute(f"SELECT {', '.join(LEGACY)} FROM applications WHERE id = ?", (app_id,)).fetchone())
    for field, value in packed(rows).items():
        if value != before[field] and (current[field] or "") != value:
            tracker.edit_field(conn, app_id, field, value or None)
    return for_apps(conn, [app_id])[app_id]


def plain(people: list[dict]) -> list[dict]:
    """Contacts as their editable fields only (what history keeps and an undo restores)."""
    return [{f: c.get(f) or "" for f in FIELDS} for c in people]


PACKED_TO_FIELD = {"target_name": "name", "target_role": "role", "email_address": "email",
                   "linkedin_request": "linkedin_sent", "email_sent": "email_sent"}


def set_packed(conn, app_id: int, field: str, value: str | None) -> None:
    """A packed tracker cell edited directly (GOAL6 §2 inline edit) on a row whose contacts were edited in the app:
    the new cell text is spread over the contacts (one part each, as an import reads the cell), and the cell keeps
    exactly the text typed."""
    people = plain(for_apps(conn, [app_id])[app_id])
    key, text = PACKED_TO_FIELD[field], value or ""
    if key == "name":
        parts = _parts(text, _NAME_SPLIT)
    elif key == "email":
        parts = _EMAIL.findall(text)
    elif key == "role":
        parts = _roles(text, len(people)) if text else []
    else:
        parts = _spread(text, len(people)) if text else []
    count = max(len(people), len(parts))
    rows = [{**(people[i] if i < len(people) else {}), key: parts[i] if i < len(parts) else ""} for i in range(count)]
    save(conn, app_id, rows, keep_empty=True)
    current = conn.execute(f"SELECT {field} FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
    if (current or None) != (value or None):
        tracker.edit_field(conn, app_id, field, value or None)


def toggle(conn, contact_id: int, flag: str, on: bool, today: date | None = None) -> dict:
    """Tick / untick one contact's LinkedIn-sent, email-sent or replied mark (ticking stamps today's date)."""
    if flag not in FLAGS:
        raise ValueError(f"flag must be one of {FLAGS}")
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None:
        raise KeyError(contact_id)
    contacts = for_apps(conn, [row["app_id"]])[row["app_id"]]
    stamp = (today or date.today()).strftime("%m/%d/%Y") if on else ""
    saved = save(conn, row["app_id"], [{**c, flag: stamp} if c["id"] == contact_id else c for c in contacts],
                 keep_empty=True)                               # unticking never drops or renumbers a contact
    return saved[row["pos"]]
