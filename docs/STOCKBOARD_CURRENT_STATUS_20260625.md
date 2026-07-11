# StockBoard Current Status

작성 기준: 2026-07-11 휴장시간 빈칸 보충·독립 타이머·저부하 최적화 검증 반영.

> 이 문서는 StockBoard의 현재 운영 상태를 빠르게 파악하기 위한 기준문서다. 새 PC 또는 새 채팅창에서는 이 문서, `docs/candidate_model_specs/NET_BUY_STRENGTH_V02.md`, `configs/candidate_models/NET_BUY_STRENGTH_V02.json`을 우선 확인한다.

---

## 0. 현재 한 줄 요약

StockBoard는 `순매수 강도 v0.2` 전용 ranking engine, 전일 거래대금 cache/API 주입, 장중 700점/장마감 600점 분모 이원화까지 반영됐다. 2026-07-11에는 휴장시간에 시장 틱이 없어도 5분강도·순간강도·잔량비 빈칸을 자동 보충하는 V2 통합 컨트롤러, Qt 메인 스레드 독립 타이머, collector→worker 상태 송신 fail-open, 완료 상태 저부하 최적화까지 실제 검증했다. 현재 휴장시간 세 지표 빈칸은 0개이며, 다음 핵심 과제는 노트북 동일 동작 확인과 정규장 장중 700점 분모/실시간 1분강도 검증이다.

---

## 1. 기준 정보

| 항목 | 내용 |
|---|---|
| 프로젝트 | aiTrade / StockBoard |
| 작업 경로 | `C:\aiTrade` |
| 브랜치 | `hot-priority-integrated-20260630` |
| 기준일 | 2026-07-11 |
| 현재 선발기준 | `NET_BUY_STRENGTH_V02` / 화면명 `순매수 강도 v0.2` |
| V2 표준 실행 | `.\stockboard_v2_large.cmd start-fast` / `http://127.0.0.1:8765/` |
| 핵심 테스트 | ranking/previous trade value + off-hours metric/timer/sender resilience 테스트 |
| 다음 핵심 과제 | 노트북 동일 검증, 정규장 장중 700점 분모/실시간 1분강도 검증 |

---

## 2. 대표님 운영 원칙

| 항목 | 원칙 |
|---|---|
| 언어 | 한국어 존댓말 |
| 답변 | 표와 단계 중심 |
| 문서 | 새 문서 남발 금지, 기존 핵심 문서 최소 갱신 |
| Git | `git add .` 금지, 의미 있는 단위로만 커밋 |
| 보고 | 변경 파일, 검증 명령, 미검증 항목, 커밋 상태 분리 보고 |
| 코드 수정 | 검증 가능한 작은 패치 단위 선호 |
| UI | 표시 전용. 계산은 Python/server/ranking engine 쪽에서 처리 |

---

## 3. 완료 작업 요약

| 구분 | 완료 내용 | 확인 상태 |
|---|---|---|
| 선발기준 | `순매수 강도 v0.2` 전용 ranking engine 구현 | API/화면 반영 확인 |
| 등급정책 | A90, B80, C70, D60, F59 정책 고정 | 테스트 반영 |
| 전일 거래대금 | `ka10086` 기반 `prev_trade_value_eok` cache/API 주입 | 179개 row 주입 확인 |
| 금액 점수 | `trade_value_eok / prev_trade_value_eok` 비율 줄세우기 | SK하이닉스 1.656배, 73.03점 확인 |
| 장마감 1분강도 | `strength_5m`는 표시값으로만 유지, 계산 제외 | `display_only`, `possible_points=0` 확인 |
| 분모 이원화 | 장중 700점, 장마감 600점 | 장마감 `score_possible_points=600` 확인 |
| Top5 | 점수/분모/항목 breakdown 출력 확인 | 후보 산출 정상 |
| UI 표시 | 1분강도 칸은 숫자만 표시하는 원상복구 완료 | 화면 확인 완료 |
| 휴장시간 빈칸 보충 | 5분강도·순간강도·잔량비 통합 순차 조회 | 토요일 실제 빈칸 0개 확인 |
| 틱 독립 실행 | 시장 틱 0건에서도 Qt 타이머로 자동 진행 | `market_tick_independent=True` 확인 |
| 저부하 최적화 | 250ms 심박 + 완료 상태 drain 10초 주기 | 실측 상태값으로 확인 |
| 상태 송신 안정화 | collector sender 직렬화/루프 fail-open | `SenderAlive=True`, 오류 0 확인 |

---

## 4. 순매수 강도 v0.2 최종 계산 정책

