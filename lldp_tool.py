#!/usr/bin/env python3
import argparse
import re
import pexpect
import getpass
from dataclasses import dataclass
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiplaner_racktopo
import subprocess

HOSTKEY_RE = r"Are you sure you want to continue connecting"
USER_RE    = r"(?i)(username|login)[: ]"
PASS_RE    = r"(?i)password.*:\s*$"

ARISTA_PROMPT_RE = r"(?m)^[^\r\n]+(?:\([^)]+\))?[>#]\s*$"
ARISTA_MORE_RE   = r"(?i)(--more--|press any key to continue|press <space> to continue)"

MAX_WORKERS = 8  # default concurrency (always enabled)

@dataclass
class Device:
    vendor: str   # "arista" | "nvidia"
    host: str
    port: int = 22

def connect_ssh(host, username, password, port=22, timeout=30, strict_hostkey="ask", debug_log=None):
    ssh_cmd = f"ssh -o StrictHostKeyChecking={strict_hostkey} -p {port} {username}@{host}"
    child = pexpect.spawn(ssh_cmd, encoding="utf-8", timeout=timeout)
    if debug_log:
        # Note: multi-threaded logging to the same file can interleave lines.
        child.logfile = open(debug_log, "a")

    i = child.expect([HOSTKEY_RE, USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])
    if i == 0:
        child.sendline("yes")
        i = child.expect([USER_RE, PASS_RE, pexpect.TIMEOUT, pexpect.EOF])

    if i == 0:  # username prompt
        child.sendline(username)
        child.expect(PASS_RE)
        child.sendline(password)
    elif i == 1:  # password prompt
        child.sendline(password)
    elif i == 2:
        raise TimeoutError(f"Login timed out connecting to {host}")
    else:
        raise ConnectionError(f"EOF while connecting to {host}")
    return child

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

def process_device(dev: Device, username: str, password: str,
                   discovery_cmd: str, action: str, timeout: int,
                   debug_log: Optional[str]) -> str:
    child = connect_ssh(dev.host, username, password, port=dev.port,
                        timeout=max(timeout, 30), debug_log=debug_log)
    try:
        if dev.vendor == "arista":
            arista_prepare(child, timeout=timeout)
            out = arista_run_command(child, discovery_cmd, timeout=timeout)
            interfaces = parse_interfaces_arista(out)
            #print(f"\n[{dev.host}] matched interfaces: {interfaces}")
            arista_config(child, interfaces, action, timeout=timeout)
            return f"{dev.host}: OK"
        else:
            prompt = nvidia_prepare(child, timeout=timeout)
            out = nvidia_run_command(child, discovery_cmd, prompt, timeout=max(timeout, 180))
            interfaces = parse_interfaces_nvidia(out)
            #print(f"\n[{dev.host}] matched interfaces: {interfaces}")
            nvidia_config(dev, child, prompt, interfaces, action, timeout=max(timeout, 180))
            return f"{dev.host}: OK"
    finally:
        close_session(child)

def main():
    parser = argparse.ArgumentParser()
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

    # racktopo additions (additive)
    parser.add_argument("-r", "--region", default=None, help="Region like aga5")
    parser.add_argument("--rack", default=None, help="Rack number like 0706")

    args = parser.parse_args()

    DEFAULT_CMDS = {
        "arista": "show interface description | grep compute",
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
    if args.rack:
        if not args.region:
            parser.error("--rack requires --region/-r (example: -r aga5 --rack 0706)")

        p = subprocess.run(
            ["python3", "multiplaner_racktopo.py", "-r", args.region, "--rack", args.rack],
            text=True,
            capture_output=True
        )
        if p.returncode != 0:
            raise RuntimeError(f"multiplaner_racktopo.py failed:\n{(p.stderr or '').strip()}")

        table_out = (p.stdout or "").rstrip()
        print("\n============ Rack Topology Table ================")
        print(table_out)

        # Parse device names from pipe table (first column)
        for line in table_out.splitlines():
            line = line.rstrip()
            if not line.startswith("|"):
                continue
            if line.startswith("|-"):
                continue
            if "name" in line and "deployment_group" in line:
                continue

            cols = [c.strip() for c in line.strip("|").split("|")]
            if not cols:
                continue

            name = cols[0]
            if not name or name.lower() == "name":
                continue
            if "netpdu" in name.lower():
                continue
            if "-m" in name.lower():   # keep your filter
                continue

            host_entries.append((name, 22))

    # Optional: de-dupe to avoid running the same host twice
    host_entries = list(dict.fromkeys(host_entries))

    if not host_entries:
        parser.error("Provide at least one --device, --device-pattern, --device-file, or --rack")

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
                results_fail.append(f"{dev.host}: FAIL ({type(e).__name__}: {e})")

    print("\n=== Summary ===")
    for line in sorted(results_ok):
        print(line)
    for line in sorted(results_fail):
        print(line)
        
if __name__ == "__main__":
    main()
