# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-14 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 세부 작업 이력은 Git history와 PR 기록에서 확인한다. 이 문서는 현재 운영 구조, 실제 검증 결과, 미검증 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard의 최소 32비트 QAx 실시간 가격 경로, FID15 대량체결 집계, 64비트 Canonical State, continuity 최종 read layer, BoardDataHub 공용 FeatureSnapshot, ThemeBoard·StrategyProjection이 같은 검증 브랜치에 연결돼 있다.

ThemeBoard는 키움 전체 테마 catalog `142개 / membership 약 907개`를 runtime 원천으로 사용한다. 현재 PC1 실측은 내부 universe `172종목`, 유효 테마 `66개`, master version `KIWOOM_THEME_20260714_085949`다. 유효 테마 수는 내부 universe와 교집합에 따라 66~68개 수준으로 변할 수 있다.

Theme Summary와 선택 Theme Detail은 분리돼 있다. 전체 테마 구성종목 상세 생성은 0건이며, 선택한 한 테마만 latest-only worker에서 정밀 상세를 만든다. Summary 정밀 주도주 계산은 상승탄력 상위 10개와 돈쏠림 상위 10개 합집합만 수행하고, 선택 테마는 순위권 밖이어도 Detail에서 항상 정밀 계산한다.

ThemeBoard UI는 대표님 PC에서 다음 기능이 실제 동작하는 것을 확인했다.

```text
평균등락률 → 상승탄력 → 돈쏠림 보기
상위 카드 20개
카드 재클릭 상세 닫기/다시 열기
선택 카드 행 바로 아래 구성종목 상세
1분쏠림·5분쏠림 상대평가 막대
전체 순위표 하단 가로 스크롤바
헤더 클릭 오름차순/내림차순 서버 정렬
상단 요약 한 줄 + 현재 1위 중복 제거
열 최소화
헤더 경계 드래그 수동 열 폭 조절
헤더 경계 더블클릭 자동 맞춤
열 폭 브라우저 저장·복원
```

PR은 계속 Draft·미병합으로 유지한다. 최종 병합 전에는 09:00~09:10 장개시 실전 부하, queue 누적, 가격 갱신 지연, 주도주 변화와 대량체결 정합성을 확인해야 한다.

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
        │    ├─ 전체 유효 테마 숫자 요약
        │    ├─ 평균등락률·상승탄력·돈쏠림 서버 완성 배열
        │    └─ 상승탄력/돈쏠림 상위 합집합만 정밀 주도주
        ├─ Theme Detail latest-only depth 1
        │    └─ 선택 테마 한 개의 정밀 구성종목 상세
        └─ StrategyProjection latest-only
```

| 항목 | 원칙 |
|---|---|
| 실시간 owner | 32비트 최소 QAx collector 한 개 |
| Canonical State | 64비트 worker State 한 개 |
| 공통 계산 | 한 번 계산한 완료 FeatureSnapshot을 세 보드가 공유 |
| ThemeBoard·StrategyBoard TR | 금지 |
| HTML 시장계산 | 금지 |
| 브라우저 역할 | 서버 완성 배열·순서·막대 표시, 선택, HTS 연동, 열 폭 조작 |
| Theme Summary | 1초 latest-only, 전체 구성종목 상세 생성 금지 |
| Theme Detail | 선택 테마 1개만 latest-only depth 1 |
| 개장폭주 보호 | latest-only queue, background heavy snapshot, StockBoard UI 50종목 |
| 문서 | 새 문서 남발 금지, 이 기준문서만 최소 갱신 |
| Git | PC 장중 검증 전 Draft PR 유지, 병합 금지 |

---

## 2. 실시간·continuity 상태

### 2.1 collector

```text
collector_limit          : 100
trade_value_sample_ms    : 500
orderbook_realtime       : False
FID 10/12/20/15          : callback 수신
FID 14                   : 500ms 샘플
large trade threshold    : 50,000,000원/체결
```

최소 collector에는 테마 조회, 보조 TR, 호가 scheduler, 별도 provider stack을 추가하지 않는다.

### 2.2 continuity

확인된 보존 필드:

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

시장 캘린더 원천:

```text
config/stockboard_market_calendar.json
```

주말은 자동 휴장이다. 공휴일·임시휴장·지연개장은 `holidays`, `special_days`, `closed`, `open_delay_minutes`, 명시적 `windows`로 처리한다. 캘린더 JSON은 경로·수정시각·크기가 바뀔 때만 다시 파싱한다.

---

## 3. 전체 테마 catalog

PC별 runtime 원천:

```text
refresh_theme_catalog.cmd
  → Kiwoom GetThemeGroupList
  → 각 테마 GetThemeGroupCode
  → data/runtime/stockboard_v2/theme_membership.json
