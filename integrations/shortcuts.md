# Shortcuts integration

在 macOS 快捷指令中添加“运行 Shell 脚本”。脚本使用登录 shell 的 `travel-mode` 路径：

```sh
travel-mode check --json
```

将输出接到“从 JSON 获取字典”即可读取 `snapshot.exists`、`battery.percent` 和 `blockers`。
需要修改系统设置时，使用“在终端中运行”并调用 `travel-mode on --profile cafe --yes` 或 `travel-mode off`，这样 sudo 密码在 Terminal 中输入。
