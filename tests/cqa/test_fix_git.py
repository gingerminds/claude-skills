import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from cqa import fix_git, fix_gitlab, fix_names, fix_state
from tests.cqa.test_cli_fix import FixCase, REMOTE
from tests.cqa.test_git_tools import commit, git as real_git

KEY = "front"
BASE = fix_names.BRANCH_BASE
DRY = fix_names.DRY_RUN_BASE
PREFIX = fix_names.BRANCH_PREFIX
V2_PASS = "Passe v2 inachevée : abandonnez-la (cqa_fix.py abandon), puis relancez fix."
DIVERGED = "La branche distante a divergé : intégrez ses commits à la main, rien n'a été poussé."
HOST = "gitlab.example.org"
HOOKS = ("post-checkout", "pre-commit", "commit-msg", "pre-push", "post-commit",
         "reference-transaction")
IDENTITY = {
    "GIT_AUTHOR_NAME": "Camille", "GIT_AUTHOR_EMAIL": "camille@example.test",
    "GIT_COMMITTER_NAME": "Camille", "GIT_COMMITTER_EMAIL": "camille@example.test",
}


def mr(iid, branch, refs):
    return {"iid": iid, "url": f"https://{HOST}/acme/shop/-/merge_requests/{iid}",
            "branch": branch, "refs": refs}


def flight(**lists):
    found = {"open": [], "closed": [], "merged": [], "branches": [], "notes": []}
    found.update(lists)
    return found


