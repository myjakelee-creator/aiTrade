# StockBoard v2 실시간 가격·등락률 갱신 중단 장애 보고서

작성 기준: 2026-07-13 11:03 KST  
상태: **미해결 · 실전 사용 중지**  
대상 브랜치: `hot-priority-integrated-20260630`  
작업 경로: `C:\aiTrade`  
화면: `http://127.0.0.1:8765/`

---

## 0. 한 줄 결론

StockBoard의 브라우저·SSE·worker 처리속도는 충분하지만, 32비트 Kiwoom OpenAPI collector가 생산용 구성에서 로그인 완료 전에 조용히 종료되거나 재접속 직후 잠시 수신한 뒤 중단된다. 같은 PC에서 최소 QAx 테스트를 정식 `QApplication.exec_()`로 실행하면 삼성전자 `_AL` 체결을 30초 동안 1,907건 연속 수신하므로, Kiwoom 설치·계정·`_AL`·SetRealReg 자체가 아니라 **생산용 collector에 추가된 patch/timer/thread/등록 순서 중 하나와 Qt/OpenAPI 수명주기의 충돌**이 현재 핵심 원인 범위다.

---

## 1. 실전 영향

| 항목 | 현재 영향 |
|---|---|
| 현재가 | 마지막 수신값 또는 시작 시드값에서 정지할 수 있음 |
| 등락률 | 현재가와 함께 정지하며 HTS와 불일치할 수 있음 |
| 거래대금 | 누적값이 멈춰 순위가 왜곡됨 |
| 거래대금 순위 | 오래된 값으로 재정렬되므로 후보 선정 신뢰 불가 |
| 등급·후보 | stale 가격·대금·강도 입력으로 계산될 수 있음 |
| 순간강도·5분강도 | 마지막 정상값 또는 보존값이 계속 표시될 수 있음 |
| 상단 `stream`·`render` | 빨라도 원천 collector가 멈추면 오래된 값을 빠르게 다시 보여줄 뿐임 |
| 자동매매 활용 | 금지. 연속 실시간 수신 검증 전 실전 사용 불가 |

**중요:** 화면에 숫자가 채워져 있고 `rt` 종목 수가 많아도 현재 수신이 살아 있다는 뜻이 아니다. `rt`는 해당 종목이 실행 이후 한 번이라도 realtime 행으로 바뀐 이력에 가깝고, 실제 신선도는 `연결 지연`, `RealDataLastAt`, `TradeReceived`, `WorkerTrades`, `price_age_sec`로 판단해야 한다.

---

## 2. 현재 최종 장애 증거

### 2.1 생산용 collector stdout

```text
collector codes=186 suffix=AL orderbook=False flush_ms=50
provider_start=True
registered_count=186
qt_event_loop=exec_ pump_timer_ms=20
```

이 출력은 다음 단계까지 실행됐다는 뜻이다.

```text
코드 186개 로드
→ QAxWidget 생성 및 provider.start_inline_qt() 반환
→ register_codes() 호출
→ 생산용 QTimer 생성
→ app.exec_() 진입 직전 로그 출력
```

그러나 다음 출력은 나타나지 않았다.

```text
OnEventConnect 성공 확인
실제 SetRealReg 성공 이후의 연속 실시간 이벤트
collector status heartbeat 갱신
```

`registered_count=186`은 로그인 완료 후 실제 등록 성공 수로 단정할 수 없다. provider의 `register_codes()`가 로그인 전에는 요청 코드를 대기열에 넣고 요청 개수를 반환할 수 있기 때문이다.

### 2.2 3분 생존 측정

```text
Time     Alive PID Login     RealReg QtExec QtTicks QtLast RealData ProviderTrades
10:59:53 False     requested   False   True       0               0              0
...
11:03:31 False     requested   False   True       0               0              0
```

확정 해석:

