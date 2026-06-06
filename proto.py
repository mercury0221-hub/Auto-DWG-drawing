"""
proto.py — DWG 자동 작도 프로토타입
사용 전 준비:
  1. .env 파일에 OPENROUTER_API_KEY=sk-or-v1-... 저장
  2. AutoCAD 실행 후 DWG 열기
  3. python server.py  (웹 UI 서버)
  또는 직접: python proto.py "폭 3m 높이 2m 사각형 방 그려줘"
"""

import sys
import os
import json
import array
import argparse
import requests

from openai import OpenAI

# ─── 상수 ───────────────────────────────────────────────────────────────────
KEY_FILE = "key.enc"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODELS_ENDPOINT = f"{OPENROUTER_BASE}/models"
DEFAULT_MODEL   = "nvidia/nemotron-3-super-120b-a12b:free"

SYSTEM_PROMPT = (
    "You are a CAD drawing assistant. "
    "Output ONLY valid JSON, no markdown, no explanation, no extra text. "
    "Schema: {\"units\": \"mm\", \"entities\": [{\"type\": \"line\", \"start\": [x,y], \"end\": [x,y]} | "
    "{\"type\": \"polyline\", \"points\": [[x,y],...], \"closed\": true|false}]}. "
    "Use millimeters for all coordinates. "
    "CRITICAL RULE: The context message includes '건물내부영역: (x1,y1)~(x2,y2)  중심:(cx,cy)'. "
    "ALL entity coordinates MUST be placed strictly inside this bounding box. "
    "Use the center coordinates as the default placement point unless a room label matches. "
    "NEVER generate coordinates at (0,0) or outside the 건물내부영역 range. "
    "If 공간레이블 has a matching room, center the entity on that label's coordinate instead. "
    "Do not output anything except the JSON object."
)


# ─── .env 자동 로드 ──────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    """
    프로젝트 폴더의 .env 파일을 읽어 os.environ에 주입한다.
    이미 설정된 환경변수는 덮어쓰지 않는다.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()   # 모듈 임포트 시 즉시 실행


# ─── 키 관리 ─────────────────────────────────────────────────────────────────

def load_api_key() -> str:
    """
    API 키 로드 순서:
      1순위: 환경변수 OPENROUTER_API_KEY  (.env 또는 시스템 환경변수)
      2순위: key.enc + MASTER_KEY  (레거시 Fernet 암호화, 하위 호환)

    API 키 변경 방법: .env 파일의 OPENROUTER_API_KEY 값을 새 키로 교체
    """
    # ── 1순위: .env / 환경변수 ────────────────────────────────────────────
    direct = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if direct:
        return direct

    # ── 2순위: 레거시 Fernet 복호화 (key.enc + MASTER_KEY) ───────────────
    master = os.environ.get("MASTER_KEY", "").strip()
    if master and os.path.exists(KEY_FILE):
        try:
            from cryptography.fernet import Fernet
            token = open(KEY_FILE, "rb").read()
            return Fernet(master.encode()).decrypt(token).decode()
        except Exception as e:
            print(f"[경고] key.enc 복호화 실패: {e}")

    # ── 실패 ─────────────────────────────────────────────────────────────
    print("[오류] API 키를 찾을 수 없습니다.")
    print("  해결 방법: .env 파일에 아래 줄을 추가하세요.")
    print("    OPENROUTER_API_KEY=sk-or-v1-...")
    sys.exit(1)


# ─── LLM 연결 (OpenRouter) ───────────────────────────────────────────────────

def get_free_model(api_key: str) -> str:
    """
    OpenRouter 모델 목록에서 pricing.prompt == "0" AND pricing.completion == "0"
    인 무료 모델을 찾아 첫 번째를 반환.
    실패하면 "openrouter/auto" 폴백.
    """
    try:
        resp = requests.get(
            MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])
        free = [
            m for m in models
            if (
                m.get("pricing", {}).get("prompt") == "0"
                and m.get("pricing", {}).get("completion") == "0"
            )
        ]
        if not free:
            print(f"[경고] 무료 모델을 찾지 못했습니다. 기본값 사용: {DEFAULT_MODEL}")
            return DEFAULT_MODEL
        # DEFAULT_MODEL이 목록에 있으면 우선 선택
        if any(m.get("id") == DEFAULT_MODEL for m in free):
            print(f"[모델] 기본 모델 사용: {DEFAULT_MODEL}")
            return DEFAULT_MODEL
        # 없으면 알파벳 정렬 첫 번째
        free.sort(key=lambda m: m.get("id", ""))
        selected = free[0]["id"]
        print(f"[모델] 선택된 무료 모델: {selected}")
        print(f"       후보 무료 모델 수: {len(free)}개")
        return selected
    except Exception as e:
        print(f"[경고] 모델 목록 조회 실패 ({e}). 기본값 사용: {DEFAULT_MODEL}")
        return DEFAULT_MODEL


