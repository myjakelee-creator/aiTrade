# STOCKBOARD_NAMEPLATE_v1.3.md

작성 기준: 2026-06-25 후보5 v0.1 및 StockBoard 운영/UI 개선 반영

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
| 진단 | 운영 표시가 아니라 원천 검증/상태 확인용 |

---

# 핵심 원칙

| 구분 | 기준 |
|---|---|
| 화면 row key | 6자리 `stock_code` |
| Store key | 6자리 normalized code |
| 주문/종목마스터 key | 6자리 code |
| 실시간 표시 가격 원천 | `_AL` 통합 코드 |
| NXT 전용 원천 | `_NX` 코드, 진단/향후 확장용 |
| KRX 원천 | 6자리 code |

예시:

```text
stock_code = 005930
realtime_source_code = 005930_AL
received_code = 005930_AL
registered_code = 005930_AL
normalized_code = 005930
```

---

# 1. 종목표 TOP100 / 후보5

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 순위 | rank / displayed_rank | 화면 산출 | 산출 | `tradable_stock_master.csv` 필터 후 StockBoard 표시 순위 |
| 전일 | rank_diff | 화면 산출 | 산출 | 전일 대비 순위 변화 |
| 전일순위 | prev_rank | 거래대금 순위 | 예정 | 향후 연결 |
| 원순위 | original_rank | ka10032 | DONE | ka10032 원본 순위. HTS/ka10032 원본 대조 기준 |
| 순위 셀 tooltip | rankCellTooltip | HTML | DONE | 표시순위, 원순위, 차이, ETF/우선주 등 tradable master 제외 후 재순위 설명 |
| 등급 | grade / candidate_grade_text | 후보 v0.1 Engine 산출 | DONE/1차 | 화면은 `candidate_grade_text` 우선. 예: A94, B86, C60 |
| 후보점수 원점수 | candidate_score_raw | 후보 v0.1 Engine 산출 | DONE/1차 | 현재 활성 항목 점수 합계 |
| 후보점수 총점 | candidate_score_max | 후보 v0.1 Engine 산출 | DONE/1차 | 고정 2080 아님. 활성 항목 max_score 합계 |
| 후보점수 | candidate_score | 후보 v0.1 Engine 산출 | DONE/1차 | `candidate_score_raw / candidate_score_max * 100` |
| 후보등급 class | candidate_grade_class | 후보 v0.1 Engine 산출 | DONE/1차 | a/b/c/d/f 표시 class |
| 후보여부 | is_candidate | 후보 v0.1 Engine 산출 | DONE/1차 | 후보5 포함 여부 |
| 후보순위 | candidate_rank | 후보 v0.1 Engine 산출 | DONE/1차 | 후보5 내부 1~5 순위 |
| 후보상태 | candidate_status | 후보 v0.1 Engine 산출 | DONE/1차 | READY/WATCH/WEAK |
| 후보사유 | candidate_reason | 후보 v0.1 Engine 산출 | DONE/1차 | 등급 tooltip용 |
| 추세확인 | trend_ok / trend_reason | 후보 v0.1 Engine 산출 | DONE/1차 | 현재가 > 시가 기준 1차 판단 |
| 종목명 | stock_name | 종목마스터 | DONE |  |
| 종목코드 | stock_code | 종목마스터 / normalized code | DONE/보존 | 6자리 문자열. DOM/Store/주문 key |
| 원본 종목코드 | raw_stock_code | ka10032 원본 | DONE/보존 | `_AL/_NX` 원천 확인용 |
| 소스 종목코드 | source_stock_code | ka10032 원본 | DONE/보존 | raw_stock_code와 동일 목적 |
| 실시간 원천코드 | realtime_source_code | OpenAPI SetRealReg | DONE/보존 | 표시용 실시간은 `_AL` 기준 |
| 수신 원본코드 | received_code | OnReceiveRealData | DONE/진단 | `_AL` suffix 유지 확인 |
| 등록 원본코드 | registered_code | SetRealReg | DONE/진단 | `_AL` 실제 등록 확인 |
| 정규화코드 | normalized_code | Python 정규화 | DONE/진단 | 6자리 Store key |
| 현재가 | price | REST + realtime overlay | DONE | 실시간 tick 있으면 `_AL` FID10 overlay |
| REST 현재가 | rest_price | TR/REST | DONE/보존 | 실시간 overlay 전 기준값 |
| 실시간 현재가 | realtime_price | OpenAPI `_AL` FID10 | DONE | StockBoard 표시 현재가의 우선 원천 |
| 등락률 | change_rate | REST + realtime overlay | DONE | 실시간 tick 있으면 `_AL` FID12 overlay |
| 실시간 등락률 | realtime_change_rate | OpenAPI `_AL` FID12 | DONE | patch 기준 |
| 금액(억) | realtime_acc_trade_value_eok_candidate | `/api/realtime_patch` 누적 거래대금 억 후보 | DONE | 초기 fallback은 `trade_value_eok`. HTML 계산 없음. `cells[6]` 500ms 표시 갱신. 순위 재정렬/후보5 재선정은 별도 Ranking Engine 또는 재정렬 단계에서 처리 |
| 실시간 누적거래량 | cumulative_volume | OpenAPI `_AL` FID13 | DONE | quote 내부 보존 |
| 실시간 누적거래대금 | cumulative_value | OpenAPI `_AL` FID14 | DONE | quote 내부 보존 |
| 체결시간 | trade_time / fid20_trade_time | OpenAPI `_AL` FID20 | DONE/보존 | 실시간 지연 판단용 아님 |
| 수신시각 | received_at | 서버 수신시각 | DONE | 실시간성 판단 기준 |
| 실시간 sequence | sequence | RealtimeStore | DONE | 실시간성 판단 기준 |
| 일봉 | ohlc | ka10086 | DONE | base OHLC. `realtime_ohlc`가 없을 때 fallback |
| 실시간 일봉 | realtime_ohlc | ka10086 base + realtime tick | DONE | 화면 일봉 우선 원천. base open 유지, tick price로 high/low/close 갱신 |
| 실시간 일봉 원천 | realtime_ohlc_source | RealtimeStore | DONE | `ka10086_base_plus_realtime_tick` |
| 일봉 원천 | ohlc_source | 표시 정책 | DONE/후보 | `realtime_ohlc` 우선, 없으면 `ohlc` fallback |
| 시가 | open | ka10086 / realtime_ohlc | DONE | realtime_ohlc에서도 base open 유지 |
| 고가 | high | ka10086 / realtime tick | DONE | realtime_ohlc는 max(base high, 기존 realtime high, tick price) |
| 저가 | low | ka10086 / realtime tick | DONE | realtime_ohlc는 min(base low, 기존 realtime low, tick price) |
| 종가 | close | ka10086 / realtime tick | DONE | realtime_ohlc는 최신 realtime price |
| VWAP | vwap | Python 산출 / base 유지 | DONE | realtime_ohlc에서는 base vwap 유지 |
| VWAP 원천 | vwap_source | RealtimeStore | DONE | 현재 `base` |
| 전일고가 | prev_high | ka10086 | DONE |  |
| 전일종가 | prev_close | ka10086 | DONE |  |
| 전일저가 | prev_low | ka10086 | DONE |  |
| 외합 원본 | foreign_sum | ka10037 외국계 창구 합계 | DONE/보존 | 장중 대체 지표. 원본 의미 변경 금지 |
| 외인 원본 | foreign_investor_net | ka10066 장마감후투자자별매매요청 | DONE/보존 | 장마감 후 종목별 외국인 순매수 |
| 외합/외인 표시명 | foreign_display_label | Python 산출 | DONE | 장중 외합(억), 장마감 후 외인(억) |
| 외합/외인 표시값 | foreign_display_value | Python 산출 | DONE | foreign_investor_net 우선, 없으면 foreign_sum |
| 외합/외인 원천 | foreign_display_source | Python 산출 | DONE | 어떤 원천을 표시했는지 |
| 프로(억) | program_net | ka90004 | DONE | 429/partial 처리 |
| 최우선매수호가 | best_bid_price | OpenAPI `_AL` 호가 | DONE | 주식호가잔량 `_AL` 기준 |
| 최우선매도호가 | best_ask_price | OpenAPI `_AL` 호가 | DONE | 주식호가잔량 `_AL` 기준 |
| 매수잔량 | bid_volume | OpenAPI `_AL` 호가 | DONE | 잔량비 계산 원천 |
| 매도잔량 | ask_volume | OpenAPI `_AL` 호가 | DONE | 잔량비 계산 원천 |
| 잔량비 | bid_ask_ratio | OpenAPI `_AL` 호가 산출 | DONE | `bid_volume / ask_volume`, 최신 호가잔량 순간값. 1분 평균 아님 |
| 순간강도 | realtime_strength | OpenAPI `_AL` FID228 `execution_strength_raw` | DONE | 최신 실시간 체결강도 순간값. Store `execution_strength`, API `realtime_strength`. 숫자는 원값 그대로 표시. 바는 `instantStrengthPosition()` 0~200 clamp, 100 균형, 0 이하 매도 포화, 200 이상 매수 포화, gamma 0.6 비선형 스케일 |
| 세션강도 | session_strength | OpenAPI `_AL` FID15 `trade_qty` 부호 누적 | DONE | 서버 시작 이후 `session_buy_qty_live / session_sell_qty_live * 100`. `sell == 0`이면 None. `/api/realtime_patch`, `/api/top100` overlay, `/api/realtime` 노출. `cells[10]` realtime_patch 갱신. tooltip은 서버 시작 이후 누적/정식 당일 backfill 미포함 표시. PID 22288 런타임 검증에서 45초 delta 36회, 대상 5종목 row 180/180 포함, 5종목 계산 일치 |
| 가격제한 상태 | price_limit_state | Python 산출 | DONE/1차 | `upper/lower/none`. 상·하한가 잔량비/강도 표시 예외처리 |
| 가격제한 사유 | price_limit_reason | Python 산출 | DONE/1차 | 예: 상한가 추정 |
| 가격제한 원천 | price_limit_source | Python 산출 | DONE/진단 | 현재 `change_rate_threshold` 우선, 제한가 원천 확보 시 교체 |
| 정확한 당일강도 | strength_day | 체결 buy/sell backfill + live 누적 | 보류 | OPT10084 샘플 기준 `cntr_trde_qty`가 unsigned이고 `sign`은 전일대비 기호로 보여 buy/sell base 원천으로 부족. 향후 `day_buy_qty_base`, `day_sell_qty_base`, `session_buy_qty_live`, `session_sell_qty_live` 구조로 확장 |
| 큰손 | big_hand | 체결 산출 | 예정 | KRT/큰손 원천 연결 후 계산 |
| 모멘텀 | momentum | 후보 v0.1 / 향후 Strategy | DONE/1차 | v0.1은 거래대금상위 + 순위차 + 시가위/아래 요약. 정식 Strategy momentum은 후속 |

