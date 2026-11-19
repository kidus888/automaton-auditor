import os
import subprocess
import tempfile

from src.tools.repo_tools import extract_git_history

MESSAGES = [
    "chore: scaffold environment and pyproject",
    "feat: add sandboxed clone tool and AST parser",
    "feat: wire StateGraph with parallel judges",
    "fix: reducer bug in graph state",
    "test: cover synthesis rules",
]


def _make_repo(path, timestamps, messages):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["git", "init", "-q", path], check=True, env=env)
    for i, (ts, msg) in enumerate(zip(timestamps, messages)):
        with open(os.path.join(path, f"file{i}.txt"), "w") as f:
            f.write(msg)
        subprocess.run(["git", "-C", path, "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", path, "commit", "-q", "-m", msg], check=True,
                       env={**env, "GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts})


def test_atomic_history_with_timestamps():
    with tempfile.TemporaryDirectory() as repo:
        stamps = [f"2026-01-0{d}T10:00:00+00:00" for d in range(1, 6)]
        _make_repo(repo, stamps, MESSAGES)
        ev = extract_git_history(repo)
    assert ev.found is True, ev.gaps
    assert "2026-01-01T10:00:00+00:00" in ev.content  # timestamps captured
    assert "Setup -> Tool Engineering -> Graph Orchestration" in ev.rationale


def test_clustered_timestamps_flag_bulk_upload():
    with tempfile.TemporaryDirectory() as repo:
        stamps = [f"2026-01-01T10:0{m}:00+00:00" for m in range(5)]
        _make_repo(repo, stamps, MESSAGES)
        ev = extract_git_history(repo)
    assert ev.found is False
    assert any("clustered" in g for g in ev.gaps)


def test_single_init_commit_fails():
    with tempfile.TemporaryDirectory() as repo:
        _make_repo(repo, ["2026-01-01T10:00:00+00:00"], ["init"])
        ev = extract_git_history(repo)
    assert ev.found is False
    assert any("Only 1 commit" in g for g in ev.gaps)
