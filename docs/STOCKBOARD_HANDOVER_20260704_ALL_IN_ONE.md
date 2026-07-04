# StockBoard 새 채팅창 첫 지침

아래 내용을 새 채팅창 첫 메시지로 그대로 붙여넣는다.

---

```text
aiTrade / StockBoard 월요일 장초반 검증실을 시작한다.

대표님은 한국어 존댓말을 원한다.
줄글보다 표와 단계 중심의 가독성 좋은 답변을 원한다.
불필요한 새 문서 생성과 작은 단위 커밋을 싫어한다.
코딩은 먼저 설명하고 대표님 승인 후 진행한다.
git add . 금지.
검증된 것과 미검증된 것을 분리해서 보고한다.

현재 브랜치:
hot-priority-integrated-20260630

현재 최신 커밋:
e4e5e78 Request close metrics for all StockBoard groups

작업 경로:
C:\aiTrade

먼저 읽을 기준문서:
1. AGENTS.md
2. docs/STOCKBOARD_CURRENT_STATUS_20260625.md
3. docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md
4. docs/STOCKBOARD_HANDOVER_20260704_PART1_STATUS_AND_AUTOMATION.md
5. docs/STOCKBOARD_HANDOVER_20260704_PART2_STRUCTURE_AND_TROUBLESHOOTING.md
6. docs/STOCKBOARD_HANDOVER_20260704_PART3_NEXT_WORK.md
7. docs/stockboard_v0_3_0_sample.html
8. stockboard_server.py
9. kiwoom_data_provider.py
10. stockboard_live.cmd

기준문서가 현재 코드보다 오래됐을 수 있으므로, 2026-07-04 인계문서 3개를 최신 사실로 우선한다.
새 문서는 만들지 말고 기존 핵심 문서만 최소 갱신한다.
이미 시행착오로 해결한 가격/시간대/stale/drop/브라우저 patch 문제를 처음부터 다시 반복하지 않는다.

현재 StockBoard는 장마감/애프터장 이후 UI가 정상 작동하는 안정점이다.
다음 핵심 과제는 월요일 08:00~09:00 장초반 거래량 폭탄 구간 실전 검증이다.

우선 과제:
1. 기준문서 최소 갱신
2. 월요일 08:00~09:00 검증 체크리스트 작성
3. 선발기준 설정 파일 방식 설계
4. 틱데이터 저장/replay 가능성 점검
5. 월요일 실전 관찰 후 병목만 수정

선발기준은 당분간 설정 파일로 관리한다.
초기에는 복잡한 조작판을 만들지 말고 configs/candidate_models/*.yaml 또는 *.json 방식으로 시작한다.
조작판은 설정 파일 방식이 안정화된 뒤 만든다.
```


---

# StockBoard 인계서 2026-07-04 Part 1
## 현재 안정 상태와 복붙 없는 자동화 운영법

## 0. 문서 목적

이 문서는 새 채팅창에서 StockBoard 작업을 이어갈 때, 이미 해결한 문제를 처음부터 다시 반복하지 않도록 하기 위한 최신 인계서다.

| 항목 | 내용 |
|---|---|
| 프로젝트 | aiTrade / StockBoard |
| 기준일 | 2026-07-04 |
| 작업 경로 | `C:\aiTrade` |
| 브랜치 | `hot-priority-integrated-20260630` |
| 최신 커밋 | `e4e5e78 Request close metrics for all StockBoard groups` |
| 다음 핵심 과제 | 월요일 08:00~09:00 장초반 거래량 폭탄 구간 실전 검증 |

---

## 1. 대표님 작업 선호

| 항목 | 원칙 |
|---|---|
| 언어 | 한국어 존댓말 |
| 답변 형식 | 줄글보다 표와 단계 중심 |
| 코딩 | 먼저 설명하고 대표님 승인 후 진행 |
| 문서 | 새 문서 남발 금지, 기존 핵심 문서 최소 갱신 |
| Git | `git add .` 금지 |
| 커밋 | 의미 있는 단위로만 커밋 |
| 보고 | 변경 파일, 검증 명령, 미검증 항목, 커밋 상태를 분리 보고 |
| UI/이미지 | 요청 전 이미지 생성 금지 |
| 코드 수정 | 검증 가능한 작은 패치 단위 선호 |

---

## 2. 현재 확정 상태

