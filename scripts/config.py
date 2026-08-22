"""
Central path configuration for every script in this folder. Nothing else
should compute CORPUS_DIR / RAG_DIR / MANIFEST_PATH itself - import them from
here, so the data location is a config concern, not something baked into
each file's source.

Resolution order (first one found wins):
  1. explicit function argument (callers can always override directly)
  2. environment variables: DART_CORPUS_DIR, DART_RAG_DIR, DART_MANIFEST_PATH
  3. scripts/config.json, if present (see config.example.json for the shape)
  4. built-in default: <repo root>/corpus, <repo root>/corpus/rag,
     <repo root>/corpus/manifest.jsonl

This keeps the common case (this repo's own corpus/ layout) working with no
setup at all, while letting anyone point the whole pipeline at a different
corpus copy (another machine, a subset extracted for testing, a differently
named folder) without touching any script's source.
"""
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

_DEFAULTS = {
    "corpus_dir": os.path.join(REPO_ROOT, "corpus"),
    "rag_dir": None,  # derived from corpus_dir below if not set explicitly
    "manifest_path": None,  # derived from corpus_dir below if not set explicitly
}


def _load_file_config():
    if not os.path.isfile(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _resolve(key, env_var):
    env_val = os.environ.get(env_var)
    if env_val:
        return env_val
    file_cfg = _load_file_config()
    if key in file_cfg and file_cfg[key]:
        return file_cfg[key]
    return _DEFAULTS[key]


def get_corpus_dir():
    return os.path.abspath(_resolve("corpus_dir", "DART_CORPUS_DIR"))


def get_rag_dir():
    val = _resolve("rag_dir", "DART_RAG_DIR")
    if val is None:
        val = os.path.join(get_corpus_dir(), "rag")
    return os.path.abspath(val)


def get_manifest_path():
    val = _resolve("manifest_path", "DART_MANIFEST_PATH")
    if val is None:
        val = os.path.join(get_corpus_dir(), "manifest.jsonl")
    return os.path.abspath(val)


# Most call sites just want these three as plain values, computed once at
# import time using the resolution order above. Anything that needs to
# override at runtime (tests, one-off scripts pointed at a different corpus)
# can still call get_corpus_dir()/get_rag_dir()/get_manifest_path() directly
# with a different environment/config in effect.
CORPUS_DIR = get_corpus_dir()
RAG_DIR = get_rag_dir()
MANIFEST_PATH = get_manifest_path()
