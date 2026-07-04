# STOCKBOARD_NAMEPLATE_v1.4_20260625.md

작성 기준: 2026-07-04 인계서 Part 1~3 반영.

이 문서는 StockBoard 화면명과 내부키를 분리하기 위한 기준표다. UI 문구가 바뀌어도 데이터 연결은 내부키 기준으로 유지한다.

---

## 1. 핵심 원칙

| 구분 | 기준 |
|---|---|
| 화면 row key | 6자리 `stock_code` |
| Store key | 6자리 normalized code |
| DOM key | 6자리 code |
| 종목마스터 key | 6자리 code |
| 실시간 표시 가격 원천 | `_AL` 통합 코드 |
| NXT 전용 원천 | `_NX` 코드, 진단/향후 확장용 |
| KRX 원천 | 6자리 code |
| 가격 표시 계산 | 서버 담당 |
| HTML 역할 | 표시 전용. 가격/등락률/거래대금 재계산 금지 |

예시:

```text
stock_code = 005930
realtime_source_code = 005930_AL
received_code = 005930_AL
registered_code = 005930_AL
normalized_code = 005930
```

---

## 2. 현재 화면 그룹명

| 화면명 | 내부명 | 설명 | 상태 | 비고 |
|---|---|---|---|---|
| Top5 | `candidateRows` / `top5` | 유력 후보 5종목 | DONE | candidate rank 기준 |
| S1 | `selectedRow` | 선택 종목 1개 | DONE | 선택 row |
| Top15 | `top20Rows` | Top5 제외 후 실제 15종목 | DONE | 내부 변수명 `top20` 잔존 가능 |
| Top30 | `top50Rows` | Top20 제외 후 실제 30종목 | DONE | 내부 변수명 `top50` 잔존 가능 |
| Top300 | `top300Rows` / `trading-board` | 전체 pool | DONE | 표시 pool |

주의:

```text
화면명은 Top15 / Top30으로 바뀌었지만 내부 변수명 top20 / top50은 아직 남아 있을 수 있다.
변수명 전체 변경은 월요일 08:00~09:00 검증 후 별도 작업이다.
```

---

## 3. Realtime lane 이름표

| Lane | 대상 | API | 목적 | 상태 |
|---|---|---|---|---|
| HOT | Top5 + S1 + Top15 | `/api/hot_realtime_patch` | 핵심 후보 빠른 갱신 | DONE |
| MID | Top30 | `/api/realtime_patch?codes=...` | 넓은 후보군 중간 갱신 | DONE |
| POOL | Top300 | `/api/realtime_patch` | 전체 pool 갱신 | DONE |

```text
/api/top100 전체 재조회는 장중 자동 반복 금지 상태다.
장중 실시간 갱신은 patch API 중심이다.
```

---

## 4. 종목표 핵심 필드

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 표시 현재가 | `display_price` | 서버 표시 정책 산출 | DONE/1차 | 화면 표시용 현재가 |
| 표시 등락률 | `display_change_rate` | 서버 표시 정책 산출 | DONE/1차 | 원천 `price` / `change_rate` 덮어쓰기 금지 |
| 표시 일봉 | `display_ohlc` | 서버 표시 정책 산출 | DONE/1차 | 없으면 `realtime_ohlc` / `ohlc` fallback |
| 표시 가격 원천 | `price_source` | 서버 표시 정책 산출 | DONE/1차 | realtime / regular_close_snapshot / aftermarket_realtime / fallback / unavailable |
| 표시 일봉 원천 | `display_ohlc_source` | 서버 표시 정책 산출 | DONE/1차 | display OHLC 원천 |
| patch 표시 진단 | `display_patch_status` | `/api/realtime_patch` 진단 | DONE/진단 | unavailable patch가 표시값 덮는 것을 방지 |
| 정규장 마감 현재가 | `regular_close_price` | RealtimeStore snapshot | DONE/1차 | 15:30 실전 검증 TODO |
| 정규장 마감 등락률 | `regular_close_change_rate` | RealtimeStore snapshot | DONE/1차 | 15:30 실전 검증 TODO |
| 정규장 마감 일봉 | `regular_close_ohlc` | RealtimeStore snapshot | DONE/1차 | 15:30 실전 검증 TODO |
| 순위 | `rank` / `displayed_rank` | 화면 산출 | 산출 | tradable master 필터 후 표시 순위 |
| 원순위 | `original_rank` | ka10032 | DONE | HTS/ka10032 원본 대조 기준 |
| 전일 | `rank_diff` | 화면 산출 | 산출 | 전일 대비 순위 변화 |
| 등급 | `grade` / `candidate_grade_text` | 후보 모델 산출 | DONE/1차 | 화면은 `candidate_grade_text` 우선 |
| 후보점수 | `candidate_score` | 후보 모델 산출 | DONE/1차 | 비율 점수 |
| 후보여부 | `is_candidate` | 후보 모델 산출 | DONE/1차 | Top5 포함 여부 |
| 후보순위 | `candidate_rank` | 후보 모델 산출 | DONE/1차 | 후보5 내부 순위 |
| 후보상태 | `candidate_status` | 후보 모델 산출 | DONE/1차 | READY/WATCH/WEAK |
| 종목명 | `stock_name` | 종목마스터 | DONE |  |
| 종목코드 | `stock_code` | 종목마스터 / normalized code | DONE/보존 | 6자리 문자열 |
| 실시간 원천코드 | `realtime_source_code` | OpenAPI SetRealReg | DONE/보존 | 표시용 실시간은 `_AL` 기준 |
| 수신 원본코드 | `received_code` | OnReceiveRealData | DONE/진단 | `_AL` suffix 유지 확인 |
| 등록 원본코드 | `registered_code` | SetRealReg | DONE/진단 | `_AL` 실제 등록 확인 |
| 정규화코드 | `normalized_code` | Python 정규화 | DONE/진단 | 6자리 Store key |