| 구분 | 현재 상태 |
|---|---|
| UI | 장마감/애프터장 이후 정상 작동 |
| 가격 정합성 | HTS 0186과 대체로 맞아 들어감 |
| 그룹 | Top5 / S1 / Top15 / Top30 / Top300 |
| 그룹 중복 | 사실상 S1만 중복 가능 |
| 미국시장 | QQQ 다음 SOXL 추가 |
| 보조지표 | close metrics 전체 그룹 채움 적용 |
| 순위 전환 | 당일/전일 거래대금 순위 전환 버튼 force refresh 연결 |
| 속도 배지 | 응답속도 표시용으로 당분간 유지 |
| HTML 규모 | 약 5,400줄 이상. 월요일 전 대분리 금지 |
| 선발기준 | 당분간 설정 파일 방식으로 관리 예정 |

---

## 3. 최근 핵심 변경 이력

| 순서 | 변경 | 의미 |
|---:|---|---|
| 1 | hot priority를 price realtime registration에 반영 | Top5/상위 종목 실시간 가격 수신 우선순위 회복 |
| 2 | `/api/top100` 자동 반복 갱신 비활성 | 느린 전체 재조회가 장중 patch를 막지 않도록 함 |
| 3 | `/api/hot_realtime_patch` code set 명시 | 후보/선택/상위 그룹을 빠르게 갱신 |
| 4 | stale trade drop 5초 제한 해제 | 애프터장 늦은 체결이 전량 drop되는 문제 해결 |
| 5 | Top20/Top50 화면명 변경 | 실제 표시 수 기준 Top15 / Top30 |
| 6 | Top50 MID lane 추가 | Top30을 별도 중간 레인으로 갱신 |
| 7 | SOXL 추가 | 미국시장 패널에서 QQQ 다음 SOXL 표시 |
| 8 | rank mode force refresh | 당일/전일 순위 전환 버튼 실제 재조회 연결 |
| 9 | close metrics 전체 그룹 확대 | Top5/S1/Top15/Top30/Top300 모두 잔량비/강도/대량체결 채움 |

---

## 4. 복붙 없는 자동화 운영법

대표님은 반복 복붙과 수동 편집을 싫어한다.  
따라서 아래 패턴을 유지한다.

### 4.1 기본 패치 패턴

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$PATCHER = "data\runtime\some_patch_$TS.py"
$BACKUP = "data\runtime\some_patch_before_$TS.patch"

git diff -- 대상파일 > $BACKUP

@'
from pathlib import Path

path = Path("대상파일")
text = path.read_text(encoding="utf-8")

def replace_once(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"PATCH_ABORTED: {label} not found")
    text = text.replace(old, new, 1)
    print(f"OK {label}")

# 정확히 1회만 치환한다.
# 못 찾으면 중단한다.
# 여러 파일 수정 시 파일별로 명확히 처리한다.

path.write_text(text, encoding="utf-8")
print("PATCH_OK")
'@ | Set-Content -Encoding UTF8 $PATCHER

python $PATCHER
```

### 4.2 검증 기본 세트

```powershell
cd C:\aiTrade

git diff --check
git status --short
git diff --stat
```

### 4.3 Python 수정 시 검증

```powershell
cd C:\aiTrade

py -3.10-32 -m py_compile stockboard_server.py
py -3.10-32 -m py_compile kiwoom_data_provider.py
```

### 4.4 서버 상태 확인

```powershell
cd C:\aiTrade

.\stockboard_live.cmd status
```

### 4.5 서버 재시작

```powershell
cd C:\aiTrade

.\stockboard_live.cmd restart
```

### 4.6 API 기본 확인

```powershell
cd C:\aiTrade

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_provider_status"

Invoke-RestMethod "http://127.0.0.1:8000/api/top100?rank_mode=today&force=1"

Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_patch?codes=005930,000660"
```

---

## 5. Git 운영 규칙

### 5.1 금지

```powershell
git add .
```

### 5.2 허용 예시

```powershell
git add docs/stockboard_v0_3_0_sample.html stockboard_server.py
```

### 5.3 커밋 전 확인

```powershell
cd C:\aiTrade

git diff --check
git status --short
git diff --stat
```

### 5.4 커밋 후 확인

```powershell
git log -1 --oneline
git status --short
```

---

## 6. PC 이동 / GitHub Desktop pull 오류 처리

GitHub Desktop에서 아래 오류가 나올 수 있다.

```text
Unable to pull when changes are present on your branch.
The following files would be overwritten:
docs\stockboard_v0_3_0_sample.html
```

### 6.1 원인

| 원인 | 설명 |
|---|---|
| 로컬 수정 있음 | PC1에 수정 흔적 있음 |
| 원격 새 커밋 있음 | PC2/노트북에서 push된 변경 있음 |
| LF → CRLF | Windows 줄바꿈 변경만 생겼을 가능성 있음 |

### 6.2 안전 처리

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$BRANCH = git branch --show-current
$BACKUP = "data\runtime\local_before_pull_$TS.patch"

git status --short
git diff --stat
git diff > $BACKUP

git restore -- docs\stockboard_v0_3_0_sample.html

git pull --ff-only origin $BRANCH

git status --short
git log -1 --oneline
```

