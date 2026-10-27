import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cqa-toolbox.sh"
STUBS = Path(__file__).resolve().parent / "stubs"
RUN_ID = "2026-10-20-1430"
REGISTRY = "registry.gitlab.gingerminds.fr/gm/tools/checklist-qualite-outillage-audit"
TARGET = "https://preprod.example.org"
SECRETS = {
    "GM_AUDIT_AUTH_USER": "cqa-user",
    "GM_AUDIT_AUTH_PASS": "pass-xyz-123",
    "GM_SF_LICENCE_USER": "sf-user",
    "GM_SF_LICENCE_KEY": "key-sf-456",
}
UNSAFE_METHODS = "^(?!(GET|HEAD|OPTIONS)$)"
IMAGES = ["agence", "screamingfrog", "nu", "testssl", "gitleaks", "redocly", "schemathesis"]
ALL_ORDER = [
    ("agence", "http --probes"),
    ("gitleaks", "git /repos/laravel"),
    ("gitleaks", "git /repos/nuxt"),
    ("agence", "summarize gitleaks"),
    ("agence", "assets --repos"),
    ("agence", "lighthouse --pages"),
    ("agence", "a11y --pages"),
    ("agence", "nu --pages"),
    ("agence", "yellowlab --pages"),
    ("testssl", "--quiet --color"),
    ("agence", "summarize testssl"),
    ("redocly", "lint /repos/laravel/docs/swagger v2.json"),
    ("redocly", "lint /repos/laravel/openapi.yaml"),
    ("agence", "summarize redocly"),
    ("schemathesis", "run /repos/laravel/docs/swagger v2.json"),
    ("schemathesis", "run /repos/laravel/openapi.yaml"),
    ("agence", "summarize schemathesis"),
    ("screamingfrog", ""),
    ("agence", "summarize crawl"),
]


def split_run(call):
    """Return (options, image name, arguments) of a docker call that names a toolbox image."""
    for index, arg in enumerate(call):
        if arg.startswith(REGISTRY + "/"):
            name = arg[len(REGISTRY) + 1 :].split(":")[0]
            return call[:index], name, call[index + 1 :]
    return None


def has_pair(items, first, second):
    return any(items[i] == first and items[i + 1] == second for i in range(len(items) - 1))


