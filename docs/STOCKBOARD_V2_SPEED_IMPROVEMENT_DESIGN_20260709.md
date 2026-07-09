# StockBoard v2 속도개선 설계도

작성일: 2026-07-09  
대상 브랜치: `hot-priority-integrated-20260630`  
상태: 1차 렌더링 속도개선 구현 완료. 09:00~09:05 실측 대기. 후속 변경은 대표님 승인 후 진행.

---

## 1. 기본 전제

대표님 목표는 Pool을 줄이는 것이 아니라 **300종목 감시망은 유지하면서, 실전에서 첫 출발선 종목을 놓치지 않는 것**이다.

따라서 속도개선 방향은 아래와 같이 잡는다.

```text
감시망: Top300 유지
화면 UI: S1 / Top20 / Top300
내부 처리: S1 / Top20 / Hidden Top50 / Top300
렌더링: Top20은 빠르게, Top300 Pool은 고정·부분·저속 갱신
실행 기준: stockboard_v2_large.cmd restart-fast
```

`docs/kiwom_realtime_architecture.md`의 핵심도 “09:00~09:05 폭주 구간에서는 수신·연산·전송·렌더링을 분리해야 한다”는 것이다. 현재 StockBoard v2는 이미 32비트 collector와 64비트 worker 분리라는 큰 방향은 맞다. 남은 병목은 주로 **Top20 최신성, Top300 Pool 렌더링, 잔량비/orderbook 부담, 1분강도 정합성**이다.

---

## 2. UI와 내부 처리 구조

| 구분 | 역할 | 화면 표시 | 처리 우선순위 |
|---|---|---:|---:|
| **S1** | 대표님이 직접 선택한 종목 | 표시 | 최우선 |
| **Top20** | 실전 후보 핵심 구간 | 표시 | 고속 처리 |
| **Hidden Top50** | Top20 진입 예비 후보 | 화면에는 따로 표시 안 함 | 중고속 처리 |
| **Top300** | 넓은 감시망 | Pool로 표시 | 저속/부분 처리 |

핵심은 **Top50을 화면에 만들지 않고 내부 완충 구간으로만 쓰는 것**이다.

```text
UI는 단순하게:
S1 / Top20 / Top300

내부는 실전적으로:
S1 / Top20 / Hidden Top50 / Top300
```

---

## 3. 속도 병목과 개선 방향 표

| 항목 | Pool | 기존/현재 문제 | 개선 방향 |
|---|---|---|---|
| **1. Top20 lag / stale** | S1 / Top20 / Hidden Top50 / Top300 | 모든 trade 이벤트가 거의 같은 처리 흐름을 타면 Top20도 폭주 시 늦어질 수 있음 | S1·Top20 hot lane, Hidden Top50 warm lane 도입. 오래된 FID20/체결 이벤트는 강하게 폐기 |
| **2. Top300 Pool 전체 재렌더링** | Top300 | Pool 전체를 짧은 주기로 다시 그리고 14개 칼럼 전체 HTML 재생성 | 적용 완료: Pool row cache, row 재사용, 1000ms 저속 갱신, 50개 순차 갱신 |
| **3. 등급점수 기반 행 이동** | Top20 / Top300 | 점수 변화에 따라 전체 정렬·행 이동 가능 | Top20은 자동 승격/강등 허용. Pool 내부는 자동 이동 금지. 필요 시 수동 정렬 |
| **4. Top20 경계 흔들림** | Top20 / Hidden Top50 | 20위 근처 종목이 자주 올라갔다 내려갈 수 있음 | Hidden Top50 완충. 2~3회 연속 진입 또는 점수 차이 조건 충족 시 승격. 이탈은 25~30위 밖 일정 시간 후 강등 |
| **5. 잔량비/orderbook 부담** | S1 / Top20 / Hidden Top50 / Top300 | normal 모드에서 orderbook을 넓게 받으면 잔량비 계산 부담 증가 | 잔량비 저속 확정. 장개시 `restart-fast`는 orderbook OFF. S1 빠르게, Top20 준실시간, Hidden/Top300 저속 |
| **6. 1분강도 정합성** | S1 / Top20 / Hidden Top50 / Top300 | 브라우저 보조 계산이 남아 있고 순간강도와 편차 큼 | 1분강도 칼럼을 키움 조회 기반 5분강도로 교체. 브라우저 강도 계산 제거 |
| **7. 일봉 mini-candle** | S1 / Top20 / Top300 | 행마다 OHLC 계산, mini-candle HTML, style 변수 생성 | 삭제하지 않음. S1/Top20은 유지. Top300 Pool은 저속 갱신 또는 OHLC 변경 시만 갱신 |
| **8. 잔량비/강도 색상바** | S1 / Top20 / Top300 | 행마다 gradient style 생성 가능 | 색상바 유지하되 bucket class 방식으로 경량화. 값 구간이 같으면 class 갱신 안 함 |
| **9. 툴팁** | S1 / Top20 / Top300 | row title, metric title 등을 행마다 생성 | 적용 일부 완료: Pool tooltip 제거. S1/Top20은 유지 |
| **10. 300행 stream/snapshot** | 전체 | `limit=300&interval_ms=100` stream 사용 | 감시는 300 유지. 화면 갱신은 S1/Top20 고속, Top300 부분/저속 갱신 |
| **11. 대량체결** | S1 / Top20 / Top300 | 숫자 칼럼 하나. collector aggregate 정상 작동 | 유지. 삭제 효과 작고 신호 손실 큼 |
| **12. 현재가/등락률/금액/프로** | 전체 | 숫자 중심이라 개별 부담 작음 | 유지. 부분 td 갱신으로 처리. Pool flash는 제한 |
| **13. 자동 열폭 계산** | 전체 | 수동 최소화/초기 auto-fit 때 셀 폭 측정 | 장중 자동 반복 금지. 수동 버튼 또는 초기 1회만 |
| **14. 상단 시장수급/context** | 상단 | 15초 주기. Kiwoom 429 가능성 있음 | UI 병목은 아님. 저속/backoff 처리. Top20 hot lane과 분리 |

