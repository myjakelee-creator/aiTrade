# StockBoard·ThemeBoard 통합 수집 및 장마감 복구 설계

최종 갱신: 2026-07-12  
상태: 대표님 정책 승인 · 상세 설계 완료 · 코드 미구현

## 1. 목적과 확정 원칙

StockBoard 14개 항목과 ThemeBoard 항목은 동일한 종목 원천을 많이 공유한다. 각 화면이 별도로 수집·조회·계산하면 OpenAPI 충돌, 중복 부하, 값 불일치가 발생한다.

최종 원칙:

```text
1. 종목 원천값은 한 번만 수집한다.
2. StockBoard와 ThemeBoard가 같은 공용 종목 상태를 사용한다.
3. 장중 실시간 경로에는 무거운 계산·파일·TR을 추가하지 않는다.
4. 장마감 OpenAPI 조회는 중앙 coordinator 한 곳만 실행한다.
5. 정확값이 없으면 마지막 정상값과 분봉 근사까지 사용한다.
6. 숫자 0 또는 빈칸을 표시하지 않는다.
7. 실제 미체결은 '거래없음', 원천 실패는 '직전값' 또는 '복구실패'로 표시한다.
8. 정확값과 추정값을 source·quality·is_estimated로 구분한다.
```

분봉 최종 근사 정책:

```text
1분 추정 거래대금 = 해당 1분봉 종가 × 해당 1분봉 거래량
5분 추정 거래대금 = Σ(각 1분봉 종가 × 각 1분봉 거래량)
```

이 근사값은 표시용 fallback이며 다음 거래일 실시간 SURGE 판정의 정확 원천으로 재사용하지 않는다.

## 2. 목표 구조

```text
Kiwoom 실시간·TR
→ 32bit collector
→ 공용 종목 상태 + source metadata
→ 64bit worker
├─ StockBoard 종목 계산·Funnel
└─ ThemeBoard 테마 집계

장마감 누락
→ AfterCloseRecoveryCoordinator
→ 한 번에 OpenAPI 요청 1개
→ 공용 종목 상태 갱신
→ 두 보드가 같은 복구값 사용
```

금지:

- ThemeBoard가 직접 OpenAPI 조회
- 분봉·강도·잔량비 scheduler가 서로 독립적으로 burst 요청
- 정확값을 추정값이 덮어쓰기
- KRX fallback이 통합장 AL 정확값을 덮어쓰기
- HTML에서 임의 숫자 생성
- `0`을 결측값 대신 표시

## 3. 공용 값 계약

