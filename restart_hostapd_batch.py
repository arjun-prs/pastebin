#!/usr/bin/env python3
"""
Restart hostapd on devices in controlled SSH batches.

Default command:
    sudo systemctl restart hostapd

The script:
  - accepts --device, --device-pattern, or --device-file inputs
  - prompts once for SSH username/password unless --username is supplied
  - runs up to --batch-size devices at a time, default 10
  - checks hostapd first and skips restart when it is already active
  - waits for the remote command to return to the shell prompt
  - verifies hostapd with `systemctl is-active hostapd`
  - closes each SSH connection before returning a result
  - writes per-device logs and summary.txt

Examples:
    python3 restart_hostapd_batch.py --device-file devices.txt
    python3 restart_hostapd_batch.py --device hsg17-q2-b46-t0-r1 --device hsg17-q2-b46-t0-r2
    python3 restart_hostapd_batch.py --device-pattern "hsg17-q2-b46-t0-r[1-10]"
"""

import argparse
import getpass
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pexpect


HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE = r"(?i)(username|login)[: ]"
PASS_RE = r"(?i)password.*:\s*$"
SHELL_PROMPT_RE = r"(?m)^.*[$#]\s*$"
SUDO_PASS_RE = r"(?i)(\[sudo\]\s*)?password(?: for [^:]+)?:\s*$"
PROMPT = "PEXPECT_PROMPT> "

DEFAULT_CMD = "sudo systemctl restart hostapd"
DEFAULT_VERIFY_CMD = "systemctl is-active hostapd"
DEFAULT_EXPECTED_VERIFY = "active"
DEFAULT_BATCH_SIZE = 10
DEFAULT_VERIFY_DELAY = 10
DEFAULT_VERIFY_RETRIES = 18
DIAGNOSTIC_COMMANDS = [
    "systemctl status hostapd.service --no-pager --lines 80",
    "sudo journalctl -xeu hostapd.service --no-pager -n 80",
]


@dataclass(frozen=True)
class Device:
    host: str
    port: int = 22


@dataclass
class CommandResult:
    rc: Optional[int]
    output: str
    disconnected: bool = False


@dataclass
class DeviceResult:
    host: str
    status: str
    detail: str
    log_path: str


def print_progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def make_log_dir(base_dir: Optional[str]) -> str:
    root = base_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "restart_hostapd_logs",
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(root, timestamp)
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def device_log_path(log_dir: str, dev: Device) -> str:
    return os.path.join(log_dir, f"{sanitize_filename(dev.host)}_{dev.port}.log")


