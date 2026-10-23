"""`job-hunt-agent baseline-master <file.docx>` (GOAL28 §1): make the person's ONE master resume the base everything reads.

The master as written in Word keeps its look; only what an ATS or a re-render can lose is normalized:
- header links show their address ("linkedin.com/in/x | github.com/y", still hyperlinked), not "LinkedIn" / "GitHub";
- a job header's or education line's dates sit on ONE right tab at the text margin, not after typed spaces (Google Docs
  re-flows typed padding and wraps the date, GOAL3 §3.5);
- body text is never justified.
Then the old bases are moved to resumes/archive/<date>/ (nothing deleted) and `baseline.import_baseline` rebuilds
template.docx (old one kept as template.<stamp>.docx), baseline.json and known_skills.txt (lines only added).
"""
import copy
import re
import shutil
from datetime import date
from pathlib import Path

import docx
from docx.oxml.ns import qn

from . import baseline

EMU_PER_TWIP = 635
RIGHT_TAB_SLACK = 20          # twips inside the text edge, so the date's last glyph never touches the margin
# w:pPr children that come after w:tabs (ECMA-376 order): a new w:tabs goes before the first one present
_AFTER_TABS = ("suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
               "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing",
               "mirrorIndents", "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap",
               "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange")


def link_text(target: str) -> str:
    """'https://www.linkedin.com/in/x/' -> 'linkedin.com/in/x' (what an ATS keeps and a reader can type)."""
    text = re.sub(r"^https?://", "", target.strip(), flags=re.I)
    return re.sub(r"^www\.", "", text, flags=re.I).rstrip("/")


def show_link_addresses(document) -> list[str]:
    """Every web hyperlink's visible text becomes its address; mail links keep their text. Returns the new texts."""
    rels = document.part.rels
    out = []
    for link in document.element.body.iter(qn("w:hyperlink")):
        rel = rels.get(link.get(qn("r:id")))
        target = getattr(rel, "target_ref", "") if rel is not None else ""
        texts = list(link.iter(qn("w:t")))
        if not target.lower().startswith(("http://", "https://")) or not texts:
            continue
        texts[0].text = link_text(target)
        for t in texts[1:]:
            t.text = ""
        out.append(texts[0].text)
    return out


def _text_right_edge(document, paragraph) -> int:
    """Where the paragraph's text column ends, in twips from the left margin (tab stops are measured from there)."""
    section = document.sections[0]
    width = (section.page_width - section.left_margin - section.right_margin) // EMU_PER_TWIP
    ind = paragraph._p.find(f"{qn('w:pPr')}/{qn('w:ind')}")
    right = int(ind.get(qn("w:right")) or 0) if ind is not None else 0
    return int(width) - right


def _set_right_tab(paragraph, pos: int) -> None:
    """Replace the paragraph's tab stops with one right tab at `pos` twips."""
    ppr = paragraph._p.get_or_add_pPr()
    tabs = ppr.makeelement(qn("w:tabs"), {})
    tabs.append(tabs.makeelement(qn("w:tab"), {qn("w:val"): "right", qn("w:pos"): str(pos)}))
    old = ppr.find(qn("w:tabs"))
    if old is not None:
        old.addprevious(tabs)
        ppr.remove(old)
        return
    after = next((c for c in ppr if c.tag in {qn(f"w:{n}") for n in _AFTER_TABS}), None)
    if after is not None:
        after.addprevious(tabs)
    else:
        ppr.append(tabs)


def _bare_copy(run_el):
    """A new run with the same run properties and no content."""
    new = copy.deepcopy(run_el)
    for child in list(new):
        if child.tag != qn("w:rPr"):
            new.remove(child)
    return new


