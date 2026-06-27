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
| 19 | HTMLModuleSplit | TODO |
| 20 | CandidateModelV02 | TODO |
| 21 | ForeignFuturesSource | TODO |
| 22 | BigHandKRT | TODO |
| 23 | DayStrengthBackfill | TODO |
| 24 | MarketSupplyRefresh | TODO |
| 25 | SignalRankingStrategyFormalize | TODO |

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