class GitCase(FixCase):
    """A bare origin, the dev's clone (front), a second clone (other) and a run directory."""

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
        self.origin = self.root / "origin.git"
        self.repo = self.root / "front"
        self.other = self.root / "other"
        real_git(self.root, "init", "-q", "--bare", str(self.origin))
        real_git(self.origin, "symbolic-ref", "HEAD", "refs/heads/develop")
        real_git(self.root, "clone", "-q", str(self.origin), str(self.repo))
        real_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/develop")
        commit(self.repo, "a.txt", "init")
        real_git(self.repo, "push", "-q", "origin", "develop")
        real_git(self.root, "clone", "-q", "-b", "develop", str(self.origin), str(self.other))
        self.run_dir = self.root / "run"
        self.write_run(flight())

    def write_run(self, repo_flight, rows=(3, 5), sections=None):
        """Write context, doctor and candidates: one key, front, whose sections map an alias
        to its refs (one securite section with rows by default)."""
        (self.run_dir / "fix").mkdir(parents=True, exist_ok=True)
        repos = [{"name": "front", "path": str(self.repo), "remote": REMOTE}]
        (self.run_dir / "context.json").write_text(json.dumps({"repos": repos}), encoding="utf-8")
        doctor = {"repos": [{"repo": "front", "host": HOST, "project": "acme/shop",
                             "status": "ok", "user": "camille", "base": "develop"}]}
        self.write_json("fix/doctor.json", doctor)
        key = {"key": KEY, "repo": "front", "push_ready": True, "sections": [
            {"category": alias.upper(), "alias": alias,
             "rows": [{"ref": ref, "title": f"Ligne {ref}"} for ref in refs]}
            for alias, refs in (sections or {"securite": rows}).items()
        ]}
        self.write_json("fix/candidates.json", {
            "keys": [key], "left": [], "flights": {"front": repo_flight}, "notes": [],
            "estimate_min": 0,
        })

    def write_json(self, relative, value):
        (self.run_dir / relative).write_text(json.dumps(value), encoding="utf-8")

    def open_pass(self, push="mr", mode="present"):
        code, out, err = self.run_main(
            ["open", self.run_dir, "--mode", mode, "--push", push, "--notion", "no"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def start(self, key=KEY):
        return self.run_main(["start", self.run_dir, key])

    def start_ok(self, key=KEY):
        code, out, err = self.start(key)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def section(self, action, alias, key=KEY):
        """Run section-start or section-end; return (code, result or None, err)."""
        code, out, err = self.run_main([f"section-{action}", self.run_dir, key, "--section", alias])
        return code, json.loads(out) if out else None, err

    def section_start(self, alias="securite", key=KEY):
        code, result, err = self.section("start", alias, key)
        self.assertEqual(code, 0, err)
        return result

    def state(self):
        return fix_state.load(self.run_dir)

    def key(self, key=KEY):
        return self.state()["keys"][key]

    def head(self, path, ref="HEAD"):
        return real_git(path, "rev-parse", ref)

    def push_branch(self, name, *subjects, start="origin/develop"):
        """Create a branch in the other clone with one commit per subject, push it; return its head."""
        real_git(self.other, "fetch", "-q", "origin")
        real_git(self.other, "checkout", "-q", "-B", name, start)
        for index, subject in enumerate(subjects):
            commit(self.other, f"{name.replace('/', '_')}-{index}.txt", subject)
        real_git(self.other, "push", "-q", "origin", f"{name}:refs/heads/{name}")
        real_git(self.other, "checkout", "-q", "develop")
        return self.head(self.other, name)

    def advance_develop(self, subject="chore: move develop"):
        real_git(self.other, "pull", "-q", "origin", "develop")
        sha = commit(self.other, "develop.txt", subject)
        real_git(self.other, "push", "-q", "origin", "develop")
        return sha

    def fixer_commit(self, worktree, name, subject):
        (Path(worktree) / name).write_text(subject, encoding="utf-8")
        for args in (("add", name), ("commit", "-q", "-m", subject)):
            result = fix_names.git(Path(worktree), subprocess.run, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        return self.head(worktree)

    def tracking_config(self):
        found = subprocess.run(
            ["git", "-C", str(self.repo), "config", "--get-regexp", r"^branch\.feature/checklist-qa-fix"],
            capture_output=True, text=True,
        )
        return found.stdout

    def worktree_paths(self):
        listing = real_git(self.repo, "worktree", "list", "--porcelain")
        return [Path(line[len("worktree "):]).resolve() for line in listing.splitlines()
                if line.startswith("worktree ")]


class DevCheckoutTest(GitCase):
    def snapshot(self):
        return {
            "status": real_git(self.repo, "status", "--porcelain=v1", "-uall"),
            "head": self.head(self.repo),
            "branch": real_git(self.repo, "symbolic-ref", "--short", "HEAD"),
            "stash": real_git(self.repo, "stash", "list", "--format=%H"),
            "develop": self.head(self.repo, "refs/heads/develop"),
            "config": (self.repo / ".git" / "config").read_bytes(),
            "files": {name: (self.repo / name).read_bytes() for name in ("a.txt", "b.txt", "notes.txt")},
        }

    def install_hooks(self):
        """Install hooks that write a witness file; return its path."""
        marker = self.root / "marker"
        for name in HOOKS:
            hook = self.repo / ".git" / "hooks" / name
            hook.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n", encoding="utf-8")
            hook.chmod(0o755)
        return marker

    def test_start_leaves_the_dev_checkout_untouched(self):
        expected = self.advance_develop()
        real_git(self.repo, "checkout", "-q", "-b", "feature/other")
        commit(self.repo, "b.txt", "other work")
        (self.repo / "a.txt").write_text("stashed", encoding="utf-8")
        real_git(self.repo, "stash", "-q")
        (self.repo / "b.txt").write_text("edited", encoding="utf-8")
        (self.repo / "notes.txt").write_text("untracked", encoding="utf-8")
        marker = self.install_hooks()
        before = self.snapshot()
        self.assertNotEqual(before["develop"], expected)
        self.assertEqual(len(before["stash"].splitlines()), 1)
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(result["start_sha"], expected)
        self.assertEqual(result["worktree"], str(self.run_dir / "fix" / "front" / "wt"))
        self.fixer_commit(result["worktree"], "c.txt", "fix(CQ-3): add c")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(marker.exists())
        self.assertEqual(self.tracking_config(), "")
        real_git(result["worktree"], "commit", "-q", "--allow-empty", "-m", "hooks are live")
        self.assertTrue(marker.exists())

    def test_takeover_runs_no_dev_hook(self):
        branch = BASE
        self.push_branch(branch, "fix(CQ-107): one")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", branch, f"origin/{branch}")
        remote = self.push_branch(branch, "fix(CQ-108): two", start=f"origin/{branch}")
        self.write_run(flight(open=[mr(12, branch, [107, 108])]))
        marker = self.install_hooks()
        config = (self.repo / ".git" / "config").read_bytes()
        self.open_pass()
        result = self.start_ok()
        self.assertEqual((result["reused"], self.head(result["worktree"])), ("mr", remote))
        self.assertEqual(self.head(self.repo, f"refs/heads/{branch}"), remote)
        self.fixer_commit(result["worktree"], "c.txt", "fix(CQ-3): add c")
        self.assertFalse(marker.exists())
        self.assertEqual((self.repo / ".git" / "config").read_bytes(), config)
        self.assertEqual(self.tracking_config(), "")
        real_git(result["worktree"], "commit", "-q", "--allow-empty", "-m", "hooks are live")
        self.assertTrue(marker.exists())

    def test_dev_fingerprint_names_the_failing_command(self):
        for word, label in (("status", "git status"), ("stash", "git stash list")):
            def run(command, **kwargs):
                if word in command:
                    return subprocess.CompletedProcess(command, 128, "", f"fatal: {word} broke")
                return subprocess.run(command, **kwargs)

            with self.subTest(word=word):
                with self.assertRaises(ValueError) as caught:
                    fix_state.dev_fingerprint(self.repo, run)
                self.assertIn(label, str(caught.exception))
                self.assertIn(f"{word} broke", str(caught.exception))

    def test_dev_fingerprint_reads_head_branch_stash_and_status(self):
        real_git(self.repo, "checkout", "-q", "-b", "feature/other")
        commit(self.repo, "b.txt", "other work")
        (self.repo / "a.txt").write_text("stashed", encoding="utf-8")
        real_git(self.repo, "stash", "-q")
        (self.repo / "b.txt").write_text("edited", encoding="utf-8")
        (self.repo / "notes.txt").write_text("untracked", encoding="utf-8")
        index = (self.repo / ".git" / "index").read_bytes()
        first = fix_state.dev_fingerprint(self.repo, subprocess.run)
        self.assertEqual(first["head"], self.head(self.repo))
        self.assertEqual(first["branch"], "feature/other")
        self.assertEqual(first["stash"], [self.head(self.repo, "refs/stash")])
        self.assertEqual(sorted(first["status"]), ["b.txt", "notes.txt"])
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)
        self.assertEqual(fix_state.dev_fingerprint(self.repo, subprocess.run), first)
        (self.repo / "b.txt").write_text("edited again", encoding="utf-8")
        second = fix_state.dev_fingerprint(self.repo, subprocess.run)
        self.assertNotEqual(second["status"]["b.txt"], first["status"]["b.txt"])
        self.assertEqual(second["status"]["notes.txt"], first["status"]["notes.txt"])
        real_git(self.repo, "checkout", "-q", "--detach")
        self.assertIsNone(fix_state.dev_fingerprint(self.repo, subprocess.run)["branch"])
        (self.repo / "a.txt").unlink()
        os.mkfifo(self.repo / "a.txt")
        self.assertIn("a.txt", fix_state.dev_fingerprint(self.repo, subprocess.run)["status"])


class StartTest(GitCase):
    def test_start_creates_the_branch_from_origin_develop(self):
        expected = self.advance_develop()
        self.open_pass()
        result = self.start_ok()
        folder = self.run_dir / "fix" / "front"
        self.assertEqual(result, {
            "branch": BASE, "worktree": str(folder / "wt"), "base": "develop",
            "start_sha": expected, "reused": None, "done_refs": [],
        })
        self.assertEqual(sorted(os.listdir(folder)), ["wt"])
        self.assertEqual(self.head(folder / "wt"), expected)
        self.assertEqual(real_git(folder / "wt", "symbolic-ref", "--short", "HEAD"), BASE)
        key = self.key()
        self.assertEqual(
            (key["branch"], key["base"], key["start_sha"], key["worktree"], key["step"]),
            (BASE, "develop", expected, str(folder / "wt"), "started"),
        )
        self.assertEqual((key["reused"], key["mr"]), (None, None))
        self.assertEqual(key["dev_fingerprint"]["head"], self.head(self.repo))
        self.assertEqual(key["dev_fingerprint"]["branch"], "develop")
        self.assertEqual(self.tracking_config(), "")

    def test_start_uses_a_single_worktree_per_repo(self):
        self.write_run(flight(), sections={"securite": (3,), "qualite": (5, 12)})
        self.open_pass()
        first = self.start_ok()
        worktree = self.run_dir / "fix" / "front" / "wt"
        self.assertEqual((first["branch"], first["worktree"]), (BASE, str(worktree)))
        self.assertEqual(self.worktree_paths(), [self.repo.resolve(), worktree.resolve()])
        self.section_start("securite")
        fixed = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        state = self.state()
        state["keys"][KEY]["sections"][0]["state"] = "settled"
        fix_state.save(self.run_dir, state)
        self.section_start("qualite")
        self.assertEqual(self.start_ok(), first)
        self.assertEqual(self.head(worktree), fixed)
        self.assertEqual(self.worktree_paths(), [self.repo.resolve(), worktree.resolve()])
        self.assertEqual(sorted(os.listdir(self.run_dir / "fix")), ["candidates.json",
                                                                    "doctor.json", "front",
                                                                    "state.json"])
        self.assertEqual(sorted(os.listdir(self.run_dir / "fix" / "front")), ["wt"])
        key = self.key()
        self.assertNotIn("slug", key)
        self.assertNotIn("category", key)
        self.assertEqual([(item["alias"], item["state"]) for item in key["sections"]],
                         [("securite", "settled"), ("qualite", "active")])

    def test_start_never_takes_a_v2_branch_as_the_pass_branch(self):
        with_mr, without_mr = PREFIX + "securite", PREFIX + "qualite"
        self.push_branch(with_mr, "fix(CQ-107): add headers")
        real_git(self.repo, "checkout", "-q", "-b", without_mr)
        local = commit(self.repo, "q.txt", "fix(CQ-3): x")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(open=[mr(12, with_mr, [107])],
                              branches=[{"branch": without_mr, "where": "local", "refs": [3]}]))
        self.open_pass()
        result = self.start_ok()
        base = self.head(self.repo, "refs/remotes/origin/develop")
        self.assertEqual(
            (result["branch"], result["reused"], result["start_sha"], result["done_refs"]),
            (BASE, None, base, []),
        )
        key = self.key()
        self.assertIsNone(key["mr"])
        self.assertEqual(key["rows"]["3"]["outcome"], "pending")
        self.assertEqual(self.head(self.repo, f"refs/heads/{without_mr}"), local)
        self.assertEqual(real_git(result["worktree"], "symbolic-ref", "--short", "HEAD"), BASE)

    def test_start_reuses_the_branch_of_an_open_mr(self):
        remote = self.push_branch(BASE, "fix(CQ-107): add headers")
        self.write_run(flight(open=[mr(12, BASE, [107])]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(
            (result["branch"], result["reused"], result["start_sha"], result["done_refs"]),
            (BASE, "mr", remote, [107]),
        )
        self.assertEqual(self.head(result["worktree"]), remote)
        key = self.key()
        self.assertEqual(key["mr"], {"iid": 12, "url": f"https://{HOST}/acme/shop/-/merge_requests/12"})
        self.assertEqual(key["reused"], "mr")
        self.assertEqual(self.tracking_config(), "")

    def test_start_reuses_an_open_mr_on_a_suffixed_branch(self):
        for suffix in ("-3", "-2"):
            self.push_branch(BASE + suffix, f"fix(CQ-10{suffix[1]}): x")
        remote = self.head(self.other, BASE + "-2")
        real_git(self.repo, "checkout", "-q", "-b", BASE)
        commit(self.repo, "local.txt", "fix(CQ-9): local")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(
            open=[mr(13, BASE + "-3", [103]), mr(12, BASE + "-2", [102]),
                  mr(11, PREFIX + "securite", [101])],
            merged=[mr(9, BASE + "-4", [1])],
            branches=[{"branch": BASE, "where": "local", "refs": [9]}],
        ))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (BASE + "-2", "mr"))
        self.assertEqual((result["start_sha"], self.head(result["worktree"])), (remote, remote))
        self.assertEqual(self.key()["mr"]["iid"], 12)

    def test_start_reuses_an_unmerged_branch_without_mr_and_counts_its_refs(self):
        real_git(self.repo, "checkout", "-q", "-b", BASE)
        local = commit(self.repo, "s.txt", "fix(CQ-3): x")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": BASE, "where": "local", "refs": [3]}]))
        self.open_pass()
        result = self.start_ok()
        base = self.head(self.repo, "refs/remotes/origin/develop")
        self.assertEqual(
            (result["branch"], result["reused"], result["start_sha"], result["done_refs"]),
            (BASE, "branch", base, [3]),
        )
        key = self.key()
        self.assertEqual(key["taken_head"], local)
        self.assertEqual(self.head(result["worktree"]), local)
        self.assertEqual(key["rows"]["3"]["outcome"], "done_before")
        self.assertEqual(key["rows"]["5"]["outcome"], "pending")
        self.assertIsNone(key["mr"])
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.open_pass(push="local")
        result = self.start_ok()
        self.assertEqual((result["start_sha"], result["done_refs"]), (local, [3]))

    def test_start_fast_forwards_a_stale_local_branch(self):
        first = self.push_branch(BASE, "fix(CQ-107): one")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", BASE, f"origin/{BASE}")
        remote = self.push_branch(BASE, "fix(CQ-108): two", start=f"origin/{BASE}")
        self.assertNotEqual(first, remote)
        self.write_run(flight(open=[mr(12, BASE, [107, 108])]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(result["start_sha"], remote)
        self.assertEqual(self.head(self.repo, f"refs/heads/{BASE}"), remote)
        self.assertEqual(self.head(result["worktree"]), remote)

    def test_first_start_refuses_local_commits_absent_from_origin(self):
        self.push_branch(BASE, "fix(CQ-20): remote")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", BASE, f"origin/{BASE}")
        real_git(self.repo, "checkout", "-q", BASE)
        local = commit(self.repo, "local.txt", "fix(CQ-21): local only")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(open=[mr(14, BASE, [20])]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(local[:7], err)
        self.assertIn("fix(CQ-21): local only", err)
        self.assertIn("Poussez-les", err)
        self.assertEqual(self.head(self.repo, f"refs/heads/{BASE}"), local)
        self.assertFalse((self.run_dir / "fix" / "front" / "wt").exists())
        self.assertEqual(self.key()["step"], "selected")

    def finish(self, key=KEY):
        """What finish does: remove the worktree, step done."""
        state = self.state()
        real_git(self.repo, "worktree", "remove", state["keys"][key]["worktree"])
        state["keys"][key]["step"] = "done"
        fix_state.save(self.run_dir, state)

    def test_start_again_after_finish_reuses_the_same_branch_and_mr(self):
        self.push_branch(BASE, "fix(CQ-107): x")
        self.write_run(flight(open=[mr(12, BASE, [107])]))
        self.open_pass()
        first = self.start_ok()
        fixed = self.fixer_commit(first["worktree"], "f.txt", "fix(CQ-3): x")
        real_git(first["worktree"], "push", "-q", "origin", f"HEAD:refs/heads/{BASE}")
        self.assertEqual(self.start_ok(), first)
        self.assertEqual(self.head(first["worktree"]), fixed)
        self.finish()
        reviewer = self.push_branch(BASE, "Apply 1 suggestion(s) to 1 file(s)",
                                    start=f"origin/{BASE}")
        again = self.start_ok()
        self.assertEqual(again, first)
        self.assertEqual(self.head(again["worktree"]), reviewer)
        key = self.key()
        self.assertEqual((key["mr"]["iid"], key["reused"], key["step"]), (12, "mr", "started"))
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.write_run(flight())
        self.open_pass(push="local")
        local = self.start_ok()
        self.assertEqual(local["branch"], BASE + "-2")
        fixed = self.fixer_commit(local["worktree"], "q.txt", "fix(CQ-5): y")
        self.finish()
        again = self.start_ok()
        self.assertEqual(again, local)
        self.assertEqual(self.head(again["worktree"]), fixed)
        self.assertEqual(again["start_sha"], self.head(self.repo, "refs/remotes/origin/develop"))

    def test_start_names_a_remote_branch_that_diverged(self):
        """M1: a remote that moved while the branch has its own commits is a divergence, not
        local commits to push: the lead integrates the remote commits by hand."""
        self.push_branch(BASE, "fix(CQ-107): x")
        self.write_run(flight(open=[mr(12, BASE, [107])]))
        self.open_pass()
        first = self.start_ok()
        local = self.fixer_commit(first["worktree"], "f.txt", "fix(CQ-3): local")
        self.finish()
        self.push_branch(BASE, "Apply 1 suggestion(s) to 1 file(s)", start=f"origin/{BASE}")
        code, out, err = self.start()
        self.assertEqual(code, 2, err)
        self.assertIn(DIVERGED, err)
        self.assertIn("fix(CQ-3): local", err)
        self.assertNotIn("Poussez-les", err)
        self.assertIn(local[:7], err)
        self.assertEqual(self.head(self.repo, f"refs/heads/{BASE}"), local)

    def test_first_start_names_a_branch_that_diverged(self):
        self.push_branch(BASE, "fix(CQ-20): remote")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", BASE, f"origin/{BASE}")
        real_git(self.repo, "checkout", "-q", BASE)
        commit(self.repo, "local.txt", "fix(CQ-21): local only")
        real_git(self.repo, "checkout", "-q", "develop")
        self.push_branch(BASE, "fix(CQ-22): remote too", start=f"origin/{BASE}")
        self.write_run(flight(open=[mr(14, BASE, [20, 22])]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2, err)
        self.assertIn(DIVERGED, err)
        self.assertIn("fix(CQ-21): local only", err)
        self.assertNotIn("Poussez-les", err)

    def test_start_never_takes_over_a_branch_when_gitlab_is_unreadable(self):
        real_git(self.repo, "checkout", "-q", "-b", BASE)
        commit(self.repo, "s.txt", "fix(CQ-3): maybe in a closed MR")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": BASE, "where": "local", "refs": [3]}],
                              notes=[fix_gitlab.NO_API_NOTE]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(
            (result["branch"], result["reused"], result["done_refs"]), (BASE + "-2", None, []),
        )
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")

    def test_start_takes_the_first_free_suffix(self):
        self.push_branch(BASE + "-2", "fix(CQ-4): closed")
        merged = self.push_branch(BASE + "-3", "fix(CQ-6): merged")
        real_git(self.other, "merge", "-q", "--ff-only", merged)
        real_git(self.other, "push", "-q", "origin", "develop")
        self.push_branch(PREFIX + "securite", "fix(CQ-7): v2")
        self.write_run(flight(merged=[mr(9, BASE, [1])], closed=[mr(11, BASE + "-2", [4])]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (BASE + "-4", None))

    def test_dry_run_branches_are_never_reused(self):
        real_git(self.repo, "checkout", "-q", "-b", DRY)
        commit(self.repo, "d.txt", "fix(CQ-3): dry")
        real_git(self.repo, "checkout", "-q", "develop")
        base = self.head(self.repo, "refs/remotes/origin/develop")
        self.open_pass(push="dry_run")
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (DRY + "-2", None))
        self.assertEqual((result["start_sha"], result["done_refs"]), (base, []))
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.open_pass(push="mr")
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (BASE, None))

    def test_start_refuses_a_branch_checked_out_by_the_dev(self):
        real_git(self.repo, "checkout", "-q", "-b", BASE)
        commit(self.repo, "s.txt", "fix(CQ-3): x")
        self.write_run(flight(branches=[{"branch": BASE, "where": "local", "refs": [3]}]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(
            f"La branche {BASE} est extraite dans votre dossier : changez de branche, puis relancez.",
            err,
        )
        key = self.key()
        self.assertEqual((key["step"], key["branch"]), ("selected", None))
        self.assertFalse((self.run_dir / "fix" / "front" / "wt").exists())

    def test_start_refuses_a_worktree_moved_to_another_branch(self):
        self.open_pass()
        first = self.start_ok()
        real_git(first["worktree"], "checkout", "-q", "-b", "elsewhere")
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(f"n'est plus sur la branche {first['branch']}", err)

    def test_start_names_a_vanished_worktree_holding_the_branch(self):
        real_git(self.repo, "branch", "-q", BASE)
        gone = self.root / "gone"
        real_git(self.repo, "worktree", "add", "-q", str(gone), BASE)
        shutil.rmtree(gone)
        self.write_run(flight(branches=[{"branch": BASE, "where": "local", "refs": []}]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(f"La branche {BASE} est encore liée au worktree disparu", err)
        self.assertIn("git worktree prune", err)

    def test_start_of_an_unknown_key_exits_2(self):
        self.open_pass()
        code, out, err = self.start("back")
        self.assertEqual(code, 2)
        self.assertIn("Clé inconnue : back", err)

    def test_start_without_an_open_pass_exits_2(self):
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py open", err)


class SectionTest(GitCase):
    QUESTION = {"text": "Quel contact ?", "options": ["Équipe", "Client"], "default": "Client"}

    def setUp(self):
        super().setUp()
        self.write_run(flight(), sections={"securite": (3,), "qualite": (5,)})

    def pass_start(self, number, alias="securite"):
        code, out, err = self.run_main(["pass-start", self.run_dir, KEY, "--pass", str(number),
                                        "--section", alias])
        return code, json.loads(out) if out else None, err

    def validate(self, kind, number, rows, **extra):
        document = {"key": KEY, "pass": number, "rows": rows, **extra}
        name = f"{kind}-{number}.json"
        (self.run_dir / "fix" / "front" / name).write_text(json.dumps(document), encoding="utf-8")
        code, out, err = self.run_main(["validate", self.run_dir, KEY, kind, "--pass", str(number)])
        return code, json.loads(out) if out else None, err

    def fixed(self, *commits):
        return {"ref": 3, "outcome": "fixed", "change": "Ajoute a.", "uat": "Lire a.",
                "commits": list(commits)}

    def test_sections_run_one_after_the_other(self):
        self.open_pass()
        worktree = Path(self.start_ok()["worktree"])
        code, result, err = self.section("start", "qualite")
        self.assertEqual(code, 2, err)
        self.assertIn("La section securite passe avant la section qualite", err)
        self.assertEqual(self.section_start("securite"), {
            "section": "securite", "category": "SECURITE", "refs": [3], "state": "active"})
        self.assertEqual(self.section_start("securite")["state"], "active")
        code, result, err = self.section("start", "qualite")
        self.assertEqual(code, 2, err)
        self.assertIn("La section securite est en cours", err)
        code, result, err = self.section("end", "qualite")
        self.assertEqual(code, 2, err)
        self.assertIn("« pending »", err)
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 2, err)
        self.assertIn("CQ-3 en attente", err)
        self.assertEqual(self.pass_start(1)[0], 0)
        first = self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        self.assertEqual(self.validate("report", 1, [self.fixed(first)])[0], 0)
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 2, err)
        self.assertIn("CQ-3 sans revue de la passe 1", err)
        blocking = [{"ref": 3, "verdict": "blocking", "notes": "Échappement manquant."}]
        self.assertEqual(self.validate("review", 1, blocking, attention=[])[0], 0)
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 2, err)
        self.assertIn("CQ-3 bloquante à la revue de la passe 1", err)
        self.assertEqual(self.pass_start(2)[0], 0)
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 2, err)
        self.assertIn("La passe 2 est commencée sans rapport validé", err)
        second = self.fixer_commit(worktree, "b3.txt", "fix(CQ-3): escape a")
        self.assertEqual(self.validate("report", 2, [self.fixed(first, second)])[0], 0)
        ok = [{"ref": 3, "verdict": "ok", "notes": None}]
        self.assertEqual(self.validate("review", 2, ok, attention=[])[0], 0)
        self.assertEqual(self.key()["sections"][0]["state"], "active")
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"section": "securite", "state": "settled", "next": "qualite"})
        code, result, err = self.section("start", "securite")
        self.assertEqual(code, 2, err)
        self.assertIn("« settled »", err)
        self.assertEqual(self.pass_start(3)[0], 2)
        self.assertEqual(self.section_start("qualite")["refs"], [5])
        self.assertEqual([(item["alias"], item["state"]) for item in self.key()["sections"]],
                         [("securite", "settled"), ("qualite", "active")])
        self.assertEqual(self.pass_start(3, "qualite")[0], 0)
        skipped = [{"ref": 5, "outcome": "skipped", "reason": "Hors du repo."}]
        self.assertEqual(self.validate("report", 3, skipped)[0], 0)
        code, result, err = self.section("end", "qualite")
        self.assertEqual(code, 0, err)
        self.assertEqual(result["next"], None)
        self.assertEqual(self.key()["step"], "reviewed")

    def test_pass_start_belongs_to_the_active_section(self):
        self.open_pass()
        self.start_ok()
        code, result, err = self.pass_start(1)
        self.assertEqual(code, 2, err)
        self.assertIn("section-start", err)
        self.section_start("securite")
        code, result, err = self.pass_start(1, "qualite")
        self.assertEqual(code, 2, err)
        self.assertIn("« pending »", err)
        code, result, err = self.pass_start(1, "seo")
        self.assertEqual(code, 2, err)
        self.assertIn("Section inconnue : seo", err)
        code, out, err = self.run_main(["pass-start", self.run_dir, KEY, "--pass", "1"])
        self.assertEqual(code, 2, err)
        self.assertEqual(self.key()["bases"], {})
        self.assertEqual(self.pass_start(1)[0], 0)
        self.assertEqual(self.pass_start(1)[0], 0)
        self.assertEqual(self.key()["pass_sections"], {"1": "securite"})
        both = [{"ref": 3, "outcome": "skipped", "reason": "Hors du repo."},
                {"ref": 5, "outcome": "skipped", "reason": "Hors du repo."}]
        code, result, err = self.validate("report", 1, both)
        self.assertEqual(code, 6, err)
        self.assertEqual(result["errors"], ["ligne CQ-5 : Réf d'une autre section (qualite)"])
        self.assertEqual(self.key()["rows"]["5"]["outcome"], "pending")
        code, result, err = self.validate("report", 1, both[:1])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["rows"]["3"]["section"], "securite")

    def test_section_end_sets_questions_aside_only_in_autonomous_mode(self):
        for mode, expected in (("present", 2), ("autonomous", 0)):
            with self.subTest(mode=mode):
                self.write_run(flight(), sections={"securite": (3,)})
                self.open_pass(mode=mode)
                self.start_ok()
                self.section_start()
                self.assertEqual(self.pass_start(1)[0], 0)
                question = [{"ref": 3, "outcome": "question", "question": self.QUESTION}]
                self.assertEqual(self.validate("report", 1, question)[0], 0)
                code, result, err = self.section("end", "securite")
                self.assertEqual(code, expected, err)
                if expected:
                    self.assertIn("CQ-3 question sans réponse", err)
                self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)

    def test_end_of_pass_answers_reopen_their_settled_section(self):
        """Autonomous mode: at the questions stage, the answer pass of a question set aside goes
        to its settled section, which stays settled; its fixed row waits for its own review."""
        self.write_run(flight(), sections={"securite": (3,)})
        self.open_pass(mode="autonomous")
        worktree = Path(self.start_ok()["worktree"])
        self.section_start()
        self.assertEqual(self.pass_start(1)[0], 0)
        question = [{"ref": 3, "outcome": "question", "question": self.QUESTION}]
        self.assertEqual(self.validate("report", 1, question)[0], 0)
        code, result, err = self.section("end", "securite")
        self.assertEqual(code, 0, err)
        code, result, err = self.pass_start(2)
        self.assertEqual(code, 2, err)
        self.assertIn("« settled »", err)
        self.assertEqual(self.run_main(["stage", self.run_dir, "questions"])[0], 0)
        code, result, err = self.pass_start(2)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["pass_sections"], {"1": "securite", "2": "securite"})
        self.assertEqual(self.key()["sections"][0]["state"], "settled")
        code, result, err = self.section("start", "securite")
        self.assertEqual(code, 2, err)
        answer = self.fixer_commit(worktree, "contact.txt", "fix(CQ-3): add the contact")
        self.assertEqual(self.validate("report", 2, [self.fixed(answer)])[0], 0)
        key = self.key()
        self.assertEqual((key["rows"]["3"]["outcome"], key["rows"]["3"]["pass"]), ("fixed", 2))
        self.assertEqual((key["step"], key["sections"][0]["state"]), ("fixed", "settled"))
        code, result, err = self.pass_start(3)
        self.assertEqual(code, 2, err)
        self.assertIn("« settled »", err)
        ok = [{"ref": 3, "verdict": "ok", "notes": None}]
        self.assertEqual(self.validate("review", 2, ok, attention=[])[0], 0)
        self.assertEqual(self.key()["step"], "reviewed")


    def test_an_answer_pass_without_a_valid_report_is_closed_by_an_empty_report(self):
        """SKILL.md section 12, step 6: an answer pass in a settled section whose report stays
        invalid drops its rows, then an empty report closes the pass, else the key stays with
        a pass in progress and nothing can publish or keep it."""
        self.write_run(flight(), sections={"securite": (3,)})
        self.open_pass(mode="autonomous")
        worktree = Path(self.start_ok()["worktree"])
        self.section_start()
        self.assertEqual(self.pass_start(1)[0], 0)
        question = [{"ref": 3, "outcome": "question", "question": self.QUESTION}]
        self.assertEqual(self.validate("report", 1, question)[0], 0)
        self.assertEqual(self.section("end", "securite")[0], 0)
        self.assertEqual(self.run_main(["stage", self.run_dir, "questions"])[0], 0)
        self.assertEqual(self.pass_start(2)[0], 0)
        self.fixer_commit(worktree, "contact.txt", "fix(securite CQ-3): add the contact")
        code, out, err = self.run_main(["drop", self.run_dir, KEY, "--refs", "CQ-3", "--reason",
                                        "rapport invalide après une nouvelle tentative"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_main(["reorder", self.run_dir, KEY])
        self.assertEqual(code, 2, err)
        self.assertIn("La passe 2 est commencée sans rapport validé", err)
        code, result, err = self.validate("report", 2, [])
        self.assertEqual(code, 0, err)
        key = self.key()
        self.assertEqual((key["pass"], key["step"]), (2, "reviewed"))
        self.assertEqual(key["rows"]["3"]["outcome"], "dropped")
        self.assertEqual(self.run_main(["reorder", self.run_dir, KEY])[0], 0)

DOC = "docs/WORDPRESS.md"
ROBOTS = "robots.txt"
FAILED = "Rapport invalide après une nouvelle tentative."


def notes(*items):
    """The documentation file of the replay tests, with one line per note."""
    return "# WordPress\n\n## Notes\n" + "".join(f"- {item}\n" for item in items) + "\nEnd.\n"


class ReplayCase(GitCase):
    """A started key with three sections (securite CQ-3, qualite CQ-5, seo CQ-12); develop
    already has the documentation file and robots.txt, and ATTRIBUTES as .gitattributes."""

    ATTRIBUTES = None

    def setUp(self):
        super().setUp()
        (self.repo / "docs").mkdir()
        if self.ATTRIBUTES:
            (self.repo / ".gitattributes").write_text(self.ATTRIBUTES, encoding="utf-8")
            real_git(self.repo, "add", ".gitattributes")
        for path in (DOC, ROBOTS):
            (self.repo / path).write_text(notes(), encoding="utf-8")
            real_git(self.repo, "add", path)
        real_git(self.repo, "commit", "-q", "-m", "docs: start the notes")
        real_git(self.repo, "push", "-q", "origin", "develop")
        self.write_run(flight(), sections={"securite": (3,), "qualite": (5,), "seo": (12,)})
        self.open_pass()
        started = self.start_ok()
        self.worktree = Path(started["worktree"])
        self.start_sha = started["start_sha"]
        self.branch = started["branch"]

    def commit_files(self, message, files):
        for path, content in files.items():
            target = self.worktree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for args in (("add", "--", *files), ("commit", "-q", "-m", message)):
            result = fix_names.git(self.worktree, subprocess.run, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        return self.head(self.worktree)

    def record(self, **values):
        """Set fields of the key ({"bases": {...}}) and rows fixed with their commits (r3=[sha])."""
        state = self.state()
        key = state["keys"][KEY]
        for name, value in values.items():
            if name[1:].isdigit():
                key["rows"][name[1:]].update(outcome="fixed", commits=value, **{"pass": 1})
            else:
                key[name] = value
        fix_state.save(self.run_dir, state)

    def subjects(self):
        return real_git(self.worktree, "log", "--reverse", "--format=%s",
                        f"{self.start_sha}..HEAD").splitlines()

    def assert_clean_on_branch(self):
        self.assertEqual(real_git(self.worktree, "status", "--porcelain", "-uall"), "")
        self.assertEqual(real_git(self.worktree, "symbolic-ref", "--short", "HEAD"), self.branch)
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), self.head(self.worktree))

    def push(self):
        real_git(self.worktree, "push", "-q", "origin", f"HEAD:refs/heads/{self.branch}")
        return self.head(self.worktree)

    def main_json(self, *argv):
        code, out, err = self.run_main([argv[0], self.run_dir, KEY, *argv[1:]])
        return code, json.loads(out) if out else None, err

    def three_notes(self, path):
        one = self.commit_files("docs(securite CQ-3): add the first note", {path: notes("one")})
        two = self.commit_files("docs(qualite CQ-5): add the second note",
                                {path: notes("one", "two")})
        three = self.commit_files("docs(seo CQ-12): add the third note",
                                  {path: notes("one", "two", "three")})
        return one, two, three

    def in_branch(self, sha):
        return subprocess.run(["git", "-C", str(self.worktree), "merge-base", "--is-ancestor",
                               sha, "HEAD"]).returncode == 0


class ReplayTest(ReplayCase):
    def test_replay_keeps_only_the_replayed_doc_additions(self):
        one, two, three = self.three_notes(DOC)
        self.record(r3=[one], r5=[two], r12=[three])
        code, result, err = self.main_json("drop", "--refs", "CQ-3", "--reason",
                                           "Revue bloquante : note fausse.")
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"dropped": [3], "also_dropped": [], "retry": []})
        self.assertEqual((self.worktree / DOC).read_text(encoding="utf-8"), notes("two", "three"))
        self.assertEqual(self.subjects(), ["docs(qualite CQ-5): add the second note",
                                           "docs(seo CQ-12): add the third note"])
        self.assert_clean_on_branch()
        rows = self.key()["rows"]
        self.assertEqual((rows["3"]["outcome"], rows["5"]["outcome"], rows["12"]["outcome"]),
                         ("dropped", "fixed", "fixed"))
        self.assertEqual(rows["12"]["commits"], [self.head(self.worktree)])
        self.assertTrue(self.in_branch(rows["5"]["commits"][0]))

    def test_replay_keeps_a_real_conflict_outside_docs(self):
        one, two, three = self.three_notes(ROBOTS)
        result = fix_git.replay(self.worktree, subprocess.run, self.start_sha,
                                [(two, None), (three, None)])
        self.assertEqual((result["mapping"], result["conflicts"]), ({}, [two]))
        self.assertEqual(real_git(self.worktree, "status", "--porcelain", "-uall"), "")
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), three)
        real_git(self.worktree, "checkout", "-q", self.branch)
        self.assertEqual((self.worktree / ROBOTS).read_text(encoding="utf-8"),
                         notes("one", "two", "three"))

    def test_replay_of_docs_resolves_only_a_sure_place(self):
        first = self.commit_files("docs(securite CQ-3): add a", {DOC: notes("a")})
        second = self.commit_files("docs(qualite CQ-5): add b", {DOC: notes("a", "b")})
        result = fix_git.replay(self.worktree, subprocess.run, self.start_sha,
                                [(second, "docs(qualite CQ-5): add b alone\n"), (first, None)])
        self.assertEqual(result["conflicts"], [first])
        self.assertEqual(list(result["mapping"]), [second])
        self.assertEqual(result["resolved"], [second])
        replayed = result["mapping"][second]
        self.assertEqual(real_git(self.worktree, "show", f"{replayed}:{DOC}"), notes("b").strip())
        self.assertEqual(real_git(self.worktree, "log", "-1", "--format=%B", replayed),
                         "docs(qualite CQ-5): add b alone")
        self.assertEqual(real_git(self.worktree, "log", "-1", "--format=%an <%ae>", replayed),
                         "Camille <camille@example.test>")
        self.assertEqual(real_git(self.worktree, "status", "--porcelain", "-uall"), "")
        self.assertEqual(self.head(self.repo, f"refs/heads/{self.branch}"), second)