def retab(paragraph, start: int, end: int) -> None:
    """The text in [start, end) (the padding before the dates) becomes ONE tab run, right after the run holding the
    left text's last character; runs left empty are removed."""
    spans, pos = [], 0
    for run in paragraph.runs:
        spans.append((run, pos, pos + len(run.text)))
        pos += len(run.text)
    anchor = next(run for run, lo, hi in spans if lo < start <= hi)
    for run, lo, hi in spans:
        if run is not anchor and (hi <= start or lo >= end):
            continue
        text = run.text
        head, tail = text[:max(0, start - lo)], (text[end - lo:] if hi > end else "")
        if run is anchor:
            run.text = head
            tab = _bare_copy(run._r)
            tab.append(tab.makeelement(qn("w:tab"), {}))
            run._r.addnext(tab)
            if tail:
                rest = _bare_copy(run._r)
                t = rest.makeelement(qn("w:t"), {})
                t.text = tail
                rest.append(t)
                tab.addnext(rest)
        elif tail:
            run.text = tail
        else:
            run._r.getparent().remove(run._r)


def tab_the_dates(document) -> list[str]:
    """Each dated job header / education line: its date padding becomes one tab and the paragraph gets one right tab
    stop at its text edge. Returns the left texts of the lines changed."""
    changed = []
    for paragraph, kind in baseline.walk(document):
        single = kind == "edu"
        if kind not in ("job_header", "edu"):
            continue
        span = baseline.padded_span(paragraph.text, single=single)
        if span is None or "".join(r.text for r in paragraph.runs) != paragraph.text:   # a link inside: left as is
            continue
        left = baseline.split_dated(paragraph.text, single=single)[0]
        retab(paragraph, *span)
        _set_right_tab(paragraph, _text_right_edge(document, paragraph) - RIGHT_TAB_SLACK)
        changed.append(left)
    return changed


def unjustify(document) -> int:
    """Body paragraphs are left-aligned, never justified ('both'). Returns how many changed."""
    n = 0
    for jc in document.element.body.iter(qn("w:jc")):
        if jc.get(qn("w:val")) in ("both", "distribute"):
            jc.set(qn("w:val"), "left")
            n += 1
    return n


def normalize(src: Path, dst: Path) -> dict:
    """Write the normalized copy of `src` to `dst`; what changed, for the report."""
    document = docx.Document(str(src))
    out = {"links": show_link_addresses(document), "dates_tabbed": tab_the_dates(document),
           "unjustified": unjustify(document)}
    document.save(str(dst))
    return out


def archive_old(resumes: Path, day: date | None = None) -> list[str]:
    """Move every .docx / .pdf in `resumes` to resumes/archive/<day>/. Nothing is deleted; a name already in that
    archive folder gets a numeric suffix."""
    folder = resumes / "archive" / (day or date.today()).isoformat()
    moved = []
    for path in sorted(resumes.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".docx", ".pdf"):
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target, n = folder / path.name, 1
        while target.exists():
            target, n = folder / f"{path.stem}.{n}{path.suffix}", n + 1
        shutil.move(str(path), str(target))
        moved.append(path.name)
    return moved


def install(master_docx: Path, profile_dir: Path, name: str, master_pdf: Path | None = None,
            day: date | None = None) -> dict:
    """Archive the old bases, write the normalized master as resumes/<name>.docx (+ the given PDF beside it), then
    rebuild template.docx / baseline.json / known_skills.txt from it. Nothing changes if the master does not parse."""
    resumes = profile_dir / "resumes"
    resumes.mkdir(parents=True, exist_ok=True)
    staged = profile_dir / f".{name}.staged.docx"
    changes = normalize(Path(master_docx), staged)
    problems = baseline.validate(baseline.parse(staged))
    if problems:
        staged.unlink()
        raise ValueError(f"could not parse {master_docx}: {problems}")
    moved = archive_old(resumes, day)
    target = resumes / f"{name}.docx"
    shutil.move(str(staged), str(target))
    if master_pdf is not None:
        shutil.copy2(master_pdf, resumes / f"{name}.pdf")
    resume = baseline.import_baseline(target, profile_dir)
    return {"normalized": changes, "archived": moved, "docx": str(target), "resume": resume}
