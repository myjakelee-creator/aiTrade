# StockBoard Board Platform 공통 속도 및 병목 개선 구현

최종 갱신: 2026-07-12  
상태: 구현 완료 · 로컬 정적/단위 검증 완료 · PC1 실전 재시작 검증 필요

## 1. 구현 목적

StockBoard, ThemeBoard, StrategyBoard와 향후 추가 보드의 상단에 동일한 속도 지표를 표시하고, 현재 확인된 가장 큰 병목인 StockBoard snapshot 중복 계산을 공유 cache로 통합한다.

절대 원칙:

```text
속도 최우선
HTML 시장 데이터 계산 금지
기능별 전용 엔진 유지
collector 실시간 callback 변경 금지
```

## 2. 각 보드 공통 상단 속도 표시

공통 표시 항목:

| 항목 | 의미 | 정상 | 경고 | 위험 |
|---|---|---:|---:|---:|
| 병목 | 서버가 판정한 현재 주요 병목 | GREEN | YELLOW | RED |
| E2E | worker 마지막 이벤트 freshness | 500ms 이하 | 500~3000ms | 3000ms 초과 |
| 수신 | collector가 worker로 보낸 이벤트/초 | 장중 양수 | 장중 0 | 장중 장시간 0 |
| 대기 | collector pending queue | 50 이하 | 51~500 | 500 초과 |
| 드롭 | 직전 1초 증가량 / 누적 | 증가 0 | - | 증가 발생 |
| WorkerQ | 비동기 event log queue | 1000 이하 | 1001~10000 | 10000 초과 |
| CPU | 64비트 worker 프로세스 CPU | 60% 이하 | 60~85% | 85% 초과 |
| Worker | worker Python bit | 64bit | - | 32bit |
| 계산 | 해당 보드 엔진 계산시간 | 보드별 기준 | 경고 기준 | 위험 기준 |
| 직렬 | JSON 직렬화 시간 | 10ms 이하 | 10~20ms | 20ms 초과 |
| 캐시 | 완성 payload 나이 | 보드별 기준 | 경고 기준 | 위험 기준 |
| Payload | 직렬화 payload 크기 | 보드별 기준 | 경고 기준 | 위험 기준 |
| API | 브라우저의 공통 속도 API RTT | 10ms 이하 | 10~50ms | 50ms 초과 |
| 렌더 | 브라우저 마지막 렌더 및 p95 | 16ms 이하 | 16~40ms | 40ms 초과 |

시장 데이터의 산술계산·점수·등급·순위는 HTML에서 수행하지 않는다. 브라우저는 서버가 완성한 속도 문구와 색상을 표시하고, 비시장 성능값인 API RTT와 render 시간을 측정한다.

## 3. 확인된 가장 큰 병목

기존 StockBoard SSE는 각 브라우저 연결이 독립적으로 다음 작업을 반복했다.

```text
state.snapshot(300)
→ 300행 deepcopy
→ 거래대금 정렬
→ 후보모델 전체 계산
→ DisplayOrder 적용
→ JSON 직렬화
```

기존 기본 SSE 간격이 100ms이므로 브라우저 한 개만 열어도 초당 최대 10회 계산될 수 있고, 브라우저 수가 늘면 계산과 직렬화가 접속자 수에 비례해 증가했다.

기존 문서의 합성 300행 후보모델 계산 중앙값 35~51ms를 기준으로 한 공학적 추정:

| StockBoard 접속 수 | 기존 snapshot 계산 횟수/초 | 후보 계산 CPU 시간 추정/초 |
|---:|---:|---:|
| 1 | 약 10회 + 상태파일 1회 | 약 385~561ms |
| 2 | 약 20회 + 상태파일 1회 | 약 735~1071ms |
| 3 | 약 30회 + 상태파일 1회 | 약 1085~1581ms |

위 값은 실전 측정값이 아니라 기존 합성 계산시간을 적용한 구조적 추정이다. 직렬화·deepcopy·브라우저 렌더 시간은 별도다.

## 4. 구현한 획기적 개선

### 4.1 StockBoard shared snapshot cache

```text
여러 StockBoard SSE client
→ StockBoardSnapshotCacheService 계산 1회
→ JSON 직렬화 1회
→ 동일 bytes를 모든 client에 재사용
```

특징:

```text
client 수와 계산 횟수 분리
이벤트가 바뀌지 않으면 재계산 생략
candidate model 변경 시 즉시 재계산
상태파일 writer도 같은 cache 재사용
payload bytes 사전 직렬화
cache version 변경 때만 SSE snapshot 전송
```

### 4.2 적응형 계산 주기

| 상황 | 계산 주기 |
|---|---:|
| 일반 정상 | 100ms |
| 09:00~09:10 | 250ms |
| 계산 60ms 이상 | 500ms |
| 계산 100ms 이상 3회 연속 | 보호모드 1000ms |
| 보호모드에서 60ms 이하 5회 | 정상 복귀 |

장개시 09:00~09:10에는 수신 안정성을 화면 점수 갱신 빈도보다 우선한다.

### 4.3 기존 동작 보존

```text
StockBoardEngine 계산식 변경 없음
ThemeBoardEngine 계산식 변경 없음
후보모델 배점 변경 없음
collector 등록/FID/TR 변경 없음
HTML 시장 계산 추가 없음
기존 URL 유지
```

## 5. 전체 처리 단계별 예상 속도

아래 표는 구조와 기존 관찰값을 바탕으로 한 공학적 예상이며, 공통 속도바로 실전 측정 후 교체한다.

