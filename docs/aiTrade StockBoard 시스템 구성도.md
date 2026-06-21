# aiTrade StockBoard 시스템 구성도

### Python 4개 + HTML 1개 버전

### Version 1.0

---

# 1. 시스템 목표

aiTrade StockBoard는

* 장마감 REST 데이터
* 실시간 OpenAPI+ 데이터
* 시그널 연구실의 매매 이론

을 결합하여

**TOP100 종목**

↓

**후보 5종목**

↓

**직관적인 StockBoard 화면**

으로 보여주는 시스템이다.

---

# 2. 전체 구조

```text
                StockBoard HTML
         docs/stockboard_v0_3_0_sample.html

                         ↑

             stockboard_server.py
         API 서버 + 실행 진입점

                         ↑

             stockboard_engine.py
    Signal + Ranking + Strategy

                         ↑

             stockboard_store.py
      데이터 저장 / 캐시 / 리플레이

                         ↑

          kiwoom_data_provider.py

      REST              OpenAPI+

 장마감 조회          실시간 스트리밍
```

---

# 3. 파일 구성

총 5개 파일

```text
Python

1.
kiwoom_data_provider.py

2.
stockboard_store.py

3.
stockboard_engine.py

4.
stockboard_server.py


HTML

5.
stockboard_v0_3_0_sample.html
```

---

# 4. kiwoom_data_provider.py

역할

키움에서 데이터를 가져오는 파일

---

수집 데이터

### REST

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

### OpenAPI+

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

쉽게 말하면

```text
시장 데이터를 가져오는 사람
```

이다.

---

# 5. stockboard_store.py

역할

모든 데이터를 저장하는 창고

---

저장 대상

```text
TOP100

OHLC

시장수급

후보5

실시간 틱

호가

체결

리플레이 데이터
```

---

쉽게 말하면

```text
냉장고 + 창고
```

이다.

---

# 6. stockboard_engine.py

역할

StockBoard의 두뇌

---

내부 구조

```text
Signal Engine

↓

Ranking Engine

↓

Strategy Engine
```

---

## Signal Engine

신호 발견

예

```text
VWAP 돌파

전고 돌파

거래대금 급증

외합 증가

프로그램 유입

외선 우호

큰손 유입

KRT 증가
```

---

## Ranking Engine

점수 계산

```text
A

B

C

D

등급

시장체온

후보 점수
```

---

## Strategy Engine

최종 판단

```text
후보5 선정

모멘텀

매수 후보

관심 후보

제외
```

---

쉽게 말하면

```text
생각하는 뇌
```

이다.

---

# 7. stockboard_server.py

역할

웹 서버

API 제공

---

제공 API

```text
/api/top100

/api/market_supply

/api/candidates5

/api/us_market

/api/futures_supply
```

---

쉽게 말하면

```text
주방과 전광판을 연결하는 직원
```

이다.

---

# 8. stockboard_v0_3_0_sample.html

역할

화면 표시

---

표시 항목

### 상단

```text
상태바

미국시장

시장수급
```

---

### 중단

```text
유력 후보 5종목
```

---

### 하단

```text
거래대금 상위 TOP100

일봉

등급

거래대금

잔량비

강도

외합

프로그램

모멘텀
```

---

중요 원칙

```text
HTML은 계산하지 않는다.

Python Backend가 계산한다.

HTML은 표시만 한다.
```

---

# 9. 후보 5종목 선정 구조

```text
TOP100

↓

Signal Engine

↓

Ranking Engine

↓

Strategy Engine

↓

Candidate Top5
```

---

시그널 연구실에서 연구한

```text
VWAP

외선

외합

프로그램

큰손

KRT

거래대금 증가

모멘텀
```

은

모두

```text
Signal

↓

Ranking

↓

Strategy
```

에서 사용된다.

---

# 10. 현재 개발 단계

### 완료

```text
StockBoard HTML

TOP100 표시

후보5 표시

OHLC

VWAP

일봉

시장수급

REST 장마감 조회
```

---

### 진행중

```text
Python 1개

↓

Python 4개

구조 정리
```

---

### 다음 단계

```text
OpenAPI+ 실시간 스트리밍

↓

외선

큰손

KRT

체결강도

↓

Signal Engine

↓

Ranking Engine

↓

Strategy Engine

↓

후보5 자동 선정
```

---

# 11. 초등학생 버전 한 줄 설명

```text
키움에서 데이터를 가져온다.

↓

창고에 저장한다.

↓

뇌가 생각한다.

↓

후보5를 뽑는다.

↓

전광판에 보여준다.
```

이것이

**aiTrade StockBoard**

전체 시스템이다.
