"""
server.py — DWG 자동 작도 웹 UI 서버
실행: python server.py
브라우저: http://localhost:5000
"""
import sys
import os
import json
import queue
import threading

# ─── 스레드별 stdout 디스패처 (Flask import 전에 전역 설치) ──────────────────
# 각 요청 스레드가 자신의 큐에 print를 라우팅, 미등록 스레드는 원본 stdout으로 폴백

_original_stdout = sys.stdout

class DispatchingStream:
    def __init__(self, fallback):
        self._local = threading.local()
        self._fallback = fallback

    def register(self, q: queue.Queue):
        self._local.queue = q

    def unregister(self):
        self._local.queue = None

    def write(self, msg: str):
        q = getattr(self._local, 'queue', None)
        if q is not None and msg.strip():
            q.put({'type': 'log', 'payload': msg.strip()})
        else:
            self._fallback.write(msg)

    def flush(self):
        self._fallback.flush()

    def isatty(self):
        return False

_dispatch = DispatchingStream(_original_stdout)
sys.stdout = _dispatch

# ─── 이후 import ─────────────────────────────────────────────────────────────
from flask import Flask, request, Response, send_from_directory  # noqa: E402
from openai import OpenAI  # noqa: E402

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)
import proto  # noqa: E402   ← proto 임포트 시 _load_dotenv() 자동 실행

app = Flask(__name__)


# ─── 라우트 ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(_BASE_DIR, 'index.html')


