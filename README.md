# tdl-bot · Telegram 私人媒体下载助手

使用 Telegram Bot 接收私聊消息链接，交给已登录的 `tdl` 下载媒体，完成后回复结果。采用 Python 标准库、SQLite 和 systemd 用户服务；无需 Docker、Redis 或第三方 Python 包。

## 功能

- systemd 用户服务：无需保持 SSH 终端连接；`enable-linger` 后能开机自启。
- SQLite 持久化队列：消息入队后写入 `~/.local/state/tdl-bot/jobs.sqlite3`；进程重启可重新处理被中断的任务。
- 保留既有 Token、白名单 User ID、tdl 登录会话，不需重新扫码。
- 继承旧版 `~/.local/state/tdl-bot/offset`，避免升级后重放之前已消费的消息。
- 下载路径用配置文件指定；移动硬盘未挂载时停止下载并保留待处理任务，不在系统盘下建立媒体目录。
- 不直接拼 Shell 命令，链接限制为 Telegram 消息 URL，仅允许已设置的个人 User ID 操作。
- `/start`、`/help`、`/status`；相同进行中链接去重；下载开始、完成、失败通知。详细命令与字段见下方「使用指南」。

## 使用指南（Telegram 私聊）

机器人只处理 `~/.config/tdl-bot/allowed_user_id` 指定用户发来的**私聊文本消息**，不在群组中接受命令。

### 支持的命令

| 输入 | 功能 |
| --- | --- |
| `/start` | 显示欢迎信息和简短使用提示 |
| `/help` | 与 `/start` 相同，目前尚无独立的完整帮助菜单 |
| `/status` | 查询任务数量、当前执行任务和存储状态 |
| Telegram 消息链接 | 提取消息中的首个符合格式的链接，并加入下载队列 |

### 发送下载链接

直接向 Bot 发送一条消息，例如：

```text
https://t.me/telegram/193
```

目前识别的消息链接形式包括：

- 公开频道或群组消息：`https://t.me/<用户名>/<消息ID>`
- 私有频道或群组消息：`https://t.me/c/<内部ID>/<消息ID>`
- 上述路径还允许附带最多两段数字路径（例如部分话题消息）；`www.t.me` 也可识别。

链接可以出现在普通文本中，但**每条消息只提取第一个符合格式的链接**；不支持直接发文件、关键词搜索、批量多链接解析或任意网址。能否成功下载还取决于已登录的 `tdl` 账号是否能访问该消息。

收到链接后机器人会回复 `已加入下载队列：任务 #编号`；同一链接如果还在等待或下载中，会提示已有任务；等待及运行任务合计达到 **20** 个时拒绝新任务。每次只执行一个下载，执行开始、成功或失败时会推送通知；已有同名文件由 `tdl --skip-same` 跳过。

### `/status` 返回字段

| 字段 | 说明 |
| --- | --- |
| `正在下载` | 当前运行任务的编号（例如 `#12`），无任务时显示「无」 |
| `等待` | 当前 `queued` 状态的任务数 |
| `完成` | SQLite 中累计 `done` 状态的任务数，不是今天完成数 |
| `失败` | SQLite 中累计 `failed` 状态的任务数，不是今天失败数 |
| `存储` | 下载目录是否就绪；未挂载、无写入权限或剩余空间不足 **2 GiB** 时给出原因 |
| `目录` | 当前媒体下载目标路径 |

示例（数值仅供演示）：

```text
📋 正在下载：#12
等待：3，完成：8，失败：1
💾 存储：正常
目录：/mnt/media/telegram
```

目前**不支持** `/queue`、`/cancel`、`/retry` 等管理命令，也不提供实时下载百分比、速度、预计剩余时间、逐任务详情查询或清理历史记录。下载失败需查看日志并重新发送消息链接。

### 配置项

服务读取 `~/.config/tdl-bot/settings.env`；所有路径需要写成**绝对路径**，不能用 `~` 或 `$HOME` 代替。修改后执行 `systemctl --user restart tdl-bot` 生效。

| 配置名 | 默认值 | 用途 |
| --- | --- | --- |
| `TDL_BOT_MEDIA_DIR` | 当前运行用户的 `~/Downloads/telegram` | 下载文件保存路径 |
| `TDL_BOT_REQUIRED_MOUNT` | 空（不检查） | 要求挂载的磁盘路径；移动硬盘模式下建议设为 `/mnt/media` |
| `TDL_BOT_API_PROXY` | `http://127.0.0.1:2080` | Telegram Bot API 使用的 HTTP 代理 |
| `TDL_PROXY` | `socks5://127.0.0.1:2080` | 传给 `tdl` 下载进程的代理 |
| `TDL_BOT_TDL_BIN` | `/usr/local/bin/tdl` | tdl 可执行文件路径 |

