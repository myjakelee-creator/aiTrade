# aiTrade Board Platform v1 설계 승인안

최종 갱신: 2026-07-12  
상태: 설계 저장 완료 · 페이지 연결안 승인 대기 · 성능 보호안 승인 대기 · 코드 미구현

## 1. 목적

현재 운영 또는 설계 중인 보드는 다음과 같다.

```text
StockBoard     종목 단위 실시간 관찰·후보 선발
ThemeBoard     테마 단위 돈쏠림·주도주 관찰
StrategyBoard  시간대별 매매전략 후보 선발
Future Boards  시장상황·리플레이·자동매매·리스크 등 확장
```

향후 보드가 추가되더라도 다음 원칙을 유지한다.

```text
1. 속도를 최우선한다.
2. HTML에서 시장 데이터 계산을 하지 않는다.
3. 각 기능별로 전용 엔진을 둔다.
4. OpenAPI는 collector 계층만 사용한다.
5. 각 보드의 오류와 지연을 다른 보드에서 격리한다.
```

---

## 2. 절대 원칙

### 2.1 속도 최우선

Kiwoom 실시간 수신 경로에는 보드 계산, 정렬, 파일 저장, HTTP 응답 생성을 넣지 않는다.

```text
Collector32
→ 수신·정규화·이벤트 전달만 수행

Worker64
→ 공용 종목 상태 저장

Board engines
→ worker 상태의 최소 복사본만 읽어 계산
```

worker lock은 기다리지 않는다.

```text
acquire(False)
→ 성공: 필요한 최소 필드만 복사
→ 실패: 해당 주기를 즉시 생략
```

### 2.2 HTML 계산 금지

HTML 허용 범위:

```text
서버가 준 표시값 렌더링
탭·접기·열폭·화면 크기 조절
종목 클릭과 HTS 명령 전송
사용자 선택을 서버에 전달
```

HTML 금지 범위:

```text
시장 데이터 산술계산
테마 합산
점수·등급·상태 판정
fallback 선택
거래대금 차이 계산
순위 계산
시장 데이터 정렬
추정값 생성
```

서버 payload가 다음을 완성해서 전달한다.

```text
display_order
display_text
display_class
display_state
display_score
display_basis
source_text
warning_flags
stock_code
```

### 2.3 기능별 전용 엔진

```text
StockBoardEngine
ThemeBoardEngine
StrategyBoardEngine
FutureBoardEngine
```

공용 계층은 데이터 공급, 실행 관리, cache, API만 담당한다. 보드별 점수와 상태 계산은 해당 전용 엔진만 소유한다.

---

## 3. 전체 계층 구조

```text
Kiwoom OpenAPI 32bit Collector
        ↓ TCP event
Shared Market State 64bit Worker
        ↓ 비차단 최소필드 복사
BoardDataHub
        ├─ CriticalWindowSampler
        ├─ StockBoardEngine Runner
        ├─ ThemeBoardEngine Runner
        ├─ StrategyBoardEngine Runner
        └─ FutureBoardEngine Runner
                 ↓
BoardRuntime · 독립 Cache · API · SSE
                 ↓
표시 전용 HTML
```

### 3.1 Collector32

담당:

```text
실시간 체결
실시간 호가
opt10046 강도
opt10004 잔량
프로그램 조회
분봉·일봉 조회
장마감 복구 조회
```

금지:

```text
테마 계산
전략 계산
후보 등급 계산
HTML 데이터 생성
```

### 3.2 Shared Market State

보존:

```text
현재가·등락률·거래대금·거래량
OHLC
잔량비
순간강도·5분강도
프로그램
대량체결
마지막 정상값
source·status·updated_at
```

보드 엔진은 공용 상태를 직접 수정하지 않는다.

### 3.3 BoardDataHub

역할:

```text
보드가 요구하는 최소 필드만 한 번 복사
동일 시점의 불변 snapshot 생성
source 품질·시장시간·거래일 결합
활성 보드 엔진에 snapshot 배포
```

핵심 최적화:

```text
나쁜 구조
StockBoard가 lock
ThemeBoard가 다시 lock
StrategyBoard가 다시 lock

개선 구조
BoardDataHub가 필요한 시점에 한 번만 lock
→ 최소 필드 복사
→ 즉시 lock 해제
→ 여러 엔진이 동일한 불변 snapshot 사용
```

