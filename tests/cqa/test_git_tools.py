import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cqa.git_tools import (
    alignment,
    blob_url,
    deployed_from_gitlab,
    remote_web_url,
    repo_state,
    same_gitlab_group,
)

UNKNOWN_SHA = "0123456789abcdef0123456789abcdef01234567"
IDENTITY = (
    "-c", "user.name=Test Dev",
    "-c", "user.email=dev@example.org",
    "-c", "commit.gpgsign=false",
)
GITLAB = "git@gitlab.example.org"


def git(cwd, *args):
    result = subprocess.run(
        ["git", *IDENTITY, "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def commit(repo, name, title):
    (Path(repo) / name).write_text(title, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", title)
    return git(repo, "rev-parse", "HEAD")


def init_develop(repo):
    git(repo, "symbolic-ref", "HEAD", "refs/heads/develop")


class RemoteWebUrlTest(unittest.TestCase):
    def test_scp_like(self):
        self.assertEqual(
            remote_web_url("git@gitlab.example.org:grp/sub/repo.git"),
            "https://gitlab.example.org/grp/sub/repo",
        )

    def test_ssh_with_port(self):
        self.assertEqual(
            remote_web_url("ssh://git@gitlab.example.org:2222/grp/repo.git"),
            "https://gitlab.example.org/grp/repo",
        )

    def test_https(self):
        self.assertEqual(
            remote_web_url("https://gitlab.example.org/grp/repo.git"),
            "https://gitlab.example.org/grp/repo",
        )

    def test_https_without_suffix_and_with_trailing_slash(self):
        self.assertEqual(
            remote_web_url("https://gitlab.example.org/grp/repo/"),
            "https://gitlab.example.org/grp/repo",
        )

    def test_https_drops_credentials_and_keeps_port(self):
        self.assertEqual(
            remote_web_url("https://bob:secret@gitlab.example.org:8443/grp/repo.git"),
            "https://gitlab.example.org:8443/grp/repo",
        )

    def test_http_stays_http(self):
        self.assertEqual(
            remote_web_url("http://gitlab.example.org/grp/repo.git"),
            "http://gitlab.example.org/grp/repo",
        )

    def test_host_is_lowercased(self):
        self.assertEqual(
            remote_web_url("git@GitLab.Example.org:grp/repo.git"),
            "https://gitlab.example.org/grp/repo",
        )

    def test_unsupported_remote_raises(self):
        remotes = (
            "",
            "/srv/git/repo.git",
            "../repo.git",
            "https://gitlab.example.org",
            "https://gitlab.example.org/grp/.git",
        )
        for remote in remotes:
            with self.subTest(remote=remote), self.assertRaises(ValueError):
                remote_web_url(remote)


class SameGitlabGroupTest(unittest.TestCase):
    def test_siblings_over_ssh_and_https(self):
        self.assertTrue(
            same_gitlab_group(
                "git@gitlab.example.org:acme/back.git", "https://gitlab.example.org/acme/front"
            )
        )

    def test_nested_group(self):
        self.assertTrue(
            same_gitlab_group(
                "git@gitlab.example.org:acme/team/back.git",
                "git@gitlab.example.org:acme/team/front.git",
            )
        )

    def test_ssh_port_does_not_matter(self):
        self.assertTrue(
            same_gitlab_group(
                "ssh://git@gitlab.example.org:2222/acme/back.git",
                "https://gitlab.example.org/acme/front.git",
            )
        )

    def test_other_host(self):
        self.assertFalse(
            same_gitlab_group(f"{GITLAB}:acme/back.git", "git@git.example.net:acme/front.git")
        )

    def test_other_group(self):
        self.assertFalse(
            same_gitlab_group(f"{GITLAB}:acme/back.git", f"{GITLAB}:other/front.git")
        )

    def test_other_depth(self):
        self.assertFalse(
            same_gitlab_group(f"{GITLAB}:acme/back.git", f"{GITLAB}:acme/team/front.git")
        )

    def test_unsupported_remote_is_not_a_sibling(self):
        self.assertFalse(same_gitlab_group("/srv/git/back.git", f"{GITLAB}:acme/front.git"))


class BlobUrlTest(unittest.TestCase):
    def test_with_line(self):
        self.assertEqual(
            blob_url("https://g.example.org/a/b", "abc", "src/x.vue", 4),
            "https://g.example.org/a/b/-/blob/abc/src/x.vue#L4",
        )

    def test_without_line(self):
        self.assertEqual(
            blob_url("https://g.example.org/a/b", "abc", "src/x.vue"),
            "https://g.example.org/a/b/-/blob/abc/src/x.vue",
        )

    def test_path_is_percent_encoded(self):
        self.assertEqual(
            blob_url("https://g.example.org/a/b", "abc", "src/my file.vue", 4),
            "https://g.example.org/a/b/-/blob/abc/src/my%20file.vue#L4",
        )

    def test_reserved_and_non_ascii_characters_are_encoded(self):
        self.assertEqual(
            blob_url("https://g.example.org/a/b", "abc", "docs/v#1?/é.md"),
            "https://g.example.org/a/b/-/blob/abc/docs/v%231%3F/%C3%A9.md",
        )


class RepoFixture(unittest.TestCase):
    """A bare origin, a seed clone that pushes to it and a work clone: all under a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed repo"
        self.work = self.root / "work repo"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        init_develop(self.origin)
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.seed)],
            check=True,
            capture_output=True,
        )
        init_develop(self.seed)
        self.first = commit(self.seed, "a.txt", "first commit")
        git(self.seed, "push", "-q", "origin", "develop")
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.work)], check=True)

    def push_from_seed(self, name, title):
        sha = commit(self.seed, name, title)
        git(self.seed, "push", "-q", "origin", "develop")
        return sha


class RepoStateTest(RepoFixture):
    def test_up_to_date_and_clean(self):
        state = repo_state(self.work)
        self.assertEqual(
            state,
            {
                "branch": "develop",
                "clean": True,
                "head": self.first,
                "ahead": 0,
                "behind": 0,
                "fetched": True,
            },
        )

    def test_behind(self):
        self.push_from_seed("b.txt", "second commit")
        self.push_from_seed("c.txt", "third commit")
        state = repo_state(self.work)
        self.assertEqual((state["behind"], state["ahead"]), (2, 0))
        self.assertTrue(state["clean"])
        self.assertTrue(state["fetched"])
        self.assertEqual(state["head"], self.first)

    def test_ahead_and_behind(self):
        self.push_from_seed("b.txt", "upstream commit")
        local = commit(self.work, "local.txt", "local commit")
        state = repo_state(self.work)
        self.assertEqual((state["behind"], state["ahead"]), (1, 1))
        self.assertEqual(state["head"], local)

    def test_dirty_with_untracked_file(self):
        (self.work / "new.txt").write_text("x", encoding="utf-8")
        self.assertFalse(repo_state(self.work)["clean"])

    def test_dirty_with_modified_file(self):
        (self.work / "a.txt").write_text("changed", encoding="utf-8")
        self.assertFalse(repo_state(self.work)["clean"])

    def test_other_branch(self):
        git(self.work, "checkout", "-q", "-b", "feature/x")
        state = repo_state(self.work)
        self.assertEqual(state["branch"], "feature/x")
        self.assertEqual((state["ahead"], state["behind"]), (0, 0))

    def test_fetch_failure_keeps_last_known_counts(self):
        self.push_from_seed("b.txt", "second commit")
        git(self.work, "fetch", "-q", "origin")
        self.origin.rename(self.root / "gone.git")
        state = repo_state(self.work)
        self.assertFalse(state["fetched"])
        self.assertEqual((state["behind"], state["ahead"]), (1, 0))

    def test_unknown_base_gives_no_counts(self):
        state = repo_state(self.work, base="nope")
        self.assertEqual((state["ahead"], state["behind"]), (None, None))
        self.assertTrue(state["fetched"])
        self.assertEqual(state["head"], self.first)

    def test_not_a_repository_raises(self):
        with self.assertRaises(RuntimeError):
            repo_state(self.root)

    def test_commands_go_through_injected_run(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            outputs = {
                "rev-parse --abbrev-ref HEAD": "release\n",
                "rev-parse HEAD": "abc123\n",
                "status --porcelain": "",
                "rev-list --left-right --count origin/main...HEAD": "3\t4\n",
            }
            key = " ".join(cmd[3:])
            code = 1 if key.startswith("fetch") else 0
            return subprocess.CompletedProcess(cmd, code, outputs.get(key, ""), "")

        state = repo_state(Path("/some/repo"), base="main", run=run)
        self.assertEqual(
            state,
            {
                "branch": "release",
                "clean": True,
                "head": "abc123",
                "ahead": 4,
                "behind": 3,
                "fetched": False,
            },
        )
        self.assertEqual(calls[0], ["git", "-C", "/some/repo", "fetch", "origin", "--quiet"])


class AlignmentTest(RepoFixture):
    def test_aligned_when_deployed_is_origin_head(self):
        second = self.push_from_seed("b.txt", "second commit")
        git(self.work, "fetch", "-q", "origin")
        self.assertEqual(
            alignment(self.work, second), {"aligned": True, "missing": [], "reason": None}
        )

    def test_aligned_when_deployed_is_ahead_of_base(self):
        git(self.work, "checkout", "-q", "-b", "release")
        extra = commit(self.work, "extra.txt", "release only commit")
        self.assertTrue(alignment(self.work, extra)["aligned"])

    def test_missing_commits_are_listed_newest_first(self):
        second = self.push_from_seed("b.txt", "second commit")
        third = self.push_from_seed("c.txt", "third: with\ttab and é")
        git(self.work, "fetch", "-q", "origin")
        result = alignment(self.work, self.first)
        self.assertIs(result["aligned"], False)
        self.assertIsNone(result["reason"])
        self.assertEqual(
            result["missing"],
            [
                {"sha": third[:7], "title": "third: with\ttab and é"},
                {"sha": second[:7], "title": "second commit"},
            ],
        )

    def test_diverged_deployed_commit(self):
        git(self.work, "checkout", "-q", "-b", "release")
        deployed = commit(self.work, "extra.txt", "release only commit")
        second = self.push_from_seed("b.txt", "second commit")
        git(self.work, "fetch", "-q", "origin")
        result = alignment(self.work, deployed)
        self.assertIs(result["aligned"], False)
        self.assertEqual(result["missing"], [{"sha": second[:7], "title": "second commit"}])

    def test_unknown_commit(self):
        self.assertEqual(
            alignment(self.work, UNKNOWN_SHA),
            {"aligned": None, "missing": [], "reason": "commit_inconnu"},
        )

    def test_unknown_base_ref(self):
        self.assertEqual(
            alignment(self.work, self.first, base_ref="origin/nope"),
            {"aligned": None, "missing": [], "reason": "base_inconnue"},
        )

    def test_custom_base_ref(self):
        git(self.work, "checkout", "-q", "-b", "release")
        local = commit(self.work, "extra.txt", "release only commit")
        result = alignment(self.work, self.first, base_ref="release")
        self.assertEqual(result["missing"], [{"sha": local[:7], "title": "release only commit"}])


def glab_stub(responses):
    """run stub: the first route whose key occurs in the glab api path answers; others fail."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        for key, payload in responses.items():
            if key in cmd[2]:
                if isinstance(payload, BaseException):
                    raise payload
                if payload is None:
                    return subprocess.CompletedProcess(cmd, 1, "", "404 Not Found")
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return subprocess.CompletedProcess(cmd, 0, text, "")
        return subprocess.CompletedProcess(cmd, 1, "", "404 Not Found")

    run.calls = calls
    return run


DEPLOYMENTS = (
    "deployments?environment=preprod&status=success&order_by=finished_at&sort=desc&per_page=1"
)


class DeployedFromGitlabTest(unittest.TestCase):
    def test_from_environment(self):
        run = glab_stub(
            {
                "/environments": [{"name": "production"}, {"name": "preprod"}],
                "/deployments": [{"sha": "aaa111", "id": 7}],
            }
        )
        result = deployed_from_gitlab("acme/back", "release", run=run)
        self.assertEqual(result, {"sha": "aaa111", "source": "environment"})
        self.assertEqual(
            run.calls,
            [
                ["glab", "api", "projects/acme%2Fback/environments"],
                ["glab", "api", f"projects/acme%2Fback/{DEPLOYMENTS}"],
            ],
        )

    def test_first_matching_environment_wins(self):
        run = glab_stub(
            {
                "/environments": [{"name": "review/x"}, {"name": "staging"}, {"name": "preprod"}],
                "/deployments": [{"sha": "bbb222"}],
            }
        )
        deployed_from_gitlab("acme/back", "release", run=run)
        self.assertIn("environment=staging&", run.calls[1][2])

    def test_environment_names_match_without_case_and_with_accent(self):
        names = ("Préprod", "PREPROD", "pre-recette", "recette-client", "release", "Staging EU")
        for name in names:
            with self.subTest(name=name):
                run = glab_stub(
                    {"/environments": [{"name": name}], "/deployments": [{"sha": "ccc333"}]}
                )
                result = deployed_from_gitlab("acme/back", "release", run=run)
                self.assertEqual(result["source"], "environment")

    def test_production_is_not_a_preprod_environment(self):
        run = glab_stub(
            {
                "/environments": [{"name": "production"}, {"name": "review/fix"}],
                "/pipelines": [{"sha": "ddd444"}],
            }
        )
        result = deployed_from_gitlab("acme/back", "release", run=run)
        self.assertEqual(result, {"sha": "ddd444", "source": "pipeline"})
        self.assertEqual(len(run.calls), 2)

    def test_environment_name_is_encoded_in_query(self):
        run = glab_stub(
            {"/environments": [{"name": "staging/eu"}], "/deployments": [{"sha": "eee555"}]}
        )
        deployed_from_gitlab("acme/back", "release", run=run)
        self.assertIn("environment=staging%2Feu&", run.calls[1][2])

    def test_fallback_to_pipeline(self):
        run = glab_stub(
            {"/environments": [{"name": "production"}], "/pipelines": [{"sha": "fff666"}]}
        )
        result = deployed_from_gitlab("acme/back", "release", run=run)
        self.assertEqual(result, {"sha": "fff666", "source": "pipeline"})
        self.assertEqual(
            run.calls[-1],
            ["glab", "api", "projects/acme%2Fback/pipelines?ref=release&status=success&per_page=1"],
        )

    def test_fallback_to_pipeline_when_no_successful_deployment(self):
        run = glab_stub(
            {
                "/environments": [{"name": "preprod"}],
                "/deployments": [],
                "/pipelines": [{"sha": "111aaa"}],
            }
        )
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run),
            {"sha": "111aaa", "source": "pipeline"},
        )

    def test_fallback_to_pipeline_when_environments_call_fails(self):
        run = glab_stub({"/environments": None, "/pipelines": [{"sha": "222bbb"}]})
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run),
            {"sha": "222bbb", "source": "pipeline"},
        )

    def test_branch_is_encoded_in_query(self):
        run = glab_stub({"/pipelines": [{"sha": "333ccc"}]})
        deployed_from_gitlab("acme/back", "release/1.0", run=run)
        self.assertIn("pipelines?ref=release%2F1.0&", run.calls[-1][2])

    def test_nested_project_path_is_encoded(self):
        run = glab_stub({"/pipelines": [{"sha": "444ddd"}]})
        deployed_from_gitlab("acme/team/back", "release", run=run)
        self.assertEqual(run.calls[0][2], "projects/acme%2Fteam%2Fback/environments")

    def test_nothing_found(self):
        run = glab_stub({"/environments": [], "/pipelines": []})
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run), {"sha": None, "source": None}
        )

    def test_no_branch_skips_pipelines(self):
        run = glab_stub({"/environments": [], "/pipelines": [{"sha": "555eee"}]})
        self.assertEqual(
            deployed_from_gitlab("acme/back", None, run=run), {"sha": None, "source": None}
        )
        self.assertEqual(len(run.calls), 1)

    def test_glab_failure_everywhere(self):
        run = glab_stub({})
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run), {"sha": None, "source": None}
        )

    def test_glab_not_installed(self):
        run = glab_stub({"/": FileNotFoundError("glab")})
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run), {"sha": None, "source": None}
        )

    def test_unexpected_payloads_are_ignored(self):
        run = glab_stub(
            {
                "/environments": "not json",
                "/pipelines": {"message": "oops"},
            }
        )
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run), {"sha": None, "source": None}
        )

    def test_items_without_sha_are_ignored(self):
        run = glab_stub(
            {
                "/environments": [{"name": "preprod"}, {"id": 3}],
                "/deployments": [{"id": 1}],
                "/pipelines": [{}],
            }
        )
        self.assertEqual(
            deployed_from_gitlab("acme/back", "release", run=run), {"sha": None, "source": None}
        )


if __name__ == "__main__":
    unittest.main()
