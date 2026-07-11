# StockBoard v2 실시간 파이프라인

최종 갱신: 2026-07-12

이 문서는 StockBoard v2의 **현재 운영 상태와 실행 기준**을 기록하는 기준 문서이다. 상세 ThemeBoard 명세와 장마감 통합 복구 설계는 아래 문서로 분리한다.

- ThemeBoard 운영 명세: `docs/STOCKBOARD_THEMEBOARD_V1_20260712.md`
- 통합 수집·장마감 복구 설계: `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`

기존 StockBoard v0.3.x 문서와 섞지 않는다.

## 1. 현재 결론

StockBoard v2는 32비트 Kiwoom OpenAPI collector와 64비트 worker를 분리해 장개시 이벤트 폭주 구간의 수신과 표시를 안정화하는 구조이다.

현재 완료 상태:

- 현재가·등락률·누적 거래대금 실시간 표시
- 5분강도 opt10046 차등 조회, 당일 복원, 정규장 마감 보충
- 잔량비 opt10004 저속 조회와 마지막 정상값 유지
- Top20·Top300 내부 행 위치 고정과 안전 승강
- 설정형 선발모델 8개, Coverage, 실제 Top300 → Top50 → Top20 → Top5 Funnel
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
- 잔량비 저속 순차 보충 정상
- 2026-07-11 선발모델 v3 적용 후 기존 선발기준 정상
- ThemeBoard 화면·10개 카드·2열 태블릿 배치·밀도형 상세 정상
- ThemeBoard 장마감 마지막값 표시 정상
- ThemeBoard 레이더 주도종목 HTS 연동 정상
- ThemeBoard 실측 `compute_ms` 약 3ms, `copy_ms` 약 0.47ms, `lock_wait_ms=0`

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

현재 ThemeBoard 브랜치 받기:

```powershell
cd C:\aiTrade
git fetch origin
git switch feature/stockboard-themeboard-v1
git reset --hard origin/feature/stockboard-themeboard-v1
.\stockboard_v2_large.cmd restart-fast
```

브라우저 변경은 `Ctrl+F5`로 확인한다.

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
| 잔량비 wrapper | `realtime_v2/collector32_large_bidask.py` | 잔량비 scheduler 설치 |
| worker | `realtime_v2/worker64_guarded_large.py` | 상태·OHLC·강도·대량체결·후보모델 |
| 최종 wrapper | `realtime_v2/worker64_guarded_large_bidask.py` | 잔량비 표시와 ThemeBoard fail-open 설치 |
| 5분강도 | `realtime_v2/strength5m_scheduler.py` | S1·Top20·Hidden50·Top300 차등 opt10046 |
| 잔량비 | `realtime_v2/orderbook_thin_scheduler.py` | 저속 opt10004 단건 조회 |
| 시장시간 | `realtime_v2/market_session.py` | 프리·정규·애프터·휴장·특별일 판단 |
| 시장달력 | `config/stockboard_market_calendar.json` | 기본 거래시간과 특별일 설정 |
| 모델 설정 | `configs/candidate_models/*.json` | 모델별 점수·guard·Funnel |
| 공통 파생값 | `stockboard_candidate_features.py` | 승인 원천에서 공통 지표 1회 계산 |
| 모델 실행 | `stockboard_candidate_engine.py` | 점수·등급·Coverage·Funnel |
| 행 위치 | `stockboard_display_order.py` | lane 내부 고정과 안전 승강 |
| StockBoard UI | `docs/stockboard_v2.html` | worker 결과 표시 전용 |
| 테마 마스터 | `config/stockboard_theme_master.json` | 10개 테마와 종목별 가중치 |
| 테마 엔진 | `stockboard_theme_engine.py` | 테마 유입·상태·주도주 계산 |
| 테마 연결 | `realtime_v2/theme_board_patch.py` | 공용 캐시·API·마감 보존·정적 UI |
| ThemeBoard UI | `docs/stockboard_theme_v1.html` | 서버 결과 표시와 HTS 연동 |

운영 원칙:

- 데이터 수집과 점수 계산을 브라우저로 이동하지 않는다.
- StockBoard와 ThemeBoard는 같은 worker 종목 원천을 공유한다.
- ThemeBoard 때문에 신규 OpenAPI 등록·TR을 추가하지 않는다.
- 장마감 통합 복구 coordinator는 별도 승인 설계이며 아직 미구현이다.
- `data/runtime/`은 Git 추적 대상이 아니다.

