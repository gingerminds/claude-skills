import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cqa_fix
from cqa import fix_gitlab
from tests.cqa.test_git_tools import commit, git as real_git
from tests.cqa.test_cli_prepare import clean_environ

REMOTE = "git@gitlab.example.org:acme/shop.git"


class FixCase(unittest.TestCase):
    def run_main(self, argv, run=subprocess.run):
        out, err = io.StringIO(), io.StringIO()
        with clean_environ():
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_fix.main([str(item) for item in argv], run=run)
        return code, out.getvalue(), err.getvalue()


def glab_ok(command, **kwargs):
    if command[0] == "git":
        return subprocess.run(command, **kwargs)
    api = command[2]
    if api == "user":
        return subprocess.CompletedProcess(command, 0, '{"username": "camille"}', "")
    if api == "version":
        return subprocess.CompletedProcess(command, 0, "{}", "")
    body = {"default_branch": "develop", "permissions": {"project_access": {"access_level": 40}}}
    return subprocess.CompletedProcess(command, 0, json.dumps(body), "")


class DoctorTest(FixCase):
    def make_repo(self, root, name, identity):
        origin = root / f"{name}.git"
        repo = root / name
        real_git(root, "init", "-q", "--bare", str(origin))
        real_git(root, "clone", "-q", str(origin), str(repo))
        real_git(repo, "symbolic-ref", "HEAD", "refs/heads/develop")
        audited = commit(repo, "a.txt", "one")
        commit(repo, "b.txt", "two")
        commit(repo, "c.txt", "three")
        real_git(repo, "push", "-q", "origin", "develop")
        real_git(repo, "reset", "-q", "--hard", audited)
        if identity:
            real_git(repo, "config", "user.name", "Camille")
            real_git(repo, "config", "user.email", "camille@example.org")
        return {"name": name, "path": str(repo), "remote": REMOTE, "audited_sha": audited}

    def test_doctor_reports_each_repo_and_writes_doctor_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos = [self.make_repo(root, "front", True), self.make_repo(root, "back", False)]
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "context.json").write_text(json.dumps({"repos": repos}), encoding="utf-8")
            with clean_environ({"HOME": tmp, "GIT_CONFIG_GLOBAL": "/dev/null",
                                "GIT_CONFIG_NOSYSTEM": "1"}):
                code, out, err = self.run_main(["doctor", run_dir], run=glab_ok)
            self.assertEqual(code, 0, err)
            report = json.loads(out)
            front, back = report["repos"]
            self.assertEqual((front["repo"], front["status"], front["base"]), ("front", "ok", "develop"))
            self.assertEqual((front["host"], front["project"]), ("gitlab.example.org", "acme/shop"))
            self.assertEqual((front["identity"], back["identity"]), (True, False))
            self.assertEqual(front["ahead"], 2)
            self.assertIs(front["fetched"], True)
            written = json.loads((run_dir / "fix" / "doctor.json").read_text(encoding="utf-8"))
            self.assertEqual(written, report)

    def test_doctor_with_a_failing_glab_and_unknown_sha(self):
        def down(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root, "front", True)
            repo["audited_sha"] = "0123456789abcdef0123456789abcdef01234567"
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "context.json").write_text(json.dumps({"repos": [repo]}), encoding="utf-8")
            code, out, err = self.run_main(["doctor", run_dir], run=down)
            self.assertEqual(code, 0, err)
            entry = json.loads(out)["repos"][0]
            self.assertEqual(entry["status"], "unauthenticated")
            self.assertIsNone(entry["ahead"])

    def test_doctor_repo_without_path_exits_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "context.json").write_text(
                json.dumps({"repos": [{"name": "front"}]}), encoding="utf-8"
            )
            code, out, err = self.run_main(["doctor", tmp])
            self.assertEqual(code, 6)
            self.assertIn("chemin", err)

    def test_doctor_without_context_exits_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main(["doctor", tmp])
            self.assertEqual(code, 6)
            self.assertIn("context.json", err)


