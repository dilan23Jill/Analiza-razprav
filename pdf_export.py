"""
PDF export of a debate analysis — a clean, printable overview with the summary,
per-speaker arguments, and fact-checked claims WITH their sources.

Pure-Python (reportlab), so it works on Windows without system libraries. If a
Unicode TTF is found on the machine it is used (so Slovenian č/š/ž render
correctly); otherwise it falls back to Helvetica.

Public API:
    build_pdf(debate: dict, language: str = "sl") -> bytes
where `debate` is the dict returned by database.get_debate (with analysis_json
and fact_check_json already parsed).
"""

from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, KeepTogether, ListFlowable, ListItem,
                                Paragraph, SimpleDocTemplate, Spacer)

from translations import label, get_verdict_label

# ── Fonts (Unicode if available, else Helvetica) ──────────────────────────────

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def _register_font() -> None:
    global FONT, FONT_BOLD
    regular = [
        "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    bold = [
        "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    reg = next((p for p in regular if os.path.exists(p)), None)
    bld = next((p for p in bold if os.path.exists(p)), None)
    if not reg:
        return
    try:
        pdfmetrics.registerFont(TTFont("DA", reg))
        FONT = "DA"
        if bld:
            pdfmetrics.registerFont(TTFont("DA-Bold", bld))
            FONT_BOLD = "DA-Bold"
        else:
            FONT_BOLD = "DA"
    except Exception:
        pass


_register_font()

# ── Labels ────────────────────────────────────────────────────────────────────

L = {
    "sl": {
        "analysis": "Analiza debate", "topic": "Tema", "mode": "Način", "date": "Datum",
        "verdict": "Sodba", "source_verdicts": "Kaj pove posamezen vir",
        "summary": "Povzetek", "position": "Stališče", "arguments": "Argumenti",
        "argument_label": "Argument", "derived": "Izpeljan argument (sklep)",
        "premises": "Premise", "premise_verdicts": "Preverjanje premis",
        "type": "Vrsta",
                "counter": "protiargument", "fallacies": "Logične napake", "rebuttals": "Izpodbijanja",
        "factcheck": "Preverjanje dejstev", "claim": "Trditev", "explanation": "Obrazložitev",
        "sources": "Viri", "speaker": "Govorec", "no_data": "Ni podatkov za prikaz.",
        "generated": "Ustvarjeno z Debate Analyzer",
    },
    "en": {
        "analysis": "Debate analysis", "topic": "Topic", "mode": "Mode", "date": "Date",
        "verdict": "Verdict", "source_verdicts": "What each source says",
        "summary": "Summary", "position": "Position", "arguments": "Arguments",
        "argument_label": "Argument", "derived": "Derived argument (conclusion)",
        "premises": "Premises", "premise_verdicts": "Fact-check of premises",
        "type": "Type",
                "counter": "counter", "fallacies": "Fallacies", "rebuttals": "Rebuttals",
        "factcheck": "Fact-check", "claim": "Claim", "explanation": "Explanation",
        "sources": "Sources", "speaker": "Speaker", "no_data": "No data to display.",
        "generated": "Generated with Debate Analyzer",
    },
}

VERDICT_COLORS = {
    "TRUE": colors.HexColor("#1a7f37"),
    "PARTIALLY_TRUE": colors.HexColor("#9a6700"),
    "MISLEADING": colors.HexColor("#bc4c00"),
    "FALSE": colors.HexColor("#cf222e"),
    "UNVERIFIABLE": colors.HexColor("#6e7781"),
}

def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "; ".join(_s(x) for x in v if x)
    return str(v).strip()


def _p(text: str) -> str:
    """Escape user text for reportlab Paragraph markup."""
    return escape(_s(text))


def _u(url: str) -> str:
    """Escape a URL for use inside a DOUBLE-QUOTED attribute. xml.sax's
    escape() does not touch quotes by default — a quote inside the URL would
    break out of the attribute and kill the whole paragraph parse."""
    return escape(_s(url), {'"': "&quot;", "'": "&#39;"})


import re as _re


def _para(text: str, style) -> Paragraph:
    """Build a Paragraph defensively: if reportlab rejects the inline markup
    (odd character in a URL, malformed tag...), fall back to plain escaped
    text so ONE bad line degrades gracefully instead of failing the export."""
    try:
        return Paragraph(text, style)
    except Exception:
        plain = escape(_re.sub(r"<[^>]+>", " ", _s(text)))
        try:
            return Paragraph(plain, style)
        except Exception:
            return Paragraph("", style)


def _styles():
    base = dict(fontName=FONT, fontSize=10, leading=14, alignment=TA_LEFT,
                textColor=colors.HexColor("#1b1f24"))
    return {
        "title": ParagraphStyle("t", **{**base, "fontName": FONT_BOLD, "fontSize": 18, "leading": 22}),
        "sub": ParagraphStyle("s", **{**base, "fontSize": 10.5, "textColor": colors.HexColor("#57606a")}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": FONT_BOLD, "fontSize": 13, "leading": 17,
                                       "spaceBefore": 12, "spaceAfter": 4,
                                       "textColor": colors.HexColor("#0a3069")}),
        "h3": ParagraphStyle("h3", **{**base, "fontName": FONT_BOLD, "fontSize": 11, "leading": 15,
                                      "spaceBefore": 8, "spaceAfter": 2}),
        "body": ParagraphStyle("b", **base),
        "small": ParagraphStyle("sm", **{**base, "fontSize": 8.5, "leading": 11,
                                         "textColor": colors.HexColor("#57606a")}),
        "li": ParagraphStyle("li", **{**base, "leading": 13}),
        # ── argument block styles ──
        "arglabel": ParagraphStyle("al", **{**base, "fontName": FONT_BOLD, "fontSize": 9,
                                            "leading": 12, "textColor": colors.HexColor("#0a3069"),
                                            "spaceBefore": 10, "spaceAfter": 4}),
        "premise": ParagraphStyle("pr", **{**base, "fontSize": 9.5, "leading": 13,
                                           "leftIndent": 22, "firstLineIndent": -12,
                                           "spaceAfter": 2,
                                           "textColor": colors.HexColor("#30363d")}),
        "conclusion": ParagraphStyle("cn", **{**base, "fontSize": 10.5, "leading": 14.5,
                                              "backColor": colors.HexColor("#f6f8fa"),
                                              "borderColor": colors.HexColor("#d0d7de"),
                                              "borderWidth": 0.5, "borderPadding": 6,
                                              "borderRadius": 3, "spaceBefore": 4,
                                              "spaceAfter": 4}),
    }


