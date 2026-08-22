"""major (주요사항보고서, DSD-XML) parser front-end."""
import os
from dsd_group_parser import parse_dsd_file, resolve_files

DOC_GROUP = "major"


class FileResult:
    def __init__(self, attachment_suffix, parsed, file_path):
        self.attachment_suffix = attachment_suffix
        self.parsed = parsed
        self.file_path = file_path


def parse(manifest_doc):
    files = resolve_files(manifest_doc)
    results = []
    for fp in files:
        base = os.path.splitext(os.path.basename(fp))[0]
        rcept_no = manifest_doc["rcept_no"]
        suffix = None if base == rcept_no else base[len(rcept_no) + 1 :]
        parsed = parse_dsd_file(fp)
        results.append(FileResult(suffix, parsed, fp))
    return results