| 필드 | 해석 |
|---|---|
| `Alive=False` | collector 프로세스가 WMI 프로세스 목록에서 검출되지 않음 |
| `Login=requested` | 마지막 상태는 `CommConnect()` 요청 직후 |
| `RealReg=False` | 실제 실시간 등록 완료 전 종료 |
| `QtExec=True` | worker에 남은 마지막 collector 상태. 현재 프로세스 생존 증거가 아님 |
| `QtTicks=0` | 생산용 20ms QTimer callback이 한 번도 정상 완료되지 않았거나, 첫 heartbeat 전에 종료 |
| `RealData=0` | 이 실행에서 OpenAPI 실시간 이벤트 0건 |
| `ProviderTrades=0` | provider가 worker로 넘긴 체결 0건 |

따라서 현재 실패 구간은 매우 좁다.

```text
app.exec_() 진입 직전
→ 첫 20ms collector_tick 정상 완료 전
→ OnEventConnect/실제 등록/첫 heartbeat 전
```

---

## 3. 정상 동작한 최소 QAx 검증

같은 PC, 같은 32비트 Python, 같은 Kiwoom OpenAPI, 같은 `_AL` 코드에서 최소 테스트를 정식 Qt 이벤트 루프로 실행했다.

```text
HWND=1383322
CommConnect=0
OnEventConnect=0
SetRealReg=0
REAL count=1 code=005930_AL type=주식체결
REAL count=10 code=005930_AL type=주식체결
REAL count=100 code=005930_AL type=주식체결
REAL count=500 code=005930_AL type=주식체결
REAL count=1000 code=005930_AL type=주식체결
REAL count=1500 code=005930_AL type=주식체결
APP_RESULT=0 LOGIN_RESULT=0 REAL_COUNT=1907
EXIT_CODE=0
EXIT_HEX=0x00000000
```

이 결과로 확인된 사항:

| 항목 | 판정 |
|---|---|
| 32비트 Python | 정상 |
| PyQt5/QAxWidget | 정상 |
| KHOpenAPI ActiveX 생성 | 정상 |
| native HWND | 정상 |
| `CommConnect()` | 정상 |
| `OnEventConnect` | 정상 |
| `SetRealReg` | 정상 |
| 삼성전자 `_AL` | 정규장에서 연속 체결 수신 정상 |
| 정식 `app.exec_()` | 30초 지속 정상 |
| Windows 종료코드 | 0, 정상 종료 |

따라서 다음 가설은 배제한다.

```text
Kiwoom OpenAPI 설치 자체 불량
계정 로그인 자체 불량
_AL은 정규장에서 연속 체결을 주지 않는다
SetRealReg 자체 실패
Windows/PC가 모든 QAx 프로세스를 강제 종료한다
```

---

## 4. 생산용 collector와 정상 최소 테스트의 차이

### 4.1 정상 최소 테스트

```text
QApplication 생성
→ QAxWidget 생성
→ native HWND 생성
→ CommConnect
→ OnEventConnect 성공 callback 내부에서 SetRealReg
→ app.exec_()
→ 30초 실시간 수신
```

특징:

- 단일 QAxWidget
- 등록은 로그인 성공 callback 이후 수행
- 별도 sender thread 없음
- worker TCP 연결 없음
- off-hours 복구 controller 없음
- strength/orderbook/close-metric scheduler 없음
- 생산용 patch stack 없음
- 종료용 QTimer 외 복잡한 timer 없음

### 4.2 생산용 collector

```text
여러 provider patch 설치
→ collector32_large import
→ EventSender thread 시작
→ PublishingStore 연결
→ QAxWidget/native HWND/CommConnect
→ 로그인 완료 전 register_codes(186) 호출 가능
→ off-hours QTimer 설치
→ 생산용 20ms QTimer 설치
→ 초기 collector status 전송
→ app.exec_()
```

생산용 진입점 `realtime_v2/collector32_large_bidask.py`에는 다음 계층이 설치된다.

