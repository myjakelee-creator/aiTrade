# StockBoard v2 실시간 collector 장애·복구 보고서

최종 갱신: 2026-07-13 14:05 KST  
최초 작성: 2026-07-13 11:03 KST  
상태: **핵심 가격·등락률 복구 · 최소 collector 장시간 관찰 중 · 자동매매 연결 보류**  
작업 브랜치: `fix/restore-stable-collector-20260713`  
기준 브랜치: `hot-priority-integrated-20260630`  
Draft PR: `#35 fix: restore stable StockBoard realtime collector`  
작업 경로: `C:\aiTrade`  
화면: `http://127.0.0.1:8765/`

---

## 0. 한 줄 결론

기존 생산용 32비트 collector는 QAx/OpenAPI 자체가 아니라 복잡한 provider·patch·timer·보조 TR 계층을 함께 실행하는 과정에서 Windows 네이티브 힙 손상(`ntdll.dll`, 예외코드 `0xc0000374`)으로 종료됐다. 생산용 collector를 최소 QAx 핵심 경로로 교체하고 현재가·등락률·체결시각·누적거래대금만 실시간 수집하도록 축소한 결과, 같은 PID로 10만 건 이상 연속 수신하고 HTS와 가격·등락률이 일치하는 상태까지 복구했다. UI는 50종목만 전송·표시하고 내부 후보 계산은 필터 후 약 185~186종목을 유지한다.

---

## 1. 현재 운영 판정

| 항목 | 현재 판정 |
|---|---|
| 현재가 | 실시간 갱신 복구, HTS와 주요 종목 일치 확인 |
| 등락률 | 실시간 갱신 복구, 현재가와 동일 이벤트 기준으로 표시 |
| 누적거래대금 | 핵심 FID 14로 실시간 수신 |
| collector 생존 | 최소 QAx collector에서 장시간 관찰 중 |
| 브라우저 표시 | 50종목으로 축소, Top20 + 표시 Pool 30 |
| 내부 후보 계산 | 필터 후 185~186종목 유지 |
| 실시간 등록 종목 | 현재 안전 검증 기준 100종목 |
| 잔량비·순간강도·5분강도·대량체결 | 기존 보존값 또는 저속 snapshot 표시 가능, 이번 복구의 실시간 보증 대상 아님 |
| 수동매매 감시 | 가격·등락률 기준 사용 가능 수준 |
| 자동매매 연결 | 장시간 생존·재시작 반복 검증 전 보류 |

**중요:** 화면에 남아 있는 잔량비·강도·프로그램·대량체결 값은 현재가·등락률과 같은 속도로 갱신된다는 뜻이 아니다. 이번 단계의 최우선 보증 범위는 현재가, 등락률, 체결시각, 누적거래대금이다.

---

## 2. 최초 장애 증상

초기 화면은 행과 각종 저장값이 표시됐지만 실시간 collector가 죽거나 멈춰 다음 상태가 반복됐다.

```text
recv/s 0
trade/s 0
top20 stale 20
연결 지연 수십~수백 초
가격·등락률 고정
```

웹서버와 worker를 재시작해도 마지막 snapshot만 다시 표시될 뿐 가격은 갱신되지 않았다. 원천 collector가 살아 있지 않으므로 서버 재시작만으로 복구할 수 없는 장애였다.

---

## 3. 원인 조사 결과

### 3.1 배제된 원인

같은 PC, 같은 32비트 Python, 같은 Kiwoom OpenAPI, 같은 `_AL` 코드에서 최소 QAx 테스트는 정상 동작했다.

```text
CommConnect=0
OnEventConnect=0
SetRealReg=0
삼성전자 _AL 30초 1,907건 수신
APP_RESULT=0
EXIT_CODE=0
```

따라서 다음은 근본 원인이 아니었다.

- Kiwoom OpenAPI 설치 불량
- 계정 로그인 실패
- `_AL` 정규장 체결 미지원
- SetRealReg 자체 실패
- 브라우저 렌더링 자체
- SSE 자체
- 64비트 worker queue 자체

### 3.2 생산용 collector 네이티브 충돌 확인

186종목 생산 collector는 로그인·등록·실제 체결 수신까지 성공한 뒤 약 4만5천 건 수신 후 종료됐다.

