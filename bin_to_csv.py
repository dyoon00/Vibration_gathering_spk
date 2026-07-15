#!/usr/bin/env python3
"""
.bin(슬롯별 X/Y/Z float32) 파일들을 하나의 CSV로 합치는 변환기.
vibration_gui.py의 "수집 중지" 시 자동 호출되며, 단독 실행(수동 변환)도 가능.
"""

import os
import glob
import array
import csv
import zipfile
from datetime import datetime

FFT_LEN = 16384
DATA_DIR = os.path.expanduser("~/Desktop/oda_vibration/data")
CSV_DIR = os.path.expanduser("~/Desktop/oda_vibration/csv")
RAW_ARCHIVE_DIR = os.path.expanduser("~/Desktop/oda_vibration/raw_archive")

# CSV 파일 하나당 샘플 수 제한 (819200 = 16384 x 50슬롯 = 약 30초 분량)
SAMPLES_PER_FILE = 819200
SLOTS_PER_FILE = SAMPLES_PER_FILE // FFT_LEN


def find_slot_file(data_dir, idx, axis):
    pattern = os.path.join(data_dir, f"slot_{idx:08d}_*_{axis}.bin")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def read_slot_floats(path):
    a = array.array('f')
    with open(path, 'rb') as f:
        a.frombytes(f.read())
    return a


def _write_header(writer, meta):
    writer.writerow(["날짜", meta.get("날짜", "")])
    writer.writerow(["파일명", meta.get("파일명", "")])
    writer.writerow(["데이터종류", meta.get("데이터종류", "")])
    writer.writerow(["모터RPM", meta.get("모터RPM", "")])
    writer.writerow(["주파수", meta.get("주파수", "")])
    writer.writerow(["토출량", meta.get("토출량", "")])
    writer.writerow(["전양정", meta.get("전양정", "")])
    writer.writerow(["축동력", meta.get("축동력", "")])
    writer.writerow(["흡입압력", meta.get("흡입압력", "")])
    writer.writerow(["토출압력", meta.get("토출압력", "")])
    writer.writerow(["전류", meta.get("전류", "")])
    writer.writerow(["효율", meta.get("효율", "")])
    writer.writerow(["역률", meta.get("역률", "")])
    writer.writerow(["전압", meta.get("전압", "")])
    writer.writerow([])  # 구분용 빈 줄

    # idx: 이 세션 전체 기준으로 계속 증가하는 샘플 번호 (파일이 나뉘어도 이어짐)
    # -> idx / 26667 로 경과시간(초) 계산 가능
    writer.writerow(["idx", "x", "y", "z"])  # 데이터 컬럼 헤더


def convert_to_csv(start_idx, end_idx, meta, data_dir=DATA_DIR, csv_dir=CSV_DIR, progress_callback=None):
    """
    start_idx ~ end_idx-1 슬롯을 순서대로 이어붙여 CSV로 저장.
    SLOTS_PER_FILE(기본 50슬롯 = 819200샘플)마다 파일을 분할함 (_part001, _part002, ...).
    meta: {"날짜":..., "파일명":..., "데이터종류":..., "모터RPM":..., "주파수":...,
           "토출량":..., "전양정":..., "축동력":..., "흡입압력":..., "토출압력":...,
           "전류":..., "효율":..., "역률":..., "전압":...}
    progress_callback(percent:int) : 5% 단위로 호출됨. 안 넘기면 콘솔에 자체 출력.
    반환: (csv_path 목록, missing_slot_count)
    """
    os.makedirs(csv_dir, exist_ok=True)

    base_name = (meta.get("파일명") or "").strip()
    if not base_name:
        base_name = f"vibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if base_name.endswith(".csv"):
        base_name = base_name[:-4]

    # 날짜_파일명_partXXX.csv 형식으로 저장
    date_str = (meta.get("날짜") or "").strip()
    if date_str:
        base_name = f"{date_str}_{base_name}"

    total_slots = max(end_idx - start_idx, 1)
    last_percent = -1

    def _report_progress(done_slots):
        nonlocal last_percent
        percent = (done_slots * 100) // total_slots
        # 5% 단위로만 알림 (오버헤드 최소화)
        if percent >= last_percent + 5 or (percent == 100 and last_percent != 100):
            last_percent = percent
            if progress_callback:
                progress_callback(percent)
            else:
                print(f"\rCSV 변환 중... {percent}%", end="", flush=True)

    missing = 0
    out_paths = []
    part = 1
    writer = None
    f = None
    global_idx = 0

    def _open_new_part():
        nonlocal part, writer, f
        if f:
            f.close()
        out_path = os.path.join(csv_dir, f"{base_name}_part{part:03d}.csv")
        # utf-8-sig(BOM 포함)로 저장 -> 엑셀에서 한글 깨짐 방지
        f = open(out_path, "w", newline="", encoding="utf-8-sig")
        writer = csv.writer(f)
        _write_header(writer, meta)
        out_paths.append(out_path)
        part += 1

    _open_new_part()
    slots_in_part = 0

    for done, slot_num in enumerate(range(start_idx, end_idx), start=1):
        if slots_in_part >= SLOTS_PER_FILE:
            _open_new_part()
            slots_in_part = 0

        fx = find_slot_file(data_dir, slot_num, "x")
        fy = find_slot_file(data_dir, slot_num, "y")
        fz = find_slot_file(data_dir, slot_num, "z")

        if not (fx and fy and fz):
            missing += 1
            _report_progress(done)
            continue

        xs = read_slot_floats(fx)
        ys = read_slot_floats(fy)
        zs = read_slot_floats(fz)

        for i in range(FFT_LEN):
            writer.writerow([global_idx, xs[i], ys[i], zs[i]])
            global_idx += 1

        slots_in_part += 1
        _report_progress(done)

    if f:
        f.close()

    if progress_callback is None:
        print()  # 콘솔 출력 썼으면 줄바꿈으로 마무리

    return out_paths, missing


