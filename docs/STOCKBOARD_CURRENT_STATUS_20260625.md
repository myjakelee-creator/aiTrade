# StockBoard Current Status

작성 기준: 2026-07-04 인계서 Part 1~3 반영.

> 이 문서는 StockBoard의 현재 운영 상태를 빠르게 파악하기 위한 기준문서다. 과거 상세 이력은 2026-07-04 인계서 Part 1~3을 우선 참고한다.

---

## 0. 현재 한 줄 요약

StockBoard는 장마감/애프터장 이후 UI가 정상 작동하는 안정점에 있으며, 다음 핵심 과제는 월요일 08:00~09:00 장초반 거래량 폭탄 구간에서 Kiwoom 실시간 수신, HOT/MID/POOL patch, UI 반영 속도, HTS 가격 정합성을 실전 검증하는 것이다.

---

## 1. 기준 정보

| 항목 | 내용 |
|---|---|
| 프로젝트 | aiTrade / StockBoard |
| 작업 경로 | `C:\aiTrade` |
| 브랜치 | `hot-priority-integrated-20260630` |
| 최신 커밋 | `e4e5e78 Request close metrics for all StockBoard groups` |
| 기준일 | 2026-07-04 |
| 다음 핵심 과제 | 월요일 08:00~09:00 장초반 거래량 폭탄 구간 실전 검증 |

---

## 2. 대표님 운영 원칙

| 항목 | 원칙 |
|---|---|
| 언어 | 한국어 존댓말 |
| 답변 | 줄글보다 표와 단계 중심 |
| 코딩 | 먼저 설명하고 대표님 승인 후 진행 |
| 문서 | 새 문서 남발 금지, 기존 핵심 문서 최소 갱신 |
| Git | `git add .` 금지 |
| 커밋 | 의미 있는 단위로만 커밋 |
| 보고 | 변경 파일, 검증 명령, 미검증 항목, 커밋 상태 분리 보고 |
| 코드 수정 | 검증 가능한 작은 패치 단위 선호 |

---

## 3. 현재 확정 상태

| 구분 | 현재 상태 |
|---|---|
| UI | 장마감/애프터장 이후 정상 작동 |
| 가격 정합성 | HTS 0186과 Top5 / Top15 / Top30 / Top300 가격이 대체로 맞아 들어감 |
| 화면 그룹 | Top5 / S1 / Top15 / Top30 / Top300 |
| 내부명 주의 | 화면명은 Top15/Top30이지만 내부 변수명 `top20` / `top50`은 아직 남아 있을 수 있음 |
| 미국시장 | QQQ 다음 SOXL 추가 |
| 보조지표 | close metrics 요청 범위를 Top5/S1/Top15/Top30/Top300 전체 visible group으로 확대 |
| 순위 전환 | 당일/전일 거래대금 순위 전환 버튼 force refresh 연결 |
| 속도 배지 | 응답시간 표시용으로 유지. 실제 데이터 변화와 분리 판단 |
| HTML 구조 | CSS와 일부 순수 helper asset 분리는 진행됨. render/main loop는 inline 유지. 월요일 검증 전 추가 대분리 금지 |
| 선발기준 | 당분간 설정 파일 방식으로 관리 예정 |

---

## 4. 현재 화면 그룹 / lane 구조

### 4.1 화면 그룹

| 화면명 | 내부명 | 설명 |
|---|---|---|
| Top5 | `candidateRows` / `top5` | 유력 후보 5종목 |
| S1 | `selectedRow` | 선택 종목 1개 |
| Top15 | `top20Rows` | Top5 제외 후 실제 15종목 |
| Top30 | `top50Rows` | Top20 제외 후 실제 30종목 |
| Top300 | `top300Rows` / `trading-board` | 전체 pool |

### 4.2 realtime lane

| Lane | 대상 | API | 목적 |
|---|---|---|---|
| HOT | Top5 + S1 + Top15 | `/api/hot_realtime_patch` | 핵심 후보 빠른 갱신 |
| MID | Top30 | `/api/realtime_patch?codes=...` | 넓은 후보군 중간 갱신 |
| POOL | Top300 | `/api/realtime_patch` | 전체 pool 갱신 |

중요:

```text
/api/top100 전체 재조회는 장중 자동 반복 금지 상태다.
장중 실시간 갱신은 patch API 중심이다.
TOP100_REFRESH_MS를 다시 30000 등으로 복구하지 말 것.
```

---

## 5. 주요 API

| API | 역할 |
|---|---|
| `/api/top100` | 전체 순위 rows. 장중 자동 반복 호출 금지 유지 |
| `/api/realtime` | 지정 코드 실시간 quote 조회 |
| `/api/realtime_patch` | 실시간 light patch |
| `/api/hot_realtime_patch` | HOT lane patch |
| `/api/realtime_provider_status` | Kiwoom provider/store/등록/이벤트 상태 |
| `/api/us_market` | 미국시장 QQQ/SOXL/SMH 등 |
| `/api/market_supply` | 코스피/코스닥 시장수급 |
| `/api/aftermarket_metrics_backfill_start` | 애프터장 metrics backfill 시작 |
| `/api/aftermarket_metrics_backfill_status` | backfill 상태 조회 |

