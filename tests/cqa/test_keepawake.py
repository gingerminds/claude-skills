import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cqa-keepawake.sh"

# Stand-in for the real inhibitors: like them, it runs its trailing command as a child, so
# only a kill of the whole process group stops everything.
INHIBITOR_STUB = """#!/bin/sh
echo "$@" >> "$STUB_LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    --*) shift ;;
    *) break ;;
  esac
done
"$@" &
wait
"""
CAFFEINATE_STUB = """#!/bin/sh
echo "caffeinate $@" >> "$STUB_LOG"
sleep infinity &
wait
"""
BASE_TOOLS = ("bash", "env", "ps", "sleep", "setsid", "kill", "rm", "dirname", "cat", "mkdir", "printf")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return True
    return bool(state) and not state.startswith("Z")


def wait_gone(pid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


class KeepawakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cqa-keepawake-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.log = self.tmp / "stub.log"
        self.pid_file = self.run_dir / "keepawake.pid"
        self.bash = shutil.which("bash")
        for name in BASE_TOOLS:
            found = shutil.which(name)
            if found and name != "bash":
                (self.bin / name).symlink_to(found)
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        if self.pid_file.exists():
            parts = self.pid_file.read_text().split()
            if parts and parts[0].isdigit():
                try:
                    os.killpg(int(parts[0]), 9)
                except (ProcessLookupError, PermissionError):
                    pass

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def keepawake(self, *args):
        env = {"PATH": str(self.bin), "STUB_LOG": str(self.log), "HOME": str(self.tmp)}
        return subprocess.run(
            [self.bash, str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def pid(self):
        return int(self.pid_file.read_text().split()[0])

    def children_of_group(self, pgid):
        out = subprocess.run(
            ["ps", "-eo", "pid=,pgid="], capture_output=True, text=True
        ).stdout
        return [int(p) for p, g in (l.split() for l in out.splitlines()) if int(g) == pgid]

    def test_keepawake_start_uses_systemd_inhibit(self):
        self.stub("systemd-inhibit", INHIBITOR_STUB)
        result = self.keepawake("start", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.pid_file.exists())
        time.sleep(0.3)
        self.assertTrue(alive(self.pid()))
        logged = self.log.read_text()
        self.assertIn("--what=sleep:idle:handle-lid-switch", logged)
        self.assertIn("--mode=block", logged)
        self.assertIn("--who=gm:cqa", logged)
        self.assertIn("--why=", logged)
        self.assertIn("sleep infinity", logged)

    def test_keepawake_start_uses_caffeinate_without_systemd(self):
        self.stub("caffeinate", CAFFEINATE_STUB)
        result = self.keepawake("start", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.pid_file.exists())
        time.sleep(0.3)
        self.assertTrue(alive(self.pid()))
        self.assertIn("caffeinate -dims", self.log.read_text())

    def test_keepawake_without_inhibitor_warns_and_succeeds(self):
        result = self.keepawake("start", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mise en veille", result.stderr)
        self.assertFalse(self.pid_file.exists())
        self.assertEqual(self.keepawake("stop", str(self.run_dir)).returncode, 0)

    def test_keepawake_stop_kills_the_inhibitor_and_its_children(self):
        self.stub("systemd-inhibit", INHIBITOR_STUB)
        self.keepawake("start", str(self.run_dir))
        time.sleep(0.3)
        pid = self.pid()
        members = self.children_of_group(pid)
        self.assertGreaterEqual(len(members), 2, members)
        result = self.keepawake("stop", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        for member in members:
            self.assertTrue(wait_gone(member), member)
        self.assertFalse(self.pid_file.exists())

    def test_keepawake_stop_ignores_a_foreign_pid(self):
        foreign = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(foreign.wait)
        self.addCleanup(foreign.kill)
        self.pid_file.write_text(f"{foreign.pid} systemd-inhibit\n")
        result = self.keepawake("stop", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(foreign.poll())
        self.assertFalse(self.pid_file.exists())

    def test_keepawake_start_twice_keeps_one_inhibitor(self):
        self.stub("systemd-inhibit", INHIBITOR_STUB)
        self.keepawake("start", str(self.run_dir))
        time.sleep(0.3)
        first = self.pid()
        self.assertEqual(self.keepawake("start", str(self.run_dir)).returncode, 0)
        self.assertEqual(self.pid(), first)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_keepawake_warns_when_the_inhibitor_exits_at_once(self):
        self.stub("systemd-inhibit", "#!/bin/sh\nexit 1\n")
        result = self.keepawake("start", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mise en veille", result.stderr)
        self.assertFalse(self.pid_file.exists())

    def test_keepawake_without_setsid_still_kills_the_group(self):
        (self.bin / "setsid").unlink()
        self.stub("systemd-inhibit", INHIBITOR_STUB)
        result = self.keepawake("start", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        time.sleep(0.3)
        pid = self.pid()
        members = self.children_of_group(pid)
        self.assertGreaterEqual(len(members), 2, members)
        self.assertEqual(self.keepawake("stop", str(self.run_dir)).returncode, 0)
        for member in members:
            self.assertTrue(wait_gone(member), member)
        self.assertFalse(self.pid_file.exists())

    def test_keepawake_usage(self):
        self.assertEqual(self.keepawake().returncode, 2)
        self.assertEqual(self.keepawake("nope", str(self.run_dir)).returncode, 2)
        self.assertEqual(self.keepawake("start").returncode, 2)


if __name__ == "__main__":
    unittest.main()
