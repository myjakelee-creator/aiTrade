# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-11

이 문서는 StockBoard v2의 실시간 구조, 시장시간 정책, 5분강도, 잔량비 표시, 선발모델, Funnel, 행 위치와 실전 검증 상태를 기록하는 단일 기준 문서이다. 기존 StockBoard v0.3.x 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신과 표시를 안정화하는 구조이다.

현재 완료 상태:

- 현재가·등락률·거래대금 실시간 표시
- 5분강도 opt10046 차등 조회, 당일 복원, 정규장 마감 보충
- 잔량비 opt10004 저속 조회와 마지막 정상값 유지
- Top20·Top300 내부 행 위치 고정
- 안전장치를 통과한 Top20 ↔ Top300 승강
- 하드코딩 기준을 제외한 설정형 선발모델 8개 실행
- 지원하지 않는 점수항목을 자동 0점 처리하지 않는 설정 검증
- 실제 Top300 → Top50 → Top20 → Top5 Funnel
- HTML 계산 금지, worker 결과 표시 전용

대표님 실전 확인:

- 행 위치 고정 정상
- Top20·Top300 정렬과 역정렬 정상
- NXT 미거래 종목의 정규장 마감 5분강도 표시 정상
- 잔량비 저속 순차 보충 정상
- 2026-07-11 선발모델 v3 적용 후 기존 선발기준 정상 작동 확인

## 2. 기준 브랜치와 실행

| 항목 | 값 |
|---|---|
| 기준 브랜치 | `hot-priority-integrated-20260630` |
| 대형 실행기 | `stockboard_v2_large.cmd` |
| 기본 실행기 | `stockboard_v2_live.cmd` |
| 화면 주소 | `http://127.0.0.1:8765/` |
| worker HTTP 포트 | `8765` |
| collector → worker TCP 포트 | `8710` |