Source 필드 후보:

| 필드 | 대상 |
|---|---|
| price_source | 현재가 |
| trade_value_source | 금액(억) |
| ohlc_source | 일봉 |
| realtime_ohlc_source | 실시간 일봉 |
| vwap_source | VWAP |
| bid_ask_ratio_source | 잔량비 |
| realtime_strength_source | 순간강도 |
| session_strength_source | 세션강도 |

현재 500ms `/api/realtime_patch` 갱신 컬럼:

- 현재가
- 등락률
- 금액(억)
- 일봉
- 잔량비
- 순간강도
- 세션강도

Fallback 원칙:

- 현재가/등락률/금액(억)/일봉은 `close_snapshot` 표시 가능.
- 일봉은 `realtime_ohlc` 우선, 없으면 기존 `ohlc` fallback.
- 잔량비는 realtime 호가가 없으면 `unavailable`.
- 순간강도는 realtime FID228이 없으면 `unavailable`.
- 세션강도는 서버 시작 이후 실시간 체결 누적이므로 fallback 금지.

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
| 실시간 sequence | sequence | RealtimeStore | DONE | 실시간성 판단 기준 |
| 실시간 최신시각 | updated_at | RealtimeStore | DONE |  |
| quote 수 | quote_count | RealtimeStore | DONE | 시장세션에 따라 변동 |
| trade event 수 | trade_event_count | RealtimeStore | DONE | 시장세션에 따라 변동 |
| orderbook event 수 | orderbook_event_count | RealtimeStore | DONE | 시장세션에 따라 변동 |
| 실시간 patch | realtime_patch | RealtimeStore snapshot/delta | DONE | 초기 full + since_sequence 기반 delta |
| TOP100 필터 진단 | top100_filter_report | ka10032 + tradable master | DONE/진단 | raw_count=269, displayed_count=177, dropped_count=92. 탈락 상위는 ETF 계열/우선주가 대부분 |
| Provider 사용 가능 | available | KiwoomOpenApiRealtimeProvider | DONE | 상태 조회 가능 |
| Provider 실행 | running | KiwoomOpenApiRealtimeProvider | DONE | env ON 시 실행 |
| QAx 준비 | qt_ready | QAxWidget | DONE | 32bit Python 필요 |
| QAx 컨트롤 생성 | control_created | QAxWidget | DONE | KHOPENAPI control |
| Qt Pump 실행 | qt_pump_running | Qt event pump | DONE | OnEventConnect / OnReceiveRealData 수신용 |
| 로그인 요청 | login_requested | CommConnect | DONE | env ON 시 true |
| 로그인 상태 | login_state | OnEventConnect | DONE | connected 확인 |
| 로그인 오류코드 | login_error_code | OnEventConnect | DONE | 0이면 성공 |
| 로그인 완료시각 | login_completed_at | OnEventConnect | DONE | connected 후 기록 |
| 실시간 등록 요청 | register_requested | register_codes | DONE | env ON + 등록 옵션 |
| 등록 종목수 | registered_count | register_codes | DONE | TOP100 결과에 따라 변동 |
| 주식체결 등록 | realreg_succeeded | SetRealReg | DONE | screen 9000/9001 |
| 주식체결 FID | realreg_fids | SetRealReg | DONE | 10;12;20;15;228;13;14;290 |
| 호가잔량 등록 | orderbook_realreg_succeeded | SetRealReg | DONE | screen 9010/9011 |
| 실시간 등록 원천 | setrealreg_codes_sample | Provider status | DONE/진단 | `_AL` 등록 확인 |
| 등록 정규화 샘플 | register_code_map_sample | Provider status | DONE/진단 | `_AL` → 6자리 |
| suffix 실험 | suffix_realreg_* | Provider status | 진단 | 기본 실행 OFF |
| 예상체결/시간외 진단 | expected_realreg_*, after_single_realreg_* | Provider status | 진단 | 기본 실행 OFF |

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
| 통합상태등 | combined_status_tone | A/K/W를 1개 램프로 통합 표시 | DONE |
| API 세부상태 | api_status | 통합상태등 tooltip용 | DONE |
| 키움 세부상태 | kiwoom_status | 통합상태등 tooltip용 | DONE |
| 웹 세부상태 | web_status | 통합상태등 tooltip용 | DONE |
| 갱신지연 | update_delay | 화면에는 초 단위 숫자만 표시, tooltip에 의미 표시 | DONE |
| 장상태 | market_session | 프리/정규/애프터/장마감 | DONE |