Bot Token 与允许使用的用户 ID 分别存放在 `~/.config/tdl-bot/token` 和 `~/.config/tdl-bot/allowed_user_id`，**不要写进仓库或公开配置**。

## 部署（在 Debian 上操作）

> 📚 **部署原理与命令复盘：** [从 Python 脚本到 systemd 用户服务：tdl-bot 项目复盘](docs/systemd-service-guide.md) —— 结合本项目源码，解释进程、Unit 文件、`install`、`daemon-reload`、`enable --now`、`enable-linger`、日志及目录设计。

要求：Linux / Debian 13、Python 3.9+、已安装并在普通用户下登录成功的 `tdl`，以及可访问 Telegram Bot API 的网络代理。以**同一个普通用户**运行 Bot 与 `tdl login`。事先创建 `~/.config/tdl-bot/token`（完整 Bot Token）和 `~/.config/tdl-bot/allowed_user_id`（你个人的 Telegram User ID，非 Bot ID），并设置 `chmod 600`；**不要提交或公开这两个文件**。

在启动新服务前，先用 `Ctrl+C` 停止旧版正在前台运行的 `bot.py`，并尽量等旧下载任务结束，以免两个程序争夺 Telegram Long Polling 更新。

建议直接将仓库克隆到唯一正式代码目录 `~/apps/tdl-bot`（如已有第一版正在运行，请先停止旧进程并备份旧目录；目录已存在时不必重复克隆）：

```bash
mkdir -p ~/apps
git clone https://github.com/Jankinsu/tdl-bot.git ~/apps/tdl-bot
```

从仓库文件安装服务配置（Python 程序直接在 Git 仓库目录运行，无须再复制一份 `bot.py`）：

```bash
install -Dm 644 ~/apps/tdl-bot/tdl-bot.service ~/.config/systemd/user/tdl-bot.service
test -e ~/.config/tdl-bot/settings.env || install -m 600 ~/apps/tdl-bot/settings.env.example ~/.config/tdl-bot/settings.env
systemctl --user daemon-reload
systemctl --user enable --now tdl-bot
sudo loginctl enable-linger "$USER"
systemctl --user status tdl-bot --no-pager
```

> 如果 `TDL_PROXY` 配置在 `~/.bashrc`，它对 systemd 服务不生效。新版已在 `settings.env` 单独配置 HTTP/SOCKS 代理。

## 查看日志 / 管理服务

```bash
systemctl --user status tdl-bot --no-pager
journalctl --user -u tdl-bot -f
tail -n 60 ~/.local/state/tdl-bot/downloads.log
systemctl --user restart tdl-bot
systemctl --user stop tdl-bot
```

未配置 `TDL_BOT_MEDIA_DIR` 时，默认下载目录为运行用户的 `~/Downloads/telegram`。发送 `/status` 可检查队列和磁盘状态。

## 移动硬盘挂载成功后

先确认真实挂载成功：

```bash
findmnt -M /mnt/media
```

通过合适的 NTFS3 `uid`、`gid`、`umask` 挂载选项确保普通用户可写；不要对**未挂载时**的 `/mnt/media` 目录执行 `chown` 给 Bot 用户。挂载点底层应由 root 拥有且用户不可写（如 0755），避免硬盘意外拔出后的竞态写入。

修改 `~/.config/tdl-bot/settings.env` 的两行：

```ini
TDL_BOT_MEDIA_DIR=/mnt/media/telegram
TDL_BOT_REQUIRED_MOUNT=/mnt/media
```

然后：

```bash
systemctl --user restart tdl-bot
```

此后磁盘未挂载时，Bot 仍可接收并排队链接，但不会往 `/mnt/media/telegram` 写文件；挂载回来后自动开始处理待办任务。原先保存在系统盘的媒体可另行迁移。

## 说明及边界

- 一个时刻只运行一个 `tdl` 下载任务；队列容量 20（等待＋运行）。
- 确认下载成功的标准是 tdl 进程退出码为 0；这不等于额外校验媒体内容哈希。
- 被重启打断的下载任务会重新尝试，不能保证精确到字节的断点续传。
- 如 Throne 没有运行，Bot API 会连接失败并持续重试；下载任务可能失败，检查日志并重新提交链接。
- 队列数据库存于 Debian 系统盘，而媒体文件存于你设置的目标盘。
- 系统重装、Python 和 tdl 重大版本升级前先备份配置与任务数据库。

## 安全注意事项

- **本仓库不包含真实 Token、个人 User ID、Telegram 账号会话或媒体文件。** 仓库当前是公开的，提交前应检查 `git status` 和 `git diff --cached`。
- `.gitignore` 是防误提交保护，不是凭据加密措施；若 Token 意外公开，应立刻去 BotFather 重新生成。
- `TDL_BOT_REQUIRED_MOUNT` 留空时不启用挂载检查；迁移到移动硬盘后必须设置该值。
- 首次运行前保证 Throne 已启动并监听配置的代理端口。