| 계층 | 목적 | 현재 위험 |
|---|---|---|
| `qt_main_thread_openapi_patch` | QAx owner thread 유지 | 이번 장애 중심 구간 |
| `openapi_native_handle_patch` | 실제 HWND 생성 | 최소 테스트에서 정상 확인 |
| `strength5m_snapshot_fallback_patch` | 5분강도 보존 | 생산용에만 존재 |
| `strength5m_definitive_preopen_patch` | 장전 강도 보충 | 생산용에만 존재 |
| `offhours_metric_completion_patch` | 휴장시간 빈칸 보충 | 생산용에만 존재 |
| `offhours_metric_resilience_patch` | 보충 오류 복구 | 생산용에만 존재 |
| `offhours_metric_timer_driver_patch` | Qt timer 기반 보충 실행 | `app.exec_()` 시작 직후 callback 가능 |
| `collector_sender_resilience_patch` | sender fail-open | 생산용에만 존재 |
| `collector_readiness_gate_patch` | 로그인·실등록 완료 판정 | 상태 표시 보완, 종료 원인으로 확정되지 않음 |
| `orderbook_thin_scheduler` | 호가 부하 절감 | fast-open에서도 import/install됨 |

현재 가장 중요한 사실은 **최소 테스트와 생산용 collector가 동일한 Qt 이벤트 루프를 사용해도 결과가 다르다**는 점이다. 따라서 `app.processEvents()`만을 단일 원인으로 확정한 이전 판단은 철회한다.

---

## 5. 지금까지의 장애 진행 순서

| 시각/단계 | 관찰 | 판정 |
|---|---|---|
| 08:59 장전 | `trades 0`, `rt 0`, 가격·등락률 표시 | 실시간이 아니라 seed 값 표시 |
| 09:14 | `LoginState=requested`, `RealRegSucceeded=False`, `RealData=0` | 로그인 완료 전 준비 오판 |
| readiness gate 적용 후 | `connected`, `RealReg=True`, 초기 `RealData/TradeReceived` 증가 | 로그인·등록 경로는 일시 복구 |
| 약 09:27 재접속 | 여러 종목 값이 한 번 들어온 뒤 고정 | 지속 수신 실패 |
| 당시 상태 | `QtPumpLastAt`, `TimerLastTickAt`, `RealDataLastAt`이 같은 시각에서 정지 | collector 또는 Qt owner thread 중단 |
| 안전 launcher 복원 | opstarter 로그인 중 강제 종료 방지 | 필요 조치였으나 단독 해결 실패 |
| pre-start cleanup 복원 | 시작 전 고아 opstarter 정리 | 필요 조치였으나 단독 해결 실패 |
| 생산용 `app.exec_()` 적용 | stdout에 `qt_event_loop=exec_` 출력 | 이벤트 루프 진입은 했으나 즉시 종료 |
| 최소 QAx `app.exec_()` | 30초 1,907건 연속 수신 | OpenAPI·`_AL`·Qt 기본환경 정상 확정 |

---

## 6. 적용된 최근 수정과 현재 평가

| PR | 변경 | 평가 |
|---|---|---|
| `#30` | ThemeBoard 전체 롤백 | StockBoard 파일을 ThemeBoard 전 상태로 복구. 현재 장애의 직접 해결책은 아님 |
| `#31` | 실제 로그인·SetRealReg 성공 전 `registered_count=0` | 준비 오판 방지에 필요, 유지 가치 있음 |
| `#32` | 로그인 중 opstarter 강제 종료 금지 | HWND 수명 보호에 필요, 유지 가치 있음 |
| `#33` | 시작 전 고아 opstarter 정리·다른 OpenAPI host 차단 | 단일 인스턴스 충돌 방지에 필요, 유지 가치 있음 |
| `#34` | 생산용 collector를 `app.exec_()`로 전환 | 최소 테스트 논리와 맞지만 생산용에서는 여전히 즉시 종료. **해결 완료 판정 금지** |

현재 상태에서는 #31~#34를 계속 덧대기보다, 생산용 patch stack을 최소 테스트에 한 계층씩 추가하는 **이분 진단**이 필요하다.

---

## 7. 배제된 원인과 아직 배제되지 않은 원인

### 7.1 배제된 원인

