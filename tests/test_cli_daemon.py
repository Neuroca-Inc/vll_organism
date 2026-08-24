"""Exercises the real `python -m vll_organism run` entrypoint as a
subprocess, not just the Python API -- this is the actual path a
supervised daemon takes, and it's the path that had two real gaps:
SIGTERM wasn't handled (only Ctrl-C/SIGINT was), and the watcher thread
was never joined on shutdown. Both are only observable by actually
starting and stopping the process, so unit-testing the functions in
isolation wouldn't have caught either.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _run_daemon(db_path, watch_dir, extra_args=()):
    return subprocess.Popen(
        [sys.executable, "-m", "vll_organism", "run",
         "--db", db_path, "--watch", watch_dir,
         "--test-embedder", "--allow-test-embedder",
         "--tick-interval", "0.2", "--poll-interval", "0.2",
         *extra_args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )


def test_sigterm_triggers_clean_shutdown_and_saves_snapshot():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")  # deliberately does not exist yet

        proc = _run_daemon(db_path, watch_dir)
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                if os.path.isdir(watch_dir):
                    break
                time.sleep(0.1)
            assert os.path.isdir(watch_dir), (
                "daemon should auto-create the watch folder rather than silently "
                "never ingesting anything -- this is the original reported symptom"
            )

            time.sleep(0.5)  # let a couple of idle ticks happen
            proc.send_signal(signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise AssertionError(
                    "process did not exit within 10s of SIGTERM -- shutdown handler is not working"
                )
            assert exit_code == 0, f"expected clean exit (0), got {exit_code}. Output:\n{proc.stdout.read()}"
        finally:
            if proc.poll() is None:
                proc.kill()

        assert os.path.isfile(db_path), "database file should have been created"

        status = subprocess.run(
            [sys.executable, "-m", "vll_organism", "status", "--db", db_path],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        )
        parsed = json.loads(status.stdout)
        assert parsed["watch_folder_ok"] is True
        assert parsed["tick"] >= 1, (
            "at least one idle tick should have run and been snapshotted before shutdown"
        )


def test_perturbation_while_ticking_does_not_corrupt_storage_transactions():
    """Reproduces a real bug found while verifying this file's other test on
    the actual subprocess path: dropping a corpus file into the watch
    folder while the tick thread is concurrently idle-ticking used to raise
    `sqlite3.OperationalError: cannot commit - no transaction is active`
    inside the tick thread (logged, swallowed by _tick_once_safely, so the
    process kept running -- but energy logging silently stopped and, if it
    happened during a `stop()`-triggered final snapshot, that snapshot
    could be lost too). Root cause: Storage wraps one sqlite3.Connection
    shared between the tick thread and the watch thread
    (check_same_thread=False), and a `storage.set_meta(...)` call added to
    scan_watch_folder() was writing to it without going through the same
    `Organism._lock` that idle_tick()/perturb_file() use to serialize all
    other access. This test drops a file mid-run (unlike the SIGTERM test
    above, which never perturbs anything and so never exercised this race)
    and asserts nothing was logged to stderr about a broken transaction,
    and that the file was actually ingested."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")

        proc = _run_daemon(db_path, watch_dir)
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not os.path.isdir(watch_dir):
                time.sleep(0.05)
            assert os.path.isdir(watch_dir)

            # Let a few idle ticks happen before AND after the drop, so the
            # write lands squarely in the middle of concurrent tick activity
            # rather than racing the very first tick.
            time.sleep(0.3)
            with open(os.path.join(watch_dir, "note.txt"), "w") as f:
                f.write("a perturbation dropped while the tick thread is live")
            time.sleep(0.6)

            proc.send_signal(signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise AssertionError("process did not exit within 10s of SIGTERM")
            output = proc.stdout.read()
            assert exit_code == 0, f"expected clean exit (0), got {exit_code}. Output:\n{output}"
            assert "cannot commit" not in output, (
                f"tick thread and watch thread raced on the shared sqlite connection:\n{output}"
            )
            assert "OperationalError" not in output, f"unexpected storage error:\n{output}"
        finally:
            if proc.poll() is None:
                proc.kill()

        status = subprocess.run(
            [sys.executable, "-m", "vll_organism", "status", "--db", db_path],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        )
        parsed = json.loads(status.stdout)
        assert parsed["chunks"] >= 1, "the file dropped mid-run should have been ingested"


def test_conflicting_watch_path_fails_fast_with_clear_error():
    """A --watch path that's a file, not a directory, must fail immediately
    at startup with a clear message and nonzero exit -- not hang, not
    silently do nothing."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        conflict = os.path.join(td, "conflict")
        with open(conflict, "w") as f:
            f.write("this is a file")

        result = subprocess.run(
            [sys.executable, "-m", "vll_organism", "run",
             "--db", db_path, "--watch", conflict,
             "--test-embedder", "--allow-test-embedder"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
        assert "not a directory" in (result.stdout + result.stderr)