| 항목 | 장중 점수 | 장마감 점수 | 비고 |
|---|---:|---:|---|
| 순위 | 100 | 100 | 필터 통과 N개 기준 100~0점 |
| 전일 | 100 | 100 | `min(max(prev_rank-rank,0),100)` |
| 금액(억) | 100 | 100 | `trade_value_eok / prev_trade_value_eok` 비율 줄세우기 |
| 잔량비 | 100 | 100 | `ask_volume / (bid_volume + ask_volume) × 100` |
| 순간강도 | 100 | 100 | 체결강도 0~200을 0~100점 환산 |
| 1분강도 | 100 | 0 | 장중 실시간 1분값만 계산, 장마감은 계산 제외 |
| 프로(억) | 100 | 100 | `program_net / trade_value_eok`, 순매수 0 이하는 0점 |
| 총 분모 | 700 | 600 | `score_total_points / score_possible_points × 100` |

중요:

```text
장마감에는 1분강도 칸에 5분강도 참고값을 숫자로만 표시한다.
하지만 점수 계산에서는 1분강도를 제외한다.
따라서 장마감 등급은 600점 분모로 계산한다.
```

---

## 5. 전일 거래대금 주입 상태

| 항목 | 기준 |
|---|---|
| cache 파일 | `data/runtime/previous_trade_value_YYYYMMDD.json` |
| 원천 우선순위 | `ka10086_amt_mn` → 전일 종가×전일 거래량 계산 → cache → fallback |
| missing 처리 | 0 저장 금지 |
| 최종 실패 | 금액(억) 항목 60점 fallback, 화면/툴팁에 미확인 표시 필요 |
| 검증 결과 | cache 생성 및 API row 주입 확인 |

검증 예시:

| 종목 | 오늘 거래대금 | 전일 거래대금 | 상태 |
|---|---:|---:|---|
| SK하이닉스 | 294,414.76억 | 177,745.37억 | `ok / ka10086_amt_mn` |
| 삼성전자 | 171,834.63억 | 113,628.82억 | `ok / ka10086_amt_mn` |

---

## 6. 장마감 검증 결과

### 6.1 SK하이닉스 breakdown

| 항목 | 점수 | 분모 | 상태 |
|---|---:|---:|---|
| 순위 | 100.0 | 100 | 계산 포함 |
| 전일 | 0.0 | 100 | 계산 포함 |
| 금액(억) | 73.03 | 100 | 계산 포함 |
| 잔량비 | 94.96 | 100 | 계산 포함 |
| 순간강도 | 54.47 | 100 | 계산 포함 |
| 1분강도 | 0.0 | 0 | `display_only` |
| 프로(억) | 0.0 | 100 | `nonpositive` |

계산:

```text
322.46 / 600 = 53.74 → F54
```

### 6.2 장마감 Top5 관찰값

| 후보 | 등급 | 점수 | 분모 | 핵심 |
|---:|---|---:|---:|---|
| 1 | 한국금융지주 | B86 | 600 | 전일상승 + 금액 + 잔량비 + 순간강도 + 프로그램 강함 |
| 2 | 삼성증권 | B83 | 600 | 금액/잔량비/프로그램 강함 |
| 3 | 인텍플러스 | C79 | 600 | 전일상승 + 금액 + 강도 + 프로그램 양호 |
| 4 | 미래에셋증권 | C77 | 600 | 순위/금액/잔량비/프로그램 고르게 양호 |
| 5 | 한화시스템 | C75 | 600 | 전일상승 + 금액 + 잔량비 + 프로그램 양호 |

주의:

```text
금융/증권주가 Top5에 다수 올라오는 현상이 관찰됐다.
현재는 계산 오류로 보지 말고, 정규장 장중 데이터에서 프로그램 비율/전일상승 점수 쏠림을 추가 검증한다.
```

---

## 7. 현재 화면 그룹 / lane 구조

| 화면명 | 내부명 | 설명 |
|---|---|---|
| Top5 | `candidateRows` / `top5` | 유력 후보 5종목 |
| S1 | `selectedRow` | 선택 종목 1개 |
| Top15 | `top20Rows` | Top5 제외 후 실제 15종목 |
| Top30 | `top50Rows` | Top20 제외 후 실제 30종목 |
| Top300 | `top300Rows` / `trading-board` | 전체 pool |

| Lane | 대상 | API | 목적 |
|---|---|---|---|
| HOT | Top5 + S1 + Top15 | `/api/hot_realtime_patch` | 핵심 후보 빠른 갱신 |
| MID | Top30 | `/api/realtime_patch?codes=...` | 넓은 후보군 중간 갱신 |
| POOL | Top300 | `/api/realtime_patch` | 전체 pool 갱신 |

