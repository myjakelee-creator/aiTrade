# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-19 14:57 KST

이 문서는 StockBoard v2의 실시간 가격 경로, 분 단위 보조지표, 거래일 유지정책과 실전 검증 상태를 기록하는 단일 기준 문서이다. 과거 v0.3.x 구조와 섞지 않는다.

## 1. 현재 생산 구조

```text
32-bit Kiwoom QAx price-only collector
  FID 10 현재가
  FID 12 등락률
  FID 20 체결시각
  FID 14 누적거래대금 500ms sampling
        ↓ latest-only sender
64-bit canonical worker
  ├─ WebSocket 0B Top100: 체결강도 FID228 + signed 체결량 FID15
  ├─ WebSocket 0D: 호가 20종목 × 12초 순환
  ├─ ka10046: 5분강도 3초당 1종목
  ├─ ka90004: 프로그램 순매수 60초 일괄조회
  ├─ FID14 차분: 1분대금
  ├─ 거래일 lifecycle / minute publisher / safety guard
  └─ SSE /api/v2/stream → 표시 전용 HTML
```

생산 가격 collector 불가침 계약:

```text
_REALTIME_FIDS = 10;12;20;14
single 32-bit QAx owner
추가 QAx owner 0
가격 callback 추가 계산 0
FID15·호가 FID를 가격 collector에 추가하지 않음
```

두 번째 QAx 로그인·실시간 owner는 가격 수신을 멈춘 전력이 있으므로 생산에서 금지한다.

## 2. 표시 열 원천과 UI 갱신

| 열 | 데이터 원천 | 수집·계산 | UI 반영 |
|---|---|---|---|
| 현재가 | QAx FID10 | 체결마다 | fast patch 약 50~150ms |
| 등락률 | QAx FID12 | 체결마다 | fast patch 약 50~150ms |
| 금액(억) | QAx FID14 | 종목별 최대 500ms | 일반 약 500ms, 개장 첫 10분 1초 |
| 대금비 | 당일 누적대금 / 전일대금 | 금액 갱신 시 | 금액과 동일 |
| 1분대금 | FID14 누적값 차분 | 완료 1분과 직전 1분 | 매분 |
| 일봉 | 기존 OHLC + 실시간 현재가 | 시·고·저 기존 OHLC, 종가 row.price 우선 | 일반 500ms, 개장 첫 10분 1초 |
| 잔량비 | WebSocket 0D FID121/125 | 20종목씩 12초 순환 | 매분 last-good |
| 체결강도 | WebSocket 0B/FID228 | Top100 연속 수집 | 매분 latest |
| 5분강도 | ka10046 | 3초당 1종목, 분당 약 20종목 | 완료된 종목을 매분 |
| 프로(억) | ka90004 | 일괄조회 | 60초 |
| 대량체결 | WebSocket 0B/FID15 | 원시 이벤트마다 5천만원 기준 누적 | 60초 |

### 2.1 일봉 종가 원칙

```text
시가 = 기존 OHLC.open
고가 = 기존 OHLC.high
저가 = 기존 OHLC.low
종가 = row.price → trade_price → OHLC.current → OHLC.close
```

일봉 종가만 화면의 실시간 현재가를 우선한다. 추가 네트워크·QAx·Worker 계산·렌더 주기는 없다.

### 2.2 1분대금 표시

```text
21.4 79%
```

- 앞 숫자: 최근 완료 1분 거래대금(억원)
- 뒤 숫자: 직전 완료 1분 대비 비율
- 100% 미만 파랑, 100% 이상 빨강
- 직전값 0이고 현재값이 양수면 `NEW`

## 3. 잔량비

```text
1~20위    12초 구독
21~40위   12초 구독
41~60위   12초 구독
61~80위   12초 구독
81~100위  12초 구독
```

한 WebSocket 연결의 별도 REG 그룹을 사용한다. 호가 이벤트마다 UI·파일·background rebuild를 하지 않고 종목별 최신 총매수·총매도잔량만 덮어쓴다.

```text
잔량비 = 총매수잔량 / 총매도잔량
```