---

## 6. 가격 표시 원칙

| 항목 | 원칙 |
|---|---|
| 가격 표시 결정 | 서버에서 한다 |
| HTML 역할 | `display_price` / `display_change_rate` / `price_source`를 표시만 한다 |
| 정규장 | realtime 가격 우선 |
| 15:30~15:40 | `regular_close_snapshot` lock 중요 |
| 15:40 이후 애프터마켓 | fresh realtime 있으면 `aftermarket_realtime`, 없으면 `regular_close_snapshot_fallback` |
| 장마감 이후 | 속도 숫자는 계속 변할 수 있음. 데이터 변화와 분리 판단 |

금지:

```text
HTML에서 현재가나 등락률을 새로 계산하지 말 것.
가격 불일치를 보정식으로 해결하지 말 것.
FID10/FID12 normalize 로직을 임의 수정하지 말 것.
후보5/등급/모멘텀 계산에서 price/change_rate 원천값을 덮어쓰지 말 것.
```

---

## 7. 6자리 key / _AL 원천 원칙

```text
row.stock_code = 005930
Store key = 005930
API row key = 005930
DOM dataset stockCode = 005930
주문 code = 005930
종목마스터 code = 005930

realtime_source_code = 005930_AL
received_code = 005930_AL
registered_code = 005930_AL
normalized_code = 005930
```

| 구분 | 기준 |
|---|---|
| 화면 row key | 6자리 `stock_code` |
| Store key | 6자리 normalized code |
| 주문/종목마스터/DOM key | 6자리 code |
| 실시간 표시 가격 원천 | `_AL` 통합 코드 |
| NXT 전용 원천 | `_NX` 코드, 진단/향후 확장용 |
| KRX 원천 | 6자리 code |

---

## 8. 이미 해결한 문제와 금지 루프

| 항목 | 현재 판단 | 금지 |
|---|---|---|
| 가격/FID 문제 | 가격 정합성은 대체로 회복됨 | FID10/FID12 정규화부터 다시 의심하지 말 것 |
| KRX/NXT/통합장 | `_AL` 통합 표시 원천 유지 | 처음부터 반복 조사하지 말 것 |
| stale trade drop | 5초 stale guard로 애프터장 늦은 체결이 전량 drop된 경험 있음 | 지연 데이터를 무조건 stale로 버리지 말 것 |
| 브라우저 문제 | HOT patch timer/payload/DOM apply 정상 확인 이력 있음 | 바로 HTML 렌더링 문제로 단정하지 말 것 |
| Top15/Top30 보조지표 빈칸 | close metrics 수집 대상을 전체 visible group으로 확대 | Top300만 훑는 과거 방식으로 되돌리지 말 것 |
| `/api/top100` 반복 호출 | 장중 patch 중심 구조로 전환 | 자동 반복 refresh를 복구하지 말 것 |

---

## 9. 현재 DONE 핵심

| 구분 | DONE |
|---|---|
| REST/API | `/api/top100`, `/api/market_supply`, `/api/realtime`, `/api/realtime_status`, `/api/realtime_provider_status`, `/api/realtime_patch` |
| OpenAPI | QAxWidget, CommConnect, Qt event pump, SetRealReg, OnReceiveRealData, GetCommRealData 최소 파싱 |
| 실시간 원천 | `_AL` 통합 원천 표시 전환, 6자리 Store/API/DOM/order key 유지 |
| 화면 갱신 | 현재가, 등락률, 금액(억), 일봉, 잔량비, 순간강도, 세션강도 patch 표시 |
| 후보5 | v0.1 candidate fields와 등급/점수 표시 |
| 운영 | `stockboard_live.cmd` 통합 런처, AHK v1 HTS bridge, active row/clipboard/UpDown navigation |
| 장마감/애프터장 | close metrics snapshot 저장/복원, opt10046/opt10004 snapshot, display price policy 1차 |
| UI | Fast/Graphic mode, visual-cell, E palette, custom tooltip, 일부 CSS/helper asset 분리 |

---

## 10. 남은 TODO 핵심

| 우선순위 | TODO | 비고 |
|---:|---|---|
| 1 | 월요일 08:00~09:00 장초반 검증 | 실전 병목 확인 |
| 2 | 기준문서 최소 갱신 | 새 채팅창이 현재 상태를 바로 이해하도록 유지 |
| 3 | 틱데이터 저장 최소 설계 | 문제 재현 가능하게 함 |
| 4 | 저장 틱데이터 replay 설계 | 장중 아니어도 테스트 가능하게 함 |
| 5 | 선발기준 설정 파일화 | Python/HTML 직접 수정 부담 제거 |
| 6 | 외선, 큰손, KRT, 정확한 당일강도 | 데이터 확장 후 진행 |
| 7 | Signal / Ranking / Strategy 정식화 | 후속 큰 덩어리 |
| 8 | HTML render/main loop 추가 분리 | 월요일 검증 이후. 당분간 보류 |

