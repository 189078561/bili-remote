from copy import deepcopy
from pathlib import Path

import pytest

from bili_remote.database import Database
from bili_remote.file_service import FileService
from bili_remote.v2.naming.filesystem import NamingFiles
from bili_remote.v2.naming.execution import RenameExecutor
from bili_remote.v2.naming.errors import NamingError


class Audit:
    def __init__(self):
        self.records = {}

    def create(self, batch):
        assert batch["id"] not in self.records
        self.update(batch)

    def update(self, batch):
        self.records[batch["id"]] = deepcopy(batch)

    def get(self, batch_id):
        return deepcopy(self.records.get(batch_id))


@pytest.fixture
def engine(settings):
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    files = NamingFiles(FileService(settings, database))
    return RenameExecutor(files, Audit()), settings.download_root


def plan(engine, pairs):
    result = []
    for old, new in pairs:
        ref = engine.files.reference("downloads", old)
        result.append({"source": ref, "target_relative_path": new, "source_fingerprint": ref["fingerprint"], "sidecar_of": None, "warnings": [], "temporary_required": True})
    return result


def test_swap_undo_and_idempotent_execution(engine):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    (root / "b.mkv").write_bytes(b"B")
    operations = plan(execution, [("a.mkv", "b.mkv"), ("b.mkv", "a.mkv")])
    batch = execution.prepare(operations, owner="session")
    result = execution.execute(batch)
    assert result["status"] == "completed"
    assert (root / "a.mkv").read_bytes() == b"B"
    assert execution.execute(batch) == result
    reverse = execution.undo_operations(batch, owner="session")
    execution.execute(execution.prepare(reverse, owner="session", undo_of=batch))
    assert (root / "a.mkv").read_bytes() == b"A"
    assert (root / "b.mkv").read_bytes() == b"B"
    assert not list(root.glob(".bili-v2-*"))


def test_late_failure_restores_swap(engine, monkeypatch):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    (root / "b.mkv").write_bytes(b"B")
    batch = execution.prepare(plan(execution, [("a.mkv", "b.mkv"), ("b.mkv", "a.mkv")]), owner="s")
    original = execution.files.move
    calls = 0
    def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected disk error")
        return original(*args)
    monkeypatch.setattr(execution.files, "move", fail_once)
    with pytest.raises(NamingError, match="未完成"):
        execution.execute(batch)
    assert execution.audit.get(batch)["status"] == "rolled_back"
    assert (root / "a.mkv").read_bytes() == b"A"
    assert (root / "b.mkv").read_bytes() == b"B"


def test_stale_and_casefold_conflicts(engine):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    operations = plan(execution, [("a.mkv", "new.mkv")])
    (root / "NEW.MKV").write_bytes(b"new")
    assert execution.files.validate_operations(operations)[0]["code"] == "target_exists"
    (root / "NEW.MKV").unlink()
    (root / "a.mkv").write_bytes(b"changed")
    with pytest.raises(NamingError):
        execution.prepare(operations, owner="s")
    assert not execution.audit.records


def test_undo_rejects_drift_and_other_session(engine):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    batch = execution.prepare(plan(execution, [("a.mkv", "z.mkv")]), owner="s")
    execution.execute(batch)
    with pytest.raises(NamingError):
        execution.undo_operations(batch, owner="other")
    (root / "z.mkv").write_bytes(b"drift")
    undo = execution.undo_operations(batch, owner="s")
    assert execution.files.validate_operations(undo)[0]["code"] == "stale_resource"


def test_symlink_traversal_reserved_permission(engine, tmp_path):
    execution, root = engine
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.mkv").write_bytes(b"private")
    (root / "link").symlink_to(outside, target_is_directory=True)
    for path in ("../outside/a.mkv", "/etc/passwd", "link/a.mkv"):
        with pytest.raises(NamingError):
            execution.files.reference("downloads", path)
    (root / "a.mkv").write_bytes(b"a")
    root.chmod(0o555)
    try:
        with pytest.raises(NamingError):
            execution.prepare(plan(execution, [("a.mkv", "b.mkv")]), owner="s")
    finally:
        root.chmod(0o755)


def test_sidecars_exact_stem_and_longest_video(engine):
    execution, root = engine
    for name in ("a.mkv", "a.extra.mkv", "a.chs.forced.srt", "a.cht.ass", "a.eng.SDH.srt", "ab.eng.srt", "a.extra.eng.srt", "a.flac"):
        (root / name).write_bytes(b"test")
    sidecars = execution.files.sidecars(execution.files.reference("downloads", "a.mkv"))
    assert {x["ref"]["relative_path"] for x in sidecars} == {"a.chs.forced.srt", "a.cht.ass", "a.eng.SDH.srt"}
    assert sidecars[0]["confirmed"]


def test_recovery_preserves_new_occupant(engine, monkeypatch):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    batch = execution.prepare(plan(execution, [("a.mkv", "b.mkv")]), owner="s")
    move = execution.files.move
    def interrupt(ref, target, fp):
        if target == "b.mkv":
            (root / "a.mkv").write_bytes(b"new occupant")
            raise OSError("injected")
        return move(ref, target, fp)
    monkeypatch.setattr(execution.files, "move", interrupt)
    with pytest.raises(NamingError):
        execution.execute(batch)
    assert (root / "a.mkv").read_bytes() == b"new occupant"
    assert execution.audit.get(batch)["status"] == "failed"
    temporary = list(root.glob(".bili-v2-*"))
    assert len(temporary) == 1 and temporary[0].read_bytes() == b"A"


def test_crash_between_move_and_audit_is_recovered_by_fingerprint(engine):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    batch_id = execution.prepare(plan(execution, [("a.mkv", "b.mkv")]), owner="s")
    batch = execution.audit.get(batch_id)
    batch["status"] = "running"
    batch["items"][0]["intent"] = "staged"
    execution.audit.update(batch)
    item = batch["items"][0]
    execution.files.move(item["source"], item["temporary_path"], item["source_fingerprint"])
    assert not (root / "a.mkv").exists()
    assert execution.recover(batch_id)["status"] == "rolled_back"
    assert (root / "a.mkv").read_bytes() == b"A"


def test_cancelled_after_staging_rolls_back(engine):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    batch = execution.prepare(plan(execution, [("a.mkv", "b.mkv")]), owner="s")
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        return checks > 1
    with pytest.raises(NamingError):
        execution.execute(batch, should_cancel=cancel)
    assert execution.audit.get(batch)["status"] == "rolled_back"
    assert (root / "a.mkv").read_bytes() == b"A"


def test_deleted_source_and_new_symlink_are_conflicts(engine, tmp_path):
    execution, root = engine
    (root / "a.mkv").write_bytes(b"A")
    operations = plan(execution, [("a.mkv", "b.mkv")])
    (root / "a.mkv").unlink()
    assert execution.files.validate_operations(operations)[0]["code"] == "stale_resource"
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"outside")
    (root / "a.mkv").symlink_to(outside)
    assert execution.files.validate_operations(operations)[0]["code"] == "stale_resource"
    assert outside.read_bytes() == b"outside"
