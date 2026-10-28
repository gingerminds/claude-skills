import base64
import contextlib
import copy
import gzip
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import urllib.response
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import cqa_plan
from cqa import target
from cqa.lots import TOOLS
from cqa.version import plugin_version
from tests.cqa import test_git_tools as git_fixtures

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GITLAB = "git@gitlab.example.org"
USER = "audit-user"
PASSWORD = "s3cret-pass-0123"
BASIC = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
PROXY_VARIABLES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}


def clean_environ(extra=None):
    """Patch os.environ without proxy, audit or git variables from the developer's shell."""
    kept = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in PROXY_VARIABLES
        and not key.startswith("GM_AUDIT_")
        and key not in ("GIT_TERMINAL_PROMPT", "GITLAB_HOST")
    }
    kept.update(extra or {})
    return mock.patch.dict(os.environ, kept, clear=True)


class CliCase(unittest.TestCase):
    def run_main(self, argv, env=None, run=subprocess.run, opener=None):
        """Run cqa_plan.main; return (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with clean_environ(env):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cqa_plan.main([str(item) for item in argv], run=run, opener=opener)
        return code, out.getvalue(), err.getvalue()

    def run_json(self, argv, **kwargs):
        code, out, err = self.run_main(argv, **kwargs)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def tmp_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name).resolve()


class _Handler(BaseHTTPRequestHandler):
    def _answer(self):
        self.server.requests.append((self.command, self.path, dict(self.headers)))
        status, headers = self.server.responder(self.command, self.path, self.headers)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = _answer
    do_GET = _answer

    def log_message(self, *args):
        pass


def start_server(case, responder):
    """Serve responder(method, path, headers) -> (status, headers) on 127.0.0.1."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.responder = responder
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def answer(status, **headers):
    return lambda method, path, request_headers: (status, headers)


def basic_gate(method, path, headers):
    if headers.get("Authorization") == BASIC:
        return 200, {}
    return 401, {"WWW-Authenticate": 'Basic realm="preprod"'}


class FakeNetwork(urllib.request.BaseHandler):
    """Handler that answers http and https requests from routes {url: (status, headers)}.

    The answers go through the real redirect and error processing of urllib, so a redirect
    is followed or refused by the code under test. A URL that is not routed fails the test.
    """

    handler_order = 100

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def _answer(self, request):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        status, headers = self.routes[request.full_url]
        message = http.client.HTTPMessage()
        for key, value in headers.items():
            message[key] = value
        response = urllib.response.addinfourl(io.BytesIO(b""), message, request.full_url, status)
        response.msg = "Answer"
        return response

    http_open = _answer
    https_open = _answer

    @property
    def urls(self):
        return [request[1] for request in self.requests]


class ClassifyAccessTest(unittest.TestCase):
    def test_classification(self):
        expected = {
            200: "open",
            204: "open",
            301: "open",
            399: "open",
            401: "basic_auth",
            403: "ip_filtered",
            199: "unreachable",
            400: "unreachable",
            404: "unreachable",
            405: "unreachable",
            500: "unreachable",
            503: "unreachable",
            None: "unreachable",
        }
        for status, access in expected.items():
            with self.subTest(status=status):
                self.assertEqual(target.classify_access(status), access)


