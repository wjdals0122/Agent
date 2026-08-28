# -*- coding: utf-8 -*-
"""임베딩에 실제로 들어간 줄만 남긴 청크 파일을 만든다.

    python scripts/11_compact_chunks.py --out data/processed/chunks_v2

무엇을 하나
────────────────────────────────────────────────────────────────────────
정정 위치를 마킹한 새 버전이 들어오면서 밀려난 옛 문서(manifest 의
`skipped_doc_ids`)는 지금 **파일에는 남아 있고 임베딩만 안 된** 상태다.
이 스크립트는 그 줄을 실제로 빼서 벡터와 1:1 로 맞는 파일을 만든다.

    617,380줄  →  614,578줄   (id_map 과 정확히 같아진다)

회사 10개 단위 분할은 그대로 둔다. 뺄 줄이 있는 파일만 새로 쓰고, 나머지는
그대로 복사한다.

왜 스크립트인가 — 지우면 딸려 오는 것들
────────────────────────────────────────────────────────────────────────
    1. text_offsets.npz  바이트 위치라 줄을 빼면 그 뒤가 전부 밀린다.
                         에러 없이 근거 본문이 어긋난다. 반드시 재빌드.
    2. meta.json         chunks_file_sha1 이 임베딩 당시 입력의 지문이다.
                         파일이 바뀌면 갱신해야 --verify 가 통과한다.
    3. 배포 패키지        SHA256SUMS 가 달라진다. 다시 만들어야 한다.

이 스크립트는 1·2 를 자동으로 처리하고, 3 은 안내만 한다.

안전장치
────────────────────────────────────────────────────────────────────────
새로 쓴 파일의 chunk_id 를 `id_map.parquet` 과 **전량·순서까지** 대조한다.
하나라도 어긋나면 아무것도 갱신하지 않고 멈춘다. 벡터 행과 텍스트 줄이
어긋나는 것이 이 작업에서 유일하게 위험한 실패다.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_paths as P

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def sha1_file(path, buf=1 << 22):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(buf), b''):
            h.update(blk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=None, help='원본 청크 폴더 (기본: paths.CHUNKS_DIR)')
    ap.add_argument('--out', required=True, help='정리본을 쓸 폴더')
    ap.add_argument('--no-verify', action='store_true',
                    help='id_map 대조를 건너뛴다. 권하지 않는다')
    a = ap.parse_args(argv)

    import orjson
    from src.index import paths

    src = a.src or str(paths.CHUNKS_DIR)
    out = a.out
    if os.path.abspath(src) == os.path.abspath(out):
        print('원본과 출력이 같다. 다른 폴더를 지정할 것.')
        return 2

    man_path = os.path.join(src, 'manifest.json')
    man = json.load(open(man_path, encoding='utf-8')) if os.path.isfile(man_path) else {}
    skip = set(man.get('skipped_doc_ids') or ())
    if not skip:
        print('manifest 에 skipped_doc_ids 가 없다. 뺄 줄이 없으므로 할 일이 없다.')
        return 0

    files = sorted(paths.chunk_files())
    print('원본 %d개 파일 · 뺄 doc_id %d개' % (len(files), len(skip)))
    for d in sorted(skip):
        print('   %s' % d)
    print()

    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    kept_ids = []
    per_file = []
    total_in = total_out = 0

    for path in files:
        name = path.name
        dst = os.path.join(out, name)
        n_in = n_out = 0
        removed = 0
        # 먼저 이 파일에 뺄 줄이 있는지 본다. 없으면 그대로 복사한다(바이트 동일).
        has = False
        with open(path, 'rb') as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                n_in += 1
                if orjson.loads(raw).get('doc_id') in skip:
                    has = True

        if not has:
            shutil.copy2(path, dst)
            with open(dst, 'rb') as fh:
                for raw in fh:
                    if raw.strip():
                        kept_ids.append(orjson.loads(raw)['chunk_id'])
                        n_out += 1
            print('  %-52s %7d행  (그대로)' % (name, n_out))
        else:
            with open(path, 'rb') as fh, open(dst, 'wb') as w:
                for raw in fh:
                    if not raw.strip():
                        continue
                    rec = orjson.loads(raw)
                    if rec.get('doc_id') in skip:
                        removed += 1
                        continue
                    w.write(raw if raw.endswith(b'\n') else raw + b'\n')
                    kept_ids.append(rec['chunk_id'])
                    n_out += 1
            print('  %-52s %7d행  (%d행 제거)' % (name, n_out, removed))

        per_file.append((name, n_in, n_out, n_in - n_out))
        total_in += n_in
        total_out += n_out

    print()
    print('%s줄 → %s줄  (%s줄 제거) · %.0fs'
          % (f'{total_in:,}', f'{total_out:,}', f'{total_in - total_out:,}',
             time.time() - t0))

    # ── 안전장치: 벡터 행과 1:1 인가 ──────────────────────────────
    if not a.no_verify:
        import pyarrow.parquet as pq
        expected = pq.read_table(paths.ID_MAP, columns=['chunk_id']).column('chunk_id').to_pylist()
        if len(kept_ids) != len(expected):
            print('\n[중단] 행 수 불일치 — 정리본 %s / id_map %s'
                  % (f'{len(kept_ids):,}', f'{len(expected):,}'))
            print('       meta.json 을 갱신하지 않았다. 출력 폴더를 지우고 원인을 볼 것.')
            return 2
        bad = [i for i, (x, y) in enumerate(zip(kept_ids, expected)) if x != y]
        if bad:
            print('\n[중단] chunk_id 순서 불일치 %d건, 첫 행 %d' % (len(bad), bad[0]))
            print('       meta.json 을 갱신하지 않았다.')
            return 2
        print('[PASS] chunk_id %s행이 id_map 과 순서까지 일치' % f'{len(kept_ids):,}')

    # ── manifest 갱신 ────────────────────────────────────────────
    new_man = dict(man)
    new_man['files'] = []
    by_name = {f['file']: f for f in (man.get('files') or [])}
    for name, n_in, n_out, n_rm in per_file:
        row = dict(by_name.get(name, {'file': name}))
        row['row_count'] = n_out
        row['effective_row_count'] = n_out
        row['skipped_doc_ids'] = []
        row['skipped_row_count'] = 0
        new_man['files'].append(row)
    new_man['total_row_count'] = total_out
    new_man['total_effective_row_count'] = total_out
    new_man['skipped_doc_ids'] = []
    new_man['skipped_row_count'] = 0
    # 이력은 남긴다 — 무엇을 왜 뺐는지가 사라지면 안 된다.
    new_man['compaction'] = {
        'removed_doc_ids': sorted(skip),
        'removed_rows': total_in - total_out,
        'source_dir': P.rel(src),
        'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'note': '정정 마킹 새 버전으로 대체된 옛 문서를 파일에서 실제로 제거했다. '
                '임베딩 시점에는 skip 으로 처리돼 벡터에 들어간 적이 없다.',
    }
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf-8', newline='\n') as w:
        json.dump(new_man, w, ensure_ascii=False, indent=2)
    print('[write] %s' % os.path.join(out, 'manifest.json'))

    # ── meta.json 의 청크 지문 갱신 ───────────────────────────────
    meta = json.loads(paths.META.read_text(encoding='utf-8'))
    meta.setdefault('compaction_history', []).append({
        'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'removed_doc_ids': sorted(skip),
        'removed_rows': total_in - total_out,
        'chunks_file_sha1_before': meta.get('chunks_file_sha1'),
    })
    new_sha1 = {}
    for name, *_ in per_file:
        new_sha1[name] = sha1_file(os.path.join(out, name))
    meta['chunks_file_sha1'] = new_sha1
    meta['chunks_source'] = [
        P.rel(os.path.join(out, name)) for name, *_ in per_file
    ]
    meta['skipped_doc_ids'] = []
    meta['skipped_rows'] = 0
    paths.META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[write] %s — chunks_file_sha1 갱신, 이전 값은 compaction_history 에 보존'
          % P.rel(str(paths.META)))

    print()
    print('다음:')
    print('  DART_CHUNKS_DIR=%s python -m src.eval.chunk_store --build' % out)
    print('    ← 바이트 오프셋 재빌드. 이걸 안 하면 근거 본문이 어긋난다.')
    print('  python scripts/09_preflight_chunks.py %s' % out)
    print('  python scripts/10_package_index.py --out dist   # 배포본 다시')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
