# -*- coding: utf-8 -*-
"""배포 패키지 — 벡터 + 청크 + 코드를 한 폴더로 묶는다.

    python scripts/10_package_index.py --out dist
    python scripts/10_package_index.py --verify dist      # 받는 쪽에서 무결성 확인

왜 스크립트인가
────────────────────────────────────────────────────────────────────────
손으로 고르면 **청크 JSONL 을 빠뜨린다.** 벡터에는 표시용 원문이 없어서
chunk_store 가 원본 JSONL 을 바이트 오프셋으로 seek 해 읽는다. 그게 없으면
검색 순위는 나오는데 근거 본문이 안 나온다 — 에러도 없이.

그리고 `text_offsets.npz` 는 파일명이 아니라 (file_idx, offset) 으로 되어 있다.
청크 파일이 한 바이트라도 다르면 **엉뚱한 문단이 근거로 나온다.** 그래서
전 파일 SHA256 을 같이 넣고, 받는 쪽이 --verify 로 대조한다.

패키지 구조 — 레포를 클론하지 않아도 그 자리에서 돌아간다
────────────────────────────────────────────────────────────────────────
    dist/
    ├── src/index/ src/eval/          임베딩·검색 코드
    ├── scripts/                      run_embed.sh, 이 스크립트
    ├── data/index/vectors/           dense · sparse · id_map · offsets
    ├── data/processed/chunks_by_10_companies/
    ├── requirements.txt
    ├── SHA256SUMS
    └── README.md                     받는 사람용 안내

paths.py 의 ROOT 가 `parents[2]` 라서 dist/ 가 그대로 루트가 된다.
환경변수 없이 `cd dist && python -m src.eval.ask "질문"` 이면 끝이다.
"""
import argparse
import hashlib
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 이 스크립트는 배포 패키지 안에도 복사된다. 거기엔 pipeline_paths 가 없고,
# --verify 는 그게 필요 없다. 그래서 없으면 없는 대로 간다.
try:
    import pipeline_paths as P
except ImportError:
    P = None

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# (원본 상대경로, 패키지 안 상대경로)
DATA_FILES = [
    ('data/index/vectors/dense.f32.npy',    'data/index/vectors/dense.f32.npy'),
    ('data/index/vectors/sparse.npz',       'data/index/vectors/sparse.npz'),
    ('data/index/vectors/id_map.parquet',   'data/index/vectors/id_map.parquet'),
    ('data/index/vectors/meta.json',        'data/index/vectors/meta.json'),
    ('data/index/vectors/text_offsets.npz', 'data/index/vectors/text_offsets.npz'),
]
CHUNKS_SRC = 'data/processed/chunks_by_10_companies'
CODE_DIRS = ['src/index', 'src/eval']
CODE_FILES = ['src/__init__.py', 'requirements.txt',
              'scripts/run_embed.sh', 'scripts/10_package_index.py']

SUMS = 'SHA256SUMS'


def sha256_file(path, buf=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(buf), b''):
            h.update(blk)
    return h.hexdigest()


def human(n):
    return '%.2f GB' % (n / 1e9) if n >= 1e9 else '%.1f MB' % (n / 1e6)


def copy(src, dst, hardlink=False):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if hardlink:
        if os.path.exists(dst):
            os.remove(dst)
        try:
            os.link(src, dst)
            return
        except OSError:
            pass  # 볼륨이 다르면 그냥 복사한다
    shutil.copy2(src, dst)


def gather(root):
    """패키지에 들어갈 (원본절대경로, 패키지상대경로) 목록."""
    items = []
    for rel_src, rel_dst in DATA_FILES:
        items.append((os.path.join(root, rel_src), rel_dst))

    src_dir = os.path.join(root, CHUNKS_SRC)
    if os.path.isdir(src_dir):
        for fn in sorted(os.listdir(src_dir)):
            if fn.startswith('.'):
                continue
            items.append((os.path.join(src_dir, fn), '%s/%s' % (CHUNKS_SRC, fn)))

    for d in CODE_DIRS:
        for fn in sorted(os.listdir(os.path.join(root, d))):
            if fn == '__pycache__' or fn.endswith('.pyc'):
                continue
            p = os.path.join(root, d, fn)
            if os.path.isfile(p):
                items.append((p, '%s/%s' % (d, fn)))

    for f in CODE_FILES:
        p = os.path.join(root, f)
        if os.path.isfile(p):
            items.append((p, f))
    return items


