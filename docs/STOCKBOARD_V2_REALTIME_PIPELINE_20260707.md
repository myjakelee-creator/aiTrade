# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-12 · universe 장애 복구, strict HTS Clipboard 실전 확인 반영

이 문서는 StockBoard v2의 **현재 운영 상태와 실행 기준**을 기록하는 기준 문서이다.

관련 문서:

- ThemeBoard 운영 명세: `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`
- 통합 수집·장마감 복구 설계: `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`

기존 StockBoard v0.3.x 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신과 표시를 보호한다.

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
- QAxWidget native HWND 생성 후 `CommConnect()`를 호출하는 노트북 호환 로그인
- `ka10032` 제한 재시도와 검증된 기존 universe fallback
- 기존 universe fallback 성공 시 `codes.txt` 원자적 재생성
- 손상되거나 지나치게 작은 cache는 사용하지 않고 시작 중단
- strict `SBV2|고유번호|6자리코드` 전용 AHK bridge
- HTS 전달 성공 후 Clipboard를 일반 6자리 종목코드로 저장
- 대량체결 collector 집계와 당일 지속 저장
- HTML 계산 금지, worker 결과 표시 전용
- 별도 `/theme` ThemeBoard v1 구현
- ThemeBoard 10개 테마, 반응형 카드, 밀도형 순위·구성종목
- ThemeBoard 장마감 마지막값을 다음 실제 프리마켓까지 유지
- ThemeBoard 공용 cache, 비차단 worker lock, 다중 탭 계산 공유

대표님 실전 확인:

- StockBoard 행 위치·정렬·역정렬 정상
- NXT 미거래 종목 정규장 마감 5분강도 표시 정상
- 휴장시간 5분강도·순간강도·잔량비 누락 0개
- 시장 틱 0건에서도 휴장시간 보충 완료
- `qt_timer_market_tick_independent_v3_fixed_heartbeat` 정상
- `PhysicalTimerMs=250`, `EffectiveMs=10000`, `SenderAlive=True`, 오류 0
- 선발모델 v3 적용 후 기존 선발기준 정상
- 노트북 OpenAPI native HWND 패치 후 로그인 정상
- ThemeBoard 화면·10개 카드·2열 태블릿·밀도형 상세 정상
- ThemeBoard 장마감 마지막값 표시 정상
- ThemeBoard 실측 `compute_ms` 약 3ms, `copy_ms` 약 0.47ms, `lock_wait_ms=0`
- strict SBV2 종목 클릭 → Kiwoom HTS 전환 → Clipboard 6자리 저장 정상 관찰

현재 실전 재현 대기:

- 실제 `ka10032` 오류가 다시 발생했을 때 `stale_fallback`으로 기동이 계속되는지 확인
- 이번 확인에서는 `universe_source_status=live`, `universe_fallback_active=false`, `seed_fetch_attempt_count=1`, `count=202`로 정상 live build가 성공함

## 2. 브랜치·PR·실행

| 항목 | 값 |
|---|---|
| 병합 대상 기준 브랜치 | `hot-priority-integrated-20260630` |
| 현재 검증 브랜치 | `feature/stockboard-themeboard-v1` |
| Draft PR | `#27` |
| 대형 실행기 | `stockboard_v2_large.cmd` |
| 실제 PowerShell 실행기 | `scripts/stockboard_v2_large_safe.ps1` |
| 기본 실행기 | `stockboard_v2_live.cmd` |
| StockBoard URL | `http://127.0.0.1:8765/` |
| ThemeBoard URL | `http://127.0.0.1:8765/theme` |
| worker HTTP 포트 | `8765` |
| collector → worker TCP 포트 | `8710` |
| OpenAPI collector Python | `C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe` |

현재 브랜치 받기와 실행:

```powershell
cd C:\aiTrade

git status --short
git fetch origin
git switch feature/stockboard-themeboard-v1
git pull --ff-only origin feature/stockboard-themeboard-v1

.\stockboard_v2_large.cmd restart-fast
```

주의:

- 현재 브랜치와 다른 원격 브랜치를 `pull --ff-only` 대상으로 지정하지 않는다.
- 표준 동기화 절차에서 `git reset --hard`를 사용하지 않는다.
- 브라우저 변경은 `Ctrl+F5`로 확인한다.

## 3. 현재 구조

