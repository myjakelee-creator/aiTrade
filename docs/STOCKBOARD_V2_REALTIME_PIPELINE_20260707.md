# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-16

이 문서는 StockBoard v2의 실시간 구조, 시장시간 정책, 보조지표 수집·유지, 선발모델, 행 위치와 실전 검증 상태를 기록하는 단일 기준 문서이다. 기존 StockBoard v0.3.x 문서와 섞지 않는다.

## 1. 현재 구조

```text
32-bit Kiwoom QAx price-only collector
  FID 10 / 12 / 20 / sampled 14
        ↓
64-bit canonical worker
  ├─ ka10004 통합 호가잔량비 REST
  ├─ WebSocket 0B/FID228 Top20 체결강도
  ├─ ka10046 5분·20분·60분강도 REST
  ├─ ka90004 프로그램 순매수 REST
  ├─ ka10055 대량체결 저속 REST
  ├─ 시장 캘린더 기반 Top1·Top20·Top100 세션 관리자
  ├─ 6지표 거래일 수명주기·최종 출력 가드
  └─ SSE /api/v2/stream → 표시 전용 HTML
```

생산 가격 collector 계약:

```text
_REALTIME_FIDS = 10;12;20;14
single QAx owner
large_trade_enabled = False
```

두 번째 QAx 로그인·실시간 등록과 FID15 재도입은 금지한다.

## 2. 6개 표시 지표 원천

| 지표 | 장중 원천 | 장마감·08:00 전 |
|---|---|---|
| 대금비 | 당일 누적 거래대금 / 전일 거래대금 | 직전 거래일 최종값 |
| 잔량비 | `ka10004`, 통합 `_AL` | 직전 거래일 최종 정상값 |
| 체결강도 | WebSocket 주식체결 `0B/FID228`, Top20 | 마지막 정상 FID228 |
| 5분강도 | `ka10046`의 5·20·60분 필드만 | 직전 거래일 최종 정상값 |
| 프로(억) | `ka90004` | 직전 거래일 최종값 |
| 대량체결 | `ka10055` 저속 최근 페이지, 5천만원 기준 | 직전 거래일 최종 누적값 |

`ka10046`의 일반 체결강도 필드는 폐기하며 체결강도를 덮어쓰지 않는다. 화면 열 제목은 키움 원래 명칭인 `체결강도`를 사용한다.

결측 `null`·빈문자열은 `0`이 아니라 `-`로 표시한다. 프로그램과 대량체결은 원천·거래일이 있는 실제 0만 0으로 인정한다.

## 3. 공통 거래일 수명주기

```text
장중·애프터마켓   현재 거래일 값만 허용
20:00 이후·08:00 전 직전 완료 거래일 최종값만 허용
08:00 프리마켓      전일 보존값 만료·새 daily state로 전환
주말·공휴일         신규조회 중지·직전 거래일 최종값 유지
날짜 불명·과거오염  최종 출력 가드에서 제거
```

통합 snapshot:

```text
data/runtime/stockboard_v2/six_metric_lifecycle.json
```

값이 실제로 바뀔 때 최대 5초에 한 번 저장한다. 조회시각과 값의 실제 대상 거래일을 분리한다.

```text
program_source_trading_date
orderbook_source_trading_date
execution_source_trading_date
strength_source_trading_date
large_trade_source_trading_date
```

00:00~08:00 프로그램 조회 결과는 조회일이 아니라 직전 완료 거래일로 태깅한다.

## 4. 캘린더 기반 세션 관리자

`realtime_v2/worker_market_metric_session_manager.py`가 시장 캘린더와 특별일 설정을 기준으로 조회 범위·간격·거래일 전환을 소유한다. 고정 09:00이 아니라 `regular_start`를 사용하므로 지연개장에도 같은 정책이 적용된다.

