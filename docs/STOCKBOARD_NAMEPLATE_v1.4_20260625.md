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
| display_price | 장상태별 표시 현재가 |
| display_change_rate | 장상태별 표시 등락률 |
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

가격 불가침 원칙:

- FID10 현재가 normalize 로직을 임의 수정하지 않는다.
- FID12 등락률 normalize 로직을 임의 수정하지 않는다.
- `price`/`change_rate`를 후보5, 등급, 모멘텀 계산에서 덮어쓰지 않는다.
- HTML에서 가격을 계산하지 않는다.
- 가격 불일치를 보정식으로 해결하지 않는다.
- 표시 가격은 반드시 source가 명시된 서버 산출값만 사용한다.
- 장마감/애프터마켓 문제는 가격 보정이 아니라 `price_source`/`display_price`/`display_change_rate` 선택 정책으로 해결한다.
- 후보5/등급/모멘텀은 가격을 읽기만 하고 원천값을 변경하지 않는다.

2026-06-25 관찰/WIP:

- 애프터마켓 가격은 HTS와 비교적 잘 맞는 것으로 관찰했다.
- 정규장 동시호가 중 가격도 대체로 맞는 것으로 관찰했다.
- 15:30 정규장 마감 직후 가격 불일치가 다시 나타났다.
- 현재 판단은 가격 파싱 회귀보다 `regular_close_snapshot` / 장상태별 `price_source` 정책 부재 가능성이 크다.
- `/api/top100`은 root 배열 189개 row로 정상이며, `stock_code`는 6자리, `realtime_source_code`는 `_AL`을 유지한다.
- PowerShell 빈 표시는 API 문제가 아니라 `{rows: [...]}`를 기대한 파싱 가정 문제였다.

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
| suffix Store 오염 방지 | kiwoom_data_provider.py WIP | Provider 진단 보완 | WIP/내일 정규장 검증 필요 | 진단 ON 상태의 6자리 KRX / `_NX` sample이 운영 RealtimeStore를 덮지 않도록 분리하는 의도. OFF -> ON -> OFF 최종 검증 전까지 DONE 금지 |
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
| suffix 진단 Store 오염 방지 검증 | suffix_store_isolation_verification | 내일 정규장 OFF -> ON -> OFF 재검증 |
| 정규장 가격 대조 | regular_session_price_compare | HTS와 6자리 key / `_AL` source 대조 |
| 정규장 마감 snapshot | regular_close_snapshot | 15:30 마감값 고정 구조 설계/구현 |
| 마감 직후 고정 표시 | regular_close_display_lock | 15:30~15:40 `regular_close_snapshot` 고정 표시 |
| 애프터마켓 fallback | aftermarket_price_policy | 15:40 이후 aftermarket_realtime / regular_close_snapshot fallback 정책 |
| 표시 가격 필드 정식화 | display_price_policy | `price_source` / `display_price` / `display_change_rate` 필드 정식화 |
| 렌더링 최적화 | realtime_render_optimization | 변경된 셀만 그리기, 일봉/색상바 throttle |

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

---

# 9. 2026-06-25 운영/UI 조작 추가 항목

| 화면/운영 항목 | 내부키/파일명 | 의미 | 상태 | 비고 |
|---|---|---|---|---|
| 종목 선택 active 상태 | `activeStockCode` | 현재 StockBoard에서 선택된 종목 코드 상태 | DONE | DOM node가 아니라 코드 문자열 기준으로 유지 |
| active 종목 표시 | `active-stock-row`, `active-stock-name` | 후보5와 하단 종목표에서 선택 종목을 강조 표시 | DONE | refresh/realtime_patch 이후 같은 코드가 있으면 복원 |
| 종목명 클릭 선택 | stock name cell click | 후보5와 하단 종목표 종목명 클릭으로 active 지정 | DONE | 가격/등락률/거래대금/후보점수/등급/모멘텀 계산 변경 없음 |
| 클립보드 연동 | clipboard stock code | 선택 종목 코드를 클립보드에 저장 | DONE | HTML은 `005930`, `005930_AL`, `005930_NX`만 허용 |
| Up/Down 이동 | keyboard navigation | 하단 표시 순서 기준으로 active 종목 이동 | DONE | 정렬/역정렬 후 현재 DOM 표시 순서 기준 |
| 영웅문 HTS 연동 | HTS Edit6 bridge | 클립보드 종목코드를 영웅문 차트 입력 컨트롤에 전달 | DONE | HTS 입력 최종값은 항상 6자리 |
| AHK v1 bridge | `stockboard_kiwoom_link_v1.ahk` | AutoHotkey v1 기반 영웅문 HTS bridge | DONE | `_AL`, `_NX` suffix는 AHK가 입력 직전에 제거 |
| AHK 관리자 실행 | `run_stockboard_kiwoom_link_v1_admin.cmd` | AHK v1 bridge를 관리자 권한으로 실행 | DONE | 일반 권한은 `Edit6 set failed`로 운영 제외 |
| 시작 런처 | `start_stockboard_live.cmd` | 대표 시작 파일 | DONE | 서버/OpenAPI/브라우저/AHK 관리자 bridge 통합 시작 |
| 시작 본체 | `scripts/start_stockboard_live.ps1` | live 시작 로직 본체 | DONE | warning 완료 시에도 launcher 자동 닫힘 |
| 종료 런처 | `stop_stockboard_live.cmd` | 대표 종료 파일 | DONE | AHK, 8000번 서버, 서버 PowerShell 로그 창 종료 |
| 서버 창 PID | `data/runtime/stockboard_server_window.pid` | visible server PowerShell window PID 저장 | DONE | stop 런처가 이 PID 기준으로 로그 창 종료 |

