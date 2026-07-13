# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-13 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 세부 작업 이력은 Git history와 PR 기록에서 확인한다. 이 문서는 현재 운영 구조, 실제 검증 결과, 미검증 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard의 최소 QAx 실시간 가격 경로, 대량체결 집계, BoardDataHub 공용 FeatureSnapshot, ThemeBoard/StrategyProjection, 개장폭주 background cache가 같은 검증 브랜치에 연결돼 있다. 2026-07-13 애프터마켓 실제 PC 검증에서 잔량비·순간강도·5분강도·프로그램·대량체결 continuity cache 188종목이 생성됐고 재접속 행에 값이 복원됐다. 다만 20:00 이후 `closed`, 주말, 공휴일, 지연개장 전 구간을 각각 시간 경계에서 직접 관찰한 검증은 아직 남아 있다.

ThemeBoard는 현재 10개 수동 테마만 포함하므로 제습기·정유·해운처럼 마스터 밖에서 급등하는 테마를 발견하지 못한다. 또한 누적 거래대금 비중 때문에 하락 중인 대형 반도체 테마가 상위에 고정될 수 있다. 최종 목표를 `돈이 큰 테마` 단일 순위가 아니라 `장개시 후 가장 빠르게 상승하고 상승을 지속하는 테마와 주도주` 탐지로 변경한다.

---

## 1. 운영 구조

```text
32-bit minimal QAx collector
  FID 10 현재가
  FID 12 등락률
  FID 20 체결시각
  FID 15 signed 체결량
  FID 14 누적거래대금 500ms 샘플
        ↓ latest-only + aggregate
64-bit worker Canonical State
        ↓ final continuity read layer
BoardDataHub shared FeatureSnapshot
        ├─ StockBoard
        ├─ ThemeProjection latest-only 1초
        └─ StrategyProjection latest-only
```

운영 원칙:

| 항목 | 원칙 |
|---|---|
| 실시간 owner | 32비트 최소 QAx collector 한 개 |
| Canonical State | 64비트 worker State 한 개 |
| 공통 계산 | 한 번 계산한 완료 FeatureSnapshot을 세 보드가 공유 |
| ThemeBoard·StrategyBoard TR | 금지 |
| HTML 계산 | 금지, 표시·선택·HTS 연동만 허용 |
| 개장폭주 보호 | latest-only queue, background heavy snapshot, UI 50종목 |
| 문서 | 새 문서 남발 금지, 이 기준문서 최소 갱신 |
| Git | PC 실전 검증 전 Draft PR 유지, 병합 금지 |

---

## 2. 2026-07-13 데이터 continuity 실제 검증

### 2.1 상태 API

대표님 PC 애프터마켓 관찰값:

```text
metric_continuity_enabled              : True
metric_continuity_phase                : aftermarket
metric_continuity_reference_date       : 20260713
metric_continuity_valid_until          : 2026-07-14T08:00:00
metric_continuity_cache_count          : 188
metric_continuity_applied_rows         : 81
metric_continuity_applied_fields       : 810
metric_continuity_scoring_blocked_rows : 154
```

판정:

| 항목 | 판정 |
|---|---|
| continuity 설치 | 정상 |
| 기준 거래일 | 2026-07-13 정상 |
| 다음 실제 프리마켓 | 2026-07-14 08:00 정상 |
| cache population | 188종목 정상 |
| 재접속 복원 | 81행·810필드 실제 적용 확인 |
| 전일값 점수 혼입 차단 | 154행에서 차단 동작 확인 |

`metric_continuity_scoring_blocked_rows=154`는 빈칸 오류가 아니다. 화면에는 마지막 유효값을 유지하되 날짜가 이전 세션인 그룹은 후보·테마 점수에서 제외한다는 뜻이다. 다만 이 수가 높으므로 다음 정규장에서 당일 잔량비·강도·프로그램 원천이 얼마나 빠르게 current-session으로 교체되는지 별도 확인한다.

### 2.2 실제 필드 표시

확인된 대상:

