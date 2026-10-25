"""Vendor-aware ATS match (decision 010): each job's resumes scored the way that job's ATS vendor documents
evaluating candidates, from config/ats_profiles.yaml `model:` blocks only.

A model is a list of channels (e.g. Greenhouse's exact-keyword Talent Filtering and its semantic Talent Matching), each
switched on by cited fact ids; inside a channel every feature (related skills, required weighting, title, years,
grade) is off unless it cites a fact. Every number the documentation does not give is ours and listed in OURS, which
the "how this is computed" page prints. A vendor without a model keeps the generic keyword % under a label that says
its matching is not publicly documented. Pure read-time arithmetic over the fits pack (forms + literal mask built at
fetch time), so editing the YAML or config/related_skills.yaml rebuilds nothing.
"""
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from . import ats_profile, config, keywords, lexicon
from .resume import ats

PREFERRED_WEIGHT = 0.5
RELATED_CREDIT = 0.5
FEATURE_WEIGHTS = {"skills": 0.6, "title": 0.2, "years": 0.2}
DEGRADE_FACTOR = 0.9
MOST = 0.5
# Our modeling choices (no vendor publishes these numbers): shown on the "how this is computed" page.
OURS = (
    ("Required vs preferred", f"a preferred keyword weighs {PREFERRED_WEIGHT} of a required one, where the vendor "
                              "documents that required skills count more but not by how much"),
    ("Related skills", f"a curated related skill (config/related_skills.yaml) earns {RELATED_CREDIT} of a match, "
                       "only where the vendor documents inferred or semantic skill matching"),
    ("Inside a channel", "skills {skills}, title {title}, years {years} (renormalized over the parts the vendor "
                         "documents)".format(**FEATURE_WEIGHTS)),
    ("Across channels", "documented channels (e.g. keyword search and AI matching) count equally (mean)"),
    ("Parsing risks", f"each documented risk found in the resume file multiplies the score by {DEGRADE_FACTOR}; a "
                      "documented 'cannot parse' condition sets it to 0"),
    ("Literal wording", "the JD's own words, case-insensitive, whole words; no plural, alias or acronym credit (no "
                        "vendor documents those)"),
    ("Title", "1 when the resume is meant for the job's title family (profile.yaml families), else 0"),
    ("Years", "years from the resume's own date ranges; 1 when they meet the JD minimum, else 1 - gap / minimum"),
    ("Grade bands", f"'most' qualifications = more than {MOST:.0%}; qualifications approximated by the JD's required "
                    "and preferred keywords"),
)
SKILL_MODES = ("literal", "taxonomy")
FEATURES = ("related", "required_weight", "preferred_zero", "title", "years", "grade")
CHECKS = ("tables", "text_boxes", "header_footer_text", "columns", "images", "file_type", "max_mb")
EFFECTS = ("degrade", "block", "tip")


@dataclass(frozen=True)
class Channel:
    name: str
    cite: tuple[str, ...]
    skills: str                                          # literal | taxonomy
    features: tuple[tuple[str, tuple[str, ...]], ...]    # enabled feature -> its cited fact ids

    def has(self, feature: str) -> bool:
        return any(f == feature for f, _ in self.features)


@dataclass(frozen=True)
class Check:
    name: str
    cite: tuple[str, ...]
    effect: str
    value: object = None


@dataclass(frozen=True)
class Model:
    vendor: str
    label: str
    channels: tuple[Channel, ...]
    checks: tuple[Check, ...]
    hosts: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]                 # fact id -> https source

    def source(self, fact_id: str) -> str:
        return dict(self.sources)[fact_id]


def _cites(vendor: str, where: str, value, known: dict) -> tuple[str, ...]:
    ids = tuple(str(v) for v in (value or []))
    if not ids:
        raise config.ConfigError(f"ats_profiles.yaml {vendor}.model {where}: needs at least one cited fact id")
    missing = [i for i in ids if i not in known]
    if missing:
        raise config.ConfigError(f"ats_profiles.yaml {vendor}.model {where}: no {vendor} fact with id {missing}")
    return ids