def write_device_log(log_path: str, lines: List[str]) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def chunked(items: List[Device], size: int) -> List[List[Device]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def strip_ansi(value: str) -> str:
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", value or "")


def expand_bracket_range(pattern: str) -> List[str]:
    match = re.search(r"\[([^\[\]]+)\]", pattern)
    if not match:
        return [pattern]

    token = match.group(1)
    range_match = re.fullmatch(r"(\d+)-(\d+)", token)
    if range_match:
        start_s, end_s = range_match.group(1), range_match.group(2)
        start, end = int(start_s), int(end_s)
        if start > end:
            raise ValueError(f"Invalid range in {pattern}: start > end")
        width = max(len(start_s), len(end_s)) if start_s.startswith("0") else 0
        replacements = [
            str(i).zfill(width) if width else str(i)
            for i in range(start, end + 1)
        ]
    elif "," in token:
        replacements = [part.strip() for part in token.split(",") if part.strip()]
        if not replacements:
            raise ValueError(f"Invalid empty choice list in {pattern}")
    else:
        replacements = list(token)

    expanded: List[str] = []
    for replacement in replacements:
        next_pattern = pattern[:match.start()] + replacement + pattern[match.end():]
        expanded.extend(expand_bracket_range(next_pattern))
    return expanded


def parse_host_line(value: str) -> Tuple[str, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) == 1 and parts[0]:
        return parts[0], 22
    if len(parts) == 2 and parts[0]:
        return parts[0], int(parts[1])
    raise argparse.ArgumentTypeError("Use host or host,port")


def load_hosts_from_file(path: str) -> List[Tuple[str, int]]:
    hosts: List[Tuple[str, int]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                hosts.append(parse_host_line(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno}: invalid host line '{line}': {exc}") from exc
    return hosts


def ssh_failure_detail(child) -> str:
    parts = []
    for value in (child.before, child.after):
        if isinstance(value, str):
            parts.append(value)
    detail = strip_ansi("".join(parts)).strip()
    if not detail:
        return ""
    detail = re.sub(r"\s+", " ", detail)
    return f": {detail[-300:]}"


def short_error(exc: Exception) -> str:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return message[:300]


def connect_ssh(
    dev: Device,
    username: str,
    password: str,
    timeout: int,
    strict_hostkey: str,
):
    ssh_cmd = (
        f"ssh -o StrictHostKeyChecking={strict_hostkey} "
        f"-o ConnectTimeout={timeout} -o ConnectionAttempts=1 "
        f"-p {dev.port} {username}@{dev.host}"
    )
    child = pexpect.spawn(ssh_cmd, encoding="utf-8", echo=False, timeout=timeout)
    try:
        index = child.expect([HOSTKEY_RE, USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
        if index == 0:
            child.sendline("yes")
            index = child.expect([USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])

        if index == 0:
            child.sendline(username)
            child.expect(PASS_RE)
            child.sendline(password)
        elif index == 1:
            child.sendline(password)
        elif index == 2:
            raise TimeoutError(f"Login timed out connecting to {dev.host}{ssh_failure_detail(child)}")
        else:
            raise ConnectionError(f"EOF while connecting to {dev.host}{ssh_failure_detail(child)}")
        return child
    except Exception:
        child.close(force=True)
        raise


def close_session(child) -> None:
    try:
        try:
            alive = child is not None and child.isalive()
        except OSError:
            alive = False

        if child is not None and alive:
            try:
                child.sendline("exit")
                child.expect(pexpect.EOF, timeout=10)
            except (OSError, pexpect.TIMEOUT, pexpect.EOF):
                pass
    finally:
        if child is not None:
            try:
                child.close()
            except OSError:
                pass


def prepare_shell(child, timeout: int) -> str:
    child.sendline("")
    child.expect([SHELL_PROMPT_RE, pexpect.TIMEOUT], timeout=5)
    child.sendline('export PROMPT_COMMAND=""')
    child.sendline("export TERM=dumb")
    child.sendline("export PAGER=cat")
    child.sendline(f'export PS1="{PROMPT}"')
    child.expect_exact(PROMPT, timeout=timeout)
    return PROMPT


def open_shell_with_retries(
    dev: Device,
    username: str,
    password: str,
    timeout: int,
    strict_hostkey: str,
    retries: int,
    retry_delay: int,
) -> Tuple[object, str]:
    attempts = max(1, retries)
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        child = None
        try:
            if attempt > 1:
                print_progress(f"{dev.host}: SSH shell attempt {attempt}/{attempts}")
            child = connect_ssh(
                dev=dev,
                username=username,
                password=password,
                timeout=timeout,
                strict_hostkey=strict_hostkey,
            )
            prompt = prepare_shell(child, timeout=timeout)
            return child, prompt
        except Exception as exc:
            last_error = exc
            close_session(child)
            if attempt >= attempts:
                break
            print_progress(
                f"{dev.host}: SSH shell setup failed ({type(exc).__name__}: {short_error(exc)}); retrying in {retry_delay}s"
            )
            time.sleep(retry_delay)

    detail = f"{type(last_error).__name__}: {short_error(last_error)}" if last_error else "unknown error"
    raise ConnectionError(f"failed to establish SSH shell on {dev.host} after {attempts} attempt(s): {detail}")


def parse_command_result(
    raw_output: str,
    marker: str,
    command: str,
    disconnected: bool,
) -> CommandResult:
    marker_match = re.search(rf"{re.escape(marker)}(\d+)", raw_output)
    if not marker_match:
        if disconnected:
            return CommandResult(rc=None, output=raw_output.strip(), disconnected=True)
        raise RuntimeError(f"Command completion marker not found for: {command}")

    rc = int(marker_match.group(1))
    output = raw_output[:marker_match.start()].strip()
    return CommandResult(rc=rc, output=output, disconnected=disconnected)


def run_shell_command(
    child,
    command: str,
    prompt: str,
    password: str,
    timeout: int,
    marker_prefix: str,
    allow_disconnect: bool = False,
) -> CommandResult:
    marker = f"__{marker_prefix}_{datetime.now().strftime('%H%M%S%f')}__:"
    wrapped_command = f"{command}; rc=$?; printf '\\n{marker}%s\\n' \"$rc\""

    child.timeout = timeout
    child.sendline("")
    child.expect_exact(prompt, timeout=timeout)
    child.sendline(wrapped_command)

    chunks: List[str] = []
    sudo_password_sent = False
    while True:
        try:
            index = child.expect([
                prompt,
                SUDO_PASS_RE,
                r"(?i)(connection to .* closed|connection closed|closed by remote host)",
                pexpect.TIMEOUT,
                pexpect.EOF,
            ], timeout=timeout)
        except OSError:
            if allow_disconnect:
                raw_output = strip_ansi("".join(chunks)).replace("\r\n", "\n")
                return parse_command_result(raw_output, marker, command, disconnected=True)
            raise

        chunks.append(child.before or "")
        if index == 0:
            break
        if index == 1:
            if sudo_password_sent:
                raise PermissionError("sudo asked for a password more than once")
            sudo_password_sent = True
            child.sendline(password)
            continue
        if index == 2:
            chunks.append(child.after or "")
            if allow_disconnect:
                raw_output = strip_ansi("".join(chunks)).replace("\r\n", "\n")
                return parse_command_result(raw_output, marker, command, disconnected=True)
            raise ConnectionError(f"Connection closed while running command: {command}")
        if index == 3:
            raise TimeoutError(f"Timed out waiting for command to finish: {command}")
        if allow_disconnect:
            raw_output = strip_ansi("".join(chunks)).replace("\r\n", "\n")
            return parse_command_result(raw_output, marker, command, disconnected=True)
        raise ConnectionError(f"Connection closed while running command: {command}")

    raw_output = strip_ansi("".join(chunks)).replace("\r\n", "\n")
    return parse_command_result(raw_output, marker, command, disconnected=False)


def output_has_expected_line(output: str, expected: str) -> bool:
    return any(line.strip() == expected for line in (output or "").splitlines())


def run_command_in_new_session(
    dev: Device,
    username: str,
    password: str,
    command: str,
    connect_timeout: int,
    command_timeout: int,
    strict_hostkey: str,
    connect_retries: int,
    connect_retry_delay: int,
    marker_prefix: str,
) -> CommandResult:
    child = None
    try:
        child, prompt = open_shell_with_retries(
            dev=dev,
            username=username,
            password=password,
            timeout=connect_timeout,
            strict_hostkey=strict_hostkey,
            retries=connect_retries,
            retry_delay=connect_retry_delay,
        )
        return run_shell_command(
            child=child,
            command=command,
            prompt=prompt,
            password=password,
            timeout=command_timeout,
            marker_prefix=marker_prefix,
        )
    finally:
        close_session(child)


def verify_in_new_sessions(
    dev: Device,
    username: str,
    password: str,
    verify_cmd: str,
    expected_verify: Optional[str],
    connect_timeout: int,
    command_timeout: int,
    strict_hostkey: str,
    connect_retries: int,
    connect_retry_delay: int,
    verify_delay: int,
    verify_retries: int,
    log_lines: List[str],
) -> CommandResult:
    attempts = max(1, verify_retries)
    last_result: Optional[CommandResult] = None
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        if verify_delay > 0:
            print_progress(f"{dev.host}: waiting {verify_delay}s before verify attempt {attempt}/{attempts}")
            time.sleep(verify_delay)

        print_progress(f"{dev.host}: verifying hostapd attempt {attempt}/{attempts}")
        try:
            result = run_command_in_new_session(
                dev=dev,
                username=username,
                password=password,
                command=verify_cmd,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                strict_hostkey=strict_hostkey,
                connect_retries=connect_retries,
                connect_retry_delay=connect_retry_delay,
                marker_prefix=f"HOSTAPD_VERIFY_DONE_{attempt}",
            )
            last_result = result
            log_lines.extend([
                "",
                f"verify attempt: {attempt}",
                f"verify rc: {result.rc}",
                "verify output:",
                result.output or "<empty>",
            ])
            if result.rc == 0 and (
                not expected_verify or output_has_expected_line(result.output, expected_verify)
            ):
                return result
        except Exception as exc:
            last_error = exc
            log_lines.extend([
                "",
                f"verify attempt: {attempt}",
                f"verify error: {type(exc).__name__}: {exc}",
            ])

    if last_error is not None and last_result is None:
        raise RuntimeError(f"verify command failed after {attempts} attempt(s): {type(last_error).__name__}: {last_error}")
    if last_result is None:
        raise RuntimeError(f"verify command did not return a result after {attempts} attempt(s)")
    if last_result.rc != 0:
        raise RuntimeError(f"verify command failed with rc={last_result.rc}")
    raise RuntimeError(f"verify output did not contain expected line '{expected_verify}'")


def append_diagnostics(
    child,
    prompt: str,
    password: str,
    timeout: int,
    log_lines: List[str],
) -> None:
    for index, diagnostic_cmd in enumerate(DIAGNOSTIC_COMMANDS, start=1):
        log_lines.extend(["", f"diagnostic command: {diagnostic_cmd}"])
        try:
            result = run_shell_command(
                child=child,
                command=diagnostic_cmd,
                prompt=prompt,
                password=password,
                timeout=timeout,
                marker_prefix=f"HOSTAPD_DIAG_{index}",
            )
            log_lines.extend([
                f"diagnostic rc: {result.rc}",
                "diagnostic output:",
                result.output or "<empty>",
            ])
        except Exception as exc:
            log_lines.extend([
                "diagnostic result: failed",
                f"diagnostic error: {type(exc).__name__}: {exc}",
            ])


def append_diagnostics_in_new_session(
    dev: Device,
    username: str,
    password: str,
    connect_timeout: int,
    command_timeout: int,
    strict_hostkey: str,
    connect_retries: int,
    connect_retry_delay: int,
    log_lines: List[str],
) -> None:
    child = None
    try:
        child, prompt = open_shell_with_retries(
            dev=dev,
            username=username,
            password=password,
            timeout=connect_timeout,
            strict_hostkey=strict_hostkey,
            retries=connect_retries,
            retry_delay=connect_retry_delay,
        )
        append_diagnostics(
            child=child,
            prompt=prompt,
            password=password,
            timeout=command_timeout,
            log_lines=log_lines,
        )
    except Exception as exc:
        log_lines.extend([
            "",
            "diagnostics session: failed",
            f"diagnostics session error: {type(exc).__name__}: {exc}",
        ])
    finally:
        close_session(child)


def process_device(
    dev: Device,
    username: str,
    password: str,
    command: str,
    verify_cmd: str,
    expected_verify: Optional[str],
    skip_verify: bool,
    connect_timeout: int,
    command_timeout: int,
    strict_hostkey: str,
    log_dir: str,
    connect_retries: int,
    connect_retry_delay: int,
    verify_delay: int,
    verify_retries: int,
) -> DeviceResult:
    log_path = device_log_path(log_dir, dev)
    log_lines = [
        f"host: {dev.host}",
        f"port: {dev.port}",
        f"start: {datetime.now().isoformat(timespec='seconds')}",
        f"command: {command}",
        f"precheck command: {verify_cmd}",
        f"verify command: {'<skipped>' if skip_verify else verify_cmd}",
    ]
    child = None

    try:
        print_progress(f"{dev.host}: connecting")
        child, prompt = open_shell_with_retries(
            dev=dev,
            username=username,
            password=password,
            timeout=connect_timeout,
            strict_hostkey=strict_hostkey,
            retries=connect_retries,
            retry_delay=connect_retry_delay,
        )

        print_progress(f"{dev.host}: checking hostapd before restart")
        precheck_result = run_shell_command(
            child=child,
            command=verify_cmd,
            prompt=prompt,
            password=password,
            timeout=command_timeout,
            marker_prefix="HOSTAPD_PRECHECK_DONE",
        )
        log_lines.extend([
            "",
            f"precheck rc: {precheck_result.rc}",
            "precheck output:",
            precheck_result.output or "<empty>",
        ])
        if precheck_result.rc == 0 and (
            not expected_verify or output_has_expected_line(precheck_result.output, expected_verify)
        ):
            close_session(child)
            child = None
            log_lines.extend([
                "",
                "result: success",
                "detail: hostapd already active; restart skipped",
                f"end: {datetime.now().isoformat(timespec='seconds')}",
            ])
            write_device_log(log_path, log_lines)
            return DeviceResult(dev.host, "OK", "hostapd already active; restart skipped", log_path)

        print_progress(f"{dev.host}: running restart command")
        command_result = run_shell_command(
            child=child,
            command=command,
            prompt=prompt,
            password=password,
            timeout=command_timeout,
            marker_prefix="HOSTAPD_RESTART_DONE",
            allow_disconnect=True,
        )
        log_lines.extend([
            "",
            f"command rc: {command_result.rc if command_result.rc is not None else 'unknown'}",
            f"command disconnected: {command_result.disconnected}",
            "command output:",
            command_result.output or "<empty>",
        ])
        close_session(child)
        child = None

        if skip_verify:
            if command_result.rc is not None and command_result.rc != 0:
                append_diagnostics_in_new_session(
                    dev=dev,
                    username=username,
                    password=password,
                    connect_timeout=connect_timeout,
                    command_timeout=command_timeout,
                    strict_hostkey=strict_hostkey,
                    connect_retries=connect_retries,
                    connect_retry_delay=connect_retry_delay,
                    log_lines=log_lines,
                )
                raise RuntimeError(f"restart command failed with rc={command_result.rc}")
        else:
            try:
                verify_in_new_sessions(
                    dev=dev,
                    username=username,
                    password=password,
                    verify_cmd=verify_cmd,
                    expected_verify=expected_verify,
                    connect_timeout=connect_timeout,
                    command_timeout=command_timeout,
                    strict_hostkey=strict_hostkey,
                    connect_retries=connect_retries,
                    connect_retry_delay=connect_retry_delay,
                    verify_delay=verify_delay,
                    verify_retries=verify_retries,
                    log_lines=log_lines,
                )
            except Exception:
                append_diagnostics_in_new_session(
                    dev=dev,
                    username=username,
                    password=password,
                    connect_timeout=connect_timeout,
                    command_timeout=command_timeout,
                    strict_hostkey=strict_hostkey,
                    connect_retries=connect_retries,
                    connect_retry_delay=connect_retry_delay,
                    log_lines=log_lines,
                )
                raise

        if command_result.rc is not None and command_result.rc != 0:
            log_lines.extend([
                "",
                f"restart command returned rc={command_result.rc}, but verification passed",
            ])

        log_lines.extend([
            "",
            "result: success",
            f"end: {datetime.now().isoformat(timespec='seconds')}",
        ])
        write_device_log(log_path, log_lines)
        return DeviceResult(dev.host, "OK", "restart complete", log_path)
    except Exception as exc:
        log_lines.extend([
            "",
            "result: failure",
            f"error: {type(exc).__name__}: {short_error(exc)}",
            f"end: {datetime.now().isoformat(timespec='seconds')}",
        ])
        write_device_log(log_path, log_lines)
        return DeviceResult(dev.host, "FAIL", f"{type(exc).__name__}: {short_error(exc)}", log_path)
    finally:
        close_session(child)


def build_devices(args) -> List[Device]:
    host_entries: List[Tuple[str, int]] = []
    if args.device:
        host_entries.extend(args.device)
    if args.device_file:
        host_entries.extend(load_hosts_from_file(args.device_file))
    for pattern in args.device_pattern:
        for host in expand_bracket_range(pattern.strip()):
            host_entries.append((host, 22))

    host_entries = list(dict.fromkeys(host_entries))
    return [Device(host=host, port=port) for host, port in host_entries]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restart hostapd over SSH in controlled batches.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--device", action="append", type=parse_host_line, help="Repeatable: --device host[,port]")
    parser.add_argument("--device-pattern", action="append", default=[], help='Repeatable: --device-pattern "hsg17-q2-b46-t0-r[1-10]"')
    parser.add_argument("--device-file", default=None, help="File with one host or host,port per line")
    parser.add_argument("--username", default=None, help="SSH username. If omitted, prompt once")
    parser.add_argument("--cmd", default=DEFAULT_CMD, help=f"Command to run. Default: {DEFAULT_CMD}")
    parser.add_argument("--verify-cmd", default=DEFAULT_VERIFY_CMD, help=f"Verification command. Default: {DEFAULT_VERIFY_CMD}")
    parser.add_argument("--expected-verify", default=DEFAULT_EXPECTED_VERIFY, help=f"Exact line expected in verify output. Default: {DEFAULT_EXPECTED_VERIFY}")
    parser.add_argument("--skip-verify", action="store_true", help="Skip the post-restart verification command")
    parser.add_argument("--connect-timeout", type=int, default=30, help="SSH login timeout in seconds")
    parser.add_argument("--connect-retries", type=int, default=3, help="SSH connection attempts before failing a device. Default: 3")
    parser.add_argument("--connect-retry-delay", type=int, default=5, help="Seconds between SSH connection attempts. Default: 5")
    parser.add_argument("--command-timeout", type=int, default=120, help="Remote command timeout in seconds")
    parser.add_argument("--verify-delay", type=int, default=DEFAULT_VERIFY_DELAY, help=f"Seconds to wait before each verification attempt. Default: {DEFAULT_VERIFY_DELAY}")
    parser.add_argument("--verify-retries", type=int, default=DEFAULT_VERIFY_RETRIES, help=f"Number of verification attempts before failing. Default: {DEFAULT_VERIFY_RETRIES}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Devices per batch. Default: 10")
    parser.add_argument("--continue-on-failure", action="store_true", help="Continue to later batches even if a device fails")
    parser.add_argument("--strict-hostkey", default="ask", choices=["ask", "no", "yes"], help="SSH StrictHostKeyChecking value")
    parser.add_argument("--log-dir", default=None, help="Optional base directory for logs")
    parser.add_argument("--dry-run", action="store_true", help="Print expanded devices and exit without SSH")
    args = parser.parse_args()

    devices = build_devices(args)
    if not devices:
        parser.error("Provide at least one --device, --device-pattern, or --device-file")

    batch_size = max(1, args.batch_size)
    batches = chunked(devices, batch_size)

    print_progress(
        f"Prepared {len(devices)} device(s) in {len(batches)} batch(es) of up to {batch_size}"
    )
    for dev in devices:
        print(f"  {dev.host},{dev.port}" if dev.port != 22 else f"  {dev.host}")

    if args.dry_run:
        print_progress(f"Dry run only. Command would be: {args.cmd}")
        print_progress(f"Pre-check command would be: {args.verify_cmd}")
        if not args.skip_verify:
            print_progress(f"Verify command would be: {args.verify_cmd}")
        return

    username = args.username or input("Username: ").strip()
    password = getpass.getpass("Password: ")
    log_dir = make_log_dir(args.log_dir)
    print_progress(f"Logs will be written under {log_dir}")

    results_ok: List[DeviceResult] = []
    results_fail: List[DeviceResult] = []
    aborted_batches = False

    for batch_index, batch in enumerate(batches, start=1):
        print_progress(f"Starting batch {batch_index}/{len(batches)} with {len(batch)} device(s)")
        batch_results: List[DeviceResult] = []
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            future_map = {
                executor.submit(
                    process_device,
                    dev,
                    username,
                    password,
                    args.cmd,
                    args.verify_cmd,
                    args.expected_verify,
                    args.skip_verify,
                    args.connect_timeout,
                    args.command_timeout,
                    args.strict_hostkey,
                    log_dir,
                    args.connect_retries,
                    args.connect_retry_delay,
                    args.verify_delay,
                    args.verify_retries,
                ): dev
                for dev in batch
            }
            for future in as_completed(future_map):
                result = future.result()
                batch_results.append(result)
                if result.status == "OK":
                    results_ok.append(result)
                else:
                    results_fail.append(result)
                print_progress(f"{result.host}: {result.status} [{result.log_path}]")

        batch_failed = any(result.status != "OK" for result in batch_results)
        if batch_failed and not args.continue_on_failure:
            aborted_batches = True
            print_progress(f"Batch {batch_index}/{len(batches)} failed. Stopping before the next batch.")
            break

        print_progress(f"Batch {batch_index}/{len(batches)} finished")

    print("\n=== Summary ===")
    for result in sorted(results_ok, key=lambda item: item.host):
        print(f"{result.host}: OK ({result.detail}) [log: {result.log_path}]")
    for result in sorted(results_fail, key=lambda item: item.host):
        print(f"{result.host}: FAIL ({result.detail}) [log: {result.log_path}]")
    if aborted_batches:
        print("Remaining batches were not started because a prior batch failed.")

    summary_path = os.path.join(log_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("action: restart_hostapd\n")
        f.write(f"command: {args.cmd}\n")
        f.write(f"precheck_command: {args.verify_cmd}\n")
        f.write(f"devices: {len(devices)}\n")
        f.write(f"batch_size: {batch_size}\n")
        f.write(f"success: {len(results_ok)}\n")
        f.write(f"failure: {len(results_fail)}\n")
        if aborted_batches:
            f.write("remaining_batches: skipped_due_to_previous_batch_failure\n")
        f.write("\n")
        for result in sorted(results_ok, key=lambda item: item.host):
            f.write(f"{result.host}: OK ({result.detail}) [{result.log_path}]\n")
        for result in sorted(results_fail, key=lambda item: item.host):
            f.write(f"{result.host}: FAIL ({result.detail}) [{result.log_path}]\n")

    print(f"\nLogs written to: {log_dir}")
    print(f"Summary file: {summary_path}")


if __name__ == "__main__":
    main()
