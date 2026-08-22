"""periodic (사업/분기/반기보고서 + 감사보고서, DSD-XML/PDF) parser front-end.

One receipt number can hold up to 3 source files: the main report plus a
standalone audit report (attachment "00760") and a consolidated audit report
("00761"). Each becomes its own FileResult / markdown output - rag_pipeline.py
is responsible for naming them {회사명}_periodic_{문서ID}[_{첨부번호}].md.

The 3 documents with no XML source (file_format == "pdf+html") are delegated
to pdf_periodic_parser.py.
"""
import major_parser  # identical per-file parsing logic; reused, not duplicated
import pdf_periodic_parser

DOC_GROUP = "periodic"
FileResult = major_parser.FileResult


def parse(manifest_doc):
    if manifest_doc.get("file_format") != "xml":
        return pdf_periodic_parser.parse(manifest_doc)
    return major_parser.parse(manifest_doc)
