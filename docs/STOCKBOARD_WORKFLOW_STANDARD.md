# StockBoard Workflow Standard

작성일: 2026-07-04  
프로젝트: aiTrade / StockBoard  
문서 성격: 기준 문서  
적용 범위: 새 ChatGPT 채팅창, Codex CLI, VS Code Codex, GitHub Issue, GitHub 파일, PowerShell 로컬 실행  
기본 작업 경로: `C:\aiTrade`  
기본 브랜치: `hot-priority-integrated-20260630`

---

## 0. 이 문서를 만든 이유

StockBoard 작업은 새 채팅창으로 옮길 때마다 작업 연결 문제가 반복되었다.

| 반복 문제 | 실제 영향 |
|---|---|
| 새 채팅창이 로컬 `C:\aiTrade`를 직접 읽을 수 있다고 착각 | 파일 확인 실패, 같은 설명 반복 |
| Codex CLI가 로컬 작업을 직접 수행할 수 있다고 가정 | Windows sandbox helper 오류로 작업 중단 |
| GitHub Issue 본문과 댓글 접근 방식 혼동 | 이슈 본문을 못 읽고 댓글만 확인하는 문제 발생 |
| 문서가 여러 개로 흩어짐 | 어느 문서가 최신 기준인지 혼란 |
| 긴 지시를 매번 직접 입력 | 복붙 누락, 시간 낭비, 시행착오 반복 |
| 실패 원인이 작업지시인지 환경문제인지 혼동 | 설치/권한 문제를 StockBoard 문제처럼 재진단 |

따라서 이 문서는 앞으로 StockBoard 작업에서 사용할 **단일 자동화 기준 문서**다.

---

## 1. 핵심 결론

StockBoard 작업의 기본 방식은 다음으로 고정한다.

```text
ChatGPT가 PowerShell patcher / 검증 명령 / commit 명령을 작성한다.
대표님이 C:\aiTrade에서 직접 실행한다.
실행 결과를 채팅에 붙여넣는다.
ChatGPT가 결과를 판독하고 다음 단계를 제시한다.
정상 확인 후 대표님이 직접 commit / push 한다.
```

Codex CLI가 직접 로컬 파일을 읽고 수정하는 방식은 **보조 수단**이다. Codex CLI 사전검증이 실패하면 복구에 시간을 쓰지 말고 즉시 PowerShell 직접 실행 방식으로 우회한다.

---

## 2. 현재 확인된 Codex CLI 문제

2026-07-04 PC1에서 다음 현상이 확인되었다.

| 확인 항목 | 결과 |
|---|---|
| `where.exe codex` | `C:\Users\myjay\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe` |
| `codex --version` | `codex-cli 0.142.4` |
| `codex.cmd` | 없음 |
| `npm.cmd` | 없음 |
| 설치 방식 | npm 방식이 아니라 OpenAI native Codex 설치 방식 |
| sandbox helper 파일 | 존재함 |
| helper 경로 | `C:\Users\myjay\AppData\Local\OpenAI\Codex\bin\fb2111b91430cb17\codex-windows-sandbox-setup.exe` |

초기에는 helper를 못 찾는 오류가 나왔다. helper 경로를 PATH에 넣은 뒤에는 helper 발견은 성공했지만 sandbox 초기화 오류가 나왔다. 정확한 `codex.exe` 경로와 helper PATH를 지정한 뒤에는 최종적으로 다음 오류로 좁혀졌다.

```text
windows sandbox: orchestrator_helper_launch_failed
setup refresh failed to launch helper
error=요청한 작업을 수행하려면 권한 상승이 필요합니다. (os error 740)
```

따라서 이 문제는 aiTrade / StockBoard 코드 문제가 아니다.

| 구분 | 판단 |
|---|---|
| StockBoard 코드 문제 | 아님 |
| GitHub Issue 지시문 문제 | 주원인 아님 |
| Codex 설치 파일 완전 부재 | 아님 |
| 직접 원인 | Codex Windows sandbox helper 실행 권한 문제 |

---

