"""The cached storage state is replaced atomically and a broken file never reaches a browser."""

from __future__ import annotations

import json
import threading

from core.auth.session_token_store import SessionTokenStore, is_readable_json_object


def _cache(tmp_path) -> SessionTokenStore:
    return SessionTokenStore("login", cache_dir=tmp_path, session_ttl_s=900)


def test_a_short_state_over_a_long_one_leaves_exactly_the_short_one(tmp_path):
    cache = _cache(tmp_path)
    long_state = {"cookies": [{"name": f"c{i}", "value": "x" * 200} for i in range(50)]}
    short_state = {"cookies": [], "origins": []}
    cache.save_storage_state(long_state)
    cache.save_storage_state(short_state)
    assert cache.storage_state_path.read_text(encoding="utf-8") == json.dumps(short_state)
    assert cache.load_storage_state() == short_state


def test_trailing_bytes_are_reported_unreadable(tmp_path):
    cache = _cache(tmp_path)
    cache.storage_state_path.write_text('{"cookies": []}{"cookies": [', encoding="utf-8")
    assert not is_readable_json_object(cache.storage_state_path)
    assert cache.load_storage_state() is None


def test_a_missing_file_is_reported_unreadable(tmp_path):
    cache = _cache(tmp_path)
    assert not cache.storage_state_valid()
    assert cache.load_storage_state() is None


def test_a_json_value_that_is_not_an_object_is_unreadable(tmp_path):
    cache = _cache(tmp_path)
    cache.storage_state_path.write_text("[]", encoding="utf-8")
    assert cache.load_storage_state() is None


def test_concurrent_writers_always_leave_a_parseable_file(tmp_path):
    states = [
        {"cookies": [{"name": f"w{w}", "value": "v" * (37 * w + 1)}], "origins": []}
        for w in range(8)
    ]
    errors: list[str] = []
    stop = threading.Event()

    def writer(state):
        cache = _cache(tmp_path)  # separate instances sharing one file
        for _ in range(60):
            cache.save_storage_state(state)

    def reader():
        cache = _cache(tmp_path)
        while not stop.is_set():
            path = cache.storage_state_path
            if not path.exists():
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(repr(exc))
                continue
            if loaded not in states:
                errors.append("a state no writer wrote")

    threads = [threading.Thread(target=writer, args=(s,)) for s in states]
    watcher = threading.Thread(target=reader)
    watcher.start()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    watcher.join()
    assert errors == []
    assert json.loads(_cache(tmp_path).storage_state_path.read_text()) in states
    assert [p.name for p in tmp_path.iterdir()] == ["login.storage.json"]
