#!/usr/bin/env python3
"""Check NVIDIA/Cumulus swp subinterfaces and optionally bounce bad lanes."""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import pexpect


DEFAULT_WAIT_SECONDS = 20
DEFAULT_TIMEOUT_SECONDS = 90


@dataclass
class InterfaceRow:
    name: str
    admin_state: str
    oper_state: str
    raw_line: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use dssh to find swp subinterfaces that are not up/up, show their "
            "transceiver details, and optionally bounce them."
        )
    )
    parser.add_argument("device", nargs="?", help="Device name, for example jbp15-q2-p4-t0-r193")
    parser.add_argument("interface", nargs="?", help="Base interface in swpX format, for example swp43")
    parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_WAIT_SECONDS,
        help=f"Seconds to wait after a bounce before rechecking status. Default: {DEFAULT_WAIT_SECONDS}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout per dssh command in seconds. Default: {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--no-bounce",
        action="store_true",
        help="Only print bad interfaces and transceiver output; do not prompt or bounce.",
    )
    parser.add_argument("--dssh", default="dssh", help="Path/name of dssh executable. Default: dssh")
    return parser.parse_args()


def prompt_missing(value: Optional[str], prompt: str) -> str:
    if value:
        return value.strip()
    return input(prompt).strip()


def validate_device(device: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", device):
        raise ValueError(f"Invalid device name: {device!r}")


def validate_base_interface(interface: str) -> None:
    if not re.fullmatch(r"swp\d+", interface):
        raise ValueError(f"Interface must be in swpX format, got: {interface!r}")


def strip_dssh_noise(output: str) -> str:
    text = re.sub(r"\x1b\]0;.*?\x07", "", output or "")
    text = text.replace("\r", "")
    cleaned: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("spawn ssh "):
            continue
        if line.startswith("Warning: Permanently added "):
            continue
        if line.startswith("Welcome to NVIDIA Cumulus"):
            continue
        if line.endswith("'s password:") or "'s password:" in line:
            continue
        cleaned.append(raw_line.rstrip())
    return "\n".join(cleaned).strip()


def run_dssh(dssh: str, device: str, command: str, timeout: int) -> Tuple[int, str]:
    child = pexpect.spawn(dssh, [device, command], encoding="utf-8", timeout=timeout)
    try:
        child.expect(pexpect.EOF)
        output = child.before
    except pexpect.TIMEOUT as exc:
        child.close(force=True)
        raise TimeoutError(f"Timed out after {timeout}s running: {command}") from exc
    finally:
        if child.isalive():
            child.close()
    status = child.exitstatus if child.exitstatus is not None else 1
    return status, strip_dssh_noise(output)


def parse_interface_rows(output: str, base_interface: str) -> List[InterfaceRow]:
    rows: List[InterfaceRow] = []
    pattern = re.compile(rf"^({re.escape(base_interface)}(?:s\d+)?)\s+(\S+)\s+(\S+)\b")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        match = pattern.match(line)
        if not match:
            continue
        name, admin_state, oper_state = match.groups()
        rows.append(
            InterfaceRow(
                name=name,
                admin_state=admin_state.lower(),
                oper_state=oper_state.lower(),
                raw_line=line,
            )
        )
    return rows


def bad_rows(rows: Iterable[InterfaceRow]) -> List[InterfaceRow]:
    return [row for row in rows if row.admin_state != "up" or row.oper_state != "up"]


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def print_command_output(title: str, output: str) -> None:
    print(f"\n$ {title}")
    print(output if output else "<no output>")


def run_checked(dssh: str, device: str, command: str, timeout: int) -> str:
    status, output = run_dssh(dssh, device, command, timeout)
    print_command_output(command, output)
    if status != 0:
        print(f"WARNING: command exited with status {status}: {command}", file=sys.stderr)
    return output


def bounce_interface(args: argparse.Namespace, interface: str) -> None:
    commands = [
        f"nv set interface {interface} link state down",
        "nv config apply",
        f"nv set interface {interface} link state up",
        "nv config apply",
    ]
    for command in commands:
        run_checked(args.dssh, args.device, command, args.timeout)

    print(f"\nWaiting {args.wait}s before rechecking {interface}...")
    time.sleep(max(0, args.wait))
    run_checked(args.dssh, args.device, f"nv show interface | grep {interface}", args.timeout)


def main() -> int:
    args = parse_args()
    args.device = prompt_missing(args.device, "Device: ")
    args.interface = prompt_missing(args.interface, "Interface (swpX): ")

    try:
        validate_device(args.device)
        validate_base_interface(args.interface)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    show_command = f"nv show interface | grep {args.interface}"
    status, output = run_dssh(args.dssh, args.device, show_command, args.timeout)
    print_command_output(show_command, output)
    if status != 0:
        print(f"ERROR: command exited with status {status}: {show_command}", file=sys.stderr)
        return 1

    rows = parse_interface_rows(output, args.interface)
    if not rows:
        print(f"\nNo interfaces matching {args.interface} were found in command output.")
        return 1

    bad = bad_rows(rows)
    if not bad:
        print(f"\nAll matching interfaces for {args.interface} are up/up.")
        return 0

    print(f"\nInterfaces not in up/up state for {args.interface}:")
    for row in bad:
        print(row.raw_line)

    for row in bad:
        if not re.fullmatch(r"swp\d+s\d+", row.name):
            print(f"\nSkipping bounce prompt for {row.name}: not a swpXsY subinterface.")
            continue

        run_checked(args.dssh, args.device, f"nv show interface {row.name} transceiver", args.timeout)
        if args.no_bounce:
            continue
        if confirm(f"Bounce {row.name}?"):
            bounce_interface(args, row.name)
        else:
            print(f"Skipping bounce for {row.name}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
