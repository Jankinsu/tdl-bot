# 从 Python 脚本到 systemd 用户服务：tdl-bot 项目复盘

> 本文记录 `tdl-bot` 从编写 Python 程序到在 Debian 13 上长期运行的过程，重点解释命令的作用及其背后的 Linux 进程管理原理。  
> 相关文件：[`bot.py`](../bot.py) · [`tdl-bot.service`](../tdl-bot.service) · [`settings.env.example`](../settings.env.example) · [返回 README](../README.md)

## 1. 总览：程序、服务与开机自启是三个层次

```text
普通 Python 程序
    │  python3 bot.py：从当前终端手动启动
    ▼
systemd 用户服务
    │  Unit 文件定义如何启动、停止、重启及加载环境
    ▼
启用服务并允许 linger
       用户退出 SSH 后继续运行；系统开机后自动启动
```

**核心认识：Python 代码定义“做什么”，systemd 定义“怎样运行、由谁管理，以及什么时候启动”。**

把脚本变成服务，并不是重新编译 Python，也不是简单地在命令后面追加 `&`，而是让服务管理器接管进程的生命周期。

## 2. Python 代码负责什么？

项目入口为 `~/apps/tdl-bot/bot.py`，使用 Python 标准库，不需要 Docker、Redis 或额外 Python 包。

| 职责 | 实现方式 | 目的 |
| --- | --- | --- |
| Telegram 通信 | `urllib.request` + Bot API + Long Polling | 接收私聊消息、发送通知 |
| 消息校验 | 用户 ID 白名单、链接格式验证 | 只处理授权用户的 Telegram 消息链接 |
| 任务持久化 | SQLite：`jobs.sqlite3` | 重启后保留等待任务 |
| 执行下载 | `subprocess.run()` 调用 `tdl` | 复用成熟的下载程序 |
| 并发响应 | 主线程 + 下载 Worker 线程 | 下载时仍能回复 `/status` |
| 存储检查 | `Path.is_mount()`、写权限、剩余空间检查 | 避免移动硬盘未挂载时写入系统盘 |

### 2.1 Python 如何调用现有的 CLI 程序？

在 [`bot.py`](../bot.py) 中，Worker 使用类似以下代码：

```python
result = subprocess.run(
    [
        TDL_BIN, "dl", "-u", link,
        "-d", str(MEDIA_DIR),
        "--skip-same", "--disable-progress-ps",
    ],
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
    env=env,
    check=False,
)
```

对应手工输入的核心命令大致是：

```bash
tdl dl -u '<Telegram 消息链接>' -d /mnt/media/telegram
```

区别在于：`subprocess.run()` **启动一个子进程并等待其结束**；我们传入的是参数列表，没有使用 Shell 拼接命令。程序通过退出码判断任务成功或失败，下载的标准输出和错误输出都写入日志。

这体现了一个实用原则：**Bot 负责调度和交互，`tdl` 负责实际下载，避免重复造轮子。**

### 2.2 为什么需要 SQLite？

任务状态按以下路径流转：

```text
queued（等待） → running（正在下载） → done（完成）
                                  └──→ failed（失败）
```

数据库位置：`~/.local/state/tdl-bot/jobs.sqlite3`。

- 消息入队时，记录链接、任务编号和状态。
- Worker 每次仅领取一个等待任务，因此同时最多执行一个下载。
- 启动时，数据库中遗留的 `running` 任务会重新标记为 `queued`，便于重试。
- Telegram 的消息偏移量也存入数据库，避免进程重启后重复消费已处理消息。

这让“收到两条链接，`/status` 显示正在下载 1、等待 1”成为有状态、可恢复的行为，而不只是一份内存队列。

**边界：** 重启后会重新尝试中断任务，但不保证精确到字节的断点续传。

### 2.3 下载时为何还可以响应命令？

```python
threading.Thread(
    target=worker,
    name="download-worker",
    daemon=True,
).start()
```

主线程负责 Telegram Long Polling，Worker 线程串行执行下载。因此下载过程不会阻塞主线程处理 `/status`。

注意：这里的 `daemon=True` 是 **Python 守护线程**标志，**不等于**把 Python 程序注册为 Linux 守护服务。后者由 systemd 完成。

## 3. 从手动运行切换为 systemd 用户服务

### 3.1 最初如何运行？

```bash
python3 ~/apps/tdl-bot/bot.py
```

这个命令由当前 Shell 启动 Python 进程。按 `Ctrl+C` 可以请求退出；直接关闭 SSH 会话通常不能作为可靠的后台运行方案。

我们需要的是：即使 SSH 断开、进程异常退出甚至 Debian 重启，机器人仍可以继续工作。

### 3.2 systemd Unit 文件是什么？

仓库中实际使用的 [`tdl-bot.service`](../tdl-bot.service)：

