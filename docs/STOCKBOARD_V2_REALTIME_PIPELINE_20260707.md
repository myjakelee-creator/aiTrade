# StockBoard v2 실시간 파이프라인 정리 20260707

작성 목적: 기존 StockBoard 기준 문서에 섞지 않고, 2026-07-07에 새로 만든 StockBoard v2 실시간 구조와 운영 원칙을 별도 문서로 고정한다.

## 1. 현재 결론

기존 StockBoard v0.3.x는 UI와 기능은 발전했지만 장개시 가격 정합성, 거래대금 정합성, 장상태 전환에서 반복 문제가 있었다. v2는 기존 파일을 직접 뜯어고치는 대신 별도 포트와 별도 프로세스로 만든 장개시 실전 검증용 실시간 보드이다.

핵심 목표는 09:00~09:05 거래 폭탄 구간에서 HTS 0186과 현재가, 등락률, 거래대금이 맞는지 확인하는 것이다.

## 2. 실행 파일과 주소

| 항목 | 값 |
|---|---|
| 실행 파일 | `stockboard_v2_live.cmd` |
| 화면 주소 | `http://127.0.0.1:8765/` |
| worker 포트 | `8765` |
| collector → worker TCP 포트 | `8710` |
| branch | `hot-priority-integrated-20260630` |

기본 실행:

```powershell
cd C:\aiTrade
git pull
.\stockboard_v2_live.cmd stop
.\stockboard_v2_live.cmd start
```

상태 확인:

```powershell
cd C:\aiTrade
.\stockboard_v2_live.cmd status
```

## 3. 구조

```text
32bit Kiwoom collector
→ TCP JSON event
→ 64bit guarded worker
→ SSE stream 100ms micro-batch
→ docs/stockboard_v2.html
```

| 계층 | 파일 | 역할 |
|---|---|---|
| universe builder | `realtime_v2/build_universe.py` | ka10032 기준 종목 universe 생성, ETF/우선주 등 tradable master 필터, 전일 거래대금 seed 결합 |
| 32bit collector | `realtime_v2/collector32.py` | Kiwoom OpenAPI 32bit 실시간 이벤트 수집, 계산하지 않고 worker로 전달 |
| base worker | `realtime_v2/worker64.py` | 상태 저장, SSE stream, snapshot API, event log batch writer |
| guarded worker | `realtime_v2/worker64_guarded.py` | seed fallback, 누적값 역행 방어, 장상태 정책 결합, 체결강도/잔량비 일중 복원 |
| 장상태 정책 | `realtime_v2/market_session.py` | 프리장, 동시호가, 정규장, 애프터장, 휴장일, 수능 지연 등 판단 |
| UI | `docs/stockboard_v2.html` | 실시간 테이블, 정렬, HTS 연동, 코드 복사, 열 폭 조절, 가로 스크롤 |
| HTS AHK bridge | `scripts/stockboard_kiwoom_link_v1.ahk` | StockBoard v2 clipboard command를 읽어 Kiwoom HTS Edit6에 종목코드 전달 |
| 달력 설정 | `config/stockboard_market_calendar.json` | 공휴일, 특별일, 수능 지연 개장 설정 |

## 4. 현재 UI 열

```text
순위 | 전일 | 종목명 | 현재가 | 등락률 | 거래대금 | 대금비 | 체결강도 | 잔량비 | 대량건
```

표시하지 않지만 내부에 유지하는 값:

```text
종목코드, 누적량, 체결량, 프로그램 순매수, 전일 거래대금, row_source, source_code, FID20 lag, 장상태
```

종목명 클릭 시 Kiwoom HTS 연동 명령을 보낸다. Shift+종목명 클릭 시 종목코드만 클립보드에 복사한다. 열 제목 클릭 시 정렬/역정렬되며, 정렬 상태는 localStorage에 저장된다. 열 경계 드래그로 폭 조절, 더블클릭으로 자동 폭 조절된다.

## 5. HTS 연동 정책

v2의 종목명 클릭은 단순 종목코드 복사가 아니라 HTS 연동 명령이다.

```text
브라우저 종목명 클릭
→ clipboard에 SBV2|sequence|005930 형태의 고유 명령 기록
→ AutoHotkey bridge가 command sequence를 감지
→ Kiwoom HTS Main/Edit6에 6자리 코드 입력
→ readback 검증 후 해당 Edit6 control에만 Enter 전송
```

중요 원칙:

| 항목 | 정책 |
|---|---|
| 중복 클릭 | 같은 종목을 다시 클릭해도 sequence가 다르므로 매번 처리 |
| 일반 clipboard 6자리 | fallback으로 허용 |
| HTS 창 제어 | WinActivate 금지. foreground window에 키 전송 금지 |
| Enter 전송 | readback이 성공한 Edit6 control HWND에만 전송 |
| 상태 파일 | `data/runtime/stockboard_v2/hts_link_status.txt` |
| bridge 시작 | `stockboard_v2_live.cmd start`가 AHK bridge도 함께 시작 |
| bridge 단독 시작 | `stockboard_v2_live.cmd ahk` 또는 메뉴 5 |

