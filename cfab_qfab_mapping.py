'''
cfab_qfab_mapping.py

This script extracts and analyzes fabric mappings between QFAB/GFAB and CFAB blocks from OCI rackmap files.
It is tailored for identifying shared (1:1, 1:N, N:1) fabric block relationships within a specific building and computing versioned mapping ratios.

Key Features:
-------------
- Scans the `autonet-rackmaps` directory for the appropriate rackmap file.
- Accepts user input for a building name (e.g., iad49) and a block number or range.
- Restricts analysis to blocks within the specified building only.
- Extracts fabric connection mappings (CFAB, QFAB, GFAB) and associated platform data.
- Applies prioritized version detection logic:
    - CFAB: prefers `cfab2.0` > `cfab1.0` > unknown
    - QFAB/GFAB: prefers `qfab3.0` > `qfab2.1` > `qfab2.0` > `qfab1.0` > `gfab1.0` > unknown
- Merges multiple records per block-pair to avoid redundancy.
- Computes and reports QFAB:CFAB block ratios dynamically.
- Outputs results to a clean Excel file (`cfab_qfab_mappings.xlsx`) and pretty terminal view.

Requirements:
-------------
- Python 3.x
- Packages: pandas, openpyxl (auto-installed if missing)

Usage:
------
1. Ensure you have access to Oracle's internal Bitbucket repository:
   ssh://git@bitbucket.oci.oraclecorp.com:7999/netauto/autonet-rackmaps.git

2. Run the script from the command line:
   ```bash
   python3 cfab_qfab_mapping.py
'''
import json
import re
import subprocess
import pandas as pd
from collections import defaultdict
from pathlib import Path


def ensure_rackmap_dir():
    rackmap_path = Path.home() / "autonet" / "autonet-rackmaps"
    if rackmap_path.exists():
        print("Rackmap directory found.")
        return rackmap_path

    print(f"Directory {rackmap_path} does not exist.")
    print("Options:\n1. Clone manually\n2. Let script clone via SSH")
    choice = input("Enter choice (1 or 2): ").strip()

    if choice == "2":
        autonet_path = Path.home() / "autonet"
        autonet_path.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "git",
            "clone",
            "ssh://git@bitbucket.oci.oraclecorp.com:7999/netauto/autonet-rackmaps.git"
        ], cwd=autonet_path)
    else:
        print("\nPlease follow these steps to manually clone the repo:")
        print("1. Open terminal and run:")
        print("   cd ~/autonet")
        print("   git clone ssh://git@bitbucket.oci.oraclecorp.com:7999/netauto/autonet-rackmaps.git")
        input("\nPress Enter once you have manually cloned the repository...")

    return rackmap_path


def determine_cfab_version(platform_text):
    platform_text = platform_text.lower()
    if "cfab_v2" in platform_text:
        return "cfab2.0"
    if any(v in platform_text for v in ["cfab1_t1", "cfab_t1", "cfab_t0"]):
        return "cfab1.0"
    if "qfab_cfab_t1" in platform_text:
        return "cfab1.0"
    return "unknown"


def determine_qfab_gfab_version(platform_text):
    if "net.ad_gfab_v1_400_t1_1.01" in platform_text:
        return "gfab1.0"
    if "net.ad_gfab_v1_400_t1_1.04" in platform_text:
        return "gfab1.4"
    if "qfabv2_t1_1.01" in platform_text:
        return "qfab2.0"
    if "qfabv2_t1_1.02" in platform_text:
        return "qfab2.1"
    if any(v in platform_text for v in ["qfabv3_t0", "qfabv3_t1"]):
        return "qfab3.0"
    if "qfab_cfab" in platform_text:
        return "qfab1.0"
    return "unknown"


