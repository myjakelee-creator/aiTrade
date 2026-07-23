from __future__ import annotations

import socket
import time
from typing import Any


STATUS_COMPAT_VERSION = "collector_status_compat_alias_v1"


def install(base) -> None:
    """Keep collector delivery alive and expose one stable status schema."""

    sender_class = base.EventSender
    if getattr(sender_class, "_stockboard_sender_resilience_installed", False):
        return

    original_init = sender_class.__init__
    original_stats = sender_class.stats
    original_publish_collector_status = base.publish_collector_status

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.serialization_error_count = 0
        self.serialization_drop_count = 0
        self.serialization_last_error = None
        self.serialization_last_event_type = None
        self.run_recovery_count = 0
        self.run_last_error = None

    def send_batch(self, sock, events: list[dict[str, Any]]) -> bool:
        sent_in_batch = 0
        for index, event in enumerate(events):
            try:
                encoded = base.safe_json_dumps(event).encode("utf-8") + b"\n"
            except Exception as error:
                # A poison status/event must never terminate the sender thread or be
                # requeued forever. Drop only that event and continue with the batch.
                self.serialization_error_count += 1
                self.serialization_drop_count += 1
                self.serialization_last_error = (
                    f"{type(error).__name__}: {error}"
                )
                self.serialization_last_event_type = str(
                    event.get("type") if isinstance(event, dict) else type(event).__name__
                )
                continue

            try:
                sock.sendall(encoded)
                self.sent_count += 1
                sent_in_batch += 1
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                self._requeue_unsent(events[index:])
                return False

        if events:
            self.last_flush_count = sent_in_batch
            self.last_flush_at = base.now_text()
        self._update_rate()
        return True

    def run(self) -> None:
        sock = None
        while not self.stop_event.is_set():
            try:
                if sock is None:
                    try:
                        sock = socket.create_connection(
                            (self.host, self.port),
                            timeout=1.0,
                        )
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        self.connected = True
                        self.last_error = None
                    except OSError as error:
                        self.connected = False
                        self.last_error = str(error)
                        time.sleep(0.2)
                        continue

                time.sleep(self.flush_sec)
                try:
                    events = self._drain()
                except Exception as error:
                    self.run_recovery_count += 1
                    self.run_last_error = f"drain:{type(error).__name__}: {error}"
                    time.sleep(0.05)
                    continue

                if not events:
                    self._update_rate()
                    continue
                if not self._send_batch(sock, events):
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
                    self.connected = False
            except Exception as error:
                # Last-resort guard: the daemon sender must never disappear silently.
                self.run_recovery_count += 1
                self.run_last_error = f"loop:{type(error).__name__}: {error}"
                self.connected = False
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
                time.sleep(0.2)

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def stats(self) -> dict[str, Any]:
        result = original_stats(self)
        result.update(
            {
                "sender_thread_alive": self.is_alive(),
                "serialization_error_count": int(
                    getattr(self, "serialization_error_count", 0) or 0
                ),
                "serialization_drop_count": int(
                    getattr(self, "serialization_drop_count", 0) or 0
                ),
                "serialization_last_error": getattr(
                    self, "serialization_last_error", None
                ),
                "serialization_last_event_type": getattr(
                    self, "serialization_last_event_type", None
                ),
                "sender_run_recovery_count": int(
                    getattr(self, "run_recovery_count", 0) or 0
                ),
                "sender_run_last_error": getattr(self, "run_last_error", None),
                "sender_resilience": "collector_sender_fail_open_v1",
            }
        )
        return result

    def publish_collector_status(sender, provider, extra=None) -> None:
        provider_status = provider.status() if provider is not None else {}
        sender_status = sender.stats()
        compatibility = dict(extra or {})
        compatibility.update(
            {
                "status": provider_status,
                "sender_stats": sender_status,
                "collector_status_compat_version": STATUS_COMPAT_VERSION,
            }
        )
        original_publish_collector_status(sender, provider, compatibility)

    sender_class.__init__ = init
    sender_class._send_batch = send_batch
    sender_class.run = run
    sender_class.stats = stats
    base.publish_collector_status = publish_collector_status
    sender_class._stockboard_sender_resilience_installed = True
