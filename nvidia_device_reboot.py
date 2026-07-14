#!/usr/bin/env python3
"""
nvidia_device_reboot.py

Purpose:
Reboot NVIDIA devices in controlled batches, wait for each device to come back,
and stop before the next batch if any device in the current batch does not
recover successfully.

What the script does:
- prompts once for the SSH username unless --username is provided
- fetches the password from `jitpw -e <region>`
- reboots devices in parallel within each batch
- automatically answers the reboot confirmation prompt
- verifies the device is back by SSHing in again and running a check command
- writes one log file per device plus a summary.txt file
- stops before the next batch if any device in the current batch fails

Accepted device inputs:
- --device host
- --device host,port
- --device-file /path/to/file
- --device-pattern "aga5-q2-p1-t1-r[1-10]"

Device file format:
- one entry per line
- each line may be `host` or `host,port`
- blank lines and lines starting with `#` are ignored

Example:
    python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py \
      --device-file /Users/tusharkeskar/Desktop/device.txt \
      -r aga \
      --batch-size 5

Example output:
    Username: tkeskar
    [15:58:43] Starting reboot run for 96 device(s) in 20 batch(es) of up to 5
    [15:58:43] Logs will be written under /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot_logs/20260421_155843
    [15:58:43] Starting batch 1/20 with 5 device(s)
    [15:58:43] aga5-q2-p1-t1-r1: starting reboot
    [15:58:45] aga5-q2-p1-t1-r1: issuing reboot command
    [15:58:45] aga5-q2-p1-t1-r1: reboot confirmation prompt detected, sending 'y'
    [15:58:47] aga5-q2-p1-t1-r1: waiting 60s before up-check
    [15:59:47] aga5-q2-p1-t1-r1: verify attempt 1
    [16:00:02] aga5-q2-p1-t1-r1: still waiting for device to come back
    [16:02:07] aga5-q2-p1-t1-r1: verify attempt 5
    [16:02:09] aga5-q2-p1-t1-r1: device is back up
    [16:02:09] aga5-q2-p1-t1-r1: reboot complete and verified
    [16:02:10] Batch 1/20 completed successfully

Help:
    python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py --help
"""
import argparse
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pexpect


HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE = r"(?i)(username|login)[: ]"
PASS_RE = r"(?i)password.*:\s*$"
CONFIRM_RE = r"(?i)(continue\?\s*\[y/N\]|continue\?\s*\[yes/no\]|are you sure.*\[[yY]/[nN]\])"
PROMPT = "PEXPECT_PROMPT> "
MAX_WORKERS = 10
DEFAULT_REBOOT_CMD = "nv action reboot system mode fast"
DEFAULT_VERIFY_CMD = "echo REBOOT_CHECK_OK"


@dataclass(frozen=True)
class Device:
    host: str
    port: int = 22


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
    root = base_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "nvidia_device_reboot_logs")
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


def get_jitpw_path() -> str:
    jitpw_path = subprocess.run(["which", "jitpw"], capture_output=True, text=True).stdout.strip()
    if jitpw_path:
        return jitpw_path

    fallback_paths = [
        os.path.expanduser("~/tools/jitpw/bin/jitpw"),
        os.path.expanduser("~/bin/jitpw"),
        "/usr/local/bin/jitpw",
    ]
    for path in fallback_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise FileNotFoundError("jitpw not found in PATH or fallback paths.")


def get_jitpw_password(region: str) -> str:
    jitpw_path = get_jitpw_path()
    result = subprocess.run([jitpw_path, "-e", region], capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"jitpw -e {region} failed: {stderr or 'no stderr'}")

    password = (result.stdout or "").strip()
    if not password:
        raise RuntimeError(f"jitpw -e {region} returned an empty password")
    return password


def expand_bracket_range(pattern: str) -> List[str]:
    match = re.search(r"\[(\d+)-(\d+)\]", pattern)
    if not match:
        return [pattern]

    start_s, end_s = match.group(1), match.group(2)
    start, end = int(start_s), int(end_s)
    if start > end:
        raise ValueError(f"Invalid range in {pattern}: start > end")

    width = max(len(start_s), len(end_s))
    return [
        pattern[:match.start()] + str(i).zfill(width) + pattern[match.end():]
        for i in range(start, end + 1)
    ]


