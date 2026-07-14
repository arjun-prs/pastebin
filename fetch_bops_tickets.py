#!/usr/bin/env python3
"""
Fetch unresolved BOPS master tickets for rack locations.

This script is read-only. It searches BOPS master tickets by building, applies
an optional rack type filter from each ticket's actual Rack Type field, follows
linked tickets, and maps the requested rack numbers from linked ticket location
fields or summaries back to the master BOPS tickets.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

import requests
from urllib3.exceptions import InsecureRequestWarning


DEFAULT_JIRA_URL = "https://jira-sd.mc1.oracleiaas.com"
DEFAULT_PASSWORD_COMMAND = (
    "ssh operator-access-token.svc.ad1.us-ashburn-1 generate --mode=password"
)
DEFAULT_FIELD_IDS = {
    "location": "customfield_18547",
    "rack_location": "customfield_10601",
    "grid_location": "customfield_18535",
    "rack_type": "customfield_11902",
}
DEFAULT_RACK_TYPES = ("t0", "t1", "ipr")
RACK_TYPE_PATTERNS = {
    "t0": (
        r"(?<![a-z0-9])t0(?![a-z0-9])",
        r"tier\s*0",
    ),
    "t1": (
        r"(?<![a-z0-9])t1(?![a-z0-9])",
        r"tier\s*1",
    ),
    "ipr": (
        r"(?<![a-z0-9])ipr(?![a-z0-9])",
        r"infrastructure\s+provisioning\s+rack",
    ),
}
HELP_EPILOG = f"""
Supported rack-type tags:
  t0    Match Rack Type values that identify tier-0 / t0 racks.
  t1    Match Rack Type values that identify tier-1 / t1 racks.
  ipr   Match Rack Type values that identify IPR racks.

Common option tags:
  --region / --building     Building value in Jira, for example aga5.
  --rack-type               Optional rack-type tag: {", ".join(DEFAULT_RACK_TYPES)}.
  --rack-type-value         Exact Jira Rack Type value for region-specific names.
  --racks-file              File with racks separated by commas, spaces, or newlines.
  --format                  Output as markdown, csv, tsv, or json.
  --show-evidence           Include the linked ticket used for rack mapping.

Examples:
  fetch_bops_tickets.py --region aga5 5034,5134,5032
  fetch_bops_tickets.py --region aga5 --racks-file racks.txt
  fetch_bops_tickets.py --region aga5 --rack-type t0 --racks-file racks.txt
  fetch_bops_tickets.py --region aga5 --rack-type-value net.ad_spc4_planar_qfab_t0_1.01 5034
