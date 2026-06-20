# StockBoard 이름표 v1

화면에 표시하는 이름과 데이터 연결에 사용하는 내부 키를 분리한다. UI 문구가 변경되더라도 데이터 연결은 내부 키를 기준으로 유지한다.

상태 기준:

- **사용 중**: 현재 화면 또는 데이터 처리에서 사용하는 항목
- **산출값**: 다른 원천 필드로 계산하는 항목
- **연결 예정**: 이름과 구조는 확정했지만 실제 데이터 연결이 필요한 항목
- **UI 확인용 fallback 사용 중**: 실제값이 없을 때 임시 표시값을 사용하는 항목
- **조회 가능 여부 확인 필요**: API 제공 방식과 조회 시점을 추가로 확인해야 하는 항목

## 종목표

| 화면명 | 내부키 | 의미 | 데이터 출처 후보 | 상태 |
|---|---|---|---|---|
| 순위 | `rank` | 필터와 거래대금 내림차순 정렬 후 화면에 다시 부여한 순번 | StockBoard 화면 산출 | 산출값 |
| 전일 등락 | `rank_diff` | `prev_rank - original_rank`로 계산한 순위 변화 | `prev_rank`, `original_rank` | 산출값 |
| 전일순위 | `prev_rank` | 해당 종목의 전일 거래대금 순위 | Kiwoom 거래대금 순위 데이터 후보 | 연결 예정 |
| 원순위 | `original_rank` | Kiwoom이 제공한 당일 거래대금 원래 순위 | Kiwoom `ka10032` 응답 | 사용 중 |
| 등급 | `grade` | 종목 선별 점수 또는 등급 | StockBoard 종목 평가 로직 | UI 확인용 fallback 사용 중 |
| 종목명 | `stock_name` | 종목의 표시 이름 | 종목 마스터 CSV, Kiwoom 종목 정보 | 사용 중 |
| 종목코드 | `stock_code` | 정규화된 6자리 종목 식별자. 화면에서는 미표시할 수 있음 | 종목 마스터 CSV, Kiwoom API | 사용 중 |
| 현재가 | `price` | 종목의 현재 체결 가격 | Kiwoom REST 또는 실시간 시세 | 사용 중 |
| 등락률 | `change_rate` | 전일 종가 대비 현재가 등락률 | Kiwoom REST 또는 실시간 시세 | 사용 중 |
| 금액(억) | `trade_value_eok` | 거래대금을 억 원 단위로 표현한 값 | Kiwoom `ka10032` 응답 또는 원 거래대금 환산 | 사용 중 |
| 일봉 | `ohlc` | 일봉 표시용 가격과 기준선을 묶은 객체 | 일봉 REST 조회, 실시간 집계 결과 | UI 확인용 fallback 사용 중 |
| 시가 | `open` | 당일 시가 | Kiwoom 일봉/당일 시세 | 연결 예정 |
| 고가 | `high` | 당일 고가 | Kiwoom 일봉/당일 시세 | 연결 예정 |
| 저가 | `low` | 당일 저가 | Kiwoom 일봉/당일 시세 | 연결 예정 |
| 종가 | `close` | 당일 종가 또는 장중 현재가 기반 표시값 | Kiwoom 일봉/당일 시세 | 연결 예정 |
| VWAP | `vwap` | 당일 거래량 가중 평균 가격 | 실시간 체결 누적 산출 또는 별도 시세 데이터 | 연결 예정 |
| 전일고가 | `prev_high` | 직전 거래일 고가 | Kiwoom 일봉 조회 | 연결 예정 |
| 전일종가 | `prev_close` | 직전 거래일 종가 | Kiwoom 일봉 조회 | 연결 예정 |
| 전일저가 | `prev_low` | 직전 거래일 저가 | Kiwoom 일봉 조회 | 연결 예정 |
| 잔량비 | `bid_ask_ratio` | 매수잔량을 매도잔량으로 나눈 비율 | Kiwoom 실시간 호가 | UI 확인용 fallback 사용 중 |
| 매수잔량 | `bid_volume` | 종목의 총매수잔량 | Kiwoom 실시간 호가 | 연결 예정 |
| 매도잔량 | `ask_volume` | 종목의 총매도잔량 | Kiwoom 실시간 호가 | 연결 예정 |
| 1분강도 | `strength_1m` | 최근 1분 매수·매도 체결 우위 지표 | Kiwoom 실시간 체결 누적 산출 | UI 확인용 fallback 사용 중 |
| 당일강도 | `strength_day` | 당일 누적 매수·매도 체결 우위 지표 | Kiwoom 실시간 체결 또는 당일 시세 | UI 확인용 fallback 사용 중 |
| 외합(억) | `foreign_sum` | 외국계 증권사 창구 순매수합. 외국인 순매수의 실시간 대체값 | Kiwoom 종목별 거래원/창구 데이터 | UI 확인용 fallback 사용 중 |
| 프로(억) | `program_net` | 종목별 프로그램 순매수 합 | Kiwoom 프로그램 매매 데이터 후보 | UI 확인용 fallback 사용 중 |
| 큰손 | `big_hand` | 큰 규모 자금의 순매수 우위를 나타내는 수치 | 체결·수급 기반 StockBoard 산출 | UI 확인용 fallback 사용 중 |
| 모멘텀 | `momentum` | 돌파, 급증, 유입 등 종목의 주요 조건 설명 | StockBoard 조건 탐지 로직 | 후보표만 UI 확인용 fallback 사용 중 |

