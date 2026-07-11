# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-12 · 휴장시간 지표 보충, 노트북 OpenAPI 로그인, HTS Clipboard 정책 반영

이 문서는 StockBoard v2의 **현재 운영 상태와 실행 기준**을 기록하는 기준 문서이다. 상세 ThemeBoard 명세와 장마감 통합 복구 설계는 아래 문서로 분리한다.

- ThemeBoard 운영 명세: `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`
- 통합 수집·장마감 복구 설계: `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`

기존 StockBoard v0.3.x 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신과 표시를 안정화하는 구조이다.

현재 완료 상태:

- 현재가·등락률·누적 거래대금 실시간 표시
- 설정형 선발모델 8개, Coverage, 실제 Top300 → Top50 → Top20 → Top5 Funnel
- Top20·Top300 내부 행 위치 고정과 안전 승강
- 5분강도 opt10046 차등 조회, 당일 복원, 정규장 마감 보충
- 잔량비 실시간 호가·opt10004 저속 조회와 마지막 정상값 유지
- 휴장시간 5분강도·순간강도·잔량비 빈칸 통합 보충
- 시장 틱이 0건이어도 동작하는 Qt 독립 타이머
- 완료 상태 250ms 고정 심박 + 실제 drain 10초 주기의 저부하 구조
- collector→worker sender 직렬화·루프 오류 fail-open
- QAxWidget native HWND를 만든 뒤 `CommConnect()`를 호출하는 노트북 호환 로그인
- 대량체결 collector 집계와 당일 지속 저장
- HTML 계산 금지, worker 결과 표시 전용
- 별도 `/theme` ThemeBoard v1 구현
- ThemeBoard 10개 테마, 반응형 4/3/2/1열 카드, 밀도형 순위·구성종목
- ThemeBoard 카드·순위·구성종목에서 Kiwoom HTS/S1 종목 연동
- ThemeBoard 장마감 마지막값을 다음 실제 프리마켓까지 유지
- ThemeBoard 공용 캐시, 비차단 worker lock, 다중 탭 계산 공유

대표님 실전 확인:

- StockBoard 행 위치·정렬·역정렬 정상
- NXT 미거래 종목 정규장 마감 5분강도 표시 정상
- 휴장시간 5분강도·순간강도·잔량비 누락 0개 확인
- 시장 틱 0건에서도 휴장시간 보충 완료 확인
- `qt_timer_market_tick_independent_v3_fixed_heartbeat` 정상
- `PhysicalTimerMs=250`, `EffectiveMs=10000`, `SenderAlive=True`, 오류 0 확인
- 2026-07-11 선발모델 v3 적용 후 기존 선발기준 정상
- 노트북에서 OpenAPI native HWND 패치 후 로그인 정상
- ThemeBoard 화면·10개 카드·2열 태블릿 배치·밀도형 상세 정상
- ThemeBoard 장마감 마지막값 표시 정상
- ThemeBoard 레이더 주도종목 HTS 연동 정상
- ThemeBoard 실측 `compute_ms` 약 3ms, `copy_ms` 약 0.47ms, `lock_wait_ms=0`

아직 실전 재확인이 필요한 항목:

- AHK가 HTS 전송 성공 후 Clipboard를 일반 6자리 코드로 바꾸는 동작
- AHK가 오직 `SBV2|고유번호|6자리코드`만 처리하고 일반 6자리 복사는 무시하는 동작
- 위 AHK 재확인은 2026-07-12 `ka10032 오류 1631`로 런처가 universe 단계에서 중단되어 대기 중

## 2. 브랜치·PR·실행

| 항목 | 값 |
|---|---|
| 병합 대상 기준 브랜치 | `hot-priority-integrated-20260630` |
| 현재 ThemeBoard 검증 브랜치 | `feature/stockboard-themeboard-v1` |
| Draft PR | `#27` |
| 대형 실행기 | `stockboard_v2_large.cmd` |
| 기본 실행기 | `stockboard_v2_live.cmd` |
| StockBoard URL | `http://127.0.0.1:8765/` |
| ThemeBoard URL | `http://127.0.0.1:8765/theme` |
| worker HTTP 포트 | `8765` |
| collector → worker TCP 포트 | `8710` |
| OpenAPI collector Python | `C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe` |

