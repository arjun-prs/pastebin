#!/usr/bin/env python3
"""
Enable or disable Cumulus/NVIDIA link flap protection on T1 interfaces whose
peer T0 is not in service.

Default behavior is dry-run:
  - derive fabric/plane scope from the target T1 device names
  - discover all T0s for that scope
  - discover in-service T0s for the fabric
  - discover target T1s from --device-name, --device-pattern, or --device-from-file
  - read each T1 running NVUE commands
  - parse interface descriptions with peer_device=<t0>
  - exclude interfaces whose peer T0 is in service
  - print the compressed nv command that would run

Use --apply to push config.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import getpass
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

try:
    import pexpect
except ImportError:  # pragma: no cover - handled at runtime for live SSH modes
    pexpect = None


HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE = r"(?i)(username|login)[: ]"
PASS_RE = r"(?i)password.*:\s*$"
SSH_PROMPT_RE = r"(?m)^.*[$#]\s*$"
SSH_DENIED_RE = r"(?i)permission denied"
DEFAULT_TIMEOUT = 60
DEFAULT_WORKERS = 8


@dataclass(frozen=True)
class PeerLink:
    interface: str
    peer_device: str
    peer_interface: str
    raw_line: str


@dataclass
class DevicePlan:
    device: str
    eligible_links: List[PeerLink]
    already_disabled: Set[str]
    skipped_in_service: List[PeerLink]
    skipped_unknown: List[PeerLink]
    commands: List[str]
    applied: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class DeviceScope:
    ncpcli_region: str
    fabric_prefix: str
    device_prefix: str
    t0_pattern: str
    t1_pattern: str


DEVICE_RE = re.compile(
    r"^(?P<root>[a-z]+\d+)-(?P<index>q\d+)-(?P<plane>p\d+)-(?P<tier>t[01])-r(?P<rack>\d+)$",
    re.IGNORECASE,
)


def normalize_prefix(prefix: str) -> str:
    return prefix.strip().rstrip("*").rstrip("-")


def derive_ncpcli_region(site_root: str) -> str:
    match = re.match(r"^([a-z]+)", site_root.strip().lower())
    if not match:
        raise ValueError(f"cannot derive ncpcli region from {site_root}")
    return match.group(1)


def scope_from_device_name(device: str) -> DeviceScope:
    match = DEVICE_RE.fullmatch(device.strip())
    if not match:
        raise ValueError(f"invalid device name: {device}")
    if match.group("tier").lower() != "t1":
        raise ValueError(f"device is not a T1 hostname: {device}")

    root = match.group("root").lower()
    index = match.group("index").lower()
    plane = match.group("plane").lower()
    fabric_prefix = f"{root}-{index}"
    device_prefix = f"{fabric_prefix}-{plane}"
    return DeviceScope(
        ncpcli_region=derive_ncpcli_region(root),
        fabric_prefix=fabric_prefix,
        device_prefix=device_prefix,
        t0_pattern=f"{device_prefix}-t0-r*",
        t1_pattern=f"{device_prefix}-t1-r*",
    )


def scope_from_device_pattern(pattern: str) -> DeviceScope:
    match = re.search(
        r"(?P<root>[a-z]+\d+)-(?P<index>q\d+)-(?P<plane>p\d+)-t1-",
        pattern,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "--device-pattern must include a concrete T1 prefix like jbp15-q2-p1-t1-"
        )

    sample = f"{match.group('root')}-{match.group('index')}-{match.group('plane')}-t1-r1"
    return scope_from_device_name(sample.lower())


def validate_single_scope(devices: Sequence[str]) -> DeviceScope:
    if not devices:
        raise ValueError("no T1 devices were supplied")
    scopes = [scope_from_device_name(device) for device in devices]
    first = scopes[0]
    for scope in scopes[1:]:
        if scope.device_prefix != first.device_prefix:
            raise ValueError(
                "all target devices must be in the same fabric plane; "
                f"got {first.device_prefix} and {scope.device_prefix}"
            )
    return first


def compile_device_pattern(pattern: str):
    regex_only_chars = set("()[]\\+^$|.")
    if any(char in pattern for char in regex_only_chars):
        return re.compile(pattern)
    return re.compile(fnmatch.translate(pattern))


def run_cmd(cmd: Sequence[str], check: bool = True) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{output.strip()}")
    return output


def get_jitpw_path(explicit_path: Optional[str] = None) -> str:
    candidates = [
        explicit_path,
        shutil.which("jitpw"),
        str(Path("~/tools/jitpw/bin/jitpw").expanduser()),
        str(Path("~/bin/jitpw").expanduser()),
        "/Users/surjeetsingh/tools/jitpw/bin/jitpw",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os_access_executable(candidate):
            return candidate
    raise FileNotFoundError("jitpw not found in PATH or expected local paths")


def os_access_executable(path: str) -> bool:
    return Path(path).exists() and Path(path).is_file() and os.access(path, os.X_OK)


def get_jitpw_password(target: str, jitpw_path: Optional[str] = None,
                       scope: str = "region", transform: str = "none") -> str:
    if scope == "region":
        cmd = [get_jitpw_path(jitpw_path), "-e", target]
    else:
        cmd = [get_jitpw_path(jitpw_path), "-qe", target]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    password = (proc.stdout or "").strip()
    if proc.returncode != 0 or not password:
        details = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"jitpw failed for {target}: {details}")
    if transform == "lower":
        return password.lower()
    if transform == "upper":
        return password.upper()
    return password


def ncpcli_devices_list(region: str, pattern: str, state: Optional[str] = None) -> str:
    cmd = ["ncpcli", "-r", region, "devices", "list", "--devices", pattern]
    if state:
        cmd.extend(["--state", state])
    return run_cmd(cmd)


def parse_device_names(text: str, tier: Optional[int] = None, prefix: Optional[str] = None) -> List[str]:
    """
    Parse ncpcli table output or a plain device-name file.

    The table formats vary across ncpcli versions, so use a conservative device
    regex and then filter by prefix/tier instead of relying on column positions.
    """
    if tier is None:
        pattern = r"\b[a-z0-9]+-q\d+-p\d+-t[01]-r\d+\b"
    else:
        pattern = rf"\b[a-z0-9]+-q\d+-p\d+-t{tier}-r\d+\b"

    seen: Set[str] = set()
    devices: List[str] = []
    for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
        name = match.group(0)
        if prefix and not name.startswith(normalize_prefix(prefix) + "-"):
            continue
        if name not in seen:
            seen.add(name)
            devices.append(name)
    return devices


def read_device_file(path: str, tier: Optional[int] = None, prefix: Optional[str] = None) -> List[str]:
    return parse_device_names(Path(path).read_text(encoding="utf-8"), tier=tier, prefix=prefix)


def expand_rack_values(values: Optional[Sequence[str]]) -> List[str]:
    racks: List[str] = []
    for value in values or []:
        for token in re.split(r"[,\s]+", value.strip()):
            token = token.strip()
            if not token:
                continue
            range_match = re.fullmatch(r"(\d+)-(\d+)", token)
            if range_match:
                start_s, end_s = range_match.groups()
                start, end = int(start_s), int(end_s)
                if start > end:
                    raise ValueError(f"invalid rack range {token}: start > end")
                width = max(len(start_s), len(end_s))
                racks.extend(str(idx).zfill(width) for idx in range(start, end + 1))
            else:
                racks.append(token)
    return list(dict.fromkeys(racks))


def racktopo_devices(region: str, racks: Sequence[str]) -> List[str]:
    script = Path(__file__).with_name("multiplaner_racktopo.py")
    devices: List[str] = []
    seen: Set[str] = set()
    for rack in expand_rack_values(racks):
        proc = subprocess.run(
            ["python3", str(script), "-r", region, "--rack", rack],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"multiplaner_racktopo.py failed for rack {rack}: {details}")
        for device in parse_device_names(proc.stdout or "", tier=1):
            if device not in seen:
                seen.add(device)
                devices.append(device)
    return devices


def is_t0_device(name: str) -> bool:
    return bool(re.search(r"-t0-r\d+$", name))


def is_t1_device(name: str) -> bool:
    return bool(re.search(r"-t1-r\d+$", name))


def parse_peer_links(config_text: str) -> List[PeerLink]:
    links: List[PeerLink] = []
    seen: Set[Tuple[str, str]] = set()

    for line in (config_text or "").splitlines():
        line = line.strip()
        match = re.match(r"^nv\s+set\s+interface\s+(\S+)\s+description\s+(.+)$", line)
        if not match:
            continue

        interface, desc = match.groups()
        peer_match = re.search(r"\bpeer_device=([^\s'\"]+)", desc)
        if not peer_match:
            continue

        peer_device = peer_match.group(1)
        if not is_t0_device(peer_device):
            continue

        peer_if_match = re.search(r"\bpeer_(?:interface|port)=([^\s'\"]+)", desc)
        peer_interface = peer_if_match.group(1) if peer_if_match else ""
        key = (interface, peer_device)
        if key in seen:
            continue
        seen.add(key)
        links.append(PeerLink(interface=interface, peer_device=peer_device,
                              peer_interface=peer_interface, raw_line=line))
    return links


def parse_disabled_interfaces(config_text: str) -> Set[str]:
    disabled: Set[str] = set()
    for line in (config_text or "").splitlines():
        line = line.strip()
        match = re.match(
            r"^nv\s+set\s+interface\s+(\S+)\s+link\s+flap-protection\s+state\s+disabled\b",
            line,
        )
        if not match:
            continue
        disabled.update(expand_interface_csv(match.group(1)))
    return disabled


def expand_interface_csv(value: str) -> List[str]:
    interfaces: List[str] = []
    for part in value.split(","):
        interfaces.extend(expand_interface_token(part.strip()))
    return interfaces


def expand_interface_token(value: str) -> List[str]:
    if not value:
        return []

    split_match = re.fullmatch(r"(swp\d+s)(\d+)-(\d+)", value)
    if split_match:
        prefix, start_s, end_s = split_match.groups()
        start, end = int(start_s), int(end_s)
        if start <= end:
            return [f"{prefix}{idx}" for idx in range(start, end + 1)]

    port_match = re.fullmatch(r"(swp)(\d+)-(\d+)(.*)", value)
    if port_match:
        prefix, start_s, end_s, suffix = port_match.groups()
        start, end = int(start_s), int(end_s)
        if start <= end:
            return [f"{prefix}{idx}{suffix}" for idx in range(start, end + 1)]

    return [value]


def compress_interface_tokens(interfaces: Sequence[str]) -> List[str]:
    compressed: List[str] = []
    idx = 0
    items = list(dict.fromkeys(interfaces))

    while idx < len(items):
        current = items[idx]
        match = re.fullmatch(r"(swp\d+s)(\d+)", current)
        if not match:
            compressed.append(current)
            idx += 1
            continue

        prefix, start_s = match.groups()
        start = int(start_s)
        end = start
        next_idx = idx + 1
        while next_idx < len(items):
            next_match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", items[next_idx])
            if not next_match:
                break
            next_value = int(next_match.group(1))
            if next_value != end + 1:
                break
            end = next_value
            next_idx += 1

        if end > start:
            compressed.append(f"{prefix}{start}-{end}")
        else:
            compressed.append(current)
        idx = next_idx

    return compressed


def build_display_link_flap_command(interfaces: Sequence[str], action: str) -> str:
    verb = "set" if action == "disable" else "unset"
    interface_csv = ",".join(compress_interface_tokens(interfaces))
    return f"nv {verb} interface {interface_csv} link flap-protection state disabled"


def filter_swp_interfaces(links: Iterable[PeerLink]) -> List[PeerLink]:
    out: List[PeerLink] = []
    for link in links:
        match = re.match(r"^swp(\d+)(?!\d)", link.interface)
        if not match:
            continue
        port = int(match.group(1))
        if 1 <= port <= 64:
            out.append(link)
    return out


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(items), size):
        yield list(items[idx:idx + size])


def build_link_flap_commands(interfaces: Sequence[str], batch_size: int, action: str) -> List[str]:
    verb = "set" if action == "disable" else "unset"
    return [
        f"nv {verb} interface {','.join(batch)} link flap-protection state disabled"
        for batch in chunked(list(interfaces), batch_size)
    ]


def require_pexpect() -> None:
    if pexpect is None:
        raise RuntimeError("pexpect is required for live SSH mode")


def connect_ssh(host: str, username: str, password: str, timeout: int,
                strict_hostkey: str, debug_log: Optional[str], port: int = 22):
    require_pexpect()
    ssh_cmd = f"ssh -o StrictHostKeyChecking={strict_hostkey} -p {port} {username}@{host}"
    child = pexpect.spawn(ssh_cmd, encoding="utf-8", timeout=timeout)
    if debug_log:
        child.logfile = open(debug_log, "a")

    i = child.expect([HOSTKEY_RE, USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
    if i == 0:
        child.sendline("yes")
        hostkey_i = child.expect([USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
        if hostkey_i == 0:
            i = 1
        elif hostkey_i == 1:
            i = 2
        elif hostkey_i == 2:
            i = 3
        else:
            i = 4

    if i == 1:
        child.sendline(username)
        child.expect(PASS_RE)
        child.sendline(password)
    elif i == 2:
        child.sendline(password)
    elif i == 3:
        raise TimeoutError(f"login timed out connecting to {host}")
    else:
        raise ConnectionError(f"EOF while connecting to {host}")

    j = child.expect([SSH_PROMPT_RE, PASS_RE, SSH_DENIED_RE, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
    if j == 0:
        return child
    if j in (1, 2):
        raise PermissionError(f"password rejected for {username}@{host}")
    if j == 3:
        raise TimeoutError(f"timed out waiting for shell prompt after login to {host}")
    raise ConnectionError(f"EOF after password authentication to {host}")


def close_session(child) -> None:
    try:
        child.sendline("exit")
        child.expect(pexpect.EOF, timeout=10)
    finally:
        child.close()


def nvidia_prepare(child, timeout: int) -> str:
    prompt = "PEXPECT_PROMPT> "
    shell_prompt_re = r"(?m)^.*[$#]\s*$"
    child.sendline("")
    child.expect([shell_prompt_re, pexpect.TIMEOUT], timeout=5)
    child.sendline("stty -echo")
    child.expect(shell_prompt_re, timeout=timeout)
    for cmd in ('export PROMPT_COMMAND=""', "export TERM=dumb", "export PAGER=cat"):
        child.sendline(cmd)
        child.expect(shell_prompt_re, timeout=timeout)
    child.sendline(f'export PS1="{prompt}"')
    child.expect_exact(prompt, timeout=timeout)
    return prompt


def nvidia_run_command(child, cmd: str, prompt: str, timeout: int) -> str:
    child.timeout = timeout
    child.sendline("")
    child.expect_exact(prompt, timeout=timeout)
    child.sendline(cmd)
    child.expect_exact(prompt, timeout=timeout)
    return (child.before or "").strip()


def read_t1_config_live(device: str, username: str, password: str, timeout: int,
                        strict_hostkey: str, debug_log: Optional[str]) -> str:
    child = connect_ssh(device, username, password, timeout=max(timeout, 30),
                        strict_hostkey=strict_hostkey, debug_log=debug_log)
    try:
        prompt = nvidia_prepare(child, timeout=timeout)
        return nvidia_run_command(
            child,
            "nv config show -o commands | grep -E 'nv set interface .*description|nv set interface .*link flap-protection state disabled' || true",
            prompt,
            timeout=max(timeout, 180),
        )
    finally:
        close_session(child)


def raise_on_nvue_error(device: str, cmd: str, output: str) -> None:
    if re.search(r"(?i)\b(error|failed|invalid)\b", output or ""):
        command_type = "apply" if cmd == "nv config apply" else "link flap-protection update"
        raise RuntimeError(f"{device}: {command_type} failed\n{output.strip()}")


def apply_t1_commands(device: str, username: str, password: str, commands: Sequence[str],
                      timeout: int, strict_hostkey: str, debug_log: Optional[str]) -> None:
    child = connect_ssh(device, username, password, timeout=max(timeout, 30),
                        strict_hostkey=strict_hostkey, debug_log=debug_log)
    try:
        prompt = nvidia_prepare(child, timeout=timeout)
        for cmd in commands:
            out = nvidia_run_command(child, cmd, prompt, timeout=max(timeout, 180))
            raise_on_nvue_error(device, cmd, out)
        out = nvidia_run_command(child, "nv config apply", prompt, timeout=max(timeout, 300))
        raise_on_nvue_error(device, "nv config apply", out)
    finally:
        close_session(child)


def build_plan_for_device(device: str, config_text: str, all_t0s: Set[str],
                          in_service_t0s: Set[str], include_unknown_t0: bool,
                          include_already_disabled: bool, swp_only: bool,
                          batch_size: int, action: str) -> DevicePlan:
    links = parse_peer_links(config_text)
    if swp_only:
        links = filter_swp_interfaces(links)

    already_disabled = parse_disabled_interfaces(config_text)
    skipped_in_service: List[PeerLink] = []
    skipped_unknown: List[PeerLink] = []
    eligible: List[PeerLink] = []

    for link in links:
        if link.peer_device in in_service_t0s:
            skipped_in_service.append(link)
            continue
        if all_t0s and link.peer_device not in all_t0s and not include_unknown_t0:
            skipped_unknown.append(link)
            continue
        if action == "disable":
            if link.interface in already_disabled and not include_already_disabled:
                continue
        else:
            if link.interface not in already_disabled:
                continue
        eligible.append(link)

    interfaces = list(dict.fromkeys(link.interface for link in eligible))
    return DevicePlan(
        device=device,
        eligible_links=eligible,
        already_disabled=already_disabled,
        skipped_in_service=skipped_in_service,
        skipped_unknown=skipped_unknown,
        commands=build_link_flap_commands(interfaces, batch_size=batch_size, action=action),
    )


def process_device(device: str, args, all_t0s: Set[str], in_service_t0s: Set[str],
                   username: Optional[str], password: Optional[str]) -> DevicePlan:
    try:
        device_password = password
        if args.jitpw and device_password is None:
            jit_target = args.device_scope.ncpcli_region if args.jitpw_scope == "region" else device
            device_password = get_jitpw_password(
                jit_target, args.jitpw_path, args.jitpw_scope, args.jitpw_transform
            )

        if args.t1_config_file:
            config_text = Path(args.t1_config_file).read_text(encoding="utf-8")
        else:
            if not username or device_password is None:
                raise RuntimeError("username/password required for live T1 reads")
            config_text = read_t1_config_live(
                device, username, device_password, timeout=args.timeout,
                strict_hostkey=args.strict_hostkey, debug_log=args.debug_log,
            )

        plan = build_plan_for_device(
            device=device,
            config_text=config_text,
            all_t0s=all_t0s,
            in_service_t0s=in_service_t0s,
            include_unknown_t0=args.include_unknown_t0,
            include_already_disabled=args.include_already_disabled,
            swp_only=not args.no_swp_filter,
            batch_size=args.batch_size,
            action=args.action,
        )

        if args.apply and plan.commands:
            if not username or device_password is None:
                raise RuntimeError("username/password required for --apply")
            apply_t1_commands(
                device, username, device_password, plan.commands, timeout=args.timeout,
                strict_hostkey=args.strict_hostkey, debug_log=args.debug_log,
            )
            plan.applied = True
        return plan
    except Exception as exc:
        return DevicePlan(device=device, eligible_links=[], already_disabled=set(),
                          skipped_in_service=[], skipped_unknown=[], commands=[],
                          error=f"{type(exc).__name__}: {exc}")


def collect_t0_sets(args) -> Tuple[Set[str], Set[str]]:
    scope = args.device_scope

    if args.t0_all_file:
        all_t0s = set(read_device_file(args.t0_all_file, tier=0, prefix=scope.device_prefix))
    else:
        all_out = ncpcli_devices_list(scope.ncpcli_region, scope.t0_pattern)
        all_t0s = set(parse_device_names(all_out, tier=0, prefix=scope.device_prefix))

    if args.t0_in_service_file:
        in_service_t0s = set(read_device_file(args.t0_in_service_file, tier=0, prefix=scope.device_prefix))
    else:
        in_service_out = ncpcli_devices_list(scope.ncpcli_region, scope.t0_pattern, state=args.in_service_state)
        in_service_t0s = set(parse_device_names(in_service_out, tier=0, prefix=scope.device_prefix))

    return all_t0s, in_service_t0s


def collect_t1_devices(args) -> List[str]:
    if args.selected_devices is not None:
        return list(args.selected_devices)

    scope = args.device_scope
    out = ncpcli_devices_list(scope.ncpcli_region, scope.t1_pattern, state=args.t1_state)
    devices = parse_device_names(out, tier=1, prefix=scope.device_prefix)
    return [device for device in devices if args.device_regex.fullmatch(device)]


def write_csv(path: str, plans: Sequence[DevicePlan]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["device", "interface", "peer_device", "peer_interface", "action"])
        for plan in plans:
            if plan.error:
                writer.writerow([plan.device, "", "", "", f"ERROR: {plan.error}"])
                continue
            for link in plan.eligible_links:
                writer.writerow([plan.device, link.interface, link.peer_device, link.peer_interface,
                                 "applied" if plan.applied else "dry-run"])


def eligible_interface_count(plan: DevicePlan) -> int:
    return len(dict.fromkeys(link.interface for link in plan.eligible_links))


def eligible_interfaces(plan: DevicePlan) -> List[str]:
    return list(dict.fromkeys(link.interface for link in plan.eligible_links))


def print_plan_summary(all_t0s: Set[str], in_service_t0s: Set[str],
                       t1_devices: Sequence[str], plans: Sequence[DevicePlan],
                       apply: bool, action: str) -> None:
    print("\n=== T0 discovery ===")
    print(f"All T0 devices:        {len(all_t0s)}")
    print(f"In-service T0 devices: {len(in_service_t0s)}")
    print(f"Not in-service T0s:    {len(all_t0s - in_service_t0s)}")

    print("\n=== T1 targets ===")
    print(f"T1 devices: {len(t1_devices)}")

    print("\n=== Per-device plan ===")
    for plan in sorted(plans, key=lambda item: item.device):
        if plan.error:
            print(f"{plan.device}: ERROR {plan.error}")
            continue

        interfaces = eligible_interfaces(plan)
        interface_count = len(interfaces)
        state = "disabled" if action == "disable" else "enabled"
        if apply:
            if plan.applied:
                status = f"OK - marked {interface_count} interfaces link flap-protection {state}"
            else:
                status = "OK - no interfaces needed changes"
        else:
            status = f"OK - dry-run would mark {interface_count} interfaces link flap-protection {state}"
            if interface_count == 0:
                status = "OK - no interfaces need changes"

        print(f"{plan.device}: {status}")
        if interfaces:
            prefix = "applied command" if plan.applied else "planned command"
            print(f"  {prefix}: {build_display_link_flap_command(interfaces, action)}")


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(
        description="Enable or disable Cumulus T1 link flap protection only toward T0s that are not in-service."
    )
    device_group = parser.add_mutually_exclusive_group(required=True)
    device_group.add_argument("--device-name", action="append",
                              help="Exact T1 hostname. Repeat for multiple devices in the same fabric plane.")
    device_group.add_argument("--device-pattern",
                              help="T1 device regex or glob, e.g. 'jbp15-q2-p1-t1-r(1|2)' or 'jbp15-q2-p1-t1-r*'.")
    device_group.add_argument("--device-from-file",
                              help="File containing target T1 hostnames or ncpcli devices list output.")
    device_group.add_argument("--rack",
                              help="Rack number like 0706. Also accepts comma-separated racks or ranges.")
    device_group.add_argument("--racks", action="append",
                              help="Multiple racks, comma-separated or repeated. Supports ranges like 0706-0708.")
    parser.add_argument("--rack-region",
                        help="Site root for rack topology lookup, e.g. jbp15. Required with --rack/--racks.")
    parser.add_argument("--in-service-state", default="in-service",
                        help="ncpcli state treated as in-service. Default: in-service")
    parser.add_argument("--t1-state", default=None,
                        help="Optional state filter for --device-pattern discovery.")
    parser.add_argument("--t0-all-file",
                        help="File containing all T0 names or ncpcli devices list output. Bypasses all-T0 ncpcli lookup.")
    parser.add_argument("--t0-in-service-file",
                        help="File containing in-service T0 names or ncpcli devices list output. Bypasses in-service ncpcli lookup.")
    parser.add_argument("--t1-config-file",
                        help="Local NVUE command output for offline parser testing. Use with exactly one --device-name.")

    parser.add_argument("--username",
                        help="SSH username for T1 access. If omitted, prompts when live SSH is needed.")
    parser.add_argument("--password",
                        help="SSH password. If omitted and live T1 access is needed, prompts securely.")
    parser.add_argument("--jitpw", action="store_true",
                        help="Use local jitpw for SSH password instead of prompting.")
    parser.add_argument("--jitpw-path",
                        help="Explicit jitpw binary path.")
    parser.add_argument("--jitpw-scope", choices=["region", "device"], default="region",
                        help="Use 'jitpw -e <region>' or 'jitpw -qe <device>'. Default: region.")
    parser.add_argument("--jitpw-transform", choices=["none", "lower", "upper"], default="none",
                        help="Optional case transform for the JIT password before SSH. Default: none.")
    parser.add_argument("--strict-hostkey", choices=["ask", "yes", "no"], default="ask")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--debug-log")

    parser.add_argument("--action", choices=["disable", "enable"], default="disable",
                        help="Disable or enable link flap protection. Default: disable.")
    parser.add_argument("--apply", action="store_true",
                        help="Push the NVUE config and run 'nv config apply'. Default is dry-run.")
    parser.add_argument("--include-already-disabled", action="store_true",
                        help="Include interfaces that already have link flap-protection disabled.")
    parser.add_argument("--include-unknown-t0", action="store_true",
                        help="Also act on parsed peer T0s that were not returned by the all-T0 discovery.")
    parser.add_argument("--no-swp-filter", action="store_true",
                        help="Do not restrict candidate interfaces to swp1..swp64.")
    parser.add_argument("--batch-size", type=int, default=64,
                        help="Interfaces per NVUE command. Default: 64.")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output-csv",
                        help="Optional CSV path for the final per-interface plan.")
    parser.add_argument("--allow-empty-discovery", action="store_true",
                        help="Allow empty T0 discovery results. This is unsafe and should only be used for parser tests.")

    args = parser.parse_args(argv)

    if args.device_name:
        args.selected_devices = list(dict.fromkeys(args.device_name))
        try:
            args.device_scope = validate_single_scope(args.selected_devices)
        except ValueError as exc:
            parser.error(str(exc))
        args.device_regex = None
    elif args.device_from_file:
        args.selected_devices = read_device_file(args.device_from_file, tier=1)
        if not args.selected_devices:
            parser.error(f"no T1 devices found in {args.device_from_file}")
        try:
            args.device_scope = validate_single_scope(args.selected_devices)
        except ValueError as exc:
            parser.error(str(exc))
        args.device_regex = None
    elif args.rack or args.racks:
        if not args.rack_region:
            parser.error("--rack/--racks requires --rack-region, e.g. --rack-region jbp15 --rack 0706")
        rack_values = []
        if args.rack:
            rack_values.append(args.rack)
        if args.racks:
            rack_values.extend(args.racks)
        try:
            args.selected_devices = racktopo_devices(args.rack_region, rack_values)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        if not args.selected_devices:
            parser.error(f"no T1 devices found from rack topology for {','.join(expand_rack_values(rack_values))}")
        try:
            args.device_scope = validate_single_scope(args.selected_devices)
        except ValueError as exc:
            parser.error(str(exc))
        args.device_regex = None
    else:
        args.selected_devices = None
        try:
            args.device_scope = scope_from_device_pattern(args.device_pattern)
            args.device_regex = compile_device_pattern(args.device_pattern)
        except ValueError as exc:
            parser.error(str(exc))
        except re.error as exc:
            parser.error(f"invalid --device-pattern regex: {exc}")

    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if args.password and args.jitpw:
        parser.error("--password and --jitpw are mutually exclusive")
    if args.t1_config_file and (
        args.selected_devices is None or len(args.selected_devices) != 1
    ):
        parser.error("--t1-config-file supports exactly one --device-name or one device in --device-from-file")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    all_t0s, in_service_t0s = collect_t0_sets(args)
    if not args.allow_empty_discovery:
        if not all_t0s:
            raise RuntimeError(f"no T0 devices discovered with pattern {args.device_scope.t0_pattern}")
        if not in_service_t0s:
            raise RuntimeError(
                f"no in-service T0 devices discovered with pattern {args.device_scope.t0_pattern}; "
                "refusing to treat every T0 as out of service"
            )

    t1_devices = collect_t1_devices(args)
    if not t1_devices:
        raise RuntimeError(f"no T1 devices discovered with pattern {args.device_scope.t1_pattern}")

    needs_live_ssh = not args.t1_config_file or args.apply
    username = args.username
    password = args.password
    if needs_live_ssh:
        if not username:
            username = input("SSH username: ").strip()
            if not username:
                raise RuntimeError("SSH username is required for live T1 access")
        if password is None and not args.jitpw:
            password = getpass.getpass(f"SSH password for {username}: ")

    plans: List[DevicePlan] = []
    workers = min(max(args.max_workers, 1), len(t1_devices))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(process_device, device, args, all_t0s, in_service_t0s,
                            username, password): device
            for device in t1_devices
        }
        for future in as_completed(future_map):
            plans.append(future.result())

    plans.sort(key=lambda item: item.device)
    print_plan_summary(all_t0s, in_service_t0s, t1_devices, plans, apply=args.apply, action=args.action)
    if args.output_csv:
        write_csv(args.output_csv, plans)
        print(f"\nWrote CSV: {args.output_csv}")

    failed = [plan for plan in plans if plan.error]
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
