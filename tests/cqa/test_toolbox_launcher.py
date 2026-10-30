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
}
UNSAFE_METHODS = "^(?!(GET|HEAD|OPTIONS)$)"
IMAGES = ["agence", "nu", "testssl", "gitleaks", "redocly", "schemathesis", "nginx", "varnish"]
ALL_ORDER = [
    ("agence", "http --probes"),
    ("gitleaks", "git /repos/laravel"),
    ("gitleaks", "git /repos/nuxt"),
    ("agence", "summarize gitleaks"),
    ("agence", "assets --repos"),
    ("agence", "stylelint --repos"),
    ("agence", "deps --repos"),
    ("agence", "lighthouse --pages /in/context.json"),
    ("agence", "lighthouse --pages /in/context.json --form-factor desktop"),
    ("agence", "a11y --pages"),
    ("agence", "viewport --pages"),
    ("agence", "nu --pages"),
    ("agence", "yellowlab --pages /in/context.json"),
    ("agence", "yellowlab --pages /in/context.json --device desktop"),
    ("testssl", "--quiet --color"),
    ("agence", "summarize testssl"),
    ("redocly", "lint /repos/laravel/docs/swagger v2.json"),
    ("redocly", "lint /repos/laravel/openapi.yaml"),
    ("agence", "summarize redocly"),
    ("schemathesis", "run /repos/laravel/docs/swagger v2.json"),
    ("schemathesis", "run /repos/laravel/openapi.yaml"),
    ("agence", "summarize schemathesis"),
    ("agence", "crawl"),
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
        (options, args), = self.runs_of("agence")
        self.assertEqual(["crawl", "--max-minutes", "30", "--max-urls", "2000"], args)
        for name in (
            "GM_AUDIT_TARGET",
            "GM_AUDIT_ROBOTS",
            "GM_AUDIT_RATE",
            "GM_AUDIT_USER_AGENT",
            "GM_AUDIT_AUTH_USER",
            "GM_AUDIT_AUTH_PASS",
        ):
            self.assertTrue(has_pair(options, "--env", name), name)
        self.assertFalse(any(name.startswith("GM_" + "SF_") for name in env_args(options)))

    def test_crawl_passes_the_prod_origin_when_given(self):
        context = json.loads((self.run_dir / "context.json").read_text(encoding="utf-8"))
        context["target"]["prod_url"] = "https://www.example.org/fr"
        (self.run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
        self.launch("run", str(self.run_dir), "crawl")
        (options, _), = self.runs_of("agence")
        self.assertTrue(has_pair(options, "--env", "GM_AUDIT_PROD_ORIGIN"))
        self.log.unlink()
        self.launch("run", str(self.run_dir), "http")
        (options, _), = self.runs_of("agence")
        self.assertNotIn("GM_AUDIT_PROD_ORIGIN", options)

    def test_secret_values_never_in_args(self):
        result = self.launch("all", str(self.run_dir), **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        text = self.log.read_text(encoding="utf-8")
        for value in SECRETS.values():
            self.assertNotIn(value, text)
            self.assertNotIn(value, result.stdout)
            self.assertNotIn(value, result.stderr)
        self.assertIn('"--env", "GM_AUDIT_AUTH_PASS"', text)

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

    def test_new_tools_commands(self):
        web = [
            "GM_AUDIT_TARGET",
            "GM_AUDIT_RATE",
            "GM_AUDIT_USER_AGENT",
            "GM_AUDIT_ROBOTS",
            "GM_AUDIT_AUTH_USER",
            "GM_AUDIT_AUTH_PASS",
        ]
        cases = (
            (
                "lighthouse-desktop",
                ["lighthouse", "--pages", "/in/context.json", "--form-factor", "desktop",
                 "--name", "lighthouse-desktop"],
                True,
            ),
            (
                "yellowlab-desktop",
                ["yellowlab", "--pages", "/in/context.json", "--device", "desktop",
                 "--name", "yellowlab-desktop"],
                True,
            ),
            ("viewport", ["viewport", "--pages", "/in/context.json"], True),
            ("stylelint", ["stylelint", "--repos", "/repos", "--context", "/in/context.json"], False),
            ("deps", ["deps", "--repos", "/repos", "--context", "/in/context.json"], False),
        )
        for tool, expected, uses_web in cases:
            with self.subTest(tool=tool):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_tool(tool, **SECRETS)
                self.assertEqual(0, result.returncode, result.stderr)
                (options, args), = self.runs_of("agence")
                self.assertEqual(expected, args)
                if uses_web:
                    self.assertEqual(
                        sorted(["HOME=/tmp"] + web),
                        sorted(env_args(options)),
                    )
                else:
                    # Project JavaScript configs are executed: no GM_AUDIT_* variable.
                    self.assertEqual(["HOME=/tmp"], env_args(options))
                self.assertTrue((self.run_dir / "tools" / tool / "raw").is_dir())

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

    def test_no_openapi_spec_makes_redocly_and_schemathesis_not_applicable(self):
        (self.laravel / "openapi.yaml").unlink()
        (self.laravel / "docs" / "swagger v2.json").unlink()
        for tool in ("redocly", "schemathesis"):
            result = self.run_tool(tool)
            self.assertEqual(0, result.returncode, result.stderr)
            document = self.read_json(f"tools/{tool}/result.json")
            self.assertEqual("not_applicable", document["status"])
            self.assertEqual("aucune spec OpenAPI trouvée dans les repos", document["reason"])
            self.assertIn(f"{tool} : non applicable (aucune spec OpenAPI", result.stdout + result.stderr)
        self.assertEqual([], self.tool_runs())

    def test_schemathesis_without_spec_is_not_applicable_even_with_auth(self):
        (self.laravel / "openapi.yaml").unlink()
        (self.laravel / "docs" / "swagger v2.json").unlink()
        result = self.run_tool("schemathesis", **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        document = self.read_json("tools/schemathesis/result.json")
        self.assertEqual("not_applicable", document["status"])
        self.assertEqual([], self.tool_runs())

    def test_crawl(self):
        result = self.run_tool("crawl", **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [["crawl", "--max-minutes", "30", "--max-urls", "2000"]],
            [args for _, args in self.runs_of("agence")],
        )

    def test_crawl_passes_the_duration_and_the_url_cap(self):
        context = json.loads((self.run_dir / "context.json").read_text(encoding="utf-8"))
        context["crawl_minutes"] = 60
        (self.run_dir / "context.json").write_text(json.dumps(context), encoding="utf-8")
        result = self.run_tool("crawl", **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        (options, args), = self.runs_of("agence")
        self.assertEqual(["crawl", "--max-minutes", "60", "--max-urls", "4000"], args)
        self.assertFalse(any("GM_CQA" in item for item in options))

    def test_crawl_failure_is_tolerated(self):
        result = self.run_tool("crawl", CQA_STUB_FAIL="agence:1 crawl", CQA_STUB_FAIL_CODE="2")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("code de sortie 2", result.stderr)

    def test_run_updates_the_manifest(self):
        manifest = self.run_dir / "tools" / "manifest.json"
        result = self.run_tool("crawl")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(manifest.is_file())
        self.assertIn("tools", json.loads(manifest.read_text(encoding="utf-8")))

    def test_run_updates_the_manifest_after_a_failed_tool(self):
        result = self.run_tool("http", CQA_STUB_FAIL="http --probes", CQA_STUB_FAIL_CODE="3")
        self.assertEqual(3, result.returncode)
        self.assertTrue((self.run_dir / "tools" / "manifest.json").is_file())

    def test_run_returns_the_manifest_failure_when_the_tool_succeeds(self):
        (self.run_dir / "tools" / "manifest.json").mkdir(parents=True)
        result = self.run_tool("crawl")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("manifeste", result.stderr)

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
        self.assertIn("testssl", names)
        self.assertIn(["a11y", "--pages", "/in/context.json"], [args for _, _, args in self.tool_runs()])
        self.assertIn("lighthouse", result.stderr)
        self.assertTrue((self.run_dir / "tools" / "manifest.json").is_file())
        self.assertIn("tools", json.loads(result.stdout))

    def test_all_keeps_tool_output_off_stdout(self):
        result = self.launch("all", str(self.run_dir))
        json.loads(result.stdout)

    def test_toolbox_older_than_1_2_skips_the_new_tools(self):
        self.check_new_tools_skipped("1.1.0")

    def test_unknown_image_label_skips_the_new_tools(self):
        self.check_new_tools_skipped("unknown")

    def check_new_tools_skipped(self, version):
        result = self.launch("all", str(self.run_dir), CQA_STUB_VERSION=version)
        self.assertEqual(0, result.returncode, result.stderr)
        new_tools = ("stylelint", "deps", "lighthouse-desktop", "viewport", "yellowlab-desktop")
        for tool in new_tools:
            with self.subTest(tool=tool):
                found = self.read_json(f"tools/{tool}/result.json")
                self.assertEqual("skipped", found["status"])
                self.assertEqual(
                    "boîte à outils 1.2 requise : lancez cqa-toolbox.sh pull", found["reason"]
                )
        runs = [args for _, _, args in self.tool_runs()]
        for args in runs:
            self.assertNotIn(args[0], ("viewport", "stylelint", "deps"), args)
            self.assertNotIn("--form-factor", args)
            self.assertNotIn("--device", args)
        self.assertIn(["lighthouse", "--pages", "/in/context.json"], runs)
        self.assertIn(["yellowlab", "--pages", "/in/context.json"], runs)

    def test_all_skips_schemathesis_with_auth(self):
        result = self.launch("all", str(self.run_dir), **SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.runs_of("schemathesis"))
        self.assertEqual("skipped", self.read_json("tools/schemathesis/result.json")["status"])


class SecretsTest(LauncherCase):
    """secrets <in_file> <out_dir>: gitleaks on the content of a file, offline and read-only,
    for the guard of the fix mode."""

    def setUp(self):
        super().setUp()
        self.in_dir = self.tmp / "guard in"
        self.in_dir.mkdir()
        self.in_file = self.in_dir / "input.txt"
        self.in_file.write_text("<?php\n$x = 1;\n", encoding="utf-8")
        self.out_dir = self.tmp / "guard out"
        self.out_dir.mkdir()

    def secrets(self, *args, **extra):
        return self.launch("secrets", *(args or (str(self.in_file), str(self.out_dir))), **extra)

    def docker_runs(self):
        return [call for call in self.calls() if call[:1] == ["run"]]

    def test_secrets_command_runs_offline(self):
        result = self.secrets(**SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        runs = self.docker_runs()
        self.assertEqual(1, len(runs))
        call = runs[0]
        self.assertTrue(has_pair(call, "--network", "none"))
        self.assertTrue(has_pair(call, "--pull", "never"))
        self.assertIn("--rm", call)
        self.assertEqual(f"{os.getuid()}:{os.getgid()}", call[call.index("--user") + 1])
        self.assertTrue(has_pair(call, "--env", "HOME=/tmp"))
        self.assertTrue(has_pair(call, "--tmpfs", "/tmp"))
        self.assertTrue(has_pair(call, "-v", f"{self.in_file}:/in/input:ro"))
        self.assertTrue(has_pair(call, "-v", f"{self.out_dir}:/out"))
        mounts = [call[i + 1] for i in range(len(call) - 1) if call[i] == "-v"]
        self.assertEqual(2, len(mounts))
        self.assertEqual(["HOME=/tmp"], env_args(call))
        for secret in ("GM_AUDIT_AUTH_USER", "GM_AUDIT_AUTH_PASS", "cqa-user", "pass-xyz-123"):
            self.assertNotIn(secret, " ".join(call))
        options, image, arguments = split_run(call)
        self.assertEqual("gitleaks", image)
        self.assertTrue(has_pair(options, "--entrypoint", "sh"))
        self.assertEqual("-c", arguments[0])
        script = arguments[1]
        for needle in ("gitleaks stdin", "--report-format json", "--report-path /out/gitleaks.json",
                       "--exit-code 0", "--no-banner", "-l error", "--ignore-gitleaks-allow",
                       "--redact=100", "</in/input"):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)
        self.assertEqual("[]", (self.out_dir / "gitleaks.json").read_text(encoding="utf-8"))
        self.assertEqual("<?php\n$x = 1;\n", self.in_file.read_text(encoding="utf-8"))
        self.assertEqual(["input.txt"], [path.name for path in self.in_dir.iterdir()])
        self.assertEqual(["gitleaks.json"], [path.name for path in self.out_dir.iterdir()])

    def test_secrets_replaces_a_stale_report(self):
        (self.out_dir / "gitleaks.json").write_text("stale", encoding="utf-8")
        self.assertEqual(0, self.secrets().returncode)
        self.assertEqual("[]", (self.out_dir / "gitleaks.json").read_text(encoding="utf-8"))

    def test_secrets_without_docker_or_image_exits_2_before_any_run(self):
        result = self.secrets(CQA_STUB_MISSING="gitleaks")
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("gitleaks", result.stderr)
        self.assertIn("cqa-toolbox.sh pull", result.stderr)
        self.assertEqual([], self.docker_runs())
        self.assertFalse((self.out_dir / "gitleaks.json").exists())
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("python3", "uname", "dirname", "basename", "id"):
            (bin_dir / name).symlink_to(shutil.which(name))
        result = self.secrets(path=str(bin_dir))
        self.assertEqual(2, result.returncode)
        self.assertIn("Docker", result.stderr)

    def test_secrets_usage_and_failures(self):
        for args in (
            [],
            [str(self.in_file)],
            [str(self.in_file), str(self.out_dir), "extra"],
            [str(self.tmp / "absent.txt"), str(self.out_dir)],
            [str(self.in_file), str(self.tmp / "absent")],
        ):
            with self.subTest(args=args):
                result = self.launch("secrets", *args)
                self.assertEqual(2, result.returncode, (args, result.stderr))
        self.assertEqual([], self.docker_runs())
        result = self.secrets(CQA_STUB_FAIL="gitleaks stdin")
        self.assertEqual(1, result.returncode)
        self.assertIn("gitleaks", result.stderr)
        self.assertFalse((self.out_dir / "gitleaks.json").exists())


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
            {"docker", "docker_version", "registry_login", "images", "agence_crawl", "agence_1_2", "arch"},
            set(report),
        )
        self.assertIs(True, report["docker"])
        self.assertEqual("27.0.1", report["docker_version"])
        self.assertIs(True, report["registry_login"])
        self.assertIs(True, report["agence_crawl"])
        self.assertIs(True, report["agence_1_2"])
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

    def test_agence_crawl_needs_toolbox_1_1(self):
        cases = {"1.1.0": True, "1.2.3": True, "2.0.0": True, "1.0.2": False, "0.0.0-dev": False}
        for version, expected in cases.items():
            with self.subTest(version=version):
                self.assertIs(expected, self.doctor(CQA_STUB_VERSION=version)["agence_crawl"])
        self.assertIs(False, self.doctor(CQA_STUB_MISSING="agence")["agence_crawl"])

    def test_agence_1_2_needs_toolbox_1_2(self):
        cases = {"1.2.0": True, "1.2.3": True, "2.0.0": True, "1.10.0": True, "1.1.0": False,
                 "1.2.0-rc1": False, "0.0.0-dev": False}
        for version, expected in cases.items():
            with self.subTest(version=version):
                self.assertIs(expected, self.doctor(CQA_STUB_VERSION=version)["agence_1_2"])
        self.assertIs(False, self.doctor(CQA_STUB_MISSING="agence")["agence_1_2"])

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
        self.assertIs(False, report["agence_crawl"])
        self.assertIs(False, report["agence_1_2"])


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


class CheckTest(LauncherCase):
    def setUp(self):
        super().setUp()
        self.check_dir = self.tmp / "check"
        self.check_dir.mkdir()
        self.worktree = self.tmp / "wt"
        self.worktree.mkdir()
        (self.check_dir / "files.txt").write_text("a.php\nweb/style.scss\n", encoding="utf-8")
        (self.check_dir / "context.json").write_text(
            json.dumps({"repos": [{"name": "front", "third_party_paths": []}]}), encoding="utf-8")

    def check(self, **extra):
        return self.launch("check", str(self.check_dir), str(self.worktree), "front", **extra)

    def docker_runs(self):
        return [call for call in self.calls() if call[:1] == ["run"]]

    def test_check_mounts_the_worktree_read_only_without_network(self):
        result = self.check(**SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        runs = self.docker_runs()
        self.assertEqual(2, len(runs))
        for call in runs:
            self.assertTrue(has_pair(call, "--network", "none"))
            # R19: a missing image is never downloaded in the middle of a fix pass.
            self.assertTrue(has_pair(call, "--pull", "never"))
            self.assertIn("--rm", call)
            self.assertEqual(f"{os.getuid()}:{os.getgid()}", call[call.index("--user") + 1])
            self.assertTrue(has_pair(call, "--env", "HOME=/tmp"))
            self.assertTrue(has_pair(call, "--tmpfs", "/tmp"))
            self.assertTrue(has_pair(call, "-v", f"{self.worktree}:/repos/front:ro"))
            mounts = [call[i + 1] for i in range(len(call) - 1) if call[i] == "-v"]
            others = [m for m in mounts if "/repos/" in m and m != f"{self.worktree}:/repos/front:ro"]
            self.assertEqual([], others)
            self.assertEqual(["HOME=/tmp"], env_args(call))
            for secret in ("GM_AUDIT_AUTH_USER", "GM_AUDIT_AUTH_PASS", "cqa-user", "pass-xyz-123"):
                self.assertNotIn(secret, " ".join(call))
        syntax, style = runs
        self.assertTrue(has_pair(syntax, "--entrypoint", "sh"))
        self.assertTrue(has_pair(syntax, "-v", f"{self.check_dir}:/in:ro"))
        self.assertEqual("agence", split_run(style)[1])
        self.assertEqual(
            ["stylelint", "--repos", "/repos", "--context", "/in/context.json"], split_run(style)[2])
        self.assertTrue(has_pair(style, "-v", f"{self.check_dir}:/in:ro"))
        self.assertTrue(has_pair(style, "-v", f"{self.check_dir}/tools:/out"))
        self.assertTrue((self.check_dir / "tools" / "stylelint" / "raw").is_dir())
        self.assertTrue((self.check_dir / "syntax.tsv").is_file())

    def test_check_skips_stylelint_without_a_style_file(self):
        (self.check_dir / "files.txt").write_text("a.php\n", encoding="utf-8")
        self.assertEqual(0, self.check().returncode)
        self.assertEqual(1, len(self.docker_runs()))

    def test_check_writes_nothing_outside_the_check_dir(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        self.assertEqual(0, self.check().returncode)
        self.assertEqual([], list(self.worktree.iterdir()))
        self.assertEqual(before, sorted(p.name for p in self.tmp.iterdir() if p.name != "docker.log"))

    def test_check_failures_and_environment(self):
        result = self.check(CQA_STUB_FAIL="--entrypoint sh")
        self.assertEqual(1, result.returncode)
        self.assertFalse((self.check_dir / "syntax.tsv").exists())
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("python3", "uname", "dirname", "basename", "id"):
            (bin_dir / name).symlink_to(shutil.which(name))
        result = self.check(path=str(bin_dir))
        self.assertEqual(2, result.returncode)
        self.assertIn("Docker", result.stderr)
        self.assertEqual(2, self.launch("check", str(self.check_dir)).returncode)
        self.assertEqual(2, self.launch("check", str(self.tmp / "none"), str(self.worktree), "front").returncode)
        (self.check_dir / "files.txt").unlink()
        self.assertEqual(2, self.check().returncode)

    def test_syntax_script_reports_one_line_per_file(self):
        if shutil.which("node") is None:
            self.skipTest("node absent")
        match = re.search(r"read -r -d '' SYNTAX_SCRIPT <<'SCRIPT' \|\| true\n(.*?)\nSCRIPT\n",
                          SCRIPT.read_text(encoding="utf-8"), re.DOTALL)
        self.assertIsNotNone(match)
        root = self.tmp / "fake-root"
        repo = root / "repos" / "front"
        repo.mkdir(parents=True)
        (repo / "good.json").write_text("\ufeff{\"a\": 1}", encoding="utf-8")
        (repo / "bad.json").write_text("{\"a\": ", encoding="utf-8")
        (repo / "a.js").write_text("let x = ;\n", encoding="utf-8")
        (repo / "b.js").write_text("let x = 1;\n", encoding="utf-8")
        (repo / "c.yml").write_text("a: 1\n", encoding="utf-8")
        (repo / "d.txt").write_text("x", encoding="utf-8")
        (repo / ".vscode").mkdir()
        (repo / ".vscode" / "settings.json").write_text("{// c\n}", encoding="utf-8")
        (repo / "tsconfig.json").write_text("{// c\n}", encoding="utf-8")
        (repo / "mytsconfig.json").write_text("{// c\n}", encoding="utf-8")
        listing = self.tmp / "in.txt"
        listing.write_text("good.json\nbad.json\na.js\nb.js\nc.yml\nd.txt\n.vscode/settings.json\n"
                           "tsconfig.json\nmytsconfig.json\n", encoding="utf-8")
        script = match.group(1).replace("/repos/", f"{root}/repos/").replace("</in/files.txt", f"<{listing}")
        result = subprocess.run(["sh", "-c", script, "sh", "front"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        status = {row[2]: (row[0], row[1]) for row in rows}
        self.assertEqual(("ok", "json"), status["good.json"])
        self.assertEqual(("ko", "json"), status["bad.json"])
        self.assertEqual(("ko", "node --check"), status["a.js"])
        self.assertEqual(("ok", "node --check"), status["b.js"])
        self.assertIn(status["c.yml"][0], ("ok", "skip"))
        self.assertNotIn("d.txt", status)
        self.assertEqual(("skip", "json"), status[".vscode/settings.json"])
        self.assertEqual(("skip", "json"), status["tsconfig.json"])
        self.assertEqual(("ko", "json"), status["mytsconfig.json"])
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_syntax_script_parses_scss_with_postcss_scss(self):
        if shutil.which("node") is None:
            self.skipTest("node absent")
        module = self.tmp / "gm-audit" / "node_modules" / "postcss-scss"
        module.mkdir(parents=True)
        (module / "package.json").write_text('{"name": "postcss-scss", "main": "index.js"}',
                                             encoding="utf-8")
        (module / "index.js").write_text(
            "exports.parse = (text) => { if (text.includes('{{')) { const e = new Error('x');"
            " e.reason = 'Unclosed block'; e.line = 2; e.column = 3; throw e; } };\n",
            encoding="utf-8")
        (self.tmp / "gm-audit" / "package.json").write_text("{}", encoding="utf-8")
        root = self.tmp / "fake-root"
        repo = root / "repos" / "front"
        repo.mkdir(parents=True)
        (repo / "good.scss").write_text(".a { .b { color: red; } }\n", encoding="utf-8")
        (repo / "bad.scss").write_text(".a {\n  .b {{\n", encoding="utf-8")
        (repo / "c.css").write_text(".a {{\n", encoding="utf-8")
        listing = self.tmp / "in.txt"
        listing.write_text("good.scss\nbad.scss\nc.css\n", encoding="utf-8")
        script = (embedded_script("SYNTAX_SCRIPT")
                  .replace("/opt/gm-audit/package.json", str(self.tmp / "gm-audit" / "package.json"))
                  .replace("/repos/", f"{root}/repos/").replace("</in/files.txt", f"<{listing}"))
        result = subprocess.run(["sh", "-c", script, "sh", "front"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual([["ok", "scss", "good.scss", ""], ["ko", "scss", "bad.scss", "2:3 Unclosed block"]],
                         rows)
        (module / "index.js").unlink()
        (module / "package.json").unlink()
        result = subprocess.run(["sh", "-c", script, "sh", "front"], capture_output=True, text=True)
        self.assertEqual("skip\tscss\tgood.scss\tmodule postcss-scss absent de l'image",
                         result.stdout.splitlines()[0])

    def put(self, root, path, text):
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def config_runs(self):
        found = {}
        for call in self.docker_runs():
            parts = split_run(call)
            if parts and parts[1] in ("nginx", "varnish"):
                found.setdefault(parts[1], []).append(call)
        return found

    def test_check_runs_nginx_and_varnish_offline(self):
        self.put(self.worktree, "docker/nginx/conf.d/default.conf",
                 "upstream backend {\n    server app-1:8080 weight=2;\n    server unix:/run/a.sock;\n}\n"
                 "upstream one { server one-line:80; server two-line:80 backup; }\n"
                 "server {\n    resolver dns.example.test valid=30s ipv6=off;\n"
                 "    location / { proxy_pass http://api.example.test:8080/x; }\n"
                 "    location ~ \\.php$ { fastcgi_pass php:9000; }\n"
                 "    location /v { proxy_pass http://$backend; }\n"
                 "    location /l { proxy_pass http://127.0.0.1:81; }\n"
                 "    # proxy_pass http://commented.example.test;\n}\n")
        self.put(self.worktree, "docker/nginx/templates/site.conf.template",
                 "server { listen ${APP_PORT}; location / { fastcgi_pass ${PHP_HOST}:9000; } }\n")
        self.put(self.worktree, "docker/varnish/default.vcl", 'vcl 4.1;\ninclude "backends.vcl";\n')
        self.put(self.worktree, "docker/varnish/backends.vcl",
                 'backend default { .host = "cms.example.test"; .port = "80"; }\n')
        self.put(self.check_dir, "base/docker/nginx/conf.d/default.conf",
                 "server { location / { proxy_pass http://old.example.test; } }\n")
        self.put(self.check_dir, "base/docker/varnish/default.vcl", 'backend b { .host = "app"; }\n')
        (self.check_dir / "files.txt").write_text(
            "a.php\ndocker/nginx/conf.d/default.conf\ndocker/nginx/templates/site.conf.template\n"
            "docker/varnish/default.vcl\nother/site.conf\nREADME.md\n", encoding="utf-8")
        result = self.check(**SECRETS)
        self.assertEqual(0, result.returncode, result.stderr)
        runs = self.config_runs()
        self.assertEqual({"nginx": 1, "varnish": 1}, {name: len(calls) for name, calls in runs.items()})
        for name, (call,) in runs.items():
            with self.subTest(image=name):
                self.assertIn(["image", "inspect", f"{REGISTRY}/{name}:1"],
                              [c[:2] + c[-1:] for c in self.calls() if c[:2] == ["image", "inspect"]])
                self.assertTrue(has_pair(call, "--network", "none"))
                self.assertTrue(has_pair(call, "--pull", "never"))
                self.assertIn("--rm", call)
                self.assertEqual(f"{os.getuid()}:{os.getgid()}", call[call.index("--user") + 1])
                self.assertEqual(["HOME=/tmp"], env_args(call))
                self.assertTrue(has_pair(call, "--tmpfs", "/tmp"))
                self.assertTrue(has_pair(call, "--entrypoint", "sh"))
                mounts = [call[i + 1] for i in range(len(call) - 1) if call[i] == "-v"]
                self.assertEqual(sorted([f"{self.check_dir}:/in:ro", f"{self.worktree}:/repos/front:ro"]),
                                 sorted(mounts))
                for secret in ("GM_AUDIT_AUTH_USER", "GM_AUDIT_AUTH_PASS", "cqa-user", "pass-xyz-123"):
                    self.assertNotIn(secret, " ".join(call))

        def hosts(call):
            return sorted(call[i + 1] for i in range(len(call) - 1) if call[i] == "--add-host")

        self.assertEqual(
            [f"{host}:127.0.0.1" for host in
             ("api.example.test", "app-1", "dns.example.test", "example.test", "old.example.test",
              "one-line", "php", "two-line")],
            hosts(runs["nginx"][0]))
        self.assertEqual(["app:127.0.0.1", "cms.example.test:127.0.0.1"], hosts(runs["varnish"][0]))
        self.assertEqual(
            "nginx\tdocker/nginx\tdocker/nginx/conf.d/default.conf\n"
            "nginx\tdocker/nginx\tdocker/nginx/templates/site.conf.template\n"
            "varnish\tdocker/varnish\tdocker/varnish/default.vcl\n",
            (self.check_dir / "config-plan.tsv").read_text(encoding="utf-8"))
        self.assertTrue((self.check_dir / "config.tsv").is_file())
        self.assertTrue((self.check_dir / "syntax.tsv").is_file())
        self.assertEqual(["docker"], sorted(p.name for p in self.worktree.iterdir()))

    def test_check_without_a_server_file_starts_no_config_container(self):
        self.assertEqual(0, self.check().returncode)
        self.assertEqual({}, self.config_runs())
        self.assertFalse(any(call[:2] == ["image", "inspect"] for call in self.calls()))
        self.assertFalse((self.check_dir / "config.tsv").exists())

    def test_missing_image_is_not_run(self):
        self.put(self.worktree, "nginx/site.conf", "server {}\n")
        self.put(self.worktree, "default.vcl", "vcl 4.1;\n")
        (self.check_dir / "files.txt").write_text("nginx/site.conf\ndefault.vcl\n", encoding="utf-8")
        result = self.check(CQA_STUB_MISSING="nginx,varnish")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({}, self.config_runs())
        self.assertEqual(
            ["head\tskip\tnginx -t\tnginx/site.conf\timage nginx absente de la boîte à outils : "
             "lancez cqa-toolbox.sh pull",
             "head\tskip\tvarnishd -C\tdefault.vcl\timage varnish absente de la boîte à outils : "
             "lancez cqa-toolbox.sh pull"],
            (self.check_dir / "config.tsv").read_text(encoding="utf-8").splitlines())
        result = self.check(CQA_STUB_FAIL="--tmpfs /var/cache/nginx")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("head\tskip\tnginx -t\tnginx/site.conf\tcontrôle nginx impossible dans la boîte à "
                      "outils (code 1)",
                      (self.check_dir / "config.tsv").read_text(encoding="utf-8").splitlines())

    def fake_bin(self, name, code):
        folder = self.tmp / "fake-bin"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text("#!/usr/bin/env python3\n" + code, encoding="utf-8")
        path.chmod(0o755)
        return folder

    def test_nginx_script_renders_templates_and_wraps_partial_files(self):
        etc = self.tmp / "etc-nginx"
        self.put(etc, "nginx.conf", "events {}\nhttp { include /etc/nginx/conf.d/*.conf; }\n")
        self.put(etc, "fastcgi_params", "fastcgi_param A b;\n")
        root = self.tmp / "fake-root"
        repo = root / "repos" / "front"
        self.put(repo, "docker/nginx/nginx.conf", "events {}\nhttp {\n include /etc/nginx/conf.d/*.conf;\n}\n")
        self.put(repo, "docker/nginx/conf.d/default.conf", "server {\n BROKEN\n}\n")
        self.put(repo, "docker/nginx/snippets/deny.conf", "location ~ /\\. { deny all; }\n")
        self.put(repo, "docker/nginx/templates/site.conf.template",
                 "server { listen ${APP_PORT}; server_name ${SERVER_NAME}; set $x ${host}; }\n")
        inbox = self.tmp / "in"
        self.put(inbox, "base/docker/nginx/conf.d/default.conf", "server {\n BROKEN\n}\n")
        self.put(inbox, "config-plan.tsv", "".join(
            f"nginx\tdocker/nginx\tdocker/nginx/{name}\n"
            for name in ("nginx.conf", "conf.d/default.conf", "snippets/deny.conf", "templates/site.conf.template")))
        work = self.tmp / "work"
        log = self.tmp / "nginx.log"
        fake = self.fake_bin("nginx", f"""import json, re, sys
args = sys.argv[1:]
conf = args[args.index("-c") + 1]
text = open(conf).read()
found = re.search(r'include "([^"]+)";', text)
target = found.group(1) if found else conf
body = open(target).read()
with open({str(log)!r}, "a") as handle:
    handle.write(json.dumps({{"args": args, "conf": text, "target": target}}) + "\\n")
level = "location" if "location / {{" in text else "server" if "server {{" in text else "http"
if "BROKEN" in body:
    sys.stderr.write('2026/10/30 10:00:00 [emerg] 7#7: unexpected "}}" in ' + target + ':2\\n')
    sys.stderr.write("nginx: configuration file " + conf + " test failed\\n")
    sys.exit(1)
if body.lstrip().startswith("location") and level == "http":
    sys.stderr.write('2026/10/30 10:00:00 [emerg] 7#7: "location" directive is not allowed here in ' + target + ':1\\n')
    sys.exit(1)
""")
        script = (embedded_script("NGINX_SCRIPT")
                  .replace("plan=/in/config-plan.tsv", f"plan={inbox}/config-plan.tsv")
                  .replace("base_root=/in/base", f"base_root={inbox}/base")
                  .replace('head_root="/repos/$repo"', f'head_root="{root}/repos/$repo"')
                  .replace("etc=/etc/nginx", f"etc={etc}")
                  .replace("work=/tmp/cqa", f"work={work}"))
        env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(["sh", "-c", script, "sh", "front"], capture_output=True, text=True, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertTrue(all(len(row) == 5 and row[2] == "nginx -t" for row in rows), rows)
        status = {(row[0], row[3]): (row[1], row[4]) for row in rows}
        self.assertEqual({
            ("head", "docker/nginx/nginx.conf"): ("ok", ""),
            ("head", "docker/nginx/conf.d/default.conf"):
                ("ko", 'unexpected "}" in docker/nginx/conf.d/default.conf:2'),
            ("head", "docker/nginx/snippets/deny.conf"): ("ok", ""),
            ("head", "docker/nginx/templates/site.conf.template"): ("ok", ""),
            ("base", "docker/nginx/conf.d/default.conf"):
                ("ko", 'unexpected "}" in docker/nginx/conf.d/default.conf:2'),
        }, status)
        copy = work / "head" / "docker" / "nginx"
        self.assertEqual("server { listen 1; server_name example.test; set $x ${host}; }\n",
                         (copy / "templates" / "site.conf").read_text(encoding="utf-8"))
        self.assertEqual(f"events {{}}\nhttp {{\n include {copy}/conf.d/*.conf;\n}}\n",
                         (copy / "nginx.conf").read_text(encoding="utf-8"))
        self.assertTrue((copy / "fastcgi_params").is_file())
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(call["args"][:3] == ["-t", "-q", "-e"] and call["args"][3] == "stderr"
                            for call in calls))
        self.assertIn(str(copy / "nginx.conf"), [call["args"][-1] for call in calls])
        deny = [call for call in calls if call["target"].endswith("snippets/deny.conf")]
        self.assertEqual(2, len(deny))
        self.assertNotIn("server {", deny[0]["conf"])
        self.assertIn("server {", deny[1]["conf"])
        self.assertFalse((repo / "docker" / "nginx" / "templates" / "site.conf").exists())

    def test_varnish_script_compiles_each_side_and_keeps_the_compiler_message(self):
        root = self.tmp / "fake-root"
        repo = root / "repos" / "front"
        self.put(repo, "docker/varnish/default.vcl", "vcl 4.1;\nBROKEN\n")
        self.put(repo, "docker/varnish/new.vcl", "vcl 4.1;\n")
        inbox = self.tmp / "in"
        self.put(inbox, "base/docker/varnish/default.vcl", "vcl 4.1;\n")
        self.put(inbox, "config-plan.tsv", "varnish\tdocker/varnish\tdocker/varnish/default.vcl\n"
                                           "varnish\tdocker/varnish\tdocker/varnish/new.vcl\n")
        state = self.tmp / "varnish-state"
        log = self.tmp / "varnishd.log"
        fake = self.fake_bin("varnishd", f"""import json, sys
args = sys.argv[1:]
with open({str(log)!r}, "a") as handle:
    handle.write(json.dumps(args) + "\\n")
path = args[args.index("-f") + 1]
if "BROKEN" in open(path).read():
    sys.stderr.write("Message from VCC-compiler:\\nExpected ';' got '}}'\\n(program line 410), at\\n"
                     "('" + path + "' Line 2 Pos 1)\\nBROKEN\\n#\\n\\nRunning VCC-compiler failed, exited with 2\\n"
                     "VCL compilation failed\\n")
    sys.exit(2)
sys.stderr.write("/* C code */\\n")
""")
        script = (embedded_script("VARNISH_SCRIPT")
                  .replace("plan=/in/config-plan.tsv", f"plan={inbox}/config-plan.tsv")
                  .replace("base_root=/in/base", f"base_root={inbox}/base")
                  .replace('head_root="/repos/$repo"', f'head_root="{root}/repos/$repo"')
                  .replace("state=/var/lib/varnish", f"state={state}")
                  .replace("err=/tmp/cqa-vcc.err", f"err={self.tmp}/vcc.err"))
        env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(["sh", "-c", script, "sh", "front"], capture_output=True, text=True, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            ["head", "ko", "varnishd -C", "docker/varnish/default.vcl",
             "Expected ';' got '}' ('docker/varnish/default.vcl' Line 2 Pos 1)"],
            ["base", "ok", "varnishd -C", "docker/varnish/default.vcl", ""],
            ["head", "ok", "varnishd -C", "docker/varnish/new.vcl", ""],
        ], [line.split("\t") for line in result.stdout.splitlines()])
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(3, len(calls))
        for call in calls:
            self.assertEqual("-C", call[0])
            folder = os.path.dirname(call[call.index("-f") + 1])
            self.assertEqual(f"vcl_path={folder}:/etc/varnish:/usr/share/varnish/vcl",
                             call[call.index("-p") + 1])
            self.assertTrue(call[call.index("-n") + 1].startswith(f"{state}/"))
        self.assertEqual(3, len({call[call.index("-n") + 1] for call in calls}))


def embedded_script(name):
    """Return the text of a sh script embedded in the launcher by read -r -d '' <name>."""
    match = re.search(rf"read -r -d '' {name} <<'SCRIPT' \|\| true\n(.*?)\nSCRIPT\n",
                      SCRIPT.read_text(encoding="utf-8"), re.DOTALL)
    if match is None:
        raise AssertionError(f"{name} absent du lanceur")
    return match.group(1)


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
