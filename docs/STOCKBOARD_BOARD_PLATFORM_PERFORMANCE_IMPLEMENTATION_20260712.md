# StockBoard Board Platform 공통 상단·속도·시장수급 복구 구현

최종 갱신: 2026-07-12  
상태: 구현 완료 · PC1 구조/복구 검증 완료 · 정규장 09:00 실전 성능 검증 필요

## 1. 목적과 절대 원칙

StockBoard, ThemeBoard, StrategyBoard와 향후 보드의 공통 상단·속도 진단을 통일하고, StockBoard 전체 snapshot 중복 계산과 휴장일 시장수급 공백을 해결한다.

```text
속도 최우선
HTML 시장 데이터 계산 금지
기능별 전용 엔진 유지
collector 실시간 callback 변경 금지
기존 OpenAPI 로그인·실시간 등록 경로 보존
새 문서 남발 금지, 이 문서에 통합 기록
```

## 2. 공통 상단 최종 구조

모든 보드는 높이 104px의 공통 상단을 사용한다. 기존 StockBoard 상단 112px보다 작아 마지막 행 가시 영역을 줄이지 않는다.

```text
1행  aiTrade | StockBoard | ThemeBoard | StrategyBoard 준비중 | Boards | 새 창
2행  병목 | E2E | 수신 | 대기 | 드롭 | WorkerQ | CPU | Worker |
     계산 | 직렬 | 복사 | 캐시 | 접속 | Payload | API | 렌더
3행  보드별 실제 조작 버튼만 표시
```

공통 상단의 모든 텍스트는 기존 StockBoard 기준과 동일한 12px로 통일한다.

### 2.1 StockBoard 전용 조작

```text
화면 크기
폭 최소화
행 위치
선발기준
```

### 2.2 ThemeBoard 전용 조작

현재 공통 상단에 별도 조작 항목을 노출하지 않는다.

다음 문구는 삭제했다.

```text
표시기준 장마감 마지막값 유지 · 날짜/시간까지
종목 클릭=HTS/S1 연동
```

종목 클릭 HTS 연동 기능 자체는 유지하고 안내문만 숨긴다.

## 3. 기존 StockBoard 중복 상단 완전 제거

기존 `#topbar.topbar`는 `display:flex !important`를 사용해 단순 숨김 클래스보다 CSS 우선순위가 높았다.

최종 숨김 규칙:

```css
#topbar.topbar.bp-native-topbar-hidden,
.topbar.bp-native-topbar-hidden {
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    max-height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    border: 0 !important;
    overflow: hidden !important;
}
```

화면에서 완전히 제거한 기존 문구:

```text
StockBoard v2 Realtime
시계
연결 상태
행 클릭=HTS/S1 즉시 연동 · 포커스 후 ↑/↓ 이동
rows / events / trades
stream / sort
recv/s / trade/s
collector_q / sent/s / cxl
worker_q / drop / logdrop
top20 lag / stale / rt
render
잔량비 배경 · 순간 배경 · 1분/5분 배경 안내
색상 기준 안내
```

기존 DOM 노드는 StockBoard JavaScript 참조 안전성을 위해 유지하되 화면에서는 표시하지 않는다.

## 4. 공통 속도바

