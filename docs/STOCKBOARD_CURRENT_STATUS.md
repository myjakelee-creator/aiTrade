# StockBoard Current Status

작성 기준: 2026-06-24 현재 코드와 최근 검증 결과 기준.

## 1. 현재 한 줄 요약

StockBoard는 REST/TR 데이터와 OpenAPI 실시간 데이터(`_AL` 통합 원천)를 RealtimeStore에 결합해 `/api/top100`, `/api/realtime`, `/api/realtime_status`, `/api/realtime_provider_status`, `/api/realtime_patch`로 제공하며, 화면은 TOP100 30초 갱신과 realtime_patch 500ms 갱신으로 현재가/등락률/잔량비/순간강도를 표시한다.

## 2. 최종 목표 구조

```text
REST 데이터
+ OpenAPI 실시간 데이터(_AL 통합 원천)
→ RealtimeStore
→ Signal Engine
→ Ranking Engine
→ Strategy Engine
→ Candidate Top5
→ StockBoard
```

운영 원칙:

- HTML은 계산하지 않고 표시한다.
- Python에서 원천값을 정리한다.
- Store는 최신 실시간 상태와 event history를 보존한다.
- Engine은 판단과 계산을 담당한다.
- Server는 API로 전달한다.
- 화면 표시 실시간 가격은 `_AL` 통합 원천을 사용한다.
- 내부 key, API row key, DOM key, Store key, 주문 code, 종목마스터 code는 6자리 종목코드를 유지한다.

## 3. DONE / TODO

### DONE

DONE 01. HTML 기본 화면
DONE 02. TOP100 표시
DONE 03. 후보5 표시 구조
DONE 04. 셀폭 조절 / localStorage
DONE 05. Full Candle / Proportional Candle
DONE 06. ka10032 거래대금 수집
DONE 07. 종목마스터 필터
DONE 08. ka10086 OHLC / VWAP
DONE 09. 휴일 최근 거래일 처리
DONE 10. .env 인증
DONE 11. 시장수급 API
DONE 12. /api/top100 구축
DONE 13. /api/market_supply 구축
DONE 14. Python 4파일 분리
DONE 15. OHLC 반복 렌더링 해결
DONE 16. program_net 안정 연결
DONE 17. foreign_sum 연결
DONE 18. foreign_investor_net 연결
DONE 19. 외합(억) ↔ 외인(억) 전환
DONE 20. RealtimeStore 구축
DONE 21. /api/realtime 구축
DONE 22. /api/realtime_status 구축
DONE 23. /api/realtime_provider_status 구축
DONE 24. QAxWidget 생성
DONE 25. CommConnect 로그인 / OnEventConnect connected 확인
DONE 26. Qt Event Pump 안정화
DONE 27. SetRealReg 등록
DONE 28. OnReceiveRealData 연결
DONE 29. GetCommRealData 최소 파싱
DONE 30. Tick 저장
DONE 31. 호가 저장
DONE 32. RealtimeStore update 연결
DONE 33. /api/realtime_patch 구축
DONE 34. DOM realtime patch 연결
DONE 35. Kiwoom OpenAPI 대체거래소 KRX/NXT/AL 규칙 문서화
DONE 36. 6자리 / _AL / _NX SetRealReg 실험
DONE 37. 표시용 실시간 가격 원천 _AL 통합 전환
DONE 38. 실시간 가격/지연 진단 완료
DONE 39. TOP100 polling 부하 완화
DONE 42. 잔량비 계산
DONE 43A. 잔량비 realtime_patch 화면 연결
DONE 44. 순간강도 realtime_patch 화면 연결

### TODO

TODO 40. /api/realtime_patch payload 경량화
TODO 41. 외선(foreign_futures_eok)
TODO 43B. 잔량비 표시 디테일
TODO 45. 순간강도 색상바
TODO 46. 당일강도 계산
TODO 47. 당일강도 색상바
TODO 48. 큰손 계산
TODO 49. KRT 계산
TODO 50. 후보5 실제 선정
TODO 51. Signal Engine
TODO 52. Ranking Engine
TODO 53. Strategy Engine
TODO 54. 미국시장 실데이터
TODO 55. Replay 기능
TODO 56. 시장체온(Market Temperature)

## 4. 현재 완료 상태

