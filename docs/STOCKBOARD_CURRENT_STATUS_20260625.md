# StockBoard / ThemeBoard Current Status

최종 갱신: 2026-07-14 KST  
문서 역할: aiTrade 보드 계열의 단일 현재상태 기준문서  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35`

> 과거 상세 이력은 Git history, PR 기록, `STOCKBOARD_REALTIME_COLLECTOR_FAILURE_20260713.md`에서 확인한다. 이 문서는 현재 운영 구조, 검증 결과, 남은 위험과 다음 우선순위만 유지한다.

---

## 0. 현재 한 줄 결론

StockBoard는 **32비트 가격 전용 최소 QAx collector + 64비트 Canonical State + background FeatureSnapshot + UI100** 구조로 운영한다.

현재 기본 선발모델은 **`거래대금 순위 v0.1`**이며, 필터를 통과한 전체 종목을 당일 누적 거래대금 순위 하나로만 평가한다.

```text
1위   = 100점 = A100
2위   =  99점 = A99
...
100위 =   1점 = F1
101위 이하 = 0점 = F0
```

다른 요소는 이 모델 점수에 반영하지 않는다.

```text
순위상승       0%
대금비         0%
순간강도       0%
5분강도        0%
프로그램       0%
대량체결       0%
조합품질       0%
거래대금 순위 100%
```

선발 Pool은 다음과 같다.

```text
HOT  1~20
WARM 21~50
COLD 51~100
```

같은 Pool 안에서는 거래대금 순위가 변해도 행을 자동으로 계속 이동하지 않는다. Pool 경계를 넘을 때만 유지시간·점수차·cooldown 안전장치를 통과해 한 종목씩 교체한다. 사용자가 열 제목을 클릭한 경우에만 전체 100종목이 화면에서 정렬·역정렬된다.

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
당일 거래대금 순위 100% 점수
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
| 기본 선발모델 | `거래대금 순위 v0.1` |
| 호환 model id | `FIVE_FACTOR_FLOW_V01` |
| 선발점수 | 당일 거래대금 순위 100% |
| Pool | HOT 20 / WARM 30 / COLD 50 |
| 자동 행 이동 | Pool 경계 교체만 허용 |
| 수동 정렬 | 브라우저 view-only |
| ThemeBoard·StrategyBoard TR | 금지 |
| HTML 시장계산·점수계산 | 금지 |
| 대량체결 | 생산 collector에서는 비활성 |
| Git | 정규장 검증 전 Draft PR 유지, 병합 금지 |

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

## 3. 거래대금 순위 단독 선발모델

### 3.1 점수 원천

필터를 통과한 전체 내부 종목을 당일 누적 거래대금 내림차순으로 줄 세운 뒤 `rank`를 부여한다.

점수식:

```text
1 <= rank <= 100 : score = 101 - rank
rank >= 101      : score = 0
```

예시:

| 거래대금 순위 | 점수 | 등급배지 |
|---:|---:|---|
| 1 | 100 | A100 |
| 2 | 99 | A99 |
| 11 | 90 | A90 |
| 12 | 89 | B89 |
| 21 | 80 | B80 |
| 22 | 79 | C79 |
| 31 | 70 | C70 |
| 32 | 69 | D69 |
| 41 | 60 | D60 |
| 42 | 59 | F59 |
| 100 | 1 | F1 |
| 101 이하 | 0 | F0 |

등급 구간은 기존 방식을 유지한다.

```text
90점 이상 A
80점 이상 B
70점 이상 C
60점 이상 D
60점 미만 F
```

### 3.2 점수 일원화

다음 값은 모두 거래대금 순위 점수와 동일하다.

```text
candidate_score
=
grade_score
=
score_percent
=
selection_score
```

호환 필드도 동일 순서를 가리킨다.

```text
selection_rank
model_rank
pool_rank
funnel_rank
entry_rank
confirmation_rank
focus_rank
```

현재 기본 모델의 네 점수 그룹은 모두 거래대금 순위 100%다.

```text
final_score        = trade_value_rank 100%
entry_score        = trade_value_rank 100%
confirmation_score = trade_value_rank 100%
focus_score        = trade_value_rank 100%
```

`required_features`와 `grade_guards`는 비어 있다. 순위상승·대금비·강도·프로그램·대량체결·조합품질과 해당 데이터의 결측·stale 상태는 이 모델 점수에 개입하지 않는다.

### 3.3 구현 이유

목적은 순수하게 당일 거래대금 집중도를 보기 위한 것이다.

```text
거래대금 순위가 높다
→ 등급점수가 높다
→ 모델 목표순위가 높다
```

`grade_score/desc`와 `rank/asc`는 동일한 종목 순서를 만들어야 한다.

관련 커밋:

```text
58fc3ff25942500d72df77dd23e8876f425ad705
feat: add exact top100 trade value rank score