## 3. 도구별 역할 구분

| 도구 | 할 수 있는 일 | 할 수 없는 일 / 주의 |
|---|---|---|
| 일반 ChatGPT 채팅창 | 설계, 분석, PowerShell 작성, patcher 작성, 로그 판독 | 대표님 PC의 `C:\aiTrade`를 직접 읽거나 수정할 수 없음 |
| GitHub 도구가 있는 ChatGPT | GitHub 원격 파일, 이슈, PR 확인 | 로컬 실행 검증은 불가 |
| Codex CLI | 로컬 파일 읽기/수정/검증 가능 | Windows sandbox helper 문제 발생 가능 |
| VS Code Codex 패널 | 로컬 작업 보조 가능 | CLI와 실행 경로가 다를 수 있음 |
| PowerShell 직접 실행 | 가장 확실한 로컬 실행 방식 | 대표님이 실행 결과를 확인해야 함 |
| GitHub Issue | 작업 요구사항 저장, 작업 지시 보관 | 댓글만 읽는 도구가 있을 수 있음. 본문 읽기 여부 확인 필요 |

---

## 4. 기본 운영 원칙

| 원칙 | 내용 |
|---|---|
| 기준 문서 단일화 | 자동화 방식은 이 문서를 우선한다 |
| 새 문서 남발 금지 | 필요 시 이 문서 또는 기존 핵심 문서만 갱신한다 |
| `git add .` 금지 | 항상 파일명을 지정한다 |
| 대표님 승인 전 코딩 금지 | 먼저 계획과 수정 대상 보고 |
| commit / push는 대표님 확인 후 | 자동 commit / push 금지. 단, 대표님이 명시적으로 GitHub 반영을 요청한 문서 작업은 예외 |
| 월요일 장초반 전 대형 변경 금지 | StockBoard 안정성 우선 |
| 실패 시 즉시 우회 | Codex CLI 복구에 장시간 쓰지 않는다 |
| 로컬 검증 우선 | GitHub에서 보이는 것과 PC 실행 상태는 별개 |
| 검증/미검증 분리 | 실제 확인하지 못한 것은 확인하지 못했다고 보고한다 |

---

## 5. 표준 작업 흐름

### 5.1 기본 흐름

```text
1. 대표님이 작업 목적을 말한다.
2. ChatGPT가 수정 대상, 위험도, 검증 방법을 설명한다.
3. 대표님이 승인한다.
4. ChatGPT가 PowerShell patcher를 작성한다.
5. 대표님이 C:\aiTrade에서 patcher를 실행한다.
6. 대표님이 실행 결과를 붙여넣는다.
7. ChatGPT가 결과를 판독한다.
8. 필요한 검증 명령을 실행한다.
9. 정상 확인 후 commit / push 명령을 제시한다.
10. 대표님이 직접 commit / push 한다.
```

### 5.2 문서만 GitHub에 반영하는 흐름

```text
1. ChatGPT가 문서 내용을 작성한다.
2. 기존 관련 문서와 중복 여부를 확인한다.
3. 기준 문서 하나로 통합한다.
4. 오래된 관련 문서는 삭제하거나 새 기준 문서로 흡수한다.
5. GitHub에 반영한다.
6. 새 채팅창 첫 지시문을 갱신한다.
```

---

## 6. 표준 PowerShell patcher 구조

### 6.1 수정 전 공통 확인

```powershell
cd C:\aiTrade

git branch --show-current
git log -1 --oneline
git status --short
```

### 6.2 안전한 patcher 기본형

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$PATCHER = "data\runtime\stockboard_patch_$TS.py"
$BACKUP = "data\runtime\stockboard_patch_before_$TS.patch"

New-Item -ItemType Directory -Force -Path "data\runtime" | Out-Null

git diff -- 대상파일 > $BACKUP

@'
from pathlib import Path

path = Path("대상파일")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
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

### 6.3 수정 후 공통 검증

```powershell
cd C:\aiTrade

git diff --check
git status --short
git diff --stat
```

### 6.4 Python 수정 시 검증

