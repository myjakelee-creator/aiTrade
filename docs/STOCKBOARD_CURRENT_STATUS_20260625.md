# StockBoard Current Status

작성 기준: 2026-06-25 후보5 v0.1, 원클릭 라이브 런처, UI 밀도/정렬 개선 반영.

## 1. 현재 한 줄 요약

StockBoard는 REST/TR 데이터와 OpenAPI 실시간 데이터(`_AL` 통합 원천)를 RealtimeStore에 결합해 `/api/top100`, `/api/realtime`, `/api/realtime_status`, `/api/realtime_provider_status`, `/api/realtime_patch`로 제공하며, 후보5 v0.1 점수모델과 500ms realtime_patch로 현재가/등락률/금액(억)/일봉/잔량비/순간강도/세션강도를 표시한다.

## 1A. 2026-06-25 핵심 갱신

- 후보5 v0.1(`CANDIDATE_V0_1_RANK_GAP_OPEN`) 적용: 거래대금 순위 60점 + 전일 대비 순위차 40점, `candidate_score_max`는 가변 총점 구조.
- `candidate_score`, `candidate_grade_text`, `candidate_status`, `candidate_rank`, `trend_ok`, `momentum`을 `/api/top100` row에 포함.
- `start_stockboard_live.cmd`와 `scripts/start_stockboard_live.ps1` 원클릭 라이브 런처 추가.
- 루트 URL(`/`) 접속 시 전광판 HTML로 redirect.
- 일봉 셀 클릭 캔들모드 전환, 하단 헤더 정렬/역정렬, 밀도 모드, 폭 초기화, 상단 레이아웃 압축 적용.
- 상·하한가 종목은 `price_limit_state`로 잔량비/순간강도/세션강도 표시 예외 처리.

## 1B. 2026-06-25 가격/장마감/suffix 진단 WIP

상태: WIP / 내일 정규장 검증 필요.

- 애프터마켓 가격은 HTS와 비교적 잘 맞는 것으로 관찰했다.
- 정규장 동시호가 중 가격도 대체로 맞는 것으로 관찰했다.
- 15:30 정규장 마감 직후 가격 불일치가 다시 나타났다.
- 현재 판단은 FID10/FID12 가격 파싱 회귀보다 `regular_close_snapshot`과 장상태별 `price_source` 선택 정책 부재 가능성이 크다.
- `/api/top100` 자체는 정상이며 root 배열 189개 row로 응답한다.
- `/api/top100`은 `{rows: [...]}` 객체가 아니라 list root 구조다. PowerShell 빈 표시는 API 문제가 아니라 파싱 가정 문제였다.
- `/api/top100` row의 `stock_code`는 6자리 운영 key를 유지한다.
- `/api/top100` row의 `realtime_source_code`는 `_AL` suffix 원천을 유지한다.
- 운영 key 원칙은 유지된다. Store/API/DOM/주문 key는 6자리, 실시간 원천 진단 필드는 `_AL` 보존이다.
- `kiwoom_data_provider.py`에는 suffix Store 오염 방지 보완 패치가 들어간 상태지만 OFF -> ON -> OFF 런타임 검증이 최종 성공하지 않았다.
- 해당 Python 수정은 커밋 예정 WIP 코드이며, DONE으로 이동하지 않는다.

가격 불가침 원칙:

- FID10 현재가 normalize 로직을 임의 수정하지 않는다.
- FID12 등락률 normalize 로직을 임의 수정하지 않는다.
- `price`/`change_rate`를 후보5, 등급, 모멘텀 계산에서 덮어쓰지 않는다.
- HTML에서 가격을 계산하지 않는다.
- 가격 불일치를 보정식으로 해결하지 않는다.
- 표시 가격은 반드시 source가 명시된 서버 산출값만 사용한다.
- 장마감/애프터마켓 문제는 가격 보정이 아니라 source 선택 정책으로 해결한다.
- 후보5/등급/모멘텀은 가격을 읽기만 하고 원천값을 변경하지 않는다.

suffix Store 오염 방지 WIP:

- 의도: suffix 진단 ON 상태에서 6자리 KRX / `_NX` sample이 운영 `RealtimeStore`를 덮지 않도록 분리한다.
- 운영 `_AL` tick은 기존 Store update path를 유지한다.
- 애프터마켓에서는 KRX 6자리 sample이 없을 수 있어 9개 sample 필수 기준은 부적절하다.
- 내일 정규장 중 OFF -> ON -> OFF 검증을 재시도한다.
- 검증 성공 전에는 이 항목을 DONE으로 이동하지 않는다.

## 1C. 2026-06-25 StockBoard UI 선택 / HTS 연동 / 통합 런처 완료

상태: DONE / 대표 PC 실행 테스트 완료.

### StockBoard 종목 선택 UI

- 종목명 클릭 시 선택 종목을 `activeStockCode`로 저장하고 active 표시를 적용한다.
- active row/name은 배경 강조로 표시하며 기존 active 표시는 제거된다.
- 후보5와 하단 거래대금 상위 종목표 모두 종목명 클릭으로 같은 선택 로직을 사용한다.
- 클립보드에는 `005930`, `005930_AL`, `005930_NX` 패턴만 저장 가능하다.
- HTML은 `_AL`, `_NX` suffix를 HTS 입력용으로 강제 제거하지 않는다.
- Up/Down 키 이동은 하단 표시 순서 기준으로 한 행씩 이동한다.
- 정렬/역정렬, `/api/top100` refresh, `/api/realtime_patch` 갱신 이후에도 같은 `stock_code`가 있으면 active 상태를 복원한다.
- 가격/등락률/거래대금/후보점수/등급/모멘텀 계산은 변경하지 않았다.
- realtime_patch / 스트리밍 표시 경로는 변경하지 않았다.

### 영웅문 HTS 연동

- 최종 AHK bridge는 `scripts/stockboard_kiwoom_link_v1.ahk`이다.
- AutoHotkey v1을 사용하며, 영웅문 HTS 제어를 위해 관리자 권한 실행이 운영 기준이다.
- 관리자 실행 보조 파일은 `scripts/run_stockboard_kiwoom_link_v1_admin.cmd`이다.
- Window Spy 기준 운영 식별값은 `ahk_class _NKHeroMainClass`, 대상 컨트롤은 `Edit6`이다.
- AHK는 클립보드의 `005930`, `005930_AL`, `005930_NX`만 유효 입력으로 처리한다.
- AHK가 HTS 입력 직전에 `_AL`, `_NX` suffix를 제거하며, HTS `Edit6`에는 항상 최종 6자리 코드만 입력한다.
- `ControlSetText` 후 `ControlGetText` readback으로 성공/실패를 구분한다.
- 빠른 Up/Down 조작 중 Windows 우측 하단 성공 알림이 누적되지 않도록 성공 알림은 운영 기본 OFF이다.
- 실패 알림은 유지한다.
- 일반 권한 실행은 `Edit6 set failed`가 확인되어 운영에서 제외한다.

### 통합 시작/종료 런처

- 대표 시작 파일은 `start_stockboard_live.cmd`이다.
- 시작 본체는 `scripts/start_stockboard_live.ps1`이다.
- 시작 흐름은 서버 실행, OpenAPI provider 준비, 브라우저 열기, AHK v1 관리자 bridge 실행이다.
- realtime `quote_count`, `realtime_patch` rows가 0이어도 장상태/체결 부재 상황에서는 fatal이 아니라 warning으로 처리한다.
- 시작 런처는 정상 완료 또는 warning 완료 시 자동으로 닫힌다.
- 서버 PowerShell window PID는 `data/runtime/stockboard_server_window.pid`에 저장한다.
- 대표 종료 파일은 `stop_stockboard_live.cmd`이다.
- 종료 흐름은 AHK bridge 종료, 8000번 StockBoard 서버 종료, 저장된 server PowerShell window PID 기준 로그 창 종료이다.
- 브라우저는 자동 종료하지 않는다.
- 종료 런처는 정상 완료 시 자동으로 닫히며, 오류/위험 상황에서만 pause를 유지한다.

### scripts 폴더 정리

현재 `scripts` 폴더 운영 파일:

```text
scripts/start_stockboard_live.ps1
scripts/stockboard_kiwoom_link_v1.ahk
scripts/run_stockboard_kiwoom_link_v1_admin.cmd
```

삭제된 실패/중복 파일:

```text
scripts/stockboard_kiwoom_link.ahk
scripts/run_stockboard_kiwoom_link_v1.cmd
```

정책:

- `scripts/_zz_`는 생성하지 않는다.
- 실패 AHK 파일은 보관하지 않고 삭제한다.
- `start_stockboard_full.cmd`, `stop_stockboard_full.cmd` 같은 full launcher 이름은 추가 생성하지 않는다.

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
DONE 45. 금액(억) realtime_patch 화면 연결
DONE 46. 순간강도 색상바 스케일 개선
DONE 47. 세션강도 realtime_patch 화면 연결
DONE 48B. 일봉 `realtime_ohlc` backend/API 계산 및 노출
DONE 48C. 일봉 `cells[7]` realtime_patch 화면 연결 및 custom tooltip
DONE 59. `/api/top100_filter_report` 진단 API
DONE 60. 순위 셀 `original_rank` tooltip
DONE 61. 후보5 v0.1 점수모델 / candidate fields
DONE 62. 32bit Python realtime Provider 원클릭 런처
DONE 63. 루트 URL 전광판 redirect
DONE 64. 일봉 클릭 캔들모드 전환
DONE 65. 하단 종목표 헤더 정렬/역정렬
DONE 66. 전광판 밀도 모드 / 강제 열폭 / 폭 초기화
DONE 67. 상·하한가 잔량비·강도 표시 예외처리
DONE 68. 상단 상태등 통합 / 갱신지연 숫자표시
DONE 69. 상단 시장정보·파이그래프 레이아웃 압축
DONE 70. 일봉·잔량비 tooltip 문구/폰트 정리

2026-06-25 추가 완료:

DONE 83. 종목명 클릭 active 표시
DONE 84. 클립보드 종목코드 저장
DONE 85. Up/Down keyboard navigation
DONE 86. 영웅문 HTS AHK v1 관리자 연동
DONE 87. AHK 성공 알림 OFF / 실패 알림 유지
DONE 88. 통합 시작 런처
DONE 89. 통합 종료 런처
DONE 90. scripts 폴더 중복 AHK 파일 정리
DONE 91. StockBoard 단일 live launcher / AHK bridge control
DONE 92. Price Fast Mode / realtime 100종목 제한
DONE 93. stale tick drop 진단
DONE 94. hybrid orderbook hot5 + rotate20
DONE 95. Graphic/Fast display mode
DONE 96. visual-cell 경량 렌더링
DONE 97. E palette visual cells
DONE 98. visual-cell tooltip native title 제거
DONE 99. 5분강도 헤더/tooltip 정리
DONE 100. 브라우저 수신 기준 최근 5분강도 임시 계산
DONE 101. opt10046 close_5m_strength 장마감 조회/저장
DONE 107. opt10004 orderbook snapshot 장마감 잔량비 조회/저장
DONE 108. Direct API Debug panel 운영 toggle

### TODO