```text
LoginState       : connected
RealRegSucceeded : True
RegisteredCount  : 186
RealData          : 45,668
TradeReceived     : 45,291
CollectorAlive    : False
```

Windows Application Error / WER 결과:

```text
Faulting application : python.exe 3.10 32-bit
Faulting module      : ntdll.dll
Exception code       : 0xc0000374
Meaning              : native heap corruption
```

100종목으로 줄여도 기존 생산 provider 구조에서는 짧은 시간 안에 다시 종료됐다. 따라서 **UI 종목 수나 단순 종목 수가 근본 원인은 아니며, 생산 collector의 복잡한 네이티브 실행 경로가 핵심 위험 범위**로 확정됐다.

정확히 어떤 DLL 내부에서 메모리가 손상됐는지는 dump 기호 분석 없이는 단정하지 않는다. 다만 최소 QAx 경로는 정상이고 대형 provider stack에서만 재현됐으므로 운영 해결은 복잡한 provider 경로를 제거하는 방식으로 진행했다.

---

## 4. 실패한 생산 구조

기존 생산 collector는 다음 계층을 한 프로세스의 QAx owner thread 주변에 함께 설치했다.

```text
KiwoomOpenApiRealtimeProvider
+ main-thread/provider patch
+ native HWND patch
+ EventSender/PublishingStore
+ orderbook scheduler
+ strength probe
+ orderbook probe
+ opt10055 probe
+ close-metric queue
+ off-hours completion timer
+ 여러 readiness/resilience wrapper
```

이 구조는 로그인·실등록에 성공하더라도 다량 체결 처리 중 네이티브 힙 손상으로 프로세스가 사라졌다. Python traceback이나 stderr는 남지 않았고 Windows WER만 APPCRASH를 기록했다.

---

## 5. 최종 복구 구조

생산용 `realtime_v2/collector32_large_bidask.py`를 최소 QAx 핵심 경로로 교체했다.

```text
32비트 Python 메인 스레드
→ QApplication 생성
→ QAxWidget 생성
→ native HWND 확보
→ CommConnect
→ OnEventConnect 성공 callback
→ SetRealReg
→ OnReceiveRealData
→ 핵심 FID 4개 조회
→ latest-only EventSender queue
→ 64비트 worker
→ SSE
→ 브라우저 50종목 표시
```

### 5.1 실시간 수집 FID

| FID | 의미 | 우선순위 |
|---:|---|---|
| 10 | 현재가 | 최우선 |
| 12 | 등락률 | 최우선 |
| 20 | 체결시각 | 신선도 판정 |
| 14 | 누적거래대금 | 순위·후보 계산 |

이번 단계에서 collector callback에서 제거한 항목:

- 체결강도 FID
- 체결량 기반 1분 집계
- 호가잔량 FID
- 보조 TR
- strength/orderbook/close-metric queue
- off-hours completion timer

### 5.2 전송 정책

모든 틱을 순서대로 화면에 재생하지 않고 종목별 최신값을 우선한다.

```text
같은 종목에서 50ms 안에 여러 체결 발생
→ 중간 가격은 병합
→ 최신 가격·등락률 전송
```

이는 전광판의 목적에 맞는다. 모든 과거 틱을 순차 처리하면 최신 가격이 뒤로 밀리기 때문이다.

---

## 6. 최종 실측 결과

### 6.1 collector 장시간 수신

동일 PID `20220`에서 확인된 증가:

```text
13:09
RealData      21,223
TradeReceived 21,223
WorkerTrades     874
Queue             25

13:15
RealData     117,149
TradeReceived117,149
WorkerTrades   3,790
Queue             24
```

약 6분 18초 동안 약 95,926건 증가, 평균 약 254건/초 수신이며 queue는 증가하지 않았다.

### 6.2 UI 50종목 적용 후 화면 지표

```text
표시 50 / 내부 185
recv/s 37.6
trade/s 13.0
collector_q 0
sent/s 37.7
worker_q 1
stream 19ms
render 31.4ms
top20 lag 3.0s
rt 50
```

판정:

| 구간 | 값 | 평가 |
|---|---:|---|
| collector 수신 | 37.6/s | 정상 |
| collector 전송 | 37.7/s | 수신과 거의 동일 |
| collector queue | 0 | 병목 없음 |
| worker queue | 1 | 사실상 병목 없음 |
| SSE 지연 | 19ms | 매우 양호 |
| 브라우저 렌더 | 31.4ms | 양호 |
| Top20 최대 FID20 지연 | 3.0초 | 감시 가능, 경고 기준 개선 필요 |
| 실시간 표시 행 | 50 | 표시 전 종목 실시간 적용 |

### 6.3 HTS 가격·등락률 대조

동시 화면에서 다음 종목이 일치했다.

| 종목 | StockBoard | HTS | 판정 |
|---|---|---|---|
| SK이노베이션 | 109,100 / +6.03% | 109,100 / +6.03% | 일치 |
| 현대차 | 443,500 / -3.06% | 443,500 / -3.06% | 일치 |
| 삼성SDI | 443,000 / +2.07% | 443,000 / +2.07% | 일치 |
| SK텔레콤 | 175,300 / +0.06% | 175,300 / +0.06% | 일치 |
| LG전자 | 186,300 / +2.14% | 186,300 / +2.14% | 일치 |
| S-Oil | 138,900 / +5.15% | 138,900 / +5.15% | 일치 |
| 미래에셋증권 | 39,700 / -5.92% | 39,700 / -5.92% | 일치 |
| LG에너지솔루션 | 329,500 / +1.07% | 329,500 / +1.07% | 일치 |
| HMM | 19,750 / +0.05% | 19,750 / +0.05% | 일치 |
| 한화오션 | 78,500 / -3.44% | 78,500 / -3.44% | 일치 |

---

## 7. UI 50종목 정책

UI는 다음과 같이 고정한다.

```text
S1 선택행         1종목
집중 후보         20종목
표시 Pool         21~50, 30종목
브라우저 고유행   총 50종목
```

내부 후보 계산은 필터 후 약 185~186종목 전체를 유지한다.

```text
universe 185~186
→ 전체 후보 점수 계산
→ Top50
→ Top20
→ Top5
→ 브라우저에는 상위 50만 전송
```

따라서 UI 50 적용은 선발 범위를 줄이지 않는다.

### 7.1 UI 축소 효과

직전 186행 화면과 비교한 참고값:

| 항목 | 186행 표시 | 50행 표시 | 변화 |
|---|---:|---:|---:|
| 화면 행 | 186 | 50 | 약 73% 감소 |
| SSE | 약 113ms | 약 19ms | 크게 개선 |
| 렌더 | 약 106ms | 약 31ms | 크게 개선 |
| 하단 Pool DOM | 166행 | 30행 | 약 82% 감소 |

측정 조건이 완전히 동일하지는 않지만 UI 50종목의 성능 이득은 충분히 크므로 유지한다.

### 7.2 브라우저 빠른 가격 경로

브라우저 표시도 두 단계로 분리했다.

```text
현재가·등락률 셀 직접 patch  약 100ms 이벤트 주기
전체 행 무거운 render         500ms 간격
표시 Pool 전체 render          1초 간격
```

가격·등락률은 표 전체 DOM을 다시 만들지 않고 해당 셀만 갱신한다.

---

## 8. 내부 Pool과 실시간 등록 정책

현재 구분:

| 구분 | 종목 수 | 역할 |
|---|---:|---|
| 내부 universe·후보 계산 | 약 185~186 | 후보 선발 범위 유지 |
| 실시간 OpenAPI 등록 | 100 | 최소 collector 안정성 우선 |
| UI 표시 | 50 | 실전 감시 성능 우선 |

내부 185종목 중 현재 실시간 입력을 받지 않는 하위 종목은 seed·저속값 의존도가 높다. 최소 collector가 장시간 안정되면 다음 순서로 실시간 등록 범위를 확대한다.

```text
100종목 장시간 안정
→ 150종목 시험
→ 185~186종목 시험
→ UI는 계속 50종목 유지
```

단계별 합격 기준:

- 동일 collector PID 30분 이상 생존
- `RealData`, `TradeReceived`, `WorkerTrades` 계속 증가
- collector queue 지속 증가 없음
- Windows Event 1000/1001 APPCRASH 없음
- 주요 종목 HTS 가격·등락률 일치
- 재시작 3회 연속 성공

---

## 9. `stale`과 `drop` 해석