## 4. 시장시간 정책

시장시간은 `config/stockboard_market_calendar.json` 기준이며 `special_days`가 있으면 특별일 설정이 우선한다.

| phase | 기본 시간 | 현재 정책 |
|---|---|---|
| before_market | 08:00 전 | 전일 마지막값 유지, 보조조회는 승인 정책만 허용 |
| premarket | 08:00~08:30 | 전일 ThemeBoard hold 해제, 새 실시간 수용 |
| opening_call | 08:30~09:00 | 실시간 수용 |
| regular | 09:00~15:20 | 실시간 최우선 |
| closing_call | 15:20~15:30 | 정규장 마감값 수용 |
| after_wait | 15:30~15:40 | 정규장 마감값 보충 |
| aftermarket | 15:40~20:00 | NXT 새 값 우선, 미거래 종목은 마지막값 유지 |
| closed | 20:00 이후 | ThemeBoard 마지막 스냅샷 유지 |
| weekend/holiday | 주말·휴장일 | 마지막 스냅샷 유지 |

ThemeBoard 보존 종료 시각은 고정 다음 날이 아니라 `next_premarket_datetime()`이 계산한 다음 실제 거래일 프리마켓이다.

## 5. StockBoard 14개 표시 항목

```text
순위 / 전일 / 등급 / 종목명 / 현재가 / 등락률 / 금액(억) / 대금비
일봉 / 잔량비 / 순간강도 / 5분강도 / 프로(억) / 대량체결
```

세부 원천·장마감 fallback·ThemeBoard 중복 관계는 `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`를 따른다.

## 6. 5분강도·잔량비 정책

### 5분강도

- Kiwoom opt10046 `strength_5m` 사용
- 순간·5분·20분·60분 snapshot을 한 조회에서 수용
- 0·빈값·오류가 마지막 정상 양수값을 덮어쓰지 않음
- 같은 거래일 재시작 시 당일 마지막 정상값 복원
- 조회 우선순위: `S1 → Top20 → Hidden Top50 → Top300`
- 다른 TR이 진행 중이면 양보
- 장마감·프리마켓 보충은 긴 간격과 backoff 사용

### 잔량비

- 실시간 호가 우선, 누락 시 opt10004 저속 조회
- 마지막 정상 양수값 cache
- 09:00~09:10 Hidden50·Top300 신규조회 제한
- 현재 선발모델 점수에서는 제외
- ThemeBoard 현재 항목에서도 제외

## 7. 선발모델 v3

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

## 8. ThemeBoard 현재 상태

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

## 9. 성능 정책

- collector 체결 처리 경로에 ThemeBoard 계산을 넣지 않는다.
- 후보모델은 선택된 모델 1개만 계산한다.
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

## 10. 검증 상태

자동 검증:

- 후보모델 registry 8개·validation error 0
- 실제 Top300 → Top50 → Top20 → Top5 Funnel
- Coverage 부족 WAIT_DATA와 승격 차단
- ThemeBoard 엔진·캐시·HTTP·마감 hold 테스트
- ThemeBoard 금지 호출 `state.snapshot()`, `state.rows()`, 후보 enrichment 없음
- ThemeBoard HTML에 `.sort`, `.reduce`, `Math.*` 계산 없음
- 10개 테마 마스터 검증
- 장마감 Friday → Monday 08:00 hold 시뮬레이션

대표님 실전 검증:

- StockBoard 기존 기능 정상
- ThemeBoard UI·밀도·반응형 정상
- 장마감 마지막값 표시 정상
- HTS 연동 정상
- 성능 진단값 정상 범위

아직 필요한 검증:

- 다음 프리마켓에서 `LAST_CLOSE → LIVE` 전환
- 다음 정규장에서 ThemeBoard `cache_version`과 성공 계산 증가
- 기존 StockBoard stream latency·queue·drop 무영향 확인
- 장마감 통합 복구 설계 구현 후 OpenAPI 요청량과 backoff 검증

## 11. 승인된 다음 개선

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

## 12. PR·문서 이력

- PR #21: 5요소 수급선발·5분강도·차등 조회
- PR #22: 행 위치 고정·시장 달력
- PR #23: NXT 미거래 정규장 마감 5분강도
- PR #24: v2 기준 문서
- PR #25: 설정형 선발모델 8개·Coverage·Funnel
- PR #27: ThemeBoard v1, 장마감 hold, 반응형 UI, HTS 연동 — Draft