def _channel(vendor: str, spec: dict, known: dict) -> Channel:
    name = str(spec.get("name") or "")
    if spec.get("skills") not in SKILL_MODES:
        raise config.ConfigError(f"ats_profiles.yaml {vendor}.model {name}: skills must be one of {SKILL_MODES}")
    unknown = set(spec) - {"name", "cite", "skills", *FEATURES}
    if unknown:
        raise config.ConfigError(f"ats_profiles.yaml {vendor}.model {name}: unknown keys {sorted(unknown)}")
    features = tuple((f, _cites(vendor, f"{name}.{f}", spec[f], known)) for f in FEATURES if f in spec)
    return Channel(name, _cites(vendor, name, spec.get("cite"), known), spec["skills"], features)


def _check(vendor: str, name: str, spec: dict, known: dict) -> Check:
    if name not in CHECKS or (spec or {}).get("effect") not in EFFECTS:
        raise config.ConfigError(f"ats_profiles.yaml {vendor}.model parse_checks.{name}: check must be one of "
                                 f"{CHECKS} with effect one of {EFFECTS}")
    return Check(name, _cites(vendor, f"parse_checks.{name}", spec.get("cite"), known), spec["effect"],
                 spec.get("value"))


def _model(vendor: str, prof: dict) -> Model | None:
    spec = prof.get("model")
    if not spec:
        return None
    known = ats_profile.facts(prof)
    return Model(vendor, str(prof.get("label") or vendor),
                 tuple(_channel(vendor, c, known) for c in spec.get("channels") or []),
                 tuple(_check(vendor, n, c, known) for n, c in (spec.get("parse_checks") or {}).items()),
                 tuple(str(h).lower() for h in spec.get("detect_hosts") or ()),
                 tuple((i, f["source"]) for i, f in known.items()))


def models(path: str | None = None) -> dict[str, Model]:
    """Every vendor with a `model:` block, validated (a path is read fresh; the default file is cached)."""
    if path is None:
        return _default_models()
    return {v: m for v, p in ats_profile.load(path).items() if (m := _model(v, p))}


@lru_cache(maxsize=1)
def _default_models() -> dict[str, Model]:
    return {v: m for v, p in ats_profile.profiles().items() if (m := _model(v, p))}


def _host_matches(host: str, pattern: str) -> bool:
    bare = pattern.lstrip(".")
    return host == bare or host.endswith("." + bare)


def vendor_of(row: dict, known: dict[str, Model]) -> str:
    """The job's ATS vendor: a model's documented apply/posting host first (a vendor behind a company domain or a
    board of another ATS), else the board's ATS."""
    for url in (row.get("apply_url"), row.get("url")):
        host = (urlsplit(url or "").hostname or "").lower()
        for vendor, model in known.items():
            if host and any(_host_matches(host, h) for h in model.hosts):
                return vendor
    return ats_profile.ats_of(row.get("board"))


OUTSIDE = "outside"           # = outside_jobs.ATS (not imported: outside_jobs needs this module's callers)

# ---------- scoring ----------

@dataclass(frozen=True)
class ChannelScore:
    name: str
    pct: int | None             # None: not applicable (no keyword this channel weighs, e.g. all preferred under
    skills: float | None        # preferred_zero); such a channel drops out of the mean
    title: float | None
    years: float | None


@dataclass(frozen=True)
class VendorScore:
    id: str
    label: str
    pct: int | None               # None: the row predates decision 010 (no literal data yet)
    generic: int
    grade: str | None
    channels: tuple[ChannelScore, ...]
    matched: tuple[str, ...]      # in the form this vendor needs
    missing: tuple[str, ...]
    parse_fired: tuple[str, ...]  # degrade/block checks found in this resume
    tips: tuple[str, ...]         # documented tip checks found
    factor: float
    n_total: int

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "pct": self.pct, "generic": self.generic, "grade": self.grade,
                "channels": [{"name": c.name, "pct": c.pct} for c in self.channels], "matched": list(self.matched),
                "missing": list(self.missing), "n_matched": len(self.matched), "n_missing": len(self.missing),
                "n_total": self.n_total, "parse_fired": list(self.parse_fired), "tips": list(self.tips)}


