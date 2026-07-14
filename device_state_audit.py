#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import shlex
import subprocess
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


TRACKED_STATES = ("deployed", "new", "in-service")
ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")
SITE_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
REGION_RE = re.compile(r"^[A-Za-z]+$")
QFAB_TIERS = ("t0", "t1", "t2")
DEFAULT_INVENTORY_DIR = Path(__file__).resolve().parent
DEFAULT_QFAB_INSTANCE = 3
MIN_DG_RACK_COLUMN_WIDTH = 36
MAX_DG_RACK_COLUMN_WIDTH = 72
MIN_SUMMARY_PG_COLUMN_WIDTH = 32
MAX_SUMMARY_PG_COLUMN_WIDTH = 88


@dataclass(frozen=True)
class Device:
    name: str
    role: str
    model: str
    state: str
    ad: str
    location: str
    automation_state: str

    @property
    def building(self) -> str:
        if ":" in self.location:
            return self.location.split(":", 1)[0]
        match = re.match(r"^([a-z]{3}\d+)", self.name, re.IGNORECASE)
        return match.group(1).lower() if match else "-"

    @property
    def rack(self) -> str:
        parts = self.location.split(":")
        return parts[1] if len(parts) >= 2 else "-"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def split_pipe_table_row(line: str) -> Optional[List[str]]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def parse_current_devices(output: str) -> List[Device]:
    devices: List[Device] = []
    for line in strip_ansi(output).splitlines():
        row = split_pipe_table_row(line)
        if row is None or len(row) < 6:
            continue
        if row[0].lower() == "name":
            continue
        devices.append(
            Device(
                name=row[0],
                role=row[1],
                model=row[2],
                state=row[3].lower(),
                ad=row[4] if len(row) > 4 else "",
                location=row[5] if len(row) > 5 else "",
                automation_state=row[6] if len(row) > 6 else "",
            )
        )
    return devices


def quote_value(value: str) -> str:
    return shlex.quote(value)


def normalize_rack(value: str) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = re.sub(r"\D+", "", text)
    return digits.zfill(4) if digits else ""


def sort_rack(value: str) -> tuple[int, str]:
    text = str(value)
    return (int(text), text) if text.isdigit() else (10**9, text)


def sort_dg(value: str) -> tuple[int, str]:
    text = str(value)
    return (int(text), text) if text.isdigit() else (10**9, text)


def building_number(value: str) -> Optional[int]:
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else None


def parse_site_tag(value: str) -> Optional[tuple[str, str]]:
    text = str(value).strip().lower()
    match = SITE_RE.fullmatch(text)
    if not match:
        return None
    return match.group(1).lower(), text


def normalize_scope_args(args: argparse.Namespace) -> None:
    region: Optional[str] = None
    building: Optional[str] = None

    def set_region(value: str, source: str) -> None:
        nonlocal region
        if region and region != value:
            raise ValueError(f"{source} resolves to region {value!r}, which conflicts with region {region!r}")
        region = value

    def set_building(value: str, source: str) -> None:
        nonlocal building
        if building and building != value:
            raise ValueError(f"{source} resolves to building {value!r}, which conflicts with building {building!r}")
        building = value

    for source, value in (
        ("--site", args.site),
        ("--region/-r", args.region),
        ("--building/-b", args.building),
    ):
        if not value:
            continue
        text = str(value).strip().lower()
        site_parts = parse_site_tag(text)
        if site_parts:
            site_region, site_building = site_parts
            set_region(site_region, source)
            set_building(site_building, source)
            continue

        if source == "--region/-r":
            if not REGION_RE.fullmatch(text):
                raise ValueError(f"{source} must be a region like hsg or a site tag like hsg17")
            set_region(text, source)
            continue

        if source == "--building/-b":
            if text.isdigit() and region:
                text = f"{region}{text}"
            set_building(text, source)

    if building and building.isdigit():
        if not region:
            raise ValueError("--building/-b with only a number requires --region/-r, or pass a site tag like hsg17")
        building = f"{region}{building}"

    if not region:
        raise ValueError("pass --site hsg17, -r hsg17, --building hsg17, or --region hsg")

    args.region = region
    args.building = building