운영 원칙:

- 화면 표시 실시간 원천 `_AL` 원칙은 변경하지 않는다.
- DOM/Store/주문 key 6자리 원칙은 변경하지 않는다.
- HTS 입력 최종값은 6자리이다.
- HTML은 가격/등락률/거래대금/후보점수/등급/모멘텀/OHLC를 새로 계산하거나 보정하지 않는다.
- realtime_patch / 스트리밍 표시 경로는 변경하지 않는다.

---

# 10. 2026-06-26 Fast/Graphic visual-cell 및 5분강도 정리

작성 기준: 2026-06-26 Price Fast Mode, Hybrid Orderbook, visual-cell 경량 렌더링 적용 후 상태.

## 화면 표시명

| 화면 표시명 | 내부 이름 / 필드 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 5분강도 | strength_5m / browser_5m_strength | opt10046 snapshot 우선, 없으면 브라우저 최근 5분 delta | DONE | 장마감 후 `strength_source=opt10046` 공식값 우선 |
| 순간강도 | realtime_strength / realtime_strength_snapshot | OpenAPI `_AL` FID228 우선, 없으면 opt10046 snapshot | DONE | 100 기준 visual-cell 배경 표시 |
| 잔량비 | bid_ask_ratio / bid_pct / ask_pct | 실시간 호가 우선, 없으면 opt10004 snapshot | DONE | 장마감 후 총매수/총매도잔량 snapshot |
| 일봉 | realtime_ohlc | ka10086 base + realtime tick | DONE | 기준선 제거, 경량 candle background |

## 5분강도 명명 정책

- 화면 컬럼명은 `5분강도`로 표시한다.
- `strength_5m`, `strength_20m`, `strength_60m`은 opt10046 공식값 필드다.
- `strength_source=opt10046`이고 `strength_5m` 값이 있을 때만 공식 5분강도라고 부른다.
- 장마감 후에는 opt10046 체결강도추이시간별요청 snapshot을 우선 표시한다.
- opt10046 snapshot이 없으면 현재 화면의 `5분강도`는 브라우저 수신 기준 최근 5분 delta 임시 표시를 사용한다.
- 계산식은 `최근5분 매수체결량 delta / 최근5분 매도체결량 delta * 100`이다.
- browser delta fallback tooltip에는 `주의: opt10046 체결강도5분 아님`을 표시한다.
- opt10046 tooltip에는 source, snapshot_at, stale_sec, status를 표시한다.

## 잔량비 장마감 snapshot 정책

- 실시간 호가잔량이 있으면 실시간 `bid_volume`, `ask_volume`, `bid_ask_ratio`를 우선 사용한다.
- 실시간 호가잔량이 없으면 `opt10004 주식호가요청` snapshot을 사용한다.
- 확정 필드:
  - `orderbook_source=opt10004`
  - `bid_volume_snapshot`
  - `ask_volume_snapshot`
  - `bid_pct`
  - `ask_pct`
  - `bid_ask_ratio_snapshot`
- Fast Mode 본문은 `64/36` 형식이다. 앞 숫자는 매수잔량 비율, 뒤 숫자는 매도잔량 비율이다.
- Fast Mode 본문에는 `수/도/%`를 붙이지 않는다.
- Graphic Mode는 같은 `bid_pct` / `ask_pct` 기준 red/blue 배경 비율을 표시한다.
- `ask_pct=0`은 정상값이며 `100/0` 또는 `0/100`처럼 표시한다.
- Tooltip에는 `잔량비: 64% / 36%`, 총매수잔량, 총매도잔량, 계산식, source, snapshot_at, stale_sec, status를 표시한다.

## 최신 운영/진단 필드

| 이름 | 의미 | 상태 |
|---|---|---|
| price_fast_mode | 현재가/등락률 우선 수신 모드 | DONE |
| realtime_code_limit | 실시간 체결 등록 제한. 현재 100 | DONE |
| orderbook_mode | orderbook 등록 정책. 현재 hybrid | DONE |
| orderbook_hot_limit | hot orderbook 대상 수. 현재 5 | DONE |
| orderbook_rotate_batch | 순환 orderbook batch. 현재 20 | DONE |
| orderbook_rotate_interval_sec | 순환 주기. 현재 5초 | DONE |
| display_mode | `fast` / `graphic` 화면 모드 | DONE |
| visual_cell_palette | E palette | DONE |
| strength_5m_enabled | opt10046 5분강도 backend 조회 활성 여부. 현재 False | DONE/진단 |
| close_5m_strength | 장마감 opt10046 5분강도 snapshot | DONE |
| orderbook_close_snapshot | 장마감 opt10004 총매수/총매도잔량 snapshot | DONE |

## E palette 고정값

| 역할 | 색상 |
|---|---|
| red | `#E75F5F` |
| blue | `#5F9EF5` |
| neutral | `#E2E8F0` |
| wick | `#111827` |

## tooltip 정책

- visual-cell은 native `title`을 사용하지 않는다.
- custom tooltip은 `data-tooltip` 단일 경로를 사용한다.
- 접근성 문구는 `aria-label`로 유지할 수 있다.
- visual-cell 내부 자식 element에도 `title`을 남기지 않는다.
- 잔량비, 순간강도, 5분강도, 일봉은 숫자 본문보다 셀 배경과 tooltip 중심으로 표시한다.
