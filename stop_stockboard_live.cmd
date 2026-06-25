@echo off
setlocal
cd /d C:\aiTrade

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting administrator rights to stop StockBoard...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue'; $hadWarningOrError=$false;" ^
  "$bridgeName='stockboard_kiwoom_link_v1.ahk';" ^
  "$projectRoot='C:\aiTrade'; $serverWindowPidFile=Join-Path $projectRoot 'data\runtime\stockboard_server_window.pid';" ^
  "Write-Host 'Stopping StockBoard AHK bridge...';" ^
  "$ahkProcesses=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'AutoHotkey*' -and $_.CommandLine -and $_.CommandLine -like ('*' + $bridgeName + '*') });" ^
  "if($ahkProcesses.Count -eq 0){ Write-Host 'No StockBoard AHK bridge process found.' } else { foreach($ahkProcess in $ahkProcesses){ Write-Host ('Stopping AHK bridge PID: ' + $ahkProcess.ProcessId); Stop-Process -Id $ahkProcess.ProcessId -Force -ErrorAction SilentlyContinue; if($? -eq $false){ Write-Host ('WARNING: Failed to stop AHK bridge PID: ' + $ahkProcess.ProcessId) -ForegroundColor Yellow; $hadWarningOrError=$true } } };" ^
  "Write-Host 'Stopping StockBoard server on port 8000...';" ^
  "$connections=@(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue);" ^
  "if($connections.Count -eq 0){ Write-Host 'No listener on port 8000.' } else { $listenerPids=@($connections | Select-Object -ExpandProperty OwningProcess -Unique); foreach($targetPid in $listenerPids){ $targetProc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $targetPid) -ErrorAction SilentlyContinue; $targetCmd=if($targetProc){$targetProc.CommandLine}else{''}; $looksLikeStockBoard=($targetCmd -match 'stockboard_server\.py' -or $targetCmd -match 'kiwoom_trade_value_rank\.py' -or ($targetCmd -match 'python' -and $targetCmd -match 'C:\\aiTrade')); if($looksLikeStockBoard){ Write-Host ('Stopping StockBoard server PID: ' + $targetPid); Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue; if($? -eq $false){ Write-Host ('WARNING: Failed to stop StockBoard server PID: ' + $targetPid) -ForegroundColor Yellow; $hadWarningOrError=$true } } else { Write-Host ('WARNING: Port 8000 PID ' + $targetPid + ' does not look like StockBoard/Python. Not stopping. CommandLine=' + $targetCmd) -ForegroundColor Yellow; $hadWarningOrError=$true } } };" ^
  "Write-Host 'Stopping visible StockBoard server PowerShell window...';" ^
  "if(Test-Path -LiteralPath $serverWindowPidFile){ $rawServerWindowPid=(Get-Content -LiteralPath $serverWindowPidFile -ErrorAction SilentlyContinue | Select-Object -First 1); $serverWindowPid=0; if([int]::TryParse($rawServerWindowPid,[ref]$serverWindowPid)){ $serverWindowProc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $serverWindowPid) -ErrorAction SilentlyContinue; if($null -eq $serverWindowProc){ Write-Host ('Server PowerShell window PID is already closed: ' + $serverWindowPid); Remove-Item -LiteralPath $serverWindowPidFile -Force -ErrorAction SilentlyContinue } else { $serverWindowName=if($serverWindowProc.Name){$serverWindowProc.Name}else{''}; $serverWindowCmd=if($serverWindowProc.CommandLine){$serverWindowProc.CommandLine}else{''}; $isPowerShell=($serverWindowName -ieq 'powershell.exe' -or $serverWindowName -ieq 'pwsh.exe'); $looksLikeStockBoardWindow=($serverWindowCmd -match 'C:\\aiTrade' -or $serverWindowCmd -match 'stockboard_server\.py' -or $serverWindowCmd -match 'STOCKBOARD_ENABLE_COM_REALTIME'); if($isPowerShell -and $looksLikeStockBoardWindow){ Write-Host ('Stopping server PowerShell window PID: ' + $serverWindowPid); Stop-Process -Id $serverWindowPid -Force -ErrorAction SilentlyContinue; if($? -eq $false){ Write-Host ('WARNING: Failed to stop server PowerShell window PID: ' + $serverWindowPid) -ForegroundColor Yellow; $hadWarningOrError=$true } else { Remove-Item -LiteralPath $serverWindowPidFile -Force -ErrorAction SilentlyContinue } } else { Write-Host ('WARNING: PID file points to a process that does not look like the StockBoard PowerShell window. Not stopping. PID=' + $serverWindowPid + ' Name=' + $serverWindowName + ' CommandLine=' + $serverWindowCmd) -ForegroundColor Yellow; $hadWarningOrError=$true } } } else { Write-Host ('WARNING: Invalid server window PID file content: ' + $rawServerWindowPid) -ForegroundColor Yellow; $hadWarningOrError=$true } } else { Write-Host 'No server PowerShell window PID file.' };" ^
  "Write-Host 'Browser windows are not closed automatically.';" ^
  "Write-Host 'Done.';" ^
  "if($hadWarningOrError){ exit 1 } else { exit 0 }"

if errorlevel 1 (
  echo.
  echo Stop completed with warnings or errors. Press any key to close this stop window.
  pause >nul
  exit /b 1
)

exit /b 0