TODO 40. /api/realtime_patch payload 경량화
TODO 41. 외선(foreign_futures_eok)
TODO 48. 정확한 당일강도 계산(backfill 원천 확보 후)
TODO 49. 정확한 당일강도 색상바
TODO 50. 큰손 계산
TODO 51. KRT 계산
TODO 53. Signal Engine 정식화
TODO 54. Ranking Engine 정식화
TODO 55. Strategy Engine 정식화
TODO 56. 미국시장 실데이터
TODO 57. Replay 기능
TODO 58. 시장체온(Market Temperature)
TODO 71. 후보 선발기준 모델 문서화
TODO 72. 후보 선발기준 레고블럭 구조
TODO 73. 후보5 v0.1 장중 관찰/평가 및 v0.2 판단
TODO 74. 영웅문 HTS 종목 동기화 조사
TODO 75. 공식 TODO 문서 병합 갱신
TODO 76. suffix 진단 Store 오염 방지 패치 정규장 검증
TODO 77. 정규장 가격 대조
TODO 78. 15:30 regular_close_snapshot 구조 설계/구현
TODO 79. 15:30~15:40 regular_close_snapshot 고정 표시
TODO 80. 15:40 이후 aftermarket_realtime / regular_close_snapshot fallback 정책
TODO 81. price_source / display_price / display_change_rate 필드 정식화
TODO 82. 실시간 렌더링 최적화, 변경된 셀만 그리기, 일봉/색상바 throttle
TODO 102. HTML module split
TODO 104. regular_close_snapshot
TODO 105. price_source/display_price/display_change_rate 정식화
TODO 106. realtime_patch payload delta 경량화

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
- DOM realtime patch가 현재가/등락률/금액(억)/잔량비/순간강도/세션강도 셀에 연결되었다.
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
- `_AL` 전환으로 장중/애프터마켓 실시간 원천은 대체로 맞아졌으나, 15:30 정규장 마감 직후에는 가격 불일치가 다시 관찰됐다.
- 현재 가격 관련 작업은 WIP이며, 내일 정규장 검증과 장상태별 source 정책 설계가 필요하다.
- 가격 불일치는 보정식이 아니라 `regular_close_snapshot`, `aftermarket_realtime`, `price_source` 선택 정책으로 해결한다.

### 후보5 v0.1 / 운영·UI 개선

- 후보5는 더 이상 거래대금 상위 5개 단순 복사가 아니라 `candidate_rank` 기준으로 표시된다.
- v0.1 점수는 현재 거래대금 표시 순위와 전일 대비 순위차만 사용한다.
- `trend_ok`는 현재가가 시가 위인지 여부로 점수와 분리해 표시한다.
- 원클릭 런처는 8000번 LISTEN PID만 종료하고 32bit Python + realtime 환경변수로 서버를 재가동한다.
- 상단 통합 상태등, 밀도 모드, 폭 초기화, 헤더 정렬, 일봉 클릭 전환, 상·하한가 표시 예외처리가 적용됐다.

## 5. 현재 API / 실시간 상태

### `GET /api/top100`

- TOP100 rows: 2026-06-25 진단 기준 root 배열 189개
- 응답 구조: `{rows: [...]}`가 아니라 list root
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
- 최근 검증 기준 delta row에는 `price`, `change_rate`, `realtime_strength`, `realtime_acc_volume`, `realtime_acc_trade_value`, `realtime_acc_trade_value_eok_candidate`, `bid_volume`, `ask_volume`, `bid_ask_ratio`가 포함된다.
- 현재가/등락률/금액(억)/일봉/잔량비/순간강도/세션강도는 HTML `applyRealtimePatchToRow()`에서 500ms patch 경로로 셀을 갱신한다.
- 순간강도는 `/api/top100` 호출 없이 `/api/realtime_patch`만 20초 확인한 결과 delta 호출 26회, delta row 2,179개, `realtime_strength` 포함 row 2,179개로 검증되었다.
- 금액(억)은 `/api/top100` 호출 없이 `/api/realtime_patch`만 20초 확인한 결과 delta 호출 27회, delta row 1,424개, `realtime_acc_trade_value_eok_candidate` 포함 row 1,424개로 검증되었다.
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
- realtime_patch는 현재가/등락률/금액(억)/잔량비/순간강도/세션강도/호가/실시간 값 갱신용이다.
- `preserveRealtimeFields()`로 top100 refresh가 현재가/등락률/금액(억)/잔량비/순간강도/세션강도/호가 실시간 셀을 덮는 위험을 완화한다.
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

- 현재가/등락률/금액(억)/일봉/잔량비/순간강도/세션강도는 `/api/realtime_patch` 500ms 경로로 화면 셀이 갱신된다.
- 금액(억)은 `/api/realtime_patch`의 `realtime_acc_trade_value_eok_candidate`를 그대로 표시한다.
- HTML에서 새 거래대금 계산을 하지 않고 서버가 내려주는 억 단위 후보값을 사용한다.
- 초기 렌더는 `realtime_acc_trade_value_eok_candidate`를 우선 사용하고, `applyRealtimePatchToRow()`는 `cells[6]` 금액(억) 셀을 즉시 갱신한다.
- `preserveRealtimeFields()`는 `amount`, `realtimeAccTradeValueEokCandidate`, `realtime_acc_trade_value_eok_candidate`를 보존한다.
- 금액(억) realtime_patch 연결 단계에서는 순위 재정렬, 후보5 재선정, 금액 기준 정렬 재계산, backend/API 구조 변경을 하지 않았다.
- 순위/후보5는 추후 Ranking Engine 또는 별도 재정렬 단계에서 처리한다.
- 잔량비는 `bid_ask_ratio = bid_volume / ask_volume`이며 최신 실시간 호가잔량 순간값이다.
- 순간강도는 OpenAPI FID 228 `execution_strength_raw` → Store `execution_strength` → API `realtime_strength` 경로를 사용한다.
- 화면 헤더는 `1분강도`에서 `순간강도`로 변경되었고 초기 렌더에서 `realtime_strength`를 우선 사용한다.
- `applyRealtimePatchToRow()`는 `cells[9]` 순간강도 셀을 realtime_patch 경로로 즉시 갱신한다.
- 순간강도 숫자는 `realtime_strength` 원값을 그대로 표시하고, 바 표시만 0~200 기준으로 clamp한다.
- 순간강도 바는 100을 균형으로 두고 100 미만은 매도 체결 우위, 100 초과는 매수 체결 우위로 표시한다.
- 순간강도 바는 0 이하 매도 포화, 200 이상 매수 포화로 처리한다.
- `instantStrengthPosition(value)`를 추가해 gamma 0.6 비선형 스케일로 100 근처 변별력을 강화했다.
- `minuteStrengthView()`만 새 스케일을 사용하고 세션강도는 기존 `minuteStrengthPosition()` 스케일을 유지한다.
- 순간강도 색상바 개선 단계에서는 backend/API 수정, 당일강도 구현, 현재가/등락률/금액(억)/잔량비 경로 변경을 하지 않았다.
- 과거 `strength_1m`, `minute_strength`, `1분강도` key는 payload 호환 fallback으로만 유지한다.
- 세션강도는 서버 시작 이후 실시간 FID15 `trade_qty` 부호 누적 기반 1차 지표다.
- `trade_qty > 0`은 `session_buy_qty_live`, `trade_qty < 0`은 `session_sell_qty_live`에 절댓값으로 누적한다.
- `session_strength = session_buy_qty_live / session_sell_qty_live * 100`이며 `sell == 0`이면 1차 구현에서는 `None` 처리한다.
- Store quote에는 `session_buy_qty_live`, `session_sell_qty_live`, `session_strength`, `session_strength_source = live_since_server_start`를 보존한다.
- `/api/realtime_patch`, `/api/top100` overlay, `/api/realtime`은 세션강도 4개 필드를 노출한다.
- HTML은 기존 `cells[10]`을 `세션강도`로 표시하고 `applyRealtimePatchToRow()`에서 즉시 갱신한다.
- 세션강도 tooltip은 매수체결량, 매도체결량, 계산식, 서버 시작 이후 누적 기준, 정식 당일 backfill 미포함 주의를 표시한다.
- 세션강도는 정확한 당일강도가 아니며 서버 재시작 시 초기화된다.
- 세션강도 런타임 검증은 운영 서버 PID 22288 기준으로 완료했다.
- 검증 시 provider는 `registered_count=174`, `realdata_received_count=22304`, `trade_seen_codes_count=135`, `orderbook_seen_codes_count=136` 상태였다.
- 45초 동안 `/api/realtime_patch` delta 호출 36회, 전체 delta rows 2,030개, 대상 5종목 delta rows 180개를 관찰했다.
- 대상 5종목 delta rows 180개 모두 `session_strength`를 포함했다.
- 같은 관찰 구간에서 buy 누적 증가 134회, sell 누적 증가 128회를 확인했다.
- 000660, 005930, 402340, 005380, 009150 모두 `session_strength = session_buy_qty_live / session_sell_qty_live * 100` 계산이 일치했다.
- `/api/top100` overlay도 세션강도 필드를 포함하며, top100 내부 값 기준 계산 일치가 확인되었다. `/api/realtime`과 `/api/top100`을 순차 호출할 때는 활발한 종목에서 호출 시점 차이로 값이 어긋날 수 있다.
- OPT10084는 호출 가능하고 `_AL` 입력도 가능하지만 현재 샘플 기준 `cntr_trde_qty`가 unsigned이고 `sign`은 전일대비 기호로 보여 buy/sell base 원천으로는 부족하다.
- 정확한 당일강도는 `day_buy_qty_base`, `day_sell_qty_base`, `session_buy_qty_live`, `session_sell_qty_live`, `strength_day` 구조로 확장 가능한 후속 과제로 보류한다.
- 일봉 캔들은 ka10086 base OHLC를 저장하고, realtime tick price로 `high`/`low`/`close`를 갱신하는 `realtime_ohlc` 기준을 사용한다. `open`, `prev_high`, `prev_close`, `prev_low`, `vwap`은 base 값을 유지하며 `vwap_source="base"`다.
- `/api/realtime_patch`와 `/api/top100` overlay는 `realtime_ohlc`를 노출한다. HTML `cells[7]`은 `realtime_ohlc`가 있으면 우선 표시하고, 없으면 기존 `ohlc`로 fallback한다.
- 일봉 tooltip은 native `title`을 제거하고 custom tooltip을 사용한다. 시가/고가/저가/종가/현재와 전일종가 대비 등락률을 표시하며, hover 중 realtime 갱신 시 tooltip 내용만 최신 값으로 갱신한다.
- 1분 평균강도는 최신 실시간 체결강도와 별도 지표로 나중에 추가한다.
- 외합(억)과 프로(억)는 TR 기반이므로 30초 또는 별도 주기 유지가 가능하다.
- 시장수급 패널은 화면에서 5초마다 `/api/market_supply`를 호출하지만 backend 값은 서버 시작 시 수집값에 가까우므로 별도 재수집 설계가 필요하다.
- 큰손/KRT는 아직 미구현이다. 후보5 v0.1용 `momentum`은 구현됐으며, 정식 Strategy momentum은 후속 과제다.

### Fallback / Source 운영 원칙

