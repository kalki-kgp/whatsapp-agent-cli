#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import shlex
import signal
import tempfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp


LOG = logging.getLogger("codex_whatsapp")


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
        self.logs_dir = self.home / "logs"
        self.bridge_log = self.logs_dir / "bridge.log"
        self.processed_limit = int(os.getenv("CW_PROCESSED_LIMIT", "2000"))
        self.typing_interval = float(os.getenv("CW_TYPING_INTERVAL", "8"))

    def ensure_dirs(self) -> None:
        for path in [self.home, self.logs_dir, self.session_dir.parent]:
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
    media_urls = event.get("mediaUrls") or []
    media_type = event.get("mediaType") or ""
    image_args: list[str] = []
    media_lines: list[str] = []
    for media_path in media_urls:
        path = Path(media_path)
        if media_type == "image" and path.exists():
            image_args.extend(["-i", str(path)])
        media_lines.append(f"- {media_type or 'file'}: {path}")

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
        f"{body or '[no text]'}\n"
    )
    if media_lines:
        prompt += "\nAttached/cached media:\n" + "\n".join(media_lines) + "\n"
    return prompt, image_args


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("Session %Y-%m-%d %H:%M")


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
    if not keep_summary:
        chat_state.pop("summary", None)


def format_saved_sessions(chat_state: dict[str, Any]) -> str:
    saved = chat_state.get("saved_sessions") or []
    if not saved:
        return "No saved sessions yet."
    lines = ["Saved sessions:"]
    for entry in saved[:10]:
        name = entry.get("name") or "(untitled)"
        model = entry.get("model") or "(default)"
        root = entry.get("root") or "(root unset)"
        saved_at = entry.get("saved_at", "")[:16].replace("T", " ")
        lines.append(f"- {name} | model={model} | root={root} | {saved_at}")
    lines.append("Use `/resume <name>` to switch back.")
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

    async def run(self) -> None:
        self.config.ensure_dirs()
        self.http = aiohttp.ClientSession()
        await self.start_bridge()
        await self.poll_loop()

    async def shutdown(self) -> None:
        self.stop_event.set()
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
                await self.send_message(
                    event["chatId"],
                    "That run blew up on the server. Check the gateway logs and try again.",
                )

    async def process_message(self, event: dict[str, Any]) -> None:
        chat_id = str(event["chatId"])
        body = (event.get("body") or "").strip()
        if body.startswith("/"):
            handled = await self.handle_gateway_command(chat_id, body)
            if handled:
                return

        chat_state = self.state.chat(chat_id)
        root = chat_state.get("root", self.state.default_root)
        await self.start_typing(chat_id)
        try:
            reply, thread_id = await self.run_agent(event, chat_state, root)
        finally:
            await self.stop_typing(chat_id)

        if thread_id and chat_state.get("thread_id") != thread_id:
            chat_state["thread_id"] = thread_id
            self.state.save()

        for chunk in split_message(reply, self.config.max_reply_chars):
            await self.send_message(chat_id, chunk)

    async def handle_gateway_command(self, chat_id: str, body: str) -> bool:
        chat_state = self.state.chat(chat_id)
        parts = body.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

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
            await self.send_message(
                chat_id,
                f"Backend: {self.config.backend}\nRoot: {root}\nThread: {thread_id}\nModel: {model}\nSummary: {summary}\nSaved sessions: {saved_count}\nMode: {self.config.mode}",
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
                "Context compacted into a carry-forward summary. Next turns will start fresh but keep the important bits.\n\n"
                + summary,
            )
            return True

        if command == "/model":
            if not arg:
                current = chat_state.get("model") or self.config.model or "(default)"
                await self.send_message(chat_id, f"Current model: {current}")
                return True
            archive_snapshot(chat_state)
            chat_state["model"] = arg
            clear_active_session(chat_state, keep_summary=True)
            self.state.save()
            await self.send_message(
                chat_id,
                f"Model set to {arg}. I kept the compacted summary if there was one and cleared the live thread.",
            )
            return True

        if command == "/title":
            if not arg:
                current = chat_state.get("title") or "(untitled)"
                await self.send_message(chat_id, f"Current title: {current}")
                return True
            chat_state["title"] = arg
            self.state.save()
            await self.send_message(chat_id, f"Title set to: {arg}")
            return True

        if command == "/resume":
            if not arg:
                await self.send_message(chat_id, format_saved_sessions(chat_state))
                return True
            target = resolve_saved_session(chat_state, arg)
            if not target:
                await self.send_message(chat_id, "Couldn’t find that saved session. Use `/resume` with no args to list them.")
                return True
            archive_snapshot(chat_state)
            chat_state["thread_id"] = target.get("thread_id") or ""
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
            self.state.save()
            await self.send_message(
                chat_id,
                f"Resumed: {target.get('name') or '(untitled)'}",
            )
            return True

        if command == "/help":
            await self.send_message(
                chat_id,
                "/status shows current chat state.\n"
                "/new or /clear starts fresh and archives the current session.\n"
                "/resume lists saved sessions or resumes one by name.\n"
                "/title <name> names the current session.\n"
                "/root /path switches the project root for this chat.\n"
                "/model <name> switches the model for this chat.\n"
                "/compact rolls the active thread into a carry-forward summary.\n"
                "/reset clears the live thread immediately.",
            )
            return True

        return False

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
        archive_snapshot(chat_state, force=True)
        chat_state["summary"] = summary.strip()
        clear_active_session(chat_state, keep_summary=True)
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
                return await self.run_codex(event, chat_state, root)
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
        async with self.http.post(
            f"http://127.0.0.1:{self.config.bridge_port}/send",
            json={"chatId": chat_id, "message": message},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"Bridge send failed: {resp.status} {detail}")

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
