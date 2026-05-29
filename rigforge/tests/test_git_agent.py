"""Tests for rigforge.git_agent — read-only git introspection module."""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from rigforge.git_agent import (
    GitStatus,
    GitCommit,
    _scrub_url,
    git_status,
    git_log,
    git_changed_files,
)


# ── _scrub_url ─────────────────────────────────────────────────────────


class TestScrubUrl:
    def test_scrubs_user_and_password(self):
        url = "https://user:pass@github.com/org/repo.git"
        assert _scrub_url(url) == "https://github.com/org/repo.git"

    def test_scrubs_token_only(self):
        url = "https://ghp_abc123@github.com/org/repo.git"
        assert _scrub_url(url) == "https://github.com/org/repo.git"

    def test_passes_through_clean_url(self):
        url = "https://github.com/org/repo.git"
        assert _scrub_url(url) == url

    def test_passes_through_ssh_url(self):
        url = "git@github.com:org/repo.git"
        assert _scrub_url(url) == url


# ── git_status ─────────────────────────────────────────────────────────


class TestGitStatus:
    def test_returns_git_status_instance(self):
        result = git_status()
        assert isinstance(result, GitStatus)

    def test_to_dict_has_required_keys(self):
        result = git_status().to_dict()
        for key in ("available", "in_repo", "branch", "commit_hash", "dirty",
                    "untracked", "staged", "unstaged", "remote_url"):
            assert key in result, f"missing key: {key}"

    def test_available_when_git_present(self):
        # git is available in the test environment
        result = git_status()
        assert result.available is True

    def test_in_repo_inside_git_tree(self, tmp_path):
        """Status should show in_repo=True when run inside a git tree."""
        # Init a temporary git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        result = git_status(cwd=str(tmp_path))
        assert result.in_repo is True

    def test_not_in_repo_outside_git_tree(self, tmp_path):
        """Status should show in_repo=False when run outside a git tree."""
        result = git_status(cwd=str(tmp_path))
        assert result.in_repo is False
        assert result.error is not None

    def test_git_unavailable(self):
        with patch("rigforge.git_agent._git_available", return_value=False):
            result = git_status()
        assert result.available is False
        assert result.in_repo is False
        assert result.error is not None

    def test_dirty_flag_with_staged_changes(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        # Create initial commit so HEAD exists
        (tmp_path / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Add an untracked file
        (tmp_path / "new_file.txt").write_text("untracked")
        result = git_status(cwd=str(tmp_path))
        assert result.dirty is True
        assert result.untracked == 1

    def test_clean_repo_not_dirty(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        (tmp_path / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        result = git_status(cwd=str(tmp_path))
        assert result.dirty is False

    def test_remote_url_scrubbed(self, tmp_path):
        """Remote URLs with credentials must be scrubbed."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://token@github.com/org/repo.git"],
            cwd=tmp_path, capture_output=True,
        )
        result = git_status(cwd=str(tmp_path))
        if result.remote_url is not None:
            assert "token" not in result.remote_url


# ── git_log ────────────────────────────────────────────────────────────


class TestGitLog:
    def test_returns_list(self):
        result = git_log()
        assert isinstance(result, list)

    def test_git_commit_fields(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        (tmp_path / "f.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "first commit"],
                       cwd=tmp_path, capture_output=True)
        result = git_log(n=1, cwd=str(tmp_path))
        assert len(result) == 1
        commit = result[0]
        assert isinstance(commit, GitCommit)
        assert commit.hash
        assert commit.subject == "first commit"
        assert "@" in commit.author  # email format

    def test_returns_empty_when_git_unavailable(self):
        with patch("rigforge.git_agent._git_available", return_value=False):
            result = git_log()
        assert result == []


# ── git_changed_files ──────────────────────────────────────────────────


class TestGitChangedFiles:
    def test_returns_list(self):
        result = git_changed_files()
        assert isinstance(result, list)

    def test_returns_empty_when_git_unavailable(self):
        with patch("rigforge.git_agent._git_available", return_value=False):
            result = git_changed_files()
        assert result == []

    def test_returns_changed_files(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=tmp_path, capture_output=True)
        (tmp_path / "a.txt").write_text("original")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        # Modify the committed file
        (tmp_path / "a.txt").write_text("modified")
        result = git_changed_files(cwd=str(tmp_path))
        assert "a.txt" in result