| 원인 후보 | 배제 근거 |
|---|---|
| 브라우저 렌더 병목 | `render 12~16ms` 수준 |
| SSE 자체 중단 | stale 상태에서도 `stream 200~2,000ms`로 worker snapshot 전달 |
| worker queue 적체 | `worker_q 0~1` 수준 |
| sender TCP 연결 자체 불가 | 초기 수신 구간에서 `sent/s` 증가 확인 |
| `_AL` 정규장 무수신 | 최소 테스트에서 30초 1,907건 수신 |
| Kiwoom 로그인 불가 | 최소 테스트 `OnEventConnect=0` |
| SetRealReg 실패 | 최소 테스트 `SetRealReg=0` |
| 기본 `app.exec_()` 불안정 | 최소 테스트 정상 지속 |
| 단순 Python traceback | stderr 0바이트, traceback 없음 |
| 명확한 Windows application crash | 당시 Application Error/WER 조회 결과 없음 |

### 7.2 아직 배제되지 않은 원인

우선순위는 아래와 같다.

| 우선순위 | 원인 후보 | 이유 |
|---:|---|---|
| 1 | 로그인 전에 `register_codes()`를 호출하는 생산용 순서 | 최소 테스트는 `OnEventConnect=0` 이후 SetRealReg. 생산용은 로그인 전에 요청 개수를 반환 |
| 2 | `offhours_metric_timer_driver`의 QTimer callback | 생산용에만 있고 `app.exec_()` 직후 첫 callback이 실행될 수 있음 |
| 3 | 생산용 20ms `collector_tick` 첫 callback | `QtTicks=0`이므로 첫 callback 진입·완료 전 종료 가능성 |
| 4 | 두 개 이상의 QTimer/patch wrapper 조합 | off-hours timer와 collector timer가 동시에 owner thread에서 동작 |
| 5 | strength/orderbook/opt10055/close-metric pending 처리 | `pump_inline_qt_once()`가 실시간 이벤트 외 보조 큐를 한 번에 처리 |
| 6 | EventSender thread·worker socket과 QAx 초기화의 상호작용 | 최소 테스트에는 sender thread가 없음 |
| 7 | `hide_console_after_login` 또는 launcher 부모 프로세스 수명 | 이번 실행은 로그인 전 종료이므로 우선순위는 낮지만 완전 배제 전 |
| 8 | 네이티브 종료가 Python/Windows 로그를 남기지 않는 경우 | stderr·WER가 비어 있어도 ActiveX 내부 종료 가능성은 남음 |

---

## 8. 화면 상단 속도값의 정확한 해석

장애 직전 화면 예시:

```text
rows 187 · events 2332 · trades 890
stream 258 ms
recv/s 1,085 · trade/s 235
collector_q 85 · sent/s 330 · cxl 20513
worker_q 1 · drop 1293 · logdrop 0
top20 lag 12.0s · stale 20 · rt 185
render 12.6 ms
연결 지연 82.5s
```

| 항목 | 해석 | 평가 |
|---|---|---|
| `rows 187` | universe 표시 행 수 | 정상 |
| `events 2332` | worker가 누적 수신한 전체 이벤트 | 과거 누적값 |
| `trades 890` | worker가 누적 적용한 체결 | 과거 누적값 |
| `stream 258ms` | worker snapshot→브라우저 지연 | 정상이나 원천 신선도와 별개 |
| `recv/s 1,085` | 직전 두 snapshot 누적 event 차이 | 초기 폭주 구간 값이 잠시 남을 수 있음 |
| `trade/s 235` | 직전 두 snapshot 누적 trade 차이 | 초기 폭주 구간 값 |
| `collector_q 85` | collector sender의 미전송 최신 이벤트 수 | 종료 직전 잔여 가능성 |
| `sent/s 330` | collector가 worker로 보낸 속도 | 초기에는 충분 |
| `cxl 20513` | 같은 종목의 대기 중간 체결을 최신 체결로 덮은 누적 횟수 | 최신 화면 우선 coalescing 동작 |
| `worker_q 1` | worker event 처리 대기 | 병목 아님 |
| `drop 1293` | worker guard가 오래된 FID20·누적값 역행 등을 거부한 횟수 | 높음. 원천 혼합/초기 burst 별도 분석 필요 |
| `logdrop 0` | 비동기 로그 queue 유실 | 정상 |
| `top20 lag 12.0s` | Top20 FID20 최대 지연 | 이미 실전 허용 범위 밖 |
| `stale 20` | Top20 전 종목이 3초 이상 오래됨 | 수신 중단 확정 |
| `rt 185` | realtime 행으로 바뀐 누적 종목 수 | 현재 생존 지표 아님 |
| `render 12.6ms` | DOM 렌더 시간 | 매우 양호 |
| `연결 지연 82.5s` | 마지막 worker/collector 이벤트 이후 경과 | 수신 중단 확정 |

