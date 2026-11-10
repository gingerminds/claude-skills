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

    def make_category_run(self, root, category_filter, excluded):
        run_dir = self.make_run(root)
        names = {1: "SÉCURITÉ", 2: "QUALITÉ", 3: "PERFORMANCE"}
        rows = [{"ref": ref, "page_id": f"p{ref}", "title": f"Ligne {ref}", "status": "À traiter",
                 "assignees": [], "category": name, "automation": "Auto : détection + MR"}
                for ref, name in names.items()]
        (run_dir / "rows.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
        context = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
        context["category_filter"] = category_filter
        (run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
        (run_dir / "lots.json").write_text(json.dumps({
            "lots": [{"id": "lot-a", "refs": [1, 2, 3]}],
            "excluded": [{"ref": ref, "reason": "category"} for ref in excluded]}), encoding="utf-8")
        item = {"verdict": "ko", "confidence": "high", "summary": "s", "todo": "t",
                "human_check": None, "divergence": None, "needs_probes": [],
                "evidence": [{"type": "file", "repo": "front", "path": "a.php"}]}
        (run_dir / "findings" / "lot-a.json").write_text(json.dumps({
            "findings": [{"ref": ref, **item} for ref in names]}), encoding="utf-8")
        return run_dir

    def candidate_rows(self, out):
        result = json.loads(out)
        return [(s["alias"], [r["ref"] for r in s["rows"]])
                for k in result["keys"] for s in k["sections"]]

    def test_candidates_category_restricts_the_run_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), ["SÉCURITÉ", "QUALITÉ"], [3])
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                self.calls = {}
                code, out, err = self.run_main(["candidates", run_dir])
                self.assertEqual(code, 0, err)
                self.assertEqual(self.candidate_rows(out), [("securite", [1]), ("qualite", [2])])
                self.assertEqual(json.loads(out)["left"], [])
                code, out, err = self.run_main(["candidates", run_dir, "--category", "securite"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.candidate_rows(out), [("securite", [1])])
            result = json.loads(out)
            self.assertEqual(result["left"], [])
            written = json.loads((run_dir / "fix" / "candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(written, result)

    def test_candidates_category_is_repeatable_and_resolves_names_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), None, [])
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                self.calls = {}
                code, out, err = self.run_main(
                    ["candidates", run_dir, "--category", "Qualité", "--category", "SÉCURITÉ"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.candidate_rows(out), [("securite", [1]), ("qualite", [2])])

    def test_candidates_category_on_a_full_run_restricts_the_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), None, [])
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                self.calls = {}
                code, out, err = self.run_main(["candidates", run_dir, "--category", "performance"])
            self.assertEqual(code, 0, err)
            self.assertEqual(self.candidate_rows(out), [("performance", [3])])
            self.assertEqual(json.loads(out)["left"], [])

    def test_candidates_category_outside_the_run_filter_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), ["SÉCURITÉ", "QUALITÉ"], [3])
            code, out, err = self.run_main(["candidates", run_dir, "--category", "performance"])
            self.assertEqual(code, 2)
            self.assertIn("La catégorie performance est hors du périmètre du run "
                          "(SÉCURITÉ, QUALITÉ).", err)
            self.assertEqual(out, "")
            self.assertFalse((run_dir / "fix" / "candidates.json").exists())
            code, out, err = self.run_main(
                ["candidates", run_dir, "--category", "securite", "--category", "performance"])
            self.assertEqual(code, 2)
            self.assertIn("La catégorie performance est hors du périmètre du run", err)

    def test_candidates_unknown_category_exits_2_with_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), None, [])
            code, out, err = self.run_main(["candidates", run_dir, "--category", "a11y"])
            self.assertEqual(code, 2)
            self.assertIn("Catégorie inconnue : a11y. Catégories de la copie : "
                          "SÉCURITÉ, QUALITÉ, PERFORMANCE.", err)
            self.assertEqual(out, "")
            self.assertFalse((run_dir / "fix" / "candidates.json").exists())

    def test_candidates_forced_ref_outside_the_requested_category_is_noted(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.make_category_run(Path(tmp), None, [])
            with mock.patch.object(fix_gitlab, "in_flight", self.empty_flight):
                self.calls = {}
                code, out, err = self.run_main(
                    ["candidates", run_dir, "--category", "securite", "--refs", "CQ-2"])
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual(self.candidate_rows(out), [("securite", [1])])
            self.assertIn("CQ-2 hors des catégories demandées : ignorée.", result["notes"])

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
            "pass_sections": {}, "rows": {"3": row, "12": row}, "left": None,
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

    def test_open_carries_only_the_stack_cores_whose_folder_exists(self):
        """Final wave S1: a core folder gone since stack-core ran (a cache cleaned, a folder
        moved) is not given to the sub-agents; a note names it."""
        present = self.run_dir / "core"
        present.mkdir()
        gone = self.run_dir / "gone"
        (self.run_dir / "fix" / "stack-core.json").write_text(json.dumps({
            "front": {"stack": "wordpress", "version": "6.5.2", "path": str(present),
                      "source": "cache"},
            "back": {"stack": "drupal", "version": "10.2", "path": str(gone), "source": "path"},
        }), encoding="utf-8")
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(list(self.state()["stack_core"]), ["front"])
        self.assertEqual(json.loads(out)["notes"], [
            f"Cœur drupal du repo back introuvable ({gone}) : non repris, relancez cqa_fix.py "
            "stack-core."])
        self.finish()
        (self.run_dir / "fix" / "stack-core.json").write_text(json.dumps({
            "back": {"stack": "drupal", "version": "10.2", "path": str(gone), "source": "path"},
        }), encoding="utf-8")
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertNotIn("stack_core", self.state())
        self.assertEqual(len(json.loads(out)["notes"]), 1)

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

    def test_an_invalid_left_record_exits_6(self):
        """A key left to the lead is key["left"] = {"reason", "at"} (cqa_fix.py leave), or null."""
        self.assertEqual(self.open()[0], 0)
        state = self.state()
        for left in ({"reason": ""}, {"reason": "x"}, {"at": "2026-10-30T10:00:00Z"}, "x", [],
                     {"reason": "x", "at": 3}):
            with self.subTest(left=left):
                state["keys"]["front"]["left"] = left
                (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
                code, out, err = self.run_main(["stage", self.run_dir, "notion"])
                self.assertEqual(code, 6)
                self.assertIn("« left »", err)
        state["keys"]["front"]["left"] = {"reason": "commit de fusion", "at": "2026-10-30T10:00:00Z"}
        (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 0)
        del state["keys"]["front"]["left"]
        (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_main(["stage", self.run_dir, "notion"])[0], 0)

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


class MergedTest(FixCase):
    """merged: which rows « En review » have their MR merged and deployed in preprod."""

    DEPLOYED = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        origin = self.root / "shop.git"
        self.repo = self.root / "shop"
        real_git(self.root, "init", "-q", "--bare", str(origin))
        real_git(self.root, "clone", "-q", str(origin), str(self.repo))
        real_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/develop")
        self.base = commit(self.repo, "a.txt", "base")
        self.merge = commit(self.repo, "b.txt", "merged fix")
        self.later = commit(self.repo, "c.txt", "later")
        real_git(self.repo, "push", "-q", "origin", "develop")
        project = self.root / "proj"
        self.run_dir = project / "20261030-run"
        (self.run_dir / "fix").mkdir(parents=True)
        self.write_run(self.run_dir, [
            {"ref": 7, "status": "En review"}, {"ref": 8, "status": "Fait"}], {})

    def write_run(self, run_dir, rows, links):
        (run_dir / "rows.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
        (run_dir / "context.json").write_text(json.dumps({"repos": [
            {"name": "shop", "path": str(self.repo), "remote": REMOTE,
             "preprod_branch": "develop"}]}), encoding="utf-8")
        (run_dir / "fix" / "mr-links.json").write_text(
            json.dumps({"links": links}), encoding="utf-8")

    def link(self, ref, iid, repo="shop"):
        url = f"https://gitlab.example.org/acme/{repo}/-/merge_requests/{iid}"
        return {str(ref): [{"repo": repo, "url": url, "iid": iid}]}

    def glab(self, mrs, deployed):
        def run(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            if command[0] != "glab":
                raise AssertionError(command)
            api = command[2]
            if "merge_requests/" in api:
                iid = int(api.rsplit("/", 1)[1])
                if iid not in mrs:
                    return subprocess.CompletedProcess(command, 1, "", "404")
                return subprocess.CompletedProcess(command, 0, json.dumps(mrs[iid]), "")
            if api.endswith("/environments"):
                return subprocess.CompletedProcess(
                    command, 0, json.dumps([{"name": "preprod"}]), "")
            if "/deployments" in api and deployed:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps([{"sha": deployed}]), "")
            return subprocess.CompletedProcess(command, 1, "", "")
        return run

    def merged(self, run):
        code, out, err = self.run_main(["merged", self.run_dir], run=run)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_merged_and_deployed_rows_are_candidates(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}}, self.later)
        result = self.merged(run)
        self.assertEqual(result["merged_deployed"], [7])
        self.assertEqual((result["merged_waiting"], result["closed"], result["open"]), ([], [], []))

    def test_merged_not_deployed_waits(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}}, self.base)
        result = self.merged(run)
        self.assertEqual(result["merged_deployed"], [])
        self.assertEqual([item["ref"] for item in result["merged_waiting"]], [7])
        self.assertIn("préprod", result["merged_waiting"][0]["why"])

    def test_closed_mr_is_listed(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        result = self.merged(self.glab({5: {"state": "closed", "sha": self.merge}}, self.later))
        self.assertEqual(result["closed"], [7])
        self.assertEqual(result["merged_deployed"], [])

    def test_open_mr_is_ignored(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"},
                                      {"ref": 8, "status": "Fait"}],
                       {**self.link(7, 5), **self.link(8, 6)})
        mrs = {5: {"state": "opened", "sha": self.merge},
               6: {"state": "merged", "merge_commit_sha": self.merge}}
        result = self.merged(self.glab(mrs, self.later))
        self.assertEqual(result["open"], [7])
        self.assertEqual((result["merged_deployed"], result["merged_waiting"], result["closed"]),
                         ([], [], []))

    def test_fast_forward_merge_uses_the_head_sha(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        run = self.glab({5: {"state": "merged", "merge_commit_sha": None,
                             "squash_commit_sha": None, "sha": self.merge}}, self.later)
        self.assertEqual(self.merged(run)["merged_deployed"], [7])

    def test_row_with_two_mrs_needs_every_one_deployed(self):
        links = {"7": [
            {"repo": "shop", "url": "https://gitlab.example.org/acme/shop/-/merge_requests/5",
             "iid": 5},
            {"repo": "other", "url": "https://gitlab.example.org/acme/other/-/merge_requests/9",
             "iid": 9},
        ]}
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], links)
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge},
                         9: {"state": "merged", "merge_commit_sha": self.merge}}, self.later)
        result = self.merged(run)
        self.assertEqual(result["merged_deployed"], [])
        self.assertEqual([item["ref"] for item in result["merged_waiting"]], [7])
        self.assertIn("other", result["merged_waiting"][0]["why"])

    def test_row_with_mrs_in_two_repos(self):
        links = {"7": [
            {"repo": "shop", "url": "https://gitlab.example.org/acme/shop/-/merge_requests/5",
             "iid": 5},
            {"repo": "other", "url": "https://gitlab.example.org/acme/other/-/merge_requests/9",
             "iid": 9},
        ]}
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], links)
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge},
                         9: {"state": "closed"}}, self.later)
        result = self.merged(run)
        self.assertEqual(result["closed"], [7])
        self.assertEqual(result["merged_deployed"], [])

    def test_newest_run_links_the_ref(self):
        older = self.run_dir.parent / "20260101-run"
        (older / "fix").mkdir(parents=True)
        self.write_run(older, [], self.link(7, 1))
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge},
                         1: {"state": "closed"}}, self.later)
        self.assertEqual(self.merged(run)["merged_deployed"], [7])

    def test_glab_unavailable_never_deploys(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))

        def run(command, **kwargs):
            if command[0] == "glab":
                raise FileNotFoundError("glab")
            return subprocess.run(command, **kwargs)

        result = self.merged(run)
        self.assertEqual(result["merged_deployed"], [])
        self.assertEqual([item["ref"] for item in result["merged_waiting"]], [7])
        self.assertTrue(any("glab" in note for note in result["notes"]))

    def test_v2_links_file_reads(self):
        (self.run_dir / "fix" / "mr-links.json").write_text(json.dumps(
            {"links": {"7": [{"url": "https://gitlab.example.org/acme/shop/-/merge_requests/5"}]}}),
            encoding="utf-8")
        run = self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}}, self.later)
        self.assertEqual(self.merged(run)["merged_deployed"], [7])

    def test_unknown_merge_commit_waits(self):
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        unknown = "0123456789abcdef0123456789abcdef01234567"
        result = self.merged(self.glab({5: {"state": "merged", "merge_commit_sha": unknown}},
                                       self.later))
        self.assertEqual([item["ref"] for item in result["merged_waiting"]], [7])

    def test_unknown_deployed_sha_waits(self):
        """Final wave R1: no deployment read from GitLab: the row waits, with the reason."""
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        result = self.merged(self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}},
                                       None))
        self.assertEqual(result["merged_deployed"], [])
        self.assertEqual([(item["ref"], item["why"]) for item in result["merged_waiting"]],
                         [(7, "shop : commit déployé en préprod inconnu")])

    def test_a_repo_absent_from_the_context_waits_with_a_note(self):
        """Final wave R1: a link to a repo the context does not know cannot be checked locally."""
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}],
                       self.link(7, 9, repo="other"))
        result = self.merged(self.glab({9: {"state": "merged", "merge_commit_sha": self.merge}},
                                       self.later))
        self.assertEqual([(item["ref"], item["why"]) for item in result["merged_waiting"]],
                         [(7, "other : repo local inconnu")])
        self.assertIn("other : repo absent de context.json, déploiement non vérifié.",
                      result["notes"])

    def test_a_link_without_repo_is_matched_on_host_and_project_path(self):
        """Final wave R1: an entry without « repo » (v2 file) is tied to a repo of the context
        only when its URL has the repo's host and exact project path; a URL of another host or
        of a path that merely contains it names no local repo."""
        same = "https://gitlab.example.org/acme/shop/-/merge_requests/5"
        other_host = "https://gitlab.other.example.org/acme/shop/-/merge_requests/6"
        longer_path = "https://gitlab.example.org/legacy/acme/shop/-/merge_requests/8"
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"},
                                      {"ref": 8, "status": "En review"},
                                      {"ref": 9, "status": "En review"}],
                       {"7": [{"url": same}], "8": [{"url": other_host}], "9": [{"url": longer_path}]})
        mrs = {iid: {"state": "merged", "merge_commit_sha": self.merge} for iid in (5, 6, 8)}
        result = self.merged(self.glab(mrs, self.later))
        self.assertEqual(result["merged_deployed"], [7])
        self.assertEqual([(item["ref"], item["why"]) for item in result["merged_waiting"]],
                         [(8, "repo inconnu : repo local inconnu"),
                          (9, "repo inconnu : repo local inconnu")])

    def test_a_row_in_review_without_any_link_is_noted(self):
        """Final wave R1: a row « En review » that no run links to a merge request is said."""
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"},
                                      {"ref": 8, "status": "En review"}], self.link(8, 5))
        result = self.merged(self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}},
                                       self.later))
        self.assertEqual(result["merged_deployed"], [8])
        self.assertIn("CQ-7 : « En review » sans MR connue dans les runs du projet : "
                      "non vérifiée.", result["notes"])

    def test_a_failed_fetch_is_noted(self):
        """Final wave R1: the deployment is still judged on the local refs, and a note says
        that git fetch origin failed."""
        self.write_run(self.run_dir, [{"ref": 7, "status": "En review"}], self.link(7, 5))
        real_git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))
        result = self.merged(self.glab({5: {"state": "merged", "merge_commit_sha": self.merge}},
                                       self.later))
        self.assertEqual(result["merged_deployed"], [7])
        self.assertIn("shop : git fetch origin a échoué : déploiement jugé sur les refs locales.",
                      result["notes"])


