#!/usr/bin/env python3
"""
Enable or disable LLDP on GPU-facing interfaces of QFAB Arista switches.

The tool uses NCPCLI only for read-only rack-to-device discovery. Device
configuration and verification are performed over direct SSH. Rack discovery
filters for EOS devices, and the default Arista interface discovery command is:

    show interfaces description | grep gpu

Usage examples:

    # Enable LLDP for several racks.
    ./qfab_arista_lldp_tool.py -r fra12 --racks 5113,5213,5713,5813 \
        --vendor arista --action enable

    # Disable LLDP on GPU-facing interfaces.
    ./qfab_arista_lldp_tool.py -r fra12 --racks 5113,5213 \
        --vendor arista --action disable

    # Use repeated rack options or a numeric rack range.
    ./qfab_arista_lldp_tool.py -r fra12 --rack 5113 --rack 5213 \
        --vendor arista --action enable
    ./qfab_arista_lldp_tool.py -r fra12 --racks 5113-5116 \
        --vendor arista --action enable

    # Operate on explicit devices instead of racks.
    ./qfab_arista_lldp_tool.py --device fra12-q2-b7-t0-r33 \
        --vendor arista --action enable

Run ./qfab_arista_lldp_tool.py --help for all options.
"""

import argparse
import re
import sys
import threading
import pexpect
import getpass
from dataclasses import dataclass
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess

HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE    = r"(?i)(username|login)[: ]"
PASS_RE    = r"(?i)password.*:\s*$"

ARISTA_PROMPT_RE = r"(?m)^[^\r\n]+(?:\([^)]+\))?[>#]\s*$"
ARISTA_MORE_RE   = r"(?i)(--more--|press any key to continue|press <space> to continue)"
ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

MAX_WORKERS = 8  # default concurrency (always enabled)
PRINT_LOCK = threading.Lock()

def progress(host: str, message: str) -> None:
    """Print one complete progress line without interleaving worker output."""
    with PRINT_LOCK:
        print(f"[{host}] {message}", flush=True)

@dataclass
class Device:
    vendor: str   # "arista" | "nvidia"
    host: str
    port: int = 22