### 6.3 주의

| 하지 말 것 | 이유 |
|---|---|
| 바로 commit | 오래된 로컬 변경이 올라갈 수 있음 |
| 바로 stash | 나중에 stash 복원 시 충돌 가능 |
| `git add .` | 불필요한 파일 포함 위험 |

---

## 7. 기준문서 갱신 원칙

| 문서 | 갱신 내용 |
|---|---|
| `docs/STOCKBOARD_CURRENT_STATUS_20260625.md` | 2026-07-04 현재 안정 상태, 커밋, 검증 예정 |
| `docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md` | 화면명 Top15/Top30, SOXL, 레인 명칭 |
| 새 문서 | 필요 최소. 인계 문서는 예외적으로 이번에 생성 |

기준문서가 오래됐더라도, 2026-07-04 인계문서의 최신 사실을 우선한다.


---

# StockBoard 인계서 2026-07-04 Part 2
## 구조, 가격/시간대 처리, 문제 해결 금지 루프

## 0. 목적

월요일 08:00~09:00 장초반 거래량 폭탄 구간에서 문제가 생겨도, 이미 시행착오로 해결한 내용을 처음부터 다시 반복하지 않도록 StockBoard 구조와 판단 순서를 정리한다.

---

## 1. 주요 파일 구조

| 파일 | 역할 |
|---|---|
| `stockboard_live.cmd` | 실행/재시작/상태 확인 통합 런처 |
| `kiwoom_data_provider.py` | Kiwoom OpenAPI COM 실시간 수신, SetRealReg 등록, 체결/호가/store 반영 |
| `stockboard_server.py` | HTTP API 서버, top100/realtime/patch/status/us_market/market_supply |
| `stockboard_store.py` | RealtimeStore |
| `stockboard_engine.py` | 순위/표시 row 준비, 후보 필드 보강 |
| `docs/stockboard_v0_3_0_sample.html` | 현재 StockBoard UI. inline JS 많음 |
| `docs/assets/*.js` | 일부 분리된 UI 보조 JS |

---

## 2. 주요 API

| API | 역할 |
|---|---|
| `/api/top100` | 전체 순위 rows. 자동 반복 호출 금지 유지 |
| `/api/realtime` | 지정 코드 실시간 quote 조회 |
| `/api/realtime_patch` | 실시간 light patch |
| `/api/hot_realtime_patch` | HOT lane patch |
| `/api/realtime_provider_status` | Kiwoom provider/store/등록/이벤트 상태 |
| `/api/us_market` | 미국시장 QQQ/SOXL/SMH 등 |
| `/api/market_supply` | 코스피/코스닥 시장수급 |
| `/api/aftermarket_metrics_backfill_start` | 애프터장 metrics backfill 시작 |
| `/api/aftermarket_metrics_backfill_status` | backfill 상태 조회 |

---

## 3. 현재 UI 그룹 구조

| 화면명 | 내부명 | 설명 |
|---|---|---|
| Top5 | candidateRows / top5 | 유력 후보 5종목 |
| S1 | selectedRow | 선택 종목 1개 |
| Top15 | top20Rows | Top5 제외 후 실제 15종목 |
| Top30 | top50Rows | Top20 제외 후 실제 30종목 |
| Top300 | top300Rows / trading-board | 전체 pool |

주의:

```text
내부 변수명 top20 / top50은 아직 남아 있을 수 있다.
화면명만 Top15 / Top30으로 바꿨다.
변수명 전체 변경은 월요일 검증 후 별도 작업이다.
```

---

## 4. 현재 realtime lane 구조

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

## 5. 가격 표시 원칙

### 5.1 핵심 원칙

```text
가격 표시 결정은 서버에서 한다.
HTML은 display_price / display_change_rate / price_source를 표시만 한다.
```

금지:

```text
HTML에서 현재가나 등락률을 새로 계산하지 말 것.
```

### 5.2 시간대별 처리

| 시간대 | 처리 |
|---|---|
| 정규장 | realtime 가격 우선 |
| 15:30~15:40 | regular_close_snapshot lock 중요 |
| 15:40 이후 애프터마켓 | fresh realtime 있으면 aftermarket_realtime |
| 애프터마켓 realtime 없음 | regular_close_snapshot_fallback |
| 장마감 이후 | 속도 숫자는 계속 변할 수 있음. 데이터 변화와 분리 판단 |

---

## 6. 절대 처음부터 다시 반복하지 말 것