"""


@dataclass(frozen=True)
class Match:
    rack: str
    bops_key: str
    bops_summary: str
    evidence_key: str
    evidence_summary: str
    evidence_status: str
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map rack numbers to unresolved BOPS master tickets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument(
        "racks",
        nargs="*",
        help="Rack numbers. You may pass space-separated values or comma-separated lists.",
    )
    parser.add_argument(
        "--racks-file",
        help="File containing rack numbers separated by commas, whitespace, or newlines.",
    )
    parser.add_argument(
        "-r",
        "--region",
        "--building",
        dest="building",
        required=True,
        help="Building/region value used by the BOPS Jira field, for example aga5.",
    )
    parser.add_argument(
        "-t",
        "--rack-type",
        choices=DEFAULT_RACK_TYPES,
        help=(
            "Rack type shorthand. If omitted, searches all BOPS rack types for "
            "the building, which is safer when regions use different Rack Type values."
        ),
    )
    parser.add_argument(
        "--rack-type-value",
        help=(
            "Exact Jira Rack Type value to filter locally. Use this when a region "
            "has a type value that does not contain t0, t1, or ipr."
        ),
    )
    parser.add_argument(
        "--jira-url",
        default=DEFAULT_JIRA_URL,
        help=f"Jira base URL. Default: {DEFAULT_JIRA_URL}",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("JIRA_USERNAME"),
        help="Jira username/email. Defaults to JIRA_USERNAME or ~/.jira/config.json username.",
    )
    parser.add_argument(
        "--password-env",
        default="JIRA_PASSWORD",
        help="Environment variable containing the Jira password. Default: JIRA_PASSWORD.",
    )
    parser.add_argument(
        "--password-command",
        default=DEFAULT_PASSWORD_COMMAND,
        help="Command used to generate the temporary Jira password.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "csv", "tsv", "json"),
        default="markdown",
        help="Output format. Default: markdown.",
    )
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help="Include the linked ticket used as evidence.",
    )
    parser.add_argument(
        "--no-links",
        action="store_true",
        help="Print plain BOPS keys instead of Markdown links.",
    )
    parser.add_argument(
        "--include-closed-bops",
        action="store_true",
        help="Do not filter BOPS tickets by unresolved resolution.",
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates. Disabled by default for this internal Jira.",
    )
    return parser.parse_args()


def normalize_racks(values: Iterable[str]) -> list[str]:
    racks: list[str] = []
    for value in values:
        for item in re.split(r"[\s,]+", value):
            rack = item.strip()
            if rack:
                racks.append(rack)
    seen: set[str] = set()
    unique = []
    for rack in racks:
        if rack not in seen:
            seen.add(rack)
            unique.append(rack)
    if not unique:
        raise SystemExit("Provide at least one rack number.")
    return unique


def load_racks(cli_racks: Iterable[str], racks_file: str | None) -> list[str]:
    values = list(cli_racks)
    if racks_file:
        try:
            with open(racks_file, "r", encoding="utf-8") as file_obj:
                values.append(file_obj.read())
        except OSError as exc:
            raise SystemExit(f"Unable to read racks file {racks_file}: {exc}") from exc
    return normalize_racks(values)


def load_username(cli_username: str | None) -> str:
    if cli_username:
        return cli_username

    config_path = os.path.expanduser("~/.jira/config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            username = json.load(config_file).get("username")
    except FileNotFoundError:
        username = None

    if not username:
        raise SystemExit(
            "Jira username not found. Pass --username or set JIRA_USERNAME."
        )
    return username


def load_password(password_env: str, password_command: str) -> str:
    env_password = os.environ.get(password_env)
    if env_password:
        return env_password

    command = shlex.split(password_command)
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Password command failed with exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )

    password = result.stdout.strip()
    if not password:
        raise SystemExit("Password command returned an empty password.")
    return password


def jira_get(
    session: requests.Session,
    jira_url: str,
    path: str,
    verify_tls: bool,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    response = session.get(
        jira_url.rstrip("/") + path,
        params=params,
        headers={"Accept": "application/json"},
        timeout=60,
        verify=verify_tls,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"Jira GET {path} failed with HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )
    return response.json()


def jql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def resolve_field_ids(
    session: requests.Session, jira_url: str, verify_tls: bool
) -> dict[str, str]:
    field_ids = dict(DEFAULT_FIELD_IDS)
    fields = jira_get(session, jira_url, "/rest/api/2/field", verify_tls)
    assert isinstance(fields, list)

    names = {
        str(field.get("name", "")).casefold(): str(field.get("id", ""))
        for field in fields
    }
    for key, field_name in {
        "location": "Location",
        "rack_location": "Rack Location",
        "grid_location": "Grid Location",
        "rack_type": "Rack Type",
    }.items():
        field_id = names.get(field_name.casefold())
        if field_id:
            field_ids[key] = field_id
    return field_ids


def search_issues(
    session: requests.Session,
    jira_url: str,
    verify_tls: bool,
    jql: str,
    fields: list[str],
    max_results: int = 100,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    start_at = 0

    while True:
        data = jira_get(
            session,
            jira_url,
            "/rest/api/2/search",
            verify_tls,
            params={
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": ",".join(fields),
            },
        )
        assert isinstance(data, dict)
        batch = data.get("issues", [])
        issues.extend(batch)
        start_at += len(batch)
        if not batch or start_at >= int(data.get("total", 0)):
            return issues


def option_or_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        text = str(value.get("value") or value.get("name") or "")
        child = value.get("child")
        if isinstance(child, dict) and child.get("value"):
            text = f"{text} {child['value']}".strip()
        return text
    if isinstance(value, list):
        return " ".join(option_or_text(item) for item in value)
    return str(value)


def master_rack_type_text(master_issue: dict[str, Any], field_ids: dict[str, str]) -> str:
    fields = master_issue.get("fields", {})
    values = [
        option_or_text(fields.get(field_ids["rack_type"])),
        fields.get("summary") or "",
        " ".join(fields.get("labels") or []),
    ]
    return " ".join(value for value in values if value)


def rack_type_matches(
    master_issue: dict[str, Any],
    field_ids: dict[str, str],
    rack_type: str | None,
    rack_type_value: str | None,
) -> bool:
    if not rack_type and not rack_type_value:
        return True

    text = master_rack_type_text(master_issue, field_ids)
    normalized_text = text.casefold().strip()
    if rack_type_value:
        actual_value = option_or_text(
            master_issue.get("fields", {}).get(field_ids["rack_type"])
        )
        return actual_value.casefold().strip() == rack_type_value.casefold().strip()

    assert rack_type is not None
    return any(
        re.search(pattern, normalized_text, re.IGNORECASE)
        for pattern in RACK_TYPE_PATTERNS[rack_type]
    )


def filter_master_issues_by_rack_type(
    master_issues: list[dict[str, Any]],
    field_ids: dict[str, str],
    rack_type: str | None,
    rack_type_value: str | None,
) -> list[dict[str, Any]]:
    return [
        issue
        for issue in master_issues
        if rack_type_matches(issue, field_ids, rack_type, rack_type_value)
    ]


def linked_issue_keys(master_issue: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for link in master_issue.get("fields", {}).get("issuelinks", []) or []:
        other = link.get("outwardIssue") or link.get("inwardIssue")
        if other and other.get("key"):
            keys.append(str(other["key"]))
    return keys


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_linked_issues(
    session: requests.Session,
    jira_url: str,
    verify_tls: bool,
    keys: list[str],
    field_ids: dict[str, str],
) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}

    details: dict[str, dict[str, Any]] = {}
    fields = [
        "summary",
        "status",
        "description",
        "labels",
        field_ids["location"],
        field_ids["rack_location"],
        field_ids["grid_location"],
    ]
    for key_chunk in chunks(list(dict.fromkeys(keys)), 80):
        jql = "key in (" + ",".join(key_chunk) + ")"
        for issue in search_issues(
            session, jira_url, verify_tls, jql, fields, max_results=100
        ):
            issue_fields = issue.get("fields", {})
            details[issue["key"]] = {
                "key": issue["key"],
                "summary": issue_fields.get("summary") or "",
                "status": option_or_text(issue_fields.get("status")),
                "description": issue_fields.get("description") or "",
                "labels": " ".join(issue_fields.get("labels") or []),
                "location": option_or_text(issue_fields.get(field_ids["location"])),
                "rack_location": option_or_text(
                    issue_fields.get(field_ids["rack_location"])
                ),
                "grid_location": option_or_text(
                    issue_fields.get(field_ids["grid_location"])
                ),
            }
    return details


def exact_rack_pattern(rack: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\d)" + re.escape(rack) + r"(?!\d)")


def summary_location_pattern(rack: str) -> re.Pattern[str]:
    return re.compile(
        r"\b(?:location|rack(?:\s+location)?|loc|position)\s*[:#-]?\s*"
        + re.escape(rack)
        + r"\b",
        re.IGNORECASE,
    )


def source_matches_rack(text: str, rack: str, summary_like: bool = False) -> bool:
    if not text:
        return False
    if summary_like and summary_location_pattern(rack).search(text):
        return True
    return exact_rack_pattern(rack).search(text) is not None


def match_racks(
    racks: list[str],
    master_issues: list[dict[str, Any]],
    linked_details: dict[str, dict[str, Any]],
) -> dict[str, list[Match]]:
    matches: dict[str, list[Match]] = defaultdict(list)
    rack_set = set(racks)

    for master_issue in master_issues:
        bops_key = str(master_issue["key"])
        bops_fields = master_issue.get("fields", {})
        bops_summary = bops_fields.get("summary") or ""

        for linked_key in linked_issue_keys(master_issue):
            linked = linked_details.get(linked_key)
            if not linked:
                continue

            sources = [
                ("rack_location", linked.get("rack_location", ""), False),
                ("location", linked.get("location", ""), False),
                ("grid_location", linked.get("grid_location", ""), False),
                ("summary", linked.get("summary", ""), True),
                ("labels", linked.get("labels", ""), False),
                ("description", linked.get("description", ""), True),
            ]
            for rack in rack_set:
                for source_name, source_text, summary_like in sources:
                    if source_matches_rack(source_text, rack, summary_like):
                        matches[rack].append(
                            Match(
                                rack=rack,
                                bops_key=bops_key,
                                bops_summary=bops_summary,
                                evidence_key=linked_key,
                                evidence_summary=linked.get("summary", ""),
                                evidence_status=linked.get("status", ""),
                                source=source_name,
                            )
                        )
                        break

    deduped: dict[str, list[Match]] = {}
    for rack, rack_matches in matches.items():
        seen: set[tuple[str, str]] = set()
        deduped[rack] = []
        for match in rack_matches:
            signature = (match.bops_key, match.evidence_key)
            if signature in seen:
                continue
            seen.add(signature)
            deduped[rack].append(match)
    return deduped


def preferred_matches(matches: dict[str, list[Match]]) -> dict[str, list[Match]]:
    preferred: dict[str, list[Match]] = {}
    for rack, rack_matches in matches.items():
        by_bops: dict[str, list[Match]] = defaultdict(list)
        for match in rack_matches:
            by_bops[match.bops_key].append(match)

        selected = []
        for bops_matches in by_bops.values():
            selected.append(sorted(bops_matches, key=evidence_rank)[0])
        preferred[rack] = sorted(selected, key=lambda item: item.bops_key)
    return preferred


def evidence_rank(match: Match) -> tuple[int, int, str]:
    key = match.evidence_key
    summary = match.evidence_summary.casefold()
    if key.startswith("DO-") and "rack deployment" in summary:
        primary_rank = 0
    elif key.startswith("DO-"):
        primary_rank = 1
    elif key.startswith("NSG-"):
        primary_rank = 2
    else:
        primary_rank = 3

    source_order = {
        "rack_location": 0,
        "location": 1,
        "grid_location": 2,
        "summary": 3,
        "labels": 4,
        "description": 5,
    }
    return (primary_rank, source_order.get(match.source, 99), key)


def bops_link(jira_url: str, key: str, no_links: bool) -> str:
    if no_links:
        return key
    url = f"{jira_url.rstrip('/')}/browse/{key}"
    return f"[{key}]({url})"


def output_rows(
    racks: list[str],
    matches: dict[str, list[Match]],
    jira_url: str,
    output_format: str,
    show_evidence: bool,
    no_links: bool,
) -> None:
    rows: list[dict[str, str]] = []
    for rack in racks:
        rack_matches = matches.get(rack, [])
        bops_values = ", ".join(
            bops_link(jira_url, match.bops_key, no_links) for match in rack_matches
        )
        row = {
            "rack": rack,
            "bops_ticket": bops_values or "NO_MATCH",
        }
        if show_evidence:
            row["evidence"] = ", ".join(
                f"{match.evidence_key} ({match.source})" for match in rack_matches
            )
        rows.append(row)

    fieldnames = ["rack", "bops_ticket"] + (["evidence"] if show_evidence else [])

    if output_format == "json":
        print(json.dumps(rows, indent=2))
        return

    if output_format in {"csv", "tsv"}:
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=fieldnames,
            delimiter="," if output_format == "csv" else "\t",
        )
        writer.writeheader()
        writer.writerows(rows)
        return

    print("| Rack | BOPS ticket |" + (" Evidence |" if show_evidence else ""))
    print("|---:|---|" + ("---|" if show_evidence else ""))
    for row in rows:
        line = f"| {row['rack']} | {row['bops_ticket']} |"
        if show_evidence:
            line += f" {row['evidence']} |"
        print(line)


def main() -> int:
    args = parse_args()
    racks = load_racks(args.racks, args.racks_file)

    if not args.verify_tls:
        requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

    username = load_username(args.username)
    password = load_password(args.password_env, args.password_command)
    session = requests.Session()
    session.auth = (username, password)

    field_ids = resolve_field_ids(session, args.jira_url, args.verify_tls)
    resolution_filter = "" if args.include_closed_bops else " AND resolution = Unresolved"
    jql = (
        f"project = BOPS{resolution_filter} "
        f"AND Building = {jql_quote(args.building)}"
    )

    master_issues = search_issues(
        session,
        args.jira_url,
        args.verify_tls,
        jql,
        fields=["key", "summary", "status", "issuelinks", "labels", field_ids["rack_type"]],
        max_results=100,
    )
    master_issues = filter_master_issues_by_rack_type(
        master_issues, field_ids, args.rack_type, args.rack_type_value
    )
    linked_keys: list[str] = []
    for issue in master_issues:
        linked_keys.extend(linked_issue_keys(issue))
    linked_details = fetch_linked_issues(
        session, args.jira_url, args.verify_tls, linked_keys, field_ids
    )

    matches = preferred_matches(match_racks(racks, master_issues, linked_details))
    output_rows(
        racks,
        matches,
        args.jira_url,
        args.format,
        args.show_evidence,
        args.no_links or args.format != "markdown",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
