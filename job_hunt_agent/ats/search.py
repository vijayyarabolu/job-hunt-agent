"""Shared loop for search-style adapters (decision 011): one query per search term, results merged by id,
an unchanged posting kept on the fast path, a description fetched only for a title-matched posting the DB lacks a JD
for. Absence is never a miss for these boards (store.SEARCH_ATS), so when a host refuses us part-way (a 429 quota on
Eightfold tenants) the postings already collected are kept: the result is ok, truncated, with the error as a note."""
from typing import Callable, Iterable

from ..net import HttpError
from .common import Ctx, FetchResult, SchemaDrift, fast_item, raw_fp, schema_failure

Posting = dict
Pages = Callable[[str], Iterable[Posting]]               # term -> postings (the adapter pages through them)


PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError, OverflowError)


def collect(terms: Iterable[str], pages: Pages, id_of: Callable[[Posting], str]
            ) -> tuple[dict, dict, HttpError | None, int]:
    """Postings by id, the terms that found each, the HTTP error that stopped collection early (if any), and how many
    postings had no usable id (counted as parse errors, never a lost board)."""
    seen: dict[str, Posting] = {}
    found_by: dict[str, set[str]] = {}
    bad = 0
    try:
        for term in terms:
            for posting in pages(term):
                try:
                    key = id_of(posting)
                except PARSE_ERRORS:
                    bad += 1
                    continue
                seen.setdefault(key, posting)
                found_by.setdefault(key, set()).add(f"term:{term}")
    except HttpError as err:
        return seen, found_by, err, bad
    return seen, found_by, None, bad


def run(board: dict, ctx: Ctx, pages: Pages, *, id_of: Callable[[Posting], str], title_of: Callable[[Posting], str],
        normalize: Callable[[dict, Posting, str | dict | None], dict],
        detail: Callable[[Posting], str | dict | None] | None = None,
        keep: Callable[[Posting], bool] = lambda posting: True) -> FetchResult:
    """Fetch every term's postings, then build items. `keep` drops postings before any detail request (e.g. non-US)."""
    ats = board["ats"]
    try:
        seen, found_by, stopped, bad = collect(ctx.search_terms, pages, id_of)
    except SchemaDrift as err:
        return schema_failure(err)
    if stopped is not None and not seen:
        return FetchResult([], stopped.status, error=str(stopped))
    items: list[dict] = []
    notes = [f"partial: {stopped}"] if stopped is not None else []
    if stopped is not None:
        detail = None                  # the host just refused us (and is paused): JDs come with the next fetch
    for key, posting in seen.items():
        try:
            if not keep(posting):
                continue
            items.append(_item(board, ats, ctx, posting, key, found_by[key], title_of, normalize, detail, notes))
        except PARSE_ERRORS:
            bad += 1
    return FetchResult(items, 200, n_raw=len(seen), n_parse_err=bad, notes=notes, truncated=stopped is not None)


def _item(board, ats, ctx, posting, key, scopes, title_of, normalize, detail, notes) -> dict:
    fp = raw_fp(posting)
    uid = f"{ats}:{board['key']}:{key}"
    title = title_of(posting)
    wants = detail is not None and ctx.matches(title) and ctx.needs_detail(uid)
    if ctx.unchanged(uid, fp) and not wants:          # an unchanged match still without its JD asks again
        return fast_item(ats, board["key"], key, title, fp)
    description = None
    if wants:
        try:
            description = detail(posting)
        except HttpError as err:
            notes.append(f"detail {key}: {err}")
        except PARSE_ERRORS as err:              # a detail answer of an unexpected shape: keep the posting, no JD
            notes.append(f"detail {key}: unexpected shape ({type(err).__name__})")
            description = None
        if description is not None and not isinstance(description, (str, dict)):
            notes.append(f"detail {key}: description is not text")
            description = None
    return {**normalize(board, posting, description), "raw_fp": fp, "scopes": sorted(scopes)}
