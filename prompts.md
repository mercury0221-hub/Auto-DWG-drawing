# DWG 자동 작도 — 프롬프트 모음

## 1. 시스템 프롬프트 (SYSTEM_PROMPT)

LLM에 전달되는 역할 및 출력 규칙 정의.  
`proto.py > SYSTEM_PROMPT` 상수에 하드코딩.

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

---

## 2. LLM 호출 메시지 구조

`proto.py > _call_llm()` 에서 실제로 전송하는 messages 배열.

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user",   "content": "도면 컨텍스트:\n{summary}\n\n요청: {text}"},
]
```

- `{summary}` : `summarize_drawing()` 가 반환한 다중 줄 문자열 (아래 섹션 3 참조)
- `{text}`    : 웹 UI 또는 CLI에서 입력한 자연어 명령

---

## 3. 도면 컨텍스트 (summarize_drawing 출력 형식)

AutoCAD에서 추출한 건물 정보를 LLM user 메시지 앞에 prepend.  
`proto.py > summarize_drawing()` 함수가 생성.

```
도면파일: A-0304_test.dwg  단위:mm  전체범위:(-1302,-29132)~(147047,92065)
건물내부영역: (49942,25166)~(128742,76966)  중심:(89342,51066)  크기:78800×51800mm
좌표기준: 도형은 반드시 건물내부영역 범위 안에 배치할 것.
공간레이블: 없음
```

### 추출 규칙

| 항목 | 추출 방법 |
|---|---|
| 도면파일 | `doc.Name` |
| 단위 | `doc.GetVariable("INSUNITS")` → 단위 문자 변환 |
| 전체범위 | `doc.GetVariable("EXTMIN")` / `EXTMAX` |
| 건물내부영역 | A-WALL, A-CON, A-WALL-MBND 레이어 Line/Polyline endpoint bounding box |
| 공간레이블 | AcDbMText / AcDbText TextString (서식 코드 제거 후) |

### 엔티티 순회 방식

- **lazy 순회**: `for ent in ms:` 사용 — `list(ms)` 일괄 로드 금지
  - `list(ms)` 는 7000+ 엔티티 일괄 COM 마샬링 → `RPC_E_CALL_REJECTED` 유발
- **MAX_ITERS = 8000**: 8000개 초과 시 순회 중단 (무한 루프 방지)
- **RPC 재시도**: 순회 중 RPC 거부 발생 시 3회 재시도, 회차당 3초 대기

### 폴백 규칙 (A-WALL 취득 실패 시)

전체범위에서 하단 35% 제거 (타이틀 블록 제외):

```
ey_floor = EXTMIN_Y + (EXTMAX_Y - EXTMIN_Y) × 0.35   (EXTMIN_Y < 0 인 경우)
건물내부영역: (EXTMIN_X, ey_floor) ~ (EXTMAX_X, EXTMAX_Y)
```

중심 좌표도 계산하여 context에 포함 → LLM이 실제 건물 위치에 배치.

---

## 4. JSON 출력 스키마

```json
{
  "units": "mm",
  "entities": [
    {
      "type": "line",
      "start": [x, y],
      "end": [x, y]
    },
    {
      "type": "polyline",
      "points": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]],
      "closed": true
    }
  ]
}
```

### 검증 규칙 (`validate()`)

| 항목 | 검사 내용 |
|---|---|
| 최상위 | `dict` 타입, `entities` 키 존재 |
| entities | `list` 타입, 1개 이상 |
| type = "line" | `start`, `end` 각각 길이 ≥ 2 인 배열 |
| type = "polyline" | `points` 길이 ≥ 2 인 배열, 각 point 길이 ≥ 2 |

---

## 5. 모델 설정 및 호출 흐름

### 기본 LLM 호출 파라미터

| 파라미터 | 값 |
|---|---|
| **기본 모델** | `nvidia/nemotron-3-super-120b-a12b:free` |
| **API Base** | `https://openrouter.ai/api/v1` |
| **max_tokens** | `512` (JSON 응답 잘림 방지) |
| **temperature** | `0` (결정론적 출력) |
| **response_format** | `{"type": "json_object"}` |

### 모델 선택 우선순위 (`server.py > /api/draw`)

```
1. use_auto = true  →  "openrouter/auto" (유료 포함, OpenRouter 자동 선택)
2. custom_model 지정  →  직접 입력한 모델 ID 사용
3. 기본  →  get_free_model() 호출
```

### 무료 모델 감지 방식 비교

| 함수 | 감지 조건 |
|---|---|
| `proto.get_free_model()` | `pricing.prompt == "0"` AND `pricing.completion == "0"` (문자열 비교) |
| `server.py /api/models` | 위 조건 + `float(pricing.prompt) == 0` + `:free` 접미사 (3-way, 더 폭넓게 탐지) |

`/api/models` 엔드포인트가 더 많은 무료 모델을 반환 (약 26개 vs 문자열 비교만 시 더 적음).

### JSON 파싱 실패 시 재시도 (`design()`)

```
1회 시도 → JSONDecodeError → 1회 재시도 → 실패 시 RuntimeError
```

### 429 / 모델 사용 불가 시 자동 폴백 (`design()`)

```
LLM 호출 실패 (429 또는 "unavailable")
  → _next_free_model() 로 다음 무료 모델 조회
  → 새 모델로 1회 재시도
  → 그래도 실패 시 RuntimeError
```

---

## 6. API 키 로드 순서 (`load_api_key()`)

```
1순위: 환경변수 OPENROUTER_API_KEY  (서버 시작 시 .env 파일에서 자동 주입)
2순위: key.enc + MASTER_KEY         (레거시 Fernet 암호화, 하위 호환용)
실패: 오류 메시지 출력 후 sys.exit(1)
```

### `.env` 자동 로드 (`_load_dotenv()`)

- `proto.py` import 시 즉시 실행
- 프로젝트 폴더의 `.env` 파일을 파싱 → `os.environ` 에 주입
- 이미 설정된 환경변수는 덮어쓰지 않음
- 형식: `KEY=VALUE` (주석 `#`, 빈 줄 무시, 따옴표 strip)

### API 키 변경 방법

```
1. .env 파일에서 OPENROUTER_API_KEY 값을 새 키로 교체
2. 서버 재시작 (서버시작.bat)
```

---

## 7. 사용 예시 (자연어 입력)

| 입력 | LLM 예상 출력 |
|---|---|
| `폭 3000mm 높이 2000mm 사각형 방 그려줘` | closed polyline, 건물 중심 기준 |
| `화장실 위치에 작은 방 그려줘` | 공간레이블 "화장실" 좌표 중심 |
| `건물 입구에 선 그어줘` | 건물 외곽 근처 line |

---

*최종 업데이트: 2026-06-06*
