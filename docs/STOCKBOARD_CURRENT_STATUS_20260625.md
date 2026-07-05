# StockBoard Current Status

작성 기준: 2026-07-05 장마감 검증 반영.

> 이 문서는 StockBoard의 현재 운영 상태를 빠르게 파악하기 위한 기준문서다. 새 PC 또는 새 채팅창에서는 이 문서, `docs/candidate_model_specs/NET_BUY_STRENGTH_V02.md`, `configs/candidate_models/NET_BUY_STRENGTH_V02.json`을 우선 확인한다.

---

## 0. 현재 한 줄 요약

StockBoard는 `순매수 강도 v0.2` 전용 ranking engine, 전일 거래대금 cache/API 주입, 장중 700점/장마감 600점 분모 이원화까지 반영됐다. 장마감 검증에서는 1분강도 항목이 `display_only`로 계산 제외되고, `score_possible_points=600` 기준으로 등급이 계산되는 것까지 확인했다. 다음 핵심 과제는 정규장 장중에 실시간 1분강도가 실제 700점 분모로 들어오는지, 그리고 Top5가 금융주/프로그램 비율에 과도하게 쏠리는지 검증하는 것이다.

---

## 1. 기준 정보

| 항목 | 내용 |
|---|---|
| 프로젝트 | aiTrade / StockBoard |
| 작업 경로 | `C:\aiTrade` |
| 브랜치 | `hot-priority-integrated-20260630` |
| 기준일 | 2026-07-05 |
| 현재 선발기준 | `NET_BUY_STRENGTH_V02` / 화면명 `순매수 강도 v0.2` |
| 표준 실행 | `stockboard_live.cmd` → `kiwoom_trade_value_rank.py` 경유 |
| 핵심 테스트 | `tests/test_stockboard_ranking_engine.py`, `tests/test_stockboard_previous_trade_value.py` |
| 다음 핵심 과제 | 정규장 장중 700점 분모/실시간 1분강도 검증 |

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

## 3. 2026-07-05 완료 작업 요약

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

python -m pytest tests/test_stockboard_ranking_engine.py tests/test_stockboard_previous_trade_value.py
```

### 8.3 실행

```powershell
cd C:\aiTrade

.\stockboard_live.cmd stop
.\stockboard_live.cmd
```

브라우저는 캐시 문제 방지를 위해 `Ctrl+F5` 강력 새로고침한다.

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
| 1 | 정규장 장중 700점 분모 확인 | `one_min_status=ok`, `score_possible_points=700` 확인 |
| 2 | 금융/증권주 쏠림 진단 | 전일 점수/프로그램 비율이 과도한지 확인 |
| 3 | 전일 점수 capped 영향 점검 | 100점 capped가 Top5를 과도하게 지배하는지 확인 |
| 4 | 프로그램 점수 상한/완만화 필요 여부 판단 | 금융주 편향이 반복되면 검토 |
| 5 | UI 툴팁 추가 개선 | 셀에는 숫자만 유지. 설명은 툴팁에만 표시 |
| 6 | 틱데이터 저장/replay 최소 설계 | 장중 재현 가능성 확보 |
| 7 | HTML render/main loop 추가 분리 | 장중 검증 이후. 당분간 보류 |

---

## 11. 금지 루프

| 항목 | 금지 |
|---|---|
| 가격/FID | FID10/FID12 정규화부터 다시 의심하지 말 것 |
| KRX/NXT/통합장 | `_AL` 통합 표시 원천 유지 |
| top100 refresh | 장중 `/api/top100` 자동 반복 호출 복구 금지 |
| UI 계산 | HTML에서 등급/점수/가격을 새로 계산하지 말 것 |
| 1분강도 표시 | 장마감에도 셀 안에는 숫자만 표시. `5분`, `5분참고` 문구를 셀에 직접 넣지 말 것 |
| 문서 | 인계 목적 외 새 문서 남발 금지 |

---

## 12. 관련 파일

| 파일 | 역할 |
|---|---|
| `stockboard_ranking_engine.py` | `NET_BUY_STRENGTH_V02` 전용 점수/등급/pool 계산 |
| `stockboard_previous_trade_value.py` | 전일 거래대금 TR/cache/API row 주입 |
| `stockboard_ranking_runtime_patch.py` | 표준 런처 경유 runtime patch 설치 |
| `kiwoom_trade_value_rank.py` | 표준 진입점. patch 설치 후 server import |
| `configs/candidate_models/NET_BUY_STRENGTH_V02.json` | 모델 설정/정책 |
| `docs/candidate_model_specs/NET_BUY_STRENGTH_V02.md` | 선발기준 설계 문서 |
| `tests/test_stockboard_ranking_engine.py` | 등급/분모/장마감 display_only 테스트 |
| `tests/test_stockboard_previous_trade_value.py` | 전일 거래대금 계산/cache 테스트 |
| `docs/stockboard_v0_3_0_sample.html` | 현재 화면 HTML |
| `docs/assets/stockboard_tooltip.js` | 툴팁 기본 helper. 셀 텍스트 수정 금지 |
