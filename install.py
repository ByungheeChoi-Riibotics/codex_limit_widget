#!/usr/bin/python3
"""현재 사용자의 Codex 경로를 보존하고 GNOME 자동시작을 설치한다."""
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys


def main():
    codex = shutil.which("codex")
    if not codex:
        sys.exit("설치 실패: Codex CLI를 설치하고 로그인한 터미널에서 다시 실행하세요 (codex 명령 없음).")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        sys.exit("설치 실패: GNOME 그래픽 로그인 세션의 터미널에서 실행하세요.")
    check = subprocess.run(["/usr/bin/python3", "-c", '''import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, AyatanaAppIndicator3
assert Gtk.init_check()[0], "GTK display unavailable"
'''], capture_output=True)
    if check.returncode:
        sys.exit("설치 실패: GTK/AppIndicator 의존성 또는 그래픽 연결을 확인하세요. README의 준비 명령을 참고하세요.")

    home = Path.home()
    target = home / ".local/share/codex-usage-indicator"
    autostart = home / ".config/autostart"
    node = shutil.which("node")
    node_dir = str(Path(node).absolute().parent) if node else ""
    codex_home = os.environ.get("CODEX_HOME")
    home_setting = f'os.environ["CODEX_HOME"] = {os.path.abspath(codex_home)!r}\n' if codex_home else ""
    launcher = '''#!/usr/bin/python3
# Managed by codex_limit_widget installer
import os
from pathlib import Path
os.environ["CODEX_USAGE_CODEX"] = {codex!r}
os.environ["PATH"] = {path_prefix!r} + os.environ.get("PATH", "/usr/bin:/bin")
{home_setting}os.execv("/usr/bin/python3", ["/usr/bin/python3", str(Path(__file__).with_name("indicator.py"))])
'''.format(codex=os.path.abspath(codex), path_prefix=node_dir + os.pathsep if node_dir else "",
           home_setting=home_setting)
    source = Path(__file__).with_name("indicator.py").read_bytes()
    desktop = autostart / "codex-usage-indicator.desktop"
    marker = "# Managed by codex_limit_widget installer"
    for path in (target / "indicator.py", target / "launch.py", desktop):
        if path.is_symlink() or (path.exists() and
                                (not path.is_file() or marker.encode() not in path.read_bytes().splitlines()[:2])):
            sys.exit(f"설치 실패: 기존 파일 충돌: {path}\nREADME의 기존 설치 전환 절차를 확인하세요.")
    target.mkdir(parents=True, exist_ok=True)
    autostart.mkdir(parents=True, exist_ok=True)
    (target / "indicator.py").write_bytes(source)
    (target / "indicator.py").chmod(0o644)
    launch_path = target / "launch.py"
    launch_path.write_text(launcher)
    launch_path.chmod(0o700)
    # Exec 인용 뒤 Desktop Entry 문자열의 백슬래시·필드 코드를 이스케이프한다.
    quoted = '"' + ''.join("\\" + char if char in '\\"$`' else char for char in str(launch_path)) + '"'
    quoted = quoted.replace("\\", "\\\\").replace("%", "%%")
    desktop.write_text(marker + "\n[Desktop Entry]\nType=Application\nName=Codex 사용량\n"
                       f"Exec=/usr/bin/python3 {quoted}\nTerminal=false\n"
                       "X-GNOME-Autostart-enabled=true\n")
    desktop.chmod(0o644)
    subprocess.Popen([str(launch_path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    print("설치 및 로그인 자동시작 설정 완료. 앱 실행을 요청했습니다.")
    print("기존 앱이 실행 중이면 메뉴에서 종료한 뒤 다음 명령으로 새 버전을 시작하세요:")
    print(f"/usr/bin/python3 {shlex.quote(str(launch_path))}")


if __name__ == "__main__":
    main()