| 항목 | 의미 | 정상 | 경고 | 위험 |
|---|---|---:|---:|---:|
| 병목 | 현재 주요 병목 종류 | GREEN | YELLOW | RED |
| E2E | worker 마지막 이벤트 freshness | 500ms 이하 | 500~3000ms | 3000ms 초과 |
| 수신 | collector 전송 이벤트/초 | 장중 양수 | 장중 0 | 장중 장시간 0 |
| 대기 | collector pending queue | 50 이하 | 51~500 | 500 초과 |
| 드롭 | 직전 표본 증가량 / 누적 | 증가 0 | - | 증가 발생 |
| WorkerQ | 비동기 event log queue | 1000 이하 | 1001~10000 | 10000 초과 |
| CPU | 64비트 worker CPU | 60% 이하 | 60~85% | 85% 초과 |
| Worker | Python bitness | 64bit | - | 32bit |
| 계산 | 해당 보드 엔진 계산시간 | 보드별 기준 | 경고 기준 | 위험 기준 |
| 직렬 | JSON 직렬화 시간 | 10ms 이하 | 10~20ms | 20ms 초과 |
| 복사 | 공용 상태 최소필드 복사시간 | 보드별 기준 | 경고 기준 | 위험 기준 |
| 캐시 | 완성 payload 나이 | 보드별 기준 | 경고 기준 | 위험 기준 |
| 접속 | 보드 client 수 | 정보 | 정보 | 정보 |
| Payload | 직렬화 payload 크기 | 보드별 기준 | 경고 기준 | 위험 기준 |
| API | 브라우저 성능 API RTT | 10ms 이하 | 10~50ms | 50ms 초과 |
| 렌더 | 브라우저 render last/p95 | 16ms 이하 | 16~40ms | 40ms 초과 |

### 4.1 병목과 계산 중복 제거

기존:

```text
병목  StockBoard 계산 313.9ms
계산  313.9ms
```

최종:

```text
병목  StockBoard 계산
계산  313.9ms
```

병목 항목에는 원인 종류만 표시하고 실제 수치는 전용 항목에 한 번만 표시한다.

같은 원칙:

```text
병목  Worker CPU       / CPU 87.2%
병목  Collector 대기  / 대기 620
병목  드롭 증가        / 드롭 +5 / 12
병목  직렬화           / 직렬 24.1ms
병목  ThemeBoard 계산  / 계산 35.2ms
```

## 5. StockBoard shared snapshot cache

기존 StockBoard SSE는 연결된 브라우저마다 다음 작업을 반복했다.

```text
state.snapshot(300)
→ 300행 deepcopy
→ 거래대금 정렬
→ 후보모델 전체 계산
→ DisplayOrder 적용
→ JSON 직렬화
```

개선 후:

```text
여러 StockBoard SSE client
→ StockBoardSnapshotCacheService 계산 1회
→ JSON 직렬화 1회
→ 동일 bytes를 모든 client와 상태 writer가 재사용
```

특징:

```text
client 수와 계산 횟수 분리
입력 상태가 바뀌지 않으면 재계산 생략
candidate model 변경 시 즉시 재계산
payload bytes 사전 직렬화
cache version 변경 때만 SSE 전송
latest-state-only, 계산 backlog 생성 금지
```

### 5.1 적응형 계산 주기

| 상황 | 계산 주기 |
|---|---:|
| 일반 정상 | 100ms |
| 09:00~09:10 | 250ms |
| 계산 60ms 이상 | 500ms |
| 계산 100ms 이상 3회 연속 | 보호모드 1000ms |
| 보호모드에서 60ms 이하 5회 | 정상 복귀 |

장개시 09:00~09:10에는 화면 점수 재계산 빈도보다 collector 수신 안정성을 우선한다.

## 6. KOSPI·KOSDAQ 빈 화면 원인과 해결

### 6.1 확인된 원인

현재 `data/runtime/stockboard_v2/market_supply.json`은 Kiwoom REST 인증 실패 응답으로 갱신되어 다음 값이 비어 있었다.

```text
market_index = None
market_change_rate = None
advancers = None
decliners = None
개인/외인/기관/프로그램 = None
_status.errors = 30건
```

과거 정상 후보 파일:

```text
data/runtime/market_supply_after_20260702_121826.json
data/runtime/market_supply_before_20260702_121826.json
```

두 파일은 PowerShell 기본 저장 형식인 UTF-16 LE(BOM FF FE)였으며, 기존 복구기는 UTF-8만 읽어 실패했다.

### 6.2 UTF-16 복구 지원

지원 인코딩:

```text
UTF-8
UTF-8 BOM
UTF-16 LE/BE
CP949
```

JSON 구조도 전체 재귀 탐색하여 다음 형식을 표준화한다.

```text
kospi / kosdaq
KOSPI / KOSDAQ
market_supply / values / result / payload / data / output
markets / rows / items 목록
중첩 wrapper 구조
```

필드 별칭:

```text
index / cur_prc / 지수                → market_index
change_rate / flu_rt / 등락률         → market_change_rate
advance / rising / 상승               → advancers
decline / fall / 하락                 → decliners
ind_netprps / 개인                     → individual_eok
frgnr_netprps / 외인                   → foreign_spot_eok
orgn_netprps / 기관                    → institution_eok
all_netprps / 프로                     → program_market_eok
```

### 6.3 유효성 기준

KOSPI와 KOSDAQ 양쪽 모두 다음을 만족해야 현재값 또는 복원값으로 사용한다.

```text
market_index > 0
market_change_rate 존재
advancers 존재
decliners 존재
advancers 또는 decliners가 1 이상
```

따라서 개장 전 `상승 0 / 하락 0` 스냅샷은 사용하지 않는다.

### 6.4 PC1 검증 결과

`market_supply_after_20260702_121826.json`:

```text
encoding: utf-16
VALID: True
KOSPI path: $.kospi
KOSDAQ path: $.kosdaq
```

파일에 기록된 값:

| 시장 | 지수 | 등락률 | 상승 | 하락 | 개인(억) | 외인(억) | 기관(억) | 프로그램(억) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KOSPI | 7,993.68 | -3.73% | 413 | 468 | +26,642 | -41,309 | +12,867 | -26,623.43 |
| KOSDAQ | 886.58 | -4.60% | 464 | 1,227 | +5,514 | -4,147 | -1,478 | -5,368.83 |

`market_supply_before_20260702_121826.json`:

```text
encoding: utf-16
VALID: False
이유: 상승 0 / 하락 0인 개장 전 스냅샷
```

위 수치는 해당 PC1 스냅샷 파일에 기록된 값을 그대로 문서화한 것이며, 별도의 시세 진위 검증 결과를 의미하지 않는다.

## 7. 마지막 정상 시장수급 보호

worker의 `market_supply_last_valid_patch` 복구 순서:

```text
현재 정상 market_supply
→ market_supply_last_valid.json
→ stockboard_v2 runtime after/before snapshot
→ 기존 data/runtime after/before snapshot
→ docs/assets snapshot
→ unavailable
```

정상값 발견 시:

```text
data/runtime/stockboard_v2/market_supply_last_valid.json
```

에 UTF-8 표준 구조로 저장한다.

응답 상태:

```text
CURRENT_VALID      현재값 정상
LAST_VALID_HOLD    마지막 정상값 유지
UNAVAILABLE        유효 스냅샷 없음
PATCH_FAIL_OPEN    복구기 오류, 원래 context 반환
ORIGINAL_CONTEXT_ERROR 원래 context 생성 오류
```

`/api/v2/context`는 복구기 오류 때문에 연결을 끊지 않으며 오류 내용을 JSON 진단 필드로 반환한다.

## 8. context_snapshot_writer 덮어쓰기 방지

기존 writer는 `fetch_market_supply()`가 dict를 반환하면 내부 값이 전부 None이어도 성공으로 보고 `market_supply.json`을 덮어썼다.

최종 정책:

```text
live payload 유효
→ market_supply.json 저장
→ market_supply_last_valid.json 저장

live payload 무효/인증 실패
→ 현재 정상 market_supply.json 유지
→ UTF-16 after/before 포함 fallback 탐색
→ 유효 fallback만 UTF-8로 저장
→ 무효 payload는 절대 정상값을 덮지 않음
```

따라서 휴장일·인증 실패·일시 오류가 마지막 정상 KOSPI·KOSDAQ 정보를 지우지 않는다.

