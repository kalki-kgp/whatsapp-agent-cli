#!/usr/bin/env python3
import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import tempfile
import time
from collections import deque
from datetime import UTC, datetime, time as datetime_time
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp


LOG = logging.getLogger("codex_whatsapp")
PYPI_PROJECT_URL = "https://pypi.org/pypi/whatsapp-agent-cli/json"
DEFAULT_MEMORY_FILES = ["user.md", "career.md", "projects.md", "preferences.md", "open-loops.md"]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _version_numbers(value: str) -> list[int]:
    head = (value or "").strip().lower().split("+", 1)[0].split("-", 1)[0]
    numbers: list[int] = []
    for part in head.replace("_", ".").split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if digits:
            numbers.append(int(digits))
    return numbers


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _version_numbers(latest)
    current_parts = _version_numbers(current)
    if not latest_parts or not current_parts:
        return False
    width = max(len(latest_parts), len(current_parts))
    latest_parts.extend([0] * (width - len(latest_parts)))
    current_parts.extend([0] * (width - len(current_parts)))
    return latest_parts > current_parts


def parse_daily_time(value: str) -> datetime_time:
    raw = (value or "04:00").strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime_time(hour=hour, minute=minute)
    except Exception:
        pass
    LOG.warning("Invalid AGENT_MEMORY_ROLLOVER_TIME=%r, using 04:00", value)
    return datetime_time(hour=4, minute=0)


def build_upgrade_prompt(current: str, latest: str, command: str) -> str:
    return (
        "The WhatsApp operator approved upgrading this whatsapp-agent-cli runtime.\n"
        f"Current installed version: {current or '(unknown)'}\n"
        f"Latest available version: {latest}\n"
        "\n"
        "Run the upgrade command below, verify the result, and keep the reply concise.\n"
        "If restarting the service interrupts this chat response, that is acceptable.\n"
        "\n"
        f"Command:\n{command}\n"
    )


class Config:
    def __init__(self) -> None:
        self.home = Path(_env_first("AGENT_WHATSAPP_HOME", "CW_HOME", default="~/.agent-whatsapp")).expanduser()
        self.bridge_port = int(_env_first("WHATSAPP_PORT", default="3010"))
        self.bridge_dir = Path(
            os.getenv("WHATSAPP_BRIDGE_DIR", str(self.home / "bridge"))
        ).expanduser()
        self.bridge_script = self.bridge_dir / "bridge.js"
        self.session_dir = Path(
            os.getenv("WHATSAPP_SESSION_DIR", str(self.home / "whatsapp" / "session"))
        ).expanduser()
        self.state_file = Path(
            os.getenv("CW_STATE_FILE", str(self.home / "state.json"))
        ).expanduser()
        self.backend = _env_first("AGENT_BACKEND", default="codex").lower()
        self.agent_command = _env_first(
            "AGENT_COMMAND",
            "CODEX_BIN" if self.backend == "codex" else "CLAUDE_BIN",
            default="codex" if self.backend == "codex" else "claude",
        )
        self.default_root = Path(
            _env_first("AGENT_ROOT", "CODEX_ROOT", "CLAUDE_ROOT", default="~")
        ).expanduser().resolve()
        self.model = _env_first("AGENT_MODEL", "CODEX_MODEL", "CLAUDE_MODEL", default="")
        self.enable_search = _env_flag(
            "AGENT_SEARCH",
            default=_env_flag("CODEX_SEARCH", default=False),
        )
        self.reply_prefix = os.getenv("WHATSAPP_REPLY_PREFIX", "")
        self.allowed_users = os.getenv("WHATSAPP_ALLOWED_USERS", "").strip()
        self.mode = os.getenv("WHATSAPP_MODE", "bot").strip() or "bot"
        self.max_reply_chars = int(os.getenv("CW_MAX_REPLY_CHARS", "3500"))
        self.send_retry_seconds = float(os.getenv("CW_SEND_RETRY_SECONDS", "60"))
        self.send_retry_interval = float(os.getenv("CW_SEND_RETRY_INTERVAL", "2"))
        self.logs_dir = self.home / "logs"
        self.bridge_log = self.logs_dir / "bridge.log"
        self.processed_limit = int(os.getenv("CW_PROCESSED_LIMIT", "2000"))
        self.typing_interval = float(os.getenv("CW_TYPING_INTERVAL", "8"))
        self.package_version = _env_first("AGENT_PACKAGE_VERSION", default="")
        self.upgrade_check = _env_flag("AGENT_UPGRADE_CHECK", default=True)
        self.upgrade_check_interval = float(os.getenv("AGENT_UPGRADE_CHECK_INTERVAL", "3600"))
        self.service_name = _env_first("SERVICE_NAME", default="agent-whatsapp")
        self.memory_enabled = _env_flag("AGENT_MEMORY_ENABLED", default=True)
        self.memory_dir = Path(
            _env_first("AGENT_MEMORY_DIR", default=str(self.home / "memory"))
        ).expanduser()
        self.memory_rollover_time_text = _env_first("AGENT_MEMORY_ROLLOVER_TIME", default="04:00")
        self.memory_rollover_time = parse_daily_time(self.memory_rollover_time_text)
        self.memory_check_interval = float(os.getenv("AGENT_MEMORY_CHECK_INTERVAL", "60"))
        self.memory_files = _env_list("AGENT_MEMORY_FILES", DEFAULT_MEMORY_FILES)
        self.transcribe_audio = _env_flag("AGENT_TRANSCRIBE_AUDIO", default=False)
        self.whisper_model = _env_first("AGENT_WHISPER_MODEL", default="base")
        self.whisper_device = _env_first("AGENT_WHISPER_DEVICE", default="cpu")
        self.whisper_compute_type = _env_first("AGENT_WHISPER_COMPUTE_TYPE", default="int8")
        self.whisper_language = _env_first("AGENT_WHISPER_LANGUAGE", default="")
        self.whisper_beam_size = int(os.getenv("AGENT_WHISPER_BEAM_SIZE", "5"))

    def ensure_dirs(self) -> None:
        paths = [self.home, self.logs_dir, self.session_dir.parent]
        if self.memory_enabled:
            paths.append(self.memory_dir)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        self.default_root.mkdir(parents=True, exist_ok=True)


class StateStore:
    def __init__(self, path: Path, default_root: Path) -> None:
        self.path = path
        self.default_root = str(default_root)
        self.data: dict[str, Any] = {"chats": {}, "processed_ids": []}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            LOG.exception("Failed to load state file %s", self.path)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def chat(self, chat_id: str) -> dict[str, Any]:
        chats = self.data.setdefault("chats", {})
        chat_state = chats.setdefault(chat_id, {})
        chat_state.setdefault("root", self.default_root)
        chat_state.setdefault("saved_sessions", [])
        return chat_state

    def mark_processed(self, message_id: str, limit: int) -> None:
        ids: list[str] = self.data.setdefault("processed_ids", [])
        ids.append(message_id)
        if len(ids) > limit:
            del ids[:-limit]

    def has_processed(self, message_id: str) -> bool:
        ids = self.data.setdefault("processed_ids", [])
        return message_id in ids


