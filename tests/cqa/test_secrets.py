import base64
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, quote_plus

from cqa.secrets import (MASK, known_values_from_env, redact, scan_lines, scan_spans, scan_text,
                         scan_tree)

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
    ("credential_define", "define( 'DB_PASSWORD', 'Adm1nP@ss2024' );"),
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
                self.assertTrue(set(labels[1:]) <= {"mysql_password_option", "provider_key"}, labels)
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


class CodeCredentialsTest(unittest.TestCase):
    """Credentials in code, as the audit and the guard of the fix mode both scan them: the same
    strict rule; the guard lets the lead waive a heuristic alert instead (fix_guard)."""

    WORDPRESS = ("$password = isset( $_POST['pass1'] ) ? (string) wp_unslash( $_POST['pass1'] ) "
                 ": '';")
    STILL_SECRETS = [
        "$password = 'hunter2hunter2';",
        "DB_PASSWORD=hunter22 # prod",
        "$password = isset( $x ) ? 'Adm1nP@ss2024' : '';",
        "$password = wp_hash( 'Adm1nP@ss2024' );",
        "password=isset(x)&token=abcd1234efgh5678",
        "PASSWORD=(hunter22)",
        "password: hunter22 (prod)",
        # A bare token that only looks like a call, a negation or a cast.
        "DB_PASSWORD=Adm1n(2024)!",
        "DB_PASSWORD=Adm1n(2024",
        "API_KEY=sk(4f9a8b7c6d5e4f3a2b1c)z",
        "password: Hunter2(prod)x",
        "PASSWORD=!$uperS3cret",
        "password=!Secret(x)y",
        "PASSWORD=(string)hunter22",
        "token: Abc(def ghi jkl",
        "$password = foo(1) . Adm1nP4ss;",
        "$password = foo( 1 ) . Adm1nP4ss;",
        "$password = isset( $_POST['p'] ) ? Adm1nP4ss : '';",
        "$password = isset( $x&token=abcd1234efgh5678 );",
        "$password = (string) Adm1nP4ss;",
        "$password = isset( $x ) ? $y : 2024;",
        "password = isset(",
        # A quoted fallback or concatenated part of the value.
        "$password = getenv( 'DB_PASSWORD' ) ?: 'AZERTY2024';",
        WORDPRESS[:-3] + "'AZERTY2024';",
        "'password' => getenv( 'DB_PASSWORD' ) ?: 'PROD_DB_2024',",
        "$password = getenv( 'DB_PASSWORD' ) ?: 's3cr3t.pass';",
        "$password = getenv( 'DB_PASSWORD' ) ?: 'gingerminds.2024';",
        "$token = env( 'API_TOKEN' ) ?? 'sk.live.4f9a8b7c6d5e4f3a';",
        "$password = isset( $x ) ? $x : 'AZERTY2024' . $suffix;",
    ]

    def test_strict_rule_flags_every_known_credential_form(self):
        self.assertEqual(scan_text(self.WORDPRESS, []), ["credential_assignment"])
        for text in self.STILL_SECRETS:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), ["credential_assignment"])

    DEFINED = [
        "define( 'DB_PASSWORD', 'Adm1nP@ss2024' );",
        "define('DB_PASSWORD','Adm1nP@ss2024');",
        'define( "SMTP_PASSWD", "Adm1nP@ss2024" );',
        "define( 'DB_PWD', 'x' );",
        "define( 'MY_APP_SECRET', 'Adm1nP@ss2024' );",
        "define( 'API_TOKEN', 'abcd1234efgh5678' );",
        "DEFINE( 'db_password', 'Adm1nP@ss2024' );",
        "define(\n    'DB_PASSWORD',\n    'Adm1nP@ss2024'\n);",
        *[f"define( '{name}', 'k;X}}9+mJ|<w@F' );" for name in (
            "AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY",
            "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT")],
    ]
    NOT_DEFINED = [
        "define( 'DB_PASSWORD', getenv( 'DB_PASSWORD' ) );",
        "define( 'DB_PASSWORD', $password );",
        "define( 'DB_PASSWORD', DB_PASSWORD_FROM_ENV );",
        "define( 'DB_PASSWORD', '' );",
        "define( 'WP_DEBUG', false );",
        "define( 'WP_ENVIRONMENT_TYPE', 'staging' );",
        "define( 'DB_NAME', 'wordpress' );",
        "define( 'OAUTH_KEY_URL', 'https://example.test/oauth' );",
        "undefine( 'DB_PASSWORD', 'Adm1nP@ss2024' );",
    ]

    def test_defined_credentials(self):
        for text in self.DEFINED:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), ["credential_define"])
            with self.subTest(redact=text):
                masked = redact(text, [])
                self.assertIn("[secret masqué]", masked)
                self.assertEqual(scan_text(masked, []), [])
                self.assertEqual(redact(masked, []), masked)
        for text in self.NOT_DEFINED:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text, []), [])
        self.assertEqual(redact("define( 'DB_PASSWORD', 'Adm1nP@ss2024' );", []),
                         "define( 'DB_PASSWORD', '[secret masqué]' );")

    def test_redact_masks_the_whole_expression(self):
        self.assertEqual(redact(self.WORDPRESS, []), "$password = [secret masqué]")

    def test_scan_lines_names_the_line_of_each_label(self):
        aws = "AKIA" + "IOSFODNN7EXAMPLE"
        text = "\n".join([
            "<?php",
            self.WORDPRESS,
            "$name = 'x';",
            f"$key = '{aws}'; // s3cr3t-value",
            "define(",
            "    'DB_PASSWORD',",
            "    'Adm1nP@ss2024'",
            ");",
            "s3cr3t-value",
        ])
        self.assertEqual(scan_lines(text, ["s3cr3t-value", ""]), [
            ("credential_assignment", 2),
            ("known_value", 4), ("aws_key", 4),
            ("credential_define", 5),
            ("known_value", 9),
        ])
        self.assertEqual(sorted({label for label, _ in scan_lines(text, ["s3cr3t-value"])}),
                         sorted(scan_text(text, ["s3cr3t-value"])))
        self.assertEqual(scan_lines("rien à signaler", ["s3cr3t-value"]), [])

    def test_scan_spans_names_the_first_and_last_line_of_each_match(self):
        """A define( or a private key over several lines spans them: the guard reports it when
        one of its lines is added, at the line where it starts."""
        text = "\n".join([
            "<?php",
            "define(",
            "    'DB_PASSWORD',",
            "    'Adm1nP@ss2024'",
            ");",
            self.WORDPRESS,
            "-----BEGIN RSA PRIVATE KEY-----",
            "MIIEowIBAAKCAQEA",
            "-----END RSA PRIVATE KEY-----",
            "s3cr3t-value",
        ])
        self.assertEqual(scan_spans(text, ["s3cr3t-value"]), [
            ("credential_define", 2, 4),
            ("credential_assignment", 6, 6),
            ("private_key", 7, 9),
            ("known_value", 10, 10),
        ])
        self.assertEqual(scan_lines(text, ["s3cr3t-value"]),
                         [(label, first) for label, first, _ in scan_spans(text, ["s3cr3t-value"])])
        self.assertEqual(scan_spans("rien à signaler", ["s3cr3t-value"]), [])

    def test_an_unclosed_private_key_spans_its_first_line_only(self):
        """Without an END line the audit still flags the key, but the guard must not take every
        later line of the file for part of it."""
        text = "\n".join(["<?php", "-----BEGIN RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA",
                          "// a", "// b"])
        self.assertEqual(scan_text(text, []), ["private_key"])
        self.assertEqual(scan_spans(text, []), [("private_key", 2, 2)])
        closed = text + "\n-----END RSA PRIVATE KEY-----\n// c"
        self.assertEqual(scan_spans(closed, []), [("private_key", 2, 6)])


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