운영 원칙:

- 60초 경계에서 확보된 Top100 last-good 값을 발행
- `0D` 이벤트를 받은 종목만 표시
- 못 받은 종목은 `-`
- ka10004 REST 값을 실시간처럼 대체하지 않음
- 180초 동안 호가 이벤트가 전혀 없으면 호가 기능만 fail-closed
- 20:00 이후 확보한 마지막 정상값은 다음 프리마켓 전까지 유지

20종목 순환 중 호가 변화가 없는 종목은 이벤트가 없을 수 있어 Top100 완전 커버리지를 보장하지 않는다. 속도와 정확성을 위해 이 한계를 허용한다.

## 4. 체결강도

- 공식 실시간 원천: WebSocket 주식체결 `0B/FID228`
- 연결 수: 1
- 구독 범위: Top100
- 수집: 이벤트마다
- UI: 매분 종목별 latest 값 발행
- 동일 거래일 last-good은 다음 정상값 또는 다음 프리마켓까지 유지
- 승인 원천은 `kiwoom_rest_ws_0B_fid228`, `kiwoom_rest_ws_0B_fid228_close_hold`만 허용
- 원천이 없거나 `opt10046`·`ka10046`인 값은 체결강도로 저장·복원·표시하지 않음

`ka10046`의 일반 체결강도 필드는 체결강도를 덮어쓰지 않는다. 과거 `execution_strength_alias_patch`는 더 이상 opt10046 값을 표준 체결강도 키로 복사하지 않으며, 현재는 FID228 원천분리 guard 역할만 수행한다.

## 5. 5분강도

- `ka10046`의 5분·20분·60분 필드만 저장
- 전역 REST 최소 간격 3초
- 분당 약 20종목
- 5분에 Top100 한 바퀴
- 5분 전체 완료를 기다리지 않고 완료된 약 20종목을 매분 발행
- 같은 거래일 정상값은 다음 성공 조회까지 유지
- 시간 경과만으로 정상값 삭제 금지
- 실제 정규장 시작 후 첫 5분은 가격 보호를 위해 조회 중지
- 체결강도와 별도 원천·별도 lifecycle로 유지하며 서로 alias하지 않음

## 6. 프로그램 순매수

- 원천: `ka90004`
- 일괄조회 주기: 60초
- one thread / one in-flight / single-flight
- 오류·부분응답 시 기존 당일 정상값 유지
- 실제 0은 0, 미수신은 `-`
- 조회시각과 실제 대상 거래일을 분리해 태깅

## 7. 대량체결

```text
체결금액 = abs(체결가 × signed 체결량)
체결금액 >= 50,000,000원 → 매수 또는 매도 1건 누적
```

- 같은 `0B` 원시 메시지의 FID15 사용
- 체결강도 latest coalescing 전에 원시 이벤트를 먼저 집계
- 매수·매도 건수와 금액을 당일 누적
- UI 60초
- 5초 단위 daily-state checkpoint

품질 상태:

```text
EXACT_LIVE       프리마켓 이전부터 연속 수집
EXACT_RECONCILED 공백을 완전히 복구
GAP_POSSIBLE     장중 재시작·재접속 공백 가능
```

`GAP_POSSIBLE`은 화면에 `~`를 붙인다. 장중 새 버전을 시작한 날은 이전 체결을 완전히 증명할 수 없으므로 `~`가 정상이다.

### 7.1 1분 모멘텀 배지

