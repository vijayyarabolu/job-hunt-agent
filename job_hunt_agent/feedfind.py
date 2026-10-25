"""GOAL35 §5.2 the job lists' search: the person's ranked jobs (every tab draws from them) found by company and its
aliases, title, place and role family with textsearch; the index is built once per ranking (feedcache.Entry). The
page also says how many more jobs match outside its other filters ("N more match outside your filters")."""
from dataclasses import dataclass

from . import config, textsearch
from .match import FAMILY_LABELS


@dataclass(frozen=True)
class Found:
    uids: frozenset
    terms: tuple
    tiers: dict                  # uid -> how well it matched (textsearch tiers: COMPANY first)


def _record(row: dict) -> textsearch.Record:
    families = " ".join(FAMILY_LABELS.get(f, f) for f in (row.get("families") or "").strip(",").split(",") if f)
    return textsearch.Record(row["uid"], {"company": row.get("company"), "title": row.get("title"),
                                          "city": row.get("location"), "role": families,
                                          "workplace": row.get("workplace")})


def find(conn, pid: str, kept, q: str) -> Found:
    index = textsearch.cached(("feed", str(config.DB_PATH), pid, id(kept), kept.fingerprint),
                              lambda: [_record(r) for r, _ in kept.ranked])
    found = index.search(textsearch.parse(q))
    keep = found.strong()        # "amazon": Amazon's jobs, not the other companies' titles that say AWS
    return Found(frozenset(index.records[i].id for i in keep), found.terms,
                 {index.records[i].id: found.tier(i) for i in keep})


def outside(pool: list, kept: list, group_of: dict) -> int:
    """The roles the search found that the other filters (or the match floor) hide: one per role, like the list."""
    shown = {group_of.get(r["uid"], r["uid"]) for r, _ in kept}
    return len({group_of.get(r["uid"], r["uid"]) for r, _ in pool} - shown)
