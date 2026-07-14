# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-14 18:55 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 상세 이력은 Git history, PR 기록, `STOCKBOARD_REALTIME_COLLECTOR_FAILURE_20260713.md`에서 확인한다. 이 문서는 현재 운영 구조, 실제 검증 결과, 남은 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard는 **32비트 가격 전용 최소 QAx collector + 64비트 Canonical State + background FeatureSnapshot + UI100** 구조로 운영한다.

선발 구조는 다음처럼 일원화됐다.

```text
등급점수 = 선발점수 = 선발순서를 결정하는 유일한 점수
HOT  1~20
WARM 21~50
COLD 51~100
```

같은 Pool 안에서는 점수와 순위가 변해도 행을 자동으로 이동하지 않는다. HOT/WARM/COLD 경계를 넘을 때만 유지시간·점수차·cooldown 안전장치를 통과해 한 종목씩 교체한다. 사용자가 열 제목을 클릭한 경우에만 전체 100종목이 화면에서 정렬·역정렬된다.

2026-07-14 애프터마켓 PC 실측에서 자동 선발순, 전체 100종목 수동정렬, S1·HTS 연동이 정상 동작했고 `render 7.4~8.3ms`, collector/worker queue 0, drop 0을 유지했다.

PR은 실제 정규장과 다음 09:00~09:10 개장 폭주 검증 전까지 Draft·미병합으로 유지한다.

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
        ↓ background heavy snapshot
단일 grade_score 선발 계산
        ↓ stable three-lane display order
HOT 20 / WARM 30 / COLD 50
        ↓ shared completed FeatureSnapshot
StockBoard / ThemeBoard / StrategyProjection
```

| 항목 | 현재 원칙 |
|---|---|
| 실시간 owner | 32비트 최소 QAx collector 한 개 |
| 생산 collector mode | `minimal_qax_price_only_v2` |
| 실시간 FID | `10;12;20;14` |
| 실시간 등록 | 100종목 안전값 유지 |
| UI 표시 | 최대 100종목 |
| 내부 후보 universe | 당일 필터 결과 약 170~190종목 |
| 선발점수 | `grade_score` 단일값 |
| Pool | HOT 20 / WARM 30 / COLD 50 |
| 자동 행 이동 | Pool 경계 교체만 허용 |
| 수동 정렬 | 브라우저 view-only |
| ThemeBoard·StrategyBoard TR | 금지 |
| HTML 시장계산·점수계산 | 금지 |
| 대량체결 | 생산 collector에서는 비활성 |
| Git | PC 장중 검증 전 Draft PR 유지, 병합 금지 |

---

## 2. 가격 collector 현재 상태

2026-07-14 장중 FID15·대량체결 aggregate가 포함된 collector는 로그인·100종목 등록·1,448건 수신 후 Python traceback과 정상 Qt 종료 표식 없이 비정상 종료했다.

생산 collector에서 다음을 제거했다.

```text
FID15 GetCommRealData
trade_qty / cntg_vol publish
collector_large_trade_patch 생산 설치
대량체결 실시간 aggregate
```

유지한 가격 핵심 경로:

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

복귀 후 PC 실측:

```text
CollectorPid      : 23732 → 23732
CollectorAlive    : True
RegisteredCount   : 100
RealData          : 22,638 → 53,707 (+31,069)
Queue             : 0
LastError         : 없음
```

FID15 하나를 직접 원인으로 확정하지 않는다. 현재 회귀 의심 범위는 **FID15 매 callback 조회와 collector 대량체결 aggregate 결합 경로**다.

관련 커밋:

```text
60ac499a13dadc14f5fa29f8d02c062e7e3164a4
fix: restore price-only minimal QAx collector

eeea3a7a79617decf0e250d15109bfcf582b0a3b
test: lock production collector to price-only QAx path
```

---

## 3. UI100과 갱신 구조

화면 구성:

```text
S1 선택 종목       1종목
집중 후보 HOT      20종목
표시 Pool WARM     21~50, 30종목
표시 Pool COLD     51~100, 50종목
브라우저 고유행    최대 100종목
```

현재가·등락률 갱신 구조:

```text
100종목 fast DOM patch     약 100ms stream 이벤트
전체 후보·점수 계산        background heavy snapshot
전체 행 heavy render       약 500ms
Pool render                약 1초
```

이번 선발·Pool 변경에서 추가하지 않은 것:

```text
새 OpenAPI 호출 없음
새 FID·TR 없음
새 collector 없음
새 socket·SSE 없음
새 worker thread 없음
새 HTTP endpoint 없음
새 선발순위 열 없음
```

따라서 HOT/WARM/COLD는 수집량을 늘리는 별도 데이터 경로가 아니라, 이미 계산된 100종목의 **선발·표시 우선순위 메타데이터**다. 현재 가격·등락률 fast patch는 100종목 모두 유지한다.

UI100 관련 커밋:

```text
7193ed674072ba5fe43a57d4a849bde7d64560c4
feat: expand StockBoard visible rows to 100