def parse_host_line(s: str) -> Tuple[str, int]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) == 1:
        return parts[0], 22
    if len(parts) == 2:
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
            except Exception as e:
                raise ValueError(f"{path}:{lineno}: invalid host line '{line}': {e}") from e
    return hosts


def connect_ssh(host: str, username: str, password: str, port: int = 22, timeout: int = 30):
    ssh_cmd = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout={timeout} -p {port} {username}@{host}"
    )
    child = pexpect.spawn(ssh_cmd, encoding="utf-8", timeout=timeout)

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
            raise TimeoutError(f"Login timed out connecting to {host}")
        else:
            raise ConnectionError(f"SSH connection closed unexpectedly for {host}")
        return child

    if index == 1:
        child.sendline(username)
        child.expect(PASS_RE)
        child.sendline(password)
    elif index == 2:
        child.sendline(password)
    elif index == 3:
        raise TimeoutError(f"Login timed out connecting to {host}")
    else:
        raise ConnectionError(f"SSH connection closed unexpectedly for {host}")
    return child


def close_session(child) -> None:
    try:
        if child is not None and child.isalive():
            child.sendline("exit")
            try:
                child.expect(pexpect.EOF, timeout=10)
            except pexpect.TIMEOUT:
                pass
    finally:
        if child is not None:
            child.close()


def nvidia_prepare(child, timeout: int = 20) -> str:
    child.sendline("")
    child.expect([r"(?m)^.*[$#]\s*$", pexpect.TIMEOUT], timeout=5)
    child.sendline('export PROMPT_COMMAND=""')
    child.sendline('export TERM=dumb')
    child.sendline('export PAGER=cat')
    child.sendline(f'export PS1="{PROMPT}"')
    child.expect_exact(PROMPT, timeout=timeout)
    return PROMPT


def nvidia_run_command(child, cmd: str, prompt: str, timeout: int = 60) -> str:
    child.timeout = timeout
    child.sendline("")
    child.expect_exact(prompt, timeout=timeout)
    child.sendline(cmd)
    try:
        child.expect_exact(cmd, timeout=5)
        child.expect(r"\r?\n", timeout=5)
    except pexpect.TIMEOUT:
        pass
    child.expect_exact(prompt, timeout=timeout)
    return (child.before or "").strip()


def nvidia_run_reboot(child, cmd: str, prompt: str, host: str, timeout: int = 60) -> Tuple[str, str]:
    child.timeout = timeout
    child.sendline("")
    child.expect_exact(prompt, timeout=timeout)
    child.sendline(cmd)

    try:
        child.expect_exact(cmd, timeout=5)
        child.expect(r"\r?\n", timeout=5)
    except pexpect.TIMEOUT:
        pass

    chunks: List[str] = []
    confirmation_sent = False
    while True:
        events = [
            prompt,
            CONFIRM_RE,
            r"(?i)(connection to .* closed|connection closed|closed by remote host)",
            pexpect.EOF,
            pexpect.TIMEOUT,
        ]
        index = child.expect(events, timeout=timeout)
        chunks.append(child.before or "")

        if index == 0:
            completion = "prompt"
            break
        if index == 1:
            print_progress(f"{host}: reboot confirmation prompt detected, sending 'y'")
            child.sendline("y")
            confirmation_sent = True
            continue
        if index in (2, 3):
            completion = "disconnect"
            break
        raise TimeoutError(f"Timed out waiting for reboot command to finish: {cmd}")

    output = "".join(chunks).strip()
    if confirmation_sent:
        completion = f"{completion}, confirmed"
    return output, completion


def verify_device_up(
    dev: Device,
    username: str,
    password: str,
    initial_wait: int,
    retry_interval: int,
    verify_timeout: int,
    verify_cmd: str,
    log_lines: List[str],
) -> str:
    deadline = time.time() + verify_timeout
    attempt = 0

    print_progress(f"{dev.host}: waiting {initial_wait}s before up-check")
    time.sleep(initial_wait)

    while time.time() < deadline:
        attempt += 1
        print_progress(f"{dev.host}: verify attempt {attempt}")
        child = None
        try:
            child = connect_ssh(dev.host, username, password, port=dev.port, timeout=min(retry_interval, 15))
            prompt = nvidia_prepare(child, timeout=20)
            output = nvidia_run_command(child, verify_cmd, prompt, timeout=30)
            log_lines.extend([
                "",
                f"verify attempt: {attempt}",
                f"verify command: {verify_cmd}",
                "verify output:",
                output or "<empty>",
            ])
            if "REBOOT_CHECK_OK" in output:
                print_progress(f"{dev.host}: device is back up")
                return f"device reachable after reboot (attempt {attempt})"
            raise RuntimeError("verification command did not return expected marker")
        except Exception as e:
            log_lines.extend([
                "",
                f"verify attempt: {attempt}",
                f"verify error: {type(e).__name__}: {e}",
            ])
            print_progress(f"{dev.host}: still waiting for device to come back")
            time.sleep(retry_interval)
        finally:
            close_session(child)

    raise TimeoutError(f"device did not come back within {verify_timeout}s")