| 단계 | 정상 예상 | 폭주/병목 예상 | 주요 위험 |
|---|---:|---:|---|
| Kiwoom callback 수신·정규화 | 0.05~0.30ms/event | 0.5ms 이상 | callback 내부 추가 작업 |
| collector coalesce·queue | 0.02~0.20ms/event | 수~수십ms | pending 증가 |
| localhost TCP 전달 | 0.1~2ms | 10ms 이상 | sender queue |
| worker event 적용 | 0.02~0.30ms/event | 1ms 이상 | lock 경합 |
| 300행 deepcopy·기본 정렬 | 0.3~3ms | 5ms 이상 | 모든 client 반복 |
| 후보모델 계산 | 35~51ms 기존 합성 기준 | 100ms 이상 | 가장 큰 CPU 병목 |
| DisplayOrder | 1~5ms 예상 | 10ms 이상 | 잦은 전체 정렬 |
| StockBoard JSON 직렬화 | 3~15ms 예상 | 20ms 이상 | client별 중복 |
| ThemeBoard 최소필드 복사 | 약 0.47ms 관찰 | 2ms 이상 | worker lock |
| ThemeBoard 계산 | 약 3ms 관찰 | 30ms 이상 | 테마 확대 |
| StrategyBoard 계산 | 5~10ms 목표 | 30ms 이상 | 중요구간 history 확대 |
| localhost API/SSE | 0.2~5ms | 50ms 이상 | thread/브라우저 정체 |
| StockBoard 브라우저 렌더 | 5~40ms 예상 | 40ms 초과 | 전체 DOM 재생성 |
| ThemeBoard 브라우저 렌더 | 2~15ms 예상 | 40ms 초과 | 카드 수 확대 |

## 6. 남은 병목과 다음 개선 순위

### 1순위 — Dirty-row StockBoard payload

현재 공유 cache도 snapshot 갱신 시 300행 전체 계산과 직렬화를 수행한다.

다음 단계:

```text
변경 종목 code 집합 관리
→ 가격·등락·강도는 dirty row patch
→ 거래대금 순위/후보점수는 별도 저속 full recompute
→ 브라우저는 행 key 기반 부분 갱신
```

권장 lane:

```text
Fast lane  50~100ms  현재가·등락률·체결강도·거래대금
Rank lane  250~500ms 거래대금 순위
Model lane 500~1000ms 후보모델·등급·Funnel
Context    5~30초    프로그램·OHLC·시장정보
```

이 분리가 단일 화면에서도 CPU와 렌더 병목을 가장 크게 줄일 수 있다.

### 2순위 — BoardDataHub 한 번 복사

ThemeBoard와 향후 StrategyBoard가 각각 worker lock을 얻지 않고 공용 최소필드 snapshot을 한 번 복사해 공유한다.

### 3순위 — 서버 완성 diff

HTML에서 비교·계산하지 않고 서버 엔진이 다음을 완성한다.

```text
changed_rows
removed_codes
display_order_changes
display_text
display_class
```

### 4순위 — 브라우저 가상화

Top300 전체 DOM을 매 갱신마다 다시 만들지 않고 보이는 행과 변경 행만 갱신한다.

### 5순위 — OpenAPI 중앙 조정

장마감 분봉·강도·프로그램·잔량 조회는 AfterCloseRecoveryCoordinator 한 곳에서 한 번에 1건만 수행한다.

## 7. 공통 페이짂 연결

```text
/          StockBoard
/theme     ThemeBoard
/strategy  StrategyBoard 준비중
/boards    경량 Board Hub
```

모든 보드 상단:

```text
StockBoard | ThemeBoard | StrategyBoard 준비중 | Boards | 새 창
```

기본은 같은 탭 이동이다. iframe, 자동 다중창, 숨은 preload를 사용하지 않는다.

## 8. 64비트 worker 강제

`realtime_v2/worker64_guarded_large_bidask.py`는 실행 시 Python bit를 확인한다.

```text
64bit → 정상 시작
32bit → 즉시 종료
data/runtime/stockboard_v2/worker_python_bits_error.txt 기록
```

PC1에서 발생한 `Python310-32로 worker64 실행` 상태를 더 이상 조용히 허용하지 않는다.

## 9. 신규 API

```text
/boards
/api/v2/boards
/api/v2/boards/performance
/api/v2/boards/shell.css
/api/v2/boards/shell.js
```

기존 URL과 API는 유지한다.

## 10. 구현 파일

```text
realtime_v2/board_platform/__init__.py
realtime_v2/board_platform/registry.py
realtime_v2/board_platform/stockboard_cache.py
realtime_v2/board_platform/performance.py
realtime_v2/board_platform/assets.py
realtime_v2/board_platform/http_patch.py
realtime_v2/worker64_guarded_large_bidask.py
tests/test_board_platform_stockboard_cache.py
tests/test_board_platform_performance.py
tests/test_board_platform_http_patch.py
```

## 11. 완료 검증

구현 환경 검증:

```text
Python py_compile 통과
Board Platform 단위 테스트 5개 통과
공통 shell JavaScript node --check 통과
```

PC1 실전 확인 필요:

```text
worker가 64bit로 실행
/와 /theme 상단 공통 속도바 표시
/boards 정상
/api/v2/boards/performance 정상
StockBoard 창 2개에서 cache 계산이 client별로 증가하지 않음
09:00~09:10 pending·drop·E2E 악화 없음
ThemeBoard compute/copy 기존 수준 유지
```