191af1ad48c3f08e8701069c4440438b13444b67
test: lock StockBoard visible rows at 100
```

---

## 4. 등급점수·선발순서 일원화

### 4.1 단일 선발값

다음 값은 동일하게 유지한다.

```text
candidate_score
=
grade_score
=
score_percent
=
selection_score
```

실제 자동 선발순서는 `grade_score` 내림차순 한 번으로 결정한다.

동점일 때만 다음 순서를 사용한다.

```text
1. WAIT_DATA가 아닌 종목
2. Coverage 높은 종목
3. 거래대금 원천순위
4. 종목코드
```

`entry_score`, `confirmation_score`, `focus_score`는 삭제하지 않고 설명·진단용 구성점수로만 보존한다. 이 값들이 별도의 Top50·Top20·Top5 순서를 만들지는 않는다.

내부 호환 순번은 모두 같은 단일 선발순서를 가리킨다.

```text
selection_rank
model_rank
pool_rank
funnel_rank
entry_rank
confirmation_rank
focus_rank
```

별도 `선발순위` 화면 열은 만들지 않는다. 화면의 `순위` 열은 계속 거래대금 순위이며, 자동 선발 상태는 등급배지와 HOT/WARM/COLD 소속으로 표현한다.

### 4.2 점수 안전장치

기존 Coverage·필수 데이터 안전정책은 유지한다.

```text
Coverage 60% 미만 또는 필수값 결측 → 최대 59점
Coverage 75% 미만                  → 최대 69점
Coverage 90% 미만                  → 최대 79점
grade guard 실패                   → 설정된 최대점수 적용
```

따라서 결측이 많은 종목이 다른 일부 요소만으로 HOT에 올라오는 것을 제한한다.

관련 커밋:

```text
c6c903c07e1c8544f1a191cb560d81da88787992
feat: unify candidate ordering on grade score

f61b1959506694644c8de9b095d122d2898673cd
fix: make grade score the absolute primary order

44f12d1d75ded6718d804bedb813e02869e76b7d
test: lock grade-score ordering monotonicity
```

---

## 5. HOT/WARM/COLD 안정 Pool

### 5.1 기본 구조

```text
grade_score 목표순위 1~20   → target_lane=hot
grade_score 목표순위 21~50  → target_lane=warm
grade_score 목표순위 51~100 → target_lane=cold
```

각 종목은 다음 상태를 가진다.

```text
selection_score
selection_rank
target_lane
active_lane
display_slot
lane_pending
update_priority
```

점수와 목표순위는 계속 갱신되지만, 같은 Pool 안에서는 `display_slot`을 유지한다.

### 5.2 행 이동 원칙

```text
HOT 내부 순위 변화   → 행 이동 없음
WARM 내부 순위 변화  → 행 이동 없음
COLD 내부 순위 변화  → 행 이동 없음
WARM/COLD → HOT      → 경계 교체만 수행
COLD → WARM          → 경계 교체만 수행
수동 헤더 정렬       → 전체 100종목 화면만 이동
```

경계 교체 시 승격 종목과 강등 종목의 자리만 바꾸고 나머지 행은 유지한다. 한 계산 주기에 최대 한 경계쌍만 교체한다.

### 5.3 안전장치

HOT 경계 기본값:

```text
도전자 20위 이내 유지       5초
강한 도전자 유지            3초
기존 HOT 30위 밖 유지      10초
강한 점수차                 8점
교체 cooldown               5초
```

WARM 경계 기본값:

```text
도전자 50위 이내 유지       5초
기존 WARM 60위 밖 유지     10초
강한 점수차                 8점
교체 cooldown               5초
```

선발모델 자체가 변경되면 이전 모델의 Pool을 천천히 교체하지 않는다.

```text
candidate_model_id 변경
→ 기존 HOT/WARM/COLD와 대기 타이머 초기화
→ 새 모델 grade_score 순서로 20/30/50 즉시 재구성
```

관련 커밋:

```text
f52162e796333a78573603c3189fc3a26dbbd182
feat: add stable HOT WARM COLD display lanes

