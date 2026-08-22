"""
exchange (거래소공시, real embedded HTML) parser front-end.

Structure: <html><head>...</head><body><div class="xforms">
  [optional correction-summary block, marked only by a bold "정정신고(보고)"
   span - a sibling div#LIB_... box is NOT a reliable correction signal, it
   is reused for non-correction attachment blocks too]
  (<div class="xforms_title">...</div> <table>...</table>)+   - one heading
  + table pair per disclosure item
</div></body></html>

A <span class="noprint"> dummy marker appears in every document and carries
no content - stripped before rendering. <a href="..."> links become markdown
links. A cell counts as a "value" cell if it (or a descendant span) carries
class="xforms_input", or it contains a link with no xforms_input label
sibling of its own (the "관련공시" case - a link-only cell has no
xforms_input class at all but is still the value, never a label).
"""
import os

from common_parse import parse_markup, collapse_ws
from lv_render import render_mixed_table
from config import CORPUS_DIR

DOC_GROUP = "exchange"


class FileResult:
    def __init__(self, attachment_suffix, parsed, file_path):
        self.attachment_suffix = attachment_suffix
        self.parsed = parsed
        self.file_path = file_path


class ParsedDoc:
    def __init__(self, company_name, document_name, is_correction_tag, markdown, warnings):
        self.company_name = company_name
        self.document_name = document_name
        self.is_correction_tag = is_correction_tag
        self.markdown = markdown
        self.warnings = warnings


def _is_value_cell(cell):
    node = cell.node
    cls = (node.get("CLASS") or "")
    if "xforms_input" in cls:
        return True
    for sp in node.find_all("SPAN"):
        if "xforms_input" in (sp.get("CLASS") or ""):
            return True
    if node.find_first("A") is not None:
        return True
    return False


def _render_links_in_place(node):
    """Convert <A href=...>text</A> to markdown [text](href) by mutating a
    text-bearing copy is overkill here; instead we special-case it inside
    text_content()-driven rendering by pre-collecting link cells' markdown
    at the point we render each table's cell text. Table rendering already
    calls Cell.text (collapse_ws(node.text_content())) which would show only
    the link's visible text and silently drop the href. To keep the href, we
    rewrite <A> nodes into a synthetic text node "[text](href)" before the
    grid is built for this table."""
    for a in node.find_all("A"):
        href = a.get("HREF") or a.get("href") or ""
        label = collapse_ws(a.text_content())
        markdown_link = f"[{label}]({href})" if href else label
        a.children = []
        a.tag = None
        a.text = markdown_link


def _strip_noprint(node):
    for sp in node.find_all("SPAN"):
        if "noprint" in (sp.get("CLASS") or ""):
            sp.children = []
            sp.tag = None
            sp.text = ""


CORRECTION_MARK = "정정신고(보고)"


def parse_html_file(file_path):
    warnings = []
    with open(file_path, "rb") as f:
        raw = f.read()
    root = parse_markup(raw)

    html_node = root.find_first("HTML")
    if html_node is None:
        warnings.append("no_html_root_element")
        html_node = root

    title_node = html_node.find_first("TITLE")
    title_text = collapse_ws(title_node.text_content()) if title_node is not None else ""
    if not title_text:
        warnings.append("missing_title")

    body_node = html_node.find_first("BODY")
    if body_node is None:
        warnings.append("missing_body")
        return ParsedDoc("", title_text, None, "", warnings)

    _strip_noprint(body_node)
    _render_links_in_place(body_node)

    body_text_all = body_node.text_content()
    is_correction = CORRECTION_MARK in body_text_all

    lines = []

    def is_title_div(n):
        return n.tag == "DIV" and "xforms_title" in (n.get("CLASS") or "")

    def walk(node):
        buf = []

        def flush():
            if not buf:
                return
            text = collapse_ws("".join(buf))
            buf.clear()
            if text:
                lines.append(text)

        for child in node.children:
            if child.tag is None:
                buf.append(child.text or "")
                continue
            if child.tag == "TABLE":
                flush()
                md = render_mixed_table(child, _is_value_cell)
                if md.strip():
                    lines.append("")
                    lines.append(md)
                    lines.append("")
                continue
            if is_title_div(child):
                flush()
                heading = collapse_ws(child.text_content())
                if heading:
                    lines.append("")
                    lines.append(f"## {heading}")
                    lines.append("")
                continue
            if child.tag in ("SCRIPT", "STYLE", "META", "STYLE-SHEET"):
                continue
            if child.tag in ("DIV",):
                # a plain (non-title) DIV is a block-level grouping wrapper -
                # recurse into it as its own block, don't fold its content
                # into this level's inline buffer
                flush()
                walk(child)
                continue
            # inline content (SPAN, bold correction-marker text, etc.) with
            # no wrapping block tag - accumulate, don't drop (this is what
            # was silently losing "정정신고(보고)" - a bare <span> at the top
            # of every correction filing, not inside a title DIV)
            buf.append(child.text_content())

        flush()

    walk(body_node)

    md = "\n".join(lines)
    out = []
    blank_run = 0
    for ln in md.split("\n"):
        if ln.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(ln)
    markdown = "\n".join(out).strip() + "\n"

    company_name = ""
    if title_text:
        company_name = title_text.split("/")[0].strip()
    if not company_name:
        warnings.append("missing_company_name")

    return ParsedDoc(company_name, title_text, is_correction, markdown, warnings)


def parse(manifest_doc):
    dir_path = os.path.join(CORPUS_DIR, manifest_doc["file_path"])
    if not os.path.isdir(dir_path):
        return []
    files = sorted(
        os.path.join(dir_path, fn) for fn in os.listdir(dir_path) if fn.lower().endswith(".xml")
    )
    results = []
    for fp in files:
        parsed = parse_html_file(fp)
        results.append(FileResult(None, parsed, fp))
    return results
