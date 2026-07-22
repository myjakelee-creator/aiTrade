# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-22 16:27 KST

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
| 대금비 | 당일 누적대금 / 전일대금 | 금액 갱신 시 | 실제 퍼센트 정수 표시, 예: 133%·1569% |
| 1분대금 | FID14 누적값 차분 | 완료 1분과 직전 1분 | 매분, `금액 (비율)` 형식 |
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

### 2.2 대금비·1분대금 표시

```text
대금비    1.33 → 133%
대금비   15.69 → 1569%
1분대금  38.5 (156%)
1분대금  38.5 (NEW)
```

대금비:

- 원본 `amount_ratio`는 배수값을 그대로 유지한다.
- UI에서만 `amount_ratio × 100`을 반올림한 정수 퍼센트로 표시한다.
- 상한을 두지 않으므로 `10x+` 대신 `1000%`, `15.69`는 `1569%`로 표시한다.
- 정렬, 100% 이상 빨강·100% 미만 파랑, 계산식과 API 값은 바꾸지 않는다.

1분대금:

- 앞 숫자: 최근 완료 1분 거래대금(억원)
- 괄호 안 숫자: 직전 완료 1분 대비 비율
- 100% 미만 파랑, 100% 이상 빨강
- 직전값 0이고 현재값이 양수면 `(NEW)`
- 값·비율 계산, 정렬, 수집·발행 주기는 바꾸지 않는다.

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
- 각 규칙은 발생 조건과 방향 유지 조건을 분리한다.
- 별도 모멘텀 열은 만들지 않고 등급 셀에서 표시한다.
- 추가 QAx·FID·REST·WebSocket·thread는 0이다.

### 7.2 반응형 모바일 화면

- 설정 파일은 `config/stockboard_view_modes.json`, 표시 패치는 `realtime_v2/html_mobile_view_patch.py`이다.
- 기본 임계값은 viewport `760px`이다.
- 모바일은 8개 핵심 셀만 DOM에 생성한다.
- 기존 SSE·가격 fast patch를 재사용한다.
- 신규 QAx·FID·REST·WebSocket·Worker thread·SSE payload는 0이다.

### 7.3 국내시장 수급 last-good 유지

- 기존 `/api/v2/context` 15초 조회 경로를 그대로 사용하며 새로운 요청을 만들지 않는다.
- KOSPI·KOSDAQ 모두 지수, 등락률, 상승·하락 수, 개인·외인·기관·프로그램 수급이 숫자로 확인될 때만 정상 스냅샷으로 승인한다.
- 마지막 정상값은 거래일별 runtime 파일에 저장한다.
- 20:00 이후 장마감, 자정, 주말·공휴일, Worker 재시작에도 직전 완료 거래일 값을 유지한다.
- 다음 거래일 08:00은 삭제 시점이 아니라 신규 당일값 수용 시작 시각이다.
- 추가 QAx·FID·REST·WebSocket·Worker thread·브라우저 계산·타이머·SSE payload는 0이다.

### 7.4 PC 이동형 휴장·장마감 보드 복원

- 각 PC는 `realtime_v2.context_snapshot_writer_portable_v2` 한 프로세스만 사용한다.
- 완료 거래일 가격·OHLC 종가는 `close_pric` 계열만 사용한다.
- 등락률은 exact 날짜 행의 공식 `flu_rt`를 우선한다.
- 후보는 검증 후에만 `ohlc_snapshot.json`으로 원자 승격한다.
- 승인 계약은 `portable_closed_board_snapshot_v2` + `exact_daily_row_fields_v2`이다.
- 추가 QAx·FID·WebSocket·Worker thread·SSE 주기 변경은 0이다.

### 7.5 날짜·프리마켓 표시 연속성과 활성장 성능

2026-07-21 실기에서 08:00 직후 전일 exact-close 보드를 너무 일찍 해제하면 당일값이 들어온 필드와 아직 비어 있는 필드가 한 행에 섞이는 문제가 확인되었다.

승인 동작:

```text
직전 검증 exact-close 전체 유지
→ 다음 실제 프리마켓은 삭제가 아니라 당일값 수용 시작
→ 당일 승인 체결 종목만 LIVE 전환
→ 나머지는 전일 exact-close 유지
```

