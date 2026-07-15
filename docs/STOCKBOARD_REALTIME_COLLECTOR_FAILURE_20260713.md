# StockBoard v2 실시간 collector 장애·복구 보고서

최종 갱신: 2026-07-15 13:37 KST  
최초 작성: 2026-07-13 11:03 KST  
상태: **가격 전용 단일 QAx owner 복구 완료 · 프로그램 정상 · 대량체결 두 번째 QAx 생산 금지**  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`  
작업 경로: `C:\aiTrade`  
화면: `http://127.0.0.1:8765/`

---

## 0. 최종 결론

StockBoard 가격 중단의 공통 원인은 브라우저나 64비트 worker가 아니라 **32비트 Kiwoom QAx 실시간 owner의 불안정 또는 중복 실시간 등록**이었다.

최종 생산 원칙:

```text
Kiwoom 실시간 QAx owner는 한 개만 허용
생산 collector는 FID10·12·20 + sampled FID14만 사용
두 번째 QAx 로그인·SetRealReg 금지
대량체결 sidecar 생산 자동실행 금지
```

2026-07-15 두 번째 QAx sidecar를 중지하고 가격 collector 하나만 재시작한 뒤 다음이 확인됐다.

```text
연결 상태       정상
recv/s           114
trade/s          72.7
collector_q      0
worker_q         0
가격·등락률      정상 갱신
프로그램 표시    193종목
프로그램 오류    없음
```

---

## 1. 장애·복구 타임라인

### 1.1 복잡한 provider 구조

과거 생산 provider는 하나의 QAx owner process에 호가·강도·보조 TR·여러 timer와 patch를 함께 설치했다. 로그인·등록·수신 후 native heap corruption이 발생했다.

```text
ntdll.dll
0xc0000374
```

판정: 가격 핵심 callback에서 보조 기능을 분리해야 했다.

### 1.2 가격 전용 최소 collector 복구

생산 collector를 다음으로 축소했다.

```text
FID10 현재가
FID12 등락률
FID20 체결시각
FID14 누적거래대금 500ms 샘플
SetRealReg 100종목
latest-only sender
```

이 구성은 같은 PID에서 대량 실시간 이벤트를 연속 수신했고 HTS 가격과 일치했다.

### 1.3 FID15·대량체결을 같은 collector에 복원한 회귀

가격 collector에 다음을 추가하자 100종목 등록 후 1,448건 수신 시점에 프로세스가 비정상 종료했다.

```text
FID15 signed 체결량 callback read
5천만원 이상 체결 aggregate
재전송·누적 보존 처리
```

Python traceback과 정상 Qt 종료 표식은 없었다. FID15 자체, aggregate 자체, 두 경로의 결합 중 무엇이 직접 원인인지는 확정하지 않는다.

운영상 결론은 원인 증명보다 마지막 안정형으로 복귀하는 것이다.

### 1.4 프로그램 순매수 표시 장애

프로그램 순매수는 `ka90004` 응답을 받았지만 single-flight 캐시 저장 시 다음 오류가 발생했다.

```text
Object of type Decimal is not JSON serializable
```

원인: 응답 메타데이터의 `Decimal`이 `atomic_write_json()`까지 전달됐다.

조치:

```text
Decimal 정수 → int
Decimal 소수 → float
Path          → POSIX 문자열
list/tuple/set → JSON list
```

조회 횟수·주기·프로그램 값 계산은 변경하지 않았다.

검증:

```text
program_net_last_error              : 없음
program_net_count                   : 3942
program_net_display_available_count : 193
physical_fetch_count                : 1
```

### 1.5 별도 대량체결 QAx sidecar 실험

가격 collector를 건드리지 않기 위해 별도 32비트 QAx sidecar를 만들었다.

```text
sidecar FID10·15·20
5천만원 이상 체결만 delta 전송
메인 가격 이벤트 전송 없음
```

sidecar 자체는 동작했다.

```text
large_trade_sidecar_event_count     : 388
large_trade_display_available_count : 58
last_error                          : 없음
```

그러나 첫 로그인에서 메인 가격이 살아난 뒤 두 번째 로그인·SetRealReg가 수행되자 메인 가격 수신이 다시 멈췄다.

판정:

```text
별도 프로세스여도 동일 Kiwoom 세션의 두 번째 QAx 실시간 owner는 생산 가격 경로와 충돌할 수 있다.
```

### 1.6 최종 복구

다음 순서로 복구했다.

```text
대량체결 sidecar 종료
최신 원격 브랜치로 hard reset
STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED=0
남은 large_trade_collector32 프로세스 종료
restart-fast
로그인 한 번만 수행
```

최종 화면에서 가격·등락률·거래대금·프로그램이 모두 정상 동작함을 확인했다.

---

## 2. 최종 생산 구조

```text
32-bit price-only QAx collector — 유일한 실시간 owner
  FID10 현재가
  FID12 등락률
  FID20 체결시각
  FID14 누적거래대금 500ms 샘플
  100종목 등록
        ↓
64-bit worker
  Canonical State
  program ka90004 single-flight
  same-day metric cache restore
  background FeatureSnapshot
        ↓
StockBoard / ThemeBoard / StrategyProjection
```

생산에서 비활성:

```text
FID15
대량체결 live aggregate
두 번째 QAx process
호가·강도 callback 추가
보조 TR in price collector
대량체결 sidecar 자동 실행
```

---

## 3. 현재 운영 판정

