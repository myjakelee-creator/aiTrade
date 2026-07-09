# StockBoard v2 속도개선 인계서

작성일: 2026-07-09  
대상 브랜치: `hot-priority-integrated-20260630`  
목적: StockBoard v2 large 기준 속도개선 작업을 후속 채팅/후속 작업에서도 이어가기 위한 최신 인계 문서  
운영 원칙: 대표님 지시 → assistant 계획 제시 → 대표님 승인 → assistant 직접 구현/커밋/보고 → 대표님 검토 → 다음 지시. 대표님에게 Codex 지시문 복붙을 요구하지 않는다.

---

## 1. 기준 문서와 기준 파일

| 구분 | 기준 |
|---|---|
| 설계 문서 | `docs/STOCKBOARD_V2_SPEED_IMPROVEMENT_DESIGN_20260709.md` |
| 인계 문서 | `docs/STOCKBOARD_V2_SPEED_HANDOVER_20260709.md` |
| 실행 기준 | `stockboard_v2_large.cmd` |
| 장개시 기준 명령 | `stockboard_v2_large.cmd restart-fast` |
| UI | `docs/stockboard_v2.html` |
| 32bit collector | `realtime_v2/collector32_large.py` |
| 64bit worker | `realtime_v2/worker64_guarded_large.py` |
| 참고 문서 | `docs/kiwom_realtime_architecture.md` |

StockBoard v2 속도개선은 legacy StockBoard v0.3.x가 아니라 **v2 large wrapper 기준**으로 진행한다. 대량체결 wrapper는 정상 작동 확인된 신호이므로 제거하지 않는다.

---

## 2. 최신 확정 방향

### 2.1 화면 UI 구조

```text
S1 / Top20 / Top300
```

- S1: 대표님이 직접 선택한 종목. 항상 최우선 갱신.
- Top20: 실전 후보 핵심 구간. 빠른 갱신과 자동 승격/강등 허용.
- Top300: 넓은 감시망. 화면에는 Pool로 표시하되 내부 행 이동과 전체 재렌더링을 제한.

### 2.2 내부 처리 구조

```text
S1 / Top20 / Hidden Top50 / Top300
```

- Hidden Top50은 화면에는 따로 만들지 않는다.
- 21~50위 구간을 Top20 진입 예비 후보로 보고 내부 warm lane으로 관리한다.
- Top20 진입 직전 종목의 가격/체결 최신성을 미리 확보하기 위한 완충 구간이다.

### 2.3 HTML 계산 금지 원칙

```text
HTML은 계산하지 않는다.
worker가 준 값을 표시만 한다.
```

이번 속도개선 패치에서도 `docs/stockboard_v2.html` 원본을 직접 크게 수정하지 않고, `worker64_guarded_large.py`가 HTML을 서빙할 때 표시 전용 렌더링 패치를 주입한다. 패치 내부에서는 `deriveClientFields`를 표시 전용으로 고정해 브라우저 보조 계산을 막는다.

---

## 3. 대량체결 구현·검증 상태

