import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from OriginAgent.cli.commands import app
from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.memory_vault import (
    MemoryVaultError,
    export_memory_vault,
    import_memory_vault,
    inspect_memory_vault,
    verify_memory_vault,
)

PASSPORT_ID = "0x" + "11" * 32
AGENT_KEY_HASH = "0x" + "22" * 32
runner = CliRunner()


def _write_key(path: Path, value: bytes | None = None) -> bytes:
    key = value or (b"k" * 32)
    path.write_text(key.hex(), encoding="ascii")
    return key


def _source_workspace(path: Path) -> Path:
    (path / "memory").mkdir(parents=True)
    (path / "SOUL.md").write_text("soul", encoding="utf-8")
    (path / "USER.md").write_text("user", encoding="utf-8")
    (path / "memory" / "MEMORY.md").write_text("memory", encoding="utf-8")
    (path / "memory" / "facts.jsonl").write_text('{"fact":"encrypted only"}\n', encoding="utf-8")
    (path / "memory" / "history.jsonl").write_text("must stay out\n", encoding="utf-8")
    (path / ".originagent").mkdir()
    (path / ".originagent" / "evolution_identity.json").write_text(
        '{"private_key":"must stay out"}\n',
        encoding="utf-8",
    )
    EvolutionLedger(path).append(EvolutionEvent.new(EventType.MODULE_PROPOSED, module_id="alpha"))
    return path


