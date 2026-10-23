"""Delivery gates must preserve frozen evidence without exempting ordinary CSVs."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.dtb import check_static


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = check_static.FROZEN_EVIDENCE
FROZEN_CSV_NAMES = ("initial_root_cases.csv", "native_same_input_cases.csv")


class DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep temporary data inside the checkout, not Windows' shared temp tree.
        temporary_parent = ROOT / "artifacts" / "checks" / "evidence-tests"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix="delivery-evidence-", dir=temporary_parent,
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / BUNDLE
        self.directory.mkdir(parents=True)

    def write_bundle(self) -> None:
        # Minimal representative bundle: authentic CSV CRLF plus a non-CSV file.
        files = {
            "initial_root_cases.csv": b'case,text\r\n1,"a\r\nb"\r\n',
            "comparison.json": b'{"paired": 1}\n',
        }
        manifest = {"files": {}}
        for name, content in files.items():
            (self.directory / name).write_bytes(content)
            manifest["files"][name] = {
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        (self.directory / "bundle_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8", newline="\n",
        )

    def verify_temporary_bundle(self):
        with patch.object(check_static, "ROOT", self.root), redirect_stdout(io.StringIO()):
            return check_static.check_evidence()

    def check_temporary_line_endings(self, names, frozen_csv):
        result = SimpleNamespace(stdout="\0".join(names) + "\0")
        with (
            patch.object(check_static, "ROOT", self.root),
            patch.object(check_static, "run", return_value=result),
            redirect_stdout(io.StringIO()),
        ):
            check_static.check_line_endings(frozen_csv)

    def test_real_frozen_bundle_verifies_without_rewriting_original_bytes(self) -> None:
        with redirect_stdout(io.StringIO()):
            frozen_csv = check_static.check_evidence()
        expected = {(BUNDLE / name).as_posix() for name in FROZEN_CSV_NAMES}
        self.assertEqual(frozen_csv, expected)
        self.assertTrue(any(b"\r\n" in (ROOT / name).read_bytes() for name in expected))

    def test_same_length_corruption_and_truncation_are_rejected(self) -> None:
        for change in ("same_length", "truncated"):
            with self.subTest(change=change):
                self.write_bundle()
                path = self.directory / "initial_root_cases.csv"
                original = path.read_bytes()
                damaged = b"X" + original[1:] if change == "same_length" else original[:-1]
                path.write_bytes(damaged)
                with self.assertRaisesRegex(RuntimeError, "Frozen evidence changed"):
                    self.verify_temporary_bundle()

    def test_non_csv_evidence_corruption_also_blocks_the_bundle(self) -> None:
        self.write_bundle()
        (self.directory / "comparison.json").write_bytes(b'{"paired": 2}\n')
        with self.assertRaisesRegex(RuntimeError, "Frozen evidence changed"):
            self.verify_temporary_bundle()

    def test_missing_listed_file_is_an_error_not_a_skip(self) -> None:
        self.write_bundle()
        (self.directory / "initial_root_cases.csv").unlink()
        with self.assertRaises((OSError, RuntimeError)):
            self.verify_temporary_bundle()

    def test_missing_or_invalid_manifest_cannot_grant_an_exception(self) -> None:
        with self.assertRaises((OSError, RuntimeError)):
            self.verify_temporary_bundle()
        (self.directory / "bundle_manifest.json").write_text("{", encoding="utf-8")
        with self.assertRaises((ValueError, RuntimeError)):
            self.verify_temporary_bundle()

    def test_empty_or_outside_manifest_entries_are_rejected(self) -> None:
        for files in ({}, {"../outside.csv": {}}, {"C:/outside.csv": {}}):
            with self.subTest(files=files):
                (self.directory / "bundle_manifest.json").write_text(
                    json.dumps({"files": files}), encoding="utf-8",
                )
                with self.assertRaises(RuntimeError):
                    self.verify_temporary_bundle()

    def test_verified_csv_keeps_crlf_while_normal_lf_csv_remains_valid(self) -> None:
        self.write_bundle()
        normal = self.root / "validation/ordinary.csv"
        normal.parent.mkdir(parents=True)
        normal.write_bytes(b"name,value\na,1\n")
        verified = self.verify_temporary_bundle()
        self.assertEqual(verified, {(BUNDLE / "initial_root_cases.csv").as_posix()})
        self.check_temporary_line_endings(
            [*verified, normal.relative_to(self.root).as_posix()], verified,
        )

    def test_unlisted_csv_cannot_inherit_the_frozen_line_ending_exception(self) -> None:
        self.write_bundle()
        verified = self.verify_temporary_bundle()
        for relative in (
            BUNDLE / "ordinary.csv",
            Path("docs/report/closeout/evidence/ordinary.csv"),
            Path("validation/ordinary.csv"),
        ):
            with self.subTest(path=relative.as_posix()):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"name,value\r\na,1\r\n")
                with self.assertRaisesRegex(RuntimeError, "Non-LF text"):
                    self.check_temporary_line_endings([relative.as_posix()], verified)

    def test_git_attributes_exempt_only_the_named_report_csvs(self) -> None:
        frozen = [(BUNDLE / name).as_posix() for name in FROZEN_CSV_NAMES]
        ordinary = [
            (BUNDLE / "ordinary.csv").as_posix(),
            (BUNDLE / "generated/ordinary.csv").as_posix(),
            "docs/report/closeout/evidence/ordinary.csv",
            "validation/ordinary.csv",
        ]
        result = subprocess.run(
            ["git", "check-attr", "-z", "text", "eol", "whitespace", "--", *frozen, *ordinary],
            cwd=ROOT, stdin=subprocess.DEVNULL, check=True,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        fields = result.stdout.rstrip("\0").split("\0")
        self.assertEqual(len(fields) % 3, 0)
        attributes = {
            (fields[index], fields[index + 1]): fields[index + 2]
            for index in range(0, len(fields), 3)
        }
        for name in frozen:
            with self.subTest(frozen=name):
                self.assertEqual(attributes[name, "text"], "unset")
                self.assertEqual(attributes[name, "whitespace"], "unset")
        for name in ordinary:
            with self.subTest(ordinary=name):
                self.assertEqual(attributes[name, "text"], "auto")
                self.assertEqual(attributes[name, "eol"], "lf")
                self.assertEqual(attributes[name, "whitespace"], "unspecified")


if __name__ == "__main__":
    unittest.main()
