import sys, os, json, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proto

api_key = proto.load_api_key()
resp = requests.get("https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
models = resp.json().get("data", [])
print(f"전체 모델: {len(models)}")

# 가격 구조 샘플
print("\n가격 샘플 (처음 3개):")
for m in models[:3]:
    p = m.get("pricing", {})
    print(f"  {m['id']}  prompt={repr(p.get('prompt'))} completion={repr(p.get('completion'))}")

# :free 접미사 확인
free_suffix = [m for m in models if m["id"].endswith(":free")]
print(f"\n':free' 접미사 모델: {len(free_suffix)}개")
for m in free_suffix[:10]:
    p = m.get("pricing", {})
    print(f"  {m['id']}  prompt={repr(p.get('prompt'))} completion={repr(p.get('completion'))}")

# pricing이 0인 모델
zero_str = [m for m in models if str(m.get("pricing",{}).get("prompt","")) == "0"]
print(f"\nprompt==\"0\" 모델: {len(zero_str)}개")