class LauncherFake:
    """Answers the launcher calls of the commands command (ci-json, image-present) without
    Docker. ci maps a repo path to the document ci-json writes (absent: exit 3)."""

    def __init__(self, ci=None, missing=(), ci_code=0, ci_error="", image_code=None):
        self.ci = ci or {}
        self.missing = set(missing)
        self.ci_code, self.ci_error, self.image_code = ci_code, ci_error, image_code
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if command[:2] != ["bash", str(cqa_fix.LAUNCHER)]:
            raise AssertionError(f"unexpected call: {command}")
        if command[2] == "ci-json":
            if self.ci_code != 0:
                return subprocess.CompletedProcess(command, self.ci_code, "", self.ci_error)
            if command[3] not in self.ci:
                return subprocess.CompletedProcess(command, 3, "", "ci-json : introuvable\n")
            Path(command[4]).write_text(json.dumps(self.ci[command[3]]), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[2] == "image-present":
            code = self.image_code if self.image_code is not None else (
                1 if command[3] in self.missing else 0)
            return subprocess.CompletedProcess(command, code, "", "")
        raise AssertionError(f"unexpected launcher command: {command}")

    def images(self):
        return [call[3] for call in self.calls if call[2] == "image-present"]

    def ci_calls(self):
        return [call for call in self.calls if call[2] == "ci-json"]


def write_file(root, path, content):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content),
                      encoding="utf-8")


