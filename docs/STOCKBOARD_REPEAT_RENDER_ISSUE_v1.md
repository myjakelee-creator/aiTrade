# STOCKBOARD_REPEAT_RENDER_ISSUE_v1.0

작성일: 2026-06-22

목적:

StockBoard에서 발생한

* 일봉 반복 문제
* 외합 반복 문제
* 프로 반복 문제
* 큰손 반복 문제

를 정리하고,
실시간 OpenAPI 연결과의 영향 범위를 구분한다.

---

# 1. 일봉(OHLC) 반복 문제

## 증상

TOP100에서

20 ~ 27번

8개 캔들이

28번 이후 계속 반복되어 표시됨.

예)

20번 캔들

↓

28번

36번

44번

...

동일 모양 반복

---

## 서버 상태

apiRows: 189

apiOhlc: 189

normalizedRows: 189

normalizedOhlc: 189

즉

서버는 정상.

ka10086 OHLC 결합도 정상.

---

## 원인

HTML 렌더링에서

row.ohlc

를 그대로 사용하지 못함.

일부 구간에서

* 후보5 데이터
* 상위 일부 데이터
* 잘못된 참조 배열

을 반복 사용.

---

## 해결

normalizeRankingRow

```text
ohlc: row.ohlc
```

보존.

TOP100

```text
rows.forEach(...)
```

전체 순회.

후보5

```text
slice(0,5)
```

선정용으로만 사용.

캔들 계산식 변경 없음.

---

## 결과

문제 해결 완료.

---

# 2. 외합(억) 반복 문제

## 현재 상태

ka10037

↓

foreign_sum

↓

/api/top100

↓

HTML

연결 성공.

---

## 검증

TOP100 rows

191

joined count

12

숫자

12

음수

6

실제0

1

null

179

예)

000660

SK하이닉스

-6574.92

정상.

---

## 증상

외합(억)

2~9번

8개 값이

10번 이후 반복.

예)

+180

+48

+89

-520

+80

+320

+520

...

반복.

---

## 원인 추정

Python 데이터 문제 가능성 낮음.

HTML 렌더링 문제 가능성 높음.

특히

sample

slice

cache

fallback

modulo(%)

같은

임시 샘플 배열 재사용 의심.

---

# 3. 프로(억) 반복 문제

## 증상

외합과 동일.

2~9번

8개 값이

10번 이후 반복.

---

## 의미

프로 데이터 자체 문제가 아니라

공통 렌더링 로직 문제 가능성 높음.

---

# 4. 큰손 반복 문제

## 현재 상태

아직 OpenAPI 연결 안됨.

---

## 증상

이미

2~9번

8개 값 반복.

---

## 의미

실시간 데이터가 없는데도 반복됨.

즉

Python

Provider

Store

OpenAPI

문제가 아님.

HTML 내부

demo

sample

mock

fallback

임시 데이터 반복 가능성이 매우 높음.

---

# 5. 공통 원인 가설

일봉 문제와 매우 유사.

정상 구조

/api/top100

↓

rows[i]

↓

normalizeRankingRow()

↓

render

↓

DOM

---

현재 의심 구조

/api/top100

↓

sample 배열

또는

slice(0,8)

또는

i % 8

↓

render

↓

DOM

↓

2~9 반복

---

# 6. OpenAPI 실시간 연결 영향

현재 판단:

영향 거의 없음.

이유:

일봉

서버 정상.

HTML 문제.

외합

서버 정상.

HTML 문제 의심.

프로

서버 정상.

HTML 문제 의심.

큰손

실시간 미연결인데 반복.

HTML 문제 거의 확정.

---

# 7. 권장 작업 순서

1.

HTML

normalizeRankingRow

점검

2.

외합

프로

큰손

렌더 함수 점검

3.

sample

slice

fallback

modulo(%)

사용 여부 확인

4.

row 단일 원천 구조로 수정

```text
row.foreign_sum
row.program_net
row.big_hand
```

직접 사용.

5.

OHLC와 동일하게

API

↓

normalize

↓

render

↓

DOM

개수가 모두 일치하는지 검증.

---

최종 판단

OpenAPI 실시간 연결 작업과는 별개의

HTML 공통 렌더링 문제일 가능성이 매우 높다.
