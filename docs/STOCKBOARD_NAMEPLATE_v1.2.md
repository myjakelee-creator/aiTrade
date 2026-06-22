# STOCKBOARD_NAMEPLATE_v1.2.md

작성 기준: 2026-06-22 최신 StockBoard 상태 기준

---

# 목적

화면에 표시하는 이름과 데이터 연결에 사용하는 내부 키를 분리한다.

UI 문구가 변경되더라도 데이터 연결은 내부 키를 기준으로 유지한다.

---

# 상태 기준

| 상태 | 의미 |
|---|---|
| DONE | 실제 데이터 연결 완료 |
| 예정 | 이름과 구조는 확정, 실제 데이터 연결 필요 |
| 임시 | UI 확인용 fallback 또는 임시 데이터 사용 |
| 산출 | 다른 필드로 계산 |
| 보존 | 원본 의미 보존용 내부키 |

---

# 1. 종목표 TOP100 / 후보5

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 순위 | rank | 화면 산출 | 산출 | TOP100 표시 순서 |
| 전일 | rank_diff | 화면 산출 | 산출 | 전일 대비 순위 변화 |
| 전일순위 | prev_rank | 거래대금 순위 | 예정 | 향후 연결 |
| 원순위 | original_rank | ka10032 | DONE | 원 거래대금 순위 |
| 등급 | grade | 종목 평가 로직 | 임시 | Signal/Ranking 전까지 임시 |
| 종목명 | stock_name | 종목마스터 | DONE |  |
| 종목코드 | stock_code | 종목마스터 | DONE | 6자리 문자열 |
| 현재가 | price | REST | DONE |  |
| 등락률 | change_rate | REST | DONE |  |
| 금액(억) | trade_value_eok | ka10032 | DONE | 거래대금 |
| 일봉 | ohlc | ka10086 | DONE | 반복 렌더링 해결 완료 |
| 시가 | open | ka10086 | DONE |  |
| 고가 | high | ka10086 | DONE |  |
| 저가 | low | ka10086 | DONE |  |
| 종가 | close | ka10086 | DONE |  |
| VWAP | vwap | Python 산출 | DONE |  |
| 전일고가 | prev_high | ka10086 | DONE |  |
| 전일종가 | prev_close | ka10086 | DONE |  |
| 전일저가 | prev_low | ka10086 | DONE |  |
| 외합 원본 | foreign_sum | ka10037 외국계 창구 합계 | DONE/보존 | 장중 대체 지표. 원본 의미 변경 금지 |
| 외인 원본 | foreign_investor_net | ka10066 장마감후투자자별매매요청 | DONE/보존 | 장마감 후 종목별 외국인 순매수 |
| 외합/외인 표시명 | foreign_display_label | Python 산출 | DONE | 장중 외합(억), 장마감 후 외인(억) |
| 외합/외인 표시값 | foreign_display_value | Python 산출 | DONE | foreign_investor_net 우선, 없으면 foreign_sum |
| 외합/외인 원천 | foreign_display_source | Python 산출 | DONE | foreign_investor_net 또는 foreign_sum |
| 프로(억) | program_net | ka90004 | DONE | 193행 연결, 429/partial 처리 |
| 잔량비 | bid_ask_ratio | 호가 | 예정 | OpenAPI 실시간 후 연결 |
| 매수잔량 | bid_volume | 호가 | 예정 | OpenAPI 실시간 후 연결 |
| 매도잔량 | ask_volume | 호가 | 예정 | OpenAPI 실시간 후 연결 |
| 1분강도 | strength_1m | 체결 | 예정 | OpenAPI 실시간 후 계산 |
| 당일강도 | strength_day | 체결 | 예정 | OpenAPI 실시간 후 계산 |
| 큰손 | big_hand | 체결 산출 | 예정 | KRT/큰손 원천 연결 후 계산 |
| 모멘텀 | momentum | Python 산출 | 예정 | Strategy 전까지 미구현 |

---

# 2. 시장수급

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 시장 | market_name | Backend | DONE |  |
| 지수 | market_index | 시장지수 | DONE |  |
| 등락률 | market_change_rate | 시장지수 | DONE |  |
| 상승종목수 | advancers | 시장통계 | DONE |  |
| 하락종목수 | decliners | 시장통계 | DONE |  |
| 상한가수 | upper_limit_count | 시장통계 | DONE |  |
| 하한가수 | lower_limit_count | 시장통계 | DONE |  |
| 개인(억) | individual_eok | ka10051 ind_netprps | DONE |  |
| 외인(억) | foreign_spot_eok | ka10051 frgnr_netprps | DONE | 시장 전체 외국인 현물 |
| 기관(억) | institution_eok | ka10051 orgn_netprps | DONE |  |
| 프로(억) | program_market_eok | ka90005 all_netprps | DONE | 시장 전체 프로그램 |
| 외선(억) | foreign_futures_eok | COM 선물투자주체 | 예정 | 향후 최우선 수급 항목 |
| 시장세션 | market_session | Python 산출 | DONE | 프리/정규/애프터/장마감 |