금지:

```text
/api/top100 전체 자동 반복 재조회는 장중 복구하지 않는다.
장중 실시간 갱신은 patch API 중심으로 유지한다.
```

---

## 8. 다른 PC에서 이어가기 절차

### 8.1 repo 동기화

```powershell
cd C:\aiTrade

git status --short

git fetch origin

git checkout hot-priority-integrated-20260630

git pull --ff-only
```

### 8.2 기본 테스트

```powershell
cd C:\aiTrade

python -m pytest -q `
  tests\test_stockboard_offhours_metric_timer_driver_patch.py `
  tests\test_stockboard_collector_sender_resilience_patch.py `
  tests\test_stockboard_offhours_metric_resilience_patch.py `
  tests\test_stockboard_offhours_metric_completion_patch.py `
  tests\test_stockboard_execution_strength_alias_patch.py `
  tests\test_stockboard_qt_main_thread_openapi_patch.py
```

### 8.3 실행

```powershell
cd C:\aiTrade

.\stockboard_v2_large.cmd stop
Start-Sleep -Seconds 5
.\stockboard_v2_large.cmd start-fast
```

브라우저는 `http://127.0.0.1:8765/`를 사용한다.

---

## 9. 다음 장중 검증 명령

### 9.1 API row / 분모 / 전일대금 확인

```powershell
cd C:\aiTrade

$response = Invoke-RestMethod "http://127.0.0.1:8000/api/top100?candidate_model=NET_BUY_STRENGTH_V02"

$flatRows = New-Object System.Collections.ArrayList
function Add-FlatRow($item) {
    if ($null -eq $item) { return }
    if ($item -is [System.Array]) {
        foreach ($sub in $item) { Add-FlatRow $sub }
    } else {
        [void]$flatRows.Add($item)
    }
}
Add-FlatRow $response

$flatRows |
  Select-Object -First 20 stock_code,stock_name,candidate_grade_text,score_percent,score_total_points,score_possible_points,prev_trade_value_eok,program_net |
  Format-Table -Auto
```

### 9.2 1분강도 상태 확인

```powershell
$items = foreach ($row in $flatRows) {
    $one = $row.score_breakdown.net_buy_strength.items |
      Where-Object { $_.key -eq "one_min_strength" } |
      Select-Object -First 1

    [PSCustomObject]@{
        stock_code = $row.stock_code
        stock_name = $row.stock_name
        grade = $row.candidate_grade_text
        score = $row.score_percent
        one_min_points = $one.points
        one_min_possible = $one.possible_points
        one_min_status = $one.status
        one_min_source = $one.source
        one_min_value = $one.value
    }
}

$items |
  Group-Object one_min_status |
  Sort-Object Count -Descending |
  Select-Object Count,Name |
  Format-Table -Auto
```

판정:

| 상태 | 의미 |
|---|---|
| `ok` | 장중 실시간 1분강도 계산 포함, 분모 700 기대 |
| `display_only` | 장마감 5분강도 표시 전용, 분모 600 기대 |
| `fallback` | 정규장인데 실시간 1분값 없음, 중립 50점 |
| `missing` | 의도하지 않은 상태. 원인 조사 필요 |

### 9.3 후보 Top5 breakdown

```powershell
$top5 = $flatRows |
  Where-Object { $_.is_candidate -eq $true -or $_.candidate_rank -ne $null } |
  Sort-Object candidate_rank |
  Select-Object -First 5

foreach ($row in $top5) {
    ""
    "===== $($row.candidate_rank)위 $($row.stock_code) $($row.stock_name) / $($row.candidate_grade_text) / $($row.score_percent)점 / 분모 $($row.score_possible_points) ====="
    $row.score_breakdown.net_buy_strength.items |
      Select-Object label,points,possible_points,status,value,source |
      Format-Table -Auto
}
```

---

## 10. 남은 TODO 핵심

| 우선순위 | TODO | 비고 |
|---:|---|---|
| 1 | 노트북 동일 동작 확인 | `Driver=v3`, `SenderAlive=True`, 빈칸 0 확인 |
| 2 | 정규장 장중 700점 분모 확인 | `one_min_status=ok`, `score_possible_points=700` 확인 |
| 3 | 금융/증권주 쏠림 진단 | 전일 점수/프로그램 비율이 과도한지 확인 |
| 4 | 전일 점수 capped 영향 점검 | 100점 capped가 Top5를 과도하게 지배하는지 확인 |
| 5 | 프로그램 점수 상한/완만화 필요 여부 판단 | 금융주 편향이 반복되면 검토 |
| 6 | UI 툴팁 추가 개선 | 셀에는 숫자만 유지. 설명은 툴팁에만 표시 |
| 7 | 틱데이터 저장/replay 최소 설계 | 장중 재현 가능성 확보 |
| 8 | HTML render/main loop 추가 분리 | 장중 검증 이후. 당분간 보류 |

