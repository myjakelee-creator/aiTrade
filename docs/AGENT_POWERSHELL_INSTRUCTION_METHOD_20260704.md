# AGENT_POWERSHELL_INSTRUCTION_METHOD_20260704

## 목적

이 문서는 aiTrade / StockBoard 작업에서 **에이전트에게 시킬 일을 PowerShell로 지시문 파일에 저장하고, 에이전트가 그 파일을 읽어 작업하게 하는 방식**을 새 채팅창이나 Codex 작업창에 전달하기 위한 문서다.

대표님은 긴 지시문을 매번 복붙하는 것을 피하고, 파일 기반으로 작업 지시를 남기며, 검증과 보고가 가능한 방식으로 운영한다.

---

## 1. 핵심 개념

| 방식 | 설명 |
|---|---|
| 기존 방식 | 긴 지시문을 채팅창에 직접 복붙 |
| 권장 방식 | PowerShell로 지시문 파일을 생성하고, 에이전트에게 그 파일 경로를 읽게 함 |
| 장점 | 복붙 실수 감소, 지시문 보존, 재실행 가능, Git 관리 가능 |
| 주의 | 로컬 파일은 Codex/로컬 에이전트는 읽을 수 있지만, 일반 ChatGPT 새 창은 직접 못 읽을 수 있음 |

---

## 2. 어떤 환경에서 가능한가

| 환경 | 로컬 `C:\aiTrade` 파일 직접 읽기 |
|---|---|
| VS Code + Codex / 로컬 에이전트 | 가능 |
| Codex CLI를 `C:\aiTrade`에서 실행 | 가능 |
| 일반 ChatGPT 새 채팅창 | 직접 불가 |
| ChatGPT + GitHub 접근 가능 | GitHub에 push된 파일은 가능 |
| 파일 업로드한 채팅창 | 업로드된 파일은 가능 |

따라서 새 채팅창에서 직접 읽게 하려면:

```text
1. 지시문/인계문서를 docs 폴더에 저장
2. GitHub에 commit/push
3. 새 채팅창에 저장소/브랜치/파일명을 알려줌
```

---

## 3. 기본 폴더 규칙

PowerShell로 만든 일회성 작업 지시문은 아래에 둔다.

```text
C:\aiTrade\data\runtime\agent_tasks\
```

보존해야 하는 인계문서나 기준문서는 아래에 둔다.

```text
C:\aiTrade\docs\
```

| 폴더 | 용도 |
|---|---|
| `data/runtime/agent_tasks/` | 임시 작업 지시문, 실행용 프롬프트 |
| `docs/` | 기준문서, 인계문서, 새 채팅창이 읽을 문서 |
| `data/runtime/*.patch` | 작업 전 diff 백업 |
| `data/runtime/*.py` | 일회성 patcher 스크립트 |

---

## 4. 가장 기본적인 PowerShell 지시문 생성 방식

아래 패턴을 사용한다.

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$TASK_DIR = "data\runtime\agent_tasks"
$TASK = "$TASK_DIR\stockboard_task_$TS.md"

New-Item -ItemType Directory -Force -Path $TASK_DIR | Out-Null

@'
# StockBoard 에이전트 작업 지시문

## 목적

여기에 에이전트에게 시킬 일을 쓴다.

## 반드시 읽을 기준문서

1. AGENTS.md
2. docs/STOCKBOARD_CURRENT_STATUS_20260625.md
3. docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md

## 작업 범위

- 수정 허용 파일:
  - 예: docs/stockboard_v0_3_0_sample.html
  - 예: stockboard_server.py

## 금지

- git add . 금지
- 대표님 승인 전 commit/push 금지
- 가격 계산을 HTML로 옮기지 말 것
- 이미 해결된 가격/FID/stale drop 문제를 처음부터 반복하지 말 것

## 검증

작업 후 반드시 아래를 실행한다.

```powershell
git diff --check
git status --short
git diff --stat
```

Python 파일 수정 시:

```powershell
py -3.10-32 -m py_compile stockboard_server.py
py -3.10-32 -m py_compile kiwoom_data_provider.py
```