---

# 3. 실시간 / OpenAPI Provider

| 화면명/상태 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 실시간 전체 상태 | realtime_status | RealtimeStore | DONE | API 존재 |
| 실시간 sequence | sequence | RealtimeStore | DONE | 현재 0이면 실시간 미수집 |
| 실시간 최신시각 | updated_at | RealtimeStore | DONE |  |
| quote 수 | quote_count | RealtimeStore | DONE | 현재 0 |
| trade event 수 | trade_event_count | RealtimeStore | DONE | 현재 0 |
| orderbook event 수 | orderbook_event_count | RealtimeStore | DONE | 현재 0 |
| Provider 사용 가능 | available | KiwoomOpenApiRealtimeProvider | DONE | 상태 조회 가능 |
| Provider 실행 | running | KiwoomOpenApiRealtimeProvider | DONE | 기본 실행 false |
| QAx 준비 | qt_ready | QAxWidget | DONE | env ON 시 확인 |
| QAx 컨트롤 생성 | control_created | QAxWidget | DONE | env ON 시 확인 |
| Qt Pump 실행 | qt_pump_running | Qt event pump | DONE | OnEventConnect 수신용 |
| 로그인 요청 | login_requested | CommConnect | DONE | env ON 시 true |
| 로그인 상태 | login_state | OnEventConnect | 진행중 | connected 최종 재검증 필요 |
| 로그인 오류코드 | login_error_code | OnEventConnect | 진행중 | 0이면 성공 |
| 로그인 완료시각 | login_completed_at | OnEventConnect | 진행중 | connected 후 기록 |
| 실시간 등록 요청 | register_requested | register_codes | DONE | env ON + 등록 옵션 |
| 등록 종목수 | registered_count | register_codes | DONE | 아직 실제 SetRealReg 아님 |

---

# 4. 미국시장

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 나스닥 | us_nasdaq | 미국지수 API | 예정 |  |
| QQQ | us_qqq | 미국 ETF | 예정 |  |
| SMH | us_smh | 미국 ETF | 예정 |  |
| IBB | us_ibb | 미국 ETF | 예정 |  |
| LIT | us_lit | 미국 ETF | 예정 |  |
| BOTZ | us_botz | 미국 ETF | 예정 |  |
| 영향 | us_market_impact | Python 산출 | 임시 | 예: 반도체 우호 |

---

# 5. 상단 상태바

| 화면명 | 내부키 | 의미 | 상태 |
|---|---|---|---|
| 날짜시간 | current_datetime | KST 현재시각 | DONE |
| A | api_status | API 서버 상태 | DONE |
| K | kiwoom_status | 키움 상태 | DONE |
| W | web_status | 웹 상태 | DONE |
| 갱신지연 | update_delay | 마지막 API 응답 경과시간 | DONE |
| 장상태 | market_session | 프리/정규/애프터/장마감 | DONE |

---

# 6. 향후 추가 예정

| 화면명 | 내부키 | 비고 |
|---|---|---|
| 시장체온 | market_temperature | TOP100 등급분포 기반 |
| 후보점수 | candidate_score | 후보5 선정용 |
| 큰손매수 | big_buy | KRT |
| 큰손매도 | big_sell | KRT |
| 큰손리듬 | krt | KRT |
| 외선변화 | foreign_futures_delta | 외선 증감 |
| 호가흡수 | orderbook_absorption | 체결 + 호가 |

---

# 7. 내부키 명명 규칙

화면명은 바뀔 수 있지만 내부키는 임의 변경하지 않는다.

중요 원칙:

| 구분 | 내부키 | 의미 |
|---|---|---|
| 외국계 창구 합계 | foreign_sum | ka10037, 장중 대체 지표 |
| 장마감 외국인 순매수 | foreign_investor_net | ka10066 |
| 화면 표시용 이름 | foreign_display_label | 외합(억) 또는 외인(억) |
| 화면 표시용 값 | foreign_display_value | 실제 표시값 |
| 화면 표시용 원천 | foreign_display_source | 어떤 원천을 표시했는지 |

---

# 8. 최종 원칙

```text
REST
↓
기본 데이터

OpenAPI COM
↓
실시간 체결 / 호가 / 외선 / 큰손 / KRT

Python
↓
모든 계산

Store
↓
실시간 데이터 저장

Engine
↓
판단과 표시용 키 산출

Server
↓
API 전달

HTML
↓
표시 전용
```

HTML은 계산하지 않는다.

Python이 계산한다.

Store는 저장한다.

Engine은 판단한다.

Server는 전달한다.

HTML은 보여준다.