class UnionDriverTest(ReplayCase):
    """The repo asks git to merge its docs and robots.txt by union: a replay never lets that
    keep the lines of a dropped commit."""

    ATTRIBUTES = "*.md merge=union\nrobots.txt merge=union\n"

    def plain_pick_keeps_the_dropped_line(self, path, commit):
        """What a plain cherry-pick of commit on start_sha does with the repo's attributes."""
        real_git(self.worktree, "checkout", "-q", "--detach", self.start_sha)
        picked = subprocess.run(["git", "-C", str(self.worktree), "cherry-pick", commit],
                                capture_output=True, text=True)
        content = (self.worktree / path).read_text(encoding="utf-8")
        subprocess.run(["git", "-C", str(self.worktree), "cherry-pick", "--abort"],
                       capture_output=True)
        real_git(self.worktree, "checkout", "-q", "--force", self.branch)
        return picked.returncode == 0 and "- one" in content

    def test_docs_get_only_their_own_lines_back(self):
        one, two, three = self.three_notes(DOC)
        self.assertTrue(self.plain_pick_keeps_the_dropped_line(DOC, two))
        result = fix_git.replay(self.worktree, subprocess.run, self.start_sha,
                                [(two, None), (three, None)])
        self.assertEqual((result["conflicts"], result["resolved"]), ([], [two]))
        self.assertEqual(real_git(self.worktree, "show", f"{result['mapping'][three]}:{DOC}"),
                         notes("two", "three").strip())
        self.assertEqual(real_git(self.worktree, "status", "--porcelain", "-uall"), "")
        self.record(r3=[one], r5=[two], r12=[three])
        real_git(self.worktree, "checkout", "-q", self.branch)
        code, result, err = self.main_json("drop", "--refs", "CQ-3", "--reason", "Note fausse.")
        self.assertEqual((code, result["also_dropped"]), (0, []), err)
        self.assertEqual((self.worktree / DOC).read_text(encoding="utf-8"), notes("two", "three"))
        self.assert_clean_on_branch()

    def test_a_served_file_gives_a_real_conflict(self):
        one, two, three = self.three_notes(ROBOTS)
        self.assertTrue(self.plain_pick_keeps_the_dropped_line(ROBOTS, two))
        result = fix_git.replay(self.worktree, subprocess.run, self.start_sha,
                                [(two, None), (three, None)])
        self.assertEqual((result["mapping"], result["conflicts"]), ({}, [two]))
        self.assertEqual(real_git(self.worktree, "status", "--porcelain", "-uall"), "")
        self.record(r3=[one], r5=[two], r12=[three])
        real_git(self.worktree, "checkout", "-q", self.branch)
        code, result, err = self.main_json("drop", "--refs", "CQ-3", "--reason", "Ligne fausse.")
        self.assertEqual((code, result["also_dropped"]), (0, [5, 12]), err)
        self.assertEqual((self.worktree / ROBOTS).read_text(encoding="utf-8"), notes())
        self.assertEqual(self.head(self.worktree), self.start_sha)
        self.assert_clean_on_branch()