안전 규칙:

- 부분 candidate에 없는 종목은 seed·query-time 가격·등락률·거래대금·OHLC를 표시하지 않는다.
- hold overlay와 당일 체결 갱신은 같은 State RLock으로 직렬화한다.
- live+hold 혼합 cache를 previous verified cache로 재사용하지 않는다.
- 한 완료 거래일의 순수 `portable_exact_close` cache만 별도로 보존·재사용한다.
- phase·파일 수정시각만으로 표시 generation을 바꾸지 않는다.
- portable retry identity는 대상 거래일·policy·parser 기준으로 유지한다.

활성장 성능 보정:

- 초기 구현은 SSE snapshot 요청마다 전일 snapshot 파일 읽기, 187행 fingerprint 계산, 전체 payload deepcopy를 반복해 가격 추종 지연을 유발할 수 있었다.
- `worker_board_display_continuity_runtime_opt.py`는 직전 완료 거래일 payload를 최초 1회만 검증·메모리 적재한다.
- 이후 활성장 snapshot은 파일 읽기·fingerprint·payload deepcopy 없이 메모리 payload를 재사용한다.
- 프리마켓 exact 후보 재조회는 5초, 정규장·애프터마켓 재조회는 300초 간격이다.
- 전일 hold overlay는 기존처럼 최대 2초당 1회만 수행한다.
- 진단 상태는 `board_display_active_payload_cache_status`, `board_display_active_payload_file_read_count`, `board_display_active_payload_retry_sec`, `board_display_active_apply_ms`로 확인한다.
- 추가 QAx·FID·REST·WebSocket·thread·timer·SSE cadence는 0이다.

### 7.6 `_AL` 가격 순서 보정과 새 거래일 누적 초기화

SOR 통합 `_AL` 스트림에서는 거래소별 이벤트가 교차 도착해 FID20과 누적거래대금이 직전 승인값보다 작게 보일 수 있다. 같은 거래일에서는 현재가·등락률만 살리고 역행 누적 필드를 기존처럼 유지한다.

새 거래일 초기화는 다음 세 날짜가 모두 명확할 때만 승인한다.

```text
Worker current trading date
= collector event receive date
> existing cumulative field trading date
```

- QAx 이벤트에는 별도 거래일 필드가 없으므로 이벤트 수신시각 `ts`의 날짜를 사용한다.
- 기존 거래대금 날짜는 `trade_value_trading_date`, 없으면 검증된 `source_trading_date`를 사용한다.
- 날짜가 없거나 서로 일치하지 않으면 fail-closed로 기존 누적값을 유지한다.
- 다음 거래일로 확인된 감소만 당일 FID14·누적거래량으로 교체한다.
- 승인 상태는 `daily_cumulative_reset_accepted_count`, `trade_field_regression_accepted_reason_counts`, `last_daily_cumulative_reset_accepted`로 확인한다.

수동 진단:

```powershell
.\stockboard_v2_large.cmd data-doctor
.\stockboard_v2_large.cmd price-doctor
.\stockboard_v2_large.cmd price-trace
.\stockboard_v2_large.cmd collector-trace
.\stockboard_v2_large.cmd price-backtrace
```

- `data-doctor`: 현재 snapshot만 읽어 값·원천·거래일·내부 계산과 새 거래일 reset 승인 상태를 저장한다.
- `price-doctor`: 사용자가 실행한 순간에만 ka10032를 1회 조회한다. 키움 전체시장 원순위는 참고용으로 두고 StockBoard 적격종목으로 필터링한 키움 적격순위와 비교한다.
- `price-trace`: 기존 JSONL event log·SSE·snapshot으로 종목별 Collector 출력 이후 가격 경로를 15초 추적한다.
- `collector-trace`: 기존 heartbeat 카운터만 15초 읽는다. 선택 상태가 `None`이어도 하위 단계 카운터 증가가 확인되면 활성 경로로 판정한다.
- `price-backtrace`: 저장된 `price_compare_*.json`과 `events_YYYYMMDD.jsonl`만 읽어 특정 비교시각 직전 Collector 출력 이벤트와 저장된 Worker 행을 대조한다.
- 모든 진단은 사용자가 실행할 때만 동작하며 평상시 background load를 추가하지 않는다.