def platform_tier(platform: str) -> Optional[str]:
    lower = platform.lower()
    compact_match = re.search(r"(?:qfab|cfab|gfab)t([012])([_\.-]|$)", lower)
    if compact_match:
        return f"t{compact_match.group(1)}"
    match = re.search(r"(^|[_\.-])t([012])([_\.-]|$)", lower)
    if match:
        return f"t{match.group(2)}"
    return None


def platform_is_cfab(platform: str) -> bool:
    return "cfab" in platform.lower()


def qfab_entries(info: dict, instance: int) -> List[dict]:
    return [
        fabric
        for fabric in info.get("fabrics", [])
        if str(fabric.get("fabric", "")).lower() == "qfab"
        and str(fabric.get("instance")) == str(instance)
    ]


def normalized_autonet_value(value) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


def normalize_dg(value) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = re.sub(r"^(?:DG|PG)", "", text, flags=re.IGNORECASE)
    return str(int(text)) if text.isdigit() else text


def qfab_t0_dg_from_name(name: str) -> str:
    match = re.search(r"-q\d+-(?:p|b)(\d+)-t0-", name, flags=re.IGNORECASE)
    return normalize_dg(match.group(1)) if match else ""


def qfab_instance_from_name(name: str) -> Optional[int]:
    match = re.search(r"-q(\d+)-", name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def load_inventory(inventory_file: str) -> dict:
    inventory_path = Path(os.path.expanduser(inventory_file)).resolve()
    if not inventory_path.exists():
        raise FileNotFoundError(f"Device inventory not found: {inventory_path}")

    with inventory_path.open(encoding="utf-8") as handle:
        inventory = json.load(handle)

    if not isinstance(inventory, dict):
        raise ValueError(f"Device inventory must be a JSON object: {inventory_path}")
    return inventory


def inventory_devices(inventory: dict) -> List[dict]:
    devices = inventory.get("devices", [])
    if not isinstance(devices, list):
        return []
    return [device for device in devices if isinstance(device, dict)]


def inventory_contains_building(inventory: dict, building_name: str) -> bool:
    building_name = building_name.lower()
    if str(inventory.get("building", "")).lower() == building_name:
        return True
    if building_name in {str(key).lower() for key in inventory.get("by_building", {})}:
        return True
    if building_name in {str(key).lower() for key in inventory.get("planar_builds", {})}:
        return True
    return any(str(device.get("building", "")).lower() == building_name for device in inventory_devices(inventory))


def inventory_candidates(region: str, building_name: str, inventory_dir: str) -> List[Path]:
    root = Path(os.path.expanduser(inventory_dir)).resolve()
    names = [
        f"{building_name}_planar_ai2nd_inventory.json",
        f"{building_name}_deployed_device_inventory.json",
        f"{building_name}_inventory.json",
        f"{region.lower()}_planar_ai2nd_inventory.json",
        f"{region.lower()}_deployed_device_inventory.json",
        f"{region.lower()}_inventory.json",
    ]
    candidates = [root / name for name in names]
    candidates.extend(sorted(root.glob(f"{region.lower()}*_inventory.json")))

    seen = set()
    unique: List[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            unique.append(path)
    return unique


def find_inventory_file(region: str, building: int, inventory_file: str, inventory_dir: str) -> Optional[Path]:
    if inventory_file:
        return Path(os.path.expanduser(inventory_file)).resolve()

    building_name = f"{region.lower()}{int(building)}"
    for path in inventory_candidates(region, building_name, inventory_dir):
        if path.name.startswith(f"{building_name}_"):
            return path
        try:
            inventory = load_inventory(str(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if inventory_contains_building(inventory, building_name):
            return path
    return None


def add_dg_racks(dg_to_racks: Dict[str, List[str]], dg_value, racks: Iterable[object]) -> None:
    dg = normalize_dg(dg_value)
    if not dg:
        return
    for rack_value in racks:
        rack = normalize_rack(str(rack_value))
        if rack:
            dg_to_racks.setdefault(dg, []).append(rack)


def racks_from_value(value) -> List[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("qfabt0_racks", "racks", "rack_positions"):
            racks = value.get(key)
            if isinstance(racks, list):
                return racks
    return []


def collect_planar_dg_racks(inventory: dict, building_name: str, dg_to_racks: Dict[str, List[str]]) -> None:
    scopes: List[dict] = [inventory]
    planar_build = inventory.get("planar_builds", {}).get(building_name)
    if isinstance(planar_build, dict):
        scopes.insert(0, planar_build)

    for scope in scopes:
        deployment_groups = scope.get("deployment_groups")
        if isinstance(deployment_groups, dict):
            for dg_key, value in deployment_groups.items():
                if isinstance(value, dict):
                    add_dg_racks(dg_to_racks, value.get("deployment_group_number") or dg_key, racks_from_value(value))
                else:
                    add_dg_racks(dg_to_racks, dg_key, racks_from_value(value))

        for map_name in ("network_racks_and_deployment_groups", "network_racks_and_placement_groups"):
            network_map = scope.get(map_name)
            if not isinstance(network_map, dict):
                continue
            for key in ("qfabt0_deployment_groups", "qfabt0_placement_groups", "qfabt0_by_placement_group"):
                qfabt0_map = network_map.get(key)
                if not isinstance(qfabt0_map, dict):
                    continue
                for dg_key, value in qfabt0_map.items():
                    add_dg_racks(dg_to_racks, dg_key, racks_from_value(value))

        placement_groups = scope.get("placement_groups")
        if isinstance(placement_groups, list):
            for value in placement_groups:
                if isinstance(value, dict):
                    add_dg_racks(
                        dg_to_racks,
                        value.get("placement_group") or value.get("deployment_group_alias"),
                        racks_from_value(value),
                    )


def load_inventory_dg_to_racks(
    region: str,
    building: int,
    inventory_file: str,
    inventory_dir: str,
    live_devices: Sequence[Device],
) -> Dict[str, List[str]]:
    """Load only DG/rack mapping data.

    Device state and deployed/new/in-service counts must come from the live
    ncpcli result. Inventory JSON may be stale and is not a status source.
    """
    building_name = f"{region.lower()}{int(building)}"
    selected_inventory = find_inventory_file(region, building, inventory_file, inventory_dir)
    if selected_inventory is None:
        raise FileNotFoundError(
            f"No local inventory JSON found for {building_name} in {Path(os.path.expanduser(inventory_dir)).resolve()}"
        )

    inventory = load_inventory(str(selected_inventory))
    dg_to_racks: Dict[str, List[str]] = {}
    collect_planar_dg_racks(inventory, building_name, dg_to_racks)

    if not dg_to_racks:
        for device in inventory_devices(inventory):
            if str(device.get("building", "")).lower() != building_name:
                continue
            if str(device.get("role", "")).lower() != "qfabt0":
                continue
            dg = qfab_t0_dg_from_name(str(device.get("name", "")))
            rack = normalize_rack(str(device.get("rack", "")))
            if dg and rack:
                dg_to_racks.setdefault(dg, []).append(rack)

        for device in live_devices:
            if device.building.lower() != building_name:
                continue
            if device.role.lower() != "qfabt0":
                continue
            dg = qfab_t0_dg_from_name(device.name)
            rack = normalize_rack(device.rack)
            if dg and rack:
                dg_to_racks.setdefault(dg, []).append(rack)

    if not dg_to_racks:
        raise ValueError(
            f"No qfab T0 deployment-group rack mapping found in {selected_inventory} "
            f"or live qfabt0 hostnames for {building_name}"
        )

    return {
        dg: sorted(set(racks), key=sort_rack)
        for dg, racks in sorted(dg_to_racks.items(), key=lambda item: sort_dg(item[0]))
    }


def infer_qfab_instance(
    region: str,
    building: int,
    live_devices: Sequence[Device],
    inventory_file: str,
    inventory_dir: str,
) -> Optional[int]:
    building_name = f"{region.lower()}{int(building)}"
    instances: Counter[int] = Counter()

    for device in live_devices:
        if device.building.lower() != building_name:
            continue
        if not device.role.lower().startswith("qfab"):
            continue
        instance = qfab_instance_from_name(device.name)
        if instance is not None:
            instances[instance] += 1

    inventory_path = find_inventory_file(region, building, inventory_file, inventory_dir)
    if inventory_path is not None:
        try:
            inventory = load_inventory(str(inventory_path))
        except (OSError, ValueError, json.JSONDecodeError):
            inventory = {}
        for device in inventory_devices(inventory):
            if str(device.get("building", "")).lower() != building_name:
                continue
            if not str(device.get("role", "")).lower().startswith("qfab"):
                continue
            instance = qfab_instance_from_name(str(device.get("name", "")))
            if instance is not None:
                instances[instance] += 1

    if not instances:
        return None
    return instances.most_common(1)[0][0]


def load_autonet_dg_to_racks(region: str, building: int, instance: int, rackmaps_dir: str) -> Dict[str, List[str]]:
    rackmap_path = Path(os.path.expanduser(rackmaps_dir)).resolve() / f"{region.lower()}.rackmap"
    if not rackmap_path.exists():
        raise FileNotFoundError(f"Autonet rackmap not found: {rackmap_path}")

    with rackmap_path.open(encoding="utf-8") as handle:
        rackmap = json.load(handle)

    building_name = f"bldg{int(building)}"
    dg_to_racks: Dict[str, List[str]] = {}
    for item in rackmap.get("buildings", []):
        if item.get("name") != building_name:
            continue
        for block in item.get("blocks", []):
            for rack, info in block.get("all", {}).items():
                platform = str(info.get("platform", ""))
                if platform_is_cfab(platform):
                    continue
                if platform_tier(platform) != "t0":
                    continue
                entries = qfab_entries(info, int(instance))
                if not entries:
                    continue
                dg = normalized_autonet_value(entries[0].get("fabric_placement_group"))
                if dg:
                    dg_to_racks.setdefault(dg, []).append(normalize_rack(rack))

    if not dg_to_racks:
        raise ValueError(
            f"No qfab T0 deployment-group rack mapping found for {region.lower()}{building} instance {instance}"
        )

    return {
        dg: sorted(set(racks), key=sort_rack)
        for dg, racks in sorted(dg_to_racks.items(), key=lambda item: sort_dg(item[0]))
    }


def load_dg_to_racks(
    region: str,
    building: int,
    instance: Optional[int],
    rackmaps_dir: str,
    inventory_file: str,
    inventory_dir: str,
    live_devices: Sequence[Device],
) -> Dict[str, List[str]]:
    effective_instance = instance or infer_qfab_instance(
        region,
        building,
        live_devices,
        inventory_file,
        inventory_dir,
    )
    inventory_path = find_inventory_file(region, building, inventory_file, inventory_dir)
    if inventory_path is not None:
        return load_inventory_dg_to_racks(region, building, str(inventory_path), inventory_dir, live_devices)
    if effective_instance is None:
        effective_instance = DEFAULT_QFAB_INSTANCE
    return load_autonet_dg_to_racks(region, building, effective_instance, rackmaps_dir)


def dg_for_live_device(device: Device, rack_to_dg: Dict[str, str]) -> str:
    dg = rack_to_dg.get(normalize_rack(device.rack), "")
    if dg:
        return dg
    if device.role.lower() == "qfabt0":
        dg = qfab_t0_dg_from_name(device.name)
        if dg:
            return dg
    return ""


def build_update_device_list_command(args: argparse.Namespace) -> str:
    parts = ["update-device-list"]
    if args.device_pattern:
        parts.extend(["--device-names-matching", quote_value(args.device_pattern)])
    if args.building:
        parts.extend(["--building", quote_value(args.building)])
    for role in args.role or []:
        parts.extend(["--role", quote_value(role)])
    return " ".join(parts)


def run_ncpcli(args: argparse.Namespace) -> List[Device]:
    cmd = [args.ncpcli_command, "-r", args.region]
    if args.connection_methods:
        cmd.extend(["--connection-methods", args.connection_methods])
    cmd.append("interactive")

    update_command = build_update_device_list_command(args)
    input_text = f"{update_command}\ncurrent-devices -va\nexit\n"
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=args.timeout,
    )
    if result.returncode != 0:
        details = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part.strip())
        raise RuntimeError(f"ncpcli failed with exit {result.returncode}\n{details}")

    devices = parse_current_devices(result.stdout)
    if not devices:
        tail = "\n".join(strip_ansi(result.stdout).splitlines()[-25:])
        raise RuntimeError(
            "No device rows were parsed from ncpcli current-devices output.\n"
            f"Selection command: {update_command}\n"
            f"Output tail:\n{tail}"
        )
    return devices


def state_bucket_count(devices: Iterable[Device]) -> Dict[str, int]:
    counts = Counter(device.state for device in devices)
    bucketed = {state: counts.get(state, 0) for state in TRACKED_STATES}
    bucketed["other"] = sum(count for state, count in counts.items() if state not in TRACKED_STATES)
    return bucketed


def state_for_devices(devices: Sequence[Device]) -> str:
    if not devices:
        return "not_deployed"
    counts = Counter(device.state for device in devices)
    total = len(devices)
    if counts.get("deployed", 0) == total:
        return "deployed"
    if counts.get("new", 0) == total:
        return "not_deployed"
    if counts.get("in-service", 0) == total:
        return "in-service"
    return "partial"


def summary_state_for_devices(devices: Sequence[Device]) -> str:
    if not devices:
        return "new"
    counts = Counter(device.state for device in devices)
    total = len(devices)
    if counts.get("deployed", 0) == total:
        return "deployed"
    if counts.get("in-service", 0):
        return "in-service"
    if counts.get("new", 0):
        return "new"
    return "in-service"


def roles_label(roles: Optional[Sequence[str]]) -> str:
    if not roles:
        return "device"
    if len(roles) == 1:
        return roles[0]
    return "/".join(roles)


def parsed_scope_label(args: argparse.Namespace, devices: Sequence[Device]) -> str:
    if args.building:
        return args.building
    buildings = sorted({device.building for device in devices if device.building != "-"})
    if len(buildings) == 1:
        return buildings[0]
    return args.region


def wrapped_csv_items(items: Sequence[str], width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for item in items:
        candidate = item if not current else f"{current},{item}"
        if current and len(candidate) > width:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or ["-"]


def other_states_text(counts: Counter[str]) -> str:
    return ",".join(
        f"{state}:{count}"
        for state, count in sorted(counts.items())
        if state != "deployed"
    ) or "-"


def dg_rack_column_width(dg_width: int, state_width: int, deployed_width: int, other_width: int) -> int:
    terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    spacing_width = 8
    available = terminal_width - dg_width - state_width - deployed_width - other_width - spacing_width
    return max(MIN_DG_RACK_COLUMN_WIDTH, min(MAX_DG_RACK_COLUMN_WIDTH, available))


def print_dg_state_for_building(
    region: str,
    building: str,
    instance: int,
    rackmaps_dir: str,
    inventory_file: str,
    inventory_dir: str,
    devices: Sequence[Device],
    role_text: str,
    include_building_in_title: bool,
) -> None:
    build_num = building_number(building)
    if build_num is None:
        raise RuntimeError(f"Cannot determine numeric building from {building!r}")

    dg_to_racks = load_dg_to_racks(
        region,
        build_num,
        instance,
        rackmaps_dir,
        inventory_file,
        inventory_dir,
        devices,
    )
    rack_to_dg = {rack: dg for dg, racks in dg_to_racks.items() for rack in racks}

    by_dg: Dict[str, List[Device]] = defaultdict(list)
    unknown_devices: List[Device] = []
    for device in devices:
        dg = dg_for_live_device(device, rack_to_dg)
        if dg:
            by_dg[dg].append(device)
        else:
            unknown_devices.append(device)

    title = f"DG deployment state from live NCP {role_text} rows"
    if include_building_in_title:
        title = f"{building} {title}"
    print(f"\n{title}")

    rows = []
    for dg in sorted(dg_to_racks, key=sort_dg):
        dg_devices = by_dg.get(dg, [])
        counts = Counter(device.state for device in dg_devices)
        deployed = counts.get("deployed", 0)
        total = len(dg_devices)
        rows.append(
            {
                "dg": f"DG{dg}",
                "racks": dg_to_racks[dg],
                "state": state_for_devices(dg_devices),
                "deployed_total": f"{deployed}/{total}",
                "other_states": other_states_text(counts),
            }
        )

    dg_width = max(4, *(len(row["dg"]) for row in rows))
    state_width = max(len("state"), *(len(row["state"]) for row in rows))
    deployed_width = max(len("deployed/total"), *(len(row["deployed_total"]) for row in rows))
    other_width = max(len("other_states"), *(len(row["other_states"]) for row in rows))
    rack_width = dg_rack_column_width(dg_width, state_width, deployed_width, other_width)

    header_line = (
        f"{'DG':<{dg_width}}  {'racks':<{rack_width}}  {'state':<{state_width}}  "
        f"{'deployed/total':>{deployed_width}}  {'other_states':<{other_width}}"
    )
    print(header_line.rstrip())
    print(
        f"{'-' * dg_width}  {'-' * rack_width}  {'-' * state_width}  "
        f"{'-' * deployed_width}  {'-' * other_width}"
    )
    for row in rows:
        rack_lines = wrapped_csv_items(row["racks"], rack_width)
        row_line = (
            f"{row['dg']:<{dg_width}}  {rack_lines[0]:<{rack_width}}  "
            f"{row['state']:<{state_width}}  {row['deployed_total']:>{deployed_width}}  "
            f"{row['other_states']:<{other_width}}"
        )
        print(row_line.rstrip())
        for rack_line in rack_lines[1:]:
            continuation_line = (
                f"{'':<{dg_width}}  {rack_line:<{rack_width}}  "
                f"{'':<{state_width}}  {'':>{deployed_width}}  {'':<{other_width}}"
            )
            print(continuation_line.rstrip())

    if unknown_devices:
        counts = Counter(device.state for device in unknown_devices)
        deployed = counts.get("deployed", 0)
        total = len(unknown_devices)
        other_states = other_states_text(counts)
        racks = ",".join(sorted({device.rack for device in unknown_devices}, key=sort_rack))
        print("\nUnmapped racks not in DG rackmap")
        print(
            f"state={state_for_devices(unknown_devices)} "
            f"deployed/total={deployed}/{total} "
            f"other_states={other_states}"
        )
        for line in textwrap.wrap(f"racks={racks}", width=120, subsequent_indent="      "):
            print(line)


def collect_dg_devices_for_building(
    region: str,
    building: str,
    instance: int,
    rackmaps_dir: str,
    inventory_file: str,
    inventory_dir: str,
    devices: Sequence[Device],
) -> Dict[str, List[Device]]:
    build_num = building_number(building)
    if build_num is None:
        raise RuntimeError(f"Cannot determine numeric building from {building!r}")

    dg_to_racks = load_dg_to_racks(
        region,
        build_num,
        instance,
        rackmaps_dir,
        inventory_file,
        inventory_dir,
        devices,
    )
    rack_to_dg = {rack: dg for dg, racks in dg_to_racks.items() for rack in racks}
    by_dg: Dict[str, List[Device]] = {dg: [] for dg in dg_to_racks}
    for device in devices:
        dg = dg_for_live_device(device, rack_to_dg)
        if dg:
            by_dg[dg].append(device)
    return by_dg


def print_summary_table(groups: Dict[str, List[str]]) -> None:
    state_width = len("in-service")
    terminal_width = shutil.get_terminal_size(fallback=(100, 24)).columns
    available_pg_width = terminal_width - state_width - 2
    pg_width = max(
        len("Placement Groups"),
        MIN_SUMMARY_PG_COLUMN_WIDTH,
        min(MAX_SUMMARY_PG_COLUMN_WIDTH, available_pg_width),
    )

    print(f"\n{'State':<{state_width}}  {'Placement Groups':<{pg_width}}")
    print(f"{'━' * state_width}  {'━' * pg_width}")
    for index, state in enumerate(("deployed", "in-service", "new")):
        pg_lines = textwrap.wrap(
            ", ".join(groups[state]) or "-",
            width=pg_width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        print(f"{state:<{state_width}}  {pg_lines[0]:<{pg_width}}".rstrip())
        for pg_line in pg_lines[1:]:
            print(f"{'':<{state_width}}  {pg_line:<{pg_width}}".rstrip())
        if index < 2:
            print(f"{'─' * state_width}  {'─' * pg_width}")


def print_dg_summary(args: argparse.Namespace, devices: Sequence[Device]) -> None:
    by_building: Dict[str, List[Device]] = defaultdict(list)
    for device in devices:
        by_building[device.building].append(device)

    multiple_buildings = len(by_building) > 1
    for building in sorted(by_building, key=lambda value: (building_number(value) or 10**9, value)):
        by_dg = collect_dg_devices_for_building(
            args.region,
            building,
            args.instance,
            args.rackmaps_dir,
            args.inventory_file,
            args.inventory_dir,
            by_building[building],
        )
        groups = {"deployed": [], "in-service": [], "new": []}
        for dg in sorted(by_dg, key=sort_dg):
            groups[summary_state_for_devices(by_dg[dg])].append(f"PG{dg}")

        if multiple_buildings:
            print(f"\n{building}")
        print_summary_table(groups)


def print_dg_state(args: argparse.Namespace, devices: Sequence[Device]) -> None:
    by_building: Dict[str, List[Device]] = defaultdict(list)
    for device in devices:
        by_building[device.building].append(device)

    multiple_buildings = len(by_building) > 1
    role_text = roles_label(args.role)
    for building in sorted(by_building, key=lambda value: (building_number(value) or 10**9, value)):
        print_dg_state_for_building(
            args.region,
            building,
            args.instance,
            args.rackmaps_dir,
            args.inventory_file,
            args.inventory_dir,
            by_building[building],
            role_text,
            multiple_buildings,
        )


def print_count_table(title: str, counts: Dict[str, int]) -> None:
    print(f"\n{title}")
    print("state       count")
    print("----------  -----")
    for state in TRACKED_STATES:
        print(f"{state:<10}  {counts.get(state, 0):>5}")
    other = counts.get("other", 0)
    if other:
        print(f"{'other':<10}  {other:>5}")


def print_group_table(title: str, devices: Sequence[Device], key_name: str) -> None:
    grouped: Dict[str, List[Device]] = defaultdict(list)
    for device in devices:
        key = getattr(device, key_name) or "-"
        grouped[key].append(device)

    print(f"\n{title}")
    print("name                  total  deployed    new  in-service  other")
    print("--------------------  -----  --------  -----  ----------  -----")
    for key in sorted(grouped):
        group_devices = grouped[key]
        counts = state_bucket_count(group_devices)
        print(
            f"{key:<20}  {len(group_devices):>5}  "
            f"{counts['deployed']:>8}  {counts['new']:>5}  "
            f"{counts['in-service']:>10}  {counts['other']:>5}"
        )


def print_device_details(devices: Sequence[Device], states: Sequence[str]) -> None:
    selected = [
        device
        for device in devices
        if not states or device.state in states
    ]
    if not selected:
        return

    print("\nDevice details")
    print("state       building  rack  role       device")
    print("----------  --------  ----  ---------  ------------------------------")
    for device in sorted(selected, key=lambda dev: (dev.state, dev.building, dev.rack, dev.role, dev.name)):
        print(
            f"{device.state:<10}  {device.building:<8}  {device.rack:<4}  "
            f"{device.role:<9}  {device.name}"
        )


def normalize_states(raw_states: Optional[str]) -> List[str]:
    if not raw_states:
        return list(TRACKED_STATES)
    return [state.strip().lower() for state in re.split(r"[,\s]+", raw_states) if state.strip()]


def should_print_dg_state(args: argparse.Namespace) -> bool:
    roles = [role.strip().lower() for role in args.role or [] if role.strip()]
    return roles == ["qfabt0"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize deployed/new/in-service device states from ncpcli for any region."
    )
    parser.add_argument(
        "-r",
        "--region",
        help="NCP region or site tag, for example hsg or hsg17.",
    )
    parser.add_argument(
        "-b",
        "--building",
        help="Optional building/site filter, for example hsg17 or aga5. A numeric value requires --region.",
    )
    parser.add_argument(
        "--site",
        help="Site/building tag, for example hsg17 or jbp15. Equivalent to passing that site tag to -r/--region.",
    )
    parser.add_argument(
        "--device-pattern",
        default="*",
        help='Device-name pattern passed to ncpcli. Default: "*".',
    )
    parser.add_argument(
        "--role",
        action="append",
        help="Optional role filter. Repeat for multiple roles, for example --role qfabt0 --role qfabt1.",
    )
    parser.add_argument(
        "-i",
        "--instance",
        type=int,
        help=(
            "QFAB instance used for DG/rack lookup in autonet-rackmaps. "
            f"Default: infer from live qfab hostnames, then fall back to {DEFAULT_QFAB_INSTANCE}."
        ),
    )
    parser.add_argument(
        "--rackmaps-dir",
        default="~/autonet/autonet-rackmaps",
        help=(
            "Directory containing <region>.rackmap files for non-IAD regions. "
            "Default: ~/autonet/autonet-rackmaps."
        ),
    )
    parser.add_argument(
        "--inventory-file",
        default="",
        help=(
            "Specific inventory JSON to use only for qfabt0 DG/rack mapping. "
            "Device state is always read from live ncpcli output. By default, "
            "the script selects a matching inventory from --inventory-dir."
        ),
    )
    parser.add_argument(
        "--inventory-dir",
        default=str(DEFAULT_INVENTORY_DIR),
        help=(
            "Directory containing local inventory JSON files for DG/rack mapping only. "
            f"Default: {DEFAULT_INVENTORY_DIR}."
        ),
    )
    parser.add_argument("--ncpcli-command", default="ncpcli")
    parser.add_argument("--connection-methods")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--workers", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--counts",
        action="store_true",
        help="Also print generic state-count summaries by building and role.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print placement-group state summary instead of detailed DG state rows. Requires --role qfabt0.",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print matching device names after the summary.",
    )
    parser.add_argument(
        "--detail-states",
        help="Comma/space separated states for --details. Default: deployed,new,in-service.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        normalize_scope_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    devices = run_ncpcli(args)

    if args.summary and not should_print_dg_state(args):
        raise SystemExit("--summary requires --role qfabt0")

    if should_print_dg_state(args):
        scope_text = parsed_scope_label(args, devices)
        role_text = roles_label(args.role)
        row_text = f"{role_text} device" if role_text != "device" else "device"
        print(f"Parsed {len(devices)} live {scope_text} {row_text} rows from NCP.")
        if args.summary:
            print_dg_summary(args, devices)
        else:
            print_dg_state(args, devices)
    else:
        update_command = build_update_device_list_command(args)
        print(f"Region: {args.region}")
        print(f"Selection command: {update_command}")
        print(f"Parsed devices: {len(devices)}")

        print_count_table("State counts", state_bucket_count(devices))
        print_group_table("State counts by building", devices, "building")
        print_group_table("State counts by role", devices, "role")

    if args.counts and should_print_dg_state(args):
        print_count_table("State counts", state_bucket_count(devices))
        print_group_table("State counts by building", devices, "building")
        print_group_table("State counts by role", devices, "role")

    if args.details:
        print_device_details(devices, normalize_states(args.detail_states))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
