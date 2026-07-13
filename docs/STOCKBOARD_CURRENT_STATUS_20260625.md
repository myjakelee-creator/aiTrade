# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-13 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 세부 작업 이력은 Git history와 PR 기록에서 확인한다. 이 문서는 현재 운영 구조, 실제 검증 결과, 미검증 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard의 최소 32비트 QAx 실시간 가격 경로, FID15 대량체결 집계, 64비트 Canonical State, continuity 최종 read layer, BoardDataHub 공용 FeatureSnapshot, ThemeBoard·StrategyProjection이 같은 검증 브랜치에 연결돼 있다.

ThemeBoard는 기존 수동 10개 고정 구조에서 벗어나 키움 전체 테마 catalog `142개 / membership 907개`를 runtime 원천으로 사용한다. 현재 내부 188종목과 교집합이 있는 유효 테마는 68개다. 전체 테마 Summary와 선택 테마 Detail을 분리했고, 전체 테마 구성종목 상세 생성은 0건이며 선택한 한 테마만 latest-only worker에서 상세를 만든다.

기본 순위는 `상승탄력`, 보조 순위는 `돈쏠림`이다. 평균등락률이 0 이하인 테마는 거래대금이 크더라도 상승탄력 점수가 F59를 넘지 못한다. 테마 주도주는 StockBoard 후보점수와 분리해 등락률·1분/5분 상승지속성·대금비·최근대금·강도·프로그램·대량체결로 선발한다.

대표님 PC 검증 결과:

```text
Theme 회귀 핵심: 7 passed in 0.32s
Theme 4단계 전체: 23 passed in 0.89s
```

PR은 계속 Draft·미병합으로 유지한다. 최종 병합 전에는 09:00~09:10 장개시 실전 부하, 주도주 변화, queue 누적 여부를 확인해야 한다.

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
        ├─ Theme Summary latest-only 1초
        │    └─ 전체 유효 테마 숫자 요약 + 서버 완성 이중 순위
        ├─ Theme Detail latest-only depth 1
        │    └─ 선택 테마 한 개의 구성종목 상세
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
| Theme Summary | 1초 latest-only, 전체 구성종목 상세 생성 금지 |
| Theme Detail | 선택 테마 1개만 latest-only depth 1 |
| 개장폭주 보호 | latest-only queue, background heavy snapshot, UI 50종목 |
| 문서 | 새 문서 남발 금지, 이 기준문서 최소 갱신 |
| Git | PC 실전 검증 전 Draft PR 유지, 병합 금지 |

---

## 2. 데이터 continuity 검증 상태

### 2.1 애프터마켓 실제 확인

```text
metric_continuity_enabled              : True
metric_continuity_reference_date       : 20260713
metric_continuity_valid_until          : 2026-07-14T08:00:00
metric_continuity_cache_count          : 188
metric_continuity_applied_rows         : 81
metric_continuity_applied_fields       : 810
metric_continuity_scoring_blocked_rows : 154
```

확인된 표시·복원 필드:

```text
bid_ask_ratio
execution_strength
strength_5m
program_net
large_trade_net_count
large_trade_net_sum_eok
metric_continuity_basis
```

판정:

| 항목 | 상태 |
|---|---|
| 애프터마켓 저장 | 실제 확인 |
| 재접속 후 복원 | 실제 확인 |
| cache 188종목 | 실제 확인 |
| 전일값 표시·점수 제외 | 실제 확인 |
| `closed` 상태 | 22시대 실제 확인 |
| `hold_active=True` | 실제 확인 |
| 정확한 20:00 전환 순간 | 직접 시간경계 관찰은 남음 |
| 주말·공휴일·지연개장 | 구조 반영, 실제 날짜 재검증 필요 |

`metric_continuity_scoring_blocked_rows`는 빈칸 오류가 아니다. 화면에는 직전 유효값을 유지하되 이전 세션 지표는 테마·주도주 점수에서 제외한다.

### 2.2 runtime 파일

```text
data/runtime/stockboard_v2/board_metric_continuity.json
data/runtime/stockboard_v2/theme_flow_history_hold.json
data/runtime/stockboard_v2/theme_momentum_hold.json
```

