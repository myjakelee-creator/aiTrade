# aiTrade StockBoard 총괄 운영실 인계 문서

작성일: 2026-06-21

---

# 1. 프로젝트 목적

aiTrade StockBoard는

**수많은 종목을 사람의 눈으로 일일이 찾지 않고**

실시간으로

* 거래대금
* 등락률
* 캔들
* 시장수급
* 큰손
* 모멘텀

을 한 화면에서 보여주고,

궁극적으로

**"지금 가장 강한 종목은 무엇인가?"**

를 사람이 즉시 판단할 수 있도록 하는 것을 목표로 한다.

현재는

**실시간 시각화 + 수동매매 지원**

이 우선이며,

자동매매는 그 다음 단계이다.

---

# 2. 주요 파일

수정 가능

```text
C:\aiTrade\kiwoom_trade_value_rank.py

C:\aiTrade\docs\stockboard_v0_3_0_sample.html
```

수정 금지

```text
.env

CSV

이미지

기타 파일
```

---

# 3. 서버

실행

```bash
cd /d C:\aiTrade

python .\kiwoom_trade_value_rank.py
```

브라우저

```text
http://127.0.0.1:8000/stockboard_v0_3_0_sample.html
```

---

# 4. 서버 중복 실행 문제

과거 문제:

```text
LISTENING 여러 개

↓

브라우저가 예전 서버 연결

↓

OHLC 20개만 보임
```

현재 해결 완료.

확인:

```bash
netstat -ano | find "8000"
```

정상:

```text
LISTENING 1개

TIME_WAIT 여러 개

OK
```

---

# 5. 거래대금 상위 종목

현재 구조

```text
ka10032

100개 × 3페이지

↓

중복 제거

↓

272개

↓

tradable_stock_master.csv 필터

↓

189개

↓

TOP100 표시
```

주의

화면 제목은

```text
거래대금 상위 100
```

으로 유지.

실제 표시 종목은

100개 이상일 수 있음.

향후 최종 100개 엄선 가능.

---

# 6. OHLC

현재 상태

```text
ka10086

TOP100 전체 종목 조회

↓

OHLC 189개

↓

joined count 189

↓

브라우저 189개 표시
```

현재 정상.

---

# 7. Full Candle

목적:

개별 종목 분석

표시:

```text
당일 OHLC

VWAP

전일 고가

전일 종가

전일 저가
```

툴팁

```text
거래일

당일 시가

당일 고가

당일 저가

당일 종가

VWAP

전일 고가

전일 종가

전일 저가
```

현재 정상.

---

# 8. Proportional Candle

목적:

종목 간 상대 비교

핵심:

**시가 기준 아님**

**전일 종가(prev_close) 기준**

---

기준

```text
prev_close

=

셀 중앙 50%
```

---

공용 스케일

TOP100 전체 rows

```text
open

high

low

close

vwap
```

각 가격을

```text
(price - prev_close)

/ prev_close
```

로 계산.

전체 절대값 중

최댓값

↓

```text
maxAbsMove
```

---

위치 계산

```text
position

=

50

+

(

((price - prev_close)

 / prev_close)

 /

maxAbsMove

)

×

50
```

---

몸통

```text
open

↓

close
```

---

꼬리

```text
low

↓

high
```

---

보조선

표시

```text
VWAP

전일종가 중심선
```

숨김

```text
전일 고가

전일 종가 개별선

전일 저가
```

이유

좁은 셀에서

기준선이 많으면

등락률 비교가 어려움.

---

# 9. C 토글

현재

```text
C

↓

Full

↓

Proportional

↓

Full
```

정상.

---

# 10. 후보 5

현재

```text
TOP100 rows

↓

상위 후보 5
```

동일 row 공유.

---

향후 개선

아래 조건으로

점수화 예정.

```text
등급

+

거래대금

+

등락률

+

캔들

+

VWAP

+

전고돌파

+

시장수급

+

큰손(KRT)

+

모멘텀
```

---

# 11. 시장수급 패널

다음 최우선 과제.

상단 고정.

필드

```text
시장

지수

등락률

상승

하락

외선

외현

기관

프로그램

외달

분위기

시장체온
```

---

추천 API 구조

```text
/api/top100

종목 전용


/api/market-summary

시장수급 전용
```

분리 유지.

---

# 12. 개발 원칙

반드시 지킬 것

```text
수정 전에는

항상

변경 예정 먼저 보고


승인 후 수정


추가 파일 생성 금지
```

---

# 13. 현재 상태 요약

완료

```text
거래대금 3페이지 조회

CSV 필터

OHLC 189개

VWAP

Full Candle

Proportional Candle

툴팁

후보5

서버 중복 방지

LISTENING 1개 확인
```

다음 단계

```text
시장수급 패널

↓

후보5 선정 로직 개선

↓

큰손(KRT)

↓

모멘텀
```

---

# 최종 목표

한 화면에서

```text
지금 시장이 강한가?

↓

어느 종목군이 강한가?

↓

어느 종목이 가장 강한가?

↓

지금 진입 가능한가?
```

를

사람이

1~2초 안에

판단할 수 있는

StockBoard를 완성한다.
