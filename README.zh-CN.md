# codex-tui

[English](README.md) · [中文](README.zh-CN.md)

一个类似 Codex Desktop 的终端界面，基于 [Textual](https://textual.textualize.io/)，
由本机安装的 `codex` CLI 驱动。左侧侧边栏管理项目（工作目录）和会话；
右侧显示当前选中的对话和提示输入框。

## 功能

- 侧边栏包含项目列表和按项目分组的会话列表（读取自 `$CODEX_HOME/sessions`，
  例如 `~/.codex/sessions`）；项目默认只显示最深层目录名，按 `F2` 可切换为完整
  路径（会记住选择）；`Ctrl+B` 可完全隐藏侧边栏，让聊天区占满整行（也会记住）
- `F1` 打开可搜索的快捷键参考页，footer 不用再塞下所有快捷键
- tmux 风格分屏：`Ctrl+\` 打开第二个只读面板，并排显示另一个会话（支持实时
  流式），`Ctrl+T` 交换当前会话与监视会话，再按一次 `Ctrl+\` 关闭分屏
- Markdown 渲染对话（用户消息为气泡，Codex 回复为 Markdown）
- 新建会话或继续已有会话时，回复通过持久的 `codex app-server` 连接逐字流式
  输出（与 Codex Desktop 相同协议），体感接近原生 CLI
- `Ctrl+O` 按标题/项目/会话 id 过滤并跳转到任意会话；`Ctrl+Up` / `Ctrl+Down`
  在当前项目的会话间循环；会话列表刷新时保持当前选中不跳走
- 后台运行：在一个会话发消息后可以切到别的会话继续干活。后台会话完成时会弹
  toast，侧边栏标题前出现 `●` 标记，按 `Ctrl+G` 直接跳到最近完成的会话
- `Esc` 中断模型当前回复
- 鼠标拖拽即可框选聊天文本，按 `Ctrl+C` 复制（OSC 52 剪贴板，MobaXTerm /
  Windows Terminal 等 SSH 终端均支持）；`Ctrl+Y` 复制最后一条 Codex 回复，
  `Ctrl+Shift+Y` 复制整个会话
- 删除会话（移到 `~/.codex-tui/trash`，可恢复）
- 可配置沙箱，让 Codex 能真正修改项目文件

## 环境要求

- Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)（或 `pip`）
- `codex` CLI（`codex --version`），已登录/配置好——TUI 直接复用你现有的
  `~/.codex` 认证和模型配置，包括自定义 provider

## 使用

```bash
uv run codex-tui
```

或安装后运行：

```bash
uv sync && uv run codex-tui
```

选项：

| 选项 | 说明 |
| --- | --- |
| `--sandbox read-only\|workspace-write\|danger-full-access` | 传给 `codex exec` 的沙箱级别；默认 `workspace-write`（允许在所选项目内编辑）。可用环境变量 `CODEX_TUI_SANDBOX` 覆盖。 |
| `--codex-bin PATH` | codex 可执行文件路径（默认：`codex`）。 |
| `--mode auto\|interactive\|exec` | 回合后端：`interactive` 通过 `codex app-server` 流式输出增量；`exec` 使用一次性 `codex exec --json`。默认 `auto`（优先 interactive，app-server 不可用时自动回退 exec）。可用环境变量 `CODEX_TUI_MODE` 覆盖。 |
| `--sessions-dir PATH` | 覆盖会话目录（默认：`$CODEX_HOME/sessions`）。 |
| `--clean-trash` | 永久删除回收站中的会话记录并退出。 |

## 快捷键

| 按键 | 作用 |
| --- | --- |
| `Tab` / `Shift+Tab` | 在侧边栏和输入框之间切换焦点 |
| `Enter` | 发送消息（无会话时新建，否则继续当前会话） |
| `Ctrl+N` | 新建会话 |
| `Ctrl+D` | 删除当前会话（再按一次确认） |
| `Ctrl+R` | 重命名当前会话 |
| `F1` | 打开快捷键参考页（支持输入过滤） |
| `F2` | 切换项目路径显示（最深层目录 ↔ 完整路径） |
| `F3` | 选择会话使用的模型（来自 `~/.codex/models.json`） |
| `F5` | 刷新项目 / 会话列表 |
| `F7` | 加载当前会话更早的消息 |
| `Ctrl+B` | 显示/隐藏左侧侧边栏（隐藏后聊天区占满整行） |
| `Ctrl+\` | 打开/关闭分屏（先选择要监视的会话） |
| `Ctrl+T` | 交换当前会话与监视会话 |
| `Ctrl+O` | 快速切换到任意会话（输入过滤，回车打开） |
| `Ctrl+Up` / `Ctrl+Down`（或 `Alt+Up` / `Alt+Down`） | 在当前项目的会话间循环 |
| `Ctrl+G` | 跳到最近完成的后台会话 |
| `Ctrl+Y` | 复制当前会话最后一条 Codex 回复 |
| `Ctrl+Shift+Y` | 复制整个当前会话（排除注入的系统上下文） |
| `Esc` | 中断当前回合 |
| `q` | 退出（按 `Ctrl+Q` 确认） |

会话标题和模型选择保存在 `~/.codex-tui/overrides.json`；codex 自己的会话文件
永远不会被修改。删除的会话会移到 `~/.codex-tui/trash` 而不是直接抹掉。

## 工作原理

- 会话记录从 `$CODEX_HOME/sessions/YYYY/MM/DD/` 下的 codex JSONL 文件解析。
  未知或未来的事件类型会被忽略，因此更新的 CLI 版本不会破坏浏览。
- 默认交互模式下，整个 TUI 生命周期只启动一个 `codex app-server --stdio`
  进程。新会话用 `thread/start`，已有会话用 `thread/resume` 续接，每条消息只是
  在活跃线程上的一个 `turn/start`——不再有每条消息冷启动 CLI 的开销。模型流式
  输出时服务端推送 `item/agentMessage/delta` 通知，聊天区增量渲染，回合结束时
  再转为 Markdown。
- 多个会话可以同时跑回合；每个会话订阅自己的线程通知，流式文本按会话缓冲，
  后台回复绝不会串到当前正在看的视图里。后台回合完成时会通过应用内 toast、
  侧边栏标记和 `Ctrl+G` 快捷键提示。
- 如果 `codex app-server` 不可用或某个回合失败，TUI 会自动回退到
  `codex exec --json`，并重新从磁盘读取会话记录。
- 删除的会话移到 `~/.codex-tui/trash`，不会直接删除。

## 限制

- 回合使用 app-server 的 `approvalPolicy: "never"`（与无交互的 `codex exec`
  行为一致），因此 TUI 内没有审批弹窗：`--sandbox` 决定 Codex 能做什么。
  `workspace-write` 允许修改所选项目内的文件；`read-only` 禁止写入；
  `danger-full-access` 允许一切操作。
- 如果找不到 `codex` 可执行文件，发送消息会显示应用内错误而不是崩溃。
- `codex app-server` 属于实验性 CLI 接口；如果未来 codex 版本改动协议，TUI
  会降级到 `exec` 模式而不是报错。

## 开发

```bash
uv run pytest -q
```