## 보고 형식

- 변경 파일
- 변경 요약
- 실행한 검증 명령
- 검증 결과
- 미검증 항목
- commit/push 여부
'@ | Set-Content -Encoding UTF8 $TASK

Write-Host "TASK_FILE=$TASK"
notepad $TASK
```

---

## 5. Codex / 로컬 에이전트에게 전달하는 짧은 문장

PowerShell로 지시문 파일을 만든 뒤, Codex나 로컬 에이전트에게는 긴 내용을 복붙하지 말고 아래처럼 짧게 말한다.

```text
C:\aiTrade에서 작업한다.
작업 지시문은 아래 파일에 있다.

data/runtime/agent_tasks/stockboard_task_YYYYMMDD_HHMMSS.md

먼저 AGENTS.md와 위 작업 지시문을 읽고, 수정 전에는 계획을 보고해라.
대표님 승인 전에는 코딩하지 마라.
```

---

## 6. GitHub를 통해 새 ChatGPT 창이 읽게 하는 방식

일반 ChatGPT 새 창은 PC 로컬 파일을 직접 볼 수 없다.  
새 창이 직접 읽게 하려면 지시문을 `docs/`에 넣고 push한다.

### 6.1 docs에 저장

```powershell
cd C:\aiTrade

$DOC = "docs\STOCKBOARD_AGENT_TASK_YYYYMMDD.md"

@'
# StockBoard Agent Task

여기에 새 채팅창이 읽을 작업 지시문을 쓴다.
'@ | Set-Content -Encoding UTF8 $DOC

git status --short
```

### 6.2 지정 파일만 commit/push

```powershell
cd C:\aiTrade

git diff --check
git status --short
git diff --stat

git add docs\STOCKBOARD_AGENT_TASK_YYYYMMDD.md

git commit -m "Add StockBoard agent task instructions"

git push origin hot-priority-integrated-20260630

git log -1 --oneline
git status --short
```

### 6.3 새 채팅창에 말할 문장

```text
GitHub 저장소:
myjakelee-creator/aiTrade

브랜치:
hot-priority-integrated-20260630

아래 파일을 GitHub에서 읽어라.

docs/STOCKBOARD_AGENT_TASK_YYYYMMDD.md

읽은 뒤 요약하고, 코딩 전에는 계획을 먼저 보고해라.
```

---

## 7. 작업 지시문 표준 양식

에이전트에게 줄 지시문은 항상 아래 구조를 따른다.

```markdown
# 작업명

## 1. 목적

무엇을 해결하려는 작업인지 쓴다.

## 2. 현재 상태

현재 브랜치, 최신 커밋, 확인된 정상 상태를 쓴다.

## 3. 반드시 읽을 문서

- AGENTS.md
- docs/STOCKBOARD_CURRENT_STATUS_20260625.md
- docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md
- 필요한 인계문서

## 4. 작업 범위

수정 가능한 파일을 명시한다.

## 5. 금지 사항

- git add . 금지
- 승인 전 commit/push 금지
- 대규모 리팩토링 금지
- 가격 계산을 HTML로 옮기기 금지

## 6. 구현 요구

구체적인 요구사항을 항목별로 쓴다.

## 7. 검증 명령

실행해야 할 명령을 쓴다.

## 8. 완료 보고 형식

