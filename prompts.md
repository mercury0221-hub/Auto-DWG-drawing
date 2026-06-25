# DWG 자동 작도 — 개발 프롬프트 기록

> 자연어 → LLM → JSON 도형 명세 → 실행 중인 AutoCAD에 직접 작도하는 프로토타입을
> 만들면서 AI 코딩 어시스턴트에게 전달한 요청들을 시간 순으로 기록한 문서입니다.
> 각 항목은 **그 시점에 한 요청**과 **그 결과로 만들어지거나 바뀐 것**을 함께 적습니다.

---

## 개발 단계 한눈에 보기

| # | 단계 | 핵심 산출물 |
|---|---|---|
| 0 | 프로젝트 발주 (PRD) | `DWG 자동 작도 PRD.md` |
| 1 | 골격 구현 (3스텝 파이프라인) | `proto.py` connect/design/validate/paint |
| 2 | AutoCAD 연결 안정화 | RPC 재시도, lazy 순회 |
| 3 | 도면 구조 진단 | `diagnose_dwg.py` |
| 4 | 도면 컨텍스트 고도화 | `summarize_drawing()` 건물영역·레이블 |
| 5 | 무료 모델 응답 견고화 | `_extract_json()`, max_tokens↑ |
| 6 | 모델 점검·폴백 | `test_model.py`, `_check_models.py`, `design()` 폴백 |
| 7 | API 키 간소화 | `.env` 우선 + `_load_dotenv()` |
| 8 | 웹 UI 추가 | `server.py` + `index.html` (SSE) |
| 9 | UI 고도화 | 모델 검색·상태표시·리사이저·하이라이트 |
| 10 | 원클릭 실행 | `서버시작.bat`, 브라우저 자동오픈 |
| 11 | 문서화 | `README.md`, `manual.md`, `prompts.md` |

---

## 0단계 — 프로젝트 발주 (PRD 전달)

**요청**

> DWG 자동 작도 프로토타입을 만들어줘. 아래 PRD를 그대로 구현하면 된다. 가볍고 단순하게,
> 단일 파일 중심으로 작성해줘. (목적: 자연어 → LLM → JSON 도형 명세 → 실행 중인 AutoCAD에
> 직접 작도. 정확도·예외처리·UI는 후순위, 파이프라인 끝까지 동작 검증이 목표.)
> 아키텍처는 connect / design / validate / paint 3~4스텝, 단일 파일 `proto.py`.
> LLM은 OpenRouter(OpenAI 호환), 무료 모델 동적 선택, 키는 Fernet 암호화.
> 토큰 최소화: 도면 요약 1~3줄만 전달, `response_format={"type":"json_object"}`로 순수 JSON 강제.
> 먼저 파일 구조와 함수 시그니처를 보여준 뒤 전체 코드를 작성해줘.

**결과** — 전체 프로젝트의 출발점. PRD 문서(`DWG 자동 작도 PRD.md`)를 기준 사양으로 확정.

---

## 1단계 — proto.py 골격 구현

**요청**

> PRD대로 `proto.py` 한 파일에 다음을 만들어줘.
> ① `connect()` — pyautocad로 실행 중 AutoCAD에 붙고 도면 단위/범위 요약.
> ② `design(text, summary)` — OpenRouter 1콜로 자연어를 JSON 도형 명세로 변환.
> ③ `validate(json)` — 필수 키/타입 체크.
> ④ `paint(json, acad)` — `AddLine`/`AddPolyline`로 직접 작도 후 `Save()`.
> 좌표 변환 헬퍼(`APoint`, double 배열)도 넣고, `argparse` CLI로 자연어 한 줄 받게 해줘.
> 무료 모델은 `/models`에서 `pricing.prompt=="0" && completion=="0"`로 필터링해 첫 번째 선택.

**결과**
- `connect()` / `summarize_drawing()` / `design()` / `validate()` / `paint()` / `main()` 골격.
- `apoint()`, `coords_to_array()` 좌표 헬퍼 (`array.array('d', ...)`).
- `get_free_model()` — 문자열 `"0"` 비교로 무료 모델 필터.
- `SYSTEM_PROMPT` 상수 + `response_format={"type":"json_object"}`.
- `--auto` 플래그로 `openrouter/auto` 라우터 강제 사용 지원.

