#!/usr/bin/env python3
"""Report rack/device lifecycle status and who completed in-service transitions.

The script uses two read-only NCPCLI sources:

1. ``devices list-state`` for the current state and authoritative State Store
   modification time.
2. ``ncp-job list`` for the matching ``VALIDATED_SET_STATE`` job, which can
   identify the human initiator and change ticket when State Store only shows
   a Network Control Plane service principal.

Examples:

    # Building mode: count physical racks and summarize their device states.
    python3 device_inservice_audit.py iad64 --timezone Asia/Kolkata

    python3 device_inservice_audit.py --building fbb1 --verbose

    # Device mode remains available for targeted checks.
    python3 device_inservice_audit.py -r iad \
        --device iad64-q1-b2-t1-r1 --device iad64-q1-b2-t1-r2

    python3 device_inservice_audit.py -r iad \
        --device-pattern 'iad64-q1-b2-t1-r*'

    python3 device_inservice_audit.py -r iad --device-file devices.txt \
        --timezone Asia/Kolkata --csv inservice-audit.csv

Rack status is derived from the current lifecycle states of NCP network
devices in each physical rack. A rack is ``in-service`` only when every listed
device in that rack is in-service. The rack's completion time/actor is taken
from the last device transition that made the rack fully in-service.

State Store reports the latest state assignment; it is not a complete
historical ledger. A device that is no longer in-service cannot be used to
reconstruct an older in-service transition with certainty.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo
    ZoneInfo = None  # type: ignore[assignment]


UTC = dt.timezone.utc
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CHANGE_RE = re.compile(r"\bCHANGE-\d+\b", re.IGNORECASE)
SERVICE_PREFIXES = ("network-control-plane/", "ocid1.dynamicgroup")
BUILDING_RE = re.compile(r"^[A-Za-z]{3}\d+$")
STATE_ORDER = {
    "in-service": 0,
    "deployed": 1,
    "maintenance": 2,
    "repair": 3,
    "new": 4,
    "decommissioned": 5,
    "not set": 6,
}
STATUS_COLORS = {
    "in-service": "1;32",
    "deployed": "36",
    "maintenance": "33",
    "repair": "1;31",
    "new": "34",
    "decommissioned": "90",
    "mixed": "1;35",
    "no-managed-devices": "90",
    "not set": "90",
}


class AuditError(RuntimeError):
    """Raised when a read-only NCPCLI lookup cannot be completed."""


@dataclass
class StateRecord:
    name: str
    state: str
    set_by: str
    set_on: Optional[dt.datetime]
    raw_set_on: str


@dataclass
class JobMatch:
    job_id: str
    job_state: str
    initiator: str
    change_id: str
    event_time: Optional[dt.datetime]
    source: str = "NCP job"


@dataclass
class AuditRecord:
    device: str
    current_state: str
    state_set_on: str
    initiated_by: str
    change_id: str
    job_id: str
    job_state: str
    source: str


@dataclass
class InventoryDevice:
    name: str
    role: str
    rack: str
    state: str


@dataclass
class PhysicalRack:
    rack: str
    block: str
    platform: str
    aid: str


@dataclass
class RackAuditRecord:
    block: str
    rack: str
    platform: str
    status: str
    devices: int
    state_breakdown: str
    in_service_since: str
    completed_by: str
    change_id: str
    completion_device: str
    job_id: str
    job_state: str
    source: str


def split_csv(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def read_device_file(path: Path) -> list[str]:
    if not path.is_file():
        raise AuditError(f"Device file not found: {path}")

    devices: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or set(line) <= {"+", "-", "|", " "}:
            continue

        # Also accept the first column of a pasted pipe-delimited table.
        if line.startswith("|"):
            columns = [column.strip() for column in line.strip("|").split("|")]
            candidate = columns[0] if columns else ""
        else:
            candidate = re.split(r"[\s,]+", line, maxsplit=1)[0]

        if candidate.lower() in {"name", "device", "hostname"}:
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate):
            devices.append(candidate)
        else:
            raise AuditError(f"Invalid device name in {path}: {candidate!r}")
    return dedupe(devices)


def resolve_ncpcli(explicit: Optional[str]) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.parent != Path(".") or "/" in explicit:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            raise AuditError(f"NCPCLI executable is not usable: {candidate}")
        found = shutil.which(explicit)
        if found:
            return found
        raise AuditError(f"NCPCLI executable was not found on PATH: {explicit}")

    preferred = Path.home() / ".pyenv/versions/ncpcli-env/bin/ncpcli"
    if preferred.is_file() and os.access(preferred, os.X_OK):
        return str(preferred)

    found = shutil.which("ncpcli")
    if found:
        return found
    raise AuditError(
        "ncpcli was not found. Activate ncpcli-env or pass --ncpcli-bin /path/to/ncpcli."
    )


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Extract a JSON list from NCPCLI output containing notices/progress text."""
    cleaned = strip_ansi(text)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*\[", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start() :].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise AuditError("NCPCLI returned no readable JSON list.")


def parse_timestamp(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "null", "None"}:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_timestamp(value: Optional[dt.datetime], timezone: dt.tzinfo) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def command_text(command: Sequence[str]) -> str:
    try:
        import shlex

        return shlex.join(command)
    except (ImportError, AttributeError):  # pragma: no cover
        return " ".join(command)


def stream_supports_unicode(stream: Any) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "⠋✓✗".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}m {remaining:02d}s"


