# TUI design

Status: proposal for review. Author: maintainer (design), implementation follows in later rounds.

## What is wrong today

Observed on a maintainer's Mac with real apps (October 2026):

- The five tabs follow system layers (power, apps, services, network). A user thinks in tasks:
  "I am going to a meeting", "I am back".
- Results use internal words ("命中 / 未命中", "battery profile", "snapshot", "AC 侧") and show
  PIDs, assertion IDs, executable paths, raw `ps` and `pmset` output.
- A row can show contradictory states at once ("✓ quit" next to a red "force quit" button and a
  floating "retry").
- Every row has a different shape: some have buttons on the far right, some none, some indent.
- Noise outweighs signal: 12 network apps that are not installed, 11 configured selectors that
  match nothing, 4 unreadable plist files in a log panel that is always open.
- No-op changes are listed (`lowpowermode: 1 → 1`); real changes have no explanation
  ("AC 侧也关 powernap+womp").

## Principles

1. One screen answers one question: "what will happen", "what happened", "how is my battery".
2. Plain language. Every line is a sentence a non-developer understands. Technical evidence lives
   in `check --verbose`, `--json` and the log, never in the default view.
3. One row shape everywhere: status mark, name, action, one-line reason. One line per row.
4. Show only what matters now. Things that are fine collapse into one summary line.
5. A row has exactly one state. Controls offered must match that state.
6. One primary button per screen.

## Structure

Two screens plus a details drawer, instead of five tabs.

```
Start ─┬─ no active session ──► Plan screen ──apply──► Result screen (same list, results)
       └─ active session ─────► Session screen ──restore──► Report
```

The scenario picker stays as the first step when no scenario is given.

### Scenarios

Real use mixes the two old scenarios: on the move the lid is opened and closed many times. Sleep
settings only act while the Mac sleeps, so they can be applied together with the open-lid
settings without affecting work. The tool cannot see the lid close without a resident process,
which it does not run; instead it prepares the Mac so that closing the lid always means a real,
cheap sleep.

- **外出（on the go, default）** — replaces `work` and most of `away`. Open lid: apps frozen,
  sync/backup/updaters paused, Low Power Mode. Closed lid: Power Nap off, wake for network
  access off, and a sleep-readiness check (below). Network keep-alive during sleep stays on
  (see "Network keep-alive").
- **过夜 / 飞行（overnight / flight）** — for long periods without use: quits apps instead of
  freezing them and offers deeper sleep (`hibernatemode 25`). Turning off Wi-Fi and Bluetooth is
  offered but not selected, with the warning "关闭后「查找」可能无法定位这台 Mac".

The CLI and configuration keep the identifiers `work` and `away`; only the displayed names and
the contents of each plan change.

```
 选择场景                                                             1 / 2 选择，回车确认

 ┌ 1  外出 ──────────────────────────────────────────────────────────────────────────┐
 │    随时开盖工作、随时合盖带走。冻结不用的 app，暂停同步和更新，确保合盖后真正睡眠。   │
 └──────────────────────────────────────────────────────────────────────────────────┘
 ┌ 2  过夜 / 飞行 ───────────────────────────────────────────────────────────────────┐
 │    长时间不用。退出 app 而不是冻结，可选更深的休眠；Wi-Fi 和蓝牙默认保持。            │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

Cards are text, not full-width solid buttons; the focused card has the accent border.

### Sleep readiness

Shown first on the plan and session screens, and drives the menu bar icon:

```
 ✓ 合盖后会正常睡眠
 ✗ 合盖后不会睡眠：系统睡眠已被禁用（SleepDisabled）。                         [恢复睡眠]
 ✗ 合盖后不会睡眠：<app> 正在阻止系统睡眠。                                       [退出 <app>]
 ▲ <app> 阻止空闲睡眠：开盖不操作时不会自动睡眠；合盖仍会睡眠。
```

Some remote-access apps switch the global setting themselves; for example a "prevent sleep
while charging" option can run `pmset disablesleep 1` on AC power and `0` on battery through a
root helper. When `SleepDisabled` is 1 and such an app is running, the line names it as the
likely source ("可能来自 UURemote 的防睡设置；恢复睡眠后，该应用可能再次改回") and warns that
restoring sleep may be undone by that app. It does not promise that the app will restore the
setting on battery; that depends on the app's own options. The tool states this as likely, not certain.

Only `SleepDisabled` and system-sleep assertions (PreventSystemSleep and similar) are red;
idle-sleep assertions are amber and say that closing the lid still sleeps. The menu bar shows the
warning icon while any red item is present; it reflects the most recent check, whose time is
shown in the menu.

### Network keep-alive

`tcpkeepalive` stays on in every scenario because Find My and push notifications depend on it.
The plan shows it as a fixed, explained line, not a checkbox:

```
     保持睡眠中的网络连接（不可关闭）：「查找」需要它来定位这台 Mac。代价是睡眠时会短暂醒来。
