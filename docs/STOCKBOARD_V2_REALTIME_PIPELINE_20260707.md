# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-10

이 문서는 StockBoard v2의 현재 실시간 구조, 선발모델, 5분강도, 행 위치 정책과 운영 검증 상태를 기록하는 단일 기준 문서이다. 기존 StockBoard v0.3.x 기준 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신 속도와 표시 안정성을 확보하는 구조이다.

현재 실전 확인 완료 항목:

- 현재가·등락률·거래대금 실시간 표시
- Top20·Top300 내부 행 위치 고정
- 안전장치를 통과한 Top20 ↔ Top300 승강
- 5요소 수급선발 v0.1 연결
- 1분강도 표시를 Kiwoom opt10046 기반 5분강도로 교체
- 같은 거래일 재시작 시 마지막 정상 5분강도 복원
- 새 거래일 재시작 시 전일 5분강도 미복원
- NXT 미거래 종목의 애프터마켓 정규장 마감 5분강도 보충
- 0·빈 응답·오류가 마지막 정상 5분강도를 덮어쓰지 않도록 보호

대표님 실전 확인:

- 행 위치 고정 정상
- NXT 거래가 없는 종목의 5분강도 정규장 마감값 표시 정상

## 2. 기준 브랜치와 실행

| 항목 | 값 |
|---|---|
| 기준 브랜치 | `hot-priority-integrated-20260630` |
| 대형 실행기 | `stockboard_v2_large.cmd` |
| 기본 실행기 | `stockboard_v2_live.cmd` |
| 화면 주소 | `http://127.0.0.1:8765/` |
| worker HTTP 포트 | `8765` |
| collector → worker TCP 포트 | `8710` |

현재 실전 실행:

```powershell
cd C:\aiTrade
git checkout hot-priority-integrated-20260630
git pull origin hot-priority-integrated-20260630
.\stockboard_v2_large.cmd restart-fast
```

브라우저:

```text
http://127.0.0.1:8765/
Ctrl+F5
```

## 3. 현재 구조

```text
Kiwoom OpenAPI 32bit
→ realtime_v2/collector32_large.py
→ TCP JSON event
→ realtime_v2/worker64_guarded_large.py
→ SSE /api/v2/stream
→ docs/stockboard_v2.html
```

| 계층 | 파일 | 역할 |
|---|---|---|
| universe builder | `realtime_v2/build_universe.py` | 거래대금 universe 생성, tradable master 필터, 전일 거래대금 결합 |
| base collector | `realtime_v2/collector32.py` | Kiwoom 실시간 이벤트 수신 |
| large collector | `realtime_v2/collector32_large.py` | 대량체결 집계, 5분강도 정상값 저장, 0·빈값 보호 |
| base worker | `realtime_v2/worker64.py` | 상태 저장, snapshot API, SSE, event log |
| guarded worker | `realtime_v2/worker64_guarded.py` | 장상태, seed fallback, 누적값 역행 방어, 일중 복원 |
| large worker | `realtime_v2/worker64_guarded_large.py` | 대량체결·5분강도 표시와 대형 UI 연결 |
| 5분강도 scheduler | `realtime_v2/strength5m_scheduler.py` | S1/Top20/Hidden Top50/Top300 차등 opt10046 조회 |
| 장상태 | `realtime_v2/market_session.py` | 프리마켓·정규장·애프터·휴장·특별일 판단 |
| 시장 달력 | `config/stockboard_market_calendar.json` | 기본 거래시간, 휴장일, 지연 개장 설정 |
| Ranking Engine | `stockboard_ranking_engine.py` | 선발모델 점수·등급·model_rank 계산 |
| 행 위치 제어 | `stockboard_display_order.py` | Top20·Top300 내부 위치 고정과 안전 승강 |
| 선발 설정 | `configs/candidate_models/*.json` | 모델별 점수 구조와 정책 |
| 모델 목록 | `configs/candidate_models/_registry.json` | 드롭다운 모델 목록과 기본 모델 |
| UI | `docs/stockboard_v2.html` | worker 결과 표시 전용 |

중요 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- HTML은 worker가 제공한 값과 행 순서를 표시한다.
- `data/runtime/`은 Git 추적 금지이다.

## 4. 시장시간 정책

시간은 `config/stockboard_market_calendar.json`을 기준으로 하며 `special_days`가 있으면 특별일 시간이 우선한다.

| phase | 기본 시간 | 정책 |
|---|---|---|
| before_market | 08:00 전 | 신규 실시간·5분강도 조회 차단 |
| premarket | 08:00~08:30 | 실시간 및 5분강도 조회 허용 |
| opening_call | 08:30~09:00 | 실시간 수용 |
| regular | 09:00~15:20 | 실시간 우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 정규장 마감 5분강도 보충 시작 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목은 정규장 마감값 유지 |
| closed | 20:00 이후 | 신규 5분강도 조회 중단, 마지막값 유지 |
| weekend/holiday | 주말·휴장일 | 실시간 조회 차단 |