---

# 5A. 운영 런처 / UI 조작

| 화면명/기능 | 내부키/파일 | 의미 | 상태 |
|---|---|---|---|
| 원클릭 라이브 시작 | start_stockboard_live.cmd | 8000 PID 종료 → 32bit Python + realtime env → 브라우저 열기 | DONE |
| 런처 본체 | scripts/start_stockboard_live.ps1 | Provider 대기, `/api/top100`, realtime 상태 검증 | DONE |
| 루트 자동 이동 | `/` redirect | `stockboard_v0_3_0_sample.html`로 302 이동 | DONE |
| 캔들모드 클릭 전환 | ohlc-cell click | 일봉 셀 클릭 시 Full/Proportional 전환 | DONE |
| 하단 헤더 정렬 | boardSort | 하단 종목표 헤더 클릭/역정렬, localStorage 유지 | DONE |
| 밀도 모드 | displayDensity | 기본/압축/초압축 | DONE |
| 폭 초기화 | column width reset | 저장 열폭 삭제 후 현재 밀도 기준폭 적용 | DONE |
| 파이그래프 tooltip | grade distribution tooltip | 화면 텍스트 제거, tooltip으로 등급분포 확인 | DONE |

---

# 6. 향후 추가 예정

| 화면명 | 내부키 | 비고 |
|---|---|---|
| 시장체온 | market_temperature | TOP100 등급분포 기반 |
| 후보점수 v0.2 | candidate_model_v2 | v0.1 관찰 후 선발기준 고도화 |
| 큰손매수 | big_buy | KRT |
| 큰손매도 | big_sell | KRT |
| 큰손리듬 | krt | KRT |
| 외선변화 | foreign_futures_delta | 외선 증감 |
| 호가흡수 | orderbook_absorption | 체결 + 호가 |
| patch delta | realtime_patch_delta | since_sequence 기반 경량 patch |
| 선발기준 레고블럭 | candidate_model_registry | 후보 모델 추가/삭제/가중치 변경 관리 |

---

# 7. 내부키 명명 규칙

화면명은 바뀔 수 있지만 내부키는 임의 변경하지 않는다.

| 구분 | 내부키 | 의미 |
|---|---|---|
| 기본 종목코드 | stock_code | 6자리 key. DOM/Store/주문/마스터 기준 |
| 원본 종목코드 | raw_stock_code | TR 원본 보존 |
| 표시 실시간 원천 | realtime_source_code | 기본 `_AL` |
| 수신 원본코드 | received_code | OnReceiveRealData 원본 |
| 등록 원본코드 | registered_code | SetRealReg 실제 등록 코드 |
| 정규화코드 | normalized_code | 6자리 Store key |
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

추가 원칙:

```text
화면 표시 실시간 가격은 _AL 통합 원천을 사용한다.
내부 식별자와 주문 코드는 6자리 코드를 유지한다.
```
