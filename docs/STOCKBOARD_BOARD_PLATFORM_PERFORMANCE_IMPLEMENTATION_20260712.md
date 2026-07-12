# StockBoard Board Platform 공통 상단·속도·시장수급 복구 구현

최종 갱신: 2026-07-12  
상태: 공통 상단·시장수급 보호·shared snapshot cache·비동기 Model lane·Fast 경로 병목 개선 완료 · 정규장 09:00 실전 검증 대기

## 1. 목적과 절대 원칙

StockBoard, ThemeBoard, StrategyBoard와 향후 보드의 공통 상단·속도 진단을 통일하고, StockBoard 전체 snapshot 중복 계산, 후보모델 결합 병목, 휴장일 시장수급 공백을 해결한다.

```text
속도 최우선
HTML 시장 데이터 계산 금지
기능별 전용 엔진 유지
collector 실시간 callback의 원천 수집 로직 변경 금지
기존 OpenAPI 로그인·실시간 등록 경로 보존
새 문서 남발 금지, 이 문서에 통합 기록
정규장 검증 전 Draft PR 병합 금지
```

## 2. 최종 상태 요약

```text
공통 Board Platform 상단             완료
StockBoard 기존 중복 상단 제거        완료
ThemeBoard v1                         완료
StrategyBoard 경로/자리만 준비         엔진 미구현
StockBoard shared snapshot cache      완료
scheduler 단일 계산 권한              완료
비동기 Model lane 분리                완료
Fast 경로 중복 quote 보정 제거         완료
장마감 display hold 반복 축소          완료
이전 거래일 fallback 캐시              완료
session metric hold 캐시               완료
시장수급 마지막 정상값 보호            완료
REST 토큰 1회 재발급/재시도            구현 완료, 정규장 검증 필요
Fast 서버 계산 병목                    휴장 기준 해결
09:00~09:10 실전 성능                  미검증
```

최종 PC1 휴장 상태 측정:

| 항목 | 결과 | 판정 |
|---|---:|---|
| Fast 계산 평균 | 44.488ms | 정상 |
| Fast 계산 최소 | 23.589ms | 정상 |
| Fast 계산 최대 | 101.452ms | 간헐 스파이크 허용 범위 |
| JSON 직렬화 평균 | 25.001ms | 다음 개선 후보 |
| Model 제출 평균 | 0.334ms | 병목 아님 |
| Model 결과 결합 평균 | 4.011ms | 정상 |

초기 약 340ms 대비 Fast 계산 평균은 약 87% 감소했다. 다만 이 수치는 휴장 상태이므로 정규장 개장 폭주 성능 완료를 의미하지 않는다.

## 3. 공통 상단 최종 구조

모든 보드는 높이 104px의 공통 상단을 사용한다. 기존 StockBoard 상단 112px보다 작아 마지막 행 가시 영역을 줄이지 않는다.

```text
1행  aiTrade | StockBoard | ThemeBoard | StrategyBoard 준비중 | Boards | 새 창
2행  병목 | E2E | 수신 | 대기 | 드롭 | WorkerQ | CPU | Worker |
     계산 | 직렬 | 복사 | 캐시 | 접속 | Payload | API | 렌더
3행  보드별 실제 조작 버튼만 표시
```

공통 상단의 모든 텍스트는 기존 StockBoard 기준과 동일한 12px로 통일한다.

StockBoard 전용 조작:

```text
화면 크기
폭 최소화
행 위치
선발기준
```

ThemeBoard는 현재 공통 상단에 별도 조작 항목을 노출하지 않는다. 종목 클릭 HTS 연동 기능은 유지하고 안내문만 숨긴다.

기존 `#topbar.topbar` DOM은 JavaScript 참조 안전성을 위해 유지하되 다음 규칙으로 화면에서 완전히 제거한다.

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

## 4. 공통 속도바 기준

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

병목 항목에는 원인 종류만 표시하고 실제 수치는 전용 항목에 한 번만 표시한다.

```text
병목  StockBoard 계산  / 계산 44.5ms
병목  Worker CPU       / CPU 87.2%
병목  Collector 대기  / 대기 620
병목  드롭 증가        / 드롭 +5 / 12
병목  직렬화           / 직렬 25.0ms
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

최종 구조:

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
candidate model 변경 시 scheduler 갱신 요청
payload bytes 사전 직렬화
cache version 변경 때만 SSE 전송
latest-state-only, 계산 backlog 생성 금지
REST/SSE/상태 writer의 동기 전체 계산 금지
```