- 기존 `0B` 체결 이벤트로 만든 **완료된 1분봉**만 사용한다.
- 시가 기준 `시돌·시붕·시지·시저`, VWAP 기준 `중돌·중붕·중지·중저` 8개 규칙을 사용한다.
- 규칙·배지·유지시간·교번시간은 `config/stockboard_momentum_badges.json`에서 관리한다.
- 수식 실행에 `eval`을 쓰지 않고 허용 변수와 비교연산만 시작 시 검증·컴파일한다.
- 각 규칙은 **발생 조건(trigger)**과 **방향 유지 조건(stay)**을 분리한다. 돌파·지지 계열은 종가가 기준선 위에 있는 동안, 붕괴·저항 계열은 종가가 기준선 아래에 있는 동안 활성 상태를 유지한다.
- 별도 모멘텀 열은 만들지 않는다. 활성 신호가 있으면 **등급 셀에서 등급배지를 대신해 모멘텀 배지**를 표시한다.
- 시가와 VWAP 신호가 동시에 활성화되면 같은 등급 셀에서 CSS로 교번한다. 정렬·후보점수용 원래 등급값은 유지한다.
- 최신 완료봉이 방향 유지 조건을 만족하면 배지는 활성 상태를 유지한다. 장마감 후 새 완료봉이 없으면 마지막 활성 배지를 계속 표시한다.
- 다음 완료봉에서 유지 조건을 벗어나면 기본값으로 2분 정상 유지 후 1분 흐림 상태를 거쳐 사라지고 등급배지가 복귀한다. 감쇠 중 유지 조건을 다시 만족하면 활성 상태로 복귀한다.
- 상단 알림 스트립은 현재 추가 구독 없이 모멘텀 0B를 수집하는 **Top100 전체**를 대상으로 하므로 화면 아래쪽 종목 신호도 표시한다. 101~300위 확대는 구독·처리량 증가를 피하기 위해 이번 범위에서 제외한다.
- SSE에는 alert version/count만 싣고 version 변경 때만 `/api/v2/momentum_alerts`를 로컬 조회한다.
- 기존 완료봉마다 발생하던 `momentum_1m_signal` background rebuild는 차단하고 신호 발생·교체·감쇠·소멸 때만 rebuild한다.
- 추가 QAx·FID·Kiwoom REST·WebSocket·thread는 0이다.
- UI 툴팁은 일봉 캔들의 시·고·저·종 외에는 사용하지 않는다. 등급·모멘텀·대량체결에도 툴팁을 붙이지 않는다.

### 7.2 반응형 모바일 화면

- 설정 파일은 `config/stockboard_view_modes.json`, 표시 패치는 `realtime_v2/html_mobile_view_patch.py`이다.
- 기본 임계값은 viewport `760px`이다. 브라우저 폭이 임계값 이하가 되면 모바일, 초과하면 데스크톱 화면으로 자동 재구성한다.
- 상단의 `모바일 보기 / 데스크톱 보기` 버튼은 현재 세션에서 강제 전환한다. 이후 브라우저 폭을 24px 이상 변경하거나 임계값을 넘나들면 강제값을 해제하고 자동판정으로 복귀한다.
- 모바일 테이블은 숨겨진 데스크톱 셀을 계속 만들지 않고 다음 **8개 셀만 DOM에 생성**한다.

```text
순위 | 전일 | 등급 | 종목명 | 등락률 | 대금비 | 체결강도 | 프로(억)
```

