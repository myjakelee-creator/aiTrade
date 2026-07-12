# StockBoard·ThemeBoard 통합 수집 및 장마감 복구

최종 갱신: 2026-07-12 · 구현 완료 및 대표님 실전 정상 확인 반영  
상태: 정책 승인 · 구현 완료 · 자동 검증 완료 · 대표님 실전 정상 작동 확인 · 시간대별 장기 관찰 계속

## 1. 목적과 확정 원칙

StockBoard 14개 항목과 ThemeBoard는 같은 종목 원천을 공유한다. 각 화면이 별도로 수집·조회·계산하면 OpenAPI 충돌, 중복 부하, 값 불일치가 생기므로 공용 상태와 중앙 복구 구조를 사용한다.

확정 원칙:

```text
1. 종목 원천값은 한 번만 수집한다.
2. StockBoard와 ThemeBoard가 같은 공용 종목 상태를 사용한다.
3. 장중 실시간 callback에는 보드 계산·파일 저장·TR enqueue를 추가하지 않는다.
4. 장마감 OpenAPI 조회는 중앙 coordinator 한 곳만 실행한다.
5. 정확값이 없으면 마지막 정상값과 분봉 근사까지 사용한다.
6. 실제 무거래와 결측을 구분한다.
7. 원천 실패는 직전값 또는 복구실패로 표시한다.
8. 정확값과 추정값을 source·quality·is_estimated로 구분한다.
9. 낮은 품질값은 높은 품질값을 덮어쓰지 않는다.
10. KRX fallback은 정확한 통합장 AL 값을 덮어쓰지 않는다.
```

분봉 최종 근사:

```text
1분 추정 거래대금 = 해당 1분봉 종가 × 해당 1분봉 거래량
5분 추정 거래대금 = Σ(각 1분봉 종가 × 해당 1분봉 거래량)
```

근사값은 표시용 fallback이며 다음 거래일 실시간 SURGE·후보모델의 정확 원천으로 자동 승격하지 않는다.

## 2. 구현 구조

```text
Kiwoom 실시간·TR
→ 32bit collector
→ 공용 종목 상태 + source metadata
→ 64bit worker
├─ StockBoard 종목 계산·Funnel
└─ ThemeBoard 테마 가중 집계

정규장·통합장 마감 직전
→ CloseWindowSamplerService
→ 정확한 종목 1분·5분 누적대금 차이
→ ThemeBoard 정확 마감 유입

장마감 누락
→ AfterCloseRecoveryCoordinator
→ 전역 OpenAPI arbitration
→ 한 시점에 TR 1개
→ 공용 종목 상태 갱신
→ 두 보드가 같은 복구값 사용
```

금지:

- ThemeBoard나 64비트 worker가 직접 OpenAPI TR 호출
- 독립 scheduler의 동시 burst 요청
- 추정값이 정확값을 덮어쓰기
- KRX fallback이 AL 정확값을 덮어쓰기
- HTML에서 임의 숫자·점수·fallback 생성
- `0`을 결측값으로 사용
- 별도 포트·별도 launcher 추가

## 3. 공용 값 계약

각 복구·보존 필드는 숫자와 다음 metadata를 함께 가진다.

```text
value
source
status
basis_time
trading_date
market_scope
quality
is_estimated
updated_at
coverage
```

예:

```json
{
  "value": 182.4,
  "source": "MINUTE_CLOSE_X_VOLUME",
  "status": "ESTIMATED",
  "basis_time": "20260710200000",
  "trading_date": "20260710",
  "market_scope": "KRX",
  "quality": 0.65,
  "is_estimated": true,
  "updated_at": "2026-07-12T20:10:00",
  "coverage": 1.0
}
```

표시:

```text
182억       정확값
≈182억      추정값
182억*      일부 구성종목만 복구
거래없음    실제 해당 구간 거래량 0
직전값      마지막 정상값 유지
복구실패    조회·저장 원천 모두 실패
```

부분 데이터를 100%로 비례 확대하지 않는다.

## 4. source 품질과 보호

기본 품질:

```text
LIVE_REALTIME           1.00
CLOSE_SAMPLER_EXACT     1.00
CLOSE_HISTORY_EXACT     1.00
TR_TRADE_VALUE          0.95
OPT10046                0.95
OPT10004                0.95
PROGRAM_BATCH           0.95
MINUTE_BAR_CLOSE        0.90
PERSISTED_LAST_VALID    0.80
MINUTE_CLOSE_X_VOLUME   0.65
PREVIOUS_DAY_DISPLAY    0.40
```

보호 규칙:

- 높은 quality를 낮은 quality가 덮어쓰지 않는다.
- 같은 quality면 최신 basis_time을 우선한다.
- 다른 trading_date는 명시적 rollover 외에는 덮어쓰지 않는다.
- `AL/INTEGRATED` 정확값을 `KRX` fallback이 덮어쓰지 않는다.
- 정확값이 있으면 같은 항목의 분봉 fallback을 요청하지 않는다.
- 0·빈값·오류가 기존 정상 양수 강도·잔량비를 지우지 않는다.
- 음수 프로그램·대량체결 net은 정상값이다.

## 5. StockBoard 14개 항목

| 항목 | 장중 원천 | 장마감·휴장 우선순위 |
|---|---|---|
| 순위 | 누적 거래대금 정렬 | 마지막 정상 순위 유지 |
| 전일 | universe 전일 순위 | 그대로 유지 |
| 등급 | 선택 후보모델 | 마지막 정상 결과, 추정 원천 자동 승격 금지 |
| 종목명 | universe/master | 그대로 유지 |
| 현재가 | 실시간 체결 | 마감가 → OHLC/분봉 종가 → 직전값 |
| 등락률 | 실시간 체결·전일종가 | 마감가 기준 재계산 → 직전값 |
| 금액(억) | 실시간 누적대금 | 마지막 누적 → 검증된 원천 → 직전값 |
| 대금비 | 당일÷전일 | 복구 당일대금으로 재계산, quality 상속 |
| 일봉 | 장중 OHLC | OHLC snapshot → 분봉 OHLC → 직전값 |
| 잔량비 | 실시간 호가 | 정규장 마감·last valid → opt10004 → 직전값 |
| 순간강도 | 실시간 체결강도 | last valid → opt10046 → 직전값 |
| 5분강도 | opt10046·당일 cache | snapshot → opt10046 → 직전값 |
| 프로(억) | program batch | 당일 snapshot → 시장별 batch → 직전값 |
| 대량체결 | collector 집계 | daily state → event/tick log → 직전값 |

대량체결은 분봉 거래량으로 추정하지 않는다.

## 6. ThemeBoard 장마감 복구

| 항목 | 계산·fallback |
|---|---|
| 테마 누적대금 | 복구된 종목 누적대금 × 가중치 합 |
| 1분 유입 | exact sampler → 종목별 분봉 fallback 가중 합 |
| 5분 유입 | exact sampler → 각 종목 5개 분봉 fallback 가중 합 |
| 시장 점유 | 복구 snapshot으로 재계산 |
| 확산 | 마지막 정상 등락률 유지 |
| 유입 가속 | 복구 1분·5분 값으로 계산, 추정 여부 상속 |
| 집중도 | 복구 종목 가중 대금 비중 |
| 주도주 점수 | 원천별 quality·freshness 반영 |
| 역할·상태 | 마지막 정상값 유지 또는 서버 재계산 |

Coverage:

```text
coverage = 확보된 구성종목 가중치 합 / 전체 구성종목 가중치 합
```

표시 예:

```text
정확값만: +1,284억
추정 포함: ≈+1,284억
부분 복구: +1,284억* · Coverage 83%
```

## 7. 마감 sampler

서비스: `CloseWindowSamplerService`

실행 구간:

```text
정규장: 15:24:50~15:30:10
통합장: 19:54:50~20:00:10
```

1초마다 복사하는 최소 필드:

```text
stock_code
trade_value_eok
sample_time
market_scope
```

처리:

```text
worker lock acquire(False)
→ 성공: 테마 구성종목 최소 필드만 복사
→ 실패: 즉시 양보
→ lock 밖: ring buffer 기록·종목/테마 계산
→ 구간 종료: 작은 JSON 원자적 저장
→ worker daily state와 ThemeBoard cache 즉시 반영
```

경계값은 목표 시각과 같거나 그 이전의 가장 가까운 정상 sample만 사용한다. 목표 이후 sample을 과거 기준값으로 사용하지 않는다.

runtime:

```text
data/runtime/stockboard_v2/close_flow_sampler_last.json
data/runtime/stockboard_v2/close_flow_sampler_YYYYMMDD_regular.json
data/runtime/stockboard_v2/close_flow_sampler_YYYYMMDD_integrated.json
```

## 8. 중앙 복구 coordinator

명칭:

```text
AfterCloseRecoveryCoordinator
controller=after_close_recovery_coordinator_v1
```

위치:

- 32비트 OpenAPI collector 계층
- QAx owner thread
- 기존 독립 Qt timer가 tick 구동
- 기존 provider inflight·pending·last_request 상태 재사용

논리 bundle:

```text
minute_bar opt10080
strength opt10046
program batch snapshot
OHLC existing snapshot + minute fallback
orderbook opt10004
large trade persisted aggregate
```

공용 idle 판정:

```text
login_state=connected
provider running/control ready
strength inflight/pending 없음
orderbook inflight/pending 없음
minute inflight/pending 없음
opt10055/close metrics inflight/pending 없음
마지막 전체 TR 요청 gap 충족
시장 phase 허용
```

한 조건이라도 충족하지 않으면 신규 요청을 보내지 않는다.

기존 strength·orderbook scheduler도 같은 provider-level arbitration을 사용한다. 장마감의 기존 off-hours completion controller는 중앙 coordinator로 대체되며 resilience와 시장 틱 비의존 timer는 유지한다.

## 9. opt10080 분봉 fallback

요청:

```text
TR: opt10080
틱범위: 1분
수정주가구분: 1
1차: CODE_AL
2차: CODE KRX fallback
```

복구:

```text
최근 종가
1분 거래대금
5분 거래대금
최근 5분 OHLC
basis_time
market_scope
coverage
```

계산:

```text
각 1분 amount = abs(close) × abs(volume) / 100,000,000
1분 = 최신 1개 amount
5분 = 최신 최대 5개 amount 합
```

금지:

```text
마지막 종가 × 5분 전체 거래량
```

거래량 0인 유효 분봉만 있는 경우:

```text
minute_recovery_status=ok
minute_recovery_trade_status=no_trade
표시=거래없음
```

`_AL` 실패 후 KRX fallback을 사용하면 `market_scope=KRX`와 낮은 quality를 유지해 통합장 정확값과 혼합하지 않는다.

## 10. 종목 우선순위와 중복 제거

| 우선순위 | 대상 |
|---:|---|
| P0 | S1 선택 종목 |
| P1 | StockBoard Top20 |
| P2 | 상위 5개 테마 주도주 TOP3 |
| P3 | 상위 5개 테마 구성종목 |
| P4 | 나머지 테마 구성종목 |
| P5 | Hidden Top50 |
| P6 | 나머지 Top300 |

현재 universe에 없는 테마 마스터 종목은 복구 계획에서 제거한다.

동일 종목이 여러 역할을 가져도 다음처럼 bundle별 한 번만 조회한다.

```text
S1 + Top20 + 테마주도주 000660
→ minute 1회
→ opt10046 1회
→ opt10004 1회
→ program은 batch 공유
```

## 11. 시간대·요청 간격·재시도

| 시간 | 작업 |
|---|---|
| 15:24:50~15:30:10 | 정규장 sampler |
| 15:30~15:40 | 기존 마감값 확정 |
| 15:40~19:54 | 애프터 실시간 우선 |
| 19:54:50~20:00:10 | 통합장 sampler |
| 20:00~20:03 | pending 정리·누락 계획 |
| 20:03~21:00 | P0~P3 복구, 기본 3초 간격 |
| 21:00 이후 | P4~P6 저속 복구, 기본 10초 간격 |
| 프리마켓 30분 전 | 중요 종목 3초 간격 최종 pass |
| 프리마켓 5분 전 | 신규 전일 복구 중단 |
| 프리마켓 시작 | 전일 hold·복구 state 해제 |

재시도:

```text
1차 실패 → 5분
2차 실패 → 30분
3차 이후 → 2시간
연속 오류 3회 → 전체 30분 backoff
```

다음 프리마켓은 시장 달력 기반 `next_premarket_datetime()`으로 계산한다.

## 12. 복구 state와 재시작

runtime:

```text
data/runtime/stockboard_v2/after_close_recovery_state.json
```

보존 항목:

- minute recovery 결과·거래일·scope
- 1분·5분 거래대금과 OHLC
- source metadata
- sampler 거래일·basis_time
- terminal `거래없음/직전값/복구실패`

저장:

- 변경분을 메모리에 모아 기본 5초 간격으로 원자적 저장
- 재시작 시 유효한 state를 daily state에 복원
- 다음 실제 premarket 시각에 자동 만료·삭제
- sampler가 종료 직전 exact 값을 추가할 수 있으므로 최종 state 저장은 sampler 종료 뒤 수행

ThemeBoard의 `theme_last_close.json`도 같은 프리마켓 경계까지 유지한다.

## 13. 실제 구현 파일

핵심:

```text
realtime_v2/after_close_recovery.py
realtime_v2/after_close_recovery_hardening.py
realtime_v2/after_close_recovery_sampler_guard.py
realtime_v2/after_close_recovery_policy_guard.py
realtime_v2/after_close_recovery_state.py
realtime_v2/after_close_theme_recovery.py
```

