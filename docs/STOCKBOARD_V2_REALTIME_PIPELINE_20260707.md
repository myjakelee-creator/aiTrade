# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-10

이 문서는 StockBoard v2의 현재 실시간 구조, 5분강도, 잔량비, 행 위치 정책, 장중/마감 표시 유지 정책과 운영 검증 상태를 기록하는 단일 기준 문서이다. 기존 StockBoard v0.3.x 기준 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신 속도와 표시 안정성을 확보하는 구조이다.

현재 실전 확인 완료 항목:

- 현재가·등락률·거래대금 실시간 표시
- Top20·Top300 내부 행 위치 고정
- 안전장치를 통과한 Top20 ↔ Top300 승강
- 열 제목 클릭 시 Focus/Pool 각각 정렬·역정렬
- 5요소 수급선발 v0.1 연결
- 1분강도 표시를 Kiwoom opt10046 기반 5분강도로 교체
- 같은 거래일 재시작 시 마지막 정상 5분강도 복원
- 새 거래일 재시작 시 전일 5분강도 미복원
- NXT 미거래 종목의 애프터마켓 정규장 마감 5분강도 보충
- 0·빈 응답·오류가 마지막 정상 5분강도를 덮어쓰지 않도록 보호
- 잔량비 마지막 정상값 cache, 빈칸 bootstrap, 애프터마켓 표시 유지
- AHK HTS bridge의 clipboard busy 방어와 explicit stop flag 방식

대표님 실전 확인:

- 행 위치 고정 정상
- Top20/Top300 정렬·역정렬 정상
- NXT 미거래 종목의 5분강도 정규장 마감값 표시 정상
- 잔량비는 느리지만 순차적으로 채워짐

현재 관찰 이슈:

- 최근 UI 안전 패치 누적으로 browser render 값이 50~60ms 수준으로 상승 관찰됨.
- 이는 잔량비 숫자 계산 자체보다 전체 300행 DOM 렌더, cell flash 비교, 컬럼폭/가로스크롤 보정, 선택 행 refocus, 연결 상태 판정 등 HTML render 함수 작업량 증가 영향이 크다.
- render 70ms 이상은 warning 기준으로 유지하고, 장중 검증 후 Pool 부분 렌더/가로스크롤 보정 빈도 축소를 검토한다.

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
→ realtime_v2/collector32_large_bidask.py
→ realtime_v2/collector32_large.py
→ TCP JSON event
→ realtime_v2/worker64_guarded_large_bidask.py
→ realtime_v2/worker64_guarded_large.py
→ SSE /api/v2/stream
→ docs/stockboard_v2.html
```

| 계층 | 파일 | 역할 |
|---|---|---|
| universe builder | `realtime_v2/build_universe.py` | 거래대금 universe 생성, tradable master 필터, 전일 거래대금 결합 |
| base collector | `realtime_v2/collector32.py` | Kiwoom 실시간 이벤트 수신 |
| large collector | `realtime_v2/collector32_large.py` | 대량체결 집계, 5분강도 정상값 저장, 0·빈값 보호 |
| bidask collector wrapper | `realtime_v2/collector32_large_bidask.py` | large collector에 잔량비 thin scheduler 설치 |
| base worker | `realtime_v2/worker64.py` | 상태 저장, snapshot API, SSE, event log |
| guarded worker | `realtime_v2/worker64_guarded.py` | 장상태, seed fallback, 누적값 역행 방어, 일중 복원 |
| large worker | `realtime_v2/worker64_guarded_large.py` | 대량체결·5분강도 표시와 대형 UI 연결 |
| bidask worker wrapper | `realtime_v2/worker64_guarded_large_bidask.py` | large worker에 잔량비 cache, display-hold, UI patch 설치 |
| 5분강도 scheduler | `realtime_v2/strength5m_scheduler.py` | S1/Top20/Hidden Top50/Top300 차등 opt10046 조회 |
| 잔량비 scheduler | `realtime_v2/orderbook_thin_scheduler.py` | S1/Top20/Hidden50/Top300 차등 opt10004 조회와 빈칸 bootstrap |
| 잔량비 cache | `realtime_v2/bidask_last_cache_patch.py` | 마지막 정상 잔량비 저장·복원·0값 덮어쓰기 방어 |
| 표시 유지 | `realtime_v2/display_hold_policy_patch.py` | 애프터·휴장·다음 프리마켓 전까지 주요 표시값 유지 |
| OHLC 가격 fallback | `realtime_v2/display_hold_ohlc_price_patch.py` | 현재가 공백 시 일봉 종가 기반 표시 보조 |
| HTML patch | `realtime_v2/html_header_sort_patch.py` | 열 정렬 복구, 연결 상태 badge 판정 보강 |
| 장상태 | `realtime_v2/market_session.py` | 프리마켓·정규장·애프터·휴장·특별일 판단 |
| 시장 달력 | `config/stockboard_market_calendar.json` | 기본 거래시간, 휴장일, 지연 개장 설정 |
| Ranking Engine | `stockboard_ranking_engine.py` | 선발모델 점수·등급·model_rank 계산 |
| 행 위치 제어 | `stockboard_display_order.py` | Top20·Top300 내부 위치 고정과 안전 승강 |
| UI | `docs/stockboard_v2.html` | worker 결과 표시 전용 |

중요 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- HTML은 worker가 제공한 값과 행 순서를 표시한다.
- `data/runtime/`은 Git 추적 금지이다.

## 4. 시장시간 정책

시간은 `config/stockboard_market_calendar.json`을 기준으로 하며 `special_days`가 있으면 특별일 시간이 우선한다.

| phase | 기본 시간 | 정책 |
|---|---|---|
| before_market | 08:00 전 | 신규 실시간·5분강도·잔량비 조회 차단, 전일 표시 유지 가능 |
| premarket | 08:00~08:30 | 실시간 및 저속 보조 조회 허용 |
| opening_call | 08:30~09:00 | 실시간 수용, 장초반 준비 |
| regular | 09:00~15:20 | 실시간 우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 정규장 마감값 보충 시작 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목은 정규장 마감값 유지 |
| closed | 20:00 이후 | 신규 조회 중단, 마지막값 유지 |
| weekend/holiday | 주말·휴장일 | 실시간 조회 차단, 다음 거래일 프리마켓 전까지 표시 유지 |

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

### 5.2 차등 조회 주기

| 구간 | 일반 | 정규장 개시 보호구간 |
|---|---:|---:|
| S1 | 30초 | 45초 |
| Top20 | 90초 | 120초 |
| Hidden Top50 | 300초 | 600초 |
| Top300 | 1,200초 | 1,800초 |

정규장 개시 보호구간은 달력의 `regular_start` 5분 전부터 10분 후까지다.

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

## 6. 잔량비 정책

잔량비는 현재 `opt10004` 단건 조회와 마지막 정상값 cache를 결합해 표시한다. 브라우저에서 잔량비를 계산하지 않는다.

### 6.1 조회 단위

```text
1회 조회 = 1종목 1회 opt10004
```

`opt10004` 자체는 종목코드 1개를 넣는 단건 TR이므로 1회 요청으로 5종목·20종목을 동시에 받을 수 없다. Top300을 더 빠르게 채우려면 향후 `SetRealReg` 호가잔량 10~20종목 회전등록을 별도로 설계해야 한다.

### 6.2 마지막 정상값 저장

정상 양수 잔량비만 cache에 저장한다. 0·빈값·오류는 기존 정상값을 덮어쓰지 않는다.

```text
data/runtime/stockboard_v2/bid_ask_ratio_last.json
```

### 6.3 차등 조회와 bootstrap

| 구간 | 일반 주기 | 빈칸 재시도 | 09:00~09:10 거래량 폭탄 구간 |
|---|---:|---:|---|
| S1 | 5초 | 6초 | 최소 보조, 30초 계열 |
| Top20 | 45초 | 18초 | 빈칸일 때만 매우 저속 보조 |
| Hidden50 | 300초 | 45초 | 신규 조회 금지 |
| Top300 | 1,200초 | 75초 | 신규 조회 금지, cache/display-hold 표시만 |

Top300 빈칸이 Top20 정기 갱신에 밀려 영원히 대기하지 않도록, 일반 구간의 빈칸 bootstrap은 다음 패턴으로 돈다.

```text
Top20 → Hidden50 → Top300 → Top300
```

단, S1은 항상 최우선이다.

### 6.4 09:00~09:10 정책

거래량 폭탄 구간에는 Top300 잔량비를 새로 채우려고 하지 않는다.

```text
09:00~09:10:
- Top300 opt10004 조회 금지
- Hidden50 opt10004 조회 금지
- Top300 SetRealReg 대량 회전등록 금지
- S1 최소 조회
- Top20 빈칸 저속 보조
- Top300은 last-cache / display-hold / 직전 정상값 표시
```

이 구간의 목표는 “새로 채우기”가 아니라 “마지막 정상값으로 빈칸을 막기”이다. 실제 Top300 신규 보충은 09:10 이후 재개한다.

## 7. 5분강도와 잔량비 균형

| 항목 | 5분강도 | 잔량비 |
|---|---|---|
| 성격 | 느린 체결 흐름 지표 | 빠르게 변하는 호가 상태 지표 |
| 주요 원천 | opt10046 | opt10004, 향후 SetRealReg 검토 |
| Top20 중요도 | 높음 | 매우 높음 |
| Top300 중요도 | 중간 | 빈칸 시 시각적 문제 큼 |
| 09:00~09:10 | S1/Top20 중심, 추가 제한 검토 | Top300/Hidden50 신규조회 금지 |
| 빈값 보호 | 0·빈값이 정상값 덮지 않음 | 0·빈값이 정상값 덮지 않음 |

현재 평가는 다음과 같다.

- 09:10 이후 빈칸 메우기 측면에서는 잔량비가 5분강도보다 우대된다.
- 이는 잔량비가 원래 거래 종목이면 있어야 하는 값이고, 수동매매 판단과 화면 가독성에 직접 영향을 주기 때문이다.
- 09:00~09:10에는 잔량비 Top300을 강하게 제한했으므로, 다음 개장 검증에서 5분강도도 같은 수준으로 제한할지 판단한다.

## 8. 선발모델 연결 상태

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

`one_min_trade_value_*`, `one_min_net_buy_value_*`는 1분강도가 아니라 1분 거래대금·순매수 흐름이므로 유지한다.

## 9. 5요소 수급선발 v0.1

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

현재 이 모델의 점수에는 1분강도와 잔량비를 넣지 않는다.

## 10. 행 위치 고정과 승강

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

## 11. 일중 상태 저장과 표시 유지

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
- OHLC와 가격 fallback

표시 유지 정책:

```text
애프터마켓·장마감·주말·공휴일·다음 거래일 08:00 전까지 마지막 정상 표시값 유지
프리마켓 시작 후 새 값 우선
```

## 12. 성능 정책

- collector는 OpenAPI 이벤트 수신을 최우선으로 한다.
- worker에서 상태·점수·행 위치를 계산한다.
- 5분강도는 단일 저속 scheduler만 사용한다.
- 잔량비는 단일 thin scheduler만 사용하며 한 번에 1종목 opt10004만 보낸다.
- 다른 TR inflight가 있으면 잔량비 조회는 양보한다.
- 09:00~09:10에는 잔량비 Top300/Hidden50 신규 조회를 금지한다.
- 브라우저는 표시 외 계산 책임을 가지지 않는다.
- render 값 50~60ms 상승은 잔량비 계산 자체보다 DOM 렌더와 UI 안전 패치 누적 영향으로 본다.
- render가 지속적으로 70ms를 넘으면 Pool rerender 빈도와 가로스크롤 보정 호출을 줄인다.

## 13. 진단값

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
event_log_queue_size
trade_count
dropped_trade_count
display_order.mode
display_order.swap_count
strength5m_scheduler.*
orderbook_thin_scheduler.*
bidask_cache_count
bidask_cache_applied_rows
display_hold_active
display_hold_applied_rows
```