운영 파일은 원자적으로 갱신한다. 단위 테스트는 실제 runtime hold 파일과 현재 장상태를 읽지 않도록 임시 경로·가상 세션으로 격리했다.

---

## 3. 세션별 보존·초기화 정책

| 구간 | snapshot형 지표: 잔량비·순간강도·5분강도 | 누적형 지표: 프로그램·대량체결 | Theme 1분·5분 history |
|---|---|---|---|
| 정규장·애프터마켓 | current-session 값 우선 | 당일 누적값 | 당일 history 갱신 |
| 장마감 후 | 마지막 유효값 유지 | 마지막 당일 누적값 유지 | 마지막 완료값 유지 |
| 주말·공휴일·지연개장 전 | 직전 거래일 값 유지 | 직전 거래일 값 유지 | 직전 거래일 값 유지 |
| 실제 새 프리마켓 시작 | 전일값 표시 가능, 점수 제외 | 0/new-session-wait | 0 또는 warmup |
| 새 당일 원천 도착 | current-session으로 교체 | 당일 누적 재개 | 60초·300초 뒤 완성 |

시장 캘린더 원천:

```text
config/stockboard_market_calendar.json
```

주말은 자동 휴장이다. 공휴일·임시휴장·지연개장은 `holidays`, `special_days`, `closed`, `open_delay_minutes`, 명시적 `windows`로 처리한다. 공식 휴장일 목록은 계속 유지보수가 필요하다.

---

## 4. 전체 테마 catalog

### 4.1 현재 원천

StockBoard를 정지한 상태에서 독립 32비트 갱신기를 한 번 실행한다.

```text
refresh_theme_catalog.cmd
  → Kiwoom GetThemeGroupList
  → 각 테마 GetThemeGroupCode
  → data/runtime/stockboard_v2/theme_membership.json
```

실제 결과:

```text
catalog theme_count : 142
membership_count    : 907
현재 유효 Theme     : 68
master_version      : KIWOOM_THEME_20260713_200535
```

`theme_count=68`은 오류가 아니다. 키움 전체 142개 중 현재 내부 188종목과 교집합이 있는 테마만 실시간 계산한다.

### 4.2 원천 우선순위

```text
1. data/runtime/stockboard_v2/theme_membership.json
2. config/stockboard_theme_master.json
```

정적 10개 테마는 장애 시 fallback이다. 장중 반복 catalog 조회, ThemeBoard 직접 TR, collector 실시간 등록 확대는 금지한다.

---

## 5. Theme Summary / Detail 분리

### 5.1 Summary

전체 68개 유효 테마에 대해 다음 숫자만 계산한다.

```text
평균·중앙 등락률
상승 확산도
1분 상승탄력
5분 상승지속성
테마 대금비 중앙값
1분·5분·누적 거래대금
프로그램·대량체결 확인
주도주 TOP3 최소 정보
```

보호 정책:

```text
source                       : theme_summary_projection
summary_only                 : True
full_theme_detail_rows_built : 0
rows with members            : 0
HTML 정렬·점수 계산          : 0
```

### 5.2 선택 Detail

사용자가 선택한 한 테마만 별도 latest-only worker가 구성종목 상세를 만든다.

실제 확인:

```text
status                : READY
detail calculate_ms   : 1.086ms
queue_policy           : latest_only_depth_1
error_count            : 0
```

HTTP 요청은 계산을 직접 실행하지 않고 선택값만 queue에 넣는다. 준비 중이면 `202 BUILDING`, 완료 후 cache를 반환한다.

### 5.3 성능 실측

장마감 후 30회 측정:

| 구간 | 평균 | 최소 | 최대 |
|---|---:|---:|---:|
| total_ms | 25.416 | 16.987 | 58.001 |
| aggregate_ms | 12.718 | 10.283 | 22.242 |
| score_sort_ms | 1.902 | 1.320 | 3.512 |
| momentum_ms | 0.214 | 0.165 | 0.482 |
| other_ms | 10.582 | 4.665 | 42.932 |

