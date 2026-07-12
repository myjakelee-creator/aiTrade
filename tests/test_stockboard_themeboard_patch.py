from pathlib import Path
from types import SimpleNamespace

from realtime_v2.themeboard_patch import install


class State:
    def __init__(self, *_args, **_kwargs):
        self.name_by_code = {}
        self.quotes = {}
        self.status = {}
        import threading
        self.lock = threading.RLock()


class WebHandler:
    def do_GET(self):
        return "original"


class FakeBase(SimpleNamespace):
    pass


def test_install_is_idempotent(tmp_path: Path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "stockboard_theme_master.json").write_text('{"themes": []}', encoding="utf-8")
    base = FakeBase(State=State, WebHandler=WebHandler, ROOT=tmp_path, RUNTIME_DIR=tmp_path / "runtime")
    install(base)
    first_init = base.State.__init__
    first_get = base.WebHandler.do_GET
    install(base)
    assert base.State.__init__ is first_init
    assert base.WebHandler.do_GET is first_get