```powershell
cd C:\aiTrade

py -3.10-32 -m py_compile stockboard_server.py
py -3.10-32 -m py_compile kiwoom_data_provider.py
py -3.10-32 -m py_compile stockboard_engine.py
py -3.10-32 -m py_compile stockboard_store.py
```

---

## 7. Git 운영 규칙

### 7.1 금지

| 금지 명령 | 이유 |
|---|---|
| `git add .` | 불필요한 파일 포함 위험 |
| 강제 push | 원격 커밋 손실 위험 |

### 7.2 허용 예시

```powershell
git add docs\STOCKBOARD_WORKFLOW_STANDARD.md

git add stockboard_server.py kiwoom_data_provider.py

git add docs\stockboard_v0_3_0_sample.html
```

### 7.3 commit / push 표준 구조

```powershell
cd C:\aiTrade

git diff --check
git status --short
git diff --stat

git add <수정한 파일 1> <수정한 파일 2>

git commit -m "<요약>" -m "<상세 설명>"

git push origin hot-priority-integrated-20260630

git log -3 --oneline
git status --short
```

### 7.4 non-fast-forward 발생 시

```powershell
cd C:\aiTrade

git fetch origin
git rebase origin/hot-priority-integrated-20260630
git status --short
git push origin hot-priority-integrated-20260630
```

충돌이 나면 즉시 멈추고 `git status --short` 결과를 보고한다.

---

## 8. 새 채팅창 시작 표준 지시문

새 채팅창에서는 아래 지시를 사용한다.

```text
aiTrade / StockBoard 작업을 이어간다.

GitHub 저장소:
myjakelee-creator/aiTrade

브랜치:
hot-priority-integrated-20260630

우선 읽을 문서:
1. AGENTS.md
2. docs/STOCKBOARD_WORKFLOW_STANDARD.md
3. docs/STOCKBOARD_HANDOVER_20260704_ALL_IN_ONE.md
4. docs/STOCKBOARD_CURRENT_STATUS_20260625.md
5. docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md

주의:
- 한국어 존댓말
- 표와 단계 중심
- git add . 금지
- 대표님 승인 전 코딩 금지
- Codex CLI 직접 로컬 수정이 실패하면 PowerShell 직접 실행 방식으로 우회
- 새 문서 남발 금지
- 월요일 장초반 전에는 안정성 우선

먼저 현재 상태를 요약하고, 수정 전에는 계획과 수정 대상부터 보고하라.
```

---

## 9. Codex CLI 사용 기준

Codex CLI는 다음 사전검증을 통과한 경우에만 사용한다.

```powershell
cd C:\aiTrade

$Codex = "C:\Users\myjay\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe"

& $Codex --version

& $Codex exec "현재 폴더가 C:\aiTrade인지 확인하고, AGENTS.md 파일 존재 여부와 git status --short 결과만 보고해라."
```

정상 조건:

| 확인 항목 | 정상 기준 |
|---|---|
| Codex 버전 | 표시됨 |
| 현재 폴더 | `C:\aiTrade` |
| AGENTS.md | 존재 확인 |
| git status | 정상 출력 |
| sandbox 오류 | 없어야 함 |

---

## 10. Codex CLI 실패 판정 기준

다음 오류가 나오면 즉시 Codex CLI 직접 작업을 중단한다.

| 오류 | 의미 | 조치 |
|---|---|---|
| `program not found` | helper 또는 PATH 문제 | Codex 작업 중단, PowerShell 방식 사용 |
| `spawn setup refresh` | sandbox 초기화 실패 | Codex 작업 중단, PowerShell 방식 사용 |
| `os error 740` | 관리자 권한 상승 필요 | Codex 작업 중단, PowerShell 방식 사용 |
| Issue 댓글만 읽음 | 본문 조회 실패 가능 | 이슈 본문을 직접 붙이거나 GitHub 파일로 지시 |
| 로컬 파일 확인 실패 | 작업 검증 불가 | 대표님 직접 PowerShell 실행 |