판정: 전체 테마 상세 생성 제거는 성공했다. 1초 latest-only 독립 worker 기준으로 다음 단계 진행은 허용했지만, 09:00 장개시에는 queue·stale discard·StockBoard 갱신 지연을 다시 측정한다.

---

## 6. 상승탄력 / 돈쏠림 이중 순위

### 6.1 상승탄력 기본 순위

| 구성 | 비중 |
|---|---:|
| 테마 평균 등락률 상대순위 | 35 |
| 상승 확산도 | 20 |
| 최근 1분 상승탄력 | 15 |
| 최근 5분 상승지속성 | 15 |
| 테마 대금비 상대순위 | 10 |
| 프로그램·대량체결 확인 | 5 |

규칙:

```text
평균 등락률 <= 0 → trend_score 최고 59, grade F
상승 확산도 < 50% → 최고점 제한
Coverage 부족 → 단계별 점수 상한
동일한 원천값 → 동일 percentile 점수
평균 후보점수 → Theme 순위에 사용하지 않음
대금비 → 각 view에 한 번만 적용
```

### 6.2 돈쏠림 보조 순위

| 구성 | 비중 |
|---|---:|
| 테마 대금비 상대순위 | 40 |
| 최근 1분 거래대금 | 25 |
| 최근 5분 거래대금 | 20 |
| 프로그램·대량체결 확인 | 10 |
| Coverage | 5 |

서버 출력:

```text
rows       = 상승탄력 서버 완성 순서
money_rows = 돈쏠림 서버 완성 순서
```

브라우저는 두 서버 배열 중 하나를 선택해 표시할 뿐 `.sort()`나 점수 계산을 하지 않는다.

---

## 7. 테마 주도주 선발

StockBoard 후보점수를 주도주 선발에서 제외하고 같은 테마 구성종목 사이의 상대순위로 계산한다.

| 구성 | 비중 |
|---|---:|
| 현재 등락률 | 30 |
| 종목 1분 상승탄력 | 15 |
| 종목 5분 상승지속성 | 10 |
| 종목 대금비 | 20 |
| 최근 1분 거래대금 | 9 |
| 최근 5분 거래대금 | 6 |
| 순간강도 | 3 |
| 5분강도 | 2 |
| 프로그램 | 2.5 |
| 대량체결 | 2.5 |

운영 규칙:

```text
등락률 <= 0 → 주도점수 최고 59
결측 항목은 제외하고 남은 가중치로 동적 재가중
전일 보존 강도·프로그램·대량체결은 표시 가능, 점수 제외
주도 = 상승 중 1위
동반 = 상승 중이며 주도점수 65 이상
후발 = 상승 중이나 점수 부족
관찰 = 보합·하락
```

Summary TOP3와 선택 Detail 구성종목은 같은 서버 주도점수·역할·순서를 사용한다. 선택 Detail의 기존 후보점수는 `stockboard_candidate_score`로 보존한다.

현재 구현은 Summary 이후 theme membership을 숫자만 한 번 추가 순회한다.

```text
leader_selection_status.summary_extra_member_passes = 1
performance_breakdown.leader_rank_ms
```

장개시 실측에서 `leader_rank_ms`가 과하면 다음 최적화는 기존 Summary aggregation 1회에 주도주 scalar 수집을 통합하는 것이다.

---

## 8. PC 테스트 검증

대표님 PC 실제 결과:

```text
7 passed in 0.32s
23 passed in 0.89s
```

검증 범위:

```text
Theme dual rank
Theme flow history
Theme leader selection
Theme Summary / selected Detail split
Theme continuity
Theme momentum
ThemeBoard 표시 계약
```

수정된 회귀:

| 문제 | 수정 |
|---|---|
| 동일 지표 테마가 theme_id tie-break로 다른 점수를 받음 | equal-values equal-percentile 적용 |
| 최소 FakeBuilder에 continuity guard가 강제 설치됨 | 필수 메서드가 없는 Builder는 fail-open |
| 단위 테스트가 실제 장마감 hold 파일을 읽음 | 임시 runtime 경로·가상 세션으로 테스트 격리 |

---

