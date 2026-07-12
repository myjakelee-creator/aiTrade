# StockBoard v2 OpenAPI 로그인 핸들 오류 수정

최종 갱신: 2026-07-12  
상태: 수정 완료 · PC1 재로그인 검증 필요

## 원인

기존 `stockboard_v2_large.cmd`는 collector가 종목 등록 수를 반환하면 OpenAPI 로그인이 끝난 것으로 판단했다. 그러나 종목 등록은 로그인 완료 전에도 대기열에 들어갈 수 있다.

그 상태에서 launcher가 `opstarter`를 강제 종료하면 로그인 창의 부모 HWND가 사라져 다음 경고가 발생할 수 있다.

```text
opstarter
핸들값이 없습니다.
프로그램을 종료합니다.
```

## 수정

- `stockboard_v2_large.cmd`를 안전 launcher 진입점으로 교체
- `scripts/stockboard_v2_large_safe.ps1` 추가
- OpenAPI 준비 판정에 다음을 모두 요구
  - `login_state == connected`
  - `openapi_native_handle_ready == true`
  - `registered_count > 0`
  - collector provider started
- 로그인 대기시간을 180초로 확대
- 로그인 중·직후 `opstarter` 강제 종료 금지
- 로그인 실패·시간초과 시 helper를 그대로 두고 경고만 출력
- worker 64비트·collector 32비트 자동 검증
- 휴장일 universe 갱신 실패 시 기존 `universe.json` 사용

## 검증

```text
안전 launcher 정적 테스트 3개 통과
opstarter 강제종료 코드 없음
connected + native HWND + 등록 완료 조건 확인
```

## PC1 확인

```powershell
cd C:\aiTrade
git pull --ff-only origin feature/stockboard-themeboard-v1
.\stockboard_v2_large.cmd restart-fast
```

로그인 후:

```powershell
.\stockboard_v2_large.cmd status
```

정상 기준:

```text
LOGIN_STATE=connected
OPENAPI_NATIVE_HANDLE_READY=True
OPENAPI_NATIVE_HWND=양수
COLLECTOR_PROVIDER_STARTED=True
COLLECTOR_REGISTERED_COUNT=1 이상
```