@app.route('/api/status')
def api_status():
    """AutoCAD 연결 상태 확인. RPC_E_CALL_REJECTED 시 재시도."""
    _dispatch.register(queue.Queue())
    try:
        import comtypes, time
        comtypes.CoInitialize()
    except Exception:
        pass

    RPC_CALL_REJECTED = -2147418111

    def _try_connect():
        from pyautocad import Autocad
        acad = Autocad(create_if_not_exists=False)
        return acad, acad.doc.Name

    try:
        for attempt in range(3):
            try:
                acad, doc_name = _try_connect()
                break
            except Exception as e:
                code = e.args[0] if e.args else None
                if (code == RPC_CALL_REJECTED or "피호출자" in str(e)) and attempt < 2:
                    time.sleep(2)
                    continue
                raise
        else:
            return {'connected': False, 'error': 'AutoCAD 응답 거부 — 팝업 창 닫고 다시 시도'}

        unit_map = {0: "없음", 1: "inch", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
        try:
            unit_str = unit_map.get(int(acad.doc.GetVariable("INSUNITS")), "알 수 없음")
        except Exception:
            unit_str = "알 수 없음"

        try:
            emin = acad.doc.GetVariable("EXTMIN")
            emax = acad.doc.GetVariable("EXTMAX")
            extents = f"({emin[0]:.0f}, {emin[1]:.0f}) ~ ({emax[0]:.0f}, {emax[1]:.0f})"
        except Exception:
            extents = "취득 불가"

        return {'connected': True, 'doc_name': doc_name, 'units': unit_str, 'extents': extents}

    except ImportError:
        return {'connected': False, 'error': 'pyautocad 미설치 — pip install pyautocad'}
    except Exception as e:
        code = e.args[0] if hasattr(e, 'args') and e.args else ''
        if code == RPC_CALL_REJECTED or "피호출자" in str(e):
            return {'connected': False,
                    'error': 'AutoCAD 응답 거부 — 팝업창을 닫고 명령 대기(Command:) 상태에서 새로고침'}
        return {'connected': False, 'error': str(e)}
    finally:
        _dispatch.unregister()


@app.route('/api/models')
def api_models():
    """OpenRouter 무료 모델 목록을 반환한다."""
    try:
        import requests as _req
        api_key = proto.load_api_key()
        resp = _req.get(
            f"{proto.OPENROUTER_BASE}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])

        EXCLUDE = {"safety", "guard", "moderation", "embed"}

        def _is_free(m):
            mid = m.get("id", "")
            p   = m.get("pricing", {})
            # pricing 필드가 "0" 문자열인 경우
            if p.get("prompt") == "0" and p.get("completion") == "0":
                return True
            # pricing 필드가 숫자 0인 경우
            try:
                if float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0:
                    return True
            except (ValueError, TypeError):
                pass
            # :free 접미사 (OpenRouter 무료 tier)
            if mid.endswith(":free"):
                return True
            return False

        free = []
        for m in data:
            mid = m.get("id", "")
            if not _is_free(m):
                continue
            if any(x in mid.lower() for x in EXCLUDE):
                continue
            arch = m.get("architecture", {})
            free.append({
                "id":       mid,
                "name":     m.get("name", mid),
                "context":  m.get("context_length", 0),
                "modality": arch.get("modality", "text"),
            })

        free.sort(key=lambda x: x["id"])
        return {"models": free, "count": len(free)}

    except SystemExit:
        return {"error": "MASTER_KEY 미설정 — 서버시작.bat 으로 재시작하거나\n$env:MASTER_KEY 를 설정 후 재시작하세요", "models": []}
    except Exception as e:
        return {"error": str(e), "models": []}


@app.route('/api/draw', methods=['POST'])
def api_draw():
    """자연어 → 작도 파이프라인 (SSE 스트림)."""
    body = request.get_json(force=True, silent=True) or {}
    text         = (body.get('text')  or '').strip()
    use_auto     = bool(body.get('auto', False))
    custom_model = (body.get('model') or '').strip()

    if not text:
        return {'error': '입력 텍스트가 없습니다.'}, 400

    log_q: queue.Queue = queue.Queue()

    def put(event_type: str, payload=''):
        """구조화된 이벤트를 큐에 직접 삽입 (dispatch 우회)."""
        log_q.put({'type': event_type, 'payload': payload})

    def run():
        _dispatch.register(log_q)
        try:
            import comtypes
            comtypes.CoInitialize()  # 작도 스레드에서도 COM 초기화
        except Exception:
            pass
        try:
            # 1. API 키 로드
            api_key = proto.load_api_key()

            # 2. OpenRouter 클라이언트
            client = OpenAI(api_key=api_key, base_url=proto.OPENROUTER_BASE)

            # 3. 모델 선택
            if use_auto:
                model = "openrouter/auto"
                put('log', f'[모델] 강제 지정: {model}')
            elif custom_model:
                model = custom_model
                put('log', f'[모델] 직접 지정: {model}')
            else:
                model = proto.get_free_model(api_key)
            put('model', model)

            # 4. AutoCAD 연결
            acad, summary = proto.connect()
            put('summary', summary)

            # 5. LLM → JSON
            json_data = proto.design(text, summary, client, model)
            put('json', json.dumps(json_data, ensure_ascii=False, indent=2))

            # 6. 검증
            proto.validate(json_data)

            # 7. 작도
            proto.paint(json_data, acad)

            put('success', '작도 완료')

        except SystemExit as e:
            put('error', f'초기화 실패 — 키 설정을 확인하세요 (exit {e.code})')
        except Exception as e:
            put('error', str(e))
        finally:
            _dispatch.unregister()
            put('done', '')  # 스트림 종료 신호

    t = threading.Thread(target=run, daemon=True)
    t.start()

    def generate():
        while True:
            try:
                event = log_q.get(timeout=90)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get('type') == 'done':
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'payload': '타임아웃 (90초 초과)'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'payload': ''})}\n\n"
                break
        t.join(timeout=5)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


# ─── 진입점 ──────────────────────────────────────────────────────────────────

def _open_browser():
    """Flask가 준비된 뒤 브라우저를 자동 오픈한다."""
    import urllib.request, webbrowser, time
    url = 'http://localhost:5000'
    for _ in range(20):          # 최대 4초 대기
        time.sleep(0.2)
        try:
            urllib.request.urlopen(url, timeout=1)
            webbrowser.open(url)
            return
        except Exception:
            continue


if __name__ == '__main__':
    _original_stdout.write("=" * 50 + "\n")
    _original_stdout.write("  DWG 자동 작도 서버\n")
    _original_stdout.write("  브라우저: http://localhost:5000\n")
    _original_stdout.write("=" * 50 + "\n")
    _original_stdout.flush()

    # 서버 준비 후 브라우저 자동 오픈 (별도 스레드)
    threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
