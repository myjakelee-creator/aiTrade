# StockBoard Current Status

# DONE / TODO 요약

## DONE

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
DONE 25. CommConnect 로그인
DONE 26. Qt Event Pump
DONE 27. SetRealReg 등록
DONE 28. OnReceiveRealData 연결

## TODO

TODO 29. GetCommRealData 최소 파싱
TODO 30. Tick 저장
TODO 31. 호가 저장
TODO 32. RealtimeStore update
TODO 33. 외선(foreign_futures_eok)
TODO 34. 잔량비 계산
TODO 35. 잔량비 색상바
TODO 36. 1분강도 계산
TODO 37. 1분강도 색상바
TODO 38. 당일강도 계산
TODO 39. 당일강도 색상바
TODO 40. 큰손 계산
TODO 41. KRT 계산
TODO 42. 후보5 실제 선정
TODO 43. Signal Engine
TODO 44. Ranking Engine
TODO 45. Strategy Engine
TODO 46. 미국시장 실데이터
TODO 47. Replay 기능
TODO 48. 시장체온(Market Temperature)

작성 기준: 2026-06-22 현재 코드와 실행 중인 API 응답 기준.

## 1. 현재 완료

- `/api/top100` 응답이 정상 반환된다.
- `/api/market_supply` 응답이 정상 반환된다.
- `/api/realtime_status` 응답이 정상 반환된다.
- `/api/realtime_provider_status` 응답이 정상 반환된다.
- TOP100 row에 `program_net` 값이 결합되어 있다.
- TOP100 row에 `foreign_sum` 값이 결합되어 있다.
- TOP100 row에 `foreign_investor_net` 값이 결합되어 있다.
- TOP100 row에 `foreign_display_label`, `foreign_display_value` 표시용 키가 존재한다.
- 현재 `foreign_display_label`은 `외인(억)`으로 반환된다.
- TOP100 row에 OHLC 데이터가 결합되어 있다.
- OpenAPI realtime provider 상태 조회 API가 존재한다.
- 환경변수 활성화 상태에서 CommConnect 로그인이 완료되었다.
- `login_state`가 `connected`로 확인되었다.
- 주식체결 `SetRealReg` 등록이 성공했다.
- `OnReceiveRealData` 이벤트 연결이 완료되었다.

## 2. 현재 미완료

- `/api/realtime_status` 기준 실시간 quote/event 데이터는 아직 수집되지 않았다.
- `realdata_received_count`는 0이다. 현재 장마감 상태라 0은 허용된다.
- `GetCommRealData` 상세 FID 파싱은 아직 구현되지 않았다.
- `RealtimeStore.update_trade()` 연결은 아직 구현되지 않았다.
- `RealtimeStore.update_orderbook()` 연결은 아직 구현되지 않았다.
- `RealtimeStore.update_foreign_line()` 연결은 아직 구현되지 않았다.
- 호가잔량 실시간 등록은 아직 구현되지 않았다.
- 외선 실시간 등록은 아직 구현되지 않았다.
- Signal, Ranking, Strategy 계산은 아직 구현되지 않았다.

## 3. 현재 동작

- 현재 시장 상태는 `장마감`이다.
- `/api/top100`은 193개 row를 반환한다.
- `program_net`은 전체 193개 row에 존재한다.
- `foreign_sum`은 12개 row에 존재한다.
- `foreign_investor_net`은 191개 row에 존재한다.
- `foreign_investor_net` 값이 존재하므로 표시 라벨은 `외인(억)`으로 전환되어 있다.
- OHLC는 전체 193개 row에 존재한다.
- realtime store는 비어 있는 상태로 조회된다.
- 환경변수 활성화 상태에서 OpenAPI provider가 실행 중이다.
- OpenAPI provider는 QAx control 생성, CommConnect, SetRealReg 등록까지 완료했다.

## 4. 현재 API