---

## 5. 실시간 표시 필드

| 화면명 | 내부키 | 데이터 원천 | 상태 | 비고 |
|---|---|---|---|---|
| 현재가 | `price` / `realtime_price` | REST + `_AL` FID10 overlay | DONE | 표시값은 `display_price` 우선 |
| 등락률 | `change_rate` / `realtime_change_rate` | REST + `_AL` FID12 overlay | DONE | 표시값은 `display_change_rate` 우선 |
| 금액(억) | `realtime_acc_trade_value_eok_candidate` | `_AL` FID14 기반 서버 산출 | DONE | HTML 계산 없음 |
| 체결시간 | `fid20_trade_time` | `_AL` FID20 | DONE/보존 | 실시간 지연 판단용 아님 |
| 수신시각 | `received_at` | 서버 수신시각 | DONE | 실시간성 판단 기준 |
| 실시간 sequence | `sequence` | RealtimeStore | DONE | 실시간성 판단 기준 |
| 일봉 | `ohlc` | ka10086 | DONE | base OHLC fallback |
| 실시간 일봉 | `realtime_ohlc` | ka10086 base + realtime tick | DONE | high/low/close 갱신 |
| 외합 원본 | `foreign_sum` | ka10037 | DONE/보존 | 장중 대체 지표 |
| 외인 원본 | `foreign_investor_net` | ka10066 | DONE/보존 | 장마감 후 외국인 순매수 |
| 외합/외인 표시명 | `foreign_display_label` | Python 산출 | DONE | 장중 외합(억), 장마감 후 외인(억) |
| 외합/외인 표시값 | `foreign_display_value` | Python 산출 | DONE | 실제 표시값 |
| 프로(억) | `program_net` | ka90004 | DONE | 429/partial 처리 |
| 잔량비 | `bid_ask_ratio` / `bid_pct` / `ask_pct` | 실시간 호가 우선, 없으면 opt10004 snapshot | DONE | Fast `64/36`, Graphic red/blue 비율 |
| 순간강도 | `realtime_strength` | `_AL` FID228 우선 | DONE | 100 기준 visual-cell |
| 5분강도 | `strength_5m` / `browser_5m_strength` | opt10046 우선, 없으면 브라우저 최근 5분 delta | DONE | fallback은 공식값 아님 표시 |
| 세션강도 | `session_strength` | `_AL` FID15 부호 누적 | DONE | 서버 시작 이후 누적 |
| 정확한 당일강도 | `strength_day` | buy/sell backfill + live 누적 | 보류 | 후속 과제 |
| 큰손 | `big_hand` | 체결 산출 | 예정 | KRT/큰손 연결 후 계산 |
| 큰손리듬 | `krt` | 체결/대량체결 산출 | 예정 | 후속 과제 |

---

## 6. 시장수급 / 미국시장

| 화면명 | 내부키 | 상태 | 비고 |
|---|---|---|---|
| 시장 | `market_name` | DONE |  |
| 지수 | `market_index` | DONE |  |
| 등락률 | `market_change_rate` | DONE |  |
| 상승종목수 | `advancers` | DONE |  |
| 하락종목수 | `decliners` | DONE |  |
| 개인(억) | `individual_eok` | DONE |  |
| 외인(억) | `foreign_spot_eok` | DONE | 시장 전체 외국인 현물 |
| 기관(억) | `institution_eok` | DONE |  |
| 프로(억) | `program_market_eok` | DONE | 시장 전체 프로그램 |
| 외선(억) | `foreign_futures_eok` | 예정 | 향후 최우선 수급 항목 |
| 나스닥 | `us_nasdaq` | 예정 |  |
| QQQ | `us_qqq` | 예정 |  |
| SOXL | `us_soxl` | 예정 | QQQ 다음 표시 |
| SMH | `us_smh` | 예정 |  |
| IBB | `us_ibb` | 예정 |  |
| LIT | `us_lit` | 예정 |  |
| BOTZ | `us_botz` | 예정 |  |

