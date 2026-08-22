"""
CompanyAliasRegistry: scans corpus/raw ONCE, lightly (company-name tags only,
no table/paragraph parsing), across ALL FOUR doc groups, and builds a
corp_code -> set-of-name-variants map. Without this, a company's exchange
filings (named via <title>, KRX's own often-abbreviated display name) and its
periodic/major/holding filings (named via <COMPANY-NAME>, the registered
legal name, occasionally an older pre-rename name) would carry different
alias sets depending on which single document happened to be read - so a
search for one legitimate name of a company would silently miss its filings
in other doc groups. This registry unifies aliases per company across the
whole corpus before any document is rendered.
"""
import json
import os
import re

from common_parse import collapse_ws
from config import CORPUS_DIR as CORPUS_DIR_DEFAULT, MANIFEST_PATH as MANIFEST_PATH_DEFAULT

# Lightweight regex extraction instead of a full lenient-tree parse: this
# registry only ever needs ONE short tag's text per file, and it runs over
# every file in the corpus, so re-parsing the entire tree here would roughly
# double total pipeline runtime for no benefit. <COMPANY-NAME>/<title> always
# appear once, near the top of the file, well before any of the malformed-
# markup content deep in <BODY> that the lenient tree builder exists for, so
# a direct regex is exact for this purpose - it only breaks down on the same
# few pathological patterns (stray '<' in text) the full parser handles, and
# those cannot occur inside DART's own machine-generated tag names.
_COMPANY_NAME_RE = re.compile(r"<COMPANY-NAME\b[^>]*>(.*?)</COMPANY-NAME>", re.S | re.I)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]*>")


def _strip_tags(s):
    return collapse_ws(_TAG_RE.sub("", s))


def _extract_names_dsd(raw_bytes):
    text = raw_bytes.decode("utf-8", errors="replace")
    m = _COMPANY_NAME_RE.search(text)
    if not m:
        return []
    name = _strip_tags(m.group(1))
    return [name] if name else []


def _extract_names_html(raw_bytes):
    text = raw_bytes.decode("utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    if not m:
        return []
    title = _strip_tags(m.group(1))
    if not title:
        return []
    return [title.split("/")[0].strip()]


class CompanyAliasRegistry:
    def __init__(self):
        self.aliases_by_corp_code = {}  # corp_code -> set[str]
        self.stats = {"docs_scanned": 0, "files_scanned": 0, "read_failures": 0}

    def add(self, corp_code, name):
        if not corp_code or not name:
            return
        self.aliases_by_corp_code.setdefault(corp_code, set()).add(name)

    def build(self, raw_root=CORPUS_DIR_DEFAULT, manifest_path=None):
        if manifest_path is None:
            manifest_path = (
                MANIFEST_PATH_DEFAULT if raw_root == CORPUS_DIR_DEFAULT
                else os.path.join(raw_root, "manifest.jsonl")
            )
        docs = []
        with open(manifest_path, encoding="utf-8") as f:
            for line in f:
                docs.append(json.loads(line))

        for d in docs:
            corp_code = d.get("corp_code")
            # the manifest's own display fields are themselves real
            # historical aliases (corp_name/listed_name can differ across a
            # rename) - free to collect, no parsing needed. flr_nm is
            # deliberately EXCLUDED: for holding (5% ownership) filings it is
            # structurally the FILING SHAREHOLDER, not the target company
            # this corp_code identifies (verified: 1083/1083 holding docs
            # have flr_nm != corp_name, e.g. filer "삼성물산" reporting on
            # target "삼성전자") - adding it here would fabricate a false
            # alias linking two different real companies. It differs from
            # corp_name in a small number of major/periodic/exchange docs
            # too (3, 13, 1 respectively) where the cause isn't verified
            # per-doc, so it is left out everywhere rather than guessed at.
            self.add(corp_code, d.get("corp_name"))
            self.add(corp_code, d.get("listed_name"))

            if d.get("file_format") != "xml":
                continue
            dir_path = os.path.join(raw_root, d["file_path"])
            if not os.path.isdir(dir_path):
                continue
            self.stats["docs_scanned"] += 1
            for fn in os.listdir(dir_path):
                if not fn.lower().endswith(".xml"):
                    continue
                fp = os.path.join(dir_path, fn)
                self.stats["files_scanned"] += 1
                try:
                    with open(fp, "rb") as f:
                        raw = f.read()
                except OSError:
                    self.stats["read_failures"] += 1
                    continue
                if d["doc_group"] == "exchange":
                    names = _extract_names_html(raw)
                else:
                    names = _extract_names_dsd(raw)
                for n in names:
                    self.add(corp_code, n)
        return self

    def get_aliases(self, corp_code, exclude=None):
        names = self.aliases_by_corp_code.get(corp_code, set())
        if exclude:
            names = names - {exclude}
        return sorted(names)

    def save(self, path):
        serializable = {k: sorted(v) for k, v in self.aliases_by_corp_code.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        reg = cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        reg.aliases_by_corp_code = {k: set(v) for k, v in data.items()}
        return reg