class ProbeTargetTest(CliCase):
    def setUp(self):
        patcher = clean_environ()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_probe_target_403_is_ip_filtered(self):
        _, url = start_server(self, answer(403))
        self.assertEqual(
            target.probe_target(url), {"status": 403, "access": "ip_filtered"}
        )

    def test_probe_target_401_basic(self):
        _, url = start_server(self, basic_gate)
        self.assertEqual(target.probe_target(url), {"status": 401, "access": "basic_auth"})

    def test_probe_target_200_is_open(self):
        _, url = start_server(self, answer(200))
        self.assertEqual(target.probe_target(url), {"status": 200, "access": "open"})

    def test_auth_is_sent_as_basic_authorization(self):
        server, url = start_server(self, basic_gate)
        result = target.probe_target(url, auth=(USER, PASSWORD))
        self.assertEqual(result, {"status": 200, "access": "open"})
        self.assertEqual(server.requests[0][2]["Authorization"], BASIC)

    def test_head_is_used_first(self):
        server, url = start_server(self, answer(200))
        target.probe_target(url)
        self.assertEqual([request[0] for request in server.requests], ["HEAD"])

    def test_get_follows_a_405_on_head(self):
        def responder(method, path, headers):
            return (405, {}) if method == "HEAD" else (200, {})

        server, url = start_server(self, responder)
        self.assertEqual(target.probe_target(url), {"status": 200, "access": "open"})
        self.assertEqual([request[0] for request in server.requests], ["HEAD", "GET"])

    def test_request_carries_the_audit_user_agent(self):
        server, url = start_server(self, answer(200))
        target.probe_target(url)
        self.assertEqual(
            server.requests[0][2]["User-Agent"], f"GingermindsAudit/{plugin_version()}"
        )

    def test_same_origin_redirect_is_followed(self):
        def responder(method, path, headers):
            return (302, {"Location": "/fr/"}) if path == "/" else (200, {})

        server, url = start_server(self, responder)
        self.assertEqual(target.probe_target(url + "/"), {"status": 200, "access": "open"})
        self.assertEqual([request[1] for request in server.requests], ["/", "/fr/"])

    def test_redirect_to_another_origin_is_not_followed(self):
        other, other_url = start_server(self, answer(200))
        _, url = start_server(self, answer(302, Location=other_url + "/landing"))
        result = target.probe_target(url, auth=(USER, PASSWORD))
        self.assertEqual(
            result, {"status": 302, "access": "open", "location": other_url + "/landing"}
        )
        self.assertEqual(other.requests, [])

    def test_upgrade_to_another_port_is_not_followed(self):
        _, url = start_server(self, answer(301, Location="https://127.0.0.1:1/"))
        self.assertEqual(
            target.probe_target(url),
            {"status": 301, "access": "open", "location": "https://127.0.0.1:1/"},
        )

    def test_credentials_are_never_sent_without_the_flag(self):
        server, url = start_server(self, basic_gate)
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(target.probe_target(url), {"status": 401, "access": "basic_auth"})
        self.assertEqual(
            [key.lower() for key in server.requests[0][2] if key.lower() == "authorization"], []
        )

    def test_http_to_https_upgrade_is_followed_and_classified_on_the_final_answer(self):
        cases = {
            "http://preprod.example.org/": "https://preprod.example.org/",
            "http://preprod.example.org:8080/": "https://preprod.example.org:8080/",
            "http://preprod.example.org:80/": "https://preprod.example.org:443/",
            "http://Preprod.Example.org/": "https://preprod.example.org/",
        }
        for start, upgraded in cases.items():
            for final, access in ((401, "basic_auth"), (403, "ip_filtered"), (200, "open")):
                with self.subTest(start=start, final=final):
                    network = FakeNetwork(
                        {start: (301, {"Location": upgraded}), upgraded: (final, {})}
                    )
                    result = target.probe_target(start, opener=target.build_opener(network))
                    self.assertEqual(result, {"status": final, "access": access})
                    self.assertEqual(network.urls, [start, upgraded])

    def test_upgrade_keeps_following_on_the_https_origin(self):
        network = FakeNetwork(
            {
                "http://preprod.example.org/": (301, {"Location": "https://preprod.example.org/"}),
                "https://preprod.example.org/": (302, {"Location": "/fr/"}),
                "https://preprod.example.org/fr/": (401, {}),
            }
        )
        result = target.probe_target(
            "http://preprod.example.org/", opener=target.build_opener(network)
        )
        self.assertEqual(result, {"status": 401, "access": "basic_auth"})
        self.assertEqual(len(network.requests), 3)

    def test_credentials_follow_an_upgrade_on_the_same_host(self):
        network = FakeNetwork(
            {
                "http://127.0.0.1/": (301, {"Location": "https://127.0.0.1/"}),
                "https://127.0.0.1/": (200, {}),
            }
        )
        opener = target.build_opener(network, authenticated=True)
        result = target.probe_target("http://127.0.0.1/", auth=(USER, PASSWORD), opener=opener)
        self.assertEqual(result, {"status": 200, "access": "open"})
        self.assertEqual([request[2]["Authorization"] for request in network.requests], [BASIC] * 2)

    def test_https_to_http_downgrade_is_not_followed(self):
        network = FakeNetwork(
            {"https://preprod.example.org/": (301, {"Location": "http://preprod.example.org/"})}
        )
        result = target.probe_target(
            "https://preprod.example.org/", opener=target.build_opener(network)
        )
        self.assertEqual(
            result,
            {"status": 301, "access": "open", "location": "http://preprod.example.org/"},
        )
        self.assertEqual(len(network.requests), 1)

    def test_redirects_to_another_host_or_port_are_not_followed(self):
        cases = [
            ("https://preprod.example.org/", "https://www.example.org/"),
            ("https://preprod.example.org/", "https://preprod.example.org:8443/"),
            ("https://preprod.example.org:8443/", "http://preprod.example.org:8443/"),
            ("http://preprod.example.org/", "https://www.example.org/"),
            ("http://preprod.example.org/", "https://preprod.example.org:8443/"),
            ("http://preprod.example.org:8080/", "https://preprod.example.org/"),
            ("http://preprod.example.org/", "http://www.example.org/"),
            ("http://preprod.example.org/", "https://preprod.example.org.evil.example.net/"),
            ("http://preprod.example.org/", "https://user:pw@preprod.example.org/"),
        ]
        for start, location in cases:
            with self.subTest(start=start, location=location):
                network = FakeNetwork({start: (302, {"Location": location})})
                result = target.probe_target(start, opener=target.build_opener(network))
                self.assertEqual(
                    result, {"status": 302, "access": "open", "location": location}
                )
                self.assertEqual(network.urls, [start])

    def test_redirect_without_location_reports_none(self):
        network = FakeNetwork({"https://preprod.example.org/": (304, {})})
        result = target.probe_target(
            "https://preprod.example.org/", opener=target.build_opener(network)
        )
        self.assertEqual(result, {"status": 304, "access": "open"})

    def test_location_is_not_reported_for_other_statuses(self):
        network = FakeNetwork(
            {"https://preprod.example.org/": (403, {"Location": "https://www.example.org/"})}
        )
        result = target.probe_target(
            "https://preprod.example.org/", opener=target.build_opener(network)
        )
        self.assertEqual(result, {"status": 403, "access": "ip_filtered"})

    def test_credentials_are_refused_over_plain_http(self):
        class Opener:
            def open(self, request, timeout=None):
                raise AssertionError("no request expected")

        urls = ("http://preprod.example.org/", "http://preprod.example.net:8080/")
        for url in (*urls, "http://localhost.example.org"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    target.probe_target(url, auth=(USER, PASSWORD), opener=Opener())
                self.assertNotIn(PASSWORD, str(caught.exception))
                self.assertIn("https", str(caught.exception))

    def test_credentials_may_travel_to_a_loopback_address_over_http(self):
        for host in ("127.0.0.1", "localhost", "[::1]", "127.0.0.2"):
            with self.subTest(host=host):
                url = f"http://{host}/"
                network = FakeNetwork({url: (200, {})})
                result = target.probe_target(
                    url, auth=(USER, PASSWORD), opener=target.build_opener(network)
                )
                self.assertEqual(result["status"], 200)

    def test_credentials_are_sent_to_an_https_target(self):
        network = FakeNetwork({"https://preprod.example.org/": (200, {})})
        opener = target.build_opener(network, authenticated=True)
        result = target.probe_target(
            "https://preprod.example.org/", auth=(USER, PASSWORD), opener=opener
        )
        self.assertEqual(result, {"status": 200, "access": "open"})
        self.assertEqual(network.requests[0][2]["Authorization"], BASIC)

    def test_authenticated_opener_ignores_the_proxy_environment(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.example.org:3128"}):
            plain = target.build_opener()
            authenticated = target.build_opener(authenticated=True)

        def proxies(opener):
            return [
                handler.proxies
                for handler in opener.handlers
                if isinstance(handler, urllib.request.ProxyHandler)
            ]

        self.assertEqual(proxies(plain), [{"http": "http://proxy.example.org:3128"}])
        self.assertEqual([found for found in proxies(authenticated) if found], [])

    def test_credentials_do_not_reach_a_proxy_of_the_environment(self):
        proxy, proxy_url = start_server(self, answer(200))
        server, url = start_server(self, basic_gate)
        with mock.patch.dict(os.environ, {"http_proxy": proxy_url}):
            result = target.probe_target(url, auth=(USER, PASSWORD))
            unauthenticated = target.probe_target(url)
        self.assertEqual(result, {"status": 200, "access": "open"})
        self.assertEqual(server.requests[0][2]["Authorization"], BASIC)
        self.assertEqual(len(proxy.requests), 1)
        self.assertNotIn("Authorization", proxy.requests[0][2])
        self.assertEqual(unauthenticated, {"status": 200, "access": "open"})

    def test_unreachable_host(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.assertEqual(
            target.probe_target(f"http://127.0.0.1:{port}"),
            {"status": None, "access": "unreachable"},
        )

    def test_timeout_is_unreachable(self):
        def slow(method, path, headers):
            time.sleep(0.6)
            return 200, {}

        _, url = start_server(self, slow)
        self.assertEqual(
            target.probe_target(url, timeout=0.1), {"status": None, "access": "unreachable"}
        )

    def test_opener_is_injectable(self):
        seen = []

        class Response:
            def getcode(self):
                return 401

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Opener:
            def open(self, request, timeout=None):
                seen.append((request.get_method(), request.full_url, timeout))
                return Response()

        result = target.probe_target(
            "https://preprod.example.org/", opener=Opener(), timeout=3.5
        )
        self.assertEqual(result, {"status": 401, "access": "basic_auth"})
        self.assertEqual(seen, [("HEAD", "https://preprod.example.org/", 3.5)])

    def test_unsupported_urls_are_refused(self):
        refused = (
            "ftp://preprod.example.org/",
            "file:///etc/hostname",
            "preprod.example.org",
            "https://",
            f"https://{USER}:{PASSWORD}@preprod.example.org/",
            "https://preprod.example.org/ x",
            "https://preprod.example.org:notaport/",
        )
        for url in refused:
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    target.probe_target(url)
                self.assertNotIn(PASSWORD, str(caught.exception))


class ProbeTargetCommandTest(CliCase):
    def test_probe_target_cli_403(self):
        _, url = start_server(self, answer(403))
        self.assertEqual(
            self.run_json(["probe-target", url]), {"status": 403, "access": "ip_filtered"}
        )

    def test_probe_target_auth_env(self):
        server, url = start_server(self, basic_gate)
        self.assertEqual(
            self.run_json(["probe-target", url]), {"status": 401, "access": "basic_auth"}
        )
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.run_main(["probe-target", url, "--auth-env"], env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"status": 200, "access": "open"})
        self.assertNotIn(PASSWORD, out + err)
        self.assertEqual(server.requests[-1][2]["Authorization"], BASIC)

    def test_auth_env_requires_both_variables(self):
        _, url = start_server(self, basic_gate)
        for env in ({}, {"GM_AUDIT_AUTH_USER": USER}, {"GM_AUDIT_AUTH_PASS": PASSWORD}):
            with self.subTest(env=sorted(env)):
                code, out, err = self.run_main(["probe-target", url, "--auth-env"], env=env)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("GM_AUDIT_AUTH_USER", err)
                self.assertNotIn(PASSWORD, err)

    def test_credentials_in_the_environment_are_not_sent_without_the_flag(self):
        server, url = start_server(self, basic_gate)
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.run_main(["probe-target", url], env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"status": 401, "access": "basic_auth"})
        self.assertEqual(len(server.requests), 1)
        self.assertNotIn("authorization", [key.lower() for key in server.requests[0][2]])

    def test_auth_env_refuses_a_clear_text_target(self):
        class Opener:
            def open(self, request, timeout=None):
                raise AssertionError("no request expected")

        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.run_main(
            ["probe-target", "http://preprod.example.org/", "--auth-env"], env=env, opener=Opener()
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("https", err)
        self.assertIn("clair", err)
        self.assertNotIn(PASSWORD, err)

    def test_auth_env_accepts_an_https_target(self):
        network = FakeNetwork({"https://preprod.example.org/": (200, {})})
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.run_main(
            ["probe-target", "https://preprod.example.org/", "--auth-env"],
            env=env,
            opener=target.build_opener(network, authenticated=True),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"status": 200, "access": "open"})
        self.assertEqual(network.requests[0][2]["Authorization"], BASIC)

    def test_unfollowed_redirect_reports_its_location(self):
        network = FakeNetwork(
            {"https://preprod.example.org/": (302, {"Location": "https://sso.example.net/login"})}
        )
        self.assertEqual(
            self.run_json(
                ["probe-target", "https://preprod.example.org/"],
                opener=target.build_opener(network),
            ),
            {"status": 302, "access": "open", "location": "https://sso.example.net/login"},
        )

    def test_refused_url_is_a_usage_error(self):
        code, out, err = self.run_main(["probe-target", f"https://{USER}:{PASSWORD}@x.example.org"])
        self.assertEqual(code, 2)
        self.assertNotIn(PASSWORD, out + err)
        self.assertNotEqual(err, "")

    def test_unreachable_is_reported_not_failed(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.assertEqual(
            self.run_json(["probe-target", f"http://127.0.0.1:{port}"]),
            {"status": None, "access": "unreachable"},
        )

    def test_injected_opener_is_used(self):
        class Opener:
            def open(self, request, timeout=None):
                raise OSError("no network")

        self.assertEqual(
            self.run_json(["probe-target", "https://preprod.example.org"], opener=Opener()),
            {"status": None, "access": "unreachable"},
        )


def valid_context():
    sha = "0123456789abcdef0123456789abcdef01234567"
    return {
        "schema": 1,
        "run_id": "2026-10-20-1430",
        "plugin_version": "0.9.0",
        "project": {
            "slug": "acme-shop",
            "name": "Acme - Shop",
            "protection": "Basic Auth sur preprod",
        },
        "lead": {"name": "Côme Dorval", "option": "Côme D"},
        "team": [{"name": "Inès Morel", "role": "front", "option": "Inès"}],
        "repos": [
            {
                "name": "laravel",
                "path": "/work/Acme - Shop - Laravel",
                "remote": f"{GITLAB}:acme/back.git",
                "web_url": "https://gitlab.example.org/acme/back",
                "stack": "laravel",
                "techno": "Laravel",
                "audited_sha": sha,
                "preprod_branch": "release",
                "deployed_sha": None,
                "aligned": None,
            },
            {
                "name": "nuxt-3",
                "path": "/work/Acme - Shop - Nuxt 3",
                "remote": f"{GITLAB}:acme/front.git",
                "web_url": "https://gitlab.example.org/acme/front",
                "stack": "vue",
                "techno": "Nuxt",
                "audited_sha": sha,
                "preprod_branch": "release",
                "deployed_sha": sha,
                "aligned": True,
            },
        ],
        "technos": ["Laravel", "Nuxt"],
        "target": {
            "preprod_url": "https://preprod.example.org",
            "prod_url": None,
            "prod_live": False,
            "access": "open",
        },
        "ssh": {"alias": None, "app_dirs": {}},
        "test_accounts": {"front": False, "backoffice": False},
        "key_pages": [{"template": "Accueil", "url": "https://preprod.example.org/"}],
        "notion": {
            "copy_page_id": None,
            "copy_data_source_id": "0123456789abcdef0123456789abcdef",
            "reference_data_source_id": "fedcba9876543210fedcba9876543210",
            "bot_id": "00112233445566778899aabbccddeeff",
            "assignee_options": ["Inès", "Côme D"],
        },
        "available_tools": ["Code", "CLI local", "HTTP"],
        "phase_filter": None,
        "dry_run": False,
    }


def delete_path(document, dotted):
    parts = [int(part) if part.isdigit() else part for part in dotted.split(".")]
    node = document
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]


def set_path(document, dotted, value):
    parts = [int(part) if part.isdigit() else part for part in dotted.split(".")]
    node = document
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


class RunDirCase(CliCase):
    def setUp(self):
        self.run_dir = self.tmp_dir() / "2026-10-20-1430"
        self.run_dir.mkdir()

    def write_context(self, context=None, **changes):
        document = valid_context() if context is None else context
        for dotted, value in changes.items():
            set_path(document, dotted.replace("__", "."), value)
        (self.run_dir / "context.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        return document

    def check(self, context):
        self.write_context(context)
        code, out, err = self.run_main(["context", "--check", self.run_dir])
        return code, json.loads(out), err


class ContextCheckTest(RunDirCase):
    def test_valid_context(self):
        code, report, _ = self.check(valid_context())
        self.assertEqual((code, report), (0, {"ok": True, "errors": []}))

    def test_reference_page_id_is_optional(self):
        for value in ("fedcba9876543210fedcba9876543210", None):
            with self.subTest(value=value):
                context = valid_context()
                context["notion"]["reference_page_id"] = value
                code, report, _ = self.check(context)
                self.assertEqual((code, report["errors"]), (0, []))

    def test_reference_page_id_must_be_a_text_or_null(self):
        for value in (3, "  ", ["x"]):
            with self.subTest(value=value):
                context = valid_context()
                context["notion"]["reference_page_id"] = value
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn("notion.reference_page_id", report["errors"][0])

    def test_sql_skip_ssl_is_an_optional_boolean(self):
        for value in (True, False):
            with self.subTest(value=value):
                context = valid_context()
                context["ssh"]["sql_skip_ssl"] = value
                code, report, _ = self.check(context)
                self.assertEqual((code, report["errors"]), (0, []))
        for value in ("true", None, 1):
            with self.subTest(value=value):
                context = valid_context()
                context["ssh"]["sql_skip_ssl"] = value
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertIn("ssh.sql_skip_ssl", report["errors"][0])
                self.assertIn("booléen", report["errors"][0])

    def test_mode_and_notion_write_are_optional_choices(self):
        for key, values in (
            ("mode", ("autonome", "interactif")),
            ("notion_write", ("all", "keep_human", "none", None)),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    context = valid_context()
                    context[key] = value
                    code, report, _ = self.check(context)
                    self.assertEqual((code, report["errors"]), (0, []))
        code, report, _ = self.check(valid_context())
        self.assertEqual((code, report["errors"]), (0, []))

    def test_mode_and_notion_write_refuse_other_values(self):
        for key, value in (
            ("mode", "auto"),
            ("mode", None),
            ("mode", True),
            ("notion_write", "tout"),
            ("notion_write", ""),
            ("notion_write", False),
        ):
            with self.subTest(key=key, value=value):
                context = valid_context()
                context[key] = value
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn(f"« {key} »", report["errors"][0])
        context = valid_context()
        context["mode"] = "auto"
        self.assertIn("autonome", self.check(context)[1]["errors"][0])
        context = valid_context()
        context["notion_write"] = "tout"
        self.assertIn("keep_human", self.check(context)[1]["errors"][0])

    def test_crawl_minutes_is_optional_and_checked(self):
        for value in (60, 5, 240):
            with self.subTest(value=value):
                context = valid_context()
                context["crawl_minutes"] = value
                code, report, _ = self.check(context)
                self.assertEqual((code, report["errors"]), (0, []))
        for value in (4, 241, "60", 60.5, True, None):
            with self.subTest(value=value):
                context = valid_context()
                context["crawl_minutes"] = value
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn("« crawl_minutes »", report["errors"][0])
                self.assertIn("entier de minutes entre 5 et 240", report["errors"][0])

    def _site(self, domain_name="shop.example.org", **changes):
        domain = domain_name
        site = {
            "domain": domain,
            "backoffice": "commun",
            "sso": False,
            "home": f"https://{domain}/",
        }
        site.update(changes)
        return site

    def test_sites_are_optional_and_validated(self):
        code, report, _ = self.check(valid_context())
        self.assertEqual((code, report["errors"]), (0, []))
        context = valid_context()
        context["sites"] = [
            self._site("a.example.org"),
            self._site("b.example.org", backoffice="propre", sso=True),
        ]
        self.assertEqual(self.check(context)[:2][1]["errors"], [])
        context["sites"] = []
        self.assertEqual(self.check(context)[0], 0)

    def test_sites_refuse_bad_shapes(self):
        cases = {
            "not a list": ({"domain": "a.example.org"}, "sites"),
            "not objects": (["a.example.org"], "sites[0]"),
        }
        for label, (value, path) in cases.items():
            with self.subTest(label=label):
                context = valid_context()
                context["sites"] = value
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertIn(f"« {path} »", report["errors"][0])
        for field, value, needle in (
            ("backoffice", "partagé", "commun"),
            ("backoffice", None, "propre"),
            ("sso", "oui", "booléen"),
            ("sso", None, "booléen"),
            ("domain", "https://a.example.org", "nom d'hôte"),
            ("domain", "a.example.org/blog", "nom d'hôte"),
            ("domain", "", "nom d'hôte"),
            ("home", "", "URL"),
            ("home", "a.example.org", "URL"),
            ("home", "https://autre.example.org/", "a.example.org"),
        ):
            with self.subTest(field=field, value=value):
                context = valid_context()
                context["sites"] = [self._site("a.example.org", **{field: value})]
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn(f"sites[0].{field}", report["errors"][0])
                self.assertIn(needle, report["errors"][0])
        context = valid_context()
        site = self._site("a.example.org")
        del site["sso"]
        context["sites"] = [site]
        self.assertIn("sites[0].sso", self.check(context)[1]["errors"][0])

    def test_sites_refuse_duplicates_and_more_than_ten(self):
        context = valid_context()
        context["sites"] = [self._site("a.example.org"), self._site("A.example.org")]
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertIn("deux fois", report["errors"][0])
        context["sites"] = [self._site(f"s{n}.example.org") for n in range(10)]
        self.assertEqual(self.check(context)[0], 0)
        context["sites"] = [self._site(f"s{n}.example.org") for n in range(11)]
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertIn("10 éléments au plus", report["errors"][0])

    def test_nullable_values_are_accepted(self):
        context = valid_context()
        context["lead"]["option"] = None
        context["team"][0].update(role=None, option=None)
        context["repos"][0].update(
            remote=None, web_url=None, stack=None, techno=None, audited_sha=None
        )
        context["target"].update(preprod_url=None, access=None)
        context["project"]["protection"] = None
        context["phase_filter"] = "MEL J0"
        context["technos"] = []
        code, report, _ = self.check(context)
        self.assertEqual((code, report["errors"]), (0, []))

    def test_every_required_key_is_checked(self):
        required = [
            "schema", "run_id", "plugin_version", "project", "lead", "team", "repos", "technos",
            "target", "ssh", "test_accounts", "key_pages", "notion", "available_tools",
            "phase_filter", "dry_run",
            "project.slug", "project.name", "project.protection",
            "lead.name", "lead.option",
            "team.0.name", "team.0.role", "team.0.option",
            "repos.0.name", "repos.0.path", "repos.0.remote", "repos.0.web_url",
            "repos.0.stack", "repos.0.techno", "repos.0.audited_sha",
            "repos.0.preprod_branch", "repos.0.deployed_sha", "repos.0.aligned",
            "target.preprod_url", "target.prod_url", "target.prod_live", "target.access",
            "ssh.alias", "ssh.app_dirs",
            "test_accounts.front", "test_accounts.backoffice",
            "key_pages.0.template", "key_pages.0.url",
            "notion.copy_page_id", "notion.copy_data_source_id",
            "notion.reference_data_source_id", "notion.bot_id", "notion.assignee_options",
        ]
        for dotted in required:
            with self.subTest(key=dotted):
                context = valid_context()
                delete_path(context, dotted)
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertFalse(report["ok"])
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn(dotted.replace(".0.", "[0]."), report["errors"][0])

    def test_context_check_rejects_11_pages(self):
        context = valid_context()
        context["key_pages"] = [
            {"template": f"Gabarit {index}", "url": f"https://preprod.example.org/{index}"}
            for index in range(11)
        ]
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("10", report["errors"][0])

    def test_ten_pages_are_accepted(self):
        context = valid_context()
        context["key_pages"] = [
            {"template": f"Gabarit {index}", "url": f"https://preprod.example.org/{index}"}
            for index in range(10)
        ]
        code, report, _ = self.check(context)
        self.assertEqual((code, report["errors"]), (0, []))

    def test_available_tools_must_belong_to_tools(self):
        context = valid_context()
        context["available_tools"] = ["Code", "Telepathie"]
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("Telepathie", report["errors"][0])

    def test_every_known_tool_is_accepted(self):
        context = valid_context()
        context["available_tools"] = list(TOOLS)
        code, report, _ = self.check(context)
        self.assertEqual((code, report["errors"]), (0, []))

    def test_wrong_types_are_reported(self):
        cases = {
            "schema": 2,
            "run_id": 7,
            "technos": "Laravel",
            "dry_run": "no",
            "target.prod_live": "false",
            "target.access": "maybe",
            "repos": [],
            "repos.0.name": "two words",
            "repos.0.aligned": "yes",
            "team": {"name": "Inès"},
            "notion.assignee_options": [1, 2],
            "key_pages.0.url": None,
            "ssh.app_dirs": [],
            "available_tools": "Code",
        }
        for dotted, value in cases.items():
            with self.subTest(key=dotted):
                context = valid_context()
                set_path(context, dotted, value)
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertTrue(report["errors"])
                self.assertIn(dotted.replace(".0.", "[0]."), report["errors"][0])

    def test_app_dirs_take_a_host_path_or_host_and_container(self):
        for app_dirs in (
            {"laravel": "/srv/app", "nuxt-3": {"host": "/opt/shop/front", "container": "/app"}},
            {"laravel": {"container": "/var/www/html"}},
            {"laravel": {"host": None, "container": "/var/www/html"}},
            {"laravel": None},
        ):
            with self.subTest(app_dirs=app_dirs):
                context = valid_context()
                context["ssh"]["app_dirs"] = app_dirs
                code, report, _ = self.check(context)
                self.assertEqual((code, report), (0, {"ok": True, "errors": []}))

    def test_legacy_app_dirs_are_tolerated_with_a_warning(self):
        context = valid_context()
        context["ssh"]["app_dirs"] = {"laravel": None, "nuxt-3": "www/front", "api": ""}
        code, report, _ = self.check(context)
        self.assertEqual((code, report["ok"], report["errors"]), (0, True, []))
        self.assertEqual(len(report["warnings"]), 2, report["warnings"])
        for warning, name in zip(report["warnings"], ("nuxt-3", "api")):
            self.assertIn(f"« ssh.app_dirs.{name} »", warning)
            self.assertIn("ancien run", warning)
            self.assertIn("redemander", warning)
            self.assertNotIn(chr(0x2014), warning)

    def test_app_dirs_invalid_forms_are_reported(self):
        for value, path, needle in (
            ("/srv/app\n", "ssh.app_dirs.laravel", "retour à la ligne"),
            ("srv\tapp", "ssh.app_dirs.laravel", "tabulation"),
            (3, "ssh.app_dirs.laravel", '{"host"'),
            (True, "ssh.app_dirs.laravel", '{"host"'),
            (["/srv/app"], "ssh.app_dirs.laravel", '{"host"'),
            ({"host": "/srv/app"}, "ssh.app_dirs.laravel.container", "absent"),
            ({}, "ssh.app_dirs.laravel.container", "absent"),
            ({"host": "/srv/app", "container": None}, "ssh.app_dirs.laravel.container",
             "chemin absolu"),
            ({"host": "/srv/app", "container": "app"}, "ssh.app_dirs.laravel.container",
             "chemin absolu"),
            ({"host": "", "container": "/app"}, "ssh.app_dirs.laravel.host", "chemin absolu"),
            ({"host": "srv/app", "container": "/app"}, "ssh.app_dirs.laravel.host",
             "chemin absolu"),
            ({"host": "/srv/app", "container": "/app", "service": "php"},
             "ssh.app_dirs.laravel", "« service »"),
        ):
            with self.subTest(value=value):
                context = valid_context()
                context["ssh"]["app_dirs"] = {"laravel": value}
                code, report, _ = self.check(context)
                self.assertEqual(code, 6)
                self.assertEqual(len(report["errors"]), 1, report["errors"])
                self.assertIn(f"« {path} »", report["errors"][0])
                self.assertIn(needle, report["errors"][0])
                self.assertNotIn(chr(0x2014), report["errors"][0])

    def test_duplicate_repo_names_are_rejected(self):
        context = valid_context()
        context["repos"][1]["name"] = "laravel"
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertIn("laravel", report["errors"][0])

    def test_live_prod_needs_a_prod_url(self):
        context = valid_context()
        context["target"]["prod_live"] = True
        code, report, _ = self.check(context)
        self.assertEqual(code, 6)
        self.assertIn("prod_url", report["errors"][0])

    def test_all_errors_are_listed(self):
        context = valid_context()
        del context["project"]
        del context["target"]
        context["available_tools"] = ["Nope"]
        _, report, _ = self.check(context)
        self.assertEqual(len(report["errors"]), 3)

    def test_missing_context_file(self):
        code, out, err = self.run_main(["context", "--check", self.run_dir])
        self.assertEqual(code, 6)
        report = json.loads(out)
        self.assertFalse(report["ok"])
        self.assertIn("context.json", report["errors"][0])

    def test_invalid_json(self):
        (self.run_dir / "context.json").write_text("{not json", encoding="utf-8")
        code, out, _ = self.run_main(["context", "--check", self.run_dir])
        self.assertEqual(code, 6)
        self.assertIn("context.json", json.loads(out)["errors"][0])

    def test_not_an_object(self):
        (self.run_dir / "context.json").write_text("[]", encoding="utf-8")
        code, out, _ = self.run_main(["context", "--check", self.run_dir])
        self.assertEqual(code, 6)
        self.assertFalse(json.loads(out)["ok"])

    def test_check_flag_is_required(self):
        self.write_context()
        code, out, _ = self.run_main(["context", self.run_dir])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")


def make_repo(parent, name, *, remote=None, files=None):
    path = Path(parent) / name
    path.mkdir(parents=True)
    git_fixtures.git(path, "init", "-q")
    for relative, content in (files or {}).items():
        file = path / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    if remote:
        git_fixtures.git(path, "remote", "add", "origin", remote)
    return path


def composer(*packages, dev=()):
    return json.dumps(
        {
            "require": {name: "*" for name in packages},
            "require-dev": {name: "*" for name in dev},
        }
    )


def package_json(*dependencies, dev=()):
    return json.dumps(
        {
            "dependencies": {name: "*" for name in dependencies},
            "devDependencies": {name: "*" for name in dev},
        }
    )


REPO_KEYS = [
    "name", "path", "project_md", "protected", "remote", "stack", "techno", "web_url",
]


class ProjectCommandTest(CliCase):
    def setUp(self):
        self.parent = self.tmp_dir()

    def project(self, path):
        return self.run_json(["project", path])

    def repo_entry(self, files, name="Acme - Shop - Laravel", remote=f"{GITLAB}:acme/back.git"):
        path = make_repo(self.parent, name, remote=remote, files=files)
        return self.project(path)["repos"][0]

    def test_project_detects_siblings(self):
        project_md = (FIXTURES / "PROJECT.md").read_text(encoding="utf-8")
        laravel = make_repo(
            self.parent,
            "Acme - Shop - Laravel",
            remote=f"{GITLAB}:acme/shop-back.git",
            files={"composer.json": composer("laravel/framework"), "PROJECT.md": project_md},
        )
        nuxt = make_repo(
            self.parent,
            "Acme - Shop - Nuxt 3",
            remote="https://gitlab.example.org/acme/shop-front.git",
            files={"package.json": package_json("nuxt", "vue")},
        )
        make_repo(self.parent, "Acme - Shop - Legacy", remote=f"{GITLAB}:other/legacy.git")
        make_repo(self.parent, "Acme - Blog - Laravel", remote=f"{GITLAB}:acme/blog-back.git")
        make_repo(self.parent, "Acme - Shop - Elsewhere", remote="git@git.example.net:acme/x.git")
        (self.parent / "Acme - Shop - Notes").mkdir()

        result = self.project(laravel)

        self.assertEqual(result["slug"], "acme-shop")
        self.assertEqual(result["name"], "Acme - Shop")
        self.assertEqual(len(result["repos"]), 1)
        repo = result["repos"][0]
        self.assertEqual(sorted(repo), REPO_KEYS)
        self.assertEqual(repo["path"], str(laravel))
        self.assertEqual(repo["name"], "laravel")
        self.assertEqual((repo["stack"], repo["techno"]), ("laravel", "Laravel"))
        self.assertEqual(repo["remote"], f"{GITLAB}:acme/shop-back.git")
        self.assertEqual(repo["web_url"], "https://gitlab.example.org/acme/shop-back")
        self.assertEqual(repo["project_md"]["lead"], "Côme Dorval")
        self.assertEqual(repo["project_md"]["branches"], {"prod": "master", "preprod": "release"})
        self.assertTrue(repo["protected"])

        self.assertEqual([item["path"] for item in result["candidates"]], [str(nuxt)])
        candidate = result["candidates"][0]
        self.assertEqual(sorted(candidate), REPO_KEYS)
        self.assertEqual(candidate["name"], "nuxt-3")
        self.assertEqual((candidate["stack"], candidate["techno"]), ("vue", "Nuxt"))
        self.assertEqual(candidate["web_url"], "https://gitlab.example.org/acme/shop-front")
        self.assertIsNone(candidate["project_md"])
        self.assertFalse(candidate["protected"])

    def test_project_from_a_subdirectory_resolves_the_repo(self):
        repo = make_repo(
            self.parent,
            "Acme - Shop - Laravel",
            remote=f"{GITLAB}:acme/back.git",
            files={"app/Http/.keep": ""},
        )
        result = self.project(repo / "app" / "Http")
        self.assertEqual(result["repos"][0]["path"], str(repo))
        self.assertEqual(result["slug"], "acme-shop")

    def test_project_without_siblings(self):
        repo = make_repo(self.parent, "Acme - Shop - Laravel", remote=f"{GITLAB}:acme/back.git")
        self.assertEqual(self.project(repo)["candidates"], [])

    def test_project_without_remote_has_no_candidates(self):
        repo = make_repo(self.parent, "Acme - Shop - Laravel")
        make_repo(self.parent, "Acme - Shop - Nuxt 3", remote=f"{GITLAB}:acme/front.git")
        result = self.project(repo)
        entry = result["repos"][0]
        self.assertEqual((entry["remote"], entry["web_url"]), (None, None))
        self.assertEqual(result["candidates"], [])

    def test_project_with_unsupported_remote(self):
        repo = make_repo(self.parent, "Acme - Shop - Laravel", remote="/srv/git/back.git")
        entry = self.project(repo)["repos"][0]
        self.assertEqual(entry["remote"], "/srv/git/back.git")
        self.assertIsNone(entry["web_url"])

    def test_remote_credentials_are_not_printed(self):
        repo = make_repo(
            self.parent,
            "Acme - Shop - Laravel",
            remote=f"https://oauth2:{PASSWORD}@gitlab.example.org/acme/back.git",
        )
        code, out, err = self.run_main(["project", repo])
        self.assertEqual(code, 0, err)
        self.assertNotIn(PASSWORD, out + err)
        entry = json.loads(out)["repos"][0]
        self.assertEqual(entry["remote"], "https://gitlab.example.org/acme/back.git")
        self.assertEqual(entry["web_url"], "https://gitlab.example.org/acme/back")

    def test_ssh_remote_user_is_kept(self):
        repo = make_repo(
            self.parent, "Acme - Shop - Laravel", remote="ssh://git@gitlab.example.org:2222/a/b.git"
        )
        self.assertEqual(
            self.project(repo)["repos"][0]["remote"], "ssh://git@gitlab.example.org:2222/a/b.git"
        )

    def test_single_part_directory_name(self):
        repo = make_repo(self.parent, "Acme", remote=f"{GITLAB}:acme/back.git")
        result = self.project(repo)
        self.assertEqual((result["slug"], result["name"]), ("acme", "Acme"))
        self.assertEqual(result["repos"][0]["name"], "acme")

    def test_not_a_git_repository(self):
        plain = self.parent / "Acme - Shop - Laravel"
        plain.mkdir()
        code, out, err = self.run_main(["project", plain])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("dépôt git", err)

    def test_missing_directory(self):
        code, _, err = self.run_main(["project", self.parent / "nowhere"])
        self.assertEqual(code, 2)
        self.assertNotEqual(err, "")

    def test_stack_detection(self):
        cases = [
            ("drupal", {"composer.json": composer("drupal/core-recommended")}, "drupal", "Drupal"),
            ("drupal bare", {"composer.json": composer("drupal/core")}, "drupal", "Drupal"),
            ("drupal dev", {"composer.json": composer(dev=("drupal/core",))}, "drupal", "Drupal"),
            ("roots", {"composer.json": composer("roots/wordpress")}, "wordpress", "Wordpress"),
            ("wp-content", {"wp-content/index.php": ""}, "wordpress", "Wordpress"),
            ("johnpbloch", {"composer.json": composer("johnpbloch/wordpress")}, "wordpress", "Wordpress"),
            ("wpackagist plugin", {"composer.json": composer("wpackagist-plugin/wordpress-seo")}, "wordpress", "Wordpress"),
            ("wpackagist theme", {"composer.json": composer(dev=("wpackagist-theme/twentytwentyfive",))}, "wordpress", "Wordpress"),
            ("laravel", {"composer.json": composer("laravel/framework")}, "laravel", "Laravel"),
            (
                "laravel with a vue front",
                {
                    "composer.json": composer("laravel/framework"),
                    "package.json": package_json("vue"),
                },
                "laravel",
                "Laravel",
            ),
            ("nuxt", {"package.json": package_json("nuxt", "vue")}, "vue", "Nuxt"),
            ("nuxt alone", {"package.json": package_json("nuxt")}, "vue", "Nuxt"),
            ("nuxt in dev", {"package.json": package_json(dev=("nuxt",))}, "vue", "Nuxt"),
            ("plain vue", {"package.json": package_json("vue")}, "vue", None),
            ("generic php", {"composer.json": composer("monolog/monolog")}, None, None),
            ("generic js", {"package.json": package_json("lodash")}, None, None),
            ("empty", {}, None, None),
            ("broken composer", {"composer.json": "{nope"}, None, None),
            ("broken package", {"package.json": "[1, 2"}, None, None),
            ("weird composer", {"composer.json": json.dumps({"require": ["a"]})}, None, None),
            ("composer list", {"composer.json": "[]"}, None, None),
        ]
        for index, (label, files, stack, techno) in enumerate(cases):
            with self.subTest(label):
                name = f"Client{index} - Shop - Repo"
                path = make_repo(self.parent, name, files=files)
                entry = self.project(path)["repos"][0]
                self.assertEqual((entry["stack"], entry["techno"]), (stack, techno))

    def test_protected_follows_project_md(self):
        cases = [
            ("Basic Auth sur preprod", True),
            ("Oui", True),
            ("IP de l'agence uniquement", True),
            ("Non", False),
            ("non", False),
            ("NON", False),
            ("Aucun", False),
            ("aucune", False),
            ("AUCUN", False),
            ("N/A", False),
            ("n/a", False),
            ("  Non  ", False),
            ("", False),
        ]
        for index, (value, expected) in enumerate(cases):
            with self.subTest(value=value):
                path = make_repo(
                    self.parent,
                    f"Client{index} - Shop - Repo",
                    files={"PROJECT.md": f"- **Accès protégés ?** : {value}\n"},
                )
                self.assertIs(self.project(path)["repos"][0]["protected"], expected)

    def test_protected_ignores_accents_and_case(self):
        path = make_repo(
            self.parent,
            "Client - Shop - Repo",
            files={"PROJECT.md": "- **Acces proteges ?** : AUCUNE\n"},
        )
        self.assertIs(self.project(path)["repos"][0]["protected"], False)

    def test_protected_is_false_without_the_label_or_the_file(self):
        with_file = make_repo(
            self.parent, "A - Shop - Repo", files={"PROJECT.md": "- **Lead technique** : Côme\n"}
        )
        without_file = make_repo(self.parent, "B - Shop - Repo")
        for path in (with_file, without_file):
            self.assertIs(self.project(path)["repos"][0]["protected"], False)
        self.assertIsNone(self.project(without_file)["repos"][0]["project_md"])
        self.assertIsNotNone(self.project(with_file)["repos"][0]["project_md"])


class TeamCommandTest(CliCase):
    def setUp(self):
        self.root = self.tmp_dir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "PROJECT.md").write_text(
            (FIXTURES / "PROJECT.md").read_text(encoding="utf-8"), encoding="utf-8"
        )

    def options_file(self, content, name="options.json"):
        path = self.root / name
        path.write_text(
            content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_team_matches_names_with_options(self):
        options = self.options_file(["Côme D", "Côme L", "Inès", "Aurèle", "Marie"])
        code, out, err = self.run_main(["team", self.repo, options])
        self.assertEqual(code, 0, err)
        self.assertIn("Inès", out)
        self.assertEqual(
            json.loads(out),
            {
                "lead": {"name": "Côme Dorval", "option": "Côme D"},
                "team": [
                    {"name": "Côme Dorval", "role": "fullstack", "option": "Côme D"},
                    {"name": "Inès Morel", "role": "front", "option": "Inès"},
                    {"name": "Aurèle Roussel", "role": "back", "option": "Aurèle"},
                ],
                "unmatched": [],
            },
        )

    def test_unmatched_names_are_listed(self):
        options = self.options_file(["Côme L", "Ines", "Marie"])
        result = self.run_json(["team", self.repo, options])
        self.assertEqual(result["lead"], {"name": "Côme Dorval", "option": None})
        self.assertEqual(
            [member["option"] for member in result["team"]], [None, "Ines", None]
        )
        self.assertEqual(result["unmatched"], ["Côme Dorval", "Aurèle Roussel"])

    def test_options_file_may_be_the_schema_output(self):
        options = self.options_file(
            {"ok": True, "problems": [], "assignee_options": ["Côme D", "Inès", "Aurèle"]}
        )
        result = self.run_json(["team", self.repo, options])
        self.assertEqual(result["unmatched"], [])

    def test_no_lead_and_no_devs(self):
        (self.repo / "PROJECT.md").write_text("# PROJECT.md\n", encoding="utf-8")
        options = self.options_file(["Côme D"])
        self.assertEqual(
            self.run_json(["team", self.repo, options]),
            {"lead": {"name": None, "option": None}, "team": [], "unmatched": []},
        )

    def test_missing_project_md(self):
        (self.repo / "PROJECT.md").unlink()
        options = self.options_file(["Côme D"])
        code, out, err = self.run_main(["team", self.repo, options])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("PROJECT.md", err)

    def test_invalid_options_file(self):
        cases = {
            "missing": self.root / "absent.json",
            "not json": self.options_file("{oops", "bad.json"),
            "wrong shape": self.options_file({"options": ["a"]}, "shape.json"),
            "not strings": self.options_file([1, 2], "numbers.json"),
        }
        for label, path in cases.items():
            with self.subTest(label):
                code, out, err = self.run_main(["team", self.repo, path])
                self.assertEqual((code, out), (2, ""))
                self.assertNotEqual(err, "")


class FakeRun:
    """run that answers glab itself and sends everything else to the real subprocess."""

    def __init__(self, glab=None):
        self.glab = glab or {}
        self.calls = []
        self.prompt_flags = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        self.prompt_flags.append(os.environ.get("GIT_TERMINAL_PROMPT"))
        if cmd[0] != "glab":
            return subprocess.run(cmd, **kwargs)
        for key, payload in self.glab.items():
            if key in cmd[2]:
                if isinstance(payload, BaseException):
                    raise payload
                return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 1, "", "404 Not Found")

    @property
    def glab_calls(self):
        return [call for call in self.calls if call[0][0] == "glab"]

    @property
    def git_calls(self):
        return [call for call in self.calls if call[0][0] == "git"]


class RepoStateCommandTest(git_fixtures.RepoFixture, CliCase):
    def test_repo_state(self):
        self.assertEqual(
            self.run_json(["repo-state", self.work]),
            {
                "branch": "develop",
                "clean": True,
                "head": self.first,
                "ahead": 0,
                "behind": 0,
                "fetched": True,
            },
        )

    def test_repo_state_counts_behind_after_fetch(self):
        self.push_from_seed("b.txt", "second commit")
        state = self.run_json(["repo-state", self.work])
        self.assertEqual((state["behind"], state["ahead"]), (1, 0))

    def test_repo_state_with_another_base(self):
        state = self.run_json(["repo-state", self.work, "--base", "nope"])
        self.assertEqual((state["ahead"], state["behind"]), (None, None))

    def test_not_a_repository(self):
        code, out, err = self.run_main(["repo-state", self.root])
        self.assertEqual((code, out), (2, ""))
        self.assertNotEqual(err, "")

    def test_main_disables_git_prompts(self):
        run = FakeRun()
        self.run_json(["repo-state", self.work], run=run)
        self.assertTrue(run.git_calls)
        self.assertEqual(set(run.prompt_flags), {"0"})

    def test_main_overrides_an_inherited_prompt_setting(self):
        run = FakeRun()
        self.run_json(["repo-state", self.work], run=run, env={"GIT_TERMINAL_PROMPT": "1"})
        self.assertEqual(set(run.prompt_flags), {"0"})


class AlignCommandTest(git_fixtures.RepoFixture, CliCase):
    def align(self, sha, run=subprocess.run):
        return self.run_json(["align", self.work, sha], run=run)

    def test_aligned(self):
        second = self.push_from_seed("b.txt", "second commit")
        self.assertEqual(
            self.align(second), {"aligned": True, "missing": [], "reason": None}
        )

    def test_fetches_before_comparing(self):
        second = self.push_from_seed("b.txt", "second commit")
        result = self.align(self.first)
        self.assertIs(result["aligned"], False)
        self.assertEqual(result["missing"], [{"sha": second[:7], "title": "second commit"}])

    def test_unknown_commit_keeps_its_reason(self):
        self.assertEqual(
            self.align(git_fixtures.UNKNOWN_SHA),
            {"aligned": None, "missing": [], "reason": "commit_inconnu"},
        )

    def test_short_sha_is_accepted(self):
        self.assertTrue(self.align(self.first[:7])["aligned"])

    def test_malformed_commits_are_refused_before_any_git_call(self):
        malformed = [
            "",
            "abc12",
            "--output=/tmp/x",
            "-1234567",
            "HEAD",
            "develop",
            "origin/develop",
            "HEAD~1",
            "g" * 10,
            "abcdef1; ls",
            "abcdef1 ",
            "a" * 65,
            "ABCDEF1234",
        ]
        for sha in malformed:
            with self.subTest(sha=sha):
                run = FakeRun()
                code, out, err = self.run_main(["align", self.work, "--", sha], run=run)
                self.assertEqual(code, 0, err)
                self.assertEqual(
                    json.loads(out), {"aligned": None, "missing": [], "reason": "commit_inconnu"}
                )
                self.assertEqual(run.calls, [])

    def test_maximum_length_hex_is_not_refused_by_the_format(self):
        run = FakeRun()
        code, out, _ = self.run_main(["align", self.work, "a" * 64], run=run)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["reason"], "commit_inconnu")
        self.assertTrue(run.git_calls)

    def test_failed_fetch_makes_aligned_unknown(self):
        git_fixtures.git(self.work, "remote", "set-url", "origin", str(self.root / "gone.git"))
        self.assertEqual(
            self.align(self.first),
            {"aligned": None, "missing": [], "reason": "base_non_a_jour"},
        )

    def test_failed_fetch_keeps_a_negative_result(self):
        second = self.push_from_seed("b.txt", "second commit")
        git_fixtures.git(self.work, "fetch", "-q", "origin")
        git_fixtures.git(self.work, "remote", "set-url", "origin", str(self.root / "gone.git"))
        result = self.align(self.first)
        self.assertIs(result["aligned"], False)
        self.assertIsNone(result["reason"])
        self.assertEqual(result["missing"], [{"sha": second[:7], "title": "second commit"}])

    def test_failed_fetch_keeps_unknown_commit_reason(self):
        git_fixtures.git(self.work, "remote", "set-url", "origin", str(self.root / "gone.git"))
        self.assertEqual(self.align(git_fixtures.UNKNOWN_SHA)["reason"], "commit_inconnu")

    def test_not_a_repository(self):
        code, out, err = self.run_main(["align", self.root, self.first])
        self.assertEqual((code, out), (2, ""))
        self.assertNotEqual(err, "")

    def test_main_disables_git_prompts(self):
        run = FakeRun()
        self.align(self.first, run=run)
        self.assertEqual(set(run.prompt_flags), {"0"})


DEPLOYMENTS = (
    "deployments?environment=preprod&status=success&order_by=finished_at&sort=desc&per_page=1"
)


class DeployedCommandTest(CliCase):
    def setUp(self):
        self.parent = self.tmp_dir()

    def repo(self, remote=f"{GITLAB}:acme/shop/back.git"):
        return make_repo(self.parent, "Acme - Shop - Laravel", remote=remote)

    def test_deployed_from_environment(self):
        repo = self.repo()
        run = FakeRun(
            {
                "/environments": [{"name": "preprod"}],
                "/deployments": [{"sha": "aaa111"}],
            }
        )
        result = self.run_json(["deployed", repo, "--branch", "release"], run=run)
        self.assertEqual(result, {"sha": "aaa111", "source": "environment"})
        self.assertEqual(
            [call[0] for call in run.glab_calls],
            [
                ["glab", "api", "projects/acme%2Fshop%2Fback/environments"],
                ["glab", "api", f"projects/acme%2Fshop%2Fback/{DEPLOYMENTS}"],
            ],
        )

    def test_deployed_from_pipeline_keeps_its_source(self):
        repo = self.repo()
        run = FakeRun({"/environments": [], "/pipelines": [{"sha": "bbb222"}]})
        result = self.run_json(["deployed", repo, "--branch", "release"], run=run)
        self.assertEqual(result, {"sha": "bbb222", "source": "pipeline"})
        self.assertIn("ref=release", run.glab_calls[-1][0][2])

    def test_glab_runs_in_the_repo_with_the_host_of_the_remote(self):
        repo = self.repo()
        run = FakeRun({"/environments": [{"name": "preprod"}], "/deployments": [{"sha": "a1"}]})
        self.run_json(["deployed", repo, "--branch", "release"], run=run)
        self.assertTrue(run.glab_calls)
        for _, kwargs in run.glab_calls:
            self.assertEqual(Path(kwargs["cwd"]).resolve(), repo)
            self.assertEqual(kwargs["env"]["GITLAB_HOST"], "gitlab.example.org")
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertIn("PATH", kwargs["env"])

    def test_host_comes_from_the_remote_of_each_repo(self):
        repo = self.repo(remote="ssh://git@git.example.net:2222/grp/sub/repo.git")
        run = FakeRun()
        self.run_json(["deployed", repo, "--branch", "release"], run=run)
        _, kwargs = run.glab_calls[0]
        self.assertEqual(kwargs["env"]["GITLAB_HOST"], "git.example.net")
        self.assertEqual(run.glab_calls[0][0][2], "projects/grp%2Fsub%2Frepo/environments")

    def test_inherited_gitlab_host_is_replaced(self):
        repo = self.repo()
        run = FakeRun()
        self.run_json(
            ["deployed", repo, "--branch", "release"],
            run=run,
            env={"GITLAB_HOST": "other.example.net"},
        )
        self.assertEqual(run.glab_calls[0][1]["env"]["GITLAB_HOST"], "gitlab.example.org")

    def test_environment_of_the_process_is_untouched(self):
        repo = self.repo()
        with clean_environ(), contextlib.redirect_stdout(io.StringIO()):
            code = cqa_plan.main(["deployed", str(repo), "--branch", "release"], run=FakeRun())
            self.assertEqual(code, 0)
            self.assertNotIn("GITLAB_HOST", os.environ)

    def test_nothing_found(self):
        repo = self.repo()
        result = self.run_json(["deployed", repo, "--branch", "release"], run=FakeRun())
        self.assertEqual(result, {"sha": None, "source": None})

    def test_glab_missing(self):
        repo = self.repo()
        result = self.run_json(
            ["deployed", repo, "--branch", "release"],
            run=FakeRun({"/environments": FileNotFoundError("glab")}),
        )
        self.assertEqual(result, {"sha": None, "source": None})

    def test_branch_is_optional(self):
        repo = self.repo()
        run = FakeRun({"/environments": [{"name": "preprod"}], "/deployments": [{"sha": "c3"}]})
        self.assertEqual(self.run_json(["deployed", repo], run=run)["sha"], "c3")

    def test_remote_that_gitlab_cannot_serve(self):
        for remote in (None, "/srv/git/back.git"):
            with self.subTest(remote=remote):
                path = make_repo(self.parent, f"Acme - Shop - R{remote is None}", remote=remote)
                run = FakeRun()
                result = self.run_json(["deployed", path, "--branch", "release"], run=run)
                self.assertEqual(
                    result, {"sha": None, "source": None, "reason": "remote_inconnu"}
                )
                self.assertEqual(run.glab_calls, [])

    def test_not_a_repository(self):
        plain = self.parent / "plain"
        plain.mkdir()
        code, out, err = self.run_main(["deployed", plain, "--branch", "release"], run=FakeRun())
        self.assertEqual((code, out), (2, ""))
        self.assertNotEqual(err, "")


PREPROD_LINES = ["GM_AUDIT_TARGET=https://preprod.example.org", "GM_AUDIT_ROBOTS=ignore"]


class ToolboxEnvTest(RunDirCase):
    def env_lines(self, **changes):
        self.write_context(**changes)
        code, out, err = self.run_main(["toolbox-env", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.endswith("\n"))
        return out.splitlines()

    def test_toolbox_env_preprod_without_phase(self):
        self.assertEqual(
            self.env_lines(),
            [
                "GM_AUDIT_TARGET=https://preprod.example.org",
                "GM_AUDIT_ROBOTS=ignore",
                "GM_AUDIT_RATE=2",
                f"GM_AUDIT_USER_AGENT=GingermindsAudit/{plugin_version()}",
                "GM_CQA_CRAWL_MINUTES=30",
                "GM_CQA_CRAWL_MAX_URLS=2000",
            ],
        )

    def test_toolbox_env_gives_the_crawl_limits(self):
        self.assertEqual(
            self.env_lines(crawl_minutes=60)[4:6],
            ["GM_CQA_CRAWL_MINUTES=60", "GM_CQA_CRAWL_MAX_URLS=4000"],
        )
        self.assertEqual(
            self.env_lines(crawl_minutes=240)[4:6],
            ["GM_CQA_CRAWL_MINUTES=240", "GM_CQA_CRAWL_MAX_URLS=8000"],
        )

    def test_toolbox_env_passes_the_prod_origin_on_preprod(self):
        lines = self.env_lines(target__prod_url="https://www.example.org/fr")
        self.assertIn("GM_AUDIT_PROD_ORIGIN=https://www.example.org", lines)
        self.assertEqual(lines[0], "GM_AUDIT_TARGET=https://preprod.example.org")
        lines = self.env_lines(target__prod_url=None)
        self.assertFalse(any(line.startswith("GM_AUDIT_PROD_ORIGIN") for line in lines))
        lines = self.env_lines(
            phase_filter="MEL J0",
            target__prod_url="https://www.example.org/fr",
            target__prod_live=True,
        )
        self.assertFalse(any(line.startswith("GM_AUDIT_PROD_ORIGIN") for line in lines))

    def test_toolbox_env_phase_prod(self):
        for phase in ("MEL J0", "Post-MEL J+7", "J0", "J+7"):
            with self.subTest(phase=phase):
                lines = self.env_lines(
                    phase_filter=phase,
                    target__prod_url="https://www.example.org",
                    target__prod_live=True,
                )
                self.assertEqual(lines[0], "GM_AUDIT_TARGET=https://www.example.org")
                self.assertEqual(lines[1], "GM_AUDIT_ROBOTS=respect")

    def test_phase_without_live_prod_stays_on_preprod(self):
        lines = self.env_lines(
            phase_filter="MEL J0",
            target__prod_url="https://www.example.org",
            target__prod_live=False,
        )
        self.assertEqual(lines[:2], PREPROD_LINES)

    def test_live_prod_without_phase_stays_on_preprod(self):
        lines = self.env_lines(target__prod_url="https://www.example.org", target__prod_live=True)
        self.assertEqual(lines[:2], PREPROD_LINES)

    def test_other_phase_stays_on_preprod(self):
        lines = self.env_lines(
            phase_filter="Build", target__prod_url="https://www.example.org", target__prod_live=True
        )
        self.assertEqual(lines[0], "GM_AUDIT_TARGET=https://preprod.example.org")

    def test_no_secret_is_printed(self):
        self.write_context()
        env = {
            "GM_AUDIT_AUTH_USER": USER,
            "GM_AUDIT_AUTH_PASS": PASSWORD,
            "NOTION_TOKEN": "ntn_0123",
        }
        code, out, err = self.run_main(["toolbox-env", self.run_dir], env=env)
        self.assertEqual(code, 0, err)
        for secret in (USER, PASSWORD, "ntn_0123"):
            self.assertNotIn(secret, out + err)
        self.assertEqual(
            [line.partition("=")[0] for line in out.splitlines()],
            [
                "GM_AUDIT_TARGET",
                "GM_AUDIT_ROBOTS",
                "GM_AUDIT_RATE",
                "GM_AUDIT_USER_AGENT",
                "GM_CQA_CRAWL_MINUTES",
                "GM_CQA_CRAWL_MAX_URLS",
            ],
        )

    def test_invalid_target_is_a_context_error(self):
        cases = {
            "no preprod": {"target__preprod_url": None},
            "not a url": {"target__preprod_url": "preprod.example.org"},
            "credentials": {"target__preprod_url": f"https://{USER}:{PASSWORD}@example.org"},
            "newline": {"target__preprod_url": "https://example.org/\nGM_AUDIT_ROBOTS=respect"},
            "live prod without url": {
                "phase_filter": "MEL J0",
                "target__prod_live": True,
                "target__prod_url": None,
            },
        }
        for label, changes in cases.items():
            with self.subTest(label):
                self.write_context(**changes)
                code, out, err = self.run_main(["toolbox-env", self.run_dir])
                self.assertEqual((code, out), (6, ""))
                self.assertNotEqual(err, "")
                self.assertNotIn(PASSWORD, err)

    def test_target_with_a_query_or_a_fragment_is_refused(self):
        cases = {
            "query": {"target__preprod_url": "https://preprod.example.org/?token=abc123"},
            "empty query": {"target__preprod_url": "https://preprod.example.org/?"},
            "fragment": {"target__preprod_url": "https://preprod.example.org/#abc123"},
            "prod query": {
                "phase_filter": "MEL J0",
                "target__prod_live": True,
                "target__prod_url": "https://www.example.org/?token=abc123",
            },
        }
        for label, changes in cases.items():
            with self.subTest(label):
                self.write_context(**changes)
                code, out, err = self.run_main(["toolbox-env", self.run_dir])
                self.assertEqual((code, out), (2, ""))
                self.assertIn("?", err)
                self.assertNotIn("abc123", err)

    def test_query_of_an_unused_url_is_not_a_problem(self):
        lines = self.env_lines(target__prod_url="https://www.example.org/?token=abc123")
        self.assertEqual(lines[:2], PREPROD_LINES)

    def test_missing_context(self):
        code, out, err = self.run_main(["toolbox-env", self.run_dir])
        self.assertEqual((code, out), (6, ""))
        self.assertIn("context.json", err)

    def test_missing_target_section(self):
        context = valid_context()
        del context["target"]
        self.write_context(context)
        code, out, _ = self.run_main(["toolbox-env", self.run_dir])
        self.assertEqual((code, out), (6, ""))


class ReposTsvTest(RunDirCase):
    def test_repos_tsv(self):
        self.write_context()
        code, out, err = self.run_main(["repos-tsv", self.run_dir])
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out,
            "laravel\t/work/Acme - Shop - Laravel\nnuxt-3\t/work/Acme - Shop - Nuxt 3\n",
        )

    def test_unsafe_entries_are_a_context_error(self):
        cases = {
            "tab in path": {"repos__0__path": "/work/a\tb"},
            "newline in path": {"repos__0__path": "/work/a\nb"},
            "empty path": {"repos__0__path": ""},
            "space in name": {"repos__0__name": "two words"},
            "colon in name": {"repos__0__name": "a:b"},
            "empty name": {"repos__0__name": ""},
            "name not text": {"repos__0__name": 3},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                self.write_context(**changes)
                code, out, err = self.run_main(["repos-tsv", self.run_dir])
                self.assertEqual((code, out), (6, ""))
                self.assertNotEqual(err, "")

    def test_no_repos(self):
        self.write_context(repos=[])
        code, out, _ = self.run_main(["repos-tsv", self.run_dir])
        self.assertEqual((code, out), (6, ""))

    def test_missing_context(self):
        code, out, err = self.run_main(["repos-tsv", self.run_dir])
        self.assertEqual((code, out), (6, ""))
        self.assertIn("context.json", err)


class RunsCommandTest(CliCase):
    def setUp(self):
        self.home = self.tmp_dir()
        self.base = self.home / ".cache" / "gm-audit" / "acme-shop"

    def runs(self, slug="acme-shop"):
        return self.run_json(["runs", slug], env={"HOME": str(self.home)})

    def make_run(self, run_id, *files):
        run_dir = self.base / run_id
        run_dir.mkdir(parents=True)
        for relative in files:
            file = run_dir / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("{}", encoding="utf-8")
        return run_dir

    def test_unknown_project(self):
        self.assertEqual(self.runs("nothing-here"), {"slug": "nothing-here", "runs": []})

    def test_runs_are_listed_newest_first_with_their_state(self):
        old = self.make_run("2026-10-18-0900", "context.json")
        full = self.make_run(
            "2026-10-20-1430",
            "context.json",
            "lots.json",
            "findings/lot-1.json",
            "notion/plan.json",
            "notion/applied.json",
            "synthese.md",
            "done.json",
        )
        empty = self.make_run("2026-10-19-1000")
        result = self.runs()
        self.assertEqual(result["slug"], "acme-shop")
        self.assertEqual(
            [item["run_id"] for item in result["runs"]],
            ["2026-10-20-1430", "2026-10-19-1000", "2026-10-18-0900"],
        )
        full_state, empty_state, old_state = result["runs"]
        self.assertEqual(full_state["path"], str(full))
        self.assertEqual(old_state["path"], str(old))
        self.assertEqual(empty_state["path"], str(empty))
        keys = ["context", "lots", "findings", "plan", "applied", "synthese", "done"]
        self.assertEqual(sorted(full_state), sorted(["run_id", "path", "write_pending", *keys]))
        self.assertEqual(
            [state["write_pending"] for state in result["runs"]], [False, False, False]
        )
        self.assertEqual([full_state[key] for key in keys], [True] * 7)
        self.assertEqual([empty_state[key] for key in keys], [False] * 7)
        self.assertEqual(
            [old_state[key] for key in keys], [True, False, False, False, False, False, False]
        )

    def write(self, run_dir, relative, value):
        (run_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / relative).write_text(json.dumps(value), encoding="utf-8")

    def pending_run(self, notion_write="all", dry_run=False, applied=("k1",), plan_id="p2"):
        run_dir = self.make_run("2026-10-20-1000", "done.json")
        self.write(run_dir, "context.json", {"notion_write": notion_write, "dry_run": dry_run})
        ops = [{"key": "k1"}, {"key": "k2"}]
        self.write(run_dir, "notion/plan.json", {"plan_id": plan_id, "ops": ops})
        entries = [{"key": key, "plan_id": "p2"} for key in applied]
        entries.append({"key": "k2", "plan_id": "p1"})
        self.write(run_dir, "notion/applied.json", {"applied": entries})
        return run_dir

    def test_write_pending_when_an_operation_of_the_plan_is_not_in_the_journal(self):
        self.pending_run()
        state = self.runs()["runs"][0]
        self.assertTrue(state["done"])
        self.assertTrue(state["write_pending"])

    def test_no_write_pending_when_the_plan_is_written_or_not_to_be_written(self):
        for options in (
            {"applied": ("k1", "k2")},
            {"notion_write": "none"},
            {"dry_run": True},
        ):
            with self.subTest(options=options):
                shutil.rmtree(self.base, ignore_errors=True)
                self.pending_run(**options)
                self.assertFalse(self.runs()["runs"][0]["write_pending"])

    def test_write_pending_without_journal_or_with_a_legacy_plan(self):
        run_dir = self.pending_run()
        (run_dir / "notion" / "applied.json").unlink()
        self.assertTrue(self.runs()["runs"][0]["write_pending"])
        self.write(run_dir, "notion/plan.json", {"ops": [{"key": "k1"}]})
        self.write(run_dir, "notion/applied.json", {"applied": [{"key": "k1"}]})
        self.assertFalse(self.runs()["runs"][0]["write_pending"])
        self.write(run_dir, "notion/plan.json", {"ops": []})
        self.assertFalse(self.runs()["runs"][0]["write_pending"])
        (run_dir / "notion" / "plan.json").write_text("{", encoding="utf-8")
        self.assertFalse(self.runs()["runs"][0]["write_pending"])

    def test_done_needs_the_final_scan_file(self):
        self.make_run("2026-10-20-1000", "context.json", "synthese.md", "notion/applied.json")
        self.assertFalse(self.runs()["runs"][0]["done"])
        (self.base / "2026-10-20-1000" / "done.json").mkdir()
        self.assertFalse(self.runs()["runs"][0]["done"])

    def test_partial_states_are_told_apart(self):
        self.make_run("2026-10-20-1000", "context.json", "lots.json", "notion/plan.json")
        state = self.runs()["runs"][0]
        self.assertEqual(
            (state["plan"], state["applied"], state["synthese"], state["findings"]),
            (True, False, False, False),
        )

    def test_empty_findings_directory_does_not_count(self):
        run_dir = self.make_run("2026-10-20-1000")
        (run_dir / "findings").mkdir()
        self.assertFalse(self.runs()["runs"][0]["findings"])

    def test_findings_need_a_json_file(self):
        self.make_run("2026-10-20-1000", "findings/notes.txt")
        self.assertFalse(self.runs()["runs"][0]["findings"])

    def test_foreign_entries_are_ignored(self):
        self.make_run("2026-10-20-1430", "context.json")
        (self.base / "scratch").mkdir()
        (self.base / "2026-10-21-0900.log").write_text("x", encoding="utf-8")
        (self.base / "2026-10-2-900").mkdir()
        self.assertEqual([item["run_id"] for item in self.runs()["runs"]], ["2026-10-20-1430"])

    def test_slug_cannot_escape_the_cache(self):
        for slug in ("../etc", "a/b", "", "Acme Shop", "-x", "x-", ".."):
            with self.subTest(slug=slug):
                code, out, err = self.run_main(["runs", slug], env={"HOME": str(self.home)})
                self.assertEqual((code, out), (2, ""))
                self.assertNotEqual(err, "")


class MainTest(CliCase):
    def test_no_command_is_a_usage_error(self):
        code, _, _ = self.run_main([])
        self.assertEqual(code, 2)

    def test_unknown_command_is_a_usage_error(self):
        code, _, _ = self.run_main(["nope"])
        self.assertEqual(code, 2)

    def test_missing_argument_is_a_usage_error(self):
        for argv in (["project"], ["align", "x"], ["team", "x"], ["runs"], ["probe-target"]):
            with self.subTest(argv=argv):
                code, out, _ = self.run_main(argv)
                self.assertEqual((code, out), (2, ""))

    def test_git_missing_is_a_usage_error(self):
        def no_git(cmd, **kwargs):
            raise FileNotFoundError("git")

        code, out, err = self.run_main(["repo-state", "/tmp"], run=no_git)
        self.assertEqual((code, out), (2, ""))
        self.assertNotEqual(err, "")

    def test_sources_have_no_em_dash(self):
        source = (Path(cqa_plan.__file__)).read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), source)
        self.assertNotIn(chr(0x2014), (Path(target.__file__)).read_text(encoding="utf-8"))


