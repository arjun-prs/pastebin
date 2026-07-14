#!/usr/bin/env python3
"""
List ZTP images, verify DAN/autonet runtime status, check device certificate
secret-key status, hostname validation, static MAC, link flap protection,
config-diff, LLDP, gNMI, system health, and optics temperature for a region.

Examples:
  ./multiplanar_site_pre_checks.py iad60
  ./multiplanar_site_pre_checks.py iad60 --racks 0119,0120,0121,0122
  ./multiplanar_site_pre_checks.py --region hsg17 --skip-ztp --skip-dan --skip-certificate --skip-static-mac --skip-link-flap --skip-gnmi --device-file hosts.txt
  ./multiplanar_site_pre_checks.py jbp15 --hostname-validation --racks 0119,0120
  ./multiplanar_site_pre_checks.py jbp15 --vendor eos
  ./multiplanar_site_pre_checks.py jbp15 --contains 5.16
  ./multiplanar_site_pre_checks.py jbp15 --ncpcli-command 'env PYENV_VERSION=netops-env ncpcli'
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import getpass

try:
    import pexpect
except ModuleNotFoundError:
    pexpect = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_REQUIRED_ZTP_IMAGE = "cumulus.cumulus-linux-5.16.3-mlx-amd64.bin"
DEFAULT_STATIC_MAC_SCRIPT = "builtin"
DEFAULT_LINK_FLAP_SCRIPT = "builtin"
DEFAULT_RACKTOPO_SCRIPT = "builtin"
DEFAULT_QFAB_SWITCHES_PER_RACK = 8
HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE = r"(?i)(username|login)[: ]"
PASS_RE = r"(?i)password.*:\s*$"
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
SSH_HOST_SUFFIX = ""
NCPCLI_PROMPT_RE = r"(?m)ncpcli@[^>\r\n]*>\s*$"
CONFIG_DIFF_COMPARE_CONFIG_CMD = "devices compare-config --latest --group-output"


@dataclass
class ImageEntry:
    vendor: str
    image: str
    raw: str


@dataclass
class ZtpDeviceStatus:
    command: str
    verified: bool
    reason: str
    required_release: str
    checked_devices: int
    output: str


@dataclass
class DanStatus:
    command: str
    verified: bool
    reason: str
    fields: Dict[str, str]


@dataclass
class CertificateStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class HostnameValidationStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class StaticMacStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class StaticMacCheckResult:
    host: str
    ok: bool
    reason: str
    output: str


@dataclass
class MgmtTsStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass(frozen=True)
class NcpDeviceRow:
    name: str
    role: str
    model: str
    state: str
    ad: str
    location: str
    automation_state: str

    @property
    def rack_site(self) -> str:
        parts = self.location.split(":")
        return parts[0] if len(parts) >= 2 else ""

    @property
    def rack(self) -> str:
        parts = self.location.split(":")
        return parts[1] if len(parts) >= 2 else ""


@dataclass
class Device:
    host: str
    port: int = 22


@dataclass
class LinkFlapStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class ConfigDiffStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class LldpStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class GnmiStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class SystemHealthStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class OpticsTemperatureStatus:
    command: str
    verified: bool
    reason: str
    output: str


@dataclass
class Report:
    target: str
    region: str
    ztp_command: str
    total_images: int
    returned_images: int
    filters: Dict[str, Optional[str]]
    ztp_required_image: str
    ztp_required_image_present: bool
    ztp_device_status: ZtpDeviceStatus
    images: List[ImageEntry]
    dan: DanStatus
    certificate: CertificateStatus
    hostname_validation: HostnameValidationStatus
    mgmt_ts: MgmtTsStatus
    static_mac: StaticMacStatus
    link_flap: LinkFlapStatus
    config_diff: ConfigDiffStatus
    lldp: LldpStatus
    gnmi: GnmiStatus
    system_health: SystemHealthStatus
    optics_temperature: OpticsTemperatureStatus


def check_state(verified: bool, reason: str) -> str:
    if reason.lower().startswith("skipped"):
        return "SKIPPED"
    return "PASS" if verified else "FAIL"


def infer_region(target: str, explicit_region: Optional[str]) -> str:
    if explicit_region:
        return explicit_region.lower()
    match = re.match(r"^([a-z]+)", target.strip().lower())
    if not match:
        raise SystemExit(f"Could not infer region from target {target!r}; pass --region, -r, or --site.")
    return match.group(1)


def shell_join(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in args)


def one_line(value: object) -> str:
    return " ".join(str(value).split())


def progress_enabled(args: argparse.Namespace) -> bool:
    return not getattr(args, "json", False) and not getattr(args, "no_progress", False)


def scoped_switch_progress_total(
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
    fallback_per_rack: int = DEFAULT_QFAB_SWITCHES_PER_RACK,
) -> int:
    if hosts is not None:
        host_count = len(ordered_unique(hosts))
        if host_count:
            return host_count
    rack_count = len(ordered_unique(racks))
    if rack_count:
        return rack_count * fallback_per_rack
    return 1


class ProgressReporter:
    def __init__(self, label: str, total: int, enabled: bool = True) -> None:
        self.label = label
        self.total = total
        self.enabled = enabled and total > 0
        self.is_tty = sys.stderr.isatty()
        self.width = 28
        self.next_checkpoint = 0
        self.step = max(1, total // 20) if total else 1
        if self.enabled:
            self.update(0)

    def update(self, done: int, ok: int = 0, flagged: int = 0, failed: int = 0) -> None:
        if not self.enabled:
            return
        if not self.is_tty and done not in (0, self.total) and done < self.next_checkpoint:
            return
        self.next_checkpoint = done + self.step
        percent = int((done / self.total) * 100) if self.total else 100
        filled = int((done / self.total) * self.width) if self.total else self.width
        bar = "#" * filled + "-" * (self.width - filled)
        line = (
            f"{self.label}: [{bar}] {done}/{self.total} ({percent:3d}%) "
            f"ok={ok} flagged={flagged} failed={failed}"
        )
        if self.is_tty:
            print(f"\r{line}", file=sys.stderr, end="", flush=True)
            if done >= self.total:
                print(file=sys.stderr, flush=True)
        else:
            print(line, file=sys.stderr, flush=True)


def ordered_unique(values: Iterable[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def command_prefix(ncpcli_command: str, region: str, connection_methods: Optional[str]) -> List[str]:
    command = shlex.split(ncpcli_command) + ["-r", region]
    if connection_methods:
        command.extend(["--connection-methods", connection_methods])
    return command


def ztp_command(ncpcli_command: str, region: str, connection_methods: Optional[str]) -> List[str]:
    return command_prefix(ncpcli_command, region, connection_methods) + ["ztp-dhcp", "list-ztp-images"]


def dan_command(ncpcli_command: str) -> List[str]:
    return shlex.split(ncpcli_command) + ["acs", "autonet-runtime", "get-prod"]


def certificate_command(
    ncpcli_command: str,
    region: str,
    connection_methods: Optional[str],
    certificate_rack_region: Optional[str],
    racks: Sequence[str],
    exclude_management_devices: bool,
) -> List[str]:
    command = command_prefix(ncpcli_command, region, connection_methods) + ["devices", "certificate", "get"]
    if certificate_rack_region and racks:
        rack_filter = ",".join(f"{certificate_rack_region}:{rack}" for rack in racks)
        command.extend(["--devices-by-rack", rack_filter])
    if exclude_management_devices:
        command.extend(["--exclude-devices", "*-m1-*"])
    return command


def split_rack_values(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    return [rack for rack in re.split(r"[,\s]+", raw_value.strip()) if rack]


def read_device_file(path_value: str) -> List[str]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"device file not found: {path}")

    hosts: List[str] = []
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            host = line.split(",", 1)[0].strip()
            if host:
                hosts.append(host)
    return ordered_unique(hosts)


def prompt_for_racks_if_needed(args: argparse.Namespace) -> List[str]:
    rack_values = split_rack_values(args.racks)
    needs_racks = (
        not args.skip_certificate
        or not args.skip_mgmt_ts
        or not args.skip_static_mac
        or not args.skip_link_flap
        or not args.skip_config_diff
        or not args.skip_lldp
        or not args.skip_gnmi
        or not args.skip_system_health
        or not args.skip_optics_temperature
        or not args.skip_hostname_validation
    )
    if rack_values or args.device_file or not needs_racks:
        return rack_values

    raw = input(
        "Enter rack numbers for certificate/mgmt-ts/static MAC/link flap/config-diff/LLDP/gNMI/system health/optics temperature/hostname validation checks "
        "(comma separated, or press Enter to skip rack/device checks): "
    )
    return split_rack_values(raw)


CHECK_SELECTORS = [
    ("ztp", "ztp", "skip_ztp", "--ztp", "--skip-ztp"),
    ("dan", "dan", "skip_dan", "--dan", "--skip-dan"),
    ("certificate", "certificate", "skip_certificate", "--certificate", "--skip-certificate"),
    ("mgmt_ts", "mgmt_ts", "skip_mgmt_ts", "--mgmt-ts", "--skip-mgmt-ts"),
    ("hostname_validation", "hostname_validation", "skip_hostname_validation", "--hostname-validation", "--skip-hostname-validation"),
    ("static_mac", "static_mac", "skip_static_mac", "--static-mac", "--skip-static-mac"),
    ("link_flap", "link_flap", "skip_link_flap", "--linkflap", "--skip-link-flap"),
    ("config_diff", "config_diff", "skip_config_diff", "--config-diff", "--skip-config-diff"),
    ("lldp", "lldp", "skip_lldp", "--lldp", "--skip-lldp"),
    ("gnmi", "gnmi", "skip_gnmi", "--gnmi", "--skip-gnmi"),
    ("system_health", "system_health", "skip_system_health", "--system-health", "--skip-system-health"),
    ("optics_temperature", "optics_temperature", "skip_optics_temperature", "--optics-temperature", "--skip-optics-temperature"),
]


def apply_check_selection(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    selected = {name for name, attr, _skip_attr, _flag, _skip_flag in CHECK_SELECTORS if getattr(args, attr, False)}
    if not selected:
        return

    for name, attr, skip_attr, flag, skip_flag in CHECK_SELECTORS:
        if getattr(args, attr, False) and getattr(args, skip_attr, False):
            parser.error(f"{flag} cannot be combined with {skip_flag}")
        if name not in selected:
            setattr(args, skip_attr, True)


def get_jitpw_path(explicit_path: Optional[str] = None) -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise FileNotFoundError(f"jitpw not found or not executable: {path}")

    jitpw_path = shutil.which("jitpw")
    if jitpw_path:
        return jitpw_path

    fallback_paths = [
        Path("~/tools/jitpw/bin/jitpw").expanduser(),
        Path("~/jitpw/bin/jitpw").expanduser(),
        Path("~/bin/jitpw").expanduser(),
        Path("/usr/local/bin/jitpw"),
    ]
    for path in fallback_paths:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    raise FileNotFoundError("jitpw not found in PATH or fallback paths.")


def extract_jitpw_password(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        lower = line.lower()
        if lower.startswith(("info:", "warning:", "error:")):
            continue
        if set(line) == {"-"}:
            continue
        return line
    raise RuntimeError("jitpw returned no password.")


def get_jitpw_password(region: str, jitpw_path_arg: Optional[str] = None) -> str:
    jitpw_path = get_jitpw_path(jitpw_path_arg)
    result = subprocess.run([jitpw_path, "-e", region], capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"jitpw -e {region} failed: {stderr}")
    return extract_jitpw_password(result.stdout or "")


def require_pexpect() -> None:
    if pexpect is None:
        raise RuntimeError("pexpect is required for interactive checks. Install it with: python3 -m pip install pexpect")


def ssh_target_host(host: str) -> str:
    suffix = SSH_HOST_SUFFIX.strip()
    if suffix and "." not in host:
        return host + (suffix if suffix.startswith(".") else f".{suffix}")
    return host


def connect_ssh(host, username, password, port=22, timeout=30, strict_hostkey="ask", debug_log=None):
    require_pexpect()
    target_host = ssh_target_host(host)
    ssh_args = [
        "-o", f"StrictHostKeyChecking={strict_hostkey}",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-o", "PubkeyAuthentication=no",
        "-o", "IdentitiesOnly=yes",
        "-p", str(port),
        f"{username}@{target_host}",
    ]
    child = pexpect.spawn("ssh", args=ssh_args, encoding="utf-8", timeout=timeout)
    if debug_log:
        log_path = Path(debug_log).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        child.logfile = log_handle
        child._debug_log_handle = log_handle

    def close_failed_child() -> None:
        try:
            log_handle = getattr(child, "_debug_log_handle", None)
            if log_handle:
                child.logfile = None
                log_handle.close()
            child.close(force=True)
        except Exception:
            pass

    i = child.expect([HOSTKEY_RE, USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
    if i == 0:
        child.sendline("yes")
        i = child.expect([USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])

    if i == 0:
        child.sendline(username)
        try:
            child.expect(PASS_RE)
        except Exception:
            close_failed_child()
            raise
        child.sendline(password)
    elif i == 1:
        child.sendline(password)
    elif i == 2:
        details = (child.before or "").strip()
        target = target_host if target_host == host else f"{host} ({target_host})"
        close_failed_child()
        raise TimeoutError(f"Login timed out connecting to {target}: {details}" if details else f"Login timed out connecting to {target}")
    else:
        details = " ".join(part.strip() for part in [child.before or "", str(child.after or "")] if part and part.strip())
        target = target_host if target_host == host else f"{host} ({target_host})"
        close_failed_child()
        raise ConnectionError(f"EOF while connecting to {target}: {details}" if details else f"EOF while connecting to {target}")
    return child


def close_session(child):
    try:
        child.sendline("exit")
        child.expect(pexpect.EOF, timeout=10)
    finally:
        log_handle = getattr(child, "_debug_log_handle", None)
        if log_handle:
            child.logfile = None
            log_handle.close()
        child.close()


def nvidia_prepare(child, timeout=20):
    prompt = "PEXPECT_PROMPT> "
    child.sendline("")
    child.expect([r"(?m)^.*[$#]\s*$", pexpect.TIMEOUT], timeout=5)
    child.sendline('export PROMPT_COMMAND=""')
    child.sendline('export TERM=dumb')
    child.sendline('export PAGER=cat')
    child.sendline(f'export PS1="{prompt}"')
    child.expect_exact(prompt, timeout=timeout)
    return prompt


def nvidia_run_command(child, cmd, prompt, timeout=180):
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


def parse_interfaces_nvidia(output: str) -> List[str]:
    interfaces: List[str] = []
    seen = set()
    for line in (output or "").splitlines():
        if not line.strip() or line[0].isspace():
            continue
        if re.match(r"^[-\s]+$", line):
            continue
        if line.lower().startswith(("interface", "port", "name")):
            continue
        intf = line.split()[0]
        if not re.match(r"^(swp|eth|bond|br|lo|vlan)\S*$", intf):
            continue
        if intf not in seen:
            seen.add(intf)
            interfaces.append(intf)
    return interfaces


def filter_swp_upto_64(interfaces: List[str]) -> List[str]:
    out: List[str] = []
    for intf in interfaces:
        match = re.match(r"^swp(\d+)(?!\d)", intf)
        if not match:
            continue
        number = int(match.group(1))
        if 1 <= number <= 64:
            out.append(intf)
    return list(dict.fromkeys(out))


def parse_racktopo_hosts(output: str) -> List[tuple]:
    hosts: List[tuple] = []
    for line in (output or "").splitlines():
        line = line.rstrip()
        if not line.startswith("|") or line.startswith("|-"):
            continue
        if "name" in line and "deployment_group" in line:
            continue
        cols = [cell.strip() for cell in line.strip("|").split("|")]
        if not cols:
            continue
        name = cols[0]
        if not name or name.lower() == "name":
            continue
        if "netpdu" in name.lower() or "-m" in name.lower():
            continue
        hosts.append((name, 22))
    return hosts


class BuiltinLinkModule:
    connect_ssh = staticmethod(connect_ssh)
    close_session = staticmethod(close_session)
    nvidia_prepare = staticmethod(nvidia_prepare)
    nvidia_run_command = staticmethod(nvidia_run_command)
    parse_interfaces_nvidia = staticmethod(parse_interfaces_nvidia)
    filter_swp_upto_64 = staticmethod(filter_swp_upto_64)
    parse_racktopo_hosts = staticmethod(parse_racktopo_hosts)


def load_link_flap_module(script_path: str):
    if script_path == "builtin":
        return BuiltinLinkModule
    path = Path(script_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"link flap protection script not found: {path}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("link_flap_protection_ref", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load link flap protection script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_flap_protection_ref"] = module
    spec.loader.exec_module(module)
    return module


def normalize_devices(payload) -> List[Dict[str, object]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def get_nested(obj: Dict[str, object], parent: Optional[str], key: str, default: str = "") -> object:
    if parent is None:
        return obj.get(key, default)
    nested = obj.get(parent)
    return nested.get(key, default) if isinstance(nested, dict) else default


def pick_elevation(device: Dict[str, object]) -> object:
    for key in ("elevation", "rack_elevation", "u", "rack_u", "ru"):
        if device.get(key) is not None:
            return device.get(key)
    loc = device.get("location")
    if isinstance(loc, dict):
        for key in ("elevation", "u", "ru"):
            if loc.get(key) is not None:
                return loc.get(key)
    return ""


def derive_block(device: Dict[str, object], fallback: str = "") -> str:
    uid = str(device.get("uid", "") or "")
    if uid:
        parts = uid.split("-")
        return "-".join(parts[:2]) if len(parts) >= 2 else uid
    return fallback


def print_pipe_table(headers: List[str], rows: List[List[object]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len("" if cell is None else str(cell)))

    def fmt(cells: List[object], center: bool = False) -> str:
        rendered = []
        for index, cell in enumerate(cells):
            text = "" if cell is None else str(cell)
            rendered.append(text.center(widths[index]) if center else text.ljust(widths[index]))
        return "| " + " | ".join(rendered) + " |"

    lines = [fmt(headers, center=True), "|-" + "-|-".join("-" * width for width in widths) + "-|"]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def racktopo_output(region: str, rack: str, ncpcli_command: str = "ncpcli") -> str:
    bldg = region.lower()
    region_code = infer_region(bldg, None)
    cmd = shlex.split(ncpcli_command) + ["-r", region_code, "plan", "operations", "get-devices-by-rack", "--bldg", bldg, "--rack-number", rack]
    result = run_command(cmd, timeout=180)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"rack topology lookup failed for rack {rack}: {details}")
    devices = normalize_devices(json.loads(result.stdout or "[]"))
    headers = ["name", "deployment_group", "fabric_instance", "fabric_name", "plane", "rack", "elevation", "block"]
    rows: List[List[object]] = []
    for device in devices:
        name = str(device.get("name", "") or "")
        if "netpdu" in name.lower():
            continue
        rows.append([
            name,
            get_nested(device, "topology", "deployment_group_instance", ""),
            get_nested(device, "topology", "fabric_instance", ""),
            get_nested(device, "topology", "fabric_name", ""),
            get_nested(device, "topology", "plane_instance", ""),
            rack,
            pick_elevation(device),
            derive_block(device, fallback=bldg),
        ])
    return print_pipe_table(headers, rows)


def rack_hosts_from_topology(
    region: str,
    rack: str,
    racktopo_script: str,
    link_module,
    ncpcli_command: str = "ncpcli",
) -> List[str]:
    if racktopo_script == "builtin":
        output = racktopo_output(region, rack, ncpcli_command)
    else:
        result = run_command([sys.executable, racktopo_script, "-r", region, "--rack", rack], timeout=180)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"multiplaner_racktopo.py failed for rack {rack}: {details}")
        output = result.stdout or ""
    return [host for host, _port in link_module.parse_racktopo_hosts(output)]


def expand_rack_hosts_from_topology(
    region: str,
    racks: Sequence[str],
    racktopo_script: str,
    link_module,
    ncpcli_command: str = "ncpcli",
    racktopo_workers: int = 1,
) -> List[str]:
    rack_list = ordered_unique(racks)
    if not rack_list:
        return []

    workers = max(1, min(racktopo_workers, len(rack_list)))
    hosts_by_index: Dict[int, List[str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                rack_hosts_from_topology,
                region,
                rack,
                racktopo_script,
                link_module,
                ncpcli_command,
            ): index
            for index, rack in enumerate(rack_list)
        }
        for future in as_completed(future_map):
            hosts_by_index[future_map[future]] = future.result()

    hosts: List[str] = []
    for index in range(len(rack_list)):
        hosts.extend(hosts_by_index.get(index, []))
    return ordered_unique(hosts)


MAC_ADDRESS_RE = re.compile(r"^\s*mac-address:\s*([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s*$")
DEFAULT_STATIC_MAC_CMD = "nv config show | grep mac-address | tail -n 5"


def validate_mac_output(output: str, expected_count: int) -> tuple:
    lines = []
    invalid = []
    valid = []
    for raw_line in (output or "").splitlines():
        line = ANSI_RE.sub("", raw_line).strip()
        if not line:
            continue
        lines.append(line)
        match = MAC_ADDRESS_RE.match(line)
        if match:
            valid.append(match.group(1).upper())
        else:
            invalid.append(line)
    if len(lines) != expected_count:
        return False, f"expected {expected_count} output lines, found {len(lines)}"
    if invalid:
        return False, "one or more lines are not valid mac-address lines"
    if len(valid) != expected_count:
        return False, f"expected {expected_count} valid MAC addresses, found {len(valid)}"
    return True, "OK"


def check_static_mac_device(dev: Device, username: str, password: str, timeout: int, expected_count: int, debug_log: Optional[str]) -> StaticMacCheckResult:
    child = connect_ssh(dev.host, username, password, port=dev.port, timeout=max(timeout, 30), debug_log=debug_log)
    try:
        prompt = nvidia_prepare(child, timeout=timeout)
        output = nvidia_run_command(child, DEFAULT_STATIC_MAC_CMD, prompt, timeout=max(timeout, 180))
        ok, reason = validate_mac_output(output, expected_count)
        return StaticMacCheckResult(dev.host, ok, reason, output)
    finally:
        close_session(child)


def effective_ssh_username(username: Optional[str], ssh_domain: Optional[str]) -> str:
    value = username or getpass.getuser()
    if ssh_domain and "@" not in value:
        return f"{value}@{ssh_domain}"
    return value


def run_static_mac_status(args: argparse.Namespace, static_region: str, racks: Sequence[str], hosts: Optional[Sequence[str]] = None) -> StaticMacStatus:
    if args.skip_static_mac:
        return StaticMacStatus("", True, "Skipped by --skip-static-mac", "")
    if not racks and hosts is None:
        return StaticMacStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    if args.static_mac_script != "builtin":
        if not racks:
            return StaticMacStatus(
                "",
                False,
                "--device-file requires --static-mac-script builtin, or provide --racks for the external static MAC script",
                "",
            )
        command = [
            sys.executable, args.static_mac_script, "-r", static_region, "--racks", ",".join(racks),
            "--timeout", str(args.static_mac_timeout), "--workers", str(args.static_mac_workers),
            "--expected-count", str(args.static_mac_expected_count),
        ]
        if args.static_mac_skip_state_check:
            command.append("--skip-state-check")
        if args.static_mac_prompt_password:
            command.append("--prompt-password")
        if args.static_mac_show_ok:
            command.append("--show-ok")
        if args.static_mac_username:
            command.extend(["--username", args.static_mac_username])
        if args.static_mac_jit_region:
            command.extend(["--jit-region", args.static_mac_jit_region])
        if args.static_mac_jitpw_path:
            command.extend(["--jitpw-path", args.static_mac_jitpw_path])
        if args.static_mac_debug_log:
            command.extend(["--debug-log", args.static_mac_debug_log])
        result = run_command(command, args.static_mac_run_timeout)
        return parse_static_mac_status(result, command)

    device_hosts = list(hosts) if hosts is not None else expand_rack_hosts_from_topology(
        static_region,
        racks,
        args.racktopo_script,
        BuiltinLinkModule,
        args.ncpcli_command,
        args.racktopo_workers,
    )
    if not device_hosts:
        return StaticMacStatus("builtin static MAC check", False, "No devices found from selected racks", "")

    username = effective_ssh_username(args.static_mac_username, args.ssh_domain)
    if args.static_mac_prompt_password:
        password = getpass.getpass("Static MAC SSH password: ")
    else:
        jit_region = args.static_mac_jit_region or infer_region(static_region, None)
        print(f"Using static MAC SSH username: {username}")
        print(f"Using static MAC jitpw region: {jit_region}")
        password = get_jitpw_password(jit_region, args.static_mac_jitpw_path)

    ok: List[StaticMacCheckResult] = []
    bad: List[StaticMacCheckResult] = []
    fail: List[str] = []
    devices = [Device(host) for host in device_hosts]
    progress = ProgressReporter("Static MAC", len(devices), enabled=progress_enabled(args))
    completed = 0
    with ThreadPoolExecutor(max_workers=min(args.static_mac_workers, len(devices))) as executor:
        future_map = {
            executor.submit(check_static_mac_device, dev, username, password, args.static_mac_timeout, args.static_mac_expected_count, args.static_mac_debug_log): dev
            for dev in devices
        }
        for future in as_completed(future_map):
            dev = future_map[future]
            try:
                result = future.result()
                if result.ok:
                    ok.append(result)
                else:
                    bad.append(result)
            except Exception as exc:
                fail.append(f"{dev.host}: FAIL ({type(exc).__name__}: {one_line(exc)})")
            finally:
                completed += 1
                progress.update(completed, ok=len(ok), flagged=len(bad), failed=len(fail))

    output_lines = ["OK devices:"]
    if args.static_mac_show_ok:
        output_lines.extend(f"{item.host}: OK" for item in sorted(ok, key=lambda item: item.host))
    output_lines.extend(["", "Devices without expected mac-address output:"])
    for item in sorted(bad, key=lambda item: item.host):
        output_lines.append(f"{item.host}: {item.reason}")
        for line in item.output.splitlines() or ["<no output>"]:
            output_lines.append(f"  {line}")
    output_lines.extend(["", "Failures:"] + sorted(fail))
    output = "\n".join(output_lines)
    command = f"builtin static MAC check: {DEFAULT_STATIC_MAC_CMD} on {len(devices)} device(s)"
    if bad or fail:
        return StaticMacStatus(command, False, f"Static MAC verification failed on {len(bad)} device(s); {len(fail)} check failure(s)", output)
    return StaticMacStatus(command, True, f"Static MAC verification passed on all {len(ok)} checked device(s)", output)


def check_link_flap_device(host: str, username: str, password: str, timeout: int, debug_log: Optional[str], link_module) -> tuple:
    child = link_module.connect_ssh(host, username, password, timeout=max(timeout, 30), debug_log=debug_log)
    try:
        prompt = link_module.nvidia_prepare(child, timeout=timeout)
        interfaces_out = link_module.nvidia_run_command(child, "nv show interface | grep swp", prompt, timeout=max(timeout, 180))
        interfaces = link_module.filter_swp_upto_64(link_module.parse_interfaces_nvidia(interfaces_out))
        if not interfaces:
            return host, True, "no swp1..swp64 interfaces found"

        disabled_out = link_module.nvidia_run_command(
            child,
            "nv config show -o commands | grep 'link flap-protection state disabled'",
            prompt,
            timeout=max(timeout, 180),
        )
        disabled_interfaces = []
        for line in disabled_out.splitlines():
            match = re.search(r"\binterface\s+(\S+)\s+link flap-protection state disabled\b", line)
            if not match:
                continue
            for intf in expand_nvue_interface_list(match.group(1)):
                if intf in interfaces:
                    disabled_interfaces.append(intf)
        disabled_interfaces = list(dict.fromkeys(disabled_interfaces))
        if disabled_interfaces:
            return host, False, f"DISABLED on {len(disabled_interfaces)} interface(s): {compact_nvue_interfaces(disabled_interfaces)}"
        return host, True, "ENABLED on swp1..swp64"
    finally:
        link_module.close_session(child)


def check_lldp_device(host: str, username: str, password: str, timeout: int, debug_log: Optional[str], link_module) -> tuple:
    child = link_module.connect_ssh(host, username, password, timeout=max(timeout, 30), debug_log=debug_log)
    try:
        prompt = link_module.nvidia_prepare(child, timeout=timeout)
        interfaces_out = link_module.nvidia_run_command(child, "nv show interface | grep swp", prompt, timeout=max(timeout, 180))
        interfaces = link_module.filter_swp_upto_64(link_module.parse_interfaces_nvidia(interfaces_out))
        if not interfaces:
            return host, True, "no swp1..swp64 interfaces found"

        disabled_out = link_module.nvidia_run_command(
            child,
            "nv config show -o commands | grep 'lldp state disabled'",
            prompt,
            timeout=max(timeout, 180),
        )
        disabled_interfaces = []
        for line in disabled_out.splitlines():
            match = re.search(r"\binterface\s+(\S+)\s+lldp state disabled\b", line)
            if not match:
                continue
            for intf in expand_nvue_interface_list(match.group(1)):
                if intf in interfaces:
                    disabled_interfaces.append(intf)
        disabled_interfaces = list(dict.fromkeys(disabled_interfaces))
        if disabled_interfaces:
            return host, False, f"DISABLED on {len(disabled_interfaces)} interface(s): {compact_nvue_interfaces(disabled_interfaces)}"
        return host, True, "ENABLED on swp1..swp64"
    finally:
        link_module.close_session(child)


def clean_terminal_output(value: str) -> str:
    return ANSI_RE.sub("", value or "").replace("\r\n", "\n").replace("\r", "\n")


def strip_echoed_command(output: str, command: str) -> str:
    lines = clean_terminal_output(output).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() == command.strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def ncpcli_interactive_command(
    ncpcli_command: str,
    ncp_region: str,
    connection_methods: Optional[str],
) -> List[str]:
    command = command_prefix(ncpcli_command, ncp_region, connection_methods)
    command.append("interactive")
    return command


def ncpcli_quoted_device_list(hosts: Sequence[str]) -> str:
    return ",".join(f'"{host.replace(chr(34), chr(92) + chr(34))}"' for host in ordered_unique(hosts))


def split_pipe_table_row(line: str) -> Optional[List[str]]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def parse_current_device_rows(output: str) -> List[NcpDeviceRow]:
    rows: List[NcpDeviceRow] = []
    for line in clean_terminal_output(output).splitlines():
        cells = split_pipe_table_row(line)
        if cells is None or len(cells) < 6:
            continue
        if cells[0].lower() == "name":
            continue
        rows.append(
            NcpDeviceRow(
                name=cells[0],
                role=cells[1],
                model=cells[2],
                state=cells[3].lower(),
                ad=cells[4] if len(cells) > 4 else "",
                location=cells[5] if len(cells) > 5 else "",
                automation_state=cells[6] if len(cells) > 6 else "",
            )
        )
    return rows


def rack_pairs_from_racks(rack_region: str, racks: Sequence[str]) -> List[str]:
    return [f"{rack_region}:{rack}" for rack in ordered_unique(racks)]


def rack_pairs_from_device_rows(rows: Sequence[NcpDeviceRow]) -> List[str]:
    pairs: List[str] = []
    for row in rows:
        if row.rack_site and row.rack:
            pairs.append(f"{row.rack_site}:{row.rack}")
    return ordered_unique(pairs)


def run_ncpcli_interactive_commands(
    command: Sequence[str],
    commands: Sequence[str],
    timeout: int,
) -> Dict[str, str]:
    require_pexpect()
    child = pexpect.spawn(
        str(command[0]),
        args=[str(part) for part in command[1:]],
        encoding="utf-8",
        timeout=timeout,
        dimensions=(200, 4000),
    )
    outputs: Dict[str, str] = {}
    try:
        child.expect(NCPCLI_PROMPT_RE, timeout=timeout)
        for cmd in commands:
            child.sendline(cmd)
            child.expect(NCPCLI_PROMPT_RE, timeout=timeout)
            outputs[cmd] = strip_echoed_command(child.before or "", cmd)
        return outputs
    finally:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=10)
        except Exception:
            child.close(force=True)
        else:
            child.close()


def build_mgmt_ts_output(
    rack_pairs: Sequence[str],
    update_command: str,
    update_output: str,
    current_output: str,
    rows: Sequence[NcpDeviceRow],
) -> str:
    ok = []
    bad = []
    for row in rows:
        line = f"{row.name}: state={row.state or '-'} role={row.role or '-'} location={row.location or '-'}"
        if row.state == "in-service":
            ok.append(line)
        else:
            bad.append(line)

    lines = [
        f"Racks checked: {', '.join(rack_pairs) if rack_pairs else '-'}",
        f"Device-list command: {update_command}",
    ]
    if update_output.strip():
        lines.extend(["Device-list output:", update_output.strip()])
    lines.extend(
        [
            "Current-devices command: current-devices -va",
            f"Parsed mgmt/ts devices: {len(rows)}",
            "",
            "OK devices:",
            *sorted(ok),
            "",
            "Devices not in-service:",
            *sorted(bad),
            "",
            "Raw current-devices output:",
            current_output.strip(),
        ]
    )
    return "\n".join(lines)


def run_mgmt_ts_status(
    args: argparse.Namespace,
    ncp_region: str,
    rack_region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> MgmtTsStatus:
    if args.skip_mgmt_ts:
        return MgmtTsStatus("", True, "Skipped by --skip-mgmt-ts", "")
    if not racks and hosts is None:
        return MgmtTsStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    interactive_cmd = ncpcli_interactive_command(args.ncpcli_command, ncp_region, args.connection_methods)
    timeout = max(args.timeout, args.mgmt_ts_timeout)
    rack_pairs = rack_pairs_from_racks(rack_region, racks)
    commands: List[str] = []
    resolve_command = ""

    if hosts is not None:
        if not hosts:
            return MgmtTsStatus("", False, "No devices found in --device-file", "")
        resolve_command = f"update-device-list --device-names-matching {ncpcli_quoted_device_list(hosts)}"
        commands.extend([resolve_command, "current-devices -va"])

    if commands:
        resolve_outputs = run_ncpcli_interactive_commands(interactive_cmd, commands, timeout=timeout)
        resolved_rows = parse_current_device_rows(resolve_outputs.get("current-devices -va", ""))
        resolved_names = {row.name.lower() for row in resolved_rows}
        missing_hosts = [host for host in hosts if host.lower() not in resolved_names]
        if missing_hosts:
            output = "\n".join(
                [
                    f"Device-list command: {resolve_command}",
                    f"Missing host(s): {', '.join(missing_hosts)}",
                    "",
                    "Raw current-devices output:",
                    resolve_outputs.get("current-devices -va", "").strip(),
                ]
            )
            command = f"{shell_join(interactive_cmd)}; {resolve_command}; current-devices -va"
            return MgmtTsStatus(command, False, f"Unable to resolve rack location for {len(missing_hosts)} host(s)", output)
        rack_pairs = rack_pairs_from_device_rows(resolved_rows)
        if not rack_pairs:
            output = "\n".join(
                [
                    f"Device-list command: {resolve_command}",
                    "Unable to resolve rack locations from selected devices.",
                    "",
                    "Raw current-devices output:",
                    resolve_outputs.get("current-devices -va", "").strip(),
                ]
            )
            command = f"{shell_join(interactive_cmd)}; {resolve_command}; current-devices -va"
            return MgmtTsStatus(command, False, "Unable to resolve rack locations from --device-file hosts", output)

    update_command = f"update-device-list --devices-by-rack {','.join(rack_pairs)} --role {args.mgmt_ts_roles}"
    current_command = "current-devices -va"
    command = f"{shell_join(interactive_cmd)}; {update_command}; {current_command}"
    outputs = run_ncpcli_interactive_commands(
        interactive_cmd,
        [update_command, current_command],
        timeout=timeout,
    )
    update_output = outputs.get(update_command, "")
    current_output = outputs.get(current_command, "")
    rows = parse_current_device_rows(current_output)
    output = build_mgmt_ts_output(rack_pairs, update_command, update_output, current_output, rows)

    if not rows:
        return MgmtTsStatus(command, False, "No mgmt/ts devices were returned for selected rack(s)", output)

    bad = [row for row in rows if row.state != "in-service"]
    if bad:
        return MgmtTsStatus(
            command,
            False,
            f"{len(bad)} of {len(rows)} mgmt/ts switch(es) are not in-service",
            output,
        )

    return MgmtTsStatus(
        command,
        True,
        f"All {len(rows)} mgmt/ts switch(es) in selected rack(s) are in-service",
        output,
    )


def extract_compare_config_devices(text: str) -> List[str]:
    devices = re.findall(r"\b[a-z][a-z0-9]*\d+-[A-Za-z0-9_.-]*\b", clean_terminal_output(text), flags=re.IGNORECASE)
    return ordered_unique(devices)


def parse_compare_config_output(output: str, expected_hosts: Optional[Sequence[str]] = None) -> Dict[str, object]:
    clean = clean_terminal_output(output)
    sections: Dict[str, List[str]] = {"errors": [], "diff": [], "no_diff": []}
    current_section: Optional[str] = None
    group_count: Optional[int] = None

    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            current_section = None
            continue
        group_match = re.match(r"^<qfabt0=(\d+)>$", line, flags=re.IGNORECASE)
        if group_match:
            group_count = int(group_match.group(1))
            current_section = None
            continue
        section_match = re.match(r"^Devices with\s+([^:]+):\s*(.*)$", line, flags=re.IGNORECASE)
        if section_match:
            label = section_match.group(1).strip().lower()
            if "no diff" in label:
                current_section = "no_diff"
            elif "error" in label:
                current_section = "errors"
            else:
                current_section = "diff"
            remainder = section_match.group(2).strip()
            if remainder:
                sections[current_section].append(remainder)
            continue
        if current_section:
            sections[current_section].append(line)

    no_diff_devices = extract_compare_config_devices(" ".join(sections["no_diff"]))
    error_text = " ".join(sections["errors"]).strip()
    diff_text = " ".join(sections["diff"]).strip()
    error_devices = extract_compare_config_devices(error_text)
    diff_devices = extract_compare_config_devices(diff_text)
    expected = ordered_unique(expected_hosts or [])
    no_diff_set = set(no_diff_devices)
    missing_expected = [host for host in expected if host not in no_diff_set]

    error_present = bool(error_devices or error_text)
    diff_present = bool(diff_devices or diff_text)
    if expected:
        verified = not error_present and not diff_present and not missing_expected
    else:
        verified = not error_present and not diff_present and bool(no_diff_devices)
    if group_count is not None and expected and group_count != len(expected):
        verified = False

    return {
        "verified": verified,
        "no_diff_devices": no_diff_devices,
        "error_devices": error_devices,
        "diff_devices": diff_devices,
        "error_text": error_text,
        "diff_text": diff_text,
        "missing_expected": missing_expected,
        "group_count": group_count,
        "raw_output": clean.strip(),
    }


def build_config_diff_output(update_command: str, update_output: str, compare_output: str, parsed: Dict[str, object]) -> str:
    no_diff_devices = parsed.get("no_diff_devices", [])
    error_devices = parsed.get("error_devices", [])
    diff_devices = parsed.get("diff_devices", [])
    missing_expected = parsed.get("missing_expected", [])
    group_count = parsed.get("group_count")

    lines = [
        f"Device-list command: {update_command}",
    ]
    if update_output.strip():
        lines.extend(["Device-list output:", update_output.strip()])
    lines.extend([
        f"Compare command: {CONFIG_DIFF_COMPARE_CONFIG_CMD}",
        f"qfabt0 group count: {group_count if group_count is not None else 'unknown'}",
        f"Devices with no Diff ({len(no_diff_devices)}): {', '.join(no_diff_devices) if no_diff_devices else '-'}",
    ])
    if diff_devices or parsed.get("diff_text"):
        lines.append(f"Devices with Diff ({len(diff_devices)}): {', '.join(diff_devices) if diff_devices else parsed.get('diff_text')}")
    if error_devices or parsed.get("error_text"):
        lines.append(f"Devices with errors ({len(error_devices)}): {', '.join(error_devices) if error_devices else parsed.get('error_text')}")
    if missing_expected:
        lines.append(f"Expected devices missing from no Diff ({len(missing_expected)}): {', '.join(missing_expected)}")
    lines.extend(["", "Raw compare-config output:", compare_output.strip()])
    return "\n".join(str(line) for line in lines if line is not None)


def parse_gnmi_status_metrics(output: str) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if set(line) <= {"-", " "}:
            continue
        if line.lower() == "operational":
            continue
        if "operational" in line.lower() and set(line.replace("operational", "").strip()) <= {"-"}:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].strip()
        value = parts[-1].strip()
        if key.startswith("[") and key.endswith("]"):
            key = key.strip("[]")
        metrics[key] = value
    return metrics


def check_gnmi_device(host: str, username: str, password: str, timeout: int, debug_log: Optional[str], link_module) -> tuple:
    child = link_module.connect_ssh(host, username, password, timeout=max(timeout, 30), debug_log=debug_log)
    try:
        prompt = link_module.nvidia_prepare(child, timeout=timeout)
        gnmi_out = link_module.nvidia_run_command(
            child,
            "nv show system gnmi-server status",
            prompt,
            timeout=max(timeout, 180),
        )
        if not gnmi_out.strip():
            return host, False, "no gNMI status output returned"

        metrics = parse_gnmi_status_metrics(gnmi_out)
        if not metrics:
            return host, False, "gNMI status output did not contain operational counters"

        active = metrics.get("total-active-subscriptions", "-")
        received = metrics.get("received-subscription-requests", "-")
        rejected = metrics.get("rejected-subscriptions", "-")
        clients = metrics.get("client", "-")
        return (
            host,
            True,
            (
                "operational; "
                f"active-subscriptions={active}, "
                f"received-requests={received}, "
                f"rejected={rejected}, "
                f"clients={clients}"
            ),
        )
    finally:
        link_module.close_session(child)


def expand_nvue_interface_token(token: str) -> List[str]:
    token = token.strip()
    if not token:
        return []

    subport_range = re.fullmatch(r"(swp\d+s)(\d+)-(\d+)", token)
    if subport_range:
        prefix, start_s, end_s = subport_range.groups()
        start, end = int(start_s), int(end_s)
        if start <= end:
            return [f"{prefix}{index}" for index in range(start, end + 1)]

    port_range = re.fullmatch(r"swp(\d+)-(\d+)", token)
    if port_range:
        start, end = (int(value) for value in port_range.groups())
        if start <= end:
            return [f"swp{index}" for index in range(start, end + 1)]

    return [token]


def expand_nvue_interface_list(value: str) -> List[str]:
    interfaces: List[str] = []
    for token in value.split(","):
        interfaces.extend(expand_nvue_interface_token(token))
    return list(dict.fromkeys(interfaces))


def compact_nvue_interfaces(interfaces: Sequence[str]) -> str:
    grouped: Dict[str, List[int]] = {}
    plain: List[str] = []
    for intf in interfaces:
        match = re.fullmatch(r"(swp\d+s)(\d+)", intf)
        if not match:
            plain.append(intf)
            continue
        prefix, subport = match.groups()
        grouped.setdefault(prefix, []).append(int(subport))

    compacted: List[str] = []
    for prefix in sorted(grouped, key=lambda item: int(re.search(r"\d+", item).group(0))):
        values = sorted(set(grouped[prefix]))
        start = previous = values[0]
        for value in values[1:]:
            if value == previous + 1:
                previous = value
                continue
            compacted.append(f"{prefix}{start}" if start == previous else f"{prefix}{start}-{previous}")
            start = previous = value
        compacted.append(f"{prefix}{start}" if start == previous else f"{prefix}{start}-{previous}")

    return ",".join(compacted + sorted(plain))


def run_link_flap_status(
    args: argparse.Namespace,
    region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> LinkFlapStatus:
    if args.skip_link_flap:
        return LinkFlapStatus("", True, "Skipped by --skip-link-flap", "")
    if not racks and hosts is None:
        return LinkFlapStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    link_module = load_link_flap_module(args.link_flap_script)
    if hosts is not None:
        device_hosts = list(hosts)
    else:
        device_hosts = expand_rack_hosts_from_topology(
            region,
            racks,
            args.racktopo_script,
            link_module,
            args.ncpcli_command,
            args.racktopo_workers,
        )
    if not device_hosts:
        return LinkFlapStatus("", False, "No devices found from selected racks", "")

    username = effective_ssh_username(args.link_flap_username, args.ssh_domain)
    if args.link_flap_prompt_password:
        password = getpass.getpass("Link flap SSH password: ")
    else:
        jit_region = args.link_flap_jit_region or infer_region(region, None)
        print(f"Using link flap SSH username: {username}")
        print(f"Using link flap jitpw region: {jit_region}")
        password = get_jitpw_password(jit_region, args.link_flap_jitpw_path)
    command = f"SSH check: nv config show -o commands | grep 'link flap-protection state disabled' on {len(device_hosts)} device(s)"

    ok: List[str] = []
    bad: List[str] = []
    fail: List[str] = []
    progress = ProgressReporter("Link flap", len(device_hosts), enabled=progress_enabled(args))
    completed = 0
    with ThreadPoolExecutor(max_workers=min(args.link_flap_workers, len(device_hosts))) as executor:
        future_map = {
            executor.submit(
                check_link_flap_device,
                host,
                username,
                password,
                args.link_flap_timeout,
                args.link_flap_debug_log,
                link_module,
            ): host
            for host in device_hosts
        }
        for future in as_completed(future_map):
            host = future_map[future]
            try:
                result_host, is_ok, detail = future.result()
                line = f"{result_host}: {detail}"
                if is_ok:
                    ok.append(line)
                else:
                    bad.append(line)
            except Exception as exc:
                fail.append(f"{host}: FAIL ({type(exc).__name__}: {one_line(exc)})")
            finally:
                completed += 1
                progress.update(completed, ok=len(ok), flagged=len(bad), failed=len(fail))

    output_lines = ["OK devices:"] + sorted(ok) + ["", "Devices with disabled config:"] + sorted(bad) + ["", "Failures:"] + sorted(fail)
    output = "\n".join(output_lines)
    if bad or fail:
        if bad:
            reason = f"Link flap protection is DISABLED on {len(bad)} device(s); {len(fail)} check failure(s)"
        else:
            reason = f"Unable to verify link flap protection on {len(fail)} device(s)"
        return LinkFlapStatus(command, False, reason, output)

    return LinkFlapStatus(command, True, f"Link flap protection is ENABLED on all {len(ok)} checked device(s)", output)


def run_config_diff_status(
    args: argparse.Namespace,
    region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> ConfigDiffStatus:
    if args.skip_config_diff:
        return ConfigDiffStatus("", True, "Skipped by --skip-config-diff", "")
    if not racks and hosts is None:
        return ConfigDiffStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    if hosts is not None:
        device_hosts = list(hosts)
    else:
        link_module = load_link_flap_module(args.link_flap_script)
        device_hosts = expand_rack_hosts_from_topology(
            region,
            racks,
            args.racktopo_script,
            link_module,
            args.ncpcli_command,
            args.racktopo_workers,
        )
    if not device_hosts:
        scope = "selected racks" if racks else "selected devices"
        return ConfigDiffStatus("", False, f"No devices found from {scope}", "")

    ncp_region = infer_region(region, None)
    if racks:
        rack_filter = ",".join(f"{region}:{rack}" for rack in ordered_unique(racks))
        update_command = f"update-device-list --rack {rack_filter} --role qfabt0"
    else:
        update_command = f"update-device-list --device-names-matching {ncpcli_quoted_device_list(device_hosts)} --role qfabt0"
    interactive_cmd = ncpcli_interactive_command(args.ncpcli_command, ncp_region, args.connection_methods)
    command = f"{shell_join(interactive_cmd)}; {update_command}; {CONFIG_DIFF_COMPARE_CONFIG_CMD}"
    timeout = max(args.timeout, args.config_diff_timeout)
    progress_total = scoped_switch_progress_total(racks, device_hosts)
    progress = ProgressReporter("Config diff", progress_total, enabled=progress_enabled(args))

    outputs = run_ncpcli_interactive_commands(
        interactive_cmd,
        [update_command, CONFIG_DIFF_COMPARE_CONFIG_CMD],
        timeout=timeout,
    )
    update_output = outputs.get(update_command, "")
    compare_output = outputs.get(CONFIG_DIFF_COMPARE_CONFIG_CMD, "")
    if re.search(r"(?i)\b(error|failed|traceback)\b", update_output):
        output = build_config_diff_output(update_command, update_output, compare_output, {
            "no_diff_devices": [],
            "error_devices": [],
            "diff_devices": [],
            "missing_expected": [],
            "group_count": None,
            "error_text": update_output.strip(),
            "diff_text": "",
        })
        progress.update(progress_total, failed=progress_total)
        return ConfigDiffStatus(command, False, "Unable to build qfabt0 device list for config-diff check", output)

    parsed = parse_compare_config_output(compare_output, device_hosts)
    output = build_config_diff_output(update_command, update_output, compare_output, parsed)
    no_diff_count = len(parsed.get("no_diff_devices", []))
    ok_count = min(no_diff_count, progress_total)
    flagged_count = max(0, progress_total - ok_count)
    progress.update(progress_total, ok=ok_count, flagged=flagged_count)
    if parsed.get("verified"):
        return ConfigDiffStatus(
            command,
            True,
            f"No config diffs found by compare-config for {no_diff_count} qfabt0 device(s)",
            output,
        )

    issues: List[str] = []
    if parsed.get("diff_devices") or parsed.get("diff_text"):
        issues.append(f"config diffs detected for {len(parsed.get('diff_devices', [])) or 'unknown'} device(s)")
    if parsed.get("error_devices") or parsed.get("error_text"):
        issues.append(f"compare-config errors for {len(parsed.get('error_devices', [])) or 'unknown'} device(s)")
    if parsed.get("missing_expected"):
        issues.append(f"{len(parsed.get('missing_expected', []))} expected device(s) missing from no Diff")
    if parsed.get("group_count") is not None and int(parsed["group_count"]) != len(device_hosts):
        issues.append(f"qfabt0 group count {parsed['group_count']} did not match expected {len(device_hosts)}")
    reason = "; ".join(issues) if issues else "Unable to verify config-diff no-diff status"
    return ConfigDiffStatus(command, False, reason, output)


def run_lldp_status(
    args: argparse.Namespace,
    region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> LldpStatus:
    if args.skip_lldp:
        return LldpStatus("", True, "Skipped by --skip-lldp", "")
    if not racks and hosts is None:
        return LldpStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    link_module = load_link_flap_module(args.link_flap_script)
    if hosts is not None:
        device_hosts = list(hosts)
    else:
        device_hosts = expand_rack_hosts_from_topology(
            region,
            racks,
            args.racktopo_script,
            link_module,
            args.ncpcli_command,
            args.racktopo_workers,
        )
    if not device_hosts:
        scope = "selected racks" if racks else "selected devices"
        return LldpStatus("", False, f"No devices found from {scope}", "")

    username = effective_ssh_username(args.link_flap_username, args.ssh_domain)
    if args.link_flap_prompt_password:
        password = getpass.getpass("LLDP SSH password: ")
    else:
        jit_region = args.link_flap_jit_region or infer_region(region, None)
        print(f"Using LLDP SSH username: {username}")
        print(f"Using LLDP jitpw region: {jit_region}")
        password = get_jitpw_password(jit_region, args.link_flap_jitpw_path)
    command = f"SSH check: nv config show -o commands | grep 'lldp state disabled' on {len(device_hosts)} device(s)"

    ok: List[str] = []
    bad: List[str] = []
    fail: List[str] = []
    progress = ProgressReporter("LLDP", len(device_hosts), enabled=progress_enabled(args))
    completed = 0
    with ThreadPoolExecutor(max_workers=min(args.link_flap_workers, len(device_hosts))) as executor:
        future_map = {
            executor.submit(
                check_lldp_device,
                host,
                username,
                password,
                args.link_flap_timeout,
                args.link_flap_debug_log,
                link_module,
            ): host
            for host in device_hosts
        }
        for future in as_completed(future_map):
            host = future_map[future]
            try:
                result_host, is_ok, detail = future.result()
                line = f"{result_host}: {detail}"
                if is_ok:
                    ok.append(line)
                else:
                    bad.append(line)
            except Exception as exc:
                fail.append(f"{host}: FAIL ({type(exc).__name__}: {one_line(exc)})")
            finally:
                completed += 1
                progress.update(completed, ok=len(ok), flagged=len(bad), failed=len(fail))

    output_lines = ["OK devices:"] + sorted(ok) + ["", "Devices with disabled config:"] + sorted(bad) + ["", "Failures:"] + sorted(fail)
    output = "\n".join(output_lines)
    if bad or fail:
        if bad:
            reason = f"LLDP is DISABLED on {len(bad)} device(s); {len(fail)} check failure(s)"
        else:
            reason = f"Unable to verify LLDP configuration on {len(fail)} device(s)"
        return LldpStatus(command, False, reason, output)

    return LldpStatus(command, True, f"LLDP is ENABLED on all {len(ok)} checked device(s)", output)


HOSTNAME_VALIDATION_FAILURE_PATTERNS = [
    (re.compile(r"\bHostname validation failures/errors saved to\b", re.IGNORECASE), "hostname validation failure output was saved"),
    (re.compile(r"\bHostname mismatch\b", re.IGNORECASE), "hostname mismatch reported"),
    (re.compile(r"\bAUTH_FAILED\b"), "device SSH authentication failed"),
    (re.compile(r"\bSERIAL_NOT_FOUND\b"), "device serial number was not found"),
    (re.compile(r"\bVENDOR_NOT_IDENTIFIED\b"), "device vendor could not be identified"),
    (re.compile(r"\bUnable to SSH/authenticate to device\b", re.IGNORECASE), "device SSH authentication failed"),
    (re.compile(r"\bSerial number not found in device command output\b", re.IGNORECASE), "device serial number was not found"),
    (re.compile(r"\bError while collecting serial number from device\b", re.IGNORECASE), "device serial collection failed"),
    (re.compile(r"\bCannot identify vendor\b", re.IGNORECASE), "device vendor could not be identified"),
    (re.compile(r"\bInvalid serial collected from device\b", re.IGNORECASE), "invalid device serial was collected"),
    (re.compile(r"\bStorekeeper asset lookup failed\b", re.IGNORECASE), "Storekeeper asset lookup failed"),
    (re.compile(r"\bNo serial numbers collected from devices\b", re.IGNORECASE), "no serial numbers were collected"),
    (re.compile(r"\|\s*ERROR\s*\|"), "hostname validation table contains ERROR rows"),
    (re.compile(r"\bwrong-serial-match-[^\s]+", re.IGNORECASE), "wrong serial match artifact was generated"),
    (re.compile(r"\bTraceback \(most recent call last\):"), "qcli traceback reported"),
]


def is_qfabt0_role(role: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", role.lower()) == "qfabt0"


def build_hostname_validation_scope_output(
    rack_pairs: Sequence[str],
    update_command: str,
    update_output: str,
    current_output: str,
    qfabt0_hosts: Sequence[str],
) -> str:
    lines = [
        f"Racks checked: {', '.join(rack_pairs) if rack_pairs else '-'}",
        f"Device-list command: {update_command}",
    ]
    if update_output.strip():
        lines.extend(["Device-list output:", update_output.strip()])
    lines.extend(
        [
            "Current-devices command: current-devices -va",
            f"Parsed qfabt0 devices: {len(qfabt0_hosts)}",
            "",
            "Selected qfabt0 devices:",
        ]
    )
    lines.extend(qfabt0_hosts or ["-"])
    lines.extend(["", "Raw current-devices output:", current_output.strip()])
    return "\n".join(lines)


def resolve_hostname_validation_qfabt0_hosts(
    args: argparse.Namespace,
    ncp_region: str,
    rack_region: str,
    racks: Sequence[str],
) -> tuple:
    rack_pairs = rack_pairs_from_racks(rack_region, racks)
    update_command = f"update-device-list --rack {','.join(rack_pairs)} --role qfabt0"
    current_command = "current-devices -va"
    interactive_cmd = ncpcli_interactive_command(args.ncpcli_command, ncp_region, args.connection_methods)
    timeout = max(args.timeout, args.hostname_validation_timeout)
    outputs = run_ncpcli_interactive_commands(
        interactive_cmd,
        [update_command, current_command],
        timeout=timeout,
    )
    update_output = outputs.get(update_command, "")
    current_output = outputs.get(current_command, "")
    rows = parse_current_device_rows(current_output)
    qfabt0_hosts = ordered_unique(row.name for row in rows if row.name and is_qfabt0_role(row.role))
    scope_output = build_hostname_validation_scope_output(
        rack_pairs,
        update_command,
        update_output,
        current_output,
        qfabt0_hosts,
    )
    command_text = f"{shell_join(interactive_cmd)}; {update_command}; {current_command}"
    return qfabt0_hosts, command_text, scope_output


def parse_hostname_validation_status(
    result: subprocess.CompletedProcess[str],
    command_text: str,
    device_hosts: Sequence[str],
    scope_output: str,
) -> HostnameValidationStatus:
    qcli_output = "\n".join(
        part for part in [(result.stdout or "").strip(), (result.stderr or "").strip()] if part
    )
    output_parts = [
        f"Devices passed to hostname validation: {len(device_hosts)}",
        "Hostname validation input devices:",
        "\n".join(device_hosts) if device_hosts else "-",
    ]
    if scope_output.strip():
        output_parts.extend(["", "qfabt0 scope output:", scope_output.strip()])
    if qcli_output:
        output_parts.extend(["", "qcli output:", qcli_output])
    output = "\n".join(output_parts).strip()

    if result.returncode != 0:
        return HostnameValidationStatus(
            command_text,
            False,
            f"Hostname validation failed with exit code {result.returncode}",
            output,
        )

    clean_output = clean_terminal_output(output)
    failures = []
    for pattern, reason in HOSTNAME_VALIDATION_FAILURE_PATTERNS:
        if pattern.search(clean_output):
            failures.append(reason)

    if failures:
        return HostnameValidationStatus(
            command_text,
            False,
            "Hostname validation reported issue(s): " + "; ".join(ordered_unique(failures)),
            output,
        )

    if not qcli_output.strip():
        return HostnameValidationStatus(
            command_text,
            False,
            "Hostname validation command completed but returned no qcli output",
            output,
        )

    return HostnameValidationStatus(
        command_text,
        True,
        f"Hostname validation completed for {len(device_hosts)} device(s); no failure markers found",
        output,
    )


def run_hostname_validation_status(
    args: argparse.Namespace,
    region: str,
    rack_region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> HostnameValidationStatus:
    if args.skip_hostname_validation:
        return HostnameValidationStatus("", True, "Skipped by --skip-hostname-validation", "")
    if not racks and hosts is None:
        return HostnameValidationStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    scope_command = ""
    scope_output = ""
    if racks:
        device_hosts, scope_command, scope_output = resolve_hostname_validation_qfabt0_hosts(
            args,
            region,
            rack_region,
            racks,
        )
        if not device_hosts:
            return HostnameValidationStatus(
                scope_command,
                False,
                "No qfabt0 devices were returned for selected rack(s)",
                scope_output,
            )
    else:
        device_hosts = ordered_unique(hosts or [])
        if not device_hosts:
            return HostnameValidationStatus("", False, "No devices found in --device-file", "")

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            tmp_path = handle.name
            for host in device_hosts:
                handle.write(f"{host}\n")

        command = shlex.split(args.qcli_command) + [
            "fabric-ops",
            "device-serial-val",
            "--region",
            region,
            "--filename",
            tmp_path,
        ]
        result = run_command(command, args.hostname_validation_timeout)
        qcli_command_text = shell_join(command).replace(shlex.quote(tmp_path), "<hostname-validation-devices-file>")
        command_text = f"{scope_command}; {qcli_command_text}" if scope_command else qcli_command_text
        return parse_hostname_validation_status(result, command_text, device_hosts, scope_output)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def run_gnmi_status(
    args: argparse.Namespace,
    region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> GnmiStatus:
    if args.skip_gnmi:
        return GnmiStatus("", True, "Skipped by --skip-gnmi", "")
    if not racks and hosts is None:
        return GnmiStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    link_module = load_link_flap_module(args.link_flap_script)
    device_hosts = list(hosts) if hosts is not None else expand_rack_hosts_from_topology(
        region,
        racks,
        args.racktopo_script,
        link_module,
        args.ncpcli_command,
        args.racktopo_workers,
    )
    if not device_hosts:
        return GnmiStatus("", False, "No devices found from selected racks", "")

    username = effective_ssh_username(args.link_flap_username, args.ssh_domain)
    if args.link_flap_prompt_password:
        password = getpass.getpass("gNMI SSH password: ")
    else:
        jit_region = args.link_flap_jit_region or infer_region(region, None)
        print(f"Using gNMI SSH username: {username}")
        print(f"Using gNMI jitpw region: {jit_region}")
        password = get_jitpw_password(jit_region, args.link_flap_jitpw_path)
    command = f"SSH check: nv show system gnmi-server status on {len(device_hosts)} device(s)"

    ok: List[str] = []
    bad: List[str] = []
    fail: List[str] = []
    progress = ProgressReporter("gNMI", len(device_hosts), enabled=progress_enabled(args))
    completed = 0
    with ThreadPoolExecutor(max_workers=min(args.link_flap_workers, len(device_hosts))) as executor:
        future_map = {
            executor.submit(
                check_gnmi_device,
                host,
                username,
                password,
                args.link_flap_timeout,
                args.link_flap_debug_log,
                link_module,
            ): host
            for host in device_hosts
        }
        for future in as_completed(future_map):
            host = future_map[future]
            try:
                result_host, is_ok, detail = future.result()
                line = f"{result_host}: {detail}"
                if is_ok:
                    ok.append(line)
                else:
                    bad.append(line)
            except Exception as exc:
                fail.append(f"{host}: FAIL ({type(exc).__name__}: {one_line(exc)})")
            finally:
                completed += 1
                progress.update(completed, ok=len(ok), flagged=len(bad), failed=len(fail))

    output_lines = ["OK devices:"] + sorted(ok) + ["", "Devices with gNMI issue:"] + sorted(bad) + ["", "Failures:"] + sorted(fail)
    output = "\n".join(output_lines)
    if bad or fail:
        if bad:
            reason = f"gNMI status could not be verified on {len(bad)} device(s); {len(fail)} check failure(s)"
        else:
            reason = f"Unable to verify gNMI status on {len(fail)} device(s)"
        return GnmiStatus(command, False, reason, output)

    return GnmiStatus(command, True, f"gNMI status is operational on all {len(ok)} checked device(s)", output)


SYSTEM_HEALTH_RUN_COMMAND = 'devices run-command "nv show system health -o json"'


def summarize_system_health_issues(value: Any) -> str:
    if value in (None, "", {}, []):
        return "-"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, dict):
                issue = item.get("issue")
                if issue is not None and len(item) == 1:
                    parts.append(f"{key}: {issue}")
                else:
                    detail = ", ".join(f"{subkey}={subvalue}" for subkey, subvalue in sorted(item.items()))
                    parts.append(f"{key}: {detail or item}")
            else:
                parts.append(f"{key}: {item}")
        return "; ".join(parts) if parts else "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "-"
    return str(value)


def parse_system_health_results(output: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for block in re.split(r"\*{8,}", clean_terminal_output(output)):
        entity_match = re.search(r"(?m)^\s*Entity:\s*(\S+)\s*$", block)
        if not entity_match:
            continue
        device = entity_match.group(1)
        result_match = re.search(r"(?ms)^\s*Result:\s*(.*)$", block)
        result_text = result_match.group(1).strip() if result_match else ""
        json_start = result_text.find("{")
        data: Dict[str, Any] = {}
        parse_error = ""
        if json_start < 0:
            parse_error = "Result did not contain JSON"
        else:
            try:
                parsed, _end = decoder.raw_decode(result_text[json_start:])
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    parse_error = f"Result JSON was {type(parsed).__name__}, not object"
            except json.JSONDecodeError as exc:
                parse_error = f"Could not parse JSON result: {exc.msg}"

        status = str(data.get("status", "")).strip()
        status_led = str(data.get("status-led", "")).strip()
        issues = data.get("issues", {})
        healthy = not parse_error and status.lower() == "ok" and issues in ({}, None, "", [])
        results.append(
            {
                "device": device,
                "healthy": healthy,
                "status": status or "-",
                "status_led": status_led or "-",
                "issues": summarize_system_health_issues(issues),
                "parse_error": parse_error,
            }
        )
    return results


def system_health_result_line(result: Dict[str, Any]) -> str:
    details = [
        f"status={result.get('status') or '-'}",
        f"status-led={result.get('status_led') or '-'}",
        f"issues={result.get('issues') or '-'}",
    ]
    if result.get("parse_error"):
        details.append(f"parse_error={result['parse_error']}")
    return f"{result.get('device')}: {'; '.join(details)}"


def build_system_health_output(
    rack_pairs: Sequence[str],
    update_command: str,
    update_output: str,
    run_output: str,
    results: Sequence[Dict[str, Any]],
) -> str:
    healthy = [system_health_result_line(result) for result in results if result.get("healthy")]
    unhealthy = [system_health_result_line(result) for result in results if not result.get("healthy")]
    lines = [
        f"Racks checked: {', '.join(rack_pairs) if rack_pairs else '-'}",
        f"Device-list command: {update_command}",
    ]
    if update_output.strip():
        lines.extend(["Device-list output:", update_output.strip()])
    lines.extend(
        [
            f"Run command: {SYSTEM_HEALTH_RUN_COMMAND}",
            f"Parsed system health results: {len(results)}",
            "",
            "Healthy devices:",
            *sorted(healthy),
            "",
            "Devices requiring review:",
            *sorted(unhealthy),
            "",
            "Raw run-command output:",
            run_output.strip(),
        ]
    )
    return "\n".join(lines)


def run_system_health_status(
    args: argparse.Namespace,
    ncp_region: str,
    rack_region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> SystemHealthStatus:
    if args.skip_system_health:
        return SystemHealthStatus("", True, "Skipped by --skip-system-health", "")
    if not racks and hosts is None:
        return SystemHealthStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    if hosts is not None:
        if not hosts:
            return SystemHealthStatus("", False, "No devices found in --device-file", "")
        rack_pairs: List[str] = []
        update_command = (
            f"update-device-list --device-names-matching {ncpcli_quoted_device_list(hosts)} "
            f"--devices-by-role {args.system_health_roles}"
        )
    else:
        rack_pairs = rack_pairs_from_racks(rack_region, racks)
        update_command = (
            f"update-device-list --rack {','.join(rack_pairs)} "
            f"--devices-by-role {args.system_health_roles}"
        )

    interactive_cmd = ncpcli_interactive_command(args.ncpcli_command, ncp_region, args.connection_methods)
    timeout = max(args.timeout, args.system_health_timeout)
    command = f"{shell_join(interactive_cmd)}; {update_command}; {SYSTEM_HEALTH_RUN_COMMAND}"
    progress_total = scoped_switch_progress_total(racks, hosts)
    progress = ProgressReporter("System health", progress_total, enabled=progress_enabled(args))
    outputs = run_ncpcli_interactive_commands(
        interactive_cmd,
        [update_command, SYSTEM_HEALTH_RUN_COMMAND],
        timeout=timeout,
    )
    update_output = outputs.get(update_command, "")
    run_output = outputs.get(SYSTEM_HEALTH_RUN_COMMAND, "")
    results = parse_system_health_results(run_output)
    output = build_system_health_output(rack_pairs, update_command, update_output, run_output, results)

    if not results:
        progress.update(progress_total, failed=progress_total)
        return SystemHealthStatus(command, False, "No system health JSON results were returned", output)

    unhealthy = [result for result in results if not result.get("healthy")]
    healthy_count = len(results) - len(unhealthy)
    progress.update(
        progress_total,
        ok=min(healthy_count, progress_total),
        flagged=max(0, progress_total - min(healthy_count, progress_total)),
    )
    if unhealthy:
        return SystemHealthStatus(
            command,
            False,
            f"{len(unhealthy)} of {len(results)} device(s) reported unhealthy system health",
            output,
        )
    return SystemHealthStatus(
        command,
        True,
        f"All {len(results)} selected device(s) report system health OK",
        output,
    )


PROMQL_REGEX_SPECIAL_RE = re.compile(r"([\\.^$|?*+()[\]{}])")


def promql_regex_escape(value: str) -> str:
    return PROMQL_REGEX_SPECIAL_RE.sub(r"\\\1", value)


def promql_string_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def promql_regex_from_values(values: Sequence[str]) -> str:
    return "|".join(promql_regex_escape(value) for value in ordered_unique(values))


def prometheus_results_from_obj(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, dict):
        if isinstance(obj.get("metric"), dict):
            return [obj]
        if isinstance(obj.get("data"), dict):
            return prometheus_results_from_obj(obj["data"])
        if isinstance(obj.get("result"), list):
            return obj["result"]
        rows: List[Dict[str, Any]] = []
        for value in obj.values():
            rows.extend(prometheus_results_from_obj(value))
        return rows
    if isinstance(obj, list):
        rows = []
        for value in obj:
            rows.extend(prometheus_results_from_obj(value))
        return rows
    return []


def parse_prometheus_output(text: str) -> List[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    try:
        return prometheus_results_from_obj(json.loads(text))
    except Exception:
        pass
    try:
        return prometheus_results_from_obj(ast.literal_eval(text))
    except Exception:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return prometheus_results_from_obj(ast.literal_eval(line))
        except Exception:
            continue

    rows: List[Dict[str, Any]] = []
    label_re = re.compile(r"\{([^{}]+)\}")
    for line in text.splitlines():
        match = label_re.search(line)
        if not match:
            continue
        labels = {}
        for key, value in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"', match.group(1)):
            labels[key] = value
        number_match = re.search(r"(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*$", line, re.IGNORECASE)
        value = number_match.group(1) if number_match else ""
        rows.append({"metric": labels, "value": [None, value]})
    return rows


def prometheus_row_metric(row: Dict[str, Any]) -> Dict[str, str]:
    metric = row.get("metric")
    if not isinstance(metric, dict):
        return {}
    return {str(key): str(value) for key, value in metric.items()}


def prometheus_row_value(row: Dict[str, Any]) -> Optional[float]:
    value = row.get("value")
    raw: Any = None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        raw = value[1]
    elif isinstance(value, (str, int, float)):
        raw = value
    elif isinstance(row.get("values"), list) and row["values"]:
        last_value = row["values"][-1]
        if isinstance(last_value, (list, tuple)) and len(last_value) >= 2:
            raw = last_value[1]
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def format_celsius(value: float) -> str:
    return f"{value:g}C"


def optics_temperature_query(device_hosts: Sequence[str], threshold_c: float) -> str:
    device_regex = promql_string_escape(promql_regex_from_values(device_hosts))
    threshold = f"{threshold_c:g}"
    return (
        'max('
        f'componentSensorValue{{job="streaming_telemetry_collector",device=~"{device_regex}",'
        f'metric="componentTemperature",sensor_name=~"transceiver.*"}} > {threshold}'
        ') by (device, sensor_name)'
    )


def run_optics_temperature_status(
    args: argparse.Namespace,
    region: str,
    racks: Sequence[str],
    hosts: Optional[Sequence[str]] = None,
) -> OpticsTemperatureStatus:
    if args.skip_optics_temperature:
        return OpticsTemperatureStatus("", True, "Skipped by --skip-optics-temperature", "")
    if not racks and hosts is None:
        return OpticsTemperatureStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")

    if hosts is not None:
        device_hosts = list(hosts)
    else:
        link_module = load_link_flap_module(args.link_flap_script)
        device_hosts = expand_rack_hosts_from_topology(
            region,
            racks,
            args.racktopo_script,
            link_module,
            args.ncpcli_command,
            args.racktopo_workers,
        )
    device_hosts = ordered_unique(device_hosts)
    if not device_hosts:
        scope = "selected racks" if racks else "selected devices"
        return OpticsTemperatureStatus("", False, f"No devices found from {scope}", "")

    ncp_region = infer_region(region, None)
    threshold = float(args.optics_temperature_threshold_c)
    query = optics_temperature_query(device_hosts, threshold)
    command = command_prefix(args.ncpcli_command, ncp_region, args.connection_methods) + [
        "prometheus",
        "results-from-query",
        "--query",
        query,
        "--full-output",
    ]
    result = run_command(command, args.optics_temperature_timeout)
    command_text = shell_join(command)
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        return OpticsTemperatureStatus(
            command_text,
            False,
            "Optics temperature Prometheus query failed",
            output,
        )

    rows = parse_prometheus_output(result.stdout)
    violations = []
    for row in rows:
        metric = prometheus_row_metric(row)
        device = metric.get("device", "")
        if not device:
            continue
        value = prometheus_row_value(row)
        if value is None or value <= threshold:
            continue
        sensor = metric.get("sensor_name") or metric.get("name") or metric.get("entPhysicalDescr") or "-"
        violations.append((device, sensor, value))

    violating_devices = ordered_unique(device for device, _sensor, _value in violations)
    violating_device_set = set(violating_devices)
    ok_devices = [host for host in device_hosts if host not in violating_device_set]
    threshold_text = format_celsius(threshold)
    output_lines = [
        f"Threshold: {threshold_text}",
        f"Prometheus query: {query}",
        "",
        "OK devices:",
    ]
    output_lines.extend(
        f"{host}: no optics temperature above {threshold_text} returned by Prometheus"
        for host in ok_devices
    )
    output_lines.extend(["", "Devices with high optics temperature:"])
    output_lines.extend(
        f"{device}: sensor={sensor} temp={format_celsius(value)} threshold={threshold_text}"
        for device, sensor, value in sorted(violations, key=lambda item: (item[0], item[1], item[2]))
    )
    output_lines.extend(["", "Failures:"])
    output = "\n".join(output_lines)

    if violations:
        return OpticsTemperatureStatus(
            command_text,
            False,
            (
                f"Found {len(violations)} optics temperature reading(s) above {threshold_text} "
                f"on {len(violating_devices)} device(s)"
            ),
            output,
        )

    return OpticsTemperatureStatus(
        command_text,
        True,
        f"No optics temperature readings above {threshold_text} across {len(device_hosts)} checked device(s)",
        output,
    )


def run_command(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), text=True, capture_output=True, timeout=timeout)


def clean_image_line(line: str) -> str:
    line = line.strip()
    if line.startswith("- "):
        line = line[2:].strip()
    return line


def parse_images(output: str, include_duplicates: bool) -> List[ImageEntry]:
    raw_images: List[str] = []
    for raw in output.splitlines():
        line = clean_image_line(raw)
        lowered = line.lower()
        if not line:
            continue
        if lowered.startswith("session:") or lowered.startswith("version:"):
            continue
        if lowered.startswith("installed version") or lowered.startswith("upgrade with"):
            continue
        if lowered.startswith("fetching available") or lowered.startswith("finished fetching"):
            continue
        if lowered.startswith("ztp is able"):
            continue
        if "." not in line:
            continue
        raw_images.append(line)

    if not include_duplicates:
        raw_images = ordered_unique(raw_images)

    entries: List[ImageEntry] = []
    for raw in raw_images:
        vendor, image = raw.split(".", 1) if "." in raw else ("unknown", raw)
        entries.append(ImageEntry(vendor=vendor, image=image, raw=raw))
    return entries


def filter_images(images: List[ImageEntry], vendor: Optional[str], contains: Optional[str]) -> List[ImageEntry]:
    filtered = images
    if vendor:
        vendor_key = vendor.lower()
        filtered = [entry for entry in filtered if entry.vendor.lower() == vendor_key]
    if contains:
        needle = contains.lower()
        filtered = [entry for entry in filtered if needle in entry.raw.lower()]
    return filtered


def release_from_ztp_image(image_name: str) -> str:
    match = re.search(r"cumulus-linux-([0-9][A-Za-z0-9_.-]*)-mlx", image_name)
    if match:
        return match.group(1)
    match = re.search(r"(\d+(?:\.\d+){1,3})", image_name)
    return match.group(1) if match else ""


def parse_product_release_by_device(output: str) -> Dict[str, str]:
    releases: Dict[str, str] = {}
    for block in re.split(r"\*{8,}", ANSI_RE.sub("", output or "")):
        entity_match = re.search(r"(?m)^\s*Entity:\s*(\S+)\s*$", block)
        if not entity_match:
            continue
        release_match = re.search(r"(?m)^\s*product-release\s+(\S+)\s*$", block)
        if release_match:
            releases[entity_match.group(1)] = release_match.group(1)
    return releases


def ztp_device_status_from_output(
    command_text: str,
    output: str,
    required_release: str,
    device_hosts: Sequence[str],
) -> ZtpDeviceStatus:
    releases = parse_product_release_by_device(output)
    expected_hosts = ordered_unique(device_hosts)
    ok: List[str] = []
    bad: List[str] = []

    for host in expected_hosts:
        release = releases.get(host)
        if release == required_release:
            ok.append(f"{host}: product-release {release}")
        elif release:
            bad.append(f"{host}: product-release {release} (expected {required_release})")
        else:
            bad.append(f"{host}: product-release missing from ncpcli output")

    evidence = "\n".join(
        ["OK devices:"]
        + sorted(ok)
        + ["", "Devices with mismatched/missing product-release:"]
        + sorted(bad)
    )
    if bad:
        return ZtpDeviceStatus(
            command_text,
            False,
            f"{len(bad)} of {len(expected_hosts)} device(s) are not confirmed on product-release {required_release}",
            required_release,
            len(expected_hosts),
            evidence,
        )
    return ZtpDeviceStatus(
        command_text,
        True,
        f"All {len(ok)} selected device(s) report product-release {required_release}",
        required_release,
        len(expected_hosts),
        evidence,
    )


def run_ztp_device_status(
    args: argparse.Namespace,
    region: str,
    required_image: str,
    device_hosts: Sequence[str],
) -> ZtpDeviceStatus:
    required_release = release_from_ztp_image(required_image)
    if not device_hosts:
        return ZtpDeviceStatus(
            "",
            True,
            "Skipped device product-release check because no racks or --device-file were provided",
            required_release,
            0,
            "",
        )
    if not required_release:
        return ZtpDeviceStatus(
            "",
            False,
            f"Could not derive required product-release from {required_image}",
            "",
            len(device_hosts),
            "",
        )

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            tmp_path = handle.name
            for host in ordered_unique(device_hosts):
                handle.write(f"{host}\n")

        command = (
            command_prefix(args.ncpcli_command, region, args.connection_methods)
            + [
                "devices",
                "run-command",
                "--devices-from-file",
                tmp_path,
                "nv show system | grep product-release",
            ]
        )
        result = run_command(command, args.timeout)
        command_text = shell_join(command).replace(shlex.quote(tmp_path), "<selected-devices-file>")
        output = "\n".join(part for part in [(result.stdout or "").strip(), (result.stderr or "").strip()] if part)
        if result.returncode != 0:
            return ZtpDeviceStatus(
                command_text,
                False,
                f"product-release check failed with exit code {result.returncode}",
                required_release,
                len(ordered_unique(device_hosts)),
                output,
            )
        return ztp_device_status_from_output(command_text, result.stdout or "", required_release, device_hosts)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def parse_dan_status(output: str, region: str, command: Sequence[str]) -> DanStatus:
    fields: Dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            fields[key] = value

    release_status = fields.get("Release_Status", "")
    regions_text = fields.get("Staging_Schema_Regions", "")
    staging_regions: List[str] = []
    if regions_text:
        try:
            parsed = ast.literal_eval(regions_text)
            if isinstance(parsed, list):
                staging_regions = [str(item).lower() for item in parsed]
        except Exception:
            staging_regions = [item.strip().strip("'\"").lower() for item in regions_text.strip("[]").split(",") if item.strip()]

    if release_status != "RELEASE":
        return DanStatus(shell_join(command), False, f"Release_Status is {release_status or 'missing'}", fields)
    if region.lower() not in staging_regions:
        return DanStatus(shell_join(command), False, f"{region} not present in Staging_Schema_Regions", fields)
    return DanStatus(shell_join(command), True, "Release_Status is RELEASE and region is staged", fields)


def parse_pretty_table_rows(output: str) -> List[Dict[str, str]]:
    headers: List[str] = []
    rows: List[Dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        lowered = [cell.lower() for cell in cells]
        if "device" in lowered and "pki verified" in lowered:
            headers = cells
            continue
        if not headers or len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def parse_certificate_status(output: str, command: Sequence[str]) -> CertificateStatus:
    cleaned = output.strip()
    if not cleaned:
        return CertificateStatus(shell_join(command), False, "Command succeeded but returned no output", cleaned)

    lowered = cleaned.lower()
    failure_tokens = [
        "not found",
        "no certificate",
        "no secret",
        "missing",
        "error",
        "failed",
    ]
    for token in failure_tokens:
        if token in lowered:
            return CertificateStatus(shell_join(command), False, f"Output contains '{token}'", cleaned)
    if "no filters were provided" in lowered:
        return CertificateStatus(shell_join(command), False, "No device/rack filter was provided", cleaned)

    certificate_rows = parse_pretty_table_rows(cleaned)
    if not certificate_rows:
        return CertificateStatus(shell_join(command), False, "No certificate rows with PKI Verified status were found", cleaned)

    failed_devices = []
    for row in certificate_rows:
        pki_verified = row.get("PKI Verified", "").strip().lower()
        if pki_verified != "true":
            failed_devices.append(row.get("Device", "<unknown>"))

    if failed_devices:
        devices = ", ".join(failed_devices)
        return CertificateStatus(
            shell_join(command),
            False,
            f"PKI Verified is not true for {len(failed_devices)} device(s): {devices}",
            cleaned,
        )

    return CertificateStatus(
        shell_join(command),
        True,
        f"PKI Verified is true for all {len(certificate_rows)} device(s)",
        cleaned,
    )


def parse_static_mac_status(result: subprocess.CompletedProcess[str], command: Sequence[str]) -> StaticMacStatus:
    output = "\n".join(part for part in [(result.stdout or "").strip(), (result.stderr or "").strip()] if part)
    if result.returncode == 0:
        return StaticMacStatus(shell_join(command), True, "Static MAC verification passed", output)
    return StaticMacStatus(shell_join(command), False, f"Static MAC verification failed with exit code {result.returncode}", output)


def wrap_cell(value: str, width: int) -> List[str]:
    wrapped: List[str] = []
    for part in str(value).splitlines() or [""]:
        wrapped.extend(
            textwrap.wrap(
                part,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
        )
    return wrapped


def print_box_table(headers: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[int]) -> None:
    top = "╭" + "┬".join("─" * (width + 2) for width in widths) + "╮"
    middle = "├" + "┼".join("─" * (width + 2) for width in widths) + "┤"
    bottom = "╰" + "┴".join("─" * (width + 2) for width in widths) + "╯"

    def print_row(values: Sequence[str], header: bool = False) -> None:
        wrapped_cells = [wrap_cell(value, widths[index]) for index, value in enumerate(values)]
        height = max(len(cell) for cell in wrapped_cells)
        for line_index in range(height):
            rendered = []
            for column_index, cell_lines in enumerate(wrapped_cells):
                text = cell_lines[line_index] if line_index < len(cell_lines) else ""
                should_center = str(headers[column_index]).strip().lower() == "status"
                if should_center and not header:
                    rendered.append(f" {text.center(widths[column_index])} ")
                else:
                    rendered.append(f" {text.ljust(widths[column_index])} ")
            print("│" + "│".join(rendered) + "│")

    print(top)
    print_row(headers, header=True)
    print(middle)
    for index, row in enumerate(rows):
        print_row(row)
        if index != len(rows) - 1:
            print(middle)
    print(bottom)


def print_section(title: str) -> None:
    print("")
    print("╔" + "═" * 88 + "╗")
    print("║ " + title.ljust(86) + " ║")
    print("╚" + "═" * 88 + "╝")


def yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def print_status_card(command: str, verified: bool, reason: str) -> None:
    print_box_table(
        ("Field", "Value"),
        [
            ("Command", command or "Skipped"),
            ("Verified", yes_no(verified)),
            ("Reason", reason),
        ],
        widths=(18, 88),
    )


def print_key_value_table(title: str, rows: Sequence[tuple], value_width: int = 88) -> None:
    if not rows:
        return
    print("")
    print(title)
    print_box_table(("Item", "Value"), [(str(key), str(value)) for key, value in rows], widths=(24, value_width))


def parse_static_mac_summary(output: str) -> List[tuple]:
    wanted = {
        "Checked",
        "OK",
        "Unexpected mac-address output",
        "SSH/command failures",
    }
    rows: List[tuple] = []
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key in wanted:
            rows.append((key, value))
    return rows


def parse_link_flap_rows(output: str) -> List[tuple]:
    rows: List[tuple] = []
    current = ""
    evidence_sections = {
        "OK devices",
        "Devices with disabled config",
        "Devices with gNMI issue",
        "Devices with mismatched/missing product-release",
        "Devices without expected mac-address output",
        "Devices not in-service",
        "Devices with high optics temperature",
        "Devices requiring review",
        "Healthy devices",
        "Failures",
    }
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {
            "OK devices:",
            "Devices with disabled config:",
            "Devices with gNMI issue:",
            "Devices with mismatched/missing product-release:",
            "Devices without expected mac-address output:",
            "Devices not in-service:",
            "Devices with high optics temperature:",
            "Devices requiring review:",
            "Healthy devices:",
            "Failures:",
        }:
            current = line.rstrip(":")
            continue
        if line in {
            "Device-list output:",
            "Raw compare-config output:",
            "Raw current-devices output:",
            "Raw run-command output:",
            "Prometheus query:",
        }:
            current = ""
            continue
        if current not in evidence_sections:
            continue
        if ": " not in line:
            continue
        device, detail = line.split(": ", 1)
        if current == "OK devices":
            status = "OK"
        elif current == "Devices with disabled config":
            status = "DISABLED"
        elif current == "Devices with gNMI issue":
            status = "FAIL"
        elif current == "Devices with mismatched/missing product-release":
            status = "MISMATCH"
        elif current == "Devices without expected mac-address output":
            status = "FAIL"
        elif current == "Devices not in-service":
            status = "NOT_IN_SERVICE"
        elif current == "Devices with high optics temperature":
            status = "HIGH_TEMP"
        elif current == "Devices requiring review":
            status = "REVIEW"
        elif current == "Healthy devices":
            status = "OK"
        elif current == "Failures":
            status = "FAIL"
        else:
            status = "INFO"
        rows.append((device, status, detail))
    return rows


def parse_system_health_summary(output: str) -> tuple[List[tuple], List[tuple]]:
    parsed_total: Optional[int] = None
    healthy_rows: List[tuple] = []
    review_rows: List[tuple] = []
    current = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed_match = re.match(r"^Parsed system health results:\s*(\d+)\s*$", line, flags=re.IGNORECASE)
        if parsed_match:
            parsed_total = int(parsed_match.group(1))
            continue
        if line in {"Healthy devices:", "Devices requiring review:"}:
            current = line.rstrip(":")
            continue
        if line in {"Device-list output:", "Raw run-command output:"}:
            current = ""
            continue
        if current not in {"Healthy devices", "Devices requiring review"}:
            continue
        if ": " not in line:
            continue
        device, detail = line.split(": ", 1)
        if current == "Healthy devices":
            healthy_rows.append((device, "OK", detail))
        else:
            review_rows.append((device, "REVIEW", detail))

    checked = parsed_total if parsed_total is not None else len(healthy_rows) + len(review_rows)
    summary_rows = [
        ("Checked devices", str(checked)),
        ("Healthy", str(len(healthy_rows))),
        ("Requires review", str(len(review_rows))),
    ]
    return summary_rows, review_rows


def parse_hostname_validation_result_rows(output: str) -> List[tuple]:
    rows: List[tuple] = []
    headers: List[str] = []
    for raw_line in clean_terminal_output(output).splitlines():
        cells = split_pipe_table_row(raw_line)
        if not cells:
            continue
        lowered = [cell.strip().lower() for cell in cells]
        if "hostname from cutsheet" in lowered and "result" in lowered:
            headers = cells
            continue
        if not headers or len(cells) != len(headers):
            continue
        record = {header.strip().lower(): value.strip() for header, value in zip(headers, cells)}
        result = record.get("result", "")
        if not result or result.upper() == "PASS":
            continue
        device = (
            record.get("hostname from cutsheet")
            or record.get("hostname collected from device")
            or record.get("serial number")
            or "Hostname validation"
        )
        evidence_parts = [f"result={result}"]
        error = record.get("error", "")
        if error:
            evidence_parts.append(f"error={error}")
        collected = record.get("hostname collected from device", "")
        if collected and collected != device:
            evidence_parts.append(f"collected={collected}")
        serial = record.get("serial number", "")
        if serial:
            evidence_parts.append(f"serial={serial}")
        rows.append((device, "REVIEW", "; ".join(evidence_parts)))
    return rows


def parse_hostname_validation_summary(status: HostnameValidationStatus) -> tuple[List[tuple], List[tuple]]:
    clean_output = clean_terminal_output(status.output)
    if not clean_output:
        return [], []

    checked_match = re.search(
        r"^Devices passed to hostname validation:\s*(\d+)\s*$",
        clean_output,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    resolved_match = re.search(
        r"^Parsed qfabt0 devices:\s*(\d+)\s*$",
        clean_output,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    racks_match = re.search(
        r"^Racks checked:\s*(.+?)\s*$",
        clean_output,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    output_match = re.search(
        r"\bOutput saved to\s+(\S+)",
        clean_output,
        flags=re.IGNORECASE,
    )
    marker_reasons = ordered_unique(
        reason for pattern, reason in HOSTNAME_VALIDATION_FAILURE_PATTERNS if pattern.search(clean_output)
    )
    result = "PASS" if status.verified else "REVIEW"

    summary_rows: List[tuple] = [
        ("Checked devices", checked_match.group(1) if checked_match else "-"),
        ("Result", result),
    ]
    if resolved_match:
        summary_rows.insert(1, ("Resolved qfabt0 devices", resolved_match.group(1)))
    if racks_match:
        summary_rows.insert(1, ("Racks", racks_match.group(1)))
    if output_match:
        summary_rows.append(("Output CSV", output_match.group(1)))
    if marker_reasons:
        summary_rows.append(("Failure markers", str(len(marker_reasons))))

    issue_rows = parse_hostname_validation_result_rows(clean_output)
    if marker_reasons:
        marker_detail = "; ".join(marker_reasons)
        if issue_rows:
            issue_rows.append(("Failure markers", "REVIEW", marker_detail))
        else:
            issue_rows = [("Hostname validation", "REVIEW", marker_detail)]
    elif not status.verified:
        issue_rows = [("Hostname validation", "REVIEW", status.reason or "Validation did not pass")]

    return summary_rows, issue_rows


def ztp_verified(report: Report) -> bool:
    if ztp_skipped(report):
        return True
    return report.ztp_required_image_present and report.ztp_device_status.verified


def ztp_skipped(report: Report) -> bool:
    return report.ztp_command == "Skipped"


def ztp_summary_reason(report: Report) -> str:
    if ztp_skipped(report):
        return "Skipped by --skip-ztp"
    image_list = ", ".join(entry.raw for entry in report.images) if report.images else "No matching images returned"
    required_state = "FOUND" if report.ztp_required_image_present else "MISSING"
    return (
        f"Required gold image: {report.ztp_required_image} ({required_state}). "
        f"Served images: {report.returned_images} matching / {report.total_images} total."
    )


def ztp_device_summary_reason(report: Report) -> str:
    if ztp_skipped(report):
        return "Skipped by --skip-ztp"
    if report.ztp_device_status.checked_devices:
        return report.ztp_device_status.reason
    return report.ztp_device_status.reason or "Skipped device product-release check"


def print_summary_table(report: Report) -> None:
    vendor = report.filters.get("vendor") or "*"
    rows = [
        (
            f"ZTP served image ({vendor})",
            "SKIPPED" if ztp_skipped(report) else ("PASS" if report.ztp_required_image_present else "FAIL"),
            ztp_summary_reason(report),
        ),
        (
            "Device product-release",
            check_state(report.ztp_device_status.verified, ztp_device_summary_reason(report)),
            ztp_device_summary_reason(report),
        ),
        ("DAN status", check_state(report.dan.verified, report.dan.reason), report.dan.reason),
        ("Certificate / secret-key", check_state(report.certificate.verified, report.certificate.reason), report.certificate.reason),
        ("Hostname validation", check_state(report.hostname_validation.verified, report.hostname_validation.reason), report.hostname_validation.reason),
        ("Mgmt/TS in-service", check_state(report.mgmt_ts.verified, report.mgmt_ts.reason), report.mgmt_ts.reason),
        ("Static MAC", check_state(report.static_mac.verified, report.static_mac.reason), report.static_mac.reason),
        ("Link flap protection", check_state(report.link_flap.verified, report.link_flap.reason), report.link_flap.reason),
        ("Config diff", check_state(report.config_diff.verified, report.config_diff.reason), report.config_diff.reason),
        ("LLDP", check_state(report.lldp.verified, report.lldp.reason), report.lldp.reason),
        ("gNMI", check_state(report.gnmi.verified, report.gnmi.reason), report.gnmi.reason),
        ("System Health", check_state(report.system_health.verified, report.system_health.reason), report.system_health.reason),
        (
            "Optics temperature",
            check_state(report.optics_temperature.verified, report.optics_temperature.reason),
            report.optics_temperature.reason,
        ),
    ]
    headers = ("Check", "Status", "Result / Evidence")
    summary_width = 120
    print("")
    print("Final summary".center(summary_width))
    print("=" * summary_width)
    print_box_table(headers, rows, widths=(26, 8, 78))


def print_report(report: Report, json_output: bool) -> None:
    if json_output:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
        return

    vendor = report.filters.get("vendor")
    contains = report.filters.get("contains")

    print_section("ZTP Served Images")
    ztp_reason = ztp_summary_reason(report) if ztp_skipped(report) else f"Required gold image {'FOUND' if report.ztp_required_image_present else 'MISSING'}"
    print_status_card(
        report.ztp_command,
        ztp_verified(report),
        ztp_reason,
    )
    if not ztp_skipped(report):
        print_key_value_table(
            "ZTP details",
            [
                ("Target", report.target),
                ("Region", report.region),
                ("Total images", report.total_images),
                ("Returned images", report.returned_images),
                ("Required gold image", report.ztp_required_image),
                ("Filters", f"vendor={vendor or '*'} contains={contains or '*'}"),
            ],
        )
        image_rows = [(str(index), entry.raw) for index, entry in enumerate(report.images, start=1)]
        print_key_value_table("Matching Cumulus images", image_rows or [("-", "No matching images returned")])
        if report.ztp_device_status.command or report.ztp_device_status.checked_devices:
            print("")
            print("ZTP device product-release check")
            print_status_card(
                report.ztp_device_status.command,
                report.ztp_device_status.verified,
                report.ztp_device_status.reason,
            )
            ztp_device_rows = parse_link_flap_rows(report.ztp_device_status.output)
            if ztp_device_rows:
                print("")
                print("ZTP device evidence")
                print_box_table(("Device", "Status", "Evidence"), ztp_device_rows, widths=(26, 10, 70))
            elif report.ztp_device_status.output:
                print_key_value_table("ZTP device output", [("Raw output", report.ztp_device_status.output)])

    print_section("DAN Status")
    print_status_card(report.dan.command, report.dan.verified, report.dan.reason)
    dan_rows = []
    for key in [
        "Id",
        "Autonet_Version",
        "Acr_Version",
        "Nemo_Min_Version",
        "Base_Schema_Version",
        "Staging_Schema_Version",
        "Staging_Schema_Regions",
        "Release_Status",
        "Created_Time",
    ]:
        if key in report.dan.fields:
            dan_rows.append((key, report.dan.fields[key]))
    print_key_value_table("DAN fields", dan_rows)

    print_section("Certificate / Secret-Key Status")
    print_status_card(report.certificate.command, report.certificate.verified, report.certificate.reason)
    certificate_rows = parse_pretty_table_rows(report.certificate.output)
    if certificate_rows:
        rendered_rows = [
            (
                row.get("Device", ""),
                row.get("Platform", ""),
                row.get("Issuer", ""),
                row.get("Expires", ""),
                row.get("PKI Verified", ""),
            )
            for row in certificate_rows
        ]
        print("")
        print("Certificate evidence")
        print_box_table(
            ("Device", "Platform", "Issuer", "Expires", "PKI"),
            rendered_rows,
            widths=(22, 24, 36, 12, 8),
        )
    elif report.certificate.output:
        print_key_value_table("Certificate output", [("Raw output", report.certificate.output)])

    print_section("Hostname Validation")
    print_status_card(
        report.hostname_validation.command,
        report.hostname_validation.verified,
        report.hostname_validation.reason,
    )
    if report.hostname_validation.output:
        hostname_summary, hostname_issue_rows = parse_hostname_validation_summary(report.hostname_validation)
        print_key_value_table("Hostname validation summary", hostname_summary)
        if hostname_issue_rows:
            print("")
            print("Hostname validation issues")
            print_box_table(("Device", "Status", "Evidence"), hostname_issue_rows, widths=(26, 10, 70))

    print_section("Management / TS Switch State")
    print_status_card(report.mgmt_ts.command, report.mgmt_ts.verified, report.mgmt_ts.reason)
    mgmt_ts_rows = parse_link_flap_rows(report.mgmt_ts.output)
    if mgmt_ts_rows:
        print("")
        print("Management / TS device evidence")
        print_box_table(("Device", "Status", "Evidence"), mgmt_ts_rows, widths=(26, 16, 64))
    elif report.mgmt_ts.output:
        print_key_value_table("Management / TS output", [("Raw output", report.mgmt_ts.output)])

    print_section("Static MAC Status")
    print_status_card(report.static_mac.command, report.static_mac.verified, report.static_mac.reason)
    static_rows = parse_static_mac_summary(report.static_mac.output)
    print_key_value_table("Static MAC counters", static_rows or [("Summary", "No summary counters found")])
    static_device_rows = parse_link_flap_rows(report.static_mac.output)
    if static_device_rows:
        print("")
        print("Static MAC device evidence")
        print_box_table(("Device", "Status", "Evidence"), static_device_rows, widths=(26, 10, 70))

    print_section("Link Flap Protection Status")
    print_status_card(report.link_flap.command, report.link_flap.verified, report.link_flap.reason)
    link_rows = parse_link_flap_rows(report.link_flap.output)
    if link_rows:
        print("")
        print("Link flap device evidence")
        print_box_table(("Device", "Status", "Evidence"), link_rows, widths=(22, 10, 74))
    elif report.link_flap.output:
        print_key_value_table("Link flap output", [("Raw output", report.link_flap.output)])

    print_section("Config-Diff Status")
    print_status_card(report.config_diff.command, report.config_diff.verified, report.config_diff.reason)
    config_diff_rows = parse_link_flap_rows(report.config_diff.output)
    if config_diff_rows:
        print("")
        print("Config-diff device evidence")
        print_box_table(("Device", "Status", "Evidence"), config_diff_rows, widths=(22, 10, 74))
    elif report.config_diff.output:
        print_key_value_table("Config-diff output", [("Raw output", report.config_diff.output)])

    print_section("LLDP Status")
    print_status_card(report.lldp.command, report.lldp.verified, report.lldp.reason)
    lldp_rows = parse_link_flap_rows(report.lldp.output)
    if lldp_rows:
        print("")
        print("LLDP device evidence")
        print_box_table(("Device", "Status", "Evidence"), lldp_rows, widths=(22, 10, 74))
    elif report.lldp.output:
        print_key_value_table("LLDP output", [("Raw output", report.lldp.output)])

    print_section("gNMI Status")
    print_status_card(report.gnmi.command, report.gnmi.verified, report.gnmi.reason)
    gnmi_rows = parse_link_flap_rows(report.gnmi.output)
    if gnmi_rows:
        print("")
        print("gNMI device evidence")
        print_box_table(("Device", "Status", "Evidence"), gnmi_rows, widths=(22, 10, 74))
    elif report.gnmi.output:
        print_key_value_table("gNMI output", [("Raw output", report.gnmi.output)])

    print_section("System Health")
    print_status_card(
        report.system_health.command,
        report.system_health.verified,
        report.system_health.reason,
    )
    system_health_summary, system_health_review_rows = parse_system_health_summary(report.system_health.output)
    if report.system_health.output:
        print_key_value_table("System health summary", system_health_summary)
    if system_health_review_rows:
        print("")
        print("Devices requiring review")
        print_box_table(("Device", "Status", "Evidence"), system_health_review_rows, widths=(22, 10, 74))

    print_section("Optics Temperature")
    print_status_card(
        report.optics_temperature.command,
        report.optics_temperature.verified,
        report.optics_temperature.reason,
    )
    optics_temperature_rows = parse_link_flap_rows(report.optics_temperature.output)
    if optics_temperature_rows:
        print("")
        print("Optics temperature evidence")
        print_box_table(("Device", "Status", "Evidence"), optics_temperature_rows, widths=(22, 12, 72))
    elif report.optics_temperature.output:
        print_key_value_table("Optics temperature output", [("Raw output", report.optics_temperature.output)])

    print_summary_table(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run multiplanar site pre-checks: ZTP image, DAN/autonet runtime, certificate, hostname validation, static MAC, link flap protection, config-diff, LLDP, gNMI, system health, and optics temperature.")
    parser.add_argument("target", nargs="?", help="Target site/region token, e.g. jbp15, aga5, iad65, or jbp.")
    parser.add_argument(
        "--region",
        "-r",
        "--site",
        dest="region",
        help="Region/site override. Defaults to leading letters from target.",
    )
    parser.add_argument("--vendor", default="cumulus", help="Only show one vendor prefix, e.g. cumulus, eos, junos. Default: cumulus.")
    parser.add_argument("--contains", help="Only show image entries containing this token, e.g. 5.16.")
    parser.add_argument("--required-ztp-image", default=DEFAULT_REQUIRED_ZTP_IMAGE,
                        help=f"Exact ZTP image required for PASS. Default: {DEFAULT_REQUIRED_ZTP_IMAGE}")
    parser.add_argument("--include-duplicates", action="store_true", help="Keep duplicate entries from ncpcli output.")
    parser.add_argument("--ncpcli-command", default=os.environ.get("NCPCLI_COMMAND", "ncpcli"), help="ncpcli executable/wrapper command.")
    parser.add_argument("--qcli-command", default=os.environ.get("QCLI_COMMAND", "qcli"), help="qcli executable/wrapper command for hostname validation.")
    parser.add_argument("--ssh-domain", help="Optional SSH domain appended to default usernames, e.g. corp.example.com makes user@corp.example.com.")
    parser.add_argument("--ssh-host-suffix", help="Optional DNS suffix appended to short device names for SSH, e.g. example.com uses host.example.com.")
    parser.add_argument("--connection-methods", help="Optional ncpcli connection methods, e.g. tunnel,proxy,direct.")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds per command. Default: 180.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--no-progress", action="store_true", help="Disable stderr progress output for per-device checks.")
    parser.add_argument("--ztp", action="store_true", help="Run only the ZTP/gold image check unless other positive check flags are also provided.")
    parser.add_argument("--dan", action="store_true", help="Run only the DAN status check unless other positive check flags are also provided.")
    parser.add_argument("--certificate", "--cert", dest="certificate", action="store_true",
                        help="Run only the certificate/secret-key check unless other positive check flags are also provided.")
    parser.add_argument("--mgmt-ts", dest="mgmt_ts", action="store_true",
                        help="Run only the mgmt/ts in-service check unless other positive check flags are also provided.")
    parser.add_argument("--hostname-validation", "--hostnamevalidation", dest="hostname_validation", action="store_true",
                        help="Run only the hostname validation check unless other positive check flags are also provided.")
    parser.add_argument("--static-mac", "--staticmac", dest="static_mac", action="store_true",
                        help="Run only the static MAC check unless other positive check flags are also provided.")
    parser.add_argument("--linkflap", "--link-flap", dest="link_flap", action="store_true",
                        help="Run only the link flap protection check unless other positive check flags are also provided.")
    parser.add_argument("--config-diff", "--configdiff", "--compare-config", dest="config_diff", action="store_true",
                        help="Run only the config-diff compare-config check unless other positive check flags are also provided.")
    parser.add_argument("--lldp", action="store_true",
                        help="Run only the SSH LLDP configuration check unless other positive check flags are also provided.")
    parser.add_argument("--gnmi", action="store_true", help="Run only the gNMI check unless other positive check flags are also provided.")
    parser.add_argument("--system-health", "--systemhealth", dest="system_health", action="store_true",
                        help="Run only the system health check unless other positive check flags are also provided.")
    parser.add_argument("--optics-temperature", "--optic-temperature", dest="optics_temperature", action="store_true",
                        help="Run only the optics temperature check unless other positive check flags are also provided.")
    parser.add_argument("--skip-ztp", action="store_true", help="Skip ZTP image check.")
    parser.add_argument("--skip-dan", action="store_true", help="Skip DAN status check.")
    parser.add_argument("--skip-certificate", action="store_true", help="Skip devices certificate get check.")
    parser.add_argument("--skip-mgmt-ts", action="store_true", help="Skip mgmt/ts in-service check.")
    parser.add_argument("--skip-hostname-validation", action="store_true", help="Skip qcli hostname validation check.")
    parser.add_argument("--racks", help="Rack numbers for certificate, mgmt/ts, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature checks, comma/space separated, e.g. 0119,0120.")
    parser.add_argument("--device-file",
                        help="File with one device hostname per line for mgmt/ts rack resolution, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature checks. Blank/# lines ignored.")
    parser.add_argument("--certificate-rack-region",
                        help="Rack prefix used for certificate check. Defaults to the target value, e.g. iad60.")
    parser.add_argument("--certificate-include-management", action="store_true",
                        help="Include management devices matching *-m1-* in certificate verification. Default excludes them.")
    parser.add_argument("--mgmt-ts-rack-region",
                        help="Rack prefix used for mgmt/ts check. Defaults to the target value, e.g. iad60.")
    parser.add_argument("--mgmt-ts-roles", default="mgmt,ts",
                        help="Role selector used for mgmt/ts check. Default: mgmt,ts.")
    parser.add_argument("--mgmt-ts-timeout", type=int, default=900,
                        help="Timeout in seconds for mgmt/ts interactive state check. Default: 900.")
    parser.add_argument("--hostname-validation-rack-region",
                        help="Rack prefix used for hostname validation qfabt0 resolution. Defaults to the target value, e.g. iad60.")
    parser.add_argument("--hostname-validation-timeout", type=int, default=3600,
                        help="Timeout in seconds for qfabt0 scope resolution and qcli hostname validation. Default: 3600.")
    parser.add_argument("--static-mac-script", default=DEFAULT_STATIC_MAC_SCRIPT,
                        help="Path to nv_static_mac_address_check.py, or builtin. Default: builtin.")
    parser.add_argument("--static-mac-region",
                        help="Region/site passed to nv_static_mac_address_check.py. Defaults to the target value, e.g. iad60.")
    parser.add_argument("--skip-static-mac", action="store_true", help="Skip static MAC verification.")
    parser.add_argument("--static-mac-skip-state-check", action="store_true", default=True,
                        help="Pass --skip-state-check to nv_static_mac_address_check.py. Default behavior.")
    parser.add_argument("--static-mac-state-check", action="store_false", dest="static_mac_skip_state_check",
                        help="Run the static MAC deployed-state check instead of skipping it.")
    parser.add_argument("--static-mac-prompt-password", action="store_true",
                        help="Pass --prompt-password to nv_static_mac_address_check.py.")
    parser.add_argument("--static-mac-show-ok", action="store_true",
                        help="Pass --show-ok to nv_static_mac_address_check.py.")
    parser.add_argument("--static-mac-timeout", type=int, default=60,
                        help="Per-device SSH command timeout for static MAC check. Default: 60.")
    parser.add_argument("--static-mac-run-timeout", type=int, default=3600,
                        help="Overall timeout for the static MAC checker subprocess. Default: 3600.")
    parser.add_argument("--static-mac-workers", type=int, default=8,
                        help="Static MAC checker worker count. Default: 8.")
    parser.add_argument("--static-mac-expected-count", type=int, default=5,
                        help="Expected static MAC address line count. Default: 5.")
    parser.add_argument("--static-mac-username", help="SSH username for static MAC check.")
    parser.add_argument("--static-mac-jit-region", help="JIT region override for static MAC check.")
    parser.add_argument("--static-mac-jitpw-path", help="Path to jitpw for static MAC check.")
    parser.add_argument("--static-mac-debug-log", help="Debug log path for static MAC SSH sessions.")
    parser.add_argument("--skip-link-flap", action="store_true", help="Skip link flap protection status check.")
    parser.add_argument("--skip-config-diff", "--skip-configdiff", "--skip-compare-config", dest="skip_config_diff", action="store_true",
                        help="Skip config-diff compare-config check.")
    parser.add_argument("--skip-lldp", action="store_true", help="Skip SSH LLDP configuration check.")
    parser.add_argument("--skip-gnmi", action="store_true", help="Skip gNMI status check.")
    parser.add_argument("--skip-system-health", action="store_true", help="Skip system health check.")
    parser.add_argument("--skip-optics-temperature", action="store_true", help="Skip optics temperature Prometheus check.")
    parser.add_argument("--link-flap-script", default=DEFAULT_LINK_FLAP_SCRIPT,
                        help="Path to link_flap_protection.py, or builtin. Default: builtin.")
    parser.add_argument("--racktopo-script", default=DEFAULT_RACKTOPO_SCRIPT,
                        help="Path to multiplaner_racktopo.py, or builtin. Default: builtin.")
    parser.add_argument("--racktopo-workers", type=int, default=1,
                        help="Parallel rack topology lookup worker count. Default: 1.")
    parser.add_argument("--link-flap-username", help="SSH username for link flap check. Defaults to local username.")
    parser.add_argument("--link-flap-jit-region", help="JIT region override for link flap check. Defaults to short region from target, e.g. iad.")
    parser.add_argument("--link-flap-jitpw-path", help="Path to jitpw for link flap check.")
    parser.add_argument("--link-flap-prompt-password", action="store_true",
                        help="Prompt for link flap SSH password instead of retrieving it with jitpw.")
    parser.add_argument("--link-flap-timeout", type=int, default=60,
                        help="Per-device SSH command timeout for link flap check. Default: 60.")
    parser.add_argument("--link-flap-workers", type=int, default=8,
                        help="Link flap checker worker count. Default: 8.")
    parser.add_argument("--link-flap-debug-log", help="Debug log path for link flap SSH sessions.")
    parser.add_argument("--config-diff-timeout", "--configdiff-timeout", "--lldp-compare-timeout", dest="config_diff_timeout", type=int, default=900,
                        help="Timeout in seconds for config-diff compare-config interactive job. Default: 900.")
    parser.add_argument("--system-health-rack-region",
                        help="Rack prefix used for system health check. Defaults to the target value, e.g. iad60.")
    parser.add_argument("--system-health-roles", default="qfabt0",
                        help="Role selector used with --devices-by-role for system health. Default: qfabt0.")
    parser.add_argument("--system-health-timeout", type=int, default=900,
                        help="Timeout in seconds for the system health interactive job. Default: 900.")
    parser.add_argument("--optics-temperature-threshold-c", type=float, default=65.0,
                        help="PASS when no optics transceiver temperature is greater than this Celsius threshold. Default: 65.")
    parser.add_argument("--optics-temperature-timeout", type=int, default=300,
                        help="Timeout in seconds for the optics temperature Prometheus query. Default: 300.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    apply_check_selection(args, parser)
    if not args.target:
        if not args.region:
            parser.error("target is required unless --region, -r, or --site is provided")
        args.target = args.region
        if re.search(r"\d", args.region):
            args.region = None

    global SSH_HOST_SUFFIX
    SSH_HOST_SUFFIX = args.ssh_host_suffix or ""
    device_file_hosts: List[str] = []
    if args.device_file:
        try:
            device_file_hosts = read_device_file(args.device_file)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    rack_values = prompt_for_racks_if_needed(args)
    has_device_scope = bool(rack_values) or bool(device_file_hosts)
    run_mgmt_ts = has_device_scope and not args.skip_mgmt_ts
    run_static_mac = has_device_scope and not args.skip_static_mac
    run_link_flap = has_device_scope and not args.skip_link_flap
    run_config_diff = has_device_scope and not args.skip_config_diff
    run_lldp = has_device_scope and not args.skip_lldp
    run_gnmi = has_device_scope and not args.skip_gnmi
    run_system_health = has_device_scope and not args.skip_system_health
    run_optics_temperature = has_device_scope and not args.skip_optics_temperature
    run_hostname_validation = has_device_scope and not args.skip_hostname_validation

    if args.skip_ztp and args.skip_dan and args.skip_certificate and not run_mgmt_ts and not run_static_mac and not run_link_flap and not run_config_diff and not run_lldp and not run_gnmi and not run_system_health and not run_optics_temperature and not run_hostname_validation:
        print("ERROR: all checks were skipped.", file=sys.stderr)
        return 2
    if args.mgmt_ts_timeout < 1:
        print("ERROR: --mgmt-ts-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.static_mac_timeout < 1:
        print("ERROR: --static-mac-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.static_mac_run_timeout < 1:
        print("ERROR: --static-mac-run-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.static_mac_workers < 1:
        print("ERROR: --static-mac-workers must be >= 1.", file=sys.stderr)
        return 2
    if args.static_mac_expected_count < 1:
        print("ERROR: --static-mac-expected-count must be >= 1.", file=sys.stderr)
        return 2
    if args.link_flap_timeout < 1:
        print("ERROR: --link-flap-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.link_flap_workers < 1:
        print("ERROR: --link-flap-workers must be >= 1.", file=sys.stderr)
        return 2
    if args.config_diff_timeout < 1:
        print("ERROR: --config-diff-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.system_health_timeout < 1:
        print("ERROR: --system-health-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.optics_temperature_threshold_c < 0:
        print("ERROR: --optics-temperature-threshold-c must be >= 0.", file=sys.stderr)
        return 2
    if args.optics_temperature_timeout < 1:
        print("ERROR: --optics-temperature-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.hostname_validation_timeout < 1:
        print("ERROR: --hostname-validation-timeout must be >= 1.", file=sys.stderr)
        return 2
    if args.racktopo_workers < 1:
        print("ERROR: --racktopo-workers must be >= 1.", file=sys.stderr)
        return 2
    if args.device_file and not device_file_hosts:
        print(f"ERROR: no devices found in --device-file: {args.device_file}", file=sys.stderr)
        return 2
    if run_static_mac and args.static_mac_script != "builtin" and not os.path.isfile(args.static_mac_script):
        print(f"ERROR: static MAC script not found: {args.static_mac_script}", file=sys.stderr)
        return 2
    if run_static_mac and args.device_file and not rack_values and args.static_mac_script != "builtin":
        print("ERROR: --device-file requires --static-mac-script builtin, or provide --racks for the external static MAC script.", file=sys.stderr)
        return 2
    needs_link_flap_module = (
        run_link_flap
        or (run_config_diff and rack_values and not device_file_hosts)
        or run_lldp
        or run_gnmi
        or (run_optics_temperature and rack_values and not device_file_hosts)
    )
    if needs_link_flap_module and args.link_flap_script != "builtin" and not os.path.isfile(args.link_flap_script):
        print(f"ERROR: link flap protection script not found: {args.link_flap_script}", file=sys.stderr)
        return 2
    if (run_link_flap or run_config_diff or run_lldp or run_gnmi or run_optics_temperature or run_static_mac) and rack_values and args.racktopo_script != "builtin" and not os.path.isfile(args.racktopo_script):
        print(f"ERROR: rack topology script not found: {args.racktopo_script}", file=sys.stderr)
        return 2

    region = infer_region(args.target, args.region)
    static_mac_region = args.static_mac_region or args.target
    certificate_rack_region = args.certificate_rack_region or args.target
    mgmt_ts_rack_region = args.mgmt_ts_rack_region or args.target
    hostname_validation_rack_region = args.hostname_validation_rack_region or args.target
    system_health_rack_region = args.system_health_rack_region or args.target
    all_images: List[ImageEntry] = []
    returned_images: List[ImageEntry] = []
    rack_device_hosts: List[str] = []
    ztp_required_image_present = False
    ztp_cmd: List[str] = []
    ztp_device_status = ZtpDeviceStatus(
        "",
        True,
        "Skipped device product-release check because no racks or --device-file were provided",
        release_from_ztp_image(args.required_ztp_image),
        0,
        "",
    )
    dan_status = DanStatus("", True, "Skipped", {})
    certificate_status = CertificateStatus("", True, "Skipped", "")
    hostname_validation_status = HostnameValidationStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    mgmt_ts_status = MgmtTsStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    static_mac_status = StaticMacStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    link_flap_status = LinkFlapStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    config_diff_status = ConfigDiffStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    lldp_status = LldpStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    gnmi_status = GnmiStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    system_health_status = SystemHealthStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    optics_temperature_status = OpticsTemperatureStatus("", True, "Skipped because neither --racks nor --device-file was provided", "")
    if args.skip_mgmt_ts:
        mgmt_ts_status = MgmtTsStatus("", True, "Skipped by --skip-mgmt-ts", "")
    if args.skip_hostname_validation:
        hostname_validation_status = HostnameValidationStatus("", True, "Skipped by --skip-hostname-validation", "")
    if args.skip_static_mac:
        static_mac_status = StaticMacStatus("", True, "Skipped by --skip-static-mac", "")
    if args.skip_link_flap:
        link_flap_status = LinkFlapStatus("", True, "Skipped by --skip-link-flap", "")
    if args.skip_config_diff:
        config_diff_status = ConfigDiffStatus("", True, "Skipped by --skip-config-diff", "")
    if args.skip_lldp:
        lldp_status = LldpStatus("", True, "Skipped by --skip-lldp", "")
    if args.skip_gnmi:
        gnmi_status = GnmiStatus("", True, "Skipped by --skip-gnmi", "")
    if args.skip_system_health:
        system_health_status = SystemHealthStatus("", True, "Skipped by --skip-system-health", "")
    if args.skip_optics_temperature:
        optics_temperature_status = OpticsTemperatureStatus("", True, "Skipped by --skip-optics-temperature", "")

    try:
        if device_file_hosts:
            rack_device_hosts = device_file_hosts
        elif rack_values and (not args.skip_ztp or run_static_mac or run_link_flap or run_config_diff or run_lldp or run_gnmi or run_optics_temperature):
            host_link_module = load_link_flap_module(args.link_flap_script)
            rack_device_hosts = expand_rack_hosts_from_topology(
                static_mac_region,
                rack_values,
                args.racktopo_script,
                host_link_module,
                args.ncpcli_command,
                args.racktopo_workers,
            )

        if not args.skip_ztp:
            ztp_progress_hosts = rack_device_hosts or device_file_hosts or None
            ztp_progress_total = scoped_switch_progress_total(rack_values, ztp_progress_hosts)
            ztp_progress = ProgressReporter("ZTP", ztp_progress_total, enabled=progress_enabled(args))
            ztp_cmd = ztp_command(args.ncpcli_command, region, args.connection_methods)
            ztp_result = run_command(ztp_cmd, args.timeout)
            if ztp_result.returncode != 0:
                ztp_progress.update(ztp_progress_total, failed=ztp_progress_total)
                output = "\n".join(part for part in [ztp_result.stdout.strip(), ztp_result.stderr.strip()] if part)
                print(f"ERROR: command failed: {shell_join(ztp_cmd)}", file=sys.stderr)
                if output:
                    print(output, file=sys.stderr)
                return 2
            all_images = parse_images(ztp_result.stdout, args.include_duplicates)
            returned_images = filter_images(all_images, args.vendor, args.contains)
            ztp_required_image_present = any(
                entry.raw == args.required_ztp_image
                for entry in all_images
            )
            ztp_device_status = run_ztp_device_status(
                args,
                region,
                args.required_ztp_image,
                rack_device_hosts,
            )
            ztp_has_device_scope = bool(rack_values or device_file_hosts)
            ztp_scope_verified = not ztp_has_device_scope or ztp_device_status.checked_devices > 0
            ztp_ok = (
                ztp_progress_total
                if ztp_required_image_present
                and ztp_device_status.verified
                and ztp_scope_verified
                else 0
            )
            ztp_progress.update(
                ztp_progress_total,
                ok=ztp_ok,
                flagged=ztp_progress_total - ztp_ok,
            )

        if not args.skip_dan:
            dan_progress_hosts = rack_device_hosts or device_file_hosts or None
            dan_progress_total = scoped_switch_progress_total(rack_values, dan_progress_hosts)
            dan_progress = ProgressReporter("DAN", dan_progress_total, enabled=progress_enabled(args))
            dan_cmd = dan_command(args.ncpcli_command)
            dan_result = run_command(dan_cmd, args.timeout)
            if dan_result.returncode != 0:
                dan_progress.update(dan_progress_total, failed=dan_progress_total)
                output = "\n".join(part for part in [dan_result.stdout.strip(), dan_result.stderr.strip()] if part)
                print(f"ERROR: command failed: {shell_join(dan_cmd)}", file=sys.stderr)
                if output:
                    print(output, file=sys.stderr)
                return 2
            dan_status = parse_dan_status(dan_result.stdout, region, dan_cmd)
            dan_progress.update(
                dan_progress_total,
                ok=dan_progress_total if dan_status.verified else 0,
                flagged=0 if dan_status.verified else dan_progress_total,
            )

        if not args.skip_certificate:
            if not rack_values:
                certificate_status = CertificateStatus("", True, "Skipped because no racks were provided", "")
            else:
                cert_cmd = certificate_command(
                    args.ncpcli_command,
                    region,
                    args.connection_methods,
                    certificate_rack_region,
                    rack_values,
                    not args.certificate_include_management,
                )
                cert_result = run_command(cert_cmd, args.timeout)
                if cert_result.returncode != 0:
                    output = "\n".join(part for part in [cert_result.stdout.strip(), cert_result.stderr.strip()] if part)
                    print(f"ERROR: command failed: {shell_join(cert_cmd)}", file=sys.stderr)
                    if output:
                        print(output, file=sys.stderr)
                    return 2
                certificate_status = parse_certificate_status(cert_result.stdout, cert_cmd)

        if run_mgmt_ts:
            mgmt_ts_status = run_mgmt_ts_status(
                args,
                region,
                mgmt_ts_rack_region,
                rack_values,
                device_file_hosts if device_file_hosts else None,
            )

        if run_hostname_validation:
            hostname_validation_status = run_hostname_validation_status(
                args,
                region,
                hostname_validation_rack_region,
                rack_values,
                device_file_hosts if device_file_hosts else None,
            )

        if run_static_mac:
            static_mac_status = run_static_mac_status(args, static_mac_region, rack_values, rack_device_hosts)

        if run_link_flap:
            link_flap_status = run_link_flap_status(args, static_mac_region, rack_values, rack_device_hosts)

        if run_config_diff:
            config_diff_status = run_config_diff_status(args, static_mac_region, rack_values, rack_device_hosts)

        if run_lldp:
            lldp_status = run_lldp_status(args, static_mac_region, rack_values, rack_device_hosts)

        if run_gnmi:
            gnmi_status = run_gnmi_status(args, static_mac_region, rack_values, rack_device_hosts)

        if run_system_health:
            system_health_status = run_system_health_status(
                args,
                region,
                system_health_rack_region,
                rack_values,
                device_file_hosts if device_file_hosts else None,
            )

        if run_optics_temperature:
            optics_temperature_status = run_optics_temperature_status(args, static_mac_region, rack_values, rack_device_hosts)
    except subprocess.TimeoutExpired as exc:
        print(f"ERROR: timed out after {exc.timeout}s: {shell_join(exc.cmd)}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        ncpcli_binary = shlex.split(args.ncpcli_command)[0] if args.ncpcli_command else ""
        qcli_binary = shlex.split(args.qcli_command)[0] if args.qcli_command else ""
        if getattr(exc, "filename", None) == ncpcli_binary:
            print(f"ERROR: could not find ncpcli command: {args.ncpcli_command}", file=sys.stderr)
            print("Hint: run from netops-env or pass --ncpcli-command 'env PYENV_VERSION=netops-env ncpcli'.", file=sys.stderr)
        elif getattr(exc, "filename", None) == qcli_binary:
            print(f"ERROR: could not find qcli command: {args.qcli_command}", file=sys.stderr)
            print("Hint: run from qcli-env or pass --qcli-command 'env PYENV_VERSION=qcli-env qcli'.", file=sys.stderr)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = Report(
        target=args.target,
        region=region,
        ztp_command=shell_join(ztp_cmd) if ztp_cmd else "Skipped",
        total_images=len(all_images),
        returned_images=len(returned_images),
        filters={"vendor": args.vendor, "contains": args.contains},
        ztp_required_image=args.required_ztp_image,
        ztp_required_image_present=ztp_required_image_present,
        ztp_device_status=ztp_device_status,
        images=returned_images,
        dan=dan_status,
        certificate=certificate_status,
        hostname_validation=hostname_validation_status,
        mgmt_ts=mgmt_ts_status,
        static_mac=static_mac_status,
        link_flap=link_flap_status,
        config_diff=config_diff_status,
        lldp=lldp_status,
        gnmi=gnmi_status,
        system_health=system_health_status,
        optics_temperature=optics_temperature_status,
    )
    print_report(report, args.json)

    if not args.skip_ztp and not ztp_verified(report):
        return 1
    if not args.skip_dan and not dan_status.verified:
        return 1
    if not args.skip_certificate and not certificate_status.verified:
        return 1
    if run_mgmt_ts and not mgmt_ts_status.verified:
        return 1
    if run_hostname_validation and not hostname_validation_status.verified:
        return 1
    if run_static_mac and not static_mac_status.verified:
        return 1
    if run_link_flap and not link_flap_status.verified:
        return 1
    if run_config_diff and not config_diff_status.verified:
        return 1
    if run_lldp and not lldp_status.verified:
        return 1
    if run_gnmi and not gnmi_status.verified:
        return 1
    if run_system_health and not system_health_status.verified:
        return 1
    if run_optics_temperature and not optics_temperature_status.verified:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