@dataclass(frozen=True)
class Result:
    vendor: str
    vendor_label: str
    model: Model | None           # the vendor's model (it may have parse checks but no channel)
    label: str
    scores: tuple[VendorScore, ...]
    best: VendorScore | None
    terms: tuple = ()             # (Term, hitmask, litmask) of the JD, for explain()
    min_years: int | None = None
    job_families: tuple[str, ...] = ()

    @property
    def modeled(self) -> bool:
        return self.model is not None and bool(self.model.channels)

    def short(self, pct) -> str:
        """Card text: 'Greenhouse est. 73%' or 'keyword 71%'."""
        return f"{self.vendor_label} est. {pct}%" if self.modeled else f"keyword {pct}%"


def _label(vendor_label: str, modeled: bool) -> str:
    if modeled:
        return f"Estimated {vendor_label} match (modeled on documented behavior)"
    return f"Keyword match ({vendor_label} matching not publicly documented)"


def weight(term: ats.Term, ch: Channel) -> float:
    if term.need:
        return 1.0
    if ch.has("preferred_zero"):
        return 0.0
    return PREFERRED_WEIGHT if ch.has("required_weight") else 1.0


def hit(term: ats.Term, bit: int, mask: int, lit: int | None, ch: Channel, have: frozenset[str]):
    """(credit 0..1, related skill used or None); credit None when a literal channel has no literal data."""
    if ch.skills == "literal":
        return (None, None) if lit is None else (float(lit >> bit & 1), None)
    if mask >> bit & 1:
        return 1.0, None
    if ch.has("related"):
        via = sorted(lexicon.related(term.name) & have)
        if via:
            return RELATED_CREDIT, via[0]
    return 0.0, None


def years_fit(resume, profile_years, min_years) -> float | None:
    have = resume.years if resume.years is not None else profile_years
    if not min_years or have is None:
        return None
    return 1.0 if have >= min_years else max(0.0, 1 - (min_years - have) / min_years)


def _grade(terms, bit: int) -> str | None:
    basic = [m >> bit & 1 for t, m, _ in terms if t.need]
    preferred = [m >> bit & 1 for t, m, _ in terms if not t.need]
    if not basic:
        return None

    def most(hits: list[int]) -> bool:
        return sum(hits) > MOST * len(hits)
    if all(basic):
        return "A" if not preferred or most(preferred) else "B"
    return "C" if most(basic) else "D"


def _channel_score(ch: Channel, terms, bit: int, resume, profile_years, min_years, job_fams) -> ChannelScore | None:
    total = got = 0.0
    for term, mask, lit in terms:
        w = weight(term, ch)
        credit, _ = hit(term, bit, mask, lit, ch, resume.terms)
        if credit is None:
            return None
        total, got = total + w, got + w * credit
    if not total:
        return ChannelScore(ch.name, None, None, None, None)
    parts = {"skills": got / total,
             "title": (1.0 if set(resume.families) & set(job_fams) else 0.0) if ch.has("title") else None,
             "years": years_fit(resume, profile_years, min_years) if ch.has("years") else None}
    used = {k: v for k, v in parts.items() if v is not None}
    pct = sum(FEATURE_WEIGHTS[k] * v for k, v in used.items()) / sum(FEATURE_WEIGHTS[k] for k in used)
    return ChannelScore(ch.name, round(100 * pct), parts["skills"], parts["title"], parts["years"])


def fired_checks(model: Model, resume) -> tuple[list[Check], list[Check]]:
    """(degrade/block checks, tip checks) this resume file trips; a profile without a file is not checked."""
    if resume.file is None:
        return [], []
    ext = resume.file.rsplit(".", 1)[-1].lower()
    found = []
    for c in model.checks:
        if c.name == "max_mb":
            tripped = resume.size_mb is not None and c.value is not None and resume.size_mb > float(c.value)
        elif c.name == "file_type":
            tripped = bool(c.value) and ext not in [str(v).lower() for v in c.value]
        else:
            tripped = c.name in resume.risks
        found += [c] if tripped else []
    return [c for c in found if c.effect != "tip"], [c for c in found if c.effect == "tip"]