---

## 4. 핵심 설계 원칙

### 4.1 Pool은 줄이지 않는다

```text
Top300 수집/계산 유지
Top300 화면 표시 유지
다만 Pool 내부 자동 이동과 전체 재렌더링은 중단 또는 강하게 제한
```

### 4.2 행 이동과 값 갱신을 분리한다

```text
값 갱신:
- 가격, 등락률, 금액, 대금비, 프로, 대량체결은 계속 갱신

행 이동:
- Top20 승격/강등만 제한적으로 허용
- Top300 Pool 내부 자동 행 이동 금지
```

### 4.3 Top20은 흔들리지 않게 완충한다

```text
Top20 진입:
- 2~3회 연속 Top20권
- 또는 점수 차이가 충분히 클 때

Top20 이탈:
- 21위로 밀렸다고 바로 강등하지 않음
- 25~30위 밖으로 일정 시간 밀릴 때 강등

신규 Top20:
- 최소 5~10초 유지
```

### 4.4 Hidden Top50은 화면이 아니라 내부 준비 구간이다

```text
21~50위 종목:
- 화면에는 Top300 Pool 안에 그대로 있음
- 내부적으로는 Top20 후보로 보고 가격/체결 최신성 우선 관리
- 5분강도/잔량비 저속 조회 우선순위 부여
```

### 4.5 HTML은 계산하지 않는다

```text
HTML은 계산 엔진이 아니다.
worker가 계산한 row 값을 표시만 한다.
```

이번 large wrapper 패치에서도 `deriveClientFields`를 표시 전용으로 고정했고, 기존 브라우저 보조 계산 경로를 막았다. 후속 작업에서도 브라우저에 매매 판단 계산을 추가하지 않는다.

---

## 5. 칼럼별 처리 정책

| 칼럼 | 유지/변경 | S1 / Top20 | Hidden Top50 | Top300 Pool |
|---|---|---|---|---|
| 순위 | 유지 | 빠른 갱신 | 보통 | 값만 갱신 |
| 전일 | 유지 | 보통 | 보통 | 보통 |
| 등급 | 유지 | 빠른 갱신, 승격/강등 반영 | 빠른 계산 | 값만 갱신, 행 이동 없음 |
| 종목명 | 유지 | 고정 | 고정 | 고정 |
| 현재가 | 유지 | 빠른 갱신 | 빠른 갱신 | 부분/순차 갱신 |
| 등락률 | 유지 | 빠른 갱신 | 빠른 갱신 | 부분/순차 갱신 |
| 금액(억) | 유지 | 빠른 갱신 | 빠른 갱신 | 부분/순차 갱신 |
| 대금비 | 유지 | 빠른 갱신 | 빠른 갱신 | 부분/순차 갱신 |
| 일봉 | 유지 | 빠른/보통 갱신 | 저속 | 저속 |
| 잔량비 | 유지하되 저속 | 준실시간 | 저속 | 저속/마지막값 |
| 순간강도 | 유지 | 빠른 갱신 | 보통 | 부분/순차 갱신 |
| 1분강도 | **5분강도로 교체 예정** | 키움 5분강도 우선 | 저속 조회 | 저속/마지막값 |
| 프로(억) | 유지 | 빠른 갱신 | 보통 | 부분/순차 갱신 |
| 대량체결 | 유지 | 빠른 갱신 | 빠른 갱신 | 부분/순차 갱신 |

---

## 6. 2026-07-09 구현된 1차 렌더링 개선

### 6.1 적용 커밋

