"""
STEP 1 - XML Corpus Profiling

Pure structural profiling of the raw DART XML corpus. Does NOT normalize,
chunk, or assign semantic meaning to any tag. Only records observed facts:
tag inventory, parent/child structure, attributes, text-length distributions,
table structure, namespaces, CDATA, hyperlink/image/attachment-like tags, and
representative samples for later Parser/Normalization design.

Input:
  corpus/manifest.jsonl
  corpus/raw/**/*.xml

Output (results/01_profile/):
  corpus_summary.json
  tag_profile.csv
  attribute_profile.csv
  parent_child_profile.csv
  xpath_profile.csv
  table_profile.json
  tag_samples.jsonl
  parse_failures.jsonl
  profiling_report.md
"""

import csv
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

from lxml import etree

random.seed(20260821)  # fixed seed for reproducible reservoir sampling

from config import CORPUS_DIR, MANIFEST_PATH

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
OUT_DIR = os.path.join(ROOT_DIR, "results", "01_profile")

SAMPLE_CAP_PER_TAG = 10          # reservoir sample size per tag for tag_samples.jsonl
TEXT_LEN_RESERVOIR_CAP = 300     # reservoir sample size per tag for text-length percentile estimate
RAW_TEXT_SAMPLE_MAXLEN = 400     # truncate stored sample text to this many chars
ATTR_VALUE_TOP_K = 15            # top-K distinct attribute values kept per (tag, attr)

AMBIGUOUS_WATCH_TAGS = ["SPAN", "P", "DIV", "TITLE", "SECTION-1", "SECTION-2", "SECTION-3",
                         "TABLE", "TABLE-GROUP", "TBODY", "THEAD", "TR", "TD", "TU", "TE"]

HYPERLINK_IMAGE_KEYWORDS = ["LINK", "HREF", "IMAGE", "IMG", "GRAPHIC", "PICTURE",
                             "ATTACH", "FILE", "ANCHOR", "SRC", "URL", "MEDIA"]


# ---------------------------------------------------------------------------
# Reservoir sampling helper
# ---------------------------------------------------------------------------
class Reservoir:
    __slots__ = ("cap", "items", "seen")

    def __init__(self, cap):
        self.cap = cap
        self.items = []
        self.seen = 0

    def offer(self, item_factory):
        self.seen += 1
        if len(self.items) < self.cap:
            self.items.append(item_factory())
        else:
            j = random.randint(0, self.seen - 1)
            if j < self.cap:
                self.items[j] = item_factory()


class TagStat:
    def __init__(self):
        self.count = 0
        self.doc_ids = set()
        self.file_keys = set()
        self.parent_counter = Counter()
        self.child_counter = Counter()
        self.attr_name_counter = Counter()
        self.attr_value_counter = defaultdict(Counter)  # attr_name -> Counter(value)
        self.text_present = 0
        self.text_blank = 0     # text is not None but strip() == ''
        self.text_absent = 0    # text is None
        self.text_len_sum = 0
        self.text_len_min = None
        self.text_len_max = None
        self.text_len_reservoir = Reservoir(TEXT_LEN_RESERVOIR_CAP)
        self.tail_present = 0
        self.tail_blank = 0
        self.tail_absent = 0
        self.empty_element_count = 0  # no children AND blank/absent text
        self.has_attrs_count = 0
        self.samples = Reservoir(SAMPLE_CAP_PER_TAG)
        # role-diversity samples: keyed by (parent_tag,) -> one representative sample
        self.role_samples = {}

    def record_text_len(self, n):
        self.text_len_sum += n
        if self.text_len_min is None or n < self.text_len_min:
            self.text_len_min = n
        if self.text_len_max is None or n > self.text_len_max:
            self.text_len_max = n
        self.text_len_reservoir.offer(lambda: n)


def short(s, maxlen=RAW_TEXT_SAMPLE_MAXLEN):
    if s is None:
        return None
    s = s.strip()
    if len(s) > maxlen:
        return s[:maxlen] + f"...[truncated, total_len={len(s)}]"
    return s


def neighbor_desc(el):
    if el is None:
        return None
    tag = el.tag if isinstance(el.tag, str) else f"<!--{type(el).__name__}-->"
    txt = short(el.text, 120) if isinstance(el.tag, str) else None
    return {"tag": tag, "text": txt, "attrib": dict(el.attrib) if isinstance(el.tag, str) else None}


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def load_manifest(limit=None):
    docs = []
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            docs.append(json.loads(line))
            if limit is not None and len(docs) >= limit:
                break
    return docs