- 2026-06-24 after-market 관찰: COM realtime 서버는 `running=True`, `login_state=connected`, `realreg_succeeded=True`, `orderbook_realreg_succeeded=True`, `registered_count=175` 상태로 정상 동작했다.
- `realdata_received_count`는 관찰 중 계속 증가했고, NXT/AL 실시간 거래가 있는 종목은 `/api/realtime_patch` 500ms 경로로 계속 갱신되었다.
- 현재 500ms realtime_patch 화면 갱신 컬럼은 현재가, 등락률, 금액(억), 일봉, 잔량비, 순간강도, 세션강도다.
- Source 후보는 `realtime`, `close_snapshot`, `regular_close_snapshot`, `nxt_after_close_snapshot`, `unavailable`, `close_snapshot_candidate`만 사용한다.
- 현재가/등락률/금액(억)은 `realtime`을 우선하고, 실시간 값이 없으면 `close_snapshot` 표시가 가능하다.
- 일봉은 `ka10086` base OHLC를 유지하고, 실시간 tick이 있으면 `realtime_ohlc`를 우선 표시한다. tick이 없거나 `realtime_ohlc`가 없으면 기존 `ohlc`를 fallback으로 유지한다.
- `realtime_ohlc`는 base open/prev_high/prev_close/prev_low/vwap을 유지하고 tick price로 high/low/close만 갱신한다. vwap은 base 유지이며 `vwap_source="base"`다.
- 잔량비는 실시간 호가가 없으면 `unavailable`이다. 마감 호가 원천 확인 전에는 `close_snapshot_candidate`로만 둔다.
- 순간강도는 `realtime` FID228을 우선하고, FID228 실시간 값이 없으면 `unavailable`이다. `OPT10047`/`OPT10084` snapshot 후보는 별도 검증 전까지 표시 원천으로 쓰지 않는다.
- 세션강도는 fallback 금지다. 서버 시작 이후 실시간 FID15 체결수량 부호 누적이라는 정의를 유지한다.
- 080220 제주반도체와 036930 주성엔지니어링은 `/api/top100`에는 있고 price/change_rate/trade_value_eok/ohlc도 있으나, `/api/realtime` quote와 `/api/realtime_patch` row가 없다. 따라서 잔량비/순간강도/세션강도는 `unavailable`이 맞다.

### 0186 통합 순위 비교 메모

- StockBoard top100 원천은 `ka10032`다.
- 현재 요청 조건은 `mrkt_tp="000"`, `mang_stk_incls="0"`, `stex_tp="3"`이다.
- `original_rank`는 `ka10032` 원본 순위다.
- `rank`/`displayed_rank`는 `tradable_stock_master.csv` 필터 후 StockBoard가 다시 매긴 표시 순위다.
- HTS/ka10032 원본 대조는 `original_rank` 기준으로 보고, StockBoard 화면의 보통주 중심 순위는 `rank`/`displayed_rank` 기준으로 본다.
- StockBoard는 `tradable_stock_master.csv` 필터 후 `rank`를 다시 매긴다.
- 키움 0186은 ETF/ETN/스팩 제외 통합 순위로 보이며, StockBoard는 일반 보통주 중심으로 우선주 포함 대부분을 제외한다.
- HTS 0186 거래대금상위(통합)와 비교할 때는 `rank`보다 `original_rank`를 먼저 비교한다.
- 실시간 금액은 overlay되지만 순위 재정렬은 아직 하지 않는다.
- 차이가 계속 나면 신규상장/종목마스터 누락 또는 필터 차이를 먼저 점검한다.
- `/api/top100_filter_report` 1차 진단 결과는 `raw_count=269`, `displayed_count=177`, `dropped_count=92`다.
- 탈락 상위 종목은 KODEX/TIGER/ACE/RISE/HANARO/SOL 등 ETF 계열과 삼성전자우/삼성전기우 등 우선주가 대부분이다.
- 현재 확인 범위에서는 순위 차이의 주원인은 의도된 ETF/우선주 제외 후 재순위다.
- 0186 순위 차이 원인 조사는 1차 완료로 본다. 탈락 92개 전체 분류는 서버 안정 시 추가 확인한다.

## 9. 다음 작업

우선순위 갱신:

1. 현재 작업 묶음 커밋/푸시 후 공식 TODO 문서에 완료 이력 반영.
2. 후보 선발기준 모델 문서화 및 레고블럭 구조 설계.
3. 후보5 v0.1 장중 관찰 후 v0.2 유지/수정/폐기 판단.
4. 외선, 큰손, KRT, 정확한 당일강도, 미국시장 실데이터 순으로 데이터 확장.
5. Replay, 시장체온, Signal/Ranking/Strategy 정식화는 후속 큰 덩어리로 진행.

보류/주의:

- suffix 진단 Store 오염 방지 패치는 커밋 예정 WIP 코드이며, 내일 정규장 OFF -> ON -> OFF 검증 전까지 DONE으로 보지 않는다.
- 장마감 `regular_close_snapshot`/fallback, 탈락 92개 전체 분류, realtime_patch 경량화는 서버 안정화 후 별도 묶음으로 진행한다.
- Git push는 매 작업마다 하지 않고 의미 있는 큰 덩어리 단위로 묶는다.

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
- FID10 현재가 normalize 로직을 임의 수정하지 않는다.
- FID12 등락률 normalize 로직을 임의 수정하지 않는다.
- 후보5/등급/모멘텀 계산에서 `price`/`change_rate` 원천값을 덮어쓰지 않는다.
- HTML에서 가격을 계산하지 않는다.
- 장마감/애프터마켓 가격 불일치를 보정식으로 해결하지 않는다.
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
- 2026-06-24: 순간강도 realtime_patch 화면 연결 완료 상태와 20초 patch-only 검증 결과를 반영.
- 2026-06-24: 순간강도 0~200 clamp 및 gamma 0.6 비선형 색상바 스케일 개선 완료 상태를 반영.
- 2026-06-25: 후보5 v0.1 점수모델, candidate fields, 원클릭 라이브 런처, 루트 redirect, UI 밀도/정렬/상단 레이아웃 개선, 상·하한가 표시 예외처리를 반영.
- 2026-06-25: 가격 불가침 원칙, `/api/top100` root 배열 189개 구조 진단, 15:30 장마감 가격 불일치 관찰, suffix Store 오염 방지 패치 WIP 상태와 내일 정규장 검증 필요 항목을 반영.
- 2026-06-26: 단일 `stockboard_live.cmd` 런처, AHK bridge 제어, Price Fast Mode, realtime 100종목 제한, stale tick drop 진단, hybrid orderbook, Graphic/Fast display mode, visual-cell E palette, tooltip single path, 브라우저 수신 기준 5분강도 임시 표시 상태를 반영.
- 2026-06-27: opt10046 체결강도추이시간별요청과 opt10004 주식호가요청으로 장마감 조회 snapshot 원천을 확정하고, Fast Mode 잔량비 `64/36` 표시 정책을 반영.

## 12. 2026-06-26 최신 운영 상태

작성 기준: 2026-06-26 장중 Price Fast Mode, Hybrid Orderbook, visual-cell 경량 UI 적용 후 검증 결과.

### 현재 장중 요약

- 대표 실행 파일은 `stockboard_live.cmd` 하나로 통합한다.
- `stockboard_live.cmd`는 server start/status/stop/restart와 AutoHotkey v1 bridge start/clean/status를 담당한다.
- AHK bridge 본체는 `scripts/stockboard_kiwoom_link_v1.ahk`를 유지한다.
- 삭제했던 보조 관리자 실행 cmd는 재생성하지 않는다.
- 가격 원천 정책은 `_AL` 통합 source 유지다.
- Store/API/DOM/order key는 6자리 stock code 유지다.
- Price Fast Mode는 장중 현재가/등락률 우선 모드다.
- realtime 등록 제한은 `STOCKBOARD_REALTIME_CODE_LIMIT=100`이다.
- stale tick drop 진단은 `STOCKBOARD_DROP_STALE_TRADE_SECONDS=5` 기준이다.
- orderbook은 `STOCKBOARD_ORDERBOOK_MODE=hybrid`로 운영한다.
- hybrid orderbook은 hot Top5 + rotate 20개/5초 구조다.
- 화면은 Graphic/Fast display mode를 지원한다.
- Fast Mode는 visual-cell 경량 렌더링을 사용한다.
- visual-cell 색상은 E palette로 고정한다.
- visual-cell native `title`은 제거하고 custom tooltip `data-tooltip` 단일 경로를 사용한다.
- 5분강도 화면 컬럼은 현재 opt10046 공식값이 아니라 브라우저 수신 기준 최근 5분 delta 임시 표시다.

### 검증 결과

```text
git diff --check: pass
python -m py_compile kiwoom_data_provider.py stockboard_server.py stockboard_store.py: pass
running=True
login_state=connected
registered_count=100
price_fast_mode=True
realtime_code_limit=100
orderbook_mode=hybrid
orderbook_registered_count=25
avg_trade_lag_sec_recent=0.635
max_trade_lag_sec=3.251
stale_trade_drop_count=0
000660_source=000660_AL
005930_source=005930_AL
402340_source=402340_AL
price/change_rate equality=True
AHK_RUNNING=True
```

### 5분강도 주의

- 현재 화면의 `5분강도`는 opt10046 공식 체결강도5분이 아니다.
- 현재 화면의 값은 브라우저가 수신한 `session_buy_qty_live` / `session_sell_qty_live` 누적 샘플의 최근 5분 delta로 임시 계산한다.
- 계산식은 `최근5분 매수체결량 delta / 최근5분 매도체결량 delta * 100`이다.
- `strength_5m_enabled=False` 상태에서는 opt10046 backend 조회/저장은 꺼져 있다.
- 장마감 공식 5분강도는 `opt10046 close_5m_strength` backend 조회/저장 TODO로 남긴다.

### HTML 분리 TODO

- 현재 `docs/stockboard_v0_3_0_sample.html`은 즉시 분리하지 않는다.
- 안정화 후 별도 작업으로 CSS/JS 분리를 진행한다.
- 분리 후보: `stockboard.css`, `stockboard_state.js`, `stockboard_render.js`, `stockboard_realtime.js`, `stockboard_tooltip.js`, `stockboard_selection.js`, `stockboard_debug.js`.
- 번들러 없이 `script`/`link` 분리부터 우선 검토한다.
- 분리 후 Headless Chrome과 실제 브라우저 검증이 필수다.

### Coding keyword TODO list