8197e59e1d2a04daf163fe2d332268bed8b3bee8
test: lock three-lane boundary safety

726225db1ac8b4eb3fe1c3d56c260b88d02d539c
fix: reset stable pools when candidate model changes

47875a596545f96ba80b2354296acc96ba488bb9
test: lock candidate-model pool reset
```

---

## 6. 수동 정렬과 자동복귀

헤더 클릭은 전체 100종목에 적용하지만 서버 Pool 상태를 바꾸지 않는 **view-only** 기능이다.

```text
첫 클릭    기본방향 정렬
두 번째    역정렬
세 번째    자동 Pool 순서 복귀
```

기본방향:

```text
순위·종목명  오름차순
그 외 숫자열 내림차순
```

수동정렬 중에도 다음은 유지한다.

```text
S1 선택 종목
HTS 연동
서버 HOT/WARM/COLD 소속
승격·강등 대기상태
auto display_slot
```

자동모드에서는 상단 진단이 `sort auto`, 수동모드에서는 예를 들어 `sort rank/asc`로 표시된다.

관련 커밋:

```text
8e8907e0097fb4ba0eb99a4963d52b414771c580
feat: sort all 100 StockBoard rows across both lanes

bc64dc20d88f2d83ce287362e1b7245f63c74bac
feat: add three-state manual sort and auto return

179ba3e221d90e48f755114d2ddc4b8d10d1b0e1
test: lock manual sort view-only behavior
```

---

## 7. 2026-07-14 애프터마켓 화면 검증

### 7.1 자동모드

상단 상태:

```text
선발기준 5요소 수급선발 v0.1
sort auto
```

집중 후보 20종목의 등급은 화면상 대체로 다음 순서였다.

```text
F57 → F56 → F55 → F54 ... → F48 → F47
```

표시 Pool은 `F46`부터 시작했다. 집중 후보의 거래대금 순위는 `12, 91, 46, 71, 70, 140...`처럼 섞여 있어, 거래대금 순위가 아니라 등급점수가 자동 선발의 기준으로 작동함을 확인했다.

### 7.2 수동 거래대금 순위 정렬

상단 상태:

```text
sort rank/asc
```

화면에 포함된 100종목 안에서 다음처럼 연속 정렬됐다.

```text
집중 후보: 1, 2, 5, 6, 8, 10 ... 30, 31
표시 Pool : 32, 33, 34, 36, 38, 39 ...
```

상단 20종목과 하단 80종목이 별도로 정렬되지 않고 하나의 전체 100종목 순서로 이어졌다. S1 종목과 HTS 연동도 유지됐다.

### 7.3 성능 실측

| 지표 | 자동모드 | 수동 rank/asc |
|---|---:|---:|
| stream | 19ms | 46ms |
| render | 7.4ms | 8.3ms |
| collector_q | 0 | 0 |
| worker_q | 0 | 0 |
| drop | 0 | 0 |
| logdrop | 0 | 0 |
| top20 lag | 0.0초 | 1.0초 |

판정:

```text
점수 일원화 정상
자동 HOT20 구성 정상
전체 100종목 수동정렬 정상
Pool 내부 행 안정화 정상
S1·HTS 연동 정상
현재 관찰 범위에서 성능 악화 없음
```

단, 위 검증은 애프터마켓 저부하 시간대다. 다음 정규장과 09:00~09:10 폭주 구간 성능 검증은 별도로 필요하다.

현재 등급이 F57 이하로 낮게 형성된 것은 정렬 기능 문제가 아니다. 애프터마켓의 결측·stale·Coverage 제한과 대량체결 실시간 비활성의 영향 가능성을 정규장에서 재검증한다.

---

## 8. 실시간·continuity 상태

현재 가격 핵심 실시간 보증 범위:

```text
현재가
등락률
체결시각
누적거래대금
```

잔량비·순간강도·5분강도·프로그램·대량체결은 현재가와 같은 실시간 속도를 보증하지 않는다. 이전 세션 보존값이나 저속 원천이 표시될 수 있다.

continuity 보존 필드:

```text
bid_ask_ratio
execution_strength
strength_5m
program_net
large_trade_net_count
large_trade_net_sum_eok
metric_continuity_basis
```

이전 세션 값은 화면 표시가 가능하지만 ThemeBoard 주도주 계산에서는 제외한다.

---

## 9. ThemeBoard 현재 상태

ThemeBoard는 StockBoard와 같은 완료 FeatureSnapshot을 재사용하며 별도 OpenAPI 등록이나 TR을 수행하지 않는다.

```text
원천 우선순위
1. data/runtime/stockboard_v2/theme_membership.json
2. config/stockboard_theme_master.json
```

현재 기능:

```text
평균등락률 → 상승탄력 → 돈쏠림 보기
상위 테마 카드 20개
선택 테마 한 개 latest-only 상세
1분·5분 자금쏠림 상대평가
서버 완성 순위·점수·막대
종목 클릭 SBV2 HTS 연동
```

---

## 10. 운영 명령

```powershell
cd C:\aiTrade
git pull --ff-only origin fix/restore-stable-collector-20260713
.\stockboard_v2_large.cmd restart-fast
```

상태 확인:

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
Queue             : 낮고 지속 증가하지 않음
LastError         : 비어 있음
```

