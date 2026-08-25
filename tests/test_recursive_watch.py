from __future__ import annotations

import os
import threading

import pytest

from vll_organism.organism import Organism, PerturbResult


class _StorageStub:
    def __init__(self) -> None:
        self.meta: dict[str, str] = {}

    def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value


class _WatchHarness:
    """Minimum state required by Organism.scan_watch_folder()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.storage = _StorageStub()
        self._known_files: dict[str, float] = {}
        self.watch_folder_ok = None
        self._watch_folder_missing_warned = False
        self.seen: list[str] = []

    def perturb_file(self, path: str) -> PerturbResult:
        self.seen.append(os.path.abspath(path))
        return PerturbResult(new_chunks=1, retry_required=False)


def _scan(harness: _WatchHarness, folder: str) -> int:
    return Organism.scan_watch_folder(harness, folder)


def test_watch_scan_recurses_into_dropped_directories(tmp_path) -> None:
    drop = tmp_path / "corpus_drop"

    top = drop / "top.txt"
    nested = drop / "open_logic" / "proofs" / "proof.tex"
    deeper = drop / "rust_reference" / "src" / "expressions" / "call.md"

    for path, text in (
        (top, "top-level corpus text"),
        (nested, "nested proof corpus text"),
        (deeper, "deeply nested reference corpus text"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    harness = _WatchHarness()

    perturbed = _scan(harness, str(drop))

    expected = {
        os.path.abspath(top),
        os.path.abspath(nested),
        os.path.abspath(deeper),
    }

    assert perturbed == 3
    assert set(harness.seen) == expected
    assert harness.watch_folder_ok is True
    assert harness.storage.meta["watch_folder_ok"] == "1"


def test_watch_scan_does_not_reingest_unchanged_nested_files(tmp_path) -> None:
    drop = tmp_path / "corpus_drop"
    nested = drop / "book" / "chapter" / "section.md"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("stable nested corpus text", encoding="utf-8")

    harness = _WatchHarness()

    assert _scan(harness, str(drop)) == 1
    assert _scan(harness, str(drop)) == 0

    assert harness.seen == [os.path.abspath(nested)]


def test_watch_scan_reingests_changed_nested_file(tmp_path) -> None:
    drop = tmp_path / "corpus_drop"
    nested = drop / "book" / "chapter.md"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("first version", encoding="utf-8")

    harness = _WatchHarness()

    assert _scan(harness, str(drop)) == 1

    original_mtime = os.path.getmtime(nested)
    nested.write_text("second version with changed content", encoding="utf-8")

    # Force a distinct mtime even on coarse timestamp filesystems.
    os.utime(nested, (original_mtime + 2.0, original_mtime + 2.0))

    assert _scan(harness, str(drop)) == 1
    assert harness.seen == [
        os.path.abspath(nested),
        os.path.abspath(nested),
    ]


def test_watch_scan_does_not_follow_directory_symlinks_outside_drop(tmp_path) -> None:
    drop = tmp_path / "corpus_drop"
    drop.mkdir()

    inside = drop / "inside.txt"
    inside.write_text("inside", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("must not be discovered", encoding="utf-8")

    link = drop / "external_link"

    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")

    harness = _WatchHarness()

    assert _scan(harness, str(drop)) == 1
    assert harness.seen == [os.path.abspath(inside)]
    assert os.path.abspath(outside_file) not in harness.seen
