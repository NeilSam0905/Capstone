#!/usr/bin/env python3
"""Render docs/CHAPTER_4_DRAFT.md into docs/chapter4.html.

Why this exists
---------------
`docs/chapter4.html` was a hand-conversion of the draft with no generator, so it went
stale the moment the draft was edited and could only be brought forward by
re-transcribing it — the one thing the chapter's own evidence rule forbids. This tool
makes the HTML *derived*: the draft is the single source, and a stale page is one
command away from correct.

It renders only the Markdown constructs Chapter 4 actually uses, and fails loudly on
anything else rather than dropping it silently:

  ## / ###          section headings (a leading "4.1" becomes the h2 number span)
  ***Table N. ...***  caption, attached to the table that follows it
  | a | b |         pipe table; a `---:` delimiter marks a numeric column
  *Source...*       a whole-paragraph italic beginning Source/Sources
  > ...             note aside; a leading **Label.** becomes the aside's label
  - / 1.            lists
  ```               fenced block
  ---               rule
  text              paragraph

Presentation (stylesheet, masthead, rail, footer) lives in tools/chapter4_shell.html,
extracted verbatim from the 16 Sep 2026 page so the design is unchanged.

Usage:  python tools/render_chapter4.py [--check]
        --check exits non-zero if the committed HTML differs from a fresh render,
        which is the CI-shaped question "is docs/chapter4.html stale?".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "docs" / "CHAPTER_4_DRAFT.md"
SHELL = ROOT / "tools" / "chapter4_shell.html"
OUT = ROOT / "docs" / "chapter4.html"

DATE = "Reconciled 23 Sep 2026"
LEDE = (
    '<p class="lede">Every figure here is transcribed from the committed artifact named beside it '
    '— <code>ustore.db</code>, the CSVs under <code>data/</code>, or the experiment logs under '
    '<code>docs/</code>. Nothing is recomputed for this chapter. Reconciled against the policy '
    'layer on 23 September 2026; the finding-by-finding audit is '
    '<code>docs/CHAPTER_4_RECONCILIATION.md</code>.</p>'
)
CHIPS = [
    ("84,399", "sales records"),
    ("519", "catalog items"),
    ("15", "forecasting methods"),
    ("3,192", "walk-forward folds"),
    ("208 priced", "58 flagged"),
    ("13 of 14", "acceptance checks"),
]

# --------------------------------------------------------------------------- inline


_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")


def _escape(text: str) -> str:
    """HTML-escape, leaving character entities the draft writes by hand intact."""
    out, pos = [], 0
    for m in _ENTITY.finditer(text):
        out.append(text[pos:m.start()].replace("&", "&amp;"))
        out.append(m.group(0))
        pos = m.end()
    out.append(text[pos:].replace("&", "&amp;"))
    return "".join(out).replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str) -> str:
    """`code` -> em/strong -> escape, in that order so markup inside code is literal."""
    spans: list[str] = []

    def stash(m: re.Match) -> str:
        spans.append(_escape(m.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = _escape(text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text, flags=re.S)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{spans[int(m.group(1))]}</code>", text)


def slug(text: str) -> str:
    text = re.sub(r"[`*]", "", text).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-")


# --------------------------------------------------------------------------- blocks


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render_table(rows: list[str], caption: str | None, number: str | None) -> str:
    header = split_row(rows[0])
    aligns = ["num" if c.endswith(":") and set(c) <= set("-: ") else "" for c in split_row(rows[1])]
    aligns += [""] * (len(header) - len(aligns))

    def cells(vals: list[str], tag: str) -> str:
        vals = vals + [""] * (len(header) - len(vals))
        return "".join(
            f'<{tag} class="{aligns[i]}">{inline(v)}</{tag}>' for i, v in enumerate(vals[: len(header)])
        )

    thead = f"<thead><tr>{cells(header, 'th')}</tr></thead>"
    body = "".join(f"<tr>{cells(split_row(r), 'td')}</tr>" for r in rows[2:])
    cap = ""
    if caption is not None:
        cap = (
            f'<figcaption class="tcap"><span class="tcap__n">Table {number}</span>'
            f'<span class="tcap__t">{inline(caption)}</span></figcaption>'
        )
    return (
        f'<figure class="tw">{cap}<div class="tscroll">'
        f"<table>{thead}<tbody>{body}</tbody></table></div></figure>"
    )


def render_note(lines: list[str]) -> str:
    text = " ".join(l.lstrip("> ").rstrip() if l.strip() != ">" else "" for l in lines)
    paras = [p.strip() for p in re.split(r"\s{2,}(?=\S)|\n\n", text) if p.strip()]
    text = " ".join(paras)
    m = re.match(r"\*\*(.+?)\*\*\s*(.*)", text, re.S)
    if m:
        label, rest = m.group(1), m.group(2)
        body = f'<p class="note__label">{inline(label)}</p>'
        return f'<aside class="note">{body}<p>{inline(rest)}</p></aside>' if rest.strip() else f'<aside class="note">{body}</aside>'
    return f'<aside class="note"><p>{inline(text)}</p></aside>'


def render_list(items: list[str], ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    lis = "".join(f"<li>{inline(i)}</li>" for i in items)
    return f'<{tag} class="list">{lis}</{tag}>'


CAPTION = re.compile(r"^\*\*\*Table\s+([0-9]+)\.\s*(.*?)\*\*\*$", re.S)
SRC_PARA = re.compile(r"^\*Sources?[:\s].*\*$", re.S)


def render(md: str) -> tuple[str, list[tuple[int, str, str, str]]]:
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str, str]] = []   # (level, id, number, title)
    pending: tuple[str, str] | None = None      # (number, caption) awaiting its table
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.startswith("# "):                       # page title -> masthead, not body
            i += 1
            continue

        if line.startswith("```"):
            fence, i = [], i + 1
            while i < n and not lines[i].startswith("```"):
                fence.append(lines[i])
                i += 1
            i += 1
            out.append(f'<pre class="block">{_escape(chr(10).join(fence))}</pre>')
            continue

        if re.fullmatch(r"-{3,}", line.strip()):
            out.append('<hr class="sep">')
            i += 1
            continue

        if line.startswith("## ") or line.startswith("### "):
            level = 2 if line.startswith("## ") else 3
            title = line.split(" ", 1)[1].strip()
            ident = slug(title)
            m = re.match(r"^(\d+\.\d+)\s+(.*)$", title)
            if level == 2 and m:
                num, rest = m.group(1), m.group(2)
                out.append(
                    f'<h2 id="{ident}" class="h2"><span class="h2__num">{num}</span>'
                    f'<span class="h2__t">{inline(rest)}</span></h2>'
                )
                toc.append((2, ident, num, rest))
            elif level == 2:
                out.append(f'<h2 id="{ident}" class="h2"><span class="h2__t">{inline(title)}</span></h2>')
                toc.append((2, ident, "", title))
            else:
                out.append(f'<h3 id="{ident}" class="h3">{inline(title)}</h3>')
                toc.append((3, ident, "", title))
            i += 1
            continue

        if line.startswith(">"):
            block = []
            while i < n and lines[i].startswith(">"):
                block.append(lines[i])
                i += 1
            out.append(render_note(block))
            continue

        if line.lstrip().startswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            if len(rows) < 2:
                raise SystemExit(f"table with no delimiter row near: {rows[0][:60]!r}")
            number, caption = pending if pending else (None, None)
            out.append(render_table(rows, caption, number))
            pending = None
            continue

        # An ordered list must open at "1." — a wrapped sentence ending in a
        # cross-reference ("...in Table\n2. This reflects...") otherwise reads as one.
        if re.match(r"^-\s", line) or re.match(r"^1\.\s", line):
            ordered = bool(re.match(r"^1\.\s", line))
            items, cur = [], ""
            while i < n and lines[i].strip():
                nxt = lines[i]
                if re.match(r"^(-\s|\d+\.\s)", nxt):
                    if cur:
                        items.append(cur.strip())
                    cur = re.sub(r"^(-\s|\d+\.\s)", "", nxt)
                elif nxt.startswith((" ", "\t")):
                    cur += " " + nxt.strip()
                else:
                    break
                i += 1
            if cur:
                items.append(cur.strip())
            out.append(render_list(items, ordered))
            continue

        para = []
        while i < n and lines[i].strip() and not lines[i].lstrip().startswith(("|", ">", "#", "```")):
            para.append(lines[i])
            i += 1
        text = "\n".join(para).strip()
        joined = " ".join(l.strip() for l in para).strip()

        m = CAPTION.match(joined)
        if m:
            pending = (m.group(1), m.group(2).strip())
            continue
        if SRC_PARA.match(joined):
            out.append(f'<p class="src">{inline(joined[1:-1])}</p>')
            continue
        out.append(f"<p>{inline(joined)}</p>")

    if pending:
        raise SystemExit(f"caption for Table {pending[0]} has no table after it")
    return "\n".join(out), toc


def render_rail(toc: list[tuple[int, str, str, str]]) -> str:
    rows = []
    for level, ident, num, title in toc:
        if level == 2 and num:
            rows.append(
                f'<a class="rail__h2" href="#{ident}"><span class="rail__num">{num}</span>'
                f'<span>{inline(title)}</span></a>'
            )
        elif level == 2:
            rows.append(f'<a class="rail__h2" href="#{ident}"><span>{inline(title)}</span></a>')
        else:
            rows.append(f'<a class="rail__h3" href="#{ident}">{inline(title)}</a>')
    return "\n".join(rows)


def build() -> str:
    body, toc = render(DRAFT.read_text(encoding="utf-8"))
    chips = "\n".join(
        f'      <span class="chip"><b>{a}</b> {b}</span>' for a, b in CHIPS
    )
    page = SHELL.read_text(encoding="utf-8")
    for key, val in (("{{DATE}}", DATE), ("{{LEDE}}", LEDE), ("{{CHIPS}}", chips),
                     ("{{RAIL}}", render_rail(toc)), ("{{BODY}}", body)):
        assert key in page, f"shell is missing {key}"
        page = page.replace(key, val)
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if docs/chapter4.html is stale")
    args = ap.parse_args()

    page = build()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != page:
            print("docs/chapter4.html is STALE — run: python tools/render_chapter4.py")
            return 1
        print("docs/chapter4.html is up to date with docs/CHAPTER_4_DRAFT.md")
        return 0

    OUT.write_text(page, encoding="utf-8")
    tables = page.count('<figure class="tw">')
    print(f"wrote {OUT.relative_to(ROOT)} — {len(page):,} bytes, {tables} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
