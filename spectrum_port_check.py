#!/usr/bin/env python3
"""
Unified Spectrum port check flow.

Examples:
  python spectrum_port_check.py --site hsg17 --rack 1010 --elevation 1 --port swp59s0
  python spectrum_port_check.py hsg17 --location 1010 1 swp59s0
  python spectrum_port_check.py --site aga4 aga4-q1-p1-t1-r1 swp1
  python spectrum_port_check.py hsg17-q2-p4-t1-r33 swp59s0
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

import pexpect


SITE_RE = re.compile(r"^[A-Za-z]+\d+$")
HOST_SITE_RE = re.compile(r"^([A-Za-z]+\d+)(?:-|$)")
NCP_PROMPT_RE = r"ncpcli@[^\r\n>]*>"
SSH_PROMPT_RE = r"(\r\n|\n|\r)[^\r\n]*[#$>] ?$"
PROMPT_ANSI_PREFIX_RE = r"(?:\x1b\[[0-?]*[ -/]*[@-~])*"
ARISTA_EXEC_PROMPT_RE = (
    rf"(\r\n|\n|\r){PROMPT_ANSI_PREFIX_RE}"
    r"[A-Za-z0-9_.-]+\[\d{2}:\d{2}:\d{2}\]# ?$"
)
ARISTA_PROMPT_RE = (
    rf"(\r\n|\n|\r){PROMPT_ANSI_PREFIX_RE}"
    r"[A-Za-z0-9_.-]+\[\d{2}:\d{2}:\d{2}\](?:\([^)]*\))?# ?$"
)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ARISTA_INTERFACE_RE = re.compile(r"^(?:et|ethernet)\d+(?:/\d+)+$", re.IGNORECASE)
SPECTRUM_INTERFACE_RE = re.compile(r"^swp\d+(?:s\d+)?$", re.IGNORECASE)
DEVICE_NAME_RE = re.compile(r"\b[A-Za-z]{2,}\d+[A-Za-z0-9_.-]*(?:-[A-Za-z0-9_.-]+)+\b")
ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\u200e\u200f\ufeff")


def normalize_cli_token(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(ZERO_WIDTH_TRANSLATION).strip()


@dataclass(frozen=True)
class RegionConfig:
    default_ssh_domain: str = ""
    password_ssh_options: bool = False
    jitpw_credential: str = ""
    ad_ssh_host: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    site: str
    region: str
    ssh_domain: str
    password_ssh_options: bool
    jitpw_credential: str
    ad_ssh_host: bool


@dataclass(frozen=True)
class Target:
    device: str
    port: str
    device_model: str = ""
    device_state: str = ""
    ssh_host: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "device", normalize_cli_token(self.device))
        object.__setattr__(self, "port", normalize_cli_token(self.port))
        object.__setattr__(self, "device_model", normalize_cli_token(self.device_model))
        object.__setattr__(self, "device_state", normalize_cli_token(self.device_state))
        object.__setattr__(self, "ssh_host", normalize_cli_token(self.ssh_host))


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    model: str
    state: str
    location: str
    ad: str = ""


@dataclass(frozen=True)
class PeerInfo:
    device: str
    port: str


@dataclass(frozen=True)
class CheckResult:
    already_up_up: bool = False
    port_unavailable: bool = False
    peer: Optional[PeerInfo] = None


REGION_CONFIGS = {
    "aga": RegionConfig(),
    "hsg": RegionConfig(default_ssh_domain="ap-batam-1", password_ssh_options=True, ad_ssh_host=True),
    "iad": RegionConfig(),
    "jbp": RegionConfig(jitpw_credential="jbp"),
    "phx": RegionConfig(password_ssh_options=True, ad_ssh_host=True),
}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize_table_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def split_pipe_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def parse_current_devices(output: str) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    header: list[str] | None = None

    for line in strip_ansi(output).splitlines():
        row = split_pipe_table_row(line)
        if row is None or not row:
            continue
        if row[0] == "Name":
            header = [normalize_table_header(cell) for cell in row]
            continue
        if row[0].startswith("-"):
            continue

        if header and len(row) == len(header):
            values = dict(zip(header, row))
            name = values.get("name", "")
            model = values.get("devicemodel", "")
            state = values.get("state", "")
            location = values.get("location", "")
            ad = values.get("ad", "")
        elif len(row) >= 7:
            name, model, state, ad, location = row[0], row[2], row[3], row[4], row[5]
        elif len(row) >= 6:
            name, model, state, ad, location = row[0], row[2], row[3], "", row[5]
        else:
            continue

        if not name or name == "Name":
            continue
        devices.append(DeviceInfo(name=name, model=model, state=state, location=location, ad=ad))

    return devices


def normalize_site(site: str) -> str:
    normalized = site.strip().lower()
    if not SITE_RE.fullmatch(normalized):
        raise ValueError("site must include a region prefix and building number, for example hsg17 or nrt1")
    return normalized


def region_for_site(site: str) -> str:
    match = re.fullmatch(r"([a-z]+)\d+", site)
    if not match:
        raise ValueError(f"cannot derive region from site {site!r}")
    return match.group(1)


def runtime_config_for_site(site: str) -> RuntimeConfig:
    site = normalize_site(site)
    region = region_for_site(site)
    region_config = REGION_CONFIGS.get(region, RegionConfig())
    ssh_domain = os.environ.get("SPECTRUM_SSH_DOMAIN", region_config.default_ssh_domain)
    jitpw_credential = os.environ.get("SPECTRUM_JITPW_REGION", region_config.jitpw_credential)
    return RuntimeConfig(
        site=site,
        region=region,
        ssh_domain=ssh_domain,
        password_ssh_options=region_config.password_ssh_options,
        jitpw_credential=jitpw_credential,
        ad_ssh_host=region_config.ad_ssh_host,
    )


def normalize_site_and_rack(default_site: str, rack: str) -> tuple[str, str]:
    rack = rack.strip().lower()
    match = re.fullmatch(r"([a-z]+\d+):(\d+)", rack, re.IGNORECASE)
    if match:
        site, rack_number = match.groups()
        return normalize_site(site), rack_number
    return default_site, rack


def infer_site_from_target_args(args: list[str]) -> Optional[str]:
    for value in args:
        if value.startswith("-"):
            continue
        match = HOST_SITE_RE.match(value)
        if match:
            return normalize_site(match.group(1))
        return None
    return None


def split_site_args(argv: list[str]) -> tuple[str, list[str]]:
    site_parser = argparse.ArgumentParser(add_help=False)
    site_parser.add_argument("--site", "--tag", dest="site")
    namespace, remaining = site_parser.parse_known_args(argv)

    if namespace.site:
        return normalize_site(namespace.site), remaining

    if remaining and SITE_RE.fullmatch(remaining[0]):
        return normalize_site(remaining[0]), remaining[1:]

    inferred_site = infer_site_from_target_args(remaining)
    if inferred_site:
        return inferred_site, remaining

    raise ValueError("missing site; pass --site hsg17 or put the site tag first")


def spawn_logged(command: str, timeout: int) -> pexpect.spawn:
    child = pexpect.spawn(command, encoding="utf-8", timeout=timeout)
    child.logfile_read = sys.stdout
    return child


def expect_or_exit(child: pexpect.spawn, patterns: Iterable[object], timeout_msg: str) -> int:
    try:
        return child.expect(list(patterns))
    except pexpect.TIMEOUT:
        print(timeout_msg)
        if child.before:
            print("Last command output before timeout:")
            print(strip_ansi(str(child.before)).strip())
        child.close(force=True)
        raise SystemExit(1)
    except pexpect.EOF:
        print(f"Process exited before the expected prompt: {timeout_msg}")
        if child.before:
            print("Command output before exit:")
            print(strip_ansi(str(child.before)).strip())
        child.close()
        raise SystemExit(1)


def run_ncp_command(child: pexpect.spawn, command: str, timeout_msg: str) -> str:
    child.sendline(command)
    expect_or_exit(child, [NCP_PROMPT_RE], timeout_msg)
    return child.before


def load_current_devices(runtime: RuntimeConfig, update_command: str, timeout_context: str) -> list[DeviceInfo]:
    child = spawn_logged(f"ncpcli -r {runtime.region} interactive", timeout=180)
    expect_or_exit(child, [NCP_PROMPT_RE], "Timed out waiting for ncpcli prompt")

    run_ncp_command(
        child,
        update_command,
        f"Timed out updating device list for {timeout_context}",
    )
    device_output = run_ncp_command(
        child,
        "current-devices -va",
        f"Timed out reading current devices for {timeout_context}",
    )

    child.sendline("quit")
    try:
        child.expect(pexpect.EOF, timeout=30)
    except pexpect.TIMEOUT:
        print("ncpcli session did not close after quit")
        child.close(force=True)
        raise SystemExit(1)
    child.close()

    return parse_current_devices(device_output)


def ssh_host_for_device(runtime: RuntimeConfig, device: DeviceInfo) -> str:
    if runtime.ad_ssh_host and device.ad and runtime.ssh_domain:
        return f"{device.name}.net.{device.ad}.{runtime.ssh_domain}"
    return device.name


def resolve_device_from_location(runtime: RuntimeConfig, rack: str, elevation: str) -> Target:
    lookup_site, rack = normalize_site_and_rack(runtime.site, rack)
    lookup_region = region_for_site(lookup_site)
    if lookup_region != runtime.region:
        print(
            f"Rack prefix {lookup_site} belongs to {lookup_region}, "
            f"but selected site {runtime.site} belongs to {runtime.region}."
        )
        raise SystemExit(1)

    elevation = elevation.strip()
    target_location = f"{lookup_site}:{rack}:{elevation}"
    print(f"Resolving device for {target_location} via ncpcli")

    devices = load_current_devices(
        runtime,
        f"update-device-list --devices-by-rack {lookup_site}:{rack}",
        f"{lookup_site}:{rack}",
    )
    matches: list[DeviceInfo] = []
    for device in devices:
        match = re.fullmatch(rf"{re.escape(lookup_site)}:(\d+):(\d+)", device.location, re.IGNORECASE)
        if not match:
            continue
        row_rack, row_elevation = match.groups()
        if row_rack == rack and row_elevation == elevation:
            matches.append(device)

    if not matches:
        print(f"No device found at {target_location}")
        raise SystemExit(1)
    if len(matches) > 1:
        print(f"Multiple devices found at {target_location}: {', '.join(device.name for device in matches)}")
        raise SystemExit(1)

    match = matches[0]
    return Target(
        device=match.name,
        port="",
        device_model=match.model,
        device_state=match.state,
        ssh_host=ssh_host_for_device(runtime, match),
    )


def resolve_device_by_name(runtime: RuntimeConfig, device_name: str) -> DeviceInfo:
    device_name = normalize_cli_token(device_name)
    print(f"Checking device state for {device_name} via ncpcli")
    devices = load_current_devices(
        runtime,
        f"update-device-list --device-names-matching {device_name}",
        device_name,
    )
    matches = [device for device in devices if normalize_cli_token(device.name) == device_name]
    if not matches:
        print(f"No device found matching hostname {device_name}; not making changes.")
        raise SystemExit(1)
    if len(matches) > 1:
        print(f"Multiple devices exactly matched {device_name}; not making changes.")
        raise SystemExit(1)
    return matches[0]


def get_password(runtime: RuntimeConfig, device: str) -> str:
    env_password = os.environ.get("SPECTRUM_SSH_PASSWORD")
    if env_password:
        return env_password

    credential_name = runtime.jitpw_credential or device
    try:
        result = subprocess.run(
            [os.path.expanduser("~/tools/jitpw/bin/jitpw"), "-qe", credential_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        print("Could not find ~/tools/jitpw/bin/jitpw")
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        print(f"jitpw failed for {credential_name}: exit {exc.returncode}")
        raise SystemExit(1)

    return result.stdout.strip()


def ssh_command_for_host(runtime: RuntimeConfig, host: str) -> str:
    if not runtime.password_ssh_options:
        return f"ssh {host}"
    return " ".join(
        [
            "ssh",
            "-o",
            "PreferredAuthentications=keyboard-interactive,password",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "NumberOfPasswordPrompts=3",
            host,
        ]
    )


def expect_ssh_prompt(child: pexpect.spawn, timeout_msg: str) -> None:
    expect_or_exit(child, [SSH_PROMPT_RE], timeout_msg)


def interface_is_arista(port: str) -> bool:
    return ARISTA_INTERFACE_RE.fullmatch(port.strip()) is not None


def interface_is_spectrum(port: str) -> bool:
    return SPECTRUM_INTERFACE_RE.fullmatch(port.strip()) is not None


def device_model_is_arista(device_model: str) -> bool:
    return "arista" in device_model.lower()


def device_state_is_in_service(device_state: str) -> bool:
    return device_state.strip().lower() == "in-service"


def refresh_target_device_info(runtime: RuntimeConfig, target: Target) -> Target:
    device_info = resolve_device_by_name(runtime, target.device)
    return Target(
        target.device,
        target.port,
        device_info.model,
        device_info.state,
        ssh_host_for_device(runtime, device_info),
    )


def target_has_resolved_device_info(target: Target) -> bool:
    return bool(target.device_model and target.device_state and target.ssh_host)


def ensure_target_device_info(runtime: RuntimeConfig, target: Target) -> Target:
    if target_has_resolved_device_info(target):
        return target
    return refresh_target_device_info(runtime, target)


def ensure_device_is_not_in_service(target: Target) -> bool:
    if device_state_is_in_service(target.device_state):
        print(
            f"{target.device} is {target.device_state}; "
            "running interface checks and link flap clear only, but skipping sudo nv port bounce."
        )
        return False
    return True


def login_and_detect_arista(child: pexpect.spawn, password: str) -> bool:
    idx = expect_or_exit(
        child,
        [r"(?i).*password:", ARISTA_EXEC_PROMPT_RE, SSH_PROMPT_RE],
        "auth timed out",
    )
    if idx == 0:
        child.sendline(password)
        idx = expect_or_exit(
            child,
            [ARISTA_EXEC_PROMPT_RE, SSH_PROMPT_RE],
            "No prompt after login",
        )
        return idx == 0
    return idx == 1


def run_ssh_command(child: pexpect.spawn, command: str, timeout_msg: str) -> str:
    child.sendline(command)
    expect_ssh_prompt(child, timeout_msg)
    return child.before


def run_sudo_command(child: pexpect.spawn, command: str, password: str, timeout_msg: str) -> str:
    child.sendline(command)
    output_parts = []
    while True:
        idx = expect_or_exit(
            child,
            [r"(?i).*password.*", SSH_PROMPT_RE],
            timeout_msg,
        )
        output_parts.append(child.before or "")
        if idx == 0:
            child.sendline(password)
            continue
        return "".join(output_parts)


def output_has_port_up_up(output: str, port: str) -> bool:
    for raw_line in strip_ansi(output).splitlines():
        fields = raw_line.strip().split()
        if len(fields) >= 3 and fields[0] == port:
            return fields[1].lower() == "up" and fields[2].lower() == "up"
    return False


def output_has_missing_requested_item(output: str) -> bool:
    return "the requested item does not exist" in strip_ansi(output).lower()


def output_has_invalid_breakout_config(output: str) -> bool:
    return "invalid breakout port" in strip_ansi(output).lower()


def invalid_breakout_lines(output: str) -> list[str]:
    return [
        line.strip()
        for line in strip_ansi(output).splitlines()
        if "invalid breakout port" in line.lower()
    ]


def extract_wrapped_key_value(text: str, key: str, token_pattern: str) -> Optional[str]:
    key_re = re.compile(rf"\b{re.escape(key)}=({token_pattern})", flags=re.IGNORECASE)
    token_re = re.compile(rf"^{token_pattern}$", flags=re.IGNORECASE)
    key_value_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*=")
    lines = strip_ansi(text).splitlines()

    for index, line in enumerate(lines):
        match = key_re.search(line)
        if not match:
            continue

        parts = [match.group(1)]
        for continuation in lines[index + 1 :]:
            stripped = continuation.strip()
            if not stripped or key_value_re.search(stripped):
                break
            if not token_re.fullmatch(stripped):
                break
            parts.append(stripped)

        return "".join(parts)

    return None


def extract_first_wrapped_key_value(
    text: str,
    keys: Iterable[str],
    token_pattern: str,
) -> Optional[str]:
    for key in keys:
        value = extract_wrapped_key_value(text, key, token_pattern)
        if value:
            return value
    return None


def parse_peer_info(description_output: str) -> Optional[PeerInfo]:
    text = strip_ansi(description_output)
    peer_device = extract_wrapped_key_value(text, "peer_device", r"[A-Za-z0-9_.-]+")
    peer_interface = extract_wrapped_key_value(text, "peer_interface", r"[A-Za-z0-9_./:-]+")
    if not peer_device or not peer_interface:
        return None
    return PeerInfo(peer_device, peer_interface)


def normalize_lldp_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def interface_tokens(text: str) -> list[str]:
    matches = re.findall(
        r"\b(?:swp\d+(?:s\d+)?|(?:et|ethernet)\d+(?:/\d+)+)\b",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(matches))


def parse_lldp_peer_from_key_values(text: str) -> Optional[PeerInfo]:
    peer_device = extract_first_wrapped_key_value(
        text,
        ("peer_device", "remote_device", "neighbor", "neighbor_device", "system_name", "sys_name"),
        r"[A-Za-z0-9_.-]+",
    )
    peer_interface = extract_first_wrapped_key_value(
        text,
        ("peer_interface", "remote_interface", "neighbor_interface", "port_id", "remote_port", "neighbor_port"),
        r"[A-Za-z0-9_./:-]+",
    )
    if peer_device and peer_interface:
        return PeerInfo(peer_device, peer_interface)
    return None


def parse_lldp_peer_from_pipe_table(output: str, local_port: str) -> Optional[PeerInfo]:
    header: list[str] | None = None
    local_port_lower = local_port.lower()

    for line in strip_ansi(output).splitlines():
        row = split_pipe_table_row(line)
        if row is None:
            continue
        normalized = [normalize_lldp_header(cell) for cell in row]
        if any(name in normalized for name in ("interface", "ifname", "localport")):
            header = normalized
            continue
        if header is None or len(row) != len(header):
            continue
        if local_port_lower not in {cell.lower() for cell in row}:
            continue

        peer_device = None
        peer_interface = None
        for name, value in zip(header, row):
            if name in {"peerdevice", "remotedevice", "neighbor", "neighbordevice", "systemname", "sysname"}:
                peer_device = value
            elif name in {
                "peerinterface",
                "remoteinterface",
                "neighborinterface",
                "portid",
                "remoteport",
                "neighborport",
            }:
                peer_interface = value

        if peer_device and peer_interface:
            return PeerInfo(peer_device, peer_interface)

    return None


def parse_lldp_peer_from_matching_line(output: str, local_port: str) -> Optional[PeerInfo]:
    local_port_lower = local_port.lower()
    for line in strip_ansi(output).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("nv show", "grep ")):
            continue
        if local_port_lower not in stripped.lower():
            continue

        peer_devices = DEVICE_NAME_RE.findall(stripped)
        peer_interfaces = [
            token
            for token in interface_tokens(stripped)
            if token.lower() != local_port_lower
        ]
        if peer_devices and peer_interfaces:
            return PeerInfo(peer_devices[-1], peer_interfaces[-1])

    return None


def parse_lldp_peer_info(lldp_output: str, local_port: str) -> Optional[PeerInfo]:
    text = strip_ansi(lldp_output)
    return (
        parse_lldp_peer_from_key_values(text)
        or parse_lldp_peer_from_pipe_table(text, local_port)
        or parse_lldp_peer_from_matching_line(text, local_port)
    )


def close_ssh_session(child: pexpect.spawn) -> None:
    child.sendline("exit")
    try:
        child.expect(pexpect.EOF, timeout=60)
    except pexpect.TIMEOUT:
        print("SSH session did not close after exit")
        child.close(force=True)
        raise SystemExit(1)
    child.close()


def run_arista_command(child: pexpect.spawn, command: str, timeout_msg: str) -> str:
    child.sendline(command)
    expect_or_exit(child, [ARISTA_PROMPT_RE], timeout_msg)
    return child.before


def arista_brief_interface_name(port: str) -> str:
    stripped = port.strip()
    if stripped.lower().startswith("ethernet"):
        return stripped
    match = re.fullmatch(r"et(.+)", stripped, re.IGNORECASE)
    if match:
        return f"Ethernet{match.group(1)}"
    return stripped


def arista_brief_output_is_up(output: str, brief_port: str) -> bool:
    for raw_line in strip_ansi(output).splitlines():
        fields = raw_line.strip().split()
        if len(fields) >= 4 and fields[0] == brief_port:
            return fields[2].lower() == "up" and fields[3].lower() == "up"
    return False


def arista_interface_is_up(child: pexpect.spawn, port: str) -> bool:
    brief_port = arista_brief_interface_name(port)
    output = run_arista_command(
        child,
        f'show ip interface brief | grep "{brief_port}"',
        "Timeout after Arista interface status check",
    )
    return arista_brief_output_is_up(output, brief_port)


def run_arista_bounce(child: pexpect.spawn, port: str) -> None:
    print("")
    print("Detected Arista interface/prompt; running Arista interface bounce")
    if arista_interface_is_up(child, port):
        print(f"{port} is already up/up in show ip interface brief; skipping port bounce.")
        close_ssh_session(child)
        return

    run_arista_command(child, "conf", "Timeout after Arista config mode command")
    run_arista_command(child, f"int {port}", "Timeout after Arista interface command")
    run_arista_command(child, "shut", "Timeout after Arista shut command")
    run_arista_command(child, "no shut", "Timeout after Arista no shut command")
    run_arista_command(child, "end", "Timeout after Arista end command")
    close_ssh_session(child)


def run_show_checks(
    child: pexpect.spawn,
    port: str,
    stage: str,
    stop_when_up_up: bool = False,
) -> CheckResult:
    print("")
    print(stage)

    description_output = None
    lldp_output = None
    status_output = None
    peer = None
    already_up_up = False
    checks = [
        (f"nv show interface lldp | grep {port}", "Timeout after LLDP command"),
        (
            f"nv show interface description | grep -A 2 {port}",
            "Timeout after description command",
        ),
        (f"nv show interface status | grep {port}", "Timeout after port status command"),
        (
            f"nv show interface {port} transceiver | grep dBm -B 1",
            "Timeout after transceiver command",
        ),
        (
            f"nv show interface {port} link phy health | grep raw-ber",
            "Timeout after FEC command",
        ),
    ]

    for command, timeout_msg in checks:
        output = run_ssh_command(child, command, timeout_msg)
        if "interface lldp" in command:
            lldp_output = output
            peer = peer or parse_lldp_peer_info(lldp_output, port)
        elif "interface description" in command:
            description_output = output
            peer = parse_peer_info(description_output) or peer
        elif "interface status" in command:
            status_output = output

        if "transceiver" in command and output_has_missing_requested_item(output):
            print("")
            print(
                f"{port} is not available on this device; nv reported "
                "'The requested item does not exist.' Skipping remaining checks and link flap clear."
            )
            return CheckResult(already_up_up=already_up_up, port_unavailable=True, peer=peer)

        if (
            description_output is not None
            and status_output is not None
            and output_has_port_up_up(description_output, port)
            and output_has_port_up_up(status_output, port)
        ):
            already_up_up = True
            if stop_when_up_up:
                return CheckResult(already_up_up=True, peer=peer)

    return CheckResult(already_up_up=already_up_up, peer=peer)


def clear_link_flap_protection(child: pexpect.spawn, port: str) -> bool:
    print(f"Clearing link flap-protection violation on {port}")
    child.sendline(f"nv action clear interface {port} link flap-protection violation")
    expect_ssh_prompt(child, "Timeout after clear violation command")
    output = child.before or ""
    normalized_output = strip_ansi(output).lower()
    if "action failed" in normalized_output or "error:" in normalized_output:
        print(f"Link flap-protection violation clear did not succeed on {port}.")
        return False
    print(f"Link flap-protection violation clear succeeded on {port}.")
    return True


def run_spectrum_port_bounce(child: pexpect.spawn, target: Target, password: str) -> bool:
    if device_state_is_in_service(target.device_state):
        print("")
        print(
            f"{target.device} is {target.device_state}; refusing sudo nv port bounce "
            f"for {target.port}."
        )
        return False

    run_sudo_command(
        child,
        f"sudo nv set interface {target.port} link state down",
        password,
        "Timeout after local shut command",
    )
    down_apply_output = run_sudo_command(
        child,
        "sudo nv config apply",
        password,
        "Timeout after local config apply (down)",
    )
    if output_has_invalid_breakout_config(down_apply_output):
        print("")
        print(
            f"nv config apply failed because one or more ports are invalid for the current breakout. "
            f"Skipping remaining bounce steps for {target.port}."
        )
        for line in invalid_breakout_lines(down_apply_output):
            print(f"  {line}")
        return False

    run_sudo_command(
        child,
        f"sudo nv set interface {target.port} link state up",
        password,
        "Timeout after local unshut command",
    )
    up_apply_output = run_sudo_command(
        child,
        "sudo nv config apply",
        password,
        "Timeout after local config apply (up)",
    )
    if output_has_invalid_breakout_config(up_apply_output):
        print("")
        print(
            f"nv config apply failed because one or more ports are invalid for the current breakout. "
            f"{target.port} may still need manual recovery if the down apply succeeded."
        )
        for line in invalid_breakout_lines(up_apply_output):
            print(f"  {line}")
        return False

    run_show_checks(child, target.port, "Post-bounce interface checks")
    return True


def run_one_side_port_check(
    runtime: RuntimeConfig,
    target: Target,
    label: str,
    discover_peer: bool = True,
) -> tuple[int, Optional[PeerInfo], bool, bool]:
    print("")
    print(f"=== {label}: {target.device} {target.port} ===")
    print(f"Running commands on device: {target.device}, port: {target.port}")
    target = ensure_target_device_info(runtime, target)
    can_bounce_port = ensure_device_is_not_in_service(target)

    password = get_password(runtime, target.device)
    ssh_host = target.ssh_host or target.device
    if ssh_host != target.device:
        print(f"SSH target: {ssh_host}")

    if sys.stdin.isatty():
        sys.stdout.write(f"\033]0;{target.device}\007")
        sys.stdout.flush()

    child = spawn_logged(ssh_command_for_host(runtime, ssh_host), timeout=60)
    prompt_is_arista = login_and_detect_arista(child, password)
    if device_model_is_arista(target.device_model) or prompt_is_arista:
        is_arista = True
    elif interface_is_arista(target.port):
        is_arista = True
    elif interface_is_spectrum(target.port):
        is_arista = False
    else:
        is_arista = prompt_is_arista

    if is_arista:
        if not can_bounce_port:
            print("")
            print("Detected Arista interface/prompt; running read-only interface status check.")
            is_up = arista_interface_is_up(child, target.port)
            print(f"{target.port} Arista status: {'up/up' if is_up else 'not up/up'}")
            close_ssh_session(child)
            return child.exitstatus or 0, None, is_up, False
        run_arista_bounce(child, target.port)
        return child.exitstatus or 0, None, False, False

    pre_check = run_show_checks(
        child,
        target.port,
        "Pre-clear interface checks",
        stop_when_up_up=can_bounce_port,
    )
    peer = pre_check.peer if discover_peer else None

    if pre_check.port_unavailable:
        close_ssh_session(child)
        return child.exitstatus or 0, None, pre_check.already_up_up, False

    if can_bounce_port and pre_check.already_up_up:
        print("")
        print(
            f"{target.port} is already up/up in interface description and status; "
            "skipping link flap-protection clear and sudo nv port bounce."
        )
        close_ssh_session(child)
        return child.exitstatus or 0, peer, True, False

    clear_succeeded = clear_link_flap_protection(child, target.port)
    print("")
    if clear_succeeded:
        print(f"Link flap-protection violation cleared on {target.port}; skipping sudo nv port bounce.")
    else:
        if can_bounce_port:
            print(
                f"Link flap-protection violation clear did not succeed on {target.port}; "
                "continuing with sudo nv port bounce."
            )
            run_spectrum_port_bounce(child, target, password)
        else:
            print(f"Skipping sudo nv port bounce for {target.port}.")

    close_ssh_session(child)
    return child.exitstatus or 0, peer, pre_check.already_up_up, clear_succeeded


def run_port_check(runtime: RuntimeConfig, target: Target) -> None:
    exit_status, peer, primary_already_up_up, primary_clear_succeeded = run_one_side_port_check(
        runtime,
        target,
        "Primary side",
        discover_peer=True,
    )

    if primary_already_up_up:
        print("")
        print("Primary side is already up/up; peer side not checked.")
        raise SystemExit(exit_status)

    if primary_clear_succeeded:
        print("")
        print("Primary side link flap-protection violation clear succeeded; peer side not checked.")
        raise SystemExit(exit_status)

    if peer is None:
        print("")
        print("No peer found in primary interface description or LLDP output; peer side not checked.")
        raise SystemExit(exit_status)

    if peer.device == target.device and peer.port == target.port:
        print("")
        print("Peer resolved to the same device and port; peer side not checked again.")
        raise SystemExit(exit_status)

    print("")
    print(f"Peer side discovered from primary checks: {peer.device} {peer.port}")
    peer_exit_status, _, _, _ = run_one_side_port_check(
        runtime,
        Target(peer.device, peer.port),
        "Peer side",
        discover_peer=False,
    )
    raise SystemExit(exit_status or peer_exit_status)


def build_target_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spectrum_port_check.py",
        description="Run Spectrum port checks by hostname or site/rack/elevation.",
    )
    parser.add_argument("positional", nargs="*", help="<device> <port> or --location rack elevation port")
    parser.add_argument("--location", "--rack-port", "--rep", nargs=3, metavar=("RACK", "ELEVATION", "PORT"))
    parser.add_argument("--rack")
    parser.add_argument("--elevation")
    parser.add_argument("--port", "--device-port", dest="port")
    return parser


def parse_target_args(runtime: RuntimeConfig, argv: list[str]) -> Target:
    parser = build_target_parser()
    args = parser.parse_args(argv)

    if args.location:
        if args.positional or args.rack or args.elevation or args.port:
            parser.error("--location cannot be combined with other target arguments")
        rack, elevation, port = args.location
        target = resolve_device_from_location(runtime, rack, elevation)
        return Target(target.device, port, target.device_model, target.device_state, target.ssh_host)

    if args.rack or args.elevation or args.port:
        if args.positional:
            parser.error("--rack/--elevation/--port cannot be combined with positional device arguments")
        if not (args.rack and args.elevation and args.port):
            parser.error("--rack, --elevation, and --port must be provided together")
        target = resolve_device_from_location(runtime, args.rack, args.elevation)
        return Target(target.device, args.port, target.device_model, target.device_state, target.ssh_host)

    if len(args.positional) != 2:
        parser.error("expected <device> <device_port>")
    return Target(args.positional[0], args.positional[1])


def build_help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        usage="%(prog)s [--site SITE | SITE] TARGET_ARGS...",
        description="Run Spectrum port checks through one standalone site-aware entrypoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Target forms:\n"
            "  --site hsg17 --rack RACK --elevation ELEVATION --port PORT\n"
            "  hsg17 --location RACK ELEVATION PORT\n"
            "  --site aga4 DEVICE PORT\n"
            "  DEVICE PORT  # site is inferred from hostnames like hsg17-...\n\n"
            "Any site prefix is accepted; the prefix before the building number is used as the ncpcli region."
        ),
    )
    parser.add_argument("--site", "--tag", metavar="SITE", help="Site/build tag, for example aga5 or hsg17.")
    parser.add_argument("target_args", nargs="*", help="Target arguments for the check.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or any(arg in {"-h", "--help"} for arg in raw_args):
        build_help_parser().print_help()
        return 0

    try:
        site, target_args = split_site_args(raw_args)
        runtime = runtime_config_for_site(site)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Try --help for usage.", file=sys.stderr)
        return 2

    print(f"Site tag              : {runtime.site}")
    print(f"NCP region            : {runtime.region}")
    target = parse_target_args(runtime, target_args)
    run_port_check(runtime, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
