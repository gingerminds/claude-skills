import contextlib
import io
import unittest
from unittest import mock

from technical_architecture.architecture import main, mask_secrets

MASK = "[masqué]"


class MaskTest(unittest.TestCase):
    def test_masked_forms(self):
        cases = {
            "0 3 * * * MYSQL_PASSWORD=Sup3r! /usr/bin/backup.sh": "0 3 * * * MYSQL_PASSWORD=[masqué] /usr/bin/backup.sh",
            "Authorization: Bearer abc.def": "Authorization: Bearer [masqué]",
            "api_key = k-123 other=keep": "api_key = [masqué] other=keep",
            "token: xyz": "token: [masqué]",
            "0 2 * * * mysqldump -uroot -pS3cretPw base > /tmp/d.sql": "0 2 * * * mysqldump -uroot -p[masqué] base > /tmp/d.sql",
            "curl https://user:hunter2@example.org/x": "curl https://user:[masqué]@example.org/x",
            "DATABASE_URL=postgres://app:Pa55word@db/app": "DATABASE_URL=postgres://app:[masqué]@db/app",
            "REDIS_URL: redis://:redispass@redis:6379": "REDIS_URL: redis://:[masqué]@redis:6379",
            "- traefik.http.middlewares.a.basicauth.users=admin:$$apr1$$x$$y": "- traefik.http.middlewares.a.basicauth.users=[masqué]",
            "SENTRY_DSN=https://k3y@o1.ingest.example.org/2": "SENTRY_DSN=[masqué]",
            "AWS_SECRET_ACCESS_KEY=abc/def": "AWS_SECRET_ACCESS_KEY=[masqué]",
            "client_credentials: s3cr3t": "client_credentials: [masqué]",
        }
        for line, expected in cases.items():
            with self.subTest(line):
                self.assertEqual(mask_secrets(line), expected)

    def test_harmless_lines_are_kept(self):
        for line in (
            "15 * * * * /usr/bin/php /app/cron.php été",
            "ports: [\"443:443\"]",
            "image: nginx:1.27",
            "server_name www.example.org;",
            "ssh -p 22022 deploy@example.org",
        ):
            with self.subTest(line):
                self.assertEqual(mask_secrets(line), line)

    def test_mask_command_filters_stdin(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("token: xyz\nimage: nginx\n")), contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["mask"]), 0)
        self.assertEqual(stdout.getvalue(), "token: [masqué]\nimage: nginx\n")


if __name__ == "__main__":
    unittest.main()