def process_device(
    dev: Device,
    username: str,
    password: str,
    reboot_cmd: str,
    log_dir: str,
    reboot_timeout: int,
    initial_wait: int,
    retry_interval: int,
    verify_timeout: int,
    verify_cmd: str,
) -> DeviceResult:
    log_path = device_log_path(log_dir, dev)
    log_lines = [
        f"host: {dev.host}",
        f"port: {dev.port}",
        f"action: reboot",
        f"start: {datetime.now().isoformat(timespec='seconds')}",
        f"reboot command: {reboot_cmd}",
    ]

    child = None
    try:
        print_progress(f"{dev.host}: starting reboot")
        child = connect_ssh(dev.host, username, password, port=dev.port, timeout=30)
        prompt = nvidia_prepare(child, timeout=20)
        print_progress(f"{dev.host}: issuing reboot command")
        reboot_output, completion = nvidia_run_reboot(child, reboot_cmd, prompt, dev.host, timeout=reboot_timeout)
        log_lines.extend([
            f"reboot completion: {completion}",
            "reboot output:",
            reboot_output or "<empty>",
        ])
    except Exception as e:
        log_lines.extend([
            "",
            "result: failure",
            f"error: {type(e).__name__}: {e}",
            f"end: {datetime.now().isoformat(timespec='seconds')}",
        ])
        write_device_log(log_path, log_lines)
        print_progress(f"{dev.host}: failed during reboot command ({type(e).__name__}: {e})")
        return DeviceResult(dev.host, "FAIL", f"reboot failed: {type(e).__name__}: {e}", log_path)
    finally:
        if child is not None:
            child.close(force=True)

    try:
        verification_detail = verify_device_up(
            dev=dev,
            username=username,
            password=password,
            initial_wait=initial_wait,
            retry_interval=retry_interval,
            verify_timeout=verify_timeout,
            verify_cmd=verify_cmd,
            log_lines=log_lines,
        )
        log_lines.extend([
            "",
            "result: success",
            f"detail: {verification_detail}",
            f"end: {datetime.now().isoformat(timespec='seconds')}",
        ])
        write_device_log(log_path, log_lines)
        print_progress(f"{dev.host}: reboot complete and verified")
        return DeviceResult(dev.host, "OK", verification_detail, log_path)
    except Exception as e:
        log_lines.extend([
            "",
            "result: failure",
            f"error: {type(e).__name__}: {e}",
            f"end: {datetime.now().isoformat(timespec='seconds')}",
        ])
        write_device_log(log_path, log_lines)
        print_progress(f"{dev.host}: reboot issued but up-check failed ({type(e).__name__}: {e})")
        return DeviceResult(dev.host, "FAIL", f"reboot issued, verify failed: {type(e).__name__}: {e}", log_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reboot NVIDIA devices, verify they come back over SSH, and gate progress batch-by-batch.",
        epilog=(
            "Examples:\n"
            "  python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py "
            "--device-file /Users/tusharkeskar/Desktop/device.txt -r aga\n"
            "  python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py "
            "--device aga5-q2-p1-t1-r10 --device aga5-q2-p1-t1-r11 -r aga --batch-size 2\n"
            "  python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py "
            "--device-pattern \"aga5-q2-p1-t1-r[1-10]\" -r aga --initial-wait 90 --verify-timeout 1200\n\n"
            "Behavior:\n"
            "  - prompts once for username unless --username is supplied\n"
            "  - gets the password from jitpw -e <region>\n"
            "  - runs devices in parallel inside each batch\n"
            "  - starts the next batch only if the current batch fully succeeds\n"
            "  - writes per-device logs and summary.txt under the log directory"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--device", action="append", type=parse_host_line, help="Repeatable device entry: host or host,port")
    parser.add_argument("--device-pattern", action="append", default=[], help='Repeatable host pattern, for example: "aga5-q2-p1-t1-r[1-10]"')
    parser.add_argument("--device-file", default=None, help="Path to file with one device per line in host or host,port format")
    parser.add_argument("--username", default=None, help="SSH username. If omitted, prompt once at startup")
    parser.add_argument("-r", "--region", required=True, help="Region passed to jitpw -e <region>, for example: aga")
    parser.add_argument("--cmd", default=DEFAULT_REBOOT_CMD, help=f"Reboot command to run on the device. Default: {DEFAULT_REBOOT_CMD}")
    parser.add_argument("--verify-cmd", default=DEFAULT_VERIFY_CMD, help=f"Command used after reboot to confirm the device is up. Default: {DEFAULT_VERIFY_CMD}")
    parser.add_argument("--reboot-timeout", type=int, default=90, help="Seconds to wait for reboot command and confirmation handling")
    parser.add_argument("--initial-wait", type=int, default=60, help="Seconds to wait after reboot before starting SSH up-checks")
    parser.add_argument("--retry-interval", type=int, default=20, help="Seconds between SSH up-check attempts")
    parser.add_argument("--verify-timeout", type=int, default=900, help="Total seconds allowed for a device to come back after reboot")
    parser.add_argument("--log-dir", default=None, help="Optional base directory for per-device logs and summary.txt")
    parser.add_argument("--batch-size", type=int, default=10, help="Devices per batch. Next batch starts only if the current batch fully succeeds")
    args = parser.parse_args()

    host_entries: List[Tuple[str, int]] = []
    if args.device:
        host_entries.extend(args.device)
    if args.device_file:
        host_entries.extend(load_hosts_from_file(args.device_file))
    for pattern in args.device_pattern:
        for host in expand_bracket_range(pattern.strip()):
            host_entries.append((host, 22))

    host_entries = list(dict.fromkeys(host_entries))
    if not host_entries:
        parser.error("Provide at least one --device, --device-pattern, or --device-file")

    username = args.username or input("Username: ").strip()
    password = get_jitpw_password(args.region)
    log_dir = make_log_dir(args.log_dir)
    devices = [Device(host=host, port=port) for host, port in host_entries]
    batch_size = max(1, min(args.batch_size, MAX_WORKERS))
    batches = chunked(devices, batch_size)

    print_progress(
        f"Starting reboot run for {len(devices)} device(s) in {len(batches)} batch(es) of up to {batch_size}"
    )
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
                    log_dir,
                    args.reboot_timeout,
                    args.initial_wait,
                    args.retry_interval,
                    args.verify_timeout,
                    args.verify_cmd,
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
        if batch_failed:
            aborted_batches = True
            print_progress(f"Batch {batch_index}/{len(batches)} failed. Stopping before the next batch.")
            break

        print_progress(f"Batch {batch_index}/{len(batches)} completed successfully")

    print("\n=== Summary ===")
    for result in sorted(results_ok, key=lambda item: item.host):
        print(f"{result.host}: OK ({result.detail}) [log: {result.log_path}]")
    for result in sorted(results_fail, key=lambda item: item.host):
        print(f"{result.host}: FAIL ({result.detail}) [log: {result.log_path}]")
    if aborted_batches:
        print("Remaining batches were not started because a prior batch did not fully recover.")

    summary_path = os.path.join(log_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("action: reboot\n")
        f.write(f"devices: {len(devices)}\n")
        f.write(f"batch_size: {batch_size}\n")
        f.write(f"success: {len(results_ok)}\n")
        f.write(f"failure: {len(results_fail)}\n\n")
        if aborted_batches:
            f.write("remaining_batches: skipped_due_to_previous_batch_failure\n\n")
        for result in sorted(results_ok, key=lambda item: item.host):
            f.write(f"{result.host}: OK ({result.detail}) [{result.log_path}]\n")
        for result in sorted(results_fail, key=lambda item: item.host):
            f.write(f"{result.host}: FAIL ({result.detail}) [{result.log_path}]\n")

    print(f"\nLogs written to: {log_dir}")
    print(f"Summary file: {summary_path}")


if __name__ == "__main__":
    main()