현재 ThemeBoard 브랜치 받기:

```powershell
cd C:\aiTrade

git status --short
git fetch origin
git switch feature/stockboard-themeboard-v1
git pull --ff-only origin feature/stockboard-themeboard-v1

.\stockboard_v2_large.cmd restart-fast
```

주의:

- `feature/stockboard-themeboard-v1`에 있으면서 `hot-priority-integrated-20260630`를 `pull --ff-only` 대상으로 지정하지 않는다.
- 현재 브랜치에만 있는 ThemeBoard 커밋이 있으므로 브랜치를 확인한 뒤 같은 원격 브랜치를 받는다.
- 문서의 표준 동기화 절차에서 `git reset --hard`는 사용하지 않는다.
- 브라우저 변경은 `Ctrl+F5`로 확인한다.

## 3. 현재 구조

```text
Kiwoom OpenAPI 32bit
→ realtime_v2/collector32_large_bidask.py
→ realtime_v2/collector32_large.py
→ TCP JSON event
→ realtime_v2/worker64_guarded_large_bidask.py
→ realtime_v2/worker64_guarded_large.py
→ StockBoard candidate/features/display order
→ SSE /api/v2/stream
→ docs/stockboard_v2.html

같은 worker 상태
→ realtime_v2/theme_board_patch.py
→ stockboard_theme_engine.py
→ 공용 ThemeBoard cache/SSE
→ /api/v2/themes/*
→ docs/stockboard_theme_v1.html
```

| 계층 | 파일 | 역할 |
|---|---|---|
| universe | `realtime_v2/build_universe.py` | 거래대금 universe와 전일 거래대금 결합 |
| 실시간 수집 | `realtime_v2/collector32.py` | Kiwoom 실시간 체결·호가 이벤트 수신 |
| 대형 수집 | `realtime_v2/collector32_large.py` | 대량체결 집계와 5분강도 정상값 저장 |
| 최종 collector | `realtime_v2/collector32_large_bidask.py` | 최종 patch 설치 순서와 OpenAPI collector 진입점 |
| Qt 메인 스레드 | `realtime_v2/qt_main_thread_openapi_patch.py` | QApplication·QAxWidget을 collector 메인 스레드에서 실행 |
| native HWND | `realtime_v2/openapi_native_handle_patch.py` | 로그인 전에 QAxWidget native Windows 핸들 생성·검증 |
| 휴장 통합 보충 | `realtime_v2/offhours_metric_completion_patch.py` | 5분강도·순간강도·잔량비 직접 순차 조회 |
| 휴장 복원력 | `realtime_v2/offhours_metric_resilience_patch.py` | 특정 종목 오류·stale rate-gap 자동 복구 |
| 독립 타이머 | `realtime_v2/offhours_metric_timer_driver_patch.py` | 시장 틱 비의존 고정 심박과 mode별 drain 주기 |
| sender 생존 | `realtime_v2/collector_sender_resilience_patch.py` | 직렬화·송신 루프 오류 fail-open |
| 순간강도 연결 | `realtime_v2/execution_strength_alias_patch.py` | opt10046 snapshot을 `execution_strength`로 동기화 |
| 지표 보존 | `realtime_v2/session_metric_hold_patch.py` | 다음 실제 프리마켓 전까지 마지막 유효 지표 유지 |
| worker | `realtime_v2/worker64_guarded_large.py` | 상태·OHLC·강도·대량체결·후보모델 |
| 최종 worker | `realtime_v2/worker64_guarded_large_bidask.py` | 잔량비 표시와 ThemeBoard fail-open 설치 |
| 5분강도 | `realtime_v2/strength5m_scheduler.py` | 장중 S1·Top20·Hidden50·Top300 차등 opt10046 |
| 잔량비 | `realtime_v2/orderbook_thin_scheduler.py` | 장중 저속 opt10004 단건 조회 |
| 시장시간 | `realtime_v2/market_session.py` | 프리·정규·애프터·휴장·특별일 판단 |
| 시장달력 | `config/stockboard_market_calendar.json` | 기본 거래시간과 특별일 설정 |
| 모델 설정 | `configs/candidate_models/*.json` | 모델별 점수·guard·Funnel |
| 공통 파생값 | `stockboard_candidate_features.py` | 승인 원천에서 공통 지표 1회 계산 |
| 모델 실행 | `stockboard_candidate_engine.py` | 점수·등급·Coverage·Funnel |
| 행 위치 | `stockboard_display_order.py` | lane 내부 고정과 안전 승강 |
| StockBoard UI | `docs/stockboard_v2.html` | worker 결과 표시 전용 |
| HTS bridge | `scripts/stockboard_kiwoom_link_v1.ahk` | SBV2 전용 명령을 Kiwoom Edit6에 전달 |
| 테마 마스터 | `config/stockboard_theme_master.json` | 10개 테마와 종목별 가중치 |
| 테마 엔진 | `stockboard_theme_engine.py` | 테마 유입·상태·주도주 계산 |
| 테마 연결 | `realtime_v2/theme_board_patch.py` | 공용 캐시·API·마감 보존·정적 UI |
| ThemeBoard UI | `docs/stockboard_theme_v1.html` | 서버 결과 표시와 HTS 연동 |