def _call_llm(client: OpenAI, model: str, text: str, summary: str) -> dict:
    """LLM 1콜. 순수 JSON dict를 반환. 실패 시 ValueError."""
    user_msg = (
        f"도면 컨텍스트:\n{summary}\n\n"
        f"요청: {text}"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        max_tokens=512,
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip()
    return json.loads(raw)


def design(text: str, summary: str, client: OpenAI, model: str) -> dict:
    """
    자연어 → JSON 도형 명세.
    파싱 실패 시 1회 재시도. 그래도 실패하면 RuntimeError.
    모델 429/사용불가 시 다음 무료 모델로 1회 폴백.
    """
    fallback_done = False

    for attempt in range(2):  # 최대 2회(초기 + 재시도 1회)
        try:
            data = _call_llm(client, model, text, summary)
            return data
        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f"[경고] JSON 파싱 실패, 1회 재시도... ({e})")
                continue
            raise RuntimeError(f"LLM 응답 파싱 2회 모두 실패: {e}") from e
        except Exception as e:
            err_str = str(e)
            # 429 또는 모델 사용 불가 → 폴백 모델로 1회 시도
            if ("429" in err_str or "unavailable" in err_str.lower()) and not fallback_done:
                fallback_done = True
                fallback = _next_free_model(client.api_key, model)
                if fallback:
                    print(f"[폴백] {model} 사용 불가 → {fallback} 시도")
                    model = fallback
                    attempt = -1  # 루프 카운터 리셋 효과 없음, 다음 반복으로
                    continue
            raise RuntimeError(f"LLM 호출 실패: {e}") from e

    raise RuntimeError("LLM 호출 최대 시도 초과")


def _next_free_model(api_key: str, current_model: str) -> str | None:
    """current_model을 제외한 다음 무료 모델 반환. 없으면 None."""
    try:
        resp = requests.get(
            MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])
        free = sorted(
            [
                m["id"] for m in models
                if (
                    m.get("pricing", {}).get("prompt") == "0"
                    and m.get("pricing", {}).get("completion") == "0"
                    and m.get("id") != current_model
                )
            ]
        )
        return free[0] if free else None
    except Exception:
        return None


# ─── AutoCAD 연결 ─────────────────────────────────────────────────────────────

def connect():
    """
    실행 중인 AutoCAD 인스턴스에 pyautocad로 연결.
    RPC_E_CALL_REJECTED(-2147418111) 시 최대 5회 재시도.
    Returns: (acad, summary_str)
    """
    import time

    try:
        from pyautocad import Autocad
    except ImportError:
        print("[오류] pyautocad가 설치되지 않았습니다: pip install pyautocad")
        sys.exit(1)

    # COM 초기화 (Flask 스레드 등 비메인 스레드 대응)
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass

    RPC_CALL_REJECTED = -2147418111
    MAX_RETRY = 5
    RETRY_DELAYS = [2, 3, 4, 5, 6]  # 회차별 대기(초)

    print("[연결] AutoCAD 인스턴스에 연결 중...")

    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            acad = Autocad(create_if_not_exists=False)
            doc_name = acad.doc.Name  # 실제 COM 호출로 연결 확인
            break
        except Exception as e:
            last_err = e
            # 오류 코드가 담긴 문자열 or 튜플에서 추출
            err_code = None
            if isinstance(e.args, tuple) and e.args:
                err_code = e.args[0]
            is_rejected = (err_code == RPC_CALL_REJECTED
                           or str(RPC_CALL_REJECTED) in str(e)
                           or "피호출자" in str(e)
                           or "call was rejected" in str(e).lower())

            if is_rejected and attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                print(f"[연결] AutoCAD 응답 거부 ({attempt + 1}/{MAX_RETRY}회) "
                      f"— {wait}초 후 재시도")
                print("       AutoCAD에서 열린 팝업 창이나 명령 입력을 완료해 주세요.")
                time.sleep(wait)
                continue

            # 재시도 불가 에러거나 최대 횟수 초과
            print(f"[오류] AutoCAD 연결 실패: {e}")
            _print_autocad_tips()
            sys.exit(1)
    else:
        print(f"[오류] AutoCAD {MAX_RETRY}회 재시도 모두 실패: {last_err}")
        _print_autocad_tips()
        sys.exit(1)

    print(f"[연결] 성공 — 도면: {doc_name}")
    summary = summarize_drawing(acad)
    print(f"[도면 요약]\n{summary}\n")
    return acad, summary


