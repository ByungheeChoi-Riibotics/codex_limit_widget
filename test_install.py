#!/usr/bin/python3
"""임시 사용자 홈에서 실제 설치·자동시작·CLI 프로세스 경계를 확인한다."""
import configparser
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-install-test-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name, 'home space "quote\' $dollar `tick` %user')
        self.home.mkdir()
        self.bin = self.home / "nvm bin"
        self.bin.mkdir()
        self.runtime = self.home / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.fifo = self.home / "started"
        os.mkfifo(self.fifo)
        self.fd = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, self.fd)
        self.env = dict(os.environ, HOME=str(self.home), XDG_RUNTIME_DIR=str(self.runtime),
                        PATH=f"{self.bin}:/usr/bin:/bin", TEST_STARTED=str(self.fifo))
        (self.bin / "codex").write_text("#!/usr/bin/env node\n")
        (self.bin / "codex").chmod(0o700)
        (self.bin / "node").write_text('''#!/usr/bin/python3
import json, os, sys
assert sys.argv[2:] == ["app-server"]
with open(os.environ["TEST_STARTED"], "w") as output:
    print(json.dumps({"codex": sys.argv[1], "node": sys.argv[0], "pid": os.getppid(), "path": os.environ["PATH"]}), file=output, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if "id" in request:
        print(json.dumps({"id": request["id"], "result": {}}), flush=True)
''')
        (self.bin / "node").chmod(0o700)
        self.pids = set()
        self.children = []
        self.addCleanup(self.stop_apps)

    def stop_apps(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for child in self.children:
            child.wait(timeout=5)

    def install(self, **changes):
        return subprocess.run(["/usr/bin/python3", str(ROOT / "install.py")],
                              env=dict(self.env, **changes), text=True, capture_output=True)

    def started(self):
        self.assertTrue(select.select([self.fd], [], [], 8)[0], "설치된 앱이 CLI를 실행하지 않았습니다")
        result = json.loads(os.read(self.fd, 4096))
        self.pids.add(result["pid"])
        return result

    def test_install_starts_and_autostart_remembers_cli_and_node_with_restricted_path(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.started()
        self.assertEqual(first["codex"], str(self.bin / "codex"))
        self.assertEqual(first["node"], str(self.bin / "node"))
        desktop = self.home / ".config/autostart/codex-usage-indicator.desktop"
        validate = subprocess.run(["desktop-file-validate", str(desktop)], capture_output=True, text=True)
        self.assertEqual(validate.returncode, 0, validate.stderr)
        # gio는 실제 Desktop Entry Exec 인용 및 %% 치환을 적용한다.
        # 별도 런타임에서 다음 그래픽 로그인에 해당하는 자동시작을 재현한다.
        second_runtime = self.home / "second runtime"
        second_runtime.mkdir(mode=0o700)
        subprocess.run(["gio", "launch", str(desktop)],
                       env=dict(self.env, PATH="/usr/bin:/bin", XDG_RUNTIME_DIR=str(second_runtime)),
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        second = self.started()
        self.assertEqual((second["codex"], second["node"]), (first["codex"], first["node"]))
        self.assertNotEqual(first["pid"], second["pid"])
        installed = self.home / ".local/share/codex-usage-indicator"
        self.assertTrue((installed / "indicator.py").is_file())
        launcher = installed / "launch.py"
        self.assertTrue(os.access(launcher, os.X_OK))
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(desktop)
        self.assertEqual(parser["Desktop Entry"]["X-GNOME-Autostart-enabled"], "true")

    def test_existing_unrelated_file_or_symlink_is_preserved_and_install_fails(self):
        target = self.home / ".local/share/codex-usage-indicator"
        target.mkdir(parents=True)
        unrelated = self.home / "unrelated"
        unrelated.write_text("keep me\n")
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                collision = target / "launch.py"
                if symlink:
                    collision.symlink_to(unrelated)
                else:
                    collision.write_text("keep me\n")
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("충돌", result.stderr)
                self.assertEqual(collision.read_text(), "keep me\n")
                self.assertEqual(unrelated.read_text(), "keep me\n")
                self.assertFalse((target / "indicator.py").exists())
                collision.unlink()

    def test_native_cli_without_node_keeps_safe_path_and_custom_codex_home(self):
        (self.bin / "node").unlink()
        (self.bin / "codex").write_text('''#!/usr/bin/python3
import json, os, sys
with open(os.environ["TEST_STARTED"], "w") as output:
    print(json.dumps({"pid": os.getppid(), "path": os.environ["PATH"], "home": os.environ.get("CODEX_HOME")}), file=output, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if "id" in request:
        print(json.dumps({"id": request["id"], "result": {}}), flush=True)
''')
        custom_home = str(self.home / "custom codex")
        result = self.install(PATH=str(self.bin), CODEX_HOME=custom_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.started()
        self.assertNotIn("", first["path"].split(os.pathsep))
        launcher = self.home / ".local/share/codex-usage-indicator/launch.py"
        another_runtime = self.home / "native runtime"
        another_runtime.mkdir(mode=0o700)
        env = dict(self.env, PATH="/usr/bin:/bin", XDG_RUNTIME_DIR=str(another_runtime))
        env.pop("CODEX_HOME", None)
        self.children.append(subprocess.Popen([str(launcher)], env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.assertEqual(self.started()["home"], custom_home)

    def test_reinstall_updates_owned_files_and_preserves_other_files(self):
        self.assertEqual(self.install().returncode, 0)
        self.started()
        target = self.home / ".local/share/codex-usage-indicator"
        other = target / "user-notes.txt"
        other.write_text("keep my notes")
        (target / "indicator.py").write_text("#!/usr/bin/python3\n# Managed by codex_limit_widget installer\n# old version\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / "indicator.py").read_bytes(), (ROOT / "indicator.py").read_bytes())
        self.assertEqual(other.read_text(), "keep my notes")

    def test_missing_cli_or_graphical_session_fails_before_installing_files(self):
        (self.bin / "codex").unlink()
        for changes, expected in (({"PATH": str(self.bin)}, "codex"),
                                  ({"DISPLAY": "", "WAYLAND_DISPLAY": ""}, "그래픽")):
            with self.subTest(changes=changes):
                if "PATH" not in changes:
                    (self.bin / "codex").write_text("#!/usr/bin/python3\n")
                    (self.bin / "codex").chmod(0o700)
                result = self.install(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse((self.home / ".local/share/codex-usage-indicator").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
