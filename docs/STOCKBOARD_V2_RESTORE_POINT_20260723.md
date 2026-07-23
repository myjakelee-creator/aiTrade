# StockBoard v2 복원지점 — FAST-TOP300-CLOSE-HOLD

기준 시각: 2026-07-23 19:10 KST

이 문서는 `docs/STOCKBOARD_V2_REALTIME_PIPELINE_20260707.md`의 전체 설계를 대체하지 않는다. 2026-07-23 애프터마켓 실기에서 가격 추종 속도가 개선된 상태를 되돌릴 수 있도록 버전, 키워드, 커밋과 유지정책만 고정한다.

## 1. 복원 식별자

```text
UI_VERSION       SBV2-20260723.16
KEYWORD          FAST-TOP300-CLOSE-HOLD
BRANCH           hotfix/SBV2-20260722.2-trade-value-rollover
PRODUCTION_BASE  58e1a3b167c4652648547bdb1f7864c2eb013ba0
CLOSE_HOLD_TEST  be03cb99bc5b4914e49e35c1ccba354f01d1e89b
UI_IDENTITY      1b848a797d9f4a18bcd9f932dd2bb194173599ea
```

`PRODUCTION_BASE`는 fast price-stream 대상이 종목코드순이 아니라 현재 거래대금순 상위 300종목이 되도록 수정한 커밋이다. 거래대금 계산값, FID, QAx, Collector, Worker 가격 guard, 100ms 주기는 변경하지 않았다.

## 2. 실기 확인

2026-07-23 19:00 KST 애프터마켓 관찰:

```text
키움 가격 추종 체감 개선
삼성전자 PriceSSE > 0 / LatestMatch True
삼성전기 PriceSSE > 0 / LatestMatch True
SK스퀘어 PriceSSE > 0 / LatestMatch True
price_sse_payload_lag median 1.5ms
price_sse_payload_lag max 16ms
dropped_trade_count 0
price_sequence_suppressed_count 0
```

`DelayMed` 수십 초 표시는 반복 가격과 이벤트 로그 시각을 매칭하는 operator trace 계산의 잔여 오차로 판정한다. 화면 fast payload 자체의 생성 지연은 1~16ms였고 최신 가격 일치는 확인됐다.

## 3. 장마감 표시 유지 계약

종목을 NXT 체결 유무 때문에 보드에서 제거하지 않는다.

```text
NXT 거래 종목     20:00 마지막 정상 가격·등락률·거래대금 유지
NXT 미거래 종목   15:30 정규장 마지막 정상 가격·등락률·거래대금 유지
20:00 이후        신규 정상값이 없으면 마지막 정상값 고정
자정/재시작       표시값 삭제 금지
다음 거래일 08:00 일괄 삭제 금지
다음 첫 정상값    해당 종목·해당 필드만 전일 hold에서 당일값으로 교체
```

내부 누적·분 bucket은 다음 실제 거래일 프리마켓 lifecycle에서 초기화할 수 있지만, 화면은 당일 정상값이 들어올 때까지 직전 완료 거래일 exact-close를 유지한다.

회귀시험 `tests/test_worker_board_display_continuity_runtime_opt.py`는 다음을 고정한다.

```text
당일 체결이 없는 종목 → 직전 exact-close 유지
당일 첫 정상 체결이 들어온 종목 → 전일 hold가 덮어쓰지 않음
```

## 4. 불변 범위

```text
_REALTIME_FIDS = 10;12;20;14
single 32-bit QAx owner
EventSender 50ms
/api/v2/price-stream 100ms
full SSE cadence
거래대금 계산식과 값
Worker 가격·누적 guard
Top100 표시 개수
```

## 5. 복원 명령

이 복원지점 브랜치 최신 커밋으로 복원:

```powershell
cd C:\aiTrade
git fetch origin
git switch hotfix/SBV2-20260722.2-trade-value-rollover
git pull --ff-only
.\stockboard_v2_large.cmd restart-fast
```

생산 동작만 특정 기준으로 되돌릴 때:

```powershell
cd C:\aiTrade
git fetch origin
git switch --detach 58e1a3b167c4652648547bdb1f7864c2eb013ba0
.\stockboard_v2_large.cmd restart-fast
```

분리 HEAD는 점검용이다. 이후 작업을 계속하려면 다시 복원지점 브랜치로 전환한다.

## 6. 아직 남은 검증

```text
20:00 NXT 실제 종료 직후 값 고정
20:00 이후 브라우저 새로고침·Worker 재시작 복원
다음 거래일 08:00 전일 exact 유지
당일 첫 정상값 수신 종목별 LIVE 전환
09:00~09:05 거래폭탄 구간 가격·queue·drop 검증
```

위 실기 전까지 Draft PR을 병합하거나 stable 태그를 만들지 않는다.