def tree(root):
    return sorted(str(path.relative_to(root)) for path in Path(root).rglob("*"))


class CommandsTest(FixCase):
    PINNED = "node:18-alpine@sha256:" + "cd34" * 16
    CI = {
        "stages": ["build", "deploy"],
        "build:npm": {"stage": "build", "image": PINNED, "variables": {"NPM_DIRECTORY": "web/assets"},
                      "before_script": "cd $NPM_DIRECTORY",
                      "script": ["npm ci", "npx gulp production", "cd $CI_PROJECT_DIR"]},
        "deploy": {"stage": "deploy", "image": "node:18-alpine", "script": ["npm run deploy"]},
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.run_dir = root / "run"
        (self.run_dir / "fix").mkdir(parents=True)
        self.front, self.back = root / "front", root / "back"
        write_file(self.front, ".gitlab-ci.yml", "stages: [build]\n")
        write_file(self.front, ".nvmrc", "20\n")
        write_file(self.front, "package.json", {"scripts": {"lint": "eslint ."}})
        write_file(self.front, "package-lock.json", "{}")
        write_file(self.back, "composer.json", {"scripts": {"test": "phpunit"}})
        self.write_context()
        rows = [{"ref": 3, "title": "Trois"}]
        candidates = {"keys": [
            {"key": name, "repo": name, "push_ready": False, "sections": [
                {"category": "SÉCURITÉ", "alias": "securite", "rows": rows}]}
            for name in ("front", "back")], "left": [], "flights": {}, "notes": [], "estimate_min": 1}
        write_file(self.run_dir, "fix/candidates.json", candidates)

    def write_context(self, **extra_back):
        write_file(self.run_dir, "context.json", {
            "project": {"slug": "boutique", "name": "Boutique Exemple", "protection": None},
            "repos": [{"name": "front", "path": str(self.front)},
                      {"name": "back", "path": str(self.back), **extra_back}]})

    def commands(self, *options, launcher=None):
        self.launcher = launcher or LauncherFake({str(self.front): self.CI}, missing={"composer:2"})
        code, out, err = self.run_main(["commands", self.run_dir, *options], run=self.launcher)
        return code, out, err

    def listed(self):
        return json.loads((self.run_dir / "fix" / "commands.json").read_text(encoding="utf-8"))

    def open(self):
        return self.run_main(["open", self.run_dir, "--mode", "present", "--push", "mr",
                              "--notion", "no"])

    def state(self):
        return json.loads((self.run_dir / "fix" / "state.json").read_text(encoding="utf-8"))

    def test_commands_writes_the_list_and_open_carries_it(self):
        before = tree(self.front), tree(self.back)
        code, out, err = self.commands(launcher=None)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result, self.listed())
        self.assertEqual(list(result), ["repos", "images"])
        front, back = result["repos"]["front"], result["repos"]["back"]
        self.assertEqual([(item["name"], item["kind"], item["dir"]) for item in front["commands"]], [
            ("build:npm (install)", "install", ""), ("build:npm", "build", ""),
            ("npm ci", "install", ""), ("npm run lint", "lint", "")])
        self.assertEqual(front["commands"][1], {
            "name": "build:npm", "dir": "", "image": self.PINNED, "kind": "build",
            "command": "cd $NPM_DIRECTORY && npx gulp production && cd /work",
            "env": {"NPM_DIRECTORY": "web/assets"}, "source": ".gitlab-ci.yml (build:npm)",
            "private": False})
        self.assertEqual(front["commands"][3]["image"], "node:20-alpine")
        self.assertEqual(len(front["notes"]), 1)
        self.assertIn("deploy", front["notes"][0])
        self.assertEqual([(item["name"], item["image"], item["dir"]) for item in back["commands"]], [
            ("composer install", "composer:2", ""), ("composer test", "composer:2", "")])
        self.assertEqual(back["notes"], [])
        self.assertEqual(result["images"], ["composer:2"])
        self.assertEqual(sorted(self.launcher.images()),
                         sorted(["composer:2", "node:20-alpine", self.PINNED]))
        ci_calls = self.launcher.ci_calls()
        self.assertEqual(len(ci_calls), 1)
        self.assertEqual(ci_calls[0][3], str(self.front))
        self.assertTrue(Path(ci_calls[0][4]).is_relative_to(self.run_dir))
        self.assertEqual((tree(self.front), tree(self.back)), before)
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["check_commands"],
                         {"front": front["commands"], "back": back["commands"]})

    def test_commands_drop_add_and_none(self):
        code, out, err = self.commands(
            "--drop", "npm run lint", "--drop", "build:npm (install)",
            "--add", "front|web|node:20-alpine|test|npm test",
            "--add", "back||composer:2|lint|composer cs | tee log")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result, self.listed())
        front, back = result["repos"]["front"]["commands"], result["repos"]["back"]["commands"]
        self.assertEqual([item["name"] for item in front],
                         ["build:npm", "npm ci", "ajout : npm test"])
        self.assertEqual(front[2], {
            "name": "ajout : npm test", "dir": "web", "image": "node:20-alpine", "kind": "test",
            "command": "npm test", "env": {}, "source": "ajouté par le lead", "private": False})
        self.assertEqual((back[2]["kind"], back[2]["command"]), ("lint", "composer cs | tee log"))
        self.assertEqual(result["images"], ["composer:2"])
        code, out, err = self.commands("--none")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {
            "repos": {"front": {"commands": [], "notes": []}, "back": {"commands": [], "notes": []}},
            "images": []})
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.listed(), json.loads(out))
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["check_commands"], {"front": [], "back": []})

    def test_commands_drop_a_name_everywhere_or_in_one_repo(self):
        write_file(self.back, "package.json", {"scripts": {"lint": "eslint ."}})
        write_file(self.back, "package-lock.json", "{}")

        def names(result, repo):
            return [item["name"] for item in result["repos"][repo]["commands"]]

        code, out, err = self.commands("--drop", "back|npm ci", "--drop", "front|npm run lint")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(names(result, "front"), ["build:npm (install)", "build:npm", "npm ci"])
        self.assertEqual(names(result, "back"), ["npm run lint", "composer install", "composer test"])
        code, out, err = self.commands("--drop", "npm ci")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertNotIn("npm ci", names(result, "front") + names(result, "back"))
        kept = self.listed()
        for value in ("back|npm nothing", "nope|npm ci", "back|build:npm"):
            with self.subTest(drop=value):
                code, out, err = self.commands("--drop", value)
                self.assertEqual(code, 2, out)
                self.assertIn(value, err)
        self.assertEqual(self.listed(), kept)

    def test_commands_refuses_unknown_drops_and_malformed_additions(self):
        self.assertEqual(self.commands()[0], 0)
        kept = self.listed()
        code, out, err = self.commands("--drop", "npm run nothing")
        self.assertEqual(code, 2)
        self.assertIn("npm run nothing", err)
        self.assertIn("npm ci", err)
        for value in ("front|web|node:20-alpine|test", "nope||node:20-alpine|test|npm test",
                      "front||node:20-alpine|deploy|npm test", "front|/abs|node:20-alpine|test|x",
                      "front|../x|node:20-alpine|test|x", "front||-x|test|x",
                      "front||node:20-alpine|test|   ", "front|a:b|node:20-alpine|test|x",
                      "front||a b|test|x", ""):
            with self.subTest(add=value):
                code, out, err = self.commands("--add", value)
                self.assertEqual(code, 2, (value, out))
                self.assertTrue(err.strip())
        code, out, err = self.commands("--none", "--add", "front||node:20-alpine|test|npm test")
        self.assertEqual(code, 2)
        code, out, err = self.commands("--none", "--drop", "npm ci")
        self.assertEqual(code, 2)
        self.assertEqual(self.listed(), kept)

    def test_commands_notes_a_ci_that_cannot_be_read_and_goes_on_with_the_manifests(self):
        for code_, error in ((1, "ci-json : .gitlab-ci.yml est invalide : bad indentation\n"),
                             (2, "L'image agence de la boîte à outils est absente (x)\n")):
            with self.subTest(code=code_):
                code, out, err = self.commands(launcher=LauncherFake(ci_code=code_, ci_error=error))
                self.assertEqual(code, 0, err)
                front = json.loads(out)["repos"]["front"]
                self.assertEqual([item["name"] for item in front["commands"]],
                                 ["npm ci", "npm run lint"])
                self.assertEqual(len(front["notes"]), 1)
                self.assertIn(".gitlab-ci.yml non lu", front["notes"][0])
                self.assertIn(error.splitlines()[0], front["notes"][0])

    def test_commands_without_docker_lists_no_image_and_says_so(self):
        code, out, err = self.commands(
            launcher=LauncherFake({str(self.front): self.CI}, image_code=2))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["images"], [])
        for name in ("front", "back"):
            self.assertIn("non vérifiée", " ".join(result["repos"][name]["notes"]))
        self.assertEqual(len(self.launcher.images()), 1)

    def test_commands_skips_third_party_paths_and_missing_folders(self):
        write_file(self.back, "vendor-libs/composer.json", {"scripts": {"test": "x"}})
        self.assertEqual(self.commands()[0], 0)
        self.assertEqual({item["dir"] for item in self.listed()["repos"]["back"]["commands"]},
                         {"", "vendor-libs"})
        self.write_context(third_party_paths=["vendor-libs"])
        self.assertEqual(self.commands()[0], 0)
        self.assertEqual({item["dir"] for item in self.listed()["repos"]["back"]["commands"]}, {""})
        write_file(self.run_dir, "context.json", {"repos": [
            {"name": "back", "path": str(self.back), "third_party_paths": "vendor-libs"}]})
        self.assertEqual(self.commands()[0], 6)
        write_file(self.run_dir, "context.json", {"repos": [
            {"name": "front", "path": str(self.front)},
            {"name": "gone", "path": str(self.front.parent / "gone")}]})
        code, out, err = self.commands()
        self.assertEqual(code, 0, err)
        gone = json.loads(out)["repos"]["gone"]
        self.assertEqual(gone["commands"], [])
        self.assertIn("introuvable", gone["notes"][0])

    def test_commands_needs_a_valid_context(self):
        (self.run_dir / "context.json").unlink()
        self.assertEqual(self.commands()[0], 6)
        write_file(self.run_dir, "context.json", {"repos": []})
        self.assertEqual(self.commands()[0], 6)
        write_file(self.run_dir, "context.json", {"repos": [{"name": "front"}]})
        self.assertEqual(self.commands()[0], 6)
        self.assertFalse((self.run_dir / "fix" / "commands.json").exists())

    def test_open_without_commands_file_keeps_an_empty_list(self):
        self.assertFalse((self.run_dir / "fix" / "commands.json").exists())
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["check_commands"], {"front": [], "back": []})

    def test_open_refuses_a_commands_file_it_cannot_trust_before_touching_anything(self):
        path = self.run_dir / "fix" / "commands.json"
        good = {"name": "npm ci", "dir": "", "image": "node:20-alpine", "kind": "install",
                "command": "npm ci", "env": {}, "source": "package.json", "private": False}
        for content in ("{broken", "[]", json.dumps({"repos": []}),
                        json.dumps({"repos": {"front": {"commands": "npm ci"}}}),
                        json.dumps({"repos": {"front": {"commands": [{**good, "kind": "deploy"}]}}}),
                        json.dumps({"repos": {"front": {"commands": [{**good, "image": "-x"}]}}})):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                code, out, err = self.open()
                self.assertEqual(code, 2, out)
                self.assertIn("commands.json", err)
                self.assertFalse((self.run_dir / "fix" / "state.json").exists())
        path.write_text(json.dumps({"repos": {"front": {"commands": [good], "notes": []}}}),
                        encoding="utf-8")
        code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["check_commands"], {"front": [good], "back": []})

    def test_a_state_without_check_commands_is_still_read(self):
        self.assertEqual(self.open()[0], 0)
        state = self.state()
        del state["check_commands"]
        write_file(self.run_dir, "fix/state.json", state)
        self.assertEqual(fix_state.check_commands(fix_state.load(self.run_dir), "front"), [])
        code, out, err = self.run_main(["stage", self.run_dir, "questions"])
        self.assertEqual(code, 0, err)

    def test_an_invalid_check_commands_record_exits_6(self):
        self.assertEqual(self.open()[0], 0)
        good = {"name": "npm ci", "dir": "", "image": "node:20-alpine", "kind": "install",
                "command": "npm ci", "env": {}, "source": "package.json", "private": False}
        for value in ([], "x", {"front": "x"}, {"front": [{"name": 1}]}, {"front": [[]]},
                      {"front": [{**good, "image": "a b"}]}, {"bad name": []},
                      {"front": [{**good, "dir": ".."}]}):
            with self.subTest(value=value):
                state = self.state()
                state["check_commands"] = value
                (self.run_dir / "fix" / "state.json").write_text(json.dumps(state), encoding="utf-8")
                code, out, err = self.run_main(["stage", self.run_dir, "questions"])
                self.assertEqual(code, 6, err)
                self.assertIn("check_commands", err)
        state = self.state()
        state["check_commands"] = {"front": [good]}
        write_file(self.run_dir, "fix/state.json", state)
        self.assertEqual(fix_state.check_commands(fix_state.load(self.run_dir), "front"), [good])
        self.assertEqual(fix_state.check_commands(fix_state.load(self.run_dir), "back"), [])


class RegistrationTest(FixCase):
    def test_every_fix_command_is_registered(self):
        for name in ("doctor", "candidates", "open", "stage", "start", "section-start",
                     "section-end", "pass-start", "validate", "guard", "review-scope", "waive-secret", "drop",
                     "reorder", "reword", "leave",
                     "check", "mr-body", "publish", "finish", "summary", "abandon", "merged", "stack-core"):
            code, out, err = self.run_main([name, "--help"])
            self.assertEqual(code, 0, name)


if __name__ == "__main__":
    unittest.main()
