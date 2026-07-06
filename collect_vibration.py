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

import socket # 소켓 생성용
import os # 파일 경로 확인용
from datetime import datetime


#---------오픈 OCD 설정-------------------------------
OPENOCD_HOST = 'localhost'
OPENOCD_PORT = 6666 #OPENOCD 포트 번호(TCL 포트)

# --------데이터 주소 (재빌드시 다시 할당해 주어야 함.까먹지 마라)---
ADDR_RING_X     = 0x200308E0
ADDR_RING_Y     = 0x200508E0
ADDR_RING_Z     = 0x200708E0
ADDR_WRITE_IDX  = 0x200A8C6C
ADDR_READ_IDX   = 0x200A8C70
ADDR_OVERRUN    = 0x200A8C74
ADDR_VIBE_STEP  = 0x20090C65   # Vibe_FFT_Step (uint8_t)

#--------- 데이터 크기관련, 링버퍼 상수 정의 -------------------
RING_SLOTS  = 2
FFT_LEN     = 16384
SLOT_BYTES  = FFT_LEN * 4   # float32 = 4바이트 (g단위, DC오프셋 제거 완료된 값)

# bin 파일을 저장할 폴더 경로
SAVE_DIR = os.path.expanduser("~/Desktop/oda_vibration/data")


class OpenOCD:
    # 로컬호스트의 openocd에 연결하는 소켓 생성후 바인딩된 6666포트에 커넥션
    def __init__(self, host=OPENOCD_HOST, port=OPENOCD_PORT):
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port)) # 호스트 튜플 묶어서 전송
        
    # TCL 포트(6666)에 명령어를 전송하는 멤버   
    def command(self, cmd):
        self.sock.sendall(cmd.encode() + b"\x1a") # 소켓이기에 명령어는 바이너리로 변환하여 전송.
                                                  # 명령어 전송후 EOF(cntrl+z) 전송하여 명령어 종료
        buf = b""                                 # 6666 포트답변 수신용 바이너리 버퍼생성
        while not buf.endswith(b"\x1a"):          # 명령어 전송후 답변 수신 EOF가 나올때까지 반복
                                                  # OpenOCD는 EOF로 ctrl + z를 사용(리눅스의 EOF는 ctrl + d랑은 관계없음)
            chunk = self.sock.recv(4096)          # OpenOCD에서 소켓통신이용 4096바이트씩 수신 버퍼
            if not chunk:                         # 데이터가 비어있다면 반복문 탈출 
                break
            buf += chunk                          # 아니면 받은 chunk buf 에추가      
                                          
        return buf[:-1].decode(errors="replace").strip() #EOF 이전까지 슬라이싱 후 바이너리 디코딩 후 공백 줄바꿈 제거
    
    
    # mcu 주소에서 32비트 데이터 읽어오는 멤버
    # 여기서 텍스트로 명령주면 command에서 바이너리로 변환하여 전송하고, 다시 바이너리로 수신한 데이터를 디코딩하여 텍스트로 반환
    def read_u32(self, addr):
        response = self.command(f"read_memory 0x{addr:x} 32 1")
        return int(response.split()[0], 0)
    
    def read_u8(self, addr):
        response = self.command(f"read_memory 0x{addr:x} 8 1") 
        return int(response.split()[0], 0)
    
    def dump_image(self, filename, addr, size_bytes):
        response = self.command(f"dump_image {filename} 0x{addr:x} {size_bytes}")
    
    
    def close(self):
        self.sock.close()
        
def slot_address(base_addr, slot):
    return base_addr + slot * SLOT_BYTES



def main():
    
    os.makedirs(SAVE_DIR, exist_ok=True)
    ocd = OpenOCD() #ocd 소켓 생성 및 연결
      
    while ocd.read_u8(ADDR_VIBE_STEP) != 1: # vibration step이 1이 될때까지 대기 -> 타이밍 꼬임 방지
                                            # (16384의 수집 및 가공완료 시점이 1)
        pass
    
    write_idx0 = ocd.read_u32(ADDR_WRITE_IDX) # 시작 시점의 write_idx 읽어오기
    ocd.write_u32(ADDR_READ_IDX, write_idx0) #SECTION 0x200A8C70에 write_idx0를 기록하여 read_idx를 초기화
    

    try:
        while True:
            write_idx = ocd.read_u32(ADDR_WRITE_IDX) # write_idx 읽어오기
            read_idx = ocd.read_u32(ADDR_READ_IDX)   # read_idx 읽어오기

            if write_idx > read_idx:                # write_idx가 read_idx보다 크면, 즉 새로운 데이터가 수집되었으면
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

        
                                                  
                                                  
                                                  