| 번호 | 키워드 | 상태 |
|---:|---|---|
| 01 | PriceFastMode | DONE |
| 02 | RealtimeLimit100 | DONE |
| 03 | StaleTickDrop | DONE |
| 04 | HybridOrderbookHot5Rotate20 | DONE |
| 05 | UnifiedLauncher | DONE |
| 06 | AHKBridgeControl | DONE |
| 07 | GraphicFastToggle | DONE |
| 08 | VisualCellRendering | DONE |
| 09 | EPaletteVisualCells | DONE |
| 10 | TooltipSinglePath | DONE |
| 11 | FiveMinuteStrengthBrowserDelta | DONE/임시 |
| 12 | opt10046Close5mStrength | DONE |
| 13 | RegularCloseSnapshot1530 | TODO |
| 14 | PriceSourceDisplayPolicy | TODO |
| 15 | AftermarketFallbackPolicy | TODO |
| 16 | SuffixStoreIsolationRegularVerify | WIP |
| 17 | RealtimePatchPayloadDelta | TODO |
| 18 | DirectDebugPanelToggle | DONE |
| 19 | HTMLModuleSplitPrep | DONE |
| 20 | HTMLCssSplit | DONE |
| 21 | HTMLJsPreSplitAudit | DONE |
| 22 | HTMLJsSafeDedup | DONE |
| 23 | HTMLJsSplitDesign | WIP |
| 24 | HTMLFormatJsSplit | WIP |
| 25 | HTMLStateConstantsSplit | WIP |
| 26 | HTMLVisualCellSplit | WIP |
| 27 | HTMLTooltipSplitDesign | WIP |
| 28 | HTMLTooltipCoreSplit | WIP |
| 29 | HTMLCloseMetricsSplitDesign | WIP |
| 30 | HTMLCloseMetricsHelperSplit | WIP |
| 31 | HTMLDirectApiDebugSplitDesign | WIP |
| 32 | HTMLDirectApiDebugHelperSplit | WIP |
| 33 | HTMLModuleSplit | TODO |
| 34 | CandidateModelV02 | TODO |
| 35 | ForeignFuturesSource | TODO |
| 36 | BigHandKRT | TODO |
| 37 | DayStrengthBackfill | TODO |
| 38 | MarketSupplyRefresh | TODO |
| 39 | SignalRankingStrategyFormalize | TODO |

## 13. 2026-06-27 장마감 조회 snapshot 확정

작성 기준: 2026-06-27 StockBoard 3C 마감 정리 후 상태.

### 장마감 후 표시 원칙

- 실시간 데이터가 있으면 실시간 값을 우선 사용한다.
- 실시간 데이터가 없으면 Kiwoom 조회 snapshot 값을 우선 사용한다.
- 조회도 불가능할 때만 마감값 없음으로 둔다.
- HTML에서 임의 장마감 값을 만들어내지 않는다.

### 체결강도 snapshot

- `opt10046 체결강도추이시간별요청` 단일 probe와 후보5 + 상위20 queue 연결을 통해 장마감 후 snapshot 조회를 확인했다.
- 조회 필드:
  - `realtime_strength_snapshot`
  - `strength_5m`
  - `strength_20m`
  - `strength_60m`
  - `strength_source=opt10046`
  - `strength_snapshot_at`
  - `strength_stale_sec`
  - `strength_status`

### 잔량비 snapshot

- `opt10004 주식호가요청`으로 총매수잔량/총매도잔량 snapshot 조회를 확인했다.
- 잔량비 장마감 조회 원천:
  - `orderbook_source=opt10004`
  - `bid_volume_snapshot`
  - `ask_volume_snapshot`
  - `bid_pct`
  - `ask_pct`
  - `bid_ask_ratio_snapshot`
- `ask_pct=0`은 정상값이며 빈값으로 처리하지 않는다. 화면은 `100/0` 또는 `0/100`처럼 표시한다.

### 화면 표시

- Fast Mode 잔량비 본문은 `64/36` 형식이다.
- 앞 숫자는 매수잔량 비율이며 빨간색, 뒤 숫자는 매도잔량 비율이며 파란색이다.
- 열 제목이 잔량비이므로 본문에는 `수/도/%`를 붙이지 않는다.
- Tooltip에는 `잔량비: 64% / 36%`, 총매수잔량, 총매도잔량, 계산식, source, snapshot_at, stale_sec, status를 유지한다.
- Graphic Mode 잔량비는 red/blue 배경 비율을 유지한다.
- E palette는 `red=#E75F5F`, `blue=#5F9EF5`, `neutral=#E2E8F0`, `wick=#111827`로 유지한다.

### 유지 TODO

- HTML module split은 TODO로 유지한다.

## 14. Direct API Debug panel 운영 toggle

- Direct API Debug panel은 운영 화면에서 기본 숨김이다.
- topbar의 `진단` 버튼으로 표시/숨김을 전환한다.
- 표시 상태는 `stockboard.debugPanelVisible` localStorage key로 유지한다.
- 가격 원천 진단 로직과 `/api/realtime` 직접 확인 경로는 유지하며, 화면 노출만 toggle로 제어한다.

## 15. HTML module split 사전 정리

- 5A에서는 실제 CSS/JS 파일 분리, `script type="module"` 전환, `import/export` 추가를 수행하지 않았다.
- `docs/stockboard_v0_3_0_sample.html` 내부에 CSS/HTML/JS 구역 경계 주석과 split 후보 맵만 추가했다.
- 향후 분리 후보는 `stockboard.css`, `stockboard_state.js`, `stockboard_api.js`, `stockboard_format.js`, `stockboard_render.js`, `stockboard_tooltip.js`, `stockboard_close_metrics.js`, `stockboard_controls.js`, `stockboard_main.js`다.
- 전역 의존성, singleton tooltip root, close metrics next-batch state, Direct API debug state, localStorage key 목록을 inline 주석으로 정리했다.
- 운영 안정성을 위해 동작 변경 없이 주석/구조 정리만 수행했고, 실제 파일 분리는 5B CSS 분리부터 단계적으로 진행한다.

## 16. HTML CSS split 5B

- 5B에서 inline `<style>` CSS를 `docs/assets/stockboard.css`로 분리했다.
- HTML은 `assets/stockboard.css?v=5b_css_split_20260627` stylesheet link를 사용한다.
- JS는 아직 inline 유지이며, `script type="module"`, `import/export`, JS 파일 생성은 하지 않았다.
- 5A 주석/구역 정리와 5B CSS 분리는 하나의 WIP 묶음으로 유지하며, 검증 완료 후 함께 커밋한다.
- 목적은 HTML 줄 수 감소와 다음 JS split 단계의 유지보수성 개선이다.

## 17. JS split 사전 조사 5C

- 5C는 JS 분리 전 조사 단계이며, 실제 JS 파일 분리, `script type="module"` 전환, `import/export` 추가는 수행하지 않는다.
- inline JS는 크게 상태/상수, 상태등/시장수급, localStorage, 컬럼 리사이즈, format/normalize, visual-cell, render, tooltip, close metrics next-batch, API refresh, Direct API debug, selection/clipboard bridge로 나눌 수 있다.
- 중복 helper 후보:
  - `ohlcTooltip`이 2회 선언되어 후행 선언이 실제 사용된다.
  - `balanceView`, `minuteStrengthView`, `sessionStrengthView`가 2회 선언되어 후행 Fast/Graphic snapshot 대응 버전이 실제 사용된다.
  - `formatVolume`은 전역 함수와 과거 `balanceView` 내부 지역 함수가 공존한다.
  - `normalizeCode`와 selection IIFE의 `normalizeStockCode`는 목적이 유사하지만 `_AL/_NX` 허용 정책이 달라 바로 통합하면 위험하다.
  - `firstValue`와 `directApiDebugFirstValue`는 유사하지만 debug payload 안전 접근 목적이 분리되어 있다.
- 죽은 코드 후보:
  - 앞쪽 `ohlcTooltip(candle)`, `balanceView`, `minuteStrengthView`, `sessionStrengthView`는 후행 동일 이름 선언에 가려지는 과거 구현 후보이다.
  - `metricView`/`metricMarkup`은 현재 visual-cell 중심 렌더링에서 직접 사용 경로가 희박해 5D에서 호출 여부를 재확인한다.
  - `title` attribute를 생성하는 markup은 native title observer가 제거하므로 custom tooltip 경로와 정리 가능성을 검토한다.
  - 이전 viewport-row 기준 lazy 방식은 현재 `requestNextCloseMetricsBatch()` 표 순서 기준으로 대체되어, 관련 잔재가 남아 있는지 5D에서 확인한다.
  - `.ohlc-fast-text` 좌측 정렬 시도는 UI 영향 확인 후 유지/정리 여부를 별도 판단한다.
- 삭제/이동 위험 코드:
  - `refreshTop100`, `loadRealtimePatch`, `renderBoard`, `renderCandidateBoard`, `applyRealtimePatchToRow`
  - `requestNextCloseMetricsBatch`, `collectNextCloseMetricCodes`, `closeMetricsRequestedCodes`, `closeMetricsPendingCodes`, `closeMetricsCompletedCodes`
  - `stripNativeTitles`, singleton balance/OHLC tooltip root와 tooltip mouse handlers
  - `setVisualCell`, `ohlcCellView`, `visualMetricView`, Fast/Graphic display mode helpers
  - Direct API debug toggle/load/update functions
  - column resize, board sort, localStorage compatibility helpers
  - selection/clipboard bridge IIFE
- 5D split 후보 보정:
  - `stockboard_state.js`: constants, DOM refs, `top100State`, localStorage keys
  - `stockboard_api.js`: `/api/top100`, realtime patch, market supply fetch
  - `stockboard_format.js`: normalize/format/firstValue/sort helpers
  - `stockboard_visual_cells.js`: Fast/Graphic visual metric and OHLC visual helpers
  - `stockboard_render.js`: row normalize, candidate/trading board render, realtime patch apply
  - `stockboard_tooltip.js`: native title stripping and singleton tooltip handlers
  - `stockboard_close_metrics.js`: next-batch lazy state and request helpers
  - `stockboard_controls.js`: density/display/debug buttons, sort, column resize, candle mode
  - `stockboard_debug.js`: Direct API diagnostic panel
  - `stockboard_selection.js`: stock selection/clipboard bridge
  - `stockboard_main.js`: boot, intervals, module wiring
- 작은 조사 단계는 커밋하지 않고 5C+5D 또는 5C+5D+5E 단위로 묶어 커밋한다.

## 18. JS split 전 안전 정리 5D

- 5D는 JS split 전 안전 정리 단계이며, 실제 JS 파일 분리, `script type="module"` 전환, `import/export` 추가는 수행하지 않았다.
- 같은 inline script scope에서 후행 선언에 의해 완전히 가려지던 앞쪽 중복 선언만 제거했다.
- 제거한 중복 선언:
  - 앞쪽 `ohlcTooltip(candle)`
  - 앞쪽 `balanceView(value, options)`
  - 앞쪽 `minuteStrengthView(value)`
  - 앞쪽 `sessionStrengthView(value, options)`
- 제거 후 위 함수들은 각각 1개 선언만 남아 있으며, 후행 Fast/Graphic 및 snapshot 대응 구현을 유지한다.
- viewport-row 기준 lazy helper 잔재(`visibleTradingRowsForCloseMetrics`, `CLOSE_METRICS_LAZY_PREFETCH_PX`, viewport+buffer row selection)는 남아 있지 않음을 확인했다.
- 삭제하지 않고 보류한 후보:
  - `metricView`/`metricMarkup`: 호출 경로가 희박하지만 가격제한/visual 과거 경로와 혼동 가능성이 있어 5E에서 재확인한다.
  - `normalizeCode`/selection IIFE `normalizeStockCode`: `_AL/_NX` 허용 정책 차이가 있어 통합하지 않는다.
  - `firstValue`/`directApiDebugFirstValue`: Direct API payload 안전 접근 목적이 있어 통합하지 않는다.
  - native `title` 생성 markup: observer/custom tooltip 정책과 연결되어 있어 별도 tooltip 정리 단계에서 다룬다.