### 7.7 비공개·공개 웹 서비스 경계

```text
StockBoard v2 canonical worker  127.0.0.1:8765  private / unchanged
Public read-only gateway        127.0.0.1:8767  loopback only
Tailscale Funnel                HTTPS public edge
```

- 기존 생산 Worker와 가격 경로는 `127.0.0.1:8765`에 그대로 유지한다.
- 공개 Gateway는 요청마다 8765가 실제로 제공하는 현재 UI를 가져오므로 과거 정적 HTML을 사용하지 않는다.
- 공개 snapshot/context는 명시적 allowlist 필드만 전달한다.
- PID·파일 경로·수집기 내부 상태·원천 raw 데이터·오류 상세는 외부에 전달하지 않는다.
- POST·PUT·PATCH·DELETE와 서버 제어 API는 차단한다.
- 공개 화면에서는 HTS 연동과 서버 제어를 제거하고 종목코드 복사만 허용한다.
- Gateway는 `127.0.0.1:8767`에만 바인딩하며 공유기 포트포워딩과 `0.0.0.0` 바인딩은 금지한다.
- Windows PowerShell 5.1 호환을 위해 공개 운영 PowerShell은 ASCII-only 계약을 유지한다.
- 정식 공개 주소는 쿼리 없는 `https://gram-jlee.tail04774a.ts.net` 루트다.
- 루트 요청은 매번 현재 8765 UI를 가져오며 `no-store`, `no-cache`, `Expires: 0` 응답 계약을 적용한다.
- 과거 쿼리 주소로 접속해도 브라우저 주소창은 자동으로 루트 URL로 정리한다.
- 루트 계약은 `stockboard_public_root_no_query_v1_20260722`이다.
- 공개 상단의 복사 안내·진단·속도·렌더·셀 토글·색상 설명은 공개 화면에서만 제거한다.
- 원본 모바일 보기에서 의도적으로 복원되는 `recv/s`, `stream|poll ... ms`, `render ... ms`도 공개 화면에서는 ID와 문구 양쪽으로 제거한다.
- 모바일 전환·화면 회전·페이지 복귀·동적 DOM 변경 뒤에도 다시 나타나지 않도록 감시한다.
- 공개 최종 상단 정리 계약은 `stockboard_public_chrome_cleanup_v3_20260722`이며 비공개 8765에는 적용하지 않는다.

### 7.8 재부팅 후 공개 단일 런처

```text
C:\aiTrade\stockboard_public.cmd
  1 Start everything and publish
  2 Show all status
  3 Stop everything and disable public access
```

- 전체 시작 순서는 Tailscale 확인 → 8765 생산 StockBoard → OpenAPI connected/SetRealReg 확인 → 8767 공개 Gateway → Funnel이다.
- 생산 8765가 이미 정상일 때는 재시작하지 않고 공개 단계만 이어간다.
- `collector32.pid`의 실제 Windows 프로세스 생존과 `login=connected`, `realreg=True`, 등록종목 수를 함께 판정한다.
- PowerShell 5.1의 `$code:` 파싱 오류는 `${code}:`로 수정했다.
- 하위 생산 런처는 별도 프로세스로 실행하고 로그인 대기 중 5초마다 진행상태를 표시한다.
- 공개 전용 긴급 복구는 메뉴 `5 Publish public gateway only`를 사용한다.
- 전체 시작 계약 버전은 `stockboard_public_all_v5_20260722`이다.

### 7.9 UI 버전 식별과 안정판

```text
안정판   VER SBV2-20260722.2 · PUBLIC-OPS-ID
후보판   VER SBV2-20260722.3 · TRADE-VALUE-ROLLOVER
```

