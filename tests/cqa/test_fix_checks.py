import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cqa import fix_checks, fix_names, fix_state
from tests.cqa.test_fix_git import GitCase

KEY = "front/securite"
PHP = "Syntaxe PHP (php -l)"
JS = "Syntaxe JavaScript (node --check)"
STYLE = "stylelint (configuration du projet)"
WHY = "à lancer dans l'environnement du projet (shared/runner.md)"
UNAVAILABLE = "boîte à outils indisponible"
NO_CONFIG = "configuration du projet non chargée"


def write(root, path, content):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def commit_files(case, worktree, message, files):
    for path, content in files.items():
        write(worktree, path, content)
        fix_names.git(worktree, subprocess.run, "add", "--", path)
    result = fix_names.git(worktree, subprocess.run, "commit", "-q", "-m", message)
    case.assertEqual(result.returncode, 0, result.stderr)


def done(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class ProjectCommandsTest(unittest.TestCase):
    def test_project_commands_from_makefile_composer_npm_and_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "Makefile", "lint:\n\tphpcs\n\nbuild:\n\techo\ntest :\n\tphpunit\nphpcs:\n\tx\n")
            write(root, "composer.json", json.dumps({"scripts": {"test": "x", "phpcs": "y", "post-install": "z"}}))
            write(root, "package.json", json.dumps({"scripts": {"lint": "x", "stylelint": "y", "build": "z"}}))
            write(root, ".husky/pre-commit", "npm test\n")
            write(root, "grumphp.yml", "grumphp: {}\n")
            write(root, "captainhook.json", "{}")
            commands = fix_checks.project_commands(root)
        self.assertEqual(
            [item["command"] for item in commands],
            ["make lint", "make test", "make phpcs", "composer test", "composer phpcs",
             "npm run lint", "npm run stylelint", "sh .husky/pre-commit", "vendor/bin/grumphp run",
             "vendor/bin/captainhook hook:pre-commit"],
        )
        self.assertIn("Hooks husky (pre-commit)", [item["name"] for item in commands])
        self.assertTrue(all(set(item) == {"name", "command"} for item in commands))

    def test_project_commands_ignore_make_variables_and_husky_without_pre_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "Makefile", "test := x\nlint ?= y\nphpcs ::= z\nlint::\n\tx\n")
            write(tmp, ".husky/commit-msg", "x\n")
            commands = fix_checks.project_commands(Path(tmp))
        self.assertEqual([item["command"] for item in commands], ["make lint"])

    def test_project_commands_of_an_empty_or_broken_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fix_checks.project_commands(Path(tmp)), [])
            write(tmp, "composer.json", "{broken")
            write(tmp, "package.json", "[]")
            self.assertEqual(fix_checks.project_commands(Path(tmp)), [])


