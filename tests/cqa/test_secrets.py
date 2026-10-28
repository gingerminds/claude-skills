import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cqa.secrets import known_values_from_env, redact, scan_text, scan_tree

JWT = ".".join(["eyJhbGciOiJub25lIn0", "eyJzdWIiOiJ0ZXN0In0", "c2lnbmF0dXJlLXZhbHVl"])

PATTERN_CASES = [
    ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private_key", "-----BEGIN PRIVATE KEY-----"),
    ("aws_key", "AKIA" + "IOSFODNN7EXAMPLE"),
    ("aws_key", "ASIA" + "IOSFODNN7EXAMPLE"),
    ("gitlab_token", "glpat-" + "x" * 20),
    ("gitlab_token", "gldt-" + "x" * 20),
    ("github_token", "ghp_" + "a" * 36),
    ("github_token", "github_pat_" + "a" * 22 + "_" + "b" * 59),
    ("notion_token", "ntn_" + "a" * 46),
    ("notion_token", "secret_" + "a" * 43),
    ("jwt", JWT),
    ("credential_assignment", "DB_PASSWORD=hunter22"),
    ("credential_assignment", "export API_KEY=abcd1234"),
    ("credential_assignment", "db_passwd: hunter22"),
    ("credential_assignment", "PWD=hunter22"),
    ("credential_assignment", "password = hunter22"),
    ("credential_assignment", "client_secret = 'abcd1234'"),
    ("credential_assignment", "'password' => 'hunter22',"),
    ("credential_assignment", "X-Auth-Token: abcd1234"),
    ("credential_assignment", "https://example.org/?api-key=abcd1234&page=2"),
    ("credential_assignment", '{"password": "hunter22"}'),
    ("credential_assignment", '{\\"password\\": \\"hunter22\\"}'),
    ("authorization_header", "Authorization: Basic dXNlcjpwYXNz"),
    ("authorization_header", "Authorization: Bearer abcd1234"),
    ("authorization_header", '"Authorization": "Bearer abcd1234"'),
    ("authorization_header", "authorization: " + "a" * 32),
]

CLEAN_TEXTS = [
    "Le mot de passe est haché ; le lien d'évitement manque.",
    "Le jeton CSRF est présent dans chaque formulaire.",
    "La politique de mots de passe impose 12 caractères.",
    "en-tête Authorization manquant",
    "token=abc",
    "password: ",
    "DB_PASSWORD",
    "Authorization: Bearer <token>",
    "Authorization: Bearer ${TOKEN}",
    "Authorization: refused",
    "DB_PASSWORD=[secret masqué]",
    "Authorization: Basic [secret masqué]",
]


class ScanTextTest(unittest.TestCase):
    def test_known_value(self):
        self.assertEqual(scan_text("mot de passe s3cr3t-value", ["s3cr3t-value"]), ["known_value"])

    def test_known_value_is_reported_once(self):
        self.assertEqual(scan_text("s3cr3t-value puis s3cr3t-value", ["s3cr3t-value"]), ["known_value"])

    def test_known_value_absent(self):
        self.assertEqual(scan_text("rien à signaler", ["s3cr3t-value"]), [])

    def test_empty_known_value_is_ignored(self):
        self.assertEqual(scan_text("any text", ["", "absent-value"]), [])

    def test_patterns(self):
        self.assertIn("private_key", scan_text("-----BEGIN OPENSSH PRIVATE KEY-----", []))
        self.assertIn("credential_assignment", scan_text("DB_PASSWORD=hunter22", []))
        self.assertIn("authorization_header", scan_text("Authorization: Basic dXNlcjpwYXNz", []))
        self.assertIn("notion_token", scan_text("ntn_" + "a" * 46, []))
        self.assertIn("gitlab_token", scan_text("glpat-" + "x" * 20, []))

    def test_each_pattern_yields_exactly_its_label(self):
        for label, text in PATTERN_CASES:
            with self.subTest(label=label, text=text[:30]):
                self.assertEqual(scan_text(text, []), [label])

    def test_clean_french_text(self):
        self.assertEqual(scan_text("Le mot de passe est haché ; le lien d'évitement manque.", []), [])

    def test_clean_texts(self):
        for text in CLEAN_TEXTS:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), [])

    def test_labels_are_unique_and_in_canonical_order(self):
        text = "\n".join([
            "DB_PASSWORD=hunter22",
            "TOKEN=abcd1234",
            "ntn_" + "a" * 46,
            "Authorization: Basic dXNlcjpwYXNz",
            "s3cr3t-value",
        ])
        self.assertEqual(
            scan_text(text, ["s3cr3t-value"]),
            ["known_value", "notion_token", "credential_assignment", "authorization_header"],
        )

    def test_known_value_and_pattern_combine(self):
        self.assertEqual(
            scan_text("DB_PASSWORD=s3cr3t-value", ["s3cr3t-value"]),
            ["known_value", "credential_assignment"],
        )

    def test_jwt_inside_authorization_header(self):
        self.assertEqual(
            scan_text("Authorization: Bearer " + JWT, []),
            ["jwt", "authorization_header"],
        )

    def test_multiline_text(self):
        text = "APP_ENV=prod\nDB_PASSWORD=hunter22\nAPP_DEBUG=false\n"
        self.assertEqual(scan_text(text, []), ["credential_assignment"])

    def test_value_on_next_line_is_not_an_assignment(self):
        self.assertEqual(scan_text("password:\n  length: 12", []), [])

    def test_pathological_input_stays_fast(self):
        text = "token" * 20000
        started = time.monotonic()
        scan_text(text, [])
        redact(text, [])
        self.assertLess(time.monotonic() - started, 5)

    def test_value_minimum_length_is_four(self):
        for text in ["token=abcd", 'token="abcd"', "token: abcd"]:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), ["credential_assignment"])
        for text in ["token=abc", 'token="abc"', "token: abc"]:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), [])