README = """# 공시 RAG 인덱스 — 배포본

bge-m3 로 임베딩한 공시 청크 **{n_rows:,}개**와 검색 코드가 들어 있다.
레포를 클론하지 않아도 **이 폴더 안에서 그대로 돌아간다.**

## 0. 무결성 확인 (먼저 할 것)

드라이브 업로드·압축을 거치면서 파일이 깨질 수 있다. 깨져도 에러가 안 나고
**엉뚱한 문단이 근거로 나오기 때문에** 반드시 먼저 확인한다.

```bash
python scripts/10_package_index.py --verify .
```

`text_offsets.npz` 는 파일명이 아니라 (파일 번호, 바이트 위치) 로 되어 있다.
청크 JSONL 이 한 바이트라도 다르면 검색 결과의 본문이 조용히 어긋난다.

## 1. 설치

```bash
python -m venv venv
venv\\Scripts\\activate          # Windows
pip install -r requirements.txt

# torch 는 CUDA 버전에 맞춰 따로. 이 인덱스를 만든 환경은 2.7.1+cu118:
#   pip install torch --index-url https://download.pytorch.org/whl/cu118
```

bge-m3 모델(약 2.3GB)은 처음 실행할 때 HuggingFace 에서 자동으로 받는다.

## 2. 쓰는 법

```bash
python -m src.eval.ask                                   # 대화형 (권장)
python -m src.eval.ask "삼성전자의 주주환원 정책은?"
python -m src.eval.ask "유상증자 목적" --company 카카오 --year 2024 -k 5
python -m src.eval.ask "삼성전자와 SK하이닉스의 매출액 비교" --latest-only
python -m src.eval.run_eval -k 5                         # 질문 세트 일괄 채점
```

모델 로드에 20~65초 걸린다. 질문을 여러 개 볼 거면 인덱스를 한 번만 올리는
**대화형**을 쓸 것. 대화형 지시어: `/company 삼성전자` `/year 2023` `/group periodic`
`/k 8` `/w 1.0` `/full` `/reset` `/gpu` `/q`

코드에서 직접 쓸 때:

```python
from src.eval.retriever import Retriever

ret = Retriever()                     # GPU 없으면 Retriever(device="cpu")
res = ret.search("삼성전자의 주주환원 정책은?", k=5, company="삼성전자")
for h in res.hits:
    print(h.rank, h.score, h.company, h.document_title)
    print(h.section_path)
    print(h.content)
```

## 3. 요구사항

| | |
| --- | --- |
| 디스크 | 이 폴더 {size} + 모델 2.3GB |
| GPU | VRAM 3.5GB (dense fp16 1.26GB + bge-m3). RTX 4090 에서 질의당 0.4~1.0초 |
| GPU 없으면 | `Retriever(device="cpu")` — RAM 5GB 정도. 동작은 하지만 느리다 |
| Python | 3.12 |

## 4. 인덱스 내용

| | |
| --- | --- |
| 모델 | `BAAI/bge-m3` — dense 1024차원(L2 정규화) + sparse 250,002 lexical |
| 행 | {n_rows:,} |
| 문서 | 4,204건 / 70개사 (커버리지 4,204 / 4,204, 결손 0) |
| 점수 | `w_dense × 코사인 + w_sparse × lexical 내적` — 기본 1.0 / 1.0 |

`dense.f32.npy` 의 행 순서 = `id_map.parquet` 의 `row` = 청크 JSONL 을 파일명 순으로
이어붙인 순서. **이 순서가 유일한 계약이다.** 청크 파일을 재정렬하거나 이름을 바꾸면
모든 검색 결과가 엉뚱한 문서를 가리킨다.

## 5. 알아둘 것

- **bge-m3 는 대칭 모델이다.** 질의에 `query: ` 같은 접두사를 붙이지 말 것.
- **`base_year` 는 접수연도다** (`int(receipt_no[:4])`). 사업연도가 아니다.
  61.4만 행이 옛 스키마라 사업연도를 안 갖고 있다. 연도 필터를 걸 때 유의할 것.
- **비교 질의는 단일 검색으로 안 된다.** "삼성전자와 SK하이닉스 매출 비교"를 그냥
  넣으면 상위 20건이 전부 한쪽 회사다. 회사별로 나눠 검색해야 한다
  (`ask` 는 회사 2곳을 감지하면 자동으로 나눈다).
- 정정 재제출로 밀려난 옛 정기보고서 14,442행(2.35%)은 `latest_only=True` 로 뺄 수 있다.

## 6. 실측 (48문항 top-5)

| | |
| --- | --- |
| 회사 적중 @1 | 100% |
| 섹션 적중 @1 | 90.0% |
| 섹션 적중 @5 | 95.0% |
| 1위가 회사·섹션 모두 적중 | 90.6% |

---

패키지 생성: {built_at}
"""