```

원천 우선순위:

```text
1. data/runtime/stockboard_v2/theme_membership.json
2. config/stockboard_theme_master.json
```

`data/runtime/`은 Git 제외 대상이다. 다른 PC에서 `git pull`만 하면 전체 테마 catalog가 복사되지 않으므로, PC별로 `refresh_theme_catalog.cmd`를 실행해야 한다. runtime catalog가 없으면 정적 10개 fallback으로 실행된다.

장중 반복 catalog 조회, ThemeBoard 직접 TR, collector 실시간 등록 확대는 금지한다.

---

## 4. Theme Summary / Detail / 주도주

### 4.1 Summary 숫자

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
HTML 점수 계산               : 0
HTML 시장 정렬 계산          : 0
```

### 4.2 선택 Detail

사용자가 선택한 한 테마만 별도 latest-only worker가 구성종목 상세를 만든다.

최근 확인:

```text
DetailStatus      : READY
DetailPrecision   : selected_detail_precise
DetailPrecise     : True
DetailCalculateMs : 약 1~2ms
```

HTTP 요청은 계산을 직접 실행하지 않고 선택값만 queue에 넣는다. 준비 중이면 `202 BUILDING`, 완료 후 cache를 반환한다.

### 4.3 정밀 주도주 범위

```text
rank_method                    : top_union_two_pass_minmax
precise_theme_count            : 최근 17
fallback_theme_count           : 최근 51
summary_member_scope           : top_momentum_money_union_only
selected_detail_always_precise : True
candidate_score_used           : False
```

주도주 가중치:

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

양수 등락 종목을 우선하고, 보합·하락 종목의 주도점수는 최고 39로 제한한다. 결측 항목은 제외하고 남은 가중치로 동적 재가중한다.

---

## 5. ThemeBoard 세 가지 보기

### 5.1 평균등락률

```text
average_rows = avg_change_rate 내림차순 서버 완성 배열
다른 지표 사용 = False
브라우저 계산 = False
```

단추 순서는 다음으로 고정한다.

```text
평균등락률 → 상승탄력 → 돈쏠림
```

### 5.2 상승탄력

| 구성 | 비중 |
|---|---:|
| 테마 평균 등락률 상대순위 | 35 |
| 상승 확산도 | 20 |
| 최근 1분 상승탄력 | 15 |
| 최근 5분 상승지속성 | 15 |
| 테마 대금비 상대순위 | 10 |
| 프로그램·대량체결 확인 | 5 |

평균 등락률이 0 이하이면 최고 F59, 상승 확산도 50% 미만이면 최고점이 제한된다.

### 5.3 돈쏠림

| 구성 | 비중 |
|---|---:|
| 테마 대금비 상대순위 | 40 |
| 최근 1분 거래대금 | 25 |
| 최근 5분 거래대금 | 20 |
| 프로그램·대량체결 확인 | 10 |
| Coverage | 5 |

---

## 6. 카드 자금쏠림 막대

절대 거래대금 막대는 반도체 대형 테마에 눌려 다른 테마가 거의 보이지 않았으므로, 카드의 1분·5분 막대를 전체 유효 테마 상대평가로 변경했다.

```text
최근 1분 또는 5분 거래대금 상대순위 : 50
대금비 상대순위                    : 20
대금비 절대 신호                   : 20
양수 프로그램·대량체결 확인         : 10
최근 거래대금 0                     : 점수 0
```

서버 출력:

```text
fund_flow_1m_bar_pct
fund_flow_1m_text
fund_flow_5m_bar_pct
fund_flow_5m_text
```