주의:

- `stockboard_v2_large.cmd status`는 현재 snapshot을 `limit=5`로 조회하므로 `ROW_COUNT=5`는 전체 universe가 5개라는 뜻이 아니다.
- 상단 연결 상태는 collector 연결, 등록 수, sender 연결, 마지막 이벤트 시각을 함께 본다.
- 애프터마켓은 실제 이벤트가 뜸할 수 있어 “연결 지연 n초”가 곧 장애를 의미하지는 않는다. 애프터 전용 문구는 추가 개선 대상이다.

정상 기준:

- `display_order.mode = lane_stable`
- 점수 변화에도 Top20·Top300 내부 행 위치 유지
- q가 지속 증가하지 않음
- 09:00~09:05 가격·등락률·거래대금이 HTS와 일치 또는 근접
- Top300 잔량비는 09:10 이후 순차적으로 채워짐
- 15:30 이후 NXT 미거래 종목의 5분강도와 잔량비 표시값이 유지 또는 순차 보충됨

## 14. 현재 남은 작업

우선순위:

1. 다음 정규장 09:00~09:10에서 체결 수신 지연, render 70ms 초과 여부, 잔량비/5분강도 제한 정책 검증
2. 09:10 이후 Top300 잔량비 bootstrap 완료율과 속도 검증
3. render 50~60ms 지속 시 Pool 부분 렌더·가로스크롤 보정 호출 축소
4. 애프터마켓 연결 상태 문구를 “연결지연”이 아닌 “애프터 저유동 · 최근수신 n초” 계열로 조정
5. 시장대비 강도형의 `market_relative_change_rate`, `relative_strength_continuation`, `green_while_market_weak`, `near_high_hold` 계산 연결
6. `TVRANK_A_V03_TEMP`를 기존 legacy 하드코딩 엔진에 정확히 연결하거나 드롭다운에서 제거
7. NXT 전일 통합 거래대금 원천 확보
8. Top300 잔량비 고속 초기 채우기가 필요하면 09:10 이후 SetRealReg 10~20종목 회전등록 별도 설계