---

## 11. 금지 루프

| 항목 | 금지 |
|---|---|
| 가격/FID | FID10/FID12 정규화부터 다시 의심하지 말 것 |
| KRX/NXT/통합장 | `_AL` 통합 표시 원천 유지 |
| top100 refresh | 장중 `/api/top100` 자동 반복 호출 복구 금지 |
| UI 계산 | HTML에서 등급/점수/가격을 새로 계산하지 말 것 |
| 1분강도 표시 | 장마감에도 셀 안에는 숫자만 표시. `5분`, `5분참고` 문구를 셀에 직접 넣지 말 것 |
| 휴장 보충 | 시장 틱을 트리거로 사용하지 말 것. 독립 Qt 타이머 유지 |
| pending queue | 휴장시간 지표 보충을 provider pending queue에 다시 의존시키지 말 것 |
| 문서 | 인계 목적 외 새 문서 남발 금지 |

---

## 12. 관련 파일

| 파일 | 역할 |
|---|---|
| `stockboard_ranking_engine.py` | `NET_BUY_STRENGTH_V02` 전용 점수/등급/pool 계산 |
| `stockboard_previous_trade_value.py` | 전일 거래대금 TR/cache/API row 주입 |
| `stockboard_ranking_runtime_patch.py` | 표준 런처 경유 runtime patch 설치 |
| `kiwoom_trade_value_rank.py` | ranking/server 진입점 |
| `realtime_v2/collector32_large_bidask.py` | V2 32-bit OpenAPI collector 진입점과 patch 설치 순서 |
| `realtime_v2/qt_main_thread_openapi_patch.py` | QApplication/QAxWidget 메인 스레드 실행 |
| `realtime_v2/offhours_metric_completion_patch.py` | 5분강도·순간강도·잔량비 휴장시간 통합 보충 |
| `realtime_v2/offhours_metric_resilience_patch.py` | 특정 종목 오류·stale gap 자동 복구 |
| `realtime_v2/offhours_metric_timer_driver_patch.py` | 틱 독립 고정 250ms 심박과 mode별 drain 실행 주기 |
| `realtime_v2/collector_sender_resilience_patch.py` | collector→worker 송신 스레드 fail-open |
| `realtime_v2/execution_strength_alias_patch.py` | `realtime_strength_snapshot` → `execution_strength` 연결 |
| `realtime_v2/session_metric_hold_patch.py` | 다음 실제 프리마켓 전까지 마지막 유효값 보존 |
| `configs/candidate_models/NET_BUY_STRENGTH_V02.json` | 모델 설정/정책 |
| `docs/candidate_model_specs/NET_BUY_STRENGTH_V02.md` | 선발기준 설계 문서 |
| `tests/test_stockboard_ranking_engine.py` | 등급/분모/장마감 display_only 테스트 |
| `tests/test_stockboard_previous_trade_value.py` | 전일 거래대금 계산/cache 테스트 |
| `tests/test_stockboard_offhours_metric_completion_patch.py` | 휴장시간 통합 보충 테스트 |
| `tests/test_stockboard_offhours_metric_timer_driver_patch.py` | 틱 독립 타이머·저부하 주기 테스트 |
| `tests/test_stockboard_collector_sender_resilience_patch.py` | sender 직렬화/루프 복구 테스트 |
| `docs/stockboard_v0_3_0_sample.html` | 구 화면 HTML 참고 |
| `docs/assets/stockboard_tooltip.js` | 툴팁 기본 helper. 셀 텍스트 수정 금지 |

---

## 13. 2026-07-11 휴장시간 지표 완성 및 최적화

### 13.1 목표와 범위

장마감 이후, 주말, 공휴일, 지연개장 전 등 시장 틱이 없는 시간에도 다음 실제 프리마켓 전까지 화면의 빈칸을 자동 보충한다.

| 지표 | 조회 원천 | 처리 |
|---|---|---|
| 5분강도 | `opt10046` | `strength_5m` 저장 |
| 순간강도 | `opt10046` 현재 체결강도 | `execution_strength`와 last-valid 값으로 동기화 |
| 잔량비 | `opt10004` | 매수/매도 잔량과 비율 저장 |

