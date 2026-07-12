# StockBoard Board Platform 공통 상단·속도·시장수급·장마감 복구 구현

최종 갱신: 2026-07-12  
상태: 공통 상단·시장수급 보호·shared snapshot cache·비동기 Model lane·Fast 경로·통합 장마감 복구 구현 완료 · 대표님 실전 정상 확인 · 정규장 09:00 성능 장기 관찰

## 1. 목적과 절대 원칙

StockBoard, ThemeBoard, StrategyBoard와 향후 보드의 공통 상단·속도 진단을 통일하고, StockBoard 전체 snapshot 중복 계산, 후보모델 결합 병목, 휴장일 시장수급 공백, 장마감 데이터 누락을 해결한다.

```text
속도 최우선
HTML 시장 데이터 계산 금지
기능별 전용 엔진 유지
collector 실시간 callback의 원천 수집 로직 변경 금지
기존 OpenAPI 로그인·실시간 등록 경로 보존
장마감 TR은 중앙 coordinator 한 곳에서만 실행
새 문서 남발 금지, 기존 기준 문서에 통합 기록
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
REST 토큰 1회 재발급/재시도            구현 완료
ka10032 검증 cache fallback            구현 완료
strict SBV2 HTS bridge                구현·실전 확인 완료
통합 장마감 source metadata            구현 완료
정규장·통합장 마감 sampler             구현 완료
AfterCloseRecoveryCoordinator         구현 완료
opt10080 minute fallback              구현 완료
ThemeBoard Coverage·추정·무거래 표시  구현 완료
복구 state 재시작 보존                 구현 완료
대표님 전체 시스템 실전 확인           정상
09:00~09:10 장개시 성능                반복 관찰
```

대표님 PC 휴장 상태 측정:

| 항목 | 결과 | 판정 |
|---|---:|---|
| Fast 계산 평균 | 44.488ms | 정상 |
| Fast 계산 최소 | 23.589ms | 정상 |
| Fast 계산 최대 | 101.452ms | 간헐 스파이크 허용 범위 |
| JSON 직렬화 평균 | 25.001ms | 다음 개선 후보 |
| Model 제출 평균 | 0.334ms | 병목 아님 |
| Model 결과 결합 평균 | 4.011ms | 정상 |
| ThemeBoard 계산 | 약 3.0ms | 정상 |
| ThemeBoard copy | 약 0.47ms | 정상 |
| ThemeBoard lock wait | 0ms | 정상 |

초기 약 340ms 대비 Fast 계산 평균은 약 87% 감소했다. 휴장 측정은 장개시 폭주 완료 판정이 아니므로 정규장 관찰을 계속한다.

## 3. 공통 상단 최종 구조

모든 보드는 높이 104px의 공통 상단을 사용한다.

```text
1행  aiTrade | StockBoard | ThemeBoard | StrategyBoard 준비중 | Boards | 새 창
2행  병목 | E2E | 수신 | 대기 | 드롭 | WorkerQ | CPU | Worker |
     계산 | 직렬 | 복사 | 캐시 | 접속 | Payload | API | 렌더
3행  보드별 실제 조작 버튼만 표시
```

StockBoard 전용 조작:

```text
화면 크기
폭 최소화
행 위치
선발기준
```

ThemeBoard는 별도 조작 항목을 노출하지 않는다. 종목 클릭 HTS 연동은 유지한다.

기존 `#topbar.topbar` DOM은 JavaScript 참조 안전성을 위해 유지하되 공통 상단 설치 후 화면에서 완전히 숨긴다.

## 4. 공통 속도바 기준

| 항목 | 정상 | 경고 | 위험 |
|---|---:|---:|---:|
| E2E | 500ms 이하 | 500~3000ms | 3000ms 초과 |
| collector pending | 50 이하 | 51~500 | 500 초과 |
| drop 증가 | 0 | - | 증가 발생 |
| WorkerQ | 1000 이하 | 1001~10000 | 10000 초과 |
| CPU | 60% 이하 | 60~85% | 85% 초과 |
| worker bit | 64bit | - | 32bit |
| StockBoard 계산 | 50ms 이하 | 50~100ms | 100ms 초과 |
| 직렬화 | 10ms 이하 | 10~20ms | 20ms 초과 |
| ThemeBoard 계산 | 10ms 이하 | 10~30ms | 30ms 초과 |
| ThemeBoard copy | 1ms 이하 | 1~2ms | 2ms 초과 |
| 브라우저 render p95 | 16ms 이하 | 16~40ms | 40ms 초과 |

병목 항목에는 원인 종류만 표시하고 실제 수치는 전용 항목에 한 번만 표시한다.

## 5. StockBoard shared snapshot cache

기존에는 SSE client마다 전체 300행 snapshot·정렬·후보모델·JSON 직렬화를 반복했다.

현재 구조:

```text
여러 StockBoard SSE client
→ StockBoardSnapshotCacheService 계산 1회
→ JSON 직렬화 1회
→ 같은 bytes를 모든 client와 상태 writer가 재사용
```

특징:

- client 수와 계산 횟수 분리
- 입력 상태가 바뀌지 않으면 재계산 생략
- candidate model 변경 시 scheduler 갱신 요청
- payload bytes 사전 직렬화
- cache version 변경 때만 SSE 전송
- latest-state-only, 계산 backlog 없음
- REST/SSE/상태 writer에서 동기 후보모델 계산 금지

적응형 계산 주기:

| 상황 | 주기 |
|---|---:|
| 일반 정상 | 100ms |
| 09:00~09:10 | 250ms |
| 계산 60ms 이상 | 500ms |
| 100ms 이상 3회 연속 | 보호모드 1000ms |
| 보호모드에서 60ms 이하 5회 | 정상 복귀 |

## 6. 비동기 Fast/Rank/Model lane

```text
Fast/Rank lane
→ 최신 가격·등락률·거래대금·순위·강도·호가
→ 최신 준비 행을 Model lane에 제출
→ 직전 완성 모델 필드 즉시 결합
→ DisplayOrder·브라우저 전송

Model lane
→ 최신 300행 한 개만 보관
→ 중간 backlog 없음
→ 기본 1000ms 간격
→ 기존 후보모델 공식 그대로 실행
→ 완료 결과 원자적 교체
```

보호:

- 모델 cache가 최신 price/rank/amount_ratio를 덮지 않음
- 모델 변경 직후 이전 모델 점수 재사용 금지
- 새 모델 완료 전 `MODEL_PENDING`
- 수동 행 고정 중 lane 재초기화 금지
- worker 종료 시 Model lane도 종료

## 7. Fast 경로 개선 결과

주요 개선:

| 원인 | 개선 |
|---|---|
| 기존 quote마다 정적 보정 반복 | 기존 quote 즉시 재사용 |
| OHLC·강도 중복 적용 | snapshot 변경 시 전체 1회 적용 |
| Model 결합 시 행 재복사 | 분리된 행에 제자리 결합 |
| 시장 달력 반복 읽기 | 메모리 cache·mtime 확인 |
| display hold 반복 병합 | 종목별 정규화·병합 cache |
| 이전 거래일 fallback 반복 판정 | 종목별 유효 원천 cache |
| session metric 동일 행 갱신 | token 변경 시만 갱신 |
| callback 안 파일 저장 가능성 | 메모리 갱신 후 주기 writer |

최종 휴장 steady-state:

```text
FastAverageMs         44.488
FastMinimumMs         23.589
FastMaximumMs        101.452
SerializeAverageMs    25.001
ModelSubmitAverageMs   0.334
ModelMergeAverageMs    4.011
```

## 8. KOSPI·KOSDAQ 시장수급 보호

지원 인코딩:

```text
UTF-8
UTF-8 BOM
UTF-16 LE/BE
CP949
```

유효성:

```text
market_index > 0
market_change_rate 존재
advancers 존재
decliners 존재
advancers 또는 decliners가 1 이상
```

복구 순서:

```text
현재 정상 market_supply
→ market_supply_last_valid.json
→ runtime after/before snapshot
→ legacy runtime snapshot
→ docs/assets snapshot
→ unavailable
```

무효 payload와 인증 실패는 기존 정상값을 덮지 않는다. 인증 실패 문구가 확인되면 토큰을 한 번 재발급해 재시도한다.

## 9. 통합 장마감 복구

### 9.1 공용 source 계약

```text
value / source / status / basis_time / trading_date
market_scope / quality / is_estimated / updated_at / coverage
```

낮은 quality·KRX fallback·추정값은 정확한 AL 값을 덮지 않는다.

### 9.2 마감 sampler

```text
정규장 15:24:50~15:30:10
통합장 19:54:50~20:00:10
```

1초마다 종목코드·누적 거래대금·시각만 비차단 복사하고, 구간 종료 후 정확한 1분·5분 차이를 worker daily state와 ThemeBoard cache에 반영한다.

### 9.3 중앙 coordinator

```text
AfterCloseRecoveryCoordinator
P0 S1
P1 Top20
P2 상위 테마 주도주
P3 상위 테마 구성종목
P4 나머지 테마
P5 Hidden50
P6 나머지 Top300
```

- 한 시점에 competing TR 1개
- 동일 종목 bundle별 중복 제거
- 5분 → 30분 → 2시간 재시도
- 연속 오류 3회 → 30분 global backoff
- 프리마켓 5분 전 신규 전일 복구 중단

### 9.4 opt10080 fallback

```text
CODE_AL 우선
→ 실패 시 6자리 KRX
→ 1분 = 해당 분 종가×거래량
→ 5분 = 각 분 종가×거래량 합
```

실제 거래량 0은 `거래없음`, 추정값은 `≈`, 부분 Coverage는 `*`로 표시한다.

