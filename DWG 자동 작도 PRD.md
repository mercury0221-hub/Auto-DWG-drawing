DWG 자동 작도 프로토타입을 만들어줘. 아래 PRD를 그대로 구현하면 된다. 가볍고 단순하게, 단일 파일 중심으로 작성해줘.

## 목적
사용자가 자연어로 입력한 설계 의도를 LLM이 JSON 도형 명세로 변환하고, 이미 실행 중인 AutoCAD의 DWG 파일에 직접 작도하는 프로토타입. 정확도·예외처리·UI 완성도는 후순위이고, 파이프라인이 끝까지 동작하는지 검증하는 게 목표다.

## 환경
- OS: Windows (AutoCAD COM 자동화이므로 Windows 전용)
- AutoCAD 유료 버전 이미 설치됨
- Python 3.x 설치됨
- input/output 파일 형식: DWG (DXF 변환·ODA Converter 불필요, AutoCAD로 네이티브 처리)

## 사전 조건 (사용자가 스크립트 실행 전에 해둘 것)
- AutoCAD를 미리 실행하고 대상 DWG를 열어둔다. pyautocad는 이미 떠 있는 인스턴스에 붙는다.

## 아키텍처 (3스텝, 단일 파일 proto.py)
1. connect(): pyautocad로 실행 중 AutoCAD에 연결하고, 현재 도면의 단위/경계(extents) 요약을 1~3줄로 추출
2. design(text, summary): LLM 1콜로 자연어를 JSON 도형 명세로 변환 (요약을 컨텍스트로 전달)
3. validate(json): 필수 키/타입 체크
4. paint(json, acad): pyautocad의 AddLine / AddPolyline로 AutoCAD에 직접 작도 후 저장

CLI로 동작: 터미널에서 자연어 한 줄 입력 → 작도. GUI 없음. 별도 미리보기 없음(AutoCAD 화면이 곧 미리보기).

## LLM 연결 (OpenRouter)
- OpenRouter API를 사용한다. OpenAI 호환이므로 openai SDK에 base_url="https://openrouter.ai/api/v1"만 설정해서 쓴다.
- API 키는 암호화해서 저장하고 런타임에 복호화해 사용한다. cryptography 라이브러리(Fernet)를 사용:
  - 최초 1회: 평문 키를 입력받아 Fernet으로 암호화해 파일(예: key.enc)로 저장하는 함수
  - 실행 시: 암호화 파일을 읽어 복호화해서 메모리에서만 사용. 평문 키를 코드/로그에 남기지 않는다.
  - Fernet 암호화 키(마스터 키)는 환경변수에서 읽는다.
- 모델 선택: 무료 모델을 OpenRouter에서 동적으로 찾아 연결한다.
  - GET https://openrouter.ai/api/v1/models 로 전체 목록을 받아, pricing.prompt == "0" 이고 pricing.completion == "0" 인 모델만 필터링한다.
  - 필터된 무료 모델 중 첫 번째(또는 모델 ID로 정렬 후 첫 번째)를 자동 선택. 선택된 모델 ID를 콘솔에 출력한다.
  - 참고: 무료 모델은 분당 20요청/일 200요청 제한이 있고 초과 시 429가 난다. 429나 모델 사용 불가 시 다음 무료 모델로 1회 폴백하는 로직을 넣어줘.
- 가장 단순하게 가려면 model="openrouter/free"(무료 모델 자동 라우터)를 쓰는 옵션도 함께 지원하고, 기본값으로 둬도 좋다.

## 토큰 최소화 전략 (중요)
- 도면 컨텍스트는 요약 1~3줄만 LLM에 전달한다. 도면 엔티티 원문은 절대 넣지 않는다.
- LLM 출력은 순수 JSON만 강제한다(설명·마크다운 금지). response_format={"type":"json_object"} 사용. 시스템 프롬프트에 "JSON 외 텍스트 금지" 명시.
- 출력 파싱 실패 시 재시도는 최대 1회로 제한.

## JSON 스키마 (최소)
{
  "units": "mm",
  "entities": [
    {"type": "line", "start": [0,0], "end": [3000,0]},
    {"type": "polyline", "points": [[0,0],[3000,0],[3000,2000],[0,2000]], "closed": true}
  ]
}
- 지원 도형: line, polyline 두 가지만. 원·치수·텍스트·레이어는 이번 범위 제외(현재 레이어에 작도).
- pyautocad의 AddLine은 시작/끝 점(APoint), AddPolyline은 좌표 배열(double 배열)을 받는다. 좌표 변환 헬퍼를 작성해줘.

## 설치 패키지
- pip install pyautocad openai cryptography requests
- comtypes는 pyautocad 의존성으로 자동 설치됨

## 완료 기준
열려 있는 DWG에서 "폭 3m 높이 2m 사각형 방 그려줘" 입력 → 사각 폴리라인이 AutoCAD 화면에 추가·저장되고 눈으로 확인된다.

## 이번 범위에서 제외 (다음 단계)
GUI, 기존 도면 정밀 파싱, 레이어·치수·텍스트·원, 제약기반 좌표 변환(LLM이 절대좌표 직접 계산), 수정 피드백 루프, 예외·재시도 고도화.

먼저 전체 파일 구조와 각 함수 시그니처를 보여준 뒤, proto.py 전체 코드를 작성하고, 마지막에 키 암호화 1회 실행 방법과 스크립트 실행 순서를 알려줘.