def summarize_drawing(acad) -> str:
    """
    DWG에서 공간 컨텍스트를 추출해 LLM용 요약 반환.
    list(ms) 대신 lazy 순회로 RPC 거부 위험을 줄임.
    실패 시 EXTMIN/EXTMAX 기반 좌표 추정으로 폴백.
    """
    import time, re

    doc = acad.doc
    ms  = acad.model

    unit_map = {0: "없음", 1: "inch", 2: "ft", 3: "mile", 4: "mm",
                5: "cm", 6: "m", 7: "km", 8: "µin", 9: "mil", 10: "yd"}

    # ── 기본 정보 ────────────────────────────────────────────
    try:
        unit_str = unit_map.get(int(doc.GetVariable("INSUNITS")), "mm")
    except Exception:
        unit_str = "mm"

    emin_v = emax_v = None
    try:
        emin_v = doc.GetVariable("EXTMIN")
        emax_v = doc.GetVariable("EXTMAX")
        ext_str = f"({emin_v[0]:.0f},{emin_v[1]:.0f})~({emax_v[0]:.0f},{emax_v[1]:.0f})"
    except Exception:
        ext_str = "취득불가"

    base_line = f"도면파일: {doc.Name}  단위:{unit_str}  전체범위:{ext_str}"

    # ── 엔티티 lazy 순회 (list(ms) 사용 금지 — 7000개 일괄 마샬링은 RPC 거부 유발) ──
    WALL_LAYERS = {"A-WALL", "A-CON", "A-WALL-MBND"}
    RPC_CALL_REJECTED = -2147418111
    MAX_ITERS = 8000

    wall_xs: list = []
    wall_ys: list = []
    labels:  list = []

    def _iterate():
        count = 0
        for ent in ms:
            count += 1
            if count > MAX_ITERS:
                break
            try:
                layer = ent.Layer
                obj   = ent.ObjectName

                if layer in WALL_LAYERS:
                    if obj == "AcDbLine":
                        sp, ep = ent.StartPoint, ent.EndPoint
                        wall_xs += [sp[0], ep[0]]
                        wall_ys += [sp[1], ep[1]]
                    elif obj in ("AcDbPolyline", "AcDb2dPolyline"):
                        coords = list(ent.Coordinates)
                        for i in range(0, len(coords) - 1, 2):
                            wall_xs.append(coords[i])
                            wall_ys.append(coords[i + 1])

                if obj == "AcDbMText":
                    try:
                        pos = ent.InsertionPoint
                        txt = ent.TextString or ""
                        txt = re.sub(r'\{\\[^}]*\}', '', txt)
                        txt = re.sub(r'\\[A-Za-z0-9.]+;?', '', txt)
                        txt = txt.replace('\\P', ' ').replace('%%u', '').strip()
                        if txt and 2 <= len(txt) <= 30 and not re.fullmatch(r'[\d\s.,+\-/°%%]+', txt):
                            labels.append((txt, int(pos[0]), int(pos[1])))
                    except Exception:
                        pass
                elif obj == "AcDbText":
                    try:
                        pos = ent.InsertionPoint
                        txt = (ent.TextString or "").strip()
                        if txt and 2 <= len(txt) <= 30 and not re.fullmatch(r'[\d\s.,+\-/°%%]+', txt):
                            labels.append((txt, int(pos[0]), int(pos[1])))
                    except Exception:
                        pass
            except Exception:
                continue
        print(f"[도면 분석] {count}개 순회 — wall포인트:{len(wall_xs)}  레이블:{len(labels)}")

    print("[도면 분석] 엔티티 순회 중...")
    for attempt in range(3):
        wall_xs.clear(); wall_ys.clear(); labels.clear()
        try:
            _iterate()
            break
        except Exception as e:
            code = e.args[0] if e.args else None
            if (code == RPC_CALL_REJECTED or "피호출자" in str(e)) and attempt < 2:
                print(f"[도면 분석] RPC 거부 재시도 {attempt + 1}/3…")
                time.sleep(3)
            else:
                print(f"[도면 분석] 순회 실패: {e}")
                break

    lines = [base_line]

    # ── 건물 영역 ─────────────────────────────────────────────
    if wall_xs:
        wx1, wy1 = min(wall_xs), min(wall_ys)
        wx2, wy2 = max(wall_xs), max(wall_ys)
        cx = int((wx1 + wx2) / 2)
        cy = int((wy1 + wy2) / 2)
        lines.append(
            f"건물내부영역: ({wx1:.0f},{wy1:.0f})~({wx2:.0f},{wy2:.0f})"
            f"  중심:({cx},{cy})"
            f"  크기:{wx2 - wx1:.0f}×{wy2 - wy1:.0f}{unit_str}"
        )
        lines.append("좌표기준: 도형은 반드시 건물내부영역 범위 안에 배치할 것.")
    elif emin_v is not None and emax_v is not None:
        # 폴백: 제목란(타이틀 블록)이 보통 음수 Y에 위치하므로 하단 35% 제거
        ey_range = emax_v[1] - emin_v[1]
        ey_floor = emin_v[1] + ey_range * 0.35 if emin_v[1] < 0 else emin_v[1]
        fbx1, fby1, fbx2, fby2 = emin_v[0], ey_floor, emax_v[0], emax_v[1]
        cx = int((fbx1 + fbx2) / 2)
        cy = int((fby1 + fby2) / 2)
        lines.append(
            f"건물내부영역: ({fbx1:.0f},{fby1:.0f})~({fbx2:.0f},{fby2:.0f})"
            f"  중심:({cx},{cy})"
        )
        lines.append("좌표기준: 도형은 반드시 건물내부영역 범위 안에 배치할 것.")
    else:
        lines.append("건물내부영역: 취득불가")
        lines.append("좌표기준: 원점(0,0) 근처에 도형을 배치할 것.")

    # ── 공간 레이블 ──────────────────────────────────────────
    if labels:
        seen, unique = set(), []
        for t, x, y in labels:
            key = t.split()[0]
            if key not in seen:
                seen.add(key)
                unique.append((t, x, y))
        unique = unique[:15]
        label_str = "  ".join(f'"{t}"@({x},{y})' for t, x, y in unique)
        lines.append(f"공간레이블: {label_str}")
    else:
        lines.append("공간레이블: 없음")

    return "\n".join(lines)