설치:

```text
realtime_v2/collector32_large_bidask.py
realtime_v2/board_platform/__init__.py
realtime_v2/worker64_guarded_large_bidask.py
```

재사용:

```text
realtime_v2/strength5m_scheduler.py
realtime_v2/orderbook_thin_scheduler.py
realtime_v2/offhours_metric_resilience_patch.py
realtime_v2/offhours_metric_timer_driver_patch.py
realtime_v2/theme_board_patch.py
realtime_v2/context_snapshot_writer.py
stockboard_theme_engine.py
```

별도 포트·별도 launcher·HTML 계산은 추가하지 않았다.

## 14. 단계별 구현 결과

| 단계 | 결과 |
|---|---|
| 1. source metadata 계약 | 완료 |
| 2. 정규장·통합장 마감 sampler | 완료 |
| 3. 공용 OpenAPI arbitration | 완료 |
| 4. minute-bar fallback | 완료 |
| 5. AfterCloseRecoveryCoordinator | 완료 |
| 6. 기존 scheduler·snapshot 점진 편입 | 완료 |
| 7. ThemeBoard Coverage·표시 publish | 완료 |
| 8. 복구 state 재시작 보존 | 완료 |
| 9. 정확 sampler·무거래·정책 guard | 완료 |

## 15. 자동 검증

전용 테스트:

```text
tests/test_after_close_recovery.py
tests/test_after_close_recovery_hardening.py
tests/test_after_close_recovery_sampler_guard.py
tests/test_after_close_recovery_policy_guard.py
tests/test_after_close_recovery_state.py
tests/test_after_close_theme_recovery.py
```

검증 항목:

- 정확한 AL 값이 KRX 추정값에 덮이지 않음
- 다른 거래일은 명시적 rollover 필요
- 각 1분봉 종가×거래량을 별도 계산해 합산
- 5분 coverage 산출
- 무거래와 결측 구분
- sampler 정확 1분·5분 저장과 ThemeBoard 즉시 publish
- sampler 목표 이후 sample 사용 금지
- 당일 exact sampler 값이 있으면 minute fallback 생략
- stale 이전 거래일 복구 결과 거부
- P0~P6 우선순위·중복 제거
- universe 밖 테마 종목 제거
- 한 번에 competing TR 1개 arbitration
- all-lane orderbook 복구
- 부분 Coverage 비례 확대 금지
- `≈`, `*`, `거래없음` 서버 표시
- terminal `직전값/복구실패` 기록
- 복구 state 재시작 복원·premarket 만료
- collector·worker 설치 wiring

## 16. 실전 검증 상태

대표님 확인 완료:

```text
최신 feature/stockboard-themeboard-v1 적용
→ StockBoard 정상
→ ThemeBoard 정상
→ OpenAPI 로그인·등록 정상
→ HTS 연동 정상
→ 통합 장마감 복구 적용 후 전체 시스템 정상 작동
```

이 확인으로 구현 단계는 완료로 판정한다.

시간에 종속된 항목은 거래일별 운영 관찰을 계속한다.

- 실제 15:30 정규장 sampler 파일·basis_time
- 실제 20:00 통합장 sampler 파일·basis_time
- opt10080 `_AL` 실응답 범위·단위와 KRX fallback 빈도
- 한 시점 TR 1개·retry·global backoff 장기 동작
- 프리마켓 5분 전 cutoff
- 다음 실제 premarket의 `LAST_CLOSE → LIVE`
- 09:00~09:10 queue·drop·latency 무영향
- StockBoard와 ThemeBoard 동일 종목 복구값 일치

## 17. 성능 중단 기준

다음 중 하나가 확인되면 신규 복구 기능을 우선 비활성화하고 기존 실시간 경로를 보호한다.

```text
09:00~09:10 collector 수신률 감소
worker queue 3초 이상 지속 증가
기존 stream latency +20ms 지속
dropped/stale 증가
TR 연속 오류 3회 후 backoff 미작동
OpenAPI 로그인·연결 불안정
sampler copy p95 > 2ms
ThemeBoard compute 3회 연속 > 30ms
동시에 competing TR 2개 이상 실행
```

중단 시 기존 StockBoard·ThemeBoard cache와 마지막 정상값은 유지한다.

## 18. 최종 판정

```text
설계                완료
코드 구현           완료
자동 회귀 테스트     완료
collector wiring     완료
worker wiring        완료
대표님 실전 확인     정상
PR #27               Draft 유지
남은 작업            시간대별 반복 관찰과 정규장 성능 확인
```
