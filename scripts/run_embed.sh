#!/usr/bin/env bash
# GPU 4장에 워커 4개. 인자는 그대로 워커에 전달된다.
#   bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
set -u
cd "$(dirname "$0")/.."
mkdir -p logs

for r in 0 1 2 3; do
  CUDA_VISIBLE_DEVICES=$r OMP_NUM_THREADS=8 \
    python -m src.index.embed_worker --rank $r --world 4 "$@" \
    > logs/embed.$r.log 2>&1 &
done

wait
echo "모든 워커 종료. logs/embed.{0..3}.log 확인."