초기에는 기존 StockBoard 경로를 즉시 바꾸지 않는다. ThemeBoard와 StrategyBoard에서 먼저 사용하고 실전 성능 검증 후 StockBoard를 단계적으로 이전한다.

### 3.4 Board Engine Runner

각 보드 엔진은 독립 runner를 가진다.

```text
입력 queue 최대 1개
새 snapshot 도착 시 이전 대기 snapshot 폐기
같은 엔진의 동시 계산 금지
완성 payload만 cache에 atomic 교체
```

장점:

```text
계산 backlog 없음
오래된 결과 계산 방지
ThemeBoard 지연이 StockBoard를 막지 않음
StrategyBoard 오류가 ThemeBoard를 막지 않음
```

### 3.5 BoardRuntime

담당:

```text
보드 등록
클라이언트 수
활성화 정책
엔진 상태
독립 cache
독립 SSE
독립 오류 상태
API 라우팅
```

BoardRuntime은 보드 계산을 하지 않는다.

---

## 4. 전용 엔진 책임

### 4.1 StockBoardEngine

```text
종목 행 생성
거래대금 순위
전일 대비 순위
대금비
후보모델
등급
Top300 → Top50 → Top20 → Top5
행 위치 정책
```

### 4.2 ThemeBoardEngine

```text
테마 가중 합산
테마 1분·5분 유입
시장점유
확산
가속도
테마 상태
주도주 점수·역할
```

### 4.3 StrategyBoardEngine

하위 전용 엔진:

```text
OpeningThemeEngine
OpeningLeaderEngine
CloseBetThemeEngine
CloseBetLeaderEngine
NextOpenExitEngine
```

### 4.4 CriticalWindowSampler

Sampler는 StrategyBoardEngine 내부가 아니라 BoardDataHub의 공용 기록 서비스로 둔다.

재사용 대상:

```text
장초반 전략
종가베팅 전략
장마감 복구
향후 리플레이보드
향후 알림보드
```

기록 항목은 primitive 숫자로 제한한다.

```text
timestamp
stock_code
price
change_rate
cumulative_volume
trade_value_eok
rank
execution_strength
strength_5m
program_net
large_trade values
OHLC
```

---

## 5. 통합 환경의 페이지 연결·이동 설계

### 5.1 표준 URL

```text
/          StockBoard
/theme     ThemeBoard
/strategy  StrategyBoard
/boards    Board Hub
```

향후 보드:

```text
/replay
/risk
/market
/automation
```

### 5.2 공통 BoardShell 상단 메뉴

모든 페이지 상단에 동일한 서버 생성 메뉴를 넣는다.

```text
StockBoard | ThemeBoard | StrategyBoard | Boards
```

표시 규칙:

```text
현재 페이지는 active 표시
준비되지 않은 페이지는 disabled 표시
주소와 라벨은 BoardRegistry가 제공
HTML별 하드코딩 중복 금지
```

BoardShell은 시장 데이터를 계산하지 않고 링크와 공통 상태만 출력한다.

### 5.3 기본 이동은 같은 탭

기본 클릭:

```text
현재 페이지 SSE 종료
같은 브라우저 탭에서 새 페이지 이동
새 페이지 SSE 연결
```

이유:

```text
세 페이지를 자동으로 동시에 열지 않음
불필요한 SSE 연결과 DOM 렌더링 방지
태블릿·PC에서 뒤로가기 사용 가능
```

### 5.4 명시적 새 창 버튼

각 페이지에는 작은 `새 창` 버튼을 둔다.

```text
기본 탭 클릭  → 같은 탭 이동
새 창 클릭    → 사용자가 명시적으로 별도 창 실행
```

자동 다중 창 실행은 금지한다.

다중 창에서도 계산은 클라이언트별로 하지 않는다.

```text
ThemeBoard 창 3개
→ ThemeBoardEngine 계산 1회
→ 같은 pre-serialized payload를 3개 SSE에 전송
```

### 5.5 Board Hub

`/boards`는 가벼운 진입 페이지다.

표시:

```text
보드명
URL
상태 READY / WAIT / ERROR
클라이언트 수
cache version
마지막 갱신 시각
열기 / 새 창
```

부하 보호:

```text
시장 행 데이터 미수신
보드별 SSE 자동 연결 금지
5초 단위 status API 또는 수동 새로고침만 사용
iframe 금지
숨은 페이지 preload 금지
```