최신 코드 받기와 실행:

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
→ stockboard_candidate_config.py
→ stockboard_candidate_features.py
→ stockboard_candidate_engine.py
→ stockboard_display_order.py
→ SSE /api/v2/stream
→ docs/stockboard_v2.html
```

| 계층 | 파일 | 역할 |
|---|---|---|
| universe | `realtime_v2/build_universe.py` | 거래대금 universe와 전일 거래대금 결합 |
| 실시간 수집 | `realtime_v2/collector32.py` | Kiwoom 실시간 이벤트 수신 |
| 대형 수집 | `realtime_v2/collector32_large.py` | 대량체결 집계와 5분강도 정상값 저장 |
| 잔량비 wrapper | `realtime_v2/collector32_large_bidask.py` | 잔량비 scheduler 설치 |
| worker | `realtime_v2/worker64_guarded_large.py` | 상태·5분강도·대량체결·점수 결과 제공 |
| 잔량비 worker | `realtime_v2/worker64_guarded_large_bidask.py` | 잔량비 cache와 표시 유지 |
| 5분강도 | `realtime_v2/strength5m_scheduler.py` | S1·Top20·Hidden50·Top300 차등 opt10046 조회 |
| 잔량비 | `realtime_v2/orderbook_thin_scheduler.py` | 저속 opt10004 단건 조회 |
| 시장시간 | `realtime_v2/market_session.py` | 프리·정규·애프터·휴장·특별일 판단 |
| 시장달력 | `config/stockboard_market_calendar.json` | 기본 거래시간과 특별일 설정 |
| 모델 설정 | `configs/candidate_models/*.json` | 모델별 점수·guard·Funnel |
| 설정 검증 | `stockboard_candidate_config.py` | registry·지원키·가중치 검증 |
| 공통 파생값 | `stockboard_candidate_features.py` | 승인된 원천에서 공통 지표 1회 계산 |
| 모델 실행 | `stockboard_candidate_engine.py` | 점수·등급·Coverage·Funnel 계산 |
| 호환 진입점 | `stockboard_ranking_engine.py` | 기존 import API 유지 |
| 행 위치 | `stockboard_display_order.py` | lane 내부 고정과 안전 승강 |
| UI | `docs/stockboard_v2.html` | worker 결과 표시 전용 |

운영 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- 선택된 선발모델 한 개만 worker에서 계산한다.
- 선발모델 때문에 신규 OpenAPI 조회를 추가하지 않는다.
- `data/runtime/`은 Git 추적 대상이 아니다.

## 4. 시장시간 정책

시장시간은 `config/stockboard_market_calendar.json` 기준이며 `special_days`가 있으면 특별일 설정이 우선한다.

| phase | 기본 시간 | 정책 |
|---|---|---|
| before_market | 08:00 전 | 신규 실시간·보조조회 차단 |
| premarket | 08:00~08:30 | 실시간·저속 보조조회 허용 |
| opening_call | 08:30~09:00 | 실시간 수용 |
| regular | 09:00~15:20 | 실시간 우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 정규장 마감값 보충 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목은 마감값 유지 |
| closed | 20:00 이후 | 신규조회 중단, 마지막값 유지 |
| weekend/holiday | 주말·휴장일 | 조회 차단 |

## 5. 5분강도 정책

표시 원천은 Kiwoom opt10046 `strength_5m`이며 브라우저는 계산하지 않는다.

| 상황 | 정책 |
|---|---|
| 같은 거래일 재시작 | 당일 마지막 정상값 복원 |
| 새 거래일 08:00 전 재시작 | 전일값 미복원, `-` 표시 |
| 프로세스가 날짜를 넘어 계속 실행 | 메모리값 유지 후 프리마켓 새 값으로 교체 |
| 0·빈값·오류 | 마지막 정상값을 덮어쓰지 않음 |
| 15:30 이후 NXT 미거래 | 정규장 마감 5분강도 보충 |

조회 우선순위:

```text
S1 → Top20 → Hidden Top50 → Top300
```

다른 TR이 진행 중이면 양보하고, 마감 보충 실패는 종목별 최대 2회만 재시도한다.

## 6. 잔량비 정책

잔량비는 화면에 표시하지만 **현재 모든 선발모델의 점수에서는 제외**한다.

- opt10004 단건 저속 조회
- 마지막 정상 양수값 cache
- 0·빈값·오류가 정상값을 덮어쓰지 않음
- 09:00~09:10 Hidden50·Top300 신규조회 제한
- 신뢰성과 속도 검증 후 새 모델 버전에서만 점수 재검토

## 7. 선발모델 v3 입력 원천

점수에 사용할 수 있는 원천은 다음으로 제한한다.

```text
순위 / 전일 / 현재가 / 등락률 / 금액(억) / 대금비 / 일봉
순간강도 / 5분강도 / 프로(억) / 대량체결
```

점수 제외:

```text
잔량비 / 1분강도 / 1분 거래대금 파생 / 1분 순매수 파생
VWAP / 외인 / 기관 / 미검증 시장지수
```

설정에 지원하지 않는 키가 있으면 해당 모델을 `INVALID`로 판정해 드롭다운에서 제외한다. 미지원 키를 조용히 0점 처리하지 않는다.

## 8. 등록 선발모델

하드코딩 `TVRANK_A_V03_TEMP`는 registry와 설정파일에서 제거했다.

| 모델 | 핵심 목적 | 상태 |
|---|---|---|
| 5요소 수급선발 v0.1 | 순위상승·대금비·강도·프로·대량체결 조합 | READY |
| 순매수 강도 v0.2 · 5분강도 | 프로그램·대량체결 비중 강화 | READY |
| 순매수 강도 v0.1 · 5분강도 | 수급 요소 균형 | READY |
| 돈쏠림 시작형 · 5분강도 | 순위와 대금비가 막 상승하는 종목 | READY |
| 조용한 매집형 | 낮은 과열도에서 프로그램·대량체결 누적 | READY |
| 폭발 확인형 · 5분강도 | 대금·강도·고가권 동시 확인 | READY |
| 프로그램 동행형 | 프로그램과 실제 체결수급 동행 | READY |
| 보드 대비 상대강도형 | Top300 중앙 등락률 대비 상대강도 | READY |

기본 모델은 `FIVE_FACTOR_FLOW_V01`이다.

## 9. 점수·Coverage·등급

각 모델은 `final_score`, `entry_score`, `confirmation_score`, `focus_score`를 가진다. 단계별 양수 가중치 합은 100이며 음수 가중치는 승인된 penalty 항목만 허용한다.

```text
candidate_score = Σ(항목점수 × 원래 가중치)
coverage = 정상 원천을 가진 양수 가중치 합 / 전체 양수 가중치
```

| Coverage | 최고점 제한 |
|---:|---:|
| 90% 이상 | 정상 |
| 75~89% | 79점 |
| 60~74% | 69점 |
| 60% 미만 | 59점·WAIT_DATA |

필수 원천이 누락되거나 stale이면 59점 이하이며 승격 대상에서 제외한다.

| 점수 | 등급 |
|---:|---|
| 90 이상 | A |
| 80 이상 | B |
| 70 이상 | C |
| 60 이상 | D |
| 60 미만 | F |

모델별 guard는 목적에 반하는 상황에서 최고점을 제한한다. 예를 들어 프로그램 동행형에서 프로그램 순매도이면 59점 이하로 제한한다.

## 10. 실제 Funnel과 행 위치

```text
Top300 전체 entry_score
→ Entry Top50
→ Top50 안에서 confirmation_score Top20
→ Top20 안에서 focus_score Top5
→ desired_top20
→ DisplayOrderController 안전 승강
```

출력 필드:

```text
entry_rank / confirmation_rank / focus_rank / model_rank
pool_stage / is_candidate / desired_top20
```

화면 정책:

- Top20 내부 행 위치 고정
- Top300 내부 행 위치 고정
- 한 번에 1종목만 승강
- 도전자·이탈 유지시간과 점수차 확인
- 교체 후 5초 cooldown
- 승격·강등 두 종목의 자리만 교환

## 11. 성능 정책

- 최대 300행 공통 파생값을 snapshot당 한 번 계산한다.
- 선택된 모델 한 개만 계산한다.
- 최대 300행 정렬 3회 수준이며 OpenAPI 수신경로와 분리한다.
- HTML은 점수 계산을 하지 않는다.
- 신규 sidecar·launcher·TR scheduler를 만들지 않는다.
- 5분강도와 잔량비의 기존 저속 조회정책을 유지한다.
- 합성 300행 기준 모델 계산 중앙값은 약 35~51ms였다.
- 실제 장중에는 stream latency와 queue 증가 여부를 계속 확인한다.

## 12. 검증 상태

자동 검증:

- 등록 모델 8개, 하드코딩 모델 0개
- 모든 설정 validation error 0개
- 잔량비·1분·VWAP·미지원 키 0개
- 기존 5요소·순매수 v0.2 호환 필드 유지
- 60종목 Top50 → Top20 → Top5 Funnel
- 보드 상대강도 실제 계산
- Coverage 부족 시 WAIT_DATA와 승격 차단
- 프로그램 순매도 guard
- stale 순간강도·프로그램 점수 차단

실전 검증:

- `/api/v2/candidate_models`에 8개 모델 표시
- 선발기준 변경 시 모델 점수와 등급 갱신
- 실제 Funnel과 기존 행 고정 정책이 함께 작동
- 하드코딩 기준 미표시
- 대표님이 2026-07-11 정상 작동을 확인함

## 13. 다음 검증·조정

- 다음 프리마켓·정규장에서 모델별 점수 분포와 A/B 등급 종목을 HTS와 대조
- 모델 전환 시 worker 계산시간, stream latency, queue 증가 여부 확인
- 배점 조정이 필요하면 수집경로를 바꾸지 않고 JSON 설정만 수정
- 잔량비는 현재 점수 제외 상태를 유지

## 14. 병합 이력

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력 정책
- PR #23: NXT 미거래 종목 정규장 마감 5분강도 보충
- PR #24: StockBoard v2 기준 문서 갱신
- PR #25: 설정형 선발모델 8개·검증기·Coverage·실제 Funnel
