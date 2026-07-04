# StockBoard 인계서 2026-07-04 Part 2
## 구조, 가격/시간대 처리, 문제 해결 금지 루프

## 0. 목적

월요일 08:00~09:00 장초반 거래량 폭탄 구간에서 문제가 생겨도, 이미 시행착오로 해결한 내용을 처음부터 다시 반복하지 않도록 StockBoard 구조와 판단 순서를 정리한다.

---

## 1. 주요 파일 구조

| 파일 | 역할 |
|---|---|
| `stockboard_live.cmd` | 실행/재시작/상태 확인 통합 런처 |
| `kiwoom_data_provider.py` | Kiwoom OpenAPI COM 실시간 수신, SetRealReg 등록, 체결/호가/store 반영 |
| `stockboard_server.py` | HTTP API 서버, top100/realtime/patch/status/us_market/market_supply |
| `stockboard_store.py` | RealtimeStore |
| `stockboard_engine.py` | 순위/표시 row 준비, 후보 필드 보강 |
| `docs/stockboard_v0_3_0_sample.html` | 현재 StockBoard UI. inline JS 많음 |
| `docs/assets/*.js` | 일부 분리된 UI 보조 JS |

---

## 2. 주요 API

| API | 역할 |
|---|---|
| `/api/top100` | 전체 순위 rows. 자동 반복 호출 금지 유지 |
| `/api/realtime` | 지정 코드 실시간 quote 조회 |
| `/api/realtime_patch` | 실시간 light patch |
| `/api/hot_realtime_patch` | HOT lane patch |
| `/api/realtime_provider_status` | Kiwoom provider/store/등록/이벤트 상태 |
| `/api/us_market` | 미국시장 QQQ/SOXL/SMH 등 |
| `/api/market_supply` | 코스피/코스닥 시장수급 |
| `/api/aftermarket_metrics_backfill_start` | 애프터장 metrics backfill 시작 |
| `/api/aftermarket_metrics_backfill_status` | backfill 상태 조회 |

---

## 3. 현재 UI 그룹 구조

| 화면명 | 내부명 | 설명 |
|---|---|---|
| Top5 | candidateRows / top5 | 유력 후보 5종목 |
| S1 | selectedRow | 선택 종목 1개 |
| Top15 | top20Rows | Top5 제외 후 실제 15종목 |
| Top30 | top50Rows | Top20 제외 후 실제 30종목 |
| Top300 | top300Rows / trading-board | 전체 pool |

주의:

```text
내부 변수명 top20 / top50은 아직 남아 있을 수 있다.
화면명만 Top15 / Top30으로 바꿨다.
변수명 전체 변경은 월요일 검증 후 별도 작업이다.
```

---

## 4. 현재 realtime lane 구조

| Lane | 대상 | API | 목적 |
|---|---|---|---|
| HOT | Top5 + S1 + Top15 | `/api/hot_realtime_patch` | 핵심 후보 빠른 갱신 |
| MID | Top30 | `/api/realtime_patch?codes=...` | 넓은 후보군 중간 갱신 |
| POOL | Top300 | `/api/realtime_patch` | 전체 pool 갱신 |

중요:

```text
/api/top100 전체 재조회는 장중 자동 반복 금지 상태다.
장중 실시간 갱신은 patch API 중심이다.
TOP100_REFRESH_MS를 다시 30000 등으로 복구하지 말 것.
```

---

## 5. 가격 표시 원칙

### 5.1 핵심 원칙

```text
가격 표시 결정은 서버에서 한다.
HTML은 display_price / display_change_rate / price_source를 표시만 한다.
```

금지:

```text
HTML에서 현재가나 등락률을 새로 계산하지 말 것.
```

### 5.2 시간대별 처리

| 시간대 | 처리 |
|---|---|
| 정규장 | realtime 가격 우선 |
| 15:30~15:40 | regular_close_snapshot lock 중요 |
| 15:40 이후 애프터마켓 | fresh realtime 있으면 aftermarket_realtime |
| 애프터마켓 realtime 없음 | regular_close_snapshot_fallback |
| 장마감 이후 | 속도 숫자는 계속 변할 수 있음. 데이터 변화와 분리 판단 |

---

## 6. 절대 처음부터 다시 반복하지 말 것

### 6.1 가격/FID 문제

이미 해결한 내용:

```text
StockBoard 가격 정합성은 대체로 회복됐다.
HTS 0186과 Top5 / Top15 / Top30 / Top300 가격이 대체로 맞아 들어간다.
가격 계산은 HTML에서 하지 않는다.
서버가 display_price / display_change_rate / price_source를 결정한다.
HTML은 서버 값을 표시만 한다.
```

금지:

```text
FID10/FID12 정규화부터 다시 의심하지 말 것.
KRX/NXT/통합장 문제를 처음부터 다시 반복 조사하지 말 것.
가격이 맞는 상태에서 대규모 price-source 재설계를 하지 말 것.
```

### 6.2 stale trade drop 문제

