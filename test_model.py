# -*- coding: utf-8 -*-
"""test_model.py — OpenRouter 모델 확인 + 텍스트/이미지 인식 테스트"""
import sys, os, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proto, requests
from openai import OpenAI

TARGET_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DIV = "-" * 56

api_key = proto.load_api_key()
client  = OpenAI(api_key=api_key, base_url=proto.OPENROUTER_BASE)

# ── 1. 모델 목록 조회 ────────────────────────────────────────
print(DIV)
print("[1] 모델 존재 확인")
print(DIV)

resp   = requests.get(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=10
)
models = resp.json().get("data", [])
print(f"  OpenRouter 전체 모델 수: {len(models)}")

# 목표 모델 정확 검색
exact = next((m for m in models if m["id"] == TARGET_MODEL), None)
# nemotron 계열 전체
nemotron_all = [m for m in models if "nemotron" in m["id"].lower()]

print(f"\n  목표 모델 ({TARGET_MODEL}):")
if exact:
    m = exact
    modality  = m.get("architecture", {}).get("modality", "text")
    ctx_len   = m.get("context_length", "?")
    pricing   = m.get("pricing", {})
    print(f"  [발견] {m['id']}")
    print(f"    context  : {ctx_len:,} tokens" if isinstance(ctx_len, int) else f"    context  : {ctx_len}")
    print(f"    modality : {modality}")
    print(f"    price    : prompt={pricing.get('prompt','?')} / completion={pricing.get('completion','?')}")
    use_model = m["id"]
    supports_vision = "image" in str(modality).lower()
else:
    print(f"  [없음] 정확히 일치하는 모델 없음")
    print(f"\n  nemotron 계열 전체 ({len(nemotron_all)}개):")
    for m in nemotron_all:
        p  = m.get("pricing", {})
        md = m.get("architecture", {}).get("modality", "text")
        print(f"    - {m['id']}")
        print(f"        modality: {md}  |  price: {p.get('prompt','?')}/{p.get('completion','?')}")

    # 대안: 무료 텍스트 생성 모델 중 첫 번째
    free_text = sorted(
        [m for m in models
         if m.get("pricing",{}).get("prompt") == "0"
         and m.get("pricing",{}).get("completion") == "0"
         and "safety" not in m["id"].lower()
         and "guard" not in m["id"].lower()],
        key=lambda m: m["id"]
    )
    if free_text:
        fallback = free_text[0]
        print(f"\n  [대안 채택] {fallback['id']}")
        use_model      = fallback["id"]
        modality       = fallback.get("architecture", {}).get("modality", "text")
        supports_vision = "image" in str(modality).lower()
    else:
        print("  [오류] 사용 가능한 무료 모델 없음")
        sys.exit(1)

# ── 2. 텍스트 인식 테스트 ─────────────────────────────────────
print()
print(DIV)
print("[2] 텍스트 인식 테스트 (CAD 설계 -> JSON)")
print(DIV)

user_prompt = "폭 3000mm 높이 2000mm 사각형 방을 그려줘"
print(f"  모델  : {use_model}")
print(f"  입력  : {user_prompt}")
print("  요청 중...")