---

## 7. 운영 UI / 런처

| 화면명/기능 | 내부키/파일 | 상태 | 비고 |
|---|---|---|---|
| 통합 라이브 런처 | `stockboard_live.cmd` | DONE | 실행/재시작/상태 확인 |
| Provider | `kiwoom_data_provider.py` | DONE | Kiwoom OpenAPI COM 실시간 수신 |
| 서버 | `stockboard_server.py` | DONE | API 서버 |
| Store | `stockboard_store.py` | DONE | RealtimeStore |
| Engine | `stockboard_engine.py` | DONE/1차 | 후보 모델 정식화 TODO |
| UI | `docs/stockboard_v0_3_0_sample.html` | DONE | render/main loop inline 유지 |
| AHK bridge | `scripts/stockboard_kiwoom_link_v1.ahk` | DONE | HTS 입력 최종값은 6자리 |
| 날짜시간 | `current_datetime` | DONE | KST 현재시각 |
| 통합상태등 | `combined_status_tone` | DONE | A/K/W 통합 표시 |
| 속도 배지 | `update_delay` | DONE | 응답시간 표시용 |
| Fast/Graphic | `display_mode` | DONE | 화면 표시 모드 |
| 밀도 모드 | `displayDensity` | DONE | 기본/압축/초압축 |
| 진단 panel | `debugPanelVisible` | DONE | 운영 기본 hidden/off |

---

## 8. Asset split 상태

| 파일 | 역할 | namespace | 상태 |
|---|---|---|---|
| `docs/assets/stockboard.css` | CSS 전체 | 없음 | DONE |
| `docs/assets/stockboard_format.js` | format helper | `window.StockBoardFormat` | DONE |
| `docs/assets/stockboard_state.js` | state constants | `window.StockBoardState` | DONE |
| `docs/assets/stockboard_visual_cells.js` | visual-cell helper | `window.StockBoardVisualCells` | DONE |
| `docs/assets/stockboard_tooltip.js` | tooltip core helper | `window.StockBoardTooltip` | DONE |
| `docs/assets/stockboard_close_metrics.js` | close metrics helper | `window.StockBoardCloseMetrics` | DONE |
| `docs/assets/stockboard_debug.js` | debug helper | `window.StockBoardDebug` | DONE |
| `docs/assets/stockboard_controls.js` | controls helper | `window.StockBoardControls` | DONE |
| `docs/assets/stockboard_render_helpers.js` | render helper | `window.StockBoardRenderHelpers` | DONE |

보류:

```text
renderBoard / refreshTop100 / loadRealtimePatch / applyRealtimePatchToRow / top100State / rowByCode / main interval loop는 inline 유지한다.
ES module import/export 전환은 장기 보류한다.
월요일 검증 전 추가 대분리는 하지 않는다.
```

---

## 9. 선발기준 설정 파일 예정

| 화면명/개념 | 내부키/경로 | 상태 | 비고 |
|---|---|---|---|
| 선발기준 모델 registry | `candidate_model_registry` | 예정 | 후보 모델 추가/삭제/가중치 변경 관리 |
| 장초반 상승탄력 | `OPENING_MOMO_V01` | 예정 | `configs/candidate_models/OPENING_MOMO_V01.yaml` 후보 |
| 프로그램 수급형 | `PROGRAM_FLOW_V01` | 예정 | `configs/candidate_models/PROGRAM_FLOW_V01.yaml` 후보 |
| 큰손형 | `LARGE_TRADE_V01` | 예정 | `configs/candidate_models/LARGE_TRADE_V01.yaml` 후보 |

```text
초기에는 조작판을 만들지 않는다.
선발기준은 당분간 설정 파일로 관리한다.
설정 파일 방식이 안정화된 뒤 조작판을 만든다.
```

---

## 10. 최종 원칙

```text
REST/TR 데이터
+ OpenAPI 실시간 데이터(_AL 통합 원천)
→ RealtimeStore
→ Engine
→ Server API
→ HTML 표시
```

- 화면 표시 실시간 가격은 `_AL` 통합 원천을 사용한다.
- 내부 식별자는 6자리 코드를 유지한다.
- HTML은 계산하지 않는다.
- Python이 계산한다.
- Store는 저장한다.
- Engine은 판단한다.
- Server는 전달한다.
- HTML은 보여준다.
