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

PREFIX = fix_names.BRANCH_PREFIX
DRY = fix_names.DRY_RUN_PREFIX
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

    def write_run(self, repo_flight, slugs=("securite",), rows=(3, 5)):
        (self.run_dir / "fix").mkdir(parents=True, exist_ok=True)
        repos = [{"name": "front", "path": str(self.repo), "remote": REMOTE}]
        (self.run_dir / "context.json").write_text(json.dumps({"repos": repos}), encoding="utf-8")
        doctor = {"repos": [{"repo": "front", "host": HOST, "project": "acme/shop",
                             "status": "ok", "user": "camille", "base": "develop"}]}
        self.write_json("fix/doctor.json", doctor)
        keys = [
            {"key": f"front/{slug}", "repo": "front", "category": slug.upper(), "slug": slug,
             "push_ready": True, "rows": [{"ref": ref, "title": f"Ligne {ref}"} for ref in rows]}
            for slug in slugs
        ]
        self.write_json("fix/candidates.json", {
            "keys": keys, "left": [], "flights": {"front": repo_flight}, "notes": [],
            "estimate_min": 0,
        })

    def write_json(self, relative, value):
        (self.run_dir / relative).write_text(json.dumps(value), encoding="utf-8")

    def open_pass(self, push="mr"):
        code, out, err = self.run_main(
            ["open", self.run_dir, "--mode", "autonomous", "--push", push, "--notion", "no"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def start(self, key="front/securite"):
        return self.run_main(["start", self.run_dir, key])

    def start_ok(self, key="front/securite"):
        code, out, err = self.start(key)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def state(self):
        return fix_state.load(self.run_dir)

    def key(self, key="front/securite"):
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
        self.fixer_commit(result["worktree"], "c.txt", "fix(CQ-3): add c")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(marker.exists())
        self.assertEqual(self.tracking_config(), "")
        real_git(result["worktree"], "commit", "-q", "--allow-empty", "-m", "hooks are live")
        self.assertTrue(marker.exists())

    def test_takeover_runs_no_dev_hook(self):
        branch = PREFIX + "securite"
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
        folder = self.run_dir / "fix" / "front" / "securite"
        branch = PREFIX + "securite"
        self.assertEqual(result, {
            "branch": branch, "worktree": str(folder / "wt"), "base": "develop",
            "start_sha": expected, "reused": None, "done_refs": [],
        })
        self.assertEqual(sorted(os.listdir(folder)), ["wt"])
        self.assertEqual(self.head(folder / "wt"), expected)
        self.assertEqual(real_git(folder / "wt", "symbolic-ref", "--short", "HEAD"), branch)
        key = self.key()
        self.assertEqual(
            (key["branch"], key["base"], key["start_sha"], key["worktree"], key["step"]),
            (branch, "develop", expected, str(folder / "wt"), "started"),
        )
        self.assertEqual((key["reused"], key["mr"]), (None, None))
        self.assertEqual(key["dev_fingerprint"]["head"], self.head(self.repo))
        self.assertEqual(key["dev_fingerprint"]["branch"], "develop")
        self.assertEqual(self.tracking_config(), "")

    def test_start_reuses_the_branch_of_an_open_mr(self):
        branch = PREFIX + "securite"
        remote = self.push_branch(branch, "fix(CQ-107): add headers")
        self.write_run(flight(open=[mr(12, branch, [107])]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(
            (result["branch"], result["reused"], result["start_sha"], result["done_refs"]),
            (branch, "mr", remote, [107]),
        )
        self.assertEqual(self.head(result["worktree"]), remote)
        key = self.key()
        self.assertEqual(key["mr"], {"iid": 12, "url": f"https://{HOST}/acme/shop/-/merge_requests/12"})
        self.assertEqual(key["reused"], "mr")
        self.assertEqual(self.tracking_config(), "")

    def test_start_reuses_an_open_mr_on_a_suffixed_branch(self):
        for suffix in ("-3", "-2"):
            self.push_branch(PREFIX + "securite" + suffix, f"fix(CQ-10{suffix[1]}): x")
        remote = self.head(self.other, PREFIX + "securite-2")
        real_git(self.repo, "checkout", "-q", "-b", PREFIX + "securite")
        commit(self.repo, "local.txt", "fix(CQ-9): local")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(
            open=[mr(13, PREFIX + "securite-3", [103]), mr(12, PREFIX + "securite-2", [102])],
            merged=[mr(9, PREFIX + "securite-1", [1])],
            branches=[{"branch": PREFIX + "securite", "where": "local", "refs": [9]}],
        ))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (PREFIX + "securite-2", "mr"))
        self.assertEqual((result["start_sha"], self.head(result["worktree"])), (remote, remote))
        self.assertEqual(self.key()["mr"]["iid"], 12)

    def test_start_reuses_an_unmerged_branch_without_mr_and_counts_its_refs(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "checkout", "-q", "-b", branch)
        local = commit(self.repo, "s.txt", "fix(CQ-3): x")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(
            (result["branch"], result["reused"], result["start_sha"], result["done_refs"]),
            (branch, "branch", local, [3]),
        )
        key = self.key()
        self.assertEqual(key["rows"]["3"]["outcome"], "done_before")
        self.assertEqual(key["rows"]["5"]["outcome"], "pending")
        self.assertIsNone(key["mr"])

    def test_start_fast_forwards_a_stale_local_branch(self):
        branch = PREFIX + "securite"
        first = self.push_branch(branch, "fix(CQ-107): one")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", branch, f"origin/{branch}")
        remote = self.push_branch(branch, "fix(CQ-108): two", start=f"origin/{branch}")
        self.assertNotEqual(first, remote)
        diverged = PREFIX + "qualite"
        self.push_branch(diverged, "fix(CQ-20): remote")
        real_git(self.repo, "fetch", "-q", "origin")
        real_git(self.repo, "branch", "-q", "--no-track", diverged, f"origin/{diverged}")
        real_git(self.repo, "checkout", "-q", diverged)
        local = commit(self.repo, "local.txt", "fix(CQ-21): local only")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(
            flight(open=[mr(12, branch, [107, 108]), mr(14, diverged, [20])]),
            slugs=("securite", "qualite"),
        )
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(result["start_sha"], remote)
        self.assertEqual(self.head(self.repo, f"refs/heads/{branch}"), remote)
        self.assertEqual(self.head(result["worktree"]), remote)
        code, out, err = self.start("front/qualite")
        self.assertEqual(code, 2)
        self.assertIn(local[:7], err)
        self.assertIn("fix(CQ-21): local only", err)
        self.assertEqual(self.head(self.repo, f"refs/heads/{diverged}"), local)
        self.assertFalse((self.run_dir / "fix" / "front" / "qualite" / "wt").exists())
        self.assertEqual(self.key("front/qualite")["step"], "selected")

    def finish(self, key="front/securite"):
        """What finish (task 6) does: remove the worktree, step done."""
        state = self.state()
        real_git(self.repo, "worktree", "remove", state["keys"][key]["worktree"])
        state["keys"][key]["step"] = "done"
        fix_state.save(self.run_dir, state)

    def test_start_again_after_finish_reuses_the_same_branch_and_mr(self):
        branch = PREFIX + "securite"
        self.push_branch(branch, "fix(CQ-107): x")
        self.write_run(flight(open=[mr(12, branch, [107])]), slugs=("securite", "qualite"))
        self.open_pass()
        first = self.start_ok()
        fixed = self.fixer_commit(first["worktree"], "f.txt", "fix(CQ-3): x")
        real_git(first["worktree"], "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        self.assertEqual(self.start_ok(), first)
        self.assertEqual(self.head(first["worktree"]), fixed)
        self.finish()
        reviewer = self.push_branch(branch, "Apply 1 suggestion(s) to 1 file(s)",
                                    start=f"origin/{branch}")
        again = self.start_ok()
        self.assertEqual(again, first)
        self.assertEqual(self.head(again["worktree"]), reviewer)
        key = self.key()
        self.assertEqual((key["mr"]["iid"], key["reused"], key["step"]), (12, "mr", "started"))
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.open_pass(push="local")
        local = self.start_ok("front/qualite")
        fixed = self.fixer_commit(local["worktree"], "q.txt", "fix(CQ-5): y")
        self.finish("front/qualite")
        again = self.start_ok("front/qualite")
        self.assertEqual(again, local)
        self.assertEqual(self.head(again["worktree"]), fixed)
        self.assertEqual(again["start_sha"], self.head(self.repo, "refs/remotes/origin/develop"))

    def test_start_never_takes_over_a_branch_when_gitlab_is_unreadable(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "checkout", "-q", "-b", branch)
        commit(self.repo, "s.txt", "fix(CQ-3): maybe in a closed MR")
        real_git(self.repo, "checkout", "-q", "develop")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}],
                              notes=[fix_gitlab.NO_API_NOTE]))
        self.open_pass()
        result = self.start_ok()
        self.assertEqual(
            (result["branch"], result["reused"], result["done_refs"]),
            (PREFIX + "securite-2", None, []),
        )
        self.assertEqual(self.key()["rows"]["3"]["outcome"], "pending")

    def test_start_takes_the_first_free_suffix(self):
        self.push_branch(PREFIX + "qualite-2", "fix(CQ-4): closed")
        merged = self.push_branch(PREFIX + "seo-geo", "fix(CQ-6): merged")
        real_git(self.other, "merge", "-q", "--ff-only", merged)
        real_git(self.other, "push", "-q", "origin", "develop")
        self.write_run(
            flight(
                merged=[mr(9, PREFIX + "securite", [1]), mr(10, PREFIX + "qualite", [2])],
                closed=[mr(11, PREFIX + "qualite-2", [4])],
            ),
            slugs=("securite", "qualite", "seo-geo"),
        )
        self.open_pass()
        self.assertEqual(self.start_ok()["branch"], PREFIX + "securite-2")
        self.assertEqual(self.start_ok("front/qualite")["branch"], PREFIX + "qualite-3")
        result = self.start_ok("front/seo-geo")
        self.assertEqual((result["branch"], result["reused"]), (PREFIX + "seo-geo-2", None))

    def test_dry_run_branches_are_never_reused(self):
        real_git(self.repo, "checkout", "-q", "-b", DRY + "securite")
        commit(self.repo, "d.txt", "fix(CQ-3): dry")
        real_git(self.repo, "checkout", "-q", "develop")
        base = self.head(self.repo, "refs/remotes/origin/develop")
        self.open_pass(push="dry_run")
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (DRY + "securite-2", None))
        self.assertEqual((result["start_sha"], result["done_refs"]), (base, []))
        self.assertEqual(self.run_main(["abandon", self.run_dir])[0], 0)
        self.open_pass(push="mr")
        result = self.start_ok()
        self.assertEqual((result["branch"], result["reused"]), (PREFIX + "securite", None))

    def test_start_refuses_a_branch_checked_out_by_the_dev(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "checkout", "-q", "-b", branch)
        commit(self.repo, "s.txt", "fix(CQ-3): x")
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": [3]}]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(
            f"La branche {branch} est extraite dans votre dossier : changez de branche, puis relancez.",
            err,
        )
        key = self.key()
        self.assertEqual((key["step"], key["branch"]), ("selected", None))
        self.assertFalse((self.run_dir / "fix" / "front" / "securite" / "wt").exists())

    def test_start_refuses_a_worktree_moved_to_another_branch(self):
        self.open_pass()
        first = self.start_ok()
        real_git(first["worktree"], "checkout", "-q", "-b", "elsewhere")
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(f"n'est plus sur la branche {first['branch']}", err)

    def test_start_names_a_vanished_worktree_holding_the_branch(self):
        branch = PREFIX + "securite"
        real_git(self.repo, "branch", "-q", branch)
        gone = self.root / "gone"
        real_git(self.repo, "worktree", "add", "-q", str(gone), branch)
        shutil.rmtree(gone)
        self.write_run(flight(branches=[{"branch": branch, "where": "local", "refs": []}]))
        self.open_pass()
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn(f"La branche {branch} est encore liée au worktree disparu", err)
        self.assertIn("git worktree prune", err)

    def test_start_of_an_unknown_key_exits_2(self):
        self.open_pass()
        code, out, err = self.start("front/nothing")
        self.assertEqual(code, 2)
        self.assertIn("front/nothing", err)

    def test_start_without_an_open_pass_exits_2(self):
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py open", err)


