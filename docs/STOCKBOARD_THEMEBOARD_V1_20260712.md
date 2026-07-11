# StockBoard ThemeBoard v1 운영 명세

최종 갱신: 2026-07-12  
상태: 구현 완료 · 대표님 UI/HTS 확인 완료 · Draft PR #27 미병합

## 1. 목적

ThemeBoard는 장중 어느 테마로 거래대금이 집중되는지와 각 테마의 주도종목을 별도 화면에서 보여준다.

```text
StockBoard: 종목 단위 관찰·선발
ThemeBoard: 테마 단위 돈쏠림·주도주 관찰
```

ThemeBoard는 신규 OpenAPI 등록이나 별도 종목 수집을 하지 않고 StockBoard v2 worker가 이미 보유한 종목 데이터를 읽는다.

## 2. URL·브랜치·파일

| 항목 | 값 |
|---|---|
| URL | `http://127.0.0.1:8765/theme` |
| 검증 브랜치 | `feature/stockboard-themeboard-v1` |
| Draft PR | `#27` |
| 병합 대상 | `hot-priority-integrated-20260630` |

관련 파일:

| 파일 | 역할 |
|---|---|
| `config/stockboard_theme_master.json` | 테마 정의·종목 가중치 |
| `stockboard_theme_engine.py` | 테마 유입·상태·주도주 계산 |
| `realtime_v2/theme_board_patch.py` | 공용 cache·API·마감 보존 |
| `docs/stockboard_theme_v1.html` | 반응형 표시·HTS 연동 |
| `realtime_v2/worker64_guarded_large_bidask.py` | fail-open 패치 설치 |
| `tests/test_stockboard_theme_engine.py` | 계산 검증 |
| `tests/test_stockboard_theme_cache.py` | cache·HTTP 검증 |
| `tests/test_stockboard_theme_cache_heartbeat_guard.py` | heartbeat·lock 경합 검증 |
| `tests/test_stockboard_theme_close_hold.py` | 장마감 보존·캘린더 검증 |

## 3. 현재 10개 테마

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

마스터 원칙:

- `weight_policy = per_stock_sum_1`
- 종목당 순위 대상 테마 최대 3개
- 최소 가중치 0.15
- 한 종목의 활성 순위 테마 가중치 합은 1.0
- 현재 테마별 기본 최소 활성 종목 수 3
- 마스터에 있어도 현재 worker universe에 없는 종목은 계산하지 않음
- ThemeBoard 때문에 실시간 등록 종목을 늘리지 않음

현재 마스터는 초기 실전 시제품이며, 테마 구성 정확성은 장중 HTS 비교를 통해 계속 조정한다.

## 4. UI

### 상위 테마 레이더

- 테마 카드 10개
- 데스크톱 4열
- 1180px 이하 3열
- 900px 이하 세로 태블릿 2열, 2×5 균형
- 520px 이하 1열
- 카드에 1분·5분 유입, 누적대금, 점유율, 확산, 주도주 표시
- 카드 주도종목 이름 클릭 시 Kiwoom HTS/S1 연동
- 카드의 다른 영역 클릭 시 해당 테마 선택

### 테마 돈쏠림 순위

한 테마를 한 행 2열로 압축한다.

```text
왼쪽: 테마명·상태·확산·1분·5분·누적·점유·가속·집중
오른쪽: 주도주 TOP3
```

### 구성종목

- 일반 화면과 세로 태블릿: 한 행 2종목
- 650px 이하: 1열
- 역할·종목명·현재가·등락률·거래대금·비중·대금비·순간·5분·프로·대량체결·주도력 표시
- 종목 카드 클릭 시 Kiwoom HTS/S1 연동

## 5. API

| 경로 | 역할 |
|---|---|
| `/theme` | ThemeBoard HTML |
| `/api/v2/themes/stream` | 공용 SSE snapshot |
| `/api/v2/themes/snapshot` | 현재 전체 테마 snapshot |
| `/api/v2/themes/detail?theme_id=...` | 선택 테마 구성종목 |
| `/api/v2/themes/status` | cache·성능·마감 hold 상태 |

HTML은 다음을 하지 않는다.

```text
테마 합산
점수 계산
정렬
상태 판정
주도주 선정
마감 fallback 계산
```

서버가 `text`, `tone`, `class`, `bar_pct`, 표시 순서를 완성해 전달한다.

## 6. 계산 원천

worker에서 짧게 복사하는 주요 필드:

```text
stock_code / stock_name / price / change_rate / trade_value_eok
amount_ratio / execution_strength / strength_5m
program_net / large_trade_net_count / freshness 필드
```

