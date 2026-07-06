# StockBoard Diagnostics

StockBoard 장초반 가격·등락률 지연 원인을 분리하기 위한 진단 폴더다.

## 목적

09:00~09:30 구간에서 가격/등락률이 Kiwoom 0186과 맞지 않을 때, 다음 구간 중 어디에서 병목이 발생하는지 수치로 확인한다.

| 구간 | 이 스크립트에서 보는 값 |
|---|---|
| Kiwoom 수신 → Store | `price_received_at`, `trade_received_at`, `price_sequence`, `trade_sequence` freshness |
| Store → API | `/api/realtime`, `/api/price_light_patch`, `/api/top100` 요청 지연 |
| API → 클라이언트 | HTTP request elapsed, server/client timestamp 차이 |
| 후보/보조지표 영향 | `one_min_strength`, `program_net`, `candidate_grade_text`, `score_percent` |
| lane 차이 | `realtime`, `price_light`, `top100` endpoint별 비교 |

브라우저 DOM 부하는 replay에서 크지 않은 것으로 보였으므로, 1차 진단은 기존 API를 외부에서 polling해서 Store/API 앞단 병목을 분리한다.

## 실행 전제

StockBoard 서버가 먼저 떠 있어야 한다.

```powershell
cd C:\aiTrade
.\stockboard_live.cmd
```

## 기본 실행

```powershell
cd C:\aiTrade
python -m stockboard_diagnostics.opening_latency_probe --duration 1800 --interval 0.5
```

기본 추적 종목:

```text
000660,005930,009150,402340,005380,010140,196170,015760,042660,010120,011070,034020,042700
```

## 특정 종목 지정

```powershell
python -m stockboard_diagnostics.opening_latency_probe --codes 000660,010140,196170,015760 --duration 600 --interval 0.5
```

## 출력 파일

`data/runtime/latency/` 아래에 생성된다.

| 파일 | 내용 |
|---|---|
| `opening_latency_samples_YYYYMMDD.csv` | endpoint/종목별 표준 샘플 |
| `opening_latency_raw_YYYYMMDD.jsonl` | provider status 등 원시 packet 일부 |
| `opening_latency_summary_YYYYMMDD.json` | p50/p95/max 요약 |

## 핵심 판정 기준

| 관찰 | 의미 | 다음 조치 |
|---|---|---|
| `price_age_sec_client`가 크다 | Store에 최신 가격이 늦게 들어오거나 API가 오래된 값을 준다 | Kiwoom 수신/Store/price lane 조사 |
| `request_elapsed_ms`가 크다 | API 응답 자체가 늦다 | server snapshot/patch 병목 조사 |
| `realtime`은 빠른데 `top100`만 늦다 | 전체 top100 row/enrichment 또는 HTML 반영 쪽 병목 | top100 재조회/overlay 분리 |
| `price_light`가 skipped/error | price_light gate 또는 hot lane 정책 문제 | PRICE_HOT lane 재설계 |
| `fid20_trade_lag_sec`가 크다 | Kiwoom trade time 기준으로 수신 이벤트 자체가 늦다 | SetRealReg/이벤트 처리/FID20 조사 |
| `realtime_source_code`가 HTS 기준과 다르다 | 통합/KRX/NXT 원천 혼용 가능성 | `_AL`/KRX source 대조 |

## 현재 한계

이 스크립트는 live server 바깥에서 API를 polling한다. 그래서 다음은 직접 측정하지 못한다.

| 미측정 구간 | 필요 후속 |
|---|---|
| `OnReceiveRealData` 진입 시각 | server 내부 latency hook 추가 |
| FID10/FID12 추출 완료 시각 | Kiwoom provider hook 추가 |
| Store lock wait | `RealtimeStore.update_trade()` 내부 hook 추가 |
| Browser DOM apply | HTML `performance.now()` beacon hook 추가 |

이번 1차 진단 결과 Store/API 앞단이 의심되면 다음 단계에서 내부 hook을 추가한다.

## 권장 운영

장초반에는 아래 명령을 별도 PowerShell에서 켜 둔다.

```powershell
cd C:\aiTrade
python -m stockboard_diagnostics.opening_latency_probe --duration 1800 --interval 0.5
```

문제 발생 시 CSV에서 해당 시각과 종목의 `price_age_sec_client`, `request_elapsed_ms`, `realtime_source_code`, `fid20_trade_lag_sec`를 먼저 확인한다.
