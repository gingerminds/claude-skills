import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cqa import fix_guard, fix_names, fix_state
from tests.cqa.test_fix_git import BASE, GitCase, IDENTITY, KEY, flight, mr
from tests.cqa.test_git_tools import commit, git as real_git

SCHEMAS = Path(__file__).resolve().parents[2] / "skills" / "checklist-quality-audit" / "schemas"
SECTION = ["--section", "securite"]

THIRD_PARTY = "code tiers (third_party_paths)"
DEPENDENCY_DIR = "dossier de dépendances"
CORE = "cœur ou module contribué de la stack"
SUBMODULE = "sous-module git"
MINIFIED = "fichier minifié"
DEPENDENCIES = "dépendances : aucun ajout ni montée en v2.0"
KNOWN_SECRET = "secret détecté (known_value)"
BAD_MESSAGE = "message de commit hors convention"
OUT_OF_MR = "Réf hors de cette MR"
MERGE = "commit de fusion"
ATTRIBUTION = "ligne d'attribution dans le message"
SECTION_MISMATCH = "section du commit différente de celle de la ligne"
SUBJECT_TOO_LONG = "sujet de plus de 72 caractères"
REWRITTEN = "historique réécrit : le départ de la passe n'est plus dans la branche"


def problem(sha, ref, path, reason, kind, line=None, label=None, waivable=False):
    """A problem as the guard prints it."""
    return {"sha": sha, "ref": ref, "path": path, "line": line, "label": label,
            "reason": reason, "kind": kind, "waivable": waivable}


def secret(sha, ref, path, label, line):
    """A secret problem: a heuristic label is a suspect, waivable on a line of a file."""
    heuristic = label in ("credential_assignment", "credential_define")
    return problem(sha, ref, path, f"secret détecté ({label})",
                   "suspect" if heuristic else "content", line, label,
                   heuristic and path is not None)