```text
bid_ask_ratio
execution_strength
strength_5m
program_net
large_trade_net_count
large_trade_net_sum_eok
metric_continuity_basis
```

S-Oil, OCI홀딩스, HLB, 한화오션, 삼성SDI, LG에너지솔루션, HMM, 에코프로 등 상위 행에서 잔량비·순간강도·5분강도·프로그램 값이 표시됐다. 대량체결이 없는 종목의 `0 / 0.0`은 누락이 아니라 정상 0이다.

### 2.3 ThemeProjection continuity

```text
enabled              : True
phase                : aftermarket
reference_date       : 20260713
valid_until          : 2026-07-14T08:00:00
cache_count          : 188
applied_rows         : 81
scoring_blocked_rows : 154

tracked_code_count   : 188
held_code_count      : 0
one_min_ready_count  : 188
five_min_ready_count : 188
market_phase         : aftermarket
hold_active          : False
```

`aftermarket`은 아직 거래가 진행되는 세션이므로 `hold_active=False`가 정상이다. 20:00 이후 `closed`에서 `hold_active=True`로 전환되고 마지막 1분·5분 유입값을 유지하는지 시간 경계 직접 검증이 남아 있다.

### 2.4 runtime 파일

```text
data/runtime/stockboard_v2/board_metric_continuity.json
  Length: 378493
  LastWriteTime: 2026-07-13 19:29:18

data/runtime/stockboard_v2/theme_flow_history_hold.json
  Length: 17703
  LastWriteTime: 2026-07-13 19:29:20
```

판정: 원자적 snapshot 파일 생성과 갱신이 실제 확인됐다.

### 2.5 현재 검증 수준

| 조건 | 상태 |
|---|---|
| 애프터마켓 중 값 저장 | 실제 확인 |
| 애프터마켓 재접속 후 복원 | 실제 확인 |
| StockBoard 공용 행 복원 | 실제 확인 |
| ThemeBoard 공용 FeatureSnapshot 전달 | 실제 확인 |
| StrategyProjection 필드 전달 | 코드·정적 구조 반영, UI 미구현 |
| 20:00 `closed` 전환 | 직접 시간경계 검증 필요 |
| 프로세스 완전 종료 후 재시작 | 추가 확인 필요 |
| 토·일요일 | 기존 휴장 보충은 확인됐으나 새 continuity 통합경로 재확인 필요 |
| 공휴일·임시휴장 | 캘린더 구조 반영, 실제 날짜 검증 필요 |
| 지연개장 | `special_days/open_delay_minutes` 구조 반영, 실제 날짜 검증 필요 |

따라서 **데이터 보존 1차 운영 검증은 성공**으로 판정한다. 모든 달력 조건의 최종 완료 판정은 각 경계 검증 후 내린다.

---

## 3. 세션별 보존·초기화 정책

| 구간 | snapshot형 지표: 잔량비·순간강도·5분강도 | 누적형 지표: 프로그램·대량체결 |
|---|---|---|
| 정규장·애프터마켓 | current-session 값 우선 | 당일 누적값 |
| 장마감 후 | 마지막 유효값 유지 | 마지막 당일 누적값 유지 |
| 주말·공휴일·지연개장 전 | 직전 거래일 값 유지 | 직전 거래일 값 유지 |
| 실제 새 프리마켓 시작 | 전일값 표시 가능, 점수 제외 | 0/new-session-wait 후 당일값으로 교체 |
| 새 당일 원천 도착 | current-session으로 교체, 점수 허용 | 당일 누적 재개 |

시장 캘린더 원천:

```text
config/stockboard_market_calendar.json
```

기본 시간:

```text
프리마켓 08:00
장전 동시호가 08:30
정규장 09:00
장마감 동시호가 15:20
정규장 종료 15:30
애프터마켓 15:40~20:00
```

주말은 자동 휴장이다. 공휴일은 `holidays`, 임시휴장·수능 지연개장 등은 `special_days`의 `closed`, `open_delay_minutes`, 명시적 `windows`로 처리한다.

---

## 4. ThemeBoard 현재 결함

### 4.1 테마 universe