- 등급 셀의 모멘텀 우선 표시와 2개 신호 CSS 교번은 데스크톱과 동일하게 유지한다.
- 모바일에서는 현재가·금액·1분대금·일봉·잔량비·5분강도·대량체결 셀을 생성하지 않는다.
- 모바일 8열의 헤더·본문은 데스크톱과 같은 12px 글꼴과 기본 셀 높이·여백을 사용한다.
- 모바일 기본 열폭은 데스크톱과 같은 8개 열의 기본 폭을 사용하며, `stockboard.v2.mobileColumnWidths.v1`에 데스크톱 열폭과 분리해 저장한다.
- 열 경계 마우스 드래그, 경계 더블클릭 자동맞춤, `폭 최소화` 전체 자동 최소화를 모바일에서도 유지한다.
- 모바일 상단에서 `화면 100%`와 `폭 최소화` 버튼을 유지하고, 행 위치·HTS 안내와 속도·queue·drop·render 원본 배지는 숨긴다.
- 종목 행 클릭은 데스크톱과 동일하게 기존 클립보드 명령을 전송해 PC AHK·키움 HTS 연동을 수행한다. 실제 스마트폰의 클립보드는 PC AHK와 공유되지 않는다.
- 표시 요소가 없는 상단 metric 행은 모드 적용 시 한 번 판정해 행 전체를 숨기며, 모멘텀 알림 아래 빈 행을 남기지 않는다.
- ThemeBoard 링크·경로·데이터 허브·선택 상세·CSS 파일은 수정하지 않으며 모바일 hide selector에도 ThemeBoard를 포함하지 않는다.
- StrategyBoard span과 새 창 버튼은 모바일에서 계속 숨긴다.
- 미국시장 정보는 12px 원래 글꼴로 전체 항목을 유지하고, viewport 폭을 넘으면 셀 단위로 다음 줄에 자동 배치한다.
- 국내시장 수급·등락 그래프 4개는 원래 크기를 유지한 1×4 배열로 표시한다.
- 국내시장 숫자표는 12px 원래 글꼴과 전체 열을 유지하며 오른쪽 열을 자동으로 숨기지 않는다.
- 모바일 표는 실제 열폭 합계로 렌더링하고 문서 가로 overflow를 허용해 브라우저 하단 가로 스크롤로 이동한다.
- `realtime_v2/html_mobile_top_status_patch.py`는 모멘텀 스트립을 모바일 최상단의 독립된 전체폭 행으로 유지한다.
- `recv/s`, `stream ms`, `render ms`는 선발기준 바로 뒤에 원래 `badge` 글꼴·여백의 개별 배지로 배치한다. 우측 공간이 부족하면 기존 topbar `flex-wrap`으로 다음 줄에 표시한다.
- 성능 배지는 기존 `computeRates`, payload lag, render 측정값을 그대로 재사용하며 추가 타이머·API·SSE·Worker 계산이 없다.
- 기존 fast-price DOM 패치는 모바일에서 현재가 셀을 건너뛰고 모바일 `등락률` 셀 인덱스만 갱신하도록 분기한다.
- 신규 QAx·FID·Kiwoom REST·WebSocket·Worker thread·SSE payload는 0이며, 모바일에서는 행당 DOM 셀이 8개로 유지된다.

### 7.3 국내시장 수급 last-good 유지

- 설정 파일은 `config/stockboard_market_context.json`, 표시 보호 패치는 `realtime_v2/worker_market_supply_hold_patch.py`이다.
- 기존 `/api/v2/context` 15초 조회 경로를 그대로 사용하며 새로운 시장수급 요청을 만들지 않는다.
- KOSPI·KOSDAQ 모두 지수, 등락률, 상승·하락 수, 개인·외인·기관·프로그램 수급이 숫자로 확인될 때만 정상 스냅샷으로 승인한다.
- 개인·외인·기관·프로그램의 실제 `0`은 정상값으로 인정한다.
- 지수·등락률·상승·하락이 없고 수급만 0인 20:00 초기화 스냅샷은 거부한다.
- 마지막 정상값은 `data/runtime/stockboard_v2/market_supply_last_good_YYYYMMDD.json`에 거래일별로 저장한다.
- 20:00 이후 장마감, 자정, 주말·공휴일, Worker 재시작에도 직전 완료 거래일의 마지막 정상값을 유지한다.
- 다음 거래일 08:00은 삭제 시점이 아니라 신규 당일값 수용 시작 시각이다. 당일 첫 정상 스냅샷이 확인될 때까지 직전 거래일 값을 유지하고 정상값 수신 즉시 교체한다.
- 후보 원천은 실제 runtime 파일 `data/runtime/stockboard_v2/market_supply.json`, `data/runtime/market_supply.json`만 사용한다. 체크아웃 수정시각으로 오래된 값을 오늘 값처럼 오인할 수 있는 정적 `docs/assets` 파일은 자동 원천에서 제외한다.
- `/api/v2/context`의 `market_supply_status`에 표시 기준, 원천 거래일, 마지막 정상시각, 후보 거부 사유를 진단용으로 제공한다.
- 추가 QAx·FID·Kiwoom REST·WebSocket·Worker thread·브라우저 계산·타이머·SSE payload는 0이다.

### 7.4 PC 이동형 휴장·장마감 보드 복원