운영 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- StockBoard와 ThemeBoard는 같은 worker 종목 원천을 공유한다.
- ThemeBoard 때문에 신규 OpenAPI 등록·TR을 추가하지 않는다.
- 휴장시간 지표 보충은 시장 틱이나 provider pending queue에 의존하지 않는다.
- 장마감 통합 복구 coordinator는 별도 승인 설계이며 아직 미구현이다.
- `data/runtime/`은 Git 추적 대상이 아니다.

## 4. 시장시간 정책

시장시간은 `config/stockboard_market_calendar.json` 기준이며 `special_days`가 있으면 특별일 설정이 우선한다.

| phase | 기본 시간 | 현재 정책 |
|---|---|---|
| before_market | 08:00 전 | 마지막 유효값 유지, 휴장 통합 보충 허용, 실제 premarket 시작 시 중단 |
| premarket | 08:00~08:30 | 전일 hold 해제, 새 실시간 수용, 휴장 보충 중단 |
| opening_call | 08:30~09:00 | 실시간 수용 |
| regular | 09:00~15:20 | 실시간 최우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 기존 정규장 마감 보충 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목은 마지막값 유지 |
| closed | 20:00 이후 | 마지막값 유지, 누락 지표 저속 보충 |
| weekend/holiday | 주말·휴장일 | 마지막값 유지, 누락 지표 저속 보충 |

보충 종료 시각은 고정 다음 날이 아니라 `next_premarket_datetime()`이 계산한 다음 실제 거래일 프리마켓이다.

## 5. StockBoard 14개 표시 항목

```text
순위 / 전일 / 등급 / 종목명 / 현재가 / 등락률 / 금액(억) / 대금비
일봉 / 잔량비 / 순간강도 / 5분강도 / 프로(억) / 대량체결
```

세부 원천·장마감 fallback·ThemeBoard 중복 관계는 `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`를 따른다.

## 6. 5분강도·순간강도·잔량비 정책

### 6.1 장중 정책

| 항목 | 원천 | 정책 |
|---|---|---|
| 5분강도 | opt10046 `strength_5m` | lane별 차등 조회, 정상 양수값 저장 |
| 순간강도 | 실시간 체결강도 우선 | 누락 시 opt10046 현재 강도 snapshot 연결 |
| 잔량비 | 실시간 호가 우선 | 누락 시 opt10004 저속 단건 조회 |

공통:

- 0·빈값·오류가 마지막 정상 양수값을 덮어쓰지 않는다.
- 같은 거래일 재시작 시 당일 마지막 정상값을 복원한다.
- 장중 조회 우선순위는 `S1 → Top20 → Hidden Top50 → Top300`이다.
- 09:00~09:10 Hidden50·Top300 잔량비 신규조회는 제한한다.
- 잔량비는 현재 선발모델 점수와 ThemeBoard 항목에서 제외한다.

### 6.2 휴장시간 통합 보충

대상 phase:

```text
closed / before_market / weekend / holiday
```