class CandidatesTest(FixCase):
    def make_run(self, root, statuses=None):
        repos = []
        for name in ("front", "back"):
            repo = root / name
            repo.mkdir()
            (repo / "a.php").write_text("<?php", encoding="utf-8")
            repos.append({"name": name, "path": str(repo), "remote": REMOTE})
        run_dir = root / "run"
        (run_dir / "findings").mkdir(parents=True)
        (run_dir / "fiches").mkdir()
        (run_dir / "fiches" / "CQ-001.md").write_text("x", encoding="utf-8")
        (run_dir / "context.json").write_text(json.dumps({"repos": repos}), encoding="utf-8")
        rows = [
            {"ref": 1, "page_id": "p1", "title": "Un", "status": "À traiter", "assignees": [],
             "category": "SÉCURITÉ", "automation": "Auto : détection + MR"},
            {"ref": 2, "page_id": "p2", "title": "Deux", "status": "À traiter", "assignees": [],
             "category": "SEO / GEO", "automation": "Manuel"},
        ]
        (run_dir / "rows.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
        (run_dir / "lots.json").write_text(json.dumps({
            "lots": [{"id": "lot-a", "refs": [1, 2]}], "excluded": []}), encoding="utf-8")
        item = {"verdict": "ko", "confidence": "high", "summary": "s", "todo": "t",
                "human_check": None, "divergence": None, "needs_probes": [],
                "evidence": [{"type": "file", "repo": "front", "path": "a.php"}]}
        (run_dir / "findings" / "lot-a.json").write_text(json.dumps({
            "findings": [{"ref": 1, **item}, {"ref": 2, **item}]}), encoding="utf-8")
        statuses = statuses or {"front": "ok", "back": "ok"}
        doctor = {"repos": [{"repo": n, "host": "gitlab.example.org", "project": "acme/shop",
                             "status": s, "base": "develop"} for n, s in statuses.items()]}
        (run_dir / "fix").mkdir()
        (run_dir / "fix" / "doctor.json").write_text(json.dumps(doctor), encoding="utf-8")
        return run_dir

    def empty_flight(self, run, repo, host, project, base, *, api):
        self.calls[repo["name"]] = api
        return {"open": [], "closed": [], "merged": [], "branches": [], "notes": ["n"]}

    def test_candidates_writes_the_file_and_needs_the_doctor(self):
        self.calls = {}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(Path(tmp), {"front": "ok", "back": "unauthenticated"})
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                code, out, err = self.run_main(["candidates", run_dir])
            self.assertEqual(code, 0, err)
            written = json.loads((run_dir / "fix" / "candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(written, json.loads(out))
            self.assertEqual([k["key"] for k in written["keys"]], ["front/securite"])
            self.assertEqual(written["keys"][0]["rows"][0]["fiche_path"], "fiches/CQ-001.md")
            self.assertEqual([e["reason"] for e in written["left"]], ["Manuel"])
            self.assertEqual(self.calls, {"front": True, "back": False})
            self.assertEqual(written["notes"], ["n"])
            self.assertEqual(set(written["flights"]), {"front", "back"})
            (run_dir / "fix" / "doctor.json").unlink()
            code, out, err = self.run_main(["candidates", run_dir])
            self.assertEqual(code, 2)
            self.assertIn("Lancez d'abord cqa_fix.py doctor.", err)

    def test_candidates_options_and_flight_api_flags(self):
        self.calls = {}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(Path(tmp), {"front": "no_push", "back": "glab_missing"})
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                code, out, err = self.run_main(
                    ["candidates", run_dir, "--refs", "CQ-2,CQ-999", "--without", "CQ-1"])
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual(self.calls, {"front": True, "back": False})
            self.assertEqual([r["ref"] for k in result["keys"] for r in k["rows"]], [2])
            self.assertIn("CQ-999 ne figure pas dans la copie : ignorée.", result["notes"])
            self.assertFalse(result["keys"][0]["push_ready"])

    def test_candidates_without_rows_json_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(Path(tmp))
            (run_dir / "rows.json").unlink()
            code, out, err = self.run_main(["candidates", run_dir])
            self.assertEqual(code, 2)
            self.assertIn("rows.json", err)


class RegistrationTest(FixCase):
    def test_every_fix_command_is_registered(self):
        for name in ("doctor", "candidates"):
            code, out, err = self.run_main([name, "--help"])
            self.assertEqual(code, 0, name)


if __name__ == "__main__":
    unittest.main()
