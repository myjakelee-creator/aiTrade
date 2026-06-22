# STOCKBOARD_PROGRAM_NET_HANDOVER_v1.0

작성일: 2026-06-22

---

# 목적

StockBoard에서 발생한

* 외합(억)
* 프로(억)
* 큰손
* 잔량비
* 1분강도
* 당일강도
* 등급

반복 렌더링 문제를 해결하고,

현재 남아있는

**program_net 미표시 문제**

를 Backend 진단 단계로 이관한다.

---

# 1. 해결 완료된 문제

## 증상

다음 열에서 반복 패턴 발생.

### 외합(억)

2~9번

↓

10번 이후 반복

---

### 프로(억)

2~9번

↓

10번 이후 반복

---

### 큰손

2~9번

↓

10번 이후 반복

---

### 잔량비

2~9번

↓

10번 이후 반복

---

### 1분강도

2~9번

↓

10번 이후 반복

---

### 당일강도

2~9번

↓

10번 이후 반복

---

### 등급

1~10번

↓

11번 이후 반복

---

# 2. 직접 원인

원인 함수

```text
normalizeRankingRow(row, fallbackIndex)
```

실제 데이터가 없으면

```text
fallbackIndex % fallbackValues.length
```

방식으로

fallback 배열을 반복 주입.

---

# 반복 주입 위치

8개 반복

```text
balanceFallbackValues

minuteStrengthFallbackValues

dailyStrengthFallbackValues

foreignSumFallbackValues

programFallbackValues

bigHandFallbackValues
```

---

10개 반복

```text
gradeFallbackValues
```

---

후보5

```text
candidateMomentumFallbackValues[index % 5]
```

---

# 렌더 경로

```text
/api/top100

↓

StockBoardTop100.update()

↓

applyRankingRows()

↓

normalizeRankingRow()

↓

renderBoard()

↓

DOM
```

---

# 조사 결과

sampleRows

demoRows

mockRows

fallbackRows

없음

---

sessionStorage

cached

없음

---

localStorage

존재

하지만

```text
캔들 모드

열 너비
```

전용.

row 데이터와 무관.

---

# 3. 수정 내용

수정 파일

```text
docs/stockboard_v0_3_0_sample.html
```

한 파일만 수정.

---

수정 사항

### 1

모든 fallback 반복 배열 제거

제거:

```text
balanceFallbackValues

minuteStrengthFallbackValues

dailyStrengthFallbackValues

foreignSumFallbackValues

programFallbackValues

bigHandFallbackValues

gradeFallbackValues
```

---

### 2

후보 모멘텀 fallback 제거

제거:

```text
candidateMomentumFallbackValues[index % ...]
```

---

### 3

실제 row 값 우선

사용:

```text
row.foreign_sum

row.program_net

row.grade

row.balance_ratio

row.bid_ask_ratio

row.strength_1m

row.minute_strength

row.strength_day

row.daily_strength
```

---

### 4

숫자 0 보존

```text
0

↓

결측 아님

↓

화면 0 표시
```

---

### 5

결측 처리

다음만 결측

```text
null

undefined

''
```

표시

```text
-
```

---

### 6

등급 결측

```text
grade='-'

gradeClass=''
```

등급분포 제외.

---

# 4. 검증 결과

반복 주입

```text
fallbackIndex %

없음
```

---

후보 모멘텀 modulo

```text
없음
```

---

git diff --check

```text
PASS
```

---

수정 파일

```text
docs/stockboard_v0_3_0_sample.html

ONLY
```

---

OHLC

캔들

```text
변경 없음
```

---

# 5. 현재 화면 상태

반복 렌더링 문제는 해결.

현재 화면은

실제 데이터가 있으면 표시.

없으면

```text
-
```

표시.

---

외합

정상 표시.

예

```text
SK하이닉스

foreign_sum

-15810.38

↓

화면

-15,810
```

---

프로

전부

```text
-
```

표시.

---

# 6. API 조사 결과

/api/top100

전체

```text
192 rows
```

---

foreign_sum

숫자

```text
12 rows
```

null

```text
179 rows
```

실제 0

```text
1 row
```

---

program_net

키 존재

```text
192 rows
```

숫자

```text
0 rows
```

null

```text
192 rows
```

---

program 유사 키

```text
program

program_eok

program_net_eok

program_value

program_sum
```

모두 없음.

---

# 7. HTML 문제 여부

HTML는

```text
program_net

program_sum

program_net_eok

프로(억)
```

순으로 읽음.

따라서

현재 프로가

```text
-
```

로 표시되는 이유는

HTML 문제가 아니라

```text
/api/top100

↓

program_net

↓

전부 null
```

이기 때문.

---

# 8. stock_code 조사

형식

```text
6자리 문자열

192 / 192
```

---

앞자리 0

```text
118 rows
```

---

중복 코드

```text
없음
```

---

결론

코드 형식 문제 가능성 낮음.

---

# 9. 남은 문제

program_net

Backend 연결 문제.

가능성

```text
ka90004 조회 비활성

조회 결과 없음

API 오류

Backend map 결합 실패

market 구분 오류

조회 대상 종목 제한
```

---

# 10. 다음 진단 대상

조사 파일

```text
kiwoom_data_provider.py

stockboard_server.py
```

우선 확인

```text
fetch_program_net()

ka90004 호출

program_map 생성

stock_code 매핑

/api/top100 결합
```

---

# 최종 결론

반복 렌더링 문제는

HTML 수정으로 해결 완료.

현재 프로 미표시는

```text
/ api/top100

program_net

↓

192개 모두 null
```

때문이며,

다음 단계는

**Backend 프로그램 순매수 연결 진단**

이다.
