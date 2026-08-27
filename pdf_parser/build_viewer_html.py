# -*- coding: utf-8 -*-
"""한화에어로스페이스 20260513000860 분기보고서 뷰어 HTML → RAG 마크다운.

DART 가 document.xml 을 안 주는(status 014) 문서라 XML 경로가 없다.
같은 접수번호의 공시뷰어 HTML 전문을 원본으로 쓴다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import periodic_parser as pp
import rag_pipeline
import viewer_html_parser as vh

DESKTOP = r'C:\Users\강정민\OneDrive - inu.ac.kr\바탕 화면'
RCEPT = '20260513000860'
SRC = os.path.join(DESKTOP, r'3.공시\corpus\raw\periodic\한화에어로스페이스',
                   RCEPT + '_quarter_2026_03', RCEPT + '_viewer.html')
MANIFEST = os.path.join(DESKTOP, r'3.공시\corpus\manifest.jsonl')
OUT_DIR = os.path.join(DESKTOP,
                       r'all_converted_md-20260816T043323Z-1-001'
                       r'\all_converted_md\results\rag')


def load_meta():
    with open(MANIFEST, encoding='utf-8') as fh:
        for line in fh:
            d = json.loads(line)
            if d.get('rcept_no') == RCEPT:
                return d
    raise SystemExit('manifest 에 %s 가 없습니다' % RCEPT)


def sibling_aliases(corp_name):
    """같은 회사의 기존 periodic md 가 쓰는 동의어 줄을 그대로 따른다.

    동의어에는 manifest 로는 알 수 없는 변형('한화에어로스페이스(주)')이
    섞여 있다. CompanyAliasRegistry 가 raw 트리 전체를 훑어 모은 것인데,
    한 건 만들자고 1,054개 폴더를 다시 훑을 이유가 없다. 이미 같은 회사
    파일 19개가 합의된 목록을 갖고 있으니 그걸 쓴다.
    """
    import glob
    found = []
    pattern = os.path.join(OUT_DIR, '%s_periodic_*.md' % corp_name)
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if line.startswith('> 동의어/검색어:'):
                    found.append([a.strip() for a in
                                  line.split(':', 1)[1].split(',')])
                    break
    if not found:
        return ()
    # 가장 긴 목록을 기준으로 삼는다.
    return tuple(max(found, key=len))


def main():
    meta = load_meta()
    src = open(SRC, encoding='utf-8', errors='replace').read()

    if not vh.looks_like_viewer_html(src):
        raise SystemExit('본문이 없는 껍데기 뷰어 HTML 입니다: %s' % SRC)

    doc = vh.parse_viewer_html(src, RCEPT, meta['corp_name'])
    body = pp.to_markdown(doc, with_header=False)

    extra = sibling_aliases(meta['corp_name'])
    aliases = rag_pipeline.build_aliases(meta, extra)
    header = rag_pipeline.build_rag_header(meta, extra)
    text = header + '\n\n' + body

    name = '%s_periodic_%s.md' % (meta['corp_name'], RCEPT)
    out = os.path.join(OUT_DIR, name)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(text)

    fields = ('corp_code', 'stock_code', 'corp_name', 'listed_name',
              'industry', 'sector', 'doc_id', 'doc_group', 'doc_subtype',
              'report_nm', 'rcept_no', 'rcept_dt', 'is_correction',
              'base_year', 'base_month', 'file_path')
    rec = {'file': name}
    rec.update((f, meta[f]) for f in fields if f in meta)
    rec['file_format'] = 'html'          # PDF 아님 — 뷰어 HTML 전문에서 뽑았다
    rec['aliases'] = list(aliases)
    rec['source_xml'] = os.path.basename(SRC)

    print('생성: %s' % out)
    print('  %s bytes / %s chars' % (format(len(text.encode()), ','),
                                     format(len(text), ',')))
    print()
    print('rag_meta.jsonl 에 넣을 줄:')
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == '__main__':
    main()