| 순서 | 커밋 | 내용 |
|---:|---|---|
| 1 | `deff188c2a9c71a42d9a606b6a773ea8932d6d50` | `worker64_guarded_large.py`에서 HTML 서빙 시 display-only 렌더 패치 주입. Pool row cache, tooltip/flash 억제, HTML 계산 금지 반영 |
| 2 | `a70d139bccb027ca4b2bb18bbc86e9b872d97b81` | Top300 Pool 갱신 주기를 250ms → 1000ms로 완화. DOM 순서가 같으면 `replaceChildren()` 생략 |
| 3 | `e5b331a542d9c1a044697837221533b9a73f793a` | Top300 Pool row를 한 번에 모두 갱신하지 않고 50개씩 순차 갱신. S1/Top20은 hot 갱신 유지 |
| 4 | `58d122ae2135141c7ab1c409378e88eb9df88412` | render 진단 표시 강화. 상단 render 배지에 `avg / p95 / max` 추가 |

### 6.2 적용 방식

```text
원본 docs/stockboard_v2.html 대규모 수정 없음
large wrapper가 HTML을 서빙할 때 속도 패치를 주입
대량체결 wrapper 유지
S1/Top20 빠른 갱신 유지
Top300 Pool 저속/부분/순차 갱신
Pool tooltip/flash 억제
HTML 계산 금지 유지
```

### 6.3 대표님 관찰 결과

```text
stream: 약 60
render: 대략 16~110ms
16ms 쪽 비중이 이전보다 훨씬 많아짐
속도개선이 눈으로 보임
```

판정:

```text
DOM 렌더링 개선 방향은 맞다.
실제 최종 판정은 09:00~09:05 장개시 실측 후 한다.
```

---

## 7. 장개시 09:00 실측 목표

정확한 배율은 09:00~09:05 실측이 필요하지만, 목표치는 다음이다.

```text
render avg:
- 20~40ms권 목표

render p95:
- 70ms 이하 목표

render max:
- 가끔 100ms 이상 가능
- 반복적으로 크면 추가 개선 필요

Top20 lag:
- 1~5초권 목표

stale:
- 0~3 수준 목표
```

가장 큰 개선 지점은 두 가지다.

```text
1. Top300 Pool 전체 재렌더링 중단/제한
2. S1/Top20/Hidden Top50 hot/warm lane 도입
```

현재는 1번의 1차 구현이 완료된 상태이며, 2번은 09:00 실측 후 진행 여부를 판단한다.

---

## 8. 키움 리얼타임 아키텍처 문서와 다른 점

`docs/kiwom_realtime_architecture.md`에는 ZeroMQ/Redis, FastAPI WebSocket, Canvas 렌더링 같은 이상적인 구조가 들어 있다. 다만 지금 당장 전부 적용하지 않는 이유는 다음과 같다.

| 항목 | 지금 바로 안 하는 이유 |
|---|---|
| ZeroMQ/Redis | 현재 병목 1순위가 IPC가 아니라 Top20 stale와 DOM 렌더링. 새 의존성은 장애 포인트 증가 |
| WebSocket/FastAPI | SSE 100ms stream으로 당장은 충분. 구조 교체보다 hot lane/렌더 개선이 우선 |
| Canvas | 표 UI, 행 클릭, 열폭 조정, HTS 연동을 다시 만들어야 함. 현재는 부분 td 업데이트가 더 안전 |
| MsgPack/binary | JSON보다 DOM 재렌더 부담이 더 큼. 먼저 렌더링부터 개선 |
| 완전 PUB/SUB | 아직 단일 UI 중심. recorder/chart worker 분리 단계에서 검토 |

---

## 9. 후속 구현 순서

후속 변경은 대표님 승인 후 진행한다.

```text
완료: 1단계 렌더링 구조 개선 1차
- Pool row cache
- Pool tooltip/flash 억제
- Pool 1000ms 저속 갱신
- Pool 50개 batch 순차 갱신
- render avg/p95/max 표시

대기: 09:00 실측 검증
- stream
- render avg / p95 / max
- Top20 lag
- stale 수
- collector queue
- worker queue
- orderbook/trade 처리량

후보 2단계: 데이터 최신성 개선
- S1 / Top20 hot lane
- Hidden Top50 warm lane
- 오래된 FID20 이벤트 폐기

후보 3단계: 잔량비 저속 설계
- S1/Top20/Hidden Top50/Top300별 갱신 주기

후보 4단계: 1분강도 → 5분강도 설계
- 키움 조회 주기
- 429 backoff
- stale 표시
```

---

## 10. 새 채팅 첫 지침

새 채팅창으로 이동할 때는 아래 한 줄만 입력한다.

```text
StockBoard v2 속도개선 이어가자. 기준 문서는 docs/STOCKBOARD_V2_SPEED_HANDOVER_20260709.md 와 docs/STOCKBOARD_V2_SPEED_IMPROVEMENT_DESIGN_20260709.md 이다. 복붙 지시문 방식이 아니라, 내가 지시하면 너가 계획을 세우고 승인 후 직접 구현·커밋·보고하는 방식으로 진행한다.
```

새 채팅에서 먼저 할 일:

```text
1. 기준 문서 2개 확인
2. 최신 커밋 상태 확인
3. 09:00~09:05 실측값 유무 확인
4. 필요한 경우 승인 가능한 계획 제시
5. 대표님 승인 후 직접 구현·커밋·보고
```