### 9.5 저장·복원

```text
close_flow_sampler_last.json
after_close_recovery_state.json
theme_last_close.json
```

재시작 후 유효한 복구값을 복원하고 다음 실제 premarket에 만료한다.

대표님은 최신 통합 복구 적용 후 전체 시스템이 정상 작동함을 확인했다. 15:30·20:00·다음 premarket 등 시간 의존 동작은 거래일별 관찰을 계속한다.

## 10. 공통 경로와 API

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

## 11. Python bitness와 안전 실행

```text
worker: 64bit 강제
collector: 고정 32bit
```

안전 launcher 준비 판정:

```text
login_state=connected
native_handle_ready=True
registered_count>0
provider_started=True
```

`opstarter`를 강제 종료하지 않는다.

## 12. 주요 구현 파일

```text
realtime_v2/board_platform/
realtime_v2/theme_board_patch.py
realtime_v2/after_close_recovery.py
realtime_v2/after_close_recovery_hardening.py
realtime_v2/after_close_recovery_sampler_guard.py
realtime_v2/after_close_recovery_policy_guard.py
realtime_v2/after_close_recovery_state.py
realtime_v2/after_close_theme_recovery.py
realtime_v2/collector32_large_bidask.py
realtime_v2/worker64_guarded_large_bidask.py
realtime_v2/context_snapshot_writer.py
realtime_v2/market_supply_last_valid_patch.py
scripts/stockboard_v2_large_safe.ps1
stockboard_v2_large.cmd
```

주요 테스트:

```text
tests/test_board_platform_*.py
tests/test_stockboard_model_lane*.py
tests/test_stockboard_fast_path*.py
tests/test_market_supply_last_valid_patch.py
tests/test_context_snapshot_writer_market_supply_guard.py
tests/test_after_close_recovery*.py
tests/test_after_close_theme_recovery.py
tests/test_stockboard_v2_safe_launcher.py
```

## 13. 완료된 대표님 검증

```text
64비트 worker 실행
32비트 collector 실행
OpenAPI native handle·로그인 정상
등록 종목 정상
StockBoard 정상
ThemeBoard 정상
/boards·성능 API 정상
공통 상단 정상
시장수급 last-valid 보호 정상
Model lane READY·오류 없음
Fast/Model 분리 정상
휴장 세 지표 누락 0
strict SBV2 HTS·Clipboard 정상
통합 장마감 복구 적용 후 전체 시스템 정상
```

## 14. 남은 할 일 — 운영 관찰

### 14.1 다음 정규장 09:00~09:10 성능

| 항목 | 기준 |
|---|---:|
| Fast 평균 | 100ms 이하 |
| Fast 최대 | 간헐적 200ms 이내 |
| E2E | 500ms 이하 중심 |
| pending | 50 이하 중심 |
| drop 증가 | 0 |
| WorkerQ | 1000 이하 중심 |
| CPU | 60% 이하 중심 |
| Model | READY |

동시에 가격·등락률·대금·후보5·등급·Funnel 정합성을 HTS와 비교한다.

### 14.2 시간 의존 복구 관찰

```text
15:30 sampler 파일·basis_time
20:00 sampler 파일·basis_time
opt10080 _AL 범위·단위
KRX fallback 빈도
한 시점 TR 1개
retry·global backoff
premarket cutoff
LAST_CLOSE → LIVE
```

### 14.3 시장수급 CURRENT_VALID

```text
REST 토큰 만료 시 1회 재발급 성공
KOSPI·KOSDAQ CURRENT_VALID
last_valid 파일 갱신
인증 실패 시 정상값 보존
```

### 14.4 JSON 직렬화

직렬화 평균 약 25ms는 다음 개선 후보다. 정규장 전체 성능이 정상이라면 즉시 수정하지 않고 실측 후 진행한다.

### 14.5 향후 보드

```text
StrategyBoard 계산 엔진
ThemeBoard·StrategyBoard 공용 DataHub 확대
StockBoard 표준 Runtime 단계 이전
```

## 15. 중단 기준

다음 중 하나면 신규 기능을 우선 비활성화하고 기존 실시간 경로를 보호한다.

```text
dropped_trade 증가
collector pending 지속 증가
worker queue 지속 증가
09:00 stream latency 악화
DataHub/Theme copy p95 2ms 초과
보드 계산 30ms 초과 반복
OpenAPI competing TR 2개 이상
로그인·연결 불안정
```

## 16. 최종 병합 조건

```text
09:00~09:10 성능 기준 통과
가격·후보5·등급·Funnel 정합성 통과
시장수급 CURRENT_VALID 또는 정상 fallback
2개 StockBoard 창 cache 단일 계산
통합 복구 시간대별 관찰 이상 없음
치명 오류·drop 증가 없음
```

위 조건 전까지 Draft PR #27을 미병합 상태로 유지한다.