- Fast Mode 일봉 좌측 정렬 polish는 보류 상태를 유지한다. `.ohlc-fast-text`는 Fast Mode 전용 wrapper/CSS로 남기며 Graphic Mode 캔들 경로는 변경하지 않는다.
- 다음 단계는 5E JS split 설계 또는 5D 추가 정리다.

## 19. JS split 설계 확정 5E

- 5E는 JS split 설계 확정 단계이며, 실제 JS 파일 생성, `script type="module"` 전환, `import/export` 추가는 수행하지 않는다.
- 작은 단계마다 커밋하지 않고 5E+5F 또는 5E+5F+5G 단위로 묶어 커밋한다.
- 1차 분리는 순수 helper 위주로 진행하고, DOM 직접 접근과 `top100State` 의존이 큰 render/refresh/main loop는 마지막으로 미룬다.

### 권장 JS split 순서

1. `stockboard_format.js`
2. `stockboard_state.js`
3. `stockboard_visual_cells.js`
4. `stockboard_tooltip.js`
5. `stockboard_close_metrics.js`
6. `stockboard_debug.js`
7. `stockboard_controls.js`
8. `stockboard_selection.js`
9. `stockboard_api.js`
10. `stockboard_render.js`
11. `stockboard_main.js`

### 5F 첫 분리 대상

- 첫 분리 대상은 `docs/assets/stockboard_format.js` 하나로 제한한다.
- 후보 함수:
  - `escapeHtml`
  - `normalizeCode`
  - `displayValue`
  - `formatInteger`
  - `numericValue`
  - `formatTruncatedInteger`
  - `formatVolume`
  - `formatPercent`
  - `formatOhlcTooltipNumber`
  - `formatOhlcMove`
  - `formatOhlcTooltipPrice`
- `firstValue`, `sortableNumber`, `sortableText`, `directApiDebugFirstValue`는 사용 범위가 넓거나 debug/render 정책과 섞여 있어 5F에서는 이동하지 않는다.

### namespace/export 방식

- ES module `import/export`는 사용하지 않는다.
- 외부 파일은 `window.StockBoardFormat` namespace를 만든다.
- 5F에서는 inline script보다 먼저 아래 순서로 로드한다.
  1. `<script src="assets/stockboard_format.js?v=5f_format_split_20260627"></script>`
  2. 기존 inline main script
- `defer`는 사용하지 않고 기존 blocking script 순서를 유지한다.
- inline script는 초기에 `const StockBoardFormat = window.StockBoardFormat || {};` 식으로 namespace를 참조한 뒤 기존 호출이 깨지지 않도록 최소 연결한다.
- 필요하면 호환 shim으로 기존 함수명 wrapper를 inline에 잠시 남긴다. 예: `const formatInteger = StockBoardFormat.formatInteger;`

### 위험도 표

| 후보 파일 | 포함 함수/역할 | DOM 의존도 | 전역 state 의존도 | 분리 위험도 | 권장 순서 | 보류 사유 |
|---|---|---:|---:|---:|---:|---|
| `stockboard_format.js` | escape/normalize/number/percent/OHLC tooltip number format | 낮음 | 낮음 | 낮음 | 1 | 5F 첫 대상 |
| `stockboard_state.js` | constants, DOM refs, `top100State`, localStorage keys | 중간 | 높음 | 중간 | 2 | namespace 안정화 후 분리 |
| `stockboard_visual_cells.js` | Fast/Graphic metric, OHLC visual helper | 낮음~중간 | 중간 | 중간 | 3 | format 분리 후 의존 정리 필요 |
| `stockboard_tooltip.js` | native title stripping, singleton tooltip handlers | 높음 | 중간 | 중간~높음 | 4 | tooltip 중복 방지 회귀 위험 |
| `stockboard_close_metrics.js` | next-batch lazy state/request helpers | 중간 | 높음 | 중간~높음 | 5 | API spam 방지와 state gate 중요 |
| `stockboard_debug.js` | Direct API debug load/update/toggle | 중간 | 중간 | 중간 | 6 | 운영 toggle 유지 필요 |
| `stockboard_controls.js` | density/display buttons, sort, resize, candle mode | 높음 | 높음 | 높음 | 7 | localStorage/DOM event 결합 큼 |
| `stockboard_selection.js` | stock selection/clipboard bridge IIFE | 높음 | 낮음~중간 | 중간 | 8 | 독립 IIFE라 분리 가능하지만 클릭/키보드 영향 있음 |
| `stockboard_api.js` | top100/realtime/market supply fetch | 중간 | 높음 | 높음 | 9 | refresh loop와 render coupling 큼 |
| `stockboard_render.js` | normalize row, candidate/trading render, patch apply | 높음 | 높음 | 매우 높음 | 10 | 화면 핵심 경로 |
| `stockboard_main.js` | boot, intervals, module wiring | 높음 | 높음 | 매우 높음 | 11 | 마지막 통합 단계 |

### 5F 작업 범위

- `docs/assets/stockboard_format.js` 하나만 생성한다.
- 순수 format/helper 함수 일부만 이동한다.
- render, tooltip, close metrics, Direct API debug, controls, main loop는 건드리지 않는다.
- HTML은 외부 script를 inline main script보다 먼저 로드한다.
- `type="module"`과 `import/export`는 계속 보류한다.
- 런타임 화면 확인 후 5E+5F 묶음 커밋을 검토한다.

## 20. 첫 JS 분리 5F

- 5F에서 `docs/assets/stockboard_format.js`를 생성했다.
- ES module은 아직 사용하지 않으며, `script type="module"` 전환과 `import/export` 추가는 수행하지 않았다.
- 외부 파일은 `window.StockBoardFormat` namespace를 사용한다.
- HTML은 기존 inline main script보다 먼저 `assets/stockboard_format.js?v=5f_format_split_20260627`를 로드한다.
- inline JS는 compatibility alias로 기존 호출부 변경을 최소화한다.
- 이동한 함수:
  - `escapeHtml`
  - `displayValue`
  - `formatInteger`
  - `numericValue`
  - `formatTruncatedInteger`
  - `formatVolume`
  - `formatPercent`
  - `formatOhlcTooltipNumber`
  - `formatOhlcMove`
  - `formatOhlcTooltipPrice`
- 5F에서 이동하지 않고 보류한 함수:
  - `normalizeCode`: `_AL/_NX` 허용 정책, 6자리 key 정책, selection bridge와 연결될 수 있어 보류한다.
  - `normalizeStockCode`: selection bridge 내부 정책 유지.
  - `firstValue`, `directApiDebugFirstValue`: render/debug payload 접근 정책과 연결되어 보류한다.
  - `sortableNumber`, `sortableText`: sort/render 경로와 함께 후속 단계에서 검토한다.
- render, tooltip, close metrics, Direct API debug, controls, main loop는 아직 inline 유지다.
- 5E+5F는 검증 후 하나의 묶음 커밋으로 처리한다.

## 21. state/constants 최소 분리 5G

- 5G에서 `docs/assets/stockboard_state.js`를 생성했다.
- ES module은 아직 사용하지 않으며, `script type="module"` 전환과 `import/export` 추가는 수행하지 않았다.
- 외부 파일은 `window.StockBoardState` namespace를 사용한다.
- HTML은 `assets/stockboard_format.js`, `assets/stockboard_state.js`, 기존 inline main script 순서로 로드한다.
- 분리한 상수:
  - localStorage key 목록: trading board column widths, sort state, display density, display mode, topbar widths, US market widths, market supply widths, candle mode, debug panel visibility
  - display mode constants: `fast`, `graphic`
  - close metrics constants: batch size 20, throttle 1000ms, scroll delta 150px, refresh delay 10000ms
- inline main script는 `STOCKBOARD_STORAGE_KEYS`, `STOCKBOARD_DISPLAY_MODES`, `STOCKBOARD_CLOSE_METRICS` compatibility alias로 기존 호출부 변경을 최소화한다.
- 이동하지 않은 mutable runtime state:
  - `top100State`
  - `renderedRows`, `candidateRows`, `rowByCode`
  - close metrics requested/pending/completed Set과 lazy timer/inflight state
  - tooltip root/state
  - Direct API debug runtime state
  - selection/clipboard bridge state
  - column resize runtime controller state
- render, tooltip, close metrics functions, Direct API debug functions, controls, main loop는 아직 inline 유지다.
- 다음 단계는 visual-cell 또는 tooltip 분리 검토다.

## 22. visual-cell helper 최소 분리 5H

- 5H에서 `docs/assets/stockboard_visual_cells.js`를 생성했다.
- ES module은 아직 사용하지 않으며, `script type="module"` 전환과 `import/export` 추가는 수행하지 않았다.
- 외부 파일은 `window.StockBoardVisualCells` namespace를 사용한다.
- HTML은 `assets/stockboard_format.js`, `assets/stockboard_state.js`, `assets/stockboard_visual_cells.js`, 기존 inline main script 순서로 로드한다.
- 분리한 함수:
  - `balanceView`
  - `minuteStrengthView`
  - `sessionStrengthView`
- `balanceView`는 `ask_pct=0` 같은 0 값을 정상 숫자로 유지하고, Fast Mode `64/36` 표시와 Graphic Mode red/blue 비율 계산 정책을 바꾸지 않는다.
- 이동하지 않고 보류한 함수:
  - `setVisualCell`: DOM 직접 조작 함수라 inline 유지.
  - `metricView`, `metricMarkup`: 과거 visual/price-limit 경로와 연결 가능성이 있어 inline 유지.
  - `ohlcTooltip`, `rowOhlcMarkup`, OHLC/render 관련 함수: tooltip/render 경로와 함께 후속 단계에서 검토.
  - tooltip singleton, close metrics next-batch, Direct API debug, selection, sort, column resize, main loop: inline 유지.
- Graphic/Fast 표시 동작 변경 없음이 목표이며, 다음 단계는 tooltip 분리 설계 또는 close metrics 분리 설계 검토다.

### 5H 보정

- 5분강도 Graphic Mode 색상비도 순간강도와 동일한 100 기준 강조 수식(`strengthVisualPosition`)을 사용한다.
- 숫자값, 원천값, tooltip 본문은 변경하지 않고 visual ratio만 보정했다.
- Fast Mode는 기존 숫자 표시를 유지하고, 잔량비 `64/36` 및 red/blue 배경 비율 계산에는 영향을 주지 않는다.
- 잔량비 Graphic Mode 색상비는 `bid_pct`/`ask_pct` 백분율을 우선 사용하고, 없으면 매수잔량/(매수+매도), 매도잔량/(매수+매도) 기준으로 직접 계산한다.
- 숫자 표시 `64/36`과 그래픽 red/blue 비율 기준을 일치시켰으며, ratio/log 변환이나 상한/하한 별도 텍스트·색상 역전은 `balanceView`에서 사용하지 않는다.
- `ask_pct=0`, `bid_pct=100`은 정상값으로 처리한다.

