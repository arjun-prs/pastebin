#!/usr/bin/env python3

import argparse
import csv
import json
import re
from pathlib import Path


REGION_RE = re.compile(r"([a-zA-Z]+)")
BUILDING_RE = re.compile(r"(\d+)")
NATURAL_SPLIT_RE = re.compile(r"(\d+)")
NON_DIGIT_RE = re.compile(r"\D")
DG_PREFIX_RE = re.compile(r"\bdg\s*-?\s*")
DG_TO_RANGE_RE = re.compile(r"\s+to\s+")
DG_TOKEN_RE = re.compile(r"[,\s]+")
PLATFORM_QFAB_TIER_RE = re.compile(r"(?:qfab|planar_qfab)_t([01])")
PLATFORM_TIER_RE = re.compile(r"[_\.-]t([01])[_\.-]")


def get_region(build):
    match = REGION_RE.match(build)
    if not match:
        raise SystemExit(f"Cannot find region from build name: {build}")
    return match.group(1).lower()


def get_autonet_file(build):
    region = get_region(build)
    return Path.home() / "autonet" / "autonet-plans" / region / f"{build}-cables.csv"


def get_building(build):
    match = BUILDING_RE.search(build)
    if not match:
        raise SystemExit(f"Cannot find building number from build name: {build}")
    return match.group(1)


def get_rackmap_file(build):
    region = get_region(build)
    return Path.home() / "autonet" / "autonet-rackmaps" / f"{region}.rackmap"


def natural_sort(value):
    parts = NATURAL_SPLIT_RE.split(str(value))
    return [int(part) if part.isdigit() else part for part in parts]


def rack_sort(value):
    digits = NON_DIGIT_RE.sub("", str(value))
    return int(digits) if digits else 999999


def parse_args():
    parser = argparse.ArgumentParser(
        description="Get T0/T1 rack numbers from an autonet cables CSV file."
    )
    parser.add_argument("build", help="Build name, example: iad77, iad60, phx20")
    parser.add_argument(
        "--tier",
        choices=["t0", "t1", "both"],
        default="both",
        help="Which tier to show. Default: both",
    )
    parser.add_argument(
        "--csv",
        help="Optional direct CSV path. Default: ~/autonet/autonet-plans/<region>/<build>-cables.csv",
    )
    parser.add_argument(
        "--qfab-only",
        action="store_true",
        help="Only include QFAB devices like iad77-q2-p1-t1-r1",
    )
    parser.add_argument(
        "--dg",
        help="Filter by deployment group / placement group. Examples: 1, dg-1, 151, 151-154",
    )
    parser.add_argument(
        "--instance",
        help="Optional QFAB instance filter from rackmap, example: 2",
    )
    parser.add_argument(
        "--devices",
        action="store_true",
        help="Print device to rack mapping instead of only rack numbers",
    )
    parser.add_argument(
        "--format",
        choices=["list", "table", "both"],
        default="list",
        help="Output style for rack numbers. Default: list",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print source file and matched device count before the output",
    )
    return parser.parse_args()


def host_matches(host, build, tier, qfab_only):
    if not host:
        return False

    if qfab_only:
        pattern = rf"^{re.escape(build)}-q\d+-p\d+-{tier}-r\d+$"
    else:
        pattern = rf"^{re.escape(build)}-.+-{tier}-r\d+$"

    return re.match(pattern, host, re.IGNORECASE) is not None


def build_host_patterns(build, tiers, qfab_only):
    device_segment = r"q\d+-p\d+" if qfab_only else r".+"
    return {
        tier: re.compile(
            rf"^{re.escape(build)}-{device_segment}-{tier}-r\d+$",
            re.IGNORECASE,
        )
        for tier in tiers
    }


def parse_dg_values(value):
    if not value:
        return []

    text = str(value).lower()
    text = DG_PREFIX_RE.sub("", text)
    text = DG_TO_RANGE_RE.sub("-", text)
    text = text.replace(";", ",")

    dgs = []
    seen = set()
    for token in DG_TOKEN_RE.split(text):
        token = token.strip()
        if not token:
            continue

        if "-" in token:
            start, end = token.split("-", 1)
            if not start.isdigit() or not end.isdigit():
                raise SystemExit(f"Invalid DG value: {token}")
            start_num = int(start)
            end_num = int(end)
            if end_num < start_num:
                start_num, end_num = end_num, start_num
            values = [str(num) for num in range(start_num, end_num + 1)]
        else:
            if not token.isdigit():
                raise SystemExit(f"Invalid DG value: {token}")
            values = [str(int(token))]

        for dg in values:
            if dg not in seen:
                dgs.append(dg)
                seen.add(dg)

    return dgs


def normalize_rack(value):
    digits = NON_DIGIT_RE.sub("", str(value or ""))
    return digits.lstrip("0") or digits


def platform_tier(platform):
    lower = str(platform or "").lower()
    match = PLATFORM_QFAB_TIER_RE.search(lower)
    if match:
        return f"t{match.group(1)}"
    match = PLATFORM_TIER_RE.search(lower)
    if match:
        return f"t{match.group(1)}"
    return ""


def load_dg_map(build, instance=None):
    rackmap_file = get_rackmap_file(build)
    if not rackmap_file.exists():
        raise SystemExit(f"Rackmap file not found: {rackmap_file}")

    building_name = f"bldg{get_building(build)}"
    with rackmap_file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    rack_to_dg = {}

    for building in data.get("buildings", []):
        if building.get("name") != building_name:
            continue

        for block in building.get("blocks", []):
            for rack, info in block.get("all", {}).items():
                tier = platform_tier(info.get("platform", ""))
                if tier not in {"t0", "t1"}:
                    continue

                for fabric in info.get("fabrics", []):
                    if str(fabric.get("fabric", "")).lower() != "qfab":
                        continue

                    if instance and str(fabric.get("instance", "")) != str(instance):
                        continue

                    dg = str(fabric.get("fabric_placement_group") or "").strip()
                    if not dg:
                        continue

                    rack_to_dg[(tier, normalize_rack(rack))] = str(int(dg)) if dg.isdigit() else dg

    return rack_to_dg