HTML은 서버가 완성한 0~100 막대와 실제 거래대금만 표시한다.

---

## 7. ThemeBoard UI 실제 검증 상태

대표님 PC에서 다음을 육안 확인했다.

| 항목 | 현재 동작 |
|---|---|
| 카드 수 | 현재 보기 서버 순위 상위 20개 |
| 평균등락률 보기 | 평균등락률 단일 기준 순서 정상 |
| 상승탄력·돈쏠림 | 각 서버 배열 전환 정상 |
| 카드 평균등락률 | 상승 빨강, 하락 파랑 |
| 카드 주도주 | 종목명 오른쪽에 등락률·색상 |
| 1분·5분 막대 | 전체 테마 상대평가 쏠림 점수 + 실제 대금 |
| 선택 상세 | 클릭 카드가 속한 행 바로 아래 표시 |
| 재클릭 | 같은 카드 재클릭 시 상세 닫기, 다시 클릭 시 열기 |
| 전체 순위표 | 모든 유효 테마 유지 |
| 좁은 화면 | 표 하단 독립 가로 스크롤바 정상 |
| 헤더 정렬 | 클릭 시 서버 cache 기준 오름차순/내림차순 토글 |
| 상단 요약 | 유효테마·입력 Feature·테마 Master 한 줄 |
| 현재 1위 요약 | 카드와 중복이므로 숨김 |
| 열 최소화 | 전체 순위표·상세표 내용 맞춤 정상 |
| 수동 열 폭 | 헤더 경계 드래그 정상 |
| 자동 맞춤 | 헤더 경계 더블클릭 정상 |
| 폭 저장 | localStorage 저장·복원 |
| 반응형 Detail | 실제 CSS grid 4/3/2/1열 기준 행 위치 재계산 |

### 7.1 확장 스크립트 장애와 해결

초기에는 서버 HTML에 확장 marker와 코드가 모두 있었지만 Chrome에서 확장 기능이 실행되지 않았다.

원인:

```text
theme && theme.average_display_rank ?? '-'
```

JavaScript는 `&&`와 `??`를 괄호 없이 혼합할 수 없어 스크립트 전체가 중단됐다.

해결:

```text
(theme && theme.average_display_rank) ?? '-'
```

`theme_average_view_script_hotfix.py`가 교정된 확장 스크립트를 기존 ThemeBoard 스크립트 내부에 삽입하고, 잘못된 두 번째 스크립트가 다시 추가되지 않도록 차단한다.

PC1 진단 확인:

```text
ExtensionLoaded        : True
AverageRows            : 66
PageHasExtensionMarker : True
PageHasAverageButton   : True
PageHasMinimizeButton  : True
PageHasColumnResizer   : True
PageHasCompactSummary  : True
```

이후 대표님 PC 브라우저에서 모든 요청 기능이 정상 동작하는 것을 확인했다.

---

## 8. 마지막 확인 성능

장마감 후 30회 측정:

| 구간 | 평균 | 최소 | 최대 |
|---|---:|---:|---:|
| total_ms | 30.343 | 26.249 | 36.859 |
| summary_core_ms | 17.369 | 13.510 | 23.356 |
| aggregate_ms | 12.633 | 10.118 | 18.867 |
| score_sort_ms | 1.200 | 1.070 | 1.489 |
| momentum_ms | 0.199 | 0.173 | 0.271 |
| dual_rank_ms | 3.583 | 2.653 | 6.989 |
| leader_rank_ms | 3.274 | 2.828 | 3.679 |
| wrapper_residual_ms | 5.918 | 1.246 | 7.268 |

판정:

```text
주도주 평균 5ms 이하  : 통과
전체 최대 40ms 이하   : 통과
전체 평균 30ms 이하   : 0.343ms 근접 초과
선택 Detail 정밀 계산 : 통과
```

최신 UI 기능은 서버 Theme 계산량을 늘리지 않는다. 다만 평균등락률 배열 생성과 브라우저 카드 20개·열 조작이 포함된 현재 버전은 장개시 실전 부하에서 다시 확인한다.

---

