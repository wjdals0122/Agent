"""
STEP 1 (finalize) - derives two purely mechanical, reproducible facts from the
already-generated 01_profile outputs, and appends them to corpus_summary.json /
profiling_report.md. Does NOT re-parse the corpus and does NOT assign meaning
to any tag - it only flags tag names that are mechanically distinguishable
(non-ASCII characters, or extremely low doc_count) as "needs confirmation in
a later step", per the "leave ambiguous as ambiguous" rule for this step.
"""
import csv
import json
import os

from config import MANIFEST_PATH

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT_DIR, "results", "01_profile")


def is_ascii(s):
    return all(ord(c) < 128 for c in s)


def main():
    tag_rows = list(csv.DictReader(open(os.path.join(OUT_DIR, "tag_profile.csv"), encoding="utf-8-sig")))

    # BUGFIX for corpus_summary.json's "nonroot_elements_with_namespace_by_tag":
    # el.nsmap in lxml returns the namespace map INHERITED from ancestors, not
    # just namespaces newly declared on that element. Since the root declares
    # xmlns:xsi, virtually every descendant inherits a non-empty nsmap, so the
    # original counter over-counted almost the entire tag inventory. The
    # actually-meaningful signal - an element name genuinely qualified by a
    # namespace - is already unambiguously visible as Clark-notation tag names
    # (e.g. "{http://www.w3.org/1999/xhtml}a") in tag_profile.csv. Recompute
    # from that instead of re-parsing the corpus.
    truly_namespaced_tags = {
        r["tag"]: {"count": int(r["count"]), "doc_count": int(r["doc_count"]), "file_count": int(r["file_count"])}
        for r in tag_rows if r["tag"].startswith("{")
    }
    xpath_rows = list(csv.reader(open(os.path.join(OUT_DIR, "xpath_profile.csv"), encoding="utf-8-sig")))
    xpath_data = xpath_rows[1:]  # skip header

    # cross-check file_count_mismatch records against manifest file_format:
    # docs declared "pdf+html" were never expected to have .xml files, so a
    # "0 xml files found" result for them is not a real mismatch/failure.
    manifest_format = {}
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            manifest_format[d["doc_id"]] = d.get("file_format")

    failures_path = os.path.join(OUT_DIR, "parse_failures.jsonl")
    with open(failures_path, encoding="utf-8") as f:
        failure_recs = [json.loads(l) for l in f]

    real_mismatches = []
    non_xml_format_docs = []
    for r in failure_recs:
        if r.get("error_type") != "file_count_mismatch":
            continue
        fmt = manifest_format.get(r["doc_id"])
        if fmt != "xml":
            non_xml_format_docs.append({**r, "declared_file_format": fmt})
        else:
            real_mismatches.append(r)

    non_ascii_rows = [r for r in tag_rows if not is_ascii(r["tag"])]
    ascii_rows = [r for r in tag_rows if is_ascii(r["tag"])]
    # Among ASCII tags, ones seen in very few documents are suspicious in the
    # same way (isolated proper-noun/brand fragments picked up as pseudo-tags
    # by the lenient recovery parser), but this is a weaker, non-mechanical
    # signal -> reported separately as "worth re-checking", not asserted.
    ascii_low_doc_rows = [r for r in ascii_rows if int(r["doc_count"]) <= 20]

    non_ascii_rows_sorted = sorted(non_ascii_rows, key=lambda r: -int(r["count"]))
    ascii_low_doc_rows_sorted = sorted(ascii_low_doc_rows, key=lambda r: -int(r["count"]))

    with open(os.path.join(OUT_DIR, "pseudo_tag_candidates.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tag", "count", "doc_count", "file_count", "flag_reason"])
        for r in non_ascii_rows_sorted:
            w.writerow([r["tag"], r["count"], r["doc_count"], r["file_count"], "non_ascii_tag_name"])
        for r in ascii_low_doc_rows_sorted:
            w.writerow([r["tag"], r["count"], r["doc_count"], r["file_count"], "ascii_but_low_doc_count(<=20)"])

    total_non_ascii_occurrences = sum(int(r["count"]) for r in non_ascii_rows)

    summary_path = os.path.join(OUT_DIR, "corpus_summary.json")
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)

    summary["tag_inventory_reliability_note"] = {
        "total_distinct_tags": len(tag_rows),
        "non_ascii_named_tags": len(non_ascii_rows),
        "non_ascii_named_tag_total_occurrences": total_non_ascii_occurrences,
        "ascii_named_tags": len(ascii_rows),
        "ascii_named_tags_with_doc_count_le_20": len(ascii_low_doc_rows),
        "method": (
            "Non-ASCII 문자를 포함하는 tag 이름은 기계적으로 식별 가능한 사실이며, 수동 표본 확인(예: "
            "'전기말', '제', 'Mirae', 'PUBG', 'PlayStation' 등) 결과 실제 XML tag가 아니라 "
            "원문 텍스트 안의 '<...>' 괄호 표기(예: '<전기말>', "
            "'<PUBG: Battlegrounds>')가 recover=True 관용(lenient) XML 파서에 의해 "
            "가짜 element로 해석된 산물로 판단된다. "
            "이는 추론(heuristic)이며 100% 확정은 아니다 - "
            "다음 단계(Parser)에서 개별 확인 필요."
        ),
        "output_file": "pseudo_tag_candidates.csv",
    }
    summary["nonroot_elements_with_namespace_by_tag"] = truly_namespaced_tags
    summary["nonroot_elements_with_namespace_by_tag_CORRECTION_NOTE"] = (
        "원래 값은 lxml el.nsmap이 조상으로부터 상속된 namespace까지 포함하는 특성 때문에 "
        "root(xsi 선언)를 가진 거의 모든 element를 'namespace 있음'으로 잘못 집계한 버그였다. "
        "이 필드는 Clark-notation 형태(예: '{http://www.w3.org/1999/xhtml}a')로 실제 "
        "qualified된 tag만 다시 집계해 대체한 값이다."
    )
    summary["file_count_mismatch_reclassified"] = {
        "real_mismatches_xml_expected_but_missing": real_mismatches,
        "non_xml_format_docs_incorrectly_flagged": non_xml_format_docs,
        "note": (
            "manifest.jsonl의 file_format 필드를 기준으로 재분류함. file_format='pdf+html'인 문서는 "
            "애초에 XML 산출물이 없는 것이 정상이므로 실패가 아님. 이번 코퍼스에는 그런 문서가 "
            f"{len(non_xml_format_docs)}건 존재 (전체 4204건 중 3건, file_format='pdf+html')."
        ),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---- append section to profiling_report.md (idempotent: truncate back to
    # the base report - ending after section 13 - before appending, so reruns
    # of this script don't duplicate sections) ----
    report_path = os.path.join(OUT_DIR, "profiling_report.md")
    with open(report_path, encoding="utf-8") as f:
        report = f.read()
    anchor = "xpath_profile.csv, table_profile.json, tag_samples.jsonl, parse_failures.jsonl, profiling_report.md"
    anchor_idx = report.find(anchor)
    if anchor_idx == -1:
        raise RuntimeError("base report anchor not found - profiling_report.md structure changed unexpectedly")
    report = report[: anchor_idx + len(anchor)]

    # Fix section 4 (Namespace), which originally dumped the buggy
    # nonroot_elements_with_namespace_by_tag (see BUGFIX note above).
    sec4_start = report.find("## 4. Namespace")
    sec5_start = report.find("## 5. CDATA")
    if sec4_start == -1 or sec5_start == -1:
        raise RuntimeError("section 4/5 anchors not found - profiling_report.md structure changed unexpectedly")
    root_nsmap_line = f"- root nsmap 종류: {summary['root_namespace_maps']}\n"
    fixed_sec4 = (
        "## 4. Namespace\n\n"
        + root_nsmap_line
        + "- root 외 element에서 실제로 namespace-qualified된 tag (Clark notation 기준, 원래 계산 방식의 "
          "버그를 본 finalize 단계에서 보정함 - 아래 섹션 15 이전 '중요' 참고): "
        + f"{truly_namespaced_tags}\n\n"
    )
    report = report[:sec4_start] + fixed_sec4 + report[sec5_start:]

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    genuine_candidates = [r for r in ascii_rows if int(r["doc_count"]) > 20]
    genuine_candidates_sorted = sorted(genuine_candidates, key=lambda r: -int(r["count"]))

    extra = []
    a = extra.append
    a("\n## 14. 섹션 11 보정 - manifest n_files 불일치 재분류\n")
    a(f"섹션 11에서 보고된 불일치 3건을 manifest의 `file_format` 필드로 재확인한 결과, "
      f"3건 모두 `file_format='pdf+html'`로 선언된 문서였다. 즉 원본이 XML로 제공되지 않고 "
      f"PDF와 viewer HTML로만 제공된 정상 케이스이며, 실제 XML 파일 누락/오류가 아니다.")
    for d in non_xml_format_docs:
        a(f"- doc={d['doc_id']} declared_file_format={d['declared_file_format']} "
          f"(declared_n_files={d['declared_n_files']}, actual xml files=0, 대신 pdf+viewer html 2개 존재)")
    a(f"- file_format='xml'로 선언되었는데 실제 개수가 어긋난 **진짜 불일치는 {len(real_mismatches)}건**이다.")
    a("")
    a("**추가 보정**: 섹션 4(Namespace)의 'root 외 element에서 namespace가 관측된 tag' 항목도 이번 finalize "
      "단계에서 다시 계산해 교체했다. lxml의 `el.nsmap`이 조상으로부터 상속된 namespace까지 포함하기 때문에, "
      "root가 xsi namespace를 선언하는 이 코퍼스에서는 사실상 거의 모든 element가 잘못 집계되었었다. "
      "교체된 값은 Clark-notation으로 실제 qualified된 tag(`{http://www.w3.org/1999/xhtml}a` 등)만 반영한다.\n")

    a("## 15. Tag 인벤토리 신뢰성에 대한 관찰 (중요)\n")
    a(f"전체 distinct tag 수는 **{len(tag_rows)}개**로 집계되었으나, 이 중 상당수는 실제 XML tag가 아니라 "
      f"parser 복구(recover=True) 과정에서 발생한 것으로 추정되는 **가짜 element**로 판단된다.\n")
    a(f"- 전체 XML 파일 중 strict(엄격) parsing 성공: {summary['parse_status_counts'].get('strict_ok', 0)}건, "
      f"recover=True로 복구 성공: {summary['parse_status_counts'].get('recovered', 0)}건 "
      f"(원인: 본문 텍스트 내 이스케이프되지 않은 `&` 및 `<단어>`형 괄호 표기 - 섹션 12 참고)")
    a(f"- tag 이름에 비-ASCII(한글 등) 문자가 포함된 tag: **{len(non_ascii_rows)}개** "
      f"(전체 등장 횟수 {total_non_ascii_occurrences}회)")
    a(f"- 수동 표본 확인 결과, 이들은 `<전기말>`, `<제>`, `<당기>`, `<보수지급금액>` 처럼 재무제표 표의 "
      f"열 머리글이나 본문 중 괄호 강조 표기가 '<...>'로 작성되어 있었고, 닫는 tag가 없어 recover=True 파서가 "
      f"이를 실제 element로 오인한 것으로 관찰된다.")
    a(f"- ASCII 이름을 가진 tag 중에서도 등장 문서 수(doc_count)가 20 이하로 매우 적은 tag가 "
      f"**{len(ascii_low_doc_rows)}개** 있으며, 표본 확인 결과 `Mirae`, `PUBG:`, `PlayStation`, `Sony`, "
      f"`BGMI`, `Celltrion`, `Global`, `XI.`, `PT` 등 특정 회사의 사업보고서 본문에 등장하는 고유명사/약어 "
      f"조각으로 확인되어, 같은 원인(괄호 표기 오인)일 가능성이 높다. 다만 이는 추정이며 100% 확정이 아니다.")
    a(f"- 위 두 그룹을 제외하면, 다수 문서(doc_count > 20)에 걸쳐 반복적으로 등장하는 **'진짜 tag 후보'는 "
      f"약 {len(genuine_candidates)}개**로 좁혀진다. 목록:\n")
    a("| tag | count | doc_count | file_count |")
    a("|---|---|---|---|")
    for r in genuine_candidates_sorted:
        a(f"| `{r['tag']}` | {r['count']} | {r['doc_count']} | {r['file_count']} |")
    a("")
    a("**중요**: 위 분류는 이번 프로파일링 단계에서 관찰된 기계적 신호(비-ASCII 여부, doc_count)에 근거한 "
      "잠정적 관찰이며, 어떤 tag가 '진짜'이고 어떤 tag가 '가짜'인지에 대한 최종 판단이 아니다. "
      "전체 후보 목록은 `pseudo_tag_candidates.csv`에 기록했으며, 최종 확인은 다음 단계(Parser 설계, 구조 "
      "판정)에서 수행되어야 한다. 본 산출물(tag_profile.csv 등)은 원본 관찰치를 그대로 보존하며 수정하지 않았다.\n")

    extra.append("\n## 16. XPath (구조적 경로) 패턴\n")
    extra.append(f"index를 제외한 tag-이름 경로 기준으로 distinct structural path **{len(xpath_data)}개**가 관찰되었다 "
                  f"(전체 목록은 `xpath_profile.csv` 참고, {os.path.getsize(os.path.join(OUT_DIR, 'xpath_profile.csv'))//1024//1024}MB). "
                  f"상위 30개:\n")
    extra.append("| structural_path | count |")
    extra.append("|---|---|")
    for path, cnt in xpath_data[:30]:
        extra.append(f"| `{path}` | {cnt} |")
    extra.append("")
    extra.append("**관찰**: `.../TABLE/TBODY/TR/TD/TABLE/TBODY/TR/TD` 처럼 TABLE이 TABLE 안에 중첩된 경로가 "
                  "상위권에 나타나 표 중첩 구조가 드물지 않음을 보여준다. 또한 `LIBRARY` tag가 SECTION 사이에 "
                  "가변적으로 삽입되는 경로(`SECTION-1/LIBRARY/SECTION-2`, `SECTION-1/SECTION-2/LIBRARY` 등)가 "
                  "다수 관찰되어, LIBRARY의 삽입 위치가 문서마다 고정적이지 않은 것으로 보인다.\n")

    with open(report_path, "a", encoding="utf-8") as f:
        f.write("\n".join(extra))

    print(json.dumps({
        "total_distinct_tags": len(tag_rows),
        "non_ascii_named_tags": len(non_ascii_rows),
        "ascii_low_doc_count_tags": len(ascii_low_doc_rows),
        "genuine_tag_candidates": len(genuine_candidates),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
