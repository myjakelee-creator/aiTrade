# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-15 13:37 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 상세 이력은 Git history, PR 기록, `STOCKBOARD_REALTIME_COLLECTOR_FAILURE_20260713.md`에서 확인한다. 이 문서는 현재 운영 구조, 검증 결과, 남은 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard는 **32비트 가격 전용 최소 QAx collector 한 개 + 64비트 Canonical State + background FeatureSnapshot + UI100** 구조로 정상 동작한다.

2026-07-15 장중 최종 확인:

```text
연결 상태       정상
실시간 가격     정상 갱신
실시간 등락률   정상 갱신
recv/s           114
trade/s          72.7
collector_q      0
worker_q         0
프로그램 표시    193종목
프로그램 오류    없음
```

대량체결 전용 두 번째 QAx sidecar는 실제 대량체결 이벤트를 수집했지만, 두 번째 로그인·실시간 등록 직후 메인 가격 수신이 멈췄다. 따라서 **생산에서는 두 번째 QAx owner를 금지하고 sidecar를 비활성화**한다.

현재 기본 선발모델은 **`거래대금 순위 v0.1`**이다.

```text
1위        A100
12위       B89
21위       B80
100위      F1
101위 이하 F0
```

---

## 1. 현재 운영 구조

```text
32-bit production price-only QAx collector — 유일한 실시간 owner
  FID 10 현재가
  FID 12 등락률
  FID 20 체결시각
  FID 14 누적거래대금 500ms 샘플
  SetRealReg 100종목
  FID15 없음
  대량체결 계산 없음
        ↓ latest-only sender
64-bit worker Canonical State
        ↓ background heavy snapshot
거래대금 순위 v0.1
        ↓ stable display order
HOT 20 / WARM 30 / COLD 50
        ↓ shared completed FeatureSnapshot
StockBoard / ThemeBoard / StrategyProjection
```

| 항목 | 현재 원칙 |
|---|---|
| 실시간 owner | 32비트 QAx collector 한 개만 허용 |
| collector mode | `minimal_qax_price_only_v2` |
| 실시간 FID | `10;12;20;14` |
| 실시간 등록 | 100종목 |
| 브라우저 표시 | 최대 100종목 |
| 내부 후보 universe | 약 170~193종목 |
| 프로그램 순매수 | 기존 64비트 background updater + `ka90004` single-flight |
| 대량체결 | 생산 실시간 비활성, 같은 날 캐시만 표시 가능 |
| 기본 선발모델 | `거래대금 순위 v0.1` |
| 모델 설정 | `configs/candidate_models/FIVE_FACTOR_FLOW_V01.json` 단일 원천 |
| Pool | HOT 20 / WARM 30 / COLD 50 |
| ThemeBoard·StrategyBoard | 완료 FeatureSnapshot 재사용, 직접 TR 금지 |
| HTML | 표시만 담당, 시장계산·점수계산 금지 |

---

## 2. 실시간 가격 경로

### 2.1 생산 보증값

```text
현재가
등락률
체결시각
누적거래대금
```

생산 collector에서는 다음을 하지 않는다.

```text
FID15 조회
호가잔량 조회
체결강도 조회
대량체결 누적
보조 TR
두 번째 QAx 로그인
두 번째 SetRealReg owner
```

### 2.2 2026-07-15 최종 장중 확인

대량체결 sidecar를 중지하고 최신 브랜치로 맞춘 뒤 `restart-fast`를 실행했다. 로그인은 한 번만 수행됐고 가격·등락률·거래대금이 다시 정상 갱신됐다.

화면 실측:

```text
연결 지연       10.4초
recv/s          114
trade/s         72.7
collector_q     0
sent/s          143
worker_q        0
render          20.9ms
```

이 결과로 브라우저·worker·가격 collector 경로가 모두 동작하는 것을 확인했다.

---

## 3. 프로그램 순매수

프로그램 순매수는 64비트 worker의 기존 background updater가 `ka90004`를 호출한다. 시작 시 강제 TR은 수행하지 않으며 기존 저속 주기를 유지한다.

```text
program_net_refresh_policy
= existing_background_updater; no startup REST/TR request
```

`TRSingleFlightCoordinator`는 같은 요청을 중복 실행하지 않고 결과를 캐시한다. Kiwoom 응답 메타데이터의 `Decimal`은 캐시 저장 직전에 JSON 기본 숫자형으로만 정규화한다.

2026-07-15 검증:

```text
program_net_last_error              : 없음
program_net_count                   : 3942
program_net_display_available_count : 193
tr_singleflight.last_mode           : physical_fetch
tr_singleflight.last_error          : 없음
tr_singleflight.physical_fetch_count: 1
```

현재 StockBoard의 `프로(억)` 열은 정상 표시된다.

---

## 4. 대량체결 현재 정책

### 4.1 확인된 사실

대량체결 전용 별도 QAx sidecar 실험은 다음까지 동작했다.

```text
large_trade_sidecar_event_count     : 388
large_trade_display_available_count : 58
last_error                          : 없음
```

그러나 sidecar의 두 번째 `CommConnect`·`SetRealReg` 이후 메인 가격 collector의 실시간 수신이 멈췄다. 별도 프로세스라도 동일 Kiwoom 세션에서 두 개의 QAx 실시간 owner를 동시에 운영하면 생산 가격 경로와 충돌할 수 있다고 판정한다.

### 4.2 생산 정책

```text
STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED=0
LARGE_TRADE_SIDECAR_PRODUCTION=disabled_due_to_price_feed_conflict
```