현재 PC1에서 확인된 대표 오류:

```text
windows sandbox: orchestrator_helper_launch_failed
setup refresh failed to launch helper
error=요청한 작업을 수행하려면 권한 상승이 필요합니다. (os error 740)
```

---

## 11. Codex CLI 문제 발생 시 표준 우회

Codex CLI가 실패하면 다음 방식으로 진행한다.

```text
1. Codex 복구 시도를 중단한다.
2. ChatGPT가 PowerShell patcher를 작성한다.
3. 대표님이 직접 실행한다.
4. 결과를 붙여넣는다.
5. ChatGPT가 해석한다.
6. 정상 확인 후 대표님이 직접 commit / push 한다.
```

월요일 장초반 검증 전에는 Codex CLI 복구보다 StockBoard 안정성이 우선이다.

---

## 12. Codex CLI를 복구해야 할 때의 순서

Codex 복구는 급한 StockBoard 작업이 없을 때 PC1부터 진행한다. 모든 PC를 동시에 바꾸지 않는다.

| 순서 | 조치 |
|---:|---|
| 1 | 정확한 `codex.exe` 경로 확인 |
| 2 | helper 파일 존재 확인 |
| 3 | helper PATH 임시 추가 후 테스트 |
| 4 | `os error 740`이면 helper 관리자 권한 1회 실행 테스트 |
| 5 | 그래도 실패하면 VS Code Codex 패널 테스트 |
| 6 | 마지막에 PC1만 재설치 검토 |
| 7 | PC1 성공 후 다른 PC 적용 여부 결정 |

주의: 실제 코드 수정 작업을 항상 관리자 PowerShell에서 수행하는 방식은 추천하지 않는다.

---

## 13. GitHub Issue 사용 기준

GitHub Issue는 작업 요구사항 저장용으로 사용한다.

### 13.1 Issue 본문 필수 구조

```markdown
# 작업 목표

# 현재 기준 문서

# 수정 대상 파일

# 하지 말 것

# 구현 요구사항

# 검증 명령

# 보고 형식
```

### 13.2 Issue 사용 지시

```text
GitHub Issue #번호의 본문을 읽어라.
댓글만 읽지 말고 본문을 읽었는지 먼저 보고해라.
본문을 읽을 수 없으면 작업하지 말고 중단하라.
대표님 승인 전에는 코드 수정하지 마라.
```

### 13.3 Issue 방식의 한계

| 한계 | 설명 |
|---|---|
| 댓글만 읽는 도구 가능 | 본문 확인이 안 될 수 있음 |
| 로컬 실행은 별도 | GitHub Issue만으로 py_compile/API 검증 불가 |
| 권한 문제와 무관 | Codex sandbox가 실패하면 로컬 수정 불가 |

---

## 14. 작업 지시 파일 방식

로컬 Codex 또는 VS Code 도구가 정상일 때는 작업 지시 파일을 사용할 수 있다.

```powershell
cd C:\aiTrade

$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$TASK_DIR = "data\runtime\agent_tasks"
$TASK = "$TASK_DIR\stockboard_task_$TS.md"

New-Item -ItemType Directory -Force -Path $TASK_DIR | Out-Null

@'
# StockBoard 작업 지시문

## 작업 목표

## 기준 문서

## 수정 대상

## 하지 말 것

## 검증 명령

## 보고 형식
'@ | Set-Content -Encoding UTF8 $TASK

Write-Host "TASK_FILE=$TASK"
notepad $TASK
```

로컬 도구에는 짧게 지시한다.

```text
C:\aiTrade에서 작업한다.
작업 지시문은 아래 파일에 있다.

data/runtime/agent_tasks/stockboard_task_YYYYMMDD_HHMMSS.md

먼저 AGENTS.md와 위 작업 지시문을 읽고, 수정 전에는 계획을 보고해라.
대표님 승인 전에는 코딩하지 마라.
```

주의: 일반 ChatGPT 새 창은 PC 로컬 파일을 직접 읽을 수 없다. 새 ChatGPT 창에서 읽게 하려면 GitHub에 올리거나 파일을 업로드해야 한다.