브라우저 확인:

```text
auto 모드        sort auto
수동 정렬         sort <key>/<asc|desc>
세 번째 클릭      sort auto 복귀
```

---

## 11. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 정규장 단일점수 검증 | 등급점수 내림차순과 HOT 목표순위 일치 |
| 2 | Pool 내부 행 고정 검증 | 같은 Pool 내 점수 변화에 자동 행 이동 없음 |
| 3 | HOT 경계 교체 검증 | 유지시간·점수차·cooldown 후 한 종목씩 교체 |
| 4 | WARM 경계 교체 검증 | COLD→WARM 교체가 안전조건 준수 |
| 5 | 선발모델 변경 검증 | 새 모델 선택 시 20/30/50 즉시 재초기화 |
| 6 | 다음 09:00~09:10 개장 폭주 | queue 증가 없음, drop 0, 가격 지연 허용범위 |
| 7 | 가격 collector 장시간 생존 | 동일 PID 30분 이상, RealData 지속 증가 |
| 8 | 대량체결 경로 원인 분리 | 생산 밖에서 FID15와 aggregate를 분리 검증 |
| 9 | collector watchdog | 비정상 종료 감지·알림·안전 재시작 |
| 10 | Draft PR 정리 | 장중 검증 통과 후 병합 여부 판단 |

---

## 12. 금지사항

```text
생산 가격 collector에 FID15·호가·강도·보조 TR을 한꺼번에 다시 추가하지 않는다.
선발점수와 별도로 entry/confirmation/focus 점수로 순서를 다시 나누지 않는다.
같은 Pool 안에서 점수 변화만으로 행을 자동 이동하지 않는다.
수동 정렬이 서버 HOT/WARM/COLD 상태를 변경하게 하지 않는다.
HOT/WARM/COLD를 이유로 새 OpenAPI·FID·TR·SSE를 추가하지 않는다.
별도 선발순위 열을 추가하지 않는다.
ThemeBoard 또는 HTML에서 직접 TR을 호출하지 않는다.
장중 실전 검증 전 PR을 병합하지 않는다.
```

---

## 13. 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 생산 가격 전용 최소 QAx collector |
| `realtime_v2/worker64_guarded_large_bidask.py` | StockBoard UI100·worker 진입점 |
| `realtime_v2/worker_opening_burst_cache_patch.py` | background heavy snapshot·100종목 fast overlay |
| `realtime_v2/candidate_score_unification_patch.py` | 등급점수·선발순서 단일화 |
| `realtime_v2/three_lane_display_order_patch.py` | HOT/WARM/COLD 안정 Pool |
| `realtime_v2/display_order_model_reset_patch.py` | 선발모델 변경 시 Pool 재초기화 |
| `realtime_v2/stockboard_global_sort_patch.py` | 전체 100종목 수동 정렬 |
| `realtime_v2/stockboard_manual_sort_mode_patch.py` | 정렬·역정렬·자동복귀 |
| `stockboard_display_order.py` | 기본 행 안정화·경계 안전장치 |
| `stockboard_candidate_engine.py` | 후보점수 계산·설명용 세부점수 |
| `realtime_v2/board_data_hub.py` | 공용 FeatureSnapshot read model |
| `realtime_v2/theme_projection_engine.py` | Theme Summary 계산 |
| `tests/test_candidate_score_unification.py` | 단일점수 단조성 계약 |
| `tests/test_three_lane_display_order.py` | 3단계 Pool 안전장치 계약 |
| `tests/test_display_order_model_reset.py` | 모델 변경 재초기화 계약 |
| `tests/test_stockboard_manual_sort_mode.py` | view-only 정렬·자동복귀 계약 |
| `tests/test_stockboard_selection_lane_performance_contract.py` | 수집·전송 경로 무부하 계약 |
