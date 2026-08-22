




















































































































"""
Document-level markdown rendering for DSD-XML documents (major/holding/
periodic). Walks BODY/SECTION-N/LIBRARY/TABLE(-GROUP)/P/TITLE/IMAGE in
document order and emits markdown, delegating table rendering to lv_render.

TITLE-loss bugfix (from the exceptions spec): a TITLE was previously only
recognized as a real heading directly under SECTION-N/COVER/CORRECTION and
silently skipped anywhere else (assumed "already handled"). Here every TITLE
is emitted where it's encountered in document order - there is no separate
"is this consumed" bookkeeping to get wrong.
"""
from common_parse import collapse_ws
from lv_render import render_table

DSD_VALUE_TAGS = ("TE", "TU")


def is_value_cell(cell):
    return cell.tag in DSD_VALUE_TAGS


SECTION_TAGS = {"SECTION-1", "SECTION-2", "SECTION-3", "SECTION-4"}
SKIP_TAGS = {"EXTRACTION", "SUMMARY", "FORMULA-VERSION", "STYLE", "SCRIPT"}
# these are rendered specially, not via generic recursion
SPECIAL_TAGS = {"TABLE", "TITLE", "IMAGE", "P", "PGBRK"} | SECTION_TAGS


def render_document_body(body_node):
    lines = []
    _walk(body_node, depth=0, lines=lines, in_table=False)
    # collapse 3+ consecutive blank lines
    out = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(ln)
    return "\n".join(out).strip() + "\n"


def _heading_level(depth):
    return "#" * min(2 + depth, 6)


def _walk(node, depth, lines, in_table):
    for child in node.children:
        if child.tag is None:
            continue  # bare text between block elements carries no structure here
        tag = child.tag

        if tag in SECTION_TAGS:
            lines.append("")
            lines.append("")
            _walk(child, depth + 1, lines, in_table)
            continue

        if tag == "TITLE":
            title_text = collapse_ws(child.text_content())
            if title_text:
                lines.append("")
                lines.append(f"{_heading_level(depth)} {title_text}")
                lines.append("")
            continue

        if tag == "TABLE":
            md = render_table(child, is_value_cell)
            if md.strip():
                lines.append("")
                lines.append(md)
                lines.append("")
            continue

        if tag == "TABLE-GROUP" or tag == "LIBRARY":
            _walk(child, depth, lines, in_table)
            continue

        if tag == "IMAGE":
            cap = child.find_first("IMG-CAPTION")
            cap_text = collapse_ws(cap.text_content()) if cap is not None else ""
            if cap_text:
                lines.append(f"*[이미지: {cap_text}]*")
            continue

        if tag == "PGBRK":
            continue

        if tag in SKIP_TAGS:
            continue

        if tag == "P":
            text = collapse_ws(child.text_content())
            if text:
                lines.append(text)
            continue

        if tag == "CORRECTION":
            _walk(child, depth, lines, in_table)
            continue

        # any other container tag (COVER, BODY nesting, unknown structural
        # wrapper): recurse so nothing nested inside it is silently dropped
        _walk(child, depth, lines, in_table)