HTS가 연동되지 않으면 먼저 아래를 확인한다.

```powershell
cd C:\aiTrade
.\stockboard_v2_live.cmd status
```

확인할 항목:

```text
AHK_RUNNING
AHK_PIDS
AHK_LAST_STATUS
```

## 6. 장상태 정책

기본 시간대:

| phase | 기본 시간 | 정책 |
|---|---|---|
| before_market | 08:00 전 | seed/마지막값 유지 |
| premarket | 08:00~08:30 | 실시간 수용, 체결 없으면 seed 유지 |
| opening_call | 08:30~09:00 | 실시간 수용, 빈칸 방지 |
| regular | 09:00~15:20 | 실시간 우선, 누적값 역행 방어 |
| closing_call | 15:20~15:30 | 실시간 수용, 체결 없으면 seed 유지 |
| after_wait | 15:30~15:40 | 정규장 마감/애프터 대기, 마지막값/seed 유지 |
| aftermarket | 15:40~20:00 | 실시간 수용 |
| closed | 20:00 이후 | seed/마지막값 유지 |
| weekend/holiday | 주말/공휴일 | 휴장 상태, seed 유지 |

수능일 등 지연 개장일은 `config/stockboard_market_calendar.json`의 `special_days`에 넣는다.

예시:

```json
{
  "special_days": {
    "YYYYMMDD": {
      "reason": "csat_delayed_open",
      "open_delay_minutes": 60
    }
  }
}
```

공휴일은 `holidays`에 `YYYYMMDD`로 추가한다.

## 7. 데이터 정책

| 값 | 정책 |
|---|---|
| 현재가/등락률/거래대금 | 실시간 이벤트가 오면 realtime 값 우선, 없으면 universe seed 값 표시 |
| 거래대금 | 장중 누적값. 이미 수용한 realtime 값보다 감소하면 stale/역행 이벤트로 보고 버림 |
| 누적거래량 | 이미 수용한 realtime 값보다 감소하면 버림 |
| FID20 지연 | 지연 자체만으로는 버리지 않고 warning으로 기록. 과거 시간 역행 또는 누적값 역행 시 버림 |
| 대금비 | 당일 거래대금 / 전일 정규장 장마감 거래대금 |
| 전일 | 전일 정규장 장마감 거래대금 순위 대비 당일 순위 변화 |
| 체결강도 | 마지막 실시간 값을 `daily_state_YYYYMMDD.json`에 저장하고 재접속 시 복원 |
| 잔량비 | 마지막 호가/잔량 값을 `daily_state_YYYYMMDD.json`에 저장하고 재접속 시 복원 |
| 대량건 | 5천만원 이상 체결 누적 net count |
| 프로그램 순매수 | worker background updater에서 수집, 화면에는 tooltip/내부값 중심 |

## 8. 전일 거래대금 정책

전일 통합 거래대금은 현재 코드 기준으로 **확정 원천이 아직 없다**. `ka10032` 당일 거래대금상위는 현재 당일 universe/seed 생성에 쓰고, 전일값은 `ka10086` 일봉 row의 전일 거래대금에서 가져온다. `ka10086`은 개별 종목 일봉 성격이라 정규장 장마감 기준으로 해석한다.

현재 전일 거래대금 우선순위:

```text
1. data/runtime/previous_trade_value_YYYYMMDD.json cache
2. ka10086 기본 6자리 종목코드 조회 = 정규장 장마감 기준 우선
3. ka10086 종목_AL 조회 = 기본 코드 조회 실패 시 fallback only
```

운영 해석:

```text
NXT 미거래 종목: 정규장 장마감 전일값과 실제 전일값이 거의 같으므로 대금비가 잘 맞는다.
NXT 거래 종목: 프리/애프터/NXT 거래분이 전일 분모에서 빠질 수 있으므로 대금비가 과대 표시될 수 있다.
```

전일 통합 거래대금 조회가 Kiwoom OpenAPI에서 확인되면 정책은 바꿀 수 있다. 그 경우 우선순위는 다음이 맞다.

```text
1. 검증된 전일 통합 거래대금 원천
2. ka10086 기본 6자리 종목코드 정규장 장마감 값
3. ka10086 _AL fallback
```

당일 거래대금을 저장해서 다음날 전일값으로 쓰는 방식은 보조 후보로 보류한다. 저장 실패, 휴장/날짜 전환, universe 변경, NXT/정규장 코드 변동, 장중 재시작에 취약하므로 현재는 1차 원천으로 쓰지 않는다.

## 9. 재접속/복원

