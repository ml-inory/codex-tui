from codex_tui.markdown import render_markdown


def _spans(text):
    return [(text.plain[s.start : s.end], str(s.style)) for s in text.spans]


def test_heading_and_paragraph_are_bold_and_separated() -> None:
    text = render_markdown("# Title\n\nSome **bold** body.")
    assert "Title" in text.plain
    assert "Some bold body." in text.plain
    assert "\n\n" in text.plain
    styles = [style for _, style in _spans(text)]
    assert any("bold" in style and "#E6EDF3" in style for style in styles)


def test_inline_emphasis_and_code() -> None:
    text = render_markdown("a `code` b **bold** c *italic* d ~~gone~~")
    plain = text.plain
    assert plain == "a code b bold c italic d gone"
    spans = _spans(text)
    assert any(part == "code" and "on #161B22" in style for part, style in spans)
    assert any(part == "bold" and "bold" in style for part, style in spans)
    assert any(part == "italic" and "italic" in style for part, style in spans)
    assert any(part == "gone" and "strike" in style for part, style in spans)


def test_fenced_code_block_keeps_content_and_language() -> None:
    text = render_markdown("```python\nprint('hi')\nx = 1\n```")
    assert "python" in text.plain
    assert "print('hi')" in text.plain
    spans = _spans(text)
    assert any(part == "print('hi')" and "on #161B22" in style for part, style in spans)


def test_lists_and_blockquote() -> None:
    text = render_markdown("- one\n- two\n\n> quoted")
    assert "• one" in text.plain
    assert "• two" in text.plain
    assert "│ quoted" in text.plain


def test_link_renders_label_only() -> None:
    text = render_markdown("see [docs](https://example.com) now")
    assert "see docs now" in text.plain
    assert "https://example.com" not in text.plain


def test_raw_text_is_not_parsed_as_markup() -> None:
    text = render_markdown("keep <b>this</b> [as-is")
    assert "keep <b>this</b> [as-is" in text.plain