| 구간 | REST 범위 | 정책 |
|---|---:|---|
| 00:00~프리마켓 전 | Top100 | 빠진 직전 거래일 잔량비·5분강도만 저빈도 보충 |
| 프리마켓 | Top100 | 당일 잔량비 준비, 5분강도·대량체결 중지 |
| 장전 동시호가 | Top20 | 잔량비만 저속 |
| 정규장 시작 후 10분 | S1 | 가격 fast patch 우선, REST 최소화, heavy render 1초 |
| 정규장 10분 이후 | Top100 | 잔량비·5분강도·대량체결 순차 조회 |
| 장마감 동시호가 | Top20 | REST 축소 |
| 15:30~15:40 | Top100 | 장마감 누락값 우선 보충 |
| 애프터마켓 | Top100 | 통합 `_AL`, NXT 미거래 종목 정규장 최종값 유지 |
| 20:00 이후 | Top100 | 누락 최종값만 한 번씩 보충 |
| 주말·공휴일 | 0 | 네트워크 조회 중지 |

기존 REST updater thread 하나와 single-flight budget 하나만 재사용한다. Top100 순환은 64비트 Worker에서 수행하며 가격 QAx callback과 분리한다.

## 5. 개장 성능 보호

```text
정규장 시작~10분 REST 범위 S1
REST 최소 간격          5초
전체 테이블 heavy render 1000ms
가격·등락률 fast patch  계속 유지
```

지연개장일은 캘린더의 `regular_start`부터 10분간 같은 보호를 적용한다.

위험 신호는 단발성 render 40ms가 아니라 다음 항목의 지속 증가이다.

```text
collector_q
worker_q
logdrop
drop
stream latency
stale / lag
```

## 6. 체결강도

- 공식 실시간 원천: WebSocket `0B/FID228`
- 연결 수: 1
- 구독 범위: Top20
- 반영 방식: 종목별 마지막 값을 1초 동안 모아 한 state lock과 한 background rebuild로 배치 반영
- 20초 이상 새 체결이 없으면 장중 현재값으로 표시하지 않음
- 장중 수집된 마지막 정상값은 장마감부터 다음 프리마켓 전까지 유지

## 7. 5분강도

- `ka10046`의 5분·20분·60분 필드만 저장
- 일반 체결강도 필드는 폐기
- Top1 → Top20 → Top100 순차 저속 조회
- 0·빈값·오류는 정상값을 덮어쓰지 않음
- 애프터마켓과 장마감은 통합 `_AL` 기준

## 8. 잔량비·대금비·프로그램·대량체결

잔량비는 `ka10004` 통합 `_AL` 기준이다. NXT 미거래 종목도 정규장 최종값을 유지하며 날짜가 다른 과거값은 제거한다.

대금비:

```text
amount_ratio = trade_value_eok / prev_trade_value_eok
```

08:00 거래일 전환 시 직전 거래일 최종 누적 거래대금을 새 거래일의 `prev_trade_value_eok`로 승격한다.

프로그램은 정적 docs snapshot을 현재값으로 재태깅하지 않는다. 캘린더 거래일 daily state와 실제 `ka90004` 조회만 신뢰한다.

대량체결은 QAx FID15가 아니라 저속 `ka10055`를 사용한다. 첫 페이지 기준이므로 상태는 `partial_recent_page_since_activation`으로 표시하며 전 종일 완전 집계라고 과장하지 않는다.

## 9. AHK 키움 연동

`Edit6`가 여러 개면 다음을 점수화해 유일한 최고점 컨트롤만 사용한다.

```text
nkre.exe
_NKHeroMainClass / NHeroMainClass
영웅문 제목
가시·활성 상태
최소화 여부
창 크기
```

동점이면 안전하게 전송하지 않는다. HTS 활성화·포커스 이동·전경 키 입력은 금지한다.

## 10. 운영·검증

기준 실행기:

```text
stockboard_v2_large.cmd
http://127.0.0.1:8765/
```

장중 검증:

```text
가격·등락률 정확성
거래대금 정확성
collector PID 유지
queue·drop·logdrop 0 유지
stream 지연
Top100 잔량비·5분강도 순차 채움
Top20 FID228 체결강도 HTS 대조
프로그램 HTS 대조
NXT 미거래 종목 정규장 최종값 유지
```

실제 프리마켓·정규장·장마감·애프터마켓 검증 전에는 관련 PR을 Draft로 유지한다.
