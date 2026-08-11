"""Tiny Markdown renderer for chat transcripts.

Rich's full CommonMark parser is overkill for chat rows: this module turns the
common subset (headings, bold/italic/strikethrough, inline code, fenced code
blocks, lists, blockquotes, links, rules) straight into styled
``rich.text.Text``, which Textual renders far cheaper than a ``Markdown``
widget.
"""

from __future__ import annotations

import re

from rich.text import Text


_CODE_BG = "#161B22"
_HEADER_STYLE = "bold #E6EDF3"
_QUOTE_COLOR = "#8B949E"
_LINK_COLOR = "#58A6FF"

_INLINE_RE = re.compile(
    r"(`[^`\n]+`|\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|__[^_\n]+__|"
    r"\*[^*\n]+\*|_[^_\n]+_|~~[^~\n]+~~|\[[^\]\n]+\]\([^)\n]+\))"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_LIST_RE = re.compile(r"^([-*+]|\d+\.)\s+(.*)$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _append_inline(text: Text, raw: str, base: str = "") -> None:
    """Append ``raw`` with inline markdown spans resolved."""

    def emit(part: str, style: str = "") -> None:
        if base and style:
            style = f"{base} {style}"
        elif base:
            style = base
        text.append(part, style=style or None)

    pos = 0
    for match in _INLINE_RE.finditer(raw):
        if match.start() > pos:
            emit(raw[pos : match.start()])
        token = match.group(0)
        if token.startswith("`"):
            emit(token[1:-1], f"on {_CODE_BG}")
        elif token.startswith("***"):
            emit(token[3:-3], "bold italic")
        elif token.startswith("**") or token.startswith("__"):
            emit(token[2:-2], "bold")
        elif token.startswith("~~"):
            emit(token[2:-2], "strike")
        elif token.startswith("["):
            emit(token[1 : token.find("]")], _LINK_COLOR)
        else:
            emit(token[1:-1], "italic")
        pos = match.end()
    emit(raw[pos:])


def render_markdown(content: str) -> Text:
    """Convert a Markdown-ish assistant message into styled text."""
    blocks: list[Text] = []
    lines = content.splitlines()
    index = 0
    count = len(lines)
    while index < count:
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue

        fence = _FENCE_RE.match(stripped)
        if fence:
            marker = fence.group(1)
            language = stripped[len(marker) :].strip()
            body: list[str] = []
            index += 1
            while index < count and not lines[index].strip().startswith(marker):
                body.append(lines[index])
                index += 1
            index += 1  # skip the closing fence
            block = Text()
            if language:
                block.append(language, style=f"bold {_HEADER_STYLE} on {_CODE_BG}")
                block.append("\n")
            for line_no, code_line in enumerate(body):
                block.append(code_line, style=f"on {_CODE_BG}")
                if line_no < len(body) - 1:
                    block.append("\n")
            blocks.append(block)
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            block = Text()
            _append_inline(block, heading.group(2), base=_HEADER_STYLE)
            blocks.append(block)
            index += 1
            continue

        if _RULE_RE.match(stripped):
            blocks.append(Text("─" * 24, style="dim"))
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < count:
                quote = lines[index].strip()
                if not quote.startswith(">"):
                    break
                quote_lines.append(quote[1:].strip())
                index += 1
            block = Text()
            for line_no, quote_line in enumerate(quote_lines):
                block.append("│ ", style=f"bold {_QUOTE_COLOR}")
                _append_inline(block, quote_line)
                if line_no < len(quote_lines) - 1:
                    block.append("\n")
            blocks.append(block)
            continue

        list_match = _LIST_RE.match(stripped)
        if list_match:
            items: list[tuple[str, str]] = []
            while index < count:
                match = _LIST_RE.match(lines[index].strip())
                if not match:
                    break
                items.append((match.group(1), match.group(2)))
                index += 1
            block = Text()
            for item_no, (marker, item) in enumerate(items):
                bullet = "•" if marker in ("-", "*", "+") else f"{item_no + 1}."
                block.append(f" {bullet} ", style="bold")
                _append_inline(block, item)
                if item_no < len(items) - 1:
                    block.append("\n")
            blocks.append(block)
            continue

        paragraph: list[str] = []
        while index < count:
            candidate = lines[index].strip()
            if not candidate:
                break
            if (
                candidate.startswith(("```", "~~~", ">", "#"))
                or _RULE_RE.match(candidate)
                or _LIST_RE.match(candidate)
            ):
                break
            paragraph.append(candidate)
            index += 1
        block = Text()
        for line_no, para_line in enumerate(paragraph):
            _append_inline(block, para_line)
            if line_no < len(paragraph) - 1:
                block.append("\n")
        blocks.append(block)

    out = Text()
    for block_no, block in enumerate(blocks):
        if block_no:
            out.append("\n\n")
        out.append(block)
    return out
