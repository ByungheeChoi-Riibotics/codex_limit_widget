# Codex Limit Widget

> [!IMPORTANT]
> This README is the execution and usage entrypoint for this repository/module.

Ubuntu 24.04 GNOME 상단 패널에 현재 사용자의 Codex **남은 한도**와 다음 리셋 시각을 표시합니다. 로그인할 때 시작하고 60초마다 갱신합니다. 표시 언어는 한국어이며, 리셋·갱신 시각은 시스템 시간대와 관계없이 **KST(Asia/Seoul)**입니다.

## 코딩 에이전트 설치 절차

이 저장소 링크를 전달받은 에이전트는 대상 사용자의 Ubuntu GNOME 세션에서 다음 순서로 진행합니다. 아래 명령은 모두 대상 PC의 사용자 계정에서 실행합니다. 컨테이너, 원격 셸 또는 `sudo` 셸의 환경을 그래픽 로그인 세션으로 가정하지 마세요.

1. Ubuntu 24.04 GNOME인지 확인하고, 로그인한 사용자의 그래픽 세션과 세션 D-Bus에 접근할 수 있는지 확인합니다. `DISPLAY` 또는 `WAYLAND_DISPLAY`와 `DBUS_SESSION_BUS_ADDRESS`가 필요합니다. 접근할 수 없으면 사용자에게 해당 데스크톱 세션에서 에이전트를 실행해 달라고 요청합니다.
2. 아래 **준비**의 의존성과 AppIndicators 확장을 확인합니다. 필요한 패키지가 없으면 승인된 `sudo` 사용이 가능할 때만 설치합니다. 비밀번호 입력이나 확장 활성화·재로그인이 필요하고 직접 처리할 수 없다면 사용자에게 요청하고 결과를 확인합니다.
3. `codex` 명령이 없으면 아래 공식 설치 안내와 설치 권한을 확인하고 가능한 설치는 직접 수행합니다. `codex login status`로 대상 사용자의 로그인 상태를 확인합니다. 사용자 인증이나 직접 처리할 수 없는 권한이 필요하면 해당 단계만 사용자에게 요청합니다. 인증 파일, 토큰, 계정의 원문 응답은 출력하거나 공유하지 않습니다.
4. 아래 **설치·실행** 순서로 저장소를 받아 설치합니다. 대상 디렉터리에 이미 저장소가 있으면 원격 주소와 `git status`를 확인하고 사용자 변경을 보존합니다. 설치 전에 아래 **업데이트·기존 설치 전환**의 충돌 규칙과 실행 중인 위젯을 확인합니다. 기존 파일의 출처가 불명확하면 임의로 옮기거나 덮어쓰지 말고 사용자에게 확인합니다.
5. 설치 메시지만으로 완료를 판단하지 않습니다. 실제 상단 패널에 위젯이 표시되고 클릭 메뉴에서 조회 결과를 볼 수 있는지 확인합니다. 자동시작 파일의 `Exec`가 설치된 `launch.py`를 가리키고 `X-GNOME-Autostart-enabled=true`인지 확인합니다. 실제 재로그인을 하지 않았다면 자동시작 설정 확인과 재로그인 후 실행 확인을 구분해 보고합니다. GUI를 볼 수 없으면 사용자에게 확인을 요청하고 미검증 상태를 보고합니다.
6. 실패하면 아래 **표시·문제 해결**을 따르고 해결되지 않은 단계와 오류를 보고합니다. 이미 실행 중인 위젯은 업데이트 시 메뉴에서 종료하기 전까지 계속 실행되므로 새 버전 확인을 위해 종료 후 다시 시작합니다.

## 준비

Ubuntu 24.04 GNOME 데스크톱에서 확인했습니다. 다른 데스크톱·운영체제는 검증하지 않았습니다. GNOME 그래픽 세션의 터미널에서 아래 명령을 실행하세요. `sudo`는 패키지 설치에만 사용합니다.