### 5.6 cross-board 선택 상태

공용 `BoardSelectionStore`를 runtime에 둔다.

```text
selected_stock_code
selected_theme_id
strategy_mode
updated_at
source_board
```

사용 예:

```text
ThemeBoard에서 주도주 클릭
→ HTS 연결
→ selected_stock_code 저장
→ StockBoard 이동 시 해당 종목 선택 표시

StrategyBoard에서 테마 클릭
→ selected_theme_id 저장
→ ThemeBoard 이동 시 해당 테마 자동 선택
```

초기 구현에서는 종목코드만 지원하고 테마·전략 context는 후속 단계로 둔다.

### 5.7 deep link

```text
/?code=005930
/theme?theme_id=HBM_EQUIPMENT
/strategy?mode=open&code=042700
```

query는 화면 초기 선택용이다. HTML이 시장 계산을 하지는 않는다.

### 5.8 제외안

기본 통합 화면으로 iframe 3분할은 사용하지 않는다.

문제:

```text
세 페이지 SSE 동시 연결
세 페이지 DOM 동시 렌더링
태블릿 가독성 저하
숨은 iframe도 계속 계산·수신
오류 원인 분리 어려움
```

대안:

```text
같은 탭 기본 이동
사용자 선택 새 창
가벼운 /boards Hub
```

---

## 6. 속도 영향·문제점·해결방안

### 6.1 보드마다 worker lock을 반복 획득

문제:

```text
보드 수가 증가할수록 lock 진입 횟수 증가
장초반 체결 폭주 시 실시간 적용과 충돌 가능
```

해결:

```text
BoardDataHub가 활성 보드 요구 필드의 합집합을 한 번만 복사
blocking=False
lock 안에서는 primitive 읽기만 수행
정렬·계산·직렬화는 lock 밖에서 수행
```

초기 안전책:

```text
StockBoard 기존 경로는 유지
ThemeBoard·StrategyBoard만 DataHub 사용
검증 후 StockBoard 이전
```

### 6.2 활성 보드 증가에 따른 복사 필드 확대

문제:

```text
모든 보드 필드를 항상 복사하면 future board 증가 시 snapshot 비대화
```

해결:

```text
FieldDemandRegistry
각 엔진 required_fields 선언
활성 엔진 필드만 합집합 구성
비활성 보드 전용 필드는 복사하지 않음
```

공통 필드는 base snapshot에 한 번만 포함한다.

### 6.3 계산 backlog

문제:

```text
새 snapshot이 계속 들어오는데 이전 계산이 쌓이면 오래된 결과 표시
```

해결:

```text
엔진별 latest-only queue 크기 1
새 입력이 오면 대기 중인 이전 입력 교체
동시 실행 1개
완료 후 최신 입력만 재계산
```

### 6.4 클라이언트 수에 비례한 중복 계산

문제:

```text
같은 보드를 여러 창에서 열 때 탭 수만큼 계산하면 CPU 증가
```

해결:

```text
보드별 shared cache
엔진 계산은 board_id당 1회
payload JSON도 1회 직렬화
SSE client에는 같은 bytes 재사용
```

### 6.5 JSON 직렬화와 payload fan-out

문제:

```text
300행 payload를 client별로 직렬화하면 CPU·메모리 증가
```

해결:

```text
cache 갱신 시 pre-serialized bytes 생성
cache version 변경 시에만 SSE 전송
변경이 없으면 heartbeat만 전송
상세 데이터는 detail API로 분리
```

### 6.6 HTML DOM 렌더링 부담

문제:

```text
큰 테이블을 100ms마다 전체 렌더링하면 브라우저 CPU와 입력 반응 저하
```

해결:

```text
서버 계산 결과만 사용
행 key 기반 부분 갱신
보드별 최소 render interval
숨김 탭은 render 주기 낮춤
시장 데이터 계산은 HTML에서 하지 않음
```

권장 초기 render interval:

```text
StockBoard 중요행 100~250ms
ThemeBoard 500~1000ms
StrategyBoard 장초반 500ms
StrategyBoard 종가 1000~2000ms
```

이는 표시 주기이며 collector 수신 주기를 늦추지 않는다.

### 6.7 StrategyBoard 중요구간 sampler

문제:

```text
09:00~09:30 시계열 저장이 실시간 처리와 경쟁할 수 있음
```