---

## 9. 현재 코드 구조의 핵심 위험

### 9.1 생산용 `register_codes` 호출 시점

현재 생산용 흐름은 대략 다음과 같다.

```text
provider.start_inline_qt()
→ 내부에서 CommConnect()
→ 즉시 provider.register_codes(codes)
→ app.exec_()
→ 나중에 OnEventConnect callback
```

정상 최소 테스트는 다음과 달랐다.

```text
CommConnect()
→ app.exec_()
→ OnEventConnect(0)
→ callback 내부에서 SetRealReg
```

다음 수정은 가장 먼저 독립 검증해야 한다.

```text
register_codes 요청은 저장만 함
→ OnEventConnect 성공 callback에서 실제 SetRealReg
→ 실제 등록 성공 뒤 readiness=True
```

### 9.2 첫 20ms timer callback이 너무 많은 일을 수행

생산용 `collector_tick()`은 다음 wrapper를 호출한다.

```text
provider.pump_inline_qt_once()
```

그리고 이 함수는 현재 다음 작업을 연속 수행한다.

```text
pending realtime registration
orderbook rotation
strength probe queue
orderbook probe queue
opt10055 probe queue
close metrics queue
```

로그인 직후 첫 tick에는 **실시간 등록만** 처리하고, 나머지 TR/보조 queue는 로그인·등록·연속 실시간 수신이 안정된 뒤 단계적으로 활성화해야 한다.

### 9.3 정규장 실시간과 휴장시간 보충이 같은 QAx owner thread를 공유

`offhours_metric_timer_driver`는 “독립 timer”라고 표현돼 있지만 별도 OS thread가 아니다. 같은 Qt owner thread의 QTimer callback이다. callback 내부의 `drain.tick()`이 느리거나 block되면 주식체결 callback도 함께 멈춘다.

정규장에는 휴장시간 빈칸 보충이 필요 없으므로 다음 원칙이 안전하다.

```text
정규장 09:00~15:30:
실시간 체결 처리 최우선
휴장시간 completion timer drain 완전 금지
보조 TR 최소화

정규장 외:
completion controller 허용
```

---

## 10. 다음 진단 계획 — 반드시 한 계층씩

### 10.1 원칙

- 생산용 전체 patch를 한 번에 수정하지 않는다.
- 최소 정상 테스트에 기능을 한 계층씩 추가한다.
- 각 단계는 전면 console에서 최소 60초 실행한다.
- 각 단계마다 `OnEventConnect`, actual SetRealReg, real count, timer tick, exit code를 기록한다.
- 실패하는 최초 단계를 원인으로 고정한다.

### 10.2 단계별 비교 실험

| 단계 | 추가 구성 | 성공 기준 |
|---:|---|---|
| A | 현재 최소 QAx smoke | 이미 1,907건/30초 성공 |
| B | `openapi_native_handle_patch` 적용 | 60초 연속 수신 |
| C | `EventSender + PublishingStore`만 추가 | worker 없이 sender thread 생존 |
| D | worker TCP 연결 추가 | `sent_count`, worker trade 증가 |
| E | 186종목 codes load, **로그인 성공 후** 일괄 등록 | 전 종목 연속 수신 |
| F | readiness gate 추가 | 실제 등록 후에만 ready |
| G | sender resilience 추가 | 이벤트 직렬화/재연결 정상 |
| H | 20ms QTimer에 pending realtime만 추가 | Qt tick·RealData 동시 증가 |
| I | orderbook rotation 추가 | 지속 수신 유지 |
| J | strength probe 추가 | 지속 수신 유지 |
| K | orderbook/opt10055/close queue 추가 | 지속 수신 유지 |
| L | offhours completion timer 추가하되 정규장 drain 차단 | 지속 수신 유지 |
| M | 최종 `collector32_large_bidask.py` | 10분 지속 수신 |

