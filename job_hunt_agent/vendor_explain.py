"""The vendor model's reasoning for one resume (GOAL5 §3 evidence, decision 010): from the generic keyword % to the
vendor estimate, channel by channel, each step with the vendor facts that turn it on and our numbers marked "ours"."""
from . import vendor_model as vm

FEATURE_NAMES = {"related": "related skills", "required_weight": "required vs preferred weighting",
                 "preferred_zero": "preferred keywords ignored", "title": "title alignment", "years": "years alignment",
                 "grade": "grade bands"}


def _urls(model: vm.Model, ids) -> str:
    seen = []
    for i in ids:
        url = model.source(i)
        seen += [url] if url not in seen else []
    return " ".join(f"[{u}]" for u in seen)


def _skill_lines(model: vm.Model, ch: vm.Channel, terms, bit: int, have: frozenset[str]) -> list[str]:
    out = []
    for term, mask, lit in terms:
        canon = bool(mask >> bit & 1)
        if ch.skills == "literal" and lit is not None and canon and not lit >> bit & 1:
            said = " / ".join(f'"{f}"' for f in term.forms) or f'"{term.name}"'
            out.append(f"    ✗ JD says {said}; your resume has {term.name} only in other words (generic counts it)")
        elif ch.skills == "taxonomy" and not canon:
            credit, via = vm.hit(term, bit, mask, lit, ch, have)
            if via:
                out.append(f"    ~ {term.name}: {credit} via {via} (related skills "
                           f"{_urls(model, dict(ch.features)['related'])}; {vm.RELATED_CREDIT} is ours)")
    return out


def _weight_line(model: vm.Model, ch: vm.Channel) -> str:
    feats = dict(ch.features)
    if ch.has("preferred_zero"):
        return f"    weights: only required keywords count {_urls(model, feats['preferred_zero'])}"
    if ch.has("required_weight"):
        return (f"    weights: required 1.0, preferred {vm.PREFERRED_WEIGHT} (ours) "
                f"{_urls(model, feats['required_weight'])}")
    return "    weights: every keyword the same (required vs preferred weighting not documented for this channel)"


def _channel_lines(result: vm.Result, ch: vm.Channel, cs: vm.ChannelScore, bit: int, resume) -> list[str]:
    model, feats = result.model, dict(ch.features)
    if cs.pct is None:
        why = feats.get("preferred_zero", ch.cite)
        return [f"  Channel: {ch.name} {_urls(model, ch.cite)}",
                f"    not applicable: every tracked keyword in this JD is preferred, and this channel counts only "
                f"required ones {_urls(model, why)}"]
    mode = ("literal: the JD's own words, case-insensitive, no alias/plural/acronym credit (ours where undocumented)"
            if ch.skills == "literal" else "taxonomy: the skill or any of its aliases counts")
    out = [f"  Channel: {ch.name} {_urls(model, ch.cite)}", f"    skills, {mode}: {cs.skills:.0%}"]
    out += _skill_lines(model, ch, result.terms, bit, resume.terms if resume else frozenset())
    out.append(_weight_line(model, ch))
    if ch.has("title"):
        fams = ", ".join(resume.families) if resume else "?"
        out.append(f"    title {cs.title:.0%}: resume for {fams}; job {', '.join(result.job_families) or '?'} "
                   f"{_urls(model, feats['title'])} (rule ours)")
    if ch.has("years"):
        out.append(f"    years {cs.years:.0%}: resume {resume.years:.1f} y vs JD {result.min_years}+ "
                   f"{_urls(model, feats['years'])} (rule ours)" if cs.years is not None and resume and resume.years
                   else f"    years: dropped (JD states no minimum or the resume has no dates) "
                        f"{_urls(model, feats['years'])}")
    out.append(f"    = channel {cs.pct}% (skills/title/years weights ours)")
    return out


def lines(result: vm.Result, vs: vm.VendorScore, resume=None) -> list[str]:
    bit = next(i for i, s in enumerate(result.scores) if s.id == vs.id)
    n = vs.n_total
    hits = sum(1 for t, m, _ in result.terms if m >> bit & 1)
    out = [f"{vs.label} resume · {result.vendor_label}: generic keyword match {vs.generic}% ({hits}/{n} JD keywords; "
           "any alias counts, every keyword the same)"]
    if not result.modeled:
        return out + [f"{result.label}: the generic keyword % is used as is."]
    model = result.model
    for ch, cs in zip(model.channels, vs.channels):
        out += _channel_lines(result, ch, cs, bit, resume)
    if vs.grade:
        ids = next(dict(c.features)["grade"] for c in model.channels if c.has("grade"))
        out.append(f"  Grade {vs.grade} (documented A-D bands {_urls(model, ids)}; 'most' = more than half, ours)")
    live = [c.pct for c in vs.channels if c.pct is not None]
    if not live:
        out.append(f"  No channel applies to this JD: the generic keyword {vs.generic}% stands in (ours)")
    elif len(live) > 1:
        out.append(f"  Combine: mean of {len(live)} channels (ours) = {round(sum(live) / len(live))}%")
    if resume is not None and resume.file is None:
        out.append("  Parsing checks: not run (this profile has no resume file yet)")
    elif vs.parse_fired:
        cites = [i for c in model.checks if c.name in vs.parse_fired for i in c.cite]
        out.append(f"  Parsing: x{vs.factor:.2f} for {', '.join(vs.parse_fired)} {_urls(model, cites)} "
                   f"({vm.DEGRADE_FACTOR} each is ours; a documented block = 0)")
    else:
        out.append(f"  Parsing: none of the {len(model.checks)} documented checks fired (x1.00)")
    used = {f for c in model.channels for f, _ in c.features}
    off = [name for f, name in FEATURE_NAMES.items() if f not in used]
    if off:
        out.append(f"  Off (no cited {model.label} fact): {', '.join(off)}")
    delta = (vs.pct or 0) - vs.generic
    out.append(f"= Estimated {result.vendor_label} match {vs.pct}% (generic {vs.generic}%, {delta:+d} points)")
    return out
