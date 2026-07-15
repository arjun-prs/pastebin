# pastebin

This repository contains operational helper scripts, one-off parsers, and workflow tools. This README documents every script file found in the repository and, where the script exposes a parser, every CLI tag/flag that can be extracted statically from `argparse` or `click`.

_Last refreshed: 2026-07-15._

## How to read this file

- "Tag" means a CLI option or argument such as `--site`, `-r`, or a positional value.
- Tags are extracted from `argparse.add_argument(...)` and `click.option(...)` / `click.argument(...)` definitions. Scripts that build parsers dynamically or read `sys.argv` directly may have additional behavior not visible to static extraction.
- If a script has no documented parser, this README says so. Those scripts usually use hard-coded filenames, direct `sys.argv` access, imported helper functions, or are meant to be edited before running.
- Scripts that SSH to devices, change switch configuration, reboot devices, create/apply change sets, or write tickets should be reviewed before use.

## Coverage

- Script files documented: `66`
- Included file types: `.py`, `.sh`, and the Office Script named `Script to validate layouts`.
- Hidden folders, virtual environments, git metadata, caches, and dependency folders are excluded.

## Script inventory

| Script | What it does | CLI tags documented |
| --- | --- | --- |
| [`abl_dedupe_spine_leaf_reports_arjun.py`](#script-abl-dedupe-spine-leaf-reports-arjun-py) | Deduplicates and summarizes ABL spine/leaf validation reports for recurring backend-network reporting. | 8 |
| [`aga_dg_report_recalculator.py`](#script-aga-dg-report-recalculator-py) | Recalculates AGA deployment-group progress from pasted report text or qcli full-report workbooks. | 20 |
| [`cfab_qfab_mapping.py`](#script-cfab-qfab-mapping-py) | Extracts CFAB/QFAB/GFAB fabric-block relationships from rackmap files and writes mapping analysis to Excel. | none found |
| [`changeset-add.py`](#script-changeset-add-py) | Builds rack-SKU add/change-set payloads from rackmap JSON data. | 5 |
| [`changeset-deletion.py`](#script-changeset-deletion-py) | Builds rack deletion change-set payloads from rackmap JSON data. | 5 |
| [`changeset-modify.py`](#script-changeset-modify-py) | Builds rack SKU modification change-set payloads from rackmap JSON data. | 5 |
| [`config-refresh-version-file-update.py`](#script-config-refresh-version-file-update-py) | Trims extracted config-refresh version blocks to the first two CSV fields. | none found |
| [`config_refresh_version_grab.py`](#script-config-refresh-version-grab-py) | Extracts version sections from raw `devices compare-config --latest` output. | none found |
| [`cutsheet_data_fill_auto_input.py`](#script-cutsheet-data-fill-auto-input-py) | Converts simple switch/port input into rows useful for cutsheet auto-fill workflows. | none found |
| [`cutsheet_preflight.py`](#script-cutsheet-preflight-py) | Validates location CSVs against expected QFAB/CFAB hardware counts, rack ordering, and layout prerequisites. | none found |
| [`data-extract_lldp_failures_3.py`](#script-data-extract-lldp-failures-3-py) | Parses LLDP failure text into expected/current connection rows for follow-up analysis. | none found |
| [`data_extract_test_optics_new.py`](#script-data-extract-test-optics-new-py) | Extracts `test_optics` failure objects from health-check output and writes `test_optics_failures.csv`. | none found |
| [`decorate_removed_links.py`](#script-decorate-removed-links-py) | Replaces GUID/port values in removed-link output with readable device and port information. | none found |
| [`device_inservice_audit.py`](#script-device-inservice-audit-py) | Reports rack/device lifecycle status and who completed in-service transitions using read-only NCPCLI data. | 28 |
| [`device_state_audit.py`](#script-device-state-audit-py) | Summarizes deployed, new, mixed, maintenance, and in-service device/rack state for a region or site. | 17 |
| [`dot1x_bug_validation.py`](#script-dot1x-bug-validation-py) | SSHes to management switches and compares successful dot1x hosts against VLAN assignment data. | none found |
| [`fabric_built_verification_tool.py`](#script-fabric-built-verification-tool-py) | Collects serial numbers from devices over SSH and enriches them with Storekeeper asset data. | 3 |
| [`fabric_built_verification_tool_juniper.py`](#script-fabric-built-verification-tool-juniper-py) | Juniper-focused variant of fabric-built serial collection and Storekeeper enrichment. | 3 |
| [`fabric_links_count.py`](#script-fabric-links-count-py) | Downloads rackmap/topology data and counts expected or missing T0/T1/T2 fabric links. | 3 |
| [`fec_failure_inventory.py`](#script-fec-failure-inventory-py) | Checks a device for FEC BER failures and prints inventory details for failed interfaces. | 1 |
| [`fetch_bops_tickets.py`](#script-fetch-bops-tickets-py) | Maps rack numbers to unresolved BOPS master tickets using Jira/BOPS fields. | 14 |
| [`flap_validations.py`](#script-flap-validations-py) | Extracts flapping interfaces from validation output and matches them to cable-plan rows. | none found |
| [`flap_validations_notconnect_filter.py`](#script-flap-validations-notconnect-filter-py) | Flap-validation parser that SSHes to devices to filter links already showing notconnect/down evidence. | none found |
| [`get_devices_by_rack.py`](#script-get-devices-by-rack-py) | Queries Prometheus for devices associated with a rack. | 1 |
| [`get_optic_issue_details.py`](#script-get-optic-issue-details-py) | Enriches bad optical power lines with remote device/interface and remote rack/elevation from Prometheus. | 2 |
| [`get_t0_t1_racks.py`](#script-get-t0-t1-racks-py) | Extracts T0/T1 rack numbers from autonet cables CSV and rackmap data. | 9 |
| [`gpuRack_deviceLinking.py`](#script-gpurack-devicelinking-py) | Finds GPU rack links to QFAB/GFAB devices and prints rack relationships. | 2 |
| [`health_check_report_generator.py`](#script-health-check-report-generator-py) | Runs NCPCLI rack health checks and turns miscabling/optic findings into DCO-friendly reports. | 7 |
| [`hsg_dg_report_recalculator.py`](#script-hsg-dg-report-recalculator-py) | Recalculates HSG deployment-group progress from pasted report text or qcli full-report workbooks. | 20 |
| [`iad_dg_report_recalculator.py`](#script-iad-dg-report-recalculator-py) | Recalculates IAD deployment-group progress from pasted report text or qcli full-report workbooks. | 21 |
| [`ifab_fec_check.py`](#script-ifab-fec-check-py) | Checks symbol BER/FEC health on device ports listed in a device file and cutsheet. | 7 |
| [`jbp15_dg_report_recalculator.py`](#script-jbp15-dg-report-recalculator-py) | Processes JBP15 qcli workbooks/dashboard CSVs and builds DG progress reports plus qcli command helpers. | 17 |
| [`jbp19_dg_report_recalculator.py`](#script-jbp19-dg-report-recalculator-py) | Processes JBP19 qcli workbooks/dashboard CSVs and builds DG progress reports plus qcli command helpers. | 17 |
| [`link_flap_protection.py`](#script-link-flap-protection-py) | Enables or disables link flap-protection on Arista and NVIDIA devices over SSH. | 11 |
| [`link_flap_protection_by_t0_state.py`](#script-link-flap-protection-by-t0-state-py) | Enables/disables Cumulus T1 link flap-protection only for T0 peers that are not in service. | 29 |
| [`lldp_tool.py`](#script-lldp-tool-py) | Enables or disables LLDP on Arista/NVIDIA interfaces selected by host and interface patterns. | 10 |
| [`multiplanar_site_pre_checks.py`](#script-multiplanar-site-pre-checks-py) | Runs multiplanar pre-checks for ZTP, DAN/autonet runtime, certificates, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature. | 77 |
| [`multiplaner_placement_rack_info.py`](#script-multiplaner-placement-rack-info-py) | Consolidates rack-location CSV exports and calculates placement-group and link-count planning data. | none found |
| [`multiplaner_racktopo.py`](#script-multiplaner-racktopo-py) | Prints deployment-group and topology information for racks or devices. | 3 |
| [`ncp_firmware_upgrade_automation.py`](#script-ncp-firmware-upgrade-automation-py) | Previews and runs `ncpcli devices firmware upgrade` with repeatable rack/device scope and run logs. | 21 |
| [`ncpcli_conn_issues.py`](#script-ncpcli-conn-issues-py) | Fetches and parses link/connectivity issues using NCPCLI. | 3 |
| [`ncpcli_direct_fec_scan.py`](#script-ncpcli-direct-fec-scan-py) | Extracts device/interface pairs from text for direct FEC scan workflows. | none found |
| [`nps_portfolio_tracker.py`](#script-nps-portfolio-tracker-py) | Tracks NPS, stock, and PPF portfolio values with live NAV/price data and trailing return reporting. | 2 |
| [`nvidia_device_reboot.py`](#script-nvidia-device-reboot-py) | Reboots NVIDIA devices in controlled batches and verifies each batch recovers over SSH before continuing. | 13 |
| [`NVIDIA_Link_Flap.py`](#script-nvidia-link-flap-py) | Checks NVIDIA/Cumulus switches for link flap-protection violations from an autonet XLSX plan and can optionally clear them. | 17 |
| [`onet_automatic.py`](#script-onet-automatic-py) | Copies ONET upgrade files to devices and runs remote upgrade commands. | none found |
| [`onet_remote_firmware_upgrade.py`](#script-onet-remote-firmware-upgrade-py) | SSHes to a device and upgrades ONET firmware versions to known-good target versions. | 3 |
| [`optics_audit_Excel_report.py`](#script-optics-audit-excel-report-py) | Parses NCPCLI optics-related text output and creates an Excel audit report. | none found |
| [`parse_topospec_api_output.py`](#script-parse-topospec-api-output-py) | Parses UFM TopoSpec diff API output and enriches GUID/port links with rack/elevation context. | 3 |
| [`phx_dg_report_recalculator.py`](#script-phx-dg-report-recalculator-py) | Builds PHX deployment-group progress reports from qcli full-report workbooks. | 15 |
| [`qcli_hc_summary_gui.py`](#script-qcli-hc-summary-gui-py) | Local web UI that builds and launches `qcli hc-summary` commands. | 3 |
| [`qfab_arista_lldp_tool.py`](#script-qfab-arista-lldp-tool-py) | Enables/disables and verifies LLDP on GPU-facing QFAB switch interfaces discovered from NCPCLI. | 10 |
| [`rack_no_parser_from_plan_rack_file.py`](#script-rack-no-parser-from-plan-rack-file-py) | Parses plan/rack files and extracts rack numbers. | 4 |
| [`reboot_ifab_devices.py`](#script-reboot-ifab-devices-py) | Runs parallel SSH checks and reboot-style actions for IFAB devices. | none found |
| [`remove_aoc_output.py`](#script-remove-aoc-output-py) | Removes noisy AOC firmware output blocks from health-check text. | none found |
| [`restart_hostapd_batch.py`](#script-restart-hostapd-batch-py) | Restarts `hostapd` on devices in controlled SSH batches with logs and recovery checks. | 19 |
| [`run_ifab_commands.py`](#script-run-ifab-commands-py) | Runs arbitrary SSH actions on IFAB/network devices in parallel. | 2 |
| [`run_ifab_tests.py`](#script-run-ifab-tests-py) | Runs IFAB health checks, parses cabling/optics issues, and aggregates results. | 8 |
| [`Script to validate layouts`](#script-script-to-validate-layouts) | Office Script for Excel that creates validation pivot sheets from a raw layout export. | none found |
| [`silencer_management_tool.py`](#script-silencer-management-tool-py) | Creates, views, searches, and expires device/interface silencers for validation workflows. | none found |
| [`spectrum_port_check.py`](#script-spectrum-port-check-py) | Site-aware Spectrum switch port checker that resolves devices by hostname or rack/elevation/port, runs interface/LLDP/optic checks, clears link flap protection, and controls bounce behavior. | 8 |
| [`storekeeper_data_by_file.py`](#script-storekeeper-data-by-file-py) | Reads serial numbers from a file and fetches Storekeeper asset details into terminal/CSV output. | 1 |
| [`switch_guid_map.py`](#script-switch-guid-map-py) | Static HSG switch-to-GUID mapping used by TopoSpec and GUID enrichment helpers. | none found |
| [`swp_subinterface_bounce.py`](#script-swp-subinterface-bounce-py) | Finds NVIDIA/Cumulus `swp` subinterfaces that are not up/up and can bounce affected lanes. | 6 |
| [`unhealthy_api_client.sh`](#script-unhealthy-api-client-sh) | Shell helper for querying an unhealthy API endpoint with a canned request pattern. | none found |
| [`work_tracker.py`](#script-work-tracker-py) | Generates a workbook summarizing validation and Codex/script effort over time. | 7 |

## Detailed script reference

<a id="script-abl-dedupe-spine-leaf-reports-arjun-py"></a>
### `abl_dedupe_spine_leaf_reports_arjun.py`

**Purpose:** Deduplicates and summarizes ABL spine/leaf validation reports for recurring backend-network reporting.

**Typical help command:**

```bash
python3 abl_dedupe_spine_leaf_reports_arjun.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--tag`, `--report-tag` | f"Report tag for auto-discovery and default output location ({', '.join(sorted(REPORT_TAGS))})." | required=True; dest=report_tag; type=normalize_report_tag |
| `--leaf-DH`, `--leaf-report` | Leaf-side full spine/bleaf report. If omitted, the latest default leaf-DH full_spine_bleaf report for the selected tag is used. | dest=leaf_report |
| `--spine-DH1`, `--spine-report-dh1` | Spine-side DH1 report. | dest=spine_report_dh1 |
| `--spine-DH2`, `--spine-report-dh2` | Spine-side DH2 report. | dest=spine_report_dh2 |
| `--output-csv` | Output CSV path for the de-duplicated non-LLDP Validation Errors rows. |  |
| `--output-xlsx` | Output XLSX path for the de-duplicated workbook. |  |
| `--both`, `--both-latest` | Auto-discover and run the latest dedupe jobs for the selected tag's default datahalls. This uses the latest DH-level full_spine_bleaf report plus the latest configured DH-level spine-to-leaf reports. | action=store_true; dest=both_latest |
| `--datahall`, `--dh` | Auto-discover and run the latest reports for one leaf datahall, for example --datahall 2 or --datahall DH3. | dest=leaf_dh_number; type=normalize_dh_number |

<a id="script-aga-dg-report-recalculator-py"></a>
### `aga_dg_report_recalculator.py`

**Purpose:** Recalculates AGA deployment-group progress from pasted report text or qcli full-report workbooks.

**Source summary:** Recalculate DG progress reports from pasted text blocks or AGA full-report workbooks. In interactive mode, the script expects: 1. The previous formatted report, or an empty block for a first report. 2. One current rack-level output for each DG in the previous report. The default AGA flow is DG1 through DG5. It uses the previous report's Current column as the new Previous column, then uses the rack-level DG outputs as the new Current values. Workbook mode reads one or more T0 qcli *_full_report.xlsx files, groups rows by the AGA5 DG rack mapping, and builds the same T0 <> T1 progress report. In workbook mode the combined_fec_with_pp sheet is split from the row values: - FEC bin 7 through 15 in Lock Status counts as Fec Bin - Pre-FEC BER greater than 1e-7 counts as Pre Fec - Combined FEC remains the row count The T1 <> T0 compact Excel artifact generated by this script is built from the same workbook rows read by this script; it does not import or call aga52_final_ppforall.py. T1-side source workbooks from aga52_final_ppforall.py, such as RX power, Pre-FEC, and FEC Bin *_full_report.xlsx files, are intentionally rejected as input here. Compact those in aga52_final_ppforall.py instead, then optionally pass the compact AGA5.2-DG...with-pp.xlsx workbook via --t1-report.

**Parser description:** Build a refreshed DG progress report from previous and current pasted outputs.

**Typical help command:**

```bash
python3 aga_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `full_report_arg` | Optional shortcut for --full-report: a single T0 full_report.xlsx generated by the combined DG qcli command. | nargs=?; type=Path |
| `--previous` | f'Read the previous formatted report from this file and update it with the generated report. If no path is given, uses {DEFAULT_PREVIOUS_REPORT}.' | nargs=?; type=Path |
| `--excel` | One or more T0 qcli *_full_report.xlsx files to use as the current T0 <> T1 input. Repeatable; rows are grouped by DG rack mapping. | action=append; nargs=+; type=Path |
| `--full-report` | Single T0 full_report.xlsx generated by a combined DG qcli command. Requires --targets unless --previous can provide the target list. | type=Path |
| `--print-qcli-command` | Print the combined qcli hc-summary command(s) for --targets, then exit. | action=store_true |
| `--t1-report` | Optional T1 <> T0 detail workbook(s), such as AGA5.2-DG9-10-15-16-...-with-pp.xlsx, used for the final T1 table. Do not pass individual T1-side RX/Pre-FEC/FEC-Bin *_full_report.xlsx files. | action=append; nargs=+; type=Path |
| `--output-excel` | Write the grouped current workbook rows and split FEC sheets to this Excel file. | type=Path |
| `--t1-output-excel` | Write the split T1 <> T0 RX/TX optics, Pre-FEC, and FEC Bin workbook to this path. With --full-report, a timestamped path is generated by default. | type=Path |
| `--combined-fec-output-excel` | Write a workbook containing one combined_fec sheet per DG to this path. With --full-report, a timestamped path is generated by default. | type=Path |
| `--no-auto-excel` | Do not write default Excel artifacts when --full-report is used. | action=store_true |
| `-o`, `--output` | Write the generated text report to this file as well as stdout. | type=Path |
| `--current` | Path to a current DG rack-level output, repeatable for any DG number | action=append; metavar=DG=PATH |
| `--targets`, `--target` | Comma-separated DG targets to process, for example 9,10,15,16,25,26. Use 'all' for every DG in the AGA5.2 rack map. | dest=targets |
| `--sentinel` | Line used to finish each interactive paste block. Default: END | default=END |
| `--optics-fec` | RX optics, TX optics, FEC BIN, and Pre-FEC counts for one DG. Repeatable. | action=append; metavar=DG=RX,TX,FECBIN,PRE |
| `--optics-fec-file` | Path to a file containing DG RX/TX optics, FEC BIN, and Pre-FEC rows or table |  |
| `--no-optics-fec-summary` | Skip the final T1 <> T0 RX/TX optics and Pre-FEC summary table | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--no-warnings` | Suppress rack total mismatch warnings | action=store_true |

<a id="script-cfab-qfab-mapping-py"></a>
### `cfab_qfab_mapping.py`

**Purpose:** Extracts CFAB/QFAB/GFAB fabric-block relationships from rackmap files and writes mapping analysis to Excel.

**Source summary:** cfab_qfab_mapping.py This script extracts and analyzes fabric mappings between QFAB/GFAB and CFAB blocks from OCI rackmap files. It is tailored for identifying shared (1:1, 1:N, N:1) fabric block relationships within a specific building and computing versioned mapping ratios. Key Features: ------------- - Scans the `autonet-rackmaps` directory for the appropriate rackmap file. - Accepts user input for a building name (e.g., iad49) and a block number or range. - Restricts analysis to blocks within the specified building only. - Extracts fabric connection mappings (CFAB, QFAB, GFAB) and associated platform data. - Applies prioritized version detection logic: - CFAB: prefers `cfab2.0` > `cfab1.0` > unknown - QFAB/GFAB: prefers `qfab3.0` > `qfab2.1` > `qfab2.0` > `qfab1.0` > `gfab1.0` > unknown - Merges multiple records per block-pair to avoid redundancy. - Computes and reports QFAB:CFAB block ratios dynamically. - Outputs results to a clean Excel file (`cfab_qfab_mappings.xlsx`) and pretty terminal view. Requirements: ------------- - Python 3.x - Packages: pandas, openpyxl (auto-installed if missing) Usage: ------ 1. Ensure you have access to Oracle's internal Bitbucket repository: ssh://git@bitbucket.oci.oraclecorp.com:7999/netauto/autonet-rackmaps.git 2. Run the script from the command line: ```bash python3 cfab_qfab_mapping.py

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-changeset-add-py"></a>
### `changeset-add.py`

**Purpose:** Builds rack-SKU add/change-set payloads from rackmap JSON data.

**Parser description:** Create changeset to update rack SKUs in rackmaps.json

**Typical help command:**

```bash
python3 changeset-add.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Region name | required=True |
| `--bldg_block` | Block names separated by commas (e.g., bldg14-block1,bldg15-block2) | required=True |
| `--old_rack_sku` | Old Rack SKU to be updated | required=True |
| `--new_rack_sku` | New Rack SKU to replace the old SKU | required=True |
| `--input_filename` | Input JSON file containing the data | required=True |

<a id="script-changeset-deletion-py"></a>
### `changeset-deletion.py`

**Purpose:** Builds rack deletion change-set payloads from rackmap JSON data.

**Parser description:** Create changeset to delete racks SKUs from rackmaps.json

**Typical help command:**

```bash
python3 changeset-deletion.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Region name | required=True; metavar=TEXT |
| `--bldg-block` | Block names separated by commas (e.g., bldg14-block1,bldg15-block2) | required=True; metavar=TEXT |
| `--old_platform` | Rack SKU, e.g., net.ad_gfab_v1_400_t2_c1_1.02 | required=True; metavar=TEXT |
| `--new_platform` | Rack SKU, e.g., net.ad_gfab_v1_400_t2_c1_1.02 | required=True; metavar=TEXT |
| `--input_filename` | Input JSON file containing the data: copy the whole rackmaps file: e.g: rackmaps.json | required=True; metavar=TEXT |

<a id="script-changeset-modify-py"></a>
### `changeset-modify.py`

**Purpose:** Builds rack SKU modification change-set payloads from rackmap JSON data.

**Parser description:** Create changeset to delete racks SKUs from rackmaps.json

**Typical help command:**

```bash
python3 changeset-modify.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Region name | required=True; metavar=TEXT |
| `--block` | Block name(s) separated by commas (e.g., bldg14-block1,bldg15-block2) | required=True; metavar=TEXT |
| `--old_platform` | Old Rack SKU(s) separated by commas (e.g., net.ad_gfab_v1_400_t2_c1_1.02,net.ad_gfab_v1_400_t2_c1_1.03) | required=True; metavar=TEXT |
| `--new_platform` | New Rack SKU(s) separated by commas (e.g., net.ad_gfab_v1_400_t2_c1_1.01,net.ad_gfab_v1_400_t2_c1_1.02) | required=True; metavar=TEXT |
| `--input_filename` | Input JSON file containing the data: copy the whole rackmaps file: e.g: rackmaps.json | required=True; metavar=TEXT |

<a id="script-config-refresh-version-file-update-py"></a>
### `config-refresh-version-file-update.py`

**Purpose:** Trims extracted config-refresh version blocks to the first two CSV fields.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-config-refresh-version-grab-py"></a>
### `config_refresh_version_grab.py`

**Purpose:** Extracts version sections from raw `devices compare-config --latest` output.

**Source summary:** To create input file: version-raw.txt ncpcli@iad 2025-04-25 08:27:14> update-device-list --devices-by-role=qfabt2 --device-names-matching iad63* --device-state-matching in-service --device-state-matching maintenance ncpcli@iad 2025-04-25 08:27:14> timeit devices compare-config --latest

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-cutsheet-data-fill-auto-input-py"></a>
### `cutsheet_data_fill_auto_input.py`

**Purpose:** Converts simple switch/port input into rows useful for cutsheet auto-fill workflows.

**Source summary:** This script takes data in input text file: "auto-fill-input.txt" sample: nrt3-q1-b6-t1-r14 Ethernet59/1 nrt3-q1-b6-t1-r15 Ethernet60/1 nrt3-q1-b6-t1-r16 Ethernet61/1 Usage: Create "auto-fill-input.txt" file in same folder as script and update "csv_file_path" variable in below code and run the script with Python python3 cutsheet_data_fill_auto_input.py

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-cutsheet-preflight-py"></a>
### `cutsheet_preflight.py`

**Purpose:** Validates location CSVs against expected QFAB/CFAB hardware counts, rack ordering, and layout prerequisites.

**Source summary:** Comprehensive tool for calculating and validating hardware configurations for a specific data center setup. It involves multiple steps, including configuration loading, user input, calculations, validation, and data reordering. Usage: python cutsheet_preflight.py Select Server Type: 1. B200 2. B300 3. GB200 4. GB300 Enter the number of your choice: 2 Select Fabric (main QFAB) Type: 1. QFAB3.0 2. QFAB3.0_ONOS 3. Multi-Planar Spectrum 8K GPU 4. Multi-Planar Spectrum 16K GPU 5. Multi-Planar Spectrum 32K GPU Enter the number of your choice: 3 Select CFAB column configuration: 1. cfab_8_column 2. cfab_16_column Enter the number of your choice: 2 Please Enter Required GPU Node/Racks : 511 ************ Platform Required to Support 511 B300 nodes/racks in Fabric Type "Multi-Planar Spectrum 8K GPU" ************ Fabric Type: Multi-Planar Spectrum 8K GPU GPU Platform Details: Required GPU Platform: GPU_V5_X11_B300_R.03 (Count: 511) GPUs per node: 8 Total GPUs offered: 4088 Network Details (Main QFAB): T1 Platform: net.ad_spc4_planar_qfab_t1_1.01 (Count: 16) T0 Platform: net.ad_spc4_planar_qfab_t0_1.01 (Count: 16) IPR Platform: net.ad_spc4_planar_qfab_ipr_1.01 (Count: 1) Network Details (CFAB): T1 Platforms: - net.ad_cfab_v2_t1_t2_1.11 (Count: 2) - net.ad_cfab_v2_t1_t2_2.11 (Count: 2) T0 Platform: net.ad_cfab_t0_1.05 (Count: 8) ******************************************************************************** Do you want to validate required hardware vs location file from atlas? (y/n): y Enter the path to the location CSV file: iad47.1.csv ********* performing validation for required vs available hardware *********** GPU_V5_X11_B300_R.03 (Count: 511): passed ✅ net.ad_spc4_planar_qfab_t1_1.01 (Count: 16): passed ✅ net.ad_spc4_planar_qfab_t0_1.01 (Count: 16): passed ✅ net.ad_spc4_planar_qfab_ipr_1.01 (Count: 1): passed ✅ net.ad_cfab_v2_t1_t2_1.11 (Count: 2): passed ✅ net.ad_cfab_v2_t1_t2_2.11 (Count: 2): passed ✅ net.ad_cfab_t0_1.05 (Count: 8): passed ✅ net.ad_cfab_v2_t3_1.02 (Count: 16): missing platform in BOM page net.oad_metro_core_zr_4.01 (Count: 8): missing platform in BOM page net.ad_cfab_v2_nt1_nt2_1.03 (Count: 4): missing platform in BOM page net.ad_cfab_v2_nt1_nt2_2.03 (Count: 4): missing platform in BOM page net.ad_cfab_v2_t1_t2_1.04 (Count: 2): missing platform in BOM page net.ad_cfab_v2_t1_t2_2.04 (Count: 2): missing platform in BOM page aux.01 (Count: 1): missing platform in BOM page aux.02 (Count: 1): missing platform in BOM page ********** Performing validation for Column [PLACEMENT_GROUP] ************ placement group 1: passed ✅ placement group 3: passed ✅ placement group 5: passed ✅ placement group 7: failed ❌ Failure reason: GPU_V5_X11_B300_R.03: Expected - 128, Available - 127 placement group 151: passed ✅ placement group 152: passed ✅ placement group 153: passed ✅ placement group 154: passed ✅ placement group 201: passed ✅ placement group is not in sequence. ❌ ********** Performing validation for Column [CFAB_FABRIC_BLOCK] ************ cfab block 1: passed ✅ cfab block 7: passed ✅ cfab block 9: failed ❌ Failure reason: GPU_V5_X11_B300_R.03: Expected - 256, Available - 255 ********** Performing validation for Column [QFAB_INSTANCE_ID] ************ cfab racks instance id 1: passed ✅ qfab racks instance id 2: passed ✅ ********** Performing validation for Column [BLOCK_NAME] and [CFAB_FABRIC_BLOCK] ************ cfab block 1: passed ✅ cfab block 3: passed ✅ cfab block 5: passed ✅ cfab block 7: passed ✅ cfab block 9: passed ✅

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-data-extract-lldp-failures-3-py"></a>
### `data-extract_lldp_failures_3.py`

**Purpose:** Parses LLDP failure text into expected/current connection rows for follow-up analysis.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-data-extract-test-optics-new-py"></a>
### `data_extract_test_optics_new.py`

**Purpose:** Extracts `test_optics` failure objects from health-check output and writes `test_optics_failures.csv`.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-decorate-removed-links-py"></a>
### `decorate_removed_links.py`

**Purpose:** Replaces GUID/port values in removed-link output with readable device and port information.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-device-inservice-audit-py"></a>
### `device_inservice_audit.py`

**Purpose:** Reports rack/device lifecycle status and who completed in-service transitions using read-only NCPCLI data.

**Source summary:** Report rack/device lifecycle status and who completed in-service transitions. The script uses two read-only NCPCLI sources: 1. ``devices list-state`` for the current state and authoritative State Store modification time. 2. ``ncp-job list`` for the matching ``VALIDATED_SET_STATE`` job, which can identify the human initiator and change ticket when State Store only shows a Network Control Plane service principal. Examples: # Building mode: count physical racks and summarize their device states. python3 device_inservice_audit.py iad64 --timezone Asia/Kolkata python3 device_inservice_audit.py --building fbb1 --verbose # Device mode remains available for targeted checks. python3 device_inservice_audit.py -r iad --device iad64-q1-b2-t1-r1 --device iad64-q1-b2-t1-r2 python3 device_inservice_audit.py -r iad --device-pattern 'iad64-q1-b2-t1-r*' python3 device_inservice_audit.py -r iad --device-file devices.txt --timezone Asia/Kolkata --csv inservice-audit.csv Rack status is derived from the current lifecycle states of NCP network devices in each physical rack. A rack is ``in-service`` only when every listed device in that rack is in-service. The rack's completion time/actor is taken from the last device transition that made the rack fully in-service. State Store reports the latest state assignment; it is not a complete historical ledger. A device that is no longer in-service cannot be used to reconstruct an older in-service transition with certainty.

**Parser description:** Count physical racks in a building, summarize their derived lifecycle status, and report who/when completed in-service racks.

**Typical help command:**

```bash
python3 device_inservice_audit.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `targets` | Building code (recommended, e.g. iad64/fbb1) or exact device names. | nargs=* |
| `-b`, `--building` | Building code, e.g. iad64 or fbb1. NCP region is inferred. |  |
| `-r`, `--region` | NCPCLI region, e.g. iad/fbb; `-r iad64` also starts building mode. |  |
| `--device`, `--exact-device` | Exact device name; repeat or provide comma-separated names. | action=append; default= |
| `--device-pattern`, `--devices` | Device glob, e.g. 'iad64-q1-b2-t1-r*'; repeatable. | action=append; default= |
| `--device-file`, `--devices-from-file` | Text file or pasted table containing device names. | type=Path |
| `--timezone` | Display timezone, e.g. UTC or Asia/Kolkata. | default=UTC |
| `--window-minutes` | Maximum time difference when matching State Store and NCP jobs. | default=10; type=int |
| `--state-store-only` | Skip NCP job cross-reference (faster, but may show a service principal). | action=store_true |
| `--device-fallback` | For unresolved racks/devices, run slower per-device NCP job queries. | action=store_true; dest=device_fallback |
| `--no-device-fallback` | Do not run per-device NCP job queries after the regional lookup. | action=store_false; dest=device_fallback |
| `--include-not-in-service` | Also display selected devices whose current state is not in-service. | action=store_true |
| `--json` | Output JSON. | action=store_true |
| `--csv` | Also write all displayed rows to CSV. | type=Path |
| `--rackmap` | Production rackmap path; normally auto-detected from ~/autonet. | type=Path |
| `--summary-only` | Building mode: print counts without the per-rack table. | action=store_true |
| `--in-service-only` | Building mode: show only racks whose managed devices are all in-service. | action=store_true |
| `--qfab-only` | Building mode: include only racks containing QFAB-role devices. | action=store_true; dest=qfab_only |
| `--all-racks` | Building mode: include racks for every NCP network-device role. | action=store_false; dest=qfab_only |
| `--verbose` | Show job/source columns. | action=store_true |
| `--no-color` | Disable terminal colors (also honored automatically for redirected output). | action=store_true |
| `--no-progress` | Disable the interactive spinner and elapsed-time updates. | action=store_true |
| `--debug` | Print NCPCLI commands. | action=store_true |
| `--workers` | _No help text in script._ | default=4; type=int |
| `--state-chunk-size` | Building mode: exact devices per State Store query. | default=50; type=int |
| `--timeout` | Timeout per NCPCLI call. | default=300; type=int |
| `--ncpcli-bin` | Path or command name for ncpcli. |  |
| `--no-agent-auth` | Do not pass use_agent_for_auth=true to ncpcli. | action=store_false; dest=use_agent_for_auth |

<a id="script-device-state-audit-py"></a>
### `device_state_audit.py`

**Purpose:** Summarizes deployed, new, mixed, maintenance, and in-service device/rack state for a region or site.

**Parser description:** Summarize deployed/new/in-service device states from ncpcli for any region.

**Typical help command:**

```bash
python3 device_state_audit.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | NCP region or site tag, for example hsg or hsg17. |  |
| `-b`, `--building` | Optional building/site filter, for example hsg17 or aga5. A numeric value requires --region. |  |
| `--site` | Site/building tag, for example hsg17 or jbp15. Equivalent to passing that site tag to -r/--region. |  |
| `--device-pattern` | Device-name pattern passed to ncpcli. Default: "*". | default=* |
| `--role` | Optional role filter. Repeat for multiple roles, for example --role qfabt0 --role qfabt1. | action=append |
| `-i`, `--instance` | f'QFAB instance used for DG/rack lookup in autonet-rackmaps. Default: infer from live qfab hostnames, then fall back to {DEFAULT_QFAB_INSTANCE}.' | type=int |
| `--rackmaps-dir` | Directory containing <region>.rackmap files for non-IAD regions. Default: ~/autonet/autonet-rackmaps. | default=~/autonet/autonet-rackmaps |
| `--inventory-file` | Specific inventory JSON to use only for qfabt0 DG/rack mapping. Device state is always read from live ncpcli output. By default, the script selects a matching inventory from --inventory-dir. |  |
| `--inventory-dir` | f'Directory containing local inventory JSON files for DG/rack mapping only. Default: {DEFAULT_INVENTORY_DIR}.' | default=str(DEFAULT_INVENTORY_DIR) |
| `--ncpcli-command` | _No help text in script._ | default=ncpcli |
| `--connection-methods` | _No help text in script._ |  |
| `--timeout` | _No help text in script._ | default=240; type=int |
| `--workers` | argparse.SUPPRESS | type=int |
| `--counts` | Also print generic state-count summaries by building and role. | action=store_true |
| `--summary` | Print placement-group state summary instead of detailed DG state rows. Requires --role qfabt0. | action=store_true |
| `--details` | Print matching device names after the summary. | action=store_true |
| `--detail-states` | Comma/space separated states for --details. Default: deployed,new,in-service. |  |

<a id="script-dot1x-bug-validation-py"></a>
### `dot1x_bug_validation.py`

**Purpose:** SSHes to management switches and compares successful dot1x hosts against VLAN assignment data.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-fabric-built-verification-tool-py"></a>
### `fabric_built_verification_tool.py`

**Purpose:** Collects serial numbers from devices over SSH and enriches them with Storekeeper asset data.

**Parser description:** Collect serial numbers from devices and fetch asset info from Storekeeper.

**Typical help command:**

```bash
python3 fabric_built_verification_tool.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--region` | First 3 airport code alphabets from device name (e.g., iad,phx,nrt). | required=True; metavar=iad,phx,nrt; type=str |
| `--filename` | The file containing the hostnames (e.g., devices.txt). | required=True; metavar=devices.txt; type=str |
| `--cutsheet` | The cutsheet CSV file containing rack and elevation information for matching (e.g., phx14-cables.csv). | required=True; metavar=phx14-cables.csv; type=str |

<a id="script-fabric-built-verification-tool-juniper-py"></a>
### `fabric_built_verification_tool_juniper.py`

**Purpose:** Juniper-focused variant of fabric-built serial collection and Storekeeper enrichment.

**Parser description:** Collect serial numbers from devices and fetch asset info from Storekeeper.

**Typical help command:**

```bash
python3 fabric_built_verification_tool_juniper.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--region` | First 3 airport code alphabets from device name (e.g., iad,phx,nrt). | required=True; metavar=iad,phx,nrt; type=str |
| `--filename` | The file containing the hostnames (e.g., devices.txt). | required=True; metavar=devices.txt; type=str |
| `--cutsheet` | The cutsheet CSV file containing rack and elevation information for matching (e.g., phx14-cables.csv). | required=True; metavar=phx14-cables.csv; type=str |

<a id="script-fabric-links-count-py"></a>
### `fabric_links_count.py`

**Purpose:** Downloads rackmap/topology data and counts expected or missing T0/T1/T2 fabric links.

**Source summary:** Overview: ------- This script is designed to analyze the network topology of a specific region, building, and block, and determine the count of missing network links. It uses the ncpcli command-line tool to download the necessary rackmap and topology files, and then parses the data to identify the fabric version and count the links between different tiers (T0-T1 and T1-T2). The script supports multiple fabric versions, including QFAB, GFAB and provides detailed output on the expected and actual link counts, as well as the number of missing links. Usage: ------ The script takes three main arguments: -r or --region: The region code (e.g., cwl15) -b or --block: The block number -i or --design: The design identifier (default: 2) Run via CLI: python random-scripts/fabric_link_count.py -r iad49 -b 40 -i 1 Purpose: ------- The purpose of this script is to provide a quick and easy way to analyze the network topology and identify potential issues with missing links. Example Output: --------------- ******** Processing for fabric version and links count in block. ****** region iad49, block 40, fabric version: qfab3.0 expected links between t0_t1: 8192 actual links between t0_t1: 6144 missing links between t0_t1: 2048

**Parser description:** Get missing links count

**Typical help command:**

```bash
python3 fabric_links_count.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Region code (e.g., cwl15) | required=True |
| `-b`, `--block` | Block number | required=True; type=int |
| `-i`, `--design` | Design identifier (default: 2) | default=2; type=int |

<a id="script-fec-failure-inventory-py"></a>
### `fec_failure_inventory.py`

**Purpose:** Checks a device for FEC BER failures and prints inventory details for failed interfaces.

**Source summary:** Author: Akhil Kadali Email: akhil.kadali@oracle.com Purpose: This script takes a device as an input and prints the interfaces failing FEC BER check and the inventory details of that interface

**Typical help command:**

```bash
python3 fec_failure_inventory.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `device` | Device you would like to check |  |

<a id="script-fetch-bops-tickets-py"></a>
### `fetch_bops_tickets.py`

**Purpose:** Maps rack numbers to unresolved BOPS master tickets using Jira/BOPS fields.

**Source summary:** Fetch unresolved BOPS master tickets for rack locations. This script is read-only. It searches BOPS master tickets by building, applies an optional rack type filter from each ticket's actual Rack Type field, follows linked tickets, and maps the requested rack numbers from linked ticket location fields or summaries back to the master BOPS tickets.

**Parser description:** Map rack numbers to unresolved BOPS master tickets.

**Typical help command:**

```bash
python3 fetch_bops_tickets.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `racks` | Rack numbers. You may pass space-separated values or comma-separated lists. | nargs=* |
| `--racks-file` | File containing rack numbers separated by commas, whitespace, or newlines. |  |
| `-r`, `--region`, `--building` | Building/region value used by the BOPS Jira field, for example aga5. | required=True; dest=building |
| `-t`, `--rack-type` | Rack type shorthand. If omitted, searches all BOPS rack types for the building, which is safer when regions use different Rack Type values. | choices=DEFAULT_RACK_TYPES |
| `--rack-type-value` | Exact Jira Rack Type value to filter locally. Use this when a region has a type value that does not contain t0, t1, or ipr. |  |
| `--jira-url` | f'Jira base URL. Default: {DEFAULT_JIRA_URL}' | default=DEFAULT_JIRA_URL |
| `--username` | Jira username/email. Defaults to JIRA_USERNAME or ~/.jira/config.json username. | default=os.environ.get('JIRA_USERNAME') |
| `--password-env` | Environment variable containing the Jira password. Default: JIRA_PASSWORD. | default=JIRA_PASSWORD |
| `--password-command` | Command used to generate the temporary Jira password. | default=DEFAULT_PASSWORD_COMMAND |
| `--format` | Output format. Default: markdown. | default=markdown; choices=markdown, csv, tsv, json |
| `--show-evidence` | Include the linked ticket used as evidence. | action=store_true |
| `--no-links` | Print plain BOPS keys instead of Markdown links. | action=store_true |
| `--include-closed-bops` | Do not filter BOPS tickets by unresolved resolution. | action=store_true |
| `--verify-tls` | Verify TLS certificates. Disabled by default for this internal Jira. | action=store_true |

<a id="script-flap-validations-py"></a>
### `flap_validations.py`

**Purpose:** Extracts flapping interfaces from validation output and matches them to cable-plan rows.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-flap-validations-notconnect-filter-py"></a>
### `flap_validations_notconnect_filter.py`

**Purpose:** Flap-validation parser that SSHes to devices to filter links already showing notconnect/down evidence.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-get-devices-by-rack-py"></a>
### `get_devices_by_rack.py`

**Purpose:** Queries Prometheus for devices associated with a rack.

**Parser description:** Query Prometheus for devices by rack.

**Typical help command:**

```bash
python3 get_devices_by_rack.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--rack` | Rack number to filter devices (e.g., 1707) | required=True |

<a id="script-get-optic-issue-details-py"></a>
### `get_optic_issue_details.py`

**Purpose:** Enriches bad optical power lines with remote device/interface and remote rack/elevation from Prometheus.

**Typical help command:**

```bash
python3 get_optic_issue_details.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--filename`, `-f` | Path of text file with validation data | required=True |
| `--region`, `-r` | Region name | required=True |

<a id="script-get-t0-t1-racks-py"></a>
### `get_t0_t1_racks.py`

**Purpose:** Extracts T0/T1 rack numbers from autonet cables CSV and rackmap data.

**Parser description:** Get T0/T1 rack numbers from an autonet cables CSV file.

**Typical help command:**

```bash
python3 get_t0_t1_racks.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `build` | Build name, example: iad77, iad60, phx20 |  |
| `--tier` | Which tier to show. Default: both | default=both; choices=t0, t1, both |
| `--csv` | Optional direct CSV path. Default: ~/autonet/autonet-plans/<region>/<build>-cables.csv |  |
| `--qfab-only` | Only include QFAB devices like iad77-q2-p1-t1-r1 | action=store_true |
| `--dg` | Filter by deployment group / placement group. Examples: 1, dg-1, 151, 151-154 |  |
| `--instance` | Optional QFAB instance filter from rackmap, example: 2 |  |
| `--devices` | Print device to rack mapping instead of only rack numbers | action=store_true |
| `--format` | Output style for rack numbers. Default: list | default=list; choices=list, table, both |
| `--verbose` | Print source file and matched device count before the output | action=store_true |

<a id="script-gpurack-devicelinking-py"></a>
### `gpuRack_deviceLinking.py`

**Purpose:** Finds GPU rack links to QFAB/GFAB devices and prints rack relationships.

**Parser description:** to fetch GPU racks linking with qfab/gfab devices with their rack numbers

**Typical help command:**

```bash
python3 gpuRack_deviceLinking.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-cutsheet_file` | Path to input cutsheet file e.g; ~/autonet/autonet-plans/hsg/hsg3-cables.xlsx | required=True |
| `-gpu_racks` | Comma-separated list of rack names e.g; 3503,3903 | required=True |

<a id="script-health-check-report-generator-py"></a>
### `health_check_report_generator.py`

**Purpose:** Runs NCPCLI rack health checks and turns miscabling/optic findings into DCO-friendly reports.

**Typical help command:**

```bash
python3 health_check_report_generator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--region` | Region against which the healthcheck will run (e.g. hsg3) | required=True; type=str |
| `--racks` | Rack identifier (e.g. 1702,1703) | required=True; type=list_str |
| `--role` | Role (e.g. ifabt2) | required=True; type=str |
| `--cablesfile` | Cables file (e.g. /Users/christopherhern/plan/hsg/hsg3-cables.csv) | required=True; type=str |
| `--summaryonly` | Provide only the summary info, exlcuding the details | required=False; action=store_true |
| `--noyubikey` | Skips asking for yubikey for ncpcli health checks | required=False; action=store_true |
| `--ncpoutput` | Shows NCPCLI raw output | required=False; action=store_true |

<a id="script-hsg-dg-report-recalculator-py"></a>
### `hsg_dg_report_recalculator.py`

**Purpose:** Recalculates HSG deployment-group progress from pasted report text or qcli full-report workbooks.

**Source summary:** Recalculate DG progress reports from pasted text blocks or HSG full-report workbooks. In interactive mode, the script expects: 1. The previous formatted report, or an empty block for a first report. 2. One current rack-level output for each DG in the previous report. The default HSG flow is the requested DG list. It uses the previous report's Current column as the new Previous column, then uses the rack-level DG outputs as the new Current values. Workbook mode reads one or more T0 qcli *_full_report.xlsx files, groups rows by the HSG17 DG rack mapping, and builds the same T0 <> T1 progress report. In workbook mode the combined_fec_with_pp sheet is split from the row values: - FEC bin 7 through 15 in Lock Status counts as Fec Bin - Pre-FEC BER greater than 1e-7 counts as Pre Fec - Combined FEC remains the row count The T1 <> T0 compact Excel artifact generated by this script is built from the same workbook rows read by this script. T1-side source workbooks, such as RX power, Pre-FEC, and FEC Bin *_full_report.xlsx files, are intentionally rejected as input here. Pass a compact HSG17-DG... workbook via --t1-report when a final T1 table is needed.

**Parser description:** Build a refreshed DG progress report from previous and current pasted outputs.

**Typical help command:**

```bash
python3 hsg_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `full_report_arg` | Optional shortcut for --full-report: a single T0 full_report.xlsx generated by the combined DG qcli command. | nargs=?; type=Path |
| `--previous` | f'Read the previous formatted report from this file and update it with the generated report. If no path is given, uses {DEFAULT_PREVIOUS_REPORT}.' | nargs=?; type=Path |
| `--excel` | One or more T0 qcli *_full_report.xlsx files to use as the current T0 <> T1 input. Repeatable; rows are grouped by DG rack mapping. | action=append; nargs=+; type=Path |
| `--full-report` | Single T0 full_report.xlsx generated by a combined DG qcli command. Requires --targets unless --previous can provide the target list. | type=Path |
| `--print-qcli-command` | Print the combined qcli hc-summary command for --targets, then exit. | action=store_true |
| `--t1-report` | Optional T1 <> T0 detail workbook(s), such as HSG17-DG3-4-5-6-...-with-pp.xlsx, used for the final T1 table. Do not pass individual T1-side RX/Pre-FEC/FEC-Bin *_full_report.xlsx files. | action=append; nargs=+; type=Path |
| `--output-excel` | Write the grouped current workbook rows and split FEC sheets to this Excel file. | type=Path |
| `--t1-output-excel` | Write the split T1 <> T0 RX/TX optics, Pre-FEC, and FEC Bin workbook to this path. No Excel file is written unless this option is provided. | type=Path |
| `--combined-fec-output-excel` | Write a workbook containing one combined_fec sheet per DG to this path. No Excel file is written unless this option is provided. | type=Path |
| `--no-auto-excel` | Compatibility flag; default behavior already skips automatic Excel output. | action=store_true |
| `-o`, `--output` | Write the generated text report to this file as well as stdout. | type=Path |
| `--current` | Path to a current DG rack-level output, repeatable for any DG number | action=append; metavar=DG=PATH |
| `--targets`, `--target` | Comma-separated DG targets to process, for example 9,10,15,16,25,26. Use 'all' for every DG in the HSG17 rack map. | dest=targets |
| `--sentinel` | Line used to finish each interactive paste block. Default: END | default=END |
| `--optics-fec` | RX optics, TX optics, FEC BIN, and Pre-FEC counts for one DG. Repeatable. | action=append; metavar=DG=RX,TX,FECBIN,PRE |
| `--optics-fec-file` | Path to a file containing DG RX/TX optics, FEC BIN, and Pre-FEC rows or table |  |
| `--no-optics-fec-summary` | Skip the final T1 <> T0 RX/TX optics and Pre-FEC summary table | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--no-warnings` | Suppress rack total mismatch warnings | action=store_true |

<a id="script-iad-dg-report-recalculator-py"></a>
### `iad_dg_report_recalculator.py`

**Purpose:** Recalculates IAD deployment-group progress from pasted report text or qcli full-report workbooks.

**Source summary:** Recalculate DG progress reports from pasted text blocks or IAD full-report workbooks. In interactive mode, the script expects: 1. The previous formatted report, or an empty block for a first report. 2. One current rack-level output for each DG in the previous report. The default IAD flow is the requested DG list. It uses the previous report's Current column as the new Previous column, then uses the rack-level DG outputs as the new Current values. Workbook mode reads one or more T0 qcli *_full_report.xlsx files, groups rows by the selected IAD DG rack mapping, and builds the same T0 <> T1 progress report. In workbook mode the combined_fec_with_pp sheet is split from the row values: - FEC bin 7 through 15 in Lock Status counts as Fec Bin - Pre-FEC BER greater than 1e-7 counts as Pre Fec - Combined FEC remains the row count The T1 <> T0 compact Excel artifact generated by this script is built from the same workbook rows read by this script. T1-side source workbooks, such as RX power, Pre-FEC, and FEC Bin *_full_report.xlsx files, are intentionally rejected as input here. Pass a compact IAD-DG... workbook via --t1-report when a final T1 table is needed.

**Parser description:** Build a refreshed DG progress report from previous and current pasted outputs.

**Typical help command:**

```bash
python3 iad_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `full_report_arg` | Optional shortcut for --full-report: a single T0 full_report.xlsx generated by the combined DG qcli command. | nargs=?; type=Path |
| `--previous` | f'Read the previous formatted report from this file and update it with the generated report. If no path is given, uses {DEFAULT_PREVIOUS_REPORT}.' | nargs=?; type=Path |
| `--excel` | One or more T0 qcli *_full_report.xlsx files to use as the current T0 <> T1 input. Repeatable; rows are grouped by DG rack mapping. | action=append; nargs=+; type=Path |
| `--full-report` | Single T0 full_report.xlsx generated by a combined DG qcli command. Requires --targets unless --previous can provide the target list. | type=Path |
| `--site` | IAD site/build to use, for example iad65 or iad77. Defaults to auto-detecting from workbook filenames, otherwise iad65. |  |
| `--print-qcli-command` | Print the combined qcli hc-summary command for --targets, then exit. | action=store_true |
| `--t1-report` | Optional T1 <> T0 detail workbook(s), such as IAD65-DG1-2-... or IAD77-DG2-... with-pp.xlsx, used for the final T1 table. Do not pass individual T1-side RX/Pre-FEC/FEC-Bin *_full_report.xlsx files. | action=append; nargs=+; type=Path |
| `--output-excel` | Write the grouped current workbook rows and split FEC sheets to this Excel file. | type=Path |
| `--t1-output-excel` | Write the split T1 <> T0 RX/TX optics, Pre-FEC, and FEC Bin workbook to this path. No Excel file is written unless this option is provided. | type=Path |
| `--combined-fec-output-excel` | Write a workbook containing one combined_fec sheet per DG to this path. No Excel file is written unless this option is provided. | type=Path |
| `--no-auto-excel` | Compatibility flag; default behavior already skips automatic Excel output. | action=store_true |
| `-o`, `--output` | Write the generated text report to this file as well as stdout. | type=Path |
| `--current` | Path to a current DG rack-level output, repeatable for any DG number | action=append; metavar=DG=PATH |
| `--targets`, `--target` | Comma-separated DG targets to process, for example 1,2,3,4 for IAD65 or 2 for IAD77. Use 'all' for every DG in the selected site rack map. | dest=targets |
| `--sentinel` | Line used to finish each interactive paste block. Default: END | default=END |
| `--optics-fec` | RX optics, TX optics, FEC BIN, and Pre-FEC counts for one DG. Repeatable. | action=append; metavar=DG=RX,TX,FECBIN,PRE |
| `--optics-fec-file` | Path to a file containing DG RX/TX optics, FEC BIN, and Pre-FEC rows or table |  |
| `--no-optics-fec-summary` | Skip the final T1 <> T0 RX/TX optics and Pre-FEC summary table | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--no-warnings` | Suppress rack total mismatch warnings | action=store_true |

<a id="script-ifab-fec-check-py"></a>
### `ifab_fec_check.py`

**Purpose:** Checks symbol BER/FEC health on device ports listed in a device file and cutsheet.

**Source summary:** Author: Akhil Kadali Email: akhil.kadali@oracle.com Purpose: This script takes a file with device names and cutsheet file as input and verifies the fec is healthy on all the ports on the devices

**Parser description:** Check Symbol BER on device ports

**Typical help command:**

```bash
python3 ifab_fec_check.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `filename` | File with device hostnames (one per line) |  |
| `--cutsheet` | Path to cutsheet CSV file | required=True |
| `--show-passed` | Show ports that passed the BER threshold | action=store_true |
| `--show-ignored` | Show ports that were ignored (non-active or N/A) | action=store_true |
| `--su` | Only check ports for a specific SU (for HSG T2 devices, 1-6 only) | type=int |
| `--password` | Password from jitpw |  |
| `--jitpw` | Path to JIT password tool (default: ~/jitpw/bin/jitpw) | default=PATH_TO_JITPW |

<a id="script-jbp15-dg-report-recalculator-py"></a>
### `jbp15_dg_report_recalculator.py`

**Purpose:** Processes JBP15 qcli workbooks/dashboard CSVs and builds DG progress reports plus qcli command helpers.

**Parser description:** JBP15 report and qcli operations helper. With no inputs, batch-process pending qcli jbp15 *_full_report.xlsx files and JBP15 dashboard Rack All CSV groups in the script directory.

**Typical help command:**

```bash
python3 jbp15_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `inputs` | Optional report workbook(s) or CSV export(s) | nargs=* |
| `-p`, `--panels` | PP matrix workbook override |  |
| `-o`, `--output` | Output path override; only valid with one explicit input |  |
| `--batch-dir` | Directory scanned in default no-arg mode | default=SCRIPT_DIR; type=Path |
| `--print-qcli-command` | Print JBP15 qcli hc-summary command(s) for --targets, then exit. | action=store_true |
| `--targets` | Comma-separated or range targets for --print-qcli-command. Examples: all, 16-19, DG5,DG8, ipr, plane1. | default=all; dest=qcli_targets |
| `--inventory-json` | JBP planar AI2ND inventory JSON used by qcli command printers. | default=DEFAULT_INVENTORY_PATH; type=Path |
| `--qcli-state` | State filter to include in the printed qcli command; pass an empty string to omit. | default=deployed |
| `--qcli-no-apex-update` | Append --no-apex-update to the printed qcli command. | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--force-refresh` | Regenerate even if a timestamped *_with_pp_YYYYMMDD_HHMMSS.xlsx output exists | action=store_true |
| `--dashboard-group-window-minutes` | Maximum timestamp span used to cluster RX/TX/Pre-FEC/FEC Bin dashboard CSV exports from the same run in default no-input mode. | default=DEFAULT_DASHBOARD_GROUP_WINDOW_MINUTES; type=int |
| `--generate-summary-report` | Write a standalone DG error count summary workbook in default no-input mode. | action=store_true |
| `--count-summary-tag` | Print latest JBP15 count summaries. Counts are not printed by default. Examples: jbp15, 15. |  |
| `--print-summary-tag` | Filter printed latest count summaries to specific DG/IPR targets. Examples: 1-3, DG5,DG8, IPR. |  |
| `--dry-run` | Show pending work without writing files | action=store_true |

<a id="script-jbp19-dg-report-recalculator-py"></a>
### `jbp19_dg_report_recalculator.py`

**Purpose:** Processes JBP19 qcli workbooks/dashboard CSVs and builds DG progress reports plus qcli command helpers.

**Parser description:** JBP19 report and qcli operations helper. With no inputs, batch-process pending qcli jbp19 *_full_report.xlsx files and JBP19 dashboard Rack All CSV groups in the script directory.

**Typical help command:**

```bash
python3 jbp19_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `inputs` | Optional report workbook(s) or CSV export(s) | nargs=* |
| `-p`, `--panels` | PP matrix workbook override |  |
| `-o`, `--output` | Output path override; only valid with one explicit input |  |
| `--batch-dir` | Directory scanned in default no-arg mode | default=SCRIPT_DIR; type=Path |
| `--print-qcli-command` | Print JBP19 qcli hc-summary command(s) for --targets, then exit. | action=store_true |
| `--targets` | Comma-separated or range targets for --print-qcli-command. Examples: all, 5-7, DG5,DG8, ipr. | default=all; dest=qcli_targets |
| `--inventory-json` | JBP planar AI2ND inventory JSON used by qcli command printers. | default=DEFAULT_INVENTORY_PATH; type=Path |
| `--qcli-state` | State filter to include in the printed qcli command; pass an empty string to omit. | default=deployed |
| `--qcli-no-apex-update` | Append --no-apex-update to the printed qcli command. | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--force-refresh` | Regenerate even if a timestamped *_with_pp_YYYYMMDD_HHMMSS.xlsx output exists | action=store_true |
| `--dashboard-group-window-minutes` | Maximum timestamp span used to cluster RX/TX/Pre-FEC/FEC Bin dashboard CSV exports from the same run in default no-input mode. | default=DEFAULT_DASHBOARD_GROUP_WINDOW_MINUTES; type=int |
| `--generate-summary-report` | Write a standalone DG error count summary workbook in default no-input mode. | action=store_true |
| `--count-summary-tag` | Print latest JBP19 count summaries. Counts are not printed by default. Examples: jbp19, 19. |  |
| `--print-summary-tag` | Filter printed latest count summaries to specific DG/IPR targets. Examples: 1-3, DG5,DG8, IPR. |  |
| `--dry-run` | Show pending work without writing files | action=store_true |

<a id="script-link-flap-protection-py"></a>
### `link_flap_protection.py`

**Purpose:** Enables or disables link flap-protection on Arista and NVIDIA devices over SSH.

**Typical help command:**

```bash
python3 link_flap_protection.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--vendor` | Vendor applied to all devices specified in this run. | required=True; choices=arista, nvidia |
| `--action` | Enable or disable Link Flap Protection on matched interfaces. | required=True; choices=enable, disable |
| `--device` | Repeatable: --device host[,port] | action=append; type=parse_host_line |
| `--device-pattern` | Repeatable: --device-pattern "aga5-q2-p1-t0-r[1-10]" | action=append; default= |
| `--device-file` | File with one host per line: host or host,port. Blank/# lines ignored. |  |
| `--timeout` | _No help text in script._ | default=60; type=int |
| `--debug-log` | _No help text in script._ |  |
| `--cmd` | Override discovery command. If omitted, a vendor-specific default is used. |  |
| `-r`, `--region` | Region like aga5 |  |
| `--rack` | Rack number like 0706. Also accepts comma-separated racks or ranges. |  |
| `--racks` | Multiple racks, comma-separated or repeated. Supports ranges like 0706-0708. | action=append; default= |

<a id="script-link-flap-protection-by-t0-state-py"></a>
### `link_flap_protection_by_t0_state.py`

**Purpose:** Enables/disables Cumulus T1 link flap-protection only for T0 peers that are not in service.

**Source summary:** Enable or disable Cumulus/NVIDIA link flap protection on T1 interfaces whose peer T0 is not in service. Default behavior is dry-run: - derive fabric/plane scope from the target T1 device names - discover all T0s for that scope - discover in-service T0s for the fabric - discover target T1s from --device-name, --device-pattern, or --device-from-file - read each T1 running NVUE commands - parse interface descriptions with peer_device=<t0> - exclude interfaces whose peer T0 is in service - print the compressed nv command that would run Use --apply to push config.

**Parser description:** Enable or disable Cumulus T1 link flap protection only toward T0s that are not in-service.

**Typical help command:**

```bash
python3 link_flap_protection_by_t0_state.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--device-name` | Exact T1 hostname. Repeat for multiple devices in the same fabric plane. | action=append |
| `--device-pattern` | T1 device regex or glob, e.g. 'jbp15-q2-p1-t1-r(1\|2)' or 'jbp15-q2-p1-t1-r*'. |  |
| `--device-from-file` | File containing target T1 hostnames or ncpcli devices list output. |  |
| `--rack` | Rack number like 0706. Also accepts comma-separated racks or ranges. |  |
| `--racks` | Multiple racks, comma-separated or repeated. Supports ranges like 0706-0708. | action=append |
| `--rack-region` | Site root for rack topology lookup, e.g. jbp15. Required with --rack/--racks. |  |
| `--in-service-state` | ncpcli state treated as in-service. Default: in-service | default=in-service |
| `--t1-state` | Optional state filter for --device-pattern discovery. |  |
| `--t0-all-file` | File containing all T0 names or ncpcli devices list output. Bypasses all-T0 ncpcli lookup. |  |
| `--t0-in-service-file` | File containing in-service T0 names or ncpcli devices list output. Bypasses in-service ncpcli lookup. |  |
| `--t1-config-file` | Local NVUE command output for offline parser testing. Use with exactly one --device-name. |  |
| `--username` | SSH username for T1 access. If omitted, prompts when live SSH is needed. |  |
| `--password` | SSH password. If omitted and live T1 access is needed, prompts securely. |  |
| `--jitpw` | Use local jitpw for SSH password instead of prompting. | action=store_true |
| `--jitpw-path` | Explicit jitpw binary path. |  |
| `--jitpw-scope` | Use 'jitpw -e <region>' or 'jitpw -qe <device>'. Default: region. | default=region; choices=region, device |
| `--jitpw-transform` | Optional case transform for the JIT password before SSH. Default: none. | default=none; choices=none, lower, upper |
| `--strict-hostkey` | _No help text in script._ | default=ask; choices=ask, yes, no |
| `--timeout` | _No help text in script._ | default=DEFAULT_TIMEOUT; type=int |
| `--debug-log` | _No help text in script._ |  |
| `--action` | Disable or enable link flap protection. Default: disable. | default=disable; choices=disable, enable |
| `--apply` | Push the NVUE config and run 'nv config apply'. Default is dry-run. | action=store_true |
| `--include-already-disabled` | Include interfaces that already have link flap-protection disabled. | action=store_true |
| `--include-unknown-t0` | Also act on parsed peer T0s that were not returned by the all-T0 discovery. | action=store_true |
| `--no-swp-filter` | Do not restrict candidate interfaces to swp1..swp64. | action=store_true |
| `--batch-size` | Interfaces per NVUE command. Default: 64. | default=64; type=int |
| `--max-workers` | _No help text in script._ | default=DEFAULT_WORKERS; type=int |
| `--output-csv` | Optional CSV path for the final per-interface plan. |  |
| `--allow-empty-discovery` | Allow empty T0 discovery results. This is unsafe and should only be used for parser tests. | action=store_true |

<a id="script-lldp-tool-py"></a>
### `lldp_tool.py`

**Purpose:** Enables or disables LLDP on Arista/NVIDIA interfaces selected by host and interface patterns.

**Typical help command:**

```bash
python3 lldp_tool.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--vendor` | Vendor applied to all devices specified in this run. | required=True; choices=arista, nvidia |
| `--action` | Enable or disable LLDP on matched interfaces. | required=True; choices=enable, disable |
| `--device` | Repeatable: --device host[,port] | action=append; type=parse_host_line |
| `--device-pattern` | Repeatable: --device-pattern "aga5-q2-p1-t0-r[1-10]" | action=append; default= |
| `--device-file` | File with one host per line: host or host,port. Blank/# lines ignored. |  |
| `--timeout` | _No help text in script._ | default=60; type=int |
| `--debug-log` | _No help text in script._ |  |
| `--cmd` | Override discovery command. If omitted, a vendor-specific default is used. |  |
| `-r`, `--region` | Region like aga5 |  |
| `--rack` | Rack number like 0706 |  |

<a id="script-multiplanar-site-pre-checks-py"></a>
### `multiplanar_site_pre_checks.py`

**Purpose:** Runs multiplanar pre-checks for ZTP, DAN/autonet runtime, certificates, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature.

**Source summary:** List ZTP images, verify DAN/autonet runtime status, check device certificate secret-key status, hostname validation, static MAC, link flap protection, config-diff, LLDP, gNMI, system health, and optics temperature for a region. Examples: ./multiplanar_site_pre_checks.py iad60 ./multiplanar_site_pre_checks.py iad60 --racks 0119,0120,0121,0122 ./multiplanar_site_pre_checks.py --region hsg17 --skip-ztp --skip-dan --skip-certificate --skip-static-mac --skip-link-flap --skip-gnmi --device-file hosts.txt ./multiplanar_site_pre_checks.py jbp15 --hostname-validation --racks 0119,0120 ./multiplanar_site_pre_checks.py jbp15 --vendor eos ./multiplanar_site_pre_checks.py jbp15 --contains 5.16 ./multiplanar_site_pre_checks.py jbp15 --ncpcli-command 'env PYENV_VERSION=netops-env ncpcli'

**Parser description:** Run multiplanar site pre-checks: ZTP image, DAN/autonet runtime, certificate, hostname validation, static MAC, link flap protection, config-diff, LLDP, gNMI, system health, and optics temperature.

**Typical help command:**

```bash
python3 multiplanar_site_pre_checks.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `target` | Target site/region token, e.g. jbp15, aga5, iad65, or jbp. | nargs=? |
| `--region`, `-r`, `--site` | Region/site override. Defaults to leading letters from target. | dest=region |
| `--vendor` | Only show one vendor prefix, e.g. cumulus, eos, junos. Default: cumulus. | default=cumulus |
| `--contains` | Only show image entries containing this token, e.g. 5.16. |  |
| `--required-ztp-image` | f'Exact ZTP image required for PASS. Default: {DEFAULT_REQUIRED_ZTP_IMAGE}' | default=DEFAULT_REQUIRED_ZTP_IMAGE |
| `--include-duplicates` | Keep duplicate entries from ncpcli output. | action=store_true |
| `--ncpcli-command` | ncpcli executable/wrapper command. | default=os.environ.get('NCPCLI_COMMAND', 'ncpcli') |
| `--qcli-command` | qcli executable/wrapper command for hostname validation. | default=os.environ.get('QCLI_COMMAND', 'qcli') |
| `--ssh-domain` | Optional SSH domain appended to default usernames, e.g. corp.example.com makes user@corp.example.com. |  |
| `--ssh-host-suffix` | Optional DNS suffix appended to short device names for SSH, e.g. example.com uses host.example.com. |  |
| `--connection-methods` | Optional ncpcli connection methods, e.g. tunnel,proxy,direct. |  |
| `--timeout` | Timeout in seconds per command. Default: 180. | default=180; type=int |
| `--json` | Print JSON output. | action=store_true |
| `--no-progress` | Disable stderr progress output for per-device checks. | action=store_true |
| `--ztp` | Run only the ZTP/gold image check unless other positive check flags are also provided. | action=store_true |
| `--dan` | Run only the DAN status check unless other positive check flags are also provided. | action=store_true |
| `--certificate`, `--cert` | Run only the certificate/secret-key check unless other positive check flags are also provided. | action=store_true; dest=certificate |
| `--mgmt-ts` | Run only the mgmt/ts in-service check unless other positive check flags are also provided. | action=store_true; dest=mgmt_ts |
| `--hostname-validation`, `--hostnamevalidation` | Run only the hostname validation check unless other positive check flags are also provided. | action=store_true; dest=hostname_validation |
| `--static-mac`, `--staticmac` | Run only the static MAC check unless other positive check flags are also provided. | action=store_true; dest=static_mac |
| `--linkflap`, `--link-flap` | Run only the link flap protection check unless other positive check flags are also provided. | action=store_true; dest=link_flap |
| `--config-diff`, `--configdiff`, `--compare-config` | Run only the config-diff compare-config check unless other positive check flags are also provided. | action=store_true; dest=config_diff |
| `--lldp` | Run only the SSH LLDP configuration check unless other positive check flags are also provided. | action=store_true |
| `--gnmi` | Run only the gNMI check unless other positive check flags are also provided. | action=store_true |
| `--system-health`, `--systemhealth` | Run only the system health check unless other positive check flags are also provided. | action=store_true; dest=system_health |
| `--optics-temperature`, `--optic-temperature` | Run only the optics temperature check unless other positive check flags are also provided. | action=store_true; dest=optics_temperature |
| `--skip-ztp` | Skip ZTP image check. | action=store_true |
| `--skip-dan` | Skip DAN status check. | action=store_true |
| `--skip-certificate` | Skip devices certificate get check. | action=store_true |
| `--skip-mgmt-ts` | Skip mgmt/ts in-service check. | action=store_true |
| `--skip-hostname-validation` | Skip qcli hostname validation check. | action=store_true |
| `--racks` | Rack numbers for certificate, mgmt/ts, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature checks, comma/space separated, e.g. 0119,0120. |  |
| `--device-file` | File with one device hostname per line for mgmt/ts rack resolution, hostname validation, static MAC, link flap, config-diff, LLDP, gNMI, system health, and optics temperature checks. Blank/# lines ignored. |  |
| `--certificate-rack-region` | Rack prefix used for certificate check. Defaults to the target value, e.g. iad60. |  |
| `--certificate-include-management` | Include management devices matching *-m1-* in certificate verification. Default excludes them. | action=store_true |
| `--mgmt-ts-rack-region` | Rack prefix used for mgmt/ts check. Defaults to the target value, e.g. iad60. |  |
| `--mgmt-ts-roles` | Role selector used for mgmt/ts check. Default: mgmt,ts. | default=mgmt,ts |
| `--mgmt-ts-timeout` | Timeout in seconds for mgmt/ts interactive state check. Default: 900. | default=900; type=int |
| `--hostname-validation-rack-region` | Rack prefix used for hostname validation qfabt0 resolution. Defaults to the target value, e.g. iad60. |  |
| `--hostname-validation-timeout` | Timeout in seconds for qfabt0 scope resolution and qcli hostname validation. Default: 3600. | default=3600; type=int |
| `--static-mac-script` | Path to nv_static_mac_address_check.py, or builtin. Default: builtin. | default=DEFAULT_STATIC_MAC_SCRIPT |
| `--static-mac-region` | Region/site passed to nv_static_mac_address_check.py. Defaults to the target value, e.g. iad60. |  |
| `--skip-static-mac` | Skip static MAC verification. | action=store_true |
| `--static-mac-skip-state-check` | Pass --skip-state-check to nv_static_mac_address_check.py. Default behavior. | action=store_true; default=True |
| `--static-mac-state-check` | Run the static MAC deployed-state check instead of skipping it. | action=store_false; dest=static_mac_skip_state_check |
| `--static-mac-prompt-password` | Pass --prompt-password to nv_static_mac_address_check.py. | action=store_true |
| `--static-mac-show-ok` | Pass --show-ok to nv_static_mac_address_check.py. | action=store_true |
| `--static-mac-timeout` | Per-device SSH command timeout for static MAC check. Default: 60. | default=60; type=int |
| `--static-mac-run-timeout` | Overall timeout for the static MAC checker subprocess. Default: 3600. | default=3600; type=int |
| `--static-mac-workers` | Static MAC checker worker count. Default: 8. | default=8; type=int |
| `--static-mac-expected-count` | Expected static MAC address line count. Default: 5. | default=5; type=int |
| `--static-mac-username` | SSH username for static MAC check. |  |
| `--static-mac-jit-region` | JIT region override for static MAC check. |  |
| `--static-mac-jitpw-path` | Path to jitpw for static MAC check. |  |
| `--static-mac-debug-log` | Debug log path for static MAC SSH sessions. |  |
| `--skip-link-flap` | Skip link flap protection status check. | action=store_true |
| `--skip-config-diff`, `--skip-configdiff`, `--skip-compare-config` | Skip config-diff compare-config check. | action=store_true; dest=skip_config_diff |
| `--skip-lldp` | Skip SSH LLDP configuration check. | action=store_true |
| `--skip-gnmi` | Skip gNMI status check. | action=store_true |
| `--skip-system-health` | Skip system health check. | action=store_true |
| `--skip-optics-temperature` | Skip optics temperature Prometheus check. | action=store_true |
| `--link-flap-script` | Path to link_flap_protection.py, or builtin. Default: builtin. | default=DEFAULT_LINK_FLAP_SCRIPT |
| `--racktopo-script` | Path to multiplaner_racktopo.py, or builtin. Default: builtin. | default=DEFAULT_RACKTOPO_SCRIPT |
| `--racktopo-workers` | Parallel rack topology lookup worker count. Default: 1. | default=1; type=int |
| `--link-flap-username` | SSH username for link flap check. Defaults to local username. |  |
| `--link-flap-jit-region` | JIT region override for link flap check. Defaults to short region from target, e.g. iad. |  |
| `--link-flap-jitpw-path` | Path to jitpw for link flap check. |  |
| `--link-flap-prompt-password` | Prompt for link flap SSH password instead of retrieving it with jitpw. | action=store_true |
| `--link-flap-timeout` | Per-device SSH command timeout for link flap check. Default: 60. | default=60; type=int |
| `--link-flap-workers` | Link flap checker worker count. Default: 8. | default=8; type=int |
| `--link-flap-debug-log` | Debug log path for link flap SSH sessions. |  |
| `--config-diff-timeout`, `--configdiff-timeout`, `--lldp-compare-timeout` | Timeout in seconds for config-diff compare-config interactive job. Default: 900. | default=900; dest=config_diff_timeout; type=int |
| `--system-health-rack-region` | Rack prefix used for system health check. Defaults to the target value, e.g. iad60. |  |
| `--system-health-roles` | Role selector used with --devices-by-role for system health. Default: qfabt0. | default=qfabt0 |
| `--system-health-timeout` | Timeout in seconds for the system health interactive job. Default: 900. | default=900; type=int |
| `--optics-temperature-threshold-c` | PASS when no optics transceiver temperature is greater than this Celsius threshold. Default: 65. | default=65.0; type=float |
| `--optics-temperature-timeout` | Timeout in seconds for the optics temperature Prometheus query. Default: 300. | default=300; type=int |

<a id="script-multiplaner-placement-rack-info-py"></a>
### `multiplaner_placement_rack_info.py`

**Purpose:** Consolidates rack-location CSV exports and calculates placement-group and link-count planning data.

**Source summary:** Script: multiplaner_placement_rack_info.py Owner: Surjeet Singh (Surjeet.Singh@oracle.com) Team: Ai2ND Script overview --------------- This script ingests rack-location CSV exports, consolidates them, and generates console tables that summarize racks by placement group and platform (QFAB T0/T1, IPR, GPU). Outputs ------- 1) Consolidated CSV: merges all input CSVs into consolidated.csv (adds source_file column). 2) Network Summary: total rack counts and device totals by role. 3) Placement Group Details: per-PG/per-platform rack positions, CFAB block, sector, and link counts. 4) Excel Report: workbook with the same summary/detail tables shown in the console output. Link count logic (high level) ----------------------------- - Core PGs (151–154): link count is based on QFABT1 rack count (T1<>IPR rule). - Non-core PGs: GPU link counts depend on GPU type (b300/gb300), and T0<>T1 link counts are computed from the number of QFABT0 racks in that PG using a multiplier derived from PG151.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-multiplaner-racktopo-py"></a>
### `multiplaner_racktopo.py`

**Purpose:** Prints deployment-group and topology information for racks or devices.

**Parser description:** Print deployment-group/topology info.

**Typical help command:**

```bash
python3 multiplaner_racktopo.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Region/building code (e.g., aga5, cwl15). Region=first 3 letters; bldg=full value. | required=True |
| `-d`, `--device` | Device name (e.g., aga5-q2-p1-t0-r29) | dest=device_name |
| `-rack`, `--rack` | Rack number (e.g., 0604) | dest=rack_number |

<a id="script-ncp-firmware-upgrade-automation-py"></a>
### `ncp_firmware_upgrade_automation.py`

**Purpose:** Previews and runs `ncpcli devices firmware upgrade` with repeatable rack/device scope and run logs.

**Source summary:** Run NCPCLI firmware upgrades with repeatable scope selection and run logs. Examples: python3 ncp_firmware_upgrade_automation.py -r nrt --change-id CHANGE-4737261 --rack nrt4:3410,nrt4:3411,nrt4:3412,nrt4:3413 --devices 'nrt4-q1-b6-t0-*' python3 ncp_firmware_upgrade_automation.py -r nrt --change-id CHANGE-4737261 --devices-from-file devices.txt --execute

**Parser description:** Preview and run ncpcli devices firmware upgrade with a repeatable scope.

**Typical help command:**

```bash
python3 ncp_firmware_upgrade_automation.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | NCPCLI working region, for example nrt. | required=True |
| `--change-id` | Change ticket, for example CHANGE-4737261. | required=True |
| `--rack` | Rack selector, repeatable or comma-separated, e.g. nrt4:3410. | action=append |
| `--role` | Device role selector, repeatable or comma-separated. | action=append |
| `--state` | Device state selector, repeatable or comma-separated. | action=append |
| `--devices` | Device glob selector, repeatable or comma-separated. | action=append |
| `--exact-device` | Exact device name, repeatable or comma-separated. | action=append |
| `--devices-from-file` | File containing device names. | type=Path |
| `--execute` | Run with --not-dry-run. Default is dry-run. | action=store_true |
| `--yes` | Must equal the change ID when --execute is used. |  |
| `--precheck-tag` | _No help text in script._ | default=DEFAULT_PRECHECK_TAG |
| `--postcheck-tag` | _No help text in script._ | default=DEFAULT_POSTCHECK_TAG |
| `--batch-size` | _No help text in script._ | default=100; type=int |
| `--pause-time` | _No help text in script._ | default=0; type=int |
| `--post-healthcheck-retry-limit` | _No help text in script._ | default=3; type=int |
| `--ncpcli-bin` | _No help text in script._ | default=ncpcli |
| `--pyenv-version` | Set empty string to avoid PYENV_VERSION. | default=ncpcli-env |
| `--no-agent-auth` | _No help text in script._ | action=store_false; dest=use_agent_for_auth |
| `--log-dir` | _No help text in script._ | default=Path('upgrade-logs'); type=Path |
| `--skip-preview` | Do not run devices list before upgrade. | action=store_true |
| `--plan-only` | Print commands and exit without running ncpcli. | action=store_true |

<a id="script-ncpcli-conn-issues-py"></a>
### `ncpcli_conn_issues.py`

**Purpose:** Fetches and parses link/connectivity issues using NCPCLI.

**Parser description:** Fetch and parse link issues using ncpcli.

**Typical help command:**

```bash
python3 ncpcli_conn_issues.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--region` | Specify the region (e.g., iad, nrt). | required=True |
| `-g`, `--bldg` | Specify the building number (e.g., 31 => iad31). | required=True |
| `-b`, `--block` | Specify the block (e.g., 44 => Block44). | required=True |

<a id="script-ncpcli-direct-fec-scan-py"></a>
### `ncpcli_direct_fec_scan.py`

**Purpose:** Extracts device/interface pairs from text for direct FEC scan workflows.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-nps-portfolio-tracker-py"></a>
### `nps_portfolio_tracker.py`

**Purpose:** Tracks NPS, stock, and PPF portfolio values with live NAV/price data and trailing return reporting.

**Parser description:** Track NPS, stock, and PPF portfolio values.

**Typical help command:**

```bash
python3 nps_portfolio_tracker.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--live-nps-nav` | Revalue NPS holdings with latest npsnav.in NAVs using units from the latest statement CSVs. This is the default. | action=store_true; dest=live_nps_nav |
| `--no-live-nps-nav` | Use NPS values from the latest statement CSVs without fetching live npsnav.in NAVs. | action=store_false; dest=live_nps_nav |

<a id="script-nvidia-device-reboot-py"></a>
### `nvidia_device_reboot.py`

**Purpose:** Reboots NVIDIA devices in controlled batches and verifies each batch recovers over SSH before continuing.

**Source summary:** nvidia_device_reboot.py Purpose: Reboot NVIDIA devices in controlled batches, wait for each device to come back, and stop before the next batch if any device in the current batch does not recover successfully. What the script does: - prompts once for the SSH username unless --username is provided - fetches the password from `jitpw -e <region>` - reboots devices in parallel within each batch - automatically answers the reboot confirmation prompt - verifies the device is back by SSHing in again and running a check command - writes one log file per device plus a summary.txt file - stops before the next batch if any device in the current batch fails Accepted device inputs: - --device host - --device host,port - --device-file /path/to/file - --device-pattern "aga5-q2-p1-t1-r[1-10]" Device file format: - one entry per line - each line may be `host` or `host,port` - blank lines and lines starting with `#` are ignored Example: python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py --device-file /Users/tusharkeskar/Desktop/device.txt -r aga --batch-size 5 Example output: Username: tkeskar [15:58:43] Starting reboot run for 96 device(s) in 20 batch(es) of up to 5 [15:58:43] Logs will be written under /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot_logs/20260421_155843 [15:58:43] Starting batch 1/20 with 5 device(s) [15:58:43] aga5-q2-p1-t1-r1: starting reboot [15:58:45] aga5-q2-p1-t1-r1: issuing reboot command [15:58:45] aga5-q2-p1-t1-r1: reboot confirmation prompt detected, sending 'y' [15:58:47] aga5-q2-p1-t1-r1: waiting 60s before up-check [15:59:47] aga5-q2-p1-t1-r1: verify attempt 1 [16:00:02] aga5-q2-p1-t1-r1: still waiting for device to come back [16:02:07] aga5-q2-p1-t1-r1: verify attempt 5 [16:02:09] aga5-q2-p1-t1-r1: device is back up [16:02:09] aga5-q2-p1-t1-r1: reboot complete and verified [16:02:10] Batch 1/20 completed successfully Help: python3 /Users/tusharkeskar/tools/random-scripts/nvidia_device_reboot.py --help

**Parser description:** Reboot NVIDIA devices, verify they come back over SSH, and gate progress batch-by-batch.

**Typical help command:**

```bash
python3 nvidia_device_reboot.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--device` | Repeatable device entry: host or host,port | action=append; type=parse_host_line |
| `--device-pattern` | Repeatable host pattern, for example: "aga5-q2-p1-t1-r[1-10]" | action=append; default= |
| `--device-file` | Path to file with one device per line in host or host,port format |  |
| `--username` | SSH username. If omitted, prompt once at startup |  |
| `-r`, `--region` | Region passed to jitpw -e <region>, for example: aga | required=True |
| `--cmd` | f'Reboot command to run on the device. Default: {DEFAULT_REBOOT_CMD}' | default=DEFAULT_REBOOT_CMD |
| `--verify-cmd` | f'Command used after reboot to confirm the device is up. Default: {DEFAULT_VERIFY_CMD}' | default=DEFAULT_VERIFY_CMD |
| `--reboot-timeout` | Seconds to wait for reboot command and confirmation handling | default=90; type=int |
| `--initial-wait` | Seconds to wait after reboot before starting SSH up-checks | default=60; type=int |
| `--retry-interval` | Seconds between SSH up-check attempts | default=20; type=int |
| `--verify-timeout` | Total seconds allowed for a device to come back after reboot | default=900; type=int |
| `--log-dir` | Optional base directory for per-device logs and summary.txt |  |
| `--batch-size` | Devices per batch. Next batch starts only if the current batch fully succeeds | default=10; type=int |

<a id="script-nvidia-link-flap-py"></a>
### `NVIDIA_Link_Flap.py`

**Purpose:** Checks NVIDIA/Cumulus switches for link flap-protection violations from an autonet XLSX plan and can optionally clear them.

**Source summary:** Rack / Q2-Q3 / T0-T1 / plane-wide linkflap checker and clearer. What it does: - Reads switches from the XLSX plan for the selected fabric - Can filter by rack and/or q2/q3 and/or t0/t1 and/or plane - Connects to each switch with dssh - Runs `nv show interface status` - Detects lines containing `linkflap` - Optionally clears link flap-protection violations - Writes CSV and HTML reports - Prints a bottom-of-run summary including every switch/interface hit Examples: python3 NVIDIA_Link_Flap.py -re aga -n 5 -r 0603 --dry-run python3 NVIDIA_Link_Flap.py -re jbp -n 15 -q2 -t0 -p2 --dry-run python3 NVIDIA_Link_Flap.py -re aga -n 5 -t1 -p4 --clear python3 NVIDIA_Link_Flap.py --xlsx ~/autonet/autonet-plans/aga/aga5-cables.xlsx -q3 --dry-run python3 NVIDIA_Link_Flap.py -re jbp -n15 -q 2 -t 1 -p 1 --dry-run Filter behavior: - Multiple filters are ANDed together. - `-q2` and `-q3` are mutually exclusive. - Rack is optional. - If no rack is given, the script searches the entire XLSX and filters by the requested name patterns. - Use `-re/--region` plus `-n/--number` for auto-discovery, or pass `--xlsx` directly. - Short flags also accept forgiving forms such as `-n15`, `-q 2`, `-t 1`, and `-p 1`.

**Parser description:** Rack / q2-q3 / T0-T1 / plane-wide linkflap checker and clearer. Short flags accept both attached and spaced forms, such as -n15 or -n 15, -q2 or -q 2, -t1 or -t 1, and -p1 or -p 1.

**Typical help command:**

```bash
python3 NVIDIA_Link_Flap.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `-r`, `--racks` | Comma-separated racks e.g. 0604,0704 |  |
| `-re`, `--region` | Region prefix used with -n, e.g. aga or jbp |  |
| `-n`, `--number` | Fabric number used with --region, e.g. 5 or 15 | type=int |
| `--autonet-root` | Optional autonet root or autonet-plans path. If omitted, the script checks $AUTONET_PLANS_ROOT, $AUTONET_ROOT, ~/autonet, ~/tools/autonet, and common nested layouts. |  |
| `--xlsx` | Path to input XLSX. Overrides auto-discovery from -re/--region and -n/--number. |  |
| `--out` | Output base name (optional) |  |
| `--interactive` | Prompt for missing inputs in the terminal | action=store_true |
| `--clear` | Clear link flap-protection violations on switches with hits | action=store_true |
| `--dry-run` | Do not clear; only report hits | action=store_true |
| `-q2` | Select only Q2 switches | action=store_true |
| `-q3` | Select only Q3 switches | action=store_true |
| `-t0` | Select only T0 switches (name contains -t0-) | action=store_true |
| `-t1` | Select only T1 switches (name contains -t1-) | action=store_true |
| `-p1` | Select only plane 1 switches (name contains -p1-) | action=store_true |
| `-p2` | Select only plane 2 switches (name contains -p2-) | action=store_true |
| `-p3` | Select only plane 3 switches (name contains -p3-) | action=store_true |
| `-p4` | Select only plane 4 switches (name contains -p4-) | action=store_true |

<a id="script-onet-automatic-py"></a>
### `onet_automatic.py`

**Purpose:** Copies ONET upgrade files to devices and runs remote upgrade commands.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-onet-remote-firmware-upgrade-py"></a>
### `onet_remote_firmware_upgrade.py`

**Purpose:** SSHes to a device and upgrades ONET firmware versions to known-good target versions.

**Source summary:** Author: Akhil Kadali Email: akhil.kadali@oracle.com Purpose: This script takes a device as an input and upgrades the firmware versions 146,228,230 to their respective healthy versions https://confluence.oci.oraclecorp.com/display/NET/CNE+Scripts+for+Cluster+Validation

**Typical help command:**

```bash
python3 onet_remote_firmware_upgrade.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `device` | the device to upgrade the optic firmware on |  |
| `-v`, `--verbose` | displays output for the commands as they are executed | action=store_true |
| `-p`, `--password` | jitpw for the device |  |

<a id="script-optics-audit-excel-report-py"></a>
### `optics_audit_Excel_report.py`

**Purpose:** Parses NCPCLI optics-related text output and creates an Excel audit report.

**Source summary:** This script takes text input file which has data produced by the following ncpcli commands: update-device-list --device-names-matching "*" --role qfabt0 --role qfabt1 --role qfabt2 --state deployed --state in-service --state maintenance devices run-command 'show inventory' | grep "Entity|^ [0-9][0-9 ]*[ ].[A-Za-z].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9][A-Z0-9 ].*$" | grep -v "FINISAR|Accelight|O-NET" Data Sample: ncpcli@aga 2024-08-26 10:08:49> devices run-command 'show inventory' | grep "Entity|^ [0-9][0-9 ]*[ ].[A-Za-z].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9][A-Z0-9 ].*$" | grep -v "FINISAR|Accelight|O-NET" 10:09:00 - WARNING [thr=6425997312]- das_client: Url: https://127.0.0.1:51460/v1/devices/aga1-q1-b2-t0-r7/command?name=show+inventory&format=text Response: 502 {"message":"problems reaching with addresses ['10.160.7.83 (OlympusDeviceUnreachable(ConnectionException("Socket error during eAPI authentication: HTTPSConnectionPool(host=\'10.160.7.83\', port=443): Max retries exceeded with url: /login (Caused by NewConnectionError(\'<urllib3.connection.HTTPSConnection object at 0x7fa7c8fd6640>: Failed to establish a new connection: [Errno 113] No route to host\'))")))']","name":"aga1-q1-b2-t0-r7"} Entity: aga1-q1-b2-t0-r1 1 Amphenol NDAAFF-O103 APE22191038WR9 F 2 Amphenol NDAAFF-O103 APE22191038WSK F 3 Amphenol NDAAFF-O103 APE22191038WV9 F 4 Amphenol NDAAFG-O106 APE2301106970E B Usage: 1: Update file path, you can add 1 or more than one files in the scrip. # List of file paths to process file_paths = [ 'lhr_optics_audit.txt', 'fra_optics_audit.txt', 'phx_optics_audit.txt', 'aga_optics_audit.txt', 'iad_optics_audit.txt', 'ord_optics_audit.txt', 'gru_optics_audit.txt', 'kix_optics_audit.txt', 'sgu_optics_audit.txt', 'sjc_optics_audit.txt', 'syd_optics_audit.txt', 'vcp_optics_audit.txt' ] 2: Run Script python3 optics_audit_Excel_report.py

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-parse-topospec-api-output-py"></a>
### `parse_topospec_api_output.py`

**Purpose:** Parses UFM TopoSpec diff API output and enriches GUID/port links with rack/elevation context.

**Typical help command:**

```bash
python3 parse_topospec_api_output.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--jsonfile` | API output from curling on the UFM container on the host (e.g. topospec_api_output.json) | required=True; type=str |
| `--linktype` | Which type of link issues to print | default=changed; choices=changed, removed, both |
| `--su` | Which type su are you auditing? | default=1 |

<a id="script-phx-dg-report-recalculator-py"></a>
### `phx_dg_report_recalculator.py`

**Purpose:** Builds PHX deployment-group progress reports from qcli full-report workbooks.

**Source summary:** Build a PHX DG progress report from qcli full_report workbooks. By default this script reads every *_full_report.xlsx in ~/qclihcdata/phx and maps them to DG1, DG2, ... in filename order. Use --excel DG=path when the file order should be explicit.

**Parser description:** Build a PHX DG progress report from qcli *_full_report.xlsx workbooks.

**Typical help command:**

```bash
python3 phx_dg_report_recalculator.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--data-dir` | f'Directory to scan for *_full_report.xlsx files. Default: {DEFAULT_DATA_DIR}' | default=DEFAULT_DATA_DIR; type=Path |
| `--excel` | Workbook input. Use a bare path to map files in order, or DG=path for explicit mapping. Repeat for multiple workbooks. Defaults to all *_full_report.xlsx files in --data-dir. | action=append |
| `--site-tag`, `--site`, `--tag` | f'PHX site/build tag for rack mapping and qcli generation. Default: {DEFAULT_SITE_TAG}.' | default=DEFAULT_SITE_TAG; choices=sorted(SITE_CONFIGS) |
| `--targets` | Comma-separated target names for bare --excel paths, for example DG1,DG2,DG3,DG4,DG5. Use 'all' for every target in the selected site rack map, or every supported qcli target when used with --print-qcli-command. |  |
| `--print-qcli-command` | Print the combined qcli hc-summary command for --targets, then exit. | action=store_true |
| `--relax` | Relax non-IPR optics RX/TX min/max thresholds by 1 dBm. | action=store_true |
| `--relax-ipr` | Relax IPR optics thresholds to RX -5..3 dBm and TX -3..3 dBm. | action=store_true |
| `--previous` | f'Previous formatted report to compare against. If --previous is given without a path, uses {DEFAULT_PREVIOUS_REPORT}. Use --previous - to read from stdin.' | nargs=?; type=Path |
| `--output` | Write the generated report to this file. | type=Path |
| `--output-excel` | Write grouped workbook rows and split FEC sheets to this Excel file. | type=Path |
| `--insights-provider` | f'Insight generator: auto, openai, aider, chatgpt, codex, or off. Aliases aider/chatgpt/codex use the OpenAI-compatible Responses API. Default: {DEFAULT_INSIGHTS_PROVIDER}.' | default=DEFAULT_INSIGHTS_PROVIDER |
| `--insights-model` | f'Model used for API-backed insight generation. Default: {DEFAULT_INSIGHTS_MODEL}.' | default=DEFAULT_INSIGHTS_MODEL |
| `--insights-base-url` | f'OpenAI-compatible API base URL. Default: {DEFAULT_INSIGHTS_BASE_URL}.' | default=DEFAULT_INSIGHTS_BASE_URL |
| `--insights-timeout` | f'Insight API request timeout in seconds. Default: {DEFAULT_INSIGHTS_TIMEOUT:g}.' | default=DEFAULT_INSIGHTS_TIMEOUT; type=float |
| `--debug` | Print workbook-to-DG mapping to stderr. | action=store_true |

<a id="script-qcli-hc-summary-gui-py"></a>
### `qcli_hc_summary_gui.py`

**Purpose:** Local web UI that builds and launches `qcli hc-summary` commands.

**Parser description:** Local web GUI for qcli hc-summary

**Typical help command:**

```bash
python3 qcli_hc_summary_gui.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--host` | _No help text in script._ | default=127.0.0.1 |
| `--port` | _No help text in script._ | default=8780; type=int |
| `--no-browser` | _No help text in script._ | action=store_true |

<a id="script-qfab-arista-lldp-tool-py"></a>
### `qfab_arista_lldp_tool.py`

**Purpose:** Enables/disables and verifies LLDP on GPU-facing QFAB switch interfaces discovered from NCPCLI.

**Source summary:** Enable or disable LLDP on GPU-facing interfaces of QFAB Arista switches. The tool uses NCPCLI only for read-only rack-to-device discovery. Device configuration and verification are performed over direct SSH. Rack discovery filters for EOS devices, and the default Arista interface discovery command is: show interfaces description | grep gpu Usage examples: # Enable LLDP for several racks. ./qfab_arista_lldp_tool.py -r fra12 --racks 5113,5213,5713,5813 --vendor arista --action enable # Disable LLDP on GPU-facing interfaces. ./qfab_arista_lldp_tool.py -r fra12 --racks 5113,5213 --vendor arista --action disable # Use repeated rack options or a numeric rack range. ./qfab_arista_lldp_tool.py -r fra12 --rack 5113 --rack 5213 --vendor arista --action enable ./qfab_arista_lldp_tool.py -r fra12 --racks 5113-5116 --vendor arista --action enable # Operate on explicit devices instead of racks. ./qfab_arista_lldp_tool.py --device fra12-q2-b7-t0-r33 --vendor arista --action enable Run ./qfab_arista_lldp_tool.py --help for all options.

**Parser description:** Enable or disable LLDP on GPU-facing interfaces of QFAB switches, then verify the requested state.

**Typical help command:**

```bash
python3 qfab_arista_lldp_tool.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--vendor` | Vendor applied to all devices specified in this run. | required=True; choices=arista, nvidia |
| `--action` | Enable or disable LLDP on matched interfaces. | required=True; choices=enable, disable |
| `--device` | Repeatable: --device host[,port] | action=append; type=parse_host_line |
| `--device-pattern` | Repeatable: --device-pattern "aga5-q2-p1-t0-r[1-10]" | action=append; default= |
| `--device-file` | File with one host per line: host or host,port. Blank/# lines ignored. |  |
| `--timeout` | _No help text in script._ | default=60; type=int |
| `--debug-log` | _No help text in script._ |  |
| `--cmd` | Override discovery command. If omitted, a vendor-specific default is used. |  |
| `-r`, `--region` | Site/building such as fra12; NCP region is derived as fra. |  |
| `--rack`, `--racks` | Rack numbers; repeat the option or use comma/space-separated values. Numeric ranges such as 0706-0708 are also supported. | action=append; default=; dest=racks |

<a id="script-rack-no-parser-from-plan-rack-file-py"></a>
### `rack_no_parser_from_plan_rack_file.py`

**Purpose:** Parses plan/rack files and extracts rack numbers.

**Parser description:** Parse input file and extract Rack Info

**Typical help command:**

```bash
python3 rack_no_parser_from_plan_rack_file.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--block` | e.g: bldg14-block1 | type=str |
| `--old_rack_sku` | e.g: net.ad_gfab_v1_400_t2_c1_1.02 | type=str |
| `--new_rack_sku` | e.g: net.ad_gfab_v1_400_t2_1.01 | type=str |
| `--input_file` | Input file path | type=str |

<a id="script-reboot-ifab-devices-py"></a>
### `reboot_ifab_devices.py`

**Purpose:** Runs parallel SSH checks and reboot-style actions for IFAB devices.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-remove-aoc-output-py"></a>
### `remove_aoc_output.py`

**Purpose:** Removes noisy AOC firmware output blocks from health-check text.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-restart-hostapd-batch-py"></a>
### `restart_hostapd_batch.py`

**Purpose:** Restarts `hostapd` on devices in controlled SSH batches with logs and recovery checks.

**Source summary:** Restart hostapd on devices in controlled SSH batches. Default command: sudo systemctl restart hostapd The script: - accepts --device, --device-pattern, or --device-file inputs - prompts once for SSH username/password unless --username is supplied - runs up to --batch-size devices at a time, default 10 - checks hostapd first and skips restart when it is already active - waits for the remote command to return to the shell prompt - verifies hostapd with `systemctl is-active hostapd` - closes each SSH connection before returning a result - writes per-device logs and summary.txt Examples: python3 restart_hostapd_batch.py --device-file devices.txt python3 restart_hostapd_batch.py --device hsg17-q2-b46-t0-r1 --device hsg17-q2-b46-t0-r2 python3 restart_hostapd_batch.py --device-pattern "hsg17-q2-b46-t0-r[1-10]"

**Parser description:** Restart hostapd over SSH in controlled batches.

**Typical help command:**

```bash
python3 restart_hostapd_batch.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--device` | Repeatable: --device host[,port] | action=append; type=parse_host_line |
| `--device-pattern` | Repeatable: --device-pattern "hsg17-q2-b46-t0-r[1-10]" | action=append; default= |
| `--device-file` | File with one host or host,port per line |  |
| `--username` | SSH username. If omitted, prompt once |  |
| `--cmd` | f'Command to run. Default: {DEFAULT_CMD}' | default=DEFAULT_CMD |
| `--verify-cmd` | f'Verification command. Default: {DEFAULT_VERIFY_CMD}' | default=DEFAULT_VERIFY_CMD |
| `--expected-verify` | f'Exact line expected in verify output. Default: {DEFAULT_EXPECTED_VERIFY}' | default=DEFAULT_EXPECTED_VERIFY |
| `--skip-verify` | Skip the post-restart verification command | action=store_true |
| `--connect-timeout` | SSH login timeout in seconds | default=30; type=int |
| `--connect-retries` | SSH connection attempts before failing a device. Default: 3 | default=3; type=int |
| `--connect-retry-delay` | Seconds between SSH connection attempts. Default: 5 | default=5; type=int |
| `--command-timeout` | Remote command timeout in seconds | default=120; type=int |
| `--verify-delay` | f'Seconds to wait before each verification attempt. Default: {DEFAULT_VERIFY_DELAY}' | default=DEFAULT_VERIFY_DELAY; type=int |
| `--verify-retries` | f'Number of verification attempts before failing. Default: {DEFAULT_VERIFY_RETRIES}' | default=DEFAULT_VERIFY_RETRIES; type=int |
| `--batch-size` | Devices per batch. Default: 10 | default=DEFAULT_BATCH_SIZE; type=int |
| `--continue-on-failure` | Continue to later batches even if a device fails | action=store_true |
| `--strict-hostkey` | SSH StrictHostKeyChecking value | default=ask; choices=ask, no, yes |
| `--log-dir` | Optional base directory for logs |  |
| `--dry-run` | Print expanded devices and exit without SSH | action=store_true |

<a id="script-run-ifab-commands-py"></a>
### `run_ifab_commands.py`

**Purpose:** Runs arbitrary SSH actions on IFAB/network devices in parallel.

**Parser description:** Run SSH actions on network devices.

**Typical help command:**

```bash
python3 run_ifab_commands.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--file` | Path to device file | required=True |
| `--action` | Action to perform | required=True; choices=fae, factory_reset, reload, show_version |

<a id="script-run-ifab-tests-py"></a>
### `run_ifab_tests.py`

**Purpose:** Runs IFAB health checks, parses cabling/optics issues, and aggregates results.

**Typical help command:**

```bash
python3 run_ifab_tests.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--region` | _No help text in script._ | required=True; type=str |
| `--racks` | _No help text in script._ | required=True; type=list_str |
| `--role` | _No help text in script._ | required=True; type=str |
| `--summaryonly` | _No help text in script._ | required=False; action=store_true |
| `--noyubikey` | _No help text in script._ | required=False; action=store_true |
| `--ncpoutput` | _No help text in script._ | required=False; action=store_true |
| `--su_number` | _No help text in script._ | required=True; type=str |
| `--tests` | Comma-separated test names to run (e.g. test_interface_phy_ifab,test_ifab_optics) | required=True; type=list_str |

<a id="script-script-to-validate-layouts"></a>
### `Script to validate layouts`

**Purpose:** Office Script for Excel that creates validation pivot sheets from a raw layout export.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-silencer-management-tool-py"></a>
### `silencer_management_tool.py`

**Purpose:** Creates, views, searches, and expires device/interface silencers for validation workflows.

**Source summary:** The tool aimed at simplifying and accelerating use of silencers, especially as we handle an increasing number of builds. The goal is to reduce the manual effort and time we spend on routine silencer operations such as creation, expiration, and lookup. Key features: * Generates and executes all 4 required silencer commands (device and remote side for tiers) in a single run. * Supports expiring multiple silencers at once by accepting a list of silencer IDs, especially useful for clearing an entire block in one command. * You can view silencer details by device name, silencer ID, block, or tier. The output includes a clear summary with key information such as silencer status, created by, device list, start time, and end time. Usage: Run the script in your terminal: python3 silencer_management_tool

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-spectrum-port-check-py"></a>
### `spectrum_port_check.py`

**Purpose:** Site-aware Spectrum switch port checker that resolves devices by hostname or rack/elevation/port, runs interface/LLDP/optic checks, clears link flap protection, and controls bounce behavior.

**Source summary:** Unified Spectrum port check flow. Examples: python spectrum_port_check.py --site hsg17 --rack 1010 --elevation 1 --port swp59s0 python spectrum_port_check.py hsg17 --location 1010 1 swp59s0 python spectrum_port_check.py --site aga4 aga4-q1-p1-t1-r1 swp1 python spectrum_port_check.py hsg17-q2-p4-t1-r33 swp59s0

**Parser description:** Run Spectrum port checks by hostname or site/rack/elevation.

**Typical help command:**

```bash
python3 spectrum_port_check.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--site`, `--tag` | Site/build tag, for example aga5 or hsg17. | metavar=SITE |
| `positional` | <device> <port> or --location rack elevation port | nargs=* |
| `--location`, `--rack-port`, `--rep` | _No help text in script._ | nargs=3; metavar=RACK, ELEVATION, PORT |
| `--rack` | _No help text in script._ |  |
| `--elevation` | _No help text in script._ |  |
| `--port`, `--device-port` | _No help text in script._ | dest=port |
| `target_args` | Target arguments for the check. | nargs=* |

<a id="script-storekeeper-data-by-file-py"></a>
### `storekeeper_data_by_file.py`

**Purpose:** Reads serial numbers from a file and fetches Storekeeper asset details into terminal/CSV output.

**Parser description:** Process a file of serial numbers and fetch asset info from Storekeeper.

**Typical help command:**

```bash
python3 storekeeper_data_by_file.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--filename` | The file containing the serial numbers (e.g., Serial_No.txt). | required=True; metavar=Serial_No.txt; type=str |

<a id="script-switch-guid-map-py"></a>
### `switch_guid_map.py`

**Purpose:** Static HSG switch-to-GUID mapping used by TopoSpec and GUID enrichment helpers.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-swp-subinterface-bounce-py"></a>
### `swp_subinterface_bounce.py`

**Purpose:** Finds NVIDIA/Cumulus `swp` subinterfaces that are not up/up and can bounce affected lanes.

**Source summary:** Check NVIDIA/Cumulus swp subinterfaces and optionally bounce bad lanes.

**Parser description:** Use dssh to find swp subinterfaces that are not up/up, show their transceiver details, and optionally bounce them.

**Typical help command:**

```bash
python3 swp_subinterface_bounce.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `device` | Device name, for example jbp15-q2-p4-t0-r193 | nargs=? |
| `interface` | Base interface in swpX format, for example swp43 | nargs=? |
| `--wait` | f'Seconds to wait after a bounce before rechecking status. Default: {DEFAULT_WAIT_SECONDS}' | default=DEFAULT_WAIT_SECONDS; type=int |
| `--timeout` | f'Timeout per dssh command in seconds. Default: {DEFAULT_TIMEOUT_SECONDS}' | default=DEFAULT_TIMEOUT_SECONDS; type=int |
| `--no-bounce` | Only print bad interfaces and transceiver output; do not prompt or bounce. | action=store_true |
| `--dssh` | Path/name of dssh executable. Default: dssh | default=dssh |

<a id="script-unhealthy-api-client-sh"></a>
### `unhealthy_api_client.sh`

**Purpose:** Shell helper for querying an unhealthy API endpoint with a canned request pattern.

**Source summary:** Usage: bash -x unhealthy_api_client.sh < <some_guid_file> Note: you do NOT need to quote the guids.

**CLI tags / arguments:**

_No documented `argparse` or `click` flags were found. Review the source before running; the script may use hard-coded inputs, direct `sys.argv`, imported helpers, or environment-specific files._

<a id="script-work-tracker-py"></a>
### `work_tracker.py`

**Purpose:** Generates a workbook summarizing validation and Codex/script effort over time.

**Source summary:** Generate June validation and Codex/script effort tracker workbook.

**Parser description:** __doc__

**Typical help command:**

```bash
python3 work_tracker.py --help
```

**CLI tags / arguments:**

| Tag / argument | What it does | Parser details |
| --- | --- | --- |
| `--start` | Start date, YYYY-MM-DD | default=2026-06-01 |
| `--end` | End date, YYYY-MM-DD. Defaults to today's date. |  |
| `--output` | Output .xlsx path. Defaults to ~/qclihcdata/work_tracker_<username>_<generated_date>_<generated_time>.xlsx |  |
| `--include-log-effort` | Add terminal-active effort estimated from OneDrive terminal logs as a separate sheet and include it in total effort columns. | action=store_true |
| `--meeting-hours-csv` | Optional CSV with date plus minutes/meeting_minutes or hours/meeting_hours. Adds a Meeting Hours sheet and includes those hours in totals. |  |
| `--min-meeting-attendees` | When --meeting-hours-csv includes attendee_count/attendees, only include meeting rows with at least this many attendees. | default=0; type=int |
| `--codex-effort-csv` | Optional CSV with date plus minutes/hours or min_minutes/max_minutes. Adds Codex/script effort into the Updated Total sheet only. |  |