class SiteOpener:
    """Opener that serves pages {url: (status, body)} and records the requests."""

    def __init__(self, pages, final=None):
        self.pages = pages
        self.final = final or {}
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        status, body = self.pages.get(request.full_url, (404, ""))
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "Error", {}, io.BytesIO())
        return _Page(self.final.get(request.full_url, request.full_url), status, body)

    @property
    def urls(self):
        return [request[1] for request in self.requests]


class _Page:
    def __init__(self, url, status, body):
        self.url, self.status, self.body = url, status, io.BytesIO(body.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.body.read(size)


HOME = "https://preprod.example.org/"


def page(url, template, status, checked=True, gap=False):
    return {"url": url, "template": template, "status": status, "checked": checked, "gap": gap}


class KeyPagesCheckTest(RunDirCase):
    def setUp(self):
        super().setUp()
        self.fast = {"GM_AUDIT_RATE": "1000"}

    def pages(self, urls):
        return [{"template": f"T{index}", "url": url} for index, url in enumerate(urls)]

    def check_pages(self, pages, opener, extra=None, env=None, **changes):
        self.write_context(key_pages=self.pages(pages), **changes)
        argv = ["key-pages-check", self.run_dir, *(extra or [])]
        return self.run_main(argv, env={**self.fast, **(env or {})}, opener=opener)

    def test_statuses_and_dead_links_of_the_home_page(self):
        home = (
            '<a href="/a">a</a> <a href="/gone">g</a> <a href="/a#top">again</a> '
            '<a href="https://other.example.org/x">out</a> <a href="mailto:x@example.org">m</a> '
            '<a href="#top">t</a> <a href="rel">r</a>'
        )
        opener = SiteOpener(
            {
                HOME: (200, home),
                HOME + "liste": (200, ""),
                HOME + "missing": (404, ""),
                HOME + "a": (200, ""),
                HOME + "rel": (200, ""),
            }
        )
        code, out, err = self.check_pages([HOME, HOME + "liste", HOME + "missing"], opener)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(
            result["pages"],
            [
                page(HOME, "T0", 200),
                page(HOME + "liste", "T1", 200),
                page(HOME + "missing", "T2", 404, gap=True),
                page(HOME + "a", None, 200),
                page(HOME + "gone", None, 404, gap=True),
                page(HOME + "rel", None, 200),
            ],
        )
        self.assertEqual(result["home_links_404"], [HOME + "gone"])
        self.assertNotIn("https://other.example.org/x", opener.urls)
        self.assertEqual(opener.urls.count(HOME), 1)

    def test_a_key_page_on_another_origin_is_not_requested(self):
        opener = SiteOpener({HOME: (200, "")})
        code, out, err = self.check_pages([HOME, "https://other.example.org/"], opener)
        self.assertEqual(code, 0, err)
        self.assertEqual(opener.urls, [HOME])
        self.assertEqual(
            json.loads(out)["pages"][1], page("https://other.example.org/", "T1", None, False)
        )

    def test_at_most_fifty_requests(self):
        links = "".join(f'<a href="/p{number}">x</a>' for number in range(80))
        opener = SiteOpener({HOME: (200, links)})
        code, out, err = self.check_pages([HOME], opener)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(opener.requests), 50)
        self.assertEqual(len(json.loads(out)["pages"]), 50)

    def test_the_rate_of_the_toolbox_is_respected(self):
        opener = SiteOpener({HOME: (200, '<a href="/a">a</a>'), HOME + "a": (200, "")})
        with mock.patch.object(cqa_plan.time, "sleep") as sleep:
            code, _, err = self.check_pages([HOME], opener, env={"GM_AUDIT_RATE": "4"})
        self.assertEqual(code, 0, err)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25])
        with mock.patch.object(cqa_plan.time, "sleep") as sleep:
            self.check_pages([HOME], opener, env={"GM_AUDIT_RATE": ""})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5])

    def test_an_invalid_rate_is_a_usage_error(self):
        opener = SiteOpener({HOME: (200, "")})
        code, _, err = self.check_pages([HOME], opener, env={"GM_AUDIT_RATE": "fast"})
        self.assertEqual(code, 2)
        self.assertEqual(opener.requests, [])

    def test_unreachable_page_has_a_null_status(self):
        class Down:
            def open(self, request, timeout=None):
                raise urllib.error.URLError("down")

        code, out, _ = self.check_pages([HOME], Down())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["pages"], [page(HOME, "T0", None, True, True)])

    def test_a_404_template_answering_404_is_not_a_gap(self):
        opener = SiteOpener({HOME: (200, ""), HOME + "nope": (404, ""), HOME + "x": (404, "")})
        self.write_context(
            key_pages=[
                {"template": "Accueil", "url": HOME},
                {"template": "404", "url": HOME + "nope"},
                {"template": "Liste", "url": HOME + "x"},
            ]
        )
        result = self.run_json(["key-pages-check", self.run_dir], env=self.fast, opener=opener)
        self.assertEqual(
            [(p["template"], p["status"], p["gap"]) for p in result["pages"]],
            [("Accueil", 200, False), ("404", 404, False), ("Liste", 404, True)],
        )

    def test_a_key_page_with_credentials_is_refused_and_never_printed(self):
        url = "https://leak-user:leak-pass@preprod.example.org/liste"
        opener = SiteOpener({HOME: (200, "")})
        code, out, err = self.check_pages([HOME, url], opener)
        self.assertEqual(code, 6)
        self.assertEqual(opener.requests, [])
        self.assertNotIn("leak", out + err)
        self.write_context(key_pages=[{"template": "Liste", "url": url}])
        code, out, err = self.run_main(["context", "--check", self.run_dir])
        self.assertEqual(code, 6)
        self.assertNotIn("leak", out + err)
        self.assertIn("key_pages[0].url", out)

    def test_after_the_deadline_the_remaining_pages_are_reported_unchecked(self):
        opener = SiteOpener({HOME: (200, '<a href="/a">a</a>'), HOME + "liste": (200, "")})
        with mock.patch.object(cqa_plan, "KEY_PAGES_DEADLINE", -1):
            code, out, err = self.check_pages([HOME, HOME + "liste"], opener)
        self.assertEqual(code, 0, err)
        self.assertEqual(opener.urls, [HOME])
        self.assertEqual(json.loads(out)["pages"][1], page(HOME + "liste", "T1", None, False))

    def test_no_more_links_are_listed_after_the_deadline(self):
        links = "".join(f'<a href="/p{number}">x</a>' for number in range(40))
        opener = SiteOpener({HOME: (200, links)})
        with mock.patch.object(cqa_plan, "KEY_PAGES_DEADLINE", -1):
            code, out, err = self.check_pages([HOME], opener)
        self.assertEqual(code, 0, err)
        self.assertEqual(opener.urls, [HOME])
        self.assertEqual(len(json.loads(out)["pages"]), 1)

    def test_home_links_follow_an_allowed_https_upgrade(self):
        http_home = "http://preprod.example.org/"
        opener = SiteOpener(
            {http_home: (200, '<a href="/a">a</a>'), HOME + "a": (200, "")},
            final={http_home: HOME},
        )
        code, out, err = self.check_pages(
            [http_home], opener, target__preprod_url="http://preprod.example.org"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(opener.urls, [http_home, HOME + "a"])

    def test_a_cross_origin_redirect_reaches_nothing_else(self):
        other, other_url = start_server(self, answer(200))
        server, url = start_server(
            self, lambda method, path, headers: (302, {"Location": other_url + "/loot"})
        )
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.check_pages(
            [url + "/x"], None, extra=["--auth-env"], env=env, target__preprod_url=url
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(other.requests, [])
        self.assertEqual(json.loads(out)["pages"][1]["status"], 302)

    def test_no_credentials_without_the_flag(self):
        opener = SiteOpener({HOME: (200, "")})
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        self.check_pages([HOME], opener, env=env)
        self.assertNotIn("Authorization", opener.requests[0][2])

    def test_credentials_are_sent_to_the_target_only_and_never_printed(self):
        server, url = start_server(self, basic_gate)
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.check_pages(
            [url + "/", "https://other.example.org/"],
            None,
            extra=["--auth-env"],
            env=env,
            target__preprod_url=url,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual([page["status"] for page in json.loads(out)["pages"]], [200, None])
        self.assertEqual({request[2]["Authorization"] for request in server.requests}, {BASIC})
        self.assertNotIn(PASSWORD, out + err)
        self.assertNotIn(BASIC.split()[1], out + err)

    def test_credentials_are_refused_over_clear_text_http(self):
        opener = SiteOpener({"http://preprod.example.org/": (200, "")})
        env = {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        code, out, err = self.check_pages(
            ["http://preprod.example.org/"],
            opener,
            extra=["--auth-env"],
            env=env,
            target__preprod_url="http://preprod.example.org/",
        )
        self.assertEqual(code, 2)
        self.assertEqual(opener.requests, [])
        self.assertNotIn(PASSWORD, out + err)

    def test_missing_variables_with_the_flag_are_a_usage_error(self):
        opener = SiteOpener({HOME: (200, "")})
        code, _, _ = self.check_pages([HOME], opener, extra=["--auth-env"])
        self.assertEqual(code, 2)

    def test_prod_run_checks_the_prod_origin(self):
        prod = "https://www.example.org/"
        opener = SiteOpener({prod: (200, "")})
        code, out, err = self.check_pages(
            [prod, HOME],
            opener,
            target__prod_url="https://www.example.org",
            target__prod_live=True,
            phase_filter="MEL J0",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(opener.urls, [prod])


class _SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        status, body = self.server.pages.get(self.path, (404, b""))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CrawlEstimateTest(RunDirCase):
    def setUp(self):
        super().setUp()
        self.fast = {"GM_AUDIT_RATE": "1000"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def serve(self, pages):
        self.server.pages = {path: (200, body) for path, body in pages.items()}

    def estimate(self, extra=None, env=None, **changes):
        sleeping = mock.patch.object(cqa_plan.crawl_budget.time, "sleep")
        with sleeping:
            return self.run_main(
                ["crawl-estimate", self.run_dir, *(extra or [])],
                env={**self.fast, **(env or {})},
            )

    def test_crawl_estimate_counts_the_sitemap_and_times_the_key_pages(self):
        prod = "https://www.example.org"
        index = (
            f"<sitemapindex><sitemap><loc>{prod}/s1.xml</loc></sitemap>"
            f"<sitemap><loc>{self.base}/s2.xml.gz</loc></sitemap></sitemapindex>"
        ).encode()
        first = (
            f"<urlset><url><loc>{prod}/a</loc></url><url><loc>{prod}/b?x=1</loc></url>"
            "<url><loc>https://other.example.net/c</loc></url></urlset>"
        ).encode()
        second = gzip.compress(
            f"<urlset><url><loc>{self.base}/a</loc></url><url><loc>{prod}/d</loc></url></urlset>".encode()
        )
        self.serve(
            {
                "/sitemap.xml": index,
                "/robots.txt": f"User-agent: *\nSitemap: {prod}/sitemap.xml\n".encode(),
                "/s1.xml": first,
                "/s2.xml.gz": second,
                "/": b"home",
                "/liste": b"list",
                "/fiche": b"card",
            }
        )
        self.write_context(
            target__preprod_url=self.base,
            target__prod_url=prod,
            key_pages=[
                {"template": "Accueil", "url": self.base + "/"},
                {"template": "Liste", "url": prod + "/liste"},
                {"template": "Fiche", "url": self.base + "/fiche"},
                {"template": "404", "url": self.base + "/nope"},
            ],
        )
        code, out, err = self.estimate(
            ["--auth-env"], {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        )
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(3, result["sitemap_urls"])
        self.assertEqual(3, result["pages_timed"])
        self.assertEqual(30, result["proposed_minutes"])
        self.assertEqual(2000, result["max_urls"])
        self.assertIsInstance(result["mean_response_s"], float)
        paths = [path for path, _ in self.server.requests]
        self.assertNotIn("/nope", paths)
        self.assertIn("/liste", paths)
        for path, headers in self.server.requests:
            self.assertEqual(BASIC, headers.get("Authorization"), path)
        self.assertNotIn(PASSWORD, out + err)
        self.assertNotIn(USER, out + err)

    def test_crawl_estimate_reports_a_truncated_count_and_refuses_clear_credentials(self):
        self.serve({"/sitemap.xml": b"<urlset></urlset>", "/": b""})
        self.write_context(target__preprod_url=self.base, key_pages=[{"template": "A", "url": self.base + "/"}])
        self.assertFalse(json.loads(self.estimate()[1])["truncated"])
        self.write_context(
            target__preprod_url="http://preprod.example.org",
            key_pages=[{"template": "A", "url": "http://preprod.example.org/"}],
        )
        code, out, err = self.estimate(
            ["--auth-env"], {"GM_AUDIT_AUTH_USER": USER, "GM_AUDIT_AUTH_PASS": PASSWORD}
        )
        self.assertNotEqual(0, code)
        self.assertNotIn(PASSWORD, out + err)

    def test_key_pages_take_half_of_the_budget_at_most(self):
        self.serve({"/sitemap.xml": b"<urlset></urlset>", "/": b""})
        self.write_context(target__preprod_url=self.base, key_pages=[{"template": "A", "url": self.base + "/"}])
        seen = []
        real = cqa_plan.crawl_budget.mean_response_time

        def spy(urls, fetch, **kwargs):
            seen.append(kwargs["deadline"] - time.monotonic())
            return real(urls, fetch, **kwargs)

        with mock.patch.object(cqa_plan.crawl_budget, "mean_response_time", spy):
            code, _, err = self.estimate()
        self.assertEqual(code, 0, err)
        self.assertLessEqual(seen[0], cqa_plan.KEY_PAGES_DEADLINE / 2)

    def test_crawl_estimate_without_a_sitemap(self):
        self.serve({"/": b"home"})
        self.write_context(target__preprod_url=self.base, key_pages=[{"template": "A", "url": self.base + "/"}])
        code, out, err = self.estimate()
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertIsNone(result["sitemap_urls"])
        self.assertEqual((30, 2000), (result["proposed_minutes"], result["max_urls"]))
        self.assertEqual(1, result["pages_timed"])

    def test_crawl_estimate_without_credentials_sends_no_header(self):
        self.serve({"/sitemap.xml": b"<urlset></urlset>", "/": b""})
        self.write_context(target__preprod_url=self.base, key_pages=[{"template": "A", "url": self.base + "/"}])
        code, out, err = self.estimate()
        self.assertEqual(code, 0, err)
        self.assertEqual(0, json.loads(out)["sitemap_urls"])
        for _, headers in self.server.requests:
            self.assertNotIn("Authorization", headers)

    def test_crawl_estimate_needs_the_credentials_with_auth_env(self):
        self.write_context(target__preprod_url=self.base)
        code, _, _ = self.estimate(["--auth-env"])
        self.assertEqual(2, code)


class FetchTimedTest(CliCase):
    def serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def test_a_slowly_streamed_body_counts_as_unanswered(self):
        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        _, base = self.serve(Slow)
        started = time.monotonic()
        status, body, _ = target.fetch_timed(base + "/", timeout=5, deadline=time.monotonic() + 0.3)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual((None, b""), (status, body))

    def test_a_cross_origin_redirect_is_not_followed_and_gets_nothing(self):
        seen = []

        class Second(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        _, other = self.serve(Second)

        class First(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", other + "/x")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        _, base = self.serve(First)
        status, body, _ = target.fetch_timed(base + "/", auth=(USER, PASSWORD))
        self.assertEqual((302, b""), (status, body))
        self.assertEqual([], seen)


class ReuseContextTest(RunDirCase):
    def setUp(self):
        super().setUp()
        self.old = self.run_dir
        self.new = self.old.parent / "2026-10-21-0900"
        self.new.mkdir()
        old = valid_context()
        old.update({"mode": "autonome", "notion_write": "all", "crawl_minutes": 30})
        old["sites"] = [
            {"domain": "preprod.example.org", "backoffice": "commun", "sso": True, "home": HOME}
        ]
        old["target"]["access"] = "basic_auth"
        old["target"]["prod_live"] = True
        old["notion"]["bot_id"] = "aa" * 16
        old["repos"][0]["preprod_branch"] = "release"
        old["ssh"] = {"alias": "example-preprod", "app_dirs": {"laravel": "/srv/app"}}
        old["test_accounts"] = {"front": True, "backoffice": True}
        old["available_tools"] = ["Code", "SSH serveur"]
        old["dry_run"] = True
        self.write_context(old)

    def fresh(self, run_id="2026-10-21-0900"):
        document = valid_context()
        document["run_id"] = run_id
        document["target"]["access"] = None
        document["repos"] = [
            dict(repo, audited_sha="f" * 40, deployed_sha=None, aligned=None, preprod_branch="main")
            for repo in document["repos"]
        ]
        (self.new / "context.json").write_text(json.dumps(document), encoding="utf-8")
        return document

    def reuse(self):
        return self.run_json(["reuse-context", self.new, self.old])

    def written(self):
        return json.loads((self.new / "context.json").read_text(encoding="utf-8"))

    def test_reusable_answers_are_copied_and_listed(self):
        self.fresh()
        result = self.reuse()
        context, old = self.written(), json.loads((self.old / "context.json").read_text())
        for key in ("repos", "notion", "lead", "team", "ssh", "test_accounts", "key_pages", "sites"):
            self.assertIn(key, result["copied"])
            if key not in ("repos", "notion"):
                self.assertEqual(context[key], old[key], key)
        self.assertEqual(context["notion"]["assignee_options"], old["notion"]["assignee_options"])
        self.assertIn("target", result["copied"])
        self.assertEqual(context["target"]["preprod_url"], old["target"]["preprod_url"])

    def test_probe_and_per_invocation_answers_are_not_copied(self):
        self.fresh()
        self.reuse()
        context = self.written()
        self.assertIsNone(context["target"]["access"])
        for key in ("mode", "notion_write", "crawl_minutes"):
            self.assertNotIn(key, context)
        self.assertEqual(context["run_id"], "2026-10-21-0900")
        self.assertEqual(context["available_tools"], ["Code", "CLI local", "HTTP"])
        self.assertFalse(context["dry_run"])

    def test_fresh_repos_are_kept_and_only_the_preprod_branch_is_taken(self):
        self.fresh()
        self.reuse()
        repos = self.written()["repos"]
        self.assertEqual([repo["audited_sha"] for repo in repos], ["f" * 40] * 2)
        self.assertEqual([repo["preprod_branch"] for repo in repos], ["release"] * 2)

    def test_repos_of_an_old_run_are_reset_when_the_new_run_has_none(self):
        self.reuse()
        for repo in self.written()["repos"]:
            self.assertIsNone(repo["audited_sha"])
            self.assertIsNone(repo["deployed_sha"])
            self.assertIsNone(repo["aligned"])

    def test_prod_live_and_the_bot_are_not_copied(self):
        fresh = self.fresh()
        self.reuse()
        context = self.written()
        self.assertIs(context["target"]["prod_live"], False)
        self.assertEqual(context["notion"]["bot_id"], fresh["notion"]["bot_id"])

    def test_resulting_context_is_valid(self):
        self.fresh()
        self.reuse()
        code, out, err = self.run_main(["context", "--check", self.new])
        self.assertEqual(code, 0, out + err)

    def test_without_a_context_in_the_new_run_only_the_answers_are_written(self):
        result = self.reuse()
        self.assertEqual(set(self.written()), set(result["copied"]))
        self.assertNotIn("run_id", self.written())

    def test_an_old_context_without_sites_does_not_list_them(self):
        old = json.loads((self.old / "context.json").read_text())
        del old["sites"]
        self.write_context(old)
        self.fresh()
        self.assertNotIn("sites", self.reuse()["copied"])
        self.assertNotIn("sites", self.written())

    def test_missing_old_context_is_an_error(self):
        (self.old / "context.json").unlink()
        code, _, err = self.run_main(["reuse-context", self.new, self.old])
        self.assertEqual(code, 6)

    def test_new_run_folder_must_exist(self):
        code, _, _ = self.run_main(["reuse-context", self.new / "nope", self.old])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
