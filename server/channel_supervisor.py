"""Supervisor for `claude --channels` when AGENT_BACKEND=claude.

Replaces the per-message `claude -p` invocation in run_claude. With channels
the WhatsApp plugin owns the WhatsApp connection and pushes inbound messages
into a single long-lived Claude Code session, so the gateway's job shrinks
to: register the local marketplace + install the plugin once, then launch
claude with the right flags and keep it alive.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import pty
import shlex
import signal
import threading
from pathlib import Path
from typing import Sequence

LOG = logging.getLogger("codex_whatsapp.channel")


class ChannelSupervisor:
    def __init__(
        self,
        *,
        claude_bin: str,
        plugin_dir: Path,
        channel_spec: str,
        workspace_root: Path,
        log_path: Path,
        model: str = "",
        memory_dir: Path | None = None,
        extra_args: Sequence[str] = (),
        env: dict[str, str] | None = None,
        restart_min_delay: float = 2.0,
        restart_max_delay: float = 30.0,
    ) -> None:
        self.claude_bin = claude_bin
        self.plugin_dir = plugin_dir
        self.channel_spec = channel_spec
        self.workspace_root = workspace_root
        self.log_path = log_path
        self.model = model.strip()
        self.memory_dir = memory_dir
        self.extra_args = list(extra_args)
        self.env = env or os.environ.copy()
        self.restart_min_delay = restart_min_delay
        self.restart_max_delay = restart_max_delay
        self.stop_event = asyncio.Event()
        self.process: asyncio.subprocess.Process | None = None

    def build_command(self) -> list[str]:
        # The plugin must be installed from the marketplace (see install.sh /
        # `claude plugin marketplace add` + `claude plugin install`). We don't
        # pass --plugin-dir here — that sideloads a second copy that the
        # channels subsystem won't bind to. --dangerously-load-development-
        # channels takes the channel entry directly and bypasses the
        # Anthropic-maintained allowlist for our local marketplace.
        args: list[str] = [
            self.claude_bin,
            "--dangerously-load-development-channels",
            self.channel_spec,
            "--add-dir",
            str(self.workspace_root),
            "--permission-mode",
            "bypassPermissions",
        ]
        if self.memory_dir and self.memory_dir != self.workspace_root:
            args.extend(["--add-dir", str(self.memory_dir)])
        # WhatsApp chat needs sub-10s replies; high-effort Opus turns take
        # 15-60s and feel broken to a phone user. Default the channel session
        # to Sonnet unless the operator explicitly overrode AGENT_MODEL.
        effective_model = self.model or "claude-sonnet-4-6"
        args.extend(["--model", effective_model])
        args.extend(self.extra_args)
        return args

    async def run(self) -> None:
        if not self.plugin_dir.exists():
            raise RuntimeError(
                f"WhatsApp plugin directory not found: {self.plugin_dir}. "
                "Set CLAUDE_PLUGIN_DIR or reinstall to populate it."
            )

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        await self._ensure_plugin_registered()
        # Plugin install/update commands transiently spawn bun via .mcp.json
        # to validate the plugin, then exit — leaving an orphan bun (parent=1)
        # that grabs the singleton lock for the WhatsApp socket. The bun
        # spawned by our long-running claude session then goes passive and
        # never sees inbound messages. Clean up before launch.
        await self._kill_stale_plugin_processes()

        delay = self.restart_min_delay
        while not self.stop_event.is_set():
            command = self.build_command()
            LOG.info("Starting claude --channels: %s", shlex.join(command))
            # Claude treats a non-TTY stdout as `--print` mode and refuses to
            # start without a prompt argument. Give it a real PTY so it runs
            # interactive, then stream the PTY output to our log file.
            master_fd, slave_fd = pty.openpty()
            reader = threading.Thread(
                target=self._pty_to_log,
                args=(master_fd, self.log_path),
                daemon=True,
            )
            reader.start()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=self.env,
                    cwd=str(self.workspace_root),
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                os.close(master_fd)
                os.close(slave_fd)
                raise RuntimeError(
                    f"claude binary not found: {self.claude_bin}. "
                    "Set CLAUDE_BIN or install Claude Code on PATH."
                ) from exc
            os.close(slave_fd)

            # Claude's TUI shows startup gates the first time it sees a
            # workspace / dev-channels load — workspace trust and the
            # "I am using this for local development" prompt. Both default
            # to the "yes" option (1), so press Enter a few times after
            # spawn to advance through them. Once the session is past the
            # gates, extra Enters land in the empty prompt box and are
            # harmless. Without this the supervisor hangs forever waiting
            # for input that never comes.
            asyncio.create_task(self._auto_advance_startup_gates(master_fd))

            exit_code = await self._wait_or_stop()
            self.process = None
            try:
                os.close(master_fd)
            except OSError:
                pass
            reader.join(timeout=2)

            if self.stop_event.is_set():
                return
            LOG.warning(
                "claude --channels exited code=%s; restarting in %.1fs", exit_code, delay
            )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.restart_max_delay)

    async def _kill_stale_plugin_processes(self) -> None:
        """Best-effort cleanup of orphan bun processes for this plugin.

        `claude plugin install` / `plugin update` transiently spawn the
        plugin's bun via .mcp.json to verify the manifest. When that wrapper
        exits, the bun gets reparented to pid 1 and keeps holding the
        singleton lock + WhatsApp socket without a working MCP stdio peer.
        Inbound notifications then go to a dead pipe and Claude appears mute.
        """
        plugin_path = str(self.plugin_dir)
        own_pid = os.getpid()
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-fa", plugin_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except FileNotFoundError:
            LOG.debug("pgrep not available; skipping stale-plugin cleanup")
            return

        killed: list[int] = []
        for line in out.decode(errors="replace").splitlines():
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid == own_pid:
                continue
            cmdline = parts[1] if len(parts) > 1 else ""
            # Only target bun processes (not other claude/python loops that
            # happen to mention the plugin path).
            if "bun" not in cmdline and "/bun" not in cmdline:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                LOG.warning("cannot signal stale plugin pid=%d (permission denied)", pid)

        if killed:
            LOG.info("killed stale plugin bun pids=%s", killed)
            # Brief settle so any lockfile from the killed bun gets released
            # before the new claude session spawns its own bun.
            await asyncio.sleep(0.5)
        # Also nuke a leftover lock file in case the process is gone but
        # the file wasn't cleaned up.
        for lock in (
            Path.home() / ".claude" / "channels" / "whatsapp" / "whatsapp.lock",
        ):
            try:
                lock.unlink()
                LOG.info("removed stale lock file %s", lock)
            except FileNotFoundError:
                pass
            except OSError as exc:
                LOG.warning("could not remove %s: %s", lock, exc)

    async def _ensure_plugin_registered(self) -> None:
        """Idempotently add the local marketplace and install the plugin.

        Claude's `--channels` only binds when the plugin is installed from a
        configured marketplace — sideloading via --plugin-dir doesn't satisfy
        the channels subsystem. We parse the marketplace and plugin names out
        of the marketplace.json shipped alongside the plugin and run the two
        CLI commands, swallowing errors that just mean "already done".
        """
        marketplace_dir = self.plugin_dir.parent
        manifest_path = marketplace_dir / ".claude-plugin" / "marketplace.json"
        if not manifest_path.exists():
            LOG.warning(
                "marketplace manifest not found at %s — skipping auto-install; "
                "the channel will start but Claude will report 'plugin not installed'",
                manifest_path,
            )
            return

        try:
            manifest = json.loads(manifest_path.read_text())
            marketplace_name = manifest["name"]
            plugins = manifest.get("plugins") or []
            plugin_name = plugins[0]["name"] if plugins else "whatsapp"
        except (KeyError, ValueError, IndexError) as exc:
            LOG.warning("could not parse %s: %s — skipping auto-install", manifest_path, exc)
            return

        await self._run_claude_command(
            ["plugin", "marketplace", "add", str(marketplace_dir)],
            "marketplace add",
        )
        await self._run_claude_command(
            ["plugin", "marketplace", "update", marketplace_name],
            "marketplace update",
        )
        await self._run_claude_command(
            ["plugin", "install", f"{plugin_name}@{marketplace_name}"],
            "plugin install",
        )
        await self._run_claude_command(
            ["plugin", "update", f"{plugin_name}@{marketplace_name}"],
            "plugin update",
        )

    async def _run_claude_command(self, args: Sequence[str], label: str) -> None:
        cmd = [self.claude_bin, *args]
        LOG.info("%s: %s", label, shlex.join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError as exc:
            raise RuntimeError(f"claude binary not found: {self.claude_bin}") from exc
        if proc.returncode == 0:
            return
        combined = (stdout.decode(errors="replace") + stderr.decode(errors="replace")).lower()
        if "already" in combined or "exists" in combined:
            LOG.debug("%s: already done", label)
            return
        LOG.warning(
            "%s exited %s — proceeding anyway. output:\n%s",
            label,
            proc.returncode,
            (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()[:500],
        )

    async def _auto_advance_startup_gates(self, master_fd: int) -> None:
        for delay in (1.5, 1.5, 1.5):
            if self.stop_event.is_set():
                return
            try:
                await asyncio.sleep(delay)
                os.write(master_fd, b"\r")
            except OSError:
                return

    @staticmethod
    def _pty_to_log(master_fd: int, log_path: Path) -> None:
        try:
            with log_path.open("ab", buffering=0) as log:
                while True:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno in (errno.EIO, errno.EBADF):
                            return
                        raise
                    if not data:
                        return
                    log.write(data)
        except Exception as exc:  # pragma: no cover — best-effort logger
            LOG.warning("pty reader stopped: %s", exc)

    async def _wait_or_stop(self) -> int | None:
        assert self.process is not None
        wait_task = asyncio.create_task(self.process.wait())
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _ = await asyncio.wait(
            {wait_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                LOG.warning("claude did not exit on SIGTERM; killing")
                self.process.kill()
                await self.process.wait()
        for task in (wait_task, stop_task):
            if not task.done():
                task.cancel()
        return self.process.returncode

    async def shutdown(self) -> None:
        self.stop_event.set()