class CustomDriverTest(UnionDriverTest):
    """Same with a merge driver of the repo's config, and a default driver."""

    ATTRIBUTES = "*.md merge=keepall\nrobots.txt merge=keepall\n"

    def setUp(self):
        super().setUp()
        real_git(self.repo, "config", "merge.keepall.driver", "git merge-file --union %A %O %B")
        real_git(self.repo, "config", "merge.default", "keepall")


class ReorderTest(ReplayCase):
    def reorder(self):
        return self.main_json("reorder")

    def test_reorder_groups_commits_by_section(self):
        a = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        b = self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b"})
        c = self.commit_files("fix(seo CQ-12): add c", {"c12.txt": "c"})
        d = self.commit_files("fix(securite CQ-3): answer d", {"d3.txt": "d"})
        e = self.commit_files("fix(qualite CQ-5): answer e", {"e5.txt": "e"})
        self.record(r3=[a, d], r5=[b, e], r12=[c])
        tree = real_git(self.worktree, "rev-parse", "HEAD^{tree}")
        code, result, err = self.reorder()
        self.assertEqual(code, 0, err)
        rewritten = result.pop("rewritten")
        self.assertEqual(result, {"reordered": True, "conflicts": []})
        self.assertEqual(self.subjects(), [
            "fix(securite CQ-3): add a", "fix(securite CQ-3): answer d",
            "fix(qualite CQ-5): add b", "fix(qualite CQ-5): answer e", "fix(seo CQ-12): add c",
        ])
        self.assertEqual(real_git(self.worktree, "rev-parse", "HEAD^{tree}"), tree)
        self.assert_clean_on_branch()
        log = real_git(self.worktree, "log", "--reverse", "--format=%H", f"{self.start_sha}..HEAD")
        new = log.splitlines()
        self.assertEqual(new[0], a)
        self.assertEqual(rewritten, {d: new[1], b: new[2], e: new[3], c: new[4]})
        rows = self.key()["rows"]
        self.assertEqual(rows["3"]["commits"], [a, new[1]])
        self.assertEqual(rows["5"]["commits"], [new[2], new[3]])
        self.assertEqual(rows["12"]["commits"], [new[4]])
        self.assertNotIn("reorder_conflicts", self.key())
        code, result, err = self.reorder()
        self.assertEqual((code, result), (0, {"reordered": False, "conflicts": [],
                                              "rewritten": {}}), err)
        self.assertEqual(self.head(self.worktree), new[4])

    def test_reorder_never_moves_pushed_commits(self):
        b = self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b"})
        a = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        remote = self.push()
        c = self.commit_files("fix(seo CQ-12): add c", {"c12.txt": "c"})
        d = self.commit_files("fix(securite CQ-3): answer d", {"d3.txt": "d"})
        self.record(r3=[a, d], r5=[b], r12=[c])
        code, result, err = self.reorder()
        self.assertEqual(code, 0, err)
        self.assertEqual((result["reordered"], result["conflicts"], sorted(result["rewritten"])),
                         (True, [], sorted([c, d])))
        self.assertEqual(self.subjects(), [
            "fix(qualite CQ-5): add b", "fix(securite CQ-3): add a",
            "fix(securite CQ-3): answer d", "fix(seo CQ-12): add c",
        ])
        new = real_git(self.worktree, "log", "--reverse", "--format=%H",
                       f"{self.start_sha}..HEAD").splitlines()
        self.assertEqual(new[:2], [b, a])
        self.assertEqual(self.head(self.repo, f"refs/remotes/origin/{self.branch}"), remote)
        self.assertEqual(real_git(self.other, "ls-remote", "origin", f"refs/heads/{self.branch}")
                         .split()[0], remote)
        self.assertEqual(self.key()["rows"]["3"]["commits"], [a, new[2]])
        self.assert_clean_on_branch()
        self.push()
        code, result, err = self.reorder()
        self.assertEqual((code, result), (0, {"reordered": False, "conflicts": [],
                                              "rewritten": {}}), err)

    def test_reorder_conflict_changes_nothing(self):
        a = self.commit_files("fix(securite CQ-3): add x", {"x.txt": "one\n"})
        b = self.commit_files("fix(qualite CQ-5): change x", {"x.txt": "two\n"})
        d = self.commit_files("fix(securite CQ-3): change x again", {"x.txt": "three\n"})
        self.record(r3=[a, d], r5=[b], bases={"1": self.start_sha, "2": a}, **{"pass": 2})
        before = self.key()
        code, result, err = self.reorder()
        self.assertEqual(code, 0, err)
        self.assertEqual(result, {"reordered": False, "conflicts": [d], "rewritten": {}})
        self.assertEqual(self.head(self.worktree), d)
        self.assert_clean_on_branch()
        after = self.key()
        self.assertEqual(after.pop("reorder_conflicts"), ["fix(securite CQ-3): change x again"])
        self.assertEqual(after, before)
        (self.worktree / "x.txt").write_text("draft", encoding="utf-8")
        code, result, err = self.reorder()
        self.assertEqual(code, 5, err)
        self.assertIn("non commitées", err)

    def test_replay_remaps_bases_and_reviews(self):
        a = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        b = self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b"})
        d = self.commit_files("fix(securite CQ-3): answer d", {"d3.txt": "d"})
        reviews = {"1": {"blocking": []}, "2": {"blocking": [5]}, "3": {"blocking": []}}
        self.record(r3=[a, d], r5=[b], bases={"1": self.start_sha, "2": a, "3": b},
                    reviews=reviews, **{"pass": 3})
        code, result, err = self.reorder()
        self.assertEqual((code, result["reordered"]), (0, True), err)
        new = real_git(self.worktree, "log", "--reverse", "--format=%H",
                       f"{self.start_sha}..HEAD").splitlines()
        key = self.key()
        self.assertEqual(key["bases"], {"1": self.start_sha, "2": a, "3": new[2]})
        self.assertEqual(key["reviews"], reviews)
        self.assertEqual(key["start_sha"], self.start_sha)
        for number, base in key["bases"].items():
            with self.subTest(base=number):
                self.assertTrue(self.in_branch(base))
        message = self.root / "message.txt"
        message.write_text("fix(qualite CQ-5): add the b file\n", encoding="utf-8")
        code, result, err = self.main_json("reword", "--commit", new[2], "--message-file",
                                           message)
        self.assertEqual(code, 0, err)
        key = self.key()
        self.assertEqual(key["bases"]["3"], self.head(self.worktree))
        self.assertEqual(key["rows"]["5"]["commits"], [self.head(self.worktree)])
        self.assertEqual(key["reviews"], reviews)


