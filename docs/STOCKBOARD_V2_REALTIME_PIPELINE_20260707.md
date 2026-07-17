# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-17 13:31 KST

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
- 별도 모멘텀 열은 만들지 않는다. 활성 신호가 있으면 **등급 셀에서 등급배지를 대신해 모멘텀 배지**를 표시한다.
- 시가와 VWAP 신호가 동시에 활성화되면 같은 등급 셀에서 CSS로 교번한다. 정렬·후보점수용 원래 등급값은 유지한다.
- 최신 완료봉이 조건을 계속 만족하면 배지는 활성 상태를 유지한다. 장마감 후 새 완료봉이 없으면 마지막 활성 배지를 계속 표시한다.
- 다음 완료봉에서 조건을 벗어나면 기본값으로 2분 정상 유지 후 1분 흐림 상태를 거쳐 사라지고 등급배지가 복귀한다.
- 전체 내부 유니버스의 활성 신호는 상단 알림 스트립에도 표시한다. SSE에는 version/count만 싣고 version 변경 때만 `/api/v2/momentum_alerts`를 로컬 조회한다.
- 기존 완료봉마다 발생하던 `momentum_1m_signal` background rebuild는 차단하고 신호 발생·교체·소멸 때만 rebuild한다.
- 추가 QAx·FID·Kiwoom REST·WebSocket·thread는 0이다.
- UI 툴팁은 일봉 캔들의 시·고·저·종 외에는 사용하지 않는다. 등급과 모멘텀에도 툴팁을 붙이지 않는다.

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
- 지연개장: 캘린더의 실제 프리마켓·정규장 시작시각 사용
- 재시작: daily state와 lifecycle snapshot에서 복원
- 다음 프리마켓: 전일 내부 누적·분 bucket·stage 일괄 초기화

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

- 설정 기반 8개 규칙 엔진, 상태 지속·이탈 감쇠·daily-state 복원 구현
- 모멘텀 열 제거, 등급 셀 우선 표시, 2개 신호 CSS 교번 구현
- 전체 내부 유니버스 상단 알림과 version-change 로컬 조회 구현
- 일봉 캔들 외 title 툴팁 제거
- 완료봉 단순 생성에 따른 불필요한 background rebuild 차단
- StockBoard CI Run #251 Windows regression 성공

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

## 12. 남은 실전 검증

1. 다음 거래일 프리마켓 이전부터 실행해 대량체결 `EXACT_LIVE` 확인
2. 다음 거래일 FID228 체결강도가 5분강도와 독립적으로 저장·표시되는지 `doctor`로 확인
3. 다음 거래일 완료 1분봉에서 모멘텀 등급 셀 대체·동시 신호 교번·상단 전체종목 알림 확인
4. 신호 이탈 후 2분 유지·1분 흐림·소멸 및 장마감 활성 신호 유지 확인
5. 09:00~09:10 개장 폭주에서 queue·drop·stream·stale·render 확인
6. 잔량비 3회전 이상 후 Top100 최종 커버리지 측정
7. 15:30·20:00·자정·익일 프리마켓 rollover 확인
8. NXT 거래·미거래 종목의 마지막 정상값 유지 확인

실제 다음 개장·장마감 검증 전에는 PR을 Draft로 유지하고 병합하지 않는다.
