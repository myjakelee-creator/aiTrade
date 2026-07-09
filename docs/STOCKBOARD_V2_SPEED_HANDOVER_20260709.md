# StockBoard v2 속도개선 인계서

작성일: 2026-07-09  
대상 브랜치: `hot-priority-integrated-20260630`  
목적: 새 채팅창/후속 설계실에서 StockBoard v2 속도개선 작업을 이어가기 위한 최신 인계 문서  
원칙: 대표님 승인 전 코딩 금지. 먼저 설계·영향 범위·검증 방법을 확정한다.

---

## 1. 새 채팅 첫 지침

새 채팅창에서는 아래 한 줄로 시작한다.

```text
StockBoard v2 속도개선 설계 이어가자. 코딩 금지. 기준 문서는 docs/STOCKBOARD_V2_SPEED_HANDOVER_20260709.md 와 docs/STOCKBOARD_V2_SPEED_IMPROVEMENT_DESIGN_20260709.md 이다.
```

후속 답변에서 먼저 해야 할 일:

```text
1. 인계서와 속도개선 설계도 확인
2. 현재 로컬 실행 상태와 09:00~09:05 관찰값 확보 여부 확인
3. 코딩 없이 구현 범위·파일 영향·검증 기준 정리
4. 대표님 승인 후에만 GitHub/Codex 방식으로 복붙 없이 진행
```

---

## 2. 최신 기준 문서

| 문서 | 용도 |
|---|---|
| `docs/STOCKBOARD_V2_SPEED_IMPROVEMENT_DESIGN_20260709.md` | 속도개선 전체 설계도. UI/내부 처리 구조, 병목 우선순위, 칼럼별 정책, 구현 순서 포함 |
| `docs/STOCKBOARD_V2_SPEED_HANDOVER_20260709.md` | 현재 인계 문서. 최신 대량체결 검증 상태, 남은 확인 자료, 다음 작업 루틴 포함 |
| `docs/kiwom_realtime_architecture.md` | 키움 OpenAPI 09:00 폭주 처리 아키텍처 참고 문서. 수신·연산·전송·렌더링 분리 원칙 확인용 |

---

## 3. 현재 확정된 방향

### 3.1 화면 UI 구조

```text
S1 / Top20 / Top300
```

- S1: 대표님이 직접 선택한 종목. 항상 최우선 갱신.
- Top20: 실전 후보 핵심 구간. 빠른 갱신과 자동 승격/강등 허용.
- Top300: 넓은 감시망. 화면에는 Pool로 표시하되 내부 행 이동과 전체 재렌더링을 제한.

### 3.2 내부 처리 구조

```text
S1 / Top20 / Hidden Top50 / Top300
```

- Hidden Top50은 화면에는 따로 만들지 않는다.
- 21~50위 구간을 Top20 진입 예비 후보로 보고 내부 warm lane으로 관리한다.
- Top20 진입 직전 종목의 가격/체결 최신성을 미리 확보하기 위한 완충 구간이다.

---

## 4. 현재 대량체결 구현·검증 상태

### 4.1 구현 파일