def build(root, out, hardlink):
    items = gather(root)
    missing = [s for s, _ in items if not os.path.isfile(s)]
    if missing:
        print('원본이 없다:')
        for m in missing:
            print('   %s' % m)
        return 2

    total = sum(os.path.getsize(s) for s, _ in items)
    print('%d개 파일 / %s → %s%s'
          % (len(items), human(total), out, ' (하드링크)' if hardlink else ''))

    os.makedirs(out, exist_ok=True)
    lines = []
    done = 0
    t0 = time.time()
    for src, rel in items:
        dst = os.path.join(out, rel.replace('/', os.sep))
        copy(src, dst, hardlink)
        h = sha256_file(dst)
        lines.append('%s  %s' % (h, rel))
        done += os.path.getsize(src)
        print('  %-58s %10s  (%3.0f%%)'
              % (rel, human(os.path.getsize(src)), done / total * 100), flush=True)

    with open(os.path.join(out, SUMS), 'w', encoding='utf-8', newline='\n') as w:
        w.write('\n'.join(lines) + '\n')

    import json
    meta = json.load(open(os.path.join(out, 'data/index/vectors/meta.json'),
                          encoding='utf-8'))
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8', newline='\n') as w:
        w.write(README.format(n_rows=meta['n_rows'], size=human(total),
                              built_at=time.strftime('%Y-%m-%d %H:%M')))

    print()
    print('완료 %.0fs — %s' % (time.time() - t0, human(total)))
    print('  %s  전 파일 체크섬' % os.path.join(out, SUMS))
    print('  %s  받는 사람용 안내' % os.path.join(out, 'README.md'))
    print()
    print('받는 쪽에서 먼저 돌릴 것:  python scripts/10_package_index.py --verify .')
    return 0


def verify(pkg):
    sums = os.path.join(pkg, SUMS)
    if not os.path.isfile(sums):
        print('%s 가 없다. 배포 패키지가 맞는가?' % sums)
        return 3

    rows = []
    with open(sums, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                continue
            h, rel = line.split('  ', 1)
            rows.append((h, rel))

    print('%d개 파일 대조' % len(rows))
    bad = []
    for i, (want, rel) in enumerate(rows, 1):
        p = os.path.join(pkg, rel.replace('/', os.sep))
        if not os.path.isfile(p):
            bad.append((rel, '없음'))
            print('  [없음] %s' % rel)
            continue
        got = sha256_file(p)
        if got != want:
            bad.append((rel, '해시 불일치'))
            print('  [FAIL] %s' % rel)
        elif i % 5 == 0 or i == len(rows):
            print('  ... %d/%d' % (i, len(rows)), flush=True)

    print()
    if bad:
        print('불일치 %d건 — 다시 받아야 한다.' % len(bad))
        for rel, why in bad:
            print('   %-58s %s' % (rel, why))
        return 2

    # meta.json 이 기록한 청크 파일 SHA1 과도 대조한다 (임베딩 당시의 입력이 맞는가)
    import json
    meta_path = os.path.join(pkg, 'data/index/vectors/meta.json')
    meta = json.load(open(meta_path, encoding='utf-8'))
    want_sha1 = meta.get('chunks_file_sha1') or {}
    n_ok = n_bad = 0
    for name, want in sorted(want_sha1.items()):
        p = os.path.join(pkg, CHUNKS_SRC.replace('/', os.sep), name)
        if not os.path.isfile(p):
            print('  [없음] %s' % name)
            n_bad += 1
            continue
        h = hashlib.sha1()
        with open(p, 'rb') as f:
            for blk in iter(lambda: f.read(1 << 22), b''):
                h.update(blk)
        if h.hexdigest() == want:
            n_ok += 1
        else:
            print('  [FAIL] meta.json 의 SHA1 과 다름: %s' % name)
            n_bad += 1

    print('전 파일 SHA256 일치 %d건' % len(rows))
    print('임베딩 당시 청크 SHA1 일치 %d건 / 불일치 %d건' % (n_ok, n_bad))
    if n_bad:
        print('\n청크가 임베딩 당시와 다르다. text_offsets 가 어긋나 '
              '근거 본문이 조용히 틀린다. 다시 받을 것.')
        return 2
    print('\n통과 — 그대로 쓰면 된다.')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='패키지를 만들 폴더')
    ap.add_argument('--verify', metavar='PKG', help='패키지 무결성 대조')
    ap.add_argument('--hardlink', action='store_true',
                    help='복사 대신 하드링크 (같은 볼륨일 때 즉시·용량 0)')
    a = ap.parse_args(argv)

    if a.verify:
        return verify(a.verify)
    if not a.out:
        ap.error('--out 또는 --verify 중 하나가 필요하다')
    if P is None:
        ap.error('패키지 생성은 레포 안에서만 된다 (scripts/pipeline_paths.py 필요). '
                 '배포본에서는 --verify 만 쓴다.')
    return build(P.REPO_ROOT, a.out, a.hardlink)


if __name__ == '__main__':
    raise SystemExit(main())