## 9. 공통 경로와 API

```text
/          StockBoard
/theme     ThemeBoard
/strategy  StrategyBoard 준비중
/boards    경량 Board Hub
```

```text
/api/v2/boards
/api/v2/boards/performance?board_id=stockboard
/api/v2/boards/performance?board_id=themeboard
/api/v2/boards/shell.css
/api/v2/boards/shell.js
/api/v2/context
```

iframe, 자동 다중창, 숨은 preload를 사용하지 않는다.

## 10. 64비트 worker 강제와 안전 실행

`realtime_v2/worker64_guarded_large_bidask.py`는 worker Python bit를 확인한다.

```text
64bit → 정상 시작
32bit → 즉시 종료
worker_python_bits_error.txt 기록
```

`scripts/stockboard_v2_large_safe.ps1`은 64비트 worker와 32비트 collector를 자동 구분하고, OpenAPI 로그인 완료 조건을 다음으로 판정한다.

```text
login_state = connected
native_handle_ready = True
registered_count > 0
provider_started = True
```

`opstarter`를 강제 종료하지 않는다.

## 11. 주요 구현 파일

```text
realtime_v2/board_platform/__init__.py
realtime_v2/board_platform/registry.py
realtime_v2/board_platform/stockboard_cache.py
realtime_v2/board_platform/performance.py
realtime_v2/board_platform/assets.py
realtime_v2/board_platform/http_patch.py
realtime_v2/market_supply_last_valid_patch.py
realtime_v2/context_snapshot_writer.py
realtime_v2/worker64_guarded_large_bidask.py
scripts/stockboard_v2_large_safe.ps1
stockboard_v2_large.cmd
```

주요 테스트:

```text
tests/test_board_platform_stockboard_cache.py
tests/test_board_platform_performance.py
tests/test_board_platform_http_patch.py
tests/test_board_platform_assets_integrity.py
tests/test_market_supply_last_valid_patch.py
tests/test_context_snapshot_writer_market_supply_guard.py
tests/test_stockboard_v2_safe_launcher.py
```

## 12. 완료된 PC1 검증

```text
64비트 worker 실행 확인
32비트 collector 실행 확인
OpenAPI native handle 정상
등록 종목 202개 확인
/theme 정상
/boards 정상
/api/v2/boards 정상
/api/v2/boards/performance 정상
공통 상단 자산 로딩 확인
기존 StockBoard 중복 상단 강제 숨김 구현
ThemeBoard 표시기준/HTS 안내 제거
병목/계산 중복 수치 제거
/api/v2/context fail-open 확인
UTF-16 after 스냅샷 VALID True 확인
개장 전 before 스냅샷 VALID False 확인
시장수급 writer 무효값 덮어쓰기 방지 구현
```

## 13. 남은 실전 검증

```text
공통 상단 최종 화면 육안 확인
KOSPI·KOSDAQ LAST_VALID_HOLD 실제 표시 확인
market_supply_last_valid.json 생성 확인
정규장 REST 인증 정상화
09:00~09:10 E2E·pending·drop·CPU 측정
StockBoard 창 2개에서 cache 계산이 client 수에 비례하지 않는지 확인
StockBoard 계산 300ms대 병목 개선: Fast/Rank/Model/Context lane 분리
```

## 14. 다음 성능 개선 순서

```text
1. Dirty-row StockBoard payload
2. Fast lane 50~100ms: 현재가·등락률·체결강도·거래대금
3. Rank lane 250~500ms: 거래대금 순위
4. Model lane 500~1000ms: 후보모델·등급·Funnel
5. Context lane 5~30초: 프로그램·OHLC·시장정보
6. BoardDataHub 한 번 복사
7. 서버 완성 row diff
8. 브라우저 가상화
9. 장마감 OpenAPI 중앙조정기
```

현재 Draft PR #27은 미병합 상태를 유지한다.