### 10.3 필수 계측 추가

다음 로그를 파일에 `flush=True`로 남겨야 한다.

```text
process_start
qapplication_created
qax_created
native_hwnd_ready
commconnect_called
exec_enter
collector_tick_enter_1
collector_tick_exit_1
on_event_connect_enter
on_event_connect_exit
setrealreg_begin
setrealreg_end
first_real_event
real_event_100
real_event_1000
about_to_quit
atexit
```

추가 상태 필드:

```text
collector_pid
process_start_at
on_event_connect_count
on_event_connect_last_code
qt_timer_callback_enter_count
qt_timer_callback_exit_count
qt_timer_callback_inflight
qt_timer_callback_last_step
first_real_event_at
last_real_event_at
process_exit_reason
```

`QtTicks=0`처럼 callback 완료 횟수만 기록하면 첫 callback 내부 어디에서 멈췄는지 알 수 없다. enter/step/exit를 분리해야 한다.

---

## 11. 다음 작업자가 먼저 확인할 파일

| 우선순위 | 파일 | 확인 포인트 |
|---:|---|---|
| 1 | `realtime_v2/qt_main_thread_openapi_patch.py` | `register_codes` 호출 시점, 첫 QTimer callback, `app.exec_()` 수명 |
| 2 | `realtime_v2/collector32_large_bidask.py` | patch 설치 순서 |
| 3 | `realtime_v2/offhours_metric_timer_driver_patch.py` | 정규장 timer drain 완전 차단 여부 |
| 4 | `realtime_v2/offhours_metric_completion_patch.py` | QAx owner thread에서 URL read/TR 호출 가능성 |
| 5 | `realtime_v2/strength5m_scheduler.py` | background thread→provider queue 상호작용 |
| 6 | `kiwoom_data_provider.py` | login callback, deferred registration, pending queue 처리 |
| 7 | `realtime_v2/collector_sender_resilience_patch.py` | sender thread 예외·프로세스 종료 영향 |
| 8 | `scripts/stockboard_v2_large_safe.ps1` | collector 부모 수명·환경변수·console hide |
| 9 | `scripts/stockboard_v2_openapi_preflight.ps1` | 시작 전 단일 인스턴스 정리 |

참고 문서:

- `docs/STOCKBOARD_CURRENT_STATUS_20260625.md`
- 롤백 전 branch의 `docs/OPENAPI_LOGIN_HANDLE_FIX_20260712.md`

현재 기준문서의 “휴장시간 독립 Qt timer 검증 완료”는 **휴장시간 기능 검증**을 의미하며, 정규장 OpenAPI collector의 장시간 생존까지 보증하지 않는다.

---

## 12. 재현 및 상태 확인 명령

모든 명령은 작업 디렉터리를 먼저 맞춘다.

### 12.1 현재 브랜치와 변경 상태

```powershell
cd C:\aiTrade

git branch --show-current
git rev-parse --short HEAD
git status --short
```

### 12.2 collector 생존 확인

```powershell
cd C:\aiTrade

Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -like '*realtime_v2\collector32_large_bidask.py*'
  } |
  Select-Object ProcessId,Name,CreationDate,CommandLine |
  Format-List
```

### 12.3 최신 collector 로그

```powershell
cd C:\aiTrade

$runtime = 'C:\aiTrade\data\runtime\stockboard_v2'

$out = Get-ChildItem $runtime -Filter 'collector32_large_*.out.log' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$err = Get-ChildItem $runtime -Filter 'collector32_large_*.err.log' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

"STDOUT=$($out.FullName)"
Get-Content $out.FullName -Tail 100

"STDERR=$($err.FullName)"
Get-Content $err.FullName -Tail 100
```

### 12.4 API 핵심 상태

```powershell
cd C:\aiTrade

$r = Invoke-RestMethod `
  "http://127.0.0.1:8765/api/v2/snapshot?limit=1&ts=$([DateTimeOffset]::Now.ToUnixTimeMilliseconds())"