class ProgressTask:
    """Animated terminal progress for a single blocking operation."""

    def __init__(self, label: str, enabled: bool, color: bool) -> None:
        self.label = label
        self.enabled = enabled
        self.color = color
        self.stream = sys.stderr
        self.started = 0.0
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.rendered = False
        self.unicode = stream_supports_unicode(self.stream)
        self.frames = (
            ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
            if self.unicode
            else ("|", "/", "-", "\\")
        )

    def _styled(self, value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if self.color else value

    def _render(self, frame: str) -> None:
        elapsed = time.monotonic() - self.started
        line = f"{frame} {self.label}  {format_duration(elapsed)}"
        self.rendered = True
        self.stream.write("\r\033[2K" + self._styled(line, "36"))
        self.stream.flush()

    def _animate(self) -> None:
        # Avoid flashing progress lines for local work that finishes immediately.
        if self.stop_event.wait(0.2):
            return
        index = 0
        while not self.stop_event.is_set():
            self._render(self.frames[index % len(self.frames)])
            index += 1
            if self.stop_event.wait(0.1):
                break

    def __enter__(self) -> "ProgressTask":
        if not self.enabled:
            return self
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if not self.enabled:
            return False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        elapsed = time.monotonic() - self.started
        if not self.rendered and exc_type is None:
            return False
        self.stream.write("\r\033[2K")
        if exc_type is None:
            marker = "✓" if self.unicode else "[ok]"
            line = f"{marker} {self.label}  {format_duration(elapsed)}"
            self.stream.write(self._styled(line, "32") + "\n")
        else:
            marker = "✗" if self.unicode else "[failed]"
            line = f"{marker} {self.label}  {format_duration(elapsed)}"
            self.stream.write(self._styled(line, "31") + "\n")
        self.stream.flush()
        return False


class TerminalProgress:
    def __init__(self, requested: bool, color: bool) -> None:
        self.enabled = (
            requested
            and sys.stderr.isatty()
            and os.environ.get("TERM", "").lower() != "dumb"
        )
        self.color = color and not os.environ.get("NO_COLOR")

    def task(self, label: str) -> ProgressTask:
        return ProgressTask(label, self.enabled, self.color)


class NcpcliRunner:
    def __init__(
        self,
        binary: str,
        region: str,
        timeout: int,
        use_agent_for_auth: bool,
        debug: bool,
    ) -> None:
        self.binary = binary
        self.region = region
        self.timeout = timeout
        self.debug = debug
        self.base = [binary, "-r", region]
        if use_agent_for_auth:
            self.base.extend(["-o", "use_agent_for_auth=true"])

    def run(self, arguments: Sequence[str]) -> str:
        command = [*self.base, *arguments]
        if self.debug:
            print(f"$ {command_text(command)}", file=sys.stderr)
        environment = os.environ.copy()
        environment.setdefault("PYENV_VERSION", "ncpcli-env")
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuditError(
                f"NCPCLI timed out after {self.timeout}s: {command_text(command)}"
            ) from exc
        except OSError as exc:
            raise AuditError(f"Unable to run ncpcli: {exc}") from exc

        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip()
            raise AuditError(
                f"NCPCLI failed with exit code {completed.returncode}:\n{details}"
            )
        return completed.stdout


def state_records_from_payloads(
    payloads: Sequence[dict[str, Any]],
) -> list[StateRecord]:
    records: dict[str, StateRecord] = {}
    for item in payloads:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        raw_set_on = str(item.get("set_on") or "-")
        records[name] = StateRecord(
            name=name,
            state=str(item.get("state") or "not set"),
            set_by=str(item.get("set_by") or "<not recorded>"),
            set_on=parse_timestamp(raw_set_on),
            raw_set_on=raw_set_on,
        )
    return sorted(records.values(), key=lambda record: record.name)


def fetch_state_records(
    runner: NcpcliRunner, exact_devices: Sequence[str], patterns: Sequence[str]
) -> list[StateRecord]:
    payloads: list[dict[str, Any]] = []

    if exact_devices:
        command = ["devices", "list-state", "--json"]
        for device in exact_devices:
            command.extend(["--exact-device", device])
        payloads.extend(extract_json_array(runner.run(command)))

    # Run patterns separately so multiple patterns behave as a union.
    for pattern in patterns:
        command = [
            "devices",
            "list-state",
            "--devices",
            pattern,
            "--json",
        ]
        payloads.extend(extract_json_array(runner.run(command)))

    return state_records_from_payloads(payloads)


def fetch_state_records_chunked(
    runner: NcpcliRunner, device_names: Sequence[str], chunk_size: int
) -> list[StateRecord]:
    payloads: list[dict[str, Any]] = []
    for start in range(0, len(device_names), chunk_size):
        command = ["devices", "list-state", "--json"]
        for device in device_names[start : start + chunk_size]:
            command.extend(["--exact-device", device])
        payloads.extend(extract_json_array(runner.run(command)))
    return state_records_from_payloads(payloads)


def parse_building_inventory(
    output: str, expected_building: str
) -> list[InventoryDevice]:
    """Parse NCPCLI's device table, retaining physical rack and state."""
    devices: dict[str, InventoryDevice] = {}
    expected = expected_building.lower()
    for raw_line in strip_ansi(output).splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 6 or columns[0].lower() == "name":
            continue

        name, role, state, location = columns[0], columns[1], columns[3], columns[5]
        location_parts = [part.strip() for part in location.split(":")]
        if len(location_parts) < 2 or location_parts[0].lower() != expected:
            continue
        rack = location_parts[1]
        if not name or not rack or rack in {"?", "-"}:
            continue
        devices[name] = InventoryDevice(
            name=name,
            role=role,
            rack=rack,
            state=state.strip().lower() or "not set",
        )

    if not devices:
        raise AuditError(
            f"No NCP network devices with physical rack locations were found in {expected_building}."
        )
    return sorted(devices.values(), key=lambda device: natural_key(device.name))


def fetch_building_inventory(
    runner: NcpcliRunner, building: str
) -> list[InventoryDevice]:
    output = runner.run(["devices", "list", "--building", building])
    return parse_building_inventory(output, building)


def rackmap_candidates(region: str, explicit: Optional[Path]) -> list[Path]:
    if explicit:
        return [explicit.expanduser()]
    return [
        Path.home() / "autonet/autonet-rackmaps" / f"{region}.rackmap",
        Path.home() / "tools/autonet/autonet-rackmaps" / f"{region}.rackmap",
    ]


def load_physical_racks(
    building: str, region: str, explicit_rackmap: Optional[Path]
) -> tuple[list[PhysicalRack], Path]:
    rackmap_path = next(
        (path for path in rackmap_candidates(region, explicit_rackmap) if path.is_file()),
        None,
    )
    if rackmap_path is None:
        searched = ", ".join(str(path) for path in rackmap_candidates(region, explicit_rackmap))
        raise AuditError(f"No production rackmap found. Checked: {searched}")

    try:
        data = json.loads(rackmap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"Unable to read rackmap {rackmap_path}: {exc}") from exc

    building_match = BUILDING_RE.fullmatch(building)
    if not building_match:
        raise AuditError(f"Building must look like iad64 or fbb1: {building}")
    digits_match = re.search(r"\d+$", building)
    assert digits_match is not None
    building_name = f"bldg{digits_match.group(0)}"
    building_data = next(
        (
            item
            for item in data.get("buildings", [])
            if str(item.get("name") or "").lower() == building_name.lower()
        ),
        None,
    )
    if not isinstance(building_data, dict):
        raise AuditError(
            f"Building {building_name} ({building}) was not found in {rackmap_path}."
        )

    racks: dict[str, PhysicalRack] = {}
    for block in building_data.get("blocks", []):
        if not isinstance(block, dict):
            continue
        block_name = str(block.get("name") or "-")
        active = block.get("all") or {}
        if not isinstance(active, dict):
            continue
        for rack, details in active.items():
            rack_name = str(rack).strip()
            if not rack_name:
                continue
            details = details if isinstance(details, dict) else {}
            physical = PhysicalRack(
                rack=rack_name,
                block=block_name,
                platform=str(details.get("platform") or "-"),
                aid=str(details.get("aid") or "-"),
            )
            previous = racks.get(rack_name)
            if previous and previous.block != block_name:
                raise AuditError(
                    f"Rack {rack_name} appears in multiple blocks in {rackmap_path}: "
                    f"{previous.block}, {block_name}"
                )
            racks[rack_name] = physical

    if not racks:
        raise AuditError(f"No active racks found for {building_name} in {rackmap_path}.")
    return sorted(racks.values(), key=lambda rack: natural_key(rack.rack)), rackmap_path


def merged_query_windows(
    records: Sequence[StateRecord], window_minutes: int
) -> list[tuple[dt.datetime, dt.datetime]]:
    margin = dt.timedelta(minutes=window_minutes)
    raw = sorted(
        (record.set_on - margin, record.set_on + margin)
        for record in records
        if record.set_on is not None
    )
    if not raw:
        return []

    # Keep each regional query bounded even if many overlapping transitions
    # form a long chain of windows.
    max_span = dt.timedelta(hours=6)
    merged: list[tuple[dt.datetime, dt.datetime]] = []
    for start, end in raw:
        if not merged:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if start <= previous_end and max(previous_end, end) - previous_start <= max_span:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def ncp_date(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    request = job.get("request") or {}
    payload = request.get("payload") if isinstance(request, dict) else None
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def is_live_inservice_job(job: dict[str, Any]) -> bool:
    payload = parse_job_payload(job)
    target_state = re.sub(r"[-_\s]", "", str(payload.get("targetState") or "")).lower()
    dry_run = payload.get("dryRun", False)
    if isinstance(dry_run, str):
        dry_run = dry_run.strip().lower() in {"1", "true", "yes"}
    return target_state == "inservice" and not bool(dry_run)


def job_target_names(job: dict[str, Any]) -> set[str]:
    request = job.get("request") or {}
    if not isinstance(request, dict):
        return set()
    raw_targets = request.get("targets") or []
    target = request.get("target")
    if isinstance(target, dict):
        raw_targets = [*raw_targets, target]

    names: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").upper() not in {"DEVICE", "DEPRECATED"}:
            continue
        name = str(item.get("name") or "").strip()
        if name and name.lower() != "deprecated":
            names.add(name)
    return names


def job_event_time(job: dict[str, Any]) -> Optional[dt.datetime]:
    metadata = job.get("metadata") or {}
    values = (
        job.get("end_date"),
        job.get("start_date"),
        metadata.get("added_date") if isinstance(metadata, dict) else None,
    )
    for value in values:
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def normalize_change_id(value: Any) -> str:
    text = str(value or "").strip()
    match = CHANGE_RE.search(text)
    return match.group(0).upper() if match else text or "-"


def job_to_match(job: dict[str, Any], source: str) -> JobMatch:
    metadata = job.get("metadata") or {}
    payload = parse_job_payload(job)
    initiator = "<not recorded>"
    if isinstance(metadata, dict):
        initiator = str(metadata.get("added_by") or initiator)
    return JobMatch(
        job_id=str(job.get("id") or "-"),
        job_state=str(job.get("state") or "UNKNOWN"),
        initiator=initiator,
        change_id=normalize_change_id(payload.get("changeId")),
        event_time=job_event_time(job),
        source=source,
    )


def choose_job(
    state_record: StateRecord,
    jobs: Sequence[dict[str, Any]],
    window_minutes: int,
    source: str,
) -> Optional[JobMatch]:
    if state_record.set_on is None:
        return None

    maximum_delta = window_minutes * 60
    candidates: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
    for job in jobs:
        if not is_live_inservice_job(job) or state_record.name not in job_target_names(job):
            continue
        event_time = job_event_time(job)
        if event_time is None:
            continue
        delta = abs((event_time - state_record.set_on).total_seconds())
        if delta > maximum_delta:
            continue

        request = job.get("request") or {}
        parent_id = request.get("parent_job_id") if isinstance(request, dict) else None
        job_type = str(request.get("job_type") or "") if isinstance(request, dict) else ""
        root_rank = 0 if not parent_id else 1
        type_rank = 0 if job_type == "VALIDATED_SET_STATE" else 1
        success_rank = 0 if str(job.get("state") or "").upper() == "SUCCEEDED" else 1
        candidates.append(((root_rank, type_rank, success_rank, delta), job))

    if not candidates:
        return None
    _, selected = min(candidates, key=lambda candidate: candidate[0])
    return job_to_match(selected, source)


def regional_job_matches(
    runner: NcpcliRunner,
    records: Sequence[StateRecord],
    window_minutes: int,
) -> tuple[dict[str, JobMatch], list[str]]:
    all_jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for start, end in merged_query_windows(records, window_minutes):
        command = [
            "ncp-job",
            "list",
            "--job-name",
            "VALIDATED_SET_STATE",
            "--from",
            ncp_date(start),
            "--until",
            ncp_date(end),
            "--json",
            "--include-payload",
        ]
        try:
            all_jobs.extend(extract_json_array(runner.run(command)))
        except AuditError as exc:
            errors.append(str(exc))

    matches: dict[str, JobMatch] = {}
    for record in records:
        match = choose_job(record, all_jobs, window_minutes, "regional NCP job history")
        if match:
            matches[record.name] = match
    return matches, errors


def device_job_match(
    runner: NcpcliRunner, record: StateRecord, window_minutes: int
) -> tuple[str, Optional[JobMatch], Optional[str]]:
    if record.set_on is None:
        return record.name, None, None
    margin = dt.timedelta(minutes=window_minutes)
    command = [
        "ncp-job",
        "list",
        "--device",
        record.name,
        "--from",
        ncp_date(record.set_on - margin),
        "--until",
        ncp_date(record.set_on + margin),
        "--json",
        "--include-payload",
    ]
    try:
        jobs = extract_json_array(runner.run(command))
        return (
            record.name,
            choose_job(record, jobs, window_minutes, "per-device NCP job history"),
            None,
        )
    except AuditError as exc:
        return record.name, None, str(exc)


def resolve_creator(runner: NcpcliRunner, match: JobMatch) -> tuple[str, str, str]:
    """Return raw initiator, resolved initiator, and optional change ID."""
    command = [
        "ncp-job",
        "get",
        "--suppress-child-jobs",
        "--no-include-payload",
        match.job_id,
    ]
    try:
        output = strip_ansi(runner.run(command))
    except AuditError:
        return match.initiator, match.initiator, match.change_id

    created_by_match = re.search(r"(?m)^\s*Created By:\s*(.+?)\s*$", output)
    change_match = re.search(r"(?m)^\s*Jira Ticket:\s*(.+?)\s*$", output)
    resolved = created_by_match.group(1).strip() if created_by_match else match.initiator
    change_id = (
        normalize_change_id(change_match.group(1)) if change_match else match.change_id
    )
    return match.initiator, resolved, change_id


def is_identity_reference(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("ocid1.") or lowered.startswith(SERVICE_PREFIXES)


def resolve_job_creators(
    runner: NcpcliRunner, matches: dict[str, JobMatch], workers: int
) -> None:
    representatives: dict[str, list[JobMatch]] = {}
    for match in matches.values():
        if is_identity_reference(match.initiator) and match.job_id != "-":
            candidates = representatives.setdefault(match.initiator, [])
            if all(candidate.job_id != match.job_id for candidate in candidates):
                candidates.append(match)
    if not representatives:
        return

    def resolve_candidates(
        raw_identity: str, candidates: Sequence[JobMatch]
    ) -> tuple[str, str]:
        # Identity lookup can fail transiently while the job lookup itself
        # succeeds. Try a few jobs created by the same identity.
        for match in candidates[:3]:
            _, username, _ = resolve_creator(runner, match)
            if username != raw_identity and not is_identity_reference(username):
                return raw_identity, username
        return raw_identity, raw_identity

    resolved: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(workers, len(representatives))
    ) as executor:
        futures = {
            executor.submit(resolve_candidates, raw, candidates): raw
            for raw, candidates in representatives.items()
        }
        for future in concurrent.futures.as_completed(futures):
            raw, username = future.result()
            resolved[raw] = username

    for match in matches.values():
        if match.initiator in resolved:
            match.initiator = resolved[match.initiator]


def find_job_matches(
    runner: NcpcliRunner,
    records: Sequence[StateRecord],
    window_minutes: int,
    workers: int,
    device_fallback: bool,
) -> tuple[dict[str, JobMatch], list[str], list[str]]:
    """Resolve best-effort human initiators for in-service state records."""
    matches, errors = regional_job_matches(runner, records, window_minutes)
    unresolved = [record for record in records if record.name not in matches]

    if unresolved and device_fallback:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(unresolved))
        ) as executor:
            futures = [
                executor.submit(device_job_match, runner, record, window_minutes)
                for record in unresolved
            ]
            for future in concurrent.futures.as_completed(futures):
                name, match, error = future.result()
                if match:
                    matches[name] = match
                if error:
                    errors.append(error)

    resolve_job_creators(runner, matches, workers)
    unresolved_names = [record.name for record in records if record.name not in matches]
    return matches, errors, unresolved_names


