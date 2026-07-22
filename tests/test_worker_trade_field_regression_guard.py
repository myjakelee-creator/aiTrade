from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_cumulative_value_regression_keeps_latest_price_and_previous_amount():
    _run(
        r'''
import sys, threading
from types import ModuleType
fake_guarded=ModuleType("realtime_v2.worker64_guarded")
fake_guarded._time_seconds=lambda value: int(str(value)) if str(value or "").isdigit() else None
def original_drop(state, quote, code, reason, event, values, trade_time, lag_sec):
    state.status["dropped_trade_count"]=int(state.status.get("dropped_trade_count") or 0)+1
fake_guarded._drop_trade=original_drop
sys.modules["realtime_v2.worker64_guarded"]=fake_guarded
from realtime_v2.worker_trade_field_regression_guard import install

def merged(event):
    result={}
    if isinstance(event.get("values"),dict): result.update(event["values"])
    if isinstance(event.get("kwargs"),dict): result.update(event["kwargs"])
    return result
class State:
    def __init__(self):
        self.lock=threading.RLock(); self.status={}
        self.quotes={"000660":{"stock_code":"000660","row_source":"realtime","price":100,"trade_value_eok":100.0,"_trade_time_seconds":101500,"trade_time":"101500"}}
    def _apply_trade(self,event):
        values=merged(event); quote=self.quotes[event["stock_code"]]
        raw=values.get("raw") if isinstance(values.get("raw"),dict) else values
        price=values.get("price") or raw.get("price_raw")
        if price not in (None,""): quote["price"]=float(price)
        cumulative=values.get("cumulative_value") or raw.get("cumulative_value_raw")
        if cumulative not in (None,""): quote["trade_value_eok"]=float(cumulative)
class Base:
    State=State; merged_event_values=staticmethod(merged)
install(Base)
state=State()
state._apply_trade({"type":"trade","stock_code":"000660","received_code":"000660_AL","kwargs":{"price":"110","trade_time":"101501","cumulative_value":"90","source_code":"000660_AL","raw":{"price_raw":"110","trade_time_raw":"101501","cumulative_value_raw":"90"}}})
assert state.quotes["000660"]["price"]==110.0
assert state.quotes["000660"]["trade_value_eok"]==100.0
assert state.status["trade_field_regression_suppressed_reason_counts"]=={"cumulative_trade_value_decreased":1}
assert state.status.get("dropped_trade_count",0)==0
'''
    )


def test_sor_older_fid20_keeps_arrival_order_price_but_holds_time_and_amount():
    _run(
        r'''
import sys, threading
from types import ModuleType
fake_guarded=ModuleType("realtime_v2.worker64_guarded")
fake_guarded._time_seconds=lambda value: int(str(value)) if str(value or "").isdigit() else None
def original_drop(state, quote, code, reason, event, values, trade_time, lag_sec):
    state.status["dropped_trade_count"]=int(state.status.get("dropped_trade_count") or 0)+1
fake_guarded._drop_trade=original_drop
sys.modules["realtime_v2.worker64_guarded"]=fake_guarded
from realtime_v2.worker_trade_field_regression_guard import install

def merged(event):
    result={}
    if isinstance(event.get("kwargs"),dict): result.update(event["kwargs"])
    return result
class State:
    def __init__(self):
        self.lock=threading.RLock(); self.status={}
        self.quotes={"000660":{"stock_code":"000660","row_source":"realtime","price":100,"change_rate":1.0,"trade_value_eok":100.0,"_trade_time_seconds":101500,"trade_time":"101500"}}
    def _apply_trade(self,event):
        values=merged(event); quote=self.quotes[event["stock_code"]]
        incoming=fake_guarded._time_seconds(values.get("trade_time"))
        if incoming is not None and incoming < quote["_trade_time_seconds"]:
            fake_guarded._drop_trade(self,quote,event["stock_code"],"older_fid20_than_last_accepted",event,values,str(values.get("trade_time") or ""),None); return
        quote["price"]=float(values.get("price")); quote["change_rate"]=float(values.get("change_rate")); quote["trade_time"]=values.get("trade_time")
        cumulative=values.get("cumulative_value")
        if cumulative not in (None,""): quote["trade_value_eok"]=float(cumulative)
class Base:
    State=State; merged_event_values=staticmethod(merged)
install(Base)
state=State()
state._apply_trade({"type":"trade","stock_code":"000660","received_code":"000660_AL","kwargs":{"price":"111","change_rate":"2.0","trade_time":"101459","cumulative_value":"99","source_code":"000660_AL"}})
quote=state.quotes["000660"]
assert quote["price"]==111.0 and quote["change_rate"]==2.0
assert quote["trade_time"]=="101500" and quote["trade_value_eok"]==100.0
assert state.status.get("dropped_trade_count",0)==0
assert state.status["trade_field_regression_suppressed_reason_counts"]=={
    "sor_fid20_interleaved_price_preserved":1,
    "cumulative_trade_value_decreased":1,
}
'''
    )


def test_non_sor_older_fid20_still_drops_whole_event():
    _run(
        r'''
import sys, threading
from types import ModuleType
fake_guarded=ModuleType("realtime_v2.worker64_guarded")
fake_guarded._time_seconds=lambda value: int(str(value)) if str(value or "").isdigit() else None
def original_drop(state, quote, code, reason, event, values, trade_time, lag_sec):
    state.status["dropped_trade_count"]=int(state.status.get("dropped_trade_count") or 0)+1
fake_guarded._drop_trade=original_drop
sys.modules["realtime_v2.worker64_guarded"]=fake_guarded
from realtime_v2.worker_trade_field_regression_guard import install

def merged(event): return dict(event.get("kwargs") or {})
class State:
    def __init__(self):
        self.lock=threading.RLock(); self.status={}
        self.quotes={"000660":{"stock_code":"000660","row_source":"realtime","price":100,"_trade_time_seconds":101500,"trade_time":"101500"}}
    def _apply_trade(self,event):
        values=merged(event); quote=self.quotes[event["stock_code"]]
        incoming=fake_guarded._time_seconds(values.get("trade_time"))
        if incoming is not None and incoming < quote["_trade_time_seconds"]:
            fake_guarded._drop_trade(self,quote,event["stock_code"],"older_fid20_than_last_accepted",event,values,str(values.get("trade_time") or ""),None); return
        quote["price"]=float(values.get("price"))
class Base:
    State=State; merged_event_values=staticmethod(merged)
install(Base)
state=State()
state._apply_trade({"type":"trade","stock_code":"000660","received_code":"000660","kwargs":{"price":"90","trade_time":"101459","source_code":"000660"}})
assert state.quotes["000660"]["price"]==100
assert state.status["dropped_trade_count"]==1
assert state.status["dropped_trade_reason_counts"]=={"older_fid20_than_last_accepted":1}
'''
    )


def test_production_entrypoint_installs_trade_field_regression_guard_v2():
    _run(
        r'''
import importlib
production=importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
guarded=importlib.import_module("realtime_v2.worker64_guarded")
assert production is not None
assert getattr(guarded.base.State,"_stockboard_trade_field_regression_guard_installed",False) is True
assert getattr(guarded.base.State,"_stockboard_trade_field_regression_guard_version",None)=="trade_field_regression_guard_v2"
'''
    )