현재 정식 마스터는 다음 10개뿐이다.

```text
HBM·반도체 장비
종합반도체
전력기기·전선
로봇·자동화
조선·기자재
방산·우주
원전·SMR
바이오·신약
2차전지 소재
자동차·부품
```

따라서 제습기·정유·해운 등 마스터에 없는 테마는 아무리 급등해도 순위에 나타나지 않는다. 10개는 최종 universe가 아니라 fallback 샘플로 격하해야 한다.

### 4.2 기존 순위 왜곡

기존 점수는 1분·5분·누적 거래대금의 상대순위 비중이 높다. 삼성전자·SK하이닉스처럼 거래대금 절대규모가 큰 종목이 포함된 반도체 테마는 구성종목 평균 등락률이 음수여도 상위에 남을 수 있다.

이 결과는 대표님의 목적과 다르다.

```text
기존: 돈의 절대규모가 큰 테마
목표: 장개시 후 가장 급등하고, 여러 종목으로 확산되며, 상승을 지속하고, 실제 돈이 확인되는 테마
```

---

## 5. ThemeBoard 새 기준 확정안

### 5.1 화면을 두 순위로 분리

| 순위 | 역할 |
|---|---|
| 상승탄력 순위 | 기본·주순위. 어느 테마가 지금 가장 강하게 오르는지 탐지 |
| 돈쏠림 순위 | 보조순위. 실제 거래대금과 수급이 어디에 몰리는지 확인 |

ThemeBoard 첫 화면과 레이더는 `상승탄력 순위`를 기본으로 한다. 거래대금은 순위의 주인이 아니라 상승의 신뢰도를 확인하는 보조 원천으로 사용한다.

### 5.2 상승탄력 주순위

초기 확정안:

| 구성 | 비중 | 의미 |
|---|---:|---|
| 구성종목 단순 평균 등락률 상대순위 | 40 | 키움 테마순위와 같은 핵심 방향 |
| 상승 종목 확산도 | 20 | 한 종목만 상승하는 가짜 테마 억제 |
| 평균 등락률 최근 1분 변화 | 15 | 지금 가속하는 테마 탐지 |
| 평균 등락률 최근 5분 지속성 | 10 | 급등 후 즉시 반납하는 테마 억제 |
| 최근 1분 거래대금 유입 상대순위 | 10 | 실제 돈 유입 확인 |
| 프로그램·대량체결 확인 | 5 | 수급 확인 |

운영 규칙:

```text
평균 등락률 음수 테마는 돈이 아무리 커도 상승탄력 상위 고정 금지
Coverage 60% 미만은 WAIT_DATA 또는 점수 상한 적용
한 종목 급등만으로 전체 테마 1위가 되지 않도록 breadth 적용
최근 1분 상승 후 5분 방향도 유지될 때 SURGE 판정
누적 거래대금 절대규모는 돈쏠림 보조순위에 유지
```

### 5.3 주도주 선발

테마 내부 종목 정렬 우선순위:

```text
현재 등락률
+ 최근 1분·5분 상승 지속성
+ 최근 거래대금 유입
+ 순간강도·5분강도
+ 프로그램·대량체결
```

역할:

| 역할 | 기준 |
|---|---|
| 주도 | 테마 상승과 거래를 동시에 이끄는 1위 |
| 동반 | 주도주와 같은 방향으로 강하게 확산 |
| 후발 | 상승 중이지만 속도·수급이 한 단계 낮음 |
| 관찰 | 테마 소속이나 상승 확인 부족 |

### 5.4 전체 테마 마스터 정책

최종 정책:

```text
Kiwoom 또는 검증된 광범위 테마 catalog를 매 거래일 갱신
runtime 동적 master를 1순위
수동 10개 config는 장애 시 fallback만 사용
ThemeBoard 자체 OpenAPI/TR 호출은 계속 금지
테마 catalog 갱신은 별도 저빈도 context/bootstrap 단계에서 수행
```