class RewordTest(ReplayCase):
    def reword(self, sha, text):
        message = self.root / "message.txt"
        message.write_text(text, encoding="utf-8")
        return self.main_json("reword", "--commit", sha, "--message-file", message)

    def test_reword_rewrites_an_unpushed_message_only(self):
        a = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        self.push()
        bad = self.commit_files("fix(qualite CQ-5): add b\n\nCo-Authored-By: Bot <bot@example.test>",
                                {"b5.txt": "b"})
        c = self.commit_files("fix(seo CQ-12): add c", {"c12.txt": "c"})
        self.record(r3=[a], r5=[bad], r12=[c], bases={"1": self.start_sha, "2": bad},
                    **{"pass": 2})
        tree = real_git(self.worktree, "rev-parse", "HEAD^{tree}")
        code, result, err = self.main_json("guard")
        self.assertEqual(code, 5, err)
        self.assertEqual([(item["sha"], item["kind"]) for item in result["problems"]],
                         [(bad, "form")])
        code, result, err = self.reword(bad, "fix(qualite CQ-5): add b\n")
        self.assertEqual(code, 0, err)
        new = real_git(self.worktree, "log", "--reverse", "--format=%H",
                       f"{self.start_sha}..HEAD").splitlines()
        self.assertEqual(new[0], a)
        self.assertNotIn(bad, new)
        self.assertEqual(result["reworded"], True)
        self.assertEqual(result["commit"], new[1])
        self.assertEqual(result["rewritten"], {bad: new[1], c: new[2]})
        self.assertEqual(real_git(self.worktree, "log", "-1", "--format=%B", new[1]),
                         "fix(qualite CQ-5): add b")
        self.assertEqual(self.subjects(), ["fix(securite CQ-3): add a", "fix(qualite CQ-5): add b",
                                           "fix(seo CQ-12): add c"])
        self.assertEqual(real_git(self.worktree, "rev-parse", "HEAD^{tree}"), tree)
        self.assert_clean_on_branch()
        key = self.key()
        self.assertEqual((key["rows"]["3"]["commits"], key["rows"]["5"]["commits"],
                          key["rows"]["12"]["commits"]), ([a], [new[1]], [new[2]]))
        self.assertEqual(key["bases"], {"1": self.start_sha, "2": new[1]})
        self.assertEqual(self.main_json("guard")[0], 0)
        head = self.head(self.worktree)
        code, result, err = self.reword(a, "fix(securite CQ-3): add the a file\n")
        self.assertEqual(code, 5, err)
        self.assertIn("origin", err)
        self.assertEqual(self.head(self.worktree), head)
        code, result, err = self.reword(new[1], "fix(seo CQ-12): add b\n")
        self.assertEqual((code, result), (5, None), err)
        self.assertIn("CQ-12", err)
        self.assertIn("CQ-5", err)
        self.assertEqual(self.head(self.worktree), head)
        for text in ("fix(securite CQ-5): add b\n",
                     "fix(qualite CQ-5): add b\n\n$password = 'hunter2hunter2';\n"):
            with self.subTest(message=text):
                code, result, err = self.reword(new[1], text)
                self.assertEqual(code, 5, err)
                self.assertFalse(result["ok"])
                self.assertTrue(result["problems"])
                self.assertNotIn("hunter2", json.dumps(result) + err)
                self.assertEqual(self.head(self.worktree), head)
        code, result, err = self.reword(self.start_sha, "fix(securite CQ-3): add a\n")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.key(), key)
        self.assert_clean_on_branch()


    def test_reword_moves_a_stray_ref_to_a_row_only(self):
        """A subject without a row of the key (form problem) may take the ref of a row; a
        commit of a taken-over branch, at or before its head, is never reworded."""
        inherited = self.commit_files("fix(securite CQ-3): inherited", {"i3.txt": "i"})
        stray = self.commit_files("fix(qualite CQ-99): add b", {"b5.txt": "b"})
        self.record(r5=[], taken_head=inherited)
        code, result, err = self.reword(stray, "fix(qualite CQ-5): add b\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.subjects(), ["fix(securite CQ-3): inherited",
                                           "fix(qualite CQ-5): add b"])
        self.assertEqual(self.key()["taken_head"], inherited)
        code, result, err = self.reword(inherited, "fix(securite CQ-3): inherited fix\n")
        self.assertEqual(code, 5, err)
        self.assertIn("reprise", err)
        self.assertEqual(self.subjects()[0], "fix(securite CQ-3): inherited")

    def test_reword_and_reorder_wait_for_the_pass_in_progress(self):
        first = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        self.record(bases={"1": self.start_sha})
        head = self.head(self.worktree)
        for argv in (["reorder"], ["reword", "--commit", first, "--message-file",
                                   self.root / "message.txt"]):
            with self.subTest(command=argv[0]):
                (self.root / "message.txt").write_text("fix(securite CQ-3): add the a file\n",
                                                       encoding="utf-8")
                code, result, err = self.main_json(*argv)
                self.assertEqual(code, 2, err)
                self.assertIn("passe 1", err)
                self.assertEqual(self.head(self.worktree), head)


