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

Apple 命令行工具在执行时使用固定系统路径，包括 `/usr/bin/sudo` 及其系统工具目标。系统路径缺失时明确报错，不回退到 PATH。可选的 `blueutil` 与 `brightness` 按 PATH 解析；`check` 显示解析路径，`check --json` 包含 `tool_paths`。使用第三方控制功能前请检查这些路径。FakeRunner 记录与 dry-run 命令保留逻辑工具名。

work 默认开启电池低电量模式、选择配置的类别、卸载明确配置的用户 LaunchAgent，并执行明确配置的应用退出或冻结动作。前台应用默认选择和网络保持可用。profile 可配置 0 到 1 的目标亮度；普通 work 默认不改亮度。work 的电源建议忽略睡眠、显示器睡眠和休眠模式覆写。电源页和网络页仍提供需要单独选择的手动动作。

away 动作可修改电池设置、AC 的 `powernap` / `womp`、全局 `disablesleep`、应用、launchd 服务、索引、Wi-Fi / 蓝牙和亮度，也可终止选中的 `caffeinate` 断言持有进程或立即关闭屏幕。`--yes` 接受默认动作，包括 AC 修改和现有的强制退出回退；`--aggressive` 还允许 CLI 中可选的激进动作。profile 可以独立请求动作。修改配置后应检查 dry-run。退出前保存文档；强退可能丢失内容，冻结可能保留防睡断言，断网可能中断远程会话。

工具仅在动作需要时请求 sudo，例如电源写入、全局睡眠修复、系统服务、索引、Wi-Fi 或向 root 进程发信号。工具先用非交互 sudo 检查精确命令的权限。打开 TUI 不请求凭据。通过 AppleEvent 正常退出应用时，macOS 可能要求自动化权限。

`--dry-run` 记录修改命令，并把配置、迁移和恢复写入隔离到临时目录。它仍执行只读观测；交互终端中的特权观测可能请求 sudo 凭据。无法读取的观测会明确标为不确定，并返回非零状态。预览通过不表示之后的真实操作一定成功。

## 命令表

| 命令 | 用途 |
| --- | --- |
| `travel-mode` / `travel-mode on` | 选择场景后进入选定界面 |
| `travel-mode off` / `travel-mode restore` | 恢复尚未完成的恢复记录 |
| `travel-mode automation install` / `uninstall` / `status [--json]` | 管理可选电源来源事件 |
| `travel-mode power --seconds 20` / `--json` | 在有限窗口内测量电池功耗 |
| `travel-mode check --reapply` | 再次暂停检测到已复活的记录项 |
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
| `travel-mode history [--json] [--limit N]` | 读取本机会话历史 |

其他参数：`--scenario work|away`、`--profile NAME`、`--dry-run`、`--yes` / `-y`、`--aggressive`、`--lang en|zh`、`--help`、`--version`。`off --force` 即使恢复失败也会丢弃剩余记录。`thaw --all` 可能恢复本工具之外停止的应用。TUI 按 `L` 切换语言时，会把所选语言保存到配置文件的 `options.language`，并保留 TOML 注释。dry-run 仅写入隔离的配置副本。隐藏的 `--demo` 用测试数据生成截图。

## 配置与类别

默认路径为 `~/.config/travel-mode/config.toml` 和 `~/.local/state/travel-mode/state.json`。通过 `TRAVEL_MODE_CONFIG` 和 `TRAVEL_MODE_STATE` 覆盖；同时支持 XDG 根目录。见[完整示例](examples/config.toml)。

