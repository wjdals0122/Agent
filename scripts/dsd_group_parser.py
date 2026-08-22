"""
Shared parsing logic for the three DSD-XML document groups (major/holding/
periodic). They differ only in: which manifest doc_group they read, and
(periodic only) handling multiple XML files per receipt number and the 3
pdf+html-only documents. Everything else - lenient parsing, table/label-value
rendering, CORRECTION detection - is identical, so it lives here once and
major_parser.py / holding_parser.py / periodic_parser.py are thin wrappers.
"""
import json
import os

from common_parse import parse_markup, collapse_ws
from dsd_walker import render_document_body
from config import CORPUS_DIR, MANIFEST_PATH


class ParsedDoc:
    def __init__(self, company_name, document_name, is_correction_tag, markdown, warnings):
        self.company_name = company_name
        self.document_name = document_name
        self.is_correction_tag = is_correction_tag
        self.markdown = markdown
        self.warnings = warnings


def parse_dsd_file(file_path):
    """Parse one DSD-XML file (a single receipt's main doc OR one of its
    attachments) into markdown + extracted identifying fields.
    Never raises for malformed input - the lenient tokenizer degrades
    gracefully; genuinely unreadable files (I/O errors) propagate."""
    warnings = []
    with open(file_path, "rb") as f:
        raw = f.read()
    root = parse_markup(raw)
    doc = root.find_first("DOCUMENT")
    if doc is None:
        warnings.append("no_DOCUMENT_root_element")
        doc = root

    company_node = doc.find_first("COMPANY-NAME")
    company_name = collapse_ws(company_node.text_content()) if company_node is not None else ""
    if not company_name:
        warnings.append("missing_company_name")

    docname_node = doc.find_first("DOCUMENT-NAME")
    document_name = collapse_ws(docname_node.text_content()) if docname_node is not None else ""

    is_correction_tag = doc.find_first("CORRECTION") is not None

    body = doc.find_first("BODY")
    if body is None:
        warnings.append("missing_BODY_element")
        markdown = ""
    else:
        markdown = render_document_body(body)

    return ParsedDoc(company_name, document_name, is_correction_tag, markdown, warnings)


def resolve_files(manifest_doc):
    dir_path = os.path.join(CORPUS_DIR, manifest_doc["file_path"])
    if not os.path.isdir(dir_path):
        return []
    return sorted(
        os.path.join(dir_path, fn) for fn in os.listdir(dir_path) if fn.lower().endswith(".xml")
    )


def load_manifest(doc_group):
    docs = []
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("doc_group") == doc_group:
                docs.append(d)
    return docs