### 5.1 scheduler 단일 계산 권한

```text
전체 snapshot 계산 권한
→ StockBoardSnapshotCacheService scheduler 단독

REST / SSE / 상태파일
→ 최신 완성 cache 읽기
→ refresh 요청 신호만 전달
→ 요청 스레드에서 후보모델/정렬/직렬화 실행 금지
```

진단값:

```text
scheduler_only
compute_success_count
compute_rate_limit_skip_count
refresh_request_count
force_refresh_request_count
coalesced_event_count
last_event_delta
remaining_compute_delay_ms
```

### 5.2 적응형 계산 주기

| 상황 | 계산 주기 |
|---|---:|
| 일반 정상 | 100ms |
| 09:00~09:10 | 250ms |
| 계산 60ms 이상 | 500ms |
| 계산 100ms 이상 3회 연속 | 보호모드 1000ms |
| 보호모드에서 60ms 이하 5회 | 정상 복귀 |

장개시 09:00~09:10에는 화면 점수 갱신 빈도보다 collector 수신 안정성을 우선한다.

## 6. 비동기 Fast/Rank/Model lane 분리

기존 `_guarded_rows()`는 빠른 행 구성과 후보모델 계산을 한 함수에서 연속 실행했다.

```text
quotes 복사
→ 거래대금 정렬
→ rank / rank_change / amount_ratio
→ enrich_candidate_model_fields(300행)
→ 점수 4그룹 / grade guard / Funnel 50·20·5
→ DisplayOrder
```

최종 구조:

```text
Fast/Rank lane
→ 최신 가격·등락률·거래대금·순위·강도·호가 계산
→ 최신 준비 행을 Model lane에 제출
→ 직전 완성 모델 필드를 즉시 결합
→ DisplayOrder 및 브라우저 전송

Model lane background
→ 제출된 최신 300행만 보관
→ 중간 계산 backlog 없음
→ 기본 1000ms 간격
→ 기존 enrich_candidate_model_fields() 그대로 실행
→ 완료 결과를 원자적으로 교체
```

후보모델 공식, 점수, 등급, grade guard, Funnel 50/20/5 계산식은 변경하지 않았다.

안전장치:

```text
현재 price/rank/rank_change/amount_ratio가 모델 cache에 의해 덮이지 않음
trade_value_rank는 최신 Fast rank로 유지
모델 변경 직후 이전 모델 점수 재사용 금지
새 모델 완료 전 MODEL_PENDING
첫 모델 완료 또는 실제 모델 변경 때만 DisplayOrder lane 1회 재초기화
수동 행 고정 중에는 lane 재초기화 금지
Model lane thread는 worker 종료 시 함께 종료
입력 없음/새 버전 없음일 때 0.5초 대기
```

PC1 확인값:

```text
ModelState        READY
ModelId           FIVE_FACTOR_FLOW_V01
ModelIntervalMs   1000
ModelError        없음
Model 제출 평균    0.334ms
Model 결합 평균    4.011ms
```

성능 API 진단값:

```text
model_lane_state
model_lane_model_id
model_lane_compute_ms
model_lane_age_ms
model_lane_interval_ms
model_lane_compute_count
model_lane_reuse_count
model_lane_coalesced
model_lane_pending
model_lane_last_error
```

## 7. Fast 경로 병목 분석과 개선

### 7.1 단계별 측정 결과

초기 계측:

```text
FastComputeMs     340.036ms
QuoteEnsureMs     131.842ms
ModelMergeMs      62.795ms
UnaccountedMs     140.734ms
```

원인과 개선:

| 원인 | 개선 |
|---|---|
| 기존 202개 quote마다 `_quote()` 재호출 | 기존 quote 즉시 재사용, 신규 quote만 초기화 |
| 전일값·OHLC·강도 중복 적용 | snapshot 변경 시 전체 quote에 1회 일괄 적용 |
| Model 결과 결합 때 행 재복사 | 이미 복사된 snapshot 행에 제자리 결합 |
| 시장 캘린더 파일 반복 읽기 | 메모리 캐시, 5초 mtime 확인 |
| display hold 202행 × 최대 3개 entry × 전체 필드 반복 | 종목별 정규화 entry·병합 결과 캐시 |
| 이전 거래일 fallback의 원천 유효성 반복 판정 | 종목별 유효 원천 캐시 |
| session metric hold의 동일 행 반복 갱신 | 종목별 token, 값 변경 시에만 갱신 |
| event callback 안의 hold 파일 저장 가능성 | callback에서는 메모리 갱신만, 주기 writer에서 저장 |

