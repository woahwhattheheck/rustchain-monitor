from pathlib import Path

source_path = Path("reward_reconciliation.py")
source = source_path.read_text(encoding="utf-8")

replacements = [
    (
        '    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")',
        '    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")',
        "stable snapshot",
    ),
    (
        '''    if not stat.S_ISREG(path_info.st_mode):
        raise ReconciliationError(f"JSON input must be a regular file: {source}")

    flags = (''',
        '''    if not stat.S_ISREG(path_info.st_mode):
        raise ReconciliationError(f"JSON input must be a regular file: {source}")
    if path_info.st_nlink != 1:
        raise ReconciliationError(f"JSON input must have exactly one hard link: {source}")

    flags = (''',
        "path single-link",
    ),
    (
        '''        if not stat.S_ISREG(before.st_mode):
            raise ReconciliationError(f"JSON input must be a regular file: {source}")
        if not _stable_file_snapshot(path_info, before):''',
        '''        if not stat.S_ISREG(before.st_mode):
            raise ReconciliationError(f"JSON input must be a regular file: {source}")
        if before.st_nlink != 1:
            raise ReconciliationError(f"JSON input must have exactly one hard link: {source}")
        if not _stable_file_snapshot(path_info, before):''',
        "descriptor single-link",
    ),
    (
        '''    if stat.S_ISLNK(path_after.st_mode) or (reparse and after_attrs & reparse):
        raise ReconciliationError(f"JSON input path changed while being read: {source}")
    if len(payload) > MAX_JSON_BYTES:''',
        '''    if stat.S_ISLNK(path_after.st_mode) or (reparse and after_attrs & reparse):
        raise ReconciliationError(f"JSON input path changed while being read: {source}")
    if after.st_nlink != 1 or path_after.st_nlink != 1:
        raise ReconciliationError(f"JSON input must have exactly one hard link: {source}")
    if len(payload) > MAX_JSON_BYTES:''',
        "post-read single-link",
    ),
]
for old, new, label in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"{label} preimage mismatch: {source.count(old)}")
    source = source.replace(old, new, 1)
source_path.write_text(source, encoding="utf-8")

test_path = Path("tests/test_reward_reconciliation_input_boundary.py")
tests = test_path.read_text(encoding="utf-8")
marker = "    def test_invalid_utf8_is_normalized_to_reconciliation_error(self):\n"
test = '''    def test_hard_link_fails_closed_and_preserves_both_names(self):
        if not hasattr(os, "link"):
            self.skipTest("hard links unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "source.json"
            alias = root / "source-alias.json"
            payload = json.dumps(self.valid_source()).encode("utf-8")
            path.write_bytes(payload)
            try:
                os.link(path, alias)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            self.assertEqual(path.stat().st_nlink, 2)
            with self.assertRaisesRegex(ReconciliationError, "exactly one hard link"):
                load_json_strict(path)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(alias.read_bytes(), payload)
            self.assertTrue(path.exists())
            self.assertTrue(alias.exists())

'''
if tests.count(marker) != 1:
    raise SystemExit(f"test insertion preimage mismatch: {tests.count(marker)}")
test_path.write_text(tests.replace(marker, test + marker, 1), encoding="utf-8")

docs_path = Path("docs/reward-reconciliation.md")
docs = docs_path.read_text(encoding="utf-8")
doc_replacements = [
    (
        "Portable source files and verification artifacts must be stable regular UTF-8 files.",
        "Portable source files and verification artifacts must be stable, single-link regular UTF-8 files.",
    ),
    (
        "The reader refuses final-component symlinks/reparse points and special files, binds the opened descriptor to the inspected path, rejects mutation during the read, and enforces a 64 MiB byte ceiling before JSON parsing.",
        "The reader refuses final-component symlinks/reparse points, hard-linked inputs, and special files; binds link count plus the opened descriptor to the inspected path; rejects mutation during the read; and enforces a 64 MiB byte ceiling before JSON parsing.",
    ),
]
for old, new in doc_replacements:
    if docs.count(old) != 1:
        raise SystemExit(f"docs preimage mismatch: {old!r}")
    docs = docs.replace(old, new, 1)
docs_path.write_text(docs, encoding="utf-8")
