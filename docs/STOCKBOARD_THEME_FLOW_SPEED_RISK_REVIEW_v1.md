# ThemeBoard 공격매수 흐름 기능 적용 시 예상 속도 문제 검토 v1

작성 기준: 2026-07-16  
대상 저장소: `myjakelee-creator/aiTrade`  
관련 문서: `docs/STOCKBOARD_THEME_FLOW_METRICS_HANDOVER_v1.md`  
목적: ThemeBoard에 공격매수 흐름 지표를 추가할 때 StockBoard 실시간 가격 수신과 개장 성능에 미칠 위험을 구현 전에 검토한다.

---

## 1. 검토 목적

ThemeBoard에 다음 지표를 추가하는 방안을 검토 중이다.

- 공격매수대금
- 공격매도대금
- 매수우위
- 현재 구간 대비 직전 동일 구간의 거래대금 가속
- 상승 참여
- 집중도
- 시간 구간 선택: 최근 10초·1분·5분·15분

기존 ThemeBoard의 상승탄력·거래대금 쏠림 기능은 유지하고, 별도의 **공격매수 흐름 관찰 화면**으로 추가할 예정이다.

구현 전에 StockBoard 실시간 가격 수신과 개장 시 성능에 미칠 영향을 우선 검토해야 한다.

---

## 2. 핵심 결론

예상되는 성능 위험은 다음 순서다.

```text
1순위: 32비트 Kiwoom collector의 체결 callback 부하 증가
2순위: callback 처리 지연으로 인한 가격 수신 freshness 악화
3순위: 64비트 worker의 종목별 31분 history 저장과 구간 계산
4순위: ThemeBoard의 1초 단위 DOM 재렌더링과 SSE payload 증가
```

Theme Flow 계산 자체는 비교적 가볍다.

가장 큰 위험은 정확한 공격매수·매도 계산을 위해 체결수량 또는 체결 방향 관련 FID를 **모든 체결 callback마다 추가 조회해야 한다는 점**이다.

---

## 3. 현재 생산 collector 기준

현재 안정화된 생산 collector는 체결 callback마다 다음 값을 조회한다.

```text
FID10 현재가
FID12 등락률
FID20 체결시각
```

누적거래대금 `FID14`는 모든 callback마다 읽지 않고 종목별 약 500ms 간격으로 샘플링한다.

현재 생산 경로에서는 다음 기능이 비활성화돼 있다.

```text
FID15 체결수량 매 callback 조회
대량체결 aggregate
실시간 호가 기반 방향 판정
```

따라서 현재 collector는 사실상 **가격 전용 최소 QAx collector**다.

---

## 4. 예상 문제 1 — GetCommRealData 호출 증가

정확한 공격매수대금을 계산하려면 개별 체결의 체결수량이 필요하다.

FID15를 추가하면 매 체결 callback의 필수 COM 조회 횟수는 다음처럼 증가한다.

```text
현재: FID10 + FID12 + FID20 = 3회
변경: FID10 + FID12 + FID20 + FID15 = 4회
```

호출 횟수만 보면 체결 callback의 `GetCommRealData()` 호출이 약 33% 증가한다.

실제 영향 예상치는 다음과 같다.

| 적용 범위 | 예상 collector 부하 증가 |
|---|---:|
| 대표 종목 5~10개 진단 | 매우 작음 |
| 테마 구성종목 약 60개 | 평상시 약 15~30% |
| 실시간 등록 100종목 전체 | 평상시 약 25~40% |
| 09:00 개장 폭주 | 위 수치보다 더 커질 수 있음 |

위 수치는 구현 전 예상치이며 실제 Kiwoom COM 호출시간과 장초반 체결건수에 따라 달라질 수 있다.

---

## 5. 예상 문제 2 — 가격 수신 지연

체결 callback은 가격 수신과 공격매수 흐름 계산이 같은 경로를 공유하게 된다.

callback 처리가 체결 발생 속도를 따라가지 못하면 다음 순서로 문제가 발생할 수 있다.

```text
FID 추가 조회
→ callback 처리시간 증가
→ 다음 체결 이벤트 처리 대기
→ 현재가·등락률 반영 지연
→ price_age_sec 증가
→ latest-only overwrite 증가
→ 화면 가격이 HTS보다 늦게 움직임
```