def get_first_word(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value.split()[0]


def collect_from_csv(csv_file, build, tiers, qfab_only, rack_to_dg=None, dg_filter=None):
    records = {}
    rack_to_dg = rack_to_dg or {}
    dg_filter = set(dg_filter or [])
    host_patterns = build_host_patterns(build, tiers, qfab_only)

    with open(csv_file, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)

        for row in reader:
            for side in ["A", "B"]:
                host = get_first_word(row.get(f"Device{side} Name"))
                if not host:
                    continue

                for tier, pattern in host_patterns.items():
                    if pattern.match(host) is None:
                        continue

                    rack = str(row.get(f"Device{side} Rack") or "").strip()
                    ru = str(row.get(f"Device{side} RU") or "").strip()
                    dtype = str(row.get(f"Device{side} Type") or "").strip()
                    dg = rack_to_dg.get((tier, normalize_rack(rack)), "")

                    if dg_filter and dg not in dg_filter:
                        break

                    key = (tier, host)
                    if key not in records:
                        records[key] = {
                            "tier": tier,
                            "host": host,
                            "rack": rack,
                            "ru": ru,
                            "type": dtype,
                            "dg": dg,
                        }
                    break

    return list(records.values())


def group_records_by_tier_and_rack(records, tiers):
    grouped = {tier: {} for tier in tiers}
    for rec in records:
        tier = rec.get("tier")
        rack = rec.get("rack")
        if tier not in grouped or not rack:
            continue
        entry = grouped[tier].setdefault(rack, {"devices": [], "dgs": set()})
        entry["devices"].append(rec["host"])
        if rec.get("dg"):
            entry["dgs"].add(rec["dg"])
    return grouped


def print_racks(build, records, tiers, grouped=None):
    grouped = grouped if grouped is not None else group_records_by_tier_and_rack(records, tiers)
    for tier in tiers:
        racks = sorted(grouped.get(tier, {}), key=rack_sort)
        print(",".join(f"{build}:{rack}" for rack in racks))


def device_range(devices):
    if not devices:
        return ""
    devices = sorted(devices, key=natural_sort)
    if len(devices) == 1:
        return devices[0]
    return f"{devices[0]} to {devices[-1]}"


def print_rack_table(build, records, tiers, grouped=None):
    grouped = grouped if grouped is not None else group_records_by_tier_and_rack(records, tiers)
    table_rows = []
    for tier in tiers:
        for rack in sorted(grouped.get(tier, {}), key=rack_sort):
            rack_group = grouped[tier][rack]
            devices = rack_group["devices"]
            dgs = sorted(rack_group["dgs"])
            table_rows.append(
                {
                    "Tier": tier.upper(),
                    "DG": ",".join(dgs),
                    "Rack": f"{build}:{rack}",
                    "Device Count": str(len(devices)),
                    "Device Range": device_range(devices),
                }
            )

    headers = ["Tier", "DG", "Rack", "Device Count", "Device Range"]
    widths = {
        header: max(len(header), *(len(row[header]) for row in table_rows))
        for header in headers
    }

    border = "+" + "+".join("-" * (widths[header] + 2) for header in headers) + "+"
    header_line = "|" + "|".join(f" {header.center(widths[header])} " for header in headers) + "|"

    print(border)
    print(header_line)
    print(border)
    for row in table_rows:
        print("|" + "|".join(f" {row[header].ljust(widths[header])} " for header in headers) + "|")
    print(border)


def print_devices(records, tiers):
    records_by_tier = {tier: [] for tier in tiers}
    for rec in records:
        if rec.get("tier") in records_by_tier:
            records_by_tier[rec["tier"]].append(rec)

    for tier in tiers:
        tier_records = records_by_tier[tier]
        tier_records.sort(key=lambda rec: natural_sort(rec["host"]))
        print(f"{tier.upper()} devices ({len(tier_records)}):")
        for rec in tier_records:
            dg_part = f" dg={rec.get('dg')}" if rec.get("dg") else ""
            print(f"{rec['host']} rack={rec['rack']} ru={rec['ru']}{dg_part} type={rec['type']}")


def main():
    args = parse_args()
    build = args.build.lower()

    if args.tier == "both":
        tiers = ["t0", "t1"]
    else:
        tiers = [args.tier]

    csv_file = Path(args.csv).expanduser() if args.csv else get_autonet_file(build)
    if not csv_file.exists():
        raise SystemExit(f"CSV file not found: {csv_file}")

    dg_filter = parse_dg_values(args.dg)
    rack_to_dg = load_dg_map(build, args.instance) if args.dg or args.format in ["table", "both"] or args.devices else {}
    records = collect_from_csv(csv_file, build, tiers, args.qfab_only, rack_to_dg, dg_filter)

    if args.verbose:
        print(f"Source: {csv_file}")
        print(f"Matched devices: {len(records)}")

    if args.devices:
        print_devices(records, tiers)
    else:
        grouped = group_records_by_tier_and_rack(records, tiers)
        if args.format in ["list", "both"]:
            print_racks(build, records, tiers, grouped=grouped)
        if args.format == "both":
            print()
        if args.format in ["table", "both"]:
            print_rack_table(build, records, tiers, grouped=grouped)


if __name__ == "__main__":
    main()