해결:

```text
worker callback에서 기록 금지
DataHub snapshot을 ring buffer에 기록
파일 쓰기 없이 메모리 보관
구간 종료 시 background thread가 1회 저장
최대 보관 시간·종목 수 제한
```

### 6.8 파일 저장과 로그

문제:

```text
실시간 경로에서 JSON 파일 저장 시 디스크 지연
로그 폭주 시 queue와 저장 지연
```

해결:

```text
실시간 callback 파일 I/O 금지
atomic write는 background thread
마감·후보 확정 등 의미 있는 시점만 저장
상태 로그는 집계값만 기록
반복 오류는 rate limit
```

### 6.9 장마감 OpenAPI 조회 충돌

문제:

```text
분봉·강도·프로그램·잔량 scheduler가 독립 동작하면 429와 응답 지연
```

해결:

```text
Collector32의 AfterCloseRecoveryCoordinator 한 곳만 요청
한 시점 요청 1개
기존 값이 있는 필드는 요청하지 않음
종목 중복 제거
priority lane
재시도와 global backoff
프리마켓 직전 신규 요청 중단
```

보드 엔진은 OpenAPI를 직접 호출하지 않는다.

### 6.10 비활성 보드의 불필요한 계산

문제:

```text
사용하지 않는 보드가 계속 계산하면 future board가 늘수록 상시 CPU 증가
```

해결:

```text
보드별 activation_policy
```

권장:

| 보드 | 실행 조건 |
|---|---|
| StockBoard | 서버 실행 중 항상 |
| ThemeBoard | client 존재 또는 마감 보존 필요 |
| Strategy 장초반 | 08:59:50~09:30 또는 client 존재 |
| Strategy 종가 | 14:29:50~15:20 또는 client 존재 |
| Strategy 익일 | 저장 후보 존재 또는 client 존재 |
| Future Board | 각 보드가 정책 선언 |

### 6.11 Python 32비트·64비트 혼동

문제:

```text
worker64가 PATH의 32비트 python으로 실행되면 성능·메모리·역할 분리가 깨짐
```

해결:

```text
launcher에서 worker Python bit 확인
64비트가 아니면 시작 중단
collector만 고정 32비트 Python 사용
status에 collector_bits·worker_bits 표시
```

### 6.12 patch 체인 증가

문제:

```text
보드마다 worker monkey patch를 추가하면 설치 순서와 오류 추적이 어려워짐
```

해결:

```text
BoardRegistry와 api_router로 표준 등록
보드별 adapter는 명시적 register()
fail-open 격리
동일 route 중복 등록 검증
향후 기존 patch를 단계적으로 adapter로 이전
```

### 6.13 공용 선택 상태 저장 부담

문제:

```text
행 클릭마다 파일을 쓰면 디스크 I/O 증가
```

해결:

```text
메모리 BoardSelectionStore 우선
파일 persistence는 debounce 또는 의미 있는 변경 시만
HTS clipboard 전송과 데이터 계산을 분리
```

### 6.14 future board 증가

문제:

```text
보드 추가 때마다 worker 핵심 코드를 직접 수정하면 회귀 위험 증가
```

해결:

```text
BoardPlugin 계약
board_id
routes
required_fields
activation_policy
compute_interval
engine_factory
HTML path
```

새 보드는 registry 등록만으로 추가하고 collector와 기존 엔진을 수정하지 않는 것을 목표로 한다.

---

## 7. 성능 예산과 중단 기준

| 영역 | 목표 |
|---|---:|
| collector callback 추가시간 | 0ms |
| DataHub lock wait | 0ms |
| DataHub lock 복사 p95 | 1ms 이하 |
| ThemeBoard 계산 p95 | 10ms 이하 |
| StrategyBoard 계산 p95 | 10ms 이하 |
| 엔진 입력 queue | 최대 1 |
| stale 계산 backlog | 0 |
| HTML 시장 계산 | 0 |
| 신규 OpenAPI 장중 호출 | 0 |

즉시 중단 조건:

```text
dropped_trade 증가
collector pending 지속 증가
worker queue 지속 증가
09:00 stream latency 악화
DataHub lock p95 2ms 초과
보드 엔진 계산 30ms 초과 3회 연속
```

중단 시:

