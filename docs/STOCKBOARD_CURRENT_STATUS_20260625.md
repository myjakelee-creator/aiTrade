# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-14 15:30 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 세부 작업 이력은 Git history와 PR 기록에서 확인한다. 이 문서는 현재 운영 구조, 실제 검증 결과, 미검증 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard는 **32비트 가격 전용 최소 QAx collector**, 64비트 Canonical State, continuity 최종 read layer, BoardDataHub 공용 FeatureSnapshot, StockBoard·ThemeBoard·StrategyProjection 구조로 운영한다.

2026-07-14 장중에 FID15 대량체결 경로가 포함된 collector가 로그인·100종목 등록·1,448건 수신 후 비정상 종료했다. 생산 collector에서 FID15와 대량체결 aggregate를 제거하고 검증된 가격 핵심 경로인 `FID10·12·20 + sampled FID14`로 복귀한 뒤, 같은 PID로 최소 8분 이상 생존하며 `RealData +31,069`, queue 0, LastError 없음이 확인됐다.

StockBoard UI는 최대 **100종목**을 표시한다. 내부 후보 universe와 OpenAPI 실시간 등록 수는 각각 별도이며, UI 100종목은 collector 종료 원인이 아니다. 장애 당시에도 `stream 21ms`, `render 6.5ms`, collector/worker queue 0, drop 0이었다.

ThemeBoard는 키움 전체 테마 catalog 약 `142개 / membership 약 907개`를 runtime 원천으로 사용한다. 내부 universe는 당일 약 172~173종목, 유효 테마는 교집합에 따라 약 66~68개 수준이다.

PR은 계속 Draft·미병합으로 유지한다. 가격 collector 장시간 생존, 재시작 반복, 다음 09:00~09:10 개장 폭주, 장마감·다음 프리마켓 전환을 추가 확인한 뒤 병합 여부를 판단한다.

---

## 1. 현재 운영 구조

```text
32-bit production price-only QAx collector
  FID 10 현재가
  FID 12 등락률
  FID 20 체결시각
  FID 14 누적거래대금 500ms 샘플
  실시간 등록 100종목
  FID15 / 대량체결 aggregate 비활성
        ↓ latest-only sender
64-bit worker Canonical State
        ↓ final continuity read layer
BoardDataHub shared FeatureSnapshot
        ├─ StockBoard 최대 100종목
        ├─ Theme Summary latest-only 1초
        ├─ Theme Detail 선택 테마 1개 latest-only
        └─ StrategyProjection latest-only
```

| 항목 | 현재 원칙 |
|---|---|
| 실시간 owner | 32비트 최소 QAx collector 한 개 |
| 생산 collector mode | `minimal_qax_price_only_v2` |
| 실시간 FID | `10;12;20;14` |
| 누적거래대금 | FID14, 종목별 기본 500ms 샘플 |
| 실시간 등록 | 100종목 안전값 유지 |
| UI 표시 | 최대 100종목 |
| 내부 후보 universe | 당일 필터 결과 약 170~190종목 |
| Canonical State | 64비트 worker State 한 개 |
| 공통 계산 | 한 번 계산한 완료 FeatureSnapshot을 세 보드가 공유 |
| ThemeBoard·StrategyBoard TR | 금지 |
| HTML 시장계산 | 금지 |
| 대량체결 | 생산 collector에서는 비활성, 기존 구현은 보류 상태로 보존 |
| Git | PC 장중 검증 전 Draft PR 유지, 병합 금지 |

---

## 2. 2026-07-14 collector 장애와 복구

### 2.1 장애형 collector

```text
CollectorPid      : 9848
CollectorAlive    : False
LoginState        : connected
RealRegSucceeded  : True
RegisteredCount   : 100
RealData          : 1,448
TradeReceived     : 1,448
RealDataLastAt    : 2026-07-14 12:57:56.440 KST
Queue             : 0
LastError         : 없음
```

관찰:

- 로그인과 SetRealReg는 정상 완료됐다.
- collector stdout에 `collector_app_result=...` 정상 종료 표식이 없었다.
- collector stderr는 비어 있었다.
- worker에는 `ConnectionResetError: WinError 10054`가 남았다.
- WinError 10054는 collector가 사라진 뒤 소켓이 강제로 끊긴 결과다.
- 최근 20분 Application Event 1000/1001 검색에서는 `python.exe / ntdll.dll / 0xc0000374`가 발견되지 않았다.
- Python 예외나 정상 Qt 종료가 아니라 32비트 collector의 비정상 종료로 판정한다.

장애형과 마지막 장시간 안정형 사이의 주요 차이는 다음 묶음이다.

```text
FID15 signed 체결량 매 callback 조회
+ collector_large_trade_patch
+ 대량체결 aggregate
```

FID15 하나가 직접 원인이라고 확정하지 않는다. 현재 확정 가능한 것은 **FID15·대량체결 결합 경로가 회귀 의심 범위**라는 점이다.

### 2.2 가격 핵심형 복귀

