# StockBoard v2 실시간 collector 장애·복구 보고서

최종 갱신: 2026-07-14 15:30 KST  
최초 작성: 2026-07-13 11:03 KST  
상태: **가격 전용 최소 collector 복귀 후 생존 확인 · FID15/대량체결 경로 보류 · 자동매매 연결 보류**  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`  
작업 경로: `C:\aiTrade`  
화면: `http://127.0.0.1:8765/`

---

## 0. 한 줄 결론

StockBoard 브라우저와 64비트 worker는 정상인데 `recv/s 0`, `trade/s 0`, 가격 고정이 나타난 원인은 32비트 QAx collector의 비정상 종료였다. UI 100종목은 `stream 21ms`, `render 6.5ms`, queue 0, drop 0으로 정상 동작했으므로 원인에서 제외한다.

2026-07-14 장애형 collector는 로그인·SetRealReg·100종목 등록에 성공하고 1,448건을 받은 뒤 Python traceback, stderr, 정상 Qt 종료 표식 없이 사라졌다. 마지막 장시간 안정형과 비교해 추가됐던 FID15 signed 체결량과 대량체결 aggregate를 생산 경로에서 제거하고 `FID10·12·20 + sampled FID14` 가격 전용 구조로 복귀한 뒤, 같은 PID로 최소 8분 이상 생존하며 `RealData +31,069`, queue 0, LastError 없음이 확인됐다.

FID15 하나가 직접 원인이라고 확정하지 않는다. 현재 확정된 회귀 의심 범위는 **FID15 매 callback 조회와 collector 대량체결 aggregate의 결합 경로**다.

---

## 1. 장애 화면 판정

장애 당시 상단 지표:

```text
표시 100 / 내부 173
stream 21ms
render 6.5ms
collector_q 0
worker_q 0
drop 0
logdrop 0
recv/s 0.0
trade/s 0.0
연결 지연 92.6s
```

판정:

| 구간 | 상태 |
|---|---|
| 브라우저 SSE | 정상 |
| 브라우저 렌더 | 정상 |
| 64비트 worker | 정상 |
| collector/worker queue | 적체 없음 |
| 이벤트 drop | 없음 |
| 신규 실시간 체결 | 완전 중단 |
| 원천 collector | 종료 또는 전체 QAx event loop 정지 의심 |

화면의 `sent/s 96.0`은 현재 신규 전송률이 아니라 마지막 collector status에 남은 값이었다. `recv/s`, `trade/s`는 worker 누적 카운터 증가량으로 계산되므로 현재 상태 판정에는 이 두 값을 우선한다.

---

## 2. 진단 결과

재시작 전 `status` 두 번과 `doctor` 결과:

```text
CollectorPid      : 9848
CollectorAlive    : False
LoginState        : connected
RealRegSucceeded  : True
RegisteredCount   : 100
NativeHandleReady : True
RealData          : 1,448
TradeReceived     : 1,448
RealDataLastAt    : 2026-07-14 12:57:56.440 KST
WorkerTrades      : 865
WorkerLastEventAt : 2026-07-14 12:57:56.758 KST
Queue             : 0
LastError         : 없음
```

로그:

```text
collector_mode=minimal_qax_critical_large_trade_v1
native_hwnd=1579148
trade_value_sample_interval_ms=500
large_trade_threshold_krw=50000000
qt_event_loop=exec_ critical_fids=10,12,20,15 sampled_fid14_ms=500
collector_ready=True registered_count=100 screens=1
```

collector stderr는 비어 있었다.

worker stderr:

```text
ConnectionResetError: [WinError 10054]
현재 연결은 원격 호스트에 의해 강제로 끊겼습니다
```

`WinError 10054`는 worker가 collector socket 단절을 감지한 결과다. worker가 collector를 죽인 원인으로 해석하지 않는다.

최근 20분 Windows Application Event 1000/1001에서 다음 패턴은 발견되지 않았다.

```text
python.exe
ntdll.dll
0xc0000374
```

WER 기록이 없다고 정상 종료인 것은 아니다. 정상 Qt 종료라면 collector는 마지막에 `collector_app_result=<code>`를 stdout에 출력해야 하지만 해당 출력이 없었다.

최종 판정:

```text
Python 처리 예외 아님
정상 app.exec_ 종료 아님
로그에 남는 명시적 종료 아님
32비트 collector 비정상 종료
```

---

## 3. 과거 장애와 현재 장애의 관계

### 3.1 과거 복잡한 provider 장애

기존 생산 provider 구조는 다음 기능을 같은 QAx owner process에 함께 설치했다.

```text
KiwoomOpenApiRealtimeProvider
main-thread/native HWND patch
orderbook scheduler
strength probe
보조 TR
close-metric queue
off-hours timer
여러 readiness/resilience wrapper
```