## 9. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 09:00~09:10 장개시 실전 부하 | collector/worker queue 지속 증가 없음 |
| 2 | 상승탄력 history 완성 | 60초·300초 후 값 정상, 급등·반납 구분 |
| 3 | 주도주 실전 대조 | 테마 내 실제 강한 종목이 TOP3에 지속 반영 |
| 4 | leader rank 성능 측정 | 평균 5ms 안팎, 전체 Summary 평균 30ms 이하 |
| 5 | exact 20:00 전환 확인 | closed·hold_active 전환 순간 확인 |
| 6 | 주말·공휴일·지연개장 | 실제 날짜별 continuity 검증 |
| 7 | StrategyBoard UI | 같은 continuity/FeatureSnapshot 표시 |
| 8 | Draft PR 최종 정리 | 장중 검증 통과 후 병합 여부 판단 |

---

## 10. 핵심 검증 명령

```powershell
cd C:\aiTrade

$t = Invoke-RestMethod `
  "http://127.0.0.1:8765/api/v2/hub/theme?ts=$([DateTimeOffset]::Now.ToUnixTimeMilliseconds())"

$t.performance_breakdown | Format-List
$t.leader_selection_status | Format-List
$t.ranking_views | ConvertTo-Json -Depth 5
$t.ranking_policy | ConvertTo-Json -Depth 5

$t.rows |
Select-Object -First 20 `
  trend_display_rank,money_display_rank,theme_name,avg_change_rate,
  breadth_pct,change_momentum_1m,change_persistence_5m,
  theme_amount_ratio,@{N="leader";E={$_.leaders[0].stock_name}},
  @{N="leader_score";E={$_.leaders[0].leadership_score}} |
Format-Table -Auto
```

---

## 11. 관련 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 최소 32비트 QAx collector |
| `realtime_v2/collector_large_trade_patch.py` | FID15 대량체결 aggregate |
| `realtime_v2/board_metric_continuity_patch.py` | 세 보드 공통 metric continuity |
| `realtime_v2/board_data_hub.py` | 공용 Canonical/Feature/Projection read model |
| `realtime_v2/theme_catalog_refresh32.py` | 장외 1회 전체 테마 catalog 생성 |
| `realtime_v2/theme_projection_summary_split_patch.py` | 전체 Summary와 선택 Detail 분리 |
| `realtime_v2/theme_projection_flow_history_patch.py` | 종목 1분·5분 거래대금 history·hold |
| `realtime_v2/theme_projection_summary_momentum_patch.py` | 테마 평균등락률 1분·5분 history |
| `realtime_v2/theme_projection_continuity_guard_patch.py` | 보존값 표시·이전 세션 점수 제외 |
| `realtime_v2/theme_projection_dual_rank_patch.py` | 상승탄력·돈쏠림 서버 이중 순위 |
| `realtime_v2/theme_leader_selection_patch.py` | 테마 내부 주도주 서버 선발 |
| `realtime_v2/theme_selected_detail_runtime.py` | 선택 Detail latest-only worker |
| `realtime_v2/worker_theme_selected_detail_patch.py` | Detail API·UI cache 연결 |
| `realtime_v2/worker_theme_dual_rank_ui_patch.py` | 서버 순위 view 전환 표시 |
| `config/stockboard_market_calendar.json` | 휴장·지연개장 포함 시장 캘린더 |
| `config/stockboard_theme_master.json` | 장애 시 10개 fallback |
| `data/runtime/stockboard_v2/theme_membership.json` | 키움 전체 runtime 테마 마스터 |
| `docs/themeboard.html` | 계산 없는 ThemeBoard 표시 UI |

---

## 12. 미검증·금지사항

```text
장중 실전 검증 전 PR 병합 금지
ThemeBoard 또는 HTML에서 직접 TR 호출 금지
HTML에서 점수·정렬·Coverage 계산 금지
최소 collector에 테마 전체조회·보조 TR 추가 금지
907종목 전체를 실시간 등록해 테마 누락을 해결하려 하지 말 것
장마감 검증만으로 09:00 성능까지 완료라고 주장하지 말 것
closed 확인만으로 정확한 20:00 경계·주말·공휴일·지연개장 완료라고 주장하지 말 것
단위 테스트가 실제 runtime hold 파일을 읽도록 두지 말 것
```