---

## 15. 문서 관리 기준

자동화 관련 기준 문서는 이 파일 하나로 통합한다.

| 문서 | 처리 기준 |
|---|---|
| `docs/STOCKBOARD_WORKFLOW_STANDARD.md` | 유지. 자동화 기준 문서 |
| `docs/AGENT_POWERSHELL_INSTRUCTION_METHOD_20260704.md` | 삭제. 본 문서에 흡수 |
| `docs/STOCKBOARD_NEW_CHAT_START_20260704.md` | 삭제. 본 문서에 흡수 |
| `docs/STOCKBOARD_HANDOVER_20260704_PART1_STATUS_AND_AUTOMATION.md` | 삭제 가능. 본 문서와 all-in-one에 흡수 |
| `docs/STOCKBOARD_HANDOVER_20260704_PART2_STRUCTURE_AND_TROUBLESHOOTING.md` | 삭제 가능. all-in-one에 흡수 |
| `docs/STOCKBOARD_HANDOVER_20260704_PART3_NEXT_WORK.md` | 삭제 가능. all-in-one에 흡수 |
| `docs/STOCKBOARD_HANDOVER_20260704_ALL_IN_ONE.md` | 유지. 상태 인계문서. 단, 자동화 방식은 본 문서가 우선 |

`STOCKBOARD_HANDOVER_20260704_ALL_IN_ONE.md` 안에 오래된 split 문서 참조가 남아 있더라도, 새 채팅 시작 기준은 이 문서의 8장을 우선한다.

---

## 16. StockBoard 현재 작업에서 특별히 지킬 것

| 항목 | 기준 |
|---|---|
| 가격 계산 | HTML이 아니라 서버 담당 |
| `/api/top100` 반복 refresh | 복구 금지 |
| stale 5초 제한 | 무작정 복구 금지 |
| Top5/S1/Top15/Top30/Top300 | 현재 구조 유지 |
| close metrics | visible group 전체 대상 유지 |
| 선발기준 | 당분간 설정 파일 방식 |
| HTML 대분할 | 월요일 검증 전 보류 |
| Tick/replay | 중요하지만 별도 승인 후 진행 |
| 월요일 08:00~09:00 | 성능/수신/정합성 검증 최우선 |

---

## 17. 실패 시 보고 형식

도구 또는 ChatGPT는 실패 시 다음 형식으로 보고한다.

```text
변경 파일:
커밋 여부:
push 여부:
실패 지점:
실패 로그 핵심:
실제로 확인한 것:
확인하지 못한 것:
다음 권장 조치:
```

금지 표현:

```text
대충 된 것 같습니다
아마 될 것입니다
확인했습니다
```

실제로 확인하지 못한 것은 반드시 “확인하지 못함”으로 보고한다.

---

## 18. 새 채팅창에서 반복하지 말 것

| 반복 금지 항목 | 이유 |
|---|---|
| Codex sandbox 오류를 StockBoard 코드 문제로 재진단 | 원인은 Codex 실행 환경 |
| `/api/top100` 자동 반복 refresh 복구 | 장중 병목을 만들 수 있음 |
| stale trade drop 5초 제한 무작정 복구 | 애프터장 체결 전량 drop 문제 재발 가능 |
| 가격/FID/KRX/NXT 문제를 처음부터 재조사 | 현재는 가격 정합성이 상당히 회복됨 |
| HTML 대분리 | 월요일 전 위험 |
| 선발기준 조작판부터 제작 | 설정 파일 방식이 먼저 |

---

## 19. 최종 운영 결론

앞으로 StockBoard 작업 자동화는 다음 원칙으로 한다.

```text
기본은 PowerShell 직접 실행 방식.
Codex CLI는 사전검증 성공 시에만 사용.
실패하면 즉시 우회.
문서는 이 기준 문서 하나를 우선.
새 채팅창은 이 문서를 먼저 읽고 시작.
월요일 장초반 전에는 환경 변경보다 안정성 우선.
```