class FailedSectionTest(ReplayCase):
    def validate(self, kind, number, rows, **extra):
        document = {"key": KEY, "pass": number, "rows": rows, **extra}
        (self.run_dir / "fix" / "front" / f"{kind}-{number}.json").write_text(
            json.dumps(document), encoding="utf-8")
        code, out, err = self.run_main(["validate", self.run_dir, KEY, kind, "--pass", str(number)])
        self.assertEqual(code, 0, err)

    def pass_start(self, number, alias):
        code, out, err = self.run_main(["pass-start", self.run_dir, KEY, "--pass", str(number),
                                        "--section", alias])
        self.assertEqual(code, 0, err)

    def settle_securite(self):
        """Section securite: CQ-3 fixed in pass 1, reviewed ok, section settled."""
        self.section_start("securite")
        self.pass_start(1, "securite")
        first = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a\n"})
        self.validate("report", 1, [{"ref": 3, "outcome": "fixed", "change": "Ajoute a.",
                                     "uat": "Lire a.", "commits": [first]}])
        self.validate("review", 1, [{"ref": 3, "verdict": "ok", "notes": None}], attention=[])
        self.assertEqual(self.section("end", "securite")[0], 0)
        self.section_start("qualite")
        self.pass_start(2, "qualite")
        return first

    def give_up(self, alias="qualite", *extra):
        return self.main_json("section-end", "--section", alias, "--failed", "--reason", FAILED,
                              *extra)

    def test_failed_section_drops_its_commits_and_saves_leftovers(self):
        first = self.settle_securite()
        self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b\n"})
        self.commit_files("chore: unreported note", {"note.txt": "note\n"})
        old_head = self.head(self.worktree)
        (self.worktree / "a3.txt").write_text("a, edited\n", encoding="utf-8")
        (self.worktree / "new").mkdir()
        (self.worktree / "new" / "untracked.txt").write_text("draft\n", encoding="utf-8")
        binary = bytes(range(256)) * 4
        (self.worktree / "img.bin").write_bytes(binary)
        folder = self.run_dir / "fix" / "front"
        (folder / "report-2.json").write_text("{not json", encoding="utf-8")
        code, result, err = self.give_up()
        self.assertEqual(code, 0, err)
        patch = "fix/front/leftover-1.patch"
        self.assertEqual(result, {
            "section": "qualite", "state": "failed", "next": "seo", "dropped": [5],
            "also_dropped": [], "retry": [],
            "leftover": {"patch": patch, "base": old_head,
                         "untracked": ["img.bin", "new/untracked.txt"]},
        })
        self.assertEqual(self.head(self.worktree), first)
        self.assertEqual(self.subjects(), ["fix(securite CQ-3): add a"])
        self.assert_clean_on_branch()
        key = self.key()
        self.assertEqual((key["rows"]["5"]["outcome"], key["rows"]["5"]["reason"],
                          key["rows"]["5"]["commits"]), ("dropped", FAILED, []))
        self.assertEqual((key["rows"]["3"]["outcome"], key["rows"]["3"]["commits"]),
                         ("fixed", [first]))
        section = next(item for item in key["sections"] if item["alias"] == "qualite")
        self.assertEqual((section["state"], section["reason"]), ("failed", FAILED))
        self.assertEqual(key["bases"], {"1": self.start_sha})
        self.assertEqual(key["pass_sections"], {"1": "securite"})
        self.assertEqual(section["leftover"], result["leftover"])
        self.assertFalse((folder / "report-2.json").exists())
        self.assertEqual((folder / "report-2.failed.json").read_text(encoding="utf-8"), "{not json")
        self.assertTrue((folder / "report-1.json").is_file())
        self.assertTrue((folder / "review-1.json").is_file())
        self.assertEqual(self.section_start("seo")["state"], "active")
        text = (self.run_dir / patch).read_bytes()
        self.assertIn(b"GIT binary patch", text)
        real_git(self.worktree, "apply", "--check", str(self.run_dir / patch))
        real_git(self.worktree, "apply", str(self.run_dir / patch))
        self.assertEqual((self.worktree / "a3.txt").read_text(encoding="utf-8"), "a, edited\n")
        self.assertEqual((self.worktree / "img.bin").read_bytes(), binary)
        self.assertEqual((self.worktree / "new" / "untracked.txt").read_text(encoding="utf-8"),
                         "draft\n")
        real_git(self.worktree, "clean", "-fdq")
        real_git(self.worktree, "checkout", "-q", "--", ".")
        self.assertEqual(self.head(self.repo, "refs/heads/develop"),
                         self.head(self.repo, "refs/remotes/origin/develop"))

    def test_failed_section_takes_the_next_free_patch_and_needs_no_leftover(self):
        self.settle_securite()
        (self.run_dir / "fix" / "front" / "leftover-1.patch").write_text("old", encoding="utf-8")
        (self.worktree / "draft.txt").write_text("draft\n", encoding="utf-8")
        code, result, err = self.give_up()
        self.assertEqual(code, 0, err)
        self.assertEqual(result["leftover"]["patch"], "fix/front/leftover-2.patch")
        self.assertEqual((self.run_dir / "fix" / "front" / "leftover-1.patch").read_text(
            encoding="utf-8"), "old")
        self.section_start("seo")
        self.pass_start(2, "seo")
        code, result, err = self.give_up("seo")
        self.assertEqual(code, 0, err)
        self.assertIsNone(result["leftover"])
        self.assertEqual((result["next"], result["dropped"]), (None, [12]))

    def test_failed_section_refuses_pushed_commits_and_bad_calls(self):
        self.settle_securite()
        self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b\n"})
        head = self.push()
        (self.worktree / "draft.txt").write_text("draft\n", encoding="utf-8")
        before = self.key()
        code, result, err = self.give_up()
        self.assertEqual(code, 5, err)
        self.assertIn("origin", err)
        self.assertEqual(self.head(self.worktree), head)
        self.assertTrue((self.worktree / "draft.txt").exists())
        self.assertEqual(self.key(), before)
        self.assertFalse(list((self.run_dir / "fix" / "front").glob("leftover-*.patch")))
        for argv, needle in (
            (["section-end", "--section", "qualite", "--reason", FAILED], "--failed"),
            (["section-end", "--section", "qualite", "--failed"], "--reason"),
            (["section-end", "--section", "securite", "--failed", "--reason", FAILED], "settled"),
        ):
            with self.subTest(argv=argv):
                code, result, err = self.main_json(*argv)
                self.assertEqual(code, 2, err)
                self.assertIn(needle, err)
        self.assertEqual(self.key(), before)


    def test_a_failed_section_rerun_gives_its_recorded_leftover(self):
        self.settle_securite()
        self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b\n"})
        (self.worktree / "draft.txt").write_text("draft\n", encoding="utf-8")

        def stuck(command, **kwargs):
            if "update-ref" in command:
                return subprocess.CompletedProcess(command, 1, "", "error: simulated lock")
            return subprocess.run(command, **kwargs)

        code, out, err = self.run_main(["section-end", self.run_dir, KEY, "--section", "qualite",
                                        "--failed", "--reason", FAILED], run=stuck)
        self.assertEqual(code, 5, err)
        self.assert_clean_on_branch()
        key = self.key()
        section = next(item for item in key["sections"] if item["alias"] == "qualite")
        self.assertEqual(section["state"], "active")
        recorded = section["leftover"]
        self.assertEqual((recorded["patch"], recorded["untracked"]),
                         ("fix/front/leftover-1.patch", ["draft.txt"]))
        code, result, err = self.give_up()
        self.assertEqual(code, 0, err)
        self.assertEqual(result["leftover"], recorded)
        self.assertFalse((self.run_dir / "fix" / "front" / "leftover-2.patch").exists())

    def test_failed_section_drops_covered_rows_only_with_their_fix(self):
        first = self.settle_securite()
        state = self.state()
        key = state["keys"][KEY]
        key["sections"][1]["refs"] = [5, 7, 8]
        own = self.commit_files("fix(qualite CQ-7): add g", {"g7.txt": "g\n"})
        for ref, row in (("5", {"outcome": "skipped", "reason": "corrigé par le commit de CQ-7"}),
                         ("7", {"outcome": "fixed", "commits": [own]}),
                         ("8", {"outcome": "skipped", "reason": "corrigé par le commit de CQ-3"})):
            key["rows"][ref] = {"outcome": "pending", "reason": None, "commits": [], "pass": 2,
                                "attention": None, "section": "qualite", **row}
        fix_state.save(self.run_dir, state)
        code, result, err = self.give_up()
        self.assertEqual(code, 0, err)
        self.assertEqual((result["dropped"], result["also_dropped"]), ([5, 7], []))
        rows = self.key()["rows"]
        self.assertEqual([rows[ref]["outcome"] for ref in ("5", "7", "8")],
                         ["dropped", "dropped", "skipped"])
        self.assertEqual(rows["8"]["reason"], "corrigé par le commit de CQ-3")
        self.assertEqual(self.head(self.worktree), first)

    def test_failed_section_takes_along_rows_of_other_sections(self):
        """A commit of the failed section made before it started (here in securite) leaves too:
        the commits after it that no longer replay take their rows along, for good in a settled
        section, with a retry in a section still to come."""
        self.section_start("securite")
        prepare = self.commit_files("fix(qualite CQ-5): prepare shared", {"shared.txt": "a\n"})
        use = self.commit_files("fix(securite CQ-3): use shared", {"shared.txt": "a\nb\n"})
        extend = self.commit_files("fix(seo CQ-12): extend shared", {"shared.txt": "a\nb\nc\n"})
        state = self.state()
        key = state["keys"][KEY]
        key["rows"]["3"].update(outcome="fixed", commits=[use], **{"pass": 1})
        key["sections"][0]["state"] = "settled"
        key["sections"][1]["state"] = "active"
        fix_state.save(self.run_dir, state)
        code, result, err = self.give_up()
        self.assertEqual(code, 0, err)
        self.assertEqual((result["dropped"], result["also_dropped"], result["retry"]),
                         ([5], [3, 12], [12]))
        rows = self.key()["rows"]
        self.assertEqual((rows["3"]["outcome"], rows["12"]["outcome"], rows["12"]["retry"]),
                         ("dropped", "pending", True))
        self.assertIn("section qualite", rows["3"]["reason"])
        self.assertEqual(self.head(self.worktree), self.start_sha)
        self.assert_clean_on_branch()
        self.assertNotIn(prepare, real_git(self.worktree, "log", "--format=%H"))
        self.assertNotIn(extend, real_git(self.worktree, "log", "--format=%H"))