def build_pdf(debate: Dict, language: str = "sl") -> bytes:
    lang = "sl" if str(language or "sl").startswith("sl") else "en"
    t = L[lang]
    analysis = debate.get("analysis_json") if isinstance(debate.get("analysis_json"), dict) else {}
    fact_check = debate.get("fact_check_json") if isinstance(debate.get("fact_check_json"), dict) else {}
    st = _styles()
    story: List[Any] = []

    meta = analysis.get("metadata") or {}
    participants = meta.get("participants") if isinstance(meta.get("participants"), dict) else {}
    title = _s(debate.get("title")) or _s(meta.get("topic")) or t["analysis"]

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(_para(_p(title), st["title"]))
    sub_bits = []
    if meta.get("topic") and _s(meta.get("topic")) != title:
        sub_bits.append(_p(meta["topic"]))
    metaline = []
    if debate.get("mode"):
        metaline.append(f"{t['mode']}: {_p(meta.get('format') or debate.get('mode'))}")
    if debate.get("created_at"):
        metaline.append(f"{t['date']}: {_p(str(debate['created_at'])[:10])}")
    if sub_bits:
        story.append(_para(" — ".join(sub_bits), st["sub"]))
    if metaline:
        story.append(_para("  ·  ".join(metaline), st["small"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#d0d7de")))

    # ── Summary ────────────────────────────────────────────────────────────────
    summary = _s(analysis.get("summary"))
    if summary:
        story.append(_para(t["summary"], st["h2"]))
        story.append(_para(_p(summary), st["body"]))

    # ── Per-speaker arguments ──────────────────────────────────────────────────
    speakers = analysis.get("speakers") if isinstance(analysis.get("speakers"), dict) else {}
    all_fallacies = analysis.get("fallacies") if isinstance(analysis.get("fallacies"), list) else []

    for name, data in speakers.items():
        if not isinstance(data, dict):
            continue
        role = _s(participants.get(name))
        heading = _p(name) + (f'  <font size="9" color="#57606a">({_p(role)})</font>' if role else "")
        story.append(_para(heading, st["h2"]))

        if data.get("position"):
            story.append(_para(f"<b>{t['position']}:</b> {_p(data['position'])}", st["body"]))

        # Verdicts grouped by the argument whose premise they check.
        fact_checks_by_arg: Dict[str, List[Any]] = {}
        for c in (fact_check.get("fact_checks") or []):
            if isinstance(c, dict) and c.get("arg_id"):
                fact_checks_by_arg.setdefault(_s(c.get("arg_id")), []).append(c)

        args = data.get("arguments") if isinstance(data.get("arguments"), list) else []
        if args:
            for i, arg in enumerate(args, 1):
                if not isinstance(arg, dict):
                    continue
                block: List[Any] = []

                # ── Label line: ARGUMENT N   type ──
                tags = _p(label("argument_type", arg["type"], lang)) if arg.get("type") else ""
                head = f"{_p(t['argument_label']).upper()} {i}"
                if tags:
                    head += f'&nbsp;&nbsp;&nbsp;<font size="8" color="#8c959f">{tags}</font>'
                block.append(_para(head, st["arglabel"]))

                # ── Premises: numbered list with hanging indent ──
                prem = arg.get("premises") or []
                if prem:
                    block.append(_para(f'<i>{_p(t["premises"])}</i>', st["small"]))
                    for n, p_ in enumerate(prem, 1):
                        p_text = p_.get("premise", p_) if isinstance(p_, dict) else p_
                        block.append(_para(
                            f'<font color="#8c959f">{n}.</font>&nbsp;{_p(p_text)}',
                            st["premise"]))

                # ── Derived argument (conclusion) in a subtle box ──
                block.append(_para(
                    f'<font size="8" color="#57606a">{_p(t["derived"]).upper()}</font><br/>'
                    f"<b>{_p(arg.get('argument'))}</b>",
                    st["conclusion"]))

                # ── Fact-check of this argument's own premises ──
                # Claims are extracted from the arguments, so each verdict names
                # the argument it belongs to and can be printed where it matters
                # instead of only in the list at the end.
                checked = [c for c in fact_checks_by_arg.get(_s(arg.get("arg_id")), [])]
                if checked:
                    block.append(_para(f'<i>{_p(t["premise_verdicts"])}</i>', st["small"]))
                    for c in checked:
                        vcol = VERDICT_COLORS.get(
                            _s(c.get("verdict")).upper(),
                            VERDICT_COLORS["UNVERIFIABLE"]).hexval()[2:]
                        block.append(_para(
                            f'<font color="#{vcol}"><b>'
                            f'{_p(get_verdict_label(c.get("verdict") or "UNVERIFIABLE", lang)["label"])}'
                            f'</b></font>&nbsp;{_p(c.get("exact_claim"))}',
                            st["premise"]))

                story.append(KeepTogether(block))
                story.append(Spacer(1, 6))

        spk_fallacies = [f for f in all_fallacies if isinstance(f, dict) and _s(f.get("speaker")) == _s(name)]
        if spk_fallacies:
            story.append(_para(t["fallacies"], st["h3"]))
            fitems = []
            for f in spk_fallacies:
                line = f"<b>{_p(label('fallacy', f.get('type'), lang))}</b>"
                if f.get("explanation"):
                    line += f" — {_p(f['explanation'])}"
                fitems.append(ListItem(_para(line, st["li"])))
            story.append(ListFlowable(fitems, bulletType="bullet", leftIndent=14))

    # ── Fact-check with sources ────────────────────────────────────────────────
    claims = fact_check.get("fact_checks") or fact_check.get("claims") or []
    claims = [c for c in claims if isinstance(c, dict)]
    if claims:
        story.append(_para(t["factcheck"], st["h2"]))
        for c in claims:
            ctext = _s(c.get("exact_claim") or c.get("claim") or c.get("statement"))
            if not ctext:
                continue
            verdict = _s(c.get("verdict") or c.get("verdict_label") or "UNVERIFIABLE").upper()
            col = VERDICT_COLORS.get(verdict, VERDICT_COLORS["UNVERIFIABLE"]).hexval()[2:]
            spk = _s(c.get("speaker"))
            vlabel = get_verdict_label(verdict, lang)["label"]
            head = f'<b><font color="#{col}">[{_p(vlabel)}]</font></b> {_p(ctext)}'
            if spk:
                head += f' <font size="8" color="#57606a">— {_p(spk)}</font>'
            story.append(_para(head, st["body"]))
            if c.get("explanation"):
                story.append(_para(f'<font size="9" color="#57606a">{_p(c["explanation"])}</font>', st["li"]))
            # Kaj pove posamezen vir, po istih petih razsodbah kot trditev.
            tally = c.get("source_verdicts") or {}
            bits = [f'{_p(get_verdict_label(v, lang)["label"])}: {tally[v]}'
                    for v in ("TRUE", "PARTIALLY_TRUE", "MISLEADING", "FALSE", "UNVERIFIABLE")
                    if isinstance(tally, dict) and tally.get(v)]
            if bits:
                story.append(_para(
                    f'<font size="9" color="#57606a"><i>{t["source_verdicts"]}:</i> '
                    + " · ".join(bits) + "</font>", st["li"]))

            srcs = _collect_sources(c)
            if srcs:
                links = []
                for s in srcs:
                    url = _s(s.get("url"))
                    title = _s(s.get("title")) or _domain(url) or url
                    sv = _s(s.get("source_verdict"))
                    mark = f' <font size="7" color="#57606a">({_p(get_verdict_label(sv, lang)["label"])})</font>' if sv else ""
                    if url:
                        links.append(f'<a href="{_u(url)}" color="#0a3069">{_p(title)}</a>{mark}')
                    elif title:
                        links.append(_p(title) + mark)
                if links:
                    story.append(_para(f'<font size="9"><i>{t["sources"]}:</i> ' + " · ".join(links) + "</font>", st["li"]))
            story.append(Spacer(1, 4))

    if len(story) <= 4:
        story.append(_para(t["no_data"], st["body"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=title, author=t["generated"],
        topMargin=16 * mm, bottomMargin=15 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    doc.build(story, onFirstPage=_footer(t), onLaterPages=_footer(t))
    return buf.getvalue()


def _footer(t: Dict):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#8c959f"))
        canvas.drawString(16 * mm, 8 * mm, t["generated"])
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, str(doc.page))
        canvas.restoreState()
    return draw


def _collect_sources(claim: Dict) -> List[Dict]:
    seen = set()
    out = []
    for s in (claim.get("sources") or []):
        url = s.get("url") if isinstance(s, dict) else s
        url = _s(url)
        if url and url not in seen:
            seen.add(url)
            out.append(s if isinstance(s, dict) else {"url": url})
    # Analize, shranjene pred prehodom na en razsojevalni korak, hranijo
    # Perplexityjeve navedke ločeno. Novejše jih imajo že v claim["sources"].
    pdata = claim.get("perplexity_data") or {}
    for url in (pdata.get("citations") or []) if isinstance(pdata, dict) else []:
        url = _s(url)
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url})
    return out


def _domain(url: str) -> str:
    u = _s(url)
    if "://" in u:
        u = u.split("://", 1)[1]
    return u.split("/", 1)[0].replace("www.", "")