생산 collector에서 다음을 제거했다.

```text
FID15 GetCommRealData
trade_qty / cntg_vol publish
collector_large_trade_patch 설치
대량체결 실시간 aggregate
```

유지한 항목:

```text
FID10 현재가
FID12 등락률
FID20 체결시각
FID14 500ms 샘플
sender resilience
sender ordering
실시간 등록 100
UI 표시 100
```

관련 코드 커밋:

```text
60ac499a13dadc14f5fa29f8d02c062e7e3164a4
fix: restore price-only minimal QAx collector

eeea3a7a79617decf0e250d15109bfcf582b0a3b
test: lock production collector to price-only QAx path
```

### 2.3 복귀 후 PC 실측

첫 측정:

```text
CollectorPid      : 23732
CollectorAlive    : True
RegisteredCount   : 100
RealData          : 22,638
TradeReceived     : 13,745
WorkerTrades      : 8,367
RealDataLastAt    : 2026-07-14 15:21:36.814 KST
Queue             : 0
LastError         : 없음
```

후속 측정:

```text
CollectorPid      : 23732
CollectorAlive    : True
RegisteredCount   : 100
RealData          : 53,707
TradeReceived     : 13,745
WorkerTrades      : 8,367
RealDataLastAt    : 2026-07-14 15:29:54.810 KST
Queue             : 0
LastError         : 없음
```

판정:

```text
동일 PID 유지
RealData +31,069
Queue 0 유지
LastError 없음
collector 생존 확인
```

15:20~15:30 종가 단일가 구간에는 `주식체결`이 아닌 실시간 이벤트가 계속 들어올 수 있다. collector는 모든 real event에서 `RealData`를 증가시키지만, real type에 `주식체결`이 없으면 `TradeReceived`와 `WorkerTrades`를 증가시키지 않는다. 따라서 이 구간에서 `RealData`만 증가한 것은 현재 코드 계약과 맞는다.

---

## 3. StockBoard UI 100종목

현재 UI 정책:

```text
S1 선택행         1종목
집중 후보         20종목
표시 Pool         21~100, 최대 80종목
브라우저 고유행   최대 100종목
```

내부 후보 계산과 OpenAPI 등록 수는 UI 표시 수와 별도다.

```text
내부 universe 약 170~190
→ 전체 후보 점수 계산
→ 브라우저 요청 limit=100
→ 집중 후보 20 + 표시 Pool 최대 80
```

성능 보호:

```text
현재가·등락률 셀 fast patch  약 100ms stream 이벤트
전체 행 heavy render          500ms 간격
Pool 전체 render              1초 간격
```

2026-07-14 UI100 적용 후 collector 중단 화면에서도 확인된 브라우저 지표:

```text
stream 21ms
render 6.5ms
collector_q 0
worker_q 0
drop 0
logdrop 0
```

따라서 UI 100종목은 해당 collector 종료의 원인이 아니다. 앞으로도 UI100은 유지하되 `render`, stream age, top20 lag를 계속 관찰한다.

UI100 관련 커밋:

```text
7193ed674072ba5fe43a57d4a849bde7d64560c4
feat: expand StockBoard visible rows to 100

191af1ad48c3f08e8701069c4440438b13444b67
test: lock StockBoard visible rows at 100
```

---

## 4. 실시간·continuity 상태

### 4.1 현재 실시간 보증 범위

```text
현재가
등락률
체결시각
누적거래대금
```

잔량비·순간강도·5분강도·프로그램·대량체결은 현재가와 같은 실시간 속도를 보증하지 않는다. 이전 세션 보존값이나 저속 원천이 표시될 수 있다.

### 4.2 continuity 보존 필드

```text
bid_ask_ratio
execution_strength
strength_5m
program_net
large_trade_net_count
large_trade_net_sum_eok
metric_continuity_basis
```

이전 세션 값은 화면 표시가 가능하지만 테마·주도주 점수에서는 제외한다.

runtime 파일:

```text
data/runtime/stockboard_v2/board_metric_continuity.json
data/runtime/stockboard_v2/theme_flow_history_hold.json
data/runtime/stockboard_v2/theme_momentum_hold.json
```

시장 캘린더:

```text
config/stockboard_market_calendar.json
```

주말은 자동 휴장이다. 공휴일·임시휴장·지연개장은 `holidays`, `special_days`, `closed`, `open_delay_minutes`, 명시적 `windows`로 처리한다.

---

## 5. ThemeBoard 현재 상태

ThemeBoard는 StockBoard와 같은 완료 FeatureSnapshot을 재사용하며 별도 OpenAPI 등록이나 TR을 수행하지 않는다.

원천 우선순위:

```text
1. data/runtime/stockboard_v2/theme_membership.json
2. config/stockboard_theme_master.json
```

현재 확인된 기능:

```text
평균등락률 → 상승탄력 → 돈쏠림 보기
상위 카드 20개
선택 카드 재클릭 상세 닫기/열기
선택 카드 행 바로 아래 구성종목 상세
1분·5분 자금쏠림 상대평가 막대
전체 순위표 서버 정렬
열 폭 드래그·더블클릭 자동맞춤·저장 복원
종목 클릭 SBV2 HTS 연동
```

Theme Summary와 선택 Theme Detail은 분리한다.

```text
Summary: 전체 유효 테마 숫자 요약, 1초 latest-only
Detail : 선택 테마 한 개만 정밀 계산
HTML  : 서버 완성 배열과 막대 표시만 수행
```

---

## 6. 운영 상태 확인

### 6.1 실행

```powershell
cd C:\aiTrade
git pull --ff-only origin fix/restore-stable-collector-20260713
.\stockboard_v2_large.cmd restart-fast
```

### 6.2 상태

```powershell
.\stockboard_v2_large.cmd status
```

정상 기준:

```text
CollectorAlive    : True
LoginState        : connected
RealRegSucceeded  : True
RegisteredCount   : 100
RealData          : 계속 증가
RealDataLastAt    : 현재 시각과 근접
Queue             : 낮고 지속 증가하지 않음
LastError         : 비어 있음
```

현재 collector mode 확인:

```powershell
$s = Invoke-RestMethod 'http://127.0.0.1:8765/api/v2/snapshot?limit=1'
$s.status.collector_status.status |
Select-Object collector_mode,realreg_fids,large_trade_enabled,realdata_received_count,trade_event_received_count,last_error |
Format-List
```

기대값:

```text
collector_mode      : minimal_qax_price_only_v2
realreg_fids        : 10;12;20;14
large_trade_enabled : False
```

### 6.3 진단

```powershell
.\stockboard_v2_large.cmd doctor
```

보고서:

```text
data/runtime/stockboard_v2/large_doctor_report.txt
```

---

## 7. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 가격 전용 collector 장시간 생존 | 동일 PID 30분 이상, RealData 지속 증가, queue 안정 |
| 2 | 재시작 반복 검증 | 3회 연속 connected + realreg + 수신 성공 |
| 3 | 다음 09:00~09:10 개장 폭주 | collector/worker queue 지속 증가 없음, drop 0, 가격 지연 없음 |
| 4 | UI100 성능 반복 확인 | render·stream·top20 lag가 실전 허용 범위 유지 |
| 5 | 대량체결 경로 원인 분리 | 별도 실험에서 FID15 단독과 aggregate patch를 분리 검증 |
| 6 | collector watchdog 설계 | 원인 분석을 숨기지 않으면서 비정상 종료 감지·알림·안전 재시작 |
| 7 | exact 20:00·다음 프리마켓 | hold/LIVE 전환 정상 |
| 8 | 대량체결 HTS 대조 | 안정적인 별도 경로 확정 뒤 방향·건수·합계 검증 |
| 9 | Draft PR 정리 | 장중 검증 통과 후 병합 여부 판단 |

---

## 8. 금지사항

```text
생산 가격 collector에 FID15·호가·강도·보조 TR을 한꺼번에 다시 추가하지 않는다.
UI100 문제로 오인해 내부 후보 계산이나 OpenAPI 등록 수를 무작정 줄이지 않는다.
collector 종료를 worker·브라우저 재시작만으로 해결했다고 판단하지 않는다.
WinError 10054를 원인으로 보지 않는다. collector 종료의 결과로 해석한다.
대량체결 값을 현재 실시간 보증값처럼 표시·사용하지 않는다.
ThemeBoard 또는 HTML에서 직접 TR을 호출하지 않는다.
장중 실전 검증 전 PR을 병합하지 않는다.
```

---

## 9. 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 생산 가격 전용 최소 QAx collector |
| `realtime_v2/collector_sender_resilience_patch.py` | sender thread fail-open |
| `realtime_v2/collector_sender_ordering_patch.py` | 재연결 시 최신 quote 순서 보호 |
| `realtime_v2/collector_large_trade_patch.py` | 보류된 대량체결 실험 구현, 생산 미설치 |
| `realtime_v2/worker64_guarded_large_bidask.py` | StockBoard UI100·worker 진입점 |
| `realtime_v2/worker_opening_burst_cache_patch.py` | background heavy snapshot·fast overlay |
| `realtime_v2/board_metric_continuity_patch.py` | 세 보드 공통 metric continuity |
| `realtime_v2/board_data_hub.py` | 공용 Canonical/Feature/Projection read model |
| `realtime_v2/theme_projection_engine.py` | Theme Summary 계산 |
| `realtime_v2/theme_selected_detail_runtime.py` | 선택 Theme Detail latest-only worker |
| `scripts/stockboard_v2_large_safe.ps1` | 시작·중지·status·doctor |
| `stockboard_v2_large.cmd` | 운영 통합 실행기, collector 기본 100 제한 |
| `tests/test_stockboard_qt_exec_loop.py` | 생산 collector 가격 전용 계약 |
| `tests/test_stockboard_display50_fast_price.py` | UI100·fast patch 회귀 계약 |