def build_version_lookups(blocks):
    cfab_versions = {}
    qfab_versions = {}

    cfab_priority = {"cfab2.0": 2, "cfab1.0": 1, "unknown": 0}
    qfab_priority = {"qfab3.0": 3, "qfab2.1": 2, "qfab2.0": 2, "qfab1.0": 1, "gfab1.0": 1, "unknown": 0}

    for block in blocks:
        for section in ["all", "__deleted__"]:
            for entry in block.get(section, {}).values():
                platform = entry.get("platform", "").lower()
                fabrics = entry.get("fabrics", [])
                for fabric in fabrics:
                    block_num = fabric.get("fabric_block")
                    if block_num is None:
                        continue

                    if fabric.get("fabric") == "cfab":
                        ver = determine_cfab_version(platform)
                        current = cfab_versions.get(block_num, "unknown")
                        if cfab_priority[ver] > cfab_priority[current]:
                            cfab_versions[block_num] = ver

                    elif fabric.get("fabric") in ["qfab", "gfab"]:
                        ver = determine_qfab_gfab_version(platform)
                        current = qfab_versions.get(block_num, "unknown")
                        if qfab_priority[ver] > qfab_priority[current]:
                            qfab_versions[block_num] = ver

    return cfab_versions, qfab_versions


def extract_fabric_mappings(blocks):
    mappings = []
    for block in blocks:
        for section in ["all", "__deleted__"]:
            for entry in block.get(section, {}).values():
                platform = entry.get("platform", "").lower()
                fabrics = entry.get("fabrics", [])
                cfab_block = next((f.get("fabric_block") for f in fabrics if f.get("fabric") == "cfab"), None)
                qfab_block = next((f.get("fabric_block") for f in fabrics if f.get("fabric") in ["qfab", "gfab"]), None)
                if cfab_block is not None and qfab_block is not None:
                    mappings.append((qfab_block, cfab_block))
    return mappings


def compute_ratios_and_merge(mappings, cfab_versions, qfab_versions):
    q2c = defaultdict(set)
    c2q = defaultdict(set)
    for q, c in mappings:
        q2c[q].add(c)
        c2q[c].add(q)

    seen = set()
    final = []
    for q, c in mappings:
        key = f"block{q}:block{c}"
        if key in seen:
            continue
        seen.add(key)
        final.append({
            "qfab/gfab version": qfab_versions.get(q, "unknown"),
            "cfab version": cfab_versions.get(c, "unknown"),
            "qfab/gfab : cfab block ratio": f"{len(c2q[c])}:{len(q2c[q])}",
            "qfab/gfab : cfab Block Mapping": key
        })
    return final


def save_to_excel(data, filename="cfab_qfab_mappings.xlsx"):
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False)
    print(f"\nExcel mapping saved to {filename}\n")
    if df.empty:
        print("No data to display.")
        return

    headers = {
        "qfab/gfab version": 24,
        "cfab version": 18,
        "qfab:cfab block ratio": 32,
        "qfab:cfab block mapping": 30
    }

    print("  ".join(k.ljust(v) for k, v in headers.items()))
    print("-" * sum(headers.values()))
    for row in data:
        print(f"{row['qfab/gfab version']:<24}{row['cfab version']:<18}{row['qfab/gfab : cfab block ratio']:<32}{row['qfab/gfab : cfab Block Mapping']:<30}")


def main():
    rackmap_dir = ensure_rackmap_dir()

    building = input("Enter building name (e.g. iad49): ").strip().lower()
    block_input = input("Enter block number (e.g. 40 or range 25-30): ").strip()
    if "-" in block_input:
        start, end = map(int, block_input.split("-"))
        blocks_to_check = set(range(start, end + 1))
    else:
        blocks_to_check = {int(block_input)}

    rackmap_file = rackmap_dir / f"{re.sub(r'[0-9]+', '', building)}.rackmap"
    if not rackmap_file.exists():
        print(f"File not found: {rackmap_file}")
        return

    with open(rackmap_file) as f:
        data = json.load(f)

    bldg_prefix = building.replace("iad", "bldg")
    building_obj = next((b for b in data["buildings"] if b.get("name") == bldg_prefix), None)
    if not building_obj:
        print(f"No building {bldg_prefix} found.")
        return

    blocks = building_obj.get("blocks", [])

    cfab_versions, qfab_versions = build_version_lookups(blocks)
    mappings = extract_fabric_mappings(blocks)

    # Filter only mappings that involve selected input blocks
    filtered = [(q, c) for q, c in mappings if q in blocks_to_check or c in blocks_to_check]
    final = compute_ratios_and_merge(filtered, cfab_versions, qfab_versions)
    save_to_excel(final)


if __name__ == "__main__":
    main()