### 기본 화면 / REST 데이터

- `/api/top100` 응답이 정상 반환된다.
- `/api/market_supply` 응답이 정상 반환된다.
- TOP100 row에 `program_net` 값이 결합되어 있다.
- TOP100 row에 `foreign_sum` 값이 결합되어 있다.
- TOP100 row에 `foreign_investor_net` 값이 결합되어 있다.
- TOP100 row에 `foreign_display_label`, `foreign_display_value` 표시 구조가 존재한다.
- TOP100 row에 OHLC, VWAP, 전일 고가/저가/종가 데이터가 결합되어 있다.

### OpenAPI provider

- QAxWidget 생성 구조가 동작한다.
- Qt event pump가 동작한다.
- CommConnect 요청과 OnEventConnect 수신으로 `login_state=connected`가 확인되었다.
- SetRealReg 등록이 성공한다.
- OnReceiveRealData 이벤트 연결이 완료되었다.
- GetCommRealData 최소 파싱이 완료되었다.
- 주식체결 최소 FID는 `10`, `12`, `20`, `15`, `228`, `13`, `14` 중심으로 사용한다.
- Tick 저장과 호가 저장이 RealtimeStore에 연결되었다.
- 주식체결과 주식호가잔량 모두 `_AL` 등록 코드 기준으로 수신한다.

### RealtimeStore / API / 화면

- RealtimeStore가 구축되었다.
- `RealtimeStore.update_trade()`가 연결되었다.
- `RealtimeStore.update_orderbook()`이 연결되었다.
- `bid_ask_ratio = bid_volume / ask_volume` 계산이 연결되었다.
- 잔량비는 최신 실시간 호가잔량 순간값 기준이며 1분 평균이 아니다.
- Store key는 6자리 종목코드를 유지한다.
- `/api/realtime` 응답이 정상 반환된다.
- `/api/realtime_status` 응답이 정상 반환된다.
- `/api/realtime_provider_status` 응답이 정상 반환된다.
- `/api/realtime_patch` 응답이 정상 반환된다.
- DOM realtime patch가 현재가/등락률/잔량비/순간강도 셀에 연결되었다.
- OpenAPI FID 228 `execution_strength_raw`가 Store `execution_strength`로 저장되고 API `realtime_strength`로 노출된다.
- 순간강도는 최신 실시간 체결강도 순간값 기준이며 1분 평균이 아니다.
- top100 refresh가 실시간 가격/등락률/호가 셀을 덮지 않도록 `preserveRealtimeFields()` 보존 로직이 들어갔다.

### 대체거래소 / 가격 원천

- Kiwoom OpenAPI 대체거래소 규칙을 문서화했다.
- KRX는 6자리 종목코드, NXT는 `6자리_NX`, 통합(AL)은 `6자리_AL`로 구분한다.
- 6자리 / `_AL` / `_NX` SetRealReg 실험을 완료했다.
- SetRealReg에 `_AL` / `_NX` 직접 등록 가능함을 확인했다.
- OnReceiveRealData `received_code`가 suffix를 유지함을 확인했다.
- `registered_code == received_code` 관계를 확인했다.
- 표시용 실시간 가격 원천은 `_AL` 통합 코드로 전환했다.
- 가격 원천 문제는 `_AL` 전환으로 해결됐다.
- 최근 관찰 기준 키움 거래대금상위(통합) 전광판과 StockBoard 가격 차이는 대체로 0~1틱 또는 조회 타이밍 차이 수준이다.

## 5. 현재 API / 실시간 상태

### `GET /api/top100`

- TOP100 rows: 최근 검증 기준 약 182개
- `stock_code`: 6자리 유지
- `price`, `realtime_price`: 실시간 tick이 있으면 `_AL` 통합 실시간 값 overlay
- `rest_price`: REST/TR 원본 가격 보존
- `received_code`, `registered_code`, `realtime_source_code`: `_AL` suffix 보존 가능
- `program_net`, `foreign_sum`, `foreign_investor_net`, OHLC 결합 유지
- `bid_ask_ratio`는 최신 실시간 호가 overlay 값이 있으면 결합된다.

### `GET /api/realtime`

