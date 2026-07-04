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