```text
Kiwoom OpenAPI 32bit
→ realtime_v2/collector32_large_bidask.py
→ realtime_v2/collector32_large.py
→ TCP JSON event
→ realtime_v2/worker64_guarded_large_bidask.py
→ realtime_v2/worker64_guarded_large.py
→ candidate/features/display order
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
| universe | `realtime_v2/build_universe.py` | ka10032 universe, 전일대금, 재시도, cache fallback 검증 |
| 대형 안전 실행 | `scripts/stockboard_v2_large_safe.ps1` | process 정리, universe 결과 확인, worker·collector·AHK 기동 |
| 실시간 수집 | `realtime_v2/collector32.py` | Kiwoom 실시간 체결·호가 수신 |
| 대형 수집 | `realtime_v2/collector32_large.py` | 대량체결·5분강도 정상값 저장 |
| 최종 collector | `realtime_v2/collector32_large_bidask.py` | 최종 patch 설치와 collector 진입점 |
| Qt 메인 스레드 | `realtime_v2/qt_main_thread_openapi_patch.py` | QApplication·QAxWidget owner thread |
| native HWND | `realtime_v2/openapi_native_handle_patch.py` | 로그인 전 Windows 핸들 생성·검증 |
| 휴장 통합 보충 | `realtime_v2/offhours_metric_completion_patch.py` | 강도·잔량비 직접 순차 조회 |
| 휴장 복원력 | `realtime_v2/offhours_metric_resilience_patch.py` | timeout·예외·stale gap 복구 |
| 독립 타이머 | `realtime_v2/offhours_metric_timer_driver_patch.py` | 시장 틱 비의존 심박과 drain 주기 |
| sender 생존 | `realtime_v2/collector_sender_resilience_patch.py` | 직렬화·송신 오류 fail-open |
| 순간강도 연결 | `realtime_v2/execution_strength_alias_patch.py` | opt10046 snapshot을 순간강도로 연결 |
| 지표 보존 | `realtime_v2/session_metric_hold_patch.py` | 다음 실제 premarket까지 마지막 유효값 유지 |
| worker | `realtime_v2/worker64_guarded_large.py` | 상태·OHLC·강도·대량체결·후보모델 |
| 최종 worker | `realtime_v2/worker64_guarded_large_bidask.py` | 잔량비·ThemeBoard fail-open 설치 |
| 5분강도 | `realtime_v2/strength5m_scheduler.py` | lane별 opt10046 차등 조회 |
| 잔량비 | `realtime_v2/orderbook_thin_scheduler.py` | 장중 opt10004 저속 조회 |
| 시장시간 | `realtime_v2/market_session.py` | 프리·정규·애프터·휴장·특별일 |
| 시장달력 | `config/stockboard_market_calendar.json` | 기본 거래시간·특별일 |
| 후보모델 | `configs/candidate_models/*.json` | 모델별 점수·guard·Funnel |
| 행 위치 | `stockboard_display_order.py` | lane 내부 고정·안전 승강 |
| HTS bridge | `scripts/stockboard_kiwoom_link_v1.ahk` | SBV2 전용 명령을 Kiwoom Edit6에 전달 |
| ThemeBoard | `realtime_v2/theme_board_patch.py` | 공용 cache·API·마감 hold |

운영 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- StockBoard와 ThemeBoard는 같은 worker 종목 원천을 공유한다.
- ThemeBoard 때문에 신규 OpenAPI 등록·TR을 추가하지 않는다.
- 휴장시간 지표 보충은 시장 틱이나 provider pending queue에 의존하지 않는다.
- `data/runtime/`은 Git 추적 대상이 아니다.

## 4. universe 시작 복구 정책

정상 경로:

```text
ka10032 live 조회
→ tradable master 필터
→ 전일 거래대금 결합
→ universe.json 원자적 저장
→ codes.txt 원자적 저장
```

일시 오류 경로:

```text
ka10032 최대 3회 제한 재시도
→ 기본 1초, 2초 간격
→ 계속 실패하면 기존 universe.json 검증
→ schema_version=1 확인
→ 유효한 고유 6자리 종목 기본 20개 이상 확인
→ 유효하면 stale_fallback metadata를 붙여 기동 계속
→ 기존 cache가 없거나 손상·과소이면 시작 중단
```

fallback metadata:

```text
universe_source_status=stale_fallback
universe_fallback_active=true
universe_fallback_reason
universe_original_source
universe_original_built_at
universe_original_trading_date
fallback_valid_item_count
fallback_invalid_item_count
```

정상 live metadata:

```text
universe_source_status=live
universe_fallback_active=false
seed_fetch_attempt_count
```

안전 런처 정책:

- `build_universe.py` 종료코드 0은 live build 또는 검증된 cache fallback 성공을 뜻한다.
- 비정상 종료코드는 live와 cache 모두 사용할 수 없다는 뜻이므로 런처가 중단한다.
- 런처가 단순 `Test-Path universe.json`만으로 손상 가능성이 있는 파일을 강제 사용하지 않는다.

자동 검증:

- 두 번 실패 후 세 번째 live 성공
- live 실패 후 정상 cache fallback
- 반복 fallback에서도 최초 live 생성일·거래일 보존
- `codes.txt` 재생성
- cache 과소·손상·부재 시 중단
- 안전 런처의 무검증 이중 fallback 금지

실전 상태:

- 2026-07-12 재실행에서는 live 조회가 첫 시도에 성공했다.
- 실제 오류 1631 상황의 fallback 기동은 다음 오류 발생 때 확인한다.

### PowerShell JSON 확인 주의

`universe.json`은 UTF-8로 저장한다. Windows PowerShell에서 인코딩을 생략하면 한글 종목명이 깨지고 `ConvertFrom-Json` 오류처럼 보일 수 있다.

```powershell
$u = Get-Content `
  C:\aiTrade\data\runtime\stockboard_v2\universe.json `
  -Raw -Encoding UTF8 |
  ConvertFrom-Json
```

## 5. 시장시간·휴장 보충 정책

| phase | 기본 시간 | 정책 |
|---|---|---|
| before_market | 08:00 전 | 마지막값 유지, 휴장 보충 허용 |
| premarket | 08:00~08:30 | 전일 hold 해제, 휴장 보충 중단 |
| opening_call | 08:30~09:00 | 실시간 수용 |
| regular | 09:00~15:20 | 실시간 최우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 정규장 마감값 보충 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목 마지막값 유지 |
| closed | 20:00 이후 | 마지막값 유지, 누락 지표 저속 보충 |
| weekend/holiday | 주말·휴장일 | 마지막값 유지, 누락 지표 저속 보충 |

휴장 보충 대상:

```text
closed / before_market / weekend / holiday
```

| 지표 | TR | 처리 |
|---|---|---|
| 5분강도 | opt10046 | 5분·20분·60분 강도 |
| 순간강도 | opt10046 | 현재 체결강도 snapshot |
| 잔량비 | opt10004 | 총 매수·매도잔량 기반 비율 |

동작 원칙:

- QAx owner thread에서 한 종목씩 직접 요청한다.
- 최소 요청 간격 2초, 단건 hard timeout 12초다.
- 재시도는 5분 → 30분 → 2시간이며 이후 2시간 간격 반복이다.
- 특정 종목 오류가 전체 drain을 멈추지 않는다.
- 빈칸이 0개이면 TR 요청을 하지 않는다.
- 다음 실제 premarket이 시작되면 중단한다.

정상 기준:

```text
controller=offhours_metric_completion_v1
driver=qt_timer_market_tick_independent_v3_fixed_heartbeat
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

## 6. OpenAPI 로그인·노트북 호환

collector는 반드시 32비트 Python을 사용한다.

로그인 순서:

```text
QAxWidget 생성
→ 화면 밖 작은 위치에 show
→ QApplication.processEvents()
→ winId() native HWND 생성
→ Windows IsWindow() 검증
→ CommConnect()
```

실전 결과:

- 노트북에서 `login_state=connected` 확인
- desktop의 자동 핸들 생성 환경에도 영향을 주지 않는 보완 구조

## 7. StockBoard 14개 표시 항목

```text
순위 / 전일 / 등급 / 종목명 / 현재가 / 등락률 / 금액(억) / 대금비
일봉 / 잔량비 / 순간강도 / 5분강도 / 프로(억) / 대량체결
```

세부 원천·장마감 fallback·ThemeBoard 중복 관계는 통합 복구 설계 문서를 따른다.

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

- 5요소 수급선발 v0.1
- 순매수 강도 v0.2 · 5분강도
- 순매수 강도 v0.1 · 5분강도
- 돈쏠림 시작형 · 5분강도
- 조용한 매집형
- 폭발 확인형 · 5분강도
- 프로그램 동행형
- 보드 대비 상대강도형

실제 Funnel:

```text
Top300 entry_score
→ Top50
→ confirmation_score Top20
→ focus_score Top5
→ DisplayOrderController 안전 승강
```

Coverage 60% 미만은 59점·WAIT_DATA로 제한하고 승격을 차단한다.

## 9. ThemeBoard 현재 상태

상세 기준은 `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`를 따른다.

- 10개 테마, 종목별 활성 가중치 합 1.0
- 테마 1분·5분 유입, 누적대금, 점유율, 확산, 가속, 집중도
- 주도주 TOP3와 전체 구성종목
- 서버 계산, HTML 표시 전용
- 클라이언트 0명이면 계산 0회
- 다중 클라이언트 공용 cache
- worker lock 비차단
- 장마감 snapshot을 다음 실제 premarket까지 `LAST_CLOSE`로 유지

## 10. HTS/S1 Clipboard 연동

브라우저 종목 클릭 명령:

```text
SBV2|<고유번호>|<6자리 종목코드>
예: SBV2|17837976715774|035420
```

AHK 정책:

- 정확히 대문자 `SBV2|숫자 고유번호|6자리 코드`만 처리한다.
- 일반 `035420`, `035420_AL`, `035420_NX`, 구 `SB|...`는 무시한다.
- 동일 종목 재클릭도 고유번호가 달라 다시 처리한다.
- 유일한 검증된 Kiwoom `Edit6` HWND에 6자리 코드를 넣는다.
- readback 성공 후 해당 Edit6 HWND에만 Enter를 전달한다.
- 성공 후 Clipboard를 일반 6자리 종목코드로 바꾼다.
- 일반 6자리 코드는 명령이 아니므로 중복 전송되지 않는다.

대표님 실전 관찰:

```text
StockBoard/ThemeBoard 종목 클릭
→ HTS 종목 전환 정상
→ Clipboard 6자리 코드 저장 정상
```

상태 파일:

```text
data/runtime/stockboard_v2/hts_link_status.txt
```

## 11. 성능 정책

- collector 체결 hot path에 ThemeBoard 계산을 넣지 않는다.
- 후보모델은 선택된 모델 1개만 계산한다.
- 휴장 보충 완료 상태의 실제 drain은 10초마다 한 번이다.
- ThemeBoard는 이벤트가 바뀌고 client가 있을 때만 계산한다.
- ThemeBoard worker lock은 `acquire(False)`로 즉시 양보한다.
- JSON·파일·테마 계산은 worker lock 밖에서 실행한다.
- HTML은 집계·정렬·점수를 계산하지 않는다.
- 실전 검증 항목은 latency, queue, stale, drop, lag이다.

## 12. 검증 상태

자동 검증 완료:

- 후보모델 registry·Funnel·Coverage guard
- 휴장시간 통합 보충·resilience·Qt timer driver
- collector sender serialization·loop recovery
- QAxWidget main-thread·native HWND 생성
- universe retry·cache fallback·codes repair·cache rejection
- 안전 런처의 검증 결과 신뢰와 이중 fallback 금지
- ThemeBoard 엔진·cache·HTTP·마감 hold
- 10개 테마 master

대표님 실전 검증 완료:

- StockBoard 기존 기능
- 휴장시간 세 지표 누락 0개
- 시장 틱 0에서도 순차 보충
- v3 고정 심박·10초 drain
- collector sender 생존·오류 0
- 노트북 OpenAPI 로그인
- ThemeBoard UI·밀도·반응형·장마감 표시
- strict SBV2 HTS 연동과 Clipboard 6자리 저장

남은 실전 검증:

1. 실제 ka10032 실패 시 `stale_fallback`으로 시작 지속
2. 다음 실제 premarket에서 `LAST_CLOSE → LIVE`
3. 다음 정규장에서 ThemeBoard `cache_version`·성공 계산 증가
4. StockBoard stream latency·queue·drop 무영향
5. 테마 master의 실제 시장 정확성 확대

## 13. 이 채팅에서 설계했지만 아직 구현하지 않은 것

### 통합 장마감 복구

`docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`는 승인된 설계이며 아직 구현하지 않았다.

단계:

1. 공용 source metadata 계약
   - `source/status/quality/is_estimated/basis_time/market_scope/coverage`
2. 정규장·통합장 마감 직전 초경량 sampler
   - 정확한 ThemeBoard 1분·5분 마감 유입 저장
3. 공용 OpenAPI arbitration
   - 모든 scheduler의 inflight·pending·gap 판정 통합
4. minute-bar fallback
   - 실제 분봉 거래대금 우선, 없으면 각 분 `종가×거래량`
5. `AfterCloseRecoveryCoordinator`
   - P0~P6 lane, bundle, retry, global backoff, premarket cutoff
6. 기존 scheduler 점진 편입
   - opt10046, program batch, OHLC, opt10004

아직 미구현인 구체 항목:

- 정확한 장마감 ThemeBoard 1분·5분 거래대금 복구
- 모든 표시값의 source·quality·추정 여부 표시
- 실제 무거래와 원천 실패를 `거래없음/직전값/복구실패`로 구분
- AL 정확값과 KRX fallback 품질 보호
- 분봉 fallback의 거래소 범위·단위 실응답 검증
- 중앙 coordinator의 한 번에 TR 1개 보장

금지:

- ThemeBoard가 직접 OpenAPI 조회
- 별도 포트·별도 launcher 추가
- collector 실시간 체결 hot path 대규모 수정
- 추정값이 정확값을 덮어쓰기
- HTML에서 임의 숫자 생성

## 14. PR·문서 이력

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력
- PR #23: NXT 미거래 정규장 마감 5분강도
- PR #24: v2 기준 문서
- PR #25: 설정형 선발모델 8개·Coverage·Funnel
- PR #27: ThemeBoard v1·장마감 hold·HTS 연동 — Draft
- 2026-07-12: 휴장 보충·native HWND·strict SBV2·ka10032 검증 fallback 반영