def resolve_xml_files(doc):
    dir_path = os.path.join(CORPUS_DIR, doc["file_path"])
    if not os.path.isdir(dir_path):
        return None, f"directory_not_found: {dir_path}"
    files = sorted(
        os.path.join(dir_path, fn) for fn in os.listdir(dir_path)
        if fn.lower().endswith(".xml")
    )
    return files, None


CDATA_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)[^>]*>\s*<!\[CDATA\[")
CDATA_COUNT_RE = re.compile(r"<!\[CDATA\[")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    docs = load_manifest()
    group_filter = os.environ.get("PROFILE_DOC_GROUP")
    if group_filter:
        docs = [d for d in docs if d.get("doc_group") == group_filter]
    limit_env = os.environ.get("PROFILE_DOC_LIMIT")
    if limit_env:
        docs = docs[: int(limit_env)]
    doc_id_filter = os.environ.get("PROFILE_DOC_ID")
    if doc_id_filter:
        docs = [d for d in docs if d.get("doc_id") == doc_id_filter]

    # ---- global aggregates ----
    tag_stats = defaultdict(TagStat)
    path_counter = Counter()            # structural tag-path (no indices) -> count
    root_tag_counter = Counter()        # per-file root tag
    doc_group_root_tag = Counter()      # (doc_group, root_tag)
    schema_location_counter = Counter()
    root_nsmap_counter = Counter()      # frozenset of nsmap items -> count
    nonroot_namespace_tags = Counter()  # tags below root that carry a namespace
    cdata_file_count = 0
    cdata_total_occurrences = 0
    cdata_tag_counter = Counter()
    rowspan_values = Counter()
    colspan_values = Counter()
    rowspan_tag_counter = Counter()     # tag that carries ROWSPAN
    colspan_tag_counter = Counter()
    table_in_table_count = 0            # TABLE nested inside another TABLE
    parse_status_counter = Counter()    # strict_ok / recovered / failed
    file_count_mismatch = []
    doc_root_tag_variety = defaultdict(set)  # doc_id -> set of root tags across its files

    parse_failures = []
    n_docs_processed = 0
    n_files_seen = 0
    n_elements_total = 0

    for doc in docs:
        doc_id = doc["doc_id"]
        files, err = resolve_xml_files(doc)
        if err:
            parse_failures.append({
                "doc_id": doc_id, "file": None, "error_type": "resolve_error",
                "message": err,
            })
            continue

        n_docs_processed += 1
        declared_n = doc.get("n_files")
        if declared_n is not None and len(files) != declared_n:
            file_count_mismatch.append({
                "doc_id": doc_id, "declared_n_files": declared_n,
                "actual_n_files": len(files), "file_path": doc["file_path"],
            })

        for fp in files:
            n_files_seen += 1
            rel_fp = os.path.relpath(fp, CORPUS_DIR).replace("\\", "/")

            with open(fp, "rb") as fh:
                raw_bytes = fh.read()

            # CDATA scan (byte-level, independent of parse outcome)
            cdata_hits = CDATA_COUNT_RE.findall(raw_bytes.decode("utf-8", errors="replace"))
            if cdata_hits:
                cdata_file_count += 1
                cdata_total_occurrences += len(cdata_hits)
                raw_text_for_cdata = raw_bytes.decode("utf-8", errors="replace")
                for m in CDATA_TAG_RE.finditer(raw_text_for_cdata):
                    cdata_tag_counter[m.group(1)] += 1

            # ---- parse ----
            status = None
            tree = None
            try:
                parser = etree.XMLParser(recover=False, huge_tree=True)
                tree = etree.fromstring(raw_bytes, parser=parser).getroottree()
                status = "strict_ok"
            except Exception as e1:
                parse_failures.append({
                    "doc_id": doc_id, "file": rel_fp, "error_type": "xml_syntax_error_strict",
                    "message": str(e1),
                })
                try:
                    parser = etree.XMLParser(recover=True, huge_tree=True)
                    root = etree.fromstring(raw_bytes, parser=parser)
                    if root is None:
                        raise ValueError("recover=True produced no root element")
                    tree = root.getroottree()
                    status = "recovered"
                except Exception as e2:
                    parse_failures.append({
                        "doc_id": doc_id, "file": rel_fp, "error_type": "xml_syntax_error_unrecoverable",
                        "message": str(e2),
                    })
                    status = "failed"

            parse_status_counter[status] += 1
            if status == "failed":
                continue

            root_el = tree.getroot()
            root_tag = root_el.tag if isinstance(root_el.tag, str) else "<non-element-root>"
            root_tag_counter[root_tag] += 1
            doc_group_root_tag[(doc.get("doc_group"), root_tag)] += 1
            doc_root_tag_variety[doc_id].add(root_tag)

            schema_loc = root_el.get("{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation")
            schema_location_counter[schema_loc] += 1

            if root_el.nsmap:
                root_nsmap_counter[tuple(sorted(root_el.nsmap.items()))] += 1

            # ---- walk the tree recursively, tracking structural path + table nesting depth ----
            def walk(el, parent_path, parent_table_depth):
                nonlocal n_elements_total, table_in_table_count
                if not isinstance(el.tag, str):
                    return  # skip comments / PIs
                tag = el.tag
                n_elements_total += 1
                st = tag_stats[tag]
                st.count += 1
                st.doc_ids.add(doc_id)
                st.file_keys.add(rel_fp)

                parent_el = el.getparent()
                parent_tag = parent_el.tag if (parent_el is not None and isinstance(parent_el.tag, str)) else "<ROOT>"
                st.parent_counter[parent_tag] += 1

                children = [c for c in el if isinstance(c.tag, str)]
                for c in children:
                    st.child_counter[c.tag] += 1

                if el.attrib:
                    st.has_attrs_count += 1
                for k, v in el.attrib.items():
                    st.attr_name_counter[k] += 1
                    vc = st.attr_value_counter[k]
                    if len(vc) < ATTR_VALUE_TOP_K * 20:
                        vc[v] += 1
                    if k.upper() == "ROWSPAN":
                        rowspan_values[v] += 1
                        rowspan_tag_counter[tag] += 1
                    if k.upper() == "COLSPAN":
                        colspan_values[v] += 1
                        colspan_tag_counter[tag] += 1

                if el.nsmap and el is not root_el:
                    # only count if this element itself declares/uses a namespace beyond root's
                    nonroot_namespace_tags[tag] += 1

                text = el.text
                if text is None:
                    st.text_absent += 1
                elif text.strip() == "":
                    st.text_blank += 1
                    st.record_text_len(len(text))
                else:
                    st.text_present += 1
                    st.record_text_len(len(text))

                tail = el.tail
                if tail is None:
                    st.tail_absent += 1
                elif tail.strip() == "":
                    st.tail_blank += 1
                else:
                    st.tail_present += 1

                is_empty = (len(children) == 0) and (text is None or text.strip() == "")
                if is_empty:
                    st.empty_element_count += 1

                cur_table_depth = parent_table_depth + (1 if tag == "TABLE" else 0)
                if tag == "TABLE" and parent_table_depth > 0:
                    table_in_table_count += 1

                cur_path = parent_path + "/" + tag
                path_counter[cur_path] += 1

                prev_el = el.getprevious()
                next_el = el.getnext()

                def make_sample():
                    try:
                        xpath = tree.getpath(el)
                    except Exception:
                        xpath = cur_path
                    return {
                        "doc_id": doc_id,
                        "xml_file": rel_fp,
                        "xpath": xpath,
                        "structural_path": cur_path,
                        "tag": tag,
                        "parent": parent_tag,
                        "attributes": dict(el.attrib),
                        "raw_text": short(text),
                        "prev_element": neighbor_desc(prev_el),
                        "next_element": neighbor_desc(next_el),
                    }

                st.samples.offer(make_sample)

                if tag in AMBIGUOUS_WATCH_TAGS:
                    role_key = (parent_tag, tuple(sorted(el.attrib.keys())))
                    if role_key not in st.role_samples:
                        st.role_samples[role_key] = make_sample()

                for c in children:
                    walk(c, cur_path, cur_table_depth)

            sys.setrecursionlimit(10000)
            walk(root_el, "", 0)

        if n_docs_processed % 200 == 0:
            print(f"[progress] docs processed={n_docs_processed} files_seen={n_files_seen} elements={n_elements_total}", file=sys.stderr)

    # =========================================================================
    # Write outputs
    # =========================================================================

    # ---- tag_profile.csv ----
    with open(os.path.join(OUT_DIR, "tag_profile.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "tag", "count", "doc_count", "file_count", "distinct_parent_tags", "distinct_child_tags",
            "distinct_attr_names", "has_attrs_count",
            "text_present", "text_blank", "text_absent",
            "text_len_min", "text_len_max", "text_len_mean",
            "text_len_p50", "text_len_p90", "text_len_p99",
            "tail_present", "tail_blank", "tail_absent",
            "empty_element_count",
        ])
        for tag, st in sorted(tag_stats.items(), key=lambda kv: -kv[1].count):
            vals = sorted(st.text_len_reservoir.items)
            mean = (st.text_len_sum / (st.text_present + st.text_blank)) if (st.text_present + st.text_blank) > 0 else 0
            w.writerow([
                tag, st.count, len(st.doc_ids), len(st.file_keys),
                len(st.parent_counter), len(st.child_counter), len(st.attr_name_counter),
                st.has_attrs_count,
                st.text_present, st.text_blank, st.text_absent,
                st.text_len_min if st.text_len_min is not None else "",
                st.text_len_max if st.text_len_max is not None else "",
                round(mean, 2),
                round(percentile(vals, 0.5), 1) if vals else "",
                round(percentile(vals, 0.9), 1) if vals else "",
                round(percentile(vals, 0.99), 1) if vals else "",
                st.tail_present, st.tail_blank, st.tail_absent,
                st.empty_element_count,
            ])

    # ---- attribute_profile.csv ----
    with open(os.path.join(OUT_DIR, "attribute_profile.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tag", "attribute", "count", "distinct_values", "top_values"])
        for tag, st in sorted(tag_stats.items(), key=lambda kv: -kv[1].count):
            for attr, cnt in sorted(st.attr_name_counter.items(), key=lambda kv: -kv[1]):
                vc = st.attr_value_counter[attr]
                top_vals = "; ".join(f"{v!r}:{c}" for v, c in vc.most_common(ATTR_VALUE_TOP_K))
                w.writerow([tag, attr, cnt, len(vc), top_vals])

    # ---- parent_child_profile.csv ----
    with open(os.path.join(OUT_DIR, "parent_child_profile.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tag", "relation", "other_tag", "count"])
        for tag, st in sorted(tag_stats.items(), key=lambda kv: -kv[1].count):
            for parent_tag, cnt in st.parent_counter.most_common():
                w.writerow([tag, "parent", parent_tag, cnt])
            for child_tag, cnt in st.child_counter.most_common():
                w.writerow([tag, "child", child_tag, cnt])

    # ---- xpath_profile.csv ----
    with open(os.path.join(OUT_DIR, "xpath_profile.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["structural_path", "count"])
        for path, cnt in path_counter.most_common():
            w.writerow([path, cnt])

    # ---- table_profile.json ----
    table_related_tags = ["TABLE", "TABLE-GROUP", "TBODY", "THEAD", "TR", "TD", "TU", "TE",
                           "COL", "COLGROUP", "table", "tbody", "thead", "tr", "td"]
    table_tag_counts = {t: tag_stats[t].count for t in table_related_tags if t in tag_stats}
    table_profile = {
        "table_related_tag_counts": table_tag_counts,
        "table_in_table_nesting_count": table_in_table_count,
        "rowspan": {
            "tags_carrying_rowspan": dict(rowspan_tag_counter),
            "value_distribution": dict(rowspan_values.most_common(50)),
        },
        "colspan": {
            "tags_carrying_colspan": dict(colspan_tag_counter),
            "value_distribution": dict(colspan_values.most_common(50)),
        },
        "td_vs_tu_note": {
            "TD_count": tag_stats["TD"].count if "TD" in tag_stats else 0,
            "TU_count": tag_stats["TU"].count if "TU" in tag_stats else 0,
            "TE_count": tag_stats["TE"].count if "TE" in tag_stats else 0,
            "THEAD_count": tag_stats["THEAD"].count if "THEAD" in tag_stats else 0,
        },
    }
    with open(os.path.join(OUT_DIR, "table_profile.json"), "w", encoding="utf-8") as f:
        json.dump(table_profile, f, ensure_ascii=False, indent=2)

    # ---- tag_samples.jsonl ----
    with open(os.path.join(OUT_DIR, "tag_samples.jsonl"), "w", encoding="utf-8") as f:
        for tag, st in sorted(tag_stats.items(), key=lambda kv: -kv[1].count):
            for s in st.samples.items:
                rec = dict(s)
                rec["sample_kind"] = "reservoir"
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if tag in AMBIGUOUS_WATCH_TAGS:
                for s in st.role_samples.values():
                    rec = dict(s)
                    rec["sample_kind"] = "role_variant"
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- parse_failures.jsonl ----
    with open(os.path.join(OUT_DIR, "parse_failures.jsonl"), "w", encoding="utf-8") as f:
        for pf in parse_failures:
            f.write(json.dumps(pf, ensure_ascii=False) + "\n")
        for m in file_count_mismatch:
            rec = dict(m)
            rec["error_type"] = "file_count_mismatch"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- hyperlink/image/attachment candidate tags (derived from full tag inventory) ----
    hyperlink_image_tags = {}
    for tag, st in tag_stats.items():
        tag_upper = tag.upper()
        if any(kw in tag_upper for kw in HYPERLINK_IMAGE_KEYWORDS):
            hyperlink_image_tags[tag] = st.count
    hyperlink_image_attrs = {}
    for tag, st in tag_stats.items():
        for attr in st.attr_name_counter:
            if any(kw in attr.upper() for kw in HYPERLINK_IMAGE_KEYWORDS):
                hyperlink_image_attrs[f"{tag}@{attr}"] = st.attr_name_counter[attr]

    # ---- corpus_summary.json ----
    corpus_summary = {
        "manifest_doc_count": len(docs),
        "docs_resolved": n_docs_processed,
        "docs_unresolved": len(docs) - n_docs_processed,
        "xml_files_seen": n_files_seen,
        "elements_total": n_elements_total,
        "distinct_tags": len(tag_stats),
        "parse_status_counts": dict(parse_status_counter),
        "parse_failure_records": len(parse_failures),
        "file_count_mismatch_records": len(file_count_mismatch),
        "root_tag_counts": dict(root_tag_counter.most_common()),
        "doc_group_x_root_tag": {f"{g}|{r}": c for (g, r), c in doc_group_root_tag.most_common()},
        "schema_location_counts": {str(k): v for k, v in schema_location_counter.most_common()},
        "root_namespace_maps": {str(dict(k)): v for k, v in root_nsmap_counter.most_common()},
        "nonroot_elements_with_namespace_by_tag": dict(nonroot_namespace_tags.most_common()),
        "cdata_file_count": cdata_file_count,
        "cdata_total_occurrences": cdata_total_occurrences,
        "cdata_preceding_tag_counts": dict(cdata_tag_counter.most_common()),
        "hyperlink_image_attachment_candidate_tags": hyperlink_image_tags,
        "hyperlink_image_attachment_candidate_attrs": hyperlink_image_attrs,
        "docs_with_multiple_root_tag_variants": {
            d: sorted(v) for d, v in doc_root_tag_variety.items() if len(v) > 1
        },
    }
    with open(os.path.join(OUT_DIR, "corpus_summary.json"), "w", encoding="utf-8") as f:
        json.dump(corpus_summary, f, ensure_ascii=False, indent=2)

    # ---- profiling_report.md ----
    write_report(
        corpus_summary, tag_stats, path_counter, table_profile,
        parse_failures, file_count_mismatch, hyperlink_image_tags, hyperlink_image_attrs,
    )

    print("DONE")
    print(json.dumps({
        "docs_resolved": n_docs_processed,
        "xml_files_seen": n_files_seen,
        "elements_total": n_elements_total,
        "distinct_tags": len(tag_stats),
        "parse_status_counts": dict(parse_status_counter),
        "parse_failure_records": len(parse_failures),
        "file_count_mismatch_records": len(file_count_mismatch),
    }, ensure_ascii=False, indent=2))


def write_report(summary, tag_stats, path_counter, table_profile,
                  parse_failures, file_count_mismatch, hyperlink_image_tags, hyperlink_image_attrs):
    lines = []
    a = lines.append
    a("# STEP 1 - XML Corpus Profiling Report\n")
    a("이 문서는 corpus/raw 하위 전체 XML 파일을 구조적으로 전수 조사한 관찰 결과다. ")
    a("의미 판단(예: SPAN -> heading)은 포함하지 않는다.\n")

    a("## 1. 전체 규모\n")
    a(f"- manifest.jsonl 문서 수: {summary['manifest_doc_count']}")
    a(f"- 디렉터리 resolve 성공 문서 수: {summary['docs_resolved']}")
    a(f"- 디렉터리 resolve 실패 문서 수: {summary['docs_unresolved']}")
    a(f"- 실제 XML 파일 수: {summary['xml_files_seen']}")
    a(f"- 전체 element 수: {summary['elements_total']}")
    a(f"- distinct tag 수: {summary['distinct_tags']}")
    a(f"- parse 상태별 파일 수: {summary['parse_status_counts']}")
    a(f"- parse_failures.jsonl 레코드 수: {summary['parse_failure_records']}")
    a(f"- manifest n_files와 실제 파일 수 불일치 건수: {summary['file_count_mismatch_records']}\n")

    a("## 2. Root Tag 분포\n")
    a("파일의 최상위 root tag 종류와 빈도:\n")
    for tag, cnt in summary["root_tag_counts"].items():
        a(f"- `{tag}`: {cnt}")
    a("")
    a("**중요 관찰**: root tag가 `DOCUMENT`가 아닌 `html`(소문자)로 시작하는 파일이 존재한다. ")
    a("이는 DART 표준 DOCUMENT/BODY XML 스키마가 아니라, 순수 HTML 문서가 `.xml` 확장자로 저장된 것으로 보인다. ")
    a("doc_group별 분포는 다음과 같다 (`doc_group|root_tag: count`):\n")
    for k, v in summary["doc_group_x_root_tag"].items():
        a(f"- {k}: {v}")
    a("")

    a("## 3. Schema Location (noNamespaceSchemaLocation)\n")
    for k, v in summary["schema_location_counts"].items():
        a(f"- `{k}`: {v}")
    a("")

    a("## 4. Namespace\n")
    a(f"- root nsmap 종류: {summary['root_namespace_maps']}")
    a(f"- root 외 element에서 namespace가 관측된 tag: {summary['nonroot_elements_with_namespace_by_tag']}\n")

    a("## 5. CDATA\n")
    a(f"- CDATA를 포함한 파일 수: {summary['cdata_file_count']}")
    a(f"- CDATA 총 등장 횟수: {summary['cdata_total_occurrences']}")
    a(f"- CDATA 직전 tag 분포 (heuristic, regex 기반): {summary['cdata_preceding_tag_counts']}\n")

    a("## 6. Hyperlink / Image / Attachment 관련 후보 tag\n")
    a("tag 이름에 LINK/HREF/IMAGE/IMG/GRAPHIC/PICTURE/ATTACH/FILE/ANCHOR/SRC/URL/MEDIA 키워드가 포함된 것을 기계적으로 추출한 결과 (역할 판단 아님):\n")
    for tag, cnt in sorted(hyperlink_image_tags.items(), key=lambda kv: -kv[1]):
        a(f"- `{tag}`: {cnt}")
    a("\n관련 attribute:\n")
    for k, v in sorted(hyperlink_image_attrs.items(), key=lambda kv: -kv[1]):
        a(f"- `{k}`: {v}")
    a("")

    a("## 7. Table 관련 구조\n")
    a(f"- table 관련 tag 등장 횟수: {table_profile['table_related_tag_counts']}")
    a(f"- TABLE 안에 TABLE이 중첩된 경우: {table_profile['table_in_table_nesting_count']}건")
    a(f"- ROWSPAN을 사용하는 tag: {table_profile['rowspan']['tags_carrying_rowspan']}")
    a(f"- ROWSPAN 값 분포(상위): {table_profile['rowspan']['value_distribution']}")
    a(f"- COLSPAN을 사용하는 tag: {table_profile['colspan']['tags_carrying_colspan']}")
    a(f"- COLSPAN 값 분포(상위): {table_profile['colspan']['value_distribution']}")
    a(f"- TD/TU/TE/THEAD 등장 횟수: {table_profile['td_vs_tu_note']}\n")
    a("**관찰**: DART 표 구조는 HTML과 달리 `TD`(일반 셀)와 `TU`(단위/값 속성을 가진 셀, AUNIT/AUNITVALUE 속성 보유)를 구분해서 사용한다. ")
    a("이 구분이 실제로 일관되는지는 attribute_profile.csv의 TU 항목에서 AUNIT 계열 속성 존재 여부로 추가 확인 필요.\n")

    a("## 8. 상위 빈출 Tag (count 기준 상위 30개)\n")
    a("| tag | count | doc_count | text_present | text_blank | text_absent | empty_element |")
    a("|---|---|---|---|---|---|---|")
    for tag, st in sorted(tag_stats.items(), key=lambda kv: -kv[1].count)[:30]:
        a(f"| `{tag}` | {st.count} | {len(st.doc_ids)} | {st.text_present} | {st.text_blank} | {st.text_absent} | {st.empty_element_count} |")
    a("")

    a("## 9. 동일 Tag의 다중 역할 사용 여부 (SPAN/P/DIV/TITLE/SECTION/TABLE 계열)\n")
    a("각 tag가 서로 다른 parent tag 아래에서 등장하는 양상을 관찰한 결과 (parent 종류가 많을수록 다양한 문맥에서 재사용됨을 시사):\n")
    for watch_tag in AMBIGUOUS_WATCH_TAGS:
        st = tag_stats.get(watch_tag)
        if st is None:
            a(f"- `{watch_tag}`: 코퍼스에 존재하지 않음")
            continue
        top_parents = st.parent_counter.most_common(10)
        a(f"- `{watch_tag}` (총 {st.count}회, distinct parent {len(st.parent_counter)}종): "
          f"상위 parent = {top_parents}")
    a("\n각 tag의 role_samples(동일 tag가 서로 다른 parent+attribute 조합으로 쓰인 대표 예시)는 tag_samples.jsonl의 "
      "`sample_kind: role_variant` 레코드를 참고.\n")

    a("## 10. Parse 실패/이상 사례 요약\n")
    err_types = Counter(pf.get("error_type") for pf in parse_failures)
    for k, v in err_types.most_common():
        a(f"- {k}: {v}건")
    if parse_failures:
        a("\n대표 사례:")
        for pf in parse_failures[:10]:
            a(f"- [{pf.get('error_type')}] doc={pf.get('doc_id')} file={pf.get('file')} msg={pf.get('message')}")
    a("")

    a("## 11. manifest n_files 불일치\n")
    if file_count_mismatch:
        for m in file_count_mismatch[:20]:
            a(f"- doc={m['doc_id']} declared={m['declared_n_files']} actual={m['actual_n_files']} path={m['file_path']}")
        if len(file_count_mismatch) > 20:
            a(f"... 외 {len(file_count_mismatch) - 20}건 (parse_failures.jsonl 참고)")
    else:
        a("불일치 없음")
    a("")

    a("## 12. 구조적으로 판단이 애매한 항목 (다음 단계 검토 필요, 의미 부여는 하지 않음)\n")
    a("- root tag가 `html`인 파일(주로 exchange 문서에서 관측)은 DART DOCUMENT 스키마 파서로 처리할 수 없으므로, "
      "Parser 설계 시 문서 포맷을 두 갈래(DART-XML vs HTML)로 분기해야 함.")
    a("- 일부 대용량 정기공시 XML(예: KB금융, 삼성생명, 하나금융지주 2025년말 사업보고서)은 strict XML parser로 "
      "파싱이 실패함. 원인은 본문 텍스트 내 이스케이프되지 않은 `&`(예: `MD&A`) 및 `<...>`형 주석 표기(예: `<정정 전>`)로 관측됨. "
      "parse_failures.jsonl에 상세 위치 기록.")
    a("- TE 태그, TU 태그의 정확한 역할은 attribute_profile.csv / tag_samples.jsonl 확인 필요 (본 단계에서는 관찰만).")
    a("- CDATA 직전 tag heuristic은 regex 기반 근사치이며, 실제 파서가 CDATA를 병합 처리하므로 완전한 목록이 아닐 수 있음.")
    a("")

    a("## 13. 산출물 목록\n")
    a("- corpus_summary.json, tag_profile.csv, attribute_profile.csv, parent_child_profile.csv, "
      "xpath_profile.csv, table_profile.json, tag_samples.jsonl, parse_failures.jsonl, profiling_report.md")

    with open(os.path.join(OUT_DIR, "profiling_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
