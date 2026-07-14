#!/usr/bin/env python3
"""Rack / Q2-Q3 / T0-T1 / plane-wide linkflap checker and clearer.

What it does:
- Reads switches from the XLSX plan for the selected fabric
- Can filter by rack and/or q2/q3 and/or t0/t1 and/or plane
- Connects to each switch with dssh
- Runs `nv show interface status`
- Detects lines containing `linkflap`
- Optionally clears link flap-protection violations
- Writes CSV and HTML reports
- Prints a bottom-of-run summary including every switch/interface hit

Examples:
    python3 NVIDIA_Link_Flap.py -re aga -n 5 -r 0603 --dry-run
    python3 NVIDIA_Link_Flap.py -re jbp -n 15 -q2 -t0 -p2 --dry-run
    python3 NVIDIA_Link_Flap.py -re aga -n 5 -t1 -p4 --clear
    python3 NVIDIA_Link_Flap.py --xlsx ~/autonet/autonet-plans/aga/aga5-cables.xlsx -q3 --dry-run
    python3 NVIDIA_Link_Flap.py -re jbp -n15 -q 2 -t 1 -p 1 --dry-run

Filter behavior:
- Multiple filters are ANDed together.
- `-q2` and `-q3` are mutually exclusive.
- Rack is optional.
- If no rack is given, the script searches the entire XLSX and filters by the requested name patterns.
- Use `-re/--region` plus `-n/--number` for auto-discovery, or pass `--xlsx` directly.
- Short flags also accept forgiving forms such as `-n15`, `-q 2`, `-t 1`, and `-p 1`.
"""

import argparse
import csv
import datetime as dt
import getpass
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pandas as pd
import pexpect

OUTDIR = Path("linkflap_outputs")
DEBUGDIR = Path("linkflap_debug")
WORKERS = 4
PROBE_TIMEOUT_S = 8
CMD_TIMEOUT_S = 90


