from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, now_text


class TRSingleFlightTimeout(TimeoutError):
    pass


class TRSingleFlightCoordinator:
    """Cross-process single-flight cache for slow TR/HTTP sources.

    A logical request key is normalized from provider, TR code, parameters,
    trading date and market session. Only the process that atomically creates
    the lease file executes the physical request. Other callers reuse the same
    cache or wait for the owner result.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or (RUNTIME_DIR / "board_data_hub" / "tr_singleflight"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._request_count = 0
        self._physical_fetch_count = 0
        self._cache_hit_count = 0
        self._wait_count = 0
        self._stale_return_count = 0
        self._error_count = 0
        self._last_key: str | None = None
        self._last_mode: str | None = None
        self._last_error: str | None = None
        self._last_completed_at: str | None = None

    @staticmethod
    def normalized_key(
        *,
        provider: str,
        tr_code: str,
        params: dict[str, Any] | None = None,
        trading_date: str = "",
        market_session: str = "",
    ) -> dict[str, Any]:
        normalized_params = json.loads(
            json.dumps(params or {}, ensure_ascii=False, sort_keys=True, default=str)
        )
        return {
            "provider": str(provider or "").strip().lower(),
            "tr_code": str(tr_code or "").strip().lower(),
            "params": normalized_params,
            "trading_date": str(trading_date or "").strip(),
            "market_session": str(market_session or "").strip().lower(),
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Return a JSON-native copy of a slow-source payload.

        Kiwoom helpers may expose Decimal metadata such as unit divisors. The
        single-flight cache must persist the same payload for all processes, so
        normalize only serialization types here instead of changing TR values or
        issuing another request.
        """

        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                return str(value)
            integral = value.to_integral_value()
            return int(integral) if value == integral else float(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): TRSingleFlightCoordinator._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [TRSingleFlightCoordinator._json_safe(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return [
                TRSingleFlightCoordinator._json_safe(item)
                for item in sorted(value, key=repr)
            ]
        return str(value)

    @staticmethod
    def _digest(key: dict[str, Any]) -> str:
        raw = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _paths(self, digest: str) -> tuple[Path, Path]:
        return self.root / f"{digest}.json", self.root / f"{digest}.lease"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _cache_fresh(payload: dict[str, Any] | None, ttl_sec: float) -> bool:
        if not isinstance(payload, dict):
            return False
        fetched_epoch = payload.get("fetched_epoch")
        try:
            age = time.time() - float(fetched_epoch)
        except (TypeError, ValueError):
            return False
        return age <= max(0.0, float(ttl_sec))

    @staticmethod
    def _lease_stale(path: Path, lease_timeout_sec: float) -> bool:
        try:
            return time.time() - path.stat().st_mtime > max(1.0, lease_timeout_sec)
        except OSError:
            return False

    @staticmethod
    def _try_acquire_lease(path: Path, digest: str) -> bool:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            content = json.dumps(
                {
                    "pid": os.getpid(),
                    "digest": digest,
                    "created_at": now_text(),
                    "created_epoch": time.time(),
                },
                ensure_ascii=False,
            )
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def execute(
        self,
        *,
        provider: str,
        tr_code: str,
        params: dict[str, Any] | None,
        fetcher: Callable[[], dict[str, Any]],
        ttl_sec: float,
        trading_date: str = "",
        market_session: str = "",
        wait_timeout_sec: float = 60.0,
        lease_timeout_sec: float = 120.0,
        poll_sec: float = 0.05,
        stale_if_error: bool = True,
    ) -> dict[str, Any]:
        key = self.normalized_key(
            provider=provider,
            tr_code=tr_code,
            params=params,
            trading_date=trading_date,
            market_session=market_session,
        )
        digest = self._digest(key)
        cache_path, lease_path = self._paths(digest)
        with self._lock:
            self._request_count += 1
            self._last_key = digest

        cached = self._read_json(cache_path)
        if self._cache_fresh(cached, ttl_sec):
            with self._lock:
                self._cache_hit_count += 1
                self._last_mode = "cache_hit"
                self._last_error = None
            return deepcopy(cached.get("payload") or {})

        stale_payload = deepcopy(cached.get("payload") or {}) if isinstance(cached, dict) else None
        deadline = time.monotonic() + max(0.1, float(wait_timeout_sec))

        while True:
            if self._try_acquire_lease(lease_path, digest):
                try:
                    # Another process may have completed between the initial cache
                    # check and this lease acquisition.
                    cached = self._read_json(cache_path)
                    if self._cache_fresh(cached, ttl_sec):
                        with self._lock:
                            self._cache_hit_count += 1
                            self._last_mode = "cache_after_lease"
                            self._last_error = None
                        return deepcopy(cached.get("payload") or {})

                    with self._lock:
                        self._physical_fetch_count += 1
                        self._last_mode = "physical_fetch"
                    raw_payload = fetcher()
                    if not isinstance(raw_payload, dict):
                        raise TypeError("TR single-flight fetcher must return dict")
                    payload = self._json_safe(raw_payload)
                    envelope = {
                        "schema_version": 1,
                        "source": "tr_singleflight_cache",
                        "key": key,
                        "digest": digest,
                        "owner_pid": os.getpid(),
                        "fetched_at": now_text(),
                        "fetched_epoch": time.time(),
                        "payload": payload,
                    }
                    atomic_write_json(cache_path, envelope)
                    with self._lock:
                        self._last_completed_at = envelope["fetched_at"]
                        self._last_error = None
                    return deepcopy(payload)
                except Exception as error:
                    with self._lock:
                        self._error_count += 1
                        self._last_error = f"{type(error).__name__}: {error}"
                        self._last_mode = "stale_after_error" if stale_payload else "error"
                    if stale_if_error and isinstance(stale_payload, dict):
                        with self._lock:
                            self._stale_return_count += 1
                        return stale_payload
                    raise
                finally:
                    try:
                        lease_path.unlink()
                    except OSError:
                        pass

            with self._lock:
                self._wait_count += 1
                self._last_mode = "wait"

            while time.monotonic() < deadline:
                cached = self._read_json(cache_path)
                if self._cache_fresh(cached, ttl_sec):
                    with self._lock:
                        self._cache_hit_count += 1
                        self._last_mode = "shared_result"
                        self._last_error = None
                    return deepcopy(cached.get("payload") or {})
                if not lease_path.exists():
                    break
                if self._lease_stale(lease_path, lease_timeout_sec):
                    try:
                        lease_path.unlink()
                    except OSError:
                        pass
                    break
                time.sleep(max(0.01, float(poll_sec)))
            else:
                if stale_if_error and isinstance(stale_payload, dict):
                    with self._lock:
                        self._stale_return_count += 1
                        self._last_mode = "stale_after_timeout"
                    return stale_payload
                with self._lock:
                    self._error_count += 1
                    self._last_mode = "timeout"
                    self._last_error = f"timeout waiting for {digest}"
                raise TRSingleFlightTimeout(f"timeout waiting for shared TR result: {digest}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "root": str(self.root),
                "request_count": self._request_count,
                "physical_fetch_count": self._physical_fetch_count,
                "cache_hit_count": self._cache_hit_count,
                "wait_count": self._wait_count,
                "stale_return_count": self._stale_return_count,
                "error_count": self._error_count,
                "last_key": self._last_key,
                "last_mode": self._last_mode,
                "last_error": self._last_error,
                "last_completed_at": self._last_completed_at,
            }


_SHARED_LOCK = threading.Lock()
_SHARED_COORDINATOR: TRSingleFlightCoordinator | None = None


def get_shared_tr_coordinator() -> TRSingleFlightCoordinator:
    global _SHARED_COORDINATOR
    with _SHARED_LOCK:
        if _SHARED_COORDINATOR is None:
            _SHARED_COORDINATOR = TRSingleFlightCoordinator()
        return _SHARED_COORDINATOR