```sh
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator git
```

GNOME 확장 앱에서 Ubuntu AppIndicators 확장을 활성화하세요. 패키지를 처음 설치했다면 로그아웃 후 다시 로그인해야 할 수 있습니다.

[공식 Codex CLI 안내](https://learn.chatgpt.com/docs/codex/cli)에 따라 각자의 CLI를 설치하고 로그인하세요. 위젯은 한도 정보가 제공되는 ChatGPT 계정을 사용합니다. API 키 계정에는 같은 한도 정보가 없을 수 있습니다.

```sh
codex login
codex login status
```

## 설치·실행

로컬 GNOME 터미널에서 실행합니다. 저장소 루트가 작업 디렉터리입니다.

```sh
git clone https://github.com/ByungheeChoi-Riibotics/codex_limit_widget.git
cd codex_limit_widget
/usr/bin/python3 install.py
```

설치 프로그램은 다음 파일을 만들고 앱 실행을 요청합니다. 이후 로그인할 때도 자동으로 시작합니다. 기존 앱이 실행 중이면 중복 실행을 막는 잠금이 적용됩니다.

- `~/.local/share/codex-usage-indicator/indicator.py`: 위젯 소스
- `~/.local/share/codex-usage-indicator/launch.py`: 사용자별 실행기
- `~/.config/autostart/codex-usage-indicator.desktop`: GNOME 자동시작 항목

설치한 터미널의 `codex` 절대경로와 발견된 `node` 디렉터리를 실행기에 기억합니다. npm/nvm 경로가 그래픽 로그인 `PATH`에 없어도 사용합니다. 네이티브 Codex 설치에는 Node가 필요하지 않습니다. `CODEX_HOME`이 지정되어 있으면 그 디렉터리 경로도 기억합니다. CLI·Node 위치나 `CODEX_HOME`을 바꾸면 해당 환경의 터미널에서 다시 설치하세요.

시작 명령은 다음과 같습니다. 메뉴의 **종료**는 현재 실행만 종료하며 다음 로그인 때 다시 시작합니다.

```sh
/usr/bin/python3 "$HOME/.local/share/codex-usage-indicator/launch.py"
```

## 표시·문제 해결

예: `Codex 5h 남음 37% · 주간 남음 67% · ↻10/30 10:16`. CLI가 반환한 기간만 표시하며, 패널에는 가장 가까운 미래 리셋 시각을 표시합니다. 메뉴에는 사용 비율, 각 리셋 시각, 마지막 성공 갱신과 **새로고침**, **종료**가 있습니다.

메뉴에는 일반 크레딧 잔액과 사용 가능한 리셋권의 총수·개별 기한도 표시합니다. 잔액은 서버가 제공한 문자열 그대로 또는 `무제한`, `미제공`으로 표시합니다. 서버가 리셋권 상세를 일부만 제공하거나 제공하지 않으면 모든 기한을 확인할 수 없습니다.

위젯이 실행 중인 매일 KST 낮 12시에 새로 조회한 결과에 72시간 이내 만료되는 사용 가능한 리셋권이 있으면 GNOME 데스크톱 알림을 보냅니다. 조회나 알림 전송에 실패하면 12:00부터 12:01 전까지 5초 간격으로 재시도하며, 12:01 이후 완료된 조회 결과는 알림에 사용하지 않습니다. 로그아웃·종료·절전으로 정오를 놓치거나, 이 시간 동안 절전·시계 변경이 발생하거나, 기한 정보가 제공되지 않으면 알림을 보내지 못합니다. 놓친 알림은 이후에 다시 보내지 않습니다.

- 조회 실패 시 마지막 성공 값을 유지하고 `!`를 붙입니다. 재조회 성공 시 정상 표시로 돌아옵니다.
- `Codex 로그인 필요`가 나오면 터미널에서 `codex login` 후 새로고침하세요.
- `Codex 한도 정보 없음`이면 계정이나 현재 CLI 응답에 표시할 한도가 없습니다.
- 패널이 보이지 않으면 AppIndicators 확장 활성화와 재로그인을 확인하세요. 위 시작 명령을 터미널에서 실행하면 오류를 볼 수 있습니다.

## 업데이트·기존 설치 전환

이 설치 프로그램이 관리 표시를 넣은 파일은 재설치로 갱신합니다. 그 외 파일과 심볼릭 링크가 설치 파일 이름과 충돌하면 수정 전에 중단합니다. 같은 디렉터리의 다른 이름 파일은 보존합니다.

```sh
git pull --ff-only
/usr/bin/python3 install.py
```

실행 중인 앱을 강제로 종료하지 않습니다. 업데이트 후 메뉴에서 **종료**한 뒤 위 시작 명령을 실행하거나 다시 로그인하세요.

수동 설치한 이전 위젯 등 관리 표시가 없는 파일은 충돌 경로를 확인하고 직접 백업한 뒤 설치하세요. 예를 들어 이 위젯의 기존 파일임을 확인했다면 먼저 메뉴에서 종료하고 아래처럼 해당 파일만 옮깁니다. 존재하는 파일만 실행하며, 같은 이름의 이전 백업을 덮어쓰지 않도록 `-i`를 사용합니다.

```sh
mkdir -p "$HOME/codex-widget-backup"
mv -i "$HOME/.local/share/codex-usage-indicator/indicator.py" "$HOME/codex-widget-backup/"
mv -i "$HOME/.local/share/codex-usage-indicator/launch.py" "$HOME/codex-widget-backup/"
mv -i "$HOME/.config/autostart/codex-usage-indicator.desktop" "$HOME/codex-widget-backup/"
/usr/bin/python3 install.py
```

## 제거

메뉴에서 종료한 뒤 로컬 터미널에서 실행합니다. 다른 파일이 남아 있으면 마지막 `rmdir`은 해당 디렉터리를 보존하고 실패합니다. Codex CLI와 계정은 유지됩니다.

```sh
rm -f "$HOME/.config/autostart/codex-usage-indicator.desktop" "$HOME/.local/share/codex-usage-indicator/indicator.py" "$HOME/.local/share/codex-usage-indicator/launch.py"
rmdir "$HOME/.local/share/codex-usage-indicator"
```

## 테스트

준비가 끝난 GNOME 그래픽 세션에서 저장소 루트 기준으로 실행합니다. 실제 계정이나 네트워크 조회 없이 가짜 CLI 프로세스와 임시 사용자 홈을 사용합니다. 설치·특수문자 경로의 자동시작·제한된 `PATH`·업데이트·충돌 보존과 한도 변환·GUI 복구·프로토콜 오류·20초 타임아웃을 확인합니다.

```sh
sudo apt install desktop-file-utils
/usr/bin/python3 -m unittest test_install test_indicator -v
```

## 데이터·호환성

위젯은 사용자의 기존 Codex CLI `app-server`를 실행해 [공식 한도 조회 프로토콜](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt)의 `account/rateLimits/read` 결과를 표시합니다. `usedPercent`는 사용 비율이므로 남음은 `100 - usedPercent`이며 최솟값은 0%입니다. 인증 파일·토큰·원문 응답을 수집하거나 별도 저장하지 않습니다. 설치 파일에는 실행 경로만 저장하며, 위젯이 실행 잠금 파일을 사용자 런타임 디렉터리에 만듭니다. 알림의 일별 중복을 막기 위해 마지막 발송일을 `XDG_STATE_HOME/codex-usage-indicator/last-reset-notification`에 저장합니다. `XDG_STATE_HOME`이 설정되지 않으면 `~/.local/state`를 사용합니다.

Codex CLI의 app-server 프로토콜은 버전에 따라 바뀔 수 있습니다. 한도 조회가 계속 실패하면 CLI 버전과 위 공식 문서를 확인하세요.