```text
신규 플랫폼 보드 계산 OFF
기존 StockBoard 경로 유지
ThemeBoard·StrategyBoard cache 마지막 정상값 유지
원인 진단 후 재활성화
```

---

## 8. 표준 API

기존 URL 유지:

```text
/
/theme
/strategy
/api/v2/snapshot
/api/v2/stream
/api/v2/themes/*
/api/v2/strategy/*
```

향후 표준 API:

```text
/api/v2/boards
/api/v2/boards/{board_id}/status
/api/v2/boards/{board_id}/snapshot
/api/v2/boards/{board_id}/stream
/api/v2/boards/{board_id}/detail
```

기존 API는 표준 API의 alias로 유지한다.

---

## 9. 목표 파일 구조

```text
realtime_v2/board_platform/
├─ contracts.py
├─ source_quality.py
├─ data_hub.py
├─ engine_runner.py
├─ runtime.py
├─ critical_window_sampler.py
├─ selection_store.py
├─ board_shell.py
└─ api_router.py

realtime_v2/boards/
├─ stockboard_adapter.py
├─ themeboard_adapter.py
└─ strategyboard_adapter.py

stockboard_candidate_engine.py
stockboard_theme_engine.py
stockboard_strategy_engine.py

docs/stockboard_v2.html
docs/stockboard_theme_v1.html
docs/stockboard_strategy_v1.html
```

---

## 10. 단계별 구현 계획

### 단계 A — 페이지 연결·이동

구현:

```text
BoardRegistry
공통 BoardShell 메뉴
/boards 경량 Hub
같은 탭 기본 이동
명시적 새 창
기존 / 와 /theme 연결
/strategy는 구현 전 disabled
```

금지:

```text
시장 데이터 계산 변경
collector 변경
기존 엔진 변경
자동 다중 창
iframe 통합
```

### 단계 B — 성능 계측·보호 기반

구현:

```text
worker 64비트 확인
보드별 client/cache/compute 상태
latest-only queue
pre-serialized payload
성능 예산과 자동 비활성화 장치
```

### 단계 C — Board Platform 공용 기반

구현:

```text
contracts
source quality
DataHub
EngineRunner
BoardRuntime
ThemeBoard adapter
```

### 단계 D — CriticalWindowSampler

```text
장초반·종가 중요구간 ring buffer
구간 종료 background 저장
```

### 단계 E — StrategyBoard

```text
OpeningThemeEngine
OpeningLeaderEngine
CloseBetThemeEngine
CloseBetLeaderEngine
NextOpenExitEngine
StrategyBoard HTML
```

### 단계 F — 통합 장마감 복구

```text
AfterCloseRecoveryCoordinator
분봉·강도·프로그램·OHLC·잔량비
종가 × 거래량 근사
source·quality·coverage
```

### 단계 G — StockBoard 이전

ThemeBoard·StrategyBoard 실전 검증 후 기존 StockBoard를 DataHub와 표준 Runtime으로 단계적으로 이전한다.

---

## 11. 승인 요청 1 — 페이지 연결안

승인 대상:

```text
공통 BoardShell
StockBoard | ThemeBoard | StrategyBoard | Boards
기본 같은 탭 이동
명시적 새 창
/boards 경량 Hub
iframe·자동 preload 금지
StrategyBoard 구현 전 disabled 표시
```

승인 후 단계 A만 먼저 구현하고 실제 페이지 이동과 기존 보드 무영향을 검증한다.

---

## 12. 승인 요청 2 — 성능 보호안

승인 대상:

```text
비차단 lock
FieldDemandRegistry
latest-only queue
shared cache
pre-serialized payload
비활성 보드 계산 중단
CriticalWindowSampler 메모리 ring buffer
파일 저장 background 처리
OpenAPI 중앙 coordinator
worker 64비트 강제 확인
성능 예산 초과 시 신규 기능 자동 중단
```

승인 후 단계 B와 단계 C를 분리 구현한다. 한 번에 StockBoard를 이전하지 않는다.

---

## 13. 승인 전 미구현 항목

현재 문서 저장 외에 다음 코드는 구현하지 않는다.

```text
BoardShell
/boards Hub
BoardDataHub
EngineRunner
BoardRuntime
CriticalWindowSampler
StrategyBoard
AfterCloseRecoveryCoordinator
StockBoard 이전
```

대표님 승인 후 각 단계별로 구현·검증·커밋·push한다.