## 23. tooltip 분리 설계/위험점 점검 5I

- 5I는 tooltip 분리 설계와 위험점 점검 단계이며, 실제 tooltip JS 파일 생성, 함수 이동, `script type="module"` 전환, `import/export` 추가는 수행하지 않는다.
- 현재 tooltip은 inline main script 안에서 balance tooltip과 OHLC tooltip을 별도 singleton root로 생성하고, document-level mouse/click/key/scroll handler로 제어한다.

### tooltip DOM/state

- root element:
  - `balanceTooltip`: `div.balance-tooltip`, `role=tooltip`, `document.body`에 append
  - `ohlcTooltipElement`: `div.ohlc-tooltip`, `role=tooltip`, `document.body`에 append
- hover state:
  - `activeBalanceTooltipCell`
  - `lastBalanceTooltipEvent`
  - `activeOhlcTooltipCell`
  - `lastOhlcTooltipEvent`
- native title cleanup:
  - `stripNativeTitles(root=document)`
  - `nativeTitleObserver`: `title` attribute mutation과 added node의 native title을 제거하고 `aria-label`로 보존
- data attributes:
  - `data-tooltip`, `data-tooltip-html`
  - `data-balance-tooltip`, `data-ohlc-tooltip`는 `setVisualCell()`에서 자식 잔재 제거 대상
  - `cell.dataset.tooltipKind = 'balance' | 'ohlc'`

### tooltip 함수 목록

- 공통/native cleanup:
  - `stripNativeTitles`
  - `nativeTitleObserver`
- balance tooltip:
  - `balanceTooltipText`
  - `positionBalanceTooltip`
  - `showBalanceTooltip`
  - `refreshBalanceTooltip`
  - `hideBalanceTooltip`
  - `balanceTooltipCellFromTarget`
  - `currentBalanceTooltipCell`
  - `updateBalanceTooltipFromMouse`
- OHLC tooltip:
  - `ohlcTooltipText`
  - `positionOhlcTooltip`
  - `showOhlcTooltip`
  - `hideOhlcTooltip`
  - `ohlcTooltipCellFromTarget`
  - `currentOhlcTooltipCell`
  - `refreshOhlcTooltip`
  - `updateOhlcTooltipFromMouse`
- render 연결:
  - `setVisualCell()`은 cell과 자식의 native `title`, `data-tooltip`, `data-tooltip-html`, `data-balance-tooltip`, `data-ohlc-tooltip` 잔재를 제거하고 최상위 `td`에 `data-tooltip`/`aria-label`/`data-tooltip-kind`를 설정한다.

### tooltip 종류별 경로

- 잔량비/순간강도/5분강도 visual tooltip:
  - render가 `setVisualCell(..., 'balance-cell visual-cell')` 호출
  - `setVisualCell()`이 `td.balance-cell[data-tooltip]`에 단일 tooltip snapshot 저장
  - `balanceTooltipCellFromTarget()`은 `td.balance-cell[data-tooltip]` 또는 내부 `[data-tooltip]`의 closest balance cell만 허용
  - `updateBalanceTooltipFromMouse()`가 show/hide와 위치 갱신 담당
- 일봉/OHLC tooltip:
  - `ohlcCellView()`/`ohlcMarkup()`이 OHLC tooltip text를 만들고 render가 `setVisualCell(..., 'ohlc-cell visual-cell', 'ohlc')` 호출
  - `ohlcTooltipCellFromTarget()`은 `td.ohlc-cell[data-tooltip]` 또는 내부 `[data-tooltip]`의 closest OHLC cell만 허용
  - `refreshOhlcTooltip()`은 realtime/refresh 중 hover target이 바뀌면 숨김
- 일반 셀 tooltip:
  - `setCell()`은 native `title`을 쓰지 않고 `aria-label`로 보존한다.
  - custom floating tooltip 경로에는 일반 셀을 포함하지 않는다.
- Direct API debug:
  - debug item markup은 `title`을 생성하지만 `nativeTitleObserver`가 제거하고 `aria-label`로 보존한다.
  - custom floating tooltip 대상에는 포함하지 않는다.

### 위험 의존성

- `setVisualCell()`의 자식 tooltip/title 잔재 제거가 깨지면 부모/자식 tooltip 중복이 재발할 수 있다.
- OHLC target은 `td.ohlc-cell`로 제한되어야 하며, balance target은 `td.balance-cell`로 제한되어야 한다.
- tooltip DOM은 singleton root를 재사용해야 하며, hover 중 새 DOM을 만들면 안 된다.
- hover 시점 snapshot 표시 정책을 유지해야 하며, hover 중 데이터 갱신이 tooltip 본문을 바꾸면 안 된다.
- `refreshBalanceTooltip()`/`refreshOhlcTooltip()`은 render/realtime patch 후 현재 hover target 검증에 필요하다.
- document scroll handler는 tooltip hide와 close metrics scroll trigger를 함께 수행한다. 분리 시 scroll handler 순서와 `handleCloseMetricsScrollTrigger()` 호출을 보존해야 한다.
- mousemove handler는 target 판별, show/hide, 위치 이동을 모두 담당하므로 과도한 분리는 회귀 위험이 있다.
- native title observer를 분리하면 Direct API debug/native title 제거 정책에 영향을 줄 수 있다.

### 5J 분리 제안

- `docs/assets/stockboard_tooltip.js` 생성은 가능하되, 5J 범위는 좁게 잡는다.
- 5J 이동 후보:
  - singleton root 생성
  - text/position/show/hide 함수
  - target 판별 함수
  - mousemove/click/keydown binding
- 5J 보류 후보:
  - `setVisualCell()`: render/DOM mutation 핵심이라 inline 유지
  - `data-tooltip` 생성/tooltip content 생성: render/OHLC/visual metric inline 유지
  - `nativeTitleObserver`: Direct API debug/title cleanup 영향이 커서 5J에서는 inline 유지 검토
  - scroll handler: close metrics trigger와 결합되어 있어 inline 유지 또는 wrapper callback 방식 검토

### tooltip 분리 위험도 표

| tooltip 구성요소 | 현재 위치 | DOM 의존도 | render/setVisualCell 의존도 | 분리 위험도 | 5J 이동 여부 | 보류 사유 |
|---|---|---:|---:|---:|---|---|
| singleton root 생성 | inline tooltip section | 높음 | 낮음 | 중간 | 후보 | body append 순서와 CSS class 유지 필요 |
| balance show/hide/position | inline tooltip section | 높음 | 중간 | 중간 | 후보 | `td.balance-cell` 제한 유지 필요 |
| OHLC show/hide/position | inline tooltip section | 높음 | 중간 | 중간 | 후보 | `td.ohlc-cell` 제한 유지 필요 |
| target 판별 함수 | inline tooltip section | 높음 | 높음 | 중간~높음 | 후보/검토 | 부모/자식 중복 tooltip 회귀 위험 |
| `refreshBalanceTooltip`/`refreshOhlcTooltip` | inline tooltip section | 높음 | 높음 | 높음 | 후보/검토 | render/realtime patch 후 hover snapshot 정책과 연결 |
| native title observer | inline tooltip section | 매우 높음 | 중간 | 높음 | 보류 | Direct API debug와 전체 native title 제거 정책 영향 |
| `setVisualCell` tooltip cleanup | inline render section | 높음 | 매우 높음 | 매우 높음 | 보류 | DOM mutation 및 중복 tooltip 방지 핵심 |
| tooltip content 생성 | render/OHLC/visual helpers | 낮음~중간 | 높음 | 높음 | 보류 | 원천/표시 정책과 결합 |
| scroll hide handler | inline event binding | 높음 | close metrics 의존 높음 | 높음 | 보류/검토 | close metrics scroll trigger와 같은 handler |

- 다음 단계 5J에서 실제 분리 범위는 singleton root + show/hide/position + target 판별 정도로 제한하고, `setVisualCell`, native title observer, tooltip content 생성은 보류하는 방향이 안전하다.

## 24. tooltip core helper 최소 분리 5J

- 5J에서 `docs/assets/stockboard_tooltip.js`를 생성했다.
- ES module은 아직 사용하지 않으며, `script type="module"` 전환과 `import/export` 추가는 수행하지 않았다.
- 외부 파일은 `window.StockBoardTooltip` namespace를 사용한다.
- HTML은 `assets/stockboard_format.js`, `assets/stockboard_state.js`, `assets/stockboard_visual_cells.js`, `assets/stockboard_tooltip.js`, 기존 inline main script 순서로 로드한다.
- 분리한 tooltip core helper:
  - `ensureTooltipElement`
  - `setTooltipContent`
  - `positionTooltipElement`
  - `showTooltipElement`
  - `hideTooltipElement`
- inline에 유지한 tooltip 함수/상태:
  - `balanceTooltipText`, `ohlcTooltipText`
  - `balanceTooltipCellFromTarget`, `ohlcTooltipCellFromTarget`
  - `currentBalanceTooltipCell`, `currentOhlcTooltipCell`
  - `refreshBalanceTooltip`, `refreshOhlcTooltip`
  - `updateBalanceTooltipFromMouse`, `updateOhlcTooltipFromMouse`
  - `stripNativeTitles`, `nativeTitleObserver`
  - document mouse/click/key/scroll event binding
- `setVisualCell()`은 render/DOM mutation 핵심이므로 inline 유지한다.
- balance tooltip 대상은 `td.balance-cell`, OHLC tooltip 대상은 `td.ohlc-cell` 제한 정책을 유지한다.
- hover 시점 snapshot 표시, hover 중 본문 갱신 금지, singleton root 재사용, scroll 시 hide 정책을 유지한다.

## 25. close metrics 분리 설계/위험점 점검 5K

- 5K는 close metrics 분리 설계와 위험점 점검 단계이며, 실제 close metrics JS 파일 생성, 함수 이동, `script type="module"` 전환, `import/export` 추가는 수행하지 않는다.
- 현재 next-batch/lazy close metrics 로직은 inline main script에 남아 있으며, `top100State`, DOM row, `refreshTop100()`, 다음 버튼, scroll handler와 결합되어 있다.

### close metrics state

- constants:
  - `CLOSE_METRICS_LAZY_BATCH_SIZE = STOCKBOARD_CLOSE_METRICS.batchSize` (`20`)
  - `CLOSE_METRICS_LAZY_THROTTLE_MS = STOCKBOARD_CLOSE_METRICS.throttleMs` (`1000`)
  - `CLOSE_METRICS_SCROLL_TRIGGER_PX = STOCKBOARD_CLOSE_METRICS.scrollDeltaTriggerPx` (`150`)
  - `CLOSE_METRICS_LAZY_REFRESH_DELAY_MS = STOCKBOARD_CLOSE_METRICS.refreshDelayMs` (`10000`)
- `top100State` runtime state:
  - `closeMetricsRequestedCodes`: 같은 page session에서 요청한 code 중복 방지
  - `closeMetricsPendingCodes`: request accepted 후 top100 overlay로 완료되기 전 gate
  - `closeMetricsCompletedCodes`: `strength_source=opt10046` 및 `orderbook_source=opt10004` 확인 code
  - `closeMetricsLazyLastRequestedAt`: throttle 기준 시각
  - `closeMetricsLazyInFlight`: API spam 방지 in-flight guard
  - `closeMetricsLazyRefreshTimer`: lazy request 후 10초 refresh 예약 중복 방지
  - `closeMetricsLastScrollY`: 아래 방향 scroll delta 계산 기준
  - `closeMetricsScrollDelta`: 150px 누적 prefetch trigger 상태

