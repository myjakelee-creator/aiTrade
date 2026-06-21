# STOCKBOARD_HANDOVER_v1.0.md

---

# 프로젝트

**aiTrade StockBoard**

목표:

수많은 종목을 사람이 일일이 찾지 않고,

시장 → 섹터 → 종목 → 진입가능성

을 한 화면에서 빠르게 판단하는 실전용 웹 StockBoard 구축.

자동매매보다

**실전 수동매매 + 시각화**

를 우선한다.

---

# 수정 가능 파일

수정 가능

* kiwoom_trade_value_rank.py
* docs/stockboard_v0_3_0_sample.html

수정 금지

* .env
* CSV
* 이미지
* 기타 모든 파일

---

# 개발 원칙

주의:

* 수정 전에는 항상 변경 예정 먼저 보고
* 승인 후 수정
* 추가 파일 생성 금지

추가 원칙:

* HTML은 표시만 담당
* 계산은 Python Backend 담당
* REST와 COM은 역할 분리
* 기능 안정화 전 파일 분리 금지

---

# 전체 구조

Kiwoom REST

↓

Python Backend

* 조회
* 정규화
* 캐시
* API 응답

↓

StockBoard HTML

* 표시
* 색상
* 정렬
* 셀폭조절
* localStorage

별도

Kiwoom OpenAPI+ COM

↓

실시간 특수 데이터

* 외선
* 체결
* 호가
* 큰손/KRT

↓

Python Backend 통합

---

# REST 담당

현재 사용 중.

담당 데이터

* 거래대금
* OHLC
* KOSPI/KOSDAQ 지수
* 상승 종목수
* 하락 종목수
* 상한가
* 하한가
* 개인 순매수
* 외국인 순매수
* 기관 순매수
* 프로그램 순매수

---

# COM 담당

현재 조사 예정.

담당 예정

* 외국인 코스피 지수선물 순매수 (외선)
* 실시간 체결
* 실시간 호가
* 큰손/KRT
* 기타 REST 미지원 데이터

---

# API 구조

## /api/top100

역할

거래대금 상위 종목

포함

* 거래대금
* OHLC
* VWAP
* full candle
* proportional candle

현재

DONE

---

## /api/market_supply

역할

시장수급

포함

market_session

KOSPI

* market_index

* market_change_rate

* advancers

* decliners

* upper_limit_count

* lower_limit_count

* individual_eok

* foreign_spot_eok

* institution_eok

* program_market_eok

* foreign_futures_eok

KOSDAQ

동일

현재

DONE

외선은 null

---

## 후보

/api/futures_supply

외선 전용

---

# 화면 구조

상단

1행

* 날짜 시간
* A
* K
* W
* 갱신 지연
* 장 상태

2행

미국시장

* 나스닥
* QQQ
* SMH
* IBB
* LIT
* BOTZ
* 반도체 우호

3행

시장수급

* 시장
* 지수
* 등락률
* 상승/하락
* 개인
* 외인
* 기관
* 프로
* 외선

우측

TOP100 등급분포

---

하단

유력 후보 5종목

↓

TOP100

↓

189개 종목 표시

---

# 현재 완료

DONE

* 서버 중복 실행 방지

* LISTENING 1개 확인

* 거래대금 3페이지

* CSV 필터

* 189개 표시

* ka10086 OHLC 189개 결합

* Full Candle

* Proportional Candle

* VWAP

* 후보5

* 시장수급

* 개인

* 외인

* 기관

* 프로그램

* 상단 상태바

* 상단 셀폭 조절

* 미국시장 셀폭 조절

* 시장수급 셀폭 조절

* TOP100 등급분포

---

# 중요한 결정사항

## 외달

제거

이유

외국인 현물 매수를 달러 환산하면

외인과 의미가 중복된다.

---

## 외선

유지

의미

외국인 코스피 지수선물 순매수

시장수급의 핵심 요소.

현재 null.

향후 반드시 COM으로 연결.

---

## 상태등

기존

I / R / B

↓

현재

A / K / W

A

API 상태

K

Kiwoom 상태

W

Web 상태

색상

초록

정상

노랑

지연/대기

빨강

오류

---

## 수신속도

제거

↓

갱신 지연

예

갱신 0.8초

---

## 장 상태

표시

* 프리마켓
* 정규장
* 애프터마켓
* 장마감

---

# localStorage

열폭 저장 사용

대상

* 상단 상태바
* 미국시장
* 시장수급
* TOP100

저장 폭이 있으면

사용자 폭 우선

저장 폭이 없으면

내용 기준 자동맞춤

---

# 다음 우선순위

1

외선(COM)

외국인 코스피 지수선물 순매수

최우선

---

2

후보5 선정 로직

등급

거래대금

등락률

VWAP

수급

모멘텀

종합 점수

---

3

큰손/KRT

체결

호가

큰손 매수

큰손 매도

---

4

모멘텀

VWAP 돌파

시가 위

전고 돌파

거래대금 급증

---

5

미국시장 실제 데이터 연결

---

6

시장체온

TOP100 등급분포 기반

시장 강도 계산

---

# Codex 지시문 규칙

항상 아래 형식을 사용

작업 목표:

...

수정 파일:

...

수정 금지:

...

현재 확인된 사실:

...

수정 방향:

...

의심 코드:

...

금지사항:

...

검증:

...

주의:

수정 전 변경 예정 먼저 보고

승인 후 수정

추가 파일 생성 금지

---

# 결론

REST

↓

기본 데이터

COM

↓

외선 + 특수 실시간 데이터

Python

↓

모든 계산

HTML

↓

표시 전용

StockBoard는

자동매매보다

실전 수동매매를 위한

고속 시장판단 시스템으로 발전시킨다.