개장 시에는 종목별 체결이 동시에 폭증하므로 평균 CPU 사용률보다 **callback 최대 처리시간과 이벤트 밀림**이 더 중요하다.

가장 우려되는 결과는 ThemeBoard 기능 때문에 StockBoard의 핵심인 현재가 수신 속도가 느려지는 것이다.

---

## 6. 예상 문제 3 — collector 안정성 회귀

이전에는 FID15 체결수량과 대량체결 aggregate가 결합된 collector가 비정상 종료한 이력이 있다.

FID15 하나가 직접 원인이라고 확정할 수는 없지만, 다음 결합은 고위험으로 봐야 한다.

```text
FID15 매 체결 조회
+ 대량체결 판정
+ 대량체결 누적 aggregate
+ latest-only event 병합
```

따라서 공격매수 흐름 개발 시 다음을 한 번에 적용하면 안 된다.

```text
FID15
호가 조회
대량체결
새 점수모델
새 ThemeBoard 화면
```

각 요소를 분리해 단계별로 검증해야 한다.

---

## 7. 예상 문제 4 — 호가 기반 방향 판정 부하

공격매수 방향을 판단하기 위해 체결가와 최우선 매수·매도호가를 매번 비교하는 방안이 있다.

체결 callback에서 호가 FID 두 개를 추가 조회한다면:

```text
현재 3회
+ 체결수량 1회
+ 매수호가 1회
+ 매도호가 1회
= 총 6회
```

현재 대비 COM getter 호출량이 거의 두 배가 될 수 있다.

예상 영향:

- collector CPU 급증
- QAx callback 점유시간 증가
- 개장 시 가격 이벤트 지연
- 호가와 체결시각 불일치에 따른 방향 오분류
- collector 종료 가능성 증가

따라서 생산 callback에서 매 체결마다 호가까지 조회하는 방식은 권장하지 않는다.

권장 우선순위:

```text
1. HTS와 검증된 직접 방향·부호 사용
2. 직전 체결가 대비 tick rule
3. 판정 불가 시 unknown
```

호가 기반 방식은 소수 종목 진단용으로만 사용하는 것이 안전하다.

---

## 8. 예상 문제 5 — latest-only 구조에서 체결 누락

현재 sender는 화면용 가격 이벤트를 종목별 최신 한 건으로 합치는 latest-only 구조다.

공격매수 흐름까지 단순히 latest-only 이벤트에 넣으면 50ms 사이에 발생한 중간 체결이 사라질 수 있다.

예:

```text
50ms 동안 한 종목에서 체결 10건 발생
화면용 가격 이벤트는 마지막 1건만 전송
```

공격매수·매도 금액은 마지막 체결 한 건만으로 계산하면 안 된다.

필수 구조:

```text
collector callback에서 모든 체결을 방향별로 합산
→ 50ms micro-batch delta 생성
→ 최신 가격 이벤트 1건에 합산값 첨부
```

필요 필드 예:

```text
flow_buy_value_delta
flow_sell_value_delta
flow_unknown_value_delta
flow_trade_count_delta
flow_sequence
```

중간 체결을 집계하기 전에 latest-only로 덮어쓰면 가격은 정상이어도 Theme Flow 값은 크게 틀릴 수 있다.

---

## 9. 예상 문제 6 — sender queue와 재전송

흐름 필드가 추가되면 이벤트 크기와 직렬화 비용이 증가한다.

로컬 TCP 대역폭 자체는 큰 문제가 아닐 가능성이 높지만, 다음 항목은 검증해야 한다.

- `pending_trade_count`
- `pending_flow_code_count`
- `coalesced_trade_overwrite_count`
- `sent_per_sec`
- 재접속 시 unsent event 재큐잉
- flow delta 중복 적용 여부
- flow sequence gap 여부

특히 전송 실패 후 이벤트가 재큐잉될 때 기존 flow delta와 새 flow delta가 중복 합산되면 공격매수대금이 부풀려질 수 있다.

가격 latest-only 재전송과 flow delta 재전송은 같은 규칙을 사용하면 안 될 수 있다.

---

## 10. 예상 문제 7 — 종목별 31분 history 메모리

10초·1분·5분·15분의 현재 구간과 직전 구간을 모두 계산하려면 약 31분 이상의 history가 필요하다.