```ini
[Unit]
Description=Personal Telegram tdl download bot

[Service]
Type=simple
WorkingDirectory=%h/apps/tdl-bot
EnvironmentFile=%h/.config/tdl-bot/settings.env
ExecStart=/usr/bin/python3 %h/apps/tdl-bot/bot.py
Restart=always
RestartSec=5
TimeoutStopSec=45

[Install]
WantedBy=default.target
```

分成三部分：

- **`[Unit]`**：描述服务，`Description` 用来显示可读名称。
- **`[Service]`**：定义如何运行服务，以及停止、重启策略。
- **`[Install]`**：定义通过 `enable` 启用服务时的关联关系。

重点字段：

| 字段 | 解释 |
| --- | --- |
| `Type=simple` | 直接运行前台进程；无需程序自行 fork 到后台 |
| `WorkingDirectory` | 设置进程的工作目录 |
| `EnvironmentFile` | 从独立配置文件向进程提供环境变量 |
| `ExecStart` | 指定 Python 解释器和脚本路径 |
| `Restart=always` | 进程正常或异常退出后通常会重启；管理员主动 `stop` 不会因此自动拉起 |
| `RestartSec=5` | 重启前等待 5 秒 |
| `TimeoutStopSec=45` | 停止流程最多等待约 45 秒，之后 systemd 可能强制结束进程 |
| `WantedBy=default.target` | 使用户管理器启动其默认目标时也启动该服务（需 `enable`） |

`%h` 是 systemd 的用户主目录占位符；对 `example-user` 用户而言就是 `/home/example-user`。

其中最重要的是：

```ini
ExecStart=/usr/bin/python3 %h/apps/tdl-bot/bot.py
```

也就是让 **systemd 而非当前终端**负责启动这个 Python 进程。

## 4. 逐条理解部署命令

以下命令在**运行 `tdl login` 的同一个普通用户**下执行。假设代码目录为 `~/apps/tdl-bot`。

### 4.1 把 Unit 安装到用户服务目录

```bash
install -Dm 644 ~/apps/tdl-bot/tdl-bot.service ~/.config/systemd/user/tdl-bot.service
```

- `install`：复制文件并同时设置权限，适合部署场景。
- `-D`：需要时自动创建目标文件的父目录。
- `-m 644`：目标文件权限为 `644`（所有者可读写，其他用户只读）。

用户级 Unit 放在 `~/.config/systemd/user/`，使用 `systemctl --user` 管理。它不同于系统级 Unit（例如放在 `/etc/systemd/system/` 并通常通过 `sudo systemctl` 管理）。

### 4.2 首次创建配置，避免覆盖已有设置

```bash
test -e ~/.config/tdl-bot/settings.env || install -m 600 ~/apps/tdl-bot/settings.env.example ~/.config/tdl-bot/settings.env
```

这里的重点是 Shell 运算符 `||`：

- `test -e 路径`：判断路径是否存在。
- `A || B`：**只有 A 失败才执行 B**。
- `install -m 600`：安装配置文件，权限为仅所有者可读写。

因此，这条命令表示：**只有正式配置不存在时才从示例安装，避免覆盖现有配置。**

前提是 `~/.config/tdl-bot/` 已存在；本项目的 Token 和白名单也保存在该目录。

正式配置位于 `~/.config/tdl-bot/settings.env`，和 Git 仓库中的示例模板分开。现有移动硬盘配置为：

```ini
TDL_BOT_MEDIA_DIR=/mnt/media/telegram
TDL_BOT_REQUIRED_MOUNT=/mnt/media
```

这两个值必须使用绝对路径；不要在配置文件中写 `~` 或 `$HOME` 期待 Shell 自动展开。

**重要区别：** 手动终端可能从 `~/.bashrc` 读取代理变量，systemd 用户服务却不会因此自动继承。故我们把 `TDL_BOT_API_PROXY` 和 `TDL_PROXY` 单独配置在 `settings.env`。

### 4.3 通知 systemd 重新读取 Unit

```bash
systemctl --user daemon-reload
```

这条命令让**用户级 systemd 管理器**重新读取 Unit 定义，但**不等于重启服务**。

可这样区分：

| 改动 | 通常需要执行 |
| --- | --- |
| 修改 `tdl-bot.service` | `systemctl --user daemon-reload`，然后 `restart` |
| 修改 `settings.env` | `systemctl --user restart tdl-bot` |
| 修改 `bot.py` | `systemctl --user restart tdl-bot` |

### 4.4 启用并立即启动

```bash
systemctl --user enable --now tdl-bot
```

这相当于：

```bash
systemctl --user enable tdl-bot
systemctl --user start tdl-bot
```

- `enable`：根据 Unit 的 `[Install]` 配置建立自动启动关联，通常通过创建符号链接实现。
- `start`：现在就启动进程。
- `--now`：把启用与立即启动合为一步。

**记住：`enable` 不等于 `start`；前者管理启动关系，后者管理当前运行状态。**