```toml
[options]
scenario = "ask" # ask | work | away
interface = "tui" # tui | cli
language = "auto" # auto | en | zh

[measurement]
seconds = 180
interval = 5
updates = 2
settle_seconds = 10

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

随包分发的[类别识别库](src/travel_mode/categories.toml) 定义 `sync`、`backup`、`updater`、`vm`、`keep-awake`，逐条记录公开依据。匹配依据包括 launchd Label、bundle id、精确应用名和可执行路径。`suggest`、后台服务页和应用页按类别分组显示识别项。类别卸载只面向 `~/Library/LaunchAgents` 与 `/Library/LaunchAgents` 中已加载且有 plist 的 LaunchAgent，不会自动卸载系统 LaunchDaemon。识别到产品不表示可以暂停它的全部组件。

`categories.pause` 选择 work 的自动类别动作；`vm` 需主动加入。两个场景的类别按钮都提供“暂停同步与备份”和“暂停更新器”。work 总览默认勾选类别组，并遵循 `pause` 中各类别的选择。iCloud 与 Time Machine 仅展示。部分备份产品和嵌入式更新器规则只识别，请在产品内管理。类别退出使用正常退出，不强退。

类别动作补充 `services.unload` 和明确的 `apps.quit` / `apps.freeze`；同一批次内重复身份只处理一次。`services.keep` 优先于卸载选择器和类别动作。`apps.skip` 与 `apps.ignore` 保护应用及匹配的类别服务。包括 `ask` 在内的明确应用动作优先于类别默认动作。`categories.ignore` 禁止自动类别动作与类别按钮处理，但明确的应用、服务配置仍生效。只识别规则不会变成类别动作。现有 `apps.with_daemon` 仍是需要单独明确配置的机制。

内置 profile 为 `work`、`flight`、`overnight`。`cafe` 已合并到 `work` 并删除，不保留别名。profile 提供的字段覆盖普通配置；自定义 `[profiles.NAME]` 覆盖同名内置值。自定义 profile 默认属于 away，除非设置 `scenario = "work"`。显式 `--scenario` 与 profile 场景冲突会报错。快照记录最后应用的场景，同时保留首次原值用于恢复。没有已保存场景时，包括旧快照，`check --json` 输出 null。

`TRAVEL_MODE_LANG` 覆盖配置语言。`TRAVEL_MODE_APP_DIRS` 添加以冒号分隔的绝对应用目录，空项或相对路径会报错。`suggest --write` 请求确认、保留注释，并在替换配置前校验。JSON schema：[check](docs/json-schema/check.schema.json)、[suggest](docs/json-schema/suggest.schema.json)。可选集成：[Shortcuts](integrations/shortcuts.md)、[菜单插件](integrations/swiftbar/travel-mode.10s.sh)、[sudoers](docs/sudoers.md)。

## 体检

`travel-mode check` 新增只读体检段；`travel-mode check --json` 在 `health` 中输出相同的结构化结果。总览在打开和显式刷新时各检查一次。可自动处理的命中项提供按钮，复用现有 Action/Executor 与恢复日志。不安装体检定时器、常驻进程或服务。现有总览的 30 秒定时器仍只读取电池。

九项检查覆盖全局 SleepDisabled、caffeinate 与已知防睡应用、非系统进程的系统防睡断言、在线外接显示器、Spotlight CPU 活动、正在运行的 Time Machine 备份、电池低电量模式、原生屏幕亮度与后台 CPU 活动。每项提供状态、证据、影响说明和处理方法。读取失败或格式异常时显示「无法检查」及命令和原因，其余项目继续检查。每项的命令截止时间为 2 秒，命令总预算为 15 秒。报告包含实际耗时；解析和渲染会在命令执行之外增加少量时间。共享读取导致原有审计无法完成时，JSON 提供 `audit_error` 和完整体检结果，不合成原有字段值。TUI 读取恢复快照失败时仍可查看诊断，但处理动作会禁用；修复读取错误后需重新打开工具。

共享进程快照只调用一次 `ps`，不调用 `top`，不等待采样。其 `%CPU` 估计值不代表瞬时功率。后台 CPU 项排除其他用户；`/System`、`/usr`、`/bin`、`/sbin`、`/Library/Apple` 下的可执行文件；本工具及祖先进程；停止的进程；Terminal、iTerm/iTerm2、Warp、Alacritty、kitty；以及前台应用和同一 bundle 内的辅助进程。无法识别前台进程时，此项显示错误。最多列出五个进程，并标注已知的 sync/backup/updater/vm 类别。应用复用退出或冻结动作；其他进程需在「活动监视器」查看。显式 app skip/ignore 与 category ignore 配置同样阻止体检处理按钮出现。仅识别的类别规则仍只提示。已配置 daemon 的应用使用现有 daemon 关闭动作。

Spotlight 仅开启索引时不算命中：`mds`、`mds_stores`、`mdworker`、`mdworker_shared` 的 CPU 读数总和须达到阈值。停止索引动作仅在用户选择时请求 sudo。防睡断言持有者使用退出动作，因为冻结不会释放断言。caffeinate 证据包含完整命令行与父进程；终止时还会按现有机制复核精确 PID 身份和防睡断言。防睡应用正在运行不代表防睡会话已开启。`keep-awake` 类别记录公开名称与来源，默认不暂停该类别。

外接显示器由短暂运行且有超时的 CoreGraphics 读取进程检测。显示器 ID 表示在线的非内置显示器，也可能是虚拟显示器，不代表已连接物理扩展坞。Time Machine 和显示器只提供手动步骤。原生亮度仅在电池供电时检查；原生后端不可读时显示错误。键盘背光与蓝牙唤醒权限未找到经过验证的可靠免 sudo 读取方式，因此不检查。

```toml
[health]
brightness_threshold = 0.7
brightness_target = 0.5
cpu_threshold = 20
spotlight_cpu_threshold = 20
```

亮度阈值与目标值为大于 0 且不超过 1 的比例；CPU 阈值为大于 0 且不超过 10000 的百分数。所选 profile 的亮度目标优先于 `health.brightness_target`。用户可自行选择是否使用处理按钮；检查本身不改设置。`off` 恢复已记录的改动，但按现有规则不重建已终止的 caffeinate 进程。

## 自动触发（可选）

自动触发默认关闭，只有显式运行 `travel-mode automation install` 才会安装。安装命令写入一个用户 LaunchAgent，并在当前 GUI 域加载。`travel-mode automation install --dry-run` 预览 plist 和命令，不安装。`travel-mode automation status`（或 `--json`）显示配置摘要、加载与运行状态和最近的本地触发记录。`travel-mode automation uninstall` 只卸载这个 agent 并删除它的 plist；本地触发记录保留。

agent 只订阅 Apple 的电源来源变化通知。事件到达时由 launchd 拉起，消费 XPC 事件、读取当前来源、处理一次后退出。没有常驻 travel-mode 进程、轮询服务、登录启动、定时唤醒或 KeepAlive。30 秒启动节流限制事件风暴，重复来源会被忽略。通知可能合并或丢失，因此工具核对当前状态，不保证记录每次快速切换。本轮不安装合盖、睡眠或唤醒触发：调研确认的公开 API 需要运行中的观察者。详见[调研与验证范围](docs/automation-research.md)。

```toml
[automation]
on_battery = "notify"  # notify | apply-work-safe | off
on_ac = "notify"       # notify | off-restore | off
retain = 50            # 本地触发记录上限，范围 1–10000
```

默认在拔电时仅提示进入 work 模式；接电且有活动快照时提示运行 `travel-mode off`。只有显式配置 `apply-work-safe` 或 `off-restore` 才会修改系统设置。`apply-work-safe` 复用现有 work Action，处理低电量模式和选中的用户 LaunchAgent。只有在 `[apps].quit` 或 `[apps].freeze` 中明确列出的应用才会退出或冻结；仅命中内置应用类别不会触发处理。带 daemon 的应用退出、索引、亮度、关屏和网络变更不在自动处理范围。低电量模式需要无需交互的 sudo 授权，否则跳过并在通知中说明。事件处理不请求密码、不启动凭据刷新线程、不测量功耗。`off-restore` 复用现有快照恢复流程并使用 `sudo -n`；恢复失败的项目保留在快照中，供手动运行 `travel-mode off`。

每次处理的外部命令总时限为 45 秒，此前原生事件等待上限为 10 秒。错误记录在状态文件旁的 `automation.json` 中，并以非零状态退出。通知失败在其它工作完成后记录，不合成结果。事件入口为 `travel-mode event power-changed`；安装的 agent 还使用一个内部 XPC 消费参数。事件和安装命令不会迁移或重写用户配置。自动触发操作错误的退出码为 5；通知投递失败返回 1。配置和状态错误仍分别返回 2/3。

2026-10-15 的一次只读**模拟**测得处理耗时 0.006204 秒、进程 CPU 时间 0.005247 秒、子进程 CPU 时间 0 秒。测试使用隔离路径，仅记录电源查询与通知两个逻辑命令，均未在本机执行。导入已完成；数据不包含 Python 启动、XPC 投递和真实通知开销。可用 `uv run python scripts/measure_event_overhead.py` 复现。开发期间未安装 agent；已安装 agent 的真实事件交付与空闲零进程状态仍需维护者按[带 guard 的验证步骤](CONTRIBUTING.md#event-automation-validation)验收。

## 功耗测量

`travel-mode power` 等待 2 次真实遥测更新，覆盖至少 1 个区间。它每 5 秒只读一次 `ioreg -rn AppleSmartBattery`，默认最多等待 180 秒。已录制的本机 macOS 每 20–60 秒更新一次这些注册表值；重复读取可能得到同一份缓存。因此每个窗口通常需要约 1–3 分钟。第一次读取只作为新鲜度参照；观察到第一次变化后才建立基线，避免区间包含执行 Action 前的负载。更新较慢时，超时前可能不足 2 次更新；此时明确返回“样本不足”，不给出平均瓦数或剩余时长。`travel-mode power --seconds 240 --updates 3 --json` 可延长等待上限并输出结构化结果，见 [power schema](docs/json-schema/power.schema.json)。此命令无需 sudo；接电、充电或没有电池时明确返回不可测量。

优先数据源是 `PowerTelemetryData.AccumulatedSystemLoad` 与 `SystemLoadAccumulatorCount`：区间平均 W = 累加 mW 差值 / 累加样本数差值 / 1000。整个窗口按累加样本数加权各区间。电池放电或功率累加器存在且增长时，另外报告电池平均功率。结果注明功率和容量来源、实际等待时长、观察到的覆盖时长与区间数。只有一个区间时注明“仅 1 个区间，精度较低”。覆盖时长是两次硬件变化被观察到的时间跨度；边界有最多一个轮询间隔的不确定性，不表示注册表连续更新。

仅当系统负载累加器整对缺失时，才在不同的 `UpdateTime` 之间用电压 × 有符号电流作区间积分，并明确标为 `voltage_current`。无符号 64 位负电流会先解码。录制中的电流可能被量化，其可靠性低于负载累加器。选中来源的字段不完整或无效时明确报错；缺少新鲜度标记、计数重置或回绕、窗口内数据源变化时不计算有效均值。工具不猜测计数器周期，也不混合不同来源。重复的计数或时间戳不计为新样本。

容量优先读取顶层 `AppleRawCurrentCapacity`/`AppleRawMaxCapacity`；两者均不存在时读取 `BatteryData.RemainingCapacity`/`FullChargeCapacity`。选定布局必须有效，不以标称容量、设计容量或百分比替代。剩余 Wh 使用剩余 mAh 和最近一次电压；预计小时数为剩余 Wh / 区间平均 W。`pmset -g batt` 另行显示 macOS 的估计。负载波动、遥测量化、缓存边界及未来的屏幕、网络和前台活动都会影响精度。短时前后对比不能证明因果关系，也不保证实际续航。

使用电池时，work 在动作前测量，动作后稳定 10 秒，再按相同策略测量。CLI 与 TUI 开始前会提示预计等待时长；两个窗口通常共需约 2–6 分钟，按默认超时和稳定期最多约 370 秒。`--no-measure` 跳过对比；`--measure` 在 away 中也开启。TUI 显示真实更新进度并保持响应。辅助读取失败或样本不足会保留在会话报告中，动作继续执行；第一个窗口无效时不再做第二个窗口对比。接电会中止当前窗口。dry-run 跳过对比。

配置项为 `[measurement] seconds = 180`（等待上限）、`interval = 5`、`updates = 2`、`settle_seconds = 10`。秒数必须有限且不超过 3600；超时大于 0，轮询间隔至少 5 秒，稳定期不小于 0，更新次数为 2–720 的整数。`power --seconds` 与 `--updates` 仅覆盖本次命令。总览仍只在 TUI 打开期间每 30 秒读取一次 ioreg，并将单次注册表读数标为缓存值，不当作区间均值。耗电排行仅由按钮触发。命令或 TUI 关闭后没有采样进程。

打开工具或运行 `check` 时还会检查已暂停项目是否复活。现有“再次处理”按钮或 `travel-mode check --reapply` 可再次暂停；这不是持续监控。

2026-10-15 的早期开销测试使用**原始探针基准**，不是现在的真实更新窗口：20.159 秒内调用 ioreg 10 次、pmset 1 次，工具 CPU 为 0.020392 秒，子进程 CPU 为 0.155477 秒，合计 0.175869 秒。TUI 空闲 300.001 秒的 CPU 为 0.931522 + 0.189740 = 1.121262 秒，调用 ioreg 10 次，没有 top。这些是历史 CPU 时间，不是耗电量，也不代表新 180 秒策略的开销。复现原始探针/TUI 计时可用 `uv run python scripts/measure_overhead.py probes --output /tmp/travel-mode-probes.json` 或 `uv run python scripts/measure_overhead.py tui --output /tmp/travel-mode-tui.json`；脚本隔离路径、使用只读 Runner，并通过 `resource.getrusage` 统计工具及子进程 CPU 时间。

## 会话报告与历史

应用动作前先用 `pmset -g ps` 判断电源来源。接电时跳过测量窗口。电源查询、电池读取或解析失败时显示原因，并继续省电动作。会话报告保留测量失败记录，不计算节省值。第一个窗口失败时不再尝试第二个窗口。取消测量和未预期的程序错误仍会中断操作。单独运行 `power` 时，读取失败仍作为错误报告。总览的电池读取失败会显示原因，不关闭 TUI。

`travel-mode off` 显示本次场景、UTC 开始与结束时间、时长、电量端点（百分比与原始 mAh）、可用的功耗估计、Action 尝试/成功/失败次数、恢复结果，以及先前实际检测到的复活项目。TUI 恢复后显示相同的可滚动报告。「网络/恢复」页提供只读的「会话历史」入口。

会话从第一个 Action 开始，不从打开 TUI 开始。开始和结束各调用一次 ioreg 与 pmset 电池查询。工具关闭后不采样。`check` 与打开的 TUI 记录实际发现的复活项目，不持续监控。现有 TUI 电池定时器会同时记录接电读数，不增加探测。动作数统计顶层 Action，不统计子进程或 PID 数；失败尝试也保留。恢复项单独计数，并跨重试累计。

整段平均功耗优先使用会话开始与结束两次读取的系统负载累加器差值，来源标为 `PowerTelemetryData.SystemLoad`，不需要持续采样。硬件缺少该累加器时，既有端点容量/电压估算标为 `capacity_delta`。仅在端点字段有效、电量与容量没有增加、保留的 pmset 日志在开始时刻或之前有电池供电记录且没有记录接电区间时提供。电源分类依赖保留的日志，不能证明事件从未遗漏。观察到接电时显示「期间曾接电，掉电统计不完整」，不提供整段掉电或功耗。日志覆盖不足或读数缺失时同样明确标为不可用。接电时仍显示端点读数，但不把差值称为掉电。已有的 before/after 测量窗口单独列出；应用后窗口均值只与相同功率来源的其他应用后窗口比较，不与整段估计混算。JSON 的 `difference_watts` 为正表示功耗更低，为负表示更高；文本显示方向与差值的绝对值。

away 报告复用睡眠段与 DarkWake 解析器，并限定在会话时间区间内。只累计日志中完整的睡眠段，不外推跨越边界的片段；没有完整匹配段时掉电为未知，不填零。记录到接电或电源覆盖未知时，不提供睡眠掉电总数。DarkWake 次数与原因仅来自区间内记录的电池事件。

```bash
travel-mode history
travel-mode history --limit 10
travel-mode history --json --limit 10
```

报告只保存在本机状态文件旁的 `history.jsonl`；设置 `TRAVEL_MODE_STATE` 会同时隔离历史路径。`[history] retain = 50` 默认保留最新 50 条，有效范围 1–10000。`history` 默认显示最新 50 条，`--limit N` 修改显示条数。JSON 遵循[历史 schema](docs/json-schema/history.schema.json)。比较只使用保留历史中同场景、同统计口径、同功率来源的其他会话，排除当前报告与缺少均值的记录。读取历史不探测 macOS，也不迁移配置。

工具先保存报告检查点，再原子写入历史，最后删除状态。归档失败时保留状态并阻止新 Action；再次运行 `off` 会重试同一报告，不重新采集结束读数，也不重复写历史。部分恢复保留会话与恢复记录。dry-run 不采样、不归档会话。旧快照的开始读数和动作数为未知，报告会明确说明。报告包含本机应用/服务名称，分享前请检查。没有跨进程写锁，请一次只运行一个修改实例。

体检 JSON 保留完整的 `health.checks` 原始观察，新增去重后的 `health.display_checks` 证据与 `references` 引用。CLI 与 TUI 使用展示视图；同一进程依次优先归入防睡工具、防睡断言、后台 CPU。总览把状态与体检合并为一个卡片。

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
| 4 | 功耗测量读取或解析失败 |
| 5 | 自动触发安装或事件处理错误 |

配置与状态错误只输出一行诊断，包含路径、相关键或原因及修复提示，不打印 Python traceback。设置 `TRAVEL_MODE_DEBUG=1` 后保留完整 traceback 和异常链。电池测量错误也只输出一行诊断；`power --json` 返回包含 `available=false`、`reason="failed"` 和 `error.type`/`error.message` 的错误对象。调试模式保留完整 traceback 和异常链。未预期异常保留正常 traceback。外部编辑器可以返回自己的退出码。

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