- 버전명에 `YYYYMMDD`가 있으면 날짜·시간을 화면에 중복 표시하지 않는다.
- 버전명에 날짜가 없을 때만 날짜를 별도로 표시한다.
- 전체 적용시각은 tooltip·설정·문서에서 확인한다.
- 안정 브랜치 `stable/SBV2-20260722.2`는 불변 기준점으로 유지한다.
- 후보판 실패 시 전체 커밋 SHA `6e48d6dce34f995770a26aacec30b7e5621e0889`로 복원한다.

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

내부 거래일 state 초기화 시점:

```text
다음 실제 거래일 프리마켓 시작
```

표시값 교체 시점:

```text
당일 첫 정상값 수신 시 해당 종목·해당 지표를 전일 hold에서 당일값으로 교체
```

운영 규칙:

- NXT 거래 종목: `_AL` 기준 20:00까지 갱신
- NXT 미거래 종목: 15:30 정규장 마지막 정상값 유지
- 20:00 이후: 신규 수집 중지, 마지막 정상값 고정
- 주말·공휴일: 신규조회 중지, 직전 완료 거래일 유지
- PC 변경 시 portable v2 exact snapshot을 해당 PC에서 재구성
- 지연개장: 캘린더의 실제 프리마켓·정규장 시작시각 사용
- 재시작: daily state와 lifecycle snapshot에서 복원
- 다음 프리마켓: 내부 누적·분 bucket·stage는 초기화하지만 표시값은 당일 정상값 수신 전까지 전일 exact를 유지

## 9. 개장 성능 보호

```text
가격 QAx 경로                  불변
가격·등락률 fast patch         계속
전체 heavy render              개장 첫 10분 1000ms
ka10046                         실제 개장 후 첫 5분 중지
체결강도·대량체결              같은 64-bit 0B 스트림
1분대금                         기존 FID14 차분
잔량비                           20종목 순환
모멘텀                          완료 1분봉에서만 규칙 평가
모바일                          기존 SSE, 행당 8개 셀만 생성
표시 연속성 active payload      프리마켓 5초 / 활성장 300초 재조회, SSE는 메모리 사용
```

위험 신호:

```text
PipelineState=CHECK
snapshot age 4초 초과 지속
collector_q 지속 증가
worker_q 지속 증가
drop / logdrop 증가
stream latency 지속 상승
render 70ms 초과 지속
active payload file_read_count가 300초 정책보다 빠르게 증가
active apply ms 지속 상승
```

`LastTradeAgeSec`·`NoRecentTrade` 증가는 종목별 무체결을 뜻하며 파이프라인 장애 신호로 사용하지 않는다. 다만 거래대금 상위 다수 종목이 동시에 100초 이상 오래되면 별도 가격 경로 진단을 수행한다.

가격 경로가 최우선이며, 문제 발생 시 잔량비 → 5분강도 순으로 중지·지연한다.

## 10. 실전 확인

### 10.1 기존 확인

- 가격·등락률·누적대금 통과
- 1분대금 통과
- 체결강도 Top100 통과
- 5분강도 조회·유지 통과
- 프로그램 통과
- 잔량비 실시간 원천 통과, 종목 커버리지는 조건부
- 대량체결 수집 통과, 장중 시작일은 `GAP_POSSIBLE`
- 일봉 종가 실시간 현재가 일치 통과

### 10.2 2026-07-21 표시 연속성 실기

- 날짜 변경 후 빈 화면 방지 통과
- 전일 exact 유지 후 당일 종목별 LIVE 전환 통과
- 장시간 `PipelineState=HEALTHY`, Worker queue/drop 정상 확인
- 당시 price-doctor 30종목 중 가격 22종목 일치, 최대 등락률 차이 0.13%p, 거래대금 차이율 0.0588%

### 10.3 2026-07-22 공개 웹 서비스 실기

- 비공개 8765와 공개 8767 UI 동기화 통과
- Tailscale Funnel 루트 주소 PC·모바일 접속 통과
- 공개 읽기 전용 allowlist와 제어 제거 통과
- 공개 화면에서 진단·속도·렌더 문구 제거 통과
- 생산 Worker·QAx collector·WebSocket·REST·SSE 주기 변경 0

### 10.4 2026-07-22 후보판 거래대금 실기

