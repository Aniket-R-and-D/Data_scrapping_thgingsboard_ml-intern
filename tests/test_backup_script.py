"""Tests for scripts/backup_release_artifacts.ps1 (temporary files only).

Drives powershell.exe via subprocess argv (no shell parsing). Skips when
powershell is unavailable. Never touches the real models/ or data/ trees.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_release_artifacts.ps1"
POWERSHELL = shutil.which("powershell")

needs_ps = pytest.mark.skipif(POWERSHELL is None, reason="powershell not available")


def _run(*args: str) -> tuple[int, str]:
    p = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(SCRIPT), *args],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout.replace("\r\n", "\n")


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    (d / "sub").mkdir(parents=True)
    (d / "a.bin").write_bytes(b"alpha-1")
    (d / "sub" / "b.bin").write_bytes(b"beta-22")
    return d


@needs_ps
def test_backup_and_verify_roundtrip(tmp_path, src):
    dst = tmp_path / "dst"
    rc, out = _run("-Destination", str(dst), "-SourceRoot", str(src), "-Files", "a.bin,sub/b.bin")
    assert rc == 0, out
    assert (dst / "a.bin").read_bytes() == b"alpha-1"
    assert (dst / "sub" / "b.bin").read_bytes() == b"beta-22"
    manifest = json.loads((dst / "hashes.json").read_text(encoding="utf-8"))
    assert manifest["source_root"] == str(src)
    by_rel = {f["rel"]: f for f in manifest["files"]}
    assert set(by_rel) == {"a.bin", "sub/b.bin"}
    assert by_rel["a.bin"]["sha256"] == hashlib.sha256(b"alpha-1").hexdigest()
    assert by_rel["a.bin"]["bytes"] == 7
    rc2, out2 = _run("-Destination", str(dst), "-Verify")
    assert rc2 == 0, out2
    assert "VERIFY PASS" in out2


@needs_ps
def test_verify_detects_tamper(tmp_path, src):
    dst = tmp_path / "dst"
    assert _run("-Destination", str(dst), "-SourceRoot", str(src), "-Files", "a.bin")[0] == 0
    (dst / "a.bin").write_bytes(b"tampered!")
    rc, out = _run("-Destination", str(dst), "-Verify")
    assert rc == 1
    assert "HASH MISMATCH: a.bin" in out


@needs_ps
def test_backup_aborts_on_missing_source(tmp_path, src):
    dst = tmp_path / "dst"
    rc, out = _run("-Destination", str(dst), "-SourceRoot", str(src), "-Files", "a.bin,nope.bin")
    assert rc == 1
    assert "MISSING: nope.bin" in out
    assert not (dst / "hashes.json").exists()


@needs_ps
def test_pin_mismatch_aborts(tmp_path, src):
    cfg = tmp_path / "release.yaml"
    cfg.write_text(
        "model_versions:\n"
        "  demo:\n"
        '    path: "a.bin"\n'
        '    sha256: "0000000000000000000000000000000000000000000000000000000000000000"\n',
        encoding="utf-8")
    dst = tmp_path / "dst"
    rc, out = _run("-Destination", str(dst), "-SourceRoot", str(src),
                   "-ReleaseConfig", str(cfg))
    assert rc == 1
    assert "PIN MISMATCH" in out


@needs_ps
def test_pin_match_allows_backup(tmp_path, src):
    digest = hashlib.sha256(b"alpha-1").hexdigest()
    cfg = tmp_path / "release.yaml"
    cfg.write_text(
        "model_versions:\n"
        "  demo:\n"
        '    path: "a.bin"\n'
        f'    sha256: "{digest}"\n',
        encoding="utf-8")
    dst = tmp_path / "dst"
    rc, out = _run("-Destination", str(dst), "-SourceRoot", str(src),
                   "-ReleaseConfig", str(cfg))
    assert rc == 0, out
    assert _run("-Destination", str(dst), "-Verify")[0] == 0


@needs_ps
def test_verify_without_manifest_fails(tmp_path):
    rc, out = _run("-Destination", str(tmp_path / "empty"), "-Verify")
    assert rc == 1
    assert "no hashes.json" in out