def write(root, path, content):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class RepoCase(unittest.TestCase):
    """A plain repo with one commit: the guard checks the commits made after it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.root), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            **IDENTITY,
        })
        env.start()
        self.addCleanup(env.stop)
        self.repo = self.root / "repo"
        real_git(self.root, "init", "-q", str(self.repo))
        self.start = commit(self.repo, "README.md", "chore: init")

    def add(self, message, files, *body):
        for path, content in files.items():
            write(self.repo, path, content)
            real_git(self.repo, "add", "--", path)
        paragraphs = [item for text in body for item in ("-m", text)]
        real_git(self.repo, "commit", "-q", "-m", message, *paragraphs)
        return real_git(self.repo, "rev-parse", "HEAD")

    def check(self, repo=None, rows=(3, 12, 107), known=()):
        sections = rows if isinstance(rows, dict) else {ref: "securite" for ref in rows}
        return fix_guard.check_commits(
            self.repo, self.start, sections, repo or {"name": "front"}, list(known), subprocess.run
        )

    @staticmethod
    def reasons(problems):
        return {problem["path"]: problem["reason"] for problem in problems}


class GuardPathTest(RepoCase):
    def test_guard_refuses_third_party_and_lockfiles(self):
        repo = {"name": "front", "stack": "wordpress",
                "third_party_paths": ["web/app/plugins/acme-forms"]}
        first = self.add("fix(securite CQ-3): touch a plugin",
                         {"web/app/plugins/acme-forms/x.php": "<?php", "composer.lock": "{}"})
        self.assertEqual(self.check(repo), [
            problem(first, 3, "composer.lock", DEPENDENCIES, "content"),
            problem(first, 3, "web/app/plugins/acme-forms/x.php", THIRD_PARTY, "content"),
        ])
        refused = {
            "package.json": DEPENDENCIES,
            "web/app/themes/t/package-lock.json": DEPENDENCIES,
            "yarn.lock": DEPENDENCIES,
            "pnpm-lock.yaml": DEPENDENCIES,
            "composer.json": DEPENDENCIES,
            "vendor/acme/lib/a.php": DEPENDENCY_DIR,
            "web/app/plugins/custom/vendor/b.php": DEPENDENCY_DIR,
            "web/app/themes/t/node_modules/x/index.js": DEPENDENCY_DIR,
            ".gitmodules": SUBMODULE,
            "web/app/themes/t/x.min.js": MINIFIED,
            "x.min.css": MINIFIED,
        }
        accepted = ("web/app/plugins/acme-forms-pro/x.php", "web/app/themes/t/functions.php",
                    "web/app/themes/t/vendors.php", "docs/composer.json.md")
        self.add("fix(securite CQ-12): touch many files",
                 {**{path: "x" for path in refused}, **{path: "y" for path in accepted}})
        real_git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.start},libs/sub")
        real_git(self.repo, "commit", "-q", "-m", "fix(securite CQ-12): add a submodule")
        later = [problem for problem in self.check(repo) if problem["sha"] != first]
        self.assertEqual(self.reasons(later), {**refused, "libs/sub": SUBMODULE})
        self.assertEqual({problem["ref"] for problem in later}, {12})
        self.assertEqual({problem["kind"] for problem in later}, {"content"})

    def test_guard_refuses_core_paths_per_stack(self):
        wordpress = ["wp-includes/x.php", "wp-admin/y.php", "wp-login.php", "web/wp/wp-settings.php"]
        drupal = ["web/core/lib/x.php", "core/x.php", "web/modules/contrib/y/y.module",
                  "web/libraries/z.js"]
        accepted = ["wp-config.php", "wp-content/themes/t/functions.php",
                    "web/modules/custom/z/z.module"]
        self.add("fix(securite CQ-3): touch the core",
                 {path: "x" for path in wordpress + drupal + accepted})
        for stack, refused in (("wordpress", wordpress), ("drupal", drupal)):
            with self.subTest(stack=stack):
                found = self.reasons(self.check({"name": "front", "stack": stack}))
                self.assertEqual(found, {path: CORE for path in refused})
        for repo in ({"name": "front", "stack": "laravel"}, {"name": "front", "stack": None}):
            with self.subTest(stack=repo["stack"]):
                self.assertEqual(self.check(repo), [])
        self.assertEqual(set(fix_guard.CORE_PATHS), {"wordpress", "drupal"})


class GuardMessageTest(RepoCase):
    def test_guard_checks_commit_messages(self):
        self.add("fix(securite CQ-107): add headers", {"a.php": "1"}, "The audit found no header.")
        bare = self.add("fix: add headers", {"b.php": "1"})
        glued = self.add("fix(securite CQ-107):add headers", {"b2.php": "1"})
        legacy = self.add("fix(CQ-107): add headers", {"b3.php": "1"})
        stray = self.add("fix(securite CQ-999): x", {"c.php": "1"})
        coauthor = self.add("fix(securite CQ-107): add d", {"d.php": "1"}, "Adds d.",
                            "Co-Authored-By: Claude <noreply@example.test>")
        generated = self.add("fix(securite CQ-107): add e", {"e.php": "1"},
                             "\U0001F916 Generated with [Claude Code](https://claude.example.test)")
        session = self.add("fix(securite CQ-107): add f", {"f.php": "1"},
                           "Claude-Session: https://claude.example.test/session")
        real_git(self.repo, "checkout", "-q", "-b", "side")
        self.add("fix(securite CQ-3): side work", {"g.php": "1"})
        real_git(self.repo, "checkout", "-q", "-")
        real_git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        merge = real_git(self.repo, "rev-parse", "HEAD")
        self.assertCountEqual(self.check(), [
            problem(bare, None, None, BAD_MESSAGE, "form"),
            problem(glued, None, None, BAD_MESSAGE, "form"),
            problem(legacy, 107, None, BAD_MESSAGE, "form"),
            problem(stray, 999, None, OUT_OF_MR, "form"),
            problem(coauthor, 107, None, ATTRIBUTION, "form"),
            problem(generated, 107, None, ATTRIBUTION, "form"),
            problem(session, 107, None, ATTRIBUTION, "form"),
            problem(merge, None, None, MERGE, "content"),
        ])

    def test_guard_checks_section_and_subject_length(self):
        rows = {3: "securite", 12: "qualite"}
        self.add("fix(qualite CQ-12): add the alt text", {"a.php": "1"})
        crossed = self.add("fix(qualite CQ-3): escape the search field", {"b.php": "1"})
        exact = "fix(securite CQ-3): " + "x" * (fix_names.SUBJECT_MAX - 20)
        self.assertEqual(len(exact), fix_names.SUBJECT_MAX)
        self.add(exact, {"c.php": "1"})
        long = self.add(exact + "y", {"d.php": "1"})
        both = self.add("fix(qualite CQ-3): " + "z" * fix_names.SUBJECT_MAX, {"e.php": "1"})
        self.assertEqual(self.check(rows=rows), [
            problem(crossed, 3, None, SECTION_MISMATCH, "form"),
            problem(long, 3, None, SUBJECT_TOO_LONG, "form"),
            problem(both, 3, None, SECTION_MISMATCH, "form"),
            problem(both, 3, None, SUBJECT_TOO_LONG, "form"),
        ])
        self.assertEqual(fix_guard.SECTION_MISMATCH, SECTION_MISMATCH)
        self.assertEqual(fix_guard.SUBJECT_TOO_LONG, SUBJECT_TOO_LONG)

    def test_guard_classes_form_and_content(self):
        known = ["s3cr3t-value"]
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        message = self.add("fix(securite CQ-3): rotate s3cr3t-value", {"a.txt": "a"},
                           "DB_PASSWORD=hunter22",
                           "Co-Authored-By: Claude <noreply@example.test>")
        content = self.add("fix(seo CQ-3): add a key",
                           {"web/key.php": f"<?php\n$key = '{aws}';\n$password = 'hunter2hunter2';\n",
                            "x.min.js": "x"})
        found = {(item["sha"], item["reason"]): (item["kind"], item["waivable"])
                 for item in self.check(known=known)}
        self.assertEqual(found, {
            (message, ATTRIBUTION): ("form", False),
            (message, KNOWN_SECRET): ("content", False),
            (message, "secret détecté (credential_assignment)"): ("suspect", False),
            (content, SECTION_MISMATCH): ("form", False),
            (content, "secret détecté (aws_key)"): ("content", False),
            (content, "secret détecté (credential_assignment)"): ("suspect", True),
            (content, MINIFIED): ("content", False),
        })
        self.assertEqual((fix_guard.FORM, fix_guard.CONTENT, fix_guard.SUSPECT),
                         ("form", "content", "suspect"))
        self.assertEqual(fix_guard.HEURISTIC_SECRET_LABELS,
                         {"credential_assignment", "credential_define"})
        real_git(self.repo, "reset", "-q", "--hard", self.start)
        self.add("fix(securite CQ-3): start again", {"b.txt": "b"})
        self.start = message
        self.assertEqual(self.check(), [problem(message, None, None, REWRITTEN, "content")])

    def test_guard_reports_heuristic_alerts_as_waivable_suspects(self):
        expression = ("<?php\n$password = isset( $_POST['pass1'] ) ? (string) "
                      "wp_unslash( $_POST['pass1'] ) : '';\n")
        read = self.add("fix(securite CQ-3): read the password field", {"login.php": expression})
        literal = self.add("fix(securite CQ-3): set a password",
                           {"seed.php": "<?php\n// Seed.\n$password = 'hunter2hunter2';\n"})
        defined = self.add("fix(securite CQ-3): define the password",
                           {"conf.php": "<?php\ndefine( 'DB_PASSWORD', 'Adm1nP@ss2024' );\n"})
        self.assertEqual(self.check(), [
            secret(read, 3, "login.php", "credential_assignment", 2),
            secret(literal, 3, "seed.php", "credential_assignment", 3),
            secret(defined, 3, "conf.php", "credential_define", 2),
        ])

    def test_guard_names_the_line_of_a_secret_in_a_later_hunk(self):
        write(self.repo, "big.php", "".join(f"<?php // {index}\n" for index in range(20)))
        real_git(self.repo, "add", "big.php")
        real_git(self.repo, "commit", "-q", "-m", "chore: before the pass")
        self.start = real_git(self.repo, "rev-parse", "HEAD")
        lines = [f"<?php // {index}\n" for index in range(20)]
        lines[2] = "$a = 1;\n"
        lines[14] = "$password = 'hunter2hunter2';\n"
        sha = self.add("fix(securite CQ-3): edit big", {"big.php": "".join(lines)})
        self.assertEqual(self.check(), [secret(sha, 3, "big.php", "credential_assignment", 15)])

    def test_guard_scans_only_added_lines(self):
        known = ["s3cr3t-value"]
        write(self.repo, "old.php", "<?php\n$auth = 's3cr3t-value';\n")
        real_git(self.repo, "add", "old.php")
        real_git(self.repo, "commit", "-q", "-m", "chore: before the pass")
        self.start = real_git(self.repo, "rev-parse", "HEAD")
        self.add("fix(securite CQ-3): edit old", {"old.php": "<?php\n$auth = getenv('AUTH');\n"})
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        added = self.add("fix(securite CQ-3): add a key", {"new.php": f"<?php\n$key = '{aws}';\n"})
        self.assertEqual(self.check(known=known), [
            secret(added, 3, "new.php", "aws_key", 2),
        ])

    def test_guard_names_a_path_with_a_space_or_an_accent(self):
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        content = f"<?php\n'{aws}';\n"
        spaced = self.add("fix(securite CQ-3): add a spaced file", {"web/my file.php": content})
        accented = self.add("fix(securite CQ-3): add an accented file", {"web/clé.php": content})
        reason = "secret détecté (aws_key)"
        self.assertEqual(self.check(), [
            secret(spaced, 3, "web/my file.php", "aws_key", 2),
            secret(accented, 3, "web/clé.php", "aws_key", 2),
        ])


class GuardCommandTest(GitCase):
    def test_guard_refuses_malformed_third_party_paths(self):
        self.open_pass()
        self.start_ok()
        for value in ("web/app/plugins/acme-forms", ["web/app/plugins/acme-forms", 3]):
            with self.subTest(value=value):
                repos = [{"name": "front", "path": str(self.repo), "remote": "x",
                          "third_party_paths": value}]
                self.write_json("context.json", {"repos": repos})
                code, out, err = self.run_main(["guard", self.run_dir, KEY])
                self.assertEqual(code, 6, err)
                self.assertIn("third_party_paths", err)

    def test_guard_ignores_commits_before_start_sha(self):
        branch = BASE
        before = self.head(self.repo, "refs/remotes/origin/develop")
        self.push_branch(branch, "fix(CQ-3): first", "Apply 1 suggestion(s) to 1 file(s)")
        self.advance_develop()
        real_git(self.other, "checkout", "-q", branch)
        real_git(self.other, "merge", "-q", "--no-ff", "-m", f"Merge branch 'develop' into {branch}",
                 "develop")
        real_git(self.other, "push", "-q", "origin", branch)
        real_git(self.other, "checkout", "-q", "develop")
        self.write_run(flight(open=[mr(12, branch, [3])]))
        self.open_pass()
        started = self.start_ok()
        self.assertEqual(started["done_refs"], [3])
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "done_before")
        self.fixer_commit(started["worktree"], "follow.txt", "fix(securite CQ-3): follow up")
        code, out, err = self.run_main(["guard", self.run_dir, KEY])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"ok": True, "problems": []})
        earlier = fix_guard.check_commits(
            Path(started["worktree"]), before, {3: "securite", 5: "securite"}, {"name": "front"},
            [], subprocess.run,
        )
        self.assertEqual({problem["reason"] for problem in earlier}, {BAD_MESSAGE, MERGE})

    def test_guard_finds_a_secret_without_printing_it(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        leaked = self.commit_files(worktree, "fix(securite CQ-3): read the value",
                                   {"web/config.php": "<?php\n$auth = 's3cr3t-value';\n"})
        self.commit_files(worktree, "fix(securite CQ-3): drop the value",
                          {"web/config.php": "<?php\n"})
        message = self.commit_files(worktree, "fix(securite CQ-5): rotate s3cr3t-value",
                                    {"b.txt": "b"})
        code, out, err = self.run_main(
            ["guard", self.run_dir, KEY], env={"GM_AUDIT_AUTH_PASS": "s3cr3t-value"}
        )
        self.assertEqual(code, 5, err)
        self.assertEqual(json.loads(out), {"ok": False, "problems": [
            secret(leaked, 3, "web/config.php", "known_value", 2),
            secret(message, 5, None, "known_value", 1),
        ]})
        self.assertNotIn("s3cr3t-value", out)
        self.assertNotIn("s3cr3t-value", err)
        self.assertEqual(self.run_main(["guard", self.run_dir, KEY])[0], 0)

    def guard_staged(self, *options):
        return self.run_main(["guard", self.run_dir, KEY, *options])

    def test_guard_staged_checks_the_index_and_message(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        head = self.head(worktree)
        message = self.root / "message.txt"
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        write(worktree, "web/key.php", f"<?php\n$key = '{aws}';\n")
        write(worktree, "vendor/acme/a.php", "<?php\n")
        write(worktree, "notes.php", f"<?php\n$key = '{aws}';\n")
        for path in ("web/key.php", "vendor/acme/a.php"):
            self.assertEqual(fix_names.git(worktree, subprocess.run, "add", "--", path).returncode, 0)
        message.write_text("fix(CQ-3): add the key\n\nCo-Authored-By: Claude <noreply@example.test>\n",
                           encoding="utf-8")
        code, out, err = self.guard_staged("--staged", "--message-file", str(message))
        self.assertEqual(code, 5, err)
        vendor = real_git(worktree, "rev-parse", ":vendor/acme/a.php")
        key_blob = real_git(worktree, "rev-parse", ":web/key.php")
        content = [
            problem(vendor, 3, "vendor/acme/a.php", DEPENDENCY_DIR, "content"),
            secret(key_blob, 3, "web/key.php", "aws_key", 2),
        ]
        self.assertEqual(json.loads(out), {"ok": False, "problems": [
            problem(None, 3, None, BAD_MESSAGE, "form"),
            problem(None, 3, None, ATTRIBUTION, "form"),
            *content,
        ]})
        self.assertEqual(self.head(worktree), head)
        self.assertEqual(real_git(worktree, "diff", "--cached", "--name-only").splitlines(),
                         ["vendor/acme/a.php", "web/key.php"])
        message.write_text("fix(seo CQ-5): " + "x" * fix_names.SUBJECT_MAX + "\n", encoding="utf-8")
        code, out, err = self.guard_staged("--staged", "--message-file", str(message))
        self.assertEqual(code, 5, err)
        self.assertEqual(json.loads(out)["problems"], [
            problem(None, 5, None, SECTION_MISMATCH, "form"),
            problem(None, 5, None, SUBJECT_TOO_LONG, "form"),
            *[{**problem, "ref": 5} for problem in content],
        ])
        real_git(worktree, "reset", "-q")
        write(worktree, "web/login.php", "<?php\n$password = isset( $_POST['p'] ) ? (string) "
                                         "wp_unslash( $_POST['p'] ) : '';\n")
        self.assertEqual(
            fix_names.git(worktree, subprocess.run, "add", "--", "web/login.php").returncode, 0)
        message.write_text("fix(securite CQ-3): read the password field\n\nThe audit found it.\n",
                           encoding="utf-8")
        blob = real_git(worktree, "rev-parse", ":web/login.php")
        code, out, err = self.guard_staged("--staged", "--message-file", str(message))
        self.assertEqual(code, 5, err)
        self.assertEqual(json.loads(out)["problems"],
                         [secret(blob, 3, "web/login.php", "credential_assignment", 2)])
        code, out, err = self.run_main(["waive-secret", self.run_dir, KEY, "--sha", blob, "--path",
                                        "web/login.php", "--line", "2", "--label",
                                        "credential_assignment"])
        self.assertEqual(code, 0, err)
        code, out, err = self.guard_staged("--staged", "--message-file", str(message))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"ok": True, "problems": []})
        self.assertEqual(self.head(worktree), head)
        committed = self.commit_files(worktree, "fix(securite CQ-3): read the password field", {})
        self.assertNotEqual(committed, head)
        code, out, err = self.run_main(["guard", self.run_dir, KEY])
        self.assertEqual((code, json.loads(out)), (0, {"ok": True, "problems": []}), err)
        for options in (["--staged"], ["--message-file", str(message)],
                        ["--staged", "--message-file", str(self.root / "absent.txt")]):
            with self.subTest(options=options):
                code, out, err = self.guard_staged(*options)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "")

    def commit_files(self, worktree, message, files):
        for path, content in files.items():
            write(worktree, path, content)
            self.assertEqual(fix_names.git(worktree, subprocess.run, "add", "--", path).returncode, 0)
        done = fix_names.git(worktree, subprocess.run, "commit", "-q", "-m", message)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.head(worktree)


class SecretWaiverTest(GitCase):
    """The lead waives a heuristic secret alert (credential_assignment, credential_define) for
    one line of one commit; no other secret is ever waivable, and the value is never stored."""

    WORDPRESS = ("$password = isset( $_POST['pass1'] ) ? (string) wp_unslash( $_POST['pass1'] ) "
                 ": '';")

    def setUp(self):
        super().setUp()
        self.open_pass()
        started = self.start_ok()
        self.worktree, self.start_sha = Path(started["worktree"]), started["start_sha"]

    def commit(self, message, files):
        for path, content in files.items():
            write(self.worktree, path, content)
            self.assertEqual(
                fix_names.git(self.worktree, subprocess.run, "add", "--", path).returncode, 0)
        done = fix_names.git(self.worktree, subprocess.run, "commit", "-q", "-m", message)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.head(self.worktree)

    def guard(self, env=None):
        code, out, err = self.run_main(["guard", self.run_dir, KEY], env=env)
        return code, json.loads(out)["problems"]

    def waive(self, sha, path, line, label, env=None):
        return self.run_main(["waive-secret", self.run_dir, KEY, "--sha", sha, "--path", path,
                              "--line", str(line), "--label", label], env=env)

    def test_the_cq107_false_positive_is_waived_by_the_lead(self):
        sha = self.commit("fix(securite CQ-3): read the password field",
                          {"web/login.php": f"<?php\n{self.WORDPRESS}\n"})
        expected = secret(sha, 3, "web/login.php", "credential_assignment", 2)
        self.assertEqual(self.guard(), (5, [expected]))
        self.assertEqual((expected["kind"], expected["waivable"]), ("suspect", True))
        code, out, err = self.waive(sha, "web/login.php", 2, "credential_assignment")
        self.assertEqual(code, 0, err)
        waiver = json.loads(out)["waiver"]
        self.assertEqual(set(waiver), {"sha", "path", "line", "label", "at"})
        self.assertEqual((waiver["sha"], waiver["path"], waiver["line"], waiver["label"]),
                         (sha, "web/login.php", 2, "credential_assignment"))
        self.assertEqual(self.key()["secret_waivers"], [waiver])
        self.assertEqual(self.guard(), (0, []))
        self.assertEqual(self.waive(sha, "web/login.php", 2, "credential_assignment")[0], 0)
        self.assertEqual(len(self.key()["secret_waivers"]), 1)
        state = (self.run_dir / fix_state.STATE_FILE).read_text(encoding="utf-8")
        for text in ("$_POST", "wp_unslash", "pass1", "isset"):
            self.assertNotIn(text, state)
        again = self.commit("fix(securite CQ-3): read it on the profile page",
                            {"web/profile.php": f"<?php\n// Profile.\n{self.WORDPRESS}\n"})
        self.assertEqual(self.guard(),
                         (5, [secret(again, 3, "web/profile.php", "credential_assignment", 3)]))

    def test_only_a_heuristic_alert_the_guard_reports_may_be_waived(self):
        env = {"GM_AUDIT_AUTH_PASS": "s3cr3t-value"}
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        known = self.commit("fix(securite CQ-3): read the value",
                            {"web/config.php": "<?php\n$auth = 's3cr3t-value';\n",
                             "web/key.php": f"<?php\n$key = '{aws}';\n"})
        literal = self.commit("fix(securite CQ-3): seed a password",
                              {"web/seed.php": "<?php\n$password = 'hunter2hunter2';\n"})
        code, problems = self.guard(env)
        self.assertEqual(problems, [
            secret(known, 3, "web/config.php", "known_value", 2),
            secret(known, 3, "web/key.php", "aws_key", 2),
            secret(literal, 3, "web/seed.php", "credential_assignment", 2),
        ])
        refused = {
            "known_value": (known, "web/config.php", 2, "known_value"),
            "aws_key": (known, "web/key.php", 2, "aws_key"),
            "hors de la passe": (self.start_sha, "web/seed.php", 2, "credential_assignment"),
            "inconnu": ("f" * 40, "web/seed.php", 2, "credential_assignment"),
            "autre ligne": (literal, "web/seed.php", 3, "credential_assignment"),
            "autre chemin": (literal, "web/other.php", 2, "credential_assignment"),
            "autre label": (literal, "web/seed.php", 2, "credential_define"),
            "ligne 0": (literal, "web/seed.php", 0, "credential_assignment"),
        }
        for case, args in refused.items():
            with self.subTest(case=case):
                code, out, err = self.waive(*args, env=env)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "")
                self.assertTrue(err.strip())
                self.assertNotIn("s3cr3t-value", err)
                self.assertNotIn("secret_waivers", self.key())
        code, out, err = self.waive(literal, "web/seed.php", 2, "credential_assignment", env=env)
        self.assertEqual(code, 0, err)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("hunter2", (self.run_dir / fix_state.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(self.guard(env), (5, problems[:2]))

    def test_a_drop_moves_the_waivers_to_the_rewritten_commits(self):
        dropped = self.commit("fix(securite CQ-5): add b", {"b5.txt": "b"})
        sha = self.commit("fix(securite CQ-3): read the password field",
                          {"web/login.php": f"<?php\n{self.WORDPRESS}\n"})
        state = self.state()
        state["keys"][KEY]["rows"]["5"].update(outcome="fixed", commits=[dropped])
        state["keys"][KEY]["rows"]["3"].update(outcome="fixed", commits=[sha])
        fix_state.save(self.run_dir, state)
        self.assertEqual(self.waive(sha, "web/login.php", 2, "credential_assignment")[0], 0)
        code, out, err = self.run_main(["drop", self.run_dir, KEY, "--refs", "CQ-5", "--reason",
                                        "Revue bloquante : échappement manquant."])
        self.assertEqual(code, 0, err)
        rewritten = self.key()["rows"]["3"]["commits"][0]
        self.assertNotEqual(rewritten, sha)
        self.assertEqual([waiver["sha"] for waiver in self.key()["secret_waivers"]], [rewritten])
        self.assertEqual(self.guard(), (0, []))


class ValidateTest(GitCase):
    def setUp(self):
        super().setUp()
        self.folder = self.run_dir / "fix" / "front"

    def started(self):
        self.open_pass()
        started = self.start_ok()
        self.worktree = Path(started["worktree"])
        self.start_sha = started["start_sha"]
        self.section_start()
        self.pass_start(1)
        return started

    def pass_start(self, number):
        code, out, err = self.run_main(
            ["pass-start", self.run_dir, KEY, "--pass", str(number), *SECTION])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def write_doc(self, name, document):
        (self.folder / name).write_text(json.dumps(document), encoding="utf-8")

    def validate(self, kind, number=1):
        code, out, err = self.run_main(
            ["validate", self.run_dir, KEY, kind, "--pass", str(number)])
        return code, json.loads(out) if out else None, err

    def report(self, fixed, number=1):
        return {"key": KEY, "pass": number, "rows": [
            {"ref": 3, "outcome": "fixed", "change": "Ajoute l'en-tête manquant.",
             "uat": "Ouvrir la page d'accueil et lire l'en-tête.", "commits": [fixed[:10]],
             "reason": None, "question": None, "covered_by": None,
             "attention": "Vérifier le cache du CDN."},
            {"ref": 5, "outcome": "skipped", "change": None, "uat": None, "commits": [],
             "reason": "Demande une décision de l'équipe UI/UX.", "question": None,
             "covered_by": None, "attention": None},
        ]}

    def test_validate_report_and_review(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        question = {"text": "Quel contact pour security.txt ?", "options": ["Équipe"],
                    "default": "Équipe"}
        bad = {
            "« outcome » inconnu": (1, {"outcome": "maybe"}),
            "au moins un commit": (0, {"commits": []}),
            "commit absent de la branche": (0, {"commits": [self.start_sha]}),
            "2 à 4 options": (1, {"outcome": "question", "question": question}),
            "Réf hors de cette clé": (1, {"ref": 99}),
        }
        for expected, (index, change) in bad.items():
            with self.subTest(error=expected):
                document = self.report(fixed)
                document["rows"][index].update(change)
                self.write_doc("report-1.json", document)
                code, result, err = self.validate("report")
                self.assertEqual(code, 6, err)
                self.assertFalse(result["ok"])
                self.assertTrue(any(expected in error for error in result["errors"]), result)
                self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")
        code, result, err = self.validate("review")
        self.assertEqual(code, 6)
        self.assertIn("review-1.json", " ".join(result["errors"]))
        self.write_doc("report-1.json", self.report(fixed))
        code, result, err = self.validate("report")
        self.assertEqual(code, 0, err)
        self.assertEqual((result["ok"], result["errors"]), (True, []))
        self.assertEqual(result["rows"], {"fixed": [3], "skipped": [5], "question": []})
        key = self.key()
        self.assertEqual(key["rows"]["3"], {
            "outcome": "fixed", "reason": None, "commits": [fixed], "pass": 1,
            "attention": "Vérifier le cache du CDN.", "remaining": None, "section": "securite",
        })
        self.assertEqual(key["rows"]["5"], {
            "outcome": "skipped", "reason": "Demande une décision de l'équipe UI/UX.",
            "commits": [], "pass": 1, "attention": None, "remaining": None, "section": "securite",
        })
        self.assertEqual((key["step"], key["pass"]), ("fixed", 1))

        review = {"key": KEY, "pass": 1, "attention": ["Nommer la constante."],
                  "rows": [{"ref": 3, "verdict": "drop", "notes": None}]}
        self.write_doc("review-1.json", review)
        self.assertEqual(self.validate("review")[0], 6)
        review["rows"][0].update(verdict="blocking", notes="L'en-tête manque sur les 404.")
        self.write_doc("review-1.json", review)
        code, result, err = self.validate("review")
        self.assertEqual(code, 0, err)
        self.assertEqual(result["blocking"], [3])
        self.assertEqual(self.key()["step"], "fixed")
        review["rows"][0].update(verdict="ok", notes=None)
        self.write_doc("review-1.json", review)
        code, result, err = self.validate("review")
        self.assertEqual((code, result["blocking"]), (0, []))
        self.assertEqual(self.key()["step"], "reviewed")

        self.pass_start(2)
        second = {"key": KEY, "pass": 2, "rows": [{
            "ref": 5, "outcome": "question",
            "question": {"text": "Quel contact pour security.txt ?",
                         "options": ["Équipe technique", "Client"], "default": "Client"},
        }]}
        self.write_doc("report-2.json", second)
        code, result, err = self.validate("report", 2)
        self.assertEqual(code, 0, err)
        row = self.key()["rows"]["5"]
        self.assertEqual((row["outcome"], row["reason"], row["pass"]),
                         ("question", "Quel contact pour security.txt ?", 2))
        self.assertEqual(self.key()["rows"]["3"]["pass"], 1)

    def test_validate_report_checks_key_pass_and_missing_rows(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        document = self.report(fixed)
        document.update(key="back", rows=document["rows"][:1])
        document["pass"] = 2
        self.write_doc("report-1.json", document)
        code, result, err = self.validate("report")
        self.assertEqual(code, 6)
        joined = " ".join(result["errors"])
        for expected in ('vaut "back"', "« pass »", "CQ-5"):
            self.assertIn(expected, joined)
        (self.folder / "report-1.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.validate("report")[0], 6)

    def test_validate_report_keeps_the_commits_of_earlier_passes(self):
        self.started()
        first = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        self.write_doc("report-1.json", self.report(first))
        self.assertEqual(self.validate("report")[0], 0)
        self.write_doc("review-1.json", {"key": KEY, "pass": 1, "attention": [], "rows": [
            {"ref": 3, "verdict": "blocking", "notes": "L'en-tête manque sur les 404."}]})
        self.assertEqual(self.validate("review")[0], 0)
        self.pass_start(2)
        second = self.fixer_commit(self.worktree, "b.php", "fix(CQ-3): cover the 404 page")
        correction = self.report(second, number=2)
        correction["rows"] = correction["rows"][:1]
        correction["rows"][0]["commits"] = [second, first[:12]]
        self.write_doc("report-2.json", correction)
        code, result, err = self.validate("report", 2)
        self.assertEqual(code, 0, err)
        row = self.key()["rows"]["3"]
        self.assertEqual((row["commits"], row["pass"]), ([first, second], 2))

    def test_validate_report_refuses_rows_it_may_not_change(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        self.fixer_commit(self.worktree, "b.php", "fix(CQ-5): start something")
        question = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        for change in ({}, {"outcome": "question", "question": question}):
            with self.subTest(outcome=change.get("outcome", "skipped")):
                document = self.report(fixed)
                document["rows"][1].update(change)
                self.write_doc("report-1.json", document)
                code, result, err = self.validate("report")
                self.assertEqual(code, 6, err)
                self.assertIn("ligne CQ-5 : commit sur la branche pour une ligne non corrigée",
                              result["errors"])
        for outcome in ("done_before", "dropped"):
            with self.subTest(outcome=outcome):
                state = self.state()
                state["keys"][KEY]["rows"]["5"]["outcome"] = outcome
                fix_state.save(self.run_dir, state)
                self.write_doc("report-1.json", self.report(fixed))
                code, result, err = self.validate("report")
                self.assertEqual(code, 6, err)
                self.assertTrue(any("CQ-5" in error and f"({outcome})" in error
                                    for error in result["errors"]), result)
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")

    def test_validate_review_judges_fixed_rows_only(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        self.write_doc("report-1.json", self.report(fixed))
        self.assertEqual(self.validate("report")[0], 0)
        review = {"key": KEY, "pass": 1, "attention": [], "rows": [
            {"ref": 3, "verdict": "ok", "notes": None},
            {"ref": 5, "verdict": "ok", "notes": None},
        ]}
        self.write_doc("review-1.json", review)
        code, result, err = self.validate("review")
        self.assertEqual(code, 6, err)
        self.assertIn("ligne CQ-5 : verdict sur une ligne non corrigée (skipped)", result["errors"])
        self.assertEqual(self.key()["step"], "fixed")

    def test_report_records_the_remaining_work(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(securite CQ-3): add the header")
        document = self.report(fixed)
        document["rows"][0]["remaining"] = "Reste l'en-tête des pages d'erreur."
        self.write_doc("report-1.json", document)
        code, result, err = self.validate("report")
        self.assertEqual(code, 0, err)
        rows = self.key()["rows"]
        self.assertEqual(rows["3"]["remaining"], "Reste l'en-tête des pages d'erreur.")
        self.assertIsNone(rows["5"]["remaining"])

    def test_review_corrections_are_recorded(self):
        self.started()
        first = self.fixer_commit(self.worktree, "a.php", "fix(securite CQ-3): add the header")
        self.write_doc("report-1.json", self.report(first))
        self.assertEqual(self.validate("report")[0], 0)
        corrections = {"change": "Ajoute l'en-tête X-Frame-Options au thème.", "uat": None,
                       "remaining": "Reste l'en-tête des pages d'erreur."}
        bad = {
            "ligne CQ-3 : « corrections » ne sert qu'à un verdict ok":
                {"verdict": "blocking", "notes": "À revoir.", "corrections": corrections},
            "ligne CQ-3 : « corrections » : champ inconnu « title » (attendu : change, uat, "
            "attention, remaining)": {"corrections": {"title": "Titre."}},
            "ligne CQ-3 : « corrections.change » dépasse 600 caractères (601)":
                {"corrections": {"change": "x" * 601}},
            "ligne CQ-3 : « corrections.remaining » dépasse 300 caractères (301)":
                {"corrections": {"remaining": "x" * 301}},
            "ligne CQ-3 : « corrections.attention » : un abandon (« Abandon : ») fait 300 "
            "caractères au plus (301)": {"corrections": {"attention": "Abandon : " + "x" * 291}},
            'ligne CQ-3 : « corrections » doit être un objet {"change", "uat", "attention", '
            '"remaining"}': {"corrections": "Ajoute l'en-tête."},
        }
        for expected, change in bad.items():
            with self.subTest(error=expected):
                row = {"ref": 3, "verdict": "ok", "notes": None, **change}
                self.write_doc("review-1.json",
                               {"key": KEY, "pass": 1, "attention": [], "rows": [row]})
                code, result, err = self.validate("review")
                self.assertEqual(code, 6, err)
                self.assertEqual(result["errors"], [expected])
                self.assertNotIn("corrections", self.key()["rows"]["3"])
        review = {"key": KEY, "pass": 1, "attention": [], "rows": [
            {"ref": 3, "verdict": "ok", "notes": None, "corrections": corrections}]}
        self.write_doc("review-1.json", review)
        code, result, err = self.validate("review")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["rows"]["3"]["corrections"], {
            "change": "Ajoute l'en-tête X-Frame-Options au thème.",
            "remaining": "Reste l'en-tête des pages d'erreur.",
        })
        review["rows"][0]["corrections"] = None
        self.write_doc("review-1.json", review)
        self.assertEqual(self.validate("review")[0], 0)
        self.assertNotIn("corrections", self.key()["rows"]["3"])
        review["rows"][0]["corrections"] = corrections
        self.write_doc("review-1.json", review)
        self.assertEqual(self.validate("review")[0], 0)
        self.assertIn("corrections", self.key()["rows"]["3"])
        self.pass_start(2)
        second = self.fixer_commit(self.worktree, "b.php", "fix(securite CQ-3): cover the 404 page")
        again = self.report(second, number=2)
        again["rows"] = again["rows"][:1]
        again["rows"][0]["commits"] = [first, second]
        self.write_doc("report-2.json", again)
        self.assertEqual(self.validate("report", 2)[0], 0)
        self.assertNotIn("corrections", self.key()["rows"]["3"])

    def test_review_scope_light_only_for_small_doc_passes(self):
        self.started()

        def commit_lines(files, subject):
            for path, lines in files.items():
                write(self.worktree, path, "".join(f"{line}\n" for line in lines))
                self.assertEqual(
                    fix_names.git(self.worktree, subprocess.run, "add", "--", path).returncode, 0)
            done = fix_names.git(self.worktree, subprocess.run, "commit", "-q", "-m", subject)
            self.assertEqual(done.returncode, 0, done.stderr)

        def scope(number):
            code, out, err = self.run_main(
                ["review-scope", self.run_dir, KEY, "--pass", str(number)])
            self.assertEqual(code, 0, err)
            return json.loads(out)

        code, out, err = self.run_main(["review-scope", self.run_dir, KEY, "--pass", "2"])
        self.assertEqual(code, 2, err)
        self.assertIn("pass-start", err)
        self.assertEqual(scope(1), {"light": False, "lines": 0, "files": []})
        readme = [f"Ligne {index}" for index in range(30)]
        commit_lines({"README.md": readme}, "docs(securite CQ-3): document the headers")
        self.assertEqual(scope(1), {"light": True, "lines": 30, "files": ["README.md"]})
        self.pass_start(2)
        commit_lines({"robots.txt": ["Disallow: /wp-admin/"]}, "fix(securite CQ-3): hide the admin")
        self.assertEqual(scope(2), {"light": False, "lines": 1, "files": ["robots.txt"]})
        self.pass_start(3)
        commit_lines({"docs/guide.rst": [f"Ligne {index}" for index in range(41)]},
                     "docs(securite CQ-3): write the guide")
        self.assertEqual(scope(3), {"light": False, "lines": 41, "files": ["docs/guide.rst"]})
        self.pass_start(4)
        commit_lines({"README.md": ["Titre"] * 5 + readme[5:], "docs/intro.adoc": ["= Intro", "x"]},
                     "docs(securite CQ-3): reword the readme")
        self.assertEqual(scope(4), {"light": True, "lines": 12,
                                    "files": ["README.md", "docs/intro.adoc"]})
        self.assertEqual(scope(1)["files"],
                         ["README.md", "docs/guide.rst", "docs/intro.adoc", "robots.txt"])
        self.assertFalse(scope(1)["light"])
        self.assertEqual(fix_guard.LIGHT_REVIEW_LINES, 40)

    def test_dev_checkout_alert_only_once_per_change(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(securite CQ-3): add the header")
        self.write_doc("report-1.json", self.report(fixed))
        self.assertIsNone(self.validate("report")[1]["dev_checkout_changed"])
        (self.repo / "a.txt").write_text("changed during the pass", encoding="utf-8")
        alert = {"paths": ["a.txt"], "head": False, "branch": False, "stash": False}
        self.assertEqual(self.validate("report")[1]["dev_checkout_changed"], alert)
        self.assertIsNone(self.validate("report")[1]["dev_checkout_changed"])
        (self.repo / "b.txt").write_text("new", encoding="utf-8")
        (self.folder / "report-1.json").write_text("{", encoding="utf-8")
        code, result, err = self.validate("report")
        self.assertEqual(code, 6, err)
        self.assertEqual(result["dev_checkout_changed"], {**alert, "paths": ["b.txt"]})
        self.write_doc("report-1.json", self.report(fixed))
        self.assertIsNone(self.validate("report")[1]["dev_checkout_changed"])
        (self.repo / "a.txt").write_text("changed again, longer", encoding="utf-8")
        self.assertEqual(self.validate("report")[1]["dev_checkout_changed"], alert)

    def test_validate_reports_a_changed_dev_checkout(self):
        self.started()
        fixed = self.fixer_commit(self.worktree, "a.php", "fix(CQ-3): add the header")
        self.write_doc("report-1.json", self.report(fixed))
        code, result, err = self.validate("report")
        self.assertEqual(code, 0, err)
        self.assertIsNone(result["dev_checkout_changed"])
        (self.repo / "a.txt").write_text("changed during the pass", encoding="utf-8")
        code, result, err = self.validate("report")
        self.assertEqual(code, 0, err)
        self.assertEqual(result["dev_checkout_changed"],
                         {"paths": ["a.txt"], "head": False, "branch": False, "stash": False})
        self.assertEqual((self.repo / "a.txt").read_text(encoding="utf-8"), "changed during the pass")
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "fixed")


class DropTest(GitCase):
    def setUp(self):
        super().setUp()
        self.write_run(flight(), rows=(3, 5, 12))
        self.open_pass()

    def drop(self, key, refs, reason="Revue bloquante : échappement manquant.", run=subprocess.run):
        code, out, err = self.run_main(
            ["drop", self.run_dir, key, "--refs", refs, "--reason", reason], run=run)
        return code, json.loads(out) if out else None, err

    def record(self, key=KEY, **commits):
        """Mark rows fixed with their commits in the state, as validate report does."""
        state = self.state()
        for ref, shas in commits.items():
            state["keys"][key]["rows"][ref.lstrip("r")].update(outcome="fixed", commits=shas)
        fix_state.save(self.run_dir, state)

    def subjects(self, worktree, start):
        return real_git(worktree, "log", "--reverse", "--format=%s", f"{start}..HEAD").splitlines()

    def assert_clean_on(self, worktree, branch):
        self.assertEqual(real_git(worktree, "status", "--porcelain", "-uall"), "")
        self.assertEqual(real_git(worktree, "symbolic-ref", "--short", "HEAD"), branch)

    def test_drop_rebuilds_the_branch_without_the_row(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        kept = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        last = self.fixer_commit(worktree, "c3.txt", "fix(CQ-3): add c")
        state = self.state()
        rows = state["keys"][KEY]["rows"]
        rows["3"].update(outcome="fixed", commits=[first, last])
        rows["12"].update(outcome="fixed", commits=[kept])
        fix_state.save(self.run_dir, state)
        code, result, err = self.drop(KEY, "CQ-3,CQ-5")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [3, 5], "also_dropped": []})
        self.assertEqual(self.subjects(worktree, started["start_sha"]), ["fix(CQ-12): add b"])
        self.assertEqual(sorted(path.name for path in worktree.glob("*.txt")), ["a.txt", "b12.txt"])
        self.assert_clean_on(worktree, started["branch"])
        rows = self.key()["rows"]
        for ref in ("3", "5"):
            self.assertEqual(
                (rows[ref]["outcome"], rows[ref]["reason"], rows[ref]["commits"]),
                ("dropped", "Revue bloquante : échappement manquant.", []),
            )
        self.assertEqual(rows["12"]["outcome"], "fixed")
        self.assertEqual(rows["12"]["commits"], [self.head(worktree)])
        self.assertNotEqual(self.head(worktree), kept)

    def test_drop_also_drops_a_row_whose_commit_no_longer_replays(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-3): add shared")
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-12): change shared")
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [3], "also_dropped": [12]})
        self.assertEqual(self.head(worktree), started["start_sha"])
        self.assert_clean_on(worktree, started["branch"])
        rows = self.key()["rows"]
        self.assertEqual((rows["3"]["outcome"], rows["12"]["outcome"]), ("dropped", "dropped"))
        self.assertIn("CQ-3", rows["12"]["reason"])
        self.assertEqual(rows["5"]["outcome"], "pending")

    def test_drop_takes_rows_covered_by_a_dropped_commit(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        shared = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        own = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        state = self.state()
        rows = state["keys"][KEY]["rows"]
        rows["3"].update(outcome="fixed", commits=[shared])
        rows["12"].update(outcome="fixed", commits=[shared, own])
        fix_state.save(self.run_dir, state)
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [3], "also_dropped": [12]})
        self.assertEqual(self.head(worktree), started["start_sha"])
        self.assert_clean_on(worktree, started["branch"])

    def test_drop_refuses_pushed_commits(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        pushed = self.fixer_commit(worktree, "a.txt", "fix(CQ-3): add a")
        real_git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{started['branch']}")
        head = self.fixer_commit(worktree, "b.txt", "fix(CQ-5): add b")
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 5)
        self.assertIn("origin", err)
        self.assertEqual(self.head(worktree), head)
        self.assertEqual(self.head(self.repo, f"refs/heads/{started['branch']}"), head)
        self.assert_clean_on(worktree, started["branch"])
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")
        (worktree / "draft.txt").write_text("not committed", encoding="utf-8")
        code, result, err = self.drop(KEY, "CQ-5")
        self.assertEqual(code, 5)
        self.assertIn("non commitées", err)
        (worktree / "draft.txt").unlink()
        code, result, err = self.drop(KEY, "CQ-5")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.head(worktree), pushed)
        self.assert_clean_on(worktree, started["branch"])

    def test_guard_and_drop_refuse_a_rewritten_history(self):
        self.advance_develop()
        started = self.start_ok()
        worktree = Path(started["worktree"])
        real_git(worktree, "reset", "-q", "--hard", "HEAD~1")
        head = self.fixer_commit(worktree, "a.txt", "fix(securite CQ-3): add a")
        code, out, err = self.run_main(["guard", self.run_dir, KEY])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(out)["problems"], [
            problem(started["start_sha"], None, None, fix_guard.REWRITTEN, "content")])
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 5)
        self.assertIn("réécrit", err)
        self.assertEqual(self.head(worktree), head)

    def test_drop_never_rewrites_a_pushed_commit_after_a_conflict(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        self.fixer_commit(worktree, "x12.txt", "fix(CQ-12): add x")
        real_git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{started['branch']}")
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-3): add shared")
        head = self.fixer_commit(worktree, "shared.txt", "fix(CQ-12): change shared")
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 5, err)
        self.assertIn("origin", err)
        self.assertEqual(self.head(self.repo, f"refs/heads/{started['branch']}"), head)
        self.assert_clean_on(worktree, started["branch"])
        rows = self.key()["rows"]
        self.assertEqual((rows["3"]["outcome"], rows["12"]["outcome"]), ("pending", "pending"))


class DropSafetyTest(DropTest):
    def test_drop_marks_a_row_without_commits_and_leaves_the_branch(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        real_git(worktree, "checkout", "-q", "-b", "side")
        self.fixer_commit(worktree, "s12.txt", "fix(CQ-12): side work")
        real_git(worktree, "checkout", "-q", started["branch"])
        real_git(worktree, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        head = self.head(worktree)
        self.record(r3=[first])
        code, result, err = self.drop(KEY, "CQ-5", reason="Demande une décision du client.")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [5], "also_dropped": []})
        self.assertEqual(self.head(worktree), head)
        self.assertEqual(self.head(self.repo, f"refs/heads/{started['branch']}"), head)
        rows = self.key()["rows"]
        self.assertEqual((rows["5"]["outcome"], rows["5"]["reason"], rows["5"]["commits"]),
                         ("dropped", "Demande une décision du client.", []))
        self.assertEqual((rows["3"]["outcome"], rows["3"]["commits"]), ("fixed", [first]))
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 5)
        self.assertIn("fusion", err)
        self.assertEqual(self.head(worktree), head)

    def test_drop_stops_on_a_cherry_pick_failure_that_is_not_a_conflict(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        head = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        self.record(r3=[first], r12=[head])
        before = self.state()

        def failing(command, **kwargs):
            if "cherry-pick" in command and "--abort" not in command:
                return subprocess.CompletedProcess(command, 1, "", "error: simulated failure")
            return subprocess.run(command, **kwargs)

        code, result, err = self.drop(KEY, "CQ-3", run=failing)
        self.assertEqual(code, 2)
        self.assertIn("simulated failure", err)
        self.assertEqual(self.head(self.repo, f"refs/heads/{started['branch']}"), head)
        self.assert_clean_on(worktree, started["branch"])
        self.assertEqual(self.state(), before)
        real_git(self.repo, "config", "commit.gpgSign", "true")
        real_git(self.repo, "config", "gpg.program", "false")
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 2)
        self.assertEqual(self.head(self.repo, f"refs/heads/{started['branch']}"), head)
        self.assert_clean_on(worktree, started["branch"])
        self.assertEqual(self.state(), before)

    def test_drop_restores_the_worktree_when_git_fails_midway(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        branch = started["branch"]
        self.fixer_commit(worktree, "x12.txt", "fix(CQ-12): add x")
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-3): add shared")
        head = self.fixer_commit(worktree, "shared.txt", "fix(CQ-12): change shared")
        before = self.state()
        calls = []

        def flaky(command, **kwargs):
            if any(str(arg).startswith("--contains=") for arg in command):
                calls.append(command)
                if len(calls) == 2:
                    return subprocess.CompletedProcess(command, 128, "", "fatal: simulated")
            return subprocess.run(command, **kwargs)

        code, result, err = self.drop(KEY, "CQ-3", run=flaky)
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.head(self.repo, f"refs/heads/{branch}"), head)
        self.assert_clean_on(worktree, branch)
        self.assertEqual(self.state(), before)

    def test_drop_saves_the_state_when_the_worktree_cannot_go_back(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        branch = started["branch"]
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        kept = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        self.record(r3=[first], r12=[kept])

        def stuck(command, **kwargs):
            if list(command[-3:]) == ["checkout", "-q", branch]:
                return subprocess.CompletedProcess(command, 1, "", "error: simulated checkout")
            return subprocess.run(command, **kwargs)

        code, result, err = self.drop(KEY, "CQ-3", run=stuck)
        self.assertEqual(code, 2)
        self.assertIn("simulated checkout", err)
        new_head = self.head(self.repo, f"refs/heads/{branch}")
        self.assertNotEqual(new_head, kept)
        rows = self.key()["rows"]
        self.assertEqual(rows["3"]["outcome"], "dropped")
        self.assertEqual(rows["12"]["commits"], [new_head])
        real_git(worktree, "checkout", "-q", branch)
        code, result, err = self.drop(KEY, "CQ-3")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.head(worktree), new_head)

    def test_drop_moves_the_key_to_reviewed_once_every_blocking_row_is_dropped(self):
        started = self.start_ok()
        worktree = Path(started["worktree"])
        self.section_start()
        self.assertEqual(
            self.run_main(["pass-start", self.run_dir, KEY, "--pass", "1", *SECTION])[0], 0)
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        second = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        folder = self.run_dir / "fix" / "front"
        report = {"key": KEY, "pass": 1, "rows": [
            {"ref": 3, "outcome": "fixed", "change": "Ajoute a.", "uat": "Lire a.",
             "commits": [first]},
            {"ref": 12, "outcome": "fixed", "change": "Ajoute b.", "uat": "Lire b.",
             "commits": [second]},
            {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."},
        ]}
        (folder / "report-1.json").write_text(json.dumps(report), encoding="utf-8")
        review = {"key": KEY, "pass": 1, "attention": [], "rows": [
            {"ref": 3, "verdict": "blocking", "notes": "Échappement manquant."},
            {"ref": 12, "verdict": "blocking", "notes": "Test manquant."},
        ]}
        (folder / "review-1.json").write_text(json.dumps(review), encoding="utf-8")
        for kind in ("report", "review"):
            code, out, err = self.run_main(["validate", self.run_dir, KEY, kind, "--pass", "1"])
            self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.drop(KEY, "CQ-3")[0], 0)
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.drop(KEY, "CQ-12")[0], 0)
        self.assertEqual(self.key()["step"], "reviewed")
        state = self.state()
        state["keys"][KEY]["step"] = "published"
        fix_state.save(self.run_dir, state)
        self.assertEqual(self.drop(KEY, "CQ-5")[0], 0)
        self.assertEqual(self.key()["step"], "published")


class PassReviewTest(GitCase):
    """R14: each fixer pass N with fixed rows gets its own review N, from the base of pass-start."""

    def setUp(self):
        super().setUp()
        self.write_run(flight(), rows=(3, 5, 12))
        self.folder = self.run_dir / "fix" / "front"
        self.open_pass()

    def pass_start(self, number):
        self.section("start", "securite")
        code, out, err = self.run_main(
            ["pass-start", self.run_dir, KEY, "--pass", str(number), *SECTION])
        return code, json.loads(out) if out else None, err

    def write_doc(self, name, document):
        (self.folder / name).write_text(json.dumps(document), encoding="utf-8")

    def validate(self, kind, number):
        code, out, err = self.run_main(["validate", self.run_dir, KEY, kind, "--pass", str(number)])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    @staticmethod
    def fixed(ref, sha):
        return {"ref": ref, "outcome": "fixed", "change": f"Corrige {ref}.", "uat": f"Lire {ref}.",
                "commits": [sha]}

    def report(self, number, *rows):
        self.write_doc(f"report-{number}.json", {"key": KEY, "pass": number, "rows": list(rows)})
        return self.validate("report", number)

    def review(self, number, **verdicts):
        rows = [{"ref": int(ref.lstrip("r")), "verdict": verdict,
                 "notes": "À corriger." if verdict == "blocking" else None}
                for ref, verdict in verdicts.items()]
        self.write_doc(f"review-{number}.json",
                       {"key": KEY, "pass": number, "rows": rows, "attention": []})
        return self.validate("review", number)

    def first_pass(self):
        """Pass 1: CQ-3 and CQ-12 fixed, CQ-5 a question; return the worktree and both SHAs."""
        self.worktree = Path(self.start_ok()["worktree"])
        self.assertEqual(self.pass_start(1)[0], 0)
        first = self.fixer_commit(self.worktree, "a3.txt", "fix(CQ-3): add a")
        second = self.fixer_commit(self.worktree, "b12.txt", "fix(CQ-12): add b")
        question = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        self.report(1, self.fixed(3, first), self.fixed(12, second),
                    {"ref": 5, "outcome": "question", "question": question})
        return first, second

    def test_pass_start_records_the_base_of_each_pass(self):
        code, result, err = self.pass_start(1)
        self.assertEqual(code, 2, err)
        self.assertIn("start", err)
        worktree = Path(self.start_ok()["worktree"])
        code, result, err = self.pass_start(0)
        self.assertEqual(code, 2, err)
        code, result, err = self.pass_start(1)
        self.assertEqual(code, 0, err)
        first = self.head(worktree)
        self.assertEqual(result, {"pass": 1, "base": first})
        moved = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        code, result, err = self.pass_start(1)
        self.assertEqual((code, result), (0, {"pass": 1, "base": first}), err)
        self.report(1, self.fixed(3, moved), {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."},
                    {"ref": 12, "outcome": "skipped", "reason": "Hors du repo."})
        code, result, err = self.pass_start(1)
        self.assertEqual(code, 2, err)
        self.assertIn("passe 1", err)
        self.assertEqual(self.pass_start(3)[0], 0)
        code, result, err = self.pass_start(2)
        self.assertEqual(code, 2, err)
        self.assertIn("passe 3", err)
        self.assertEqual(self.key()["bases"], {"1": first, "3": moved})

    def test_an_interrupted_pass_keeps_its_base_so_its_commits_stay_in_range(self):
        """N1: commits of an interrupted run stay inside bases[N]..HEAD once pass-start reruns."""
        worktree = Path(self.start_ok()["worktree"])
        base = self.pass_start(1)[1]["base"]
        interrupted = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        self.assertEqual(self.pass_start(1)[1], {"pass": 1, "base": base})
        resumed = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        self.report(1, self.fixed(3, interrupted), self.fixed(12, resumed),
                    {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."})
        self.assertEqual(self.key()["rows"]["3"]["commits"], [interrupted])

    def test_pass_start_moves_a_base_that_left_the_branch(self):
        worktree = Path(self.start_ok()["worktree"])
        base = self.pass_start(1)[1]["base"]
        gone = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        state = self.state()
        state["keys"][KEY]["bases"]["1"] = gone
        fix_state.save(self.run_dir, state)
        real_git(worktree, "reset", "-q", "--hard", base)
        self.assertEqual(self.pass_start(1)[1], {"pass": 1, "base": base})

    def test_validate_report_refuses_a_commit_outside_its_pass(self):
        worktree = Path(self.start_ok()["worktree"])
        before = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        self.assertEqual(self.pass_start(1)[0], 0)
        self.write_doc("report-1.json", {"key": KEY, "pass": 1, "rows": [
            self.fixed(3, before),
            {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."},
            {"ref": 12, "outcome": "skipped", "reason": "Hors du repo."},
        ]})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "report", "--pass", "1"])
        self.assertEqual(code, 6, err)
        self.assertIn(f"ligne CQ-3 : commit hors de la passe 1 : {before[:12]}",
                      json.loads(out)["errors"])
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")

    def test_validate_ties_reports_and_reviews_to_their_pass(self):
        """N2: no report without pass-start, no report of a closed pass, no review before its report."""
        worktree = Path(self.start_ok()["worktree"])
        skipped = [{"ref": ref, "outcome": "skipped", "reason": "Hors du repo."} for ref in (3, 5, 12)]
        self.write_doc("report-1.json", {"key": KEY, "pass": 1, "rows": skipped})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "report", "--pass", "1"])
        self.assertEqual(code, 6, err)
        self.assertTrue(any("pass-start" in error for error in json.loads(out)["errors"]), out)
        self.first_pass_from(worktree)
        self.review(1, r3="ok", r12="ok")
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "report", "--pass", "1"])
        self.assertEqual(code, 6, err)
        self.assertIn("la passe 1 est close : sa revue est enregistrée", json.loads(out)["errors"])
        self.assertEqual(self.pass_start(2)[0], 0)
        self.write_doc("review-2.json", {"key": KEY, "pass": 2, "rows": [], "attention": []})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "review", "--pass", "2"])
        self.assertEqual(code, 6, err)
        self.assertIn("le rapport de la passe 2 n'est pas validé : validez-le avant la revue",
                      json.loads(out)["errors"])
        self.assertNotIn("2", self.key()["reviews"])
        self.report(2, {"ref": 5, "outcome": "skipped", "reason": "Laissée aux devs par le lead."})
        self.assertEqual(self.key()["reports"], [1, 2])
        self.write_doc("report-1.json", {"key": KEY, "pass": 1, "rows": skipped})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "report", "--pass", "1"])
        self.assertEqual(code, 6, err)
        self.assertIn("la passe 1 est close : le rapport de la passe 2 est déjà validé",
                      json.loads(out)["errors"])
        self.validate("review", 2)
        self.write_doc("review-3.json", {"key": KEY, "pass": 3, "rows": [], "attention": []})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "review", "--pass", "3"])
        self.assertEqual(code, 6, err)
        self.assertTrue(any("pass-start" in error for error in json.loads(out)["errors"]), out)

    def first_pass_from(self, worktree):
        """The commits and report of first_pass, on a worktree already started."""
        self.worktree = worktree
        self.assertEqual(self.pass_start(1)[0], 0)
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        second = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        question = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        self.report(1, self.fixed(3, first), self.fixed(12, second),
                    {"ref": 5, "outcome": "question", "question": question})

    def test_drop_remaps_the_bases_through_its_rewrite(self):
        """N4: a kept base follows its new SHA, a dropped one its nearest kept ancestor."""
        worktree = Path(self.start_ok()["worktree"])
        start = self.pass_start(1)[1]["base"]
        a3 = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        b12 = self.fixer_commit(worktree, "b12.txt", "fix(CQ-12): add b")
        c5 = self.fixer_commit(worktree, "c5.txt", "fix(CQ-5): add c")
        state = self.state()
        key = state["keys"][KEY]
        for ref, sha in (("3", a3), ("12", b12), ("5", c5)):
            key["rows"][ref].update(outcome="fixed", commits=[sha], **{"pass": 1})
        key["bases"] = {"1": start, "2": b12, "3": c5}
        fix_state.save(self.run_dir, state)
        drop = ["drop", self.run_dir, KEY, "--reason", "Revue bloquante."]
        self.assertEqual(self.run_main(drop + ["--refs", "CQ-12"])[0], 0)
        new_c5 = self.head(worktree)
        self.assertNotEqual(new_c5, c5)
        self.assertEqual(self.key()["bases"], {"1": start, "2": a3, "3": new_c5})
        self.assertEqual(self.run_main(drop + ["--refs", "CQ-3"])[0], 0)
        self.assertEqual(self.key()["bases"], {"1": start, "2": start, "3": self.head(worktree)})
        for number, base in self.key()["bases"].items():
            with self.subTest(base=number):
                self.assertEqual(
                    subprocess.run(["git", "-C", str(worktree), "merge-base", "--is-ancestor",
                                    base, "HEAD"]).returncode, 0)

    def test_a_later_pass_needs_its_own_review(self):
        self.first_pass()
        result = self.review(1, r3="ok", r12="ok")
        self.assertEqual(result["blocking"], [])
        self.assertEqual(self.key()["reviews"], {"1": {"blocking": []}})
        self.assertEqual(self.key()["step"], "reviewed")
        self.assertEqual(self.pass_start(2)[0], 0)
        third = self.fixer_commit(self.worktree, "c5.txt", "fix(CQ-5): add the contact")
        self.report(2, self.fixed(5, third))
        self.assertEqual(self.key()["step"], "fixed")
        self.review(1, r3="ok", r12="ok")
        self.assertEqual(self.key()["step"], "fixed")
        self.review(2, r5="ok")
        self.assertEqual(self.key()["reviews"], {"1": {"blocking": []}, "2": {"blocking": []}})
        self.assertEqual(self.key()["step"], "reviewed")

    def test_a_row_blocking_in_review_1_and_fixed_again_is_judged_by_review_2(self):
        first, second = self.first_pass()
        self.assertEqual(self.review(1, r3="blocking", r12="ok")["blocking"], [3])
        self.assertEqual(self.key()["reviews"]["1"], {"blocking": [3]})
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.pass_start(2)[0], 0)
        again = self.fixer_commit(self.worktree, "a3-bis.txt", "fix(CQ-3): escape a")
        correction = self.fixed(3, again)
        correction["commits"].append(first)
        self.report(2, correction)
        self.review(2, r3="ok")
        self.assertEqual(self.key()["step"], "reviewed")

    def test_drop_recomputes_every_pass_not_only_the_latest_review(self):
        self.first_pass()
        self.review(1, r3="blocking", r12="ok")
        self.assertEqual(self.pass_start(2)[0], 0)
        third = self.fixer_commit(self.worktree, "c5.txt", "fix(CQ-5): add the contact")
        self.report(2, self.fixed(5, third))
        self.review(2, r5="blocking")
        drop = ["drop", self.run_dir, KEY, "--reason", "Revue bloquante."]
        code, out, err = self.run_main(drop[:3] + ["--refs", "CQ-5"] + drop[3:])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["step"], "fixed")
        code, out, err = self.run_main(drop[:3] + ["--refs", "CQ-3"] + drop[3:])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["step"], "reviewed")

    def test_a_pass_without_fixed_rows_is_reviewed_at_once(self):
        """R15: validate report applies the R14 rule, so such a key never waits for a review."""
        self.start_ok()
        self.assertEqual(self.pass_start(1)[0], 0)
        question = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        self.report(1, {"ref": 3, "outcome": "skipped", "reason": "Hors du repo."},
                    {"ref": 5, "outcome": "question", "question": question},
                    {"ref": 12, "outcome": "skipped", "reason": "Déjà corrigé sur develop."})
        self.assertEqual(self.key()["step"], "reviewed")
        self.assertEqual(self.pass_start(2)[0], 0)
        self.report(2, {"ref": 5, "outcome": "skipped", "reason": "Laissée aux devs par le lead."})
        self.assertEqual(self.key()["step"], "reviewed")

    def test_a_pass_without_fixed_rows_waits_for_earlier_reviews(self):
        self.first_pass()
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.pass_start(2)[0], 0)
        self.report(2, {"ref": 5, "outcome": "skipped", "reason": "Laissée aux devs par le lead."})
        self.assertEqual(self.key()["step"], "fixed")
        self.review(1, r3="ok", r12="ok")
        self.assertEqual(self.key()["step"], "reviewed")

    def test_a_pass_with_new_fixed_rows_still_waits_for_its_review(self):
        self.first_pass()
        self.review(1, r3="ok", r12="ok")
        self.assertEqual(self.pass_start(2)[0], 0)
        third = self.fixer_commit(self.worktree, "c5.txt", "fix(CQ-5): add the contact")
        self.report(2, self.fixed(5, third))
        self.assertEqual(self.key()["step"], "fixed")

    def test_a_pending_row_keeps_the_key_from_reviewed(self):
        self.worktree = Path(self.start_ok()["worktree"])
        state = self.state()
        state["keys"][KEY]["rows"]["5"].update(outcome="skipped", reason="Hors du repo.", **{"pass": 1})
        fix_state.save(self.run_dir, state)
        code, out, err = self.run_main(["drop", self.run_dir, KEY, "--refs", "CQ-12",
                                        "--reason", "Retirée par le lead."])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")
        self.assertEqual(self.key()["step"], "started")

    def raw_report(self, number, *rows):
        self.write_doc(f"report-{number}.json", {"key": KEY, "pass": number, "rows": list(rows)})
        code, out, err = self.run_main(["validate", self.run_dir, KEY, "report", "--pass", str(number)])
        return code, json.loads(out) if out else None, err

    def test_a_row_fixed_in_an_unreviewed_pass_is_never_reported_again(self):
        """A row fixed again belongs to the new pass, whose review does not read its earlier
        commits: re-reporting it waits for the review of the pass that fixed it."""
        first, second = self.first_pass()
        self.assertEqual(self.pass_start(2)[0], 0)
        before = json.dumps(self.key(), sort_keys=True)
        skipped = {"ref": 5, "outcome": "skipped", "reason": "Laissée aux devs par le lead."}
        code, out, err = self.raw_report(2, self.fixed(3, first), self.fixed(12, second), skipped)
        self.assertEqual(code, 6, err)
        self.assertEqual(out["errors"], [
            "ligne CQ-3 : la revue de la passe 1 n'est pas enregistrée",
            "ligne CQ-12 : la revue de la passe 1 n'est pas enregistrée",
        ])
        self.assertEqual(json.dumps(self.key(), sort_keys=True), before)
        third = self.fixer_commit(self.worktree, "a3-fix.txt", "fix(CQ-3): fix the check")
        again = {**self.fixed(3, third), "commits": [first, third]}
        code, out, err = self.raw_report(2, again, skipped)
        self.assertEqual(code, 6, err)
        self.assertEqual(out["errors"], ["ligne CQ-3 : la revue de la passe 1 n'est pas enregistrée"])
        self.assertEqual(json.dumps(self.key(), sort_keys=True), before)
        self.review(1, r3="ok", r12="ok")
        code, out, err = self.raw_report(2, again, skipped)
        self.assertEqual(code, 0, err)
        row = self.key()["rows"]["3"]
        self.assertEqual((row["pass"], row["commits"]), (2, [first, third]))
        self.assertEqual(self.key()["step"], "fixed")
        self.review(2, r3="ok")
        self.assertEqual(self.key()["step"], "reviewed")

    def test_a_started_pass_without_report_keeps_the_key_from_reviewed(self):
        """R16: between pass-start N and the validated report N, the key is never reviewed."""
        first, second = self.first_pass()
        self.review(1, r3="ok", r12="blocking")
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.pass_start(2)[0], 0)
        code, out, err = self.run_main(["drop", self.run_dir, KEY, "--refs", "CQ-12",
                                        "--reason", "Revue bloquante."])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["step"], "fixed")
        self.review(1, r3="ok")
        self.assertEqual(self.key()["step"], "fixed")
        third = self.fixer_commit(self.worktree, "c5.txt", "fix(CQ-5): add the contact")
        self.report(2, self.fixed(5, third))
        self.assertEqual(self.key()["step"], "fixed")
        self.review(2, r5="ok")
        self.assertEqual(self.key()["step"], "reviewed")
        self.assertEqual(self.pass_start(3)[0], 0)
        self.assertEqual(self.key()["step"], "fixed")
        self.assertEqual(self.pass_start(3)[0], 0)
        self.report(3)
        self.assertEqual(self.key()["step"], "reviewed")


class SettledRowsTest(GitCase):
    """Section 12 of SKILL.md: a key already done needs no fixer, and a row left to the devs is
    dropped without one (R18)."""

    def setUp(self):
        super().setUp()
        self.folder = self.run_dir / "fix" / "front"

    def main_ok(self, *args):
        code, out, err = self.run_main([args[0], self.run_dir, *args[1:]])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def write_report(self, number, rows):
        (self.folder / f"report-{number}.json").write_text(
            json.dumps({"key": KEY, "pass": number, "rows": rows}), encoding="utf-8")

    def test_an_empty_report_brings_a_key_whose_rows_are_all_done_before_to_reviewed(self):
        branch = BASE
        real_git(self.repo, "checkout", "-q", "-b", branch)
        commit(self.repo, "s.txt", "fix(securite CQ-3): x")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}]),
                       rows=(3,))
        self.open_pass()
        self.assertEqual(self.start_ok()["done_refs"], [3])
        self.section_start()
        self.main_ok("pass-start", KEY, "--pass", "1", *SECTION)
        self.write_report(1, [])
        result = self.main_ok("validate", KEY, "report", "--pass", "1")
        self.assertEqual((result["ok"], result["errors"]), (True, []))
        self.assertEqual(result["rows"], {"fixed": [], "skipped": [], "question": []})
        key = self.key()
        self.assertEqual((key["step"], key["pass"]), ("reviewed", 1))
        self.assertEqual(key["rows"]["3"]["outcome"], "done_before")
        self.assertEqual(self.main_ok("guard", KEY)["ok"], True)

    def test_a_question_left_to_the_devs_is_dropped_without_touching_the_branch(self):
        self.write_run(flight(), rows=(3, 5))
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        self.section_start()
        self.main_ok("pass-start", KEY, "--pass", "1", *SECTION)
        fixed = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        question = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        self.write_report(1, [
            {"ref": 3, "outcome": "fixed", "change": "Ajoute a.", "uat": "Lire a.",
             "commits": [fixed]},
            {"ref": 5, "outcome": "question", "question": question},
        ])
        self.main_ok("validate", KEY, "report", "--pass", "1")
        result = self.main_ok("drop", KEY, "--refs", "CQ-5", "--reason",
                              "laissée aux devs par le lead")
        self.assertEqual(result, {"dropped": [5], "also_dropped": []})
        self.assertEqual(self.head(worktree), fixed)
        rows = self.key()["rows"]
        self.assertEqual(rows["5"]["outcome"], "dropped")
        self.assertEqual(rows["5"]["reason"], "laissée aux devs par le lead")
        self.assertEqual((rows["3"]["outcome"], rows["3"]["commits"]), ("fixed", [fixed]))


class ValidateReportTest(unittest.TestCase):
    """fix_guard.validate_report alone: every problem of a row comes out of one run."""

    def test_report_remaining_options_and_abandon_limits(self):
        sha = "a" * 40
        rows = {"3": {"outcome": "pending"}, "5": {"outcome": "pending"},
                "7": {"outcome": "pending"}}
        fixed = {"ref": 3, "outcome": "fixed", "change": "Ajoute a.", "uat": "Lire a.",
                 "commits": [sha[:12]]}
        skipped = {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."}
        asked = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}
        question = {"ref": 7, "outcome": "question", "question": asked}

        def check(*items, sections=None):
            return fix_guard.validate_report(
                {"key": KEY, "pass": 1, "rows": list(items)}, key=KEY, number=1, rows=rows,
                commits=[{"sha": sha, "ref": 3}], sections=sections,
            )

        self.assertEqual(fix_guard.MAX_REMAINING, 300)
        entries, errors = check({**fixed, "remaining": "r" * 300}, skipped, question)
        self.assertEqual(errors, [])
        self.assertEqual([entry["remaining"] for entry in entries], ["r" * 300, None, None])
        entries, errors = check({**fixed, "remaining": "r" * 301},
                                {**skipped, "remaining": "Tout."}, question)
        self.assertEqual(errors, [
            "ligne CQ-3 : « remaining » dépasse 300 caractères (301)",
            "ligne CQ-5 : « remaining » ne sert qu'à une ligne corrigée (fixed)",
        ])
        for option in ("Ne rien faire", "Laisser en l'état", "Laisser en l\u2019état",
                       "Laisser tel quel", "Confier la ligne à un dev", "La laisser aux devs",
                       "Laisser aux devs", "Laisser à un dev", "Pas de changement",
                       "Ne pas corriger", "RIEN FAIRE pour l'instant", "Ne rien changer",
                       "Laisser la ligne telle quelle", "Garder tel quel", "Garder en l'état",
                       "Ne pas toucher", "Ne pas toucher au fichier"):
            with self.subTest(option=option):
                refused = {**question, "question": {**asked, "options": ["Équipe", option],
                                                    "default": "Équipe"}}
                self.assertEqual(check(fixed, skipped, refused)[1], [
                    f"ligne CQ-7 : « question.options » : « {option} » laisse la ligne en "
                    "l'état ; l'orchestrateur propose déjà de la laisser aux devs"
                ])
        for option in ("Laisser un alt vide (image décorative)",
                       "Ne rien afficher si le champ est vide", "Laisser le lien ouvrir un onglet",
                       "Ajouter le devis au devant de la page", "Changement de libellé",
                       "Envoyer le formulaire au développeur", "Garder le lien et ajouter rel"):
            with self.subTest(accepted=option):
                kept = {**question, "question": {**asked, "options": ["Équipe", option],
                                                 "default": option}}
                self.assertEqual(check(fixed, skipped, kept)[1], [])
        self.assertEqual(fix_guard.MAX_ABANDON, 300)
        abandon = "Abandon : " + "a" * (fix_guard.MAX_ABANDON - len("Abandon : "))
        self.assertEqual(check({**fixed, "attention": abandon}, skipped, question)[1], [])
        self.assertEqual(check({**fixed, "attention": abandon + "a"}, skipped, question)[1], [
            "ligne CQ-3 : « attention » : un abandon (« Abandon : ») fait 300 caractères au "
            "plus (301)"
        ])
        self.assertEqual(
            check({**fixed, "attention": "Abandon\u00a0: " + "a" * 300}, skipped, question)[1],
            ["ligne CQ-3 : « attention » : un abandon (« Abandon : ») fait 300 caractères au "
             "plus (310)"])
        note = "n" * fix_guard.MAX_NOTE
        self.assertEqual(check({**fixed, "attention": note}, skipped, question)[1], [])
        sections = {"3": "securite", "5": "securite", "7": "securite", "9": "qualite"}
        stray = [{"ref": ref, "outcome": "skipped", "reason": "Hors du repo."} for ref in (9, 11)]
        self.assertEqual(check(fixed, skipped, question, *stray, sections=sections)[1], [
            "ligne CQ-9 : Réf d'une autre section (qualite)",
            "ligne CQ-11 : Réf hors de cette clé",
        ])
        self.assertEqual(check(fixed, skipped, question, *stray)[1], [
            "ligne CQ-9 : Réf hors de cette clé",
            "ligne CQ-11 : Réf hors de cette clé",
        ])

    def test_every_problem_of_a_row_is_reported_in_one_run(self):
        first, second = "a" * 40, "b" * 40
        rows = {"3": {"outcome": "fixed", "commits": [first], "pass": 1},
                "5": {"outcome": "pending"}}
        commits = [{"sha": first, "ref": 3}, {"sha": second, "ref": 5}]
        document = {"key": KEY, "pass": 2, "rows": [
            {"ref": 5, "outcome": "fixed", "change": "Ajoute b.", "commits": [second[:12]]},
            {"ref": 3, "outcome": "fixed", "change": "Corrige a.", "uat": "Lire a.",
             "commits": [first], "attention": "x" * (fix_guard.MAX_NOTE + 1)},
        ]}
        entries, errors = fix_guard.validate_report(
            document, key=KEY, number=2, rows=rows, commits=commits, pass_commits=set(),
            reviewed=set(),
        )
        self.assertEqual(entries, [])
        self.assertEqual(errors, [
            "ligne CQ-5 : « uat » est obligatoire : un texte en français de 600 caractères au plus",
            f"ligne CQ-5 : commit hors de la passe 2 : {second[:12]}",
            f"ligne CQ-3 : « attention » dépasse {fix_guard.MAX_NOTE} caractères ({fix_guard.MAX_NOTE + 1})",
            "ligne CQ-3 : la revue de la passe 1 n'est pas enregistrée",
        ])
        document["rows"][0]["uat"] = "Lire b."
        document["rows"][1]["attention"] = None
        entries, errors = fix_guard.validate_report(
            document, key=KEY, number=2, rows=rows, commits=commits, pass_commits={second},
            reviewed={1},
        )
        self.assertEqual(errors, [])
        self.assertEqual([entry["ref"] for entry in entries], [5, 3])
        entries, errors = fix_guard.validate_report(
            document, key=KEY, number=2, rows=rows, commits=commits,
        )
        self.assertEqual((errors, len(entries)), ([], 2))


class ValidateReviewTest(unittest.TestCase):
    """fix_guard.validate_review alone: review N judges the rows fixed in pass N only."""

    def test_a_verdict_on_a_row_of_another_pass_is_an_error(self):
        rows = {"3": {"outcome": "fixed", "commits": ["a" * 40], "pass": 1},
                "5": {"outcome": "fixed", "commits": ["b" * 40], "pass": 2}}
        document = {"key": KEY, "pass": 2, "attention": [], "rows": [
            {"ref": 3, "verdict": "blocking", "notes": "L'en-tête manque sur les 404."},
            {"ref": 5, "verdict": "ok", "notes": None},
        ]}
        blocking, errors = fix_guard.validate_review(document, key=KEY, number=2, rows=rows)
        self.assertEqual((blocking, errors),
                         ([], ["ligne CQ-3 : verdict sur une ligne d'une autre passe (1)"]))
        document["rows"] = document["rows"][1:]
        self.assertEqual(fix_guard.validate_review(document, key=KEY, number=2, rows=rows),
                         ([], []))


class SchemaTest(unittest.TestCase):
    def test_schemas_document_the_values_the_validator_accepts(self):
        report = json.loads((SCHEMAS / "fix-report.schema.json").read_text(encoding="utf-8"))
        row = report["$defs"]["row"]["properties"]
        self.assertEqual(row["outcome"]["enum"], list(fix_guard.REPORT_OUTCOMES))
        self.assertEqual(row["change"]["maxLength"], fix_guard.MAX_TEXT)
        self.assertEqual(row["uat"]["maxLength"], fix_guard.MAX_TEXT)
        self.assertEqual(row["reason"]["maxLength"], fix_guard.MAX_REASON)
        self.assertEqual(row["attention"]["maxLength"], fix_guard.MAX_NOTE)
        self.assertEqual(row["remaining"]["maxLength"], fix_guard.MAX_REMAINING)
        question = report["$defs"]["question"]["properties"]
        self.assertEqual(question["text"]["maxLength"], fix_guard.MAX_TEXT)
        options = question["options"]
        self.assertEqual((options["minItems"], options["maxItems"]),
                         (fix_guard.MIN_OPTIONS, fix_guard.MAX_OPTIONS))
        self.assertEqual(options["items"]["maxLength"], fix_guard.MAX_OPTION)
        review = json.loads((SCHEMAS / "fix-review.schema.json").read_text(encoding="utf-8"))
        verdict = review["$defs"]["row"]["properties"]["verdict"]
        self.assertEqual(verdict["enum"], list(fix_guard.VERDICTS))
        self.assertEqual(fix_guard.VERDICTS, ("ok", "blocking"))
        notes = review["$defs"]["row"]["properties"]["notes"]
        self.assertEqual(notes["maxLength"], fix_guard.MAX_TEXT)
        remarks = review["properties"]["attention"]["items"]
        self.assertEqual(remarks["maxLength"], fix_guard.MAX_NOTE)
        corrections = review["$defs"]["corrections"]
        self.assertEqual(corrections["additionalProperties"], False)
        self.assertEqual(
            {name: field["maxLength"] for name, field in corrections["properties"].items()},
            fix_guard.CORRECTION_LIMITS)
        self.assertEqual(fix_guard.CORRECTION_LIMITS, {
            "change": fix_guard.MAX_TEXT, "uat": fix_guard.MAX_TEXT,
            "attention": fix_guard.MAX_NOTE, "remaining": fix_guard.MAX_REMAINING})
        self.assertEqual(review["$defs"]["row"]["properties"]["corrections"],
                         {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/corrections"}],
                          "description": review["$defs"]["row"]["properties"]["corrections"]
                          ["description"]})


if __name__ == "__main__":
    unittest.main()