class InterruptTest(ReplayCase):
    def test_an_interrupt_during_a_replay_restores_the_worktree(self):
        first = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a"})
        head = self.commit_files("fix(qualite CQ-5): add b", {"b5.txt": "b"})

        def interrupted(command, **kwargs):
            result = subprocess.run(command, **kwargs)
            if "cherry-pick" in command and "--abort" not in command:
                raise KeyboardInterrupt
            return result

        with self.assertRaises(KeyboardInterrupt):
            fix_git.drop_commits(self.worktree, interrupted, branch=self.branch,
                                 start_sha=self.start_sha, refs={3}, linked={3: [first]},
                                 allowed={3, 5})
        self.assertEqual(self.head(self.worktree), head)
        self.assert_clean_on_branch()


class V2PassTest(GitCase):
    """A pass opened by the v2 fix mode (one key per repo and category) is closed, never resumed."""

    def v2_state(self, branch=None, start_sha=None, worktree=None, step="selected"):
        row = {"outcome": "pending", "reason": None, "commits": [], "pass": 0, "attention": None}
        return {
            "started_at": "2026-10-28T10:00:00Z", "finished_at": None, "stage": "keys",
            "mode": "present", "push": "mr", "notion": False,
            "keys": {"front/securite": {
                "repo": "front", "category": "SÉCURITÉ", "slug": "securite", "push_ready": True,
                "branch": branch, "base": "develop" if branch else None, "start_sha": start_sha,
                "worktree": worktree, "reused": None, "mr": None, "step": step, "pass": 0,
                "dev_fingerprint": None, "bases": {}, "reviews": {}, "reports": [],
                "rows": {"3": dict(row), "5": dict(row)},
            }},
        }

    def test_open_and_start_refuse_an_unfinished_v2_pass(self):
        fix_state.save(self.run_dir, self.v2_state())
        before = (self.run_dir / "fix" / "state.json").read_text(encoding="utf-8")
        for argv in (["open", self.run_dir, "--mode", "present", "--push", "mr", "--notion", "no"],
                     ["start", self.run_dir, KEY],
                     ["start", self.run_dir, "front/securite"],
                     ["stage", self.run_dir, "questions"]):
            with self.subTest(command=argv[0], key=argv[-1]):
                code, out, err = self.run_main(argv)
                self.assertEqual(code, 2, err)
                self.assertIn(V2_PASS, err)
        self.assertEqual((self.run_dir / "fix" / "state.json").read_text(encoding="utf-8"), before)
        self.assertFalse((self.run_dir / "fix" / "front").exists())

    def test_abandon_handles_a_v2_state(self):
        branch = PREFIX + "securite"
        start = self.head(self.repo, "refs/remotes/origin/develop")
        worktree = self.run_dir / "fix" / "front" / "securite" / "wt"
        real_git(self.repo, "worktree", "add", "-q", "--no-track", "-b", branch, str(worktree),
                 start)
        self.fixer_commit(worktree, "a3.txt", "fix(CQ-3): add a")
        (worktree.parent / "report-1.json").write_text('{"key": "front/securite"}',
                                                       encoding="utf-8")
        fix_state.save(self.run_dir, self.v2_state(branch, start, str(worktree), "fixed"))
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"removed": [str(worktree)],
                                           "unpushed": [{"branch": branch, "commits": 1}]})
        self.assertNotIn(worktree.resolve(), self.worktree_paths())
        self.assertIsNotNone(self.state()["finished_at"])
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}]))
        archived = self.run_dir / self.open_pass()["archived"]
        self.assertTrue((archived / "front" / "securite" / "report-1.json").is_file())
        self.assertFalse((self.run_dir / "fix" / "front").exists())
        self.assertEqual(list(self.state()["keys"]), [KEY])
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (BASE, None))
        real_git(self.repo, "rev-parse", "--verify", f"refs/heads/{branch}")