---

## 2단계 — AutoCAD 연결 안정화

**상황** — 실제 건축 도면(`A-0304_test.dwg`)에 붙이자 엔티티가 7천 개를 넘었고,
`list(ms)`로 한 번에 읽는 순간 `RPC_E_CALL_REJECTED`("피호출자가 호출을 거부했습니다")가 떴다.

**요청**

> 실도면에 붙였더니 엔티티가 7천 개가 넘어서 `list(ms)`로 한꺼번에 읽으면 COM이 호출을 거부해.
> 연결과 도면 순회 둘 다 재시도 로직을 넣고, 엔티티는 한 번에 말고 하나씩 lazy하게 순회하도록 바꿔줘.
> 무한 루프 방지 상한도 두고, 연결 실패 시 사용자에게 체크리스트(팝업 닫기/명령 대기 상태)도 보여줘.

**결과**
- `connect()` — `RPC_CALL_REJECTED = -2147418111` 감지, 최대 5회 재시도(`RETRY_DELAYS=[2,3,4,5,6]`).
- `summarize_drawing()` — `for ent in ms:` lazy 순회 + `MAX_ITERS = 8000` 상한 + RPC 거부 3회 재시도.
- `_print_autocad_tips()` 연결 체크리스트.
- `comtypes.CoInitialize()` COM 초기화(이후 Flask 멀티스레드 대비).

---

## 3단계 — 도면 구조 진단 도구 제작

**상황** — 작도는 되는데 도형이 원점 근처나 도면 밖 등 엉뚱한 위치에 그려졌다.
LLM에 보내는 요약이 부실한 게 원인으로 보여, 먼저 도면 구조부터 정확히 파악하기로 했다.

**요청**

> 작도 결과가 엉뚱한 위치에 떨어져. LLM에 보낼 요약을 고치기 전에, 현재 열린 DWG가 실제로 어떤
> 구조인지 분석하는 진단 스크립트를 따로 만들어줘. 엔티티 타입별 개수, 레이어 목록,
> 닫힌 폴리라인 bbox(면적순), 블록·텍스트 샘플, 그리고 "지금 보내는 요약 vs 개선 가능한 요약"을
> 비교해서 출력해줘.

**결과** — `diagnose_dwg.py`
- 엔티티 타입 카운트 / 레이어 목록 / 닫힌 폴리라인 bbox 면적순 / 블록·텍스트 샘플.
- `[6] 현재 LLM 컨텍스트 vs 개선 가능 컨텍스트` 비교 출력 → 다음 단계 설계의 기준이 됨.

---

## 4단계 — 도면 컨텍스트 고도화 (좌표 안전장치)

**상황** — 진단 결과, 이 도면은 `A-WALL` 계열 레이어의 Line/Polyline이 건물 벽이고,
타이틀블록이 음수 Y 하단에 있어 전체 범위(EXTMIN/EXTMAX)만으로는 건물 위치를 잡을 수 없었다.

**요청**

> 진단해보니 `A-WALL`/`A-CON`/`A-WALL-MBND` 레이어가 건물 벽이야. `summarize_drawing()`을 고쳐서
> ① 이 레이어 좌표로 건물내부영역(bbox)과 중심점을 계산해 요약에 넣고, ② MText/Text에서 공간 레이블
> ("화장실" 등)과 좌표를 추출해줘. 벽 레이어를 못 잡으면 EXTMIN/EXTMAX로 폴백하되, 타이틀블록이 보통
> 음수 Y 하단에 있으니 하단 35%는 잘라내. 시스템 프롬프트에는 "모든 좌표는 건물내부영역 안에,
> (0,0)이나 도면 밖 금지, 중심점을 기본 배치점으로, 레이블 매칭 시 그 좌표 중심으로"를 강하게 박아줘.

