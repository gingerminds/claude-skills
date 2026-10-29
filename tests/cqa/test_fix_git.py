import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from cqa import fix_gitlab, fix_names, fix_state
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
        self.assertTrue(any("CQ-5" in error for error in result["errors"]), result)
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