def _print_autocad_tips():
    print()
    print("  ── AutoCAD 연결 체크리스트 ──────────────────────")
    print("  1. AutoCAD가 완전히 실행된 상태인지 확인")
    print("  2. DWG 파일이 열려 있고 명령 대기 상태인지 확인")
    print("     (명령줄에 'Command:' 표시되어야 함)")
    print("  3. AutoCAD에 팝업·경고창이 없는지 확인")
    print("  4. AutoCAD 보안 경고가 나타나면 '허용' 클릭")
    print("  5. AutoCAD 재시작 후 다시 시도")
    print("  ─────────────────────────────────────────────────")


# ─── 검증 ─────────────────────────────────────────────────────────────────────

def validate(data: dict) -> None:
    """
    필수 키/타입 체크. 실패 시 ValueError 발생.
    """
    if not isinstance(data, dict):
        raise ValueError("최상위 값이 dict가 아닙니다.")
    if "entities" not in data:
        raise ValueError("'entities' 키가 없습니다.")
    if not isinstance(data["entities"], list):
        raise ValueError("'entities'가 리스트가 아닙니다.")
    if len(data["entities"]) == 0:
        raise ValueError("'entities'가 비어 있습니다.")

    for i, ent in enumerate(data["entities"]):
        if not isinstance(ent, dict):
            raise ValueError(f"entities[{i}]가 dict가 아닙니다.")
        etype = ent.get("type")
        if etype == "line":
            for key in ("start", "end"):
                v = ent.get(key)
                if not isinstance(v, (list, tuple)) or len(v) < 2:
                    raise ValueError(f"entities[{i}].{key}가 올바른 좌표가 아닙니다: {v}")
        elif etype == "polyline":
            pts = ent.get("points")
            if not isinstance(pts, (list, tuple)) or len(pts) < 2:
                raise ValueError(f"entities[{i}].points가 2개 이상의 좌표 배열이어야 합니다.")
            for j, pt in enumerate(pts):
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    raise ValueError(f"entities[{i}].points[{j}]가 올바른 좌표가 아닙니다: {pt}")
        else:
            raise ValueError(f"entities[{i}].type이 'line' 또는 'polyline'이어야 합니다: {etype!r}")