class AbandonTest(GitCase):
    def test_abandon_keeps_branches_with_unpushed_commits(self):
        self.write_run(flight(), slugs=("securite", "qualite"))
        self.open_pass()
        busy = self.start_ok()
        idle = self.start_ok("front/qualite")
        self.fixer_commit(busy["worktree"], "1.txt", "fix(CQ-3): one")
        self.fixer_commit(busy["worktree"], "2.txt", "fix(CQ-5): two")
        (Path(idle["worktree"]) / "draft.txt").write_text("uncommitted", encoding="utf-8")
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(sorted(result["removed"]), sorted([busy["worktree"], idle["worktree"]]))
        self.assertEqual(result["unpushed"], [{"branch": busy["branch"], "commits": 2}])
        for item in (busy, idle):
            self.assertFalse(Path(item["worktree"]).exists())
            self.assertNotIn(Path(item["worktree"]), self.worktree_paths())
            real_git(self.repo, "rev-parse", "--verify", f"refs/heads/{item['branch']}")
        self.assertIsNotNone(self.state()["finished_at"])
        self.assertFalse(fix_state.pending(self.run_dir))
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 2)

    def test_open_removes_the_worktrees_of_a_finished_pass(self):
        self.open_pass()
        started = self.start_ok()
        self.fixer_commit(started["worktree"], "1.txt", "fix(CQ-3): one")
        (self.run_dir / "fix" / "front" / "securite" / "report-1.json").write_text("{}", encoding="utf-8")
        state = self.state()
        state["finished_at"] = "2026-10-29T10:00:00Z"
        fix_state.save(self.run_dir, state)
        result = self.open_pass()
        self.assertEqual(result["removed"], [started["worktree"]])
        self.assertNotIn(Path(started["worktree"]), self.worktree_paths())
        archived = self.run_dir / result["archived"]
        self.assertTrue((archived / "front" / "securite" / "report-1.json").is_file())
        self.assertFalse((archived / "front" / "securite" / "wt").exists())
        self.assertFalse((self.run_dir / "fix" / "front").exists())
        again = self.start_ok()
        self.assertEqual(again["branch"], PREFIX + "securite-2")
