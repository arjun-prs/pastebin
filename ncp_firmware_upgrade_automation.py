#!/usr/bin/env python3
"""
Run NCPCLI firmware upgrades with repeatable scope selection and run logs.

Examples:
  python3 ncp_firmware_upgrade_automation.py -r nrt --change-id CHANGE-4737261 \
    --rack nrt4:3410,nrt4:3411,nrt4:3412,nrt4:3413 \
    --devices 'nrt4-q1-b6-t0-*'

  python3 ncp_firmware_upgrade_automation.py -r nrt --change-id CHANGE-4737261 \
    --devices-from-file devices.txt --execute
"""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_PRECHECK_TAG = "test_pass"
DEFAULT_POSTCHECK_TAG = "test_pass"


def split_csv(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


def add_scope_args(command: list[str], args: argparse.Namespace) -> None:
    for rack in split_csv(args.rack):
        command.extend(["--devices-by-rack", rack])
    for role in split_csv(args.role):
        command.extend(["--devices-by-role", role])
    for state in split_csv(args.state):
        command.extend(["--device-state-matching", state])
    for pattern in split_csv(args.devices):
        command.extend(["--devices", pattern])
    for device in split_csv(args.exact_device):
        command.extend(["--exact-device", device])
    if args.devices_from_file:
        command.extend(["--devices-from-file", str(args.devices_from_file)])


def validate_scope(args: argparse.Namespace) -> None:
    has_scope = any(
        [
            args.rack,
            args.role,
            args.state,
            args.devices,
            args.exact_device,
            args.devices_from_file,
        ]
    )
    if not has_scope:
        raise SystemExit("Refusing to run without a device scope. Add rack/device/file/role/state filters.")

    if args.execute and not args.change_id.startswith("CHANGE-"):
        raise SystemExit("For --execute, --change-id must look like CHANGE-1234567.")

    if args.execute and args.yes != args.change_id:
        raise SystemExit(f"For --execute, add --yes {args.change_id} to confirm the real upgrade.")


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_streaming(command: list[str], log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {shell_join(command)}\n\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
        return process.wait()


def build_base_ncpcli(args: argparse.Namespace) -> list[str]:
    command: list[str] = []
    if args.pyenv_version:
        command.extend(["env", f"PYENV_VERSION={args.pyenv_version}"])
    command.extend([args.ncpcli_bin, "-r", args.region])
    if args.use_agent_for_auth:
        command.extend(["-o", "use_agent_for_auth=true"])
    return command


def build_list_command(args: argparse.Namespace) -> list[str]:
    command = [*build_base_ncpcli(args), "devices", "list"]
    add_scope_args(command, args)
    return command


def build_upgrade_command(args: argparse.Namespace) -> list[str]:
    command = [
        *build_base_ncpcli(args),
        "devices",
        "firmware",
        "upgrade",
    ]
    add_scope_args(command, args)
    command.extend(
        [
            "--precheck-tag",
            args.precheck_tag,
            "--postcheck-tag",
            args.postcheck_tag,
            "--change-id",
            args.change_id,
            "--skip-validators-per-device",
            "--skip-traffic-shift",
            "--skip-impact-checks",
            "--skip-optional-prechecks",
            "--optimized",
            "--disable-hitless",
            "--disable-crawl-walk",
            "--batch-size",
            str(args.batch_size),
            "--pause-time",
            str(args.pause_time),
            "--post-healthcheck-retry-limit",
            str(args.post_healthcheck_retry_limit),
            "--tailf",
        ]
    )
    command.append("--not-dry-run" if args.execute else "--dry-run")
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and run ncpcli devices firmware upgrade with a repeatable scope.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-r", "--region", required=True, help="NCPCLI working region, for example nrt.")
    parser.add_argument("--change-id", required=True, help="Change ticket, for example CHANGE-4737261.")
    parser.add_argument("--rack", action="append", help="Rack selector, repeatable or comma-separated, e.g. nrt4:3410.")
    parser.add_argument("--role", action="append", help="Device role selector, repeatable or comma-separated.")
    parser.add_argument("--state", action="append", help="Device state selector, repeatable or comma-separated.")
    parser.add_argument("--devices", action="append", help="Device glob selector, repeatable or comma-separated.")
    parser.add_argument("--exact-device", action="append", help="Exact device name, repeatable or comma-separated.")
    parser.add_argument("--devices-from-file", type=Path, help="File containing device names.")
    parser.add_argument("--execute", action="store_true", help="Run with --not-dry-run. Default is dry-run.")
    parser.add_argument("--yes", help="Must equal the change ID when --execute is used.")
    parser.add_argument("--precheck-tag", default=DEFAULT_PRECHECK_TAG)
    parser.add_argument("--postcheck-tag", default=DEFAULT_POSTCHECK_TAG)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--pause-time", type=int, default=0)
    parser.add_argument("--post-healthcheck-retry-limit", type=int, default=3)
    parser.add_argument("--ncpcli-bin", default="ncpcli")
    parser.add_argument("--pyenv-version", default="ncpcli-env", help="Set empty string to avoid PYENV_VERSION.")
    parser.add_argument("--no-agent-auth", dest="use_agent_for_auth", action="store_false")
    parser.add_argument("--log-dir", type=Path, default=Path("upgrade-logs"))
    parser.add_argument("--skip-preview", action="store_true", help="Do not run devices list before upgrade.")
    parser.add_argument("--plan-only", action="store_true", help="Print commands and exit without running ncpcli.")
    parser.set_defaults(use_agent_for_auth=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_scope(args)

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = args.log_dir / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"Run logs: {log_dir.resolve()}")
    print(f"Mode: {'EXECUTE (--not-dry-run)' if args.execute else 'DRY-RUN'}")

    list_command = build_list_command(args)
    upgrade_command = build_upgrade_command(args)

    if args.plan_only:
        if not args.skip_preview:
            print("\nDevice preview command:")
            print(shell_join(list_command))
        print("\nFirmware upgrade command:")
        print(shell_join(upgrade_command))
        return 0

    if not args.skip_preview:
        print("\nPreviewing selected devices:")
        print(shell_join(list_command))
        rc = run_streaming(list_command, log_dir / "devices-list.log")
        if rc != 0:
            print(f"\nDevice preview failed with exit code {rc}. Upgrade not started.", file=sys.stderr)
            return rc

    print("\nFirmware upgrade command:")
    print(shell_join(upgrade_command))
    return run_streaming(upgrade_command, log_dir / "firmware-upgrade.log")


if __name__ == "__main__":
    raise SystemExit(main())
