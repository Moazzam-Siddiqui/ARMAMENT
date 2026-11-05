import json

from sentinel_ops import audit


def test_record_writes_json_line_to_configured_location(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))

    import asyncio
    asyncio.run(
        audit.record(
            "restart_service",
            "api",
            "Observed stuck worker pool",
            "succeeded",
        )
    )

    event = json.loads(path.read_text(encoding="utf-8").strip())
    assert event["action"] == "restart_service"
    assert event["service"] == "api"
    assert event["outcome"] == "succeeded"
    assert event["reason"] == "Observed stuck worker pool"


def test_audit_rotation_keeps_bounded_backups(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))
    monkeypatch.setattr(audit, "MAX_BYTES", 8)
    monkeypatch.setattr(audit, "BACKUP_COUNT", 2)

    audit._append("12345678")
    audit._append("abcdefgh")
    audit._append("ABCDEFGH")

    assert path.read_text(encoding="utf-8") == "ABCDEFGH\n"
    assert path.with_name("audit.jsonl.1").read_text(encoding="utf-8") == "abcdefgh\n"
    assert path.with_name("audit.jsonl.2").read_text(encoding="utf-8") == "12345678\n"


def test_recent_events_returns_newest_first_and_enforces_limit(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))
    audit._append(json.dumps({"action": "first"}))
    audit._append(json.dumps({"action": "second"}))

    assert [event["action"] for event in audit.recent_events(1)] == ["second"]
    assert [event["action"] for event in audit.recent_events(2)] == [
        "second",
        "first",
    ]


def test_existing_audit_permissions_are_restricted(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))
    path.write_text('{"action":"test"}\n', encoding="utf-8")
    path.chmod(0o644)

    audit._append('{"action":"next"}')

    assert path.stat().st_mode & 0o777 == 0o600


def test_recent_events_includes_rotated_history_and_skips_corrupt_lines(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))
    path.write_bytes(b'{"action":"active"}\ninvalid-\xff\n')
    path.with_name("audit.jsonl.1").write_text('{"action":"older"}\n', encoding="utf-8")

    assert [event["action"] for event in audit.recent_events(2)] == ["active", "older"]


def test_large_audit_entries_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("ARMAMENT_AUDIT_LOG", str(path))
    audit._append(json.dumps({"action": "test", "reason": "x" * 100000}))

    assert path.stat().st_size <= audit.MAX_ENTRY_BYTES
    assert json.loads(path.read_text(encoding="utf-8"))["detail"] == {"truncated": True}