### close metrics 함수 목록

- `hasCloseMetricsSnapshot(row)`: 완료 여부 판단
- `syncCloseMetricsCompletion(rows)`: top100 refresh/realtime patch 후 completed/pending Set 반영
- `rowNeedsCloseMetrics(row)`: 완료/requested/pending 제외 후 요청 필요 여부 판단
- `collectNextCloseMetricCodes()`: `#trading-board tbody tr[data-stock-code]` DOM 순서와 `top100State.renderedRows`를 결합해 다음 20개 code 수집
- `scheduleCloseMetricsLazyRefresh()`: lazy request 성공 후 `refreshTop100()` 10초 1회 예약
- `requestLazyCloseMetrics(codes, trigger)`: `/api/close_metrics_request` fetch, requested/pending Set 갱신
- `requestNextCloseMetricsBatch(trigger)`: throttle/in-flight/button disabled/collect/request orchestration
- `handleCloseMetricsScrollTrigger()`: 아래 방향 scroll 누적 150px마다 `requestNextCloseMetricsBatch('scroll')`

### API 호출 경로

- 호출 URL: `/api/close_metrics_request?codes=005930,000660&priority=lazy&force=0`
- `probe` 파라미터는 넣지 않는다.
- backend `enqueue_close_metrics()`가 strength + orderbook 둘 다 enqueue하는 기존 정책을 사용한다.
- HTTP handler에서 COM 직접 호출 금지 원칙을 유지한다. 브라우저는 queue 요청만 보내고 실제 TR 처리는 backend queue가 담당한다.

### 완료/미완료 판단 기준

- 완료:
  - `row.strength_source === 'opt10046'`
  - `row.orderbook_source === 'opt10004'`
- 미완료:
  - 둘 중 하나라도 없으면 미완료
  - `closeMetricsRequestedCodes`, `closeMetricsPendingCodes`, `closeMetricsCompletedCodes`에 있으면 즉시 재요청하지 않음
  - `ask_pct=0`, `bid_pct=100`, `ask_pct=0`은 정상값이며 미완료로 보지 않음

### trigger 경로

- 다음 버튼: `nextScrollButton.click` -> `requestNextCloseMetricsBatch('button')`
- scroll prefetch: document capture `scroll` handler -> tooltip hide -> `handleCloseMetricsScrollTrigger()` -> 아래 방향 150px 누적 -> `requestNextCloseMetricsBatch('scroll')`
- lazy request 성공: `requestLazyCloseMetrics()` -> `scheduleCloseMetricsLazyRefresh()` -> 10초 후 `refreshTop100()`
- 기존 refresh loop: `window.setInterval(refreshTop100, TOP100_REFRESH_MS)` 30초 주기
- refresh/top100 반영:
  - `StockBoardTop100.refresh()`에서 `syncCloseMetricsCompletion(normalizedRows)`
  - `applyRealtimePatchToRow()` 경로에서도 row 단위 `syncCloseMetricsCompletion([row])`

### 위험 의존성

- `collectNextCloseMetricCodes()`는 DOM row 순서와 `top100State.renderedRows`를 동시에 읽는다. 순수 helper처럼 보이지만 DOM/state 의존이 높다.
- `requestNextCloseMetricsBatch()`는 throttle, in-flight, 버튼 disabled, code 수집, fetch 호출을 모두 묶는다.
- close metrics Set은 `renderBoard`, `refreshTop100`, realtime patch overlay와 연결되어 완료 판단이 늦거나 누락되면 재요청/미요청 문제가 생길 수 있다.
- scroll handler는 tooltip hide와 close metrics trigger를 같은 handler에서 수행한다. 분리 시 tooltip 동작과 close metrics prefetch가 함께 깨질 수 있다.
- `scheduleCloseMetricsLazyRefresh()`는 `refreshTop100()`를 직접 호출한다. API fetch helper와 main refresh loop 의존이 있다.
- 180개 close metrics cache가 이미 채워진 상태에서는 신규 lazy request 검증이 어렵다. requested/pending/completed Set 초기 상태와 cache 포화 상태를 구분해야 한다.
- `requestLazyCloseMetrics()`는 실패 시 requested/pending Set을 즉시 되돌리지 않는다. API spam 방지에는 유리하지만 실패 재시도 정책을 바꾸면 회귀 위험이 있다.

### 5L 최소 분리 후보

- `docs/assets/stockboard_close_metrics.js` 생성은 가능하되, 5L 범위는 순수 helper 중심으로 좁게 잡는다.
- 5L 이동 후보:
  - 완료 여부 판단 helper: `hasCloseMetricsSnapshot(row)`
  - 요청 필요 여부 판단 helper: 현재 Set 조회를 인자로 받는 순수 함수 형태
  - request URL/query 생성 helper: `codes`, `priority`, `force` -> URLSearchParams/string
  - throttle 가능 여부 판단 helper: `now`, `lastRequestedAt`, `throttleMs`
- 5L 보류 후보:
  - `requestNextCloseMetricsBatch()` 전체
  - `requestLazyCloseMetrics()` fetch 호출
  - `collectNextCloseMetricCodes()` DOM/top100State 직접 접근
  - scroll handler
  - 다음 버튼 event binding
  - `refreshTop100()`와 lazy refresh timer
  - `closeMetricsRequestedCodes`/`Pending`/`Completed` Set 자체

### close metrics 분리 위험도 표

| 구성요소 | 현재 위치 | DOM 의존도 | state 의존도 | API 의존도 | 분리 위험도 | 5L 이동 여부 | 보류 사유 |
|---|---|---:|---:|---:|---:|---|---|
| `hasCloseMetricsSnapshot` | inline close metrics section | 낮음 | 낮음 | 없음 | 낮음 | 후보 | 순수 row 판단 |
| `rowNeedsCloseMetrics` | inline close metrics section | 낮음 | 높음 | 없음 | 중간 | helper화 후보 | Set을 인자로 받는 형태 필요 |
| request query 생성 | `requestLazyCloseMetrics` 내부 | 낮음 | 낮음 | 중간 | 낮음 | 후보 | fetch와 분리 가능 |
| throttle 판단 | `requestNextCloseMetricsBatch` 내부 | 낮음 | 중간 | 없음 | 낮음 | 후보 | now/last/throttle 인자화 가능 |
| `collectNextCloseMetricCodes` | inline close metrics section | 높음 | 높음 | 없음 | 높음 | 보류 | DOM row 순서와 renderedRows 결합 |
| `requestLazyCloseMetrics` | inline close metrics section | 낮음 | 높음 | 높음 | 높음 | 보류 | Set mutation/fetch/refresh 예약 결합 |
| `requestNextCloseMetricsBatch` | inline close metrics section | 중간 | 높음 | 높음 | 높음 | 보류 | throttle/in-flight/button/fetch orchestration |
| scroll trigger | inline document scroll handler | 높음 | 중간 | 없음 | 높음 | 보류 | tooltip hide와 같은 handler |
| 다음 버튼 binding | inline controls section | 중간 | 중간 | 없음 | 중간 | 보류 | controls 분리 전까지 inline 유지 |
| lazy refresh timer | inline close metrics section | 낮음 | 높음 | 중간 | 높음 | 보류 | `refreshTop100()` 직접 의존 |
| completed Set sync | inline close metrics + patch/refresh | 낮음 | 높음 | 없음 | 높음 | 보류 | overlay 완료 판단과 재요청 방지 핵심 |

- 다음 단계 5L에서는 순수 판단/query/throttle helper만 분리하고, DOM event binding, fetch 호출, Set mutation, refresh 예약은 inline 유지하는 방향이 안전하다.

## 26. close metrics 순수 helper 최소 분리 5L

- 5L에서 `docs/assets/stockboard_close_metrics.js`를 생성했다.
- ES module은 아직 사용하지 않으며, `script type="module"` 전환과 `import/export` 추가는 수행하지 않았다.
- 외부 파일은 `window.StockBoardCloseMetrics` namespace를 사용한다.
- HTML은 `assets/stockboard_format.js`, `assets/stockboard_state.js`, `assets/stockboard_visual_cells.js`, `assets/stockboard_tooltip.js`, `assets/stockboard_close_metrics.js`, 기존 inline main script 순서로 로드한다.
- 분리한 순수 helper:
  - `hasCloseMetricsSnapshot(row)`
  - `needsCloseMetrics(row, code, requestedCodes, pendingCodes, completedCodes)`
  - `canRequestCloseMetrics(now, lastRequestedAt, inFlight, throttleMs)`
  - `buildCloseMetricsRequestUrl(codes, options)`
- 완료 판단 기준은 그대로 유지한다:
  - `strength_source === 'opt10046'`
  - `orderbook_source === 'opt10004'`
- API URL 생성 정책:
  - `/api/close_metrics_request?codes=...&priority=lazy&force=0`
  - `probe` 파라미터는 사용하지 않는다.
- inline에 유지한 close metrics 함수/상태:
  - `closeMetricsRequestedCodes`, `closeMetricsPendingCodes`, `closeMetricsCompletedCodes`
  - `closeMetricsLazyLastRequestedAt`, `closeMetricsLazyInFlight`, `closeMetricsLazyRefreshTimer`
  - `closeMetricsLastScrollY`, `closeMetricsScrollDelta`
  - `syncCloseMetricsCompletion`
  - `collectNextCloseMetricCodes`
  - `scheduleCloseMetricsLazyRefresh`
  - `requestLazyCloseMetrics`
  - `requestNextCloseMetricsBatch`
  - `handleCloseMetricsScrollTrigger`
  - next button click binding, document scroll handler, `refreshTop100`
- DOM row 순서, Set mutation, fetch 호출, refresh 예약, event binding은 아직 inline 유지한다.
- 다음 단계에서는 request/fetch 분리 또는 controls 분리 여부를 별도 검토한다.

## 27. Direct API debug 분리 설계/위험점 점검 5N

- 5N은 Direct API debug 분리 설계와 위험점 점검 단계다.
- 실제 debug JS 파일 생성, 함수 이동, `script type="module"` 전환, `import/export` 추가는 아직 수행하지 않는다.
- 현재 Direct API debug는 운영 화면 기본 hidden/off 상태지만, 진단 fetch/update loop는 inline main script에서 계속 실행된다.

### Direct API debug DOM/state

- DOM:
  - `#direct-api-debug-panel` / `directApiDebugPanel`: Direct API debug 표시줄 root. 기본 `hidden`.
  - `#debug-toggle` / `debugToggle`: topbar `진단` toggle button.
  - debug item markup: `direct-api-debug-item`, `direct-api-debug-ok`, `direct-api-debug-lag`, `direct-api-debug-wait`.
- localStorage:
  - `debugPanelVisibleStorageKey = STOCKBOARD_STORAGE_KEYS.debugPanelVisible`
  - 실제 key: `stockboard.debugPanelVisible`