class RunChecksTest(unittest.TestCase):
    """run_checks with a fake launcher that writes what the toolbox would."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.check_dir = self.root / "check"
        self.worktree = self.root / "wt"
        self.worktree.mkdir()
        self.calls = []
        self.tsv = ""
        self.configs = []
        self.report = []
        self.code = 0

    def fake(self, command, **kwargs):
        self.calls.append(command)
        if command[0] == "bash":
            if self.code != 0:
                return done(self.code)
            check_dir = Path(command[3])
            (check_dir / "syntax.tsv").write_text(self.tsv, encoding="utf-8")
            shutil.rmtree(check_dir / "tools", ignore_errors=True)
            raw = check_dir / "tools" / "stylelint" / "raw" / "front"
            raw.mkdir(parents=True)
            for index, config in enumerate(self.configs, 1):
                (raw / f"config-{index}.json").write_text(json.dumps(config), encoding="utf-8")
            (raw / "report.json").write_text(json.dumps(self.report), encoding="utf-8")
            return done()
        raise AssertionError(command)

    def checks(self, files, added=None, third_party=None):
        return fix_checks.run_checks(
            self.fake, Path("/x/cqa-toolbox.sh"), self.check_dir, self.worktree, "front", files,
            added or {}, third_party,
        )

    def by_name(self, checks):
        return {check["name"]: check for check in checks}

    def test_launcher_gets_the_listing_and_the_context(self):
        self.tsv = ""
        self.checks(["a.php", "dir/b.js"], third_party=["web/plugins/x"])
        self.assertEqual(self.calls, [["bash", "/x/cqa-toolbox.sh", "check", str(self.check_dir),
                                       str(self.worktree), "front"]])
        self.assertEqual((self.check_dir / "files.txt").read_text(encoding="utf-8"), "a.php\ndir/b.js\n")
        self.assertEqual(json.loads((self.check_dir / "context.json").read_text(encoding="utf-8")),
                         {"repos": [{"name": "front", "third_party_paths": ["web/plugins/x"]}]})

    def test_syntax_results_become_checks(self):
        self.tsv = (
            "ok\tphp -l\ta.php\t\n"
            "ko\tphp -l\twp-content/themes/t/functions.php\tParse error\n"
            "ok\tnode --check\tb.js\t\n"
            "skip\tyaml\tc.yml\tmodule YAML absent de l'image\n"
            "ok\tjson\td.json\t\n"
        )
        checks = self.by_name(self.checks(
            ["a.php", "wp-content/themes/t/functions.php", "b.js", "c.yml", "d.json"]))
        self.assertEqual(checks[PHP], {
            "name": PHP, "status": "failed", "files": ["wp-content/themes/t/functions.php"],
            "detail": "wp-content/themes/t/functions.php : Parse error"})
        self.assertEqual(checks[JS]["status"], "passed")
        self.assertEqual(checks[JS]["files"], ["b.js"])
        self.assertEqual(checks["JSON"]["status"], "passed")
        self.assertEqual(checks["YAML"], {"name": "YAML", "status": "not_run", "files": ["c.yml"],
                                          "detail": "module YAML absent de l'image"})
        self.assertNotIn(STYLE, checks)

    def test_a_check_without_a_changed_file_is_left_out(self):
        self.tsv = "ok\tjson\td.json\t\n"
        self.assertEqual([check["name"] for check in self.checks(["d.json"])], ["JSON"])
        self.assertEqual(self.checks(["README.md"]), [])

    def test_stylelint_counts_only_errors_on_added_lines(self):
        files = ["css/a.css", "css/b.css"]
        self.configs = [{"source": "front/.stylelintrc.json", "applied": "project",
                         "files": ["front/css/a.css", "front/css/b.css"], "config": {}}]

        def entry(path, line, severity, rule="color-hex-length"):
            return {"file": f"front/{path}", "line": line, "column": 1, "rule": rule,
                    "severity": severity, "text": "Expected short hex"}

        self.report = [entry("css/a.css", 9, "error"), entry("css/a.css", 2, "warning")]
        added = {"css/a.css": {1, 2, 3}, "css/b.css": {4}}
        check = self.by_name(self.checks(files, added))[STYLE]
        self.assertEqual((check["status"], check["files"]), ("passed", files))
        self.report.append(entry("css/b.css", 4, "error"))
        check = self.by_name(self.checks(files, added))[STYLE]
        self.assertEqual((check["status"], check["files"]), ("failed", ["css/b.css"]))
        self.assertIn("css/b.css:4 color-hex-length", check["detail"])

    def test_stylelint_ignores_checklist_rules_unless_the_project_applies_them(self):
        self.report = [
            {"file": "front/a.scss", "line": 1, "column": 1, "rule": "max-nesting-depth",
             "severity": "error", "text": "deep"},
            {"file": "front/a.scss", "line": 2, "column": 1, "rule": "selector-class-pattern",
             "severity": "error", "text": "name"},
        ]
        added = {"a.scss": {1, 2}}
        for applied, status in (("project+checklist", "passed"), ("project", "failed")):
            with self.subTest(applied=applied):
                self.check_dir = self.root / f"check-{applied}"
                self.configs = [{"source": "front/.stylelintrc.json", "applied": applied,
                                 "files": ["front/a.scss"], "config": {}}]
                check = self.by_name(self.checks(["a.scss"], added))[STYLE]
                self.assertEqual(check["status"], status)

    def test_stylelint_accepts_report_files_with_or_without_the_repo_prefix(self):
        for prefixed in (True, False):
            with self.subTest(prefixed=prefixed):
                self.check_dir = self.root / f"check-{prefixed}"
                name = "front/a.css" if prefixed else "a.css"
                self.configs = [{"source": None, "applied": "project", "files": [name], "config": {}}]
                self.report = [{"file": name, "line": 1, "column": 1, "rule": "color-hex-length",
                                "severity": "error", "text": "x"}]
                check = self.by_name(self.checks(["a.css"], {"a.css": {1}}))[STYLE]
                self.assertEqual((check["status"], check["files"]), ("failed", ["a.css"]))

    def test_stylelint_reports_an_unexpected_report_format(self):
        self.configs = [{"source": None, "applied": "project", "files": ["front/a.css"], "config": {}}]
        self.report = [{"file": "/abs/elsewhere/a.css", "line": 1, "column": 1,
                        "rule": "color-hex-length", "severity": "error", "text": "x"}]
        check = self.by_name(self.checks(["a.css"], {"a.css": {1}}))[STYLE]
        self.assertEqual(check, {"name": STYLE, "status": "not_run", "files": [],
                                 "detail": "format de rapport stylelint inattendu"})

    def test_a_file_name_with_a_tab_or_newline_is_reported_not_dropped(self):
        self.tsv = "ok\tphp -l\ta.php\t\n"
        checks = self.by_name(self.checks(["a.php", "b\tc.php", "d\ne.js", "notes\t.md"]))
        self.assertEqual(checks[PHP]["files"], ["a.php"])
        self.assertEqual(checks["Nom de fichier"], {
            "name": "Nom de fichier", "status": "not_run", "files": ["b\tc.php", "d\ne.js"],
            "detail": "nom de fichier non pris en charge"})
        self.assertEqual((self.check_dir / "files.txt").read_text(encoding="utf-8"), "a.php\n")
        self.calls.clear()
        only = self.checks(["b\tc.php"])
        self.assertEqual([c["name"] for c in only], ["Nom de fichier"])
        self.assertEqual(self.calls, [])

    def test_stylelint_is_not_run_without_the_project_configuration(self):
        self.configs = [{"source": None, "applied": "checklist", "files": ["front/a.css"], "config": {}}]
        self.report = [{"file": "front/a.css", "line": 1, "column": 1, "rule": "color-hex-length",
                        "severity": "error", "text": "x"}]
        check = self.by_name(self.checks(["a.css"], {"a.css": {1}}))[STYLE]
        self.assertEqual(check, {"name": STYLE, "status": "not_run", "files": [], "detail": NO_CONFIG})

    def test_check_falls_back_to_host_binaries(self):
        self.code = 2
        files = ["a.php", "b.js", "s.css"]
        for found in ({"php": "/usr/bin/php"}, {}):
            self.calls.clear()
            self.check_dir = self.root / f"check-{len(found)}"
            host = []

            def which(name):
                return found.get(name)

            def fake(command, **kwargs):
                if command[0] == "bash":
                    return done(2)
                host.append(command)
                return done(0) if command[1] == "-l" else done(1, "", "boom")

            with mock.patch("cqa.fix_checks.shutil.which", which):
                checks = self.by_name(fix_checks.run_checks(
                    fake, Path("/x/cqa-toolbox.sh"), self.check_dir, self.worktree, "front", files, {}))
            self.assertEqual(checks[STYLE]["status"], "not_run")
            self.assertEqual(checks[JS]["status"], "not_run")
            self.assertEqual(checks[JS]["detail"], UNAVAILABLE)
            if found:
                self.assertEqual(checks[PHP]["status"], "passed")
                self.assertEqual(host, [["/usr/bin/php", "-l", str(self.worktree / "a.php")]])
            else:
                self.assertEqual(checks[PHP]["status"], "not_run")
                self.assertEqual(checks[PHP]["detail"], UNAVAILABLE)
                self.assertEqual(host, [])

    def test_host_syntax_reports_a_failure_message(self):
        def fake(command, **kwargs):
            return done(255, "PHP Parse error: syntax error in %s/a.php on line 2\n" % self.worktree)

        with mock.patch("cqa.fix_checks.shutil.which", lambda name: "/usr/bin/" + name):
            entries = fix_checks.host_syntax(self.worktree, ["a.php"], fake)
        self.assertEqual(entries, [{"status": "ko", "check": "php -l", "path": "a.php",
                                    "message": "PHP Parse error: syntax error in a.php on line 2"}])


class CheckCommandTest(GitCase):
    def setUp(self):
        super().setUp()
        self.launcher_calls = []
        self.tsv = ""
        self.launcher_code = 0

    def run_fake(self, command, **kwargs):
        if command[0] != "bash":
            return subprocess.run(command, **kwargs)
        self.launcher_calls.append(command)
        check_dir = Path(command[3])
        self.assertEqual(sorted(p.name for p in check_dir.iterdir()), ["context.json", "files.txt"])
        if self.launcher_code == 0:
            (check_dir / "syntax.tsv").write_text(self.tsv, encoding="utf-8")
        return done(self.launcher_code)

    def started(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        return worktree

    def commit(self, worktree, message, files):
        commit_files(self, worktree, message, files)

    def check(self):
        code, out, err = self.run_main(["check", self.run_dir, KEY], run=self.run_fake)
        return code, json.loads(out) if out else None, err

    def test_check_maps_a_failed_file_to_its_rows(self):
        worktree = self.started()
        self.commit(worktree, "fix(CQ-3): theme", {"wp-content/themes/t/functions.php": "<?php\n"})
        self.commit(worktree, "fix(CQ-5): other", {"wp-content/themes/t/other.php": "<?php\n"})
        self.tsv = ("ko\tphp -l\twp-content/themes/t/functions.php\tParse error\n"
                    "ok\tphp -l\twp-content/themes/t/other.php\t\n")
        code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertEqual(out["failed_refs"], [3])
        self.assertEqual([(c["name"], c["status"]) for c in out["checks"]], [(PHP, "failed")])
        self.assertEqual(out["not_run"], [])
        folder = self.run_dir / "fix" / "front" / "securite"
        saved = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"checks": out["checks"], "not_run": []})
        self.assertEqual(self.key()["step"], "checked")
        self.assertTrue(Path(self.launcher_calls[0][1]).name == "cqa-toolbox.sh")
        self.assertEqual(self.launcher_calls[0][2:3], ["check"])
        self.assertEqual(self.launcher_calls[0][3], str(folder / "check"))

    def test_check_lists_the_project_commands_as_not_run(self):
        worktree = self.started()
        self.commit(worktree, "fix(CQ-3): make", {"Makefile": "lint:\n\tx\n", "readme.md": "x"})
        code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertEqual(out["checks"], [])
        self.assertEqual(out["not_run"], [{"name": "Makefile (lint)", "command": "make lint", "why": WHY}])
        self.assertEqual(self.launcher_calls, [])

    def test_check_recreates_its_folder_and_never_lowers_the_step(self):
        worktree = self.started()
        self.commit(worktree, "fix(CQ-3): theme", {"a.php": "<?php\n"})
        state = self.state()
        state["keys"][KEY]["step"] = "reviewed"
        fix_state.save(self.run_dir, state)
        folder = self.run_dir / "fix" / "front" / "securite" / "check"
        write(folder, "stale.txt", "old")
        self.tsv = "ok\tphp -l\ta.php\t\n"
        code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertEqual(out["checks"][0]["status"], "passed")
        self.assertEqual(self.key()["step"], "reviewed")
        self.assertFalse((folder / "stale.txt").exists())

    def test_check_uses_the_host_when_the_launcher_fails(self):
        worktree = self.started()
        self.commit(worktree, "fix(CQ-3): theme", {"a.php": "<?php\n", "s.css": "a{}\n"})
        self.launcher_code = 2
        with mock.patch("cqa.fix_checks.shutil.which", lambda name: None):
            code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertEqual({c["name"]: (c["status"], c["detail"]) for c in out["checks"]},
                         {PHP: ("not_run", UNAVAILABLE), STYLE: ("not_run", UNAVAILABLE)})
        self.assertEqual(out["failed_refs"], [])
        self.assertEqual(self.key()["step"], "checked")

    def test_check_refuses_a_malformed_third_party_list(self):
        self.started()
        repos = [{"name": "front", "path": str(self.repo), "remote": "x", "third_party_paths": "x"}]
        self.write_json("context.json", {"repos": repos})
        code, out, err = self.run_main(["check", self.run_dir, KEY], run=self.run_fake)
        self.assertEqual(code, 6, err)
        self.assertIn("third_party_paths", err)


class GitFilesTest(GitCase):
    def test_changed_files_added_lines_and_failed_refs(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        start = self.key()["start_sha"]

        def commit(message, files):
            commit_files(self, worktree, message, files)

        commit("fix(CQ-3): first", {"web/a.php": "<?php\n", "dir with space/é.css": "a {}\nb {}\n"})
        commit("fix(CQ-5): second", {"web/b.php": "<?php\n", "web/a.php": "<?php\n// more\n"})
        commit("chore: unrelated", {"web/c.php": "<?php\n"})
        fix_names.git(worktree, subprocess.run, "rm", "-q", "a.txt")
        fix_names.git(worktree, subprocess.run, "commit", "-q", "-m", "fix(CQ-3): remove")
        self.assertEqual(fix_checks.changed_files(worktree, start, subprocess.run),
                         ["dir with space/é.css", "web/a.php", "web/b.php", "web/c.php"])
        added = fix_checks.added_lines(worktree, start, subprocess.run)
        self.assertEqual(added["web/a.php"], {1, 2})
        self.assertEqual(added["dir with space/é.css"], {1, 2})
        failed = [{"name": PHP, "status": "failed", "files": ["web/a.php"], "detail": ""}]
        self.assertEqual(fix_checks.failed_refs(worktree, start, failed, subprocess.run), [3, 5])
        failed = [{"name": PHP, "status": "failed", "files": ["web/b.php", "web/c.php"], "detail": ""}]
        self.assertEqual(fix_checks.failed_refs(worktree, start, failed, subprocess.run), [5])
        passed = [{"name": PHP, "status": "passed", "files": ["web/a.php"], "detail": ""}]
        self.assertEqual(fix_checks.failed_refs(worktree, start, passed, subprocess.run), [])


if __name__ == "__main__":
    unittest.main()