def display_channel(model: Model | None) -> Channel | None:
    """The channel whose wording the matched/missing lists use: the literal one when the vendor has one."""
    if model is None or not model.channels:
        return None
    return next((c for c in model.channels if c.skills == "literal"), model.channels[0])


def _display(model: Model | None, terms, bit: int, resume) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Matched / missing in the vendor's form: the JD's own words for a literal channel, canonical names (plus
    related credit) otherwise; most important first."""
    ch = display_channel(model)
    matched, missing = [], []
    for term, mask, lit in sorted(terms, key=lambda x: keywords._order(x[0])):
        if ch is not None and ch.skills == "literal" and lit is not None:
            (matched if lit >> bit & 1 else missing).append(term.forms[0] if term.forms else term.name)
            continue
        credit, via = hit(term, bit, mask, lit, ch, resume.terms) if ch else (float(mask >> bit & 1), None)
        if credit and via:
            matched.append(f"{term.name} (~ via {via})")
        else:
            (matched if credit else missing).append(term.name)
    return tuple(matched), tuple(missing)


def _base(chans: list[ChannelScore], generic: int) -> float:
    """Mean of the applicable channels (ours); with none applicable the generic keyword % stands in."""
    live = [c.pct for c in chans if c.pct is not None]
    return sum(live) / len(live) if live else generic


def _one(model: Model | None, terms, bit, resume, profile_years, min_years, job_fams, pairs) -> VendorScore:
    generic = keywords.score_one(pairs, bit, resume.id, resume.label)
    if model is None or not model.channels:
        return VendorScore(resume.id, resume.label, generic.pct, generic.pct, None, (), generic.matched,
                           generic.missing, (), (), 1.0, generic.n_total)
    chans = [_channel_score(c, terms, bit, resume, profile_years, min_years, job_fams) for c in model.channels]
    fired, tips = fired_checks(model, resume)
    factor = 0.0 if any(c.effect == "block" for c in fired) else DEGRADE_FACTOR ** len(fired)
    pct = None if not terms or any(c is None for c in chans) else round(factor * _base(chans, generic.pct))
    graded = any(c.has("grade") for c in model.channels)
    matched, missing = _display(model, terms, bit, resume)
    return VendorScore(resume.id, resume.label, pct, generic.pct, _grade(terms, bit) if graded else None,
                       tuple(c for c in chans if c), matched, missing, tuple(c.name for c in fired),
                       tuple(c.name for c in tips), factor, len(terms))


def score(row: dict, resumes, profile_years=None, known: dict[str, Model] | None = None) -> Result:
    """Every resume scored under the job's vendor model (or the generic keyword % when the vendor has none)."""
    known = models() if known is None else known
    vendor = vendor_of(row, known)
    model = known.get(vendor)
    prof = ats_profile.profiles().get(vendor) if model is None else None
    # GOAL36 §5.1: an outside job's record (board outside:<person>) on an unknown ATS is "this ATS", like any other
    vendor_label = model.label if model else str((prof or {}).get("label") or (vendor if vendor != OUTSIDE else "")
                                                 or "this ATS")
    ignore = set(lexicon.settings()["display_ignore"])
    terms = tuple(t for t in keywords.unpack_full(row.get("fit_terms")) if t[0].name not in ignore)
    pairs = [(t, m) for t, m, _ in terms]
    job_fams = tuple(f for f in (row.get("families") or "").split(",") if f)
    used = model if model and model.channels else None
    scores = tuple(_one(used, terms, i, r, profile_years, row.get("min_years"), job_fams, pairs)
                   for i, r in enumerate(resumes))
    title_id = next((r.id for f in job_fams for r in resumes if f in r.families), None)
    ranked = [s for s in scores if s.pct is not None]
    best = max(ranked, key=lambda s: (s.pct, s.id == title_id, s.generic)) if ranked else None
    return Result(vendor, vendor_label, model, _label(vendor_label, used is not None), scores, best, terms,
                  row.get("min_years"), job_fams)


def explain(result: Result, vs: VendorScore, resume=None) -> list[str]:
    """The model's reasoning for one resume, generic -> vendor (decision 010); see vendor_explain.lines."""
    from . import vendor_explain
    return vendor_explain.lines(result, vs, resume)
