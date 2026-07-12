# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-12 · 통합 장마감 복구 구현 및 대표님 실전 정상 확인 반영

이 문서는 StockBoard v2의 **현재 운영 상태와 실행 기준**을 기록하는 기준 문서이다.

관련 문서:

- ThemeBoard 운영 명세: `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`
- 통합 수집·장마감 복구: `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`
- Board Platform 성능 구현: `docs/STOCKBOARD_BOARD_PLATFORM_PERFORMANCE_IMPLEMENTATION_20260712.md`

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
- collector→worker sender 직렬화·루프 오류 fail-open
- QAxWidget native HWND 생성 후 `CommConnect()`를 호출하는 노트북 호환 로그인
- `ka10032` 제한 재시도와 검증된 기존 universe fallback
- 손상·과소 cache 거부와 `codes.txt` 원자적 복원
- strict `SBV2|고유번호|6자리코드` 전용 AHK bridge
- HTS 전달 성공 후 Clipboard를 일반 6자리 종목코드로 저장
- 대량체결 collector 집계와 당일 지속 저장
- StockBoard 공용 snapshot cache와 비동기 Model lane
- 별도 `/theme` ThemeBoard v1, 공용 cache, 장마감 hold
- 공용 source metadata와 정확값 보호
- 정규장·통합장 마감 sampler
- 중앙 `AfterCloseRecoveryCoordinator`
- opt10080 1분봉 기반 1분·5분 거래대금 fallback
- ThemeBoard 부분 복구 Coverage와 정확/추정/무거래 표시
- 복구 state 재시작 보존과 다음 실제 premarket 만료
- HTML 계산 금지, worker 결과 표시 전용

대표님 실전 확인:

- StockBoard 기존 표시·정렬·행 위치 정상
- 휴장시간 5분강도·순간강도·잔량비 누락 0개
- 시장 틱 0건에서도 휴장시간 보충 완료
- `qt_timer_market_tick_independent_v3_fixed_heartbeat` 정상
- `PhysicalTimerMs=250`, 완료 상태 실제 drain 약 10초 주기, sender 오류 0
- 선발모델 v3 정상
- 노트북 OpenAPI native HWND 패치 후 로그인 정상
- ThemeBoard 화면·태블릿·장마감 표시 정상
- strict SBV2 종목 클릭 → HTS 전환 → Clipboard 6자리 저장 정상
- 통합 장마감 복구 적용 후 전체 시스템 정상 작동 확인

계속 관찰할 항목:

- 실제 `ka10032` 오류 재발 시 `stale_fallback` 기동 반복 확인
- 15:30·20:00 sampler 파일과 basis_time의 거래일별 확인
- 실제 opt10080 `_AL` 범위·단위와 KRX fallback 빈도
- 다음 실제 premarket의 `LAST_CLOSE → LIVE` 반복 확인
- 09:00~09:10 latency·queue·drop 성능

## 2. 브랜치·PR·실행

| 항목 | 값 |
|---|---|
| 병합 대상 | `hot-priority-integrated-20260630` |
| 현재 검증 브랜치 | `feature/stockboard-themeboard-v1` |
| Draft PR | `#27` |
| 실행기 | `stockboard_v2_large.cmd` |
| 실제 PowerShell 실행기 | `scripts/stockboard_v2_large_safe.ps1` |
| StockBoard | `http://127.0.0.1:8765/` |
| ThemeBoard | `http://127.0.0.1:8765/theme` |
| worker HTTP | `8765` |
| collector → worker TCP | `8710` |
| collector Python | `C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe` |

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
- 표준 동기화에 `git reset --hard`와 `stash pop`을 사용하지 않는다.
- 브라우저 변경은 `Ctrl+F5`로 확인한다.

## 3. 현재 구조

```text
Kiwoom OpenAPI 32bit
→ collector32_large_bidask.py
→ TCP JSON event
→ worker64_guarded_large_bidask.py
→ 공용 종목 상태
├─ StockBoard snapshot cache / candidate model lane / display order
├─ ThemeBoard cache / theme engine
└─ source metadata / close sampler / recovery state

장마감 누락
→ AfterCloseRecoveryCoordinator
→ 전역 OpenAPI arbitration
→ opt10080 / opt10046 / opt10004 + 기존 program·OHLC snapshot
→ 공용 종목 상태
→ StockBoard·ThemeBoard 동일 복구값 사용
```