- runtime state/constants:
  - `DIRECT_API_DEBUG_CODES = ['000660', '005930', '009150', '402340', '005380']`
  - `DIRECT_API_DEBUG_REFRESH_MS = 500`
  - `directApiDebugLoading`: overlapping fetch 방지 flag
  - panel `hidden`, toggle `active`, toggle `aria-pressed`, panel `aria-label`

### Direct API debug 함수 목록

- 표시/숨김:
  - `loadDebugPanelVisible()`
  - `saveDebugPanelVisible(visible)`
  - `applyDebugPanelVisible(visible)`
  - `debugToggle` click event binding
- payload/value helpers:
  - `directApiDebugFirstValue(row, keys)`
  - `directApiDebugQuote(payload, code)`
  - `directApiDebugSource(quote)`
  - `directApiDebugSequence(payload, quote)`
  - `directApiDebugTimeText(value)` - 현재 사용 여부가 낮은 helper 후보로 보이지만 5N에서는 삭제하지 않는다.
- table DOM compare helpers:
  - `readTableDomPriceForCode(code)`
  - `readTableDomChangeRateForCode(code)`
- fetch/render loop:
  - `updateDirectApiDebugPanel(payload, fetchStartedAt, fetchDurationMs)`
  - `loadDirectApiDebug()`
  - `startDirectApiDebugPanel()`

### API 호출 경로

- Direct API debug fetch:
  - `/api/realtime?codes=000660,005930,009150,402340,005380`
  - `cache: 'no-store'`
- `/api/top100`을 직접 호출하지 않는다.
- `/api/realtime_provider_status`를 직접 호출하지 않는다.
- table 표시값 비교는 `#candidate-board`와 `#trading-board`의 현재 DOM row cell text를 읽어 수행한다.
- hidden 상태에서도 `startDirectApiDebugPanel()`이 `loadDirectApiDebug()`와 500ms interval을 시작하므로 API 호출과 `directApiDebugPanel.innerHTML` 갱신이 계속된다.

### 표시/숨김/localStorage 정책

- 운영 화면 기본값은 hidden/off다.
- topbar `진단` 버튼으로 panel 표시/숨김을 toggle한다.
- 사용자가 바꾼 상태는 `stockboard.debugPanelVisible`에 `1`/`0`으로 저장한다.
- reload 후 `loadDebugPanelVisible()` 결과를 `applyDebugPanelVisible()`로 반영한다.
- visible 상태는 panel `hidden`, button `active` class, button `aria-pressed`로 표현한다.
- hidden 상태에서도 진단 로직은 제거되지 않으며, 현재 구현은 DOM 업데이트도 계속 수행한다.

### 위험 의존성

- `nativeTitleObserver`가 debug item의 native `title`을 제거하고 `aria-label`로 보존한다. debug render가 `title` markup을 만들기 때문에 observer와의 연결을 분리 단계에서 깨뜨리면 native tooltip 중복이 재발할 수 있다.
- Direct API debug panel은 운영 화면에서 반드시 기본 hidden이어야 한다.
- localStorage key는 이미 `stockboard_state.js`의 `STOCKBOARD_STORAGE_KEYS.debugPanelVisible`로 분리되어 있으므로 5O에서 key literal을 새로 만들면 안 된다.
- `debugToggle`은 Fast/Graphic/다음 버튼과 같은 topbar controls 영역에 있다. controls 분리 전에는 event binding 이동 위험이 있다.
- `readTableDomPriceForCode()`와 `readTableDomChangeRateForCode()`는 table cell index 4/5에 의존한다. render column 구조가 바뀌면 debug 결과가 DIFF로 흔들릴 수 있다.
- 500ms debug API loop는 hidden 상태에서도 계속 돈다. 5O에서 성능 최적화를 시도할 경우 운영 진단 정책 변화가 되므로 별도 단계로 분리하는 편이 안전하다.
- `updateDirectApiDebugPanel()`은 panel hidden 상태에서도 `innerHTML`을 갱신한다. DOM update 최소화는 가능하지만 이번 설계 단계에서는 동작 변경 후보로만 기록한다.
- debug item `title` cleanup은 tooltip singleton과 직접 연결되지는 않지만 native title 제거 정책 전체와 연결된다.

### 5O 최소 분리 후보

- `docs/assets/stockboard_debug.js` 생성은 가능하지만 5O 범위는 좁게 잡는다.
- 5O 이동 후보:
  - debug URL 생성 helper: codes -> `/api/realtime?codes=...`
  - debug payload value pick helper: `directApiDebugFirstValue`
  - quote 선택 helper: `directApiDebugQuote`
  - source/sequence helper: `directApiDebugSource`, `directApiDebugSequence`
  - visible state load/save/apply helper는 DOM/localStorage 의존이 작지 않으므로 이동 시 `debugToggle`, `directApiDebugPanel`, storage key를 인자로 받는 형태가 안전하다.
- 5O 보류 후보:
  - actual fetch loop: `loadDirectApiDebug`, `startDirectApiDebugPanel`
  - panel render 전체: `updateDirectApiDebugPanel`
  - topbar button event binding
  - `nativeTitleObserver`
  - table DOM compare helpers가 cell index에 의존하는 부분
  - refresh/main loop와 가까운 interval 시작 위치

### Direct API debug 분리 위험도 표

| 구성요소 | 현재 위치 | DOM 의존도 | API 의존도 | localStorage 의존도 | 분리 위험도 | 5O 이동 여부 | 보류 사유 |
|---|---|---:|---:|---:|---:|---|---|
| debug panel root/toggle refs | inline constants/state | 높음 | 없음 | 낮음 | 중간 | 보류 | topbar controls와 직접 연결 |
| `loadDebugPanelVisible`/`saveDebugPanelVisible` | inline localStorage helpers | 낮음 | 없음 | 높음 | 낮음~중간 | 후보 | key는 `StockBoardState` alias를 재사용해야 함 |
| `applyDebugPanelVisible` | inline controls helpers | 높음 | 없음 | 없음 | 중간 | 신중 검토 | DOM refs를 인자로 받으면 분리 가능 |
| `directApiDebugFirstValue` | inline debug section | 없음 | 낮음 | 없음 | 낮음 | 후보 | 순수 payload helper |
| `directApiDebugQuote` | inline debug section | 낮음 | 중간 | 없음 | 낮음~중간 | 후보 | `normalizeCode` inline 의존 유지 필요 |
| source/sequence helpers | inline debug section | 없음 | 낮음 | 없음 | 낮음 | 후보 | payload helper |
| table DOM compare helpers | inline debug section | 높음 | 없음 | 없음 | 중간~높음 | 보류 | board DOM selector와 cell index 4/5 의존 |
| `updateDirectApiDebugPanel` | inline debug section | 매우 높음 | 중간 | 없음 | 높음 | 보류 | render markup/title/native cleanup과 결합 |
| `loadDirectApiDebug` | inline debug section | 중간 | 높음 | 없음 | 높음 | 보류 | fetch, loading flag, render callback 결합 |
| `startDirectApiDebugPanel` | inline debug section | 낮음 | 높음 | 없음 | 높음 | 보류 | 500ms interval과 boot order 영향 |
| debug button click binding | inline controls/event binding | 높음 | 없음 | 중간 | 중간~높음 | 보류 | controls 분리 전까지 inline 유지가 안전 |
| `nativeTitleObserver` interaction | inline tooltip section | 매우 높음 | 없음 | 없음 | 높음 | 보류 | debug item `title` cleanup과 native tooltip 중복 방지 정책 |

- 다음 5O에서는 순수 payload/URL helper 위주로만 분리하고, fetch loop/render/event binding/native title cleanup은 inline 유지하는 방향이 가장 안전하다.

## 28. Direct API debug 최소 helper 분리 + hidden fetch 중단 5O

- 5O에서 `docs/assets/stockboard_debug.js`를 생성했다.
- ES module은 아직 사용하지 않으며 `script type="module"` 전환과 `import/export` 추가는 수행하지 않는다.
- 외부 파일은 `window.StockBoardDebug` namespace를 사용한다.
- HTML 로드 순서:
  1. `assets/stockboard_format.js`
  2. `assets/stockboard_state.js`
  3. `assets/stockboard_visual_cells.js`
  4. `assets/stockboard_tooltip.js`
  5. `assets/stockboard_close_metrics.js`
  6. `assets/stockboard_debug.js`
  7. 기존 inline main script

### 분리한 debug helper

- `loadDebugPanelVisible(storageKey)`
- `saveDebugPanelVisible(storageKey, visible)`
- `buildDirectApiDebugUrl(codes)`
- `pickDirectApiValue(payload, keys)`
- `applyDebugPanelVisibility(panel, button, visible)`

### inline 유지 항목

- `DIRECT_API_DEBUG_CODES`
- `DIRECT_API_DEBUG_REFRESH_MS`
- `directApiDebugLoading`
- `directApiDebugFirstValue`
- `directApiDebugQuote`
- `readTableDomPriceForCode`
- `readTableDomChangeRateForCode`
- `directApiDebugSource`
- `directApiDebugSequence`
- `directApiDebugTimeText`
- `updateDirectApiDebugPanel`
- `loadDirectApiDebug`
- `startDirectApiDebugPanel`
- topbar `debugToggle` click binding
- `nativeTitleObserver`, `stripNativeTitles`
- render/main loop, tooltip, close metrics, selection/clipboard bridge

### hidden/off 상태 fetch 중단

- `loadDirectApiDebug()` 시작부에서 `directApiDebugPanel?.hidden !== false`이면 즉시 `return null` 한다.
- 따라서 운영 기본 hidden/off 상태에서는 500ms interval tick이 유지되어도 `/api/realtime?codes=...` fetch를 수행하지 않는다.
- `진단` 버튼을 ON으로 바꾸면 `applyDebugPanelVisible(true)`와 localStorage 저장 후 `loadDirectApiDebug()`를 1회 즉시 호출한다.
- ON 상태에서만 interval tick이 실제 fetch/update를 수행한다.
- OFF로 바꾸면 panel이 hidden 처리되고 이후 interval tick은 fetch를 skip한다.
- `stockboard.debugPanelVisible` localStorage key, toggle `active` class, `aria-pressed`, panel `hidden` 정책은 유지한다.

### 유지 정책

- Direct API debug 진단 로직 자체는 제거하지 않는다.
- API URL 정책은 `/api/realtime?codes=...`를 유지한다.
- native title cleanup은 inline `nativeTitleObserver`가 계속 담당한다.
- debug item render가 생성하는 native `title`은 기존처럼 observer가 제거하고 `aria-label`로 보존한다.
- Fast/Graphic, 다음 버튼, close metrics next-batch, tooltip singleton, render/main loop는 수정 범위 밖으로 유지한다.

### 남은 보류

- debug fetch loop 전체 분리는 보류한다.
- debug panel render 전체 분리는 보류한다.
- table DOM compare helper는 cell index 4/5 의존이 있어 보류한다.
- topbar controls/event binding 분리는 controls 단계까지 보류한다.
- hidden 상태 DOM update 최소화는 fetch skip으로 대부분 해소되지만, 추가 최적화는 브라우저 검증 후 별도 단계에서 판단한다.