**결과**
- `summarize_drawing()` — `WALL_LAYERS = {"A-WALL","A-CON","A-WALL-MBND"}` 기반 건물영역+중심+크기 산출.
- MText 서식코드 제거(`re.sub`) 후 공간레이블 `"이름"@(x,y)` 최대 15개.
- 폴백: `ey_floor = EXTMIN_Y + (range)*0.35` (음수 Y일 때) — 타이틀블록 제외.
- `SYSTEM_PROMPT`에 `CRITICAL RULE … 건물내부영역 … NEVER (0,0) … room label` 좌표 제약 추가.

---

## 5단계 — 무료 모델 응답 견고화 (JSON 파싱)

**상황** — 무료 모델들이 `response_format`을 줘도 ```json 코드펜스```나 설명문을 덧붙이고,
복잡한 도형에서는 JSON이 중간에 잘려 파싱이 깨졌다.

**요청**

> 무료 모델이 코드펜스나 설명문을 붙이고 가끔 JSON이 잘려서 파싱이 깨져. ① 응답에서 코드펜스·앞뒤
> 설명을 벗겨내고 첫 `{`~마지막 `}`만 잘라 파싱하는 함수를 만들고, ② 잘림 방지로 `max_tokens`를
> 2048로 키워줘. ③ 모델이 실제로 뭘 뱉었는지 디버깅용으로 원문 일부를 로그에 찍어줘.

**결과**
- `_extract_json()` — 코드펜스 정규식 제거 → 직접 파싱 → 실패 시 `{`~`}` 구간만 재시도.
- `_call_llm()` `max_tokens=2048`로 상향.
- `[LLM 원문]` 프리뷰 로그(최대 500자)로 파싱 실패 시 원문 확인.

---

## 6단계 — 모델 점검 도구 & 자동 폴백

**상황** — 기본값으로 쓸 무료 모델을 정해야 했고, 무료 모델은 분당 20·일 200 제한이라 429가 잦았다.

**요청 (모델 점검)**

> 어떤 무료 모델을 기본값으로 쓸지 정하려 해. OpenRouter 모델 목록에서 가격 구조(`pricing` 필드가
> 문자열 `"0"`인지)와 `:free` 접미사를 확인하는 작은 스크립트(`_check_models.py`)와, 특정 모델(`nemotron`)이
> 존재하는지 + 텍스트→JSON 변환 + 이미지(vision) 인식까지 테스트하는 `test_model.py`를 만들어줘.

**요청 (자동 폴백)**

> 무료 모델은 429가 잘 떠. `design()`에서 지정 모델이 실패(파싱 실패 또는 429/사용불가)하면 다른 무료
> 모델로 순차 폴백하게 해줘. 최대 4개까지 시도하고 다 실패하면 명확한 에러를 던져.

**결과**
- `_check_models.py` — `pricing` 샘플 / `:free` 접미사 / `prompt=="0"` 개수 출력.
- `test_model.py` — 모델 존재 확인 + CAD→JSON 텍스트 테스트 + 2×2 PNG 합성 vision 테스트.
- `design()` — `MAX_ATTEMPTS=4`, 지정 모델 + 무료 모델 풀 순차 시도, `_free_models()` 헬퍼.
- `DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"` 확정.

---

## 7단계 — API 키 관리 간소화 (.env 우선)

**상황** — Fernet 암호화 + `MASTER_KEY` 환경변수 방식이 비개발자가 쓰기엔 너무 번거로웠다.

**요청**

> Fernet + `MASTER_KEY` 방식이 비개발자한테 너무 번거로워. 프로젝트 폴더 `.env`에
> `OPENROUTER_API_KEY=...` 한 줄만 넣으면 자동으로 읽히게 해줘. 기존 `key.enc` 방식은 지우지 말고
> 하위호환(2순위)으로 남겨. import 시 `.env`를 자동 로드하되 이미 설정된 환경변수는 덮어쓰지 마.

