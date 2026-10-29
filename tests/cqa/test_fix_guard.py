import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cqa import fix_guard, fix_names, fix_state
from tests.cqa.test_fix_git import GitCase, IDENTITY, PREFIX, flight, mr
from tests.cqa.test_git_tools import commit, git as real_git

SCHEMAS = Path(__file__).resolve().parents[2] / "skills" / "checklist-quality-audit" / "schemas"
KEY = "front/securite"

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
        return fix_guard.check_commits(
            self.repo, self.start, set(rows), repo or {"name": "front"}, list(known), subprocess.run
        )

    @staticmethod
    def reasons(problems):
        return {problem["path"]: problem["reason"] for problem in problems}


class GuardPathTest(RepoCase):
    def test_guard_refuses_third_party_and_lockfiles(self):
        repo = {"name": "front", "stack": "wordpress",
                "third_party_paths": ["web/app/plugins/acme-forms"]}
        first = self.add("fix(CQ-3): touch a plugin",
                         {"web/app/plugins/acme-forms/x.php": "<?php", "composer.lock": "{}"})
        self.assertEqual(self.check(repo), [
            {"sha": first, "ref": 3, "path": "composer.lock", "reason": DEPENDENCIES},
            {"sha": first, "ref": 3, "path": "web/app/plugins/acme-forms/x.php", "reason": THIRD_PARTY},
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
        self.add("fix(CQ-12): touch many files",
                 {**{path: "x" for path in refused}, **{path: "y" for path in accepted}})
        real_git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.start},libs/sub")
        real_git(self.repo, "commit", "-q", "-m", "fix(CQ-12): add a submodule")
        later = [problem for problem in self.check(repo) if problem["sha"] != first]
        self.assertEqual(self.reasons(later), {**refused, "libs/sub": SUBMODULE})
        self.assertEqual({problem["ref"] for problem in later}, {12})

    def test_guard_refuses_core_paths_per_stack(self):
        wordpress = ["wp-includes/x.php", "wp-admin/y.php", "wp-login.php", "web/wp/wp-settings.php"]
        drupal = ["web/core/lib/x.php", "core/x.php", "web/modules/contrib/y/y.module",
                  "web/libraries/z.js"]
        accepted = ["wp-config.php", "wp-content/themes/t/functions.php",
                    "web/modules/custom/z/z.module"]
        self.add("fix(CQ-3): touch the core", {path: "x" for path in wordpress + drupal + accepted})
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
        self.add("fix(CQ-107): add headers", {"a.php": "1"}, "The audit found no header.")
        bare = self.add("fix: add headers", {"b.php": "1"})
        glued = self.add("fix(CQ-107):add headers", {"b2.php": "1"})
        stray = self.add("fix(CQ-999): x", {"c.php": "1"})
        coauthor = self.add("fix(CQ-107): add d", {"d.php": "1"}, "Adds d.",
                            "Co-Authored-By: Claude <noreply@example.test>")
        generated = self.add("fix(CQ-107): add e", {"e.php": "1"},
                             "\U0001F916 Generated with [Claude Code](https://claude.example.test)")
        session = self.add("fix(CQ-107): add f", {"f.php": "1"},
                           "Claude-Session: https://claude.example.test/session")
        real_git(self.repo, "checkout", "-q", "-b", "side")
        self.add("fix(CQ-3): side work", {"g.php": "1"})
        real_git(self.repo, "checkout", "-q", "-")
        real_git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        merge = real_git(self.repo, "rev-parse", "HEAD")
        self.assertCountEqual(self.check(), [
            {"sha": bare, "ref": None, "path": None, "reason": BAD_MESSAGE},
            {"sha": glued, "ref": None, "path": None, "reason": BAD_MESSAGE},
            {"sha": stray, "ref": 999, "path": None, "reason": OUT_OF_MR},
            {"sha": coauthor, "ref": 107, "path": None, "reason": ATTRIBUTION},
            {"sha": generated, "ref": 107, "path": None, "reason": ATTRIBUTION},
            {"sha": session, "ref": 107, "path": None, "reason": ATTRIBUTION},
            {"sha": merge, "ref": None, "path": None, "reason": MERGE},
        ])

    def test_guard_scans_only_added_lines(self):
        known = ["s3cr3t-value"]
        write(self.repo, "old.php", "<?php\n$auth = 's3cr3t-value';\n")
        real_git(self.repo, "add", "old.php")
        real_git(self.repo, "commit", "-q", "-m", "chore: before the pass")
        self.start = real_git(self.repo, "rev-parse", "HEAD")
        self.add("fix(CQ-3): edit old", {"old.php": "<?php\n$auth = getenv('AUTH');\n"})
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        added = self.add("fix(CQ-3): add a key", {"new.php": f"<?php\n$key = '{aws}';\n"})
        self.assertEqual(self.check(known=known), [
            {"sha": added, "ref": 3, "path": "new.php", "reason": "secret détecté (aws_key)"},
        ])


class GuardCommandTest(GitCase):
    def test_guard_ignores_commits_before_start_sha(self):
        branch = PREFIX + "securite"
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
        self.fixer_commit(started["worktree"], "follow.txt", "fix(CQ-3): follow up")
        code, out, err = self.run_main(["guard", self.run_dir, KEY])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"ok": True, "problems": []})
        earlier = fix_guard.check_commits(
            Path(started["worktree"]), before, {3, 5}, {"name": "front"}, [], subprocess.run
        )
        self.assertEqual({problem["reason"] for problem in earlier}, {BAD_MESSAGE, MERGE})

    def test_guard_finds_a_secret_without_printing_it(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        leaked = self.commit_files(worktree, "fix(CQ-3): read the value",
                                   {"web/config.php": "<?php\n$auth = 's3cr3t-value';\n"})
        self.commit_files(worktree, "fix(CQ-3): drop the value", {"web/config.php": "<?php\n"})
        message = self.commit_files(worktree, "fix(CQ-5): rotate s3cr3t-value", {"b.txt": "b"})
        code, out, err = self.run_main(
            ["guard", self.run_dir, KEY], env={"GM_AUDIT_AUTH_PASS": "s3cr3t-value"}
        )
        self.assertEqual(code, 5, err)
        self.assertEqual(json.loads(out), {"ok": False, "problems": [
            {"sha": leaked, "ref": 3, "path": "web/config.php", "reason": KNOWN_SECRET},
            {"sha": message, "ref": 5, "path": None, "reason": KNOWN_SECRET},
        ]})
        self.assertNotIn("s3cr3t-value", out)
        self.assertNotIn("s3cr3t-value", err)
        self.assertEqual(self.run_main(["guard", self.run_dir, KEY])[0], 0)

    def commit_files(self, worktree, message, files):
        for path, content in files.items():
            write(worktree, path, content)
            self.assertEqual(fix_names.git(worktree, subprocess.run, "add", "--", path).returncode, 0)
        done = fix_names.git(worktree, subprocess.run, "commit", "-q", "-m", message)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.head(worktree)


class ValidateTest(GitCase):
    def setUp(self):
        super().setUp()
        self.folder = self.run_dir / "fix" / "front" / "securite"

    def started(self):
        self.open_pass()
        started = self.start_ok()
        self.worktree = Path(started["worktree"])
        self.start_sha = started["start_sha"]
        return started

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
            "attention": "Vérifier le cache du CDN.",
        })
        self.assertEqual(key["rows"]["5"], {
            "outcome": "skipped", "reason": "Demande une décision de l'équipe UI/UX.",
            "commits": [], "pass": 1, "attention": None,
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
        document.update(key="front/qualite", rows=document["rows"][:1])
        document["pass"] = 2
        self.write_doc("report-1.json", document)
        code, result, err = self.validate("report")
        self.assertEqual(code, 6)
        joined = " ".join(result["errors"])
        for expected in ("front/qualite", "« pass »", "CQ-5"):
            self.assertIn(expected, joined)
        (self.folder / "report-1.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.validate("report")[0], 6)

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
        self.write_run(flight(), slugs=("securite", "qualite"), rows=(3, 5, 12))
        self.open_pass()

    def drop(self, key, refs, reason="Revue bloquante : échappement manquant."):
        code, out, err = self.run_main(["drop", self.run_dir, key, "--refs", refs, "--reason", reason])
        return code, json.loads(out) if out else None, err

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

        other = self.start_ok("front/qualite")
        worktree = Path(other["worktree"])
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-3): add shared")
        self.fixer_commit(worktree, "shared.txt", "fix(CQ-12): change shared")
        code, result, err = self.drop("front/qualite", "CQ-3")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [3], "also_dropped": [12]})
        self.assertEqual(self.head(worktree), other["start_sha"])
        self.assert_clean_on(worktree, other["branch"])
        rows = self.key("front/qualite")["rows"]
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
        head = self.fixer_commit(worktree, "a.txt", "fix(CQ-3): add a")
        code, out, err = self.run_main(["guard", self.run_dir, KEY])
        self.assertEqual(code, 5)
        self.assertIn({"sha": started["start_sha"], "ref": None, "path": None,
                       "reason": fix_guard.REWRITTEN}, json.loads(out)["problems"])
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


class SchemaTest(unittest.TestCase):
    def test_schemas_document_the_values_the_validator_accepts(self):
        report = json.loads((SCHEMAS / "fix-report.schema.json").read_text(encoding="utf-8"))
        row = report["$defs"]["row"]["properties"]
        self.assertEqual(row["outcome"]["enum"], list(fix_guard.REPORT_OUTCOMES))
        self.assertEqual(row["change"]["maxLength"], fix_guard.MAX_TEXT)
        self.assertEqual(row["reason"]["maxLength"], fix_guard.MAX_REASON)
        options = report["$defs"]["question"]["properties"]["options"]
        self.assertEqual((options["minItems"], options["maxItems"]),
                         (fix_guard.MIN_OPTIONS, fix_guard.MAX_OPTIONS))
        review = json.loads((SCHEMAS / "fix-review.schema.json").read_text(encoding="utf-8"))
        verdict = review["$defs"]["row"]["properties"]["verdict"]
        self.assertEqual(verdict["enum"], list(fix_guard.VERDICTS))
        self.assertEqual(fix_guard.VERDICTS, ("ok", "blocking"))


if __name__ == "__main__":
    unittest.main()
