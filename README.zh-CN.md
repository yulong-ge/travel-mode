# travel-mode

**[English](README.md) | 简体中文**

`travel-mode` 调整 macOS 电源设置并暂停选定的后台程序，通过快照恢复本工具所做的修改。

## 适用场景

**断电工作（`work`）。** 适合会议、会场等没有充电器的地方。在保留屏幕与网络的同时，暂停已识别的同步、备份、更新程序及用户明确配置的重型应用。默认电源动作只开启电池低电量模式。显示器与系统睡眠计时、休眠模式、AC 设置、Wi-Fi、蓝牙和亮度保留现有配置。工具不会启动进程来阻止屏幕休眠。Spotlight 索引停止是可选项，需勾选或确认。

**合盖外出 / 过夜（`away`）。** 使用现有的睡眠省电建议，为合盖外出或过夜休眠做准备。应用、服务与网络动作需要在执行前检查。`flight` 和 `overnight` profile 属于这一场景；`flight` 还请求断网、hibernatemode 25、降低亮度和停止索引。

工具仅在命令或终端用户界面（TUI）打开期间工作，不安装常驻服务、守护进程或定时轮询任务。续航受硬件、电池状况和前台负载影响，尚未验证连续工作 7–8 小时的续航。

## 安装

选择一种方式安装：

```bash
uv tool install git+https://github.com/yulong-ge/travel-mode
```

```bash
pipx install git+https://github.com/yulong-ge/travel-mode
```

支持 Python 3.10–3.13 与 macOS。以上命令从仓库安装。蓝牙控制可选使用 `blueutil`；亮度控制使用可用的亮度适配器。界面会说明缺少的能力。开发环境见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 30 秒上手

```bash
travel-mode check
travel-mode on --scenario work --dry-run --yes
travel-mode
travel-mode off
```

直接运行后，首屏显示两个场景按钮。选择“断电工作”或“合盖外出 / 过夜”，检查动作，再执行选中的分组。`--scenario work|away` 与 `--profile NAME` 跳过这一首屏。`check` 只读检查机器；`off` 恢复已记录的修改。非交互终端中，直接运行会转为 `check`。

## 不喜欢 TUI 的用法

```bash
travel-mode --no-tui
travel-mode on --scenario work --yes
travel-mode config set-interface cli
travel-mode --tui --scenario away
```

当 `options.scenario` 为 `"ask"` 时，CLI 第一题选择场景。`--yes` 要求提供 `--scenario`、`--profile` 或预先配置场景，不会自动代选。work 下，`--yes` 跳过动作设为 `ask` 的应用和可选 Spotlight；明确配置的退出、冻结仍会执行。work 批量动作不会自动强退；退出失败会停止该批次。

界面优先级依次为 `--tui` / `--no-tui`、`TRAVEL_MODE_INTERFACE=cli|tui`、`options.interface`（默认 `"tui"`）。非交互终端回退到 `check`；显式 `--no-tui` 与 `--yes` 则请求执行流程。

## 修改范围与安全

work 默认开启电池低电量模式、选择配置的类别、卸载明确配置的用户 LaunchAgent，并执行明确配置的应用退出或冻结动作。前台应用默认选择和网络保持可用。profile 可配置 0 到 1 的目标亮度；普通 work 默认不改亮度。work 的电源建议忽略睡眠、显示器睡眠和休眠模式覆写。电源页和网络页仍提供需要单独选择的手动动作。

away 动作可修改电池设置、AC 的 `powernap` / `womp`、全局 `disablesleep`、应用、launchd 服务、索引、Wi-Fi / 蓝牙和亮度，也可终止选中的 `caffeinate` 断言持有进程或立即关闭屏幕。`--yes` 接受默认动作，包括 AC 修改和现有的强制退出回退；`--aggressive` 还允许 CLI 中可选的激进动作。profile 可以独立请求动作。修改配置后应检查 dry-run。退出前保存文档；强退可能丢失内容，冻结可能保留防睡断言，断网可能中断远程会话。

工具仅在动作需要时请求 sudo，例如电源写入、全局睡眠修复、系统服务、索引、Wi-Fi 或向 root 进程发信号。工具先用非交互 sudo 检查精确命令的权限。打开 TUI 不请求凭据。通过 AppleEvent 正常退出应用时，macOS 可能要求自动化权限。