PASSWORD = "Sup3rS3cretDb"
SALT = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4"
APP_KEY = "base64:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmE="
LIVE_KEY = "sk_live_" + "51H8abcdEFGHijklMNOPqrstUVWX"


class CommonSecretFormsTest(unittest.TestCase):
    """The secrets a plain read of a configuration file or a process list puts in the output (B-02)."""

    def assert_masked(self, text, literal, expected=None):
        masked = redact(text, [])
        self.assertNotIn(literal, masked, text)
        self.assertIn(MASK, masked, text)
        if expected is not None:
            self.assertEqual(masked, expected)
        self.assertNotEqual(scan_text(text, []), [], text)
        self.assertEqual(scan_text(masked, []), [], masked)
        self.assertEqual(redact(masked, []), masked, text)

    def assert_untouched(self, text):
        self.assertEqual(redact(text, []), text)
        self.assertEqual(scan_text(text, []), [], text)

    def test_redact_database_url_credentials(self):
        for text, expected in (
            (f"DATABASE_URL=mysql://app:{PASSWORD}@db:3306/app",
             f"DATABASE_URL=mysql://app:{MASK}@db:3306/app"),
            (f'DATABASE_URL: "postgres://app:{PASSWORD}@db/app"',
             f'DATABASE_URL: "postgres://app:{MASK}@db/app"'),
            (f"root 12 0.0 curl https://admin:{PASSWORD}@example.org/x",
             f"root 12 0.0 curl https://admin:{MASK}@example.org/x"),
            (f"psql postgresql://app:{PASSWORD}@db/app", f"psql postgresql://app:{MASK}@db/app"),
            (f"REDIS_URL=redis://:{PASSWORD}@cache:6379/0", f"REDIS_URL=redis://:{MASK}@cache:6379/0"),
            ("amqp://guest:p%40ss%3Aw0rd@mq:5672/%2f", f"amqp://guest:{MASK}@mq:5672/%2f"),
            (f"mongodb+srv://app:{PASSWORD}@cluster.example.org/db?retryWrites=true",
             f"mongodb+srv://app:{MASK}@cluster.example.org/db?retryWrites=true"),
            ("psql postgresql://app:Sup3r@S3cret@db/app", f"psql postgresql://app:{MASK}@db/app"),
        ):
            with self.subTest(text=text):
                literal = text.split("://")[1].split(":", 1)[1].rsplit("@", 1)[0]
                self.assert_masked(text, literal, expected)
        self.assertEqual(scan_text(f"mysql://app:{PASSWORD}@db/app", []), ["url_credentials"])

    def test_url_without_credentials_or_with_a_placeholder_is_kept(self):
        for text in (
            "https://example.org:8080/path",
            "ssh://git@example.org:22/org/repo.git",
            "git@example.org:org/repo.git",
            "mysql://app@db/app",
            "mysql://app:${DB_PASSWORD}@db/app",
            "mysql://app:$DB_PASSWORD@db/app",
            "postgres://user:<password>@host/db",
            "https://example.org/a@b",
            "Le lien https://example.org:443 est valide, écrire à camille@example.org.",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_pass_suffix_names(self):
        for text, expected in (
            (f"DB_PASS={PASSWORD}", f"DB_PASS={MASK}"),
            (f"smtp_pass: {PASSWORD}", f"smtp_pass: {MASK}"),
            (f"pass: {PASSWORD}", f"pass: {MASK}"),
            (f"'smtp_pass' => '{PASSWORD}',", f"'smtp_pass' => '{MASK}',"),
            (f"dbPass = '{PASSWORD}'", f"dbPass = '{MASK}'"),
            ("DB_PASS=hunter", f"DB_PASS={MASK}"),
            (f"mot_de_passe = {PASSWORD}", f"mot_de_passe = {MASK}"),
            (f"credentials: admin:{PASSWORD}", f"credentials: {MASK}"),
            ('auth: "YWRtaW46U3VwM3JTM2NyZXREYg=="', f'auth: "{MASK}"'),
            (f"APP_KEY={APP_KEY}", f"APP_KEY={MASK}"),
            ("ENCRYPTION_KEY=0123abcd4567ef89", f"ENCRYPTION_KEY={MASK}"),
            (f"$databases['default']['default']['password'] = '{PASSWORD}';",
             f"$databases['default']['default']['password'] = '{MASK}';"),
        ):
            with self.subTest(text=text):
                literal = next(part for part in (PASSWORD, "hunter", "YWRtaW46U3VwM3JTM2NyZXREYg==",
                                                 APP_KEY, "0123abcd4567ef89") if part in text)
                self.assert_masked(text, literal, expected)

    def test_a_short_value_after_a_password_name_is_masked(self):
        for text, expected in (
            ("MAIL_PASSWORD=abc", f"MAIL_PASSWORD={MASK}"),
            ("password=abc", f"password={MASK}"),
            ('"passwd": "xyz"', f'"passwd": "{MASK}"'),
            ("DB_PASS=abc", f"DB_PASS={MASK}"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, text[-4:].strip('"=: '), expected)
        for text in ("token=abc", "password: ***", "password: ...", "password=no", "password: "):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_hash_salt(self):
        for text, expected in (
            (f"$settings['hash_salt'] = '{SALT}';", f"$settings['hash_salt'] = '{MASK}';"),
            (f"hash_salt: {SALT}", f"hash_salt: {MASK}"),
            (f"SALT={SALT}", f"SALT={MASK}"),
            (f"hashSalt: '{SALT}'", f"hashSalt: '{MASK}'"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, SALT, expected)
        for text in ("salt: 10", "saltRounds: 12", "hash_salt: from_env", "hash_salt: ${HASH_SALT}"):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_space_separated_setenv_and_cli_options(self):
        for text, expected in (
            (f"SetEnv DB_PASSWORD {PASSWORD}", f"SetEnv DB_PASSWORD {MASK}"),
            (f"fastcgi_param DB_PASSWORD {PASSWORD};", f"fastcgi_param DB_PASSWORD {MASK};"),
            (f'fastcgi_param DB_PASSWORD "{PASSWORD}";', f'fastcgi_param DB_PASSWORD "{MASK}";'),
            (f"env[DB_PASS] = {PASSWORD}", f"env[DB_PASS] = {MASK}"),
            (f"ENV DB_PASSWORD {PASSWORD}", f"ENV DB_PASSWORD {MASK}"),
            (f"root 12 0.0 mysqldump --user=root --password {PASSWORD} app",
             f"root 12 0.0 mysqldump --user=root --password {MASK} app"),
            (f"mysql --password '{PASSWORD}' app", f"mysql --password '{MASK}' app"),
            (f"app --api-key {PASSWORD} --verbose", f"app --api-key {MASK} --verbose"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, PASSWORD, expected)
        for text in (
            "mysqldump --password $DB_PASS app",
            'mysqldump --password "$DB_PASS" app',
            "mysqldump --password=$DB_PASS app",
            "mysqldump --password --single-transaction app",
            "mysql --no-password",
            "mysql --password-file /etc/mysql/pw.cnf",
            "SetEnv DB_PASSWORD ${DB_PASSWORD}",
            "SetEnv APP_ENV production",
            "export token expired yesterday",
            "the env password changed yesterday",
            "fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;",
            "fastcgi_param HTTP_AUTHORIZATION $http_authorization;",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_curl_user_and_redis_auth(self):
        for text, expected in (
            (f"root 12 0.0 curl -u admin:{PASSWORD} https://example.org",
             f"root 12 0.0 curl -u admin:{MASK} https://example.org"),
            (f"curl -s --user admin:{PASSWORD} https://example.org",
             f"curl -s --user admin:{MASK} https://example.org"),
            (f"curl -u 'admin:{PASSWORD}' https://example.org",
             f"curl -u 'admin:{MASK}' https://example.org"),
            (f"redis-cli -a {PASSWORD} ping", f"redis-cli -a {MASK} ping"),
            (f"redis-cli -h cache -p 6379 -a {PASSWORD} ping",
             f"redis-cli -h cache -p 6379 -a {MASK} ping"),
            (f"redis-cli --pass {PASSWORD} ping", f"redis-cli --pass {MASK} ping"),
            (f"sshpass -p {PASSWORD} ssh deploy@example.org",
             f"sshpass -p {MASK} ssh deploy@example.org"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, PASSWORD, expected)
        for text in (
            "curl -u admin https://example.org",
            "curl -u admin:$PASS https://example.org",
            "redis-cli -a $REDIS_PASS ping",
            "redis-cli -h cache ping",
            "grep -a pattern /var/log/syslog",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_cookie_header(self):
        for text, expected in (
            (f"Cookie: SESSabcdef0123456789={SALT}", f"Cookie: {MASK}"),
            (f"Cookie: lang=fr; session={PASSWORD}", f"Cookie: {MASK}"),
            (f'"Cookie": "SESS={PASSWORD}"', f'"Cookie": "{MASK}"'),
            (f"curl -H 'Cookie: sid={PASSWORD}' https://example.org",
             f"curl -H 'Cookie: {MASK}' https://example.org"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, PASSWORD if PASSWORD in text else SALT, expected)
        for text in (
            "Cookie: ",
            "en-tête Cookie: absent",
            "Set-Cookie: sid=abc123; Path=/; Secure; HttpOnly; SameSite=Lax",
            "$headers['Cookie'] = $cookie;",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_redact_prefixed_live_key(self):
        for key in (
            LIVE_KEY,
            "rk_live_" + "51H8abcdEFGHijklMNOPqrstUVWX",
            "sk_test_" + "51H8abcdEFGHijklMNOPqrstUVWX",
            "whsec_" + "abcdEFGHijklMNOPqrstUVWX0123",
            "xoxb-" + "1234567890-abcdefghijKLMN",
        ):
            for template in ("{}", "STRIPE_KEY: {}", "clé {} fin"):
                text = template.format(key)
                with self.subTest(text=text):
                    self.assert_masked(text, key)
        for text in ("task_live_status", "sk_live", "disk_live_reload: on", "risk_test_plan"):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_scan_text_raises_on_each_form_that_redact_masks(self):
        forms = (
            f"DATABASE_URL=mysql://app:{PASSWORD}@db:3306/app",
            f"DB_PASS={PASSWORD}",
            f"smtp_pass: {PASSWORD}",
            f"$settings['hash_salt'] = '{SALT}';",
            f"SetEnv DB_PASSWORD {PASSWORD}",
            f"fastcgi_param DB_PASSWORD {PASSWORD};",
            f"env[DB_PASS] = {PASSWORD}",
            f"mysqldump --user=root --password {PASSWORD} app",
            f"redis-cli -a {PASSWORD} ping",
            f"curl -u admin:{PASSWORD} https://example.org",
            f"Cookie: SESS={PASSWORD}",
            f"STRIPE_KEY: {LIVE_KEY}",
            LIVE_KEY,
            f"APP_KEY={APP_KEY}",
            f"credentials: admin:{PASSWORD}",
            "MAIL_PASSWORD=abc",
        )
        for text in forms:
            with self.subTest(text=text):
                self.assertNotEqual(scan_text(text, []), [])
                self.assertNotEqual(scan_text("Résumé : " + text + "\nfin", []), [])
                masked = redact(text, [])
                self.assertNotEqual(masked, text)
                self.assertEqual(scan_text(masked, []), [])
        text = "\n".join(forms)
        self.assertEqual(scan_text(redact(text, []), []), [])
        self.assertEqual(sorted({label for label, _ in scan_lines(text, [])}),
                         sorted(scan_text(text, [])))

    def test_the_named_forms_stay_waivable_by_the_lead(self):
        text = f"<?php\n// x\nDB_PASS={PASSWORD}\n"
        self.assertEqual(scan_lines(text, []), [("credential_assignment", 3)])

    def test_redact_does_not_mask_benign_policy_lines(self):
        for text in (
            "password_policy: strong",
            "password_hash: bcrypt",
            "password_hashing: argon2id",
            "password_min_length: 12",
            "password_required: true",
            "password_file: /run/credentials/db",
            "'password_algorithm' => 'sha512',",
            "token_lifetime: 3600",
            "token_type: Bearer",
            "secret_name: db_credentials",
            "auth: required",
            "auth: basic",
            "credentials: include",
            "fetch(url, {credentials: 'include'})",
            "smoke_pass: true",
            "pass: true",
            "passenger: nginx",
            "pass_through: on",
            "compass: north-12345",
            "bypass: Sup3rS3cretDb-is-not-a-name",
            "ssh_key: /home/deploy/.ssh/id_ed25519",
            "primary_key: id",
            "cache_key: user-list",
            "salt: 10",
            "Le mot de passe est haché avec bcrypt ; la politique impose 12 caractères.",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_code_that_only_looks_like_a_secret_name_is_kept(self):
        for text in (
            'NO_PASS = "Aucune passe fix en cours : lancez d\'abord cqa_fix.py open."',
            'OUT_OF_PASS = "commit hors de la passe {number} : {sha}"',
            'published_pass = key.get("pass") or 0',
            '_emit({"pass": args.number, "base": base})',
            'f"pass-start --pass {args.number}."',
            "values[token] = args[index]",
            'key["secret_waivers"] = waivers',
            "auth: Optional[tuple] = None,",
            "auth = _auth_from_env() if args.auth_env else None",
            'LEFT_KEY = "- {repo} : {reason} (branche {branch})."',
            "_SQL_CREDENTIAL = re.compile(",
            "MYSQL_PASSWORD_FILE: /run/secrets/db_password",
            "AUTH_TOKEN_URL=https://auth.example.org/token",
            "grep -a pattern /var/log/syslog",
            "auth_basic_user_file /etc/nginx/.htpasswd;",
            "proxy_set_header Authorization $http_authorization;",
            "SetEnv SYMFONY_SECRET_PLACEHOLDER",
            "GM_AUDIT_AUTH_PASS='<mot de passe>' python3 cqa_plan.py scan",
        ):
            with self.subTest(text=text):
                self.assert_untouched(text)

    def test_a_secret_in_an_array_index_is_masked_when_it_looks_like_one(self):
        for text, expected in (
            (f"$conf['password'] = '{PASSWORD}';", f"$conf['password'] = '{MASK}';"),
            (f"$conf['smtp']['password'] = '{PASSWORD}';", f"$conf['smtp']['password'] = '{MASK}';"),
            (f"env['DB_PASSWORD'] = {PASSWORD}", f"env['DB_PASSWORD'] = {MASK}"),
        ):
            with self.subTest(text=text):
                self.assert_masked(text, PASSWORD, expected)

    def test_new_forms_stay_fast_on_pathological_input(self):
        for text in (
            "pass" * 20000, "_PASS" * 20000, "curl -u " * 20000, "redis-cli " * 20000,
            "SetEnv " * 20000, "--password " * 20000, "a://:" * 20000, "x://" + "u:" * 50000,
            "Cookie: a=" * 20000, "A_" * 50000 + "B", "sk_live_" + "a" * 200000,
        ):
            with self.subTest(text=text[:20]):
                started = time.monotonic()
                scan_text(text, [])
                redact(text, [])
                self.assertLess(time.monotonic() - started, 5)

    def test_a_policy_name_with_a_real_looking_value_is_still_masked(self):
        for text in (
            f"password_hash: {PASSWORD}",
            "password_hash=$2y$10$abcdefghijklmnopqrstuv",
            f"password_policy: {PASSWORD}",
        ):
            with self.subTest(text=text):
                self.assert_masked(text, text.split(": ")[-1].split("=")[-1])


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

    def test_known_values_include_basic_base64_and_url_encoded_password(self):
        password = "p@ss w/rd:2026"
        env = {"GM_AUDIT_AUTH_USER": "camille", "GM_AUDIT_AUTH_PASS": password}
        basic = base64.b64encode(f"camille:{password}".encode()).decode()
        values = known_values_from_env(env)
        self.assertEqual(values[0], password)
        self.assertIn(basic, values)
        self.assertIn(quote(password, safe=""), values)
        self.assertIn(quote_plus(password), values)
        self.assertEqual(len(values), len(set(values)))
        text = (f"Authorization: Basic {basic}\ncurl https://camille:{quote(password, safe='')}"
                f"@example.org\nform={quote_plus(password)}\npassword {password}")
        masked = redact(text, values)
        for secret in (basic, quote(password, safe=""), quote_plus(password), password):
            self.assertNotIn(secret, masked)
        self.assertIn("known_value", scan_text(f"x {basic} y", values))
        self.assertEqual(scan_text(masked, values), [])

    def test_derived_forms_need_what_they_derive_from(self):
        self.assertEqual(known_values_from_env({"GM_AUDIT_AUTH_PASS": "auth-pass-1"}), ["auth-pass-1"])
        self.assertEqual(
            known_values_from_env({"GM_AUDIT_AUTH_USER": "camille", "GM_AUDIT_AUTH_PASS": "  "}), [])
        basic = base64.b64encode(b"camille:auth-pass-1").decode()
        self.assertEqual(
            known_values_from_env({"GM_AUDIT_AUTH_USER": "camille",
                                   "GM_AUDIT_AUTH_PASS": "auth-pass-1"}),
            ["auth-pass-1", basic])

    def test_a_short_password_still_masks_its_basic_form(self):
        basic = base64.b64encode(b"camille:abc").decode()
        self.assertEqual(
            known_values_from_env({"GM_AUDIT_AUTH_USER": "camille", "GM_AUDIT_AUTH_PASS": "abc"}),
            [basic])


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
