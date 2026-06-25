# DWG 자동 작도 (DWG Auto-Drafting)

자연어로 입력한 설계 의도를 LLM이 JSON 도형 명세로 변환하고, **이미 실행 중인 AutoCAD의 DWG 파일에 직접 작도**하는 프로토타입입니다.

> 예) `"폭 3m 높이 2m 사각형 방 그려줘"` → AutoCAD 화면에 사각 폴리라인이 추가·저장됨

스마트팩토리 응용 프로그래밍 최종 과제 / Vibe coding 프로젝트.

---

## 동작 원리

```
자연어 입력
   │
   ▼
[connect]  실행 중인 AutoCAD에 연결 → 도면 단위·건물 영역·공간 레이블 요약 (1~3줄)
   │
   ▼
[design]   LLM 1콜 → 자연어 + 도면 요약을 JSON 도형 명세로 변환 (OpenRouter)
   │
   ▼
[validate] 필수 키/타입 검증
   │
   ▼
[paint]    pyautocad의 AddLine / AddPolyline 으로 직접 작도 후 저장
```

핵심 설계 원칙:

- **토큰 최소화** — 도면 엔티티 원문은 LLM에 절대 전달하지 않고, 1~3줄 요약(단위·건물 내부 영역·공간 레이블)만 컨텍스트로 보냅니다.
- **순수 JSON 강제** — `response_format={"type":"json_object"}` + 시스템 프롬프트로 마크다운·설명 없이 JSON만 출력하도록 강제. 파싱 실패 시 1회 재시도.
- **무료 모델 자동 선택** — OpenRouter에서 `pricing.prompt == 0 && pricing.completion == 0` 모델을 동적으로 찾아 연결하고, 429/사용 불가 시 다음 무료 모델로 1회 폴백.
- **좌표 안전장치** — LLM이 도형을 항상 건물 내부 영역 안에 배치하도록 프롬프트로 제약 (원점(0,0)·도면 밖 배치 금지).

### 지원 도형

| 타입 | 필드 | 비고 |
|------|------|------|
| `line` | `start: [x,y]`, `end: [x,y]` | 직선 |
| `polyline` | `points: [[x,y],...]`, `closed: bool` | 폴리라인 (닫힘/열림) |

> 원·치수·텍스트·레이어는 이번 범위 제외 (현재 레이어에 작도).

#### JSON 스키마 예시

```json
{
  "units": "mm",
  "entities": [
    {"type": "line", "start": [0, 0], "end": [3000, 0]},
    {"type": "polyline", "points": [[0,0],[3000,0],[3000,2000],[0,2000]], "closed": true}
  ]
}
```

---

## 요구 사항

