# StockBoard 인계서 2026-07-04 Part 3
## 다음 작업, 선발기준 설정 파일, 틱데이터, HTML 분리

## 0. 다음 작업 우선순위

| 순서 | 작업 | 이유 |
|---:|---|---|
| 1 | 기준문서 최소 갱신 | 새 채팅창이 현재 상태를 읽고 바로 이해 |
| 2 | 월요일 08:00~09:00 검증 체크리스트 | 실전 구간 병목 확인 |
| 3 | 틱데이터 저장 최소 설계 | 문제 재현 가능하게 함 |
| 4 | 저장 틱데이터 replay 설계 | 장중 아니어도 테스트 가능 |
| 5 | 선발기준 설정 파일화 | Python/HTML 직접 수정 부담 제거 |
| 6 | HTML 기능별 분리 | 월요일 검증 이후 유지보수 개선 |
| 7 | 선발기준 조작판 | 설정 파일 방식 안정화 후 진행 |

---

## 1. 기준문서에 반드시 반영할 내용

문서 갱신은 새 문서를 남발하지 말고 기존 핵심 문서만 최소 갱신한다.

| 문서 | 반영 내용 |
|---|---|
| `docs/STOCKBOARD_CURRENT_STATUS_20260625.md` | 2026-07-04 안정 상태, 최신 커밋, 레인, 월요일 검증 |
| `docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md` | 화면명 Top5/S1/Top15/Top30/Top300, SOXL, 속도 배지 의미 |

반영 문구 예시:

```text
2026-07-04 기준 StockBoard 현재 상태

- 최신 커밋: e4e5e78 Request close metrics for all StockBoard groups
- 브랜치: hot-priority-integrated-20260630
- 그룹명: Top5 / S1 / Top15 / Top30 / Top300
- 내부 top20/top50 이름은 아직 유지될 수 있음
- 가격 표시 계산은 서버 담당, HTML은 표시 담당
- 실시간 레인: HOT / MID / POOL
- /api/top100 자동 반복 refresh는 비활성 유지
- stale trade drop 5초 제한으로 애프터장 체결이 전량 drop된 문제가 있었고, 현재는 늦은 체결을 수용하도록 조정됨
- close metrics 요청 범위는 전체 visible group으로 확대됨
- QQQ 다음 SOXL 추가
- 당일/전일 순위 전환 버튼 force refresh 연결
- 속도 배지는 응답시간 표시용으로 유지
- 월요일 08:00~09:00 성능 검증이 다음 핵심 과제
- 선발기준은 당분간 설정 파일 방식으로 관리
```

---

## 2. 선발기준 운영 원칙

대표님 결정:

```text
선발기준은 당분간 설정 파일로 사용한다.
코드 수정으로 선발기준을 바꾸지 않는다.
조작판은 나중에 만든다.
```

### 2.1 왜 설정 파일부터인가

| 방식 | 장점 | 단점 |
|---|---|---|
| Python 직접 수정 | 빠르게 하드코딩 가능 | 매번 코딩 필요, 실수 위험 |
| HTML 직접 수정 | 화면 반영 쉬움 | 계산 책임이 UI로 섞임 |
| 설정 파일 | 조건 변경 쉬움, 기록/버전관리 쉬움 | 초기 parser 필요 |
| 조작판 | 가장 편함 | 처음부터 만들면 일이 큼 |

결론:

```text
1단계는 설정 파일.
2단계는 설정 파일을 읽는 엔진.
3단계는 설정 파일을 편집하는 조작판.
```

### 2.2 추천 경로

```text
configs/candidate_models/
```

예시 파일:

```text
configs/candidate_models/OPENING_MOMO_V01.yaml
configs/candidate_models/PROGRAM_FLOW_V01.yaml
configs/candidate_models/LARGE_TRADE_V01.yaml
```

### 2.3 설정 파일 초안 예시

```yaml
id: OPENING_MOMO_V01
name: 장초반 상승탄력
enabled: true
description: 09:00 이후 거래대금 급증, 1분강도, 대량체결, 프로그램 수급을 조합한다.

universe:
  rank_basis: today
  max_rank: 100
  exclude_etf: true
  exclude_preferred: true

filters:
  - field: trade_value_rank
    op: <=
    value: 50
  - field: change_rate
    op: between
    value: [-5, 20]
  - field: one_min_strength
    op: >=
    value: 100

score:
  - field: trade_value_rank_score
    weight: 30
  - field: one_min_strength
    weight: 20
  - field: large_trade_net_count
    weight: 20
  - field: program_net
    weight: 10
  - field: bid_ask_ratio
    weight: 10
  - field: rank_up_speed
    weight: 10

output:
  candidate_count: 5
  sort_by: total_score
  tie_breakers:
    - trade_value
    - one_min_strength
    - large_trade_net_count
```

### 2.4 선발기준 후보 항목

| 분류 | 항목 |
|---|---|
| 순위 | 전일 거래대금 순위, 당일 거래대금 순위, 순위 상승폭 |
| 가격 | 등락률, 시가 대비, 전일 종가 대비 |
| 거래 | 거래대금, 1분 거래대금, 거래대금 증가율 |
| 체결 | 순간강도, 1분강도, 당일강도 |
| 큰손 | 대량체결 건수, 순매수 건수, 순매수 금액 |
| 수급 | 외합, 프로그램, 외인, 기관 |
| 호가 | 잔량비, 매수잔량, 매도잔량 |
| 캔들 | VWAP 위치, 고가 돌파, 전고 돌파 |
| 시장 | 코스피/코스닥 분위기, 미국장 영향 |

