#!/usr/bin/env python3
"""Personal Telegram-to-tdl download bot (Python stdlib only).

Run under the same Linux user that successfully ran `tdl login`.
Configuration: ~/.config/tdl-bot/{token,allowed_user_id,settings.env}
Persistent queue: ~/.local/state/tdl-bot/jobs.sqlite3
"""

import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

os.umask(0o077)
HOME = Path.home()
CONFIG_DIR = HOME / ".config/tdl-bot"
STATE_DIR = HOME / ".local/state/tdl-bot"
STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
DB_PATH = STATE_DIR / "jobs.sqlite3"
LOG_PATH = STATE_DIR / "downloads.log"
OLD_OFFSET_PATH = STATE_DIR / "offset"

TOKEN = (CONFIG_DIR / "token").read_text(encoding="utf-8").strip()
OWNER_ID = int((CONFIG_DIR / "allowed_user_id").read_text(encoding="utf-8").strip())
BOT_API = f"https://api.telegram.org/bot{TOKEN}/"
API_PROXY = os.getenv("TDL_BOT_API_PROXY", "http://127.0.0.1:2080")
TDL_PROXY = os.getenv("TDL_PROXY", "socks5://127.0.0.1:2080")
TDL_BIN = os.getenv("TDL_BOT_TDL_BIN", "/usr/local/bin/tdl")
MEDIA_DIR = Path(os.getenv("TDL_BOT_MEDIA_DIR", str(HOME / "Downloads/telegram"))).expanduser()
MOUNT_SETTING = os.getenv("TDL_BOT_REQUIRED_MOUNT", "").strip()
REQUIRED_MOUNT = Path(MOUNT_SETTING).expanduser() if MOUNT_SETTING else None
MAX_PENDING = 20
MIN_FREE_BYTES = 2 * 1024**3  # Leave at least 2 GiB free at task start.
STOP = threading.Event()
LINK_RE = re.compile(r"https://(?:www\.)?t\.me/[^\s<>\"']+", re.IGNORECASE)

if not MEDIA_DIR.is_absolute():
    raise SystemExit("TDL_BOT_MEDIA_DIR must be an absolute path")
if REQUIRED_MOUNT is not None:
    if not REQUIRED_MOUNT.is_absolute() or str(REQUIRED_MOUNT) == "/":
        raise SystemExit("TDL_BOT_REQUIRED_MOUNT must be an absolute non-root directory")
    if not MEDIA_DIR.resolve().is_relative_to(REQUIRED_MOUNT.resolve()):
        raise SystemExit("Download directory must be inside TDL_BOT_REQUIRED_MOUNT")

OPENER = build_opener(ProxyHandler({"https": API_PROXY}))


def now():
    return int(time.time())


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_id INTEGER NOT NULL UNIQUE,
            url TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            finished_at INTEGER,
            error TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs(status, id)")
        if conn.execute("SELECT 1 FROM state WHERE key='offset'").fetchone() is None:
            # Preserve the offset from the previously running MVP so old updates aren't replayed.
            offset = 0
            try:
                offset = int(OLD_OFFSET_PATH.read_text().strip())
            except (OSError, ValueError):
                pass
            conn.execute("INSERT INTO state(key, value) VALUES('offset', ?)", (str(offset),))
        # A job interrupted by reboot/service restart can be attempted again.
        count = conn.execute("UPDATE jobs SET status='queued', started_at=NULL WHERE status='running'").rowcount
        if count:
            print(f"Recovered {count} interrupted job(s)", flush=True)


def offset_value():
    with db() as conn:
        return int(conn.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0])


def api(method, **params):
    req = Request(BOT_API + method, data=urlencode(params).encode("utf-8"))
    with OPENER.open(req, timeout=40) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError(f"Bot API {method} returned ok=false")
    return payload["result"]


def notify(chat_id, text):
    try:
        api("sendMessage", chat_id=chat_id, text=text)
    except Exception as exc:
        # The underlying exception/URL could contain the secret Bot Token.
        print(f"Notification error: {type(exc).__name__}", flush=True)


def extract_link(text):
    match = LINK_RE.search(text)
    if not match:
        return None
    candidate = match.group().rstrip(".,;)]}，。；）")
    if len(candidate) > 500:
        return None
    parsed = urlsplit(candidate)
    if (parsed.scheme != "https" or parsed.hostname not in ("t.me", "www.t.me")
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None):
        return None
    # Public-channel or private-channel message URLs, optionally with a topic ID.
    if not re.fullmatch(r"/(?:c/\d+|[A-Za-z0-9_]{5,32})/\d+(?:/\d+){0,2}/?", parsed.path):
        return None
    return candidate