**결과**
- `_load_dotenv()` — 모듈 import 시 즉시 실행, `KEY=VALUE` 파싱(주석/따옴표 처리), 기존 env 보존.
- `load_api_key()` — 1순위 `OPENROUTER_API_KEY`(.env/환경변수), 2순위 `key.enc + MASTER_KEY`(레거시).
- `.env.example` 템플릿 추가, `setup_key.py`는 레거시 도구로 유지.

---

## 8단계 — 웹 UI 추가 (Flask + SSE)

**상황** — CLI가 불편해 웹 UI를 붙이기로 했다. `proto.py` 로직은 그대로 재사용하는 게 조건.

**요청**

> CLI가 불편하니 웹 UI를 붙이자. `proto.py` 로직은 그대로 재사용하고 `server.py`(Flask)를 새로 만들어줘.
> ① `/api/status` AutoCAD 연결 상태, ② `/api/models` 무료 모델 목록, ③ `/api/draw`는 작도 진행 로그를
> SSE로 실시간 스트리밍. 핵심: `proto.py`의 `print()`를 건드리지 말고 그대로 웹 로그로 흘려보내고 싶어 —
> 요청 스레드별로 stdout을 가로채 해당 요청 큐로 보내는 디스패처를 만들어줘. Flask는 별도 스레드라
> COM 초기화도 필요해.

**결과**
- `server.py` — `DispatchingStream`(스레드 로컬 큐로 `sys.stdout` 라우팅, Flask import 前 전역 설치).
- `/api/draw` — 작도 워커 스레드 + `queue.Queue` + SSE `generate()`(90초 타임아웃), 구조화 이벤트(`log/model/summary/json/success/error/done`).
- 각 스레드 `comtypes.CoInitialize()`, `proto.connect/design/validate/paint` 재사용.

---

## 9단계 — 프론트엔드 고도화 (index.html)

**요청** *(UI를 여러 차례 다듬으며 반복한 요청 모음)*

> 화면을 GitHub 다크 테마로 만들어줘. 왼쪽=입력/모델선택, 오른쪽=실행로그+JSON 뷰어 2분할.
> ① 헤더에 AutoCAD 연결 상태 점(초록/빨강/노랑 깜빡임)과 6초 폴링, ② 예시 명령 칩, ③ 모델 모드 3종
> (무료자동 / openrouter/auto / 직접입력), ④ "🔍 검색"으로 OpenRouter 무료 모델 목록을 오버레이로 띄워
> 필터·선택, ⑤ JSON 구문 강조(정규식 말고 토크나이저로), ⑥ 좌우 패널 너비 드래그 리사이저(더블클릭 복원),
> ⑦ Ctrl+Enter 실행. 그리고 `/api/models`는 무료 감지를 더 폭넓게(`"0"` 문자열 + `float==0` + `:free`) 해줘.

**결과**
- `index.html` — 다크 테마 CSS 변수, 상태 점, 예시 칩, 모델 검색 오버레이(필터/뱃지 vision·context),
  JSON 토크나이저 하이라이트, 리사이저, `fetch`+`ReadableStream`으로 SSE 수신(POST 스트리밍).
- `server.py /api/models` — `_is_free()` 3중 판정(문자열·`float==0`·`:free`) + safety/guard/moderation/embed 제외.

---

## 10단계 — 원클릭 실행 & 배포 편의

**요청**

> 비개발자도 더블클릭 한 번으로 쓰게 해줘. `서버시작.bat`은 `.env` 존재를 확인하고, 포트 5000이 이미
> 쓰이면 새 서버를 띄우지 말고 브라우저만 열어줘(UTF-8 콘솔). 서버는 준비되면 브라우저를 자동으로 열고,
> 실행 중일 때 쓰는 바로가기(`DWG작도UI.url`)도 만들어줘.

**결과**
- `서버시작.bat` — `chcp 65001`, `.env` 확인, `netstat`로 포트 5000 점유 시 브라우저만 오픈.
- `server.py:_open_browser()` — 서버 준비될 때까지 폴링 후 `webbrowser.open` (별도 데몬 스레드).
- `DWG작도UI.url` 바로가기 추가.

---

## 11단계 — 문서화

**요청**

