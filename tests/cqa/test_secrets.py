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

    def test_f_string_with_a_long_blank_run_stays_fast(self):
        for blank in (" ", "\t"):
            with self.subTest(blank=repr(blank)):
                text = "token = f'Bearer " + blank * 80000 + "x'"
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
        "password=${DB_PASSWORD}",
        "MYSQL_PASSWORD: $MYSQL_PASSWORD",
        "'password' => $_ENV['DB_PASSWORD'],",
        "Le mot de passe vient de password=$PASSWORD.",
        "PGPASSWORD=$DB_PASS psql -h db.example.org",
        "$token = $this->token;",
        "'token' => $request->bearerToken(),",
        "password: $password,",
        "$token = $this->jwtManager->create($user);",
        "'password' => $hasher->hashPassword($user, $plainPassword),",
        "'password' => $encoder->encodePassword($user, $plain),",
        "$password = $this->hash($password);",
        "$token = $jwt->encode($payload);",
        "'password' => $this->hasher->make($data['password']),",
        "$token = $request->get($field);",
        "$token = $this->em->getRepository(Token::class)->find($id);",
        "DB_PASSWORD=$(cat /run/secrets/db_password)",
        "password: ${MYSQL_PASSWORD:?required}",
        "token = f'Bearer {token}'",
        "TOKEN=$(openssl rand -hex 32)",
        'DB_PASSWORD="$(cat /run/secrets/db_password)"',
        "password: ${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}",
        "'token': f'{settings.API_TOKEN}'",
        "SECRET_KEY=$(head -c 32 /dev/urandom | base64)",
    ]
    # (line, its secret literal): masking must remove that literal, not only the first token.
    SECRETS = [
        ('password: "S3cr3t-Pass!"', "S3cr3t-Pass!"),
        ('password: "Pa(ss)word!"', "Pa(ss)word!"),
        ("DB_PASSWORD=Pa55word2024", "Pa55word2024"),
        ("'password' => 'hunter2hunter2'", "hunter2hunter2"),
        ("api_key=sk_live_51Habcdefghijkl", "sk_live_51Habcdefghijkl"),
        ("token: abcd1234efgh5678", "abcd1234efgh5678"),
        ("'password' => '$uper-Secret1',", "$uper-Secret1"),
        ('DB_PASSWORD="$ecretPa55"', "$ecretPa55"),
        ('"password": "$2y$10$abcdefghijklmnopqrstuv"', "$2y$10$abcdefghijklmnopqrstuv"),
        ('password: ":Secret123"', "Secret123"),
        ('"password": "password"', ': "password"'),
        ("'password' => env('DB_PASSWORD', 'RealSecret123'),", "RealSecret123"),
        ("'secret' => env('STRIPE_SECRET', 'sk_live_abcdef123456'),", "sk_live_abcdef123456"),
        ("'password' => Hash::make('Adm1nP@ss2024'),", "Adm1nP@ss2024"),
        ("'password' => bcrypt('Adm1nP@ss2024'),", "Adm1nP@ss2024"),
        ("$user->password = Hash::make('Adm1nP@ss2024');", "Adm1nP@ss2024"),
        ("'password' => $_ENV['DB_PASSWORD'] ?? 'RealSecret123',", "RealSecret123"),
        ("MYSQL_PASSWORD: ${MYSQL_PASSWORD:-S3cretDefault}", "S3cretDefault"),
        ("PASSWORD=${DB_PASSWORD:-S3cretDefault}", "S3cretDefault"),
        ("DB_PASSWORD=password", "=password"),
        ("password=:Secret123", "Secret123"),
        ("token = hmac.new(b'RealSecretKey123', msg, sha256).hexdigest()", "RealSecretKey123"),
        ("token = jwt.encode(payload, b'my-signing-secret-123', algorithm='HS256')", "my-signing-secret-123"),
        ("token = jwt.encode(payload, 'my-signing-secret-123', algorithm='HS256')", "my-signing-secret-123"),
        ("token = hmac.new(key, b'RealSecretKey123')", "RealSecretKey123"),
        ("token = hmac.new (key, b'RealSecretKey123')", "RealSecretKey123"),
        ("token = cfg[b'RealSecretKey123']", "RealSecretKey123"),
        ("token = base64.b64encode(b'admin:Adm1nP@ss2024').decode()", "Adm1nP@ss2024"),
        ("password = make_password(r'Adm1nP@ss2024')", "Adm1nP@ss2024"),
        ("password = make_password(f'Adm1nP@ss2024')", "Adm1nP@ss2024"),
        ("password = HASHBYTES('SHA2_256', N'Adm1nP@ss2024')", "Adm1nP@ss2024"),
        ("'password' => Hash::make('Adm1nP@ss2024'), // l'admin d'origine", "Adm1nP@ss2024"),
        ("MYSQL_PASSWORD: $$ecretPa55", "$ecretPa55"),
        ("WORDPRESS_DB_PASSWORD=$$ecretPa55", "$ecretPa55"),
        ("password_hash=$2y$10$abcdefghijklmnopqrstuv", "$2y$10$abcdefghijklmnopqrstuv"),
        ("password=$PASSWORD&token=abcd1234efgh5678", "abcd1234efgh5678"),
        ("?password=$PASSWORD&api_key=sk_live_51Habcdefghijkl", "sk_live_51Habcdefghijkl"),
        ("password=$user->name;secret=abcd1234efgh5678", "abcd1234efgh5678"),
        ("api_key=id=sk_live_51Habcdefghijkl", "sk_live_51Habcdefghijkl"),
        ("token=name=abcd1234efgh5678", "abcd1234efgh5678"),
        ("password=type=text&token=abcd1234efgh5678", "abcd1234efgh5678"),
        ("SECRET_KEY = b'RealSecretKey123'", "RealSecretKey123"),
        ("password = r'Adm1nP@ss2024'", "Adm1nP@ss2024"),
        ("$token = $jwt->encode($payload, 'my-signing-secret-123');", "my-signing-secret-123"),
        ("'password' => $hasher->hashPassword($user, 'Adm1nP@ss2024'),", "Adm1nP@ss2024"),
        ("DB_PASSWORD=$(echo Adm1nP@ss2024)", "Adm1nP@ss2024"),
        ("DB_PASSWORD=$(echo 'Adm1nP@ss2024' | base64)", "Adm1nP@ss2024"),
        ("PASSWORD=$(htpasswd -nbB admin Adm1nP@ss2024)", "Adm1nP@ss2024"),
        ('DB_PASSWORD="$(printf Adm1nP@ss2024)"', "Adm1nP@ss2024"),
        ("MYSQL_PASSWORD: ${MYSQL_PASSWORD:=S3cretDefault}", "S3cretDefault"),
        ("MYSQL_PASSWORD: ${MYSQL_PASSWORD:+S3cretDefault}", "S3cretDefault"),
        ("token = f'Adm1nP@ss{suffix}'", "Adm1nP@ss"),
        ("token = f'{prefix}Adm1nP@ss2024'", "Adm1nP@ss2024"),
        ("token = f'Bearer abcd1234efgh5678'", "abcd1234efgh5678"),
        ("password = f'{19800101}'", "19800101"),
        ("HTPASSWD=$(htpasswd -nb admin changeme)", "changeme"),
        ("ADMIN_PASSWORD_HASH=$(openssl passwd -6 changeme)", "changeme"),
        ("PASSWORD=$(mkpasswd -m sha-512 changeme)", "changeme"),
        ("PASSWORD=$(openssl enc -d -aes-256-cbc -k changeme -in /x)", "changeme"),
        ("DB_PASSWORD=$(mysql -uroot -prootpass app)", "rootpass"),
        ("X_PASSWORD=$(command echo Adm1nP4ss)", "Adm1nP4ss"),
        ("X_PASSWORD=$(builtin printf Adm1nP4ss)", "Adm1nP4ss"),
        ("X_PASSWORD=$(cat /x | echo Adm1nP4ss)", "Adm1nP4ss"),
        ("SECRET=$(decrypt Adm1n/P4ss)", "Adm1n/P4ss"),
        ("DB_PASSWORD=$(mysql --password=changeme app)", "changeme"),
        ("DB_PASSWORD=$(mysql --password changeme)", "changeme"),
        ("X_PASSWORD=$(exec cat /x | env echo changeme)", "changeme"),
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
        for text, literal in self.SECRETS:
            with self.subTest(text=text):
                self.assertIn(literal, text)
                labels = scan_text(text, [])
                self.assertEqual(labels[0], "credential_assignment")
                self.assertTrue(set(labels[1:]) <= {"mysql_password_option"}, labels)
                masked = redact(text, [])
                self.assertIn("[secret masqué]", masked)
                self.assertNotIn(literal, masked)
                self.assertEqual(scan_text(masked, []), [])
                self.assertEqual(redact(masked, []), masked)

    def test_a_masked_call_stops_at_the_end_of_its_line(self):
        text = "token = jwt.encode(payload, b'my-signing-secret-123')\nDB_HOST=db.example.org"
        self.assertEqual(redact(text, []), "token = [secret masqué]\nDB_HOST=db.example.org")

    def test_secret_after_a_form_description_is_still_masked(self):
        text = "password : type=password\nDB_PASSWORD=Pa55word2024"
        self.assertEqual(scan_text(text, []), ["credential_assignment"])
        self.assertEqual(redact(text, []), "password : type=password\nDB_PASSWORD=[secret masqué]")


class LiteralCredentialsTest(unittest.TestCase):
    """The guard of the fix mode flags a literal value only (design 4.2.1): an expression, a
    call, a superglobal, a variable or an operator on the right is not a secret."""

    WORDPRESS = ("$password = isset( $_POST['pass1'] ) ? (string) wp_unslash( $_POST['pass1'] ) "
                 ": '';")
    EXPRESSIONS = [
        WORDPRESS,
        "$token = !empty( $_GET['t'] ) ? sanitize_text_field( wp_unslash( $_GET['t'] ) ) : null;",
        "$password = (string) $request->get( 'password' );",
        "$secret = wp_unslash( $_POST['secret'] ?? '' );",
    ]
    STILL_SECRETS = [
        "$password = 'hunter2hunter2';",
        "DB_PASSWORD=hunter22 # prod",
        "$password = isset( $x ) ? 'Adm1nP@ss2024' : '';",
        "$password = wp_hash( 'Adm1nP@ss2024' );",
        "password=isset(x)&token=abcd1234efgh5678",
        "PASSWORD=(hunter22)",
        "password: hunter22 (prod)",
    ]

    def test_literal_only_credentials(self):
        self.assertEqual(scan_text(self.WORDPRESS, []), ["credential_assignment"])
        for text in self.EXPRESSIONS:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, [], literal_credentials=True), [])
        for text in self.STILL_SECRETS:
            for mode in (False, True):
                with self.subTest(text=text, literal_credentials=mode):
                    self.assertEqual(scan_text(text, [], literal_credentials=mode),
                                     ["credential_assignment"])
        for text, _ in NonSecretTest.SECRETS:
            with self.subTest(text=text):
                labels = scan_text(text, [], literal_credentials=True)
                self.assertEqual(labels[0], "credential_assignment")
                self.assertTrue(set(labels[1:]) <= {"mysql_password_option"}, labels)
        for label, text in PATTERN_CASES:
            with self.subTest(label=label, text=text[:30]):
                self.assertEqual(scan_text(text, [], literal_credentials=True), [label])
        for text in NonSecretTest.NON_SECRETS + CLEAN_TEXTS:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, [], literal_credentials=True), [])

    def test_redact_keeps_masking_expressions(self):
        self.assertEqual(redact(self.WORDPRESS, []), "$password = [secret masqué]")