# ─── 작도 헬퍼 ───────────────────────────────────────────────────────────────

def apoint(x, y, z=0.0):
    """pyautocad APoint 생성."""
    from pyautocad import APoint
    return APoint(float(x), float(y), float(z))


def coords_to_array(points) -> array.array:
    """
    [[x1,y1],[x2,y2],...] → AddPolyline이 받는 VARIANT double 배열.
    pyautocad는 array.array('d', [...]) 형태를 받는다.
    """
    flat = []
    for pt in points:
        flat.append(float(pt[0]))
        flat.append(float(pt[1]))
        flat.append(0.0)  # Z=0
    return array.array("d", flat)


# ─── 작도 ─────────────────────────────────────────────────────────────────────

def paint(data: dict, acad) -> None:
    """
    JSON 도형 명세를 AutoCAD ModelSpace에 작도하고 저장.
    """
    ms = acad.model  # ActiveDocument.ModelSpace

    drawn = 0
    for ent in data["entities"]:
        etype = ent["type"]
        try:
            if etype == "line":
                start = apoint(ent["start"][0], ent["start"][1])
                end = apoint(ent["end"][0], ent["end"][1])
                ms.AddLine(start, end)
                drawn += 1
                print(f"  [작도] line {ent['start']} → {ent['end']}")

            elif etype == "polyline":
                pts_array = coords_to_array(ent["points"])
                pline = ms.AddPolyline(pts_array)
                closed = ent.get("closed", False)
                pline.Closed = bool(closed)
                drawn += 1
                print(f"  [작도] polyline {len(ent['points'])}점, closed={closed}")

        except Exception as e:
            print(f"  [경고] entities 작도 실패 ({etype}): {e}")

    if drawn == 0:
        raise RuntimeError("작도된 도형이 없습니다.")

    # 저장
    acad.doc.Save()
    print(f"\n[완료] {drawn}개 도형 작도 및 저장 완료.")


# ─── CLI 진입점 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DWG 자동 작도 프로토타입",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예:
  python proto.py --setup
      → API 키 암호화 저장 (최초 1회)

  python proto.py "폭 3m 높이 2m 사각형 방 그려줘"
      → 자동으로 무료 모델 선택 후 작도

  python proto.py --auto "폭 3m 높이 2m 사각형 방 그려줘"
      → openrouter/auto 모델 강제 사용
        """,
    )
    parser.add_argument("text", nargs="?", help="자연어 설계 의도 (예: '폭 3m 높이 2m 사각형 방 그려줘')")
    parser.add_argument("--setup", action="store_true", help="API 키 암호화 저장 (최초 1회)")
    parser.add_argument("--auto", action="store_true", help="openrouter/auto 모델 강제 사용")
    args = parser.parse_args()

    # ── 1. 최초 설정 ──
    if args.setup:
        encrypt_api_key()
        return

    if not args.text:
        parser.print_help()
        sys.exit(1)

    # ── 2. API 키 로드 ──
    api_key = load_api_key()

    # ── 3. OpenRouter 클라이언트 ──
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE)

    # ── 4. 모델 선택 ──
    if args.auto:
        model = "openrouter/auto"
        print(f"[모델] 강제 지정: {model}")
    else:
        model = get_free_model(api_key)

    # ── 5. AutoCAD 연결 ──
    acad, summary = connect()

    # ── 6. LLM → JSON ──
    print(f"\n[LLM] 요청: {args.text}")
    data = design(args.text, summary, client, model)
    print(f"[LLM] 응답 JSON:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n")

    # ── 7. 검증 ──
    validate(data)
    print("[검증] 통과")

    # ── 8. 작도 ──
    paint(data, acad)


if __name__ == "__main__":
    main()