| 항목 | 판정 |
|---|---|
| 현재가 | 가격 전용 collector 실시간 정상 |
| 등락률 | 가격과 같은 callback에서 정상 |
| 체결시각 | FID20 |
| 누적거래대금 | FID14 500ms 샘플 |
| 실시간 등록 | 100종목 |
| UI 표시 | 최대 100종목 |
| 프로그램 순매수 | 193종목 정상 표시 |
| 대량체결 | 생산 실시간 비활성, 같은 날 정상 캐시만 참고 가능 |
| 두 번째 QAx owner | 생산 금지 |
| ThemeBoard·StrategyBoard | 완료 FeatureSnapshot 재사용 |
| 자동매매 연결 | 다음 개장폭주·장시간 검증 전 보류 |
| PR #35 | Draft 유지 |

---

## 4. 런처 안전장치

`stockboard_v2_large.cmd`는 다음을 보장한다.

```text
시작 전 실험 sidecar 종료
STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED 기본값 0
sidecar 자동 시작 없음
가격 collector 시작 후 문구 출력:
LARGE_TRADE_SIDECAR_PRODUCTION=disabled_due_to_price_feed_conflict
```

수동 sidecar 관리자도 다음 변수 없이는 시작하지 않는다.

```text
STOCKBOARD_ALLOW_LARGE_TRADE_EXPERIMENT=1
```

이 변수는 생산에서 설정하지 않는다.

---

## 5. 실행·검증 명령

### 5.1 최신 코드 적용

```powershell
cd C:\aiTrade
git status --short
git fetch origin
git reset --hard origin/fix/restore-stable-collector-20260713
$env:STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED = "0"

Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'large_trade_collector32'
  } |
  ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
  }

.\stockboard_v2_large.cmd restart-fast
```

정상 조건:

```text
로그인 창 한 번
LoginState connected
RealRegSucceeded True
RegisteredCount 100
LastError 없음
```

### 5.2 가격 증가 확인

```powershell
$a = Invoke-RestMethod "http://127.0.0.1:8765/api/v2/snapshot?limit=1"
Start-Sleep -Seconds 10
$b = Invoke-RestMethod "http://127.0.0.1:8765/api/v2/snapshot?limit=1"

[pscustomobject]@{
  RealDataIncrease =
    [int]$b.status.collector_status.status.realdata_received_count -
    [int]$a.status.collector_status.status.realdata_received_count
  TradeIncrease =
    [int]$b.status.collector_status.status.trade_event_received_count -
    [int]$a.status.collector_status.status.trade_event_received_count
  LastError = $b.status.collector_status.status.last_error
}
```

합격:

```text
RealDataIncrease > 0
TradeIncrease    > 0
LastError        없음
```

### 5.3 프로그램 확인

```powershell
$s = (Invoke-RestMethod "http://127.0.0.1:8765/api/v2/snapshot?limit=1").status

$s | Select-Object `
  program_net_last_error, `
  program_net_count, `
  program_net_display_available_count

$s.tr_singleflight | Select-Object `
  last_mode, `
  last_error, `
  last_completed_at, `
  physical_fetch_count, `
  cache_hit_count
```

합격:

```text
program_net_last_error 비어 있음
program_net_display_available_count > 0
tr_singleflight.last_error 비어 있음
```

---

## 6. 다음 작업

1. 가격 collector 동일 PID 장시간 생존 확인  
2. 다음 09:00~09:10 개장 폭주 검증  
3. 재시작 반복 시 로그인 한 번만 표시되는지 확인  
4. queue·drop·render·top20 lag 반복 측정  
5. 대량체결은 두 번째 QAx를 사용하지 않는 대체 원천 연구  
6. collector 종료·heartbeat 정지 watchdog 설계  
7. 자동매매 연결은 위 검증 뒤 판단  

대량체결 대체 설계 후보:

```text
기존 Recorder 틱 원천 재사용
공식 REST/TR로 가능한 범위 확인
단일 QAx owner 안에서 callback 부하 없이 분리 가능한 구조 연구
장후 replay/분석용으로 우선 한정
```

검증 전 생산에 연결하지 않는다.

---

## 7. 금지사항

```text
생산에서 두 번째 QAx 로그인·SetRealReg를 실행하지 않는다.
대량체결 sidecar를 자동 시작하지 않는다.
가격 collector에 FID15를 다시 추가하지 않는다.
프로그램 순매수를 startup TR로 강제하지 않는다.
대량체결 열의 캐시값을 현재 실시간 보증값으로 해석하지 않는다.
collector가 멈췄는데 worker나 브라우저만 재시작하지 않는다.
UI100을 collector 종료 원인으로 취급하지 않는다.
장중·개장 검증 전 Draft PR을 병합하지 않는다.
```

---

## 8. 핵심 커밋

```text
60ac499a13dadc14f5fa29f8d02c062e7e3164a4
fix: restore price-only minimal QAx collector

ac661ad2f6181c97064714f64f0680ab19c4ad7d
fix: normalize Decimal payloads in TR single-flight cache

33af677222bf11f96a957997b841e42ea40c8ecc
fix: normalize cached paths across Windows and POSIX

b6c8394fe35807ebac244a512f3b056fd11765b6
feat: isolate large-trade FID15 collection from price path

07785e00cd0c3af86cb9a2f9dc2d92cc78f4c6ab
fix: disable large-trade sidecar after price feed conflict
```

최종 단계는 **가격 핵심 경로 복구 완료, 프로그램 정상, 대량체결 실시간 생산 비활성, 다음 개장폭주 검증 대기**다.
