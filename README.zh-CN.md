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
- 固定配色：界面颜色全部使用固定的深色色值（即 `textual-dark` 深色方案），
  并锁定 Textual 8.x，任何机器、任何终端的 ANSI 调色板都不会影响显示效果
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

### 在其他机器上安装

`codex-tui` 就是普通 Python 包，任何装了 `uv` 的机器都能把它装成全局命令。
一行命令安装（Linux / macOS，含 WSL）：

```bash
curl -fsSL https://raw.githubusercontent.com/ml-inory/codex-tui/main/install.sh | bash
```

或者手动安装：

```bash
git clone https://github.com/ml-inory/codex-tui.git ~/codex-tui
uv tool install --editable ~/codex-tui
codex-tui
```

脚本会把仓库 clone 到 `~/codex-tui`（可用 `--dir` 指定别的目录，`--release`
则安装独立副本而非 editable），把 `codex-tui` 命令装进 uv 的工具环境并做
校验。默认是 editable 安装，之后 `git -C ~/codex-tui pull` 就能更新程序。
机器上需要装好并登录 `codex` CLI 才能跑回合；Windows 请在 PowerShell 里执行
上面的手动步骤。

选项：

| 选项 | 说明 |
| --- | --- |
| `--sandbox read-only\|workspace-write\|danger-full-access` | 传给 `codex exec` 的沙箱级别；默认 `workspace-write`（允许在所选项目内编辑）。可用环境变量 `CODEX_TUI_SANDBOX` 覆盖。 |
| `--yolo` | Codex yolo 模式：跳过所有审批提示并禁用沙箱（等价于 `codex --dangerously-bypass-approvals-and-sandbox`）。强制沙箱为 `danger-full-access`，与显式指定其他 `--sandbox` 值冲突。极其危险。 |
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
| `Ctrl+D` | 删除当前会话（再按一次 Ctrl+D 确认） |
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

## 斜杠命令

输入框里支持 codex 风格的斜杠命令，回车后本地处理、不会发给模型：

| 命令 | 作用 |
| --- | --- |
| `/clear`、`/new` | 清空聊天区并开始新会话（旧会话保留在侧边栏） |
| `/help` | 打开快捷键参考页 |
| `/model` | 选择会话模型 |
| `/rename` | 重命名当前会话 |
| `/quit`、`/exit` | 退出 TUI |

未识别的命令会提示 `未知命令` 并被忽略。

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
  后台回复绝不会串到当前正在看的视图里，切换项目或会话也不会中断正在运行的
  回合。回合运行时工具调用会实时显示在聊天区：
  每个 `exec_command` 和文件修改都有一条仿 codex CLI 的状态行（`• Running
  <命令>` → `• Ran <命令>`，成功绿色、失败红色），命令输出以暗色 `└` 前缀
  边跑边显示在该行下方，不用对着转圈等结果。模型在等待后台终端时，会像
  codex CLI 一样显示一闪一闪的 `• Waiting for background terminal · <命令>`
  状态行。只读探索会合并成彩色的 `• Explored` 单元（`└ Search <关键词> in
  <路径>` / `Read <文件>`，动作名青色），文件修改显示为 `• Edited <文件>
  (+N -M)` 并带彩色 diff 预览（新增行绿色、删除行红色），和 codex 一致。
  回合运行时状态行还有旋转的加载动画（`⠋ Codex is working…`），
  侧边栏里的会话也会出现动态标记，从其他视图也能一眼看出 agent 在工作。
  流式文本和工具输出会合并到约 25fps 刷新（短回复仍然即时显示），长回答
  通过有界的分块组件流式渲染，每帧只重绘一小段尾部而不是整条消息；工作时
  终端标题同样会显示活动动画。助手消息改用内置的轻量 Markdown 渲染器
  （标题、粗斜体、行内代码、代码块、列表、引用），不再解析完整 CommonMark，
  打开转录快约 10 倍。
  切换会话/项目也做了响应性优化：会话扫描在后台线程执行、侧边栏列表批量
  重建、长会话转录采用懒渲染——打开会话时只构建可见的尾部，往上滚动时
  自动补充更早的消息，`F7` 可直接跳到最早的消息，不会再整屏卡死。侧边栏
  列表只读取会话元数据（完整转录按需解析），即使有成百上千个大会话，切换
  也能保持流畅。
  后台回合完成时会通过应用内 toast、侧边栏标记和 `Ctrl+G` 快捷键提示。
- 如果 `codex app-server` 不可用或某个回合失败，TUI 会自动回退到
  `codex exec --json`，并重新从磁盘读取会话记录。
- 删除的会话移到 `~/.codex-tui/trash`，不会直接删除。

## 限制

- 回合使用 app-server 的 `approvalPolicy: "never"`（与无交互的 `codex exec`
  行为一致），因此 TUI 内没有审批弹窗：`--sandbox` 决定 Codex 能做什么。
  `workspace-write` 允许修改所选项目内的文件；`read-only` 禁止写入；
  `danger-full-access` 允许一切操作。`--yolo` 是后者的快捷写法（审批本来
  就处于关闭状态）。
- 如果找不到 `codex` 可执行文件，发送消息会显示应用内错误而不是崩溃。
- `codex app-server` 属于实验性 CLI 接口；如果未来 codex 版本改动协议，TUI
  会降级到 `exec` 模式而不是报错。

## 开发

```bash
uv run pytest -q
```
