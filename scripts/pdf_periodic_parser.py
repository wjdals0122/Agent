"""
Fallback parser for the 3 periodic documents that have no XML source at all
(manifest file_format == "pdf+html") - only a PDF and a "_viewer.html" exist
(see STEP 1 profiling, corpus_summary.json -> file_count_mismatch_reclassified).

The viewer HTML is DART's own web-viewer page markup (navigation chrome,
script-rendered page images), not the disclosure content itself, so it is not
a usable source for text extraction. The PDF is the only real source of
content for these 3 documents. Per the "don't fabricate" principle, this
module does not attempt OCR/text-layout reconstruction of the PDF (out of
scope for this pass) - it records a clearly-marked placeholder document, so
the document is still traceable to its raw source (doc_id / rcept_no /
file_path) and its absence of parsed content is an explicit, logged fact
rather than a silent gap.
"""
import os

from config import CORPUS_DIR

DOC_GROUP = "periodic"


class FileResult:
    def __init__(self, attachment_suffix, parsed, file_path):
        self.attachment_suffix = attachment_suffix
        self.parsed = parsed
        self.file_path = file_path


class PlaceholderParsed:
    def __init__(self, company_name, document_name, pdf_path):
        self.company_name = company_name
        self.document_name = document_name
        self.is_correction_tag = None  # unknown - not derivable without parsing the PDF
        self.markdown = (
            f"# {document_name}\n\n"
            f"> 이 문서는 원본이 XML로 제공되지 않고 PDF+viewer HTML로만 제공됩니다 "
            f"(manifest file_format=\"pdf+html\"). 이번 파싱 단계에서는 PDF 본문 추출을 "
            f"수행하지 않았으므로 본문 내용이 없습니다. 원본 PDF: "
            f"`{os.path.relpath(pdf_path, CORPUS_DIR).replace(os.sep, '/')}`\n"
        )
        self.warnings = ["pdf_only_no_text_extraction"]


def parse(manifest_doc):
    dir_path = os.path.join(CORPUS_DIR, manifest_doc["file_path"])
    pdf_path = os.path.join(dir_path, manifest_doc["rcept_no"] + ".pdf")
    parsed = PlaceholderParsed(manifest_doc.get("corp_name", ""), manifest_doc.get("report_nm", ""), pdf_path)
    return [FileResult(None, parsed, pdf_path)]
