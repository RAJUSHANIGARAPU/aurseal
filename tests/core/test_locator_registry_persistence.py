"""
Locator registry persistence safety: concurrent heals always leave a parseable
file with every heal in it, and the default never touches the committed file.
"""

from __future__ import annotations

import json
import multiprocessing
import threading

from autonomous_ui.locator_store import LocatorStore, strip_heal_metadata

KEYS = [f"k{i}" for i in range(12)]


def _registry(tmp_path):
    path = tmp_path / "shared_locators.json"
    path.write_text(json.dumps({k: {"type": "testid", "value": k} for k in KEYS}))
    return path


def _heal_all(path: str, worker: int, rounds: int) -> None:
    for round_ in range(rounds):
        for key in KEYS[worker::4]:
            LocatorStore(path, autopersist=True).update(
                key, {"type": "text", "value": f"{key}-w{worker}-r{round_}"}
            )


def test_many_writers_and_a_reader_never_see_a_broken_file(tmp_path):
    path = _registry(tmp_path)
    errors: list[str] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                errors.append(repr(exc))
                continue
            if set(data) != set(KEYS):
                errors.append(f"keys lost: {sorted(set(KEYS) - set(data))}")

    watcher = threading.Thread(target=reader)
    watcher.start()
    writers = [threading.Thread(target=_heal_all, args=(str(path), w, 15)) for w in range(4)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    watcher.join()

    assert errors == []
    final = json.loads(path.read_text())
    # read-modify-write under the lock: every key carries its last round's heal
    for worker in range(4):
        for key in KEYS[worker::4]:
            assert strip_heal_metadata(final[key]) == {
                "type": "text",
                "value": f"{key}-w{worker}-r14",
            }
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared_locators.json"]


def test_separate_processes_do_not_lose_each_others_heals(tmp_path):
    path = _registry(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_heal_all, args=(str(path), w, 3)) for w in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    final = json.loads(path.read_text())
    assert all(final[k]["type"] == "text" for k in KEYS)


def test_default_proposes_and_leaves_the_committed_file_byte_identical(tmp_path, monkeypatch):
    monkeypatch.delenv("HEAL_AUTOPERSIST", raising=False)
    path = _registry(tmp_path)
    before = path.read_bytes()
    store = LocatorStore(path)
    store.update("k0", {"type": "role", "role": "button"})
    assert path.read_bytes() == before
    assert "k0" in store.pending()
    assert store.get("k0")["type"] == "role"  # in memory, updated in both modes


def test_autopersist_env_rewrites_the_source_of_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("HEAL_AUTOPERSIST", "true")
    path = _registry(tmp_path)
    LocatorStore(path).update("k0", {"type": "role", "role": "button"})
    assert json.loads(path.read_text())["k0"]["type"] == "role"
    assert not (tmp_path / "shared_locators.heals-pending.json").exists()
