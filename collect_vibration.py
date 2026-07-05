#!/usr/bin/env python3
"""
STM32U575 + IIS3DWB 진동 데이터 수집 스크립트 (OpenOCD Tcl RPC 기반, print 없는 경량 버전)

- OpenOCD가 6666(Tcl RPC) 포트로 떠있어야 함:
    openocd -f interface/stlink.cfg -f target/stm32u5x.cfg
- ring_write_idx / ring_read_idx를 폴링해서 새 슬롯이 생기면
  dump_image로 x/y/z 배열을 바이너리(.bin)로 통째로 저장.
- 화면 출력이 전혀 없음(오버헤드 최소화). 상태 확인은 GUI(vibration_gui.py)나
  telnet으로 직접 ring_write_idx/ring_overrun_cnt 읽어서 확인.
"""

import socket
import os
from datetime import datetime

# ── OpenOCD 설정 ──────────────────────────────────────────
OPENOCD_HOST = "localhost"
OPENOCD_PORT = 6666

# ── 심볼 주소 (arm-none-eabi-nm으로 뽑은 값. 재빌드시 바뀔 수 있음!) ──
ADDR_RING_X     = 0x200308E0
ADDR_RING_Y     = 0x200508E0
ADDR_RING_Z     = 0x200708E0
ADDR_WRITE_IDX  = 0x200A8C6C
ADDR_READ_IDX   = 0x200A8C70
ADDR_OVERRUN    = 0x200A8C74
ADDR_VIBE_STEP  = 0x20090C65   # Vibe_FFT_Step (uint8_t)

RING_SLOTS  = 2
FFT_LEN     = 16384
SLOT_BYTES  = FFT_LEN * 4   # float32 = 4바이트 (g단위, DC오프셋 제거 완료된 값)

SAVE_DIR = os.path.expanduser("~/Desktop/oda_vibration/data")


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


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    ocd = OpenOCD()

    # Vibe_FFT_Step이 1이 되는 순간(진입점)까지 대기
    while ocd.read_u8(ADDR_VIBE_STEP) != 1:
        pass

    # 시작 시점 백로그 폐기: read_idx를 write_idx로 맞춰서 "지금부터" 실시간 수집
    write_idx0 = ocd.read_u32(ADDR_WRITE_IDX)
    ocd.write_u32(ADDR_READ_IDX, write_idx0)

    try:
        while True:
            write_idx = ocd.read_u32(ADDR_WRITE_IDX)
            read_idx = ocd.read_u32(ADDR_READ_IDX)

            if write_idx > read_idx:
                for idx in range(read_idx, write_idx):
                    slot = idx % RING_SLOTS
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

                    prefix = os.path.join(SAVE_DIR, f"slot_{idx:08d}_{ts}")
                    fx, fy, fz = f"{prefix}_x.bin", f"{prefix}_y.bin", f"{prefix}_z.bin"

                    ocd.dump_image(fx, slot_address(ADDR_RING_X, slot), SLOT_BYTES)
                    ocd.dump_image(fy, slot_address(ADDR_RING_Y, slot), SLOT_BYTES)
                    ocd.dump_image(fz, slot_address(ADDR_RING_Z, slot), SLOT_BYTES)

                ocd.write_u32(ADDR_READ_IDX, write_idx)

    except KeyboardInterrupt:
        pass
    finally:
        ocd.close()


if __name__ == "__main__":
    main()