def env_args(options):
    return [options[i + 1] for i in range(len(options) - 1) if options[i] == "--env"]


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "docker.log"
        self.laravel = self.tmp / "Acme - Shop - Laravel"
        self.nuxt = self.tmp / "Acme - Shop - Nuxt"
        for repo in (self.laravel, self.nuxt):
            repo.mkdir()
            (repo / ".git").mkdir()
        (self.laravel / "openapi.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
        (self.laravel / "docs").mkdir()
        (self.laravel / "docs" / "swagger v2.json").write_text("{}", encoding="utf-8")
        self.run_dir = self.make_run()

    def make_run(self, *, target=TARGET, repos=None, parent=None):
        run_dir = (parent or self.tmp) / RUN_ID
        run_dir.mkdir(parents=True)
        repos = repos or [("laravel", str(self.laravel)), ("nuxt", str(self.nuxt))]
        context = {
            "target": {"preprod_url": target, "prod_url": None, "prod_live": False},
            "repos": [{"name": name, "path": path} for name, path in repos],
        }
        (run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
        (run_dir / "probes.json").write_text("{}", encoding="utf-8")
        (run_dir / "probes-followup.json").write_text("{}", encoding="utf-8")
        return run_dir

    def environment(self, path=None, **extra):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GM_", "CQA_"))
        }
        env["PATH"] = path or str(STUBS) + os.pathsep + env["PATH"]
        env["HOME"] = str(self.tmp)
        env["CQA_STUB_LOG"] = str(self.log)
        env.update(extra)
        return env

    def launch(self, *args, path=None, script=SCRIPT, cwd=None, **extra):
        return subprocess.run(
            [shutil.which("bash"), str(script), *args],
            env=self.environment(path, **extra),
            cwd=cwd or self.tmp,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def tool_runs(self):
        """Return (options, image, arguments) of each tool container started by the launcher."""
        found = []
        for call in self.calls():
            parts = split_run(call)
            if parts and "--rm" in call and "--user" in call:
                found.append(parts)
        return found

    def runs_of(self, image):
        return [(opts, args) for opts, name, args in self.tool_runs() if name == image]

    def read_json(self, relative):
        return json.loads((self.run_dir / relative).read_text(encoding="utf-8"))


class CommonOptionsTest(LauncherCase):
    def test_user_and_mounts(self):
        result = self.launch("run", str(self.run_dir), "http")
        self.assertEqual(0, result.returncode, result.stderr)
        (options, args), = self.runs_of("agence")
        self.assertEqual(f"{os.getuid()}:{os.getgid()}", options[options.index("--user") + 1])
        self.assertIn("--rm", options)
        self.assertTrue(has_pair(options, "--env", "HOME=/tmp"))
        self.assertTrue(has_pair(options, "--tmpfs", "/tmp"))
        self.assertTrue(has_pair(options, "-v", f"{self.run_dir}:/in:ro"))
        self.assertTrue(has_pair(options, "-v", f"{self.run_dir}/tools:/out"))
        self.assertTrue(has_pair(options, "-v", f"{self.laravel}:/repos/laravel:ro"))
        self.assertTrue(has_pair(options, "-v", f"{self.nuxt}:/repos/nuxt:ro"))
        self.assertEqual(["http", "--probes", "/in/probes.json"], args)

    def test_cdpath_does_not_divert_relative_paths(self):
        decoy = self.tmp / "decoy"
        (decoy / RUN_ID).mkdir(parents=True)
        (decoy / "scripts").mkdir()
        result = self.launch("run", RUN_ID, "http", CDPATH=str(decoy))
        self.assertEqual(0, result.returncode, result.stderr)
        (options, _), = self.runs_of("agence")
        self.assertTrue(has_pair(options, "-v", f"{self.run_dir}:/in:ro"))
        self.log.unlink()
        result = self.launch(
            "run", str(self.run_dir), "http",
            script="scripts/cqa-toolbox.sh", cwd=ROOT, CDPATH=str(decoy),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len(self.runs_of("agence")))

    def test_image_ref_default_and_override(self):
        self.launch("run", str(self.run_dir), "assets")
        (_, image, _), = self.tool_runs()
        self.assertEqual("agence", image)
        self.assertIn(f"{REGISTRY}/agence:1", self.calls()[0])
        self.log.unlink()
        self.launch("run", str(self.run_dir), "assets", GM_CQA_TOOLBOX_TAG="1.2")
        self.assertIn(f"{REGISTRY}/agence:1.2", self.calls()[0])
        self.log.unlink()
        self.launch(
            "run",
            str(self.run_dir),
            "assets",
            GM_CQA_REGISTRY="registry.example.org/team/",
            GM_CQA_TOOLBOX_TAG="2",
        )
        self.assertIn("registry.example.org/team/agence:2", self.calls()[0])

    def test_env_passed_by_name_only(self):
        self.launch("run", str(self.run_dir), "http", **SECRETS)
        (options, _), = self.runs_of("agence")
        for name in (
            "GM_AUDIT_TARGET",
            "GM_AUDIT_RATE",
            "GM_AUDIT_USER_AGENT",
            "GM_AUDIT_ROBOTS",
            "GM_AUDIT_AUTH_USER",
            "GM_AUDIT_AUTH_PASS",
        ):
            self.assertTrue(has_pair(options, "--env", name), name)
        self.assertNotIn("GM_SF_LICENCE_KEY", options)
        for value in (TARGET, "ignore", "GingermindsAudit/"):
            self.assertFalse(any(value in arg for arg in options), value)

    def test_unset_credentials_are_not_passed(self):
        self.launch("run", str(self.run_dir), "http")
        (options, _), = self.runs_of("agence")
        self.assertTrue(has_pair(options, "--env", "GM_AUDIT_TARGET"))
        self.assertNotIn("GM_AUDIT_AUTH_USER", options)
        self.assertNotIn("GM_AUDIT_AUTH_PASS", options)

    def test_only_useful_env_per_tool(self):
        self.launch("run", str(self.run_dir), "assets", **SECRETS)
        (options, _), = self.runs_of("agence")
        self.assertEqual(["HOME=/tmp"], env_args(options))
        self.log.unlink()
        self.launch("run", str(self.run_dir), "crawl", **SECRETS)
        (options, args), = self.runs_of("screamingfrog")
        self.assertEqual([], args)
        for name in ("GM_AUDIT_TARGET", "GM_AUDIT_ROBOTS", "GM_SF_LICENCE_USER", "GM_SF_LICENCE_KEY"):
            self.assertTrue(has_pair(options, "--env", name), name)
        self.assertTrue(has_pair(options, "--env", "GM_AUDIT_AUTH_USER"))
        self.assertNotIn("GM_AUDIT_AUTH_PASS", options)
        self.assertNotIn("GM_AUDIT_RATE", options)

    def test_secret_values_never_in_args(self):
        result = self.launch("all", str(self.run_dir), **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        text = self.log.read_text(encoding="utf-8")
        for value in SECRETS.values():
            self.assertNotIn(value, text)
            self.assertNotIn(value, result.stdout)
            self.assertNotIn(value, result.stderr)
        self.assertIn('"--env", "GM_AUDIT_AUTH_PASS"', text)
        self.assertIn('"--env", "GM_SF_LICENCE_KEY"', text)

    def test_stale_output_is_wiped_and_raw_is_created(self):
        stale = self.run_dir / "tools" / "testssl"
        (stale / "raw").mkdir(parents=True)
        (stale / "raw" / "old.json").write_text("{}", encoding="utf-8")
        (stale / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
        self.launch("run", str(self.run_dir), "testssl")
        self.assertTrue((stale / "raw").is_dir())
        self.assertFalse((stale / "raw" / "old.json").exists())
        self.assertFalse((stale / "result.json").exists())
        self.launch("run", str(self.run_dir), "http")
        self.assertTrue((self.run_dir / "tools" / "http" / "raw").is_dir())


class ToolCommandsTest(LauncherCase):
    def run_tool(self, tool, *flags, **extra):
        result = self.launch("run", str(self.run_dir), tool, *flags, **extra)
        return result

    def test_http_followup(self):
        result = self.run_tool("http", "--followup")
        self.assertEqual(0, result.returncode, result.stderr)
        (_, args), = self.runs_of("agence")
        self.assertEqual(
            ["http", "--probes", "/in/probes-followup.json", "--name", "http-followup"], args
        )
        self.assertTrue((self.run_dir / "tools" / "http-followup" / "raw").is_dir())

    def test_followup_only_for_http(self):
        result = self.run_tool("lighthouse", "--followup")
        self.assertEqual(2, result.returncode)
        self.assertEqual([], self.calls())

    def test_pages_tools(self):
        for tool in ("lighthouse", "a11y", "yellowlab"):
            self.run_tool(tool)
        self.assertEqual(
            [[tool, "--pages", "/in/context.json"] for tool in ("lighthouse", "a11y", "yellowlab")],
            [args for _, args in self.runs_of("agence")],
        )

    def test_assets(self):
        self.run_tool("assets")
        (_, args), = self.runs_of("agence")
        self.assertEqual(["assets", "--repos", "/repos"], args)

    def test_nu_network_cleanup(self):
        result = self.run_tool("nu")
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.calls()
        net, name = f"cqa-{RUN_ID}", f"cqa-nu-{RUN_ID}"
        create = calls.index(["network", "create", net])
        stale = [call for call in calls[:create] if call[:1] != ["image"]]
        self.assertEqual([["rm", "-f", name], ["network", "rm", net]], stale)
        service = next(i for i, call in enumerate(calls) if call[:2] == ["run", "-d"])
        self.assertEqual(
            ["run", "-d", "--rm", "--name", name, "--network", net, f"{REGISTRY}/nu:1"],
            calls[service],
        )
        audit = next(i for i, call in enumerate(calls) if "--user" in call)
        removal = calls.index(["rm", "-f", name], audit)
        network_removal = calls.index(["network", "rm", net], audit)
        self.assertTrue(create < service < audit < removal < network_removal)
        options, args = self.runs_of("agence")[0]
        self.assertEqual(["nu", "--pages", "/in/context.json"], args)
        self.assertTrue(has_pair(options, "--network", net))
        self.assertTrue(has_pair(options, "--env", f"GM_AUDIT_NU_URL=http://{name}:8888/"))

    def test_nu_cleanup_after_failure(self):
        result = self.run_tool("nu", CQA_STUB_FAIL="nu --pages", CQA_STUB_FAIL_CODE="3")
        self.assertEqual(3, result.returncode)
        calls = self.calls()
        self.assertIn(["rm", "-f", f"cqa-nu-{RUN_ID}"], calls)
        self.assertIn(["network", "rm", f"cqa-{RUN_ID}"], calls)

    def test_nu_stale_network_from_a_killed_run_is_removed_first(self):
        result = self.run_tool("nu", CQA_STUB_FAIL="network rm")
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.calls()
        create = calls.index(["network", "create", f"cqa-{RUN_ID}"])
        self.assertLess(calls.index(["network", "rm", f"cqa-{RUN_ID}"]), create)
        self.assertLess(calls.index(["rm", "-f", f"cqa-nu-{RUN_ID}"]), create)

    def test_nu_network_failure_starts_nothing(self):
        result = self.run_tool("nu", CQA_STUB_FAIL="network create")
        self.assertEqual(1, result.returncode)
        calls = self.calls()
        self.assertFalse(any(call[:2] == ["run", "-d"] for call in calls))
        self.assertIn(["network", "rm", f"cqa-{RUN_ID}"], calls)
        self.assertIn("nu", result.stderr)

    def test_testssl(self):
        result = self.run_tool("testssl")
        self.assertEqual(0, result.returncode, result.stderr)
        (_, args), = self.runs_of("testssl")
        self.assertEqual(
            [
                "--quiet",
                "--color",
                "0",
                "--jsonfile",
                "/out/testssl/raw/testssl.json",
                "preprod.example.org",
            ],
            args,
        )
        self.assertEqual(
            ["summarize", "testssl", "--raw", "/out/testssl/raw"], self.runs_of("agence")[0][1]
        )

    def test_testssl_keeps_the_port(self):
        self.run_dir = self.make_run(
            target="https://preprod.example.org:8443/shop", parent=self.tmp / "other"
        )
        self.launch("run", str(self.run_dir), "testssl")
        (_, args), = self.runs_of("testssl")
        self.assertEqual("preprod.example.org:8443", args[-1])

    def test_testssl_skipped_for_http_target(self):
        self.run_dir = self.make_run(target="http://preprod.example.org", parent=self.tmp / "other")
        result = self.run_tool("testssl")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.tool_runs())
        document = self.read_json("tools/testssl/result.json")
        self.assertEqual("skipped", document["status"])
        self.assertIn("HTTP", document["reason"])

    def test_gitleaks_per_repo(self):
        result = self.run_tool("gitleaks")
        self.assertEqual(0, result.returncode, result.stderr)
        scans = [args for _, args in self.runs_of("gitleaks")]
        self.assertEqual(
            [
                [
                    "git",
                    f"/repos/{name}",
                    "--redact=100",
                    "--report-format",
                    "json",
                    "--report-path",
                    f"/out/gitleaks/raw/{name}.json",
                    "--exit-code",
                    "0",
                ]
                for name in ("laravel", "nuxt")
            ],
            scans,
        )
        self.assertEqual(
            ["summarize", "gitleaks", "--raw", "/out/gitleaks/raw"], self.runs_of("agence")[0][1]
        )

    def gitleaks_scans(self):
        return {args[1].rsplit("/", 1)[-1]: args for _, args in self.runs_of("gitleaks")}

    def test_gitleaks_scans_files_when_the_repo_has_no_git_folder(self):
        shutil.rmtree(self.nuxt / ".git")
        result = self.run_tool("gitleaks")
        self.assertEqual(0, result.returncode, result.stderr)
        scans = self.gitleaks_scans()
        self.assertEqual("git", scans["laravel"][0])
        self.assertEqual(
            [
                "dir",
                "/repos/nuxt",
                "--redact=100",
                "--report-format",
                "json",
                "--report-path",
                "/out/gitleaks/raw/nuxt.json",
                "--exit-code",
                "0",
            ],
            scans["nuxt"],
        )
        self.assertEqual(1, len(self.runs_of("agence")))

    def test_gitleaks_keeps_git_mode_when_git_is_a_file(self):
        shutil.rmtree(self.nuxt / ".git")
        (self.nuxt / ".git").write_text("gitdir: ../elsewhere/.git\n", encoding="utf-8")
        result = self.run_tool("gitleaks")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"git"}, {args[0] for args in self.gitleaks_scans().values()})

    def test_gitleaks_announces_the_mode_of_each_repo(self):
        shutil.rmtree(self.nuxt / ".git")
        result = self.run_tool("gitleaks")
        lines = {
            name: next((line for line in result.stderr.splitlines() if f"« {name} »" in line), "")
            for name in ("laravel", "nuxt")
        }
        self.assertIn("historique git", lines["laravel"])
        self.assertIn("sans .git", lines["nuxt"])
        self.assertTrue(all(line.startswith("gitleaks :") for line in lines.values()), lines)

    def test_gitleaks_failure_skips_summary(self):
        result = self.run_tool("gitleaks", CQA_STUB_FAIL="git /repos/nuxt")
        self.assertEqual(1, result.returncode)
        self.assertEqual(2, len(self.runs_of("gitleaks")))
        self.assertEqual([], self.runs_of("agence"))
        self.assertIn("nuxt", result.stderr)

    def test_redocly_redirects_stdout_to_raw(self):
        result = self.run_tool("redocly")
        self.assertEqual(0, result.returncode, result.stderr)
        lints = [args for _, args in self.runs_of("redocly")]
        cap = ["--max-problems", "10000"]
        self.assertEqual(
            [
                ["lint", "/repos/laravel/docs/swagger v2.json", "--format", "json", *cap],
                ["lint", "/repos/laravel/openapi.yaml", "--format", "json", *cap],
            ],
            lints,
        )
        raw = self.run_dir / "tools" / "redocly" / "raw"
        self.assertEqual(["laravel-1.json", "laravel-2.json"], sorted(p.name for p in raw.iterdir()))
        self.assertEqual({"problems": []}, json.loads((raw / "laravel-1.json").read_text()))
        self.assertEqual(
            ["summarize", "redocly", "--raw", "/out/redocly/raw"], self.runs_of("agence")[0][1]
        )

    def test_redocly_is_not_capped_at_one_hundred_problems(self):
        self.run_tool("redocly")
        for _, args in self.runs_of("redocly"):
            self.assertTrue(has_pair(args, "--max-problems", "10000"), args)

    def test_redocly_findings_exit_code_does_not_stop_summary(self):
        result = self.run_tool("redocly", CQA_STUB_FAIL="lint", CQA_STUB_FAIL_CODE="1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(self.runs_of("redocly")))
        self.assertEqual(1, len(self.runs_of("agence")))

    def test_schemathesis(self):
        result = self.run_tool("schemathesis")
        self.assertEqual(0, result.returncode, result.stderr)
        runs = [args for _, args in self.runs_of("schemathesis")]
        tail = [
            "--url",
            TARGET,
            "--include-method",
            "GET",
            "--exclude-method-regex",
            UNSAFE_METHODS,
            "--exclude-checks",
            "unsupported_method",
            "--max-examples",
            "20",
            "--rate-limit",
            "2/s",
            "--report",
            "junit",
            "--report-dir",
            "/out/schemathesis/raw",
        ]
        self.assertEqual(
            [
                ["run", "/repos/laravel/docs/swagger v2.json", *tail],
                ["run", "/repos/laravel/openapi.yaml", *tail],
            ],
            runs,
        )
        self.assertEqual(
            ["summarize", "schemathesis", "--raw", "/out/schemathesis/raw"],
            self.runs_of("agence")[0][1],
        )

    def schemathesis_args(self):
        self.assertEqual(0, self.run_tool("schemathesis").returncode)
        return [args for _, args in self.runs_of("schemathesis")]

    def test_schemathesis_excludes_the_unsupported_method_check(self):
        for args in self.schemathesis_args():
            self.assertTrue(has_pair(args, "--exclude-checks", "unsupported_method"), args)

    def test_schemathesis_never_probes_an_unsafe_method(self):
        for args in self.schemathesis_args():
            self.assertTrue(has_pair(args, "--include-method", "GET"), args)
            pattern = args[args.index("--exclude-method-regex") + 1]
            excluded = re.compile(pattern, re.IGNORECASE)
            for method in ("POST", "PUT", "PATCH", "DELETE", "TRACE", "QUERY", "CONNECT", "trace"):
                self.assertTrue(excluded.search(method), method)
            for method in ("GET", "HEAD", "OPTIONS", "get"):
                self.assertFalse(excluded.search(method), method)

    def test_schemathesis_skipped_with_auth(self):
        result = self.run_tool("schemathesis", **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.tool_runs())
        document = self.read_json("tools/schemathesis/result.json")
        self.assertEqual("skipped", document["status"])
        self.assertEqual("authentification requise", document["reason"])

    def test_no_openapi_spec_skips_redocly_and_schemathesis(self):
        (self.laravel / "openapi.yaml").unlink()
        (self.laravel / "docs" / "swagger v2.json").unlink()
        for tool in ("redocly", "schemathesis"):
            result = self.run_tool(tool)
            self.assertEqual(0, result.returncode, result.stderr)
            document = self.read_json(f"tools/{tool}/result.json")
            self.assertEqual("skipped", document["status"])
            self.assertIn("OpenAPI", document["reason"])
        self.assertEqual([], self.tool_runs())

    def test_crawl(self):
        result = self.run_tool("crawl", **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([[]], [args for _, args in self.runs_of("screamingfrog")])
        self.assertEqual(
            ["summarize", "crawl", "--raw", "/out/crawl/raw"], self.runs_of("agence")[0][1]
        )

    def test_crawl_summary_still_runs_when_crawler_refuses(self):
        result = self.run_tool("crawl", CQA_STUB_FAIL="screamingfrog", CQA_STUB_FAIL_CODE="2")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len(self.runs_of("agence")))

    def test_tool_exit_code_is_passed_through(self):
        result = self.run_tool("http", CQA_STUB_FAIL="http --probes", CQA_STUB_FAIL_CODE="3")
        self.assertEqual(3, result.returncode)


class AllTest(LauncherCase):
    def test_all_chains_every_tool_then_manifest(self):
        result = self.launch("all", str(self.run_dir))
        self.assertEqual(0, result.returncode, result.stderr)
        sequence = [(name, " ".join(args)) for _, name, args in self.tool_runs()]
        self.assertEqual([name for name, _ in ALL_ORDER], [name for name, _ in sequence], sequence)
        for (_, got), (_, prefix) in zip(sequence, ALL_ORDER):
            self.assertTrue(got.startswith(prefix), (got, prefix))
        manifest = json.loads(result.stdout)
        self.assertIn("tools", manifest)
        self.assertTrue((self.run_dir / "tools" / "manifest.json").is_file())

    def test_all_continues_after_a_failure(self):
        result = self.launch("all", str(self.run_dir), CQA_STUB_FAIL="lighthouse --pages")
        self.assertEqual(1, result.returncode)
        names = [name for _, name, _ in self.tool_runs()]
        self.assertIn("screamingfrog", names)
        self.assertIn(["a11y", "--pages", "/in/context.json"], [args for _, _, args in self.tool_runs()])
        self.assertIn("lighthouse", result.stderr)
        self.assertTrue((self.run_dir / "tools" / "manifest.json").is_file())
        self.assertIn("tools", json.loads(result.stdout))

    def test_all_keeps_tool_output_off_stdout(self):
        result = self.launch("all", str(self.run_dir))
        json.loads(result.stdout)

    def test_all_skips_schemathesis_with_auth(self):
        result = self.launch("all", str(self.run_dir), **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.runs_of("schemathesis"))
        self.assertEqual("skipped", self.read_json("tools/schemathesis/result.json")["status"])


class RefusalsTest(LauncherCase):
    def test_usage_errors(self):
        for args in (
            [],
            ["nope"],
            ["doctor", "extra"],
            ["pull", "extra"],
            ["run"],
            ["run", "dir"],
            ["all"],
            ["all", "a", "b"],
            ["run", str(self.run_dir), "nope"],
            ["run", str(self.run_dir), "http", "--other"],
            ["run", str(self.run_dir), "http", "--followup", "x"],
            ["run", str(self.tmp / "missing"), "http"],
            ["all", str(self.tmp / "missing")],
        ):
            result = self.launch(*args)
            self.assertEqual(2, result.returncode, (args, result.stderr))
        self.assertEqual([], self.calls())

    def test_missing_context_is_refused(self):
        (self.run_dir / "context.json").unlink()
        result = self.launch("run", str(self.run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertIn("context.json", result.stderr)
        self.assertEqual([], self.calls())

    def test_missing_repo_is_refused(self):
        shutil.rmtree(self.nuxt)
        result = self.launch("run", str(self.run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertIn("nuxt", result.stderr)
        self.assertEqual([], self.calls())

    def test_relative_repo_path_is_refused(self):
        self.run_dir = self.make_run(repos=[("laravel", "repos/laravel")], parent=self.tmp / "other")
        result = self.launch("run", str(self.run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertEqual([], self.calls())

    def test_colon_in_a_path_is_refused(self):
        self.run_dir = self.make_run(parent=self.tmp / "a:b")
        result = self.launch("run", str(self.run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertEqual([], self.calls())

    def test_invalid_run_id_is_refused(self):
        odd = self.tmp / "odd name"
        odd.mkdir()
        run_dir = odd / "run id"
        shutil.copytree(self.run_dir, run_dir)
        result = self.launch("run", str(run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertEqual([], self.calls())

    def test_unreadable_context_is_refused(self):
        (self.run_dir / "context.json").write_text("[]", encoding="utf-8")
        result = self.launch("run", str(self.run_dir), "assets")
        self.assertEqual(2, result.returncode)
        self.assertEqual([], self.calls())

    def test_missing_docker_is_refused(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("python3", "uname", "dirname", "basename", "id"):
            (bin_dir / name).symlink_to(shutil.which(name))
        for args in (["run", str(self.run_dir), "assets"], ["pull"], ["all", str(self.run_dir)]):
            result = self.launch(*args, path=str(bin_dir))
            self.assertEqual(2, result.returncode, args)
            self.assertIn("Docker", result.stderr)

    def test_missing_probes_file_is_reported(self):
        (self.run_dir / "probes.json").unlink()
        result = self.launch("run", str(self.run_dir), "http")
        self.assertEqual(2, result.returncode)
        self.assertIn("probes.json", result.stderr)
        self.assertEqual([], self.calls())


class DoctorTest(LauncherCase):
    def doctor(self, **extra):
        result = self.launch("doctor", **extra)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_report_without_run_directory(self):
        report = self.doctor()
        self.assertEqual(
            {"docker", "docker_version", "registry_login", "images", "sf_licence", "sf_eula", "arch"},
            set(report),
        )
        self.assertIs(True, report["docker"])
        self.assertEqual("27.0.1", report["docker_version"])
        self.assertIs(True, report["registry_login"])
        self.assertIs(False, report["sf_licence"])
        self.assertIs(True, report["sf_eula"])
        self.assertIn(report["arch"], ("amd64", "arm64"))
        self.assertEqual(IMAGES, list(report["images"]))
        self.assertEqual({"1.2.3"}, set(report["images"].values()))
        calls = self.calls()
        self.assertIn(["manifest", "inspect", f"{REGISTRY}/agence:1"], calls)
        self.assertTrue(
            any(call[:2] == ["image", "inspect"] and call[-1] == f"{REGISTRY}/nu:1" for call in calls)
        )

    def test_registry_login_failure(self):
        self.assertIs(False, self.doctor(CQA_STUB_NO_LOGIN="1")["registry_login"])

    def test_missing_images_are_null(self):
        images = self.doctor(CQA_STUB_MISSING="nu,testssl")["images"]
        self.assertIsNone(images["nu"])
        self.assertIsNone(images["testssl"])
        self.assertEqual("1.2.3", images["agence"])

    def test_daemon_down(self):
        report = self.doctor(CQA_STUB_NO_DAEMON="1")
        self.assertIs(True, report["docker"])
        self.assertIsNone(report["docker_version"])

    def test_licence_needs_both_values_and_is_never_printed(self):
        self.assertIs(True, self.doctor(**SECRETS)["sf_licence"])
        self.assertIs(False, self.doctor(GM_SF_LICENCE_USER="sf-user")["sf_licence"])
        result = self.launch("doctor", **SECRETS)
        for value in SECRETS.values():
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_eula_is_read_from_the_screamingfrog_image(self):
        self.assertIs(False, self.doctor(CQA_STUB_NO_EULA="1")["sf_eula"])
        self.assertIsNone(self.doctor(CQA_STUB_MISSING="screamingfrog")["sf_eula"])
        self.assertTrue(
            any(
                call[:2] == ["image", "inspect"] and ".Config.Env" in " ".join(call)
                and call[-1] == f"{REGISTRY}/screamingfrog:1"
                for call in self.calls()
            )
        )

    def test_registry_and_tag_overrides(self):
        self.doctor(GM_CQA_REGISTRY="registry.example.org/team", GM_CQA_TOOLBOX_TAG="3")
        self.assertIn(["manifest", "inspect", "registry.example.org/team/agence:3"], self.calls())

    def test_without_docker(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("python3", "uname", "dirname"):
            (bin_dir / name).symlink_to(shutil.which(name))
        report = self.doctor(path=str(bin_dir))
        self.assertIs(False, report["docker"])
        self.assertIsNone(report["docker_version"])
        self.assertIs(False, report["registry_login"])
        self.assertEqual({None}, set(report["images"].values()))
        self.assertIsNone(report["sf_eula"])


class PullTest(LauncherCase):
    def test_pulls_every_image(self):
        result = self.launch("pull")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["pull", f"{REGISTRY}/{name}:1"] for name in IMAGES], self.calls())

    def test_failure_continues_and_reports(self):
        result = self.launch("pull", CQA_STUB_FAIL="/nu:")
        self.assertEqual(1, result.returncode)
        self.assertEqual(len(IMAGES), len(self.calls()))
        self.assertIn("docker login", result.stderr)
        self.assertIn("registry.gitlab.gingerminds.fr", result.stderr)


class SourceTest(unittest.TestCase):
    def source(self):
        return SCRIPT.read_text(encoding="utf-8")

    def test_bash32_constructs_absent(self):
        src = self.source()
        for bad in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "&>>", "|&", "[[ -v", "coproc"):
            self.assertNotIn(bad, src)

    def test_no_eval_no_trace_strict_mode(self):
        src = self.source()
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        self.assertIsNone(re.search(r"\beval\b", code))
        self.assertIsNone(re.search(r"\bset -[a-z]*x", code))
        self.assertIn("set -euo pipefail", src)

    def test_no_em_dash_and_valid_syntax(self):
        for path in (SCRIPT, STUBS / "docker"):
            self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"), path)
        result = subprocess.run(
            [shutil.which("bash"), "-n", str(SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_scripts_are_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertTrue(os.access(STUBS / "docker", os.X_OK))


if __name__ == "__main__":
    unittest.main()
