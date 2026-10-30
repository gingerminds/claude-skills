import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

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
            ["git", "-C", "/x", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "fetch"],
        )


    def test_git_out_strips_or_gives_none(self):
        answers = iter([done([], out=" abc\n"), done([], out="\n"), done([], 1, out="abc")])
        run = lambda command, **kwargs: next(answers)
        self.assertEqual(fix_names.git_out(Path("/x"), run, "rev-parse", "HEAD"), "abc")
        self.assertIsNone(fix_names.git_out(Path("/x"), run, "rev-parse", "HEAD"))
        self.assertIsNone(fix_names.git_out(Path("/x"), run, "rev-parse", "HEAD"))


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
                if user is True:
                    return done(command, 0, '{"username": "camille"}')
                refused = user if isinstance(user, str) else "glab: 401 Unauthorized (HTTP 401)"
                return done(command, 1, "", refused)
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

    def test_a_network_failure_is_not_an_authentication_failure(self):
        """M5: only an HTTP 401 or 403 of glab api user means the lead is not logged in."""
        for stderr, status in (
            ("glab: 401 Unauthorized (HTTP 401)", "unauthenticated"),
            ("glab: 403 Forbidden (HTTP 403)", "unauthenticated"),
            ("Get \"https://gitlab.example.org/api/v4/user\": dial tcp: lookup "
             "gitlab.example.org: no such host", "unreachable"),
            ("Get \"https://gitlab.example.org/api/v4/user\": net/http: TLS handshake "
             "timeout", "unreachable"),
            ("", "unreachable"),
        ):
            with self.subTest(stderr=stderr):
                calls = []
                found = fix_gitlab.repo_access(self.fake(user=stderr, calls=calls), self.repo())
                self.assertEqual(found["status"], status)
                self.assertIsNone(found["user"])
                self.assertEqual([command[2] for command in calls], ["user"])

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
    PREFIX = fix_names.BRANCH_PREFIX

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        origin = root / "origin.git"
        self.repo = root / "repo"
        real_git(root, "init", "-q", "--bare", str(origin))
        real_git(root, "clone", "-q", str(origin), str(self.repo))
        real_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/develop")
        commit(self.repo, "a.txt", "init")
        real_git(self.repo, "push", "-q", "origin", "develop")
        real_git(self.repo, "fetch", "-q", "origin")

    def branch(self, name, subject):
        real_git(self.repo, "checkout", "-q", "-b", name, "develop")
        commit(self.repo, name.replace("/", "_") + ".txt", subject)
        real_git(self.repo, "checkout", "-q", "develop")

    def flight(self, run):
        return fix_gitlab.in_flight(
            run, {"path": str(self.repo)}, HOST, "acme/shop", "develop", api=True
        )

    def api(self, listings, commits, calls=None, fail=()):
        """Fake run: git is real, glab answers from listings (by state or branch) and commits."""

        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            path = command[-1]
            if calls is not None:
                calls.append(command)
            if "/commits" in path:
                iid = int(path.split("/merge_requests/")[1].split("/")[0])
                if iid in fail or commits.get(iid) is None:
                    return done(command, 1)
                return done(command, out=json.dumps([{"title": t} for t in commits[iid]]))
            query = path.split("merge_requests?")[1]
            key = unquote(query.split("source_branch=")[1].split("&")[0]) if "source_branch=" in query \
                else query.split("state=")[1].split("&")[0]
            value = listings.get(key)
            return done(command, 1) if value is None else done(command, out=json.dumps(value))

        return run

    def mr(self, iid, branch):
        return {"iid": iid, "web_url": f"https://{HOST}/acme/shop/-/merge_requests/{iid}",
                "source_branch": branch}

    def test_in_flight_reads_refs_from_commit_subjects(self):
        calls = []
        listings = {
            "opened": [self.mr(12, self.PREFIX + "securite"), self.mr(13, "feature/other")],
            "closed": [self.mr(9, self.PREFIX + "seo-geo")],
        }
        commits = {
            12: ["fix(CQ-107): add headers", "fix(CQ-107): follow up",
                 "Apply 1 suggestion(s) to 1 file(s)"],
            9: ["fix(CQ-40): x"],
        }
        found = self.flight(self.api(listings, commits, calls))
        self.assertEqual(
            found["open"],
            [{"iid": 12, "url": f"https://{HOST}/acme/shop/-/merge_requests/12",
              "branch": self.PREFIX + "securite", "refs": [107]}],
        )
        self.assertEqual(found["closed"][0]["refs"], [40])
        for call in calls:
            self.assertIn("--paginate", call)
        self.assertTrue(any("state=opened&per_page=100" in c[-1] for c in calls))

    def test_legacy_subjects_and_branches_are_in_flight(self):
        base = fix_names.BRANCH_BASE
        listings = {
            "opened": [self.mr(12, self.PREFIX + "securite"), self.mr(14, base),
                       self.mr(15, "dry-run/checklist-qa-fix")],
            "closed": [self.mr(16, base + "-2")],
        }
        commits = {
            12: ["fix(CQ-107): add headers"],
            14: ["fix(securite CQ-108): add more", "feat(seo CQ-9): y"],
            16: ["fix(qualite CQ-5): z"],
        }
        found = self.flight(self.api(listings, commits))
        self.assertEqual([(m["iid"], m["refs"]) for m in found["open"]],
                         [(12, [107]), (14, [9, 108])])
        self.assertEqual([(m["iid"], m["refs"]) for m in found["closed"]], [(16, [5])])

    def test_new_pass_branch_without_mr_is_listed(self):
        self.branch(fix_names.BRANCH_BASE, "fix(securite CQ-4): x")
        self.branch(self.PREFIX + "qualite", "fix(CQ-3): x")
        found = self.flight(self.api({"opened": [], "closed": []}, {}))
        self.assertEqual(
            sorted((b["branch"], b["refs"]) for b in found["branches"]),
            [(fix_names.BRANCH_BASE, [4]), (self.PREFIX + "qualite", [3])],
        )

    def test_opened_mr_beyond_the_first_page_is_found(self):
        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            path = command[-1]
            if "/commits" in path:
                return done(command, out='[{"title": "fix(CQ-1): x"}]')
            if "state=opened" in path:
                return done(command, out=json.dumps([self.mr(1, "feature/x")]) + "\n"
                            + json.dumps([self.mr(120, self.PREFIX + "securite")]))
            return done(command, out="[]")

        self.assertEqual(self.flight(run)["open"][0]["iid"], 120)

    def test_squash_merged_branch_is_found_by_the_per_branch_query(self):
        self.branch(self.PREFIX + "qualite", "fix(CQ-3): x")
        calls = []
        listings = {self.PREFIX + "qualite": [self.mr(7, self.PREFIX + "qualite")],
                    "opened": [], "closed": []}
        found = self.flight(self.api(listings, {7: ["fix(CQ-3): x"]}, calls))
        self.assertEqual([m["iid"] for m in found["merged"]], [7])
        self.assertEqual(found["merged"][0]["refs"], [3])
        self.assertEqual(found["branches"], [])
        self.assertTrue(any("source_branch=" in c[-1] and "state=merged" in c[-1] for c in calls))

    def test_unreadable_commits_give_unknown_refs_and_a_note(self):
        listings = {"opened": [self.mr(12, self.PREFIX + "securite")], "closed": []}
        found = self.flight(self.api(listings, {12: None}))
        self.assertIsNone(found["open"][0]["refs"])
        self.assertIn("Commits de la MR !12 illisibles : ses Réf sont inconnues.", found["notes"])

    def test_partial_api_failure_empties_the_mr_lists(self):
        self.branch(self.PREFIX + "qualite", "fix(CQ-3): x")
        listings = {"opened": [self.mr(12, self.PREFIX + "securite")], "closed": []}
        found = self.flight(self.api(listings, {12: ["fix(CQ-1): x"]}))
        self.assertEqual((found["open"], found["closed"], found["merged"]), ([], [], []))
        self.assertEqual(found["notes"], ["GitLab illisible : les MR existantes ne sont pas connues."])
        self.assertEqual([b["branch"] for b in found["branches"]], [self.PREFIX + "qualite"])


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
            if "source_branch=" in path:
                if prefix + "squashed" in path:
                    return done(command, out=json.dumps([{"iid": 3, "web_url": "u",
                                "source_branch": prefix + "squashed"}]))
                return done(command, out="[]")
            listing = {
                "opened": [{"iid": 1, "web_url": "u", "source_branch": prefix + "securite"}],
                "closed": [{"iid": 2, "web_url": "u", "source_branch": prefix + "seo-geo"}],
            }[path.split("state=")[1].split("&")[0]]
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
            if "source_branch=" in path:
                return done(command, out=json.dumps([{"iid": 3, "web_url": "u",
                                                      "source_branch": prefix + "squashed"}]))
            if "/commits" in path:
                return done(command, out="[]")
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