def html_escape(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def normalize_region(region: str) -> str:
    s = str(region or "").strip().lower()
    if not s:
        raise ValueError("Region cannot be empty.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", s):
        raise ValueError(f"Invalid region {region!r}. Use letters, numbers, '-' or '_'.")
    return s


def build_fabric_name(region: str, number: int) -> str:
    region_norm = normalize_region(region)
    try:
        fabric_number = int(number)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid fabric number {number!r}.") from exc
    if fabric_number < 0:
        raise ValueError("Fabric number must be 0 or greater.")
    return f"{region_norm}{fabric_number}"


def infer_fabric_from_xlsx(xlsx_path: Path) -> Optional[str]:
    m = re.fullmatch(r"(.+)-cables", xlsx_path.stem, re.IGNORECASE)
    if not m:
        return None
    fabric = m.group(1).strip().lower()
    return fabric or None


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    out: List[Path] = []
    for path in paths:
        expanded = path.expanduser()
        key = str(expanded)
        if key in seen:
            continue
        seen.add(key)
        out.append(expanded)
    return out


def _plans_root_variants(base_path: Path) -> List[Path]:
    base = base_path.expanduser()
    if base.name == "autonet-plans":
        return [base]
    return [base, base / "autonet-plans"]


def candidate_plans_roots(explicit_root: Optional[str]) -> List[Path]:
    home = Path.home()
    base_candidates: List[Path] = []
    if explicit_root:
        base_candidates.append(Path(explicit_root))

    for env_name in ("AUTONET_PLANS_ROOT", "AUTONET_ROOT"):
        env_value = os.getenv(env_name)
        if env_value:
            base_candidates.append(Path(env_value))

    base_candidates.extend(
        [
            home / "autonet-plans",
            home / "autonet",
            home / "tools" / "autonet",
            home / "autonet" / "autonettools" / "autonet",
            home / "tools" / "autonet" / "autonettools" / "autonet",
        ]
    )

    plans_roots: List[Path] = []
    for base in _dedupe_paths(base_candidates):
        plans_roots.extend(_plans_root_variants(base))
    return _dedupe_paths(plans_roots)


def resolve_xlsx_path(
    xlsx_arg: Optional[str],
    fabric: Optional[str],
    region: Optional[str],
    autonet_root: Optional[str],
) -> Path:
    if xlsx_arg:
        xlsx_path = Path(xlsx_arg).expanduser()
        if not xlsx_path.is_file():
            raise FileNotFoundError(f"XLSX not found: {xlsx_path}")
        return xlsx_path.resolve()

    if not fabric or not region:
        raise ValueError("Provide --xlsx or both -re/--region and -n/--number.")

    filename = f"{fabric}-cables.xlsx"
    checked: List[Path] = []
    for plans_root in candidate_plans_roots(autonet_root):
        candidate = plans_root / region / filename
        checked.append(candidate)
        if candidate.is_file():
            return candidate.resolve()

    checked_lines = "\n".join(f"  - {path}" for path in checked)
    raise FileNotFoundError(
        f"Could not find {filename}. Pass --xlsx directly or point --autonet-root, "
        "AUTONET_ROOT, or AUTONET_PLANS_ROOT at your checkout.\n"
        f"Checked:\n{checked_lines}"
    )


def is_target_switch(host: str, fabric: str) -> bool:
    h = (host or "").strip().lower()
    fabric_norm = (fabric or "").strip().lower()
    if not h or not fabric_norm:
        return False
    if "-m1-" in h:
        return False
    return h.startswith(f"{fabric_norm}-q2-") or h.startswith(f"{fabric_norm}-q3-")


def get_device_column_sets(df: pd.DataFrame) -> List[Tuple[str, str, Optional[str]]]:
    column_sets: List[Tuple[str, str, Optional[str]]] = []
    for side in ("A", "B"):
        rack_col = f"Device{side} Rack"
        name_col = f"Device{side} Name"
        ru_col = f"Device{side} RU"
        if rack_col in df.columns and name_col in df.columns:
            column_sets.append((rack_col, name_col, ru_col if ru_col in df.columns else None))
    if not column_sets:
        raise ValueError(f"Missing rack/name columns. Found: {list(df.columns)}")
    return column_sets


def normalize_rack_value(v: Any) -> str:
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = s.split(".")[0]
    return s.zfill(4) if re.fullmatch(r"\d{1,4}", s) else s


def normalize_ru_value(v: Any) -> str:
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = s.split(".")[0]
    return s


def format_ru_label(ru: Any) -> str:
    s = str(ru).strip()
    if not s or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+", s):
        return f"U{s}"
    return s


def format_console_target_label(rack: str, ru: str, host: str) -> str:
    prefix_parts = [part for part in (rack, format_ru_label(ru)) if part]
    prefix = f"{' '.join(prefix_parts)} " if prefix_parts else ""
    return f"{prefix}{host}"


def ru_sort_key(ru: str) -> Tuple[int, str]:
    s = str(ru).strip() if ru is not None else ""
    if s.isdigit():
        return (int(s), s)
    return (10**9, s)


def iter_switch_entries(
    df: pd.DataFrame, fabric: str, column_sets: Optional[List[Tuple[str, str, Optional[str]]]] = None
) -> Iterator[Tuple[str, str, str]]:
    device_columns = column_sets or get_device_column_sets(df)
    for _, row in df.iterrows():
        for rack_col, name_col, ru_col in device_columns:
            sw = str(row.get(name_col, "")).strip()
            if not sw or not is_target_switch(sw, fabric):
                continue
            rack = normalize_rack_value(row.get(rack_col, ""))
            ru = normalize_ru_value(row.get(ru_col, "")) if ru_col else ""
            yield (rack, sw, ru)


def run_dssh_with_password(host: str, remote_cmd: str, password: str, timeout: int) -> str:
    child = pexpect.spawn("dssh", args=[host, remote_cmd], encoding="utf-8", timeout=timeout)
    while True:
        i = child.expect(
            [
                r"(?i)are you sure you want to continue connecting",
                r"(?i)password:",
                pexpect.EOF,
                pexpect.TIMEOUT,
            ]
        )
        if i == 0:
            child.sendline("yes")
        elif i == 1:
            child.sendline(password)
        elif i == 2:
            return child.before
        else:
            raise subprocess.TimeoutExpired(cmd=f"dssh {host} {remote_cmd}", timeout=timeout)


def dssh_probe(host: str, password: str) -> bool:
    try:
        out = run_dssh_with_password(host, "echo PROBE_OK", password=password, timeout=PROBE_TIMEOUT_S)
        return "PROBE_OK" in out
    except Exception:
        return False


def switches_for_rack_from_xlsx(
    df: pd.DataFrame,
    rack: str,
    fabric: str,
    column_sets: Optional[List[Tuple[str, str, Optional[str]]]] = None,
) -> List[Tuple[str, str]]:
    rack_norm = normalize_rack_value(rack)
    sw_to_rus: Dict[str, List[str]] = {}
    for entry_rack, sw, ru in iter_switch_entries(df, fabric, column_sets):
        if entry_rack != rack_norm:
            continue
        sw_to_rus.setdefault(sw, [])
        if ru:
            sw_to_rus[sw].append(ru)

    out: List[Tuple[str, str]] = []
    for sw in sorted(sw_to_rus):
        rus = sw_to_rus.get(sw, [])
        out.append((sw, sorted(set(rus), key=ru_sort_key)[0] if rus else ""))
    return out


def parse_linkflap_output(output: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    current_entity = ""
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if line.startswith("Entity:"):
            current_entity = line
            continue
        if "linkflap" in line:
            hits.append((current_entity, line.strip()))
    return hits


def clear_linkflap(host: str, password: str) -> Tuple[bool, str]:
    try:
        out = run_dssh_with_password(
            host,
            "nv action clear system link flap-protection violation",
            password=password,
            timeout=CMD_TIMEOUT_S,
        )
        return True, out.strip() or "OK"
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT (>{CMD_TIMEOUT_S}s)"
    except Exception as e:
        return False, f"ERROR: {e}"


def inspect_switch(host: str, password: str, do_clear: bool) -> Tuple[str, Optional[List[Tuple[str, str]]], Optional[str], Optional[str]]:
    if not dssh_probe(host, password):
        return host, None, None, "UNREACHABLE (dssh probe failed)"

    try:
        raw = run_dssh_with_password(host, "nv show interface status", password=password, timeout=CMD_TIMEOUT_S)
        hits = parse_linkflap_output(raw)
    except subprocess.TimeoutExpired:
        return host, None, None, f"TIMEOUT running nv show interface status (>{CMD_TIMEOUT_S}s)"
    except Exception as e:
        return host, None, None, f"ERROR running nv show interface status: {e}"

    clear_status = "SKIPPED"
    clear_output = ""
    if do_clear and hits:
        ok, msg = clear_linkflap(host, password)
        clear_status = "CLEARED" if ok else "CLEAR_FAILED"
        clear_output = msg
    elif do_clear:
        clear_status = "NO_LINKFLAP"

    return host, hits, clear_status, clear_output


def build_hits_only_html(title: str, generated_utc: str, results: List[Dict[str, Any]], print_friendly: bool) -> str:
    print_css = ""
    if print_friendly:
        print_css = """@page { size: portrait; margin: 10mm; }
@media print { .toolbar { display: none !important; } }"""

    css = f"""
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 16px; color:#111; }}
h2 {{ margin:0 0 6px 0; }}
.sub {{ color:#666; margin-bottom: 12px; font-size: 13px; }}
.card {{ border:1px solid #e6e6e6; border-radius:10px; padding:10px 12px; margin:10px 0; }}
table {{ border-collapse: collapse; width:100%; font-size:12px; margin: 10px 0 18px 0; }}
th, td {{ border:1px solid #e2e2e2; padding:6px 8px; vertical-align: top; }}
th {{ background:#f7f7f7; text-align:left; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; }}
.toolbar {{ margin: 8px 0 12px 0; color:#666; font-size:12px; }}
{print_css}
"""
    sections: List[str] = []
    total_hits = 0
    for r in results:
        hits = r.get("hits") or []
        if not hits:
            continue
        total_hits += len(hits)
        rows = "".join(
            f"<tr><td>{html_escape(entity)}</td><td class='mono'>{html_escape(line)}</td></tr>"
            for entity, line in hits
        )
        sections.append(
            f"""
<div class="card">
  <div><b>{html_escape(r['host'])}</b>{f' • RU {html_escape(r.get("ru", ""))}' if r.get("ru") else ''} — linkflap hits: <b>{len(hits)}</b></div>
  <table>
    <thead><tr><th>Entity</th><th>Matched line</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
"""
        )

    tip = "Print tip: enable Background graphics if your browser supports it." if print_friendly else "Tip: use browser find (Cmd+F)."
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html_escape(title)}</title><style>{css}</style></head>
<body>
<h2>{html_escape(title)}</h2>
<div class="sub">Generated: <b>{html_escape(generated_utc)}</b> • Total linkflap lines: <b>{total_hits}</b></div>
<div class="toolbar">{html_escape(tip)}</div>
{''.join(sections) if sections else '<div>No linkflap hits.</div>'}
</body></html>
"""


def build_full_html(title: str, generated_utc: str, results: List[Dict[str, Any]], print_friendly: bool) -> str:
    print_css = ""
    if print_friendly:
        print_css = """@page { size: portrait; margin: 10mm; }
@media print { .toolbar { display: none !important; } }"""

    css = f"""
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 16px; color:#111; }}
h2 {{ margin:0 0 6px 0; }}
.sub {{ color:#666; margin-bottom: 12px; font-size: 13px; }}
.card {{ border:1px solid #e6e6e6; border-radius:10px; padding:10px 12px; margin:10px 0; }}
table {{ border-collapse: collapse; width:100%; font-size:12px; margin: 10px 0 18px 0; }}
th, td {{ border:1px solid #e2e2e2; padding:6px 8px; vertical-align: top; }}
th {{ background:#f7f7f7; text-align:left; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; }}
.toolbar {{ margin: 8px 0 12px 0; color:#666; font-size:12px; }}
{print_css}
"""

    sections: List[str] = []
    total_hits = 0
    total_cleared = 0
    for r in results:
        hits = r.get("hits") or []
        total_hits += len(hits)
        if r.get("clear_status") == "CLEARED":
            total_cleared += 1

        rows = []
        if r.get("error"):
            rows.append(f"<tr><td colspan='2' class='mono'>ERROR: {html_escape(r['error'])}</td></tr>")
        elif not hits:
            rows.append("<tr><td colspan='2'>No linkflap found</td></tr>")
        else:
            for entity, line in hits:
                rows.append(f"<tr><td>{html_escape(entity)}</td><td class='mono'>{html_escape(line)}</td></tr>")

        clear_status = r.get("clear_status", "")
        clear_output = r.get("clear_output", "")
        clear_line = f"<div>Clear status: <b>{html_escape(clear_status)}</b></div>" if clear_status else ""
        if clear_output:
            clear_line += f"<div class='mono'>{html_escape(clear_output)}</div>"

        sections.append(
            f"""
<div class="card">
  <div><b>{html_escape(r['host'])}</b>{f' • RU {html_escape(r.get("ru", ""))}' if r.get("ru") else ''} — linkflap hits: <b>{len(hits)}</b></div>
  {clear_line}
  <table>
    <thead><tr><th>Entity</th><th>Matched line</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</div>
"""
        )

    tip = "Print tip: enable Background graphics if your browser supports it." if print_friendly else "Tip: use browser find (Cmd+F)."
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html_escape(title)}</title><style>{css}</style></head>
<body>
<h2>{html_escape(title)}</h2>
<div class="sub">Generated: <b>{html_escape(generated_utc)}</b> • Switches with hits: <b>{sum(1 for r in results if (r.get('hits') or []))}</b> • Total linkflap lines: <b>{total_hits}</b> • Switches cleared: <b>{total_cleared}</b></div>
<div class="toolbar">{html_escape(tip)}</div>
{''.join(sections) if sections else '<div>No results.</div>'}
</body></html>
"""


def build_csv_rows(results: List[Dict[str, Any]]) -> Tuple[List[List[str]], List[List[str]], List[List[str]]]:
    all_rows: List[List[str]] = []
    hit_rows: List[List[str]] = []
    fail_rows: List[List[str]] = []
    for r in results:
        host = r["host"]
        ru = r.get("ru", "")
        err = r.get("error")
        clear_status = r.get("clear_status", "")
        clear_output = r.get("clear_output", "")
        hits = r.get("hits")
        if err:
            fail_rows.append([host, ru, err])
            continue
        if not hits:
            all_rows.append([host, ru, "", "", "NO_LINKFLAP", clear_status, clear_output])
            continue
        for entity, line in hits:
            all_rows.append([host, ru, entity, line, "LINKFLAP", clear_status, clear_output])
            hit_rows.append([host, ru, entity, line, clear_status, clear_output])
    return all_rows, hit_rows, fail_rows


def normalize_cli_argv(argv: Optional[List[str]] = None) -> List[str]:
    raw = list(argv if argv is not None else sys.argv[1:])
    normalized: List[str] = []
    i = 0

    while i < len(raw):
        token = raw[i]

        if token == "-q" and i + 1 < len(raw) and raw[i + 1] in {"2", "3"}:
            normalized.append(f"-q{raw[i + 1]}")
            i += 2
            continue
        if token == "-t" and i + 1 < len(raw) and raw[i + 1] in {"0", "1"}:
            normalized.append(f"-t{raw[i + 1]}")
            i += 2
            continue
        if token == "-p" and i + 1 < len(raw) and raw[i + 1] in {"1", "2", "3", "4"}:
            normalized.append(f"-p{raw[i + 1]}")
            i += 2
            continue

        if token.startswith("-re") and token != "-re" and not token.startswith("--"):
            attached = token[3:]
            if attached:
                normalized.extend(["-re", attached])
                i += 1
                continue

        if token.startswith("-n") and token != "-n" and not token.startswith("--"):
            attached = token[2:]
            if re.fullmatch(r"\d+", attached):
                normalized.extend(["-n", attached])
                i += 1
                continue

        if token.startswith("-r") and token not in {"-r", "-re"} and not token.startswith("--") and not token.startswith("-re"):
            attached = token[2:]
            if attached:
                normalized.extend(["-r", attached])
                i += 1
                continue

        normalized.append(token)
        i += 1

    return normalized


def dedupe_targets_by_host(targets: List[Tuple[str, str, str]]) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    deduped: List[Tuple[str, str, str]] = []
    host_to_index: Dict[str, int] = {}
    warnings: List[str] = []
    warned_hosts = set()

    for rack, sw, ru in targets:
        idx = host_to_index.get(sw)
        if idx is None:
            host_to_index[sw] = len(deduped)
            deduped.append((rack, sw, ru))
            continue

        prev_rack, _host, prev_ru = deduped[idx]
        merged_rack = prev_rack or rack
        merged_ru = prev_ru or ru

        if sw not in warned_hosts and (
            (rack and prev_rack and rack != prev_rack) or (ru and prev_ru and ru != prev_ru)
        ):
            warnings.append(
                f"{sw}: keeping rack/RU {prev_rack or '-'} / {prev_ru or '-'} and ignoring "
                f"{rack or '-'} / {ru or '-'}"
            )
            warned_hosts.add(sw)

        deduped[idx] = (merged_rack, sw, merged_ru)

    return deduped, warnings


def any_selection_filters(args: argparse.Namespace) -> bool:
    return bool(args.racks or args.q2 or args.q3 or args.t0 or args.t1 or args.p1 or args.p2 or args.p3 or args.p4)


def prompt_text(prompt: str, default: str = "") -> str:
    shown = f"{prompt} [{default}]" if default else prompt
    try:
        value = input(f"{shown}: ").strip()
    except EOFError as exc:
        raise ValueError("Interactive input was cancelled.") from exc
    return value or default


def prompt_fabric_inputs(args: argparse.Namespace) -> None:
    if args.xlsx or (args.region and args.number is not None):
        return

    while True:
        region = prompt_text("Region prefix (example: aga, jbp)")
        try:
            args.region = normalize_region(region)
            break
        except ValueError as exc:
            print(exc)

    while True:
        number_text = prompt_text("Fabric number (example: 5, 15)")
        try:
            args.number = int(number_text)
            build_fabric_name(args.region, args.number)
            break
        except (TypeError, ValueError) as exc:
            print(exc)


def prompt_mode_input(args: argparse.Namespace) -> None:
    if args.clear or args.dry_run:
        return

    while True:
        mode = prompt_text("Run mode: dry-run or clear", "dry-run").strip().lower()
        if mode in {"dry-run", "dryrun"}:
            args.dry_run = True
            args.clear = False
            return
        if mode == "clear":
            args.clear = True
            args.dry_run = False
            return
        print("Choose 'dry-run' or 'clear'.")


def prompt_filter_inputs(args: argparse.Namespace) -> None:
    if any_selection_filters(args):
        return

    while True:
        args.racks = prompt_text("Rack filter (comma-separated, blank for none)", args.racks)

        while True:
            q_choice = prompt_text("Q filter: q2, q3, both, or none", "both").strip().lower()
            if q_choice in {"q2", "q3", "both", "none"}:
                args.q2 = q_choice == "q2"
                args.q3 = q_choice == "q3"
                break
            print("Choose q2, q3, both, or none.")

        while True:
            t_choice = prompt_text("T filter: t0, t1, both, or none", "both").strip().lower()
            if t_choice in {"t0", "t1", "both", "none"}:
                args.t0 = t_choice == "t0"
                args.t1 = t_choice == "t1"
                break
            print("Choose t0, t1, both, or none.")

        while True:
            plane_choice = prompt_text("Plane filter: 1,2,3,4, all, or none", "all").strip().lower().replace(" ", "")
            args.p1 = args.p2 = args.p3 = args.p4 = False
            if plane_choice in {"all", "none"}:
                break
            tokens = [token for token in plane_choice.split(",") if token]
            if tokens and all(token in {"1", "2", "3", "4"} for token in tokens):
                args.p1 = "1" in tokens
                args.p2 = "2" in tokens
                args.p3 = "3" in tokens
                args.p4 = "4" in tokens
                break
            print("Choose all, none, or a comma-separated list using 1,2,3,4.")

        if any_selection_filters(args):
            return
        print("Choose at least one filter: rack(s), q2/q3, t0/t1, or specific plane(s).")


def prompt_for_missing_args(args: argparse.Namespace, interactive_requested: bool) -> argparse.Namespace:
    if not interactive_requested:
        return args
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        if args.interactive:
            raise ValueError("--interactive requires a TTY.")
        return args

    print("Interactive mode. Press Enter to accept the shown default when available.")
    prompt_fabric_inputs(args)
    prompt_filter_inputs(args)
    prompt_mode_input(args)
    return args


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Rack / q2-q3 / T0-T1 / plane-wide linkflap checker and clearer. "
            "Short flags accept both attached and spaced forms, such as -n15 or -n 15, "
            "-q2 or -q 2, -t1 or -t 1, and -p1 or -p 1."
        )
    )
    ap.add_argument("-r", "--racks", default="", help="Comma-separated racks e.g. 0604,0704")
    ap.add_argument("-re", "--region", default="", help="Region prefix used with -n, e.g. aga or jbp")
    ap.add_argument("-n", "--number", type=int, default=None, help="Fabric number used with --region, e.g. 5 or 15")
    ap.add_argument(
        "--autonet-root",
        default=None,
        help=(
            "Optional autonet root or autonet-plans path. If omitted, the script checks "
            "$AUTONET_PLANS_ROOT, $AUTONET_ROOT, ~/autonet, ~/tools/autonet, and common nested layouts."
        ),
    )
    ap.add_argument(
        "--xlsx",
        default=None,
        help="Path to input XLSX. Overrides auto-discovery from -re/--region and -n/--number.",
    )
    ap.add_argument("--out", default=None, help="Output base name (optional)")
    ap.add_argument("--interactive", action="store_true", help="Prompt for missing inputs in the terminal")
    ap.add_argument("--clear", action="store_true", help="Clear link flap-protection violations on switches with hits")
    ap.add_argument("--dry-run", action="store_true", help="Do not clear; only report hits")
    ap.add_argument("-q2", action="store_true", help="Select only Q2 switches")
    ap.add_argument("-q3", action="store_true", help="Select only Q3 switches")
    ap.add_argument("-t0", action="store_true", help="Select only T0 switches (name contains -t0-)")
    ap.add_argument("-t1", action="store_true", help="Select only T1 switches (name contains -t1-)")
    ap.add_argument("-p1", action="store_true", help="Select only plane 1 switches (name contains -p1-)")
    ap.add_argument("-p2", action="store_true", help="Select only plane 2 switches (name contains -p2-)")
    ap.add_argument("-p3", action="store_true", help="Select only plane 3 switches (name contains -p3-)")
    ap.add_argument("-p4", action="store_true", help="Select only plane 4 switches (name contains -p4-)")
    return ap.parse_args(normalize_cli_argv(argv))


def main() -> None:
    args = parse_args()
    args = prompt_for_missing_args(args, interactive_requested=(args.interactive or len(sys.argv) == 1))
    do_clear = bool(args.clear) and not bool(args.dry_run)
    if args.clear and args.dry_run:
        raise ValueError("Choose only one of --clear or --dry-run")
    if args.q2 and args.q3:
        raise ValueError("Choose only one of -q2 or -q3")

    region = normalize_region(args.region) if args.region else ""
    if bool(region) != (args.number is not None):
        raise ValueError("Provide both -re/--region and -n/--number together.")

    requested_fabric = build_fabric_name(region, args.number) if region else ""
    xlsx_path = resolve_xlsx_path(
        xlsx_arg=args.xlsx,
        fabric=requested_fabric or None,
        region=region or None,
        autonet_root=args.autonet_root,
    )
    inferred_fabric = infer_fabric_from_xlsx(xlsx_path)
    if requested_fabric and inferred_fabric and requested_fabric != inferred_fabric:
        raise ValueError(
            f"--xlsx looks like {inferred_fabric}, but -re/--region and -n/--number resolved to {requested_fabric}."
        )
    fabric = requested_fabric or inferred_fabric
    if not fabric:
        raise ValueError("Could not infer the fabric name from --xlsx. Provide -re/--region and -n/--number.")

    racks = [normalize_rack_value(x.strip()) for x in args.racks.split(",") if x.strip()]
    racks = [r for r in racks if r]

    planes: List[str] = []
    if args.p1:
        planes.append("1")
    if args.p2:
        planes.append("2")
    if args.p3:
        planes.append("3")
    if args.p4:
        planes.append("4")

    if not racks and not args.q2 and not args.q3 and not args.t0 and not args.t1 and not planes:
        raise ValueError("Provide at least one filter: -r, -q2, -q3, -t0, -t1, or -p1/-p2/-p3/-p4")

    df = pd.read_excel(xlsx_path, sheet_name=0)
    df.columns = [str(c).strip() for c in df.columns]
    device_column_sets = get_device_column_sets(df)

    targets: List[Tuple[str, str, str]] = []

    if racks:
        for rack in racks:
            for sw, ru in switches_for_rack_from_xlsx(df, rack, fabric, device_column_sets):
                sw_l = sw.lower()
                if args.q2 and "-q2-" not in sw_l:
                    continue
                if args.q3 and "-q3-" not in sw_l:
                    continue
                if args.t0 and "-t0-" not in sw_l:
                    continue
                if args.t1 and "-t1-" not in sw_l:
                    continue
                if planes and not any(f"-p{p}-" in sw_l for p in planes):
                    continue
                targets.append((rack, sw, ru))
    else:
        for rack, sw, ru in iter_switch_entries(df, fabric, device_column_sets):
            sw_l = sw.lower()
            if args.q2 and "-q2-" not in sw_l:
                continue
            if args.q3 and "-q3-" not in sw_l:
                continue
            if args.t0 and "-t0-" not in sw_l:
                continue
            if args.t1 and "-t1-" not in sw_l:
                continue
            if planes and not any(f"-p{p}-" in sw_l for p in planes):
                continue
            targets.append((rack, sw, ru))

    targets, dedupe_warnings = dedupe_targets_by_host(targets)
    if dedupe_warnings:
        print(
            f"WARNING: Found conflicting rack/RU metadata for {len(dedupe_warnings)} host(s). "
            "Keeping the first non-empty values per host."
        )
        for line in dedupe_warnings[:10]:
            print(f"  - {line}")
        if len(dedupe_warnings) > 10:
            print(f"  - ... {len(dedupe_warnings) - 10} more")

    if not targets:
        print("No switches matched the selected filters.")
        return

    password = getpass.getpass("Switch password (used for all dssh logins): ")
    ts = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    ts_tag = ts.strftime("%Y%m%d_%H%M%SZ")

    OUTDIR.mkdir(parents=True, exist_ok=True)
    DEBUGDIR.mkdir(parents=True, exist_ok=True)

    base = args.out or f"linkflap_{fabric}_{ts_tag}"
    out_csv = (OUTDIR / f"{base}.csv").resolve()
    hit_csv = (OUTDIR / f"{base}_HITS.csv").resolve()
    fail_csv = (OUTDIR / f"{base}_FAILURES.csv").resolve()
    html_path = (OUTDIR / f"{base}.html").resolve()
    html_print_path = (OUTDIR / f"{base}_print.html").resolve()
    hits_html_path = (OUTDIR / f"{base}_HITS.html").resolve()
    hits_html_print_path = (OUTDIR / f"{base}_HITS_print.html").resolve()

    filter_bits = []
    if racks:
        filter_bits.append("racks=" + ",".join(racks))
    if args.q2:
        filter_bits.append("q2")
    if args.q3:
        filter_bits.append("q3")
    if args.t0:
        filter_bits.append("T0")
    if args.t1:
        filter_bits.append("T1")
    if planes:
        filter_bits.append("planes=" + ",".join(planes))

    print(
        f"Fabric: {fabric}\n"
        f"XLSX: {xlsx_path}\n"
        f"Filters: {' '.join(filter_bits) if filter_bits else 'none'}\n"
        f"Targets: {len(targets)} | Workers: {WORKERS} | Probe: {PROBE_TIMEOUT_S}s | Cmd: {CMD_TIMEOUT_S}s | Clear: {do_clear}"
    )

    results: List[Dict[str, Any]] = []
    host_to_rack_ru = {sw: (rack, ru) for rack, sw, ru in targets}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(inspect_switch, sw, password, do_clear) for _rack, sw, _ru in targets]
        for fut in as_completed(futs):
            host, hits, clear_status, clear_output = fut.result()
            rack, ru = host_to_rack_ru.get(host, ("", ""))
            target_label = format_console_target_label(rack, ru, host)
            if hits is None:
                print(f"{target_label}: SKIP/FAIL: {clear_output}")
                results.append(
                    {
                        "host": host,
                        "rack": rack,
                        "ru": ru,
                        "error": clear_output,
                        "hits": None,
                        "clear_status": clear_status,
                        "clear_output": clear_output,
                    }
                )
                continue

            print(f"{target_label}: linkflap_hits={len(hits)} clear={clear_status or 'SKIPPED'}")
            results.append(
                {
                    "host": host,
                    "rack": rack,
                    "ru": ru,
                    "hits": hits,
                    "clear_status": clear_status,
                    "clear_output": clear_output,
                }
            )

    all_rows, hit_rows, fail_rows = build_csv_rows(results)

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host", "ru", "entity", "matched_line", "status", "clear_status", "clear_output"])
        w.writerows(all_rows)

    with open(hit_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host", "ru", "entity", "matched_line", "clear_status", "clear_output"])
        w.writerows(hit_rows)

    with open(fail_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host", "ru", "error"])
        w.writerows(fail_rows)

    generated_utc = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    title = f"Linkflap Report ({base})"
    html_path.write_text(build_full_html(title, generated_utc, results, print_friendly=False), encoding="utf-8")
    html_print_path.write_text(build_full_html(title + " (Print-Friendly)", generated_utc, results, print_friendly=True), encoding="utf-8")
    hits_title = f"Linkflap Hits Only ({base})"
    hits_html_path.write_text(build_hits_only_html(hits_title, generated_utc, results, print_friendly=False), encoding="utf-8")
    hits_html_print_path.write_text(build_hits_only_html(hits_title + " (Print-Friendly)", generated_utc, results, print_friendly=True), encoding="utf-8")

    total_switches_with_hits = sum(1 for r in results if r.get("hits"))
    total_linkflap_lines = sum(len(r.get("hits") or []) for r in results)
    total_cleared = sum(1 for r in results if r.get("clear_status") == "CLEARED")
    total_failures = len([r for r in results if r.get("error")])

    print(f"\nWrote FULL CSV:            {out_csv}")
    print(f"Wrote HITS CSV:            {hit_csv}")
    print(f"Wrote FAILURES CSV:        {fail_csv}")
    print(f"Wrote HTML:                {html_path}")
    print(f"Wrote PRINT HTML:          {html_print_path}")
    print(f"Wrote HITS-ONLY HTML:      {hits_html_path}")
    print(f"Wrote HITS-ONLY PRINT:     {hits_html_print_path}")
    print(f"Total switches with hits:   {total_switches_with_hits}")
    print(f"Total linkflap lines:       {total_linkflap_lines}")
    print(f"Switches cleared:           {total_cleared}")
    print(f"Failures:                   {total_failures}")

    print("\nLinkflap hits by switch/interface:")
    any_hits = False
    for r in results:
        hits = r.get("hits") or []
        if not hits:
            continue
        any_hits = True
        host = r.get("host", "")
        print(f"- {format_console_target_label(r.get('rack', ''), r.get('ru', ''), host)}")
        for entity, line in hits:
            port_match = re.match(r"^(swp\S+)\s+", line)
            port = port_match.group(1) if port_match else line.split()[0] if line.split() else ""
            print(f"  {port} | {entity} | {line}")
    if not any_hits:
        print("- No linkflap hits found.")

    print(f"\nDebug directory: {DEBUGDIR.resolve()}")


if __name__ == "__main__":
    main()
