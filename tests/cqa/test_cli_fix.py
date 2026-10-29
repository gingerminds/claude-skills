import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cqa_fix
from cqa import fix_gitlab, fix_state
from tests.cqa.test_git_tools import commit, git as real_git
from tests.cqa.test_cli_prepare import clean_environ

REMOTE = "git@gitlab.example.org:acme/shop.git"


GITLEAKS_DOWN = "gitleaks indisponible : contrôle maison seul"
CANARY = "CANARY-VALUE-NEVER-COPIED"


def is_secrets_call(command) -> bool:
    """True for the launcher call of the guard: bash .../cqa-toolbox.sh secrets <in> <out_dir>."""
    return (len(command) == 5 and command[0] == "bash" and command[2] == "secrets"
            and Path(command[1]).name == "cqa-toolbox.sh")


def fake_gitleaks(command, rules):
    """Answer the launcher's secrets command as the toolbox would, without Docker.

    rules maps the text of a line to the RuleID gitleaks gives it; the report lists one
    finding per flagged line of <in>, Match and Secret being canaries. rules None stands for
    a workstation without Docker (exit 2, nothing written).
    """
    if rules is None:
        return subprocess.CompletedProcess(
            command, 2, "", "Docker est introuvable : installez Docker Desktop (macOS) ou "
                            "Docker Engine (Linux).\n")
    text = Path(command[3]).read_bytes().decode("utf-8", "replace")
    findings = []
    for number, line in enumerate(text.split("\n"), start=1):
        for needle, rule in rules.items():
            if needle in line:
                findings.append({
                    "Description": rule, "StartLine": number, "EndLine": number,
                    "StartColumn": 1, "EndColumn": 2, "Match": CANARY, "Secret": CANARY,
                    "File": "", "SymlinkFile": "", "Commit": "", "Entropy": 4.2, "Author": "",
                    "Email": "", "Date": "", "Message": "", "Tags": [], "RuleID": rule,
                    "Fingerprint": f":{rule}:{number}",
                })
    (Path(command[4]) / "gitleaks.json").write_text(json.dumps(findings), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "", "")