def _export(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = _source_workspace(tmp_path / "source")
    key_file = tmp_path / "vault.key"
    _write_key(key_file)
    vault_path = tmp_path / "vault.json"
    vault = export_memory_vault(
        source,
        passport_id=PASSPORT_ID,
        agent_key_hash=AGENT_KEY_HASH,
        key_file=key_file,
        out=vault_path,
    )
    return vault_path, key_file, vault


def test_export_verify_and_import_apply_round_trip(tmp_path) -> None:
    vault_path, key_file, vault = _export(tmp_path)

    public_text = vault_path.read_text(encoding="utf-8")
    assert "encrypted only" not in public_text
    assert "must stay out" not in public_text
    assert key_file.read_text(encoding="ascii").strip() not in public_text
    assert {item["path"] for item in vault["metadata"]["included_files"]} == {
        "SOUL.md",
        "USER.md",
        "memory/MEMORY.md",
        "memory/facts.jsonl",
        "memory/evolution_events.jsonl",
    }

    assert inspect_memory_vault(vault_path)["ok"] is True
    assert verify_memory_vault(vault_path)["ok"] is True
    assert verify_memory_vault(vault_path, key_file)["decrypted"] is True

    target = tmp_path / "target"
    dry_run = import_memory_vault(vault_path, key_file=key_file, target_workspace=target)
    assert dry_run.ok is True
    assert dry_run.dry_run is True
    assert not (target / "SOUL.md").exists()

    applied = import_memory_vault(vault_path, key_file=key_file, target_workspace=target, apply=True)
    assert applied.ok is True
    assert applied.import_event_hash
    assert (target / "SOUL.md").read_text(encoding="utf-8") == "soul"
    assert (target / "memory" / "facts.jsonl").read_text(encoding="utf-8") == '{"fact":"encrypted only"}\n'
    assert not (target / "memory" / "history.jsonl").exists()
    assert not (target / ".originagent" / "evolution_identity.json").exists()
    assert EvolutionLedger(target).verify_chain().ok is True


def test_tampered_public_fields_fail_verify(tmp_path) -> None:
    vault_path, key_file, vault = _export(tmp_path)
    tampered = dict(vault)
    tampered["metadata"] = dict(vault["metadata"])
    tampered["metadata"]["passport_id"] = "0x" + "33" * 32
    vault_path.write_text(json.dumps(tampered), encoding="utf-8")

    result = verify_memory_vault(vault_path, key_file)

    assert result["ok"] is False
    assert any("vault_digest mismatch" in error for error in result["errors"])


def test_tampered_ciphertext_and_wrong_key_fail_without_writes(tmp_path) -> None:
    vault_path, key_file, vault = _export(tmp_path)
    wrong_key = tmp_path / "wrong.key"
    _write_key(wrong_key, b"x" * 32)
    target = tmp_path / "target"

    with pytest.raises(MemoryVaultError, match="authentication tag mismatch"):
        import_memory_vault(vault_path, key_file=wrong_key, target_workspace=target, apply=True)
    assert not target.exists()

    tampered = dict(vault)
    tampered["encrypted_payload_b64"] = "AA=="
    vault_path.write_text(json.dumps(tampered), encoding="utf-8")

    result = verify_memory_vault(vault_path, key_file)

    assert result["ok"] is False
    assert not target.exists()


def test_conflict_requires_replace_and_replace_preserves_other_files(tmp_path) -> None:
    vault_path, key_file, _vault = _export(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "SOUL.md").write_text("existing", encoding="utf-8")
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(MemoryVaultError, match="conflicting files"):
        import_memory_vault(vault_path, key_file=key_file, target_workspace=target, apply=True)

    import_memory_vault(
        vault_path,
        key_file=key_file,
        target_workspace=target,
        apply=True,
        replace=True,
    )

    assert (target / "SOUL.md").read_text(encoding="utf-8") == "soul"
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_broken_source_ledger_blocks_export(tmp_path) -> None:
    source = _source_workspace(tmp_path / "source")
    event_path = source / "memory" / "evolution_events.jsonl"
    line = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    line["module_id"] = "tampered"
    event_path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    key_file = tmp_path / "vault.key"
    _write_key(key_file)

    with pytest.raises(MemoryVaultError, match="source evolution ledger is broken"):
        export_memory_vault(
            source,
            passport_id=PASSPORT_ID,
            agent_key_hash=AGENT_KEY_HASH,
            key_file=key_file,
            out=tmp_path / "vault.json",
        )


def test_public_privacy_scan_rejects_forbidden_metadata(tmp_path) -> None:
    vault_path, _key_file, vault = _export(tmp_path)
    vault["metadata"]["prompt"] = "do not expose this"
    vault_path.write_text(json.dumps(vault), encoding="utf-8")

    result = verify_memory_vault(vault_path)

    assert result["ok"] is False
    assert any("forbidden private field" in error for error in result["errors"])


def test_evolution_vault_cli_export_verify_and_import(tmp_path) -> None:
    source = _source_workspace(tmp_path / "source")
    key_file = tmp_path / "vault.key"
    _write_key(key_file)
    vault_path = tmp_path / "vault.json"
    target = tmp_path / "target"

    export_result = runner.invoke(
        app,
        [
            "evolution-vault",
            "export",
            "--workspace",
            str(source),
            "--passport-id",
            PASSPORT_ID,
            "--agent-key-hash",
            AGENT_KEY_HASH,
            "--key-file",
            str(key_file),
            "--out",
            str(vault_path),
        ],
    )
    assert export_result.exit_code == 0, export_result.output
    assert json.loads(export_result.stdout)["vault_digest"]

    verify_result = runner.invoke(
        app,
        ["evolution-vault", "verify", "--vault", str(vault_path), "--key-file", str(key_file)],
    )
    assert verify_result.exit_code == 0, verify_result.output
    assert json.loads(verify_result.stdout)["decrypted"] is True

    dry_run = runner.invoke(
        app,
        [
            "evolution-vault",
            "import",
            "--vault",
            str(vault_path),
            "--key-file",
            str(key_file),
            "--target-workspace",
            str(target),
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.stdout)["dry_run"] is True
    assert not (target / "SOUL.md").exists()

    applied = runner.invoke(
        app,
        [
            "evolution-vault",
            "import",
            "--vault",
            str(vault_path),
            "--key-file",
            str(key_file),
            "--target-workspace",
            str(target),
            "--apply",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["dry_run"] is False
    assert (target / "SOUL.md").read_text(encoding="utf-8") == "soul"
