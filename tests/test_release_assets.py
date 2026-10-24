import json
import tempfile
import unittest
from pathlib import Path

from tools.release_assets import application_version, digest, stage_assets


class ReleaseAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "detretlabel").mkdir()
        (self.root / "detretlabel" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
        self.output = self.root / "dist" / "windows-x64"
        self.output.mkdir(parents=True)
        files = []
        for name in ("DetRetLabel-single.exe", "DetRetLabel-single-win64.zip", "DetRetLabel-folder-win64.zip"):
            path = self.output / name
            path.write_bytes(name.encode())
            files.append({"path": name, "bytes": path.stat().st_size, "sha256": digest(path)})
        (self.output / "使用说明.txt").write_text("测试说明", encoding="utf-8")
        self.manifest = {"version": "0.1.0", "author": "H9elix", "build": "Build20261025", "files": files,
                         "smoke_tests": [{"variant": "single", "checks": 34}, {"variant": "folder", "checks": 34}]}
        self.write_manifest()

    def write_manifest(self):
        (self.output / "build-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_version_tag_and_source_metadata_must_agree(self):
        self.assertEqual(application_version(self.root, "v0.1.0"), "0.1.0")
        with self.assertRaisesRegex(ValueError, "tag must match"):
            application_version(self.root, "v9.9.9")
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must match"):
            application_version(self.root)

    def test_stage_verified_binaries_and_cross_platform_checksums(self):
        unrelated = self.output / "private-image.jpg"
        unrelated.write_bytes(b"private")
        destination = stage_assets(self.root, "v0.1.0")
        self.assertFalse((destination / unrelated.name).exists())
        self.assertTrue((destination / "USAGE.txt").exists())
        self.assertFalse((destination / "使用说明.txt").exists())
        self.assertIn("H9elix", (destination / "RELEASE_NOTES.md").read_text(encoding="utf-8"))
        checksums = (destination / "SHA256SUMS.txt").read_bytes()
        self.assertNotIn(b"\r", checksums)
        for line in checksums.decode().splitlines():
            expected, name = line.split("  ", 1)
            self.assertEqual(digest(destination / name), expected)

    def test_changed_binary_or_missing_variant_prevents_release(self):
        (self.output / "DetRetLabel-single.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "integrity"):
            stage_assets(self.root, "v0.1.0")
        self.manifest["smoke_tests"] = self.manifest["smoke_tests"][:1]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Both executable"):
            stage_assets(self.root, "v0.1.0")