class NonSecretTest(unittest.TestCase):
    NON_SECRETS = [
        "password : type=password, autocomplete=current-password",
        'autocomplete="new-password"',
        'type="password"',
        "name=password",
        "'password' => ['required', 'confirmed', Password::defaults()]",
        "$token = bin2hex(random_bytes(32))",
        "'secret' => config('services.turnstile.secret')",
        "password=$PASSWORD",
        'password="${DB_PASSWORD}"',
        "Hash::make($request->password)",
        "$x->password = Hash::make($request->password);",
        "Le mot de passe vient de `password=$PASSWORD` dans l'environnement de l'application.",
        "Hachage via `$user->password = Hash::make($request->password)`, conforme à l'attente de l'OWASP.",
        "`'password' => ['required', 'confirmed', Password::defaults()]` : règle d'Laravel, c'est l'usage.",
        "'password' => env('DB_PASSWORD', ''),",
        "'password' => Hash::make($input['password']),",
        "Hash::make($data['password'])",
        "$password = $request->input('password');",
        "$token = $request->header('X-Api-Token');",
        "['password' => $password, 'remember' => $remember]",
        "`password=$PASSWORD` : d'abord l'ordre, L'app, N'oubliez pas, qu'on, s'il, jusqu'ici, aujourd'hui.",
    ]
    SECRETS = [
        'password: "S3cr3t-Pass!"',
        'password: "Pa(ss)word!"',
        "DB_PASSWORD=Pa55word2024",
        "'password' => 'hunter2hunter2'",
        "api_key=sk_live_51Habcdefghijkl",
        "token: abcd1234efgh5678",
        "'password' => '$uper-Secret1',",
        'DB_PASSWORD="$ecretPa55"',
        '"password": "$2y$10$abcdefghijklmnopqrstuv"',
        'password: ":Secret123"',
        '"password": "password"',
        "'password' => env('DB_PASSWORD', 'RealSecret123'),",
        "'secret' => env('STRIPE_SECRET', 'sk_live_abcdef123456'),",
        "'password' => Hash::make('Adm1nP@ss2024'),",
        "'password' => bcrypt('Adm1nP@ss2024'),",
        "$user->password = Hash::make('Adm1nP@ss2024');",
        "'password' => $_ENV['DB_PASSWORD'] ?? 'RealSecret123',",
        "MYSQL_PASSWORD: ${MYSQL_PASSWORD:-S3cretDefault}",
        "PASSWORD=${DB_PASSWORD:-S3cretDefault}",
        "DB_PASSWORD=password",
        "password=:Secret123",
        "token = hmac.new(b'RealSecretKey123', msg, sha256).hexdigest()",
        "token = jwt.encode(payload, b'my-signing-secret-123', algorithm='HS256')",
        "token = base64.b64encode(b'admin:Adm1nP@ss2024').decode()",
        "password = make_password(r'Adm1nP@ss2024')",
        "password = make_password(f'Adm1nP@ss2024')",
        "password = HASHBYTES('SHA2_256', N'Adm1nP@ss2024')",
        "'password' => Hash::make('Adm1nP@ss2024'), // l'admin d'origine",
    ]

    def test_form_field_descriptions_are_not_secrets(self):
        for text in self.NON_SECRETS[:4]:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), [])
                self.assertEqual(redact(text, []), text)

    def test_code_expressions_are_not_secrets(self):
        for text in self.NON_SECRETS[4:]:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), [])
                self.assertEqual(redact(text, []), text)

    def test_credential_assignment_still_catches_real_secrets(self):
        for text in self.SECRETS:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), ["credential_assignment"])
                masked = redact(text, [])
                self.assertIn("[secret masqué]", masked)
                self.assertEqual(scan_text(masked, []), [])
                for literal in ("RealSecret123", "Adm1nP@ss2024", "S3cretDefault", "sk_live_abcdef123456"):
                    self.assertNotIn(literal, masked)

    def test_secret_after_a_form_description_is_still_masked(self):
        text = "password : type=password\nDB_PASSWORD=Pa55word2024"
        self.assertEqual(scan_text(text, []), ["credential_assignment"])
        self.assertEqual(redact(text, []), "password : type=password\nDB_PASSWORD=[secret masqué]")


