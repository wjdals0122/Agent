"""GPU 온도·전력 가드.

이 머신은 4x RTX 4090(각 450W)이라 전부 돌리면 1.8kW를 넘겨 전원이 내려간다.
그래서 실험은 (1) GPU 한 장만 쓰고 (2) 전력 상한을 걸고 (3) 온도가 넘으면 쉰다.

  python -m src.eval.gpu_guard                  # 현재 상태 한 번 출력
  python -m src.eval.gpu_guard --watch          # 5초마다 갱신
  python -m src.eval.gpu_guard --cap 300        # 전 GPU 전력 상한 300W (관리자 권한 필요)
  python -m src.eval.gpu_guard --cap default    # 상한 원복

주의: select_gpu()는 torch를 import 하기 전에 불러야 한다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

# 워커 로그가 cp949 UnicodeEncodeError로 죽은 전례가 있다. 전 진입점에서 막는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 데스크톱을 물고 있어 우리가 함부로 채우면 안 되는 GPU (memory/gpu3-is-display-gpu 참고)
DISPLAY_GPU = 3

QUERY = (
    "index,temperature.gpu,power.draw,power.limit,"
    "memory.used,memory.total,utilization.gpu"
)


def read_gpus() -> list[dict]:
    """nvidia-smi 한 번 찍어서 GPU별 상태를 dict 리스트로 돌려준다."""
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout
    gpus = []
    for line in out.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        gpus.append({
            "index": int(f[0]),
            "temp": float(f[1]),
            "power": float(f[2]),
            "power_limit": float(f[3]),
            "mem_used": float(f[4]),
            "mem_total": float(f[5]),
            "util": float(f[6]),
        })
    return gpus


def format_table(gpus: list[dict]) -> str:
    rows = ["idx  온도   전력          메모리            사용률",
            "---  -----  ------------  ----------------  ------"]
    for g in gpus:
        tag = "  <- 디스플레이" if g["index"] == DISPLAY_GPU else ""
        rows.append(
            f"{g['index']:>3}  {g['temp']:>3.0f}°C  "
            f"{g['power']:>5.0f}/{g['power_limit']:>3.0f}W  "
            f"{g['mem_used']:>6.0f}/{g['mem_total']:>5.0f}MiB  "
            f"{g['util']:>4.0f}%{tag}"
        )
    return "\n".join(rows)


def set_power_cap(watts: int | str) -> None:
    """전 GPU 전력 상한을 건다. watts='default'면 원복. 관리자 권한이 필요하다."""
    arg = "0" if watts == "default" else str(int(watts))
    for g in read_gpus():
        r = subprocess.run(
            ["nvidia-smi", "-i", str(g["index"]), "-pl", arg],
            capture_output=True, text=True,
        )
        ok = r.returncode == 0
        msg = (r.stdout or r.stderr).strip().splitlines()
        print(f"GPU {g['index']}: {'OK' if ok else '실패'} — {msg[-1] if msg else ''}")
        if not ok:
            print("  (관리자 권한 PowerShell에서 실행해야 한다)")


def cooldown(gpu: int, max_temp: float, resume_temp: float, timeout: float = 900.0) -> float:
    """해당 GPU가 max_temp를 넘었으면 resume_temp 아래로 내려올 때까지 막는다.

    반환값은 기다린 초. 넘지 않았으면 즉시 0.0.
    """
    t0 = time.time()
    waited = False
    while True:
        temp = next(g["temp"] for g in read_gpus() if g["index"] == gpu)
        if not waited and temp < max_temp:
            return 0.0
        if waited and temp <= resume_temp:
            break
        if time.time() - t0 > timeout:
            print(f"[guard] {timeout:.0f}초를 기다려도 {temp:.0f}°C — 그대로 진행한다", flush=True)
            break
        if not waited:
            print(f"[guard] GPU {gpu} {temp:.0f}°C > {max_temp:.0f}°C — "
                  f"{resume_temp:.0f}°C까지 대기", flush=True)
            waited = True
        time.sleep(5)
    elapsed = time.time() - t0
    if waited:
        print(f"[guard] 재개 ({elapsed:.0f}초 대기)", flush=True)
    return elapsed


class TempMonitor:
    """백그라운드로 온도를 샘플링해 최고치를 기록한다. with 문으로 쓴다."""

    def __init__(self, gpu: int, interval: float = 2.0):
        self.gpu = gpu
        self.interval = interval
        self.peak_temp = 0.0
        self.peak_power = 0.0
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                g = next(x for x in read_gpus() if x["index"] == self.gpu)
            except Exception:
                continue
            self.peak_temp = max(self.peak_temp, g["temp"])
            self.peak_power = max(self.peak_power, g["power"])
            self.samples += 1

    def __enter__(self) -> "TempMonitor":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)

    def summary(self) -> str:
        return f"최고 {self.peak_temp:.0f}°C / {self.peak_power:.0f}W ({self.samples}회 샘플)"


def select_gpu(physical: int) -> None:
    """torch import 전에 호출할 것. 이후 torch가 보는 장치는 cuda:0 하나뿐이다."""
    os.environ["CUDA_VISIBLE_DEVICES"] = str(physical)
    # 길이가 제각각인 배치에서 캐싱 할당자가 조각나 예약량이 부풀어 오르는 걸 막는다
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def apply_memory_fraction(physical: int, fraction: float | None = None) -> float:
    """select_gpu + torch import 이후에 호출. 디스플레이 GPU면 기본으로 상한을 건다."""
    import torch

    if fraction is None:
        fraction = 0.8 if physical == DISPLAY_GPU else 0.9
    torch.cuda.set_per_process_memory_fraction(fraction, 0)
    return fraction


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="5초마다 갱신")
    ap.add_argument("--cap", help="전 GPU 전력 상한 W, 또는 'default'로 원복")
    args = ap.parse_args()

    if args.cap:
        set_power_cap(args.cap if args.cap == "default" else int(args.cap))
        print()

    if args.watch:
        try:
            while True:
                print(f"\n{time.strftime('%H:%M:%S')}\n{format_table(read_gpus())}", flush=True)
                time.sleep(5)
        except KeyboardInterrupt:
            return 0
    else:
        print(format_table(read_gpus()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