```text
PipelineState                         HEALTHY
WorkerQueue                           0
WorkerDrop                            0/blank
TradeFieldGuardVersion                trade_field_regression_guard_v3
RatioMismatch                         0
ka10032 found                         30/30
trade value within 0.5%               30/30
max trade value delta                 0.3366%
price exact                           4/30
max price delta                       8,000원
max change-rate delta                 1.27%p
NoRecentTrade                         29/30
```

- 거래대금 경로는 통과했다.
- 기존 순위 `REVIEW`는 키움 전체시장 원순위와 StockBoard 적격 압축순위를 직접 비교한 진단 정의 오류였다.
- 생산 순위 계산은 변경하지 않고 키움 적격순위를 재계산하는 방식으로 진단기를 수정했다.
- 가격·등락률은 별도 원인 추적이 필요하다.

### 10.5 2026-07-22 가격 경로 추적

15:36 `price-trace`:

```text
정규장 종료 후 15초
추적 5종목 Collector 출력 이벤트 0
Worker trade 증가 0
SSE snapshot 7회
SSE payload 지연 중앙값 172ms
```

정규장 종료 후 측정이므로 장중 가격 지연 원인을 확정하는 자료로 사용하지 않는다.

15:59 `collector-trace`:

```text
QAx realdata callback 증가       439
주식체결 callback 증가           438
EventSender 거래 수신 증가       438
EventSender 전송 증가            391
Worker 거래 적용 증가            342
Worker drop 증가                 0
누적필드 보류 증가               296
```

- QAx → Provider → EventSender → Worker 경로는 애프터마켓에서 활성 상태였다.
- `provider_qt_pump_running=None`, `provider_trade_event_applied_count=None`을 `False/0`으로 취급한 기존 `QT_EVENT_PUMP_NOT_RUNNING` 판정은 진단기 오판이다.
- 명시적 `False`만 장애로 판정하고, 하위 단계 카운터가 증가하면 `PRICE_PATH_ACTIVE_WITH_OPTIONAL_STATUS_UNKNOWN`으로 판정한다.
- 14:57 가격 차이의 위치는 저장된 `price_compare_20260722_145718.json`과 `events_20260722.jsonl` 역추적으로 확인한다.
- JSONL에는 원시 Collector 출력 이벤트만 있고 guard 결정 결과는 직접 기록되지 않으므로 `WORKER_OR_GUARD_NOT_APPLIED`는 이벤트와 저장 행을 비교한 추론임을 명시한다.

### 10.6 남은 실기

1. `price-backtrace`로 14:57 종목별 Collector 출력 공백과 Worker 행 불일치를 분리
2. 다음 실제 정규장과 08:59~09:05에 `collector-trace` 재실행
3. 다음 실제 프리마켓에서 당일 누적 reset 승인 확인
4. 위 조건 전까지 Draft 유지·병합 금지

## 11. 운영 명령

### 11.1 후보판 적용

```powershell
cd C:\aiTrade
git fetch origin
git switch hotfix/SBV2-20260722.2-trade-value-rollover
git pull --ff-only
.\stockboard_v2_large.cmd restart-fast
```

### 11.2 수동 진단

```powershell
.\stockboard_v2_large.cmd price-doctor
.\stockboard_v2_large.cmd data-doctor
.\stockboard_v2_large.cmd price-trace
.\stockboard_v2_large.cmd collector-trace
.\stockboard_v2_large.cmd price-backtrace
```

14:57 보고서를 명시한 역추적:

```powershell
py -3 scripts\stockboard_v2_price_history_backtrace.py `
  --compare-json C:\aiTrade\data\runtime\stockboard_v2\price_compare_20260722_145718.json `
  --event-log C:\aiTrade\data\runtime\stockboard_v2\events_20260722.jsonl
```

### 11.3 안정판 즉시 복원

```powershell
cd C:\aiTrade
git fetch origin
git switch stable/SBV2-20260722.2
.\stockboard_v2_large.cmd restart-fast
```

2026-07-22 16:27 KST 기준으로 거래대금 경로는 통과했지만 가격·등락률 후보판 승격은 보류한다. 14:57 과거 로그 역추적과 다음 실제 정규장·프리마켓 검증 후에만 새 안정판 승격을 판단한다.