보충 원천:

| 지표 | TR | 한 요청에서 처리 |
|---|---|---|
| 5분강도 | opt10046 | 5분·20분·60분 강도 |
| 순간강도 | opt10046 | 현재 체결강도 snapshot |
| 잔량비 | opt10004 | 총 매수·매도잔량 기반 비율 |

동작 원칙:

- 시장 틱이 없어도 Qt owner-thread 타이머가 독립 실행한다.
- provider pending queue를 사용하지 않고 QAx owner thread에서 직접 한 종목씩 요청한다.
- 최소 요청 간격 2초, 단건 hard timeout 12초다.
- 실패 재시도는 5분 → 30분 → 2시간이며 이후 2시간 간격을 반복한다.
- 특정 종목 timeout·예외가 전체 순차 처리를 멈추지 않는다.
- 빈칸이 0개이면 TR 요청을 하지 않는다.
- 다음 실제 premarket이 시작되면 휴장 보충을 중단한다.

컨트롤러·타이머:

```text
controller = offhours_metric_completion_v1
driver     = qt_timer_market_tick_independent_v3_fixed_heartbeat
```

| 상태 | 물리 QTimer | 실제 drain |
|---|---:|---:|
| 조회 진행·inflight | 250ms | 250ms cadence |
| provider 로그인 대기 | 250ms | 1초 cadence |
| complete·inactive | 250ms | 10초 cadence |

물리 타이머는 항상 살아 있고, 완료 상태에서는 대부분의 tick이 즉시 skip된다. 따라서 `TimerTick`은 계속 증가하지만 `DrainRun`은 약 10초마다 한 번만 증가한다.

실전 정상 기준:

```text
Mode=complete
PhysicalTimerMs=250
EffectiveMs=10000
TimerTick 지속 증가
DrainRun 약 10초마다 증가
SenderAlive=True
SerializeErrors=0
SenderRecover=0
Errors=0
```

### 6.3 collector sender 생존

- 상태 이벤트 한 건이 JSON 직렬화되지 않아도 그 이벤트만 버리고 sender thread는 계속 실행한다.
- socket·drain·loop 예외가 발생하면 연결을 닫고 자동 재연결한다.
- `collector_status.ts`가 계속 갱신되고 `sender_thread_alive=True`여야 한다.

## 7. OpenAPI 로그인·노트북 호환

collector는 반드시 32비트 Python을 사용한다.

```text
C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe
```

노트북에서 KOA Studio 로그인은 정상인데 StockBoard 로그인 시 `핸들값이 없습니다` 오류가 발생했다. 원인은 QAxWidget 생성 직후 native HWND가 확정되지 않은 상태에서 `CommConnect()`를 호출한 것으로 판정했다.

현재 로그인 순서:

```text
QAxWidget 생성
→ 화면 밖 작은 위치에 show
→ QApplication.processEvents()
→ winId()로 native HWND 생성
→ Windows IsWindow() 검증
→ CommConnect()
```

실전 결과:

- 노트북에서 OpenAPI 로그인 정상 확인
- `login_state=connected`
- native handle 패치는 데스크톱 자동 핸들 생성 환경에도 영향을 주지 않는 보완 구조

## 8. 선발모델 v3

점수 원천:

```text
순위 / 전일 / 현재가 / 등락률 / 금액(억) / 대금비 / 일봉
순간강도 / 5분강도 / 프로(억) / 대량체결
```

현재 제외:

```text
잔량비 / 1분강도 / 1분 거래대금 / 1분 순매수
VWAP / 외인 / 기관 / 미검증 시장지수
```

등록 모델:

| 모델 | 목적 | 상태 |
|---|---|---|
| 5요소 수급선발 v0.1 | 순위상승·대금비·강도·프로·대량체결 | READY |
| 순매수 강도 v0.2 · 5분강도 | 프로그램·대량체결 비중 강화 | READY |
| 순매수 강도 v0.1 · 5분강도 | 수급 요소 균형 | READY |
| 돈쏠림 시작형 · 5분강도 | 순위와 대금비 초기 상승 | READY |
| 조용한 매집형 | 낮은 과열도에서 수급 누적 | READY |
| 폭발 확인형 · 5분강도 | 대금·강도·고가권 동시 확인 | READY |
| 프로그램 동행형 | 프로그램과 실제 체결수급 동행 | READY |
| 보드 대비 상대강도형 | Top300 중앙 등락률 대비 | READY |