## 一键安装、升级与卸载

本项目和 [tg-auto](https://github.com/Jankinsu/tg-auto) 采用一致的目录规范：GitHub 管理代码，Debian 本地 `~/.config/tdl-bot/` 管理凭据与配置，`~/.local/state/tdl-bot/` 管理 SQLite 队列和下载日志，用户级 systemd 管理常驻服务。

### 一键安装（保留已有数据）

先以运行 `tdl login` 的**同一个普通 Linux 用户**登录 Debian（不要用 root / `sudo bash`）。确认 `tdl` 已安装且登录成功，Throne 代理正常运行。已有仓库用 `git pull` 更新；新机器先按前文克隆至 `~/apps/tdl-bot`。

```bash
cd ~/apps/tdl-bot
git pull
bash scripts/install.sh
```

脚本检查 Python、tdl 可执行路径，建立本地配置/状态目录（700），仅在缺少配置时复制 `settings.env.example`，仅在缺少凭据时交互输入 BotFather Token 和允许访问的个人 Telegram User ID，设置文件 600 权限，检查 `bot.py` 语法，并安装 `~/.config/systemd/user/tdl-bot.service`。

**重要**：默认只安装/更新 unit，**不启动或重启服务**，避免中断正在下载的任务。现有 `settings.env`、Token、User ID、`jobs.sqlite3`、offset、下载日志、tdl 登录状态及媒体文件均不覆盖。原 unit 与仓库模板不同时备份为 `tdl-bot.service.backup.*`。安装脚本不创建新的 tdl 会话，不自动挂载硬盘。

首次确认 `settings.env` 的媒体目录/挂载路径/代理后，手动启动：

```bash
systemctl --user enable --now tdl-bot.service
systemctl --user status tdl-bot.service --no-pager
```

也可以明确选择安装后启动：

```bash
bash scripts/install.sh --enable-service
```

如果升级代码后需要重启**已经在运行**的服务：

```bash
bash scripts/install.sh --restart
```

重启可能中断当前下载；下载队列会持久化，但被中断任务可能重新执行。**代码更新本身不等于服务自动重启**。

`loginctl show-user "$USER" -p Linger` 检查用户级服务是否能在退出登录后继续运行。如果需要且显示 `Linger=no`，执行 `sudo loginctl enable-linger "$USER"`。这项设置属于 Linux 用户，卸载项目不会关闭它。

### 一键卸载（默认保留所有数据）

```bash
cd ~/apps/tdl-bot
bash scripts/uninstall.sh
```

默认停止并禁用 `tdl-bot.service`，移除用户级 unit 并重新加载 systemd。**保留**仓库代码、本机 `token`/`allowed_user_id`/`settings.env`、SQLite 队列与日志、tdl 登录会话、媒体文件和此前 unit 备份，方便之后重新安装。

### 彻底卸载（需显式确认）

```bash
cd ~/apps/tdl-bot
bash scripts/uninstall.sh --purge
```

会要求输入 `DELETE tdl-bot`，确认后删除以下**仅属于 tdl-bot** 的目录和文件：

```text
~/apps/tdl-bot/                           # 仓库源码
~/.config/tdl-bot/                        # Bot Token、白名单 ID、settings.env
~/.local/state/tdl-bot/                   # jobs.sqlite3、offset、downloads.log
~/.config/systemd/user/tdl-bot.service   # 服务 unit
~/.config/systemd/user/tdl-bot.service.backup.*  # 安装脚本生成的 unit 备份
```

**不会删除**：`tdl` 可执行文件、`tdl login` 的个人 Telegram 会话、`/mnt/media` 硬盘内容、已下载媒体、`tdl-bot` 之外的服务（包括 `tg-auto`、Throne、青龙）、Python、系统软件包、GitHub 远程仓库、linger 设置和 systemd journal 历史日志。彻底卸载会**永久清除 SQLite 中的待下载任务和任务历史**，请先处理/备份队列。自动卸载不能让已泄露的 Bot Token 失效：如要撤销或轮换 Token，请通过 BotFather 操作。

无交互终端的彻底卸载只能显式执行 `bash scripts/uninstall.sh --purge --yes`，请谨慎使用。

### 手动核查与更新

```bash
cd ~/apps/tdl-bot
git pull
bash scripts/install.sh
systemctl --user status tdl-bot.service --no-pager
journalctl --user -u tdl-bot.service -n 50 --no-pager
```

不要把 `~/.config/tdl-bot/` 或 `~/.local/state/tdl-bot/` 复制回 GitHub 仓库。公开仓库只能提交程序、非敏感模板和文档；`.gitignore` 不能消除已经进入 Git 历史的秘密。泄露的 Token 必须立即轮换。