- **OS**: Windows (AutoCAD COM 자동화이므로 Windows 전용)
- **AutoCAD**: 유료 버전 설치 및 실행 중 (DWG 네이티브 처리, ODA Converter·DXF 변환 불필요)
- **Python 3.x**
- **OpenRouter API 키** ([openrouter.ai](https://openrouter.ai))

### 패키지 설치

```powershell
pip install pyautocad openai cryptography requests flask
```

> `comtypes`는 pyautocad 의존성으로 자동 설치됩니다.

---

## 설치 / API 키 설정

API 키는 다음 우선순위로 로드됩니다.

1. 환경변수 `OPENROUTER_API_KEY`
2. 프로젝트 폴더의 `.env` 파일 (`OPENROUTER_API_KEY=...`) — **MASTER_KEY 불필요, 가장 간단**
3. 암호화 파일 `key.enc` (Fernet, `MASTER_KEY` 환경변수 필요)

### 방법 A — `.env` 파일 (가장 간단)

프로젝트 폴더에 `.env` 파일을 만들고:

```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx
```

### 방법 B — 암호화 저장 (권장, 평문 키를 디스크에 남기지 않음)

```powershell
# 1) Fernet 마스터 키 생성 (최초 1회)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2) 마스터 키를 환경변수로 설정
$env:MASTER_KEY = "<위에서 생성된 키>"

# 3) API 키 암호화 저장 → key.enc 생성
python proto.py --setup
```

> `setup_key.py`로도 가능: `$env:OPENROUTER_KEY` 와 `$env:MASTER_KEY` 를 설정한 뒤 `python setup_key.py` 실행.

> ⚠️ `.env`, `.master_key`, `key.enc` 는 `.gitignore`에 포함되어 있습니다. **절대 커밋하지 마세요.**

---

## 빠른 시작 — 초보자용 단계별 가이드

처음 실행한다면 아래 순서를 그대로 따라 하세요. (Windows 기준)

### STEP 0. 준비물 설치 (최초 1회만)

1. **Python 설치** — [python.org](https://www.python.org/downloads/)에서 Python 3.x 설치.
   설치 화면에서 **"Add Python to PATH"** 체크박스를 꼭 켜세요.
2. **AutoCAD 설치** — 유료 정식 버전이 설치되어 있어야 합니다.
3. **필수 패키지 설치** — 시작 메뉴에서 **PowerShell**을 열고 아래를 붙여넣어 실행:

   ```powershell
   pip install pyautocad openai cryptography requests flask
   ```

### STEP 1. AutoCAD를 먼저 실행하고 도면(DWG)을 연다

> ⚠️ **가장 중요!** 프로그램은 "이미 실행 중인" AutoCAD에 연결됩니다. AutoCAD가 꺼져 있으면 작동하지 않습니다.

1. AutoCAD를 실행합니다.
2. 작도할 도면(예: 함께 제공된 `A-0304_test.dwg`)을 엽니다.
3. AutoCAD 화면 아래 명령줄이 **`명령:` / `Command:` 대기 상태**인지 확인합니다.
   (팝업·경고창이 떠 있으면 모두 닫으세요. 도면 위에서 다른 명령이 실행 중이면 `Esc`를 누릅니다.)

### STEP 2. OpenRouter API 키 발급 후 입력

LLM(인공지능)을 사용하기 위해 무료 API 키가 필요합니다.

1. [openrouter.ai](https://openrouter.ai)에 가입(로그인)합니다.
2. 우측 상단 프로필 → **Keys** 메뉴 → **Create Key** 클릭 → 생성된 키(`sk-or-v1-...`)를 복사합니다.
3. 프로젝트 폴더에 있는 **`.env` 파일**을 메모장으로 열어 아래 한 줄을 붙여넣고 저장합니다.
   (`.env` 파일이 없으면 새로 만드세요. 파일 이름은 점으로 시작하는 `.env` 입니다.)

   ```
   OPENROUTER_API_KEY=sk-or-v1-여기에_복사한_키를_붙여넣기
   ```

> 💡 키는 무료입니다. `=` 양옆에 공백이나 따옴표를 넣지 마세요.
> 더 안전하게 보관하려면 아래 [API 키 설정](#설치--api-키-설정)의 방법 B(암호화 저장)를 참고하세요.

### STEP 3. UI(웹 화면) 실행

가장 쉬운 방법은 **`서버시작.bat` 파일을 더블클릭**하는 것입니다.
검은 창이 뜨면서 서버가 켜지고, 잠시 후 브라우저가 자동으로 열립니다.

> 만약 `서버시작.bat` 실행 시 "python을 찾을 수 없습니다" 같은 오류가 나면,
> 메모장으로 `서버시작.bat`을 열어 맨 아래 Python 경로
> (`C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe`)를
> 본인 PC에 설치된 경로로 바꾸거나, 간단히 `python server.py`로 수정하세요.

또는 PowerShell에서 직접 실행해도 됩니다:

```powershell
python server.py
```

→ 자동으로 브라우저에서 **`http://localhost:5000`** 가 열립니다.
(자동으로 안 열리면 브라우저 주소창에 직접 입력하세요.)

### STEP 4. 화면에서 작도하기

1. 화면 우측 상단의 **AutoCAD 연결 상태**가 초록색(연결됨)인지 확인합니다.
   빨간색이면 STEP 1을 다시 확인하고 **↺ 새로고침** 버튼을 누르세요.
2. 가운데 **설계 입력**칸에 자연어로 입력합니다. 예: `폭 3m 높이 2m 사각형 방 그려줘`
   (아래 예시 칩을 클릭해도 자동으로 입력됩니다.)
3. 모델은 기본값인 **무료 자동선택** 그대로 두면 됩니다.
   특정 모델을 쓰고 싶으면 **직접 입력 → 🔍 검색**으로 무료 모델을 고를 수 있습니다.
4. **▶ 작도 실행** 버튼을 누릅니다. (또는 `Ctrl + Enter`)
5. 오른쪽 **실행 로그**에 진행 상황이, **JSON 출력**에 생성된 도형 명세가 표시되고,
   완료되면 **AutoCAD 화면에 도형이 추가·저장**됩니다.

---

## 사용 방법 (요약 / CLI)

> 어느 방식이든 **AutoCAD를 먼저 실행하고 대상 DWG를 연 뒤** 실행하세요. pyautocad는 이미 떠 있는 인스턴스에 붙습니다. AutoCAD 명령줄이 `Command:` 대기 상태여야 합니다.

### 1) 웹 UI 방식 (`server.py`) — 권장

```powershell
python server.py        # 또는 서버시작.bat 더블클릭
```

- 자동으로 브라우저(`http://localhost:5000`)가 열립니다.
- AutoCAD 연결 상태 확인, 무료 모델 목록 조회/선택, 자연어 입력 → 작도 진행 로그를 SSE 실시간 스트림으로 표시합니다.

### 2) CLI 방식 (`proto.py`)

```powershell
# 무료 모델 자동 선택 후 작도
python proto.py "폭 3m 높이 2m 사각형 방 그려줘"

# openrouter/auto 라우터 강제 사용
python proto.py --auto "폭 3m 높이 2m 사각형 방 그려줘"
```

---

## 파일 구성

| 파일 | 설명 |
|------|------|
| `proto.py` | 핵심 파이프라인 — connect / design / validate / paint, CLI 진입점 |
| `server.py` | Flask 웹 UI 서버 (SSE 스트리밍, AutoCAD 상태/모델 API) |
| `index.html` | 웹 UI 프론트엔드 |
| `setup_key.py` | 환경변수 기반 `key.enc` 생성 스크립트 |
| `서버시작.bat` | 웹 서버 원클릭 실행 (Windows) |
| `diagnose_dwg.py` | 현재 열린 DWG의 엔티티 구조 분석 진단 도구 |
| `_check_models.py` | OpenRouter 무료 모델 목록 확인 도구 |
| `test_model.py` | LLM 응답 테스트 도구 |
| `DWG 자동 작도 PRD.md` | 제품 요구사항 정의서 (PRD) |
| `prompts.md` | 프롬프트 모음 |
| `A-0304_test.dwg` | 테스트용 도면 |
| `.env` / `key.enc` / `.master_key` | API 키 (Git 제외) |

---

## 문제 해결 (Troubleshooting)

| 증상 | 해결 |
|------|------|
| **AutoCAD 연결 실패 / 응답 거부** | AutoCAD가 완전히 실행됐는지, DWG가 열려 `Command:` 대기 상태인지 확인. 팝업·경고창을 닫고 다시 시도. (`connect()`는 RPC 거부 시 최대 5회 자동 재시도) |
| **보안 경고 팝업** | AutoCAD 보안 경고가 뜨면 '허용' 클릭 |
| **`pyautocad 미설치`** | `pip install pyautocad` |
| **API 키 오류** | `.env`에 `OPENROUTER_API_KEY` 설정 또는 `python proto.py --setup` 실행 |
| **429 (요청 한도 초과)** | 무료 모델은 분당 20요청 / 일 200요청 제한. 자동으로 다음 무료 모델로 1회 폴백하며, 잠시 후 재시도 |
| **포트 5000 사용 중** | `서버시작.bat`이 기존 서버를 재활용하거나 브라우저만 엽니다 |

---

## 범위 / 한계

이 프로토타입은 **파이프라인이 끝까지 동작하는지 검증**하는 것이 목표입니다. 정확도·예외처리·UI 완성도는 후순위입니다.

**이번 범위 제외 (다음 단계):** GUI 고도화, 기존 도면 정밀 파싱, 레이어·치수·텍스트·원, 제약 기반 좌표 변환, 수정 피드백 루프, 예외·재시도 고도화.