> 마지막으로 문서를 정리하자. ① `README.md` — 동작 원리·설치·CLI/웹 사용·파일 구성·트러블슈팅,
> ② `manual.md` — 비개발자용 초보 가이드(용어 설명 포함), ③ `prompts.md` — 개발 프롬프트 기록과
> 런타임 프롬프트 정리, ④ `.gitignore` — `.env`/`key.enc`/`*.bak`/`__pycache__`/`.claude/` 제외.

**결과** — `README.md`, `manual.md`, `prompts.md`, `.gitignore`, `.env.example`.

---

## 부록 A — 런타임 프롬프트 원문

> 프로그램이 *실행 중* 실제로 LLM에 전송하는 프롬프트/포맷.

### A-1. 시스템 프롬프트 (`proto.py > SYSTEM_PROMPT`)

```
You are a CAD drawing assistant.
Output ONLY valid JSON, no markdown, no explanation, no extra text.
Schema: {"units": "mm", "entities": [{"type": "line", "start": [x,y], "end": [x,y]} |
{"type": "polyline", "points": [[x,y],...], "closed": true|false}]}.
Use millimeters for all coordinates.
CRITICAL RULE: The context message includes '건물내부영역: (x1,y1)~(x2,y2)  중심:(cx,cy)'.
ALL entity coordinates MUST be placed strictly inside this bounding box.
Use the center coordinates as the default placement point unless a room label matches.
NEVER generate coordinates at (0,0) or outside the 건물내부영역 range.
If 공간레이블 has a matching room, center the entity on that label's coordinate instead.
Do not output anything except the JSON object.
```

### A-2. user 메시지 구조 (`_call_llm()`)

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user",   "content": "도면 컨텍스트:\n{summary}\n\n요청: {text}"},
]
# response_format={"type":"json_object"}, max_tokens=2048, temperature=0
```

### A-3. 도면 컨텍스트(`summarize_drawing()`) 출력 예시

```
도면파일: A-0304_test.dwg  단위:mm  전체범위:(-1302,-29132)~(147047,92065)
건물내부영역: (49942,25166)~(128742,76966)  중심:(89342,51066)  크기:78800×51800mm
좌표기준: 도형은 반드시 건물내부영역 범위 안에 배치할 것.
공간레이블: 없음
```

### A-4. JSON 출력 스키마

```json
{
  "units": "mm",
  "entities": [
    {"type": "line", "start": [x, y], "end": [x, y]},
    {"type": "polyline", "points": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "closed": true}
  ]
}
```

### A-5. 사용 예시 (자연어 입력 → 기대 출력)

| 입력 | LLM 예상 출력 |
|---|---|
| `폭 3000mm 높이 2000mm 사각형 방 그려줘` | closed polyline, 건물 중심 기준 |
| `화장실 위치에 작은 방 그려줘` | 공간레이블 "화장실" 좌표 중심 |
| `건물 입구에 선 그어줘` | 건물 외곽 근처 line |

---

## 부록 B — 주요 설계 결정과 이유

| 결정 | 이유 |
|---|---|
| `for ent in ms:` (lazy) + `MAX_ITERS=8000` | 7000개 `list()` 일괄 마샬링이 RPC 호출을 거부시킴 |
| `RETRY_DELAYS=[2,3,4,5,6]` 점증 대기 | AutoCAD가 바쁠 때 한두 번 더 기다리면 연결됨 |
| 폴백 시 하단 35% 컷 | 타이틀블록(음수 Y)을 건물로 오인해 도형이 제목란에 그려짐 |
| `_extract_json`의 `{`~`}` 컷 | 무료 모델이 설명문/코드펜스를 덧붙임 |
| `max_tokens` 2048 | 복잡한 도형에서 JSON이 중간에 잘림 |
| `.env` 1순위 / Fernet 2순위 | MASTER_KEY 방식이 비개발자에게 과함 |
| stdout 디스패처 | proto의 `print`를 고치지 않고 웹 로그로 재사용 |
| `/api/models` 3중 무료 판정 | 단순 `"0"` 비교는 무료 모델을 적게 잡음 |

---

*최종 업데이트: 2026-06-25*