def split_message(text: str, limit: int) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return ["Done."]
    if len(cleaned) <= limit:
        return [cleaned]

    parts: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < max(0, limit // 2):
            cut = remaining.rfind(" ", 0, limit)
        if cut < max(0, limit // 3):
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return [part for part in parts if part]


def build_prompt(event: dict[str, Any], root: str) -> tuple[str, list[str]]:
    body = (event.get("body") or "").strip()
    transcription_text = (event.get("transcriptionText") or "").strip()
    transcription_error = (event.get("transcriptionError") or "").strip()
    media_urls = event.get("mediaUrls") or []
    media_type = event.get("mediaType") or ""
    image_args: list[str] = []
    media_lines: list[str] = []
    for media_path in media_urls:
        path = Path(media_path)
        if media_type == "image" and path.exists():
            image_args.extend(["-i", str(path)])
        media_lines.append(f"- {media_type or 'file'}: {path}")

    user_message = body or "[no text]"
    if transcription_text:
        if not body or body in {"[audio received]", "[ptt received]"}:
            user_message = "[voice message transcription]\n" + transcription_text
        else:
            user_message = body + "\n\nVoice message transcription:\n" + transcription_text
    elif transcription_error:
        user_message = user_message + "\n\nVoice transcription failed:\n" + transcription_error

    prompt = (
        "You are replying to a WhatsApp user through a server-side CLI coding agent gateway.\n"
        "Keep responses concise, direct, and practical.\n"
        "If you make changes or run commands, summarize only the important outcome.\n"
        f"Current workspace root: {root}\n"
        f"WhatsApp chat id: {event.get('chatId')}\n"
        f"Sender: {event.get('senderName') or event.get('senderId')}\n"
        "\n"
        "Gateway admin commands like /reset and /root are handled outside of the agent.\n"
        "Treat the content below as the user's actual message.\n"
        "\n"
        "User message:\n"
        f"{user_message}\n"
    )
    if media_lines:
        prompt += "\nAttached/cached media:\n" + "\n".join(media_lines) + "\n"
    return prompt, image_args


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def local_now() -> datetime:
    return datetime.now().astimezone()


def default_session_name() -> str:
    return local_now().strftime("Session %Y-%m-%d %H:%M")


def chat_memory_slug(chat_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", chat_id).strip("-")[:48]
    digest = hashlib.sha1(chat_id.encode("utf-8")).hexdigest()[:10]
    if cleaned:
        return f"{cleaned}-{digest}"
    return digest


def memory_file_description(filename: str) -> str:
    descriptions = {
        "user.md": "stable details about the WhatsApp operator",
        "career.md": "career history, goals, preferences, and constraints",
        "projects.md": "active projects, repos, decisions, and status",
        "preferences.md": "communication, coding, tooling, and workflow preferences",
        "open-loops.md": "unresolved tasks, follow-ups, and promises",
    }
    return descriptions.get(filename, "topic-specific long-term memory")


def markdown_title(filename: str) -> str:
    stem = Path(filename).stem.replace("-", " ").replace("_", " ").strip()
    return stem.title() if stem else "Memory"


def ensure_memory_scaffold(memory_dir: Path, memory_files: list[str]) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "sessions").mkdir(parents=True, exist_ok=True)

    for filename in memory_files:
        path = memory_dir / filename
        if not path.exists():
            path.write_text(f"# {markdown_title(filename)}\n\n", encoding="utf-8")

    index_path = memory_dir / "MEMORY.md"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8", errors="replace")
    else:
        content = (
            "# Memory Index\n\n"
            "Long-term memory for this WhatsApp chat. Keep this file as the map; "
            "put details in topic files.\n\n"
            "## Core Memory Files\n\n"
        )

    changed = False
    if "## Core Memory Files" not in content:
        content = content.rstrip() + "\n\n## Core Memory Files\n\n"
        changed = True
    for filename in memory_files:
        marker = f"]({filename})"
        if marker not in content:
            content = (
                content.rstrip()
                + f"\n- [{filename}]({filename}) - {memory_file_description(filename)}\n"
            )
            changed = True
    if "## Session History" not in content:
        content = content.rstrip() + "\n\n## Session History\n\n"
        changed = True
    if changed or not index_path.exists():
        index_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return index_path


def read_memory_index(index_path: Path, limit: int = 6000) -> str:
    try:
        text = index_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[Memory index truncated.]"


def build_carry_forward_summary(update_reply: str, session_id: str, session_file: Path) -> str:
    summary = update_reply.strip() or "Memory rollover completed."
    return (
        "Carry-forward context for this WhatsApp agent session.\n"
        f"Session id: {session_id or '(none)'}\n"
        f"Session memory record: {session_file}\n"
        "\n"
        "Use this as the compacted context for continued work. Read the memory index "
        "or session record only when more detail is needed.\n"
        "\n"
        f"{summary}"
    )


def archive_snapshot(chat_state: dict[str, Any], *, force: bool = False) -> bool:
    thread_id = (chat_state.get("thread_id") or "").strip()
    summary = (chat_state.get("summary") or "").strip()
    title = (chat_state.get("title") or "").strip() or default_session_name()
    root = (chat_state.get("root") or "").strip()
    model = (chat_state.get("model") or "").strip()
    if not force and not thread_id and not summary:
        return False

    saved = chat_state.setdefault("saved_sessions", [])
    snapshot = {
        "name": title,
        "thread_id": thread_id,
        "root": root,
        "model": model,
        "summary": summary,
        "saved_at": now_iso(),
        "session_started_at": chat_state.get("session_started_at") or "",
    }

    updated = False
    if thread_id:
        for idx, entry in enumerate(saved):
            if entry.get("thread_id") == thread_id:
                saved[idx] = snapshot
                updated = True
                break
    if not updated:
        saved.insert(0, snapshot)
    if len(saved) > 30:
        del saved[30:]
    return True


def clear_active_session(chat_state: dict[str, Any], *, keep_summary: bool = False) -> None:
    chat_state.pop("thread_id", None)
    chat_state.pop("title", None)
    chat_state.pop("session_started_at", None)
    if not keep_summary:
        chat_state.pop("summary", None)


def format_saved_sessions(chat_state: dict[str, Any]) -> str:
    saved = chat_state.get("saved_sessions") or []
    active_id = (chat_state.get("thread_id") or "").strip()
    active_title = (chat_state.get("title") or "").strip()
    active_summary = (chat_state.get("summary") or "").strip()
    if not saved and not active_id and not active_title and not active_summary:
        return "No saved sessions yet."
    lines = ["Available sessions:"]
    if active_id or active_title or active_summary:
        name = active_title or "(untitled)"
        thread_id = active_id or "(no id yet)"
        model = chat_state.get("model") or "(default)"
        root = chat_state.get("root") or "(root unset)"
        started = str(chat_state.get("session_started_at") or "")[:16].replace("T", " ")
        suffix = f" | started={started}" if started else ""
        lines.append(f"- current: {name} | id={thread_id} | model={model} | root={root}{suffix}")
    for entry in saved:
        if active_id and entry.get("thread_id") == active_id:
            continue
        name = entry.get("name") or "(untitled)"
        thread_id = entry.get("thread_id") or "(no id)"
        model = entry.get("model") or "(default)"
        root = entry.get("root") or "(root unset)"
        saved_at = entry.get("saved_at", "")[:16].replace("T", " ")
        lines.append(f"- {name} | id={thread_id} | model={model} | root={root} | {saved_at}")
    lines.append("Use `/resume <name>` or `/resume <id>` to switch back.")
    lines.append("Use `/search-session <query>` when you only remember part of the work.")
    return "\n".join(lines)


def resolve_saved_session(chat_state: dict[str, Any], query: str) -> dict[str, Any] | None:
    saved = chat_state.get("saved_sessions") or []
    needle = query.strip().lower()
    if not needle:
        return None
    for entry in saved:
        if (entry.get("name") or "").lower() == needle:
            return entry
    for entry in saved:
        if needle in (entry.get("name") or "").lower():
            return entry
    for entry in saved:
        if needle == (entry.get("thread_id") or "").lower():
            return entry
    return None


def session_search_blob(entry: dict[str, Any]) -> str:
    fields = [
        entry.get("name") or "",
        entry.get("thread_id") or "",
        entry.get("root") or "",
        entry.get("model") or "",
        entry.get("summary") or "",
        entry.get("session_started_at") or "",
        entry.get("saved_at") or "",
    ]
    return "\n".join(str(field) for field in fields if field)


def score_saved_session(entry: dict[str, Any], query: str) -> int:
    needle = query.strip().lower()
    if not needle:
        return 0
    name = str(entry.get("name") or "").lower()
    thread_id = str(entry.get("thread_id") or "").lower()
    root = str(entry.get("root") or "").lower()
    summary = str(entry.get("summary") or "").lower()
    blob = session_search_blob(entry).lower()
    tokens = [token for token in re.split(r"[^a-z0-9_.:/-]+", needle) if token]

    score = 0
    if name == needle:
        score += 200
    if thread_id == needle:
        score += 180
    if needle in name:
        score += 90
    if needle in thread_id:
        score += 80
    if needle in root:
        score += 35
    if needle in summary:
        score += 25
    for token in tokens:
        if token in name:
            score += 25
        if token in root:
            score += 12
        if token in summary:
            score += 8
        if token in blob:
            score += 3
    return score


def search_saved_sessions(
    chat_state: dict[str, Any], query: str, *, limit: int = 5
) -> list[tuple[int, dict[str, Any]]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in chat_state.get("saved_sessions") or []:
        score = score_saved_session(entry, query)
        if score > 0:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (item[0], item[1].get("saved_at") or ""), reverse=True)
    return ranked[:limit]


def format_chat_help() -> str:
    return (
        "whatsapp-agent chat commands\n\n"
        "Session\n"
        "/status - show backend, root, model, active session id, memory, and saved count\n"
        "/title <name> - name the current session; this name appears in /resume\n"
        "/resume - list the current session plus saved sessions\n"
        "/resume <name-or-id> - switch back to a saved session\n"
        "/search-session <query> - find and resume the best matching saved session\n"
        "/new - archive the current session and start a fresh one\n"
        "/reset - clear the live session immediately\n\n"
        "Workspace\n"
        "/root /absolute/path - change the repo or working directory for this chat\n"
        "/model <name> - change the model for future turns without clearing this session\n"
        "/model - show the current model\n\n"
        "Memory\n"
        "/compact - write a carry-forward summary while keeping the same session id\n"
        "/memory - show this chat's memory files and rollover state\n"
        "/memory update - update memory files and compact this session now\n\n"
        "Approvals\n"
        "/yes - approve a pending gateway action, like an upgrade\n"
        "/no - dismiss a pending gateway action\n\n"
        "Normal messages go straight to the agent. Each WhatsApp chat keeps its own root, "
        "model, session id, title, and memory."
    )


class WhatsAppAgentGateway:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = StateStore(config.state_file, config.default_root)
        self.http: aiohttp.ClientSession | None = None
        self.bridge_process: asyncio.subprocess.Process | None = None
        self.bridge_log_handle = None
        self.stop_event = asyncio.Event()
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.typing_tasks: dict[str, asyncio.Task[Any]] = {}
        self.runtime_seen: deque[str] = deque(maxlen=config.processed_limit)
        self.runtime_seen_set: set[str] = set()
        self.latest_package_version = ""
        self.upgrade_notice = ""
        self.last_upgrade_check = 0.0
        self.memory_task: asyncio.Task[Any] | None = None
        self.whisper_model: Any | None = None
        self.whisper_lock = asyncio.Lock()

    def upgrade_command(self) -> str:
        install_dir = shlex.quote(str(self.config.home))
        service = shlex.quote(f"{self.config.service_name}.service")
        return (
            "uvx --upgrade --from whatsapp-agent-cli whatsapp-agent "
            f"--install-dir {install_dir} install --reconfigure && "
            f"systemctl --user restart {service}"
        )

    async def run(self) -> None:
        self.config.ensure_dirs()
        self.http = aiohttp.ClientSession()
        await self.start_bridge()
        if self.config.memory_enabled:
            self.memory_task = asyncio.create_task(self.memory_rollover_loop())
        try:
            await self.poll_loop()
        finally:
            if self.memory_task:
                self.memory_task.cancel()
                try:
                    await self.memory_task
                except asyncio.CancelledError:
                    pass

    async def shutdown(self) -> None:
        self.stop_event.set()
        if self.memory_task:
            self.memory_task.cancel()
        for task in list(self.typing_tasks.values()):
            task.cancel()
        if self.http and not self.http.closed:
            await self.http.close()
        if self.bridge_process and self.bridge_process.returncode is None:
            self.bridge_process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.bridge_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.bridge_process.kill()
                await self.bridge_process.wait()
        if self.bridge_log_handle:
            self.bridge_log_handle.close()
            self.bridge_log_handle = None

    async def start_bridge(self) -> None:
        await self._kill_port_process(self.config.bridge_port)
        bridge_env = os.environ.copy()
        bridge_env["WHATSAPP_MODE"] = self.config.mode
        bridge_env["WHATSAPP_ALLOWED_USERS"] = self.config.allowed_users
        bridge_env["WHATSAPP_REPLY_PREFIX"] = self.config.reply_prefix
        log_handle = self.config.bridge_log.open("a")
        self.bridge_log_handle = log_handle
        self.bridge_process = await asyncio.create_subprocess_exec(
            "node",
            str(self.config.bridge_script),
            "--port",
            str(self.config.bridge_port),
            "--session",
            str(self.config.session_dir),
            "--mode",
            self.config.mode,
            stdout=log_handle,
            stderr=log_handle,
            cwd=str(self.config.bridge_dir),
            env=bridge_env,
        )
        await self.wait_for_bridge()

    async def wait_for_bridge(self) -> None:
        assert self.http is not None
        url = f"http://127.0.0.1:{self.config.bridge_port}/health"
        for _ in range(30):
            if self.bridge_process and self.bridge_process.returncode is not None:
                raise RuntimeError(
                    f"WhatsApp bridge exited with code {self.bridge_process.returncode}"
                )
            try:
                async with self.http.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(1)
        raise RuntimeError("WhatsApp bridge did not become healthy in time")

    async def poll_loop(self) -> None:
        assert self.http is not None
        url = f"http://127.0.0.1:{self.config.bridge_port}/messages"
        while not self.stop_event.is_set():
            try:
                async with self.http.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(2)
                        continue
                    payload = await resp.json()
                for event in payload:
                    await self.handle_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Polling loop error")
                await asyncio.sleep(2)

    async def handle_event(self, event: dict[str, Any]) -> None:
        message_id = str(event.get("messageId") or "")
        if not message_id:
            return
        if message_id in self.runtime_seen_set or self.state.has_processed(message_id):
            return
        self.runtime_seen.append(message_id)
        self.runtime_seen_set.add(message_id)
        while len(self.runtime_seen_set) > self.config.processed_limit:
            dropped = self.runtime_seen.popleft()
            self.runtime_seen_set.discard(dropped)

        chat_id = str(event.get("chatId") or "")
        if not chat_id:
            return

        lock = self.chat_locks.setdefault(chat_id, asyncio.Lock())
        asyncio.create_task(self._handle_event_locked(lock, event, message_id))

    async def _handle_event_locked(
        self, lock: asyncio.Lock, event: dict[str, Any], message_id: str
    ) -> None:
        async with lock:
            try:
                await self.process_message(event)
                self.state.mark_processed(message_id, self.config.processed_limit)
                self.state.save()
            except Exception:
                LOG.exception("Failed to process message %s", message_id)
                try:
                    await self.send_message(
                        event["chatId"],
                        "That run blew up on the server. Check the gateway logs and try again.",
                    )
                except Exception:
                    LOG.exception("Also failed to send the error message for %s", message_id)

    async def process_message(self, event: dict[str, Any]) -> None:
        chat_id = str(event["chatId"])
        body = (event.get("body") or "").strip()
        LOG.info(
            "Incoming message chat=%s sender=%s media=%s body=%s",
            chat_id,
            event.get("senderName") or event.get("senderId") or "(unknown)",
            event.get("mediaType") or "text",
            body[:300].replace("\n", " "),
        )
        if body.startswith("/"):
            handled = await self.handle_gateway_command(chat_id, body)
            if handled:
                return

        chat_state = self.state.chat(chat_id)
        root = chat_state.get("root", self.state.default_root)
        if self.config.transcribe_audio:
            await self.attach_audio_transcription(event)
        await self.start_typing(chat_id)
        try:
            reply, thread_id = await self.run_agent(event, chat_state, root)
        finally:
            await self.stop_typing(chat_id)

        if thread_id and chat_state.get("thread_id") != thread_id:
            chat_state["thread_id"] = thread_id
            chat_state.setdefault("session_started_at", now_iso())
            self.state.save()
        elif thread_id and not chat_state.get("session_started_at"):
            chat_state["session_started_at"] = now_iso()
            self.state.save()

        reply = await self.add_upgrade_notice(reply, chat_state)
        for chunk in split_message(reply, self.config.max_reply_chars):
            LOG.info("Sending reply chat=%s body=%s", chat_id, chunk[:300].replace("\n", " "))
            await self.send_message(chat_id, chunk)

    def audio_paths_from_event(self, event: dict[str, Any]) -> list[Path]:
        media_type = str(event.get("mediaType") or "").lower()
        if media_type not in {"audio", "ptt"}:
            return []
        paths: list[Path] = []
        for media_path in event.get("mediaUrls") or []:
            path = Path(str(media_path))
            if path.exists():
                paths.append(path)
        return paths

    async def attach_audio_transcription(self, event: dict[str, Any]) -> None:
        paths = self.audio_paths_from_event(event)
        if not paths:
            return
        transcripts: list[str] = []
        errors: list[str] = []
        for path in paths:
            try:
                transcript = await self.transcribe_audio_file(path)
                if transcript:
                    transcripts.append(transcript)
            except Exception as exc:
                LOG.exception("Audio transcription failed for %s", path)
                errors.append(f"{path.name}: {exc}")
        if transcripts:
            event["transcriptionText"] = "\n\n".join(transcripts)
            LOG.info(
                "Transcribed audio chat=%s text=%s",
                event.get("chatId"),
                event["transcriptionText"][:300].replace("\n", " "),
            )
        if errors:
            event["transcriptionError"] = "\n".join(errors)

    async def transcribe_audio_file(self, path: Path) -> str:
        async with self.whisper_lock:
            return await asyncio.to_thread(self._transcribe_audio_file_sync, path)

    def _transcribe_audio_file_sync(self, path: Path) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Re-run `whatsapp-agent install --reconfigure` "
                "and enable voice transcription."
            ) from exc

        if self.whisper_model is None:
            LOG.info(
                "Loading faster-whisper model=%s device=%s compute_type=%s",
                self.config.whisper_model,
                self.config.whisper_device,
                self.config.whisper_compute_type,
            )
            self.whisper_model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
            )

        language = self.config.whisper_language or None
        segments, info = self.whisper_model.transcribe(
            str(path),
            language=language,
            beam_size=self.config.whisper_beam_size,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        detected = getattr(info, "language", "") or ""
        probability = getattr(info, "language_probability", 0.0) or 0.0
        if detected:
            LOG.info("Detected audio language=%s probability=%.2f file=%s", detected, probability, path)
        return text.strip()

    async def add_upgrade_notice(self, reply: str, chat_state: dict[str, Any]) -> str:
        await self.refresh_upgrade_notice()
        notice = self.build_upgrade_notice(chat_state)
        if not notice:
            return reply
        self.state.save()
        return f"{reply.rstrip()}\n\n{notice}"

    def build_upgrade_notice(self, chat_state: dict[str, Any]) -> str:
        latest = self.latest_package_version
        current = self.config.package_version
        if not latest or not is_newer_version(latest, current):
            chat_state.pop("pending_upgrade", None)
            return ""
        if chat_state.get("dismissed_upgrade_version") == latest:
            chat_state.pop("pending_upgrade", None)
            return ""

        pending = {
            "type": "upgrade",
            "from_version": current,
            "to_version": latest,
            "command": self.upgrade_command(),
            "created_at": now_iso(),
        }
        chat_state["pending_upgrade"] = pending
        return (
            f"Upgrade available: whatsapp-agent-cli {current} -> {latest}.\n"
            "Reply /yes to approve the upgrade, or /no to dismiss this version.\n"
            f"I will ask the agent to run: {pending['command']}"
        )

    async def refresh_upgrade_notice(self) -> None:
        if not self.config.upgrade_check or not self.config.package_version:
            return
        now = time.monotonic()
        if now - self.last_upgrade_check < self.config.upgrade_check_interval:
            return
        self.last_upgrade_check = now

        assert self.http is not None
        try:
            async with self.http.get(
                PYPI_PROJECT_URL,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
            latest = str((payload.get("info") or {}).get("version") or "").strip()
        except Exception:
            LOG.debug("Failed to check whatsapp-agent-cli version", exc_info=True)
            return

        self.latest_package_version = latest
        if is_newer_version(latest, self.config.package_version):
            self.upgrade_notice = (
                f"Upgrade available: whatsapp-agent-cli {self.config.package_version} -> {latest}.\n"
                "Reply /yes to approve the upgrade, or /no to dismiss this version."
            )
        else:
            self.upgrade_notice = ""

    def memory_dir_for_chat(self, chat_id: str) -> Path:
        return self.config.memory_dir / chat_memory_slug(chat_id)

    def ensure_chat_memory(self, chat_id: str) -> Path:
        return ensure_memory_scaffold(self.memory_dir_for_chat(chat_id), self.config.memory_files)

    def build_memory_context(self, chat_id: str, chat_state: dict[str, Any]) -> str:
        if not self.config.memory_enabled:
            return ""
        index_path = self.ensure_chat_memory(chat_id)
        memory_dir = index_path.parent
        previous_sessions = chat_state.get("saved_sessions") or []
        session_lines = []
        for entry in previous_sessions[:5]:
            session_id = entry.get("thread_id") or "(no id)"
            name = entry.get("name") or "(untitled)"
            saved_at = (entry.get("saved_at") or "")[:16].replace("T", " ")
            session_lines.append(f"- {name} | id={session_id} | saved={saved_at}")
        session_text = "\n".join(session_lines) if session_lines else "- none yet"
        index_text = read_memory_index(index_path)
        return (
            "Long-term memory is available for this WhatsApp chat.\n"
            f"Memory directory: {memory_dir}\n"
            f"Memory index: {index_path}\n"
            "Use these files as durable context when they are relevant. "
            "Do not update memory files unless the user asks or a scheduled memory rollover prompt asks you to.\n"
            "\n"
            "Recent saved session ids:\n"
            f"{session_text}\n"
            "\n"
            "Current MEMORY.md:\n"
            f"{index_text or '[empty]'}\n"
            "\n"
        )

    async def memory_rollover_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.run_due_memory_rollovers()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Scheduled memory rollover failed")
            await asyncio.sleep(max(5.0, self.config.memory_check_interval))

    def memory_rollover_due(self, chat_state: dict[str, Any], now: datetime) -> bool:
        if not self.config.memory_enabled:
            return False
        if not (chat_state.get("thread_id") or chat_state.get("summary")):
            return False

        cutoff = datetime.combine(now.date(), self.config.memory_rollover_time, tzinfo=now.tzinfo)
        if now < cutoff:
            return False

        date_key = now.date().isoformat()
        if chat_state.get("last_memory_rollover_date") == date_key:
            return False

        started_raw = chat_state.get("session_started_at")
        if started_raw:
            try:
                started = datetime.fromisoformat(str(started_raw))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=now.tzinfo)
                if started.astimezone(now.tzinfo) >= cutoff:
                    return False
            except ValueError:
                pass
        return True

    async def run_due_memory_rollovers(self) -> None:
        now = local_now()
        chat_ids = list((self.state.data.get("chats") or {}).keys())
        for chat_id in chat_ids:
            chat_state = self.state.chat(chat_id)
            if not self.memory_rollover_due(chat_state, now):
                continue
            lock = self.chat_locks.setdefault(chat_id, asyncio.Lock())
            async with lock:
                chat_state = self.state.chat(chat_id)
                if not self.memory_rollover_due(chat_state, local_now()):
                    continue
                await self.rollover_memory_session(chat_id, chat_state, reason="scheduled")

    def build_memory_rollover_prompt(
        self,
        chat_id: str,
        chat_state: dict[str, Any],
        memory_dir: Path,
        index_path: Path,
        session_id: str,
        session_file: Path,
    ) -> str:
        saved_sessions = chat_state.get("saved_sessions") or []
        saved_lines = []
        for entry in saved_sessions[:10]:
            saved_lines.append(
                "- "
                f"{entry.get('name') or '(untitled)'} | "
                f"id={entry.get('thread_id') or '(no id)'} | "
                f"saved={str(entry.get('saved_at') or '')[:16].replace('T', ' ')}"
            )
        saved_text = "\n".join(saved_lines) if saved_lines else "- none yet"
        files = "\n".join(f"- {memory_dir / filename}" for filename in self.config.memory_files)
        return (
            "Scheduled daily memory rollover for this WhatsApp agent session.\n"
            "You are currently inside the live session that is being compacted in place.\n"
            "After this, the gateway will keep the same session id and continue with the updated carry-forward summary.\n"
            "\n"
            f"WhatsApp chat id: {chat_id}\n"
            f"Current session id: {session_id or '(none)'}\n"
            f"Current root: {chat_state.get('root', self.state.default_root)}\n"
            f"Memory directory: {memory_dir}\n"
            f"Memory index: {index_path}\n"
            f"Gateway session note path: {session_file}\n"
            "\n"
            "Update the long-term memory files from the current conversation context.\n"
            "Keep durable facts, preferences, project state, career/user details, decisions, constraints, and open loops.\n"
            "Avoid transcript dumps, temporary chatter, secrets, credentials, and large logs.\n"
            "\n"
            "Required indexing rules:\n"
            "- Keep MEMORY.md as the table of contents with links to topic files.\n"
            "- Keep these core files indexed and updated when relevant:\n"
            f"{files}\n"
            "- Add new topic files only when they make the memory clearer, and link them from MEMORY.md.\n"
            "- Add this session id to MEMORY.md Session History so future agents can find or resume it if needed.\n"
            "- The gateway will write the session note after your reply; focus on MEMORY.md and topic files.\n"
            "\n"
            "Recent saved sessions:\n"
            f"{saved_text}\n"
            "\n"
            "After editing the files, reply with a concise carry-forward summary for this ongoing session.\n"
            "Include: what changed in memory, previous session id, important current goals, files/repos, decisions, constraints, and open loops."
        )

    def write_memory_session_record(
        self,
        chat_id: str,
        chat_state: dict[str, Any],
        session_id: str,
        session_file: Path,
        update_reply: str,
    ) -> None:
        title = chat_state.get("title") or default_session_name()
        root = chat_state.get("root") or self.state.default_root
        model = chat_state.get("model") or self.config.model or "(default)"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            "# Session Memory Record\n\n"
            f"- Chat id: `{chat_id}`\n"
            f"- Session id: `{session_id or '(none)'}`\n"
            f"- Title: {title}\n"
            f"- Root: `{root}`\n"
            f"- Model: {model}\n"
            f"- Saved at: {now_iso()}\n\n"
            "## Memory Update Summary\n\n"
            f"{update_reply.strip() or 'Memory update completed.'}\n",
            encoding="utf-8",
        )

        index_path = self.ensure_chat_memory(chat_id)
        content = index_path.read_text(encoding="utf-8", errors="replace")
        if "## Session History" not in content:
            content = content.rstrip() + "\n\n## Session History\n\n"
        rel = session_file.relative_to(index_path.parent)
        link_marker = f"]({rel})"
        if link_marker not in content:
            content = (
                content.rstrip()
                + f"\n- {local_now().strftime('%Y-%m-%d %H:%M')} - "
                + f"{title} | session id: `{session_id or '(none)'}` | [{rel}]({rel})\n"
            )
            index_path.write_text(content, encoding="utf-8")

    async def rollover_memory_session(
        self,
        chat_id: str,
        chat_state: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        if not (chat_state.get("thread_id") or chat_state.get("summary")):
            return "No active session to roll over."

        index_path = self.ensure_chat_memory(chat_id)
        memory_dir = index_path.parent
        session_id = (chat_state.get("thread_id") or "").strip()
        short_id = hashlib.sha1((session_id or now_iso()).encode("utf-8")).hexdigest()[:8]
        session_file = (
            memory_dir
            / "sessions"
            / f"{local_now().strftime('%Y-%m-%d-%H%M')}-{short_id}.md"
        )
        prompt = self.build_memory_rollover_prompt(
            chat_id,
            chat_state,
            memory_dir,
            index_path,
            session_id,
            session_file,
        )
        fake_event = {
            "chatId": chat_id,
            "senderName": "memory-rollover",
            "senderId": "memory-rollover",
            "body": prompt,
            "mediaUrls": [],
            "mediaType": "",
        }
        root = chat_state.get("root", self.state.default_root)
        LOG.info("Running %s memory rollover for chat %s", reason, chat_id)
        reply, thread_id = await self.run_agent(fake_event, chat_state, root, prompt_override=prompt)
        if thread_id and not session_id:
            session_id = thread_id
            chat_state["thread_id"] = thread_id
        self.write_memory_session_record(chat_id, chat_state, session_id, session_file, reply)
        chat_state["summary"] = build_carry_forward_summary(reply, session_id, session_file)
        archive_snapshot(chat_state, force=True)
        chat_state["last_memory_rollover_date"] = local_now().date().isoformat()
        chat_state["last_memory_rollover_at"] = now_iso()
        chat_state["last_memory_session_id"] = session_id
        chat_state["last_memory_dir"] = str(memory_dir)
        self.state.save()
        return reply.strip() or "Memory updated and carry-forward summary is ready."

    def restore_saved_session(
        self, chat_state: dict[str, Any], target: dict[str, Any]
    ) -> None:
        chat_state["thread_id"] = target.get("thread_id") or ""
        chat_state["session_started_at"] = target.get("session_started_at") or now_iso()
        chat_state["root"] = target.get("root") or chat_state.get("root", self.state.default_root)
        if target.get("model"):
            chat_state["model"] = target.get("model")
        else:
            chat_state.pop("model", None)
        if target.get("summary"):
            chat_state["summary"] = target.get("summary")
        else:
            chat_state.pop("summary", None)
        if target.get("name"):
            chat_state["title"] = target.get("name")

    async def handle_gateway_command(self, chat_id: str, body: str) -> bool:
        chat_state = self.state.chat(chat_id)
        parts = body.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command in {"/yes", "/y", "/approve"}:
            return await self.handle_upgrade_approval(chat_id, chat_state)

        if command in {"/no", "/n", "/deny"}:
            pending = chat_state.get("pending_upgrade") or {}
            if pending.get("type") != "upgrade":
                await self.send_message(chat_id, "Nothing is awaiting approval.")
                return True
            version = str(pending.get("to_version") or "")
            if version:
                chat_state["dismissed_upgrade_version"] = version
            chat_state.pop("pending_upgrade", None)
            self.state.save()
            await self.send_message(chat_id, f"Okay, I won't ask again for version {version}.")
            return True

        if command in {"/reset", "/new", "/clear"}:
            archived = archive_snapshot(chat_state)
            clear_active_session(chat_state)
            self.state.save()
            prefix = "Archived current session and started fresh." if archived else "Started a fresh session."
            await self.send_message(chat_id, f"{prefix}\nUse `/resume` to list saved sessions.")
            return True

        if command == "/status":
            thread_id = chat_state.get("thread_id") or "(none)"
            root = chat_state.get("root", self.state.default_root)
            model = chat_state.get("model") or self.config.model or "(default)"
            summary = "yes" if chat_state.get("summary") else "no"
            saved_count = len(chat_state.get("saved_sessions") or [])
            memory = "off"
            if self.config.memory_enabled:
                memory = (
                    f"{self.memory_dir_for_chat(chat_id)} "
                    f"(rollover {self.config.memory_rollover_time_text})"
                )
            await self.send_message(
                chat_id,
                f"Backend: {self.config.backend}\nRoot: {root}\nThread: {thread_id}\nModel: {model}\nSummary: {summary}\nSaved sessions: {saved_count}\nMemory: {memory}\nMode: {self.config.mode}",
            )
            return True

        if command == "/root":
            if not arg:
                await self.send_message(chat_id, "Usage: /root /absolute/path")
                return True
            new_root = Path(arg).expanduser()
            if not new_root.is_absolute():
                new_root = (self.config.default_root / new_root).resolve()
            if not new_root.exists() or not new_root.is_dir():
                await self.send_message(chat_id, f"That path is not a directory: {new_root}")
                return True
            chat_state["root"] = str(new_root)
            chat_state.pop("thread_id", None)
            self.state.save()
            await self.send_message(
                chat_id,
                f"Root set to {new_root}. I cleared the old thread so the next turn starts there cleanly.",
            )
            return True

        if command in {"/compact", "/compress"}:
            if not chat_state.get("thread_id") and not chat_state.get("summary"):
                await self.send_message(chat_id, "Nothing to compact yet.")
                return True
            await self.start_typing(chat_id)
            try:
                summary, _ = await self.compact_session(chat_id, chat_state)
            finally:
                await self.stop_typing(chat_id)
            self.state.save()
            await self.send_message(
                chat_id,
                "Context compacted into a carry-forward summary for this same session.\n\n"
                + summary,
            )
            return True

        if command == "/model":
            if not arg:
                current = chat_state.get("model") or self.config.model or "(default)"
                await self.send_message(chat_id, f"Current model: {current}")
                return True
            chat_state["model"] = arg
            archive_snapshot(chat_state)
            self.state.save()
            await self.send_message(
                chat_id,
                f"Model set to {arg}. The active session id and context are unchanged.",
            )
            return True

        if command == "/title":
            if not arg:
                current = chat_state.get("title") or "(untitled)"
                await self.send_message(chat_id, f"Current title: {current}")
                return True
            chat_state["title"] = arg
            archive_snapshot(chat_state)
            self.state.save()
            await self.send_message(chat_id, f"Title set to: {arg}")
            return True

        if command == "/resume":
            if not arg:
                archive_snapshot(chat_state)
                self.state.save()
                await self.send_message(chat_id, format_saved_sessions(chat_state))
                return True
            target = resolve_saved_session(chat_state, arg)
            if not target:
                await self.send_message(chat_id, "Couldn’t find that saved session. Use `/resume` with no args to list them.")
                return True
            archive_snapshot(chat_state)
            self.restore_saved_session(chat_state, target)
            self.state.save()
            await self.send_message(
                chat_id,
                f"Resumed: {target.get('name') or '(untitled)'}",
            )
            return True

        if command in {"/search-session", "/search-sessions", "/sessions"}:
            if not arg:
                await self.send_message(chat_id, "Usage: /search-session <query>")
                return True
            archive_snapshot(chat_state)
            matches = search_saved_sessions(chat_state, arg, limit=5)
            if not matches:
                await self.send_message(chat_id, f"No saved session matched: {arg}")
                self.state.save()
                return True
            target = matches[0][1]
            archive_snapshot(chat_state)
            self.restore_saved_session(chat_state, target)
            self.state.save()
            alternatives = []
            for score, entry in matches[1:4]:
                name = entry.get("name") or "(untitled)"
                thread_id = entry.get("thread_id") or "(no id)"
                alternatives.append(f"- {name} | id={thread_id} | score={score}")
            alt_text = "\n\nOther matches:\n" + "\n".join(alternatives) if alternatives else ""
            await self.send_message(
                chat_id,
                f"Resumed best match for `{arg}`: {target.get('name') or '(untitled)'}"
                + alt_text,
            )
            return True

        if command in {"/memory", "/mem"}:
            if not self.config.memory_enabled:
                await self.send_message(chat_id, "Memory is disabled by AGENT_MEMORY_ENABLED=0.")
                return True
            index_path = self.ensure_chat_memory(chat_id)
            memory_dir = index_path.parent
            if arg.lower() in {"update", "rollover", "save"}:
                if not (chat_state.get("thread_id") or chat_state.get("summary")):
                    await self.send_message(chat_id, "No active session to roll over yet.")
                    return True
                await self.start_typing(chat_id)
                try:
                    reply = await self.rollover_memory_session(
                        chat_id,
                        chat_state,
                        reason="manual",
                    )
                finally:
                    await self.stop_typing(chat_id)
                await self.send_message(
                    chat_id,
                    "Memory updated. This same session will continue with the carry-forward summary.\n\n"
                    + reply,
                )
                return True
            last_rollover = chat_state.get("last_memory_rollover_at") or "(never)"
            last_session = chat_state.get("last_memory_session_id") or "(none)"
            active_session = chat_state.get("thread_id") or "(none)"
            await self.send_message(
                chat_id,
                f"Memory dir: {memory_dir}\n"
                f"Index: {index_path}\n"
                f"Rollover time: {self.config.memory_rollover_time_text}\n"
                f"Active session id: {active_session}\n"
                f"Last compacted session id: {last_session}\n"
                f"Last rollover: {last_rollover}\n"
                "Use `/memory update` to update memory and compact this session now.",
            )
            return True

        if command == "/rollover":
            if not self.config.memory_enabled:
                await self.send_message(chat_id, "Memory is disabled by AGENT_MEMORY_ENABLED=0.")
                return True
            if not (chat_state.get("thread_id") or chat_state.get("summary")):
                await self.send_message(chat_id, "No active session to roll over yet.")
                return True
            await self.start_typing(chat_id)
            try:
                reply = await self.rollover_memory_session(chat_id, chat_state, reason="manual")
            finally:
                await self.stop_typing(chat_id)
            await self.send_message(
                chat_id,
                "Memory updated. This same session will continue with the carry-forward summary.\n\n"
                + reply,
            )
            return True

        if command == "/help":
            await self.send_message(chat_id, format_chat_help())
            return True

        return False

    async def handle_upgrade_approval(
        self, chat_id: str, chat_state: dict[str, Any]
    ) -> bool:
        pending = chat_state.get("pending_upgrade") or {}
        if pending.get("type") != "upgrade":
            await self.send_message(chat_id, "Nothing is awaiting approval.")
            return True

        latest = str(pending.get("to_version") or "")
        current = str(pending.get("from_version") or self.config.package_version)
        command = str(pending.get("command") or self.upgrade_command())
        chat_state.pop("pending_upgrade", None)
        self.state.save()

        root = chat_state.get("root", self.state.default_root)
        prompt = build_upgrade_prompt(current, latest, command)
        fake_event = {
            "chatId": chat_id,
            "senderName": "gateway",
            "senderId": "gateway",
            "body": prompt,
            "mediaUrls": [],
            "mediaType": "",
        }

        await self.send_message(chat_id, f"Approved. Starting upgrade to {latest}.")
        await self.start_typing(chat_id)
        try:
            reply, thread_id = await self.run_agent(
                fake_event,
                chat_state,
                root,
                prompt_override=prompt,
            )
        finally:
            await self.stop_typing(chat_id)

        if thread_id and chat_state.get("thread_id") != thread_id:
            chat_state["thread_id"] = thread_id
            chat_state.setdefault("session_started_at", now_iso())
            self.state.save()
        elif thread_id and not chat_state.get("session_started_at"):
            chat_state["session_started_at"] = now_iso()
            self.state.save()

        for chunk in split_message(reply, self.config.max_reply_chars):
            await self.send_message(chat_id, chunk)
        return True

    async def compact_session(
        self, chat_id: str, chat_state: dict[str, Any]
    ) -> tuple[str, str | None]:
        root = chat_state.get("root", self.state.default_root)
        existing_summary = (chat_state.get("summary") or "").strip()
        prompt = (
            "Compact this conversation into a durable working summary for a future session.\n"
            "Include: goals, current repo/root, relevant files, decisions already made, unfinished work, and important constraints.\n"
            "Write it as concise bullet points.\n"
        )
        if existing_summary:
            prompt += "\nExisting carried summary:\n" + existing_summary + "\n"
        fake_event = {
            "chatId": chat_id,
            "senderName": "system",
            "senderId": "system",
            "body": prompt,
            "mediaUrls": [],
            "mediaType": "",
        }
        summary, _ = await self.run_agent(fake_event, chat_state, root, prompt_override=prompt)
        chat_state["summary"] = summary.strip()
        archive_snapshot(chat_state, force=True)
        return summary.strip(), None

    async def run_agent(
        self,
        event: dict[str, Any],
        chat_state: dict[str, Any],
        root: str,
        *,
        prompt_override: str | None = None,
    ) -> tuple[str, str | None]:
        if self.config.backend == "claude":
            return await self.run_claude(event, chat_state, root, prompt_override=prompt_override)
        return await self.run_codex(event, chat_state, root, prompt_override=prompt_override)

    async def run_codex(
        self,
        event: dict[str, Any],
        chat_state: dict[str, Any],
        root: str,
        *,
        prompt_override: str | None = None,
    ) -> tuple[str, str | None]:
        prompt, image_args = build_prompt(event, root)
        if prompt_override is not None:
            prompt = prompt_override
        summary = (chat_state.get("summary") or "").strip()
        title = (chat_state.get("title") or "").strip()
        if summary and prompt_override is None:
            prompt = (
                f"Carried session summary:\n{summary}\n\n"
                + (f"Session title: {title}\n\n" if title else "")
                + prompt
            )
        if prompt_override is None and self.config.memory_enabled:
            memory_context = self.build_memory_context(str(event["chatId"]), chat_state)
            if memory_context:
                prompt = memory_context + "\n" + prompt
        existing_thread = chat_state.get("thread_id")
        out_path = Path(tempfile.mkstemp(prefix="codex-wa-", suffix=".txt")[1])
        args = [self.config.agent_command, "exec"]
        if existing_thread:
            args.extend(["resume", "--json"])
        else:
            args.extend(["--json", "--skip-git-repo-check", "-C", str(root)])
        args.extend(["--dangerously-bypass-approvals-and-sandbox", "-o", str(out_path)])
        selected_model = (chat_state.get("model") or self.config.model).strip()
        if selected_model:
            args.extend(["-m", selected_model])
        if self.config.enable_search:
            args.append("--search")
        args.extend(image_args)
        if existing_thread:
            args.append(existing_thread)
        args.append(prompt)

        LOG.info("Running Codex for chat %s: %s", event["chatId"], shlex.join(args[:-1]) + " <prompt>")
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        reply = out_path.read_text(errors="replace").strip() if out_path.exists() else ""
        thread_id = existing_thread

        for line in stdout_text.splitlines():
            try:
                event_obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_obj.get("type") == "thread.started":
                thread_id = event_obj.get("thread_id") or thread_id

        if proc.returncode != 0:
            combined = "\n".join(part for part in [stderr_text.strip(), stdout_text.strip()] if part).lower()
            if existing_thread and ("session" in combined and "not found" in combined):
                chat_state.pop("thread_id", None)
                return await self.run_codex(event, chat_state, root, prompt_override=prompt_override)
            detail = stderr_text.strip() or stdout_text.strip() or "Codex exited with an unknown error."
            reply = reply or f"Codex failed:\n{detail[:1500]}"

        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass

        return reply or "Done.", thread_id

    async def run_claude(
        self,
        event: dict[str, Any],
        chat_state: dict[str, Any],
        root: str,
        *,
        prompt_override: str | None = None,
    ) -> tuple[str, str | None]:
        prompt, _ = build_prompt(event, root)
        if prompt_override is not None:
            prompt = prompt_override
        summary = (chat_state.get("summary") or "").strip()
        title = (chat_state.get("title") or "").strip()
        if summary and prompt_override is None:
            prompt = (
                f"Carried session summary:\n{summary}\n\n"
                + (f"Session title: {title}\n\n" if title else "")
                + prompt
            )
        if prompt_override is None and self.config.memory_enabled:
            memory_context = self.build_memory_context(str(event["chatId"]), chat_state)
            if memory_context:
                prompt = memory_context + "\n" + prompt

        existing_thread = (chat_state.get("thread_id") or "").strip()
        thread_id = existing_thread or str(uuid4())
        args = [
            self.config.agent_command,
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            str(root),
        ]
        if self.config.memory_enabled:
            memory_dir = self.memory_dir_for_chat(str(event["chatId"]))
            if str(memory_dir) != str(root):
                args.extend(["--add-dir", str(memory_dir)])
        if existing_thread:
            args.extend(["--resume", existing_thread])
        else:
            args.extend(["--session-id", thread_id])

        selected_model = (chat_state.get("model") or self.config.model).strip()
        if selected_model:
            args.extend(["--model", selected_model])
        args.append(prompt)

        LOG.info("Running Claude for chat %s: %s", event["chatId"], shlex.join(args[:-1]) + " <prompt>")
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        try:
            payload = json.loads(stdout_text) if stdout_text else {}
        except json.JSONDecodeError:
            payload = {}

        if payload.get("session_id"):
            thread_id = str(payload["session_id"])

        reply = str(payload.get("result") or "").strip()
        is_error = bool(payload.get("is_error"))
        if proc.returncode != 0 or is_error:
            combined = "\n".join(part for part in [stderr_text, stdout_text] if part).lower()
            if existing_thread and "session" in combined and "not found" in combined:
                chat_state.pop("thread_id", None)
                return await self.run_claude(event, chat_state, root, prompt_override=prompt_override)
            detail = stderr_text or stdout_text or "Claude exited with an unknown error."
            reply = reply or f"Claude failed:\n{detail[:1500]}"

        return reply or "Done.", thread_id

    async def send_message(self, chat_id: str, message: str) -> None:
        assert self.http is not None
        url = f"http://127.0.0.1:{self.config.bridge_port}/send"
        deadline = time.monotonic() + self.config.send_retry_seconds
        last_error = ""

        while True:
            try:
                async with self.http.post(
                    url,
                    json={"chatId": chat_id, "message": message},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        return
                    detail = await resp.text()
                    last_error = f"Bridge send failed: {resp.status} {detail}"
                    if resp.status < 500 or time.monotonic() >= deadline:
                        raise RuntimeError(last_error)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = f"Bridge send failed: {exc}"
                if time.monotonic() >= deadline:
                    raise RuntimeError(last_error) from exc

            LOG.warning("Bridge not ready for send to %s; retrying: %s", chat_id, last_error)
            await asyncio.sleep(self.config.send_retry_interval)

    async def send_typing(self, chat_id: str) -> None:
        assert self.http is not None
        try:
            async with self.http.post(
                f"http://127.0.0.1:{self.config.bridge_port}/typing",
                json={"chatId": chat_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ):
                pass
        except Exception:
            LOG.debug("Typing indicator failed", exc_info=True)

    async def start_typing(self, chat_id: str) -> None:
        async def _loop() -> None:
            while True:
                await self.send_typing(chat_id)
                await asyncio.sleep(self.config.typing_interval)

        await self.stop_typing(chat_id)
        self.typing_tasks[chat_id] = asyncio.create_task(_loop())

    async def stop_typing(self, chat_id: str) -> None:
        task = self.typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _kill_port_process(self, port: int) -> None:
        proc = await asyncio.create_subprocess_exec(
            "fuser",
            "-k",
            f"{port}/tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


async def _main() -> None:
    load_env_file(Path(_env_first("AGENT_ENV_FILE", "CW_ENV_FILE", default="~/.agent-whatsapp/.env")).expanduser())
    logging.basicConfig(
        level=os.getenv("CW_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    gateway = WhatsAppAgentGateway(Config())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, gateway.stop_event.set)

    runner = asyncio.create_task(gateway.run())
    stopper = asyncio.create_task(gateway.stop_event.wait())
    done, pending = await asyncio.wait(
        {runner, stopper}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    try:
        if runner in done:
            await runner
    finally:
        await gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(_main())
