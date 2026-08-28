#!/usr/bin/env bash
# GPU 워커 병렬 실행. 인자는 그대로 워커에 전달된다.
#   bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
#
# 다른 작업이 물고 있는 GPU 를 피하려면 쓸 GPU 만 골라 준다. world 는 개수에서 나온다.
#   GPUS=1,2 bash scripts/run_embed.sh --max-length 1024 --batch-tokens 16384
#
# 주의: world(=GPU 개수)가 샤드 경계다. 바꿔서 다시 돌리려면
# _work/dense.shard*.npy 를 지우고 처음부터 가야 한다. embed_merge 에도
# 같은 --world 를 줘야 한다.
set -u
cd "$(dirname "$0")/.."
mkdir -p logs

GPUS="${GPUS:-0,1,2,3}"
IFS=',' read -r -a DEVS <<< "$GPUS"
WORLD=${#DEVS[@]}

echo "GPU [$GPUS] → 워커 $WORLD개 (world=$WORLD)"
echo "머지할 때: python -m src.index.embed_merge --world $WORLD"

for r in "${!DEVS[@]}"; do
  dev="${DEVS[$r]}"
  echo "  rank $r → GPU $dev  (logs/embed.$r.log)"
  CUDA_VISIBLE_DEVICES="$dev" OMP_NUM_THREADS=8 \
    python -m src.index.embed_worker --rank "$r" --world "$WORLD" "$@" \
    > "logs/embed.$r.log" 2>&1 &
done

wait
echo "모든 워커 종료. logs/embed.{0..$((WORLD-1))}.log 확인."
