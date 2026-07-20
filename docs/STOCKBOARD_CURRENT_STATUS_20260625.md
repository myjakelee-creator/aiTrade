# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-16 18:20 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-live-metrics-rest-20260715`  
기준 브랜치: `fix/restore-stable-collector-20260713`  
Draft PR: `#37`

> 과거 상세 이력은 Git history와 PR 기록에서 확인한다. 이 문서는 현재 운영 구조, 실측 결과, 남은 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard v2는 **32비트 가격 전용 최소 QAx collector 한 개 + 64비트 WebSocket/REST 보조지표 + 분 단위 UI 발행 + 거래일 last-good 유지** 구조로 정상 동작한다.

2026-07-16 애프터마켓 실측:

```text
가격·등락률           정상
실시간 표시 행         100
worker_q / drop        0 / 0
logdrop                 0
stream                  약 22~35ms
render                  약 7~48ms
체결강도 Top100         정상
5분강도                 정상 유지
프로그램                정상
1분대금                 정상
잔량비                  실시간 0D 수신 정상, 일부 종목 결측 허용
대량체결                수집 정상, 오늘은 장중 시작으로 GAP_POSSIBLE
일봉 종가               실시간 현재가 우선 적용 정상
```

현재 기본 선발모델은 **`거래대금 순위 v0.1`**이다.

---

## 1. 현재 운영 구조

```text
32-bit production price-only QAx collector — 유일한 QAx 실시간 owner
  FID10 현재가
  FID12 등락률
  FID20 체결시각
  FID14 누적거래대금 500ms sampling
  SetRealReg 100종목 _AL
        ↓ latest-only sender
64-bit canonical worker
  ├─ WebSocket 0B Top100: FID228 체결강도 + FID15 signed 체결량
  ├─ WebSocket 0D: 호가 20종목 × 12초 순환
  ├─ ka10046: 5분강도 3초당 1종목
  ├─ ka90004: 프로그램 60초 일괄조회
  ├─ FID14 차분: 1분대금
  ├─ minute publisher / lifecycle / safety / rollover guard
  └─ SSE → StockBoard / ThemeBoard / StrategyProjection
```

| 항목 | 현재 원칙 |
|---|---|
| QAx owner | 32비트 가격 collector 한 개만 허용 |
| collector mode | `minimal_qax_price_only_v2` |
| 가격 FID | `10;12;20;14` |
| 가격 등록 | Top100 `_AL` |
| 보조 WebSocket | 64비트 연결 1개 |
| REST | one thread / one in-flight / single-flight |
| 브라우저 | 표시만 담당, 직접 TR·시장계산 금지 |
| 기본 선발모델 | `거래대금 순위 v0.1` |
| PR 상태 | 실전 검증 전 Draft 유지 |

---

## 2. 가격·대금·일봉

### 2.1 가격 경로

```text
현재가·등락률      체결 이벤트마다, UI 약 50~150ms
누적거래대금       FID14 종목별 최대 500ms sampling
금액·대금비·일봉  일반 500ms, 개장 첫 10분 1000ms
```

생산 가격 collector에서는 다음을 하지 않는다.

```text
FID15 조회
호가잔량 조회
체결강도 조회
대량체결 누적
보조 TR
두 번째 QAx 로그인
두 번째 SetRealReg owner
```

### 2.2 일봉

```text
시가 = 기존 OHLC.open
고가 = 기존 OHLC.high
저가 = 기존 OHLC.low
종가 = 실시간 row.price 최우선
```

종가 선택은 브라우저 값 선택만 바꿨으며 네트워크·QAx·Worker·렌더 주기 부담을 추가하지 않는다.

### 2.3 1분대금

```text
21.4 79%
```

- 최근 완료 1분 거래대금과 직전 완료 1분 대비 비율
- 100% 미만 파랑, 100% 이상 빨강
- 직전 0·현재 양수면 `NEW`
- 추가 키움 요청 없이 FID14 차분 사용

---

## 3. 잔량비

```text
20종목 × 12초 × 5그룹 = 60초 Top100 회전
원천 = WebSocket 0D FID121 / FID125
UI = 매분 last-good 발행
```

운영 원칙:

- 이벤트를 받은 종목만 표시
- 못 받은 종목은 `-`
- ka10004 REST로 가짜 보정하지 않음
- 180초 무응답이면 잔량비 기능만 자동 포기
- 20:00 이후 확보한 마지막 정상값은 다음 프리마켓까지 유지

NXT 미거래 종목은 애프터마켓에서 새 호가가 없을 수 있다. 정규장 중 확보한 마지막 값이 있으면 유지하고, 새 버전을 정규장 마감 뒤 시작해 값이 없었던 종목은 계속 `-`로 둔다.

---

## 4. 체결강도·5분강도·프로그램

### 4.1 체결강도

```text
원천       WebSocket 0B/FID228
범위       Top100
수집       이벤트마다
UI         60초
유지       다음 정상값 또는 다음 프리마켓까지
```

### 4.2 5분강도