주요 파일:

| 영역 | 파일 | 역할 |
|---|---|---|
| universe | `realtime_v2/build_universe.py` | ka10032 재시도·검증 cache fallback |
| 안전 실행 | `scripts/stockboard_v2_large_safe.ps1` | bitness·로그인·기동·중단 |
| 최종 collector | `realtime_v2/collector32_large_bidask.py` | collector patch 설치와 진입점 |
| Qt owner thread | `realtime_v2/qt_main_thread_openapi_patch.py` | QAx owner thread |
| native HWND | `realtime_v2/openapi_native_handle_patch.py` | 로그인 전 HWND 생성·검증 |
| sender | `realtime_v2/collector_sender_resilience_patch.py` | 직렬화·송신 fail-open |
| 독립 타이머 | `realtime_v2/offhours_metric_timer_driver_patch.py` | 시장 틱 비의존 timer |
| worker | `realtime_v2/worker64_guarded_large_bidask.py` | 최종 worker 진입점 |
| Board Platform | `realtime_v2/board_platform/` | 공용 cache·성능·상단·Model lane |
| ThemeBoard | `realtime_v2/theme_board_patch.py` | 테마 cache·API·마감 hold |
| 통합 복구 | `realtime_v2/after_close_recovery.py` | metadata·sampler·coordinator·분봉 |
| 복구 보강 | `realtime_v2/after_close_recovery_hardening.py` | 거래일·P0~P6·안전 처리 |
| sampler 보호 | `realtime_v2/after_close_recovery_sampler_guard.py` | 정확값·무거래 보호 |
| 정책 보호 | `realtime_v2/after_close_recovery_policy_guard.py` | universe·P0~P6·종료 상태 보호 |
| 복구 저장 | `realtime_v2/after_close_recovery_state.py` | 재시작 복원·premarket 만료 |
| 테마 복구 | `realtime_v2/after_close_theme_recovery.py` | Coverage·표시·cache 반영 |
| HTS bridge | `scripts/stockboard_kiwoom_link_v1.ahk` | strict SBV2 → Kiwoom Edit6 |

운영 원칙:

- collector 체결 callback에 보드 계산·파일 I/O·TR enqueue를 넣지 않는다.
- StockBoard와 ThemeBoard는 같은 worker 종목 원천을 사용한다.
- ThemeBoard 자체는 OpenAPI를 직접 호출하지 않는다.
- 장마감 TR은 중앙 coordinator가 한 번에 하나만 허용한다.
- 낮은 품질값과 추정값이 정확한 AL 값을 덮어쓰지 않는다.
- `data/runtime/`은 Git 추적 대상이 아니다.

## 4. universe 시작 복구

정상:

```text
ka10032 live 조회
→ tradable master 필터
→ 전일 거래대금 결합
→ universe.json / codes.txt 원자적 저장
```

오류:

```text
최대 3회 제한 재시도
→ 계속 실패하면 기존 universe.json 검증
→ schema_version=1
→ 고유 6자리 종목 기본 20개 이상
→ 정상 cache면 stale_fallback으로 기동 계속
→ cache 부재·손상·과소이면 시작 중단
```

주요 metadata:

```text
universe_source_status
universe_fallback_active
universe_fallback_reason
universe_original_built_at
universe_original_trading_date
fallback_valid_item_count
seed_fetch_attempt_count
```

안전 런처는 단순 파일 존재 여부로 진행하지 않고 `build_universe.py`의 검증 결과만 신뢰한다.

PowerShell JSON 확인:

```powershell
$u = Get-Content `
  C:\aiTrade\data\runtime\stockboard_v2\universe.json `
  -Raw -Encoding UTF8 |
  ConvertFrom-Json
```

## 5. 시장시간·휴장 보충

| phase | 정책 |
|---|---|
| before_market | 마지막값 유지, 누락 지표 보충 |
| premarket | 전일 hold 해제, 전일 복구 중단 |
| opening_call | 실시간 수용 |
| regular | 실시간 최우선 |
| closing_call | 정규장 마감값 수용 |
| after_wait | 정규장 마감값 보충 |
| aftermarket | NXT 새 값 우선, 미거래 종목 last valid |
| closed/weekend/holiday | 마지막값 유지, 중앙 저속 복구 |