대량체결은 기존 안정 파일을 바로 덮어쓰지 않고 wrapper 방식으로 구현했다.

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large.py` | 기존 32비트 collector를 import하고, coalescing 전에 5천만원 이상 대량체결 buy/sell count·금액 delta를 micro-batch 집계 |
| `realtime_v2/worker64_guarded_large.py` | 기존 guarded worker를 import하고, collector delta를 종목별 누적값으로 반영·daily_state persist |
| `stockboard_v2_large.cmd` | 대량체결 wrapper 실행용 런처. `restart`, `status`, `doctor` 지원 |

### 4.2 주요 커밋

| 커밋 | 내용 |
|---|---|
| `6892e016cdce637961da9e5cb4b453a6db0f9425` | 대량체결 wrapper 런처 추가 |
| `7b3360ef44174d6d94c93cb7b4f5a106f8810d13` | wrapper import path 수정 |
| `222be4371e2db58a67fd2733dc69f455cb75fbf2` | `stockboard_v2_large.cmd doctor` 추가 |
| `8b799839807d2c964e1d8d6f48296d58db05dab5` | 속도개선 설계도 문서 추가 |

### 4.3 doctor 검증 결과 요약

대표님이 업로드한 `large_doctor_report.txt` 기준, 2026-07-09 14:33에 대량체결 wrapper는 정상 작동했다.

```text
GIT_BRANCH=hot-priority-integrated-20260630
GIT_HEAD=222be43
PY_COMPILE=True
HEALTH=True
SNAPSHOT=True
ROW_COUNT=177
EVENT_COUNT=209328
TRADE_COUNT=88526
ORDERBOOK_COUNT=44350
COLLECTOR_PROVIDER_STARTED=True
COLLECTOR_REGISTERED_COUNT=177
COLLECTOR_CONNECTED=True
COLLECTOR_SENT_PER_SEC=128.06
COLLECTOR_LARGE_BUY_COUNT=1916
COLLECTOR_LARGE_SELL_COUNT=2068
COLLECTOR_LARGE_BUY_SUM_EOK=2941.4621
COLLECTOR_LARGE_SELL_SUM_EOK=3236.8031
ROWS_WITH_LARGE_TRADE=159
```

LS ELECTRIC 확인값:

```text
010120 LS ELECTRIC
buy=26
sell=23
net=3
buy_eok=24.591
sell_eok=20.0171
net_eok=4.5739
source=collector_aggregate
```

판정:

```text
대량체결 wrapper: 정상 작동
collector aggregate: 정상
LS ELECTRIC 반영: 정상
주의: 실행 이후 누적값 기준이므로 키움 당일 전체 누적과 직접 비교하면 안 됨
```

---

## 5. 현재 남은 확인 자료

후속 설계실이 아직 모르는 정보는 아래 4개다. 이 중 특히 09:00~09:05 관찰값이 중요하다.

| 필요 자료 | 이유 | 현재 상태 |
|---|---|---|
| 최신 인계 문서 1개 | 2026-07-09 이후 논의가 repo 문서에 없으면 반영 필요 | 본 문서로 반영 완료 |
| 장개시 실제 상단 지표 | stream, collector_q, worker_q, top20 lag, stale, render ms 확인 필요 | 아직 09:00~09:05 실측값 없음 |
| 현재 체감 증상 | “가격이 늦다”, “화면이 버벅인다”, “Top20이 흐려진다”, “HTS와 값이 다르다” 중 무엇인지 구분 필요 | 대표님 체감 보고 필요 |
| `stockboard_v2_live.cmd status` 또는 `stockboard_v2_large.cmd doctor` 출력 | collector/worker/AHK/orderbook 실행 상태 확인 | large doctor는 14:33 기준 확보. 장개시용은 추가 필요 |

---

## 6. 속도개선 핵심 설계 요약

| 항목 | Pool | 현재방식 | 개선방법 |
|---|---|---|---|
| Top20 lag / stale | S1 / Top20 / Hidden Top50 / Top300 | 모든 trade 이벤트가 거의 같은 처리 흐름. Top20도 폭주 시 늦어질 수 있음 | S1·Top20 hot lane, Hidden Top50 warm lane. 오래된 FID20/체결 이벤트 강제 폐기 |
| Top300 Pool 전체 재렌더링 | Top300 | 14개 칼럼 전체 HTML을 다시 만들고 `tbody.innerHTML` 교체 | Pool 내부 행 위치 고정. 종목코드 기준 `<tr>` 재사용. 바뀐 `td`만 갱신 |
| 등급점수 기반 행 이동 | Top20 / Top300 | 점수 변화에 따라 Pool 내부까지 행 이동 가능 | Top20은 완충 규칙으로 승격/강등. Top300 Pool 내부 자동 이동 금지. 수동 정렬 허용 |
| Top20 경계 흔들림 | Top20 / Hidden Top50 | 20위 근처 종목이 자주 올라갔다 내려갈 수 있음 | Hidden Top50 완충. 연속 진입/점수 차이/최소 유지시간 적용 |
| 잔량비/orderbook 부담 | S1 / Top20 / Hidden Top50 / Top300 | normal 모드에서 orderbook을 넓게 받아 잔량비 계산 | 잔량비 저속 확정. S1 빠르게, Top20 준실시간, Hidden Top50 순차 저속, Top300 저속/마지막값 |
| 1분강도 정합성 | S1 / Top20 / Hidden Top50 / Top300 | 브라우저 보조 계산이 남아 있고 순간강도와 편차 큼 | 1분강도 칼럼을 키움 조회 기반 5분강도로 교체. 브라우저 강도 계산 제거 |
| 일봉 mini-candle | S1 / Top20 / Top300 | 행마다 OHLC 계산, mini-candle HTML, style 변수 생성 | 삭제하지 않음. S1/Top20 유지. Top300 Pool은 저속 갱신 또는 OHLC 변경 시만 갱신 |
| 잔량비/강도 색상바 | S1 / Top20 / Top300 | 행마다 gradient style 생성 가능 | 색상바 유지하되 bucket class 방식으로 경량화 |
| 툴팁 | S1 / Top20 / Top300 | row title, metric title 등을 행마다 생성 | S1/Top20 유지. Top300 Pool tooltip 제거. 필요 시 공용 상세 패널 |
| 300행 stream/snapshot | 전체 | `limit=300&interval_ms=100` stream 사용 | 감시는 300 유지. 화면 갱신은 S1/Top20 고속, Top300 부분/저속 갱신 |

---

## 7. 잔량비 정책 확정

잔량비는 **저속**으로 정한다.

```text
실시간 잔량비:
- S1 중심
- Top20은 준실시간 또는 빠른 저속

