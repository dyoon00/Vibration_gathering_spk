#!/usr/bin/env python3
"""
진동 데이터 수집 GUI (라즈베리파이 바탕화면 실행용)

- 수집 시작: OpenOCD 자동 실행 -> Vibe_FFT_Step==1 대기 -> read_idx sync -> 실시간 폴링 수집 시작
- 수집 중지: 수집 종료 -> OpenOCD 종료 -> 이번 세션 .bin들을 CSV로 자동 변환(입력한 메타데이터 헤더 포함)

bin_to_csv.py와 같은 폴더에 둬야 함.
"""

import os
import sys
import socket
import subprocess
import threading
import time

# 260702 : GUI 스레드와 수집 스레드가 GIL을 공유하므로, 전환 주기를 늘려서
#          수집 스레드(SWD 폴링)가 더 길게 끊김없이 도는 쪽에 우선권을 줌.
sys.setswitchinterval(0.1)
from datetime import datetime
from tkinter import Tk, StringVar
from tkinter import ttk, messagebox
import tkinter.font as tkfont

from bin_to_csv import convert_to_csv, archive_bin_files, DATA_DIR

# ── OpenOCD / 심볼 설정 (collect_vibration.py와 동일) ──
OPENOCD_HOST = "localhost"
OPENOCD_PORT = 6666

ADDR_RING_X     = 0x200308E0
ADDR_RING_Y     = 0x200508E0
ADDR_RING_Z     = 0x200708E0
ADDR_WRITE_IDX  = 0x200A8C6C
ADDR_READ_IDX   = 0x200A8C70
ADDR_OVERRUN    = 0x200A8C74
ADDR_VIBE_STEP  = 0x20090C65

RING_SLOTS  = 2
FFT_LEN     = 16384
SLOT_BYTES  = FFT_LEN * 4

SAVE_DIR = DATA_DIR


