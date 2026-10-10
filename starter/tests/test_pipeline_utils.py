"""Tests for the shared pipeline helpers."""

from __future__ import annotations

import os

from quantum_lake_student.pipeline_utils import git_revision


def looks_like_a_sha(value: str) -> bool:
    if len(value) != 40:
        return False
    for character in value:
        if character not in "0123456789abcdef":
            return False
    return True


def test_git_revision_returns_a_sha_or_says_unknown():
    """run.json has to name the code that produced it, so this must never come
    back empty. It is either a real commit or an honest "unknown"."""
    revision = git_revision()
    assert revision
    assert revision == "unknown" or looks_like_a_sha(revision)


def test_git_revision_prefers_git_over_the_stamped_file(tmp_path, monkeypatch):
    """The stamped .git-revision file is a fallback and nothing updates it, so
    it can go stale. A working git must win over it.

    Skipped when git cannot answer at all, since then the stamp is all we have.
    """
    monkeypatch.chdir(tmp_path)
    real = git_revision()
    if real == "unknown":
        import pytest

        pytest.skip("git is unavailable here, so the stamp is the only source")

    stale = "0" * 40
    with open(os.path.join(tmp_path, ".git-revision"), "w") as handle:
        handle.write(stale + "\n")

    assert git_revision() == real
    assert git_revision() != stale


def test_git_revision_falls_back_to_the_stamp_when_git_cannot_answer(tmp_path, monkeypatch):
    """Inside the container git needs the repo directory compose mounts at
    /repo/.git. If that is missing, for example on a container built before
    the mount was added, the stamped file keeps run.json from saying unknown."""
    monkeypatch.chdir(tmp_path)

    def no_git(*args, **kwargs):
        raise OSError("git unavailable")

    import subprocess

    monkeypatch.setattr(subprocess, "check_output", no_git)

    stamped = "a" * 40
    with open(os.path.join(tmp_path, ".git-revision"), "w") as handle:
        handle.write(stamped + "\n")

    assert git_revision() == stamped
