#!/usr/bin/env python3
"""
CSV(진동 데이터) 파일 하나(_partNNN.csv)를 읽어서 X/Y/Z 파형 그래프로 그려서 PNG로 저장.

사용법:
    python3 plot_waveform.py <csv파일경로> [출력png경로]

- 파일 하나(최대 819,200샘플)만 대상으로 함 -> 메모리 부담 없음.
- Agg 백엔드 사용 -> 화면(Display) 없는 SSH 환경에서도 동작, 그냥 PNG 파일로 저장됨.
"""

import sys
import csv
import os
import platform
import matplotlib
matplotlib.use("Agg")  # 화면 없는 환경에서도 동작하도록
import matplotlib.pyplot as plt

# 한글 폰트 설정 (OS별로 다름). 없으면 조용히 기본 폰트로 폴백(제목 일부만 깨짐).
_KOREAN_FONTS = {
    "Windows": "Malgun Gothic",
    "Linux": "NanumGothic",   # sudo apt install fonts-nanum 필요
}
try:
    plt.rcParams["font.family"] = _KOREAN_FONTS.get(platform.system(), "sans-serif")
    plt.rcParams["axes.unicode_minus"] = False  # 한글 폰트 사용시 마이너스 기호 깨짐 방지
except Exception:
    pass

SAMPLING_FREQ = 26667.0
FFT_LEN = 16384


def load_csv(path):
    meta = {}
    idxs, xs, ys, zs = [], [], [], []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    i = 0
    # 메타데이터 헤더 읽기 ("idx" 헤더 줄 나올 때까지)
    while i < len(rows) and (not rows[i] or rows[i][0] != "idx"):
        if len(rows[i]) >= 2:
            meta[rows[i][0]] = rows[i][1]
        i += 1
    i += 1  # "idx,x,y,z" 헤더 줄 자체는 건너뜀

    for row in rows[i:]:
        if len(row) < 4:
            continue
        idxs.append(int(row[0]))
        xs.append(float(row[1]))
        ys.append(float(row[2]))
        zs.append(float(row[3]))

    return meta, idxs, xs, ys, zs


def plot_waveform(csv_path, out_path=None):
    meta, idxs, xs, ys, zs = load_csv(csv_path)

    if not idxs:
        raise ValueError("데이터가 없습니다 (빈 CSV).")

    # 경과 시간(초) 계산: idx / 샘플링주파수
    times = [i / SAMPLING_FREQ for i in idxs]

    if out_path is None:
        out_path = os.path.splitext(csv_path)[0] + ".png"

    fig, axes = plt.subplots(3, 1, figsize=(14, 8), sharex=True)

    axes[0].plot(times, xs, linewidth=0.5, color="tab:red")
    axes[0].set_ylabel("X (g)")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(times, ys, linewidth=0.5, color="tab:green")
    axes[1].set_ylabel("Y (g)")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(times, zs, linewidth=0.5, color="tab:blue")
    axes[2].set_ylabel("Z (g)")
    axes[2].set_xlabel("Time (s)")
    axes[2].grid(True, alpha=0.3)

    title = (f"{meta.get('파일명', os.path.basename(csv_path))} | "
             f"{meta.get('데이터종류', '')} | RPM={meta.get('모터RPM', '')} | "
             f"주파수={meta.get('주파수', '')} | {meta.get('날짜', '')}")
    fig.suptitle(title, fontsize=11)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 plot_waveform.py <csv파일경로> [출력png경로]")
        sys.exit(1)

    csv_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    saved = plot_waveform(csv_path, out_path)
    print(f"저장 완료: {saved}")