class OpenOCD:
    def __init__(self, host=OPENOCD_HOST, port=OPENOCD_PORT):
        self.sock = socket.create_connection((host, port))

    def command(self, cmd):
        self.sock.sendall(cmd.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf[:-1].decode(errors="replace").strip()

    def read_u32(self, addr):
        resp = self.command(f"read_memory 0x{addr:x} 32 1")
        return int(resp.split()[0], 0)

    def read_u8(self, addr):
        resp = self.command(f"read_memory 0x{addr:x} 8 1")
        return int(resp.split()[0], 0)

    def write_u32(self, addr, value):
        self.command(f"write_memory 0x{addr:x} 32 {{{value}}}")

    def dump_image(self, filename, addr, size_bytes):
        resp = self.command(f"dump_image {filename} 0x{addr:x} {size_bytes}")
        if "Error" in resp or "error" in resp:
            raise RuntimeError(f"dump_image failed for {filename}: {resp}")

    def close(self):
        self.sock.close()


def slot_address(base_addr, slot):
    return base_addr + slot * SLOT_BYTES


class VibrationApp:
    def __init__(self, root):
        self.root = root
        root.title("진동 데이터 수집기")

        self.openocd_proc = None
        self.ocd = None
        self.collector_thread = None
        self.stop_event = threading.Event()

        self.session_start_idx = None
        self.last_write_idx = None
        self.overrun_baseline = None
        self.overrun_now = 0

        # 260702 : 한글 폰트 미설치 환경(네모 깨짐) 대비. sudo apt install fonts-nanum 후 적용됨.
        try:
            default_font = tkfont.nametofont("TkDefaultFont")
            default_font.configure(family="NanumGothic", size=10)
            style = ttk.Style()
            style.configure(".", font=default_font)
        except Exception:
            pass  # 폰트 없으면 조용히 기본 폰트로 폴백

        self._build_ui()

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        self.entries = {}
        row = 0

        # 날짜
        ttk.Label(frm, text="날짜").grid(row=row, column=0, sticky="w", pady=4)
        e = ttk.Entry(frm, width=30)
        e.insert(0, datetime.now().strftime("%Y-%m-%d"))
        e.grid(row=row, column=1, pady=4, padx=6)
        self.entries["날짜"] = e
        row += 1

        # 파일명
        ttk.Label(frm, text="파일명").grid(row=row, column=0, sticky="w", pady=4)
        e = ttk.Entry(frm, width=30)
        e.grid(row=row, column=1, pady=4, padx=6)
        self.entries["파일명"] = e
        row += 1

        # 데이터종류 (직접 입력)
        ttk.Label(frm, text="데이터종류").grid(row=row, column=0, sticky="w", pady=4)
        e = ttk.Entry(frm, width=30)
        e.grid(row=row, column=1, pady=4, padx=6)
        self.entries["데이터종류"] = e
        row += 1

        # 모터 RPM
        ttk.Label(frm, text="모터 RPM").grid(row=row, column=0, sticky="w", pady=4)
        e = ttk.Entry(frm, width=30)
        e.grid(row=row, column=1, pady=4, padx=6)
        self.entries["모터 RPM"] = e
        row += 1

        # 주파수
        ttk.Label(frm, text="주파수").grid(row=row, column=0, sticky="w", pady=4)
        e = ttk.Entry(frm, width=30)
        e.grid(row=row, column=1, pady=4, padx=6)
        self.entries["주파수"] = e
        row += 1

        # 토출량, 전양정, 축동력, 흡입압력, 토출압력, 전류, 효율, 역률, 전압
        for label in ["토출량", "전양정", "축동력", "흡입압력", "토출압력",
                      "전류", "효율", "역률", "전압"]:
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=4)
            e = ttk.Entry(frm, width=30)
            e.grid(row=row, column=1, pady=4, padx=6)
            self.entries[label] = e
            row += 1

        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=12)

        self.start_btn = ttk.Button(btn_frame, text="수집 시작", command=self.start_collection)
        self.start_btn.grid(row=0, column=0, padx=6)

        self.stop_btn = ttk.Button(btn_frame, text="수집 중지", command=self.stop_collection, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=6)
        row += 1

        self.status_var = StringVar(value="대기 중")
        ttk.Label(frm, textvariable=self.status_var, foreground="blue").grid(
            row=row, column=0, columnspan=2, pady=8, sticky="w")

    def _get_meta(self):
        return {
            "날짜": self.entries["날짜"].get(),
            "파일명": self.entries["파일명"].get(),
            "데이터종류": self.entries["데이터종류"].get(),
            "모터RPM": self.entries["모터 RPM"].get(),
            "주파수": self.entries["주파수"].get(),
            "토출량": self.entries["토출량"].get(),
            "전양정": self.entries["전양정"].get(),
            "축동력": self.entries["축동력"].get(),
            "흡입압력": self.entries["흡입압력"].get(),
            "토출압력": self.entries["토출압력"].get(),
            "전류": self.entries["전류"].get(),
            "효율": self.entries["효율"].get(),
            "역률": self.entries["역률"].get(),
            "전압": self.entries["전압"].get(),
        }

    def start_collection(self):
        if not self.entries["파일명"].get().strip():
            messagebox.showwarning("입력 필요", "파일명을 입력해주세요.")
            return

        os.makedirs(SAVE_DIR, exist_ok=True)
        self.status_var.set("OpenOCD 실행 중...")
        self.root.update_idletasks()

        self.openocd_proc = subprocess.Popen(
            ["nice", "-n", "-20",
             "openocd", "-f", "interface/stlink.cfg", "-f", "target/stm32u5x.cfg",
             "-c", "adapter speed 8000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # OpenOCD가 6666 포트를 열 때까지 대기 (최대 10초)
        self.ocd = None
        for _ in range(50):
            try:
                self.ocd = OpenOCD()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.2)

        if self.ocd is None:
            messagebox.showerror("연결 실패", "OpenOCD에 연결하지 못했습니다.")
            self.status_var.set("연결 실패")
            return

        self.status_var.set("Vibe_FFT_Step == 1 대기 중...")
        self.root.update_idletasks()
        while self.ocd.read_u8(ADDR_VIBE_STEP) != 1:
            time.sleep(0.001)

        write_idx0 = self.ocd.read_u32(ADDR_WRITE_IDX)
        self.ocd.write_u32(ADDR_READ_IDX, write_idx0)
        self.session_start_idx = write_idx0
        self.last_write_idx = write_idx0
        self.overrun_baseline = self.ocd.read_u32(ADDR_OVERRUN)
        self.overrun_now = 0

        self.stop_event.clear()
        self.collector_thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.collector_thread.start()

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set(f"수집 중... (시작 슬롯 #{write_idx0})")
        self._refresh_status()

    def _collect_loop(self):
        while not self.stop_event.is_set():
            write_idx = self.ocd.read_u32(ADDR_WRITE_IDX)
            read_idx = self.ocd.read_u32(ADDR_READ_IDX)

            if write_idx > read_idx:
                for idx in range(read_idx, write_idx):
                    slot = idx % RING_SLOTS
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    prefix = os.path.join(SAVE_DIR, f"slot_{idx:08d}_{ts}")
                    fx, fy, fz = f"{prefix}_x.bin", f"{prefix}_y.bin", f"{prefix}_z.bin"

                    self.ocd.dump_image(fx, slot_address(ADDR_RING_X, slot), SLOT_BYTES)
                    self.ocd.dump_image(fy, slot_address(ADDR_RING_Y, slot), SLOT_BYTES)
                    self.ocd.dump_image(fz, slot_address(ADDR_RING_Z, slot), SLOT_BYTES)

                self.ocd.write_u32(ADDR_READ_IDX, write_idx)
                self.last_write_idx = write_idx

            overrun = self.ocd.read_u32(ADDR_OVERRUN)
            self.overrun_now = overrun - self.overrun_baseline

    def _refresh_status(self):
        if self.collector_thread and self.collector_thread.is_alive():
            wi = self.last_write_idx if self.last_write_idx is not None else self.session_start_idx
            n_slots = (wi - self.session_start_idx) if self.session_start_idx is not None else 0
            self.status_var.set(
                f"수집 중... 슬롯 #{wi} (누적 {n_slots}개) | 이번 세션 유실: {self.overrun_now}"
            )
            # 260702 : 0.5초 -> 20초로 완화. 화면 갱신 빈도를 줄여 수집 스레드 부담 최소화.
            #          (overrun_now 자체는 수집 스레드에서 실시간 갱신되므로 감지 자체엔 영향 없음)
            self.root.after(20000, self._refresh_status)

    def stop_collection(self):
        self.status_var.set("중지 중...")
        self.root.update_idletasks()

        self.stop_event.set()
        if self.collector_thread:
            self.collector_thread.join(timeout=5)

        end_idx = self.last_write_idx if self.last_write_idx is not None else self.session_start_idx

        if self.ocd:
            self.ocd.close()
        if self.openocd_proc:
            self.openocd_proc.terminate()
            try:
                self.openocd_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.openocd_proc.kill()

        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

        if end_idx is not None and self.session_start_idx is not None and end_idx > self.session_start_idx:
            self.status_var.set("CSV 변환 중...")
            self.root.update_idletasks()
            meta = self._get_meta()

            def _on_progress(percent):
                self.status_var.set(f"CSV 변환 중... {percent}%")
                self.root.update_idletasks()

            try:
                paths, missing = convert_to_csv(self.session_start_idx, end_idx, meta,
                                                 progress_callback=_on_progress)

                def _on_zip_progress(percent):
                    self.status_var.set(f"원본 압축 중... {percent}%")
                    self.root.update_idletasks()

                zip_path, n_archived, zip_missing = archive_bin_files(
                    self.session_start_idx, end_idx, meta, progress_callback=_on_zip_progress
                )

                self.status_var.set(f"완료: CSV {len(paths)}개, 원본 압축 {n_archived}개 파일")
                path_list = "\n".join(os.path.basename(p) for p in paths)
                messagebox.showinfo(
                    "완료",
                    f"CSV 저장 완료 ({len(paths)}개 파일):\n{path_list}\n(누락 슬롯: {missing}개)\n\n"
                    f"원본 .bin 압축 완료:\n{os.path.basename(zip_path)}\n"
                    f"({n_archived}개 파일 압축, 원본 삭제됨)"
                )
            except Exception as e:
                self.status_var.set(f"CSV 변환/압축 실패: {e}")
                messagebox.showerror("오류", f"CSV 변환/압축 실패:\n{e}")
        else:
            self.status_var.set("수집된 데이터 없음")


if __name__ == "__main__":
    root = Tk()
    app = VibrationApp(root)
    root.mainloop()
