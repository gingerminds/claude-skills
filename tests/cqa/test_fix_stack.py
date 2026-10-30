import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cqa import fix_stack
from tests.cqa.test_cli_fix import FixCase

VERSION_PHP = "<?php\n$wp_version = '{v}';\n$wp_db_version = 1;\n"


def make_tar(members) -> bytes:
    """members: (name, kind, payload) with kind file, dir, symlink or hardlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = payload.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                info.linkname = payload
                archive.addfile(info)
    return buffer.getvalue()


def wordpress_tar(version: str) -> bytes:
    return make_tar([
        ("wordpress", "dir", None),
        ("wordpress/wp-includes", "dir", None),
        ("wordpress/wp-includes/version.php", "file", VERSION_PHP.format(v=version)),
        ("wordpress/index.php", "file", "<?php"),
    ])


class FakeNetwork:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def __call__(self, url, limit=fix_stack.MAX_ARCHIVE):
        self.calls.append(url)
        if url not in self.files:
            raise OSError("404")
        return self.files[url]


def network_for(version, archive):
    url = fix_stack.archive_url(version)
    digest = hashlib.sha1(archive).hexdigest()
    return FakeNetwork({url: archive, url + ".sha1": f"{digest}\n".encode()})


class StackCase(FixCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "cache"
        patcher = mock.patch.object(fix_stack, "cache_root", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_dir(self, stack="wordpress"):
        repo = self.root / "dev" / "site"
        repo.mkdir(parents=True)
        run = self.root / "run"
        (run / "fix").mkdir(parents=True)
        (run / "context.json").write_text(json.dumps({"repos": [
            {"name": "site", "path": str(repo), "stack": stack}]}), encoding="utf-8")
        self.repo = repo
        return run

    def stack_core(self, run, *extra, network=None):
        with mock.patch.object(fix_stack, "fetch", network or FakeNetwork({})):
            code, out, err = self.run_main(["stack-core", run, *extra])
        return code, (json.loads(out) if out.strip() else None), err


class StackCoreTest(StackCase):
    def test_core_found_in_the_dev_repo(self):
        run = self.run_dir()
        core = self.repo / "web" / "wp" / "wp-includes"
        core.mkdir(parents=True)
        (core / "version.php").write_text(VERSION_PHP.format(v="6.5.2"), encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual(code, 0)
        self.assertEqual((result["stack"], result["version"], result["found"]),
                         ("wordpress", "6.5.2", True))
        self.assertEqual(result["path"], str((self.repo / "web" / "wp").resolve()))
        state_file = json.loads((run / "fix" / "stack-core.json").read_text(encoding="utf-8"))
        self.assertEqual(state_file["site"]["path"], result["path"])

    def test_core_is_recorded_in_the_pass_state(self):
        run = self.run_dir()
        (self.repo / "wp-includes").mkdir()
        (self.repo / "wp-includes" / "version.php").write_text(
            VERSION_PHP.format(v="6.4"), encoding="utf-8")
        (run / "fix" / "state.json").write_text(json.dumps({
            "started_at": "x", "finished_at": None, "stage": "keys", "mode": "present",
            "push": "dry_run", "notion": False, "keys": {}}), encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual(code, 0, result)
        state = json.loads((run / "fix" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stack_core"]["site"]["path"], result["path"])

    def test_version_from_composer_and_dockerfile(self):
        run = self.run_dir()
        (self.repo / "composer.json").write_text(json.dumps({
            "require": {"johnpbloch/wordpress": "6.4.3"}}), encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual((code, result["version"], result["found"]), (0, "6.4.3", False))
        (self.repo / "composer.json").write_text(json.dumps({
            "require": {"roots/wordpress": "^6.4"}}), encoding="utf-8")
        (self.repo / "Dockerfile").write_text(
            "FROM wordpress:6.2.1-php8.2-apache AS base\n", encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual(result["version"], "6.2.1")
        (self.repo / "composer.lock").write_text(json.dumps({"packages": [
            {"name": "roots/wordpress", "version": "6.5.1"}]}), encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual(result["version"], "6.5.1")

    def test_download_checks_sha1_and_extracts_safely(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        network = network_for("6.5.2", wordpress_tar("6.5.2"))
        code, result, err = self.stack_core(run, "--download", network=network)
        self.assertEqual(code, 0, err)
        target = self.cache / "wordpress" / "6.5.2"
        self.assertEqual(Path(result["path"]), target.resolve())
        self.assertTrue(result["found"])
        self.assertTrue((target / "wp-includes" / "version.php").is_file())
        self.assertEqual(sorted(p.name for p in (self.cache / "wordpress").iterdir()), ["6.5.2"])
        self.assertEqual(network.calls, [fix_stack.archive_url("6.5.2"),
                                         fix_stack.archive_url("6.5.2") + ".sha1"])
        again = FakeNetwork({})
        code, result, _ = self.stack_core(run, "--download", network=again)
        self.assertEqual((code, again.calls), (0, []))
        self.assertTrue(result["found"])

    def test_sha1_mismatch_leaves_nothing(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        network = network_for("6.5.2", wordpress_tar("6.5.2"))
        network.files[fix_stack.archive_url("6.5.2") + ".sha1"] = b"0" * 40 + b"\n"
        code, result, err = self.stack_core(run, "--download", network=network)
        self.assertNotEqual(code, 0)
        self.assertIn("empreinte", err)
        self.assertEqual(list(self.cache.rglob("*")), [])

    def test_malicious_archives_are_refused(self):
        outside = self.cache / "wordpress" / "evil"
        cases = {
            "parent": [("../evil", "file", "x")],
            "nested parent": [("wordpress/../../evil", "file", "x")],
            "absolute": [("/tmp/evil-absolute", "file", "x")],
            "symlink out": [("wordpress/link", "symlink", "/etc")],
            "symlink dotdot": [("wordpress/link", "symlink", "../../..")],
            "hardlink out": [("wordpress/h", "hardlink", "/etc/passwd")],
            "hardlink missing": [("wordpress/h", "hardlink", "wordpress/nothing")],
            "link inside": [("wordpress", "dir", None), ("wordpress/a", "file", "x"),
                            ("wordpress/l", "symlink", "a")],
            "symlink chain": [("wordpress", "dir", None), ("wordpress/a", "symlink", "b"),
                              ("wordpress/b", "symlink", "../.."),
                              ("wordpress/a/evil", "file", "x")],
        }
        for label, members in cases.items():
            with self.subTest(label):
                run = self.run_dir() if not (self.root / "run").exists() else self.root / "run"
                (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
                network = network_for("6.5.2", make_tar(members))
                code, _, err = self.stack_core(run, "--download", network=network)
                self.assertEqual(code, 5, err)
                self.assertFalse(outside.exists())
                self.assertFalse(Path("/tmp/evil-absolute").exists())
                self.assertEqual(list(self.cache.rglob("*")), [])

    def test_top_level_wordpress_symlink_is_refused(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        members = [("wordpress", "symlink", "/etc")]
        network = network_for("6.5.2", make_tar(members))
        code, _, err = self.stack_core(run, "--download", network=network)
        self.assertEqual(code, 5, err)
        self.assertEqual(list(self.cache.rglob("*")), [])

    def test_missing_data_filter_refuses_cleanly(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        network = network_for("6.5.2", wordpress_tar("6.5.2"))
        with mock.patch.object(fix_stack, "has_data_filter", return_value=False):
            code, _, err = self.stack_core(run, "--download", network=network)
        self.assertEqual(code, 2)
        self.assertIn("Python", err)
        self.assertEqual(network.calls, [])

    def test_http_exception_is_a_clean_failure(self):
        import http.client

        opener = mock.Mock()
        opener.open.side_effect = http.client.BadStatusLine("x")
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(OSError):
                fix_stack.fetch("https://wordpress.org/wordpress-6.5.2.tar.gz")

    def test_full_width_digits_are_not_a_version(self):
        self.assertFalse(fix_stack.valid_version("\uff16.\uff15.\uff12"))
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:\uff16.\uff15\n", encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertIsNone(result["version"])

    def test_existing_target_with_the_marker_is_never_replaced(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        network = network_for("6.5.2", wordpress_tar("6.5.2"))
        target = self.cache / "wordpress" / "6.5.2"
        real = fix_stack._safe_members

        def appear(tar):
            (target / "wp-includes").mkdir(parents=True)
            (target / "wp-includes" / "version.php").write_text("mine", encoding="utf-8")
            return real(tar)

        with mock.patch.object(fix_stack, "_safe_members", side_effect=appear):
            code, result, err = self.stack_core(run, "--download", network=network)
        self.assertEqual(code, 0, err)
        self.assertEqual((target / "wp-includes" / "version.php").read_text(), "mine")
        self.assertEqual(sorted(p.name for p in (self.cache / "wordpress").iterdir()), ["6.5.2"])

    def test_stale_work_dirs_are_cleaned(self):
        import os
        import time
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        parent = self.cache / "wordpress"
        old, fresh = parent / ".tmp-old", parent / ".tmp-fresh"
        old.mkdir(parents=True)
        fresh.mkdir()
        past = time.time() - 3 * 86400
        os.utime(old, (past, past))
        network = network_for("6.5.2", wordpress_tar("6.5.2"))
        self.stack_core(run, "--download", network=network)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_bad_version_is_refused_before_any_download(self):
        for version in ("6.5.2/../../x", "6.5.2-rc1", "latest", "6", "6.5.2" + ".1", ""):
            with self.subTest(version=version):
                self.assertFalse(fix_stack.valid_version(version))
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        network = FakeNetwork({})
        with mock.patch.object(fix_stack, "read_version", return_value="../../x"):
            code, _, err = self.stack_core(run, "--download", network=network)
        self.assertEqual(code, 2)
        self.assertEqual(network.calls, [])
        with self.assertRaises(ValueError):
            fix_stack.download("1.0/../x", self.cache, FakeNetwork({}))

    def test_download_without_a_version_is_refused(self):
        run = self.run_dir()
        code, _, err = self.stack_core(run, "--download")
        self.assertEqual(code, 2)
        self.assertIn("Version", err)

    def test_path_is_checked(self):
        run = self.run_dir()
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = self.stack_core(run, "--path", empty)
        self.assertEqual(code, 2)
        code, _, err = self.stack_core(run, "--path", self.root / "missing")
        self.assertEqual(code, 2)
        core = self.root / "core-copy"
        (core / "wp-includes").mkdir(parents=True)
        (core / "wp-includes" / "version.php").write_text(
            VERSION_PHP.format(v="6.3.1"), encoding="utf-8")
        link = self.root / "link"
        link.symlink_to(core)
        code, result, _ = self.stack_core(run, "--path", link)
        self.assertEqual(code, 0)
        self.assertEqual(Path(result["path"]), core.resolve())
        self.assertEqual(result["version"], "6.3.1")
        self.assertTrue(result["found"])

    def test_core_beside_the_repo_is_ignored(self):
        run = self.run_dir()
        (self.repo / "Dockerfile").write_text("FROM wordpress:6.5.2\n", encoding="utf-8")
        other = self.repo.parent / "other-project" / "wp-includes"
        other.mkdir(parents=True)
        (other / "version.php").write_text(VERSION_PHP.format(v="5.9.3"), encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual((result["found"], result["version"]), (False, "6.5.2"))
        self.assertNotIn("dev", json.dumps(result))

    def test_a_later_call_keeps_the_earlier_choice_of_the_repo(self):
        run = self.run_dir()
        core = self.root / "chosen"
        (core / "wp-includes").mkdir(parents=True)
        (core / "wp-includes" / "version.php").write_text(
            VERSION_PHP.format(v="6.3.1"), encoding="utf-8")
        self.stack_core(run, "--path", core)
        code, result, _ = self.stack_core(run)
        self.assertFalse(result["found"])
        record = json.loads((run / "fix" / "stack-core.json").read_text(encoding="utf-8"))
        self.assertEqual(record["site"]["path"], str(core.resolve()))

    def test_open_carries_the_choice_into_the_new_state(self):
        run = self.run_dir()
        core = self.root / "chosen"
        (core / "wp-includes").mkdir(parents=True)
        (core / "wp-includes" / "version.php").write_text(
            VERSION_PHP.format(v="6.3.1"), encoding="utf-8")
        self.stack_core(run, "--path", core)
        (run / "fix" / "candidates.json").write_text(json.dumps({"keys": [{
            "key": "site", "repo": "site", "push_ready": True,
            "sections": [{"alias": "securite", "category": "SÉCURITÉ",
                          "rows": [{"ref": 1}]}]}]}), encoding="utf-8")
        code, _, err = self.run_main(["open", run, "--mode", "present", "--push", "dry_run",
                                      "--notion", "no"])
        self.assertEqual(code, 0, err)
        state = json.loads((run / "fix" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stack_core"]["site"]["path"], str(core.resolve()))

    def test_drupal_is_never_downloaded(self):
        run = self.run_dir("drupal")
        code, _, err = self.stack_core(run, "--download")
        self.assertEqual(code, 2)
        code, result, _ = self.stack_core(run)
        self.assertEqual((code, result["stack"], result["found"]), (0, "drupal", False))
        self.assertTrue(result["note"])
        drupal = self.repo / "web" / "core" / "lib"
        drupal.mkdir(parents=True)
        (drupal / "Drupal.php").write_text("<?php\nconst VERSION = '10.2.3';\n", encoding="utf-8")
        code, result, _ = self.stack_core(run)
        self.assertEqual((result["found"], result["version"]), (True, "10.2.3"))
        self.assertEqual(Path(result["path"]), (self.repo / "web").resolve())


class RedirectTest(unittest.TestCase):
    def test_redirect_to_another_host_is_refused(self):
        handler = fix_stack._SameHost()
        request = mock.Mock(full_url="https://wordpress.org/a")
        with self.assertRaises(fix_stack.StackRefused):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/a")
        with self.assertRaises(fix_stack.StackRefused):
            handler.redirect_request(request, None, 302, "Found", {}, "http://wordpress.org/a")

    def test_non_https_url_is_refused(self):
        with self.assertRaises(fix_stack.StackRefused):
            fix_stack.fetch("http://wordpress.org/wordpress-6.5.2.tar.gz")


if __name__ == "__main__":
    unittest.main()