저속 잔량비:
- Hidden Top50과 Top300 대상
- 20개씩 순차 또는 제한된 batch로 천천히 갱신

마지막값:
- 최신 조회가 없을 때 마지막 정상값 표시
- 오래되면 흐리게/stale 표시
```

잔량비는 orderbook 기반이라 부담이 크다. 장초반에는 trade 최신성 확보가 더 중요하므로 Pool 전체 잔량비 실시간 유지보다 저속·마지막값 방식이 맞다.

---

## 8. 1분강도 → 5분강도 방향

현재 1분강도는 순간강도와 편차가 크고 정규장에서도 신뢰도가 낮다는 관찰이 있었다. 따라서 아래 방향이 유력하다.

```text
현재:
- 1분강도는 collector/브라우저 보조 계산 영향이 있고 흔들림이 큼

개선:
- 1분강도 칼럼을 키움 조회 기반 5분강도로 교체
- S1/Top20 우선 조회
- Hidden Top50 저속 조회
- Top300은 저속 또는 마지막값
- 429 발생 시 backoff
```

주의:

```text
Top300 전체를 짧은 주기로 조회하면 안 됨
조회는 별도 저속 worker/context로 분리
```

---

## 9. 09:00 장개시 실측 목표

장개시 09:00~09:05에는 아래 지표를 반드시 남긴다.

| 지표 | 목표 |
|---|---:|
| Top20 lag | 1~5초 이내 |
| stale | 0~3 수준 |
| render ms | 10~40ms권 목표 |
| collector_q | 폭주 시에도 장시간 누적되지 않아야 함 |
| worker_q | backlog가 지속 증가하지 않아야 함 |
| collector sent/s | 갑작스런 급락/정지 여부 확인 |
| trade/s | 장초반 체결 폭주량 확인 |
| orderbook count | 잔량비 저속화 필요성 확인 |
| HTS 대조 | HTS +9%인데 보드 +7% 같은 가격 지연 여부 확인 |

예상 개선 효과는 설계상 아래 정도다. 단, 실제 수치는 09:00 실측으로 확인해야 한다.

```text
렌더 부담: 50~80% 감소 가능
Top20 체감 최신성: 3배~10배 개선 가능
Top20 lag: 10초~300초 문제를 1초~5초권으로 줄이는 것이 목표
stale: 16~20 수준을 0~3 수준으로 줄이는 것이 목표
```

---

## 10. 키움 리얼타임 아키텍처와 다른 점

`docs/kiwom_realtime_architecture.md`는 ZeroMQ/Redis, FastAPI WebSocket, Canvas 렌더링 등 이상적인 구조를 제안한다. 현재 바로 적용하지 않는 이유는 다음이다.

| 항목 | 지금 바로 안 하는 이유 |
|---|---|
| ZeroMQ/Redis | 현재 병목 1순위가 IPC가 아니라 Top20 stale와 DOM 렌더링. 새 의존성은 장애 포인트 증가 |
| WebSocket/FastAPI | SSE 100ms stream으로 당장은 충분. 구조 교체보다 hot lane/렌더 개선이 우선 |
| Canvas | 표 UI, 행 클릭, 열폭 조정, HTS 연동을 다시 만들어야 함. 현재는 부분 td 업데이트가 더 안전 |
| MsgPack/binary | JSON보다 DOM 재렌더 부담이 더 큼. 먼저 렌더링부터 개선 |
| 완전 PUB/SUB | 아직 단일 UI 중심. recorder/chart worker 분리 단계에서 검토 |

현재는 전체 엔진 교체보다, 기존 구조 안에서 아래 병목을 제거하는 것이 우선이다.

```text
1. Top20 hot lane / Hidden Top50 warm lane
2. Pool 행 고정 + 부분 td 갱신
3. 잔량비 저속화
4. 1분강도 → 5분강도 교체
5. 그래도 부족하면 ZeroMQ/WebSocket/Canvas 검토
```

---

## 11. 다음 작업 순서

대표님 승인 전까지 코딩 금지.

```text
1단계: 렌더링 구조 개선 설계 확정
- Pool 내부 행 이동 중단
- Pool 부분 td 갱신
- Top20 승격/강등 완충