def job_summary(conn):
    summary = {row["status"]: row["n"] for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    running = conn.execute("SELECT id FROM jobs WHERE status='running' ORDER BY id LIMIT 1").fetchone()
    return summary, running["id"] if running else None


def storage_status():
    """Never create the destination under an unmounted disk."""
    if REQUIRED_MOUNT is not None and not REQUIRED_MOUNT.is_mount():
        return False, f"移动硬盘未挂载：{REQUIRED_MOUNT}"
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        if not os.access(MEDIA_DIR, os.W_OK):
            return False, f"下载目录没有写入权限：{MEDIA_DIR}"
        space = shutil.disk_usage(MEDIA_DIR)
        if space.free < MIN_FREE_BYTES:
            return False, "可用磁盘空间不足 2 GiB"
    except OSError as exc:
        return False, f"下载目录不可用（{type(exc).__name__}）"
    return True, "已就绪"


def process_update(update):
    """Atomically record both the Telegram offset and any newly queued job."""
    update_id = int(update["update_id"])
    msg = update.get("message") or {}
    sender = msg.get("from") or {}
    chat = msg.get("chat") or {}
    allowed = (sender.get("id") == OWNER_ID and chat.get("type") == "private"
               and chat.get("id") == OWNER_ID)
    result = None
    with db() as conn:
        old_offset = int(conn.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0])
        if update_id < old_offset:
            return
        if allowed:
            text = (msg.get("text") or "").strip()
            if text in ("/start", "/help"):
                result = "👋 下载助手在线。发送 Telegram 消息链接即可排队。\n/status 查看任务。"
            elif text == "/status":
                counts, running_id = job_summary(conn)
                is_ready, reason = storage_status()
                result = (f"📋 正在下载：{('#' + str(running_id)) if running_id else '无'}\n"
                          f"等待：{counts.get('queued', 0)}，完成：{counts.get('done', 0)}，失败：{counts.get('failed', 0)}\n"
                          f"💾 存储：{'正常' if is_ready else reason}\n目录：{MEDIA_DIR}")
            else:
                link = extract_link(text)
                if not link:
                    result = "请发送 Telegram 消息链接，例如 https://t.me/telegram/193"
                else:
                    existing = conn.execute(
                        "SELECT id FROM jobs WHERE url=? AND status IN ('queued','running') LIMIT 1",
                        (link,)).fetchone()
                    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                    if existing:
                        result = f"📌 相同链接已在队列中（任务 #{existing['id']}）"
                    elif count >= MAX_PENDING:
                        result = "⚠️ 当前等待和下载任务已达到上限 20 个，请稍后再试。"
                    else:
                        cursor = conn.execute(
                            "INSERT INTO jobs(update_id,url,chat_id,status,created_at) VALUES(?,?,?,'queued',?)",
                            (update_id, link, OWNER_ID, now()))
                        result = f"📥 已加入下载队列：任务 #{cursor.lastrowid}"
        conn.execute("UPDATE state SET value=? WHERE key='offset'", (str(update_id + 1),))
    if allowed and result:
        notify(OWNER_ID, result)


def worker():
    storage_notified = False
    while not STOP.is_set():
        with db() as conn:
            row = conn.execute("SELECT id,url,chat_id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            storage_notified = False
            STOP.wait(2)
            continue
        ok, reason = storage_status()
        if not ok:
            if not storage_notified:
                print(f"Storage waiting: {reason}", flush=True)
                notify(OWNER_ID, f"⏸ 下载暂停，队列已保存。{reason}\n挂载恢复后自动继续。")
                storage_notified = True
            STOP.wait(30)
            continue
        storage_notified = False
        job_id, link, chat_id = row["id"], row["url"], row["chat_id"]
        with db() as conn:
            claim = conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='queued'",
                                 (now(), job_id)).rowcount
        if not claim:
            continue
        notify(chat_id, f"⬇️ 开始下载任务 #{job_id}")
        returncode = None
        error = None
        try:
            env = os.environ.copy()
            env["TDL_PROXY"] = TDL_PROXY
            with LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(f"\n=== {time.strftime('%F %T')} task #{job_id} {link} ===\n")
                log.flush()
                result = subprocess.run(
                    [TDL_BIN, "dl", "-u", link, "-d", str(MEDIA_DIR), "--skip-same",
                     "--disable-progress-ps"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env=env, check=False,
                )
            returncode = result.returncode
        except Exception as exc:
            error = type(exc).__name__
            print(f"Download error for job #{job_id}: {error}", flush=True)
        success = returncode == 0
        with db() as conn:
            conn.execute("UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=?",
                         ("done" if success else "failed", now(), None if success else (error or f"exit {returncode}"), job_id))
        if success:
            notify(chat_id, f"✅ 下载完成（或文件已存在）！任务 #{job_id}\n保存目录：{MEDIA_DIR}")
        else:
            notify(chat_id, f"❌ 下载失败：任务 #{job_id}（{error or ('退出码 ' + str(returncode))}）。请检查下载日志。")


def request_stop(_signum, _frame):
    STOP.set()


def main():
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    init_db()
    if not Path(TDL_BIN).is_file():
        raise SystemExit(f"tdl executable not found: {TDL_BIN}")
    threading.Thread(target=worker, name="download-worker", daemon=True).start()
    print(f"tdl-bot started; media={MEDIA_DIR}; required_mount={REQUIRED_MOUNT or '(none)'}", flush=True)
    while not STOP.is_set():
        try:
            updates = api("getUpdates", offset=offset_value(), timeout=25,
                          allowed_updates=json.dumps(["message"]))
            for update in updates:
                if STOP.is_set():
                    break
                process_update(update)
        except Exception as exc:
            print(f"Polling error: {type(exc).__name__}; retry in 5s", flush=True)
            STOP.wait(5)
    print("tdl-bot stopped", flush=True)


if __name__ == "__main__":
    main()