테마 누적 거래대금:

```text
Σ(종목 누적 거래대금 × 테마 가중치)
```

현재 1분·5분 유입:

```text
1분 = 현재 테마 누적대금 − 60초 전 누적대금
5분 = 현재 테마 누적대금 − 300초 전 누적대금
```

테마 점수 구성:

```text
1분·5분 유입 percentile
+ 유입 가속
+ 시장 점유
+ 상승·하락 확산
+ 주도주 지원
− 1위 종목 과도 집중 penalty
```

상태:

```text
WAIT_DATA / SURGE / RISING / COOLING / STEADY
```

주도주 입력:

```text
테마 내 거래대금 비중
상대 등락률
대금비
순간강도
5분강도
프로그램
대량체결
```

## 7. 공용 cache·성능 보호

- ThemeBoard 클라이언트 0명: 계산 0회
- 탭이 여러 개여도 producer 1개
- 직렬화된 payload를 모든 클라이언트가 공유
- worker lock은 `acquire(False)` 비차단
- lock이 바쁘면 즉시 양보
- 계산·정렬·JSON·파일 저장은 lock 밖
- heartbeat만 바뀌면 quote 복사하지 않음
- 계산 30ms 초과 시 저속 모드
- detail payload는 표시 대상 테마만 준비
- ThemeBoard 실패 시 기존 StockBoard worker 계속 실행

대표님 PC 관찰값:

| 항목 | 값 |
|---|---:|
| compute | 약 3.0ms |
| lock/copy | 약 0.47ms |
| lock wait | 0ms |
| lock probe | 약 0.0015ms |
| payload | 약 12KB |

## 8. 장마감 마지막값 보존

runtime 파일:

```text
data/runtime/stockboard_v2/theme_last_close.json
```

Git에 커밋하지 않는다.

보존 정책:

```text
장중/애프터: LIVE
20:00 이후: 마지막 완성 snapshot을 LAST_CLOSE로 유지
주말·휴장·before_market: 동일 snapshot 유지
다음 실제 premarket: hold 해제 후 LIVE 전환
```

다음 프리마켓 경계는 `realtime_v2/market_session.py`와 `config/stockboard_market_calendar.json`을 사용한다. 특별일·지연 개장·휴장일을 건너뛴다.

재시작 후에도 유효기간이 남아 있으면 snapshot과 detail을 복원한다.

표시 fallback은 기존 worker 필드에서 다음 순서를 사용한다.

```text
현재 정상값
→ 당일 마지막 정상값
→ 정규장 마감값
→ seed/OHLC 값
→ 전일 표시 fallback
```

정확한 장마감 1분·5분 거래대금 복구는 현재 미구현이며 통합 설계 문서에서 다룬다.

## 9. HTS/S1 연동

모든 종목 클릭은 다음 클립보드 형식을 사용한다.

```text
SBV2|<timestamp>|<6자리 종목코드>
```

지원 위치:

- 상위 테마 레이더의 주도종목 이름
- 테마 순위의 주도종목 카드
- 선택 테마 구성종목 카드

종목 클릭 시 `stopPropagation()`으로 테마 선택 클릭과 분리한다.

## 10. 검증 상태

자동 검증:

- master weight 합·최대 소속·최소 가중치
- 10개 테마
- 가중 거래대금 중복 방지
- 상태·순위·집중도
- 클라이언트 0 계산 없음
- 다중 클라이언트 공용 cache
- heartbeat pre-lock skip
- worker lock 비차단
- HTTP route
- 잘못된 master fail-open
- Friday close → Monday premarket hold
- HTML 계산 금지 검사

대표님 확인:

- 화면 정상
- 10개 카드 정상
- 세로 태블릿 2열 정상
- 순위·구성종목 밀도 정상
- 장마감 값 유지 정상
- 카드·순위·구성종목 HTS 연동 정상

남은 검증:

- 실제 다음 premarket에서 `LAST_CLOSE → LIVE`
- 정규장 cache_version 지속 증가
- StockBoard stream latency·queue·drop 무영향
- 테마 마스터 실전 정확성 확대

## 11. 다음 개선

다음 개선은 `docs/STOCKBOARD_UNIFIED_DATA_COLLECTION_RECOVERY_DESIGN_20260712.md`를 따른다.

특히 장마감 1분·5분 거래대금은 다음 우선순위로 설계한다.

```text
장중 마감 sampler 정확값
→ 저장된 마지막 완성값
→ 분봉 실제 거래대금
→ 각 1분봉 종가 × 거래량 근사
```

정확값과 추정값은 source·quality·is_estimated로 구분한다.