### 4.5 为什么还要 enable-linger？

```bash
sudo loginctl enable-linger "$USER"
```

用户级 systemd 管理器的生命周期默认可能受用户登录状态影响。`enable-linger` 允许这个用户的管理器在没有登录会话时继续运行，并且随系统启动。

因此，`enable` + `enable-linger` 才完整满足私人服务器的需求：**不必保持 SSH 登录，开机后仍能运行机器人**。

`"$USER"` 是 Shell 变量；在 `example-user` 的会话里会展开为 `example-user`。本命令中的 `sudo` 是为修改系统级 linger 设置，并不意味着机器人要以 root 身份运行。

### 4.6 检查部署结果

```bash
systemctl --user status tdl-bot --no-pager
```

- `status`：查看服务是否运行、最近的日志和退出情况。
- `--no-pager`：直接输出到终端，不进入分页查看界面。

## 5. 日常维护与日志

| 命令 | 用途 |
| --- | --- |
| `systemctl --user status tdl-bot` | 查询服务状态 |
| `systemctl --user start tdl-bot` | 启动 |
| `systemctl --user stop tdl-bot` | 停止 |
| `systemctl --user restart tdl-bot` | 重启；修改 Python 或环境配置后使用 |
| `systemctl --user enable tdl-bot` | 设置随用户管理器自动启动 |
| `systemctl --user disable tdl-bot` | 取消自动启动 |
| `journalctl --user -u tdl-bot -f` | 实时查看服务日志 |
| `tail -n 60 ~/.local/state/tdl-bot/downloads.log` | 查看最后 60 行下载日志 |

日志分工：

- **systemd Journal**：查看机器人启动、退出、轮询错误以及 Python 的标准输出。
- **`downloads.log`**：查看通过 `subprocess.run()` 重定向的 `tdl` 下载详情。

`journalctl` 中的 `-u` 表示筛选指定 Unit；`-f` 是持续跟踪。`tail -n 60` 表示显示文件最后 60 行。

**注意：** 不要同时运行前台版 `python3 bot.py` 和 systemd 版机器人，否则可能有两个进程争夺 Telegram Long Polling 消息。

## 6. 为什么要把代码、配置、状态和媒体分开？

```text
/home/example-user/
├── apps/tdl-bot/
│   ├── bot.py                  # 代码（Git 管理）
│   └── tdl-bot.service         # Unit 模板
├── .config/
│   ├── systemd/user/
│   │   └── tdl-bot.service     # 已安装的 Unit
│   └── tdl-bot/
│       ├── token              # Bot 密钥（不要入库）
│       ├── allowed_user_id    # 授权用户（不要入库）
│       └── settings.env       # 本机配置（不要入库）
└── .local/state/tdl-bot/
    ├── jobs.sqlite3           # 持久化队列
    └── downloads.log          # 下载日志

/mnt/media/telegram/           # 移动硬盘中的媒体文件
```

这种结构体现了**关注点分离**：

| 类型 | 位置 | 理由 |
| --- | --- | --- |
| 代码 | `~/apps/tdl-bot` | 方便通过 Git 更新 |
| 用户配置与凭据 | `~/.config/tdl-bot` | 与公开源码分离 |
| 运行状态 | `~/.local/state/tdl-bot` | 任务数据和日志持久保留 |
| 大型媒体 | `/mnt/media/telegram` | 不占满 Debian 系统盘 |

仓库公开，因此**绝不能提交实际 Bot Token、Telegram 登录会话、个人身份文件或下载媒体**。

移动硬盘模式还需设置 `TDL_BOT_REQUIRED_MOUNT=/mnt/media`。代码会先检查挂载，再创建下载目录；硬盘缺席时暂停 Worker、保留待办任务，避免在系统盘上误建媒体目录。

## 7. 三条最应该记住的命令

```bash
systemctl --user daemon-reload
systemctl --user enable --now tdl-bot
sudo loginctl enable-linger "$USER"
```

分别是：

1. **重新加载定义**：让 systemd 认识新 Unit。
2. **启用并启动服务**：把它纳入用户服务管理并立即运行。
3. **脱离登录会话**：让服务在用户未登录时也能运行。

完整的依赖链是：

```text
Debian 启动
  → 用户级 systemd 管理器
  → 读取 tdl-bot.service
  → 启动 Python 进程
  → 主线程接收 Telegram 消息
  → Worker 线程创建 tdl 子进程
  → 视频保存到已挂载的移动硬盘
```

**这次项目真正学到的，并不只是“让脚本在后台运行”，而是如何用操作系统原生的进程与服务管理机制，构建一个可配置、可恢复、可观察、可长期运行的小型服务。**

---

> 本文基于仓库中的实际程序、Unit 文件及部署流程整理；具体可执行命令以 [README 部署章节](../README.md#部署在-debian-上操作) 为准。
