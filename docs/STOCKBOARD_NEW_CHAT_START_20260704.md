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