## 9. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 09:00~09:10 장개시 실전 부하 | collector/worker queue 지속 증가 없음, 가격 지연 없음 |
| 2 | 최신 30회 성능 재측정 | 주도주 평균 <=5ms, 전체 평균 실용 30~31ms, 최대 <=40ms |
| 3 | 상승탄력 history 실전 확인 | 60초·300초 후 급등·반납 구분 정상 |
| 4 | 주도주 실전 대조 | 테마 내 실제 강한 종목이 TOP3에 지속 반영 |
| 5 | 대량체결 HTS 대조 | 방향·건수·누적 합계 실전 정합 |
| 6 | exact 20:00 전환 확인 | closed·hold_active 전환 순간 확인 |
| 7 | 주말·공휴일·지연개장 | 실제 날짜별 continuity 검증 |
| 8 | StrategyBoard UI | 같은 continuity/FeatureSnapshot 표시 |
| 9 | Draft PR 최종 정리 | 장중 검증 통과 후 병합 여부 판단 |

---

## 10. 핵심 검증 명령

```powershell
cd C:\aiTrade

$tests = @(
    "tests\test_theme_average_view_script_hotfix.py"
    "tests\test_theme_average_view_layout.py"
    "tests\test_theme_fund_flow_toggle.py"
    "tests\test_theme_projection_dual_rank.py"
    "tests\test_theme_leader_top_scope.py"
    "tests\test_theme_leader_selection.py"
    "tests\test_themeboard_v2.py"
)

python -m pytest -q $tests
```

실행 화면:

```text
http://127.0.0.1:8765/theme
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
| `realtime_v2/theme_projection_dual_rank_patch.py` | 상승탄력·돈쏠림 서버 순위 및 자금쏠림 막대 |
| `realtime_v2/theme_average_view_layout_patch.py` | 평균등락률 배열·상단 요약·열 폭 UI 확장 |
| `realtime_v2/theme_average_view_script_hotfix.py` | Chrome 문법 오류 교정·확장 실행 보장 |
| `realtime_v2/theme_leader_selection_patch.py` | 상위 두 순위 합집합 정밀 주도주 선발 |
| `realtime_v2/theme_projection_performance_accounting_patch.py` | 최종 Theme 성능 회계 |
| `realtime_v2/theme_projection_fast_primitives_patch.py` | 6자리 종목코드 fast path |
| `realtime_v2/theme_selected_detail_runtime.py` | 선택 Detail latest-only worker |
| `realtime_v2/worker_theme_selected_detail_patch.py` | Detail API·UI cache·확장 설치 순서 |
| `realtime_v2/worker_theme_dual_rank_ui_patch.py` | 카드·보기·서버 정렬·가로 스크롤·Detail 배치 |
| `tests/test_theme_average_view_script_hotfix.py` | JavaScript 교정 회귀 |
| `tests/test_theme_average_view_layout.py` | 평균 보기·상단 요약·열 조작 계약 |
| `tests/test_theme_fund_flow_toggle.py` | 쏠림 막대·상세 토글 회귀 |
| `config/stockboard_market_calendar.json` | 휴장·지연개장 포함 시장 캘린더 |
| `config/stockboard_theme_master.json` | 장애 시 10개 fallback |
| `data/runtime/stockboard_v2/theme_membership.json` | PC별 키움 runtime 테마 마스터 |
| `docs/themeboard.html` | 계산 없는 ThemeBoard 기본 표시 UI |

---

## 12. 미검증·금지사항

```text
장중 실전 검증 전 PR 병합 금지
ThemeBoard 또는 HTML에서 직접 TR 호출 금지
HTML에서 점수·Coverage·시장 순위 계산 금지
헤더 정렬은 서버 cache 순서만 요청하고 Feature 재계산 금지
최소 collector에 테마 전체조회·보조 TR 추가 금지
907종목 전체를 실시간 등록해 테마 누락을 해결하려 하지 말 것
장마감 검증만으로 09:00 성능까지 완료라고 주장하지 말 것
closed 확인만으로 정확한 20:00 경계·주말·공휴일·지연개장 완료라고 주장하지 말 것
단위 테스트가 실제 runtime hold 파일을 읽도록 두지 말 것
```