100종목을 초 단위로 저장하는 경우:

```text
100종목 × 1,860초 = 186,000개 시점
```

각 시점에 다음 값을 저장할 수 있다.

```text
buy cumulative
sell cumulative
unknown cumulative
trade count
timestamp
```

조밀한 ring buffer나 숫자 배열을 사용하면 추가 메모리를 약 10~20MB 수준으로 제한할 수 있다.

반대로 각 초 데이터를 Python dict와 tuple 객체로 저장하면 객체 오버헤드 때문에 50~120MB 이상으로 증가할 가능성이 있다.

권장 조건:

```text
고정 길이 ring buffer
누적 prefix 값
초 단위 정수 index
종목당 1개 배열
31분 이전 데이터 즉시 제거
```

---

## 11. 예상 문제 8 — 네 시간 구간 중복 계산

시간 버튼은 다음 네 개다.

```text
10초
1분
5분
15분
```

각 시간 구간마다 별도 worker를 만들거나 history 전체를 다시 순회하면 계산량이 불필요하게 커진다.

비권장:

```text
10초 worker
1분 worker
5분 worker
15분 worker
```

권장:

```text
1초마다 공용 Theme Flow 계산 1회
→ 10초·1분·5분·15분 결과를 동시에 생성
→ 공용 cache에 저장
→ 브라우저는 선택한 구간만 표시
```

누적 prefix 구조를 사용하면 각 구간 계산은 history 합산이 아니라 몇 번의 뺄셈으로 끝낼 수 있다.

현재 테마 마스터 규모에서는 잘 구현할 경우 Theme Flow 계산시간은 대략 1~5ms/초 수준으로 예상된다.

---

## 12. 예상 문제 9 — 기존 ThemeBoard projection과의 중복 계산

현재 ThemeBoard에는 이미 다음 계산이 존재한다.

- 상승탄력 순위
- 거래대금 쏠림 순위
- 1분·5분 거래대금
- 평균등락률
- 상승 확산도
- 대금비
- 주도주
- 프로그램·대량체결 보조값

공격매수 흐름을 기존 projection 안에 무리하게 추가하면 하나의 builder가 지나치게 커질 수 있다.

위험:

- ThemeBoard 전체 계산시간 증가
- 기존 상승탄력 화면까지 지연
- 오류 발생 시 모든 ThemeBoard 결과가 갱신되지 않음
- 성능 원인 구분 어려움
- 기존 테스트 범위 확대

권장:

```text
기존 ThemeProjection 유지
새 ThemeFlowProjection 별도
공용 ThemeFlowStore 사용
같은 BoardDataHub 또는 별도 cache에 결과 게시
```

새 기능 실패 시 기존 ThemeBoard는 계속 동작하는 fail-open 구조가 필요하다.

---

## 13. 예상 문제 10 — 1초 SSE payload 증가

공격매수 흐름 summary에 다음 필드를 추가하면 payload가 커진다.

- 현재 공격매수·매도·unknown
- 직전 구간 값
- 매수우위
- 방향확정률
- 가속
- 집중도
- 상승 참여
- 시간범위
- 상태와 신뢰도

테마 10개 규모에서는 큰 문제가 아닐 가능성이 높다.

그러나 모든 시간 구간의 상세 구성종목 값을 한 payload에 포함하면 불필요하게 커질 수 있다.

권장:

```text
일반 SSE
→ 선택 시간 구간의 테마 summary만 전송

상세 API
→ 현재 선택한 테마의 구성종목만 전송

10초·1분·5분·15분 전체 상세
→ 브라우저로 동시 전송 금지
```

---

## 14. 예상 문제 11 — 브라우저 전체 재렌더링

현재 ThemeBoard는 1초마다 테마 카드와 순위표를 다시 렌더링한다.

공격매수 화면까지 숨겨진 상태에서 계속 렌더링하면 DOM 작업이 증가한다.

위험:

- 레이더 카드 전체 재생성
- 테마 순위표 전체 재생성
- 선택 테마 상세 API 반복 호출
- 현재 선택 위치·스크롤 상태 흔들림
- HTS 종목 클릭 event 재바인딩
- 장시간 사용 시 메모리 증가

권장:

```text
현재 선택된 화면만 갱신
숨겨진 화면은 DOM 갱신 금지
테마 구조가 같으면 숫자 셀만 patch
선택 상세만 갱신
시간 버튼 변경 시 서버 cache 결과 즉시 표시
```

브라우저 영향은 collector 영향보다 작지만, 전체 재렌더링이 누적되면 화면 끊김이 발생할 수 있다.

---

## 15. 예상 문제 12 — 여러 브라우저 연결

Theme Flow 계산이 API 요청이나 브라우저 연결마다 실행되면 브라우저 수에 비례해 계산량이 증가한다.

잘못된 구조:

```text
브라우저 1개 → 1초당 계산 1회
브라우저 3개 → 1초당 계산 3회
```

필수 구조:

```text
서버 공용 계산 1회
→ 공용 cache 저장
→ 모든 SSE 연결이 같은 결과 재사용
```

브라우저 수가 늘어나도 projection 계산 횟수는 증가하지 않아야 한다.

---

## 16. 예상 문제 13 — 정규장·시간외 history 혼합

정규장과 시간외 체결을 같은 history에 넣으면 다음 날 10초·1분·5분·15분 비교값이 잘못될 수 있다.

필수 처리:

- 09:00 정규장 시작 시 새 session
- 15:30 정규장 흐름 고정
- NXT 시간외 흐름과 정규장 흐름 분리
- 거래일 변경 시 history 초기화
- 휴일·주말에는 이전 정규장 snapshot만 표시
- 장전 예상체결을 정규장 공격매수로 집계하지 않음

세션 전환 시 history 초기화나 복원 작업이 한꺼번에 실행되면 짧은 CPU·디스크 burst가 발생할 수도 있다.

---

## 17. 예상 문제 14 — 테마 중복 membership

한 종목은 여러 테마에 포함될 수 있다.

예:

```text
SK하이닉스
→ HBM
→ 종합반도체
→ 메모리
```

종목의 체결은 Store에 한 번만 저장해야 한다.

테마 projection에서 membership에 따라 여러 테마로 배분하거나 합산해야 한다.

잘못된 구조:

```text
collector 단계에서 테마별 체결 복제
```

이렇게 하면 collector 계산량과 전송량이 membership 수만큼 증가한다.

권장:

```text
collector·Store
→ 종목별 한 번 저장

Theme Flow projection
→ 테마 membership을 읽어 집계
```

---

## 18. Flow Lite와 Full Flow 속도 비교

### 18.1 Flow Lite

현재 `FID14 누적거래대금` 차분만 사용한다.

계산 가능:

- 현재·직전 전체 거래대금
- 가속
- 대금가속
- 상승 참여
- 집중도

계산 불가:

- 정확한 공격매수
- 정확한 공격매도
- 매수우위
- 방향확정률

예상 영향:

```text
32비트 collector 추가 부하: 거의 0%
64비트 worker CPU: 매우 작음
추가 메모리: 약 5~15MB
가격 수신 영향: 사실상 없음
```

### 18.2 Full Flow

FID15 또는 검증된 방향 원천을 매 체결마다 추가로 읽는다.

예상 영향:

```text
테마 종목만 적용
→ collector 부하 약 15~30% 증가 가능

100종목 전체 적용
→ 약 25~40% 증가 가능

개장 폭주
→ 위 수치 이상 발생 가능
```

호가 FID까지 매 체결 조회하는 방식은 60~100% 수준의 callback 부하 증가 가능성이 있어 비권장이다.

---

## 19. 권장 구현 순서

### D0 — 소수 종목 방향 진단

- 대표 종목 5~10개
- FID15 단독 추가
- 대량체결 aggregate 금지
- 호가 FID 동시 추가 금지
- 생산 ThemeBoard 표시 금지
- HTS 체결 방향 대조
- collector PID 장시간 생존 확인

### D1 — Flow Lite

- 기존 FID14 사용
- 전체대금·가속·상승 참여·집중도만 구현
- 공격매수·매수우위는 `-` 또는 `진단중`
- 가격 수신 회귀 확인

### D2 — 제한적 Full Flow

- 검증된 방향 원천만 적용
- 테마 구성종목과 실시간 등록종목의 교집합만 대상
- 50ms 방향별 금액 합산
- 별도 ThemeFlowStore 연결