$c = $r.status.collector_status
$p = $c.status

[pscustomobject]@{
  LoginState       = $p.login_state
  RealRegSucceeded = $p.realreg_succeeded
  RegisteredCount  = $c.registered_count
  QtExec            = $p.qt_exec_loop_active
  QtTicks           = $p.qt_exec_loop_tick_count
  QtLast            = $p.qt_exec_loop_last_tick_at
  RealData          = $p.realdata_received_count
  TradeReceived     = $p.trade_event_received_count
  RealDataLastAt    = $p.realdata_last_received_at
  WorkerEvents      = $r.status.event_count
  WorkerTrades      = $r.status.trade_count
  WorkerLastEventAt = $r.status.last_event_at
  Queue             = $c.sender_stats.pending_total_count
} | Format-List
```

---

## 13. 해결 완료 판정 기준

다음 항목을 모두 만족하기 전에는 해결 완료로 판정하지 않는다.

| 검증 | 합격 기준 |
|---|---|
| collector 생존 | 10분 이상 같은 PID 유지 |
| 로그인 | `login_state=connected` |
| 실제 등록 | `realreg_succeeded=True`, 실제 등록 종목 수 양수 |
| Qt loop | `qt_exec_loop_active=True`, tick 지속 증가 |
| provider 실시간 | `RealData`, `TradeReceived`가 매 측정 구간 증가 |
| worker 실시간 | `WorkerTrades` 지속 증가 |
| 최신 시각 | `RealDataLastAt`, `WorkerLastEventAt` 현재 시각 근접 |
| Top20 stale | 장중 활발한 종목 기준 0에 가깝게 유지 |
| 가격 정합성 | SK하이닉스·삼성전자·LG전자·현대차·LS ELECTRIC 등 HTS 대조 |
| 등락률 정합성 | 가격과 동일 이벤트 FID12 기준 HTS 대조 |
| 재시작 | 3회 연속 정상 로그인·등록·10분 생존 |
| 장개시 | 09:00~09:10 폭주 구간 별도 검증 |

추가로 `drop`은 누적 숫자만 보지 말고 원인별 증가율을 나눠야 한다.

```text
older_fid20_than_last_accepted
cumulative_trade_value_decreased
cumulative_volume_decreased
market_session_closed
```

정상 실시간이 복구된 뒤에도 drop 증가가 빠르면 KRX/NXT/통합 `_AL` 이벤트 순서와 누적값 원천 혼합을 별도 진단한다.

---

## 14. 금지 사항

- 화면의 숫자가 움직였다는 이유만으로 해결 완료 판정 금지
- 재접속 직후 초기 snapshot 한 번을 연속 실시간으로 오인 금지
- `registered_count` 요청 수를 실제 SetRealReg 성공 수로 오인 금지
- `stream`, `render`, `rt`만 보고 원천 수신 정상 판정 금지
- 전체 patch stack을 한 번에 다시 수정 금지
- 로그인 진행 중 또는 살아 있는 collector의 opstarter 강제 종료 금지
- 장중 collector owner thread에서 휴장시간 보충 TR 무제한 실행 금지
- 실제 10분 지속 검증 전 현재 상태 문서에 “완료” 기록 금지
- 자동매매 입력으로 사용 금지

---

## 15. 현재 최종 판정

```text
StockBoard UI: 정상
SSE/브라우저 렌더: 정상
worker 처리속도: 정상
최소 Kiwoom QAx + _AL + app.exec_: 정상
생산용 collector: 비정상 종료
가격·등락률 지속 갱신: 실패
실전 사용 가능 여부: 불가
최종 root cause: 미확정
현재 원인 범위: 생산용 collector patch/timer/thread/로그인 전 등록 순서와 Qt/OpenAPI 수명주기의 충돌
```

다음 작업의 시작점은 `app.exec_()`를 다시 바꾸는 것이 아니라, **성공한 최소 QAx 테스트에 생산용 요소를 한 계층씩 추가해 최초 실패 계층을 찾는 것**이다.