기본 모델은 `FIVE_FACTOR_FLOW_V01`이다.

Coverage 상한:

| Coverage | 최고점 |
|---:|---:|
| 90% 이상 | 정상 |
| 75~89% | 79 |
| 60~74% | 69 |
| 60% 미만 | 59·WAIT_DATA |

실제 Funnel:

```text
Top300 entry_score
→ Top50
→ confirmation_score Top20
→ focus_score Top5
→ DisplayOrderController 안전 승강
```

## 9. ThemeBoard 현재 상태

상세 기준은 `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`를 따른다.

핵심 상태:

- 10개 테마, 종목별 가중치 합 1.0
- 테마 1분·5분 유입, 누적대금, 점유율, 확산, 가속, 집중도
- 주도주 TOP3와 전체 구성종목
- 카드·순위·구성종목 종목 클릭 HTS/S1 연동
- 서버 계산, HTML 표시 전용
- 클라이언트 0명이면 계산 0회
- 다중 클라이언트 공용 캐시
- worker lock 비차단
- 장마감 마지막값을 runtime JSON으로 저장·복원
- 다음 실제 프리마켓까지 `LAST_CLOSE` 유지

## 10. HTS/S1 Clipboard 연동

브라우저가 종목 클릭 시 쓰는 명령:

```text
SBV2|<고유번호>|<6자리 종목코드>
예: SBV2|17837976715774|035420
```

AHK 처리 정책:

- 정확히 `SBV2|숫자 고유번호|6자리 코드` 형식만 처리한다.
- 일반 `035420`, `035420_AL`, `035420_NX`, 구 `SB|...` 형식은 모두 무시한다.
- 동일 종목을 다시 클릭해도 고유번호가 달라 다시 처리된다.
- Kiwoom의 유일한 검증된 `Edit6` HWND에 6자리 코드를 넣는다.
- readback이 동일한지 확인한 뒤 해당 Edit6 HWND에만 Enter를 보낸다.
- 성공 후 Clipboard의 `SBV2|...` 명령을 일반 6자리 코드로 바꾼다.
- 일반 6자리 코드는 명령 형식이 아니므로 중복 HTS 전송이 발생하지 않는다.
- 일반 숫자 6자리를 다른 프로그램에서 복사해도 HTS가 임의로 바뀌지 않는다.

상태 파일:

```text
data/runtime/stockboard_v2/hts_link_status.txt
```

정상 예시:

```text
...|ok|035420|... / stockboard_v2_command / ... / clipboard saved 035420
```

현재 strict command-only 코드 반영은 완료됐으며, 실제 재실행 확인은 `ka10032 오류 1631` 해결 후 진행한다.

## 11. 성능 정책

- collector 체결 처리 경로에 ThemeBoard 계산을 넣지 않는다.
- 후보모델은 선택된 모델 1개만 계산한다.
- 휴장 보충 타이머의 250ms heartbeat는 시간 비교 후 즉시 반환하며 완료 상태의 실제 계산은 10초마다 한 번이다.
- ThemeBoard는 이벤트가 바뀌고 클라이언트가 있을 때만 공용 계산한다.
- ThemeBoard lock은 `acquire(False)`로 즉시 양보한다.
- JSON 직렬화·파일 저장·테마 계산은 worker lock 밖에서 실행한다.
- HTML은 집계·정렬·점수 계산을 하지 않는다.
- 09:00~09:10 StockBoard pool 렌더 간격은 1초, 이후 250ms다.
- 실전 검증 항목은 stream latency, worker/collector queue, stale, drop, lag이다.

ThemeBoard 관찰값:

| 항목 | 관찰 |
|---|---:|
| compute | 약 3ms |
| copy/lock 점유 | 약 0.47ms |
| lock wait | 0ms |
| lock probe | 약 0.0015ms |
| payload | 약 12KB |

## 12. 검증 상태