`--dry-run` 记录修改命令，并把配置、迁移和恢复写入隔离到临时目录。它仍执行只读观测；交互终端中的特权观测可能请求 sudo 凭据。无法读取的观测会明确标为不确定，并返回非零状态。预览通过不表示之后的真实操作一定成功。

## 命令表

| 命令 | 用途 |
| --- | --- |
| `travel-mode` / `travel-mode on` | 选择场景后进入选定界面 |
| `travel-mode off` / `travel-mode restore` | 恢复尚未完成的恢复记录 |
| `travel-mode check` / `travel-mode check --json` | 只读检查；JSON 包含当前已保存的生效场景 |
| `travel-mode report` / `travel-mode report --json` | 只读睡眠 / DarkWake 报告 |
| `travel-mode config path` | 显示配置和状态路径 |
| `travel-mode config init` | 写入模板；`--force` 覆盖已有文件 |
| `travel-mode config validate` | 校验配置 |
| `travel-mode config edit` | 用 `$VISUAL` 或 `$EDITOR` 打开，然后校验 |
| `travel-mode config set-language en` / `zh` | 保存语言 |
| `travel-mode config set-interface cli` / `tui` | 保存默认界面 |
| `travel-mode thaw` / `travel-mode thaw --all` | 恢复记录的应用 / 检查全部停止的应用进程 |
| `travel-mode profiles` | 列出 profile |
| `travel-mode suggest --samples 3` | 只读能耗与类别建议 |
| `travel-mode suggest --json` | 结构化建议 |
| `travel-mode suggest --write` | 确认后添加应用、服务或守护程序配置 |

其他参数：`--scenario work|away`、`--profile NAME`、`--dry-run`、`--yes` / `-y`、`--aggressive`、`--lang en|zh`、`--help`、`--version`。`off --force` 即使恢复失败也会丢弃剩余记录。`thaw --all` 可能恢复本工具之外停止的应用。TUI 按 `L` 切换语言并保留 TOML 注释。隐藏的 `--demo` 用测试数据生成截图。

## 配置与类别

默认路径为 `~/.config/travel-mode/config.toml` 和 `~/.local/state/travel-mode/state.json`。通过 `TRAVEL_MODE_CONFIG` 和 `TRAVEL_MODE_STATE` 覆盖；同时支持 XDG 根目录。见[完整示例](examples/config.toml)。

```toml
[options]
scenario = "ask" # ask | work | away
interface = "tui" # tui | cli
language = "auto" # auto | en | zh

[categories]
pause = ["sync", "backup", "updater"]
ignore = [] # categories.toml 中的规则 id 或公开名称

[services]
unload = []
keep = []

[apps]
default = "ask"
quit = []
freeze = []
skip = []

[profiles.work]
# brightness = 0.5
```

随包分发的[类别识别库](src/travel_mode/categories.toml) 定义 `sync`、`backup`、`updater`、`vm`，逐条记录公开依据。匹配依据包括 launchd Label、bundle id、精确应用名和可执行路径。`suggest`、后台服务页和应用页按类别分组显示识别项。类别卸载只面向 `~/Library/LaunchAgents` 与 `/Library/LaunchAgents` 中已加载且有 plist 的 LaunchAgent，不会自动卸载系统 LaunchDaemon。识别到产品不表示可以暂停它的全部组件。

`categories.pause` 选择 work 的自动类别动作；`vm` 需主动加入。两个场景的类别按钮都提供“暂停同步与备份”和“暂停更新器”。work 总览默认勾选类别组，并遵循 `pause` 中各类别的选择。iCloud 与 Time Machine 仅展示。部分备份产品和嵌入式更新器规则只识别，请在产品内管理。类别退出使用正常退出，不强退。

类别动作补充 `services.unload` 和明确的 `apps.quit` / `apps.freeze`；同一批次内重复身份只处理一次。`services.keep` 优先于卸载选择器和类别动作。`apps.skip` 与 `apps.ignore` 保护应用及匹配的类别服务。包括 `ask` 在内的明确应用动作优先于类别默认动作。`categories.ignore` 禁止自动类别动作与类别按钮处理，但明确的应用、服务配置仍生效。只识别规则不会变成类别动作。现有 `apps.with_daemon` 仍是需要单独明确配置的机制。