```text
원천       ka10046 5분·20분·60분 필드
간격       3초당 1종목
처리량     분당 약 20종목
UI         완료 종목을 매분
보존       다음 성공 조회까지
개장 보호  실제 정규장 시작 후 첫 5분 중지
```

정상값을 시간 경과만으로 삭제하지 않는다.

### 4.3 프로그램

```text
원천       ka90004
주기       60초 일괄조회
오류       직전 당일 정상값 유지
0          실제 0만 0, 미수신은 -
```

---

## 5. 대량체결

```text
원천       WebSocket 0B/FID15
기준       abs(체결가 × signed 체결량) >= 5천만원
집계       원시 이벤트마다 coalescing 전에 수행
UI         60초
저장       5초 checkpoint
```

품질 상태:

```text
EXACT_LIVE       프리마켓 이전부터 연속 수집
EXACT_RECONCILED 공백 완전 복구
GAP_POSSIBLE     장중 재시작·재접속 공백 가능
```

`GAP_POSSIBLE`은 화면에 `~`를 표시한다. 오늘은 장중 적용했으므로 `approved_large_full_session_coverage=False`가 정상이다.

두 번째 QAx sidecar는 생산에서 계속 금지한다.

---

## 6. 데이터 유지와 거래일 전환

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

- NXT 거래 종목은 `_AL` 기준 20:00까지 갱신
- NXT 미거래 종목은 15:30 마지막 정상값 유지
- 20:00 이후 신규 수집 중지·last-good 고정
- 주말·공휴일 신규조회 중지
- 지연개장은 시장 캘린더 사용
- 다음 프리마켓에서 전일 누적·bucket·stage 일괄 초기화

---

## 7. 2026-07-16 확인 결과

```text
realtime_strength_ws_status          ok
realtime_strength_ws_backend         websockets.sync
realtime_strength_ws_selected_count  100
0B event count = raw processed count  일치
0D orderbook raw event               정상 증가
ka10046 request/success/error         57 / 57 / 0
worker_q / drop / logdrop             0 / 0 / 0
1분대금 quality                       COMPLETE_MINUTE
```

| 항목 | 판정 |
|---|---|
| 가격·등락률·누적대금 | 통과 |
| 체결강도 Top100 | 통과 |
| 5분강도 조회·유지 | 통과 |
| 프로그램 | 통과 |
| 1분대금 | 통과 |
| 일봉 종가 현재가 일치 | 통과 |
| 잔량비 | 실시간 원천 통과, 커버리지 조건부 |
| 대량체결 | 수집 통과, 장중 시작일은 GAP_POSSIBLE |

---

## 8. 운영 명령

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

---

## 9. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 다음 거래일 프리마켓 이전 실행 | 대량체결 `EXACT_LIVE` |
| 2 | 09:00~09:10 개장 폭주 검증 | queue·drop 0, stream·stale 허용범위 |
| 3 | 잔량비 3회전 이상 커버리지 | 실시간 수신 종목 수·미수신 사유 확정 |
| 4 | 15:30·20:00 유지 검증 | NXT 거래·미거래 종목 last-good 유지 |
| 5 | 자정·익일 프리마켓 rollover | 전일 유지 후 정확한 초기화 |
| 6 | Draft PR #37 정리 | 실전 검증 후 병합 여부 판단 |

---

## 10. 금지사항

```text
두 번째 QAx 실시간 owner를 생산에서 실행하지 않는다.
생산 가격 collector에 FID15·호가 FID를 추가하지 않는다.
가격 callback에 보조지표·후보점수 계산을 넣지 않는다.
HTML에서 시장계산·직접 TR을 수행하지 않는다.
불명확한 잔량비·대량체결을 가짜 0으로 표시하지 않는다.
다음 개장·장마감 검증 전 Draft PR을 병합하지 않는다.
```

---

## 11. 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 생산 가격 전용 최소 QAx collector |
| `realtime_v2/worker64_guarded_large_bidask.py` | StockBoard worker/UI 진입점 |
| `realtime_v2/worker_approved_minute_pipeline.py` | 체결·호가·대량체결·1분대금 분 단위 파이프라인 |
| `realtime_v2/worker_approved_minute_pipeline_runtime_fix.py` | ka10046 개장 지연·런타임 보정 |
| `realtime_v2/worker_approved_minute_pipeline_safety.py` | 호가 fail-closed·대량체결 checkpoint |
| `realtime_v2/worker_approved_minute_rollover_guard.py` | 다음 프리마켓 거래일 전환 |
| `realtime_v2/html_approved_minute_metrics_patch.py` | 1분대금 열·분 단위 UI 표시 |
| `realtime_v2/html_realtime_candle_close_patch.py` | 일봉 종가 실시간 현재가 우선 |
| `realtime_v2/tr_singleflight.py` | REST single-flight |
| `configs/stockboard_live_metrics_rest.json` | 보조지표 주기·원천·성능 계약 |
| `stockboard_v2_large.cmd` | 생산 런처 |