가짜 값은 만들지 않는다. 한 종목이 응답하지 않아도 전체 큐를 막지 않고 다음 종목으로 진행한다.

### 13.2 최종 실행 정책

| 항목 | 정책 |
|---|---|
| 활성 phase | `closed`, `before_market`, `weekend`, `holiday` |
| 중단 시점 | 실제 프리마켓 시작 시 자동 비활성 |
| QAx 실행 | QApplication/QAxWidget와 TR 요청 모두 collector 메인 스레드 |
| 시장 틱 의존 | 없음 |
| provider pending queue | 사용하지 않음 |
| TR 최소 간격 | 종목당 2초 |
| 종목 timeout | 12초 후 해당 종목만 건너뜀 |
| 재시도 | 5분 → 30분 → 2시간 반복 |
| 물리 타이머 | 250ms 고정 심박 |
| 실제 drain 실행 | 조회 중 250ms, provider 대기 1초, complete/비활성 10초 |
| sender | 직렬화 오류 한 건만 폐기하고 스레드 계속 실행 |

### 13.3 실제 검증 결과

2026-07-11 토요일, 실시간 시장 틱 0건 상태에서 검증했다.

| 상태값 | 확인값 | 판정 |
|---|---:|---|
| `driver` | `qt_timer_market_tick_independent_v3_fixed_heartbeat` | 최종 드라이버 적용 |
| `market_tick_independent` | `True` | 틱 없이 실행 |
| `legacy_pump_drain_suppressed` | `True` | 20ms 중복 drain 제거 |
| `mode` | `complete` | 세 지표 빈칸 0 |
| `timer_interval_ms` | `250` | 물리 심박 유지 |
| `effective_drain_interval_ms` | `10000` | 완료 상태 저부하 |
| `TimerTick` | 지속 증가 | Qt 타이머 생존 |
| `DrainRun` | 약 10초마다 증가 | 무거운 검사 제한 |
| `CollectorTs` | 계속 최신화 | collector→worker 상태 전송 정상 |
| `SenderAlive` | `True` | sender 스레드 정상 |
| `SerializeErrors` | `0` | 직렬화 오류 없음 |
| `SenderRecover` | `0` | 복구 개입 없이 안정 |

실제 관찰 예:

```text
15:26:22  DrainRun=9   NextDrain=3.141
15:26:26  DrainRun=10  NextDrain=9.359
15:26:32  DrainRun=10  NextDrain=2.313
15:26:36  DrainRun=11  NextDrain=9.282
```

### 13.4 성능 영향

| 상태 | 키움 TR | 실제 스냅샷/큐 검사 | 판정 |
|---|---:|---:|---|
| 빈칸 보충 중 | 최대 2초당 1건 | 빠른 주기 | 휴장시간 허용 |
| complete | 0건 | 약 10초당 1회 | 부담 미미 |
| 프리마켓·정규장 | 휴장 보충 TR 0건 | 비활성 확인만 | 장초반 영향 없음 |

물리 타이머는 초당 약 4회 호출되지만 대부분 시간 비교 후 즉시 반환한다. 완료 상태에서 키움 TR은 발생하지 않으며, 09:00~09:10 거래량 폭탄 구간과 직접 경쟁하지 않는다.

### 13.5 최종 검증 명령

```powershell
$r = Invoke-RestMethod 'http://127.0.0.1:8765/api/v2/snapshot?limit=300'
$c = $r.status.collector_status
$p = $c.status
$s = $p.strength5m_scheduler
$x = $c.sender_stats

[pscustomobject]@{
    CollectorTs     = $c.ts
    Driver          = $s.driver
    Mode            = $s.mode
    PhysicalTimerMs = $s.timer_interval_ms
    EffectiveMs     = $s.effective_drain_interval_ms
    TimerTick       = $s.timer_tick_count
    DrainRun        = $s.drain_run_count
    DrainSkip       = $s.drain_skip_count
    TickAge         = $s.timer_last_tick_age_sec
    DrainAge        = $s.timer_last_drain_age_sec
    NextDrain       = $s.next_drain_in_sec
    SenderAlive     = $x.sender_thread_alive
    SerializeErrors = $x.serialization_error_count
    SenderRecover   = $x.sender_run_recovery_count
    SenderError     = $x.sender_run_last_error
}
```

정상 기준:

```text
Driver=qt_timer_market_tick_independent_v3_fixed_heartbeat
Mode=complete
PhysicalTimerMs=250
EffectiveMs=10000
TimerTick 계속 증가
DrainRun 약 10초마다 증가
CollectorTs 계속 최신화
SenderAlive=True
SerializeErrors=0
SenderRecover=0
```