### 2.5 설정 파일 방식의 최소 구현 단계

| 단계 | 내용 |
|---:|---|
| 1 | YAML/JSON 파일 1개 작성 |
| 2 | `candidate_model_loader.py` 또는 기존 engine에 loader 추가 |
| 3 | 허용 field/op 검증 |
| 4 | 점수 계산 결과를 row에 추가 |
| 5 | UI의 선발기준 select와 연결 |
| 6 | 결과와 기존 후보5 비교 |
| 7 | 안정화 후 조작판 설계 |

주의:

```text
초기에는 조작판을 만들지 않는다.
설정 파일이 안정화된 뒤 조작판을 만든다.
```

---

## 3. 틱데이터 저장 / replay

### 3.1 가능 여부

```text
가능하다.
저장된 틱데이터를 replay feeder로 흘려보내면 StockBoard를 장중처럼 재생할 수 있다.
```

### 3.2 권장 구조

```text
저장 tick
→ replay feeder
→ RealtimeStore
→ /api/realtime_patch
→ StockBoard UI
```

### 3.3 우선 저장 대상

| 대상 | 이유 |
|---|---|
| 체결 tick | 가격/체결량/체결강도/대량체결 분석 |
| 호가 snapshot | 잔량비/매수·매도 압력 분석 |
| realtime patch | 화면 표시와 store 비교 |
| rank snapshot | 거래대금 순위 변화 추적 |
| API 응답시간 | UI/서버 병목 분석 |
| provider status | stale/drop/등록 문제 재현 |

### 3.4 초기 저장 형식

처음에는 단순한 JSONL을 권장한다.

```text
data/runtime/ticks/YYYYMMDD/*.jsonl
```

예시:

```json
{"ts":"2026-07-06T09:00:01.123+09:00","event":"trade","code":"005930","price":314500,"volume":1200,"strength":135.2}
{"ts":"2026-07-06T09:00:01.280+09:00","event":"trade","code":"000660","price":1984000,"volume":80,"strength":142.7}
{"ts":"2026-07-06T09:00:01.300+09:00","event":"orderbook","code":"005930","bid_volume":120000,"ask_volume":90000,"ratio":1.33}
```

나중에 필요하면 SQLite 또는 Parquet로 확장한다.

### 3.5 replay 목표

| 목표 | 설명 |
|---|---|
| UI 재생 | 과거 장초반을 화면에서 재현 |
| 성능 테스트 | 09:00 거래량 폭탄 구간 부하 테스트 |
| 후보 검증 | 선발기준 설정 파일 성능 비교 |
| 버그 재현 | 장중 아니어도 문제 재현 |
| 전략 연구 | 매수/청산 후보 조건 분석 |

---

## 4. HTML 5,400줄 문제

현재 판단:

```text
월요일 08:00~09:00 검증 전 대규모 분리 금지.
현재 잘 작동하는 구조를 깨면 안 된다.
```

### 4.1 단기 방침

| 항목 | 방침 |
|---|---|
| HTML 대분리 | 보류 |
| 긴급 수정 | 현 파일에 최소 패치 |
| 레인/가격/보조지표 | 월요일 검증 전 안정 유지 |
| 리팩토링 | 월요일 검증 후 |

### 4.2 월요일 이후 분리 후보

| 후보 파일 | 내용 |
|---|---|
| `stockboard_realtime_lanes.js` | HOT/MID/POOL patch |
| `stockboard_rank_controls.js` | 당일/전일 순위 전환 |
| `stockboard_close_metrics.js` | 잔량비/1분강도/대량체결 채움 |
| `stockboard_candidate_models.js` | 후보 선발 모델 |
| `stockboard_render_sections.js` | Top5/S1/Top15/Top30/Top300 렌더 |

원칙:

```text
한 번에 대분리하지 말 것.
기능 단위로 작게 분리하고 매번 화면 검증한다.
```

---

## 5. 월요일 실전 검증 후 판단

| 결과 | 다음 조치 |
|---|---|
| 08:00~09:00 문제 없음 | 기준문서 갱신 후 선발기준 설정 파일 작업 |
| 가격 지연 | provider/status/registration 우선 확인 |
| UI 렉 | lane interval, DOM update, patch payload 크기 확인 |
| Top30/Top300 느림 | MID/POOL lane 조정 |
| 보조지표 늦음 | close metrics batch size/throttle 조정 |
| 후보5 부정확 | candidate model 설정 파일 작업 우선 |

---

## 6. 새 채팅창에서의 첫 작업 권장 순서

```text
1. AGENTS.md 읽기
2. docs/STOCKBOARD_CURRENT_STATUS_20260625.md 읽기
3. docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md 읽기
4. 2026-07-04 인계서 Part 1~3 읽기
5. 기준문서 최소 갱신
6. 월요일 08:00~09:00 검증 체크리스트 작성
7. 선발기준 설정 파일 설계안 설명
8. 대표님 승인 후 코딩
```

---

## 7. 다음 채팅창에서 바로 쓰는 작업 요청 예시

```text
위 인계문서를 읽고 현재 StockBoard 상태를 요약해줘.
그 다음 월요일 08:00~09:00 검증 체크리스트를 만들어줘.
코딩은 하지 말고 먼저 검증 순서와 확인 명령만 제시해줘.
```