각 필드는 숫자 하나가 아니라 다음 metadata를 가진다.

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
  "basis_time": "15:30",
  "trading_date": "20260710",
  "market_scope": "AL_OR_DECLARED",
  "quality": 0.65,
  "is_estimated": true,
  "coverage": 1.0
}
```

표시 예:

```text
182억       정확값
≈182억      추정값
182억*      일부 구성종목 추정 포함
거래없음    실제 해당 구간 체결 없음
직전값      마지막 정상값 유지
복구실패    조회·저장 원천 모두 실패
```

## 4. StockBoard 14개 항목 종합 설계

| 항목 | 장중 원천·계산 | 장마감 우선순위 | ThemeBoard 중복 | 충돌·부담 | 개선 |
|---|---|---|---|---|---|
| 순위 | 누적 거래대금 정렬 | 마지막 마감 순위 | 주도주 참고 | worker 정렬 중복 가능 | StockBoard 순위 1회 계산 후 공유 |
| 전일 | universe 전일 순위 | 그대로 유지 | 거의 없음 | 부하 없음 | 기존 유지 |
| 등급 | 후보모델 | 마지막 정상 결과 | 주도력 보조 가능 | ThemeBoard 재사용 시 의미 혼동 | 테마 엔진은 원천값 사용, 등급 직접 재사용 금지 |
| 종목명 | universe/master | 그대로 | 공통 | 없음 | 공용 code→name |
| 현재가 | 실시간 체결 | 마감가 → OHLC/분봉 종가 → 직전값 | 주도주 | 가격 source 충돌 | source 우선순위와 market_scope 유지 |
| 등락률 | 실시간 FID | 마감가·전일종가 계산 → 직전값 | 확산·상대강도 | 기준가 불일치 | 동일 전일종가 계약 |
| 금액(억) | 실시간 누적 거래대금 | 마지막 누적 → 분봉 합산 근사 → 직전값 | 핵심 | ThemeBoard 별도 집계 시 중복 | 종목값 공유 후 테마 가중 합산 |
| 대금비 | 당일 대금 ÷ 전일 대금 | 복구 당일대금으로 재계산 | 주도력 | 추정 당일대금 사용 시 품질 저하 | quality 상속 |
| 일봉 | 장중 OHLC | OHLC snapshot → 일봉 TR → 분봉 OHLC | 주도주 위치 | AL/KRX source 충돌 | 정확 source 보호 |
| 잔량비 | 실시간 호가 | 정규장 마감/last valid → opt10004 → 직전값 | 현재 미사용 | orderbook TR 경쟁 | coordinator 최저 우선순위 |
| 순간강도 | 실시간 체결강도 | last valid → opt10046 → 직전값 | 핵심 | strength scheduler 중복 | opt10046 bundle 공유 |
| 5분강도 | opt10046·당일 cache | snapshot → opt10046 → 직전값 | 핵심 | 가장 잦은 보조 TR | 기존 scheduler를 coordinator에 편입 |
| 프로(억) | program snapshot/batch | 당일 파일 → 일괄 조회 → 직전값 | 핵심 | 종목별 조회 시 폭증 | 시장별 일괄 조회 1회 |
| 대량체결 | collector 체결 집계 | daily_state → event/tick replay → 직전값 | 핵심 | 분봉으로 복원 불가 | 실시간 저장·로그 복구만 |

### 값이 없는 경우 표시 원칙

| 상황 | 표시 |
|---|---|
| 정확한 0이 의미 있는 숫자지만 대표님 정책상 빈값 방지 필요 | `거래없음`, `매수없음`, `매도없음` 등 의미 텍스트 |
| 마지막 정상값 있음 | `직전값` metadata와 함께 숫자 표시 |
| 분봉 근사 가능 | `≈` 또는 추정 표시 |
| 일부 구성만 복구 | 숫자와 Coverage 표시 |
| 아무 원천도 없음 | `복구실패` |

## 5. ThemeBoard 항목 종합 설계

| 항목 | 공용 원천 | 계산 | 장마감 fallback |
|---|---|---|---|
| 테마 누적대금 | 종목 누적 거래대금 | Σ(대금×가중치) | 복구된 종목 누적값 합 |
| 1분 유입 | 종목/테마 누적 history | 현재−60초 전 | 마감 sampler → 분봉 근사 |
| 5분 유입 | 종목/테마 누적 history | 현재−300초 전 | 마감 sampler → 5개 분봉 근사 합 |
| 시장 점유 | 모든 테마 누적대금 | 테마/전체 | 복구 snapshot으로 재계산 |
| 확산 | 종목 등락률 | 상승−하락 | 마감 등락률 유지 |
| 유입 가속 | 1분·5분 | 1분/(5분/5) | 마감값으로 계산, 추정 여부 상속 |
| 집중도 | 종목 테마 대금 비중 | top1/테마합 | 복구값으로 재계산 |
| 주도주 점수 | 대금·등락·대금비·강도·프로·대량 | 서버 계산 | 각 원천 fallback 품질 반영 |
| 역할 | 주도주 점수 | 주도/동반/후발/관찰 | 마지막 정상 역할 또는 재계산 |
| 테마 상태 | 유입·확산·점수 | SURGE 등 | 마감 상태 고정, 다음 premarket까지 유지 |

테마 Coverage:

```text
coverage = 확보된 구성종목 가중치 합 / 전체 구성종목 가중치 합
```

표시:

```text
정확값만: 1,284억
추정 포함: ≈1,284억
부분 복구: ≈1,284억 · Coverage 83%
```

부분 데이터를 100%로 확대 추정하지 않는다.

## 6. 장중 최적 수집

### 기존 실시간 경로

변경하지 않는다.

```text
현재가 / 등락률 / 누적 거래대금 / 순간강도
대량체결 / 장중 OHLC / 실시간 호가
```

틱 처리 함수 안에서 ThemeBoard 계산·JSON·파일 저장·TR enqueue를 하지 않는다.

### 마감 sampler

정확한 1분·5분 거래대금 확보를 위해 필요한 구간에만 실행한다.

```text
정규장: 15:24:50~15:30:10
통합장: 19:54:50~20:00:10
```

1초마다 필요한 최소 필드:

```text
stock_code
trade_value_eok
sample_time
```

처리:

```text
worker lock acquire(False)
→ 성공: 테마 구성종목 최소 필드만 복사
→ 실패: 즉시 양보
→ lock 밖: 테마 가중 합산·ring buffer 기록
→ 구간 종료: 작은 JSON 1회 저장
```

예상 부담 목표:

| 항목 | 목표 |
|---|---:|
| 실행 구간 | 하루 2회, 각 약 6분 |
| OpenAPI | 0 |
| lock wait | 0 |
| copy p95 | 0.5ms 이하 |
| 계산 p95 | 1ms 이하 |
| 메모리 | 수십 KB 이하 |
| 디스크 | 구간 종료 1회 |
| 09:00 영향 | 0 |

정확한 경계 샘플이 없으면 경계 이전 가장 가까운 정상 샘플을 사용하고 basis_time을 기록한다.

## 7. 장마감 중앙 복구 coordinator

명칭:

```text
AfterCloseRecoveryCoordinator
```

위치:

- 32비트 OpenAPI collector 계층
- 기존 provider의 inflight·pending·last_request 상태를 재사용
- ThemeBoard/64비트 worker에서 직접 TR 호출 금지

논리 queue:

```text
minute_bar
strength_opt10046
program_batch
ohlc
orderbook_opt10004
```

한 시점에 요청 1개:

```text
어떤 inflight 또는 경쟁 pending queue가 있으면 신규 요청 없음
```

기존 5분강도·잔량비 scheduler는 장기적으로 coordinator에 편입한다. 즉시 대규모 재작성하지 않고 공용 arbitration 함수부터 만든다.

## 8. 조회 bundle과 fallback

### A. 분봉 bundle

한 종목 조회로 가능한 범위:

```text
마지막 종가
최근 1분 추정 거래대금
최근 5분 추정 거래대금
최근 고가·저가
basis_time
```

우선순위:

```text
분봉 실제 거래대금 필드
→ 각 1분봉 종가 × 각 1분봉 거래량
```

5분은 각 분을 따로 계산해 합산한다.

금지:

```text
마지막 종가 × 5분 전체 거래량
```

분봉 TR의 정확한 TR명, 거래소구분 3(AL) 지원 여부, 반환 단위는 구현 전 공식 명세와 실제 응답으로 검증한다.

### B. opt10046 강도 bundle

한 조회에서 함께 처리:

```text
순간강도 snapshot
5분강도
20분강도
60분강도
```

우선순위:

```text
현재 정상값
→ 당일 last valid snapshot
→ 정규장 마감 snapshot
→ opt10046
→ 직전값
```

0·빈값·오류가 정상 양수값을 덮어쓰지 않는다.

### C. 프로그램 bundle

```text
당일 program snapshot 파일
→ 시장별 일괄 프로그램 조회
→ 마지막 정상값
```

ThemeBoard 때문에 종목별 프로그램 TR을 반복하지 않는다.

### D. OHLC bundle

```text
실시간 장중 OHLC
→ AL OHLC snapshot
→ 일봉 TR
→ 분봉 OHLC 합성
→ 직전값
```

낮은 품질 source가 높은 품질 source를 덮어쓰지 않는다.

### E. 잔량비 bundle

```text
실시간 호가
→ 정규장 마감 호가
→ last valid
→ opt10004
→ 직전값
```

ThemeBoard가 사용하지 않으므로 낮은 우선순위다.

### F. 대량체결

```text
실시간 collector 누적
→ daily_state
→ event JSONL/틱 로그 replay
→ 직전값
```

분봉 거래량으로 대량체결 건수를 추정하지 않는다.

## 9. 종목 우선순위와 중복 제거

| 우선순위 | 대상 |
|---:|---|
| P0 | S1 선택 종목 |
| P1 | StockBoard Top20 |
| P2 | 상위 5개 테마 주도주 TOP3 |
| P3 | 상위 5개 테마 전체 구성종목 |
| P4 | 나머지 5개 테마 |
| P5 | Hidden Top50 |
| P6 | 나머지 Top300 |

동일 종목은 여러 역할이 있어도 한 bundle당 1회만 조회한다.

```text
S1 + Top20 + 테마주도주인 000660
→ minute 1회
→ opt10046 1회
→ program은 batch 공유
```

## 10. 시간대와 요청 간격

| 시간 | 작업 |
|---|---|
| 15:24:50~15:30:10 | 정규장 마감 sampler |
| 15:30~15:40 | 기존 close metrics·마감값 확정 |
| 15:40~19:54 | 애프터 실시간 우선 |
| 19:54:50~20:00:10 | 통합장 sampler |
| 20:00~20:03 | pending 정리, 누락 행렬 생성 |
| 20:03~21:00 | P0~P3 복구 |
| 21:00~프리마켓 30분 전 | P4~P6 저속 복구 |
| 프리마켓 30분 전 | P0~P3 최종 pass |
| 프리마켓 5분 전 | 신규 전일 복구 요청 중단 |
| 프리마켓 시작 | 전일 hold 해제 |

요청 간격:

| 상황 | 최소 간격 |
|---|---:|
| 20:03~21:00 | 3초 |
| 21:00 이후 | 10초 |
| 주말·휴장 | 10초 |
| 프리마켓 30분 전 중요 종목 | 3초 |
| 프리마켓 5분 전 | 신규 요청 금지 |

재시도:

```text
1차 실패 → 5분
2차 실패 → 30분
3차 실패 → 2시간
연속 오류 3회 → 전체 30분 backoff
```

다음 프리마켓은 `next_premarket_datetime()`으로 계산한다.

## 11. 충돌·보호 규칙

### OpenAPI arbitration

공용 idle 판정은 최소 다음을 확인한다.

```text
strength inflight/pending
orderbook inflight/pending
minute-bar inflight/pending
opt10055/close metrics inflight/pending
마지막 전체 TR 요청 시각
로그인/연결 상태
시장 phase
```

### source 보호

품질 우선순위 예:

```text
LIVE_REALTIME = 1.00
CLOSE_HISTORY_EXACT = 1.00
TR_TRADE_VALUE = 0.95
OPT10046 = 0.95
PROGRAM_BATCH = 0.95
PERSISTED_LAST_VALID = 0.80
MINUTE_CLOSE_X_VOLUME = 0.65
PREVIOUS_DAY_DISPLAY = 0.40
```

규칙:

- 높은 quality를 낮은 quality가 덮어쓰지 않음
- 같은 quality면 최신 basis_time 우선
- 다른 trading_date는 명시적 rollover 외 덮어쓰기 금지
- `AL` 정확값을 `KRX` fallback이 덮어쓰기 금지
- 추정값은 후보모델 필수 원천으로 자동 승격하지 않음

### 계산 안정

복구 종목 하나마다 ThemeBoard 전체를 즉시 재계산하지 않는다.

```text
최대 5초에 한 번
또는 한 bundle batch 완료 시
```

마감 화면 순위 흔들림을 줄이기 위해 display hysteresis를 유지한다.

## 12. 속도·조회량

10개 테마 고유 종목 약 60개 기준 최악 추정:

| 작업 | 최대 |
|---|---:|
| minute-bar | 약 60 |
| opt10046 | 강도 누락만, 최대 약 60 |
| program | batch 1~수회 |
| OHLC | 누락만 |
| opt10004 | 중요 종목부터 |
| 대량체결 | TR 없음 |

3~10초 간격이면 중요 데이터는 10~30분, 나머지 Top300은 야간에 천천히 처리한다.

장중 핵심 경로 부담:

- 실시간 collector 추가 작업 없음
- sampler는 마감 직전 제한 구간만 실행
- ThemeBoard는 공용 cache 유지
- 09:00~09:10 신규 작업 없음

## 13. 주요 문제점

### 분봉 정확성

- 분봉 실제 거래대금 필드가 없으면 종가×거래량은 근사다.
- 변동성이 큰 분봉에서 오차가 커질 수 있다.
- 반드시 `≈`, source, quality를 표시한다.

### 거래소 범위

- 실시간 누적이 AL인데 분봉이 KRX라면 같은 값처럼 혼합하면 안 된다.
- 분봉 TR의 거래소 구분 3 지원 여부를 확인한다.
- 지원하지 않으면 `market_scope=KRX`를 표시한다.

### 0과 결측

- `0`을 결측으로 쓰지 않는다.
- 실제 무거래는 `거래없음`.
- 요청 실패는 `직전값` 또는 `복구실패`.
- 음수 프로그램·대량체결 net은 정상값이다.

### 부분 복구

- 일부 종목만 확보한 테마 합계를 전체처럼 표시하지 않는다.
- Coverage를 함께 표시한다.
- 누락분을 비례 확대하지 않는다.

### scheduler 통합 위험

- 기존 5분강도·잔량비 scheduler를 한 번에 재작성하면 회귀 위험이 크다.
- 1단계는 공용 arbitration, 2단계는 minute queue, 3단계부터 기존 scheduler 편입 순서로 진행한다.

## 14. 단계별 구현

### 단계 1 — source metadata 계약

- 공용 `source/status/quality/is_estimated/basis_time`
- 화면 기존 숫자 유지
- 정확값 보호 테스트

### 단계 2 — 마감 sampler

- 정규장·통합장 마감 6분 구간
- 비차단 최소 필드 복사
- 작은 runtime snapshot
- ThemeBoard 1분·5분 정확 마감값 채움

### 단계 3 — 공용 arbitration

- provider inflight·pending·gap 판정 1개
- 기존 scheduler가 공용 판정 사용
- 기능 변화 없이 충돌만 줄임

### 단계 4 — minute-bar fallback

- 누락 종목 전용 queue
- 실제 거래대금 우선
- 없으면 각 분 `close×volume`
- StockBoard·ThemeBoard 공용 반영

### 단계 5 — AfterCloseRecoveryCoordinator

- P0~P6 lane
- bundle·backoff·프리마켓 cutoff
- 상태 API와 진단값

### 단계 6 — 기존 scheduler 편입

- opt10046
- program batch
- OHLC
- opt10004
- 점진 편입과 회귀 테스트

## 15. 구현 시 변경 예상 범위

예상 신규 파일은 최대한 1개로 제한한다.

```text
realtime_v2/after_close_recovery.py
```

수정 예상:

```text
realtime_v2/collector32_large_bidask.py 또는 최종 collector wrapper
realtime_v2/strength5m_scheduler.py
realtime_v2/orderbook_thin_scheduler.py
realtime_v2/worker64_guarded_large.py
realtime_v2/theme_board_patch.py
stockboard_theme_engine.py
관련 tests
```

금지:

```text
docs/stockboard_v2.html에서 계산 추가
별도 포트·별도 launcher
collector 실시간 체결 핫패스 대규모 수정
자동 클라우드 업로드
main 직접 커밋
```

## 16. 성능 중단 기준

다음 중 하나면 신규 복구 기능을 즉시 비활성화한다.

```text
09:00~09:10 collector 수신률 감소
worker queue 3초 이상 지속 증가
기존 stream latency +20ms 지속
dropped/stale 증가
TR 연속 오류 3회
OpenAPI 로그인·연결 불안정
sampler copy p95 > 2ms
ThemeBoard compute 3회 연속 > 30ms
```

## 17. 검증 체크리스트

자동:

- source 우선순위
- 정확값이 추정값에 덮이지 않음
- 실제 무거래와 결측 구분
- 1분·5분 `close×volume` 합
- 테마 가중치·Coverage
- 종목 중복 제거
- 한 번에 inflight 1개
- backoff·프리마켓 cutoff
- no-client ThemeBoard 계산 없음
- HTML 계산 없음

실전:

- 15:30·20:00 마감값
- 재시작 복원
- 장마감 누락 순차 채움
- StockBoard와 ThemeBoard 동일 종목 값 일치
- HTS 값과 분봉 근사 오차 확인
- queue·latency·drop 무영향