class RedactTest(unittest.TestCase):
    def test_redact_keeps_label(self):
        self.assertEqual(redact("token=abcdef123456", []), "token=[secret masqué]")

    def test_env_style_output(self):
        self.assertEqual(redact("DB_PASSWORD=supersecret", []), "DB_PASSWORD=[secret masqué]")

    def test_known_value_is_masked_everywhere(self):
        self.assertEqual(
            redact("mot de passe s3cr3t-value, encore s3cr3t-value", ["s3cr3t-value"]),
            "mot de passe [secret masqué], encore [secret masqué]",
        )

    def test_longest_known_value_wins(self):
        self.assertEqual(redact("abcdef-extra", ["abcdef", "abcdef-extra"]), "[secret masqué]")

    def test_known_value_with_regex_characters(self):
        self.assertEqual(redact("x a+b(c)[d] y", ["a+b(c)[d]"]), "x [secret masqué] y")

    def test_empty_known_value_is_ignored(self):
        self.assertEqual(redact("abc", [""]), "abc")

    def test_quoted_value_keeps_its_quotes(self):
        self.assertEqual(redact('{"password": "hunter22"}', []), '{"password": "[secret masqué]"}')
        self.assertEqual(redact("client_secret = 'abcd1234'", []), "client_secret = '[secret masqué]'")

    def test_quoted_value_with_spaces_is_fully_masked(self):
        self.assertEqual(
            redact('password: "correct horse battery"', []),
            'password: "[secret masqué]"',
        )

    def test_escaped_quotes_are_preserved(self):
        self.assertEqual(
            redact('{\\"password\\": \\"hunter22\\"}', []),
            '{\\"password\\": \\"[secret masqué]\\"}',
        )

    def test_value_embedded_in_a_json_string(self):
        self.assertEqual(
            redact('{"k": "DB_PASSWORD=hunter22"}', []),
            '{"k": "DB_PASSWORD=[secret masqué]"}',
        )

    def test_authorization_keeps_the_scheme(self):
        self.assertEqual(
            redact("Authorization: Basic dXNlcjpwYXNz", []),
            "Authorization: Basic [secret masqué]",
        )

    def test_token_is_replaced_entirely(self):
        self.assertEqual(redact("jeton glpat-" + "x" * 20 + " fin", []), "jeton [secret masqué] fin")

    def test_private_key_block_is_masked_with_its_body(self):
        pem = "\n".join([
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "b3BlbnNzaC1rZXktdjEAAAAA",
            "-----END OPENSSH PRIVATE KEY-----",
        ])
        self.assertEqual(redact("avant\n" + pem + "\naprès", []), "avant\n[secret masqué]\naprès")

    def test_truncated_private_key_is_masked_until_the_end(self):
        self.assertEqual(
            redact("avant\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow", []),
            "avant\n[secret masqué]",
        )

    def test_clean_text_is_unchanged(self):
        for text in CLEAN_TEXTS:
            with self.subTest(text=text):
                self.assertEqual(redact(text, []), text)

    def test_redacted_output_is_clean_and_stable(self):
        known = ["s3cr3t-value"]
        sample = "\n".join([
            "DB_HOST=db.example.org",
            "DB_PASSWORD=hunter22",
            'api_key: "abcd1234 efgh5678"',
            "Authorization: Bearer " + JWT,
            "Authorization: Basic dXNlcjpwYXNz",
            "ntn_" + "a" * 46,
            "AKIA" + "IOSFODNN7EXAMPLE",
            "-----BEGIN RSA PRIVATE KEY-----",
            "MIIEowIBAAKCAQEA",
            "-----END RSA PRIVATE KEY-----",
            "mot de passe s3cr3t-value",
        ])
        once = redact(sample, known)
        self.assertNotEqual(once, sample)
        self.assertEqual(scan_text(once, known), [])
        self.assertEqual(redact(once, known), once)
        self.assertIn("DB_HOST=db.example.org", once)