class ProcessAndKubeSecretsTest(unittest.TestCase):
    """A database password on a command line (ps auxww) and a kubeconfig key (F2, F16)."""

    def test_password_option_of_database_clients_is_masked(self):
        for line, kept in (
            ("mysql -uroot -phunter2 db", "mysql -uroot -p"),
            ("root 4242 0.0 0.1 mysqldump -h db -u app -pS3cr3t! shop", "mysqldump -h db -u app -p"),
            ("mariadb -u app -p'two words' shop", "mariadb -u app -p"),
            ("/usr/bin/mariadb-dump --single-transaction -pAbc123 shop", "--single-transaction -p"),
            ("mysqladmin -u root -proot status", "mysqladmin -u root -p"),
        ):
            with self.subTest(line=line):
                masked = redact(line, [])
                self.assertIn(kept + "[secret masqué]", masked)
                self.assertNotIn("hunter2", masked)
                self.assertNotIn("S3cr3t", masked)
                self.assertNotIn("two words", masked)
                self.assertNotIn("Abc123", masked)
                self.assertEqual(scan_text(line, []), ["mysql_password_option"])
                self.assertEqual(scan_text(masked, []), [])
                self.assertEqual(redact(masked, []), masked)

    def test_database_command_lines_without_a_password_value_stay(self):
        for line in (
            "mysql -u root -p shop",
            "mysql -h db -P 3306 shop",
            "/usr/sbin/mysqld --pid-file=/run/mysqld/mysqld.pid",
            "mysql --protocol=tcp shop",
            "Le client mysql demande le mot de passe avec -p seul.",
        ):
            with self.subTest(line=line):
                self.assertEqual(redact(line, []), line)
                self.assertEqual(scan_text(line, []), [])

    def test_kubeconfig_client_key_is_masked(self):
        line = "    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQo="
        masked = redact(line, [])
        self.assertEqual(masked, "    client-key-data: [secret masqué]")
        self.assertEqual(scan_text(line, []), ["kube_client_key"])
        self.assertEqual(scan_text(masked, []), [])
        certificate = "    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCg=="
        self.assertEqual(redact(certificate, []), certificate)


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

    def test_skip_dirs_matches_whole_segments(self):
        for relative in ("fix/front/securite/wt/a.php", "fix/front/securite/report.json",
                         "fix/front/wt/b.php", "fix/history/x/front/securite/wt/c.php"):
            self.write(relative, "DB_PASSWORD=hunter22")
        self.assertEqual(
            [path for path, _ in scan_tree(self.root, [], skip_dirs=("fix/*/*/wt",))],
            ["fix/front/securite/report.json", "fix/front/wt/b.php",
             "fix/history/x/front/securite/wt/c.php"],
        )

    def test_missing_root_raises(self):
        with self.assertRaises(FileNotFoundError):
            scan_tree(self.root / "absent", [])

    def test_file_root_raises(self):
        self.write("a.txt", "ok")
        with self.assertRaises(NotADirectoryError):
            scan_tree(self.root / "a.txt", [])