자동 검증:

- 후보모델 registry 8개·validation error 0
- 실제 Top300 → Top50 → Top20 → Top5 Funnel
- Coverage 부족 WAIT_DATA와 승격 차단
- 휴장시간 통합 보충·resilience·Qt timer driver 테스트
- collector sender serialization·loop recovery 테스트
- QAxWidget main-thread·native handle 생성 테스트
- ThemeBoard 엔진·캐시·HTTP·마감 hold 테스트
- ThemeBoard 금지 호출 `state.snapshot()`, `state.rows()`, 후보 enrichment 없음
- ThemeBoard HTML에 `.sort`, `.reduce`, `Math.*` 계산 없음
- 10개 테마 마스터 검증
- 장마감 Friday → Monday 08:00 hold 시뮬레이션

대표님 실전 검증:

- StockBoard 기존 기능 정상
- 휴장시간 세 지표 누락 0개
- 시장 틱 0에서도 순차 보충 정상
- v3 고정 심박·10초 drain 정상
- collector sender 생존·오류 0
- 노트북 OpenAPI 로그인 정상
- ThemeBoard UI·밀도·반응형 정상
- 장마감 마지막값 표시 정상
- 기존 HTS 연동 정상
- 성능 진단값 정상 범위

아직 필요한 검증:

1. `ka10032 오류 1631` 이후에도 기존 universe로 기동하는 복구 정책 구현·검증
2. AHK strict `SBV2` 전용 파서와 성공 후 6자리 Clipboard 저장 실전 확인
3. 다음 프리마켓에서 `LAST_CLOSE → LIVE` 전환
4. 다음 정규장에서 ThemeBoard `cache_version`과 성공 계산 증가
5. 기존 StockBoard stream latency·queue·drop 무영향 확인
6. 장마감 통합 복구 설계 구현 후 OpenAPI 요청량과 backoff 검증

## 13. 현재 알려진 운영 이슈

### 13.1 휴장일 ka10032 오류 1631

2026-07-12 노트북에서 `stockboard_v2_large.cmd start-fast` 실행 시 universe 생성 단계에서 다음 응답을 확인했다.

```text
Unexpected ka10032 response
return_msg = 서비스 처리 중 오류 [1631]
return_code = 7
```

현재 동작:

```text
build_universe.py 실패
→ launcher가 build_universe failed로 전체 시작 중단
→ worker·collector·AHK가 시작되지 않음
```

판정:

- AHK 수정과 무관하다.
- QAx/OpenAPI 로그인 문제와도 무관하다.
- Kiwoom REST `ka10032` 일시 오류를 universe 단계에서 fail-closed 처리한 것이 직접 원인이다.
- 현재 코드에는 짧은 재시도와 기존 정상 `universe.json` fallback이 없다.

필요한 개선:

```text
ka10032 짧은 제한 재시도
→ 계속 실패하면 기존 universe.json/codes.txt 유효성 검사
→ 유효하면 stale source를 표시하고 기동 계속
→ 기존 파일도 없거나 손상됐을 때만 전체 중단
```

이 개선은 아직 구현되지 않았다.

## 14. 승인된 다음 개선

`docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`의 통합 설계를 기준으로 한다.

현재 상태는 **설계 승인, 미구현**이다.

핵심 방향:

```text
장중: 기존 실시간 유지 + 마감 직전 초경량 sampler
장마감: 마지막 정상값/파일 우선
누락: 중앙 AfterCloseRecoveryCoordinator가 한 번에 TR 1개
최종 fallback: 분봉 종가 × 거래량 근사값
화면: 숫자 0·빈칸 금지, 출처·추정 여부 명시
```

## 15. PR·문서 이력

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력
- PR #23: NXT 미거래 정규장 마감 5분강도
- PR #24: v2 기준 문서
- PR #25: 설정형 선발모델 8개·Coverage·Funnel
- PR #27: ThemeBoard v1, 장마감 hold, 반응형 UI, HTS 연동 — Draft
- 2026-07-12: 휴장 통합 보충·v3 fixed heartbeat·sender fail-open·노트북 native HWND 로그인·SBV2 전용 AHK 정책 기록
