import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cqa import fix_gitlab, fix_names
from tests.cqa.test_git_tools import commit, git as real_git

HOST = "gitlab.example.org"
REMOTE = f"git@{HOST}:acme/shop.git"
PROJECT = "projects/acme%2Fshop"


def done(command, code=0, out="", err=""):
    return subprocess.CompletedProcess(command, code, out, err)


class NamesTest(unittest.TestCase):
    def test_category_slugs(self):
        for name, slug in (
            ("SÉCURITÉ", "securite"),
            ("SEO / GEO", "seo-geo"),
            ("ÉCO-CONCEPTION / WEBPERFORMANCE", "eco-conception-webperformance"),
            ("RGPD & LÉGAL", "rgpd-legal"),
            ("PROCESS INTERNE", "process-interne"),
            ("ACCESSIBILITÉ", "accessibilite"),
            ("QUALITÉ", "qualite"),
        ):
            self.assertEqual(fix_names.category_slug(name), slug)

    def test_empty_category_is_refused(self):
        with self.assertRaises(ValueError):
            fix_names.category_slug(" / ")

    def test_ref_of_subject(self):
        self.assertEqual(fix_names.ref_of_subject("fix(CQ-107): add headers"), 107)
        self.assertEqual(fix_names.ref_of_subject("feat(CQ-3): x"), 3)
        for subject in ("fix: add headers", "fix(CQ-107):x", "Merge branch 'develop'"):
            self.assertIsNone(fix_names.ref_of_subject(subject))

    def test_branch_matches(self):
        prefix = fix_names.BRANCH_PREFIX
        self.assertTrue(fix_names.branch_matches(prefix + "securite", prefix, "securite"))
        self.assertTrue(fix_names.branch_matches(prefix + "securite-2", prefix, "securite"))
        self.assertFalse(fix_names.branch_matches(prefix + "securite-x", prefix, "securite"))
        self.assertFalse(fix_names.branch_matches(prefix + "seo-geo", prefix, "securite"))

    def test_git_disables_hooks(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return done(command)

        fix_names.git(Path("/x"), run, "fetch")
        self.assertEqual(
            calls[0],
            ["git", "-C", "/x", "-c", "core.hooksPath=/dev/null", "fetch"],
        )


class RemoteTest(unittest.TestCase):
    def test_host_and_path_of_scp_ssh_and_https_remotes(self):
        self.assertEqual(
            fix_gitlab.host_and_path("git@gitlab.example.org:acme/shop.git"),
            (HOST, "acme/shop"),
        )
        self.assertEqual(
            fix_gitlab.host_and_path("ssh://git@gitlab.example.org:2222/acme/shop.git"),
            (HOST, "acme/shop"),
        )
        self.assertEqual(
            fix_gitlab.host_and_path("https://gitlab.example.org:8443/acme/web/shop.git"),
            ("gitlab.example.org:8443", "acme/web/shop"),
        )

    def test_glab_gets_the_host_of_the_repo(self):
        envs = []

        def run(command, **kwargs):
            envs.append(kwargs["env"].get("GITLAB_HOST"))
            return done(command, out="{}")

        fix_gitlab.glab(run, HOST, "api", "user")
        fix_gitlab.glab_json(run, HOST, "version")
        self.assertEqual(envs, [HOST, HOST])

    def test_glab_json_reads_paginated_pages(self):
        def run(command, **kwargs):
            return done(command, out='[{"a": 1}]\n[{"a": 2}]')

        self.assertEqual(fix_gitlab.glab_json(run, HOST, "x", paginate=True), [{"a": 1}, {"a": 2}])

    def test_glab_json_is_none_on_failure_or_text(self):
        self.assertIsNone(fix_gitlab.glab_json(lambda c, **k: done(c, 1), HOST, "x"))
        self.assertIsNone(fix_gitlab.glab_json(lambda c, **k: done(c, out="<html>"), HOST, "x"))

        def missing(command, **kwargs):
            raise FileNotFoundError("glab")

        self.assertIsNone(fix_gitlab.glab_json(missing, HOST, "x"))


class AccessTest(unittest.TestCase):
    def fake(self, user=True, version="{}", level=30, develop=True, calls=None):
        def run(command, **kwargs):
            if calls is not None:
                calls.append(command)
            if command[0] == "git":
                out = "abc\trefs/heads/develop\n" if develop else ""
                return done(command, out=out)
            api = command[2]
            if api == "user":
                return done(command, 0, '{"username": "camille"}') if user else done(command, 1)
            if api == "version":
                return done(command, out=version)
            body = {"default_branch": "main", "permissions": {"project_access": {"access_level": level}}}
            return done(command, out=json.dumps(body))

        return run

    def repo(self, remote=REMOTE):
        return {"name": "shop", "path": "/nowhere", "remote": remote}

    def test_repo_access_statuses(self):
        def missing(command, **kwargs):
            raise FileNotFoundError("glab")

        self.assertEqual(fix_gitlab.repo_access(missing, self.repo())["status"], "glab_missing")
        calls = []
        github = fix_gitlab.repo_access(self.fake(calls=calls), self.repo("git@github.com:acme/shop.git"))
        self.assertEqual(github["status"], "not_gitlab")
        self.assertEqual(calls, [])
        self.assertEqual(
            fix_gitlab.repo_access(self.fake(user=False), self.repo())["status"], "unauthenticated"
        )
        self.assertEqual(
            fix_gitlab.repo_access(self.fake(version="<html>"), self.repo())["status"], "not_gitlab"
        )
        self.assertEqual(fix_gitlab.repo_access(self.fake(level=20), self.repo())["status"], "no_push")
        ok = fix_gitlab.repo_access(self.fake(), self.repo())
        self.assertEqual((ok["status"], ok["user"], ok["base"]), ("ok", "camille", "develop"))
        self.assertEqual((ok["host"], ok["project"], ok["repo"]), (HOST, "acme/shop", "shop"))
        main = fix_gitlab.repo_access(self.fake(develop=False), self.repo())
        self.assertEqual(main["base"], "main")

    def test_group_access_level_counts(self):
        def run(command, **kwargs):
            if command[0] == "git":
                return done(command, out="x")
            api = command[2]
            if api == "user":
                return done(command, out='{"username": "camille"}')
            body = {"permissions": {"project_access": None, "group_access": {"access_level": 40}}}
            return done(command, out=json.dumps(body))

        self.assertEqual(fix_gitlab.repo_access(run, self.repo())["status"], "ok")


class InFlightApiTest(unittest.TestCase):
    def test_in_flight_reads_refs_from_commit_subjects(self):
        urls = []
        mrs = {
            "opened": [
                {"iid": 12, "web_url": "https://gitlab.example.org/acme/shop/-/merge_requests/12",
                 "source_branch": "feature/checklist-qa-fix-securite"},
                {"iid": 13, "web_url": "u", "source_branch": "feature/other"},
            ],
            "closed": [{"iid": 9, "web_url": "u9", "source_branch": "feature/checklist-qa-fix-seo-geo"}],
            "merged": [{"iid": 7, "web_url": "u7", "source_branch": "feature/checklist-qa-fix-qualite"}],
        }
        commits = {
            12: ["fix(CQ-107): add headers", "fix(CQ-107): follow up", "Apply 1 suggestion(s) to 1 file(s)"],
            9: ["fix(CQ-40): x"],
            7: ["fix(CQ-3): y"],
        }

        def run(command, **kwargs):
            if command[0] == "git":
                return done(command, 128)
            urls.append(command)
            path = command[-1]
            if "/commits" in path:
                iid = int(path.split("/merge_requests/")[1].split("/")[0])
                return done(command, out=json.dumps([{"title": t} for t in commits[iid]]))
            state = path.split("state=")[1].split("&")[0]
            return done(command, out=json.dumps(mrs[state]))

        found = fix_gitlab.in_flight(run, {"path": "/nowhere"}, HOST, "acme/shop", "develop", api=True)
        self.assertEqual(
            found["open"],
            [{"iid": 12, "url": "https://gitlab.example.org/acme/shop/-/merge_requests/12",
              "branch": "feature/checklist-qa-fix-securite", "refs": [107]}],
        )
        self.assertEqual(found["closed"][0]["refs"], [40])
        self.assertEqual(found["merged"][0]["iid"], 7)
        commit_calls = [c for c in urls if "/commits" in c[-1]]
        self.assertEqual(len(commit_calls), 3)
        for call in commit_calls:
            self.assertIn("--paginate", call)
            self.assertTrue(call[-1].endswith("commits?per_page=100"))
        self.assertTrue(any("state=opened&per_page=100" in c[-1] for c in urls))


class InFlightGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        self.repo = root / "repo"
        real_git(root, "init", "-q", "--bare", str(self.origin))
        real_git(root, "clone", "-q", str(self.origin), str(self.repo))
        real_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/develop")
        commit(self.repo, "a.txt", "init")
        real_git(self.repo, "push", "-q", "origin", "develop")
        real_git(self.repo, "fetch", "-q", "origin")

    def branch(self, name, subject):
        real_git(self.repo, "checkout", "-q", "-b", name, "develop")
        commit(self.repo, name.replace("/", "_") + ".txt", subject)
        real_git(self.repo, "checkout", "-q", "develop")

    def flight(self, api=False, run=None):
        run = run or subprocess.run
        return fix_gitlab.in_flight(
            run, {"path": str(self.repo)}, HOST, "acme/shop", "develop", api=api
        )

    def test_in_flight_lists_unmerged_branches_without_mr(self):
        prefix = fix_names.BRANCH_PREFIX
        self.branch(prefix + "qualite", "fix(CQ-3): x")
        self.branch(prefix + "securite", "fix(CQ-4): x")
        self.branch(prefix + "seo-geo", "fix(CQ-5): x")
        self.branch(fix_names.DRY_RUN_PREFIX + "qualite", "fix(CQ-6): x")
        real_git(self.repo, "checkout", "-q", "-b", prefix + "merged", "develop")
        commit(self.repo, "m.txt", "fix(CQ-7): x")
        real_git(self.repo, "checkout", "-q", "develop")
        real_git(self.repo, "merge", "-q", "--ff-only", prefix + "merged")
        real_git(self.repo, "push", "-q", "origin", "develop")
        real_git(self.repo, "fetch", "-q", "origin")

        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            path = command[-1]
            if "/commits" in path:
                return done(command, out="[]")
            state = path.split("state=")[1].split("&")[0]
            listing = {
                "opened": [{"iid": 1, "web_url": "u", "source_branch": prefix + "securite"}],
                "closed": [{"iid": 2, "web_url": "u", "source_branch": prefix + "seo-geo"}],
                "merged": [{"iid": 3, "web_url": "u", "source_branch": prefix + "squashed"}],
            }[state]
            return done(command, out=json.dumps(listing))

        found = self.flight(api=True, run=run)
        self.assertEqual(
            found["branches"], [{"branch": prefix + "qualite", "where": "local", "refs": [3]}]
        )

    def test_squash_merged_branch_with_mr_is_not_listed(self):
        prefix = fix_names.BRANCH_PREFIX
        self.branch(prefix + "squashed", "fix(CQ-9): x")

        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            path = command[-1]
            if "state=merged" in path:
                return done(command, out=json.dumps([{"iid": 3, "web_url": "u",
                                                      "source_branch": prefix + "squashed"}]))
            return done(command, out="[]")

        self.assertEqual(self.flight(api=True, run=run)["branches"], [])

    def test_in_flight_without_api_reads_branches_only(self):
        prefix = fix_names.BRANCH_PREFIX
        self.branch(prefix + "qualite", "fix(CQ-3): x")
        real_git(self.repo, "push", "-q", "origin", prefix + "qualite")
        self.branch(prefix + "securite", "fix(CQ-4): x")
        real_git(self.repo, "push", "-q", "origin", prefix + "securite")
        real_git(self.repo, "branch", "-q", "-D", prefix + "securite")
        real_git(self.repo, "fetch", "-q", "origin")
        found = self.flight(api=False)
        self.assertEqual((found["open"], found["closed"], found["merged"]), ([], [], []))
        self.assertEqual(
            found["notes"], ["GitLab illisible : les MR existantes ne sont pas connues."]
        )
        self.assertEqual(
            found["branches"],
            [{"branch": prefix + "qualite", "where": "both", "refs": [3]},
             {"branch": prefix + "securite", "where": "origin", "refs": [4]}],
        )


if __name__ == "__main__":
    unittest.main()
