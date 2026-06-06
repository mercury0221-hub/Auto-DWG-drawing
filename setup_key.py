"""
setup_key.py — 환경변수 OPENROUTER_KEY 를 읽어 key.enc 생성
사용:
  $env:MASTER_KEY     = "..."
  $env:OPENROUTER_KEY = "sk-or-..."
  py setup_key.py
"""
import os, sys
from cryptography.fernet import Fernet

master  = os.environ.get("MASTER_KEY", "").strip()
api_key = os.environ.get("OPENROUTER_KEY", "").strip()

if not master:
    print("[오류] 환경변수 MASTER_KEY 가 설정되지 않았습니다.")
    sys.exit(1)
if not api_key:
    print("[오류] 환경변수 OPENROUTER_KEY 가 설정되지 않았습니다.")
    sys.exit(1)

fernet = Fernet(master.encode())
token  = fernet.encrypt(api_key.encode())
with open("key.enc", "wb") as f:
    f.write(token)

print(f"[완료] key.enc 생성됨 ({len(token)} bytes)")
print("       평문 키는 메모리에서만 사용됐습니다.")