- 요청 code는 6자리 종목코드를 사용한다.
- 응답 quote key는 6자리 종목코드 기준이다.
- quote 내부 원천 코드 필드는 `_AL` suffix를 보존한다.
- trade/orderbook event history는 유지된다.
- quote에는 `bid_volume`, `ask_volume`, `bid_ask_ratio`가 포함된다.

### `GET /api/realtime_status`

- `sequence`, `updated_at`, `quote_count`, `trade_event_count`, `orderbook_event_count`를 제공한다.
- 실시간 갱신 여부는 `sequence`와 `updated_at` 기준으로 판단한다.

### `GET /api/realtime_provider_status`

- QAxWidget, Qt pump, 로그인, SetRealReg, OnReceiveRealData 상태를 제공한다.
- 등록 입력 코드, 정규화 코드, SetRealReg 실제 등록 코드, raw → normalized map 샘플을 확인할 수 있다.
- 주식체결/주식호가잔량 마지막 sample과 seen code 진단 필드를 제공한다.
- suffix 실험과 진단 screen은 기본 실행에서 OFF 상태여야 한다.

### `GET /api/realtime_patch`

- 초기 또는 fallback 시 full patch를 반환한다.
- 이후에는 `since_sequence` 기반 delta patch를 반환한다.
- 최근 검증 기준 delta row에는 `price`, `change_rate`, `realtime_strength`, `realtime_acc_volume`, `realtime_acc_trade_value`, `bid_volume`, `ask_volume`, `bid_ask_ratio`가 포함된다.
- 현재가/등락률/잔량비/순간강도는 HTML `applyRealtimePatchToRow()`에서 500ms patch 경로로 셀을 갱신한다.
- 순간강도는 `/api/top100` 호출 없이 `/api/realtime_patch`만 20초 확인한 결과 delta 호출 26회, delta row 2,179개, `realtime_strength` 포함 row 2,179개로 검증되었다.
- 시장세션, TOP100 필터, 거래대금 조회 결과, 실시간 이벤트 수신 상태에 따라 row 수와 payload는 변동 가능하다.

## 6. _AL 통합 실시간 가격 원천 규칙

```text
row.stock_code = 005930
Store key = 005930
API row key = 005930
DOM dataset stockCode = 005930
주문 code = 005930
종목마스터 code = 005930

realtime_source_code = 005930_AL
received_code = 005930_AL
registered_code = 005930_AL
normalized_code = 005930
```

- 화면 row key는 기존 6자리 `stock_code`를 유지한다.
- Store key는 6자리 normalized code를 유지한다.
- 주문/종목마스터/DOM key에는 `_AL`을 전파하지 않는다.
- 실시간 표시 원천은 `_AL` 통합 코드다.
- 주식체결과 주식호가잔량 모두 `_AL` 등록 대상으로 맞춰져 있다.
- `_AL` source code는 quote/event 내부 진단 필드로 보존한다.
- `_NX`는 표시 원천으로 전환하지 않는다.

## 7. 운영 기준

- `/api/top100` 자동 갱신: 30000ms
- `/api/realtime_patch` 자동 갱신: 500ms
- TOP100 refresh는 순위/구성/거래대금/기본 데이터 갱신용이다.
- realtime_patch는 현재가/등락률/잔량비/순간강도/호가/실시간 값 갱신용이다.
- `preserveRealtimeFields()`로 top100 refresh가 현재가/등락률/잔량비/순간강도/호가 실시간 셀을 덮는 위험을 완화한다.
- FID20은 실시간 지연 판단용이 아니라 체결 원천시각 보존용이다.
- 실시간성 판단은 `received_at`과 `sequence` 기준이다.
- suffix 실험 screen 9100/9110/9120은 기본 실행에서 OFF다.
- 주식예상체결/시간외단일가 진단 screen 9020/9030도 기본 실행에서 OFF다.

최근 검증 기준:

```text
TOP100 rows ≈ 173
registered_count ≈ 173
realtime_patch delta rows = 변경 row 수에 따라 변동
realtime_patch refresh = 500ms
TOP100 refresh = 30000ms
```

위 수치는 시장세션, 필터, 거래대금 조회 결과, 실시간 수신 상태에 따라 변동 가능하다. 문서에서는 고정 성공 기준으로 사용하지 않는다.

## 8. 컬럼별 갱신 경로 감사

최근 감사 기준:

- 현재가/등락률/잔량비는 `/api/realtime_patch` 500ms 경로로 화면 셀이 갱신된다.
- 잔량비는 `bid_ask_ratio = bid_volume / ask_volume`이며 최신 실시간 호가잔량 순간값이다.
- 금액(억)은 현재 `/api/top100` 30초 경로다. `realtime_acc_trade_value` 기반 실시간화 후보로 남긴다.
- 일봉 캔들은 `/api/top100`/초기 OHLC 기반이다. 실시간화는 별도 검토가 필요하다.
- 기존 화면명 `1분강도`는 다음 작업에서 `순간강도`로 변경을 검토한다.
- 순간강도 원천 후보는 `/api/realtime_patch`의 `realtime_strength`다.
- 1분 평균강도는 최신 실시간 체결강도와 별도 지표로 나중에 추가한다.
- 외합(억)과 프로(억)는 TR 기반이므로 30초 또는 별도 주기 유지가 가능하다.
- 시장수급 패널은 화면에서 5초마다 `/api/market_supply`를 호출하지만 backend 값은 서버 시작 시 수집값에 가까우므로 별도 재수집 설계가 필요하다.
- 큰손/모멘텀은 backend 계산이 아직 미구현이다.

## 9. 다음 작업

1순위 작업은 잔량비 표시 디테일(TODO 43B) 또는 순간강도 계산(TODO 44)이다.

TODO 43B 잔량비 표시 디테일:

- 색상 방향
- tooltip 문구
- 극단값 표시/클램프
- 디자인 디테일

그 다음 작업 순서:

1. 외선(foreign_futures_eok)
2. 잔량비 표시 디테일(TODO 43B)
3. 순간강도 계산 / 순간강도 색상바
4. 당일강도 계산 / 당일강도 색상바
5. 큰손 계산
6. KRT 계산
7. 후보5 실제 선정
8. Signal Engine
9. Ranking Engine
10. Strategy Engine
11. 미국시장 실데이터
12. Replay 기능
13. 시장체온(Market Temperature)

## 10. 금지사항

- 승인 없이 Python 파일을 수정하지 않는다.
- 승인 없이 HTML 파일을 수정하지 않는다.
- 승인 없이 `.env`를 수정하지 않는다.
- 승인 없이 CSV를 수정하지 않는다.
- 승인 없이 이미지 파일을 수정하지 않는다.
- 승인 없이 다른 문서를 수정하지 않는다.
- `_AL` 통합 전환을 되돌리거나 `_NX` 표시 원천 전환을 하지 않는다.
- Store key를 `_AL` 또는 composite key로 바꾸지 않는다.
- 주문/종목마스터/DOM key에 `_AL` suffix를 전파하지 않는다.
- 가격 계산식에 임의 보정을 추가하지 않는다.
- Signal, Ranking, Strategy 계산은 별도 승인 전 진행하지 않는다.
- sample, demo, mock, fallback 값을 실제 화면 표시값으로 주입하지 않는다.

## 11. 변경 이력

- 2026-06-22: StockBoard 기본 화면, TOP100, 시장수급, OHLC/VWAP, OpenAPI provider 초기 상태 정리.
- 2026-06-23: Tick 저장, 호가 저장, RealtimeStore update, `/api/realtime_patch`, DOM realtime patch 상태 반영.
- 2026-06-23: KRX/NXT/AL 규칙 문서화, 6자리 / `_AL` / `_NX` SetRealReg 실험 결과 반영.
- 2026-06-23: 표시용 실시간 가격 원천을 `_AL` 통합 코드로 전환한 상태 반영.
- 2026-06-23: TOP100 polling 30000ms, realtime_patch 500ms 운영 기준과 payload 경량화 TODO 반영.
- 2026-06-23: `STOCKBOARD_CODING_ROADMAP_v2.1_UPDATED.md` 핵심 내용을 통합해 `STOCKBOARD_CURRENT_STATUS.md`를 마스터 상태 문서로 정리.
- 2026-06-24: 잔량비 계산(TODO 42)과 잔량비 realtime_patch 화면 연결(TODO 43A) 완료 상태 반영.
- 2026-06-24: 전체 컬럼 갱신 경로 감사 결과와 순간강도 작업 기준을 반영.