### D3 — ThemeBoard 관찰 UI

- 기존 상승탄력·거래대금 쏠림 유지
- 공격매수 흐름은 세 번째 독립 화면
- 후보5·StockBoard 선발점수와 연결 금지

### D4 — 09:00 실전 검증

- 개장 09:00~09:10
- collector CPU·PID·callback 지연
- price freshness
- queue·drop·overwrite
- Theme Flow 계산시간
- 브라우저 렌더시간 확인

---

## 20. 성능 중단 기준 제안

다음 중 하나라도 발생하면 Full Flow를 즉시 비활성화할 수 있어야 한다.

| 항목 | 중단 또는 재검토 기준 |
|---|---:|
| collector CPU | 기존 대비 평상시 20% 이상 지속 증가 |
| 개장 peak CPU | 기존 대비 30% 이상 증가 |
| `price_age_sec` | 기존 기준보다 10% 이상 지속 악화 |
| callback 처리시간 | 체결 간격보다 긴 상태 반복 |
| sender pending queue | 1초 내 정상 복귀하지 못함 |
| collector PID | 비정상 종료 또는 재시작 |
| Theme Flow p95 | 10ms 초과 |
| Theme Flow max | 30ms 초과 반복 |
| 추가 메모리 | 25MB 초과 |
| 브라우저 long task | 50ms 이상 반복 |
| StockBoard 가격 | HTS 대비 눈에 띄는 지연 발생 |

기능 스위치도 필요하다.

```text
STOCKBOARD_THEME_FLOW_ENABLED=0
```

OFF 시에는 다음이 즉시 중단돼야 한다.

```text
추가 FID 조회
flow aggregate
ThemeFlowStore 갱신
Theme Flow projection
ThemeBoard 공격매수 화면 갱신
```

기존 StockBoard와 기존 ThemeBoard는 그대로 동작해야 한다.

---

## 21. StockBoard 설계실에 요청하는 검토 사항

다음 항목을 중심으로 검토한다.

1. 현재 최소 collector에 FID15를 추가해도 가격 수신 안정성을 유지할 수 있는가.
2. FID15를 같은 collector에 넣는 것과 별도 진단 collector를 두는 것 중 어느 쪽이 안전한가.
3. 테마 구성종목 약 60개만 FID15 대상으로 제한할 수 있는가.
4. 체결 callback에서 호가를 읽지 않고 직접 방향 또는 tick rule만 사용하는 것이 적절한가.
5. flow delta를 latest-only sender에서 손실 없이 합산·재전송할 구조는 무엇인가.
6. ThemeFlowStore를 기존 State에 포함할지 독립 객체로 둘지.
7. 네 시간 구간을 한 번에 계산하는 prefix/ring buffer 설계가 적절한가.
8. 기존 ThemeProjection과 ThemeFlowProjection을 완전히 분리해야 하는가.
9. 09:00 성능 중단 기준이 충분한가.
10. Flow Lite를 먼저 적용하고 Full Flow를 나중에 승인하는 순서가 적절한가.

---

## 22. 현재 권고안

속도와 안정성을 최우선으로 보면 다음 순서가 가장 안전하다.

```text
1. Flow Lite 먼저 구현
2. D0에서 FID15 소수 종목 독립 진단
3. 개장 성능과 collector 생존 확인
4. 테마 구성종목에만 제한적 Full Flow 적용
5. 공격매수 흐름을 ThemeBoard 관찰 화면으로만 노출
6. 충분한 실전·리플레이 검증 전에는 후보5 점수와 연결하지 않음
```

Theme Flow의 Python 계산이나 ThemeBoard UI 자체는 큰 병목이 아닐 가능성이 높다.

전체 시스템의 성능과 안정성을 결정하는 요소는 **32비트 Kiwoom callback에 체결수량·방향 판정을 얼마나 무겁게 추가하느냐**다.

---

## 23. 문서 상태

- 설계·성능 위험 검토 문서다.
- 구현 승인 문서가 아니다.
- 실제 부하 수치는 구현 전 예상치다.
- D0 진단과 09:00~09:10 실측 결과로 수치를 다시 갱신해야 한다.
- 기존 가격 원천, 선발모델, ThemeBoard 기존 순위, 런처, AHK 동작은 이 문서 작성으로 변경하지 않는다.
