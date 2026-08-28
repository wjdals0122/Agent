# -*- coding: utf-8 -*-
"""파이프라인 전체가 공유하는 경로 한 곳.

레포에는 이미 `corpus/`가 있고 목표 계층은 `data/`를 말한다. 원문을 옮기지
않는다(절대 규칙 1: data/raw는 읽기 전용) — 대신 `data/raw`를 기존
`corpus/raw`에 **매핑**하고, 새로 파생되는 산출물만 `data/` 아래에 쌓는다.

    data/raw        →  corpus/raw          (읽기 전용, 원위치 그대로)
    baseline/       →  0단계 회귀 기준선의 **해시**. git에 들어간다.
                       기준선이 이 기계에만 있으면 기준선이 아니다.
    data/baseline_md/ →  그 해시를 만든 md 원본 1.1GB. git에 안 넣는다.
    data/interim    →  doc.json + parse_report.jsonl
    data/processed  →  corpus.db, chunks, tables
    data/index      →  검색 인덱스
    reports/        →  검증 CSV/MD

DART_* 환경변수로 덮어쓸 수 있다(scripts/config.py와 같은 규칙).
"""
import hashlib
import json
import os
import unicodedata

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
PARSER_DIR = os.path.join(REPO_ROOT, 'parser')

CORPUS_DIR = os.path.abspath(os.environ.get(
    'DART_CORPUS_DIR', os.path.join(REPO_ROOT, 'corpus')))
RAW_ROOT = os.path.abspath(os.environ.get(
    'DART_RAW_ROOT', os.path.join(CORPUS_DIR, 'raw')))
MANIFEST_PATH = os.path.abspath(os.environ.get(
    'DART_MANIFEST_PATH', os.path.join(CORPUS_DIR, 'manifest.jsonl')))

DATA_DIR = os.path.abspath(os.environ.get(
    'DART_DATA_DIR', os.path.join(REPO_ROOT, 'data')))
# 해시는 레포 안(추적됨), md 본체는 data/ 안(무시됨).
BASELINE_DIR = os.path.join(REPO_ROOT, 'baseline')
BASELINE_HASH_DIR = os.path.join(BASELINE_DIR, 'hash')
BASELINE_INDEX = os.path.join(BASELINE_DIR, 'index.jsonl')
BASELINE_MD_DIR = os.path.join(DATA_DIR, 'baseline_md')

INTERIM_DIR = os.path.join(DATA_DIR, 'interim')
INTERIM_DOCS_DIR = os.path.join(INTERIM_DIR, 'docs')
PARSE_REPORT = os.path.join(INTERIM_DIR, 'parse_report.jsonl')
ALIAS_CACHE = os.path.join(INTERIM_DIR, 'alias_registry.json')

PROCESSED_DIR = os.path.join(DATA_DIR, 'processed')
INDEX_DIR = os.path.join(DATA_DIR, 'index')
REPORTS_DIR = os.path.join(REPO_ROOT, 'reports')
DOCS_DIR = os.path.join(REPO_ROOT, 'docs')
CONFIG_DIR = os.path.join(REPO_ROOT, 'config')


def nfc(s):
    """경로 문자열의 유니코드 정규화를 NFC 로 맞춘다.

    한글 파일·폴더 이름은 같은 글자가 **두 가지 바이트열**로 존재한다.
    NFC('한'=U+D55C 한 자) 와 NFD('ㅎ+ㅏ+ㄴ' 세 자)다. 맥·구글드라이브·
    일부 zip 을 거쳐 복사된 코퍼스는 NFD 로 저장되는데, `manifest.jsonl`
    과 `baseline/index.jsonl` 은 NFC 로 적혀 있다. 윈도우 파일시스템은
    둘을 같은 이름으로 봐 주지 않으므로, NFC 경로로 `isdir()` 하면
    **폴더가 실제로 있는데도 없다고 나온다**(실측: 4,204개 중 4,054개).

    E3(선언된 charset 과 실제 바이트가 다르다)와 같은 계열의 문제다 —
    적혀 있는 이름과 저장된 이름이 다르다. 그래서 같은 방식으로 다룬다:
    **기록에 남는 경로는 언제나 NFC 로 통일**하고(rel), 디스크를 열 때만
    실제 저장형을 찾는다(on_disk).
    """
    return unicodedata.normalize('NFC', s) if s else s


def rel(path):
    """레포 기준 상대경로. 구분자는 '/', 유니코드는 NFC.

    산출물(doc.json / parse_report / baseline index)에 적히는 경로는 전부
    이 모양이다. 기계가 달라도 같은 문자열이 나와야 단계 사이 조인이 된다.
    """
    return nfc(os.path.relpath(path, REPO_ROOT).replace('\\', '/'))


def on_disk(path):
    """이 경로가 어떤 정규화로 저장돼 있든 **실제로 존재하는 경로**를 준다.

    없으면 None. 있는지 없는지를 판정하는 자리에서 이걸 쓴다.
    """
    for cand in (path, unicodedata.normalize('NFC', path),
                 unicodedata.normalize('NFD', path)):
        if os.path.exists(cand):
            return cand
    return None


def ensure_dirs(*paths):
    for p in paths:
        os.makedirs(p, exist_ok=True)


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def config_hash(obj):
    """멱등 판정용. 설정 dict를 안정적으로 직렬화해 해시한다."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]
