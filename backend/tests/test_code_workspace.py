"""Source-bound changes cannot rewrite live source or invent completed tests."""

import difflib
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tauric import assistant, code_workspace


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    source = tmp_path / "backend/tauric/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    monkeypatch.setattr(assistant, "source_root", lambda: tmp_path)
    path = "backend/tauric/example.py"
    patch = "".join(
        difflib.unified_diff(
            ["value = 1\n"], ["value = 2\n"], fromfile="a/" + path, tofile="b/" + path
        )
    )
    ref = SimpleNamespace(path=path, sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    return source, SimpleNamespace(files=[ref], patch=patch)


def test_source_changes_are_real_in_workspace_and_runtime_is_untouched(workspace):
    source, proposal = workspace
    result = code_workspace.stage(proposal)
    assert result["workspace_files"]["backend/tauric/example.py"]["content"] == "value = 2\n"
    assert source.read_text() == "value = 1\n"
    assert (
        result["workspace_files"]["backend/tauric/example.py"]["syntax_validation"]
        == "python_syntax_passed"
    )
    assert result["code_changes_applied"] is True and result["runtime_code_changed"] is False
    assert (
        result["tests_executed"] is False and result["approval"] == "tauric_release_checks_required"
    )


@pytest.mark.parametrize(
    "alter", ["checksum", "context", "syntax", "undeclared", "counts", "private_path"]
)
def test_rejects_stale_inconsistent_or_out_of_scope_changes(workspace, alter):
    source, proposal = workspace
    if alter == "checksum":
        proposal.files[0].sha256 = "0" * 64
    elif alter == "context":
        proposal.patch = proposal.patch.replace("-value = 1", "-value = 7")
    elif alter == "syntax":
        proposal.patch = proposal.patch.replace("+value = 2", "+value = (")
    elif alter == "undeclared":
        proposal.patch = proposal.patch.replace(
            "b/backend/tauric/example.py", "b/backend/tauric/other.py"
        )
    elif alter == "counts":
        proposal.patch = proposal.patch.replace("@@ -1 +1 @@", "@@ -1,2 +1 @@")
    elif alter == "private_path":
        proposal.files[0].path = "../secrets/private.py"
    with pytest.raises(HTTPException):
        code_workspace.stage(proposal)
    assert source.read_text() == "value = 1\n"