휴장 보충과 통합 복구 대상:

| 지표 | 원천 |
|---|---|
| 순간·5분·20분·60분 강도 | opt10046 |
| 잔량비 | opt10004 |
| 1분·5분 거래대금·분봉 OHLC | opt10080 |
| 프로그램 | 기존 시장별 batch snapshot |
| OHLC | 기존 실시간·snapshot + 분봉 fallback |
| 대량체결 | collector 누적·daily state |

공통 원칙:

- QAx owner thread에서 단건 순차 처리
- 전역 inflight·pending·request gap 확인
- 단건 timeout 후 다음 종목 진행
- 재시도 5분 → 30분 → 2시간
- 연속 오류 3회면 전역 30분 backoff
- 프리마켓 5분 전 신규 전일 복구 중단
- 실제 premarket 시작 시 전일 hold 해제

정상 controller:

```text
controller=after_close_recovery_coordinator_v1
driver=qt_timer_market_tick_independent_v3_fixed_heartbeat
```

## 6. OpenAPI 로그인·노트북 호환

collector는 고정 32비트 Python, worker는 64비트 Python을 사용한다.

```text
QAxWidget 생성
→ 화면 밖 작은 위치에 show
→ QApplication.processEvents()
→ winId() native HWND 생성
→ Windows IsWindow() 검증
→ CommConnect()
```

안전 launcher 준비 판정:

```text
login_state=connected
openapi_native_handle_ready=True
registered_count>0
provider_started=True
```

`opstarter`는 강제 종료하지 않는다.

## 7. StockBoard 14개 항목과 선발모델

```text
순위 / 전일 / 등급 / 종목명 / 현재가 / 등락률 / 금액(억) / 대금비
일봉 / 잔량비 / 순간강도 / 5분강도 / 프로(억) / 대량체결
```

선발모델 점수 원천:

```text
순위 / 전일 / 현재가 / 등락률 / 금액(억) / 대금비 / 일봉
순간강도 / 5분강도 / 프로(억) / 대량체결
```

현재 점수 제외:

```text
잔량비 / 1분강도 / 복구된 1분 거래대금
VWAP / 외인 / 기관 / 미검증 시장지수
```

복구 추정값은 화면 fallback이며 후보모델 필수 원천으로 자동 승격하지 않는다.

등록 모델 8개:

- 5요소 수급선발 v0.1
- 순매수 강도 v0.2 · 5분강도
- 순매수 강도 v0.1 · 5분강도
- 돈쏠림 시작형 · 5분강도
- 조용한 매집형
- 폭발 확인형 · 5분강도
- 프로그램 동행형
- 보드 대비 상대강도형

```text
Top300 entry_score
→ Top50
→ confirmation_score Top20
→ focus_score Top5
→ DisplayOrderController 안전 승강
```

Coverage 60% 미만은 59점·WAIT_DATA로 제한하고 승격을 차단한다.

## 8. ThemeBoard

- 10개 테마, 종목별 활성 가중치 합 1.0
- 테마 1분·5분 유입, 누적대금, 점유율, 확산, 가속, 집중도
- 주도주 TOP3와 구성종목
- 서버 계산, HTML 표시 전용
- client 0명이면 계산 0회
- 다중 client 공용 cache
- worker lock 비차단
- 장마감 snapshot과 복구값을 다음 실제 premarket까지 유지
- 정확값·추정값·부분 Coverage·거래없음 표시

상세는 ThemeBoard 운영 명세를 따른다.

## 9. HTS/S1 Clipboard 연동

브라우저 명령:

```text
SBV2|<고유번호>|<6자리 종목코드>
```

AHK 정책:

- 정확한 대문자 `SBV2|숫자|6자리`만 처리
- 일반 6자리·`_AL`·`_NX`·구 `SB|...` 무시
- 검증된 단일 Kiwoom Edit6 HWND에만 입력
- readback 성공 후 해당 HWND에만 Enter
- 성공 후 Clipboard를 일반 6자리 코드로 변경
- 일반 6자리는 명령이 아니므로 중복 전송 없음

상태 파일:

```text
data/runtime/stockboard_v2/hts_link_status.txt
```

## 10. 통합 장마감 복구

### 공용 값 계약

```text
value / source / status / basis_time / trading_date
market_scope / quality / is_estimated / updated_at / coverage
```

보호:

- 낮은 quality가 높은 quality를 덮지 않음
- 추정값이 동일·상위 정확값을 덮지 않음
- KRX fallback이 정확한 AL 값을 덮지 않음
- 다른 거래일은 명시적 rollover만 허용
- 당일 정확 sampler 값이 있으면 분봉 fallback 생략

### 마감 sampler

```text
정규장 15:24:50~15:30:10
통합장 19:54:50~20:00:10
```

1초마다 종목코드·누적 거래대금·시각만 비차단 복사한다. 구간 종료 후 종목별 1분·5분 차이와 테마 가중 합계를 저장한다.

### 분봉 fallback

```text
opt10080
→ _AL 우선
→ 6자리 KRX fallback
→ 1분 = 해당 1분봉 종가×거래량
→ 5분 = 각 1분봉 종가×거래량 합
```

실제 무거래는 `거래없음`, 마지막 정상값은 `직전값`, 모든 원천 실패는 `복구실패`다.

### P0~P6

```text
P0 S1
P1 Top20
P2 상위 테마 주도주
P3 상위 테마 구성종목
P4 나머지 테마
P5 Hidden50
P6 나머지 Top300
```

동일 종목은 역할이 여러 개여도 bundle별 한 번만 조회한다.

### 저장·전환

```text
close_flow_sampler_last.json
after_close_recovery_state.json
theme_last_close.json
```

복구 state는 재시작 후 복원하고 다음 실제 premarket에 만료한다.

## 11. 성능 정책

- 실시간 collector callback 추가 계산 0
- StockBoard 전체 snapshot은 scheduler 1회 계산·직렬화 후 모든 client 공유
- 후보모델은 비동기 latest-only Model lane
- ThemeBoard 비차단 copy와 공용 cache
- 복구 coordinator는 장마감 후에만 실행
- 09:00~09:10 신규 장마감 복구 없음
- HTML 시장 데이터 계산 0

대표님 휴장 실측:

```text
ThemeBoard compute 약 3ms
ThemeBoard copy 약 0.47ms
ThemeBoard lock wait 0ms
StockBoard Fast 평균 약 44.5ms
Model submit 약 0.33ms
Model merge 약 4.0ms
```

정규장 09:00~09:10은 별도 실측을 계속한다.

## 12. 검증 상태

자동 검증 완료:

- 후보모델 registry·Funnel·Coverage guard
- 휴장 보충·resilience·Qt timer
- sender serialization·loop recovery
- QAx main-thread·native HWND
- universe retry·cache fallback·codes repair
- ThemeBoard 엔진·cache·HTTP·close hold
- source 우선순위·AL 정확값 보호·거래일 rollover
- sampler 정확 1분·5분 계산과 저장
- 분봉 각 분 `close×volume` 합·OHLC·Coverage
- 무거래와 결측 구분
- 전역 OpenAPI arbitration
- P0~P6 우선순위·중복 제거
- backoff·premarket cutoff
- ThemeBoard 부분 복구·추정·Coverage 표시
- 복구 state 재시작 복원·premarket 만료
- collector·worker 설치 wiring

대표님 실전 확인 완료:

- 기존 StockBoard 기능
- 휴장시간 세 지표 보충
- 시장 틱 0 상태 독립 timer
- sender 생존과 오류 0
- 노트북 OpenAPI 로그인
- ThemeBoard UI·장마감 hold
- strict SBV2 HTS·Clipboard
- 최신 통합 장마감 복구 적용 후 전체 시스템 정상 작동

계속 관찰:

1. 실제 ka10032 실패 시 stale fallback
2. 15:30·20:00 sampler 거래일별 파일
3. opt10080 `_AL` 실응답 범위·단위
4. 한 시점 TR 1개와 backoff 장기 동작
5. 다음 premarket `LAST_CLOSE → LIVE`
6. 09:00~09:10 queue·drop·latency
7. 테마 master 실제 시장 정확성

## 13. PR·문서 이력

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력
- PR #23: NXT 미거래 정규장 마감 5분강도
- PR #24: v2 기준 문서
- PR #25: 설정형 선발모델 8개·Coverage·Funnel
- PR #27: ThemeBoard·Board Platform·통합 장마감 복구 — Draft
- 2026-07-12: 휴장 보충·native HWND·strict SBV2·universe fallback·통합 복구 정상 확인 반영