```

### Plan screen

```
 断电工作 · 电池 62% · 本次未开始                                        q 退出  l 语言  ? 帮助

 需要注意
 ▲ UURemote 一直阻止空闲睡眠。不合盖时 Mac 不会自动睡眠。                       [退出 UURemote]
 ▲ 屏幕亮度 87%，高于建议的 70%。                                                [调到 70%]
   其它 6 项正常                                                                    展开 ▸

 将要执行  14 项                                                     全部 · 电源 · App · 服务 · 网络
 电源
 [x] 开启低电量模式            降低能耗，效果因机型而异
 [x] 接电时关闭 Power Nap       Mac 睡眠时不会定期醒来同步邮件和 iCloud
     已符合：网络唤醒已关闭、休眠模式 3
 App
 [x] 微信          冻结 ▾      保留运行状态，恢复后继续使用
 [x] Google Drive  退出 ▾      冻结后访问云盘文件可能卡住
 [ ] Google Chrome 询问 ▾      你可能正在使用
 后台服务
 [x] Google 更新程序  暂停      更新程序，恢复时重新加载
 网络
     保持连接（断电工作不改网络）

                                                     [ 开始省电（14 项） ]
```

- The filter row ("全部 · 电源 · App · 服务 · 网络") replaces the five tabs; it only filters the
  same list.
- An action selector (`冻结 ▾`) cycles freeze / quit / ask / skip with space; the change offers
  "remember for next time" after a successful run, as today.
- Items that are already in the target state are folded into one "已符合" line per group.
- Not-installed network apps, selectors that match nothing and unreadable plists are not shown;
  a single dim line at the end says "3 个配置项当前没有匹配的程序 · 1 个后台服务文件无法读取"
  and opens the details drawer. These are never shown as errors or in red.
- Empty placeholder plists (`<dict/>`, commonly created to stop an updater from reinstalling
  itself) are recognised and not counted at all.

### Result screen

The same list; the checkbox column becomes the result.

```
 ✓ 开启低电量模式
 ✓ 微信          已冻结
 ▲ Google Drive  拒绝正常退出（可能在显示确认框），已强制退出
 ✗ Lark          拒绝退出，可能正在显示确认框                      [重试]  [强制退出]
 ─────────────────────────────────────────────────────────────────────────────────────
 成功 12 项，失败 1 项。详情见 ~/.local/state/travel-mode/travel-mode.log      [完成]
```

Row states and the only controls allowed in each:

| State | Mark | Controls |
| --- | --- | --- |
| planned, selected | `[x]` | action selector |
| planned, not selected | `[ ]` | action selector |
| running | spinner | none |
| done | `✓` green | none |
| done with warning | `▲` amber | none |
| failed, app still running | `✗` red | retry, force quit (apps only) |
| failed, nothing to retry | `✗` red | none |

### Session screen

Shown when a session is active (also after reopening the TUI).

```
 合盖外出中 · 已 2 小时 10 分                                            q 退出  l 语言  ? 帮助

 电池   62% → 48%（消耗 14%）· 平均 9.8 W · 按此约可用 3 小时 40 分
 改动   电源设置 3 项 · App 5 个 · 后台服务 5 个
 注意   ▲ 1 个 app 在暂停后又运行了：Google Drive                         [再次处理]

                                                     [ 恢复全部（off） ]
```

If the user starts a new scenario while a session is active, the screen asks first: restore and
start new (default), continue this session, or cancel.

### Details drawer

`d` toggles a drawer at the bottom with the log and technical evidence for the focused row
(PID, command, exit code, assertion name). Closed by default. The always-open log panel is
removed; the last result line appears in the footer instead.

## Visual rules

- Palette: green = done/ok, amber = needs attention, red = failed, accent blue = the one primary
  button and the focus border. Everything secondary is the default or dim foreground.
- No solid full-width colored blocks except the primary button.
- Columns: mark 3 cells, name 16–24 cells (truncate with …), action 8 cells, reason fills the rest
  and is truncated before it wraps. At 80×24 the reason moves under the name in dim text.
- Headings are plain text in bold; no box around every section.
- Footer keys: `q 退出  l 语言  r 刷新  d 详情  ? 帮助`.

## Acceptance

Every screen is captured at 80×24, 120×36 and 160×45 in both languages, with demo data that has
at least 25 apps and with a live read-only run. The maintainer reviews each image against this
document before the round is accepted.