이 구조는 로그인·등록·수신 후 `ntdll.dll / 0xc0000374` native heap corruption으로 종료됐다.

### 3.2 2026-07-13 가격 핵심형 복구

복잡한 provider를 제거하고 가격 핵심 FID만 읽는 최소 QAx collector로 교체한 뒤 같은 PID에서 10만 건 이상 연속 수신했다.

검증된 핵심 경로:

```text
FID10 현재가
FID12 등락률
FID20 체결시각
FID14 누적거래대금 저속 샘플
latest-only EventSender
```

### 3.3 2026-07-14 회귀 경계

가격 핵심형에 다음이 추가된 상태에서 짧은 수신 후 비정상 종료가 재발했다.

```text
FID15 signed 체결량을 매 주식체결 callback에서 조회
collector_large_trade_patch 설치
5천만원 이상 체결 aggregate
재연결 시 aggregate 보존 로직
```

대량체결 patch는 Python 코드이며 자체적으로 QAx/TR을 생성하지 않는다. 그러나 생산 callback의 추가 FID15 QAx read와 aggregate 경로가 함께 활성화된 상태가 마지막 안정형과 다른 핵심 구간이다.

현재 증거만으로 다음을 개별 확정하지 않는다.

```text
FID15 자체가 원인
large_trade Python aggregate 자체가 원인
두 경로의 결합이 원인
Kiwoom/QAx의 우발 종료
```

운영 해결은 원인을 완전히 증명할 때까지 기다리지 않고 마지막 장시간 안정형으로 복귀하는 방식으로 진행한다.

---

## 4. 생산 복구 구조

현재 생산 `realtime_v2/collector32_large_bidask.py`:

```text
32비트 Python 메인 스레드
→ QApplication
→ QAxWidget native HWND
→ CommConnect
→ SetRealReg 100종목
→ OnReceiveRealData
→ FID10 현재가
→ FID12 등락률
→ FID20 체결시각
→ FID14 종목별 500ms 샘플
→ latest-only EventSender
→ 64비트 worker
→ SSE
→ StockBoard 최대 100종목
```

생산 collector에서 비활성화한 기능:

```text
FID15 체결량
대량체결 aggregate
호가잔량
체결강도
보조 TR
orderbook scheduler
strength scheduler
off-hours completion timer
```

상태 계약:

```text
collector_mode          : minimal_qax_price_only_v2
realreg_fids            : 10;12;20;14
large_trade_enabled     : False
large_trade_input_fid   : None
```

관련 커밋:

```text
60ac499a13dadc14f5fa29f8d02c062e7e3164a4
fix: restore price-only minimal QAx collector

eeea3a7a79617decf0e250d15109bfcf582b0a3b
test: lock production collector to price-only QAx path
```

---

## 5. 복귀 후 실측

첫 측정:

```text
CollectorPid      : 23732
CollectorAlive    : True
LoginState        : connected
RealRegSucceeded  : True
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
LoginState        : connected
RealRegSucceeded  : True
RegisteredCount   : 100
RealData          : 53,707
TradeReceived     : 13,745
WorkerTrades      : 8,367
RealDataLastAt    : 2026-07-14 15:29:54.810 KST
Queue             : 0
LastError         : 없음
```

증가:

```text
RealData +31,069
동일 PID 유지
Queue 0
LastError 없음
```

15:20~15:30 종가 단일가 구간에는 `주식체결`이 아닌 real event가 지속될 수 있다. 현재 callback은 모든 real event에서 `RealData`를 증가시키고, real type에 `주식체결`이 포함될 때만 FID10·12·20·14를 읽어 `TradeReceived`를 증가시킨다.

따라서 이 측정에서 `RealData`만 증가하고 `TradeReceived`, `WorkerTrades`가 고정된 것은 코드 계약과 일치한다. 이 결과는 QAx 이벤트 루프와 heartbeat가 살아 있음을 보여준다.

---

## 6. UI 100종목과 collector 장애 분리

현재 UI:

```text
S1 선택행         1종목
집중 후보         20종목
표시 Pool         21~100, 최대 80종목
브라우저 고유행   최대 100종목
```

UI100은 브라우저 snapshot/stream 요청 limit만 100으로 확대한다.

변경하지 않은 것:

```text
OpenAPI 실시간 등록 기본 100
내부 후보 universe
heavy snapshot 계산 범위
collector callback
ThemeBoard projection
```

장애 화면에서도 다음이 확인됐다.

```text
stream 21ms
render 6.5ms
collector_q 0
worker_q 0
drop 0
logdrop 0
```

따라서 UI 100종목은 collector 종료 원인에서 제외한다.

관련 커밋:

```text
7193ed674072ba5fe43a57d4a849bde7d64560c4
feat: expand StockBoard visible rows to 100

191af1ad48c3f08e8701069c4440438b13444b67
test: lock StockBoard visible rows at 100
```

---

## 7. 현재 운영 판정

| 항목 | 판정 |
|---|---|
| 현재가 | 가격 전용 collector에서 실시간 보증 |
| 등락률 | 가격과 같은 callback에서 실시간 보증 |
| 체결시각 | FID20 실시간 보증 |
| 누적거래대금 | FID14 500ms 샘플 |
| collector 등록 | 100종목 |
| UI 표시 | 최대 100종목 |
| 내부 후보 계산 | 약 170~190종목 유지 |
| 대량체결 | 생산 실시간 비활성, 이전 값/보존값만 가능 |
| 잔량비·강도·프로그램 | 가격과 같은 실시간 속도 미보증 |
| 수동매매 감시 | 가격·등락률 기준 사용 가능, 장시간 재검증 중 |
| 자동매매 연결 | 장시간·재시작·개장폭주 검증 전 보류 |
| PR #35 | Draft 유지 |

---

## 8. 실행·검증 명령

### 최신 코드 적용

```powershell
cd C:\aiTrade
git status --short
git pull --ff-only origin fix/restore-stable-collector-20260713

python -m py_compile realtime_v2\collector32_large_bidask.py
python -m pytest -q `
  tests\test_stockboard_qt_exec_loop.py `
  tests\test_stockboard_display50_fast_price.py

.\stockboard_v2_large.cmd restart-fast
```

### 상태 확인

```powershell
.\stockboard_v2_large.cmd status
```

### collector mode 확인

```powershell
$s = Invoke-RestMethod 'http://127.0.0.1:8765/api/v2/snapshot?limit=1'
$s.status.collector_status.status |
Select-Object collector_mode,running,login_state,realreg_succeeded,realreg_code_count,realreg_fids,large_trade_enabled,realdata_received_count,trade_event_received_count,last_error |
Format-List
```

기대값:

```text
collector_mode      : minimal_qax_price_only_v2
running             : True
login_state         : connected
realreg_succeeded   : True
realreg_code_count  : 100
realreg_fids        : 10;12;20;14
large_trade_enabled : False
```

### 장시간 확인

```powershell
.\stockboard_v2_large.cmd status
Start-Sleep -Seconds 1800
.\stockboard_v2_large.cmd status
```

합격 기준:

```text
CollectorPid 동일
CollectorAlive=True
RealData 증가
Queue 낮고 지속 증가 없음
LastError 없음
```

---

## 9. 다음 작업

1. 가격 전용 collector 동일 PID 30분 이상 검증  
2. 재시작 3회 연속 로그인·등록·수신 검증  
3. 다음 정규장 09:00~09:10 장개시 폭주 검증  
4. UI100 render·stream·top20 lag 반복 확인  
5. FID15 단독 실험과 aggregate patch 실험을 생산 collector 밖에서 분리  
6. 원인 분리 후 대량체결 별도 저위험 경로 결정  
7. collector 종료·heartbeat 정지를 감지하는 watchdog 설계  
8. 자동매매 연결은 위 검증 뒤 판단  

watchdog은 장애를 숨기는 용도로 먼저 넣지 않는다. 가격 핵심형 생존을 확인한 뒤, collector PID 종료 또는 heartbeat 장기 정지를 명확히 표시하고 안전 재시작하는 방식으로 설계한다.

---

## 10. 금지사항

```text
생산 가격 collector에 FID15·호가·강도·TR을 한꺼번에 복원하지 않는다.
대량체결 열을 실시간 보증값으로 사용하지 않는다.
UI100을 collector native 종료 원인으로 취급하지 않는다.
WinError 10054를 근본 원인으로 취급하지 않는다.
collector가 죽었는데 worker나 브라우저만 재시작하지 않는다.
가격 핵심형 장시간 검증 전 실시간 등록 수 확대를 진행하지 않는다.
PC 장중 검증 전 Draft PR을 병합하지 않는다.
```

---

## 11. 변경 이력 요약

| 단계 | 결과 |
|---|---|
| 복잡한 provider 생산 구조 | 대량 수신 중 native heap corruption |
| 최소 가격 collector | 같은 PID 10만 건 이상 수신, HTS 가격 일치 |
| FID15·대량체결 복원형 | 100종목 등록 후 1,448건에서 비정상 종료 |
| UI 50 → 100 | stream·render 정상, collector 장애와 무관 |
| 가격 전용 collector 재복귀 | 같은 PID 유지, RealData +31,069, queue 0 |

현재 단계는 **가격 핵심 경로 재복구 성공, 장시간·재시작·다음 개장폭주 검증 중**으로 판정한다.