- 다른 PC의 `data/runtime` 파일을 복사하거나 클라우드에 자동 업로드하지 않는다.
- 각 PC는 `realtime_v2.context_snapshot_writer_portable_v2` 한 프로세스만 사용해 직전 완료 거래일의 보드값을 자체 재구성한다.
- 생산 시작 전 base·singleflight·portable·portable_v2 context writer 변형을 모두 종료하고 portable v2 소유자가 정확히 1개일 때만 준비 완료로 인정한다.
- `context_owner_status.json`에 프로세스 수, owner PID·모듈, legacy writer 검출 여부를 기록한다.
- 완료 거래일 가격·OHLC 종가는 `close_pric` 계열만 사용하고 `cur_prc`는 선택값으로 사용하지 않는다.
- 등락률은 exact 날짜 행의 공식 `flu_rt`를 우선하고, 없을 때만 완료일 종가와 직전 거래일 종가로 재계산한다.
- 후보는 `ohlc_snapshot_candidate.json`으로 먼저 저장한 뒤 parser version, 거래일, coverage, OHLC 범위를 검증하고 통과한 경우에만 `ohlc_snapshot.json`으로 원자 승격한다.
- 승인 계약은 `portable_closed_board_snapshot_v2` + `exact_daily_row_fields_v2`이다.
- portable generation·source trading date·display basis를 opening-burst heavy cache signature에 포함하고, generation이 바뀌면 기존 cache owner가 한 번만 structure-change rebuild한다.
- 새 generation cache가 준비되기 전에는 구형 seed·stale cache 행을 표시하지 않는다.
- 추가 QAx·FID·WebSocket·Worker thread·SSE 주기 변경은 0이며, 휴장·장마감 재구성은 기존 저우선순위 context 경로에서 수행한다.

## 8. 공통 거래일 유지정책

초기화하지 않는 시점:

```text
15:30 정규장 종료
20:00 NXT 종료
자정
브라우저 새로고침
Worker 재시작
WebSocket 재접속
토요일·일요일·공휴일
```

초기화 시점:

```text
다음 실제 거래일 프리마켓 시작
```

운영 규칙:

- NXT 거래 종목: `_AL` 기준 20:00까지 갱신
- NXT 미거래 종목: 15:30 정규장 마지막 정상값 유지
- 20:00 이후: 신규 수집 중지, 마지막 정상값 고정
- 주말·공휴일: 신규조회 중지, 직전 완료 거래일 유지
- 휴장 복원은 값뿐 아니라 승인 원천과 source trading date가 모두 맞을 때만 허용
- PC 변경 시 다른 PC의 runtime을 복사하지 않고 portable v2 exact snapshot을 해당 PC에서 재구성
- context writer는 portable v2 단일 소유자만 허용하며 legacy writer가 검출되면 시작 실패
- 지연개장: 캘린더의 실제 프리마켓·정규장 시작시각 사용
- 재시작: daily state와 lifecycle snapshot에서 복원
- 다음 프리마켓: 전일 내부 누적·분 bucket·stage 일괄 초기화
- 국내시장 수급은 프리마켓 시각에 즉시 지우지 않고 첫 정상 당일 스냅샷으로 교체될 때까지 별도 last-good을 유지

## 9. 개장 성능 보호

```text
가격 QAx 경로                  불변
가격·등락률 fast patch         계속
전체 heavy render              개장 첫 10분 1000ms
ka10046                         실제 개장 후 첫 5분 중지
체결강도·대량체결              같은 64-bit 0B 스트림
1분대금                         기존 FID14 차분
잔량비                           20종목 순환, 무응답 시 자동 포기
모멘텀                          완료 1분봉에서만 규칙 평가
모바일                          기존 SSE, 행당 8개 셀만 생성
모바일 성능 배지                기존 렌더 값 문자열 재사용
국내시장 수급 hold              기존 context 조회에서 작은 딕셔너리 검증
portable closed-board 복원       휴장·장마감 저우선순위 1회 재구성
```

위험 신호:

```text
collector_q 지속 증가
worker_q 지속 증가
drop / logdrop 증가
stream latency 지속 상승
stale / top20 lag 증가
render 70ms 초과 지속
```