class KnownValuesFromEnvTest(unittest.TestCase):
    def test_env_values(self):
        self.assertEqual(
            known_values_from_env({"NOTION_TOKEN": "ntn_abcdef", "GM_AUDIT_AUTH_PASS": "abc"}),
            ["ntn_abcdef"],
        )

    def test_two_variables_in_a_stable_order(self):
        env = {
            "NOTION_TOKEN": "notion-1",
            "GM_AUDIT_AUTH_PASS": "auth-pass-1",
            "HOME": "/home/someone",
        }
        self.assertEqual(known_values_from_env(env), ["notion-1", "auth-pass-1"])

    def test_minimum_length_is_six(self):
        self.assertEqual(known_values_from_env({"NOTION_TOKEN": "12345"}), [])
        self.assertEqual(known_values_from_env({"NOTION_TOKEN": "123456"}), ["123456"])

    def test_missing_and_unrelated_variables(self):
        self.assertEqual(known_values_from_env({}), [])
        self.assertEqual(known_values_from_env({"HOME": "/home/someone", "PATH": "/usr/bin"}), [])

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(known_values_from_env({"NOTION_TOKEN": "  ntn_abcdef\r\n"}), ["ntn_abcdef"])
        self.assertEqual(known_values_from_env({"NOTION_TOKEN": "   abc   "}), [])

    def test_defaults_to_the_process_environment(self):
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "ntn_from_env"}, clear=True):
            self.assertEqual(known_values_from_env(), ["ntn_from_env"])


class ScanTreeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_scan_tree(self):
        self.write("a.txt", "ok")
        self.write("b.json", '{"k": "DB_PASSWORD=hunter22"}')
        self.assertEqual(scan_tree(self.root, []), [("b.json", "credential_assignment")])

    def test_clean_tree(self):
        self.write("a.txt", "ok")
        self.write("sub/b.md", "Le mot de passe est haché.")
        self.assertEqual(scan_tree(self.root, []), [])

    def test_empty_tree(self):
        self.assertEqual(scan_tree(self.root, []), [])

    def test_paths_are_relative_posix_and_sorted(self):
        self.write("z.env", "DB_PASSWORD=hunter22")
        self.write("a/b/c.log", "Authorization: Basic dXNlcjpwYXNz")
        self.assertEqual(
            scan_tree(self.root, []),
            [("a/b/c.log", "authorization_header"), ("z.env", "credential_assignment")],
        )

    def test_one_pair_per_label_and_file(self):
        self.write("x.txt", "DB_PASSWORD=hunter22\nTOKEN=abcd1234\nAuthorization: Basic dXNlcjpwYXNz")
        self.assertEqual(
            scan_tree(self.root, []),
            [("x.txt", "credential_assignment"), ("x.txt", "authorization_header")],
        )

    def test_known_values(self):
        self.write("note.txt", "le mot de passe est s3cr3t-value")
        self.assertEqual(scan_tree(self.root, ["s3cr3t-value"]), [("note.txt", "known_value")])

    def test_binary_file_is_scanned_without_error(self):
        (self.root / "blob.bin").write_bytes(b"\xff\xfe\x00DB_PASSWORD=hunter22\x80")
        self.assertEqual(scan_tree(self.root, []), [("blob.bin", "credential_assignment")])

    def test_exclude_skips_by_first_segment(self):
        self.write("fiches/a.md", "DB_PASSWORD=hunter22")
        self.write("findings/fiches/b.md", "DB_PASSWORD=hunter22")
        self.assertEqual(
            scan_tree(self.root, [], exclude=("fiches",)),
            [("findings/fiches/b.md", "credential_assignment")],
        )

    def test_missing_root_raises(self):
        with self.assertRaises(FileNotFoundError):
            scan_tree(self.root / "absent", [])

    def test_file_root_raises(self):
        self.write("a.txt", "ok")
        with self.assertRaises(NotADirectoryError):
            scan_tree(self.root / "a.txt", [])