内置 profile 为 `work`、`flight`、`overnight`。`cafe` 已合并到 `work` 并删除，不保留别名。profile 提供的字段覆盖普通配置；自定义 `[profiles.NAME]` 覆盖同名内置值。自定义 profile 默认属于 away，除非设置 `scenario = "work"`。显式 `--scenario` 与 profile 场景冲突会报错。快照记录最后应用的场景，同时保留首次原值用于恢复。没有已保存场景时，包括旧快照，`check --json` 输出 null。

`TRAVEL_MODE_LANG` 覆盖配置语言。`TRAVEL_MODE_APP_DIRS` 添加以冒号分隔的绝对应用目录，空项或相对路径会报错。`suggest --write` 请求确认、保留注释，并在替换配置前校验。JSON schema：[check](docs/json-schema/check.schema.json)、[suggest](docs/json-schema/suggest.schema.json)。可选集成：[Shortcuts](integrations/shortcuts.md)、[菜单插件](integrations/swiftbar/travel-mode.10s.sh)、[sudoers](docs/sudoers.md)。

## 恢复机制

可恢复动作在修改前保存恢复意图。电源记录仅包含实际修改的键，分别记录电池与 AC。`off` 恢复原值、继续运行记录的进程身份、重新加载卸载的服务、重新打开退出的应用，并恢复修改过的网络、亮度与索引设置。LaunchAgent 使用 unload/load，不使用 launchctl 的 disable 机制。

每成功恢复一项就保存进度。失败记录保留，供下次 `off` 重试。重新打开应用无法恢复未保存文档或精确窗口状态。已终止的 `caffeinate` 和立即关闭屏幕无法撤销。超过 24 小时的快照会提示，因为恢复可能覆盖后来对已记录键所做的修改。当前没有跨进程锁，一次只运行一个修改实例。

没有快照时，非交互 `off` 会失败，不重置设置。交互 `off` 提供需要单独确认的出厂电源重置。保留损坏快照，修复所示问题后重试。用 `travel-mode thaw` 恢复已记录的冻结应用；恢复其他停止的进程前先检查身份。无法恢复亮度时可使用系统设置。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 命令完成 |
| 1 | 操作失败、恢复尚未完成，或出现未预期异常 |
| 2 | 配置或参数错误；也用于不支持的平台或缺少平台能力 |
| 3 | 恢复状态非法、不可读或不可写 |

配置与状态错误只输出一行诊断，包含路径、相关键或原因及修复提示，不打印 Python traceback。设置 `TRAVEL_MODE_DEBUG=1` 后保留完整 traceback 和异常链。未预期异常保留正常 traceback。外部编辑器可以返回自己的退出码。

## 兼容性与验证边界

仓库记录了维护者在 2026-10-15 的早期真机检查：macOS 27.0.1、arm64。这是历史结果；本轮场景与类别改动没有重新执行系统修改。

| 早期使用临时夹具完成的真机验证 | 范围 |
| --- | --- |
| 应用与服务 | AppleEvent 正常退出、精确进程强退、主进程与辅助进程冻结 / 恢复、用户 LaunchAgent 卸载 / 重载、重开应用 |
| 电源与恢复 | 电池往返恢复、AC powernap / womp、全局 disablesleep、hibernatemode 25、原始电源输出恢复 |
| 恢复与亮度 | CLI thaw --all、亮度修改与恢复 |
| 界面 | CLI 问答和 TUI 键盘 / 鼠标执行与恢复 |

Wi-Fi、蓝牙、Spotlight、立即关闭屏幕、caffeinate 终止及 AC 睡眠计时尚未真机验证。work 与类别行为使用隔离 FakeRunner 和演示夹具测试，没有验证真实产品暂停或电池续航。CI 覆盖 Ubuntu 与 macOS 上的 Python 3.10–3.13；Linux 仅作为测试主机，不是支持的运行平台。其他硬件和 macOS 版本仍未验证。

## 贡献与许可

[CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。[MIT](LICENSE)。