- 변경 파일
- 변경 요약
- 검증 결과
- 미검증
- 다음 할 일
```

---

## 8. StockBoard 전용 금지 사항

에이전트 지시문에는 아래 금지 사항을 반복해서 넣는다.

| 금지 | 이유 |
|---|---|
| `git add .` 금지 | 불필요한 파일 포함 위험 |
| 승인 전 commit/push 금지 | 대표님 검토 우선 |
| HTML에서 가격 계산 금지 | 서버가 가격 표시 원천 담당 |
| `/api/top100` 자동 반복 복구 금지 | 장중 서버 지연 원인 |
| FID/가격 원천 처음부터 재검증 금지 | 이미 시행착오로 해결 |
| stale 5초 제한 무작정 복구 금지 | 애프터장 늦은 체결 전량 drop 경험 |
| 월요일 전 HTML 대분리 금지 | 안정 상태 훼손 위험 |

---

## 9. StockBoard 전용 기준 상태

지시문에 아래 상태를 넣어두면 에이전트가 처음부터 다시 헤매지 않는다.

```text
현재 StockBoard 안정 상태:
- 브랜치: hot-priority-integrated-20260630
- 최신 커밋: e4e5e78 Request close metrics for all StockBoard groups
- 그룹: Top5 / S1 / Top15 / Top30 / Top300
- Top15 내부명은 top20Rows일 수 있음
- Top30 내부명은 top50Rows일 수 있음
- 가격 표시 계산은 서버 담당
- HTML은 display_price / display_change_rate / price_source 표시만 담당
- 실시간 레인: HOT / MID / POOL
- close metrics는 전체 visible group 대상으로 확대됨
- QQQ 다음 SOXL 추가됨
- 순위 전환 버튼은 force refresh 방식
- 속도 배지는 응답속도 표시용으로 유지
```

---

## 10. PowerShell 지시문 생성 + GitHub push 통합 예시

보존해야 하는 작업 지시문은 `docs/`에 저장하고 push한다.

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$DOC = "docs\STOCKBOARD_AGENT_TASK_$TS.md"

@'
# StockBoard Agent Task

## 목적

월요일 08:00~09:00 장초반 검증 준비.

## 현재 상태

- 브랜치: hot-priority-integrated-20260630
- 최신 커밋: e4e5e78 Request close metrics for all StockBoard groups
- UI 정상 작동
- 가격 정합성 대체로 회복
- Top5 / S1 / Top15 / Top30 / Top300 구성

## 지시

먼저 기준문서와 인계문서를 읽고 현재 상태를 요약한다.
코딩은 하지 말고 월요일 검증 체크리스트만 작성한다.

## 금지

- 코딩 금지
- git add . 금지
- commit/push 금지
'@ | Set-Content -Encoding UTF8 $DOC

git diff --check
git status --short
git diff --stat

git add $DOC

git commit -m "Add StockBoard agent task instructions"

git push origin hot-priority-integrated-20260630

git log -1 --oneline
git status --short
```

---

## 11. 일회성 지시문과 보존 지시문 구분

| 종류 | 위치 | Git commit |
|---|---|---|
| 일회성 지시문 | `data/runtime/agent_tasks/` | 보통 안 함 |
| 새 채팅창이 읽을 지시문 | `docs/` | commit/push 함 |
| 기준문서/인계문서 | `docs/` | commit/push 함 |
| 임시 patcher | `data/runtime/` | 보통 안 함 |

---

## 12. 새 채팅창에 전달할 짧은 안내문

```text
이 프로젝트에서는 긴 작업 지시문을 채팅창에 직접 복붙하지 않는다.
PowerShell로 작업 지시문을 md 파일로 만들고, 에이전트가 그 파일을 읽어 작업하게 한다.

로컬 Codex/VS Code 에이전트는 C:\aiTrade의 파일을 직접 읽을 수 있다.
일반 ChatGPT 새 창은 로컬 파일을 직접 읽을 수 없으므로, docs 폴더에 저장하고 GitHub에 push한 뒤 GitHub에서 읽게 한다.

우선 아래 문서를 읽어라.

docs/AGENT_POWERSHELL_INSTRUCTION_METHOD_20260704.md
docs/STOCKBOARD_NEW_CHAT_START_20260704.md
docs/STOCKBOARD_HANDOVER_20260704_PART1_STATUS_AND_AUTOMATION.md
docs/STOCKBOARD_HANDOVER_20260704_PART2_STRUCTURE_AND_TROUBLESHOOTING.md
docs/STOCKBOARD_HANDOVER_20260704_PART3_NEXT_WORK.md
```

---

## 13. 결론

대표님 운영 방식은 다음으로 고정한다.

```text
긴 지시문 직접 복붙
→ PowerShell로 md 지시문 생성
→ 에이전트에게 파일 경로만 전달
→ 검증 명령 실행
→ 지정 파일만 git add
→ 의미 있는 단위로 commit/push
```