class FixCase(unittest.TestCase):
    """The fix commands with the toolbox simulated: the guard's gitleaks call never starts a
    container. gitleaks maps the text of a line to the RuleID the fake reports ({} finds
    nothing); None stands for a workstation without Docker. toolbox_inputs keeps what each
    call fed gitleaks."""

    gitleaks = {}

    def run_main(self, argv, run=subprocess.run, env=None):
        if not hasattr(self, "toolbox_inputs"):
            self.toolbox_inputs, self.toolbox_paths = [], []

        def runner(command, **kwargs):
            if is_secrets_call(command):
                self.toolbox_inputs.append(Path(command[3]).read_bytes().decode("utf-8", "replace"))
                self.toolbox_paths.append((Path(command[3]), Path(command[4])))
                return fake_gitleaks(command, self.gitleaks)
            return run(command, **kwargs)

        out, err = io.StringIO(), io.StringIO()
        with clean_environ(env):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_fix.main([str(item) for item in argv], run=runner)
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
            self.assertEqual(entry["status"], "unreachable")
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
            self.assertEqual([k["key"] for k in written["keys"]], ["front"])
            self.assertEqual([s["alias"] for s in written["keys"][0]["sections"]], ["securite"])
            self.assertEqual(written["keys"][0]["sections"][0]["rows"][0]["fiche_path"],
                             "fiches/CQ-001.md")
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
            self.assertEqual(
                [r["ref"] for k in result["keys"] for s in k["sections"] for r in s["rows"]], [2])
            self.assertIn("CQ-999 ne figure pas dans la copie : ignorée.", result["notes"])
            self.assertFalse(result["keys"][0]["push_ready"])

    def test_candidates_read_a_v2_mr_through_gitlab(self):
        url = "https://gitlab.example.org/acme/shop/-/merge_requests/318"

        def gitlab(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            path = command[-1]
            if path.endswith("/merge_requests/318/commits?per_page=100"):
                answer = [{"title": "fix(CQ-1): add headers"}]
            elif "state=opened" in path:
                answer = [{"iid": 318, "web_url": url,
                           "source_branch": "feature/checklist-qa-fix-securite"}]
            else:
                answer = []
            return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(Path(tmp))
            code, out, err = self.run_main(["candidates", run_dir, "--refs", "CQ-2"], run=gitlab)
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual([(s["alias"], [r["ref"] for r in s["rows"]])
                              for k in result["keys"] for s in k["sections"]], [("seo", [2])])
            self.assertEqual([(e["ref"], e["reason"], e["url"]) for e in result["left"]],
                             [(1, "déjà dans la MR !318", url)])
            self.assertEqual(result["flights"]["front"]["open"][0]["refs"], [1])

    def test_candidates_without_rows_json_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_run(Path(tmp))
            (run_dir / "rows.json").unlink()
            code, out, err = self.run_main(["candidates", run_dir])
            self.assertEqual(code, 2)
            self.assertIn("rows.json", err)


class PassTest(FixCase):
    UNFINISHED = "Une passe fix est inachevée : reprenez-la ou abandonnez-la (cqa_fix.py abandon)."
    PROJECT = {"slug": "boutique", "name": "Boutique Exemple", "protection": None}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "run"
        (self.run_dir / "fix").mkdir(parents=True)
        rows = [{"ref": 3, "title": "Trois"}, {"ref": 12, "title": "Douze"}]
        self.write_candidates([{"key": "front", "repo": "front", "push_ready": False, "sections": [
            {"category": "SÉCURITÉ", "alias": "securite", "rows": rows}]}])
        self.write_context({"project": self.PROJECT,
                            "repos": [{"name": "front", "path": "/srv/front"}]})

    def write_candidates(self, keys):
        candidates = {"keys": keys, "left": [], "flights": {}, "notes": [], "estimate_min": 18}
        (self.run_dir / "fix" / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")

    def write_context(self, context):
        (self.run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")

    def open(self, mode="present", push="mr", notion="yes"):
        return self.run_main(
            ["open", self.run_dir, "--mode", mode, "--push", push, "--notion", notion])

    def state(self):
        return json.loads((self.run_dir / "fix" / "state.json").read_text(encoding="utf-8"))

    def finish(self):
        state = fix_state.load(self.run_dir)
        state["finished_at"] = "2026-10-29T10:00:00Z"
        fix_state.save(self.run_dir, state)

    def test_open_writes_the_state(self):
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        state = self.state()
        self.assertRegex(state["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(
            {k: state[k] for k in ("finished_at", "stage", "mode", "push", "notion")},
            {"finished_at": None, "stage": "keys", "mode": "present", "push": "mr", "notion": True},
        )
        self.assertEqual(state["project_name"], "Boutique Exemple")
        row = {"outcome": "pending", "reason": None, "commits": [], "pass": 0, "attention": None,
               "section": "securite"}
        self.assertEqual(state["keys"], {"front": {
            "repo": "front", "push_ready": False,
            "branch": None, "base": None, "start_sha": None, "worktree": None, "reused": None,
            "mr": None, "step": "selected", "pass": 0, "dev_fingerprint": None,
            "bases": {}, "reviews": {}, "reports": [],
            "sections": [{"category": "SÉCURITÉ", "alias": "securite", "refs": [3, 12],
                          "state": "pending", "reason": None}],
            "pass_sections": {}, "rows": {"3": row, "12": row},
        }})
        result = json.loads(out)
        self.assertEqual(result["keys"], ["front"])
        self.assertEqual(result["project_name"], "Boutique Exemple")
        self.assertIsNone(result["archived"])

    def test_open_builds_one_key_per_repo_with_sections(self):
        row = lambda ref: {"ref": ref, "title": f"Ligne {ref}"}
        self.write_candidates([
            {"key": "back", "repo": "back", "push_ready": True, "sections": [
                {"category": "QUALITÉ", "alias": "qualite", "rows": [row(7)]}]},
            {"key": "front", "repo": "front", "push_ready": False, "sections": [
                {"category": "SÉCURITÉ", "alias": "securite", "rows": [row(3), row(12)]},
                {"category": "SEO / GEO", "alias": "seo", "rows": [row(40)]}]},
        ])
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["keys"], ["back", "front"])
        state = self.state()
        self.assertEqual(list(state["keys"]), ["back", "front"])
        front = state["keys"]["front"]
        self.assertEqual(front["sections"], [
            {"category": "SÉCURITÉ", "alias": "securite", "refs": [3, 12], "state": "pending",
             "reason": None},
            {"category": "SEO / GEO", "alias": "seo", "refs": [40], "state": "pending",
             "reason": None},
        ])
        self.assertEqual({ref: item["section"] for ref, item in front["rows"].items()},
                         {"3": "securite", "12": "securite", "40": "seo"})
        self.assertEqual((front["pass_sections"], front["push_ready"]), ({}, False))
        self.assertNotIn("slug", front)
        self.assertNotIn("category", front)
        back = state["keys"]["back"]
        self.assertEqual(([item["alias"] for item in back["sections"]], list(back["rows"]),
                          back["push_ready"]), (["qualite"], ["7"], True))

    def test_open_records_the_project_name(self):
        self.assertEqual(self.open()[0], 0)
        self.assertEqual(self.state()["project_name"], "Boutique Exemple")
        self.finish()
        self.write_context({"repos": [{"name": "front", "path": "/srv/Acme - Boutique - WordPress"}]})
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["project_name"], "Acme - Boutique")
        self.assertEqual(json.loads(out)["project_name"], "Acme - Boutique")

    def test_dry_run_forces_notion_off(self):
        code, out, err = self.open(mode="autonomous", push="dry_run", notion="yes")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.state()["push"], self.state()["notion"]), ("dry_run", False))

    def test_open_needs_candidates_and_valid_options(self):
        code, out, err = self.open(push="everywhere")
        self.assertEqual(code, 2)
        v2 = {"key": "front/securite", "repo": "front", "category": "SÉCURITÉ",
              "slug": "securite", "push_ready": False, "rows": [{"ref": 3, "title": "Trois"}]}
        self.write_candidates([v2])
        code, out, err = self.open()
        self.assertEqual(code, 2)
        self.assertIn("relancez cqa_fix.py candidates", err)
        (self.run_dir / "context.json").unlink()
        code, out, err = self.open()
        self.assertEqual(code, 6)
        self.assertIn("context.json", err)
        (self.run_dir / "fix" / "candidates.json").unlink()
        code, out, err = self.open()
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py candidates", err)
        self.assertFalse((self.run_dir / "fix" / "state.json").exists())

    def test_open_refuses_while_a_pass_is_unfinished(self):
        self.assertEqual(self.open()[0], 0)
        before = self.state()
        code, out, err = self.open(push="local")
        self.assertEqual(code, 2)
        self.assertIn(self.UNFINISHED, err)
        self.assertEqual(self.state(), before)

    def test_open_archives_a_finished_pass(self):
        self.assertEqual(self.open()[0], 0)
        folder = self.run_dir / "fix" / "front"
        folder.mkdir(parents=True)
        (folder / "report-1.json").write_text('{"key": "front"}', encoding="utf-8")
        self.finish()
        old = self.state()
        code, out, err = self.open(push="local")
        self.assertEqual(code, 0, err)
        archived = json.loads(out)["archived"]
        self.assertRegex(archived, r"^fix/_history/\d{4}-\d{2}-\d{2}-\d{6}$")
        history = self.run_dir / archived
        self.assertEqual(
            json.loads((history / "state.json").read_text(encoding="utf-8")), old
        )
        self.assertTrue((history / "front" / "report-1.json").is_file())
        self.assertFalse((self.run_dir / "fix" / "front").exists())
        state = self.state()
        self.assertEqual((state["finished_at"], state["push"]), (None, "local"))
        self.assertEqual(state["keys"]["front"]["step"], "selected")

    def test_pending_until_summary(self):
        self.assertFalse(fix_state.pending(self.run_dir))
        self.assertEqual(self.open()[0], 0)
        self.assertTrue(fix_state.pending(self.run_dir))
        for stage in ("questions", "notion", "summary"):
            code, out, err = self.run_main(["stage", self.run_dir, stage])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.state()["stage"], stage)
            self.assertTrue(fix_state.pending(self.run_dir))
        self.finish()
        self.assertFalse(fix_state.pending(self.run_dir))

    def test_stage_moves_forward_only(self):
        code, out, err = self.run_main(["stage", self.run_dir, "questions"])
        self.assertEqual(code, 2)
        self.assertEqual(self.open()[0], 0)
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 0)
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 0)
        code, out, err = self.run_main(["stage", self.run_dir, "questions"])
        self.assertEqual(code, 2)
        self.assertIn("notion", err)
        self.assertEqual(self.run_main(["stage", self.run_dir, "keys"])[0], 2)
        self.assertEqual(self.run_main(["stage", self.run_dir, "later"])[0], 2)
        self.assertEqual(self.state()["stage"], "notion")

    def test_invalid_state_exits_6(self):
        (self.run_dir / "fix" / "state.json").write_text("{", encoding="utf-8")
        self.assertTrue(fix_state.pending(self.run_dir))
        self.assertEqual(self.open()[0], 6)
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 6)
        (self.run_dir / "fix" / "state.json").unlink()
        self.assertEqual(self.open()[0], 0)
        state = self.state()
        state["keys"]["front"]["branch"] = "--upload-pack=x"
        (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 6)
        state["keys"]["front"]["branch"] = "feature/checklist-qa-fix-securite"
        (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 6)
        state["keys"]["front"]["branch"] = "feature/checklist-qa-fix-2"
        state["keys"]["front"]["sections"][0]["state"] = "done"
        (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 6)

    def test_key_commands_need_a_started_key(self):
        self.assertEqual(self.open()[0], 0)
        key = "front"
        for argv in (
            ["section-start", self.run_dir, key, "--section", "securite"],
            ["section-end", self.run_dir, key, "--section", "securite"],
            ["pass-start", self.run_dir, key, "--pass", "1", "--section", "securite"],
            ["validate", self.run_dir, key, "report", "--pass", "1"],
            ["validate", self.run_dir, key, "review", "--pass", "1"],
            ["guard", self.run_dir, key],
            ["check", self.run_dir, key],
            ["drop", self.run_dir, key, "--refs", "CQ-3", "--reason", "Hors-scope."],
            ["mr-body", self.run_dir, key],
            ["publish", self.run_dir, key],
            ["finish", self.run_dir, key],
        ):
            with self.subTest(command=argv[0]):
                code, out, err = self.run_main(argv)
                self.assertEqual(code, 2, err)
                self.assertIn("cqa_fix.py start", err)
        self.assertEqual(self.state()["keys"][key]["rows"]["3"]["outcome"], "pending")
        code, out, err = self.run_main(["guard", self.run_dir, "back"])
        self.assertEqual(code, 2)
        self.assertIn("Clé inconnue : back", err)

    def test_abandon_without_a_pass_exits_2(self):
        code, out, err = self.run_main(["abandon", self.run_dir])
        self.assertEqual(code, 2)
        self.assertIn("cqa_fix.py open", err)

    def test_save_replaces_the_state_in_one_step(self):
        self.assertEqual(self.open()[0], 0)
        state = fix_state.load(self.run_dir)
        state["stage"] = "notion"
        with mock.patch("cqa.notion_read.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                fix_state.save(self.run_dir, state)
        self.assertEqual(fix_state.load(self.run_dir)["stage"], "keys")
        (self.run_dir / "fix" / "state.json.tmp").unlink()
        fix_state.save(self.run_dir, state)
        self.assertEqual(sorted(os.listdir(self.run_dir / "fix")), ["candidates.json", "state.json"])
        self.assertEqual(fix_state.load(self.run_dir)["stage"], "notion")


class RegistrationTest(FixCase):
    def test_every_fix_command_is_registered(self):
        for name in ("doctor", "candidates", "open", "stage", "start", "section-start",
                     "section-end", "pass-start", "validate", "guard", "review-scope", "waive-secret", "drop",
                     "reorder", "reword",
                     "check", "mr-body", "publish", "finish", "summary", "abandon"):
            code, out, err = self.run_main([name, "--help"])
            self.assertEqual(code, 0, name)


if __name__ == "__main__":
    unittest.main()
