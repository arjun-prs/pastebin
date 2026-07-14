#!/usr/bin/env python3
import argparse
import json
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import re


def run_command(argv: Sequence[str]) -> Optional[str]:
    """
    Run ncpcli safely (no shell). Returns stdout string, or None on error.
    """
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        print(f"Error executing command:\n{err}")
        return None
    return (p.stdout or "").strip()


def get_nested(obj: Dict[str, Any], parent: Optional[str], key: str, default: str = "") -> Any:
    if parent is None:
        return obj.get(key, default)
    return (obj.get(parent) or {}).get(key, default)


def to_str(v: Any) -> str:
    return "" if v is None else str(v)


def derive_block(device: Dict[str, Any], fallback: str = "") -> str:
    """
    Best-effort block derivation from uid like 'bldg5-block5-rack3-qfabt08' -> 'bldg5-block5'
    """
    uid = to_str(device.get("uid", ""))
    if uid:
        parts = uid.split("-")
        return "-".join(parts[:2]) if len(parts) >= 2 else uid
    return fallback


def pick_elevation(device: Dict[str, Any]) -> Any:
    """
    Best-effort elevation lookup; adjust keys if your JSON uses a specific one.
    """
    for k in ("elevation", "rack_elevation", "u", "rack_u", "ru"):
        if device.get(k) is not None:
            return device.get(k)
    # some payloads store location.elevation
    loc = device.get("location") or {}
    for k in ("elevation", "u", "ru"):
        if loc.get(k) is not None:
            return loc.get(k)
    return ""


def print_pipe_table(headers: List[str], rows: List[List[Any]]) -> None:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(to_str(cell)))

    def fmt_row(cells: List[Any], center: bool = False) -> str:
        rendered = []
        for i, c in enumerate(cells):
            s = to_str(c)
            rendered.append(s.center(widths[i]) if center else s.ljust(widths[i]))
        return "| " + " | ".join(rendered) + " |"

    print(fmt_row(headers, center=True))
    print("|-" + "-|-".join("-" * w for w in widths) + "-|")
    for r in rows:
        print(fmt_row(r, center=False))


def normalize_devices(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def build_rows_for_rack(devices: List[Dict[str, Any]], bldg: str, rack: str) -> Tuple[List[str], List[List[Any]]]:
    headers = ["name", "deployment_group", "fabric_instance", "fabric_name", "plane", "rack", "elevation", "block"]

    rows: List[List[Any]] = []
    for d in devices:
        name = to_str(d.get("name", ""))
        if "netpdu" in name.lower():
            continue

        rows.append([
            d.get("name", ""),
            get_nested(d, "topology", "deployment_group_instance", ""),
            get_nested(d, "topology", "fabric_instance", ""),
            get_nested(d, "topology", "fabric_name", ""),
            get_nested(d, "topology", "plane_instance", ""),
            rack,
            pick_elevation(d),
            derive_block(d, fallback=bldg),
        ])
    return headers, rows


def build_rows_for_device(devices: List[Dict[str, Any]]) -> Tuple[List[str], List[List[Any]]]:
    headers = ["name", "deployment_group", "fabric_instance", "fabric_name", "plane", "rack", "elevation", "block"]

    rows: List[List[Any]] = []
    for d in devices:
        name = to_str(d.get("name", ""))
        if "netpdu" in name.lower():
            continue

        rows.append([
            d.get("name", ""),
            get_nested(d, "topology", "deployment_group_instance", ""),
            get_nested(d, "topology", "fabric_instance", ""),
            get_nested(d, "topology", "fabric_name", ""),
            get_nested(d, "topology", "plane_instance", ""),
            get_nested(d, "location", "rack", ""),
            get_nested(d, "location", "elevation", pick_elevation(d)),
            get_nested(d, "location", "block", derive_block(d, fallback="")),
        ])
    return headers, rows


def get_deployment_group_info_by_rack(region: str, bldg: str, rack: str) -> None:
    argv = [
        "ncpcli", "-r", region,
        "plan", "operations", "get-devices-by-rack",
        "--bldg", bldg,
        "--rack-number", rack,
    ]
    out = run_command(argv)
    if not out:
        return

    devices = normalize_devices(json.loads(out))
    headers, rows = build_rows_for_rack(devices, bldg=bldg, rack=rack)
    print_pipe_table(headers, rows)


def get_deployment_group_info_by_device(region: str, device_name: str) -> None:
    argv = [
        "ncpcli", "-r", region,
        "plan", "operations", "get-device-by-name",
        f"--device-name={device_name}",
    ]
    out = run_command(argv)
    if not out:
        return

    devices = normalize_devices(json.loads(out))
    headers, rows = build_rows_for_device(devices)
    print_pipe_table(headers, rows)


def parse_bldg(region: str):
    """
    Examples:
      aga5  -> region_code=aga, bldg=aga5
    """
    m = re.fullmatch(r"([A-Za-z]{3})(\d+)", region.strip())
    if not m:
        raise ValueError("Region must be 3 letters followed by digits (e.g., aga5, cwl15)")
    region_code = m.group(1).lower()
    bldg = f"{region_code}{m.group(2)}"
    return region_code, bldg


def main() -> int:
    parser = argparse.ArgumentParser(description="Print deployment-group/topology info.")

    # Keep -r as region (and derive bldg from it)
    parser.add_argument(
        "-r", "--region",
        required=True,
        help="Region/building code (e.g., aga5, cwl15). Region=first 3 letters; bldg=full value.",
    )

    # Engineer picks ONE: device OR rack-number
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "-d", "--device",
        dest="device_name",
        help="Device name (e.g., aga5-q2-p1-t0-r29)",
    )
    mode.add_argument(
        "-rack", "--rack",
        dest="rack_number",
        help="Rack number (e.g., 0604)",
    )

    args = parser.parse_args()

    try:
        region_code, bldg = parse_bldg(args.region)
    except ValueError as e:
        parser.error(str(e))

    if args.device_name:
        get_deployment_group_info_by_device(region_code, args.device_name)
    else:
        # rack mode
        get_deployment_group_info_by_rack(region_code, bldg, args.rack_number)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())