def connect_ssh(host, username, password, port=22, timeout=30, strict_hostkey="ask", debug_log=None):
    ssh_args = [
        "-o", f"StrictHostKeyChecking={strict_hostkey}",
        "-o", "PreferredAuthentications=keyboard-interactive,password",
        "-o", "PubkeyAuthentication=no",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "ConnectionAttempts=1",
        "-p", str(port),
        f"{username}@{host}",
    ]
    child = pexpect.spawn("ssh", args=ssh_args, encoding="utf-8", timeout=timeout)
    if debug_log:
        # Note: multi-threaded logging to the same file can interleave lines.
        child.logfile = open(debug_log, "a")

    try:
        while True:
            i = child.expect([HOSTKEY_RE, USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
            if i == 0:
                child.sendline("yes")
            elif i == 1:
                child.sendline(username)
            elif i == 2:
                child.sendline(password)
                return child
            elif i == 3:
                details = " ".join((child.before or "").split())
                suffix = f": {details[-400:]}" if details else ""
                raise TimeoutError(f"Login timed out connecting to {host}{suffix}")
            else:
                details = " ".join((child.before or "").split())
                suffix = f": {details[-400:]}" if details else ""
                raise ConnectionError(f"EOF while connecting to {host}{suffix}")
    except Exception:
        child.close(force=True)
        raise

def close_session(child):
    try:
        child.sendline("exit")
        child.expect(pexpect.EOF, timeout=10)
    finally:
        child.close()

# ---------------- Pattern expansion ----------------
def expand_bracket_range(pattern: str) -> List[str]:
    m = re.search(r"\[(\d+)-(\d+)\]", pattern)
    if not m:
        return [pattern]

    start_s, end_s = m.group(1), m.group(2)
    start, end = int(start_s), int(end_s)
    if start > end:
        raise ValueError(f"Invalid range in {pattern}: start > end")

    width = max(len(start_s), len(end_s))
    return [
        pattern[:m.start()] + str(i).zfill(width) + pattern[m.end():]
        for i in range(start, end + 1)
    ]

# ---------------- Arista EOS ----------------
def arista_prepare(child, timeout=20):
    child.sendline("")
    child.expect(ARISTA_PROMPT_RE, timeout=timeout)
    child.sendline("terminal length 0")
    child.expect(ARISTA_PROMPT_RE, timeout=timeout)

def arista_run_command(child, cmd, timeout=60):
    child.timeout = timeout
    child.sendline(cmd)
    chunks = []
    while True:
        i = child.expect([ARISTA_PROMPT_RE, ARISTA_MORE_RE, pexpect.TIMEOUT, pexpect.EOF])
        chunks.append(child.before)
        if i == 0:
            break
        elif i == 1:
            child.send(" ")
        elif i == 2:
            raise TimeoutError(f"Timed out waiting for EOS prompt after: {cmd}")
        else:
            raise ConnectionError("Connection closed unexpectedly")

    raw = "".join(chunks)
    cleaned = re.sub(rf"(?s)^\s*{re.escape(cmd)}\s*\r?\n", "", raw).strip()
    return cleaned

def arista_config(child, interfaces: List[str], action: str, timeout=60):
    if not interfaces:
        return

    if_range = ",".join(interfaces)
    arista_run_command(child, "configure terminal", timeout=timeout)
    if action == "enable":
        # LLDP must be enabled globally before interface transmit/receive works.
        arista_run_command(child, "lldp run", timeout=timeout)
    arista_run_command(child, f"interface range {if_range}", timeout=timeout)

    if action == "enable":
        arista_run_command(child, "default lldp transmit", timeout=timeout)
        arista_run_command(child, "default lldp receive", timeout=timeout)
    else:
        arista_run_command(child, "no lldp transmit", timeout=timeout)
        arista_run_command(child, "no lldp receive", timeout=timeout)

    arista_run_command(child, "exit", timeout=timeout)
    arista_run_command(child, "end", timeout=timeout)
    arista_run_command(child, "write memory", timeout=timeout)

# ---------------- NVIDIA / Cumulus Linux (nv) ----------------
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

def nvidia_config(dev: Device, child, prompt, interfaces: List[str], action: str, timeout=180):
    if not interfaces:
        return
    if_csv = ",".join(interfaces)

    if action == "enable":
        cmd = f"nv set interface {if_csv} lldp state enabled"
    else:
        cmd = f"nv set interface {if_csv} lldp state disabled"

    out = nvidia_run_command(child, cmd, prompt, timeout=timeout)
    print(f"[nvidia]: {dev.host} ran: {cmd}\n{out}")

    apply_out = nvidia_run_command(child, "nv config apply", prompt, timeout=timeout)
    print(f"[nvidia]: {dev.host} ran: nv config apply\n{apply_out}")

# ---------------- Parsing ----------------
def parse_interfaces_arista(output: str) -> List[str]:
    interfaces = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith(("port", "interface")):
            continue
        interfaces.append(line.split()[0])
    return interfaces

def parse_interfaces_nvidia(output: str) -> list[str]:
    interfaces: list[str] = []
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

def expand_nvue_interface_token(token: str) -> List[str]:
    """Expand the interface ranges emitted by `nv config show -o commands`."""
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

def verify_arista_lldp(child, interfaces: List[str], action: str, timeout=60) -> Tuple[bool, str]:
    if not interfaces:
        return False, "verification failed: discovery returned no interfaces"

    if action == "enable":
        global_output = arista_run_command(
            child, "show running-config | include ^lldp run", timeout=timeout
        )
        if not re.search(r"(?m)^\s*lldp run\s*$", global_output):
            return False, "LLDP NOT ENABLED globally: 'lldp run' is absent"

    mismatches: List[str] = []
    for interface in interfaces:
        output = arista_run_command(
            child, f"show running-config interfaces {interface}", timeout=timeout
        )
        transmit_disabled = bool(re.search(r"(?m)^\s*no lldp transmit\s*$", output))
        receive_disabled = bool(re.search(r"(?m)^\s*no lldp receive\s*$", output))
        is_enabled = not transmit_disabled and not receive_disabled
        is_disabled = transmit_disabled and receive_disabled
        verified = is_enabled if action == "enable" else is_disabled
        if not verified:
            if is_enabled:
                state = "enabled"
            elif is_disabled:
                state = "disabled"
            else:
                state = "partially disabled"
            mismatches.append(f"{interface}={state}")

    expected = "ENABLED" if action == "enable" else "DISABLED"
    if mismatches:
        return False, f"LLDP NOT {expected}: " + ", ".join(mismatches)
    global_detail = " globally and" if action == "enable" else ""
    return True, f"VERIFIED LLDP {expected}{global_detail} on {len(interfaces)} interface(s)"

def verify_nvidia_lldp(child, prompt, interfaces: List[str], action: str, timeout=180) -> Tuple[bool, str]:
    if not interfaces:
        return False, "verification failed: discovery returned no interfaces"

    output = nvidia_run_command(
        child,
        "nv config show -o commands | grep 'lldp state disabled'",
        prompt,
        timeout=timeout,
    )
    disabled = set()
    for line in output.splitlines():
        match = re.search(r"\binterface\s+(\S+)\s+lldp state disabled\b", line)
        if match:
            disabled.update(expand_nvue_interface_list(match.group(1)))

    interface_set = set(interfaces)
    disabled &= interface_set
    if action == "enable":
        mismatches = sorted(disabled)
        expected = "ENABLED"
    else:
        mismatches = sorted(interface_set - disabled)
        expected = "DISABLED"

    if mismatches:
        return False, f"LLDP NOT {expected}: " + ", ".join(mismatches)
    return True, f"VERIFIED LLDP {expected} on {len(interfaces)} interface(s)"

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

def expand_rack_values(values: Optional[List[str]]) -> List[str]:
    """Accept repeated, comma/space-separated rack values and numeric ranges."""
    racks: List[str] = []
    for value in values or []:
        for token in re.split(r"[,\s]+", value.strip()):
            if not token:
                continue
            range_match = re.fullmatch(r"(\d+)-(\d+)", token)
            if not range_match:
                racks.append(token)
                continue
            start_s, end_s = range_match.groups()
            start, end = int(start_s), int(end_s)
            if start > end:
                raise argparse.ArgumentTypeError(
                    f"Invalid rack range {token}: start > end"
                )
            width = max(len(start_s), len(end_s))
            racks.extend(str(index).zfill(width) for index in range(start, end + 1))
    return list(dict.fromkeys(racks))

def parse_ncpcli_hosts(table_out: str) -> List[Tuple[str, int]]:
    hosts: List[Tuple[str, int]] = []
    name_index: Optional[int] = None
    for line in ANSI_RE.sub("", table_out).splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|-"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if not columns:
            continue

        normalized = [re.sub(r"[^a-z0-9]", "", column.lower()) for column in columns]
        if "name" in normalized:
            name_index = normalized.index("name")
            continue
        if name_index is None or name_index >= len(columns):
            continue

        name = columns[name_index]
        if not name or set(name) <= {"-", "+"}:
            continue
        if not re.fullmatch(r"[A-Za-z]{3}\d+-[A-Za-z0-9._-]+", name):
            continue
        if "netpdu" in name.lower() or "-m" in name.lower():
            continue
        hosts.append((name, 22))
    return list(dict.fromkeys(hosts))

def ncp_region_and_site(value: str) -> Tuple[str, str]:
    match = re.fullmatch(r"([A-Za-z]{3})(\d+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            "--region must be a site such as fra12 (three letters followed by digits)"
        )
    region = match.group(1).lower()
    return region, f"{region}{match.group(2)}"

def load_rack_hosts_from_ncpcli(
    site_value: str, racks: List[str], vendor: str
) -> List[Tuple[str, int]]:
    region, site = ncp_region_and_site(site_value)
    config_platform = "eos" if vendor == "arista" else "cumulus"
    command = ["ncpcli", "-r", region, "devices", "list"]
    for rack in racks:
        command.extend(["--devices-by-rack", f"{site}:{rack}"])
    command.extend(["--config-platform", config_platform, "--verbose"])

    print(
        f"\nResolving {vendor} devices in {site} racks "
        f"{', '.join(racks)} via NCPCLI..."
    )
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"NCPCLI device lookup failed with exit {result.returncode}")

    hosts = parse_ncpcli_hosts(result.stdout or "")
    if not hosts:
        output_tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(
            f"NCPCLI returned no {config_platform} devices for {site} racks "
            f"{', '.join(racks)}. Output tail:\n{output_tail or '<no output>'}"
        )

    print(f"Resolved {len(hosts)} device(s):")
    for host, _ in hosts:
        print(f"  {host}")
    return hosts

def process_device(dev: Device, username: str, password: str,
                   discovery_cmd: str, action: str, timeout: int,
                   debug_log: Optional[str]) -> str:
    progress(dev.host, f"Connecting over SSH on port {dev.port}...")
    child = connect_ssh(dev.host, username, password, port=dev.port,
                        timeout=max(timeout, 30), debug_log=debug_log)
    try:
        if dev.vendor == "arista":
            arista_prepare(child, timeout=timeout)
            progress(dev.host, "Login successful; EOS prompt detected.")
            progress(dev.host, f"Discovering interfaces: {discovery_cmd}")
            out = arista_run_command(child, discovery_cmd, timeout=timeout)
            interfaces = parse_interfaces_arista(out)
            if not interfaces:
                output = " ".join((out or "<no output>").split())
                raise RuntimeError(
                    f"discovery command matched no interfaces ({discovery_cmd!r}); "
                    f"output: {output[-400:]}"
                )
            progress(
                dev.host,
                f"Matched {len(interfaces)} interface(s): {', '.join(interfaces)}",
            )
            progress(dev.host, f"Applying LLDP action: {action}...")
            arista_config(child, interfaces, action, timeout=timeout)
            progress(dev.host, "Configuration applied and write memory completed.")
            progress(dev.host, "Verifying LLDP configuration...")
            verified, detail = verify_arista_lldp(
                child, interfaces, action, timeout=timeout
            )
        else:
            prompt = nvidia_prepare(child, timeout=timeout)
            progress(dev.host, "Login successful; NVIDIA shell prompt detected.")
            progress(dev.host, f"Discovering interfaces: {discovery_cmd}")
            out = nvidia_run_command(child, discovery_cmd, prompt, timeout=max(timeout, 180))
            interfaces = parse_interfaces_nvidia(out)
            if not interfaces:
                output = " ".join((out or "<no output>").split())
                raise RuntimeError(
                    f"discovery command matched no interfaces ({discovery_cmd!r}); "
                    f"output: {output[-400:]}"
                )
            progress(
                dev.host,
                f"Matched {len(interfaces)} interface(s): {', '.join(interfaces)}",
            )
            progress(dev.host, f"Applying LLDP action: {action}...")
            nvidia_config(dev, child, prompt, interfaces, action, timeout=max(timeout, 180))
            progress(dev.host, "Configuration applied with nv config apply.")
            progress(dev.host, "Verifying LLDP configuration...")
            verified, detail = verify_nvidia_lldp(
                child, prompt, interfaces, action, timeout=max(timeout, 180)
            )
        if not verified:
            raise RuntimeError(detail)
        progress(dev.host, f"PASS - {detail}")
        return f"{dev.host}: PASS - {detail}"
    finally:
        close_session(child)

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Enable or disable LLDP on GPU-facing interfaces of QFAB switches, "
            "then verify the requested state."
        ),
        epilog="""examples:
  Enable LLDP for multiple racks:
    %(prog)s -r fra12 --racks 5113,5213,5713,5813 --vendor arista --action enable

  Disable LLDP using repeated rack options:
    %(prog)s -r fra12 --rack 5113 --rack 5213 --vendor arista --action disable

  Enable LLDP using a rack range:
    %(prog)s -r fra12 --racks 5113-5116 --vendor arista --action enable

  Target one device directly:
    %(prog)s --device fra12-q2-b7-t0-r33 --vendor arista --action enable

NCPCLI is used only for read-only rack discovery. Configuration and
verification are performed directly over SSH.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vendor", required=True, choices=["arista", "nvidia"],
                        help="Vendor applied to all devices specified in this run.")
    parser.add_argument("--action", required=True, choices=["enable", "disable"],
                        help="Enable or disable LLDP on matched interfaces.")
    parser.add_argument("--device", action="append", type=parse_host_line,
                        help="Repeatable: --device host[,port]")
    parser.add_argument("--device-pattern", action="append", default=[],
                        help='Repeatable: --device-pattern "aga5-q2-p1-t0-r[1-10]"')
    parser.add_argument("--device-file", default=None,
                        help="File with one host per line: host or host,port. Blank/# lines ignored.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--debug-log", default=None)
    parser.add_argument("--cmd", default=None,
                        help="Override discovery command. If omitted, a vendor-specific default is used.")

    # Rack-based device discovery through NCPCLI.
    parser.add_argument(
        "-r", "--region", default=None,
        help="Site/building such as fra12; NCP region is derived as fra.",
    )
    parser.add_argument(
        "--rack", "--racks", dest="racks", action="append", default=[],
        help=("Rack numbers; repeat the option or use comma/space-separated values. "
              "Numeric ranges such as 0706-0708 are also supported."),
    )

    args = parser.parse_args()

    DEFAULT_CMDS = {
        "arista": "show interfaces description | grep gpu",
        "nvidia": "nv show interface description | grep -E 'compute|gpu'",
    }
    cmd = args.cmd or DEFAULT_CMDS[args.vendor]

    # Build host list from existing inputs first (no behavior change)
    host_entries: List[Tuple[str, int]] = []
    if args.device:
        host_entries.extend(args.device)
    if args.device_file:
        host_entries.extend(load_hosts_from_file(args.device_file))
    for pat in (args.device_pattern or []):
        for host in expand_bracket_range(pat.strip()):
            host_entries.append((host, 22))

    # ADD: rack mode (additive)
    rack_values = expand_rack_values(args.racks)
    if rack_values:
        if not args.region:
            parser.error("--rack/--racks requires --region/-r")
        try:
            host_entries.extend(
                load_rack_hosts_from_ncpcli(args.region, rack_values, args.vendor)
            )
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))

    # Optional: de-dupe to avoid running the same host twice
    host_entries = list(dict.fromkeys(host_entries))

    if not host_entries:
        parser.error("Provide at least one --device, --device-pattern, --device-file, or --rack/--racks")

    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")

    devices = [Device(vendor=args.vendor, host=h, port=p) for (h, p) in host_entries]

    results_ok: List[str] = []
    results_fail: List[str] = []

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(devices))) as ex:
        future_map = {
            ex.submit(process_device, dev, username, password, cmd, args.action, args.timeout, args.debug_log): dev
            for dev in devices
        }
        for fut in as_completed(future_map):
            dev = future_map[fut]
            try:
                results_ok.append(fut.result())
            except Exception as e:
                failure = f"{dev.host}: FAIL ({type(e).__name__}: {e})"
                results_fail.append(failure)
                progress(dev.host, f"FAIL - {type(e).__name__}: {e}")

    print("\n=== Summary ===")
    for line in sorted(results_ok):
        print(line)
    for line in sorted(results_fail):
        print(line)
    print(
        f"\nVerification result: {len(results_ok)} passed, "
        f"{len(results_fail)} failed"
    )
    return 1 if results_fail else 0
        
if __name__ == "__main__":
    sys.exit(main())
