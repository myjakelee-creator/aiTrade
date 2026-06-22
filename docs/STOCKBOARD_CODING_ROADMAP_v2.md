# STOCKBOARD_CODING_ROADMAP_v2.0

작성 기준: 2026-06-22

---

# 최종 목표

```text
REST 데이터

+

OpenAPI 실시간 데이터

↓

RealtimeStore

↓

Signal Engine

↓

Ranking Engine

↓

Strategy Engine

↓

Candidate Top5

↓

StockBoard
```

---

# 현재 완료 (DONE)

## DONE 1 ~ 12

```text
HTML 보드 기본 화면

TOP100 표시

후보5 표시 구조

셀폭 조절 / localStorage

일봉 full / proportional 모드

ka10032 거래대금 3페이지 조회

tradable_stock_master.csv 필터

ka10086 OHLC / VWAP 연결

휴일 최근 거래일 대체

.env 인증키 로딩

시장수급 연결

/api/top100
/api/market_supply
```

---

## DONE 13

Python 4개 파일 분리

```text
kiwoom_data_provider.py

stockboard_store.py

stockboard_engine.py

stockboard_server.py
```

---

## DONE 14

OHLC 반복 렌더링 문제 해결

최종 상태

```text
TOP100 row count = 193

OHLC = 193

apiRows = normalizedRows = renderRows = domRows
```

---

## DONE 15

program_net 안정 연결

```text
program_net = 193

HTTP 429 대응

KOSPI/KOSDAQ 분리

partial result 유지
```

---

## DONE 16

foreign_sum 연결

```text
외국계 창구 합계

foreign_sum = 12
```

---

## DONE 17

foreign_investor_net 연결

```text
ka10066

foreign_investor_net = 191
```

---

## DONE 18

외합(억) ↔ 외인(억) 자동 전환

```text
장중

외합(억)

↓

foreign_sum

장마감

외인(억)

↓

foreign_investor_net
```

---

## DONE 19

RealtimeStore 구축

```text
quotes

trade_events

orderbook_events

foreign_line

snapshot

stale 관리
```

---

## DONE 20

실시간 API 구축

```text
/api/realtime

/api/realtime_status
```

---

## DONE 21

Provider 상태 API 구축

```text
/api/realtime_provider_status
```

---

## DONE 22

QAxWidget 생성 구조

```text
QApplication

QAxWidget

status 관리
```

---

## DONE 23

CommConnect 로그인 요청 구조

```text
CommConnect

login_state

login_error_code

login_completed_at
```

---

## DONE 24

Qt Event Pump 구조

```text
app.processEvents()

qt_pump_thread
```

---

# 현재 위치

```text
STEP10 진행 중
```

현재 상태

```text
QAxWidget 생성 완료

CommConnect 요청 완료

Qt Pump 완료

OnEventConnect connected
최종 검증 중
```

---

# 다음 작업 (TODO)

## TODO 25

OnEventConnect connected 최종 확인

성공 기준

```text
login_state = connected
```

---

## TODO 26

SetRealReg 등록

```text
TOP193

100 + 93

화면번호 분리
```

---

## TODO 27

OnReceiveRealData 수신 확인

---

## TODO 28

GetCommRealData 파싱

---

## TODO 29

Tick 저장

---

## TODO 30

호가 저장

---

## TODO 31

RealtimeStore update 연결

---

## TODO 32

외선 연결

---

## TODO 33

잔량비 계산

---

## TODO 34

잔량비 색상바

---

## TODO 35

1분강도 계산

---

## TODO 36

1분강도 색상바

---

## TODO 37

당일강도 계산

---

## TODO 38

당일강도 색상바

---

## TODO 39

큰손/KRT 계산

---

## TODO 40

후보5 실제 선정

---

## TODO 41

Signal Engine 구현

---

## TODO 42

Ranking Engine 구현

---

## TODO 43

Strategy Engine 구현

---

## TODO 44

미국시장 실제 데이터 연결

---

## TODO 45

Replay 기능

---

# 현재 핵심 수치

```text
TOP100 rows = 193

OHLC = 193

program_net = 193

foreign_sum = 12

foreign_investor_net = 191

Realtime quote count = 0

Realtime trade count = 0

Realtime orderbook count = 0
```

---

# 절대 원칙

```text
HTML은 계산하지 않는다.

Python이 계산한다.

Store는 저장한다.

Engine은 판단한다.

Server는 전달한다.

HTML은 표시한다.
```