def natural_key(value: str) -> list[Any]:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", value)]


def device_state(
    device: InventoryDevice, state_by_name: dict[str, StateRecord]
) -> str:
    state_record = state_by_name.get(device.name)
    return (
        state_record.state.strip().lower()
        if state_record is not None
        else device.state.strip().lower()
    ) or "not set"


def rack_groups(
    inventory: Sequence[InventoryDevice],
) -> dict[str, list[InventoryDevice]]:
    groups: dict[str, list[InventoryDevice]] = {}
    for device in inventory:
        groups.setdefault(device.rack, []).append(device)
    return groups


def fully_inservice_device_names(
    inventory: Sequence[InventoryDevice],
) -> list[str]:
    names: list[str] = []
    for devices in rack_groups(inventory).values():
        if devices and all(device.state == "in-service" for device in devices):
            names.extend(device.name for device in devices)
    return dedupe(names)


def rack_completion_records(
    inventory: Sequence[InventoryDevice], state_records: Sequence[StateRecord]
) -> dict[str, StateRecord]:
    """Pick the last device transition that made each rack fully in-service."""
    state_by_name = {record.name: record for record in state_records}
    completion: dict[str, StateRecord] = {}
    for rack, devices in rack_groups(inventory).items():
        states = [device_state(device, state_by_name) for device in devices]
        if not states or any(state != "in-service" for state in states):
            continue
        candidates = [
            state_by_name[device.name]
            for device in devices
            if device.name in state_by_name
        ]
        if not candidates:
            continue
        # Unknown timestamps sort first; a known latest timestamp is preferred.
        completion[rack] = max(
            candidates,
            key=lambda record: record.set_on or dt.datetime.min.replace(tzinfo=UTC),
        )
    return completion