현재 loader가 지원하는 runtime 후보 경로를 실제 주원천으로 연결하고, 정적 10개 파일의 우선순위를 낮춘다. 정확한 Kiwoom 전체 테마 구성종목 수집 방식은 최소 QAx collector 안정성을 훼손하지 않는 별도 단계로 구현한다.

---

## 6. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 20:00 `closed` continuity 확인 | `hold_active=True`, 값·파일 유지 |
| 2 | 완전 종료 후 재시작 확인 | 5개 지표 빈칸 0, 기준일·basis 정상 |
| 3 | 전체 테마 catalog 연결 | 제습기·정유·해운 등 마스터 밖 테마 표시 |
| 4 | 상승탄력 주순위 구현 | 평균등락률 중심 정렬, 반도체 하락일 상위 고정 제거 |
| 5 | 테마 평균등락률 1분·5분 history | 급등 지속·반납 구분 |
| 6 | 주도주 정렬 변경 | 등락률·지속성·유입 순 |
| 7 | 09:00~09:10 실전 부하 검증 | collector/worker queue 누적 없음 |
| 8 | StrategyBoard UI | 같은 continuity/FeatureSnapshot 표시 |

---

## 7. 핵심 검증 명령

```powershell
cd C:\aiTrade

$r = Invoke-RestMethod `
  "http://127.0.0.1:8765/api/v2/snapshot?limit=50&ts=$([DateTimeOffset]::Now.ToUnixTimeMilliseconds())"

$r.status |
Select-Object `
  metric_continuity_enabled,
  metric_continuity_phase,
  metric_continuity_reference_date,
  metric_continuity_valid_until,
  metric_continuity_cache_count,
  metric_continuity_applied_rows,
  metric_continuity_applied_fields,
  metric_continuity_scoring_blocked_rows |
Format-List

$r.rows |
Select-Object -First 20 `
  stock_code,stock_name,bid_ask_ratio,execution_strength,strength_5m,
  program_net,large_trade_net_count,large_trade_net_sum_eok,
  metric_continuity_basis |
Format-Table -Auto

$t = Invoke-RestMethod `
  "http://127.0.0.1:8765/api/v2/hub/theme?ts=$([DateTimeOffset]::Now.ToUnixTimeMilliseconds())"

$t.metric_continuity_status | Format-List
$t.flow_history_status | Format-List
```

20:00 이후 기대값:

```text
metric_continuity_phase : closed
flow_history_status.hold_active : True
valid_until : 다음 실제 거래일 프리마켓
잔량비·순간강도·5분강도·프로그램·대량체결 빈칸 없음
```

---

## 8. 관련 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 최소 32비트 QAx collector |
| `realtime_v2/collector_large_trade_patch.py` | FID15 대량체결 aggregate |
| `realtime_v2/board_metric_continuity_patch.py` | 세 보드 공통 metric continuity |
| `realtime_v2/theme_projection_flow_history_patch.py` | 테마 1분·5분 유입 history·hold |
| `realtime_v2/theme_projection_continuity_guard_patch.py` | 전일 보존값 표시, 점수 제외 |
| `realtime_v2/board_data_hub.py` | 공용 Canonical/Feature/Projection read model |
| `realtime_v2/theme_projection_engine.py` | ThemeProjection 계산 |
| `realtime_v2/strategy_projection_engine.py` | StrategyProjection 분류 |
| `config/stockboard_market_calendar.json` | 휴장·지연개장 포함 시장 캘린더 |
| `config/stockboard_theme_master.json` | 현재 10개 fallback 테마 마스터 |
| `docs/themeboard.html` | 계산 없는 ThemeBoard 표시 UI |

---

## 9. 미검증·금지사항

```text
PC 실전 검증 전 PR 병합 금지
ThemeBoard 또는 HTML에서 직접 TR 호출 금지
HTML에서 점수·정렬·Coverage 계산 금지
최소 collector에 무거운 테마 전체조회·보조 TR을 즉시 추가하지 말 것
10개 fallback만 늘려서 전체 테마 문제를 해결했다고 주장하지 말 것
애프터마켓 검증만으로 주말·공휴일·지연개장까지 완료라고 주장하지 말 것
```