## 15. 삭제·정리 정책

이번 2026-07-10 작업에서 생성·갱신된 다음 파일은 현재 실행 경로에 걸려 있으므로 유지한다.

- `realtime_v2/collector32_large_bidask.py`
- `realtime_v2/worker64_guarded_large_bidask.py`
- `realtime_v2/bidask_last_cache_patch.py`
- `realtime_v2/orderbook_thin_scheduler.py`
- `realtime_v2/display_hold_policy_patch.py`
- `realtime_v2/display_hold_ohlc_price_patch.py`
- `realtime_v2/html_header_sort_patch.py`
- `realtime_v2/strength5m_scheduler.py`
- `stockboard_v2_large.cmd`
- `scripts/stockboard_kiwoom_link_v1.ahk`

현재 삭제하지 않는 이유:

- wrapper 파일은 fail-open 구조와 기존 large worker/collector 보호를 위해 필요하다.
- patch 파일은 기능별 회귀 방지와 원인 분리를 위해 당분간 분리 유지한다.
- 다음 정규장 검증 후 안정화되면 `display_hold_ohlc_price_patch.py` 일부는 `display_hold_policy_patch.py`로 통합할 수 있다.

Git에 넣지 않을 것:

- `data/runtime/`
- `*.pid`
- `*.log`
- `*_error.txt`
- runtime snapshot/cache json

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

## 16. 검증 상태

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
- Top20/Top300 열 정렬 정상
- NXT 미거래 종목 5분강도 정상
- 잔량비는 느리지만 순차 채움 확인

병합 이력:

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력 정책
- PR #23: 정규장 마감 5분강도 보충