### 6.1 가격/FID 문제

이미 해결한 내용:

```text
StockBoard 가격 정합성은 대체로 회복됐다.
HTS 0186과 Top5 / Top15 / Top30 / Top300 가격이 대체로 맞아 들어간다.
가격 계산은 HTML에서 하지 않는다.
서버가 display_price / display_change_rate / price_source를 결정한다.
HTML은 서버 값을 표시만 한다.
```

금지:

```text
FID10/FID12 정규화부터 다시 의심하지 말 것.
KRX/NXT/통합장 문제를 처음부터 다시 반복 조사하지 말 것.
가격이 맞는 상태에서 대규모 price-source 재설계를 하지 말 것.
```

### 6.2 stale trade drop 문제

이미 겪은 시행착오:

| 증상 | 원인 |
|---|---|
| `trade_event_received_count` 증가 | Kiwoom 이벤트는 들어옴 |
| `trade_event_applied_count = 0` | 전부 drop |
| `stale_trade_drop_count`가 received와 같음 | stale guard가 막음 |
| 애프터장 체결 lag 12~13초 | 5초 제한에 걸림 |

해결 방향:

```text
STOCKBOARD_DROP_STALE_TRADE_SECONDS=5 때문에 애프터장 늦은 체결이 전량 drop 됐다.
stale drop을 0으로 풀자 가격 반영이 정상화됐다.
```

문제 발생 시 먼저 볼 것:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/realtime_provider_status"
```

중요 필드:

```text
trade_event_received_count
trade_event_applied_count
stale_trade_drop_seconds
stale_trade_drop_count
latest_only_dropped_count
last_trade_lag_sec
trade_last_fid10_raw
trade_last_fid20_raw
```

금지:

```text
애프터장/장초반 지연 데이터를 무조건 stale로 버리지 말 것.
5초 stale guard를 무작정 복구하지 말 것.
```

### 6.3 브라우저 문제로 오판하지 말 것

이미 확인한 것:

```text
브라우저 HOT patch timer 정상.
hot_realtime_patch payload 정상.
applyHotRealtimePatch DOM 적용 정상.
핵심 문제는 브라우저가 아니라 provider/store stale drop 및 등록 우선순위였다.
```

문제 발생 시 확인 순서:

| 순서 | 확인 |
|---:|---|
| 1 | API가 값을 주는가 |
| 2 | patch payload에 price_sequence가 증가하는가 |
| 3 | DOM apply가 되는가 |
| 4 | provider가 trade_event를 applied 하는가 |
| 5 | SetRealReg 등록 목록에 hot priority 종목이 들어갔는가 |

금지:

```text
바로 HTML 렌더링 문제로 단정하지 말 것.
무작정 setInterval을 늘리거나 줄이지 말 것.
```

---

## 7. Top15/Top30 보조지표 빈칸 문제

이미 해결한 내용:

```text
기존에는 close metrics lazy collection이 Top300 table만 훑었다.
그래서 Top15/Top30의 잔량비, 1분강도, 대량체결이 늦게 또는 안 채워졌다.
현재는 수집 대상을 전체 visible group으로 확대했다.
```

현재 요청 순서:

```text
Top5 → S1 → Top15 → Top30 → Top300
```

확인할 함수:

```text
collectNextCloseMetricCodes()
candidateBoard
selectedBoard
top20Board
top50Board
board
```

---

## 8. 문제 상황별 판단표

### 8.1 가격이 안 움직일 때

| 관찰 | 판단 |
|---|---|
| received 증가, applied 0 | stale/drop 문제 가능성 |
| realdata 증가, registered_count 낮음 | SetRealReg 등록 문제 |
| `/api/realtime_patch` 값 있음, DOM만 안 바뀜 | HTML apply 문제 |
| `/api/realtime_patch` 값 없음 | provider/store 문제 |

### 8.2 Top5만 빠르고 나머지가 느릴 때

| 항목 | 확인 |
|---|---|
| Top15 | HOT lane 공유 |
| Top30 | MID lane |
| Top300 | POOL lane |
| 정상 여부 | 전부 같은 속도로 움직이지 않는 것이 정상 |

### 8.3 장마감 후 속도 숫자가 바뀔 때

```text
속도 배지는 데이터 속도가 아니라 API 응답시간이다.
장이 끝나도 브라우저 timer가 돌면 실제 0.xx초 숫자는 변할 수 있다.
가격/등락률 변화와 응답속도 변화는 분리해서 판단한다.
```

---

## 9. 월요일 08:00~09:00 검증 체크포인트

### 9.1 08:00 프리마켓

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

### 9.2 09:00 장개시

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