개선 후 주요 단계:

```text
QuoteEnsure      131.842ms → 약 0.2ms
ModelMerge        62.795ms → 평균 4.011ms
PreviousDaily     65.238ms → 약 8.5ms
Session hold      58.933ms → 약 7.8ms
Display hold     120.736ms → 약 23.9ms
```

### 7.2 최종 steady-state 결과

PC1에서 10회 측정한 휴장 steady-state:

```text
FastAverageMs        44.488
FastMinimumMs        23.589
FastMaximumMs       101.452
SerializeAverageMs   25.001
ModelSubmitAverageMs  0.334
ModelMergeAverageMs   4.011
```

판정:

```text
Fast 서버 계산 병목         해결
Model 제출/결합 병목         해결
JSON 직렬화                 다음 개선 후보
정규장 09:00 처리량          미검증
```

cProfile과 세부 계측은 1회성·제한형으로 유지하여 정상 운용 시 반복 프로파일링 backlog를 만들지 않는다.

## 8. KOSPI·KOSDAQ 시장수급 보호

### 8.1 확인된 문제

`market_supply.json`이 Kiwoom REST 인증 실패 응답으로 갱신되면서 다음 값이 비어 있었다.

```text
market_index = None
market_change_rate = None
advancers = None
decliners = None
개인/외인/기관/프로그램 = None
```

과거 정상 후보 파일은 UTF-16 LE였고 기존 복구기는 UTF-8만 읽어 실패했다.

지원 인코딩:

```text
UTF-8
UTF-8 BOM
UTF-16 LE/BE
CP949
```

JSON 구조는 `kospi/kosdaq`, `KOSPI/KOSDAQ`, 중첩 `market_supply/values/result/payload/data/output`, `markets/rows/items`를 재귀 탐색해 표준화한다.

유효성 기준:

```text
market_index > 0
market_change_rate 존재
advancers 존재
decliners 존재
advancers 또는 decliners가 1 이상
```

개장 전 `상승 0 / 하락 0` 스냅샷은 정상값으로 사용하지 않는다.

### 8.2 마지막 정상값 복구 순서

```text
현재 정상 market_supply
→ market_supply_last_valid.json
→ stockboard_v2 runtime after/before snapshot
→ 기존 data/runtime after/before snapshot
→ docs/assets snapshot
→ unavailable
```

정상값은 다음에 UTF-8 표준 구조로 저장한다.

```text
data/runtime/stockboard_v2/market_supply_last_valid.json
```

응답 상태:

```text
CURRENT_VALID          현재값 정상
LAST_VALID_HOLD        마지막 정상값 유지
UNAVAILABLE            유효 스냅샷 없음
PATCH_FAIL_OPEN        복구기 오류, 원래 context 반환
ORIGINAL_CONTEXT_ERROR 원래 context 생성 오류
```

### 8.3 writer 덮어쓰기 방지와 토큰 복구

```text
live payload 유효
→ market_supply.json 저장
→ market_supply_last_valid.json 저장

live payload 무효/인증 실패
→ 인증 실패 문구 검사
→ 토큰 폐기 및 1회 재발급/재시도
→ 재시도 실패 시 현재 정상 market_supply.json 유지
→ UTF-16 after/before 포함 fallback 탐색
→ 유효 fallback만 UTF-8로 저장
→ 무효 payload는 절대 정상값을 덮지 않음
```

휴장일·인증 실패·일시 오류가 마지막 정상 KOSPI·KOSDAQ 정보를 지우지 않는다. 토큰 복구는 구현됐지만 정규장 `CURRENT_VALID` 실전 확인이 남아 있다.

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

`worker64_guarded_large_bidask.py`는 worker Python bit를 확인한다.

```text
64bit → 정상 시작
32bit → 즉시 종료
worker_python_bits_error.txt 기록
```

안전 launcher는 64비트 worker와 32비트 collector를 자동 구분하고 OpenAPI 로그인 완료 조건을 다음으로 판정한다.

```text
login_state = connected
native_handle_ready = True
registered_count > 0
provider_started = True
```

`opstarter`를 강제 종료하지 않는다.

## 11. 주요 구현 파일과 테스트

주요 구현 파일:

```text
realtime_v2/board_platform/__init__.py
realtime_v2/board_platform/registry.py
realtime_v2/board_platform/stockboard_cache.py
realtime_v2/board_platform/model_lane.py
realtime_v2/board_platform/model_lane_merge_optimize.py
realtime_v2/board_platform/fast_path_optimize.py
realtime_v2/board_platform/fast_path_profile.py
realtime_v2/board_platform/hot_path_cprofile.py
realtime_v2/board_platform/market_session_cache.py
realtime_v2/board_platform/display_hold_fast.py
realtime_v2/board_platform/previous_daily_fast.py
realtime_v2/board_platform/session_metric_fast.py
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
tests/test_stockboard_model_lane.py
tests/test_stockboard_model_lane_merge_optimize.py
tests/test_stockboard_fast_path_optimize.py
tests/test_stockboard_fast_path_profile.py
tests/test_stockboard_hot_path_cprofile.py
tests/test_board_platform_hold_fast.py
tests/test_board_platform_fallback_session_fast.py
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
Model lane 설치 대상 오류 수정
Model lane READY 및 last_error 없음 확인
Fast 계산과 Model 계산 분리 확인
Fast steady-state 평균 44.488ms 확인
ModelSubmit 평균 0.334ms 확인
ModelMerge 평균 4.011ms 확인
```

## 13. 남은 할 일 — 최신 우선순위

### 13.1 1순위: 다음 정규장 09:00~09:10 실전 성능 검증

휴장 측정만으로 장개시 완료 판정을 하지 않는다.

| 항목 | 완료 기준 |
|---|---:|
| Fast 계산 평균 | 100ms 이하 |
| Fast 계산 최대 | 간헐적 200ms 이내 |
| E2E | 500ms 이하 중심 |
| Collector pending | 50 이하 중심 |
| Drop 증가 | 0 |
| WorkerQ | 1000 이하 중심 |
| Worker CPU | 60% 이하 중심 |
| Model 상태 | READY |
| Model/Fast 오류 | 없음 |

동시에 확인할 것:

```text
StockBoard 창 2개에서 cache 계산 횟수가 client 수에 비례하지 않음
후보5·등급·Funnel 결과가 기존 계산과 동일
선발기준 변경 시 MODEL_PENDING 후 새 모델 정상 전환
가격·등락률·거래대금·순위가 장초반에 실제 HTS 흐름을 따라감
브라우저 render p95와 API RTT가 위험 기준을 넘지 않음
```

### 13.2 2순위: 정규장 시장수급 CURRENT_VALID 확인

```text
REST 토큰 만료 시 1회 재발급/재시도 성공 여부
KOSPI·KOSDAQ CURRENT_VALID 전환
market_supply_last_valid.json 생성/갱신
인증 실패 시 기존 정상값 보존
LAST_VALID_HOLD 실제 화면 표시
```

### 13.3 3순위: JSON 직렬화와 payload 경량화

현재 직렬화 평균은 25.001ms로 Fast 계산보다 다음 개선 가치가 높다.

우선 검토:

```text
기본 stream에서 중첩 score_breakdown·진단 필드 분리
상세 필드는 종목 상세 API 또는 필요 시 요청
변경 없는 중첩 객체 재전송 제한
payload 크기와 serialize p95 측정
```

정규장 전체 성능이 정상이라면 급하게 수정하지 않고 실측 후 진행한다.

### 13.4 4순위: 정규장 결과가 기준을 넘을 때만 추가 서버 최적화

```text
Dirty-row StockBoard payload
거래대금 Rank lane 250~500ms 분리
서버 완성 row diff
BoardDataHub 한 번 복사
Context lane 5~30초: 프로그램·OHLC·시장정보
```

휴장 평균 44.488ms 상태에서는 위 구조를 미리 추가하지 않는다. 장초반 실측 실패 시 가장 큰 항목부터 진행한다.

### 13.5 5순위: 브라우저와 보드 확장

```text
렌더 p95 40ms 초과 시 행 가상화 또는 DOM diff 강화
StrategyBoard 계산 엔진 구현
장마감 OpenAPI 중앙조정기
ThemeBoard·StrategyBoard 공용 BoardDataHub 확대
```

### 13.6 최종 병합 조건

```text
09:00~09:10 성능 기준 통과
가격·후보5·등급·Funnel 정합성 통과
시장수급 CURRENT_VALID 또는 정상 fallback 동작 확인
2개 StockBoard 창 cache 단일 계산 확인
치명 오류·drop 증가 없음
```

위 조건 전까지 Draft PR #27은 미병합 상태를 유지한다.
