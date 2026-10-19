"""Shared loop for full-list boards whose list has no description (iCIMS classic sitemap, Jobvite):
every listed posting becomes an item (a full list, so an absent job counts as a miss), an unchanged posting stays on
the fast path, and a title-matched posting that changed costs one detail request, at most MAX_DETAILS per run.

A matched posting whose detail could not be read this run (budget spent, host error) is stored with a marked raw
fingerprint, so the next run does not take it for unchanged and asks again; without the mark it would sit on the fast
path with no JD for good.
"""
from typing import Callable, Iterable

from ..net import HttpError
from .common import Ctx, FetchResult, fast_item, raw_fp

MAX_DETAILS = 25
PENDING = "detail-pending"
PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError, OverflowError)

Entry = dict
Detail = dict


def run(board: dict, ctx: Ctx, entries: Iterable[Entry], *, id_of: Callable[[Entry], str],
        title_of: Callable[[Entry], str], normalize: Callable[[dict, Entry, Detail | None], dict],
        detail: Callable[[Entry], Detail | None] | None, max_details: int = MAX_DETAILS, truncated: bool = False,
        notes: list[str] | None = None, wants_detail: Callable[[Entry], bool] = lambda entry: True) -> FetchResult:
    """`wants_detail` spares the request for a matched posting the list already places outside the US. detail=None:
    the list itself carries every field (GOAL16 Gem, Recruitee ...), so each posting is normalized from its entry."""
    ats, key = board["ats"], board["key"]
    items: list[dict] = []
    notes = list(notes or [])
    bad, budget, n_raw = 0, max_details, 0
    for entry in entries:
        n_raw += 1
        try:
            ext_id, title = id_of(entry), title_of(entry)
            fp = raw_fp(entry)
            if ctx.unchanged(f"{ats}:{key}:{ext_id}", fp):
                items.append(fast_item(ats, key, ext_id, title, fp))
                continue
            found = None
            if detail is None:
                found = entry
            elif ctx.matches(title) and wants_detail(entry):
                if budget > 0:
                    budget -= 1
                    found = _detail(detail, entry, ext_id, notes)
                if found is None:
                    fp = raw_fp(entry, PENDING)
            items.append({**normalize(board, entry, found), "raw_fp": fp})
        except PARSE_ERRORS:
            bad += 1
    if budget <= 0:
        notes.append(f"detail budget of {max_details} spent; the rest come next run")
    return FetchResult(items, 200, n_raw=n_raw, n_parse_err=bad, notes=notes, truncated=truncated)


def _detail(detail: Callable[[Entry], Detail | None], entry: Entry, ext_id: str, notes: list[str]) -> Detail | None:
    try:
        found = detail(entry)
    except HttpError as err:
        notes.append(f"detail {ext_id}: {err}")
        return None
    except PARSE_ERRORS as err:
        notes.append(f"detail {ext_id}: unexpected page ({type(err).__name__})")
        return None
    if found is None:
        notes.append(f"detail {ext_id}: no job data on the page")
    return found