## 시장수급

| 화면명 | 내부키 | 의미 | 데이터 출처 후보 | 상태 |
|---|---|---|---|---|
| 시장 | `market` | KOSPI, KOSDAQ 등 시장 구분 | Kiwoom 시장 구분 정보 | 연결 예정 |
| 지수 | `index_value` | 시장의 현재 지수 값 | Kiwoom 업종·지수 시세 | 연결 예정 |
| 등락률 | `market_change_rate` | 시장 지수의 전일 대비 등락률 | Kiwoom 업종·지수 시세 | 연결 예정 |
| 상승종목수 | `advancers` | 현재가가 상승한 종목 수 | Kiwoom 시장 등락 종목 통계 | 연결 예정 |
| 하락종목수 | `decliners` | 현재가가 하락한 종목 수 | Kiwoom 시장 등락 종목 통계 | 연결 예정 |
| 상승변화 | `advancer_delta` | 직전 집계 대비 상승 종목 수 변화 | 시장 통계 시계열 산출 | 산출값 |
| 하락변화 | `decliner_delta` | 직전 집계 대비 하락 종목 수 변화 | 시장 통계 시계열 산출 | 산출값 |
| 외선(억) | `foreign_futures_net` | 외국인의 선물 순매수 금액 | Kiwoom 투자자별 선물 수급 | 연결 예정 |
| 외인(억) | `foreign_net` | 외국인의 시장 순매수 금액 | Kiwoom 투자자별 시장 수급 | 연결 예정 |
| 기관(억) | `institution_net` | 기관의 시장 순매수 금액 | Kiwoom 투자자별 시장 수급 | 연결 예정 |
| 프로(억) | `market_program_net` | 시장 전체 프로그램 순매수 금액 | Kiwoom 프로그램 매매 현황 | 연결 예정 |
| 외합(억) | `market_foreign_sum` | 시장 전체 외국계 창구 순매수합 | 종목별 `foreign_sum` 합산 또는 시장 창구 데이터 | 연결 예정 |
| 외달(억) | `foreign_dollar_net` | 외국인 달러 환산 수급 또는 관련 외화 기준 수급 지표 | Kiwoom 또는 별도 시장·환율 데이터 | 조회 가능 여부 확인 필요: 스트리밍 또는 장마감 조회 가능 여부 별도 확인 |
| 분위기 | `market_mood` | 시장 강약을 요약한 문구 또는 점수 | 시장 등락·수급 기반 StockBoard 산출 | 연결 예정 |
| 시장체온 | `market_temperature` | 상위 100종목의 A/B/C/D 등급 가중 평균 | TOP100 `grade` 분포 | 산출값 |

## 미국시장

| 화면명 | 내부키 | 의미 | 데이터 출처 후보 | 상태 |
|---|---|---|---|---|
| 나스닥 | `us_nasdaq` | 미국 나스닥 지수 등락 정보 | 미국 지수 시세 API 또는 외부 주입 | 연결 예정 |
| QQQ | `us_qqq` | Invesco QQQ ETF 등락 정보 | 미국 ETF 시세 API 또는 외부 주입 | 연결 예정 |
| SMH | `us_smh` | 반도체 ETF 등락 정보 | 미국 ETF 시세 API 또는 외부 주입 | 연결 예정 |
| IBB | `us_ibb` | 바이오 ETF 등락 정보 | 미국 ETF 시세 API 또는 외부 주입 | 연결 예정 |
| LIT | `us_lit` | 2차전지·리튬 ETF 등락 정보 | 미국 ETF 시세 API 또는 외부 주입 | 연결 예정 |
| BOTZ | `us_botz` | 로봇·AI ETF 등락 정보 | 미국 ETF 시세 API 또는 외부 주입 | 연결 예정 |
| 미국장 영향 | `us_market_impact` | 미국 지수와 섹터 흐름이 국내시장에 미칠 영향을 요약한 문구 | 미국시장 데이터 기반 StockBoard 산출 또는 외부 주입 | UI 확인용 fallback 사용 중 |