try:
    r = client.chat.completions.create(
        model=use_model,
        messages=[
            {"role": "system", "content": proto.SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=512,
        temperature=0,
    )
    raw    = r.choices[0].message.content.strip()
    tokens = r.usage.total_tokens if r.usage else "?"
    print(f"  토큰  : {tokens}")
    print(f"  응답  :\n{raw}")

    parsed = json.loads(raw)
    proto.validate(parsed)
    print(f"\n  [결과] JSON 파싱.검증 성공")
    ents = parsed.get("entities", [])
    print(f"  entities 수 : {len(ents)}")
    for i, e in enumerate(ents):
        print(f"    [{i}] type={e.get('type')}  ", end="")
        if e.get("type") == "line":
            print(f"start={e.get('start')} end={e.get('end')}")
        elif e.get("type") == "polyline":
            pts = e.get("points", [])
            print(f"points={len(pts)}개  closed={e.get('closed')}")

except json.JSONDecodeError as e:
    print(f"\n  [실패] JSON 파싱 오류: {e}")
    print(f"  원본 : {raw[:200]}")
except Exception as e:
    print(f"\n  [실패] API 호출 오류: {e}")

# ── 3. 이미지 인식 테스트 ─────────────────────────────────────
print()
print(DIV)
print("[3] 이미지 인식 테스트 (Vision)")
print(DIV)

print(f"  모델 vision 지원: {'예' if supports_vision else '아니오'}")

if not supports_vision:
    print(f"  {use_model} 은 이미지 입력을 지원하지 않습니다.")
    # vision 지원 무료 모델 제안
    free_vision = [
        m for m in models
        if "image" in str(m.get("architecture",{}).get("modality","")).lower()
        and m.get("pricing",{}).get("prompt") == "0"
        and m.get("pricing",{}).get("completion") == "0"
    ]
    # 이미지 인식은 vision 지원 모델로 자동 전환하여 테스트
    VISION_FALLBACK = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    vision_candidate = next(
        (m for m in free_vision if VISION_FALLBACK in m["id"]),
        free_vision[0] if free_vision else None
    )

    if vision_candidate:
        print(f"\n  Vision 테스트를 위해 대체 모델 사용: {vision_candidate['id']}")

        import struct, zlib, base64
        def make_tiny_png():
            def chunk(name, data):
                c = struct.pack(">I", len(data)) + name + data
                return c + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
            sig  = b"\x89PNG\r\n\x1a\n"
            ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            # 2×2 RGB: filter(1) + pixel1_RGB(3) + pixel2_RGB(3) per row × 2 rows
            raw  = (b"\x00" + b"\xff\xff\xff" * 2) * 2
            idat = chunk(b"IDAT", zlib.compress(raw))
            iend = chunk(b"IEND", b"")
            return sig + ihdr + idat + iend

        png_b64 = base64.b64encode(make_tiny_png()).decode()
        print("  테스트 이미지: 2x2 흰 픽셀 PNG")
        print("  요청 중...")
        try:
            rv = client.chat.completions.create(
                model=vision_candidate["id"],
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text",      "text": "이 이미지를 한 문장으로 설명해줘."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
                    ],
                }],
                max_tokens=128,
            )
            msg  = rv.choices[0].message
            text = msg.content  # reasoning 모델은 None일 수 있음
            # reasoning 모델은 content 대신 다른 필드 사용
            if text is None:
                raw_msg = rv.model_dump()
                inner   = raw_msg.get("choices", [{}])[0].get("message", {})
                text    = (inner.get("reasoning") or inner.get("reasoning_content")
                           or str(inner))
            print(f"  응답: {str(text)[:200]}")
            print(f"  finish_reason: {rv.choices[0].finish_reason}")
            print("\n  [결과] 이미지 인식 성공")
        except Exception as e:
            print(f"  [결과] 이미지 인식 실패: {e}")
    else:
        print("  현재 사용 가능한 무료 vision 모델 없음")
else:
    # 최소 유효 PNG: 흰 배경 2×2 픽셀
    import struct, zlib
    def make_tiny_png():
        def chunk(name, data):
            c = struct.pack(">I", len(data)) + name + data
            return c + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
        sig  = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        raw  = b"\x00\xff\xff\xff\x00\xff\xff\xff"  # 2행 × RGB
        idat = chunk(b"IDAT", zlib.compress(raw))
        iend = chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    import base64
    png_b64 = base64.b64encode(make_tiny_png()).decode()
    print("  테스트 이미지: 2x2 흰 픽셀 PNG")
    print("  요청 중...")
    try:
        rv = client.chat.completions.create(
            model=use_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",      "text": "이 이미지를 한 문장으로 설명해줘."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
                ],
            }],
            max_tokens=64,
        )
        print(f"  응답: {rv.choices[0].message.content.strip()}")
        print("\n  [결과] 이미지 인식 성공")
    except Exception as e:
        print(f"  [결과] 이미지 인식 실패: {e}")

print()
print(DIV)
print("테스트 완료")
print(DIV)