- `stockboard_v2_large.cmd`는 시작 전 남아 있는 sidecar를 항상 종료한다.
- 런처는 sidecar를 자동 시작하지 않는다.
- worker는 sidecar delta patch를 생산 설치하지 않는다.
- sidecar 파일은 수동·격리 실험용으로만 남긴다.
- 수동 실행도 `STOCKBOARD_ALLOW_LARGE_TRADE_EXPERIMENT=1`을 명시해야 한다.
- 데이터 원천이 없으면 화면은 가짜 `0` 대신 `-`를 표시한다.
- 같은 날 저장된 정상 누적값은 참고용으로 복원할 수 있다.

**대량체결을 위해 가격 안정성을 희생하지 않는다.**

---

## 5. 선발모델 JSON 단일화

활성 Registry는 최종 모델 한 개만 가진다.

```text
거래대금 순위 v0.1
```

8개 선발기준 정책은 한 JSON에서 관리한다.

```text
거래대금 순위   활성 100%
순위상승        비활성 0%
대금비          비활성 0%
순간강도        비활성 0%
5분강도         비활성 0%
프로그램        비활성 0%
대량체결        비활성 0%
조합품질        비활성 0%
```

나머지 7개 기준은 JSON에 정책만 존재하며 `on_demand`다. 현재 최종 점수 경로에서는 계산하지 않아 속도 부담을 추가하지 않는다.

점수식:

```text
1 <= rank <= 100 : score = 101 - rank
rank >= 101      : score = 0
```

등급 경계도 JSON의 `grade_bands`를 사용한다.

---

## 6. UI와 Pool

```text
S1 선택 종목      1종목
집중 후보 HOT     20종목
표시 Pool WARM    30종목
표시 Pool COLD    50종목
브라우저 고유행   최대 100종목
```

같은 Pool 내부에서는 순위 변화만으로 행을 계속 이동하지 않는다. Pool 경계를 넘을 때만 유지시간·점수차·cooldown 안전장치를 통과해 교체한다.

헤더 클릭은 view-only다.

```text
첫 클릭    정렬
두 번째    역정렬
세 번째    자동 Pool 순서 복귀
```

---

## 7. 운영 명령

최신 코드 적용:

```powershell
cd C:\aiTrade
git fetch origin
git reset --hard origin/fix/restore-stable-collector-20260713
$env:STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED = "0"
.\stockboard_v2_large.cmd restart-fast
```

정상 시작 표식:

```text
LARGE_TRADE_SIDECAR_PRODUCTION=disabled_due_to_price_feed_conflict
```

상태 확인:

```powershell
.\stockboard_v2_large.cmd status
```

10초 증가 확인:

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

합격 기준:

```text
RealDataIncrease > 0
TradeIncrease    > 0
LastError        비어 있음
로그인 창        한 번만 표시
```

---

## 8. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 가격 collector 장시간 생존 | 동일 PID, RealData·Trade 지속 증가 |
| 2 | 다음 09:00~09:10 개장 폭주 검증 | queue 적체 없음, 가격 지연 허용범위 |
| 3 | 재시작 반복 검증 | 로그인 한 번, 등록 100, 가격 즉시 회복 |
| 4 | HOT/WARM 경계 교체 검증 | 유지시간·점수차·cooldown 준수 |
| 5 | 대량체결 대체 원천 설계 | 두 번째 QAx owner를 사용하지 않는 방식 |
| 6 | collector watchdog | 종료·heartbeat 정지 감지와 안전 알림 |
| 7 | Draft PR 정리 | 장중·개장 검증 후 병합 여부 판단 |

대량체결 대체 원천은 Recorder의 기존 틱 원천, 공식 REST/TR 가능 여부, 또는 가격 collector callback을 무겁게 하지 않는 단일-owner 구조를 별도 연구한다. 검증 전 생산에 연결하지 않는다.

---

## 9. 금지사항

```text
두 번째 QAx 실시간 owner를 생산에서 실행하지 않는다.
대량체결 sidecar를 자동 실행하지 않는다.
생산 가격 collector에 FID15를 다시 추가하지 않는다.
프로그램 순매수를 startup TR로 강제하지 않는다.
가격·등락률 callback에 후보점수 계산을 넣지 않는다.
HTML에서 시장계산·점수계산·직접 TR을 수행하지 않는다.
같은 Pool 안에서 순위 변화만으로 행을 자동 이동하지 않는다.
장중·개장 검증 전 Draft PR을 병합하지 않는다.
```

---

## 10. 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 생산 가격 전용 최소 QAx collector |
| `realtime_v2/worker64_guarded_large_bidask.py` | StockBoard worker/UI 진입점 |
| `realtime_v2/worker_opening_burst_cache_patch.py` | background heavy snapshot·fast overlay |
| `realtime_v2/tr_singleflight.py` | 저속 TR single-flight와 JSON-safe cache |
| `realtime_v2/worker_tr_singleflight_patch.py` | 프로그램 updater 연결·metric cache 복원 |
| `realtime_v2/worker_metric_restore_patch.py` | 프로그램·대량체결 같은 날 캐시 표시 정책 |
| `configs/candidate_models/FIVE_FACTOR_FLOW_V01.json` | 최종 8기준 JSON 정책 |
| `configs/candidate_models/_registry.json` | 활성 최종모델 1개 Registry |
| `realtime_v2/large_trade_collector32.py` | 수동 격리실험용 sidecar, 생산 금지 |
| `scripts/stockboard_large_trade_sidecar.ps1` | sidecar 수동 실험 관리자, 기본 실행 차단 |
| `stockboard_v2_large.cmd` | 생산 런처·sidecar 종료·자동실행 금지 |
| `tests/test_tr_singleflight.py` | Decimal 캐시 직렬화 계약 |
| `tests/test_worker_metric_restore_patch.py` | 캐시 복원·가짜 0 방지 계약 |
| `tests/test_large_trade_sidecar.py` | sidecar 생산 비활성 계약 |