4683a3ec3d4618a5e0aabf680f4caa592be589c6
fix: make default selection pure trade value rank

b7428ad96b5b2ac236056e1bb2aac2361187e0ae
fix: rename default model to trade value rank

3d58c4ade0d3a068aa4b57ea0fb37f549e754e85
test: lock pure trade value ranking model
```

---

## 4. UI100과 갱신 구조

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

거래대금 점수는 이미 존재하는 `rank` 숫자에 `101-rank`를 적용하는 순수 산술이다. 기존 다요소 점수 계산보다 단순하며 다음을 추가하지 않는다.

```text
새 OpenAPI 호출 없음
새 FID·TR 없음
새 collector 없음
새 socket·SSE 없음
새 worker thread 없음
새 HTTP endpoint 없음
새 DOM 작업 없음
새 선발순위 열 없음
```

따라서 거래대금 단독 선발모델은 collector·stream·render 경로의 부하를 늘리지 않는다.

---

## 5. HOT/WARM/COLD 안정 Pool

목표 Pool:

```text
grade_score 목표순위 1~20   → target_lane=hot
grade_score 목표순위 21~50  → target_lane=warm
grade_score 목표순위 51~100 → target_lane=cold
```

현재 기본 모델에서는 `grade_score`가 거래대금 순위 점수이므로 다음과 같다.

```text
거래대금 1~20위    → HOT 목표
거래대금 21~50위   → WARM 목표
거래대금 51~100위  → COLD 목표
```

같은 Pool 안에서는 `display_slot`을 유지한다.

```text
HOT 내부 순위 변화   → 행 이동 없음
WARM 내부 순위 변화  → 행 이동 없음
COLD 내부 순위 변화  → 행 이동 없음
WARM/COLD → HOT      → 경계 교체만 수행
COLD → WARM          → 경계 교체만 수행
수동 헤더 정렬       → 전체 100종목 화면만 이동
```

경계 교체 시 승격 종목과 강등 종목의 자리만 바꾸고 나머지 행은 유지한다. 한 계산 주기에 최대 한 경계쌍만 교체한다.

안전장치 기본값:

```text
HOT 도전자 20위 이내 유지    5초
강한 도전자 유지             3초
기존 HOT 30위 밖 유지       10초
WARM 도전자 50위 이내 유지   5초
기존 WARM 60위 밖 유지      10초
강한 점수차                  8점
교체 cooldown                5초
```

선발모델 자체가 변경되면 이전 모델의 Pool과 대기 타이머를 초기화하고 새 모델 순서로 20/30/50을 즉시 재구성한다.

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

상단 진단:

```text
자동 Pool 순서      sort auto
거래대금 순위 보기  sort rank/asc
등급점수 순위 보기  sort grade_score/desc
```

현재 기본 모델에서는 다음 두 수동정렬 결과가 같아야 한다.

```text
sort rank/asc
=
sort grade_score/desc
```

하지만 `sort auto`는 Pool 내부 행 고정 정책을 유지하므로 시간이 지나면 수동 점수순 화면과 행 순서가 달라질 수 있다.

스트림 연결과 가격·등락률 fast patch 주기는 세 상태 모두 동일하다. 수동 정렬에서는 heavy render 시 최대 100개 객체를 한 번 정렬하는 소규모 브라우저 작업만 추가된다.

2026-07-14 애프터마켓 실측:

| 지표 | 자동모드 | 수동 rank/asc |
|---|---:|---:|
| stream | 19ms | 46ms |
| render | 7.4ms | 8.3ms |
| collector_q | 0 | 0 |
| worker_q | 0 | 0 |
| drop/logdrop | 0/0 | 0/0 |
| top20 lag | 0.0초 | 1.0초 |

위 수치는 거래대금 단독 모델 전환 전의 UI 정렬 경로 실측이다. 거래대금 단독 점수는 계산량을 줄이므로 정규장과 개장 폭주에서 성능을 다시 확인한다.

---

## 7. 실시간·continuity 상태

현재 가격 핵심 실시간 보증 범위:

```text
현재가
등락률
체결시각
누적거래대금
```

잔량비·순간강도·5분강도·프로그램·대량체결은 현재가와 같은 실시간 속도를 보증하지 않는다. 이전 세션 보존값이나 저속 원천이 표시될 수 있다.

이 값들은 화면 참고용으로 계속 표시할 수 있지만 `거래대금 순위 v0.1`의 점수에는 반영하지 않는다.

---

## 8. ThemeBoard 현재 상태

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

## 9. 운영 명령

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

거래대금 단독 모델 확인:

```text
드롭다운          거래대금 순위 v0.1
순위 1            A100
순위 12           B89
순위 21           B80
순위 100          F1
순위 101 이하     F0
```

---

## 10. 다음 우선순위

| 우선순위 | 작업 | 완료 기준 |
|---:|---|---|
| 1 | 거래대금 단독점수 화면 검증 | 1위=A100, 12위=B89, 100위=F1, 101위 이하=F0 |
| 2 | 두 수동정렬 동치 검증 | `rank/asc`와 `grade_score/desc` 종목순서 일치 |
| 3 | Pool 내부 행 고정 검증 | 같은 Pool 내 순위 변화에 자동 행 이동 없음 |
| 4 | HOT 경계 교체 검증 | 유지시간·점수차·cooldown 후 한 종목씩 교체 |
| 5 | WARM 경계 교체 검증 | COLD→WARM 교체가 안전조건 준수 |
| 6 | 다음 09:00~09:10 개장 폭주 | queue 증가 없음, drop 0, 가격 지연 허용범위 |
| 7 | 가격 collector 장시간 생존 | 동일 PID 30분 이상, RealData 지속 증가 |
| 8 | 대량체결 경로 원인 분리 | 생산 밖에서 FID15와 aggregate를 분리 검증 |
| 9 | collector watchdog | 비정상 종료 감지·알림·안전 재시작 |
| 10 | Draft PR 정리 | 장중 검증 통과 후 병합 여부 판단 |

---

## 11. 금지사항

```text
생산 가격 collector에 FID15·호가·강도·보조 TR을 한꺼번에 다시 추가하지 않는다.
거래대금 단독 모델에 다른 점수요소·Coverage cap·필수값 guard를 다시 섞지 않는다.
거래대금 순위 101위 이하에 양수 점수를 부여하지 않는다.
같은 Pool 안에서 순위 변화만으로 행을 자동 이동하지 않는다.
수동 정렬이 서버 HOT/WARM/COLD 상태를 변경하게 하지 않는다.
HOT/WARM/COLD를 이유로 새 OpenAPI·FID·TR·SSE를 추가하지 않는다.
별도 선발순위 열을 추가하지 않는다.
ThemeBoard 또는 HTML에서 직접 TR을 호출하지 않는다.
장중 실전 검증 전 PR을 병합하지 않는다.
```

---

## 12. 핵심 파일

| 파일 | 역할 |
|---|---|
| `realtime_v2/collector32_large_bidask.py` | 생산 가격 전용 최소 QAx collector |
| `realtime_v2/worker64_guarded_large_bidask.py` | StockBoard UI100·worker 진입점 |
| `realtime_v2/worker_opening_burst_cache_patch.py` | background heavy snapshot·100종목 fast overlay |
| `realtime_v2/trade_value_rank_score_patch.py` | 거래대금 순위 1~100위에 100~1점 부여 |
| `configs/candidate_models/FIVE_FACTOR_FLOW_V01.json` | `거래대금 순위 v0.1` 100% 모델 설정 |
| `realtime_v2/candidate_score_unification_patch.py` | 점수·선발순서 단일화 |
| `realtime_v2/three_lane_display_order_patch.py` | HOT/WARM/COLD 안정 Pool |
| `realtime_v2/display_order_model_reset_patch.py` | 선발모델 변경 시 Pool 재초기화 |
| `realtime_v2/stockboard_global_sort_patch.py` | 전체 100종목 수동 정렬 |
| `realtime_v2/stockboard_manual_sort_mode_patch.py` | 정렬·역정렬·자동복귀 |
| `stockboard_display_order.py` | 기본 행 안정화·경계 안전장치 |
| `stockboard_candidate_engine.py` | 후보점수 계산·호환 필드 |
| `tests/test_trade_value_rank_score.py` | 100~1점·F0·단독모델 계약 |
| `tests/test_candidate_score_unification.py` | 단일점수 단조성 계약 |
| `tests/test_three_lane_display_order.py` | 3단계 Pool 안전장치 계약 |
| `tests/test_stockboard_manual_sort_mode.py` | view-only 정렬·자동복귀 계약 |
| `tests/test_stockboard_selection_lane_performance_contract.py` | 수집·전송 경로 무부하 계약 |