class AbandonTest(GitCase):
    def test_abandon_keeps_branches_with_unpushed_commits(self):
        self.open_pass()
        busy = self.start_ok()
        self.fixer_commit(busy["worktree"], "1.txt", "fix(CQ-3): one")
        self.fixer_commit(busy["worktree"], "2.txt", "fix(CQ-5): two")
        (Path(busy["worktree"]) / "draft.txt").write_text("uncommitted", encoding="utf-8")
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"removed": [busy["worktree"]],
                                           "unpushed": [{"branch": BASE, "commits": 2}]})
        self.assertFalse(Path(busy["worktree"]).exists())
        self.assertNotIn(Path(busy["worktree"]), self.worktree_paths())
        real_git(self.repo, "rev-parse", "--verify", f"refs/heads/{BASE}")
        self.assertIsNotNone(self.state()["finished_at"])
        self.assertFalse(fix_state.pending(self.run_dir))
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 2)
        self.open_pass()
        idle = self.start_ok()
        self.assertEqual(idle["branch"], BASE + "-2")
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"removed": [idle["worktree"]], "unpushed": []})
        real_git(self.repo, "rev-parse", "--verify", f"refs/heads/{BASE}-2")

    def test_open_removes_the_worktrees_of_a_finished_pass(self):
        self.open_pass()
        started = self.start_ok()
        self.fixer_commit(started["worktree"], "1.txt", "fix(CQ-3): one")
        (self.run_dir / "fix" / "front" / "report-1.json").write_text("{}", encoding="utf-8")
        state = self.state()
        state["finished_at"] = "2026-10-29T10:00:00Z"
        fix_state.save(self.run_dir, state)
        result = self.open_pass()
        self.assertEqual(result["removed"], [started["worktree"]])
        self.assertNotIn(Path(started["worktree"]), self.worktree_paths())
        archived = self.run_dir / result["archived"]
        self.assertTrue((archived / "front" / "report-1.json").is_file())
        self.assertFalse((archived / "front" / "wt").exists())
        self.assertFalse((self.run_dir / "fix" / "front").exists())
        again = self.start_ok()
        self.assertEqual(again["branch"], BASE + "-2")


class LeftKeyTest(ReplayCase):
    """A key left to the lead (a merge commit or a rewritten history in its branch) is recorded
    in fix/state.json by leave: reorder, reword, mr-body, publish and finish refuse it until
    leave --clear, and a new pass starts without it (ruling P19)."""

    REASON = "commit de fusion dans la branche"

    def leave(self, *argv):
        return self.main_json("leave", *argv)

    def reviewed(self):
        """Bring the key to reviewed with its sections settled: CQ-3 fixed in pass 1 with one
        commit, reviewed without a blocking verdict, CQ-5 and CQ-12 skipped."""
        sha = self.commit_files("fix(securite CQ-3): add a", {"a3.txt": "a\n"})
        state = self.state()
        key = state["keys"][KEY]
        for section in key["sections"]:
            section["state"] = "settled"
        key["rows"]["3"].update(outcome="fixed", commits=[sha], **{"pass": 1})
        for ref in ("5", "12"):
            key["rows"][ref].update(outcome="skipped", reason="Hors-scope.")
        key.update(step="reviewed", bases={"1": self.start_sha}, reviews={"1": {"blocking": []}},
                   pass_sections={"1": "securite"}, **{"pass": 1})
        fix_state.save(self.run_dir, state)
        return sha

    def test_leave_records_the_reason_and_clear_takes_the_key_back(self):
        code, result, err = self.leave("--reason", f"  {self.REASON}  ")
        self.assertEqual(code, 0, err)
        left = self.key()["left"]
        self.assertEqual(left["reason"], self.REASON)
        self.assertRegex(left["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(result, {"key": KEY, "left": left, "changed": True, "branch": self.branch,
                                  "worktree": str(self.worktree)})
        code, result, err = self.leave("--reason", self.REASON)
        self.assertEqual((code, result["changed"], result["left"]), (0, False, left), err)
        self.assertEqual(self.key()["left"], left)
        code, result, err = self.leave("--reason", "historique réécrit")
        self.assertEqual((code, result["changed"], result["left"]["reason"]),
                         (0, True, "historique réécrit"), err)
        code, result, err = self.leave("--clear")
        self.assertEqual(code, 0, err)
        self.assertEqual((result["left"], result["changed"]), (None, True))
        self.assertIsNone(self.key()["left"])
        code, result, err = self.leave("--clear")
        self.assertEqual((code, result["left"], result["changed"]), (0, None, False), err)
        for argv in (["--reason", "x", "--clear"], [], ["--reason", "   "],
                     ["--reason", "x" * 301]):
            with self.subTest(argv=argv):
                code, result, err = self.leave(*argv)
                self.assertEqual((code, result), (2, None), err)
        code, out, err = self.run_main(["leave", self.run_dir, "back", "--reason", "x"])
        self.assertEqual(code, 2)
        self.assertIn("Clé inconnue : back", err)
        self.assertIsNone(self.key()["left"])
        self.assert_clean_on_branch()

    def test_a_left_key_is_refused_by_reorder_reword_mr_body_publish_and_finish(self):
        sha = self.reviewed()
        code, result, err = self.leave("--reason", self.REASON)
        self.assertEqual(code, 0, err)
        message = self.root / "message.txt"
        message.write_text("fix(securite CQ-3): add the a file\n", encoding="utf-8")
        before = self.key()
        for argv in (["reorder"], ["reword", "--commit", sha, "--message-file", str(message)],
                     ["mr-body"], ["publish"], ["finish"], ["finish", "--keep-local"]):
            with self.subTest(command=" ".join(argv)):
                code, result, err = self.main_json(*argv)
                self.assertEqual((code, result), (2, None), err)
                self.assertIn(self.REASON, err)
                self.assertIn(f"La clé {KEY} est laissée au lead", err)
                self.assertIn("leave", err)
        self.assertEqual(self.key(), before)
        self.assertEqual(self.head(self.worktree), sha)
        self.assertEqual(real_git(self.worktree, "log", "-1", "--format=%s"), "fix(securite CQ-3): add a")
        self.assert_clean_on_branch()
        self.assertFalse((self.run_dir / "fix" / "front" / "mr.md").exists())
        self.assertFalse((self.run_dir / "fix" / "front" / "mr-title.txt").exists())
        self.assertEqual(real_git(self.other, "ls-remote", "--heads", "origin", self.branch), "")
        self.assertEqual(self.leave("--clear")[0], 0)
        code, result, err = self.main_json("reorder")
        self.assertEqual((code, result["reordered"]), (0, False), err)
        code, result, err = self.main_json("finish", "--keep-local")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.key()["step"], self.key()["left"]), ("done", None))
        self.assertFalse(self.worktree.exists())
        code, result, err = self.leave("--reason", self.REASON)
        self.assertEqual((code, result), (2, None), err)
        self.assertIn("« done »", err)
        self.assertIsNone(self.key()["left"])

    def test_a_new_open_starts_without_left(self):
        self.assertEqual(self.leave("--reason", self.REASON)[0], 0)
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.key()["left"]["reason"], self.REASON)
        archived = self.run_dir / self.open_pass()["archived"]
        old = json.loads((archived / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(old["keys"][KEY]["left"]["reason"], self.REASON)
        key = self.key()
        self.assertIn("left", key)
        self.assertIsNone(key["left"])
        self.start_ok()
        code, result, err = self.main_json("reorder")
        self.assertEqual(code, 0, err)
