# StockBoard Replay Lab

Date: 2026-07-05

Purpose: Replay 08:59~09:10 tick data into the real StockBoard HTML through a live-compatible replay API, with OpenAPI/Kiwoom/order paths disabled.

Key conclusion: Real StockBoard HTML and DOM rendering show almost no delay under replay load. The likely live bottleneck is OpenAPI callback -> Python processing -> realtime store -> API payload generation.

Folder structure:
replay/
  stockboard_replay_server.py
  stockboard_live_compatible_replay_server.py
  tools/
  docs/
  scripts/

Root launcher:
start_stockboard_replay.cmd

Run: double-click C:\aiTrade\start_stockboard_replay.cmd

Controls:
Space play/pause
R goto 08:59:00
9 goto 09:00:00
H reset
Left/Right -10s/+10s
Up/Down +1m/-1m
PgUp/PgDn -5m/+5m
1/5/0/3 speed 1x/5x/10x/30x
B open board
Q/X stop replay server and exit

Status meaning:
Original Tick = last source tick applied to store
Target Time = replay clock target
Process Lag = real processing delay
Event Gap = normal gap between source ticks
api_top100_ms = top100 API build time
api_patch_ms = realtime_patch API build time

Next TODO:
1. Add live OpenAPI callback -> store -> API delay instrumentation
2. Add queue_depth, event/sec, last_event_age_ms to realtime_provider_status
3. Add build_ms and newest_event_age_ms to realtime_patch
4. Check whether ranking/candidate calculation is coupled to realtime callback path
5. Soften realtime patch flash effect: reduce yellow background and thick border