대량체결은 기존 안정 파일을 바로 덮어쓰지 않고 wrapper 방식으로 구현했다.

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large.py` | 기존 32비트 collector를 import하고, coalescing 전에 5천만원 이상 대량체결 buy/sell count·금액 delta를 micro-batch 집계 |
| `realtime_v2/worker64_guarded_large.py` | 기존 guarded worker를 import하고, collector delta를 종목별 누적값으로 반영·daily_state persist |
| `stockboard_v2_large.cmd` | 대량체결 wrapper 실행용 런처. `restart`, `restart-fast`, `status`, `doctor` 지원 |

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

## 4. 2026-07-09 야간 속도개선 적용 내역

애프터마켓 종료 전 대표님이 수동 관찰한 결과, stream과 render가 체감상 빨라졌고 render 16ms 구간 비중이 증가했다. 실시간 장개시 검증은 다음 거래일 09:00~09:05에 수행한다.

### 4.1 적용 커밋

| 순서 | 커밋 | 내용 |
|---:|---|---|
| 1 | `deff188c2a9c71a42d9a606b6a773ea8932d6d50` | `worker64_guarded_large.py`에서 HTML 서빙 시 display-only 렌더 패치 주입. Pool row cache, tooltip/flash 억제, HTML 계산 금지 반영 |
| 2 | `a70d139bccb027ca4b2bb18bbc86e9b872d97b81` | Top300 Pool 갱신 주기를 250ms → 1000ms로 완화. DOM 순서가 같으면 `replaceChildren()` 생략 |
| 3 | `e5b331a542d9c1a044697837221533b9a73f793a` | Top300 Pool row를 한 번에 모두 갱신하지 않고 50개씩 순차 갱신. S1/Top20은 hot 갱신 유지 |
| 4 | `58d122ae2135141c7ab1c409378e88eb9df88412` | render 진단 표시 강화. 상단 render 배지에 `avg / p95 / max` 추가 |

### 4.2 현재 관찰값

대표님 관찰:

```text
stream: 약 60
render: 대략 16~110ms
16ms 쪽 비중이 이전보다 훨씬 많아짐
속도개선이 눈으로 보임
```

판정:

```text
1차 DOM 렌더링 경량화 방향은 맞음
남은 검증은 09:00~09:05 장개시 실측
```

### 4.3 현재 패치의 성격

```text
적용 대상: stockboard_v2_large.cmd 경로
원본 docs/stockboard_v2.html 대규모 변경 없음
대량체결 wrapper 유지
S1/Top20 hot 갱신 유지
Top300 Pool 저속/부분/순차 갱신
HTML 계산 금지 유지
```

---

## 5. 내일 장개시 실행 절차

장개시 전 실행:

```powershell
cd C:\aiTrade
git pull origin hot-priority-integrated-20260630
python -m py_compile realtime_v2\collector32_large.py realtime_v2\worker64_guarded_large.py
.\stockboard_v2_large.cmd restart-fast
```

브라우저:

```text
http://127.0.0.1:8765/
Ctrl+F5
```

장개시 후 이상 시 doctor:

```powershell
cd C:\aiTrade
.\stockboard_v2_large.cmd doctor
```

기존 안정 런처 복귀가 필요할 때:

```powershell
cd C:\aiTrade
.\stockboard_v2_live.cmd restart
```

---

## 6. 09:00~09:05 실측 체크리스트

상단에서 반드시 볼 값:

| 지표 | 목표/해석 |
|---|---|
| stream | 100~1000ms 중심이면 1차 정상. 현재 관찰값 약 60은 양호 |
| render latest | 순간값. 단독 판단 금지 |
| render avg | 20~40ms권이면 양호 |
| render p95 | 70ms 이하 목표. 100ms 이상이면 추가 개선 필요 |
| render max | 가끔 100ms 이상 가능. 자주 반복되면 원인 확인 |
| top20 lag | 1~5초 이내 목표 |
| stale | 0~3 수준 목표 |
| collector_q | 지속 증가하면 collector/전송 병목 |
| worker_q | 지속 증가하면 worker 처리 병목 |
| trade/s | 장초반 체결 폭주량 확인 |
| 대량체결 | 숫자 유지·증가 여부 확인 |
| HTS 대조 | SK하이닉스, 삼성전자, 삼성전기 등 현재가/등락률/거래대금 대조 |

화면에서 render 배지는 다음처럼 보인다.

```text
render 16.0 ms · avg 22 · p95 64 · max 110
```

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

현재 `stockboard_v2_large.cmd restart-fast`는 orderbook을 끄는 fast-open 모드이므로 09:00~09:05 장개시 기준으로 적절하다.

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

## 9. 다음 작업 후보

09:00~09:05 실측 전에는 더 큰 구조 변경을 하지 않는다. 실측 후 아래 중 하나를 선택한다.

| 상황 | 다음 작업 |
|---|---|
| render p95는 안정, top20 lag/stale 문제 | S1/Top20 hot lane, Hidden Top50 warm lane 강화 |
| render p95/max가 여전히 큼 | Pool batch 50 → 30 조정 또는 Pool heavy cell 추가 경량화 |
| Pool 값이 너무 늦게 따라옴 | Pool batch 50 → 80 조정 |
| collector_q 증가 | collector flush/coalescing 또는 orderbook 정책 확인 |
| worker_q 증가 | worker snapshot 생성 비용 분리 검토 |
| 가격/거래대금이 HTS와 불일치 | 렌더링이 아니라 데이터 원천/장상태 정책 재진단 |
| 잔량비 필요 | S1/Top20 우선 저속 orderbook 단계 설계 |
| 1분강도 신뢰도 문제 | 키움 조회 기반 5분강도 전환 설계 |

---

## 10. 복붙 없는 운영 루틴

후속 작업은 아래 방식으로 한다.

```text
1. 대표님이 지시한다.
2. assistant가 기준 문서/파일을 확인한다.
3. assistant가 승인 가능한 계획을 짧게 제시한다.
4. 대표님이 승인하면 assistant가 직접 구현한다.
5. assistant가 수정 파일, 커밋, 검증 결과, 대표님 확인 명령을 보고한다.
6. 대표님이 로컬 화면/HTS/doctor 결과를 확인한다.
7. 대표님이 다음 지시를 내린다.
```

대표님에게 Codex 지시문 복붙을 요구하지 않는다. 단, assistant는 대표님 PC의 `127.0.0.1`에 직접 접속할 수 없으므로 로컬 확인은 짧은 명령과 관찰값 보고로 진행한다.

---

## 11. 후속 설계실에서 반드시 물어볼 것

```text
1. 09:00~09:05 실제 stream / render avg / p95 / max 관찰값이 있는가?
2. top20 lag / stale 수치는 어떤가?
3. collector_q / worker_q가 지속 증가했는가?
4. 현재 체감 문제는 가격 지연인가, 화면 버벅임인가, Top20 흐림인가, HTS 값 불일치인가?
5. Top300 Pool 값이 너무 늦게 따라오는 느낌이 있는가?
6. 잔량비 저속화는 S1/Top20/Hidden Top50/Top300 각각 몇 초 주기로 할 것인가?
7. 1분강도 → 5분강도 교체 시 키움 조회 대상과 주기를 어떻게 제한할 것인가?
```
