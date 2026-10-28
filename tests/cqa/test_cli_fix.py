import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import cqa_fix
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
            self.assertIn("fetched", front)
            written = json.loads((run_dir / "fix" / "doctor.json").read_text(encoding="utf-8"))
            self.assertEqual(written, report)

    def test_doctor_without_context_exits_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main(["doctor", tmp])
            self.assertEqual(code, 6)
            self.assertIn("context.json", err)


class RegistrationTest(FixCase):
    def test_every_fix_command_is_registered(self):
        for name in ("doctor",):
            code, out, err = self.run_main([name, "--help"])
            self.assertEqual(code, 0, name)


if __name__ == "__main__":
    unittest.main()
