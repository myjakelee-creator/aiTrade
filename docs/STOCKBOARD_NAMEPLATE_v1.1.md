# STOCKBOARD_NAMEPLATE_v1.1.md

---

# 목적

화면에 표시하는 이름과

데이터 연결에 사용하는 내부 키를 분리한다.

UI 문구가 변경되더라도

데이터 연결은 내부 키를 기준으로 유지한다.

---

# 상태 기준

| 상태   | 의미                           |
| ---- | ---------------------------- |
| DONE | 실제 데이터 연결 완료                 |
| 예정   | 이름과 구조는 확정, 실제 데이터 연결 필요     |
| 임시   | UI 확인용 fallback 또는 임시 데이터 사용 |
| 산출   | 다른 필드로 계산                    |

---

# 1. 종목표 (TOP100 / 후보5)

| 화면명   | 내부키             | 데이터 원천    | 상태   |
| ----- | --------------- | --------- | ---- |
| 순위    | rank            | 화면 산출     | 산출   |
| 전일    | rank_diff       | 화면 산출     | 산출   |
| 전일순위  | prev_rank       | 거래대금 순위   | 예정   |
| 원순위   | original_rank   | ka10032   | DONE |
| 등급    | grade           | 종목 평가 로직  | 임시   |
| 종목명   | stock_name      | 종목마스터     | DONE |
| 종목코드  | stock_code      | 종목마스터     | DONE |
| 현재가   | price           | REST 실시간  | DONE |
| 등락률   | change_rate     | REST 실시간  | DONE |
| 금액(억) | trade_value_eok | ka10032   | DONE |
| 일봉    | ohlc            | ka10086   | DONE |
| 시가    | open            | ka10086   | DONE |
| 고가    | high            | ka10086   | DONE |
| 저가    | low             | ka10086   | DONE |
| 종가    | close           | ka10086   | DONE |
| VWAP  | vwap            | Python 산출 | DONE |
| 전일고가  | prev_high       | ka10086   | DONE |
| 전일종가  | prev_close      | ka10086   | DONE |
| 전일저가  | prev_low        | ka10086   | DONE |
| 잔량비   | bid_ask_ratio   | 호가        | 예정   |
| 매수잔량  | bid_volume      | 호가        | 예정   |
| 매도잔량  | ask_volume      | 호가        | 예정   |
| 1분강도  | strength_1m     | 체결        | 예정   |
| 당일강도  | strength_day    | 체결        | 예정   |
| 외합(억) | foreign_sum     | 거래원       | 예정   |
| 프로(억) | program_net     | 프로그램      | 예정   |
| 큰손    | big_hand        | 체결 산출     | 예정   |
| 모멘텀   | momentum        | Python 산출 | 예정   |

---

# 2. 시장수급

| 화면명   | 내부키                 | 데이터 원천                | 상태   |
| ----- | ------------------- | --------------------- | ---- |
| 시장    | market_name         | Backend               | DONE |
| 지수    | market_index        | 시장지수                  | DONE |
| 등락률   | market_change_rate  | 시장지수                  | DONE |
| 상승종목수 | advancers           | 시장통계                  | DONE |
| 하락종목수 | decliners           | 시장통계                  | DONE |
| 상한가수  | upper_limit_count   | 시장통계                  | DONE |
| 하한가수  | lower_limit_count   | 시장통계                  | DONE |
| 개인(억) | individual_eok      | ka10051 ind_netprps   | DONE |
| 외인(억) | foreign_spot_eok    | ka10051 frgnr_netprps | DONE |
| 기관(억) | institution_eok     | ka10051 orgn_netprps  | DONE |
| 프로(억) | program_market_eok  | ka90005 all_netprps   | DONE |
| 외선(억) | foreign_futures_eok | COM 선물투자주체            | 예정   |
| 시장세션  | market_session      | Python 산출             | DONE |

---

# 3. 미국시장

| 화면명  | 내부키              | 데이터 원천    | 상태 |
| ---- | ---------------- | --------- | -- |
| 나스닥  | us_nasdaq        | 미국지수 API  | 예정 |
| QQQ  | us_qqq           | 미국 ETF    | 예정 |
| SMH  | us_smh           | 미국 ETF    | 예정 |
| IBB  | us_ibb           | 미국 ETF    | 예정 |
| LIT  | us_lit           | 미국 ETF    | 예정 |
| BOTZ | us_botz          | 미국 ETF    | 예정 |
| 영향   | us_market_impact | Python 산출 | 임시 |

참고

현재 화면에는

"미국장 영향"

문구는 제거되었고

값만 표시한다.

예

반도체 우호

---

# 4. 상단 상태바

| 화면명  | 내부키              | 의미              | 상태   |
| ---- | ---------------- | --------------- | ---- |
| 날짜시간 | current_datetime | KST 현재시각        | DONE |
| A    | api_status       | API 서버 상태       | DONE |
| K    | kiwoom_status    | 키움 상태           | DONE |
| W    | web_status       | 웹 상태            | DONE |
| 갱신지연 | update_delay     | 마지막 API 응답 경과시간 | DONE |
| 장상태  | market_session   | 프리/정규/애프터/장마감   | DONE |

---

# 5. 향후 추가 예정

| 화면명  | 내부키                   | 비고             |
| ---- | --------------------- | -------------- |
| 시장체온 | market_temperature    | TOP100 등급분포 기반 |
| 후보점수 | candidate_score       | 후보5 선정용        |
| 큰손매수 | big_buy               | KRT            |
| 큰손매도 | big_sell              | KRT            |
| 큰손리듬 | krt                   | KRT            |
| 외선변화 | foreign_futures_delta | 외선 증감          |
| 호가흡수 | orderbook_absorption  | 체결 + 호가        |

---

# 내부키 명명 규칙

화면명은 자유롭게 변경 가능

내부키는 변경 금지

예

외선(억)

↓

foreign_futures_eok

개인(억)

↓

individual_eok

프로(억)

↓

program_market_eok

시장세션

↓

market_session

미국시장 영향

↓

us_market_impact

---

# 최종 원칙

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

화면명은 바뀔 수 있지만

내부키는 바뀌지 않는다.
