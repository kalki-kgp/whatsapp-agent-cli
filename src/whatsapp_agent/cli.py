"""whatsapp-agent CLI — thin wrapper around the bundled bash + node runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Iterable

from . import __version__

DEFAULT_INSTALL_DIR = Path(os.environ.get("AGENT_WHATSAPP_HOME", str(Path.home() / ".agent-whatsapp")))
DEFAULT_SERVICE_NAME = os.environ.get("SERVICE_NAME", "agent-whatsapp")

# Files / dirs at the destination that must NEVER be overwritten by sync.
PRESERVE = {".env", ".venv", "node_modules", "whatsapp", "state.json", "logs", "memory"}

GREEN = "\033[38;2;37;211;102m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    GREEN = RED = YELLOW = DIM = BOLD = RESET = ""


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _runtime_root() -> Path:
    """Return the on-disk path to the bundled runtime files inside the wheel."""
    return Path(str(files("whatsapp_agent") / "_runtime"))


def _sync_runtime(dest: Path) -> None:
    """Copy bundled runtime into ``dest``, preserving user state."""
    src = _runtime_root()
    if not src.exists():
        raise SystemExit(
            f"Bundled runtime not found at {src}. "
            "This wheel is missing data files — please reinstall."
        )

    dest.mkdir(parents=True, exist_ok=True)

    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if rel.parts and rel.parts[0] in PRESERVE:
            continue
        if any(part in {"node_modules", "__pycache__", ".venv"} for part in rel.parts):
            continue

        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if target.suffix == ".sh":
                target.chmod(0o755)


def _exec_bash(script: Path, args: Iterable[str], env: dict | None = None) -> int:
    """Run a bash script, inheriting stdio. Returns exit code."""
    if not script.exists():
        _fail(f"Script not found: {script}")
        return 1
    cmd = ["bash", str(script), *args]
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.call(cmd, env=full_env)


# ── subcommands ──────────────────────────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir or DEFAULT_INSTALL_DIR)
    install_dir.mkdir(parents=True, exist_ok=True)

    print(f"  {DIM}Syncing bundled runtime → {install_dir}{RESET}")
    _sync_runtime(install_dir)
    _ok("runtime files in place")

    script = install_dir / "scripts" / "install.sh"
    forwarded: list[str] = []
    if args.reconfigure:
        forwarded.append("--reconfigure")
    if args.non_interactive:
        forwarded.append("--non-interactive")

    env = {
        "INSTALL_DIR": str(install_dir),
        "SERVICE_NAME": args.service or DEFAULT_SERVICE_NAME,
        "SKIP_CLONE": "1",
        "AGENT_PACKAGE_VERSION": __version__,
    }
    return _exec_bash(script, forwarded, env=env)


def cmd_pair(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir or DEFAULT_INSTALL_DIR)
    env_file = install_dir / ".env"
    script = install_dir / "scripts" / "pair.sh"
    if not env_file.exists():
        _fail(f"No configured install found at {install_dir}.")
        _warn("Run `whatsapp-agent install` first, then `whatsapp-agent pair`.")
        return 1
    if not script.exists():
        _warn(f"Pair script missing at {script}; repairing runtime files.")
        try:
            _sync_runtime(install_dir)
        except Exception as exc:
            _fail(f"Could not repair runtime files: {exc}")
            _warn("Run `whatsapp-agent install --reconfigure` to rebuild the runtime.")
            return 1
        if not script.exists():
            _fail(f"Pair script still missing at {script}.")
            _warn("Run `whatsapp-agent install --reconfigure` to rebuild the runtime.")
            return 1
        _ok("runtime files repaired")
    forwarded: list[str] = []
    if args.reuse:
        forwarded.append("--reuse")
    if args.reset:
        forwarded.append("--reset")
    if args.yes:
        forwarded.append("--yes")
    return _exec_bash(script, forwarded, env={"AGENT_WHATSAPP_HOME": str(install_dir)})


def cmd_run(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir or DEFAULT_INSTALL_DIR)
    venv_python = install_dir / ".venv" / "bin" / "python"
    gateway = install_dir / "server" / "gateway.py"

    if not venv_python.exists():
        _fail(f"Runtime venv not found at {venv_python}.")
        _warn("Run `whatsapp-agent install` first.")
        return 1
    if not gateway.exists():
        _fail(f"gateway.py missing at {gateway}.")
        return 1

    env = os.environ.copy()
    env["AGENT_WHATSAPP_HOME"] = str(install_dir)
    env_file = install_dir / ".env"
    parsed_env: dict[str, str] = {}
    if env_file.exists():
        env["AGENT_ENV_FILE"] = str(env_file)
        parsed_env = _parse_env_file(env_file)

    if args.plain or not sys.stdout.isatty():
        print("  whatsapp-agent gateway running in foreground. Press Ctrl-C to stop.")
        return subprocess.call([str(venv_python), str(gateway)], env=env, cwd=str(install_dir))

    logs_dir = install_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    gateway_log = logs_dir / "gateway.log"
    with gateway_log.open("a", buffering=1) as log_handle:
        log_handle.write(f"\n--- whatsapp-agent run started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        proc = subprocess.Popen(
            [str(venv_python), str(gateway)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(install_dir),
            text=True,
        )
        return _run_monitor(proc, install_dir, parsed_env, gateway_log)


def cmd_service(args: argparse.Namespace) -> int:
    if shutil.which("systemctl") is None:
        _fail("systemctl not found — service control is Linux-only.")
        _warn("Use `whatsapp-agent run` to run the gateway in the foreground.")
        return 1

    service = f"{args.service or DEFAULT_SERVICE_NAME}.service"
    verb = args.verb

    if verb == "logs":
        cmd = ["journalctl", "--user", "-u", service, "-f"]
    else:
        flags: list[str] = []
        if verb == "status":
            flags = ["--no-pager"]
        cmd = ["systemctl", "--user", verb, service, *flags]

    return subprocess.call(cmd)


def _run_quiet(cmd: list[str]) -> bool:
    return subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _dangerous_delete_target(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    protected = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path.home().parent.resolve(),
    }
    return resolved in protected


def cmd_uninstall(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir or DEFAULT_INSTALL_DIR).expanduser()
    service = f"{args.service or DEFAULT_SERVICE_NAME}.service"

    if _dangerous_delete_target(install_dir):
        _fail(f"Refusing to remove unsafe install dir: {install_dir}")
        return 1

    if not args.yes:
        if not sys.stdin.isatty():
            _fail("Refusing to uninstall without --yes in a non-interactive shell.")
            return 1
        print(f"\n  {BOLD}Uninstall whatsapp-agent{RESET}")
        print(f"  Service:     {service}")
        print(f"  Install dir: {install_dir}")
        answer = input("\n  Remove the service and delete this install dir? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            _warn("uninstall cancelled.")
            return 1

    if shutil.which("systemctl") is not None:
        _run_quiet(["systemctl", "--user", "stop", service])
        _run_quiet(["systemctl", "--user", "disable", service])
        unit_path = Path.home() / ".config" / "systemd" / "user" / service
        if unit_path.exists() or unit_path.is_symlink():
            unit_path.unlink()
            _ok(f"removed service unit {unit_path}")
        _run_quiet(["systemctl", "--user", "daemon-reload"])
        _run_quiet(["systemctl", "--user", "reset-failed", service])
    else:
        _warn("systemctl not found — skipped service cleanup.")

    if install_dir.exists() or install_dir.is_symlink():
        if install_dir.is_symlink() or install_dir.is_file():
            install_dir.unlink()
        else:
            shutil.rmtree(install_dir)
        _ok(f"removed install dir {install_dir}")
    else:
        _warn(f"install dir not found: {install_dir}")

    _ok("uninstall complete")
    _warn("The whatsapp-agent CLI is still installed. Run `whatsapp-agent install` before pairing again.")
    _warn("To remove the CLI too, run `uv tool uninstall whatsapp-agent-cli`.")
    return 0


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _tail_lines(path: Path, limit: int = 60) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception as exc:
        return [f"Could not read {path}: {exc}"]
    return lines[-limit:]


def _state_lines(install_dir: Path, env: dict[str, str]) -> list[str]:
    lines = [
        f"home: {install_dir}",
        f"backend: {env.get('AGENT_BACKEND', '(unset)')}",
        f"model: {env.get('AGENT_MODEL') or '(cli default)'}",
        f"root: {env.get('AGENT_ROOT', '(unset)')}",
        f"mode: {env.get('WHATSAPP_MODE', '(unset)')}",
        f"port: {env.get('WHATSAPP_PORT', '3010')}",
        f"voice: {'on' if env.get('AGENT_TRANSCRIBE_AUDIO') == '1' else 'off'}",
    ]
    state_path = install_dir / "state.json"
    if not state_path.exists():
        lines.append("state: waiting for first message")
        return lines
    try:
        state = json.loads(state_path.read_text())
    except Exception as exc:
        lines.append(f"state: unreadable ({exc})")
        return lines
    chats = state.get("chats") or {}
    lines.append(f"chats: {len(chats)}")
    for chat_id, chat_state in list(chats.items())[-5:]:
        title = chat_state.get("title") or "(untitled)"
        thread = chat_state.get("thread_id") or "(none)"
        root = chat_state.get("root") or env.get("AGENT_ROOT") or "(unset)"
        model = chat_state.get("model") or env.get("AGENT_MODEL") or "(default)"
        lines.append(f"- {title} | {chat_id}")
        lines.append(f"  id={thread} model={model} root={root}")
    return lines


def _message_history_lines(install_dir: Path, limit: int = 60) -> list[str]:
    state_path = install_dir / "state.json"
    if not state_path.exists():
        return ["No message history yet. New WhatsApp messages will appear here."]
    try:
        state = json.loads(state_path.read_text())
    except Exception as exc:
        return [f"Could not read message history: {exc}"]

    history = state.get("message_history") or []
    if not history:
        return ["No message history yet. New WhatsApp messages will appear here."]

    chats = state.get("chats") or {}
    lines: list[str] = []
    for entry in history[-limit:]:
        chat_id = str(entry.get("chat_id") or "")
        chat_state = chats.get(chat_id) or {}
        chat_label = chat_state.get("title") or chat_id.replace("@s.whatsapp.net", "")
        at = str(entry.get("at") or "")
        clock = at[11:16] if len(at) >= 16 else "--:--"
        direction = "IN " if entry.get("direction") == "in" else "OUT"
        sender = entry.get("sender") or ("agent" if direction == "OUT" else "(unknown)")
        text = " ".join(str(entry.get("text") or "").split())
        lines.append(f"{clock} {direction} {chat_label} | {sender}: {text}")
    return lines


def _draw_line(stdscr: object, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    safe = text.replace("\t", "    ")
    try:
        stdscr.addnstr(y, x, safe.ljust(max(0, width - 1)), max(0, width - 1), attr)
    except Exception:
        pass


def _run_monitor(proc: subprocess.Popen, install_dir: Path, env: dict[str, str], gateway_log: Path) -> int:
    try:
        import curses
    except Exception:
        _warn("curses is unavailable; falling back to plain foreground mode.")
        return proc.wait()

    bridge_log = install_dir / "logs" / "bridge.log"

    def loop(stdscr: object) -> int:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(250)
        selected = "messages"
        while True:
            key = stdscr.getch()
            if key in {ord("q"), ord("Q"), 3}:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                return proc.returncode or 0
            if key in {ord("g"), ord("G")}:
                selected = "gateway"
            if key in {ord("b"), ord("B")}:
                selected = "bridge"
            if key in {ord("m"), ord("M")}:
                selected = "messages"

            height, width = stdscr.getmaxyx()
            stdscr.erase()
            running = proc.poll() is None
            title = "whatsapp-agent run"
            status = "running" if running else f"exited {proc.returncode}"
            _draw_line(
                stdscr,
                0,
                0,
                f" {title} [{status}]  q:quit  m:messages  g:gateway  b:bridge",
                width,
                curses.A_REVERSE,
            )

            left_width = min(46, max(28, width // 3))
            log_width = max(20, width - left_width - 3)
            _draw_line(stdscr, 2, 1, "Session", left_width, curses.A_BOLD)
            for idx, line in enumerate(_state_lines(install_dir, env)[: height - 5]):
                _draw_line(stdscr, 3 + idx, 1, line, left_width)

            for y in range(1, height):
                _draw_line(stdscr, y, left_width + 1, "|", 2)

            if selected == "messages":
                _draw_line(
                    stdscr,
                    2,
                    left_width + 3,
                    "messages: recent WhatsApp activity",
                    log_width,
                    curses.A_BOLD,
                )
                log_lines = _message_history_lines(install_dir, max(5, height - 5))
            else:
                log_path = gateway_log if selected == "gateway" else bridge_log
                _draw_line(
                    stdscr,
                    2,
                    left_width + 3,
                    f"{selected} log: {log_path}",
                    log_width,
                    curses.A_BOLD,
                )
                log_lines = _tail_lines(log_path, max(5, height - 5))
            start_y = 3
            for idx, line in enumerate(log_lines[-(height - start_y - 1):]):
                _draw_line(stdscr, start_y + idx, left_width + 3, line, log_width)
            if not running:
                _draw_line(
                    stdscr,
                    height - 1,
                    0,
                    " gateway exited; press q to close ",
                    width,
                    curses.A_REVERSE,
                )
            stdscr.refresh()
            if not running:
                time.sleep(0.5)

    try:
        return curses.wrapper(loop)
    except KeyboardInterrupt:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
        return 130


def cmd_doctor(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir or DEFAULT_INSTALL_DIR)
    print(f"\n  {BOLD}whatsapp-agent doctor{RESET}  {DIM}({install_dir}){RESET}\n")

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        suffix = f"  {DIM}{detail}{RESET}" if detail else ""
        if ok:
            _ok(f"{label}{suffix}")
        else:
            _fail(f"{label}{suffix}")
            failures += 1

    py_ok = sys.version_info >= (3, 10)
    check("python ≥ 3.10", py_ok, f"found {sys.version.split()[0]}")
    check("uv on PATH", shutil.which("uv") is not None, shutil.which("uv") or "missing")
    node = shutil.which("node")
    check("node on PATH", node is not None, node or "missing")

    install_ok = (install_dir / "bridge" / "bridge.js").exists()
    check("install dir populated", install_ok, str(install_dir))

    env_path = install_dir / ".env"
    env = _parse_env_file(env_path)
    check(".env present", env_path.exists(), str(env_path))

    backend = env.get("AGENT_BACKEND", "")
    check("AGENT_BACKEND set", bool(backend), backend or "(empty)")

    cli_path = env.get("AGENT_COMMAND", "")
    cli_exists = bool(cli_path) and Path(cli_path).exists()
    check("CLI binary exists", cli_exists, cli_path or "(empty)")

    allowed = env.get("WHATSAPP_ALLOWED_USERS", "").strip()
    check("WHATSAPP_ALLOWED_USERS set", bool(allowed), allowed or "(empty)")

    venv_py = install_dir / ".venv" / "bin" / "python"
    check("python venv built", venv_py.exists(), str(venv_py))
    if env.get("AGENT_TRANSCRIBE_AUDIO") == "1" and venv_py.exists():
        whisper_ok = subprocess.run(
            [str(venv_py), "-c", "import faster_whisper"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        check("faster-whisper installed", whisper_ok, env.get("AGENT_WHISPER_MODEL", "base"))

    bridge_modules = install_dir / "bridge" / "node_modules"
    check("bridge node_modules", bridge_modules.exists(), str(bridge_modules))

    print()
    if failures == 0:
        _ok(f"{BOLD}all good.{RESET}")
        return 0
    _fail(f"{failures} check(s) failed. Run `whatsapp-agent install` to fix.")
    return 1


def cmd_path(args: argparse.Namespace) -> int:
    print(args.install_dir or DEFAULT_INSTALL_DIR)
    return 0


# ── argparse wiring ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-agent",
        description="Run a coding CLI (claude / codex) behind a dedicated WhatsApp number.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--install-dir",
        default=None,
        metavar="PATH",
        help=f"Override install root (default: {DEFAULT_INSTALL_DIR}).",
    )

    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    sub.required = True

    p_install = sub.add_parser("install", help="Install / reconfigure the runtime (interactive).")
    p_install.add_argument("--reconfigure", action="store_true",
                           help="Re-run prompts using saved .env values as defaults.")
    p_install.add_argument("--non-interactive", action="store_true",
                           help="No prompts; use env vars + auto-detect.")
    p_install.add_argument("--service", default=None, help="systemd user service name.")
    p_install.set_defaults(func=cmd_install)

    p_pair = sub.add_parser("pair", help="Pair or verify the WhatsApp session.")
    pair_mode = p_pair.add_mutually_exclusive_group()
    pair_mode.add_argument("--reuse", action="store_true",
                           help="Use existing WhatsApp credentials and verify they connect.")
    pair_mode.add_argument("--reset", "--new", action="store_true",
                           help="Back up existing credentials and show a fresh QR code.")
    p_pair.add_argument("-y", "--yes", action="store_true",
                        help="Do not prompt when --reset is selected.")
    p_pair.set_defaults(func=cmd_pair)

    p_run = sub.add_parser("run", help="Run the gateway with a live terminal monitor.")
    p_run.add_argument("--plain", action="store_true",
                       help="Use the old foreground log mode instead of the terminal monitor.")
    p_run.set_defaults(func=cmd_run)

    p_svc = sub.add_parser("service", help="Control the systemd user service.")
    p_svc.add_argument("verb",
                       choices=["start", "stop", "restart", "status",
                                "enable", "disable", "logs"],
                       help="Action to perform.")
    p_svc.add_argument("--service", default=None, help="systemd user service name.")
    p_svc.set_defaults(func=cmd_service)

    p_uninstall = sub.add_parser("uninstall", help="Remove the service and install dir.")
    p_uninstall.add_argument("--service", default=None, help="systemd user service name.")
    p_uninstall.add_argument("-y", "--yes", action="store_true",
                             help="Do not prompt before deleting the install dir.")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_doc = sub.add_parser("doctor", help="Run diagnostics on the install.")
    p_doc.set_defaults(func=cmd_doctor)

    p_path = sub.add_parser("path", help="Print the install dir.")
    p_path.set_defaults(func=cmd_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        _fail("cancelled.")
        return 130