가격 경로가 최우선이며, 문제 발생 시 잔량비 → 5분강도 순으로 중지·지연한다.

## 10. 2026-07-16 실전 확인

애프터마켓 적용 후 확인:

```text
realtime_strength_ws_status         ok
realtime_strength_ws_backend        websockets.sync
realtime_strength_ws_selected_count 100
0B event_count와 raw 처리 count     일치
0D orderbook raw events             정상 증가
ka10046 request/success/error        57 / 57 / 0
worker_q / drop / logdrop            0 / 0 / 0
1분대금 quality                      COMPLETE_MINUTE
```

판정:

| 항목 | 상태 |
|---|---|
| 가격·등락률·누적대금 | 통과 |
| 1분대금 | 통과 |
| 체결강도 Top100 | 통과 |
| 5분강도 조회·유지 | 통과 |
| 프로그램 | 통과 |
| 잔량비 | 실시간 원천 통과, 종목 커버리지는 조건부 |
| 대량체결 | 수집 통과, 장중 시작일은 `GAP_POSSIBLE` |
| 일봉 종가 실시간 현재가 일치 | 통과 |

### 10.1 2026-07-17 휴장일·PC 전환 확인

- 캘린더 phase `holiday`, 신규 거래 0 확인
- 잔량비와 5분강도는 검증된 직전 거래일 값만 부분 복원
- 노트북 daily-state의 `execution_strength` 70건은 5분강도와 같은 값이고 원천일이 `20260711`로 남은 legacy alias 오염값으로 판정
- 오염된 체결강도는 표시하지 않고 `-` 유지가 정상
- 구형 opt10046 alias 제거 및 FID228 전용 source guard 적용
- StockBoard CI Run #209 Windows regression 성공

### 10.2 FID228 개장 검증 doctor

- Worker snapshot에 `execution_strength_diagnostics`를 추가
- FID228 신뢰 원천 수, 최종 체결강도 표시 수, 5분강도 표시 수를 분리 집계
- 두 강도의 동일값 수, 원천·거래일 불일치 숨김 수, 마지막 FID228 수신시각을 기록
- WebSocket 상태·구독 수와 collector_q·worker_q·drop·logdrop을 같은 진단에 포함
- 상위 10종목의 체결강도·원천·거래일과 5분강도·원천·거래일을 한 줄씩 출력
- 추가 QAx·FID·REST·WebSocket·thread·브라우저 계산 없음
- StockBoard CI Run #227 Windows regression 성공

### 10.3 1분 모멘텀 배지 구현

- 설정 기반 8개 규칙 엔진과 trigger/stay 분리, 상태 지속·이탈 감쇠·daily-state 복원 구현
- 모멘텀 열 제거, 등급 셀 우선 표시, 2개 신호 CSS 교번 구현
- 실시간 Top100 상단 알림과 version-change 로컬 조회 구현
- 일봉 캔들 외 title 툴팁 제거, 대량체결 패치의 숨은 모멘텀 선행 설치 결합 제거
- 완료봉 단순 생성에 따른 불필요한 background rebuild 차단
- StockBoard CI Run #281 Windows regression 성공
- Targeted pytest 101 passed / 0 failed

### 10.4 반응형 모바일 화면 구현

- viewport 760px 자동 전환과 상단 수동 전환 버튼 구현
- 모바일 전용 8열 DOM 렌더러와 모바일·데스크톱 열폭 독립 저장 구현
- 모바일 fast-price 인덱스 보호와 기존 AHK·키움 HTS 클릭 연동 복원
- 화면 크기·폭 최소화·열 드래그·경계 더블클릭 자동맞춤 복원
- 모멘텀 아래 비어 있는 상단 metric 행 자동 숨김 구현
- 미국시장 전체 항목 12px 줄바꿈, 국내 그래프 원래 크기 1×4, 국내 숫자표 12px 전체 열 유지
- 문서 하단 가로 스크롤과 모바일 8열 12px 원래 글꼴 구현
- 모멘텀 스트립을 모바일 최상단 전체폭으로 복원
- `recv/s`, `stream`, `render`를 선발기준 다음의 원래 크기 개별 배지로 배치하고 폭 부족 시 자동 줄바꿈
- 속도·queue·drop·render 원본 배지와 StrategyBoard·새 창은 계속 숨김
- ThemeBoard 링크 유지와 ThemeBoard 선택자·데이터 경로 비접촉 회귀 고정
- 복원 브랜치 `restore/stockboard-mobile-topstatus-v3-20260717`을 변경 전 HEAD에 고정
- StockBoard CI Run #333 Windows regression 성공
- Targeted pytest 113 passed / 0 failed