이미 겪은 시행착오:

| 증상 | 원인 |
|---|---|
| `trade_event_received_count` 증가 | Kiwoom 이벤트는 들어옴 |
| `trade_event_applied_count = 0` | 전부 drop |
| `stale_trade_drop_count`가 received와 같음 | stale guard가 막음 |
| 애프터장 체결 lag 12~13초 | 5초 제한에 걸림 |

해결 방향:

```text
STOCKBOARD_DROP_STALE_TRADE_SECONDS=5 때문에 애프터장 늦은 체결이 전량 drop 됐다.
stale drop을 0으로 풀자 가격 반영이 정상화됐다.
```

문제 발생 시 먼저 볼 것:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_provider_status"
```

중요 필드:

```text
trade_event_received_count
trade_event_applied_count
stale_trade_drop_seconds
stale_trade_drop_count
latest_only_dropped_count
last_trade_lag_sec
trade_last_fid10_raw
trade_last_fid20_raw
```

금지:

```text
애프터장/장초반 지연 데이터를 무조건 stale로 버리지 말 것.
5초 stale guard를 무작정 복구하지 말 것.
```

### 6.3 브라우저 문제로 오판하지 말 것

이미 확인한 것:

```text
브라우저 HOT patch timer 정상.
hot_realtime_patch payload 정상.
applyHotRealtimePatch DOM 적용 정상.
핵심 문제는 브라우저가 아니라 provider/store stale drop 및 등록 우선순위였다.
```

문제 발생 시 확인 순서:

| 순서 | 확인 |
|---:|---|
| 1 | API가 값을 주는가 |
| 2 | patch payload에 price_sequence가 증가하는가 |
| 3 | DOM apply가 되는가 |
| 4 | provider가 trade_event를 applied 하는가 |
| 5 | SetRealReg 등록 목록에 hot priority 종목이 들어갔는가 |

금지:

```text
바로 HTML 렌더링 문제로 단정하지 말 것.
무작정 setInterval을 늘리거나 줄이지 말 것.
```

---

## 7. Top15/Top30 보조지표 빈칸 문제

이미 해결한 내용:

```text
기존에는 close metrics lazy collection이 Top300 table만 훑었다.
그래서 Top15/Top30의 잔량비, 1분강도, 대량체결이 늦게 또는 안 채워졌다.
현재는 수집 대상을 전체 visible group으로 확대했다.
```

현재 요청 순서:

```text
Top5 → S1 → Top15 → Top30 → Top300
```

확인할 함수:

```text
collectNextCloseMetricCodes()
candidateBoard
selectedBoard
top20Board
top50Board
board
```

---

## 8. 문제 상황별 판단표

### 8.1 가격이 안 움직일 때

| 관찰 | 판단 |
|---|---|
| received 증가, applied 0 | stale/drop 문제 가능성 |
| realdata 증가, registered_count 낮음 | SetRealReg 등록 문제 |
| `/api/realtime_patch` 값 있음, DOM만 안 바뀜 | HTML apply 문제 |
| `/api/realtime_patch` 값 없음 | provider/store 문제 |

### 8.2 Top5만 빠르고 나머지가 느릴 때

| 항목 | 확인 |
|---|---|
| Top15 | HOT lane 공유 |
| Top30 | MID lane |
| Top300 | POOL lane |
| 정상 여부 | 전부 같은 속도로 움직이지 않는 것이 정상 |

### 8.3 장마감 후 속도 숫자가 바뀔 때

```text
속도 배지는 데이터 속도가 아니라 API 응답시간이다.
장이 끝나도 브라우저 timer가 돌면 실제 0.xx초 숫자는 변할 수 있다.
가격/등락률 변화와 응답속도 변화는 분리해서 판단한다.
```

---

## 9. 월요일 08:00~09:00 검증 체크포인트

### 9.1 08:00 프리마켓

| 확인 | 명령/화면 |
|---|---|
| 서버 상태 | `stockboard_live.cmd status` |
| 로그인/등록 | `/api/realtime_provider_status` |
| 수신 이벤트 | `realdata_received_count` |
| 체결 적용 | `trade_event_applied_count` |
| stale drop | `stale_trade_drop_count` |
| UI | Top5/S1/Top15/Top30/Top300 표시 |

명령:

```powershell
cd C:\aiTrade

.\stockboard_live.cmd status

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_provider_status"
```

### 9.2 09:00 장개시

| 확인 | 기준 |
|---|---|
| HOT patch | 멈추지 않아야 함 |
| price_sequence | 증가해야 함 |
| trade_event_applied_count | 빠르게 증가해야 함 |
| Top5 가격 | HTS와 대조 |
| Top15/Top30 가격 | 늦어도 계속 따라와야 함 |
| UI | 멈춤/브라우저 렉 없어야 함 |

샘플 API:

```powershell
cd C:\aiTrade

$codes = "005930,000660,009150,402340,005380,011070"

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime?codes=$codes" |
  Select-Object -ExpandProperty quotes
```
