@echo off
setlocal EnableExtensions
chcp 65001 > nul
cd /d "%~dp0"

title StockBoard Opening Latency Diagnostics

echo ============================================================
echo   StockBoard 장초반 지연 진단
echo ============================================================
echo.
echo 서버가 먼저 실행되어 있어야 합니다: stockboard_live.cmd
echo 결과 폴더: data\runtime\latency
echo.

set "CODES=000660,005930,009150,402340,005380,010140,196170,015760,042660,010120,011070,034020,042700"
set "DURATION=1800"
set "INTERVAL=0.5"

if not "%~1"=="" set "DURATION=%~1"
if not "%~2"=="" set "INTERVAL=%~2"
if not "%~3"=="" set "CODES=%~3"

echo 추적 종목: %CODES%
echo 진단 시간: %DURATION%초
echo 호출 간격: %INTERVAL%초
echo.
echo 중간 종료: Ctrl+C
echo.

python -m stockboard_diagnostics.opening_latency_probe --codes "%CODES%" --duration %DURATION% --interval %INTERVAL%

echo.
echo ============================================================
echo   진단 종료
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir='data\runtime\latency'; $summary=Get-ChildItem $dir -Filter 'opening_latency_summary_*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if($summary){ Write-Host '최신 요약:' $summary.FullName; $j=Get-Content $summary.FullName -Raw | ConvertFrom-Json; if($j.summary_by_code_endpoint){ $j.summary_by_code_endpoint | Select-Object stock_code,endpoint,request_elapsed_ms_p95,price_age_sec_client_p95,price_age_sec_api_p95,fid20_trade_lag_sec_p95 | Format-Table -Auto } elseif($j.summary){ $j.summary | Format-Table -Auto } } else { Write-Host '요약 파일이 없습니다. Ctrl+C로 종료한 경우에도 생성되어야 합니다.' }"

echo.
echo 창을 닫으려면 아무 키나 누르세요.
pause > nul