### 10.5 모바일 최종 화면 실기 확인

2026-07-17 19:28 KST 대표님 브라우저 확인:

- 좁은 브라우저 폭에서 모바일 모드 자동 전환 정상
- 모멘텀 알림 행이 최상단에서 브라우저 우측 끝까지 전체폭으로 표시됨
- `StockBoard`, `ThemeBoard`, 시계, `데스크톱 보기`, `화면 100%`, `폭 최소화`, 연결 상태, 선발기준 표시 정상
- `recv/s`, `stream`, `render` 개별 배지가 선발기준 다음에 배치되고 폭 부족 시 다음 줄로 자연스럽게 이동함
- 미국시장 항목 줄바꿈, 국내 그래프 1×4, 국내시장 전체 열 표시 정상
- 모바일 8열, 열폭 조절, 하단 가로 스크롤, 종목 클릭 AHK·키움 HTS 연동 정상
- ThemeBoard 유지, StrategyBoard 숨김 정상
- 확인 당시 `recv/s 0.7`, `stream 116 ms`, `render 45.0 ms`, `연결 OK · 휴장일`
- 최종 정상 UI 복원 브랜치 `restore/stockboard-mobile-final-verified-20260717`을 커밋 `0c736556876a797f5875fc5a459a12b5d61e5a94`에 고정
- 문서 반영 전 최종 StockBoard CI Run #335 성공

### 10.6 국내시장 수급 20:00 last-good 구현

2026-07-17 20:41 KST 대표님 브라우저 관찰:

- 20:00 이후 국내시장 지수·등락률·상승·하락이 `-`로 바뀌고 개인·외인·기관·프로그램이 0으로 초기화되는 현상 확인
- 가격·종목·미국시장·stream·render는 정상으로 국내시장 context lifecycle 문제로 판정
- 비정상 초기화 스냅샷 거부와 거래일별 last-good 저장 구현
- 장마감·자정·주말·휴장·Worker 재시작 후 같은 완료 거래일값 복원 구현
- 다음 거래일 프리마켓에서 이전값을 유지하고 첫 정상 당일 스냅샷 수신 시 교체 구현
- 실제 0 수급값 허용, 지수·등락률·breadth 누락과 결합된 전체 0 초기화만 거부
- 정적 docs 스냅샷의 체크아웃 수정시각 오인을 막기 위해 runtime 원천만 신뢰
- `/api/v2/context.market_supply_status` 진단 추가
- 추가 QAx·FID·REST·WebSocket·thread·timer·SSE payload 0
- StockBoard CI Run #349 Windows regression 성공
- Targeted pytest 119 passed / 0 failed

### 10.7 휴장일 PC2 portable v2 복원 1차 실기 통과

2026-07-19 14:57 KST 대표님 PC2 브라우저 확인:

- PC가 바뀌어도 다른 PC의 runtime 복사 없이 직전 완료 거래일 `20260716` 보드를 재구성함
- 구형 `context_snapshot_writer.py`와 portable v2가 같은 `ohlc_snapshot.json`을 번갈아 덮어쓰던 원인을 제거함
- 생산 launcher는 `realtime_v2.context_snapshot_writer_portable_v2` 단일 소유자만 시작하며 legacy writer를 시작 전에 모두 종료함
- 완료 거래일 현재가는 `close_pric`, 등락률은 exact 날짜 행의 공식 `flu_rt`를 사용함
- 대표 종목 확인값: SK하이닉스 `1,830,000 / -12.10%`, 삼성전자 `253,500 / -9.30%`
- 거래대금·대금비·일봉·순위·등급·후보가 함께 채워지고, 시작 후 잘못된 seed 값으로 되돌아가던 현상이 대표님 관찰 기준 재발하지 않음
- 확인 화면에서 `stream 43 ms`, `collector_q 0`, `worker_q 0`, `drop 0`, `logdrop 0`
- portable exact snapshot과 heavy cache generation 전환이 완료된 뒤 전체 행을 원자적으로 공개함
- 단일 소유자 launcher 최종 커밋 `3d8b42e280e80afec1797b1bd60136365904769e`
- StockBoard CI Run #447 Windows regression 성공
- Targeted pytest 154 passed / 0 failed
- 판정: **휴장일 PC2 portable exact-close 표시 정확성 1차 통과**
- 다음 실제 거래일의 프리마켓·정규장·15:30·20:00 전환은 별도 실전 검증으로 유지함

## 11. 운영 명령

```powershell
cd C:\aiTrade
git fetch origin
git switch fix/restore-live-metrics-rest-20260715
git reset --hard origin/fix/restore-live-metrics-rest-20260715
.\stockboard_v2_large.cmd restart-fast
```

접속:

```text
http://127.0.0.1:8765/
```

실전 진단:

```powershell
.\stockboard_v2_large.cmd doctor
```

`doctor`는 기존 `large_doctor_report.txt`에 FID228/5분강도 진단을 이어서 기록한다. 다음 거래일에는 `EXECUTION_SOURCE_CONTRACT_OK=True`, `EXECUTION_TRUSTED_FID228_COUNT>0`, `EXECUTION_UNTRUSTED_POSITIVE_COUNT=0`을 우선 확인한다.

국내시장 수급 진단은 `/api/v2/context`의 `market_supply_status`에서 `display_basis`, `source_trading_date`, `candidate_reject_reason`을 확인한다.

Context writer 단일 소유자 진단:

```powershell
Get-Content `
  C:\aiTrade\data\runtime\stockboard_v2\context_owner_status.json `
  -Raw | ConvertFrom-Json | Format-List
```

정상 기준:

```text
ready                         True
context_writer_process_count  1
context_writer_owner_module   realtime_v2.context_snapshot_writer_portable_v2
legacy_context_writer_detected False
```

## 12. 남은 실전 검증

1. 다음 거래일 프리마켓 이전부터 실행해 대량체결 `EXACT_LIVE` 확인
2. 다음 거래일 FID228 체결강도가 5분강도와 독립적으로 저장·표시되는지 `doctor`로 확인
3. 다음 거래일 완료 1분봉에서 모멘텀 등급 셀 대체·동시 신호 교번·상단 Top100 전체 알림 확인
4. 신호 방향 유지, 이탈 후 2분 유지·1분 흐림·소멸, 감쇠 중 재활성 및 장마감 활성 신호 유지 확인
5. 실제 휴대전화 세로·가로 회전과 360~760px 폭에서 표·상단 모멘텀 알림 확인
6. 09:00~09:10 개장 폭주에서 queue·drop·stream·stale·render 확인
7. 잔량비 3회전 이상 후 Top100 최종 커버리지 측정
8. 다음 거래일 19:59→20:00 국내시장 수급 last-good 유지와 비정상 초기화 거부 확인
9. 자정·Worker 재시작·주말·휴장 후 국내시장 수급 복원 확인
10. 다음 거래일 08:00 이전값 유지와 첫 정상 당일 스냅샷 자동 교체 확인
11. 다음 거래일 프리마켓·정규장·15:30·20:00에서 portable board가 live passthrough와 exact-close로 정상 전환되는지 확인
12. NXT 거래·미거래 종목의 마지막 정상값 유지 확인
13. 다른 PC에서 재시작해도 context writer가 1개만 유지되고 legacy writer 재등장이 없는지 재확인

실제 다음 개장·장마감 검증 전에는 PR을 Draft로 유지하고 병합하지 않는다.