def state_breakdown(states: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    ordered = sorted(
        counts,
        key=lambda state: (STATE_ORDER.get(state, 99), natural_key(state)),
    )
    return ", ".join(f"{state}={counts[state]}" for state in ordered)


def build_rack_audit_records(
    physical_racks: Sequence[PhysicalRack],
    inventory: Sequence[InventoryDevice],
    state_records: Sequence[StateRecord],
    completion: dict[str, StateRecord],
    job_matches: dict[str, JobMatch],
    timezone: dt.tzinfo,
) -> list[RackAuditRecord]:
    state_by_name = {record.name: record for record in state_records}
    devices_by_rack = rack_groups(inventory)
    physical_by_name = {rack.rack: rack for rack in physical_racks}
    # Preserve Plan-only racks if the local rackmap is temporarily behind.
    for rack_name in devices_by_rack:
        physical_by_name.setdefault(
            rack_name,
            PhysicalRack(
                rack=rack_name,
                block="plan-only",
                platform="-",
                aid="-",
            ),
        )

    output: list[RackAuditRecord] = []
    for rack, physical in physical_by_name.items():
        devices = devices_by_rack.get(rack, [])
        states = [device_state(device, state_by_name) for device in devices]
        unique_states = set(states)
        if not states:
            status = "no-managed-devices"
        else:
            status = next(iter(unique_states)) if len(unique_states) == 1 else "mixed"
        completion_record = completion.get(rack)
        job = job_matches.get(completion_record.name) if completion_record else None

        output.append(
            RackAuditRecord(
                block=physical.block,
                rack=rack,
                platform=physical.platform,
                status=status,
                devices=len(devices),
                state_breakdown=state_breakdown(states) if states else "-",
                in_service_since=(
                    format_timestamp(completion_record.set_on, timezone)
                    if status == "in-service" and completion_record
                    else "-"
                ),
                completed_by=(
                    job.initiator
                    if job
                    else completion_record.set_by
                    if status == "in-service" and completion_record
                    else "-"
                ),
                change_id=job.change_id if job else "-",
                completion_device=(
                    completion_record.name
                    if status == "in-service" and completion_record
                    else "-"
                ),
                job_id=job.job_id if job else "-",
                job_state=job.job_state if job else "-",
                source=(
                    job.source
                    if job
                    else "State Store"
                    if status == "in-service" and completion_record
                    else "-"
                ),
            )
        )
    return sorted(output, key=lambda record: natural_key(record.rack))


def build_audit_records(
    state_records: Sequence[StateRecord],
    job_matches: dict[str, JobMatch],
    timezone: dt.tzinfo,
) -> list[AuditRecord]:
    output: list[AuditRecord] = []
    for state in state_records:
        job = job_matches.get(state.name)
        output.append(
            AuditRecord(
                device=state.name,
                current_state=state.state,
                state_set_on=format_timestamp(state.set_on, timezone),
                initiated_by=job.initiator if job else state.set_by,
                change_id=job.change_id if job else "-",
                job_id=job.job_id if job else "-",
                job_state=job.job_state if job else "-",
                source=job.source if job else "State Store",
            )
        )
    return output


def terminal_supports_color(disabled: bool = False) -> bool:
    """Use ANSI color only for an interactive terminal that has not opted out."""
    return (
        not disabled
        and sys.stdout.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM", "").lower() != "dumb"
    )


def terminal_supports_unicode() -> bool:
    if not sys.stdout.isatty():
        return False
    encoding = sys.stdout.encoding or "utf-8"
    try:
        "─│┌┐└┘├┤┬┴┼•".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def styled(value: str, ansi_code: str, enabled: bool) -> str:
    return f"\033[{ansi_code}m{value}\033[0m" if enabled else value


def pretty_status(value: str) -> str:
    return value.replace("-", " ").title()


def pretty_breakdown(value: str, unicode_output: bool) -> str:
    if value == "-":
        return value
    separator = " · " if unicode_output else ", "
    parts: list[str] = []
    for item in value.split(","):
        state, marker, count = item.strip().partition("=")
        parts.append(f"{count} {pretty_status(state)}" if marker else item.strip())
    return separator.join(parts)


def print_section(title: str, color: bool) -> None:
    print(f"\n{styled(title, '1;36', color)}")


def print_box_table(
    records: Sequence[Any],
    columns: Sequence[tuple[str, str, str]],
    color: bool,
) -> None:
    """Print a dependency-free table with clean borders and aligned numbers."""
    unicode_output = terminal_supports_unicode()

    def display_value(record: Any, attribute: str) -> str:
        value = str(getattr(record, attribute))
        if value == "-" and unicode_output:
            return "—"
        if attribute in {"status", "current_state"}:
            value = pretty_status(value)
        if attribute == "state_breakdown":
            value = pretty_breakdown(value, unicode_output)

        if sys.stdout.isatty():
            limits = {
                "completed_by": 24,
                "initiated_by": 24,
                "state_breakdown": 32,
                "platform": 24,
                "completion_device": 36,
                "device": 42,
                "job_id": 36,
                "source": 24,
            }
            limit = limits.get(attribute)
            if limit and len(value) > limit:
                ellipsis = "…" if unicode_output else "..."
                value = value[: limit - len(ellipsis)] + ellipsis
        return value

    values = [
        [display_value(record, attribute) for _, attribute, _ in columns]
        for record in records
    ]
    widths = [
        max(len(heading), *(len(row[index]) for row in values))
        for index, (heading, _, _) in enumerate(columns)
    ]

    if unicode_output:
        top = ("┌", "┬", "┐", "─")
        middle = ("├", "┼", "┤", "─")
        bottom = ("└", "┴", "┘", "─")
        vertical = "│"
    else:
        top = middle = bottom = ("+", "+", "+", "-")
        vertical = "|"

    def border(parts: tuple[str, str, str, str]) -> str:
        left, join, right, horizontal = parts
        return left + join.join(horizontal * (width + 2) for width in widths) + right

    def rendered_row(row_values: Sequence[str], headings: bool = False) -> str:
        cells: list[str] = []
        for value, width, (_, attribute, alignment) in zip(row_values, widths, columns):
            padded = value.rjust(width) if alignment == "right" else value.ljust(width)
            if headings:
                padded = styled(padded, "1", color)
            elif attribute in {"status", "current_state"}:
                raw_status = value.lower().replace(" ", "-")
                padded = styled(padded, STATUS_COLORS.get(raw_status, "37"), color)
            cells.append(f" {padded} ")
        return vertical + vertical.join(cells) + vertical

    print(border(top))
    print(rendered_row([heading for heading, _, _ in columns], headings=True))
    print(border(middle))
    for row_values in values:
        print(rendered_row(row_values))
    print(border(bottom))


def print_status_summary(
    counts: dict[str, int], total: int, color: bool
) -> None:
    unicode_output = terminal_supports_unicode()
    bullet = "●" if unicode_output else "-"
    separator = "   "
    raw_parts = [f"Total {total}"] + [
        f"{bullet} {pretty_status(status)} {count}" for status, count in counts.items()
    ]
    styled_parts = [styled(raw_parts[0], "1", color)]
    for raw, status in zip(raw_parts[1:], counts):
        styled_parts.append(styled(raw, STATUS_COLORS.get(status, "37"), color))

    terminal_width = max(40, shutil.get_terminal_size((120, 20)).columns)
    current_raw = "  "
    current_styled = "  "
    for raw, rendered in zip(raw_parts, styled_parts):
        candidate = raw if current_raw == "  " else separator + raw
        candidate_styled = rendered if current_raw == "  " else separator + rendered
        if current_raw != "  " and len(current_raw) + len(candidate) > terminal_width:
            print(current_styled)
            current_raw = "  " + raw
            current_styled = "  " + rendered
        else:
            current_raw += candidate
            current_styled += candidate_styled
    print(current_styled)


def print_table(records: Sequence[AuditRecord], verbose: bool, color: bool) -> None:
    columns: list[tuple[str, str, str]] = [
        ("Device", "device", "left"),
        ("Current State", "current_state", "left"),
        ("State Set On", "state_set_on", "left"),
        ("Initiated By", "initiated_by", "left"),
        ("Change", "change_id", "left"),
    ]
    if verbose:
        columns.extend(
            [
                ("Job ID", "job_id", "left"),
                ("Job State", "job_state", "left"),
                ("Source", "source", "left"),
            ]
        )
    print_box_table(records, columns, color)


def print_rack_table(
    records: Sequence[RackAuditRecord],
    verbose: bool,
    color: bool,
    qfab_only: bool = True,
) -> None:
    terminal_width = (
        shutil.get_terminal_size((120, 20)).columns if sys.stdout.isatty() else 10_000
    )
    has_in_service = any(record.status == "in-service" for record in records)
    columns: list[tuple[str, str, str]] = [
        ("Rack", "rack", "left"),
        ("Status", "status", "left"),
        ("QFABs" if qfab_only else "Devices", "devices", "right"),
    ]
    show_breakdown = not has_in_service or terminal_width >= 100 or verbose
    if show_breakdown:
        columns.append(("Device States", "state_breakdown", "left"))
    if has_in_service:
        columns.extend(
            [
                ("In Service Since", "in_service_since", "left"),
                ("Completed By", "completed_by", "left"),
            ]
        )
        if terminal_width >= 120 or verbose:
            columns.append(("Change", "change_id", "left"))
    if verbose:
        columns.extend(
            [
                ("Block", "block", "left"),
                ("Platform", "platform", "left"),
                ("Completion Device", "completion_device", "left"),
                ("Job ID", "job_id", "left"),
                ("Job State", "job_state", "left"),
                ("Source", "source", "left"),
            ]
        )
    print_box_table(records, columns, color)
    if not show_breakdown:
        mixed = [record for record in records if record.status == "mixed"]
        if mixed:
            print("  Mixed-rack details:")
            for record in mixed:
                detail = pretty_breakdown(
                    record.state_breakdown, terminal_supports_unicode()
                )
                print(f"    {record.rack}: {detail}")


def write_csv(
    path: Path, records: Sequence[Any], record_type: type
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(record_type.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


def status_counts(records: Sequence[RackAuditRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    return dict(
        sorted(
            counts.items(),
            key=lambda item: (STATE_ORDER.get(item[0], 99), natural_key(item[0])),
        )
    )


def run_building_audit(
    args: argparse.Namespace,
    runner: NcpcliRunner,
    building: str,
    region: str,
    timezone: dt.tzinfo,
    progress: TerminalProgress,
) -> int:
    with progress.task(f"Loading {building.upper()} rack inventory"):
        all_physical_racks, rackmap_path = load_physical_racks(
            building, region, args.rackmap
        )
    discovery_scope = "QFAB" if args.qfab_only else "network"
    with progress.task(
        f"Discovering {discovery_scope} devices in {building.upper()}"
    ):
        all_inventory = fetch_building_inventory(runner, building)
    inventory = (
        [
            device
            for device in all_inventory
            if device.role.strip().lower().startswith("qfab")
        ]
        if args.qfab_only
        else all_inventory
    )
    if not inventory:
        scope_name = "QFAB devices" if args.qfab_only else "NCP network devices"
        raise AuditError(f"No {scope_name} were found in {building}.")

    relevant_rack_names = {device.rack for device in inventory}
    physical_racks = [
        rack for rack in all_physical_racks if rack.rack in relevant_rack_names
    ]
    if args.summary_only:
        state_records: list[StateRecord] = []
    else:
        in_service_device_names = fully_inservice_device_names(inventory)
        if in_service_device_names:
            with progress.task(
                "Reading transition metadata for "
                f"{len(in_service_device_names)} in-service devices"
            ):
                state_records = fetch_state_records_chunked(
                    runner,
                    in_service_device_names,
                    args.state_chunk_size,
                )
        else:
            state_records = []
    completion = rack_completion_records(inventory, state_records)
    completion_records = list(completion.values())

    job_matches: dict[str, JobMatch] = {}
    lookup_errors: list[str] = []
    unresolved_jobs: list[str] = []
    if completion_records and not args.state_store_only:
        fallback = args.device_fallback is True
        with progress.task(
            f"Resolving completion history for {len(completion_records)} racks"
        ):
            job_matches, lookup_errors, unresolved_jobs = find_job_matches(
                runner,
                completion_records,
                args.window_minutes,
                args.workers,
                fallback,
            )

    rack_records = build_rack_audit_records(
        physical_racks,
        inventory,
        state_records,
        completion,
        job_matches,
        timezone,
    )
    displayed = (
        [record for record in rack_records if record.status == "in-service"]
        if args.in_service_only
        else rack_records
    )
    output_rows = [] if args.summary_only else displayed
    counts = status_counts(rack_records)
    plan_racks = len({device.rack for device in inventory})
    rackmap_names = {rack.rack for rack in physical_racks}
    plan_only_racks = len({device.rack for device in inventory} - rackmap_names)
    total_reported_racks = len(rack_records)

    if lookup_errors and args.debug:
        for error in dedupe(lookup_errors):
            print(f"Job-history warning: {error}", file=sys.stderr)
    if unresolved_jobs:
        unresolved_racks = sorted(
            (
                rack
                for rack, state in completion.items()
                if state.name in set(unresolved_jobs)
            ),
            key=natural_key,
        )
        print(
            "Warning: no matching human-initiator NCP job was found for "
            f"{len(unresolved_racks)} in-service rack(s); State Store identity is shown instead.",
            file=sys.stderr,
        )

    if args.csv:
        write_csv(args.csv, output_rows, RackAuditRecord)
        print(f"CSV written to {args.csv.resolve()}", file=sys.stderr)

    summary = {
        "building": building,
        "region": region,
        "scope": "qfab" if args.qfab_only else "all-network-devices",
        "rackmap": str(rackmap_path),
        "rack_locations_in_production_rackmap": len(physical_racks),
        "racks_with_ncp_network_devices": plan_racks,
        "devices_in_scope": len(inventory),
        "additional_plan_only_racks": plan_only_racks,
        "total_racks_reported": total_reported_racks,
        "status_counts": counts,
    }
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "racks": [asdict(record) for record in output_rows],
                },
                indent=2,
            )
        )
        return 0

    color = terminal_supports_color(args.no_color)
    scope_label = "QFAB" if args.qfab_only else "network"
    unicode_output = terminal_supports_unicode()
    title_separator = "—" if unicode_output else "-"
    title = f"{scope_label.upper()} Rack Status {title_separator} {building.upper()}"
    rule = "━" if unicode_output else "="
    print(f"\n{styled(title, '1;36', color)}")
    print(rule * len(title))
    metadata_separator = "  •  " if unicode_output else "  |  "
    print(
        f"Region {region.upper()}"
        f"{metadata_separator}Scope {scope_label} only"
        f"{metadata_separator}Source {rackmap_path.name}"
    )
    if args.verbose:
        print(f"Rackmap {rackmap_path}")
    print(
        f"Inventory {len(physical_racks)} rackmap + {plan_only_racks} Plan-only "
        f"= {total_reported_racks} racks{metadata_separator}{len(inventory)} devices"
    )

    print_section("Status Summary", color)
    print_status_summary(counts, total_reported_racks, color)

    note = (
        f"Status is derived from current {scope_label} device states; a rack is "
        f"In Service only when every included {scope_label} device is In Service."
    )
    note_width = max(40, shutil.get_terminal_size((120, 20)).columns)
    wrapped_note = textwrap.fill(
        note,
        width=note_width,
        initial_indent="  ",
        subsequent_indent="  ",
    )
    print(styled(wrapped_note, "2", color))
    if counts.get("in-service", 0):
        completion_note = (
            "Who/when reflects the final device transition that completed the rack."
        )
        wrapped_completion_note = textwrap.fill(
            completion_note,
            width=note_width,
            initial_indent="  ",
            subsequent_indent="  ",
        )
        print(styled(wrapped_completion_note, "2", color))
    if args.summary_only:
        return 0

    detail_title = f"Rack Details ({len(output_rows)} shown)"
    if len(output_rows) != total_reported_racks:
        detail_title = (
            f"Rack Details ({len(output_rows)} of {total_reported_racks} shown)"
        )
    print_section(detail_title, color)
    if output_rows:
        print_rack_table(output_rows, args.verbose, color, args.qfab_only)
    else:
        print("  No racks matched the requested output filter.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Count physical racks in a building, summarize their derived lifecycle "
            "status, and report who/when completed in-service racks."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Building code (recommended, e.g. iad64/fbb1) or exact device names.",
    )
    parser.add_argument(
        "-b",
        "--building",
        help="Building code, e.g. iad64 or fbb1. NCP region is inferred.",
    )
    parser.add_argument(
        "-r",
        "--region",
        help="NCPCLI region, e.g. iad/fbb; `-r iad64` also starts building mode.",
    )
    parser.add_argument(
        "--device",
        "--exact-device",
        action="append",
        default=[],
        help="Exact device name; repeat or provide comma-separated names.",
    )
    parser.add_argument(
        "--device-pattern",
        "--devices",
        action="append",
        default=[],
        help="Device glob, e.g. 'iad64-q1-b2-t1-r*'; repeatable.",
    )
    parser.add_argument(
        "--device-file",
        "--devices-from-file",
        type=Path,
        help="Text file or pasted table containing device names.",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="Display timezone, e.g. UTC or Asia/Kolkata.",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=10,
        help="Maximum time difference when matching State Store and NCP jobs.",
    )
    parser.add_argument(
        "--state-store-only",
        action="store_true",
        help="Skip NCP job cross-reference (faster, but may show a service principal).",
    )
    fallback = parser.add_mutually_exclusive_group()
    fallback.add_argument(
        "--device-fallback",
        dest="device_fallback",
        action="store_true",
        help="For unresolved racks/devices, run slower per-device NCP job queries.",
    )
    fallback.add_argument(
        "--no-device-fallback",
        dest="device_fallback",
        action="store_false",
        help="Do not run per-device NCP job queries after the regional lookup.",
    )
    parser.add_argument(
        "--include-not-in-service",
        action="store_true",
        help="Also display selected devices whose current state is not in-service.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--csv", type=Path, help="Also write all displayed rows to CSV.")
    parser.add_argument(
        "--rackmap",
        type=Path,
        help="Production rackmap path; normally auto-detected from ~/autonet.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Building mode: print counts without the per-rack table.",
    )
    parser.add_argument(
        "--in-service-only",
        action="store_true",
        help="Building mode: show only racks whose managed devices are all in-service.",
    )
    rack_scope = parser.add_mutually_exclusive_group()
    rack_scope.add_argument(
        "--qfab-only",
        dest="qfab_only",
        action="store_true",
        help="Building mode: include only racks containing QFAB-role devices.",
    )
    rack_scope.add_argument(
        "--all-racks",
        dest="qfab_only",
        action="store_false",
        help="Building mode: include racks for every NCP network-device role.",
    )
    parser.add_argument("--verbose", action="store_true", help="Show job/source columns.")
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable terminal colors (also honored automatically for redirected output).",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the interactive spinner and elapsed-time updates.",
    )
    parser.add_argument("--debug", action="store_true", help="Print NCPCLI commands.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--state-chunk-size",
        type=int,
        default=50,
        help="Building mode: exact devices per State Store query.",
    )
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per NCPCLI call.")
    parser.add_argument("--ncpcli-bin", help="Path or command name for ncpcli.")
    parser.add_argument(
        "--no-agent-auth",
        dest="use_agent_for_auth",
        action="store_false",
        help="Do not pass use_agent_for_auth=true to ncpcli.",
    )
    parser.set_defaults(
        use_agent_for_auth=True,
        device_fallback=None,
        qfab_only=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.window_minutes <= 0:
        raise AuditError("--window-minutes must be greater than zero.")
    if args.workers <= 0:
        raise AuditError("--workers must be greater than zero.")
    if args.state_chunk_size <= 0:
        raise AuditError("--state-chunk-size must be greater than zero.")

    positional = split_csv(args.targets)
    building = args.building.lower() if args.building else None
    has_explicit_device_scope = bool(args.device or args.device_pattern or args.device_file)

    # Make the common commands simple: `script.py iad64` or `script.py -r iad64`.
    if not building and not has_explicit_device_scope and len(positional) == 1:
        if BUILDING_RE.fullmatch(positional[0]):
            building = positional.pop().lower()
    if (
        not building
        and not has_explicit_device_scope
        and not positional
        and args.region
        and BUILDING_RE.fullmatch(args.region)
    ):
        building = args.region.lower()
        args.region = None

    if building and (positional or has_explicit_device_scope):
        raise AuditError("Do not combine --building with device selectors.")
    if building and not BUILDING_RE.fullmatch(building):
        raise AuditError(f"Building must look like iad64 or fbb1: {building}")

    if building:
        derived_region = building[:3].lower()
        region = (args.region or derived_region).lower()
        if region != derived_region:
            raise AuditError(
                f"Building {building} belongs to region {derived_region}, not {region}."
            )
        exact_devices: list[str] = []
        patterns: list[str] = []
    else:
        if not args.region:
            raise AuditError(
                "Provide a building (e.g. iad64/fbb1) or -r REGION with device selectors."
            )
        region = args.region.lower()
        exact_devices = positional + split_csv(args.device)
        if args.device_file:
            exact_devices.extend(read_device_file(args.device_file))
        exact_devices = dedupe(exact_devices)
        patterns = dedupe(split_csv(args.device_pattern))
        if not exact_devices and not patterns:
            raise AuditError(
                "Provide a building or at least one device, --device-pattern, or --device-file."
            )

    if ZoneInfo is None:
        if args.timezone.upper() != "UTC":
            raise AuditError("This Python version only supports --timezone UTC.")
        timezone: dt.tzinfo = UTC
    else:
        try:
            timezone = ZoneInfo(args.timezone)
        except Exception as exc:
            raise AuditError(f"Unknown timezone: {args.timezone}") from exc

    runner = NcpcliRunner(
        binary=resolve_ncpcli(args.ncpcli_bin),
        region=region,
        timeout=args.timeout,
        use_agent_for_auth=args.use_agent_for_auth,
        debug=args.debug,
    )
    progress = TerminalProgress(
        requested=not args.no_progress and not args.debug,
        color=not args.no_color,
    )

    if building:
        return run_building_audit(
            args, runner, building, region, timezone, progress
        )

    target_count = len(exact_devices) + len(patterns)
    target_label = "selector" if target_count == 1 else "selectors"
    with progress.task(f"Reading device states for {target_count} {target_label}"):
        state_records = fetch_state_records(runner, exact_devices, patterns)
    found = {record.name for record in state_records}
    missing = [device for device in exact_devices if device not in found]
    if missing:
        print(
            "Warning: no State Store result for: " + ", ".join(missing),
            file=sys.stderr,
        )

    inservice = [
        record for record in state_records if record.state.strip().lower() == "in-service"
    ]
    not_inservice = [record for record in state_records if record not in inservice]
    if not_inservice and not args.include_not_in_service:
        print(
            "Skipped devices not currently in-service: "
            + ", ".join(record.name for record in not_inservice),
            file=sys.stderr,
        )

    job_matches: dict[str, JobMatch] = {}
    lookup_errors: list[str] = []
    unresolved_jobs: list[str] = []
    if inservice and not args.state_store_only:
        fallback_enabled = args.device_fallback is not False
        device_label = "device" if len(inservice) == 1 else "devices"
        with progress.task(
            f"Resolving transition history for {len(inservice)} {device_label}"
        ):
            job_matches, lookup_errors, unresolved_jobs = find_job_matches(
                runner,
                inservice,
                args.window_minutes,
                args.workers,
                fallback_enabled,
            )

    displayed_states = list(inservice)
    if args.include_not_in_service:
        displayed_states.extend(not_inservice)
        displayed_states.sort(key=lambda record: record.name)
    output = build_audit_records(displayed_states, job_matches, timezone)

    if lookup_errors and args.debug:
        for error in dedupe(lookup_errors):
            print(f"Job-history warning: {error}", file=sys.stderr)
    if unresolved_jobs:
        print(
            "Warning: no matching NCP initiator job was found for: "
            + ", ".join(unresolved_jobs)
            + ". Showing State Store identity instead.",
            file=sys.stderr,
        )

    if args.csv:
        write_csv(args.csv, output, AuditRecord)
        print(f"CSV written to {args.csv.resolve()}", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(record) for record in output], indent=2))
    elif output:
        print_table(output, args.verbose, terminal_supports_color(args.no_color))
    else:
        print("No selected devices are currently in-service.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