def archive_bin_files(start_idx, end_idx, meta, data_dir=DATA_DIR, archive_dir=RAW_ARCHIVE_DIR,
                       progress_callback=None, delete_originals=True):
    """
    start_idx ~ end_idx-1 슬롯의 .bin 원본들을 zip 하나로 압축.
    성공적으로 압축된 파일은 delete_originals=True면 원본 삭제(용량 절약).
    반환: (zip_path, 압축된 파일 개수, 못 찾은 슬롯 개수)
    """
    os.makedirs(archive_dir, exist_ok=True)

    base_name = (meta.get("파일명") or "").strip() or f"vibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    date_str = (meta.get("날짜") or "").strip()
    if date_str:
        base_name = f"{date_str}_{base_name}"

    zip_path = os.path.join(archive_dir, f"{base_name}_raw.zip")

    total_slots = max(end_idx - start_idx, 1)
    last_percent = -1

    def _report_progress(done_slots):
        nonlocal last_percent
        percent = (done_slots * 100) // total_slots
        if percent >= last_percent + 5 or (percent == 100 and last_percent != 100):
            last_percent = percent
            if progress_callback:
                progress_callback(percent)
            else:
                print(f"\r압축 중... {percent}%", end="", flush=True)

    archived_files = []
    missing = 0

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for done, slot_num in enumerate(range(start_idx, end_idx), start=1):
            for axis in ("x", "y", "z"):
                path = find_slot_file(data_dir, slot_num, axis)
                if path is None:
                    missing += 1
                    continue
                zf.write(path, arcname=os.path.basename(path))
                archived_files.append(path)
            _report_progress(done)

    if progress_callback is None:
        print()

    # zip에 정상적으로 다 들어간 뒤에만 원본 삭제 (압축 실패시 원본 보존)
    if delete_originals:
        for path in archived_files:
            try:
                os.remove(path)
            except OSError:
                pass

    return zip_path, len(archived_files), missing


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("사용법: python3 bin_to_csv.py <start_idx> <end_idx>")
        sys.exit(1)

    s, e = int(sys.argv[1]), int(sys.argv[2])
    meta = {
        "날짜": datetime.now().strftime("%Y-%m-%d"),
        "파일명": f"manual_{s}_{e}",
        "데이터종류": "",
        "모터RPM": "",
        "주파수": "",
        "토출량": "",
        "전양정": "",
        "축동력": "",
        "흡입압력": "",
        "토출압력": "",
        "전류": "",
        "효율": "",
        "역률": "",
        "전압": "",
    }
    paths, missing = convert_to_csv(s, e, meta)
    print(f"저장 완료: {len(paths)}개 파일 (누락 슬롯: {missing}개)")
    for p in paths:
        print(f"  - {p}")