---

## 11. 월요일 08:00~09:00 검증 체크리스트

### 11.1 08:00 프리마켓

| 확인 | 명령/화면 |
|---|---|
| 서버 상태 | `stockboard_live.cmd status` |
| 로그인/등록 | `/api/realtime_provider_status` |
| 수신 이벤트 | `realdata_received_count` |
| 체결 적용 | `trade_event_applied_count` |
| stale drop | `stale_trade_drop_count` |
| UI | Top5/S1/Top15/Top30/Top300 표시 |

명령:

```powershell
cd C:\aiTrade

.\stockboard_live.cmd status

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_provider_status"
```

### 11.2 09:00 장개시

| 확인 | 기준 |
|---|---|
| HOT patch | 멈추지 않아야 함 |
| price_sequence | 증가해야 함 |
| trade_event_applied_count | 빠르게 증가해야 함 |
| Top5 가격 | HTS와 대조 |
| Top15/Top30 가격 | 늦어도 계속 따라와야 함 |
| UI | 멈춤/브라우저 렉 없어야 함 |

샘플 API:

```powershell
cd C:\aiTrade

$codes = "005930,000660,009150,402340,005380,011070"

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime?codes=$codes" |
  Select-Object -ExpandProperty quotes
```

---

## 12. 문제 상황별 판단표

| 증상 | 먼저 확인 | 판단 |
|---|---|---|
| 가격이 안 움직임 | received/applied/drop count | received 증가, applied 0이면 stale/drop 문제 가능성 |
| realdata 증가, registered_count 낮음 | SetRealReg 등록 목록 | 등록 우선순위 문제 가능성 |
| `/api/realtime_patch` 값 있음, DOM만 안 바뀜 | patch payload / DOM apply | HTML apply 문제 가능성 |
| `/api/realtime_patch` 값 없음 | provider/store | 수신 또는 store 문제 가능성 |
| Top5만 빠르고 나머지가 느림 | HOT/MID/POOL lane | lane 차이 자체는 정상 |
| 보조지표 늦음 | close metrics batch/throttle | batch size/throttle 조정 후보 |

---

## 13. 선발기준 설정 파일 방향

대표님 결정:

```text
선발기준은 당분간 설정 파일로 사용한다.
코드 수정으로 선발기준을 바꾸지 않는다.
조작판은 나중에 만든다.
```

추천 경로:

```text
configs/candidate_models/
```

예시 파일:

```text
configs/candidate_models/OPENING_MOMO_V01.yaml
configs/candidate_models/PROGRAM_FLOW_V01.yaml
configs/candidate_models/LARGE_TRADE_V01.yaml
```

---

## 14. 틱데이터 저장 / replay 방향

가능하다. 저장된 틱데이터를 replay feeder로 흘려보내면 StockBoard를 장중처럼 재생할 수 있다.

권장 구조:

```text
저장 tick
→ replay feeder
→ RealtimeStore
→ /api/realtime_patch
→ StockBoard UI
```

초기 저장 형식:

```text
data/runtime/ticks/YYYYMMDD/*.jsonl
```

우선 저장 대상:

| 대상 | 이유 |
|---|---|
| 체결 tick | 가격/체결량/체결강도/대량체결 분석 |
| 호가 snapshot | 잔량비/매수·매도 압력 분석 |
| realtime patch | 화면 표시와 store 비교 |
| rank snapshot | 거래대금 순위 변화 추적 |
| API 응답시간 | UI/서버 병목 분석 |
| provider status | stale/drop/등록 문제 재현 |

---

## 15. 문서 우선순위

| 문서 | 우선순위 |
|---|---|
| `docs/STOCKBOARD_HANDOVER_20260704_PART1_STATUS_AND_AUTOMATION.md` | 최신 사실 우선 |
| `docs/STOCKBOARD_HANDOVER_20260704_PART2_STRUCTURE_AND_TROUBLESHOOTING.md` | 최신 사실 우선 |
| `docs/STOCKBOARD_HANDOVER_20260704_PART3_NEXT_WORK.md` | 최신 사실 우선 |
| `docs/STOCKBOARD_CURRENT_STATUS_20260625.md` | 현재 문서. 최신 요약 유지 |
| `docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md` | 화면명/내부키 매핑 유지 |

기준문서가 코드보다 오래됐을 수 있으므로, 2026-07-04 인계서 3개를 최신 사실로 우선한다.

---

## 16. 최종 운영 원칙

```text
REST/TR 데이터
+ OpenAPI 실시간 데이터(_AL 통합 원천)
→ RealtimeStore
→ Engine
→ Server API
→ HTML 표시
```

- HTML은 계산하지 않는다.
- Python이 계산한다.
- Store는 저장한다.
- Engine은 판단한다.
- Server는 전달한다.
- HTML은 보여준다.
- 화면 표시 실시간 가격은 `_AL` 통합 원천을 사용한다.
- 내부 식별자와 주문 코드는 6자리 코드를 유지한다.