2단계: 데이터 최신성 개선 설계 확정
- S1 / Top20 hot lane
- Hidden Top50 warm lane
- 오래된 FID20 이벤트 폐기

3단계: 잔량비 저속 설계
- S1/Top20/Hidden Top50/Top300별 갱신 주기

4단계: 1분강도 → 5분강도 설계
- 키움 조회 주기
- 429 backoff
- stale 표시

5단계: 칼럼 경량화
- Pool tooltip 제거
- 색상바 bucket class
- 일봉 저속 갱신

6단계: 09:00 실측 검증
- Top20 lag
- stale 수
- render ms
- collector queue
- worker queue
- orderbook/trade 처리량
```

---

## 12. 복붙 없는 운영 루틴

후속 채팅에서는 아래 루틴을 유지한다.

```text
1. 대표님이 지시
2. assistant가 작업 시작 전 시작 시각·예상 시간·실패 간주 시각 제시
3. 작업 중이면 가능한 범위에서 동작 중 상태 유지
4. 외부 비동기 작업이면 무한 대기 금지, 실패 간주 시각 명시
5. 작업 완료 후 수정 파일·커밋·검증·대표님 실행 명령 보고
6. 대표님 검토 후 다음 지시
```

단, assistant는 대표님 PC의 `127.0.0.1`에 직접 접속할 수 없다. 로컬 상태 확인은 doctor 명령처럼 한 줄 실행으로 줄이는 방식이 현실적이다.

---

## 13. 현재 실행·검증 명령

대량체결 wrapper 상태 확인:

```powershell
cd C:\aiTrade
.\stockboard_v2_large.cmd doctor
```

기존 안정 런처 복귀:

```powershell
cd C:\aiTrade
.\stockboard_v2_live.cmd restart
```

대량체결 wrapper 실행:

```powershell
cd C:\aiTrade
git pull origin hot-priority-integrated-20260630
python -m py_compile realtime_v2\collector32_large.py realtime_v2\worker64_guarded_large.py
.\stockboard_v2_large.cmd restart
```

---

## 14. 후속 설계실에서 반드시 물어볼 것

```text
1. 09:00~09:05 실제 top20 lag / stale / render ms 관찰값이 있는가?
2. 현재 체감 문제는 가격 지연인가, 화면 버벅임인가, Top20 흐림인가, HTS 값 불일치인가?
3. 현재는 stockboard_v2_live.cmd 기준인가, stockboard_v2_large.cmd 기준인가?
4. 잔량비 저속화는 S1/Top20/Hidden Top50/Top300 각각 몇 초 주기로 할 것인가?
5. 1분강도 → 5분강도 교체 시 키움 조회 대상과 주기를 어떻게 제한할 것인가?
```