- `GET /api/top100`
  - row count: 193
  - OHLC count: 193
  - `program_net` count: 193
  - `foreign_sum` count: 12
  - `foreign_investor_net` count: 191
  - `foreign_display_label`: `외인(억):193`

- `GET /api/market_supply`
  - `market_session`: `장마감`

- `GET /api/realtime_status`
  - `sequence`: 0
  - `updated_at`: null
  - `quote_count`: 0
  - `trade_event_count`: 0
  - `orderbook_event_count`: 0

- `GET /api/realtime_provider_status`
  - `available`: true
  - `running`: true
  - `registered_count`: 193
  - `last_error`: null
  - `last_received_at`: null
  - `qt_ready`: true
  - `control_created`: true
  - `login_requested`: true
  - `login_state`: `connected`
  - `login_error_code`: 0
  - `qt_pump_running`: true
  - `realreg_requested`: true
  - `realreg_succeeded`: true
  - `realreg_error`: null
  - `realreg_screen_count`: 2
  - `realreg_code_count`: 193
  - `realreg_fids`: `10;12;20;15;228;13;14`
  - `realreg_real_type`: `주식체결`
  - `realreg_screens`: `9000`, `9001`
  - `realdata_received_count`: 0
  - `realdata_last_received_at`: null
  - `start_requested`: true
  - `start_succeeded`: true
  - `register_requested`: true
  - `register_succeeded`: true

## 5. 현재 OpenAPI 상태

- QAxWidget 생성 구조가 동작한다.
- Qt event pump가 동작한다.
- CommConnect 요청이 수행되었다.
- `OnEventConnect` 수신으로 `login_state=connected`가 확인되었다.
- TOP100 193개 종목이 주식체결 실시간 등록 대상으로 전달되었다.
- 등록 화면번호는 `9000`, `9001`이다.
- 등록 FID는 `10;12;20;15;228;13;14`이다.
- `SetRealReg` 호출은 Qt pump thread 내부에서 처리된다.
- `OnReceiveRealData`는 연결되었고 수신 카운트만 기록한다.
- 현재 장마감 상태라 `realdata_received_count=0`은 허용된다.
- `GetCommRealData` 호출은 아직 없다.
- RealtimeStore update 연결은 아직 없다.

## 6. 현재 Row Count

- TOP100 row count: 193
- OHLC row count: 193
- `program_net` row count: 193
- `foreign_sum` row count: 12
- `foreign_investor_net` row count: 191
- realtime quote count: 0
- realtime trade event count: 0
- realtime orderbook event count: 0
- realreg code count: 193
- realreg screen count: 2
- realdata received count: 0

## 7. 다음 작업

- 장중에 `realdata_received_count`가 증가하는지 확인한다.
- `OnReceiveRealData`에서 주식체결 FID 값을 읽는 STEP을 별도로 진행한다.
- `GetCommRealData` 파싱 결과를 `RealtimeStore.update_trade()`에 연결하는 STEP을 별도로 진행한다.
- 호가잔량 실시간 등록과 `RealtimeStore.update_orderbook()` 연결은 별도 STEP으로 진행한다.
- `/api/realtime`에 실시간 값이 들어오는지 검증한다.
- Signal, Ranking, Strategy 계산은 실시간 수집 안정화 이후 별도 승인으로 진행한다.

## 8. 금지사항

- 승인 없이 Python 파일을 수정하지 않는다.
- 승인 없이 HTML 파일을 수정하지 않는다.
- 승인 없이 `.env`를 수정하지 않는다.
- 승인 없이 CSV를 수정하지 않는다.
- 승인 없이 이미지 파일을 수정하지 않는다.
- 승인 없이 문서 파일을 추가하거나 수정하지 않는다.
- `GetCommRealData` FID 파싱은 별도 승인 전 추가하지 않는다.
- RealtimeStore update 연결은 별도 승인 전 추가하지 않는다.
- Signal, Ranking, Strategy 계산은 별도 승인 전 추가하지 않는다.
- sample, demo, mock, fallback 값을 실제 화면 표시값으로 주입하지 않는다.