## 5. 5분강도 정책

표시 원천은 Kiwoom opt10046의 `strength_5m`이다. 브라우저에서 5분강도를 계산하지 않는다.

### 5.1 거래일과 재시작

| 상황 | 표시 정책 |
|---|---|
| 같은 거래일 재시작 | 당일 마지막 정상값 복원 |
| 새 거래일 08:00 전 재시작 | 전일값 미복원, `-` 표시 |
| 프로세스를 재시작하지 않고 날짜 변경 | 메모리의 기존값 유지 후 새 프리마켓 값이 들어오면 교체 |
| 정상 양수 수신 | 새 값 저장·표시 |
| 0·빈값·오류 | 기존 정상값 유지, 정상값이 없으면 `-` |

당일 정상값 저장:

```text
data/runtime/stockboard_v2/strength_snapshot.json
```

스냅샷의 `trading_date`가 현재 거래일과 다르면 복원하지 않는다.

### 5.2 차등 조회 주기

| 구간 | 일반 | 정규장 개시 보호구간 |
|---|---:|---:|
| S1 | 30초 | 45초 |
| Top20 | 90초 | 120초 |
| Hidden Top50 | 300초 | 600초 |
| Top300 | 1,200초 | 1,800초 |

정규장 개시 보호구간은 달력의 `regular_start` 5분 전부터 10분 후까지다.

다음 작업이 진행 중이면 5분강도 조회는 양보한다.

- strength probe inflight/pending
- orderbook probe inflight/pending
- opt10055 inflight/pending
- close metrics queue

### 5.3 정규장 마감 보충

15:30 이후 마지막 5분강도 수신시각이 정규장 마감 전인 종목은 마감 보충 대상으로 본다.

```text
S1 → Top20 → Hidden Top50 → Top300
```

- NXT 거래 종목: 애프터마켓 새 값이 있으면 갱신
- NXT 미거래 종목: 정규장 마감 기준 5분강도 표시
- 정상 마감값 수신 종목: 보충 완료
- 빈값·오류 종목: 종목별 최대 2회 재시도
- 애프터마켓 종료 이후: 보충 조회 중단

## 6. 선발모델 연결 상태

기본 모델:

```text
FIVE_FACTOR_FLOW_V01 / 5요소 수급선발 v0.1
```

드롭다운 선택은 snapshot/stream의 `candidate_model`로 worker에 전달된다. worker는 선택 모델을 `stockboard_ranking_engine.py`에 전달하고, 계산 결과에 표시순서 제어기를 적용한다.

| 모델 | 실행 상태 | 엔진 |
|---|---|---|
| 5요소 수급선발 v0.1 | 완료 | 전용 `FiveFactorFlowV01RankingEngine` |
| 순매수 강도 v0.2 · 5분강도 | 완료 | 전용 `NetBuyStrengthV02RankingEngine` |
| 순매수 강도 v0.1 · 5분강도 | 연결됨 | 공통 설정 엔진 |
| 돈쏠림 시작형 · 5분강도 | 연결됨 | 공통 설정 엔진 |
| 폭발 확인형 · 5분강도 | 연결됨 | 공통 설정 엔진 |
| 조용한 매집형 | 연결됨 | 공통 설정 엔진 |
| 프로그램 동행형 | 연결됨 | 공통 설정 엔진 |
| 시장대비 강도형 | 부분 구현 | 상대강도 전용 키 일부 미구현 |
| 현재 하드코딩 기준 | 부분 구현 | 원래 legacy 엔진과 정확한 분기 미완료 |

수정 완료된 1분강도 → 5분강도 항목:

- 순매수 강도 v0.2
- 순매수 강도 v0.1
- 돈쏠림 시작형
- 폭발 확인형

`one_min_trade_value_*`, `one_min_net_buy_value_*`는 1분강도가 아니라 1분 거래대금·순매수 흐름이므로 유지한다.

## 7. 5요소 수급선발 v0.1

점수 구성:

| 요소 | 배점 |
|---|---:|
| 거래대금 순위상승 | 20 |
| 전일 대비 거래대금 비율 | 20 |
| 순간 체결강도 | 15 |
| 프로그램 순매수 | 10 |
| 대량체결 순매수 | 10 |
| 요소 조합 품질 | 25 |
| 합계 | 100 |

제외 항목:

- 1분강도
- 잔량비

등급:

```text
A 90 이상
B 80 이상
C 70 이상
D 60 이상
F 60 미만
```

## 8. 행 위치 고정과 승강

Ranking Engine은 점수·등급·`model_rank`를 계속 갱신하지만 화면 행 위치는 `stockboard_display_order.py`가 관리한다.

```text
Top20 내부 위치 고정
Top300 내부 위치 고정
Top20 ↔ Top300 승강만 안전장치 적용
```

일반 승강:

- Top300 도전자가 모델 Top20을 5초 이상 유지
- Top20 기존 종목이 모델 30위 밖을 10초 이상 유지

강한 승강:

- 점수 차이 8점 이상
- 도전자와 이탈 조건을 각각 3초 이상 유지

공통 안전장치:

- 한 번에 1종목만 교체
- 교체 후 5초 cooldown
- 승격·강등 두 종목의 자리만 맞교환
- 나머지 행은 이동하지 않음

계산량은 최대 300행 O(n) 순회이며 점수 재계산이나 DOM 정렬을 추가하지 않는다.

## 9. 일중 상태 저장

일중 상태 파일:

```text
data/runtime/stockboard_v2/daily_state_YYYYMMDD.json
```

주요 복원 대상:

- 대량체결 건수·금액
- 프로그램 순매수
- 순간 체결강도
- 잔량비·호가
- 5분강도 관련 정상값

새 거래일 파일과 전 거래일 파일을 혼용하지 않는다.

## 10. 성능 정책

- collector는 OpenAPI 이벤트 수신을 최우선으로 한다.
- worker에서 상태·점수·행 위치를 계산한다.
- 5분강도는 단일 저속 scheduler만 사용한다.
- 다른 TR 작업이 있으면 5분강도 조회가 양보한다.
- 정규장 마감 보충은 별도 고속 루프가 아니라 기존 단일 큐를 사용한다.
- event log는 batch write를 사용한다.
- 브라우저는 표시 외 계산 책임을 가지지 않는다.

## 11. 진단값

API:

```text
/api/v2/health
/api/v2/snapshot?limit=300
/api/v2/stream
/api/v2/candidate_models
/api/v2/display_order
```

주요 상태:

```text
market_phase
market_phase_label
stream latency
event_log_queue_size
trade_count
dropped_trade_count
lagged_trade_warning_count
display_order.mode
display_order.swap_count
strength5m_scheduler.market_phase
strength5m_scheduler.market_accepts_query
strength5m_scheduler.regular_close_sweep_active
strength5m_scheduler.close_sweep_attempt_count
```

정상 기준:

- `display_order.mode = lane_stable`
- 점수 변화에도 Top20·Top300 내부 행 위치 유지
- q가 지속 증가하지 않음
- 09:00~09:05 가격·등락률·거래대금이 HTS와 일치 또는 근접
- 15:30 이후 NXT 미거래 종목의 5분강도가 순차적으로 채워짐

## 12. 현재 남은 작업

우선순위:

1. 시장대비 강도형의 `market_relative_change_rate`, `relative_strength_continuation`, `green_while_market_weak`, `near_high_hold` 계산 연결
2. `TVRANK_A_V03_TEMP`를 기존 legacy 하드코딩 엔진에 정확히 연결하거나 드롭다운에서 제거
3. 설정형 모델의 A등급 guard, 모델별 등급선, Top300 → Top50 → Top20 → Top5 단계 계산을 설계대로 엄밀히 적용
4. NXT 전일 통합 거래대금 원천 확보
5. 다음 정규장·애프터마켓에서 TR 부하와 마감 보충 완료율 재검증

## 13. 삭제·정리 정책

이번 2026-07-10 작업에서 생성된 다음 파일은 모두 현재 기능 또는 회귀 방지에 필요하므로 유지한다.

- `realtime_v2/strength5m_scheduler.py`
- `tests/test_stockboard_display_order.py`
- `tests/test_stockboard_strength5m_runtime.py`
- `tests/test_stockboard_strength5m_close_sweep.py`

임시 설계 문서, 중복 launcher, runtime snapshot, PID, 로그 파일은 Git에 추가하지 않는다.

기존에 제거된 구 v1 sidecar 파일은 다시 생성하지 않는다.

```text
stockboard_live_with_program_net.cmd
scripts/stockboard_program_net_snapshot.py
scripts/run_stockboard_program_net_snapshot.cmd
scripts/start_stockboard_program_net_sidecar_hidden.ps1
scripts/stop_stockboard_program_net_sidecar.ps1
scripts/stop_stockboard_program_net_sidecar.cmd
scripts/stockboard_speed_recorder.py
scripts/run_stockboard_speed_recorder.cmd
```

## 14. 검증 상태

코드·정책 검증:

- Ranking Engine 모델 분기 확인
- 모든 모델의 worker 선택 연결 확인
- 4개 기존 모델의 `strength_5m` 항목 연결 확인
- Top20·Top300 행 위치 고정 테스트 통과
- 안전 승강 1종목 교체 테스트 통과
- 시장 달력 기반 5분강도 조회 차단 테스트 통과
- 0·빈값 보호 테스트 통과
- 정규장 마감 보충 테스트 통과

대표님 실전 검증:

- 행 위치 고정 정상
- NXT 미거래 종목 5분강도 정상

병합 이력:

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력 정책
- PR #23: 정규장 마감 5분강도 보충
