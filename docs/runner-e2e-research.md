# Hosted macOS e2e and integration constraints

Read-only source review, 2026-10-16. No hosted workflow, installation, permission
change, or real e2e experiment was run during this review.

## Hosted macOS capabilities

GitHub documents standard macOS runners as virtual machines and documents
passwordless `sudo` for Linux and macOS. The `-latest` label can move between
stable images. These facts do not establish that a VM has a battery, adjustable
display backlight, or permission to control every application.
[GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

The current runner-image table maps `macos-latest` to macOS 26 arm64. That image's
published inventory does not list `tmux`; this does not prove the executable is
absent, but it does not establish preinstallation. Ensure the UI harness's tmux
dependency in the workflow rather than assuming it. The upstream tmux project
documents Homebrew installation on macOS.
[Image inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-26-arm64-Readme.md),
[Current label table](https://github.com/actions/runner-images#available-images),
[tmux installation](https://github.com/tmux/tmux/wiki/Installing).

The image build configures guest sleep settings and enables automation mode.
The separate TCC configuration grants selected source/target pairs, including
`osascript` and System Events; it does not grant every dynamically created test
bundle. Apple documents app-control permission separately under Privacy &
Security > Automation. A fixture app can therefore be denied AppleEvents even
when another AppleScript succeeds. This is an inference from the explicit
permission list, not a claim that every hosted runner denies the fixture.
[Image configuration](https://github.com/actions/runner-images/blob/main/images/macos/scripts/build/configure-machine.sh),
[TCC configuration](https://github.com/actions/runner-images/blob/main/images/macos/scripts/build/configure-tccdb-macos.sh),
[Apple Automation permission](https://support.apple.com/guide/mac-help/allow-apps-to-control-other-apps-on-mac-mchl07817563/mac).

The reviewed build source shows `pmset` is used in the guest. It does not document
the runtime `pmset -g`/`SleepDisabled` output or guarantee every job has a usable
GUI launchd domain. No runtime claim about these capabilities was verified here.
Retain actual read results and report unexpected failures; the CI environment
alone is not a reason to skip them.

Runtime capability observations must determine individual SKIPs. Missing Battery
Power settings should skip battery-setting round trips. An unavailable native
brightness read should skip the brightness round trip. An explicit AppleEvent
authorization denial should skip that app-quit assertion. Other parse errors,
command failures, and safety-identity failures must remain failures. A hosted
runner's broad sudo policy must not widen the fixture guard's exact argv/PID/path
allowlist. Do not edit TCC databases or request additional privileges in CI.

The upload action accepts an explicit retention period; `retention-days: 14` is
within its documented range. Upload failure evidence with an `always()` step.
Hidden files are excluded by default; do not broadly enable them for evidence
directories that also contain isolated user homes or credentials.
[upload-artifact documentation](https://github.com/actions/upload-artifact#retention-period),
[hidden-file behavior](https://github.com/actions/upload-artifact#uploading-hidden-files).

## SwiftBar execution

SwiftBar names plugins `{name}.{time}.{ext}`; the `m` modifier means minutes.
Thus `travel-mode.5m.sh` requests five-minute refreshes. A standard plugin runs,
prints its menu, and exits. A streamable plugin is persistent and is unsuitable
here. Menu commands can launch in Terminal rather than execute a full audit on
every refresh.
[SwiftBar plugin naming and API](https://github.com/swiftbar/SwiftBar#plugin-naming),
[standard versus streamable plugins](https://github.com/swiftbar/SwiftBar#plugin-types).

## Sudoers generation

The installed macOS `man sudoers` and upstream manual agree: an absolute command
with no argument restriction permits arbitrary arguments to that executable.
Consequently `/usr/bin/pmset` grants all pmset operations, not only low-power mode.
Argument globs match the concatenated argument string, including whitespace;
`*` cannot establish a single-path restriction. Regex argument matching requires
sudo 1.9.10 or later. Do not silently assume that feature on every supported Mac.
[Upstream sudoers manual](https://www.sudo.ws/docs/man/1.9.14/sudoers.man.pdf).

The local manual also documents reserved `ALL` and uppercase alias tokens.
Validate usernames before formatting rules; reject whitespace and sudoers syntax
characters, and ensure a username cannot become an alias or `ALL` grant.
Restrict run-as to root. Broader rules must state their actual risk, especially
loading an arbitrary root LaunchDaemon from a user-selected plist.

Read-only local `man visudo` confirms `-c` is check-only and `-f` selects an
alternate file; success is exit 0 and syntax failure is exit 1. Checking a
standalone fragment does not prove the entire installed policy or effective
authorization. Check effective privileges separately using noninteractive
list-mode probes; never authenticate merely to render or inspect a fragment.