아래 값은 일중 상태 파일에 저장된다.

```text
data/runtime/stockboard_v2/daily_state_YYYYMMDD.json
```

복원 대상:

```text
대량건
프로그램 순매수
체결강도
잔량비
매수/매도 잔량
최우선 매수/매도 호가
```

재시작 시 같은 날짜이면 복원된다. NXT 거래가 없는 종목은 정규장 마감 후 마지막 정규장 값이 유지되고, NXT/애프터장 거래가 있는 종목은 애프터장 중 들어온 마지막 값이 덮어쓴다. 단, `data/runtime/`은 Git 추적 대상이 아니다.

## 10. 성능 정책

| 항목 | 정책 |
|---|---|
| collector | 32bit OpenAPI 이벤트를 가능한 가볍게 수신하고 TCP로 전달 |
| worker | 64bit에서 최신 상태 유지, 계산과 표시 상태 관리 |
| stream | SSE `/api/v2/stream`, 100ms micro-batch |
| event log | 매 이벤트 직접 쓰기 금지. AsyncEventLogger가 batch write |
| UI | EventSource stream 기반. 끊기면 1초 polling fallback |

## 11. 진단값

상단 또는 status에서 확인할 값:

```text
stream latency
q / event_log_queue_size
trades
events
DROPPED_TRADE_COUNT
LAST_DROPPED_TRADE
lagged_trade_warning_count
market_phase
market_phase_label
row_source
source_code
AHK_RUNNING
AHK_LAST_STATUS
```

장개시 정상 기준:

```text
stream 100~1000ms 중심
q가 지속적으로 증가하지 않음
trades가 빠르게 증가
삼성전자, SK하이닉스, 삼성전기 가격/등락률/거래대금이 HTS 0186과 일치 또는 거의 근접
거래대금이 장중 비정상적으로 감소하지 않음
상위 20개가 지속적으로 흐려지지 않음
종목명 1회 클릭으로 HTS가 해당 종목으로 전환
```

## 12. 현재 남은 리스크

| 리스크 | 설명 | 대응 |
|---|---|---|
| Kiwoom 원천 지연 | OpenAPI 이벤트 자체가 늦게 올 수 있음 | FID20 lag와 row_source 확인 |
| AL/NX/일반 코드 차이 | 일부 종목은 NXT/정규장 등록 기준이 다를 수 있음 | source_code 확인 후 등록 정책 조정 |
| 장개시 폭탄 | 09:00~09:05 이벤트 폭주 | stream/q/trades 확인 |
| 휴장일/특별일 누락 | 공휴일/수능일은 config 갱신 필요 | `config/stockboard_market_calendar.json` 관리 |
| HTS control 변경 | Kiwoom 화면/버전에 따라 Edit6가 달라질 수 있음 | `AHK_LAST_STATUS`와 AHK script TargetControl 확인 |
| NXT 전일분 미반영 | 전일 분모가 정규장 장마감 기준이므로 NXT 거래종목 대금비가 과대 가능 | 현재 허용. 검증된 통합 전일 원천 확보 시 전환 |

## 13. 삭제/정리된 구 v1 sidecar 파일

v2가 프로그램 순매수와 속도 진단을 자체 구조로 흡수했으므로 아래 v1 임시 sidecar/recorder 파일은 제거했다.

```text
stockboard_live_with_program_net.cmd
scripts/stockboard_program_net_snapshot.py
scripts/run_stockboard_program_net_snapshot.cmd
scripts/start_stockboard_program_net_sidecar_hidden.ps1
scripts/stop_stockboard_program_net_sidecar.ps1
scripts/stop_stockboard_program_net_sidecar.cmd
scripts/stockboard_speed_recorder.py
scripts/run_stockboard_speed_recorder.cmd
```

## 14. 내일 장개시 운영 원칙

내일 장개시 전에는 UI 장식 추가 금지. 가격 정합성, 속도, 장상태 정책 안정성, HTS 1회 클릭 연동만 본다.

검증 절차:

```powershell
cd C:\aiTrade
git pull
.\stockboard_v2_live.cmd stop
.\stockboard_v2_live.cmd start
.\stockboard_v2_live.cmd status
```

브라우저:

```text
http://127.0.0.1:8765/
Ctrl+F5
```

09:00~09:05 중점 확인:

```text
1. HTS 0186 대비 현재가/등락률/거래대금
2. stream latency
3. q 증가 여부
4. trades 증가 속도
5. 거래대금 감소 여부
6. row_source가 seed에서 realtime으로 정상 전환되는지
7. 종목명 클릭 한 번으로 HTS가 정확히 해당 종목으로 전환되는지
8. 대금비는 전일 정규장 장마감 분모 기준으로 해석한다
9. 재접속 후 체결강도/잔량비가 당일 마지막 값으로 유지되는지
```
