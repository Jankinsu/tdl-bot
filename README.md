# tdl-bot · Telegram 私人媒体下载助手

使用 Telegram Bot 接收私聊消息链接，交给已登录的 `tdl` 下载媒体，完成后回复结果。采用 Python 标准库、SQLite 和 systemd 用户服务；无需 Docker、Redis 或第三方 Python 包。

## 功能

- systemd 用户服务：无需保持 SSH 终端连接；`enable-linger` 后能开机自启。
- SQLite 持久化队列：消息入队后写入 `~/.local/state/tdl-bot/jobs.sqlite3`；进程重启可重新处理被中断的任务。
- 保留既有 Token、白名单 User ID、tdl 登录会话，不需重新扫码。
- 继承旧版 `~/.local/state/tdl-bot/offset`，避免升级后重放之前已消费的消息。
- 下载路径用配置文件指定；移动硬盘未挂载时停止下载并保留待处理任务，不在系统盘下建立媒体目录。
- 不直接拼 Shell 命令，链接限制为 Telegram 消息 URL，仅允许已设置的个人 User ID 操作。
- `/start`、`/help`、`/status`；相同进行中链接去重；下载开始、完成、失败通知。

## 部署（在 Debian 上操作）

要求：Linux / Debian 13、Python 3.9+、已安装并在普通用户下登录成功的 `tdl`，以及可访问 Telegram Bot API 的网络代理。以**同一个普通用户**运行 Bot 与 `tdl login`。事先创建 `~/.config/tdl-bot/token`（完整 Bot Token）和 `~/.config/tdl-bot/allowed_user_id`（你个人的 Telegram User ID，非 Bot ID），并设置 `chmod 600`；**不要提交或公开这两个文件**。

在启动新服务前，先用 `Ctrl+C` 停止旧版正在前台运行的 `bot.py`，并尽量等旧下载任务结束，以免两个程序争夺 Telegram Long Polling 更新。

克隆仓库并安装（如已有第一版正在运行，请先停止旧进程）：

```bash
git clone https://github.com/Jankinsu/tdl-bot.git ~/tdl-bot
```

随后执行：

```bash
install -Dm 755 ~/tdl-bot/bot.py ~/apps/tdl-bot/bot.py
install -Dm 644 ~/tdl-bot/tdl-bot.service ~/.config/systemd/user/tdl-bot.service
test -e ~/.config/tdl-bot/settings.env || install -m 600 ~/tdl-bot/settings.env.example ~/.config/tdl-bot/settings.env
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