### 9.1 stale 기준

현재 화면의 stale 기준은 약 3초라 거래가 잠시 없는 종목도 stale로 잡힐 수 있다. 가격이 HTS와 일치하고 collector가 살아 있는 경우 다음 기준이 더 적절하다.

```text
0~10초   정상
10~30초  주의
30초 초과 실제 stale
```

따라서 `stale 20` 숫자만으로 collector 사망을 판정하지 않는다. 반드시 아래를 함께 본다.

- `CollectorAlive`
- `RealDataLastAt`
- `RealData` 증가량
- `TradeReceived` 증가량
- `WorkerTrades` 증가량
- HTS 실제 가격 대조

### 9.2 drop 누적값

`drop`은 누적값이다. 절대 숫자보다 짧은 구간의 증가량을 본다. collector queue가 0에 가깝고 가격이 HTS와 일치하면 과거 누적 drop 자체를 현재 병목으로 판정하지 않는다.

---

## 10. 실행·확인 명령

### 10.1 최신 브랜치 적용

```powershell
cd C:\aiTrade
git pull --ff-only origin fix/restore-stable-collector-20260713
$env:STOCKBOARD_V2_COLLECTOR_LIMIT = "100"
$env:PYTHONFAULTHANDLER = "1"
.\stockboard_v2_large.cmd restart-fast
```

### 10.2 상태 확인

```powershell
.\stockboard_v2_large.cmd status
```

정상 기준:

```text
CollectorAlive    : True
LoginState        : connected
RealRegSucceeded  : True
RegisteredCount   : 100
RealData          : 계속 증가
TradeReceived     : 계속 증가
WorkerTrades      : 계속 증가
RealDataLastAt    : 현재 시각과 근접
Queue             : 낮고 지속 증가하지 않음
LastError         : 비어 있음
```

### 10.3 현재 생산 collector 식별 로그

```text
collector_mode=minimal_qax_critical_v1
qt_event_loop=exec_ minimal_callback_fids=10,12,20,14
collector_ready=True registered_count=100
```

구형 collector 로그가 나오면 최신 코드가 적용되지 않은 것이다.

---

## 11. 남은 작업

우선순위 순서:

1. 최소 collector 30분 이상 및 재시작 3회 안정 검증
2. 실시간 등록 150종목 시험
3. 실시간 등록 185~186종목 시험
4. stale 경고 기준 10초/30초 단계화
5. 순간강도는 별도 저부하 경로로 복원 검토
6. 잔량비·5분강도·대량체결은 저속 snapshot 또는 별도 프로세스로 분리
7. 다음 정규장 09:00~09:10 개장 폭주 검증
8. 자동매매 연결은 위 검증 완료 뒤 판단

**금지:** 안정화된 가격 핵심 collector에 보조 TR·호가·강도 timer를 한 번에 다시 합치지 않는다. 기능은 반드시 한 계층씩 복원하고 각 단계에서 장시간 생존을 확인한다.

---

## 12. 변경 이력 요약

| 단계 | 결과 |
|---|---|
| 복잡한 provider thread 복원 | `QApplication was not created in main thread` 후 종료 |
| main-thread light collector | 로그인·등록·수신 성공 후 `ntdll.dll / 0xc0000374` APPCRASH |
| 186 → 100종목 축소 | 기존 provider stack에서는 여전히 종료, 종목 수 단독 원인 배제 |
| 최소 QAx critical collector | 10만 건 이상 연속 수신, 동일 PID 생존, queue 안정 |
| UI 186 → 50종목 | SSE·렌더 지연 크게 감소, 내부 후보 계산 185 유지 |
| 가격·등락률 셀 fast patch | 화면 체감 갱신 속도 개선, HTS 대조 일치 |

---

## 13. 최종 현재 판단

```text
핵심 현재가·등락률 수집       복구
누적거래대금 수집             복구
collector queue               정상
worker/SSE/browser             정상
UI 50종목                     유지
내부 후보 Pool 185~186        유지
실시간 등록 100               현재 안전 검증값
보조 지표 실시간성            미보증
자동매매 실전 연결            보류
PR #35                        Draft 유지
```

현재 단계는 “장애 미해결”이 아니라 **가격 핵심 경로 복구 성공, 확대·장시간 검증 중**으로 판정한다.
