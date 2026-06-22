# STOCKBOARD_CODING_ROADMAP_v1.0

## 목표

Python 4개 + HTML 1개 구조로

실시간 데이터 수집

↓

데이터 저장

↓

Signal

↓

Ranking

↓

Strategy

↓

후보 5종목

↓

StockBoard 표시

를 완성한다.

---

# STEP 0

## 현재 상태 백업

```text
kiwoom_trade_value_rank.py

docs/
stockboard_v0_3_0_sample.html

.env

tradable_stock_master.csv
```

현재 동작 상태 보존

기능 추가 금지

---

# STEP 1

## Python 4개 파일 분리

생성

```text
kiwoom_data_provider.py

stockboard_store.py

stockboard_engine.py

stockboard_server.py
```

HTML

```text
docs/stockboard_v0_3_0_sample.html
```

유지

---

## 역할

### kiwoom_data_provider.py

데이터 수집

```text
REST

거래대금

OHLC

VWAP

시장수급

프로그램

외합


OpenAPI+

체결

호가

잔량

외선

큰손

KRT
```

---

### stockboard_store.py

데이터 저장

```text
TOP100

시장수급

OHLC

후보5

실시간 틱

호가

체결

리플레이
```

---

### stockboard_engine.py

계산

```text
Signal

↓

Ranking

↓

Strategy
```

---

### stockboard_server.py

API

```text
/api/top100

/api/market_supply

/api/candidates5

/api/us_market

/api/futures_supply
```

---

### stockboard_v0_3_0_sample.html

표시만 담당

```text
계산 금지

API 표시만
```

---

# STEP 2

## REST 데이터 완성

완성 항목

```text
거래대금

OHLC

VWAP

전일 고가

전일 종가

전일 저가

시장수급

프로그램 순매수

외합
```

---

검증

```text
TOP100

모든 종목

OHLC 표시

거래대금 일치

전일선 일치
```

---

# STEP 3

## OpenAPI 실시간 연결

수집

```text
체결

호가

매수잔량

매도잔량

외선

큰손

KRT

체결강도
```

---

저장

```text
실시간 틱

호가

체결

장마감 저장

리플레이 저장
```

---

검증

```text
실시간 수신

저장

재생

정상
```

---

# STEP 4

## Signal Engine

구현

```text
VWAP 돌파

전고 돌파

시가 위

거래대금 급증

외합 증가

프로그램 증가

외선 우호

큰손 유입

KRT 증가
```

---

출력

```text
signal list
```

---

# STEP 5

## Ranking Engine

입력

```text
signal list
```

---

출력

```text
grade

A

B

C

D

market_temperature

candidate_score
```

---

# STEP 6

## Strategy Engine

입력

```text
candidate_score

grade

market_temperature
```

---

출력

```text
Candidate Top5

momentum

is_candidate
```

---

# STEP 7

## 후보 5종목

구조

```text
TOP100

↓

Signal

↓

Ranking

↓

Strategy

↓

Candidate Top5
```

---

적용 이론

```text
VWAP

외선

외합

프로그램

큰손

KRT

거래대금 증가

시장체온

모멘텀
```

---

# STEP 8

## 미국시장

수집

```text
NASDAQ

QQQ

SMH

IBB

LIT

BOTZ
```

---

API

```text
/api/us_market
```

---

HTML 표시

```text
미국시장 패널
```

---

# STEP 9

## 최종 완성

화면

```text
상태바

미국시장

시장수급

후보 5종목

TOP100

등급분포

시장체온
```

---

구조

```text
kiwoom_data_provider.py

↓

stockboard_store.py

↓

stockboard_engine.py

↓

stockboard_server.py

↓

stockboard_v0_3_0_sample.html
```

---

최종 원칙

```text
HTML은 계산하지 않는다.

Python이 계산한다.

Store는 저장한다.

Engine은 생각한다.

Server는 전달한다.

HTML은 보여준다.
```
