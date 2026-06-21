# STOCKBOARD_DASHBOARD_DISPLAY_CONTRACT_v1.0

작성일 : 2026-06-19
버전 : v1.0

---

# 1. 목적

본 문서는 StockBoard Frontend Dashboard 와 Backend Engine 간의 데이터 표시 규약(Display Contract)을 정의한다.

StockBoard는 데이터를 계산하지 않는다.

모든 데이터는

Kiwoom / 미국 데이터
→ Normalizer
→ Market State Store
→ Signal Engine
→ Ranking Engine
→ Strategy Engine
→ Backend API
→ WebSocket
→ Frontend Dashboard

순으로 전달되며,

Frontend Dashboard는 본 문서에 정의된 내부 Key 만 수신하여 화면에 표시한다.

---

# 2. 기본 규칙

## 2.1 UI는 계산하지 않는다.

금지

* 거래대금 계산
* VWAP 계산
* 등급 계산
* 시그널 계산
* 큰손 계산
* 모멘텀 생성

허용

* 표시
* 정렬
* 색상 변경
* 깜빡임 효과
* 스크롤

---

## 2.2 화면 표시명과 내부 Key는 분리한다.

예)

화면

큰손

↓

내부 Key

big_hand_index

---

화면

프(억)

↓

내부 Key

program_eok

---

Signal, Ranking, Strategy 가 변경되어도

UI 내부 Key는 변경하지 않는다.

---

# 3. 상단 상태 패널

| 화면 표시명 | 내부 Key              | 타입     | 예시                  |
| ------ | ------------------- | ------ | ------------------- |
| 날짜시간   | date_time           | string | 2025/08/17 14:05:17 |
| 인      | interface_status    | enum   | GREEN               |
| 레      | recorder_status     | enum   | GREEN               |
| 판      | dashboard_status    | enum   | GREEN               |
| 수신속도   | receive_latency_sec | float  | 0.21                |
| 장구분    | market_session      | enum   | 정규장                 |
| 대표종목   | focus_stock_name    | string | 삼성전기                |
| 모멘텀    | focus_momentum      | string | VWAP돌파 + 시가위        |

---

# 4. 시장수급 패널

| 화면 표시명 | 내부 Key              |     단위 | 예시      |
| ------ | ------------------- | -----: | ------- |
| 시장     | market_name         |      - | KOSPI   |
| 지수     | market_index        |     pt | 3250    |
| 등락률    | market_change_rate  |      % | +1.23   |
| 상승/하락  | advance_decline     |      개 | 720/180 |
| 외선(억)  | foreign_futures_eok |      억 | +5200   |
| 외인(억)  | foreign_spot_eok    |      억 | +2300   |
| 기관(억)  | institution_eok     |      억 | -800    |
| 프로(억)  | program_market_eok  |      억 | +1250   |
| 외달(억)  | foreign_dollar_eok  |      억 | +8600   |
| 분위기    | market_mood         | string | 92 폭등장  |

---

# 5. 미국시장 패널

| 화면 표시명 | 내부 Key            |     단위 | 예시     |
| ------ | ----------------- | -----: | ------ |
| 나스닥    | nasdaq_change     |      % | +1.85  |
| QQQ    | qqq_change        |      % | +1.72  |
| SMH    | smh_change        |      % | +2.25  |
| IBB    | ibb_change        |      % | +0.55  |
| LIT    | lit_change        |      % | -1.25  |
| BOTZ   | botz_change       |      % | +0.82  |
| 한국장 영향 | us_market_comment | string | 반도체 우호 |

---

# 6. 유력 후보 전광판

| 화면 표시명 | 내부 Key            |     단위 | 예시                  |
| ------ | ----------------- | -----: | ------------------- |
| 순위     | rank              |      위 | 1                   |
| 전일     | prev_rank         |      위 | 82                  |
| 등급     | grade_score       |      - | A92                 |
| 종목명    | stock_name        |      - | 삼성전기                |
| 현재가    | price             |      원 | 168300              |
| 등락율    | change_rate       |      % | +8.25               |
| 금액(억)  | trade_value_eok   |      억 | 2350                |
| 일봉     | mini_daily_candle | object | {...}               |
| 잔량비    | orderbook_ratio   |      배 | 0.72                |
| 1분강도   | strength_1m       |      - | 185                 |
| 당일강도   | strength_day      |      - | 148                 |
| 외합(억)  | foreign_sum_eok   |      억 | +18                 |
| 프(억)   | program_eok       |      억 | +350                |
| 큰손     | big_hand_index    |     등급 | +SS                 |
| 모멘텀    | momentum_text     | string | VWAP돌파 + 시가위 + 전고돌파 |

---

# 7. 거래대금 순위 전광판

거래대금 순위 전광판은

유력 후보 전광판과

동일한 내부 Key를 사용한다.

차이점은

유력 후보 전광판

= Ranking Engine 상위 5종목

거래대금 순위 전광판

= 거래대금 상위 100종목

이다.

---

# 8. 핵심 규칙

Signal Engine

Ranking Engine

Strategy Engine

AI 평가

미국시장 데이터

큰손 알고리즘

이 아무리 변경되어도

Frontend Dashboard는

본 문서의 내부 Key만 수신해야 한다.

이 규칙은 StockBoard 전체 시스템의 최우선 규칙으로 정의한다.
