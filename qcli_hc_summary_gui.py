#!/usr/bin/env python3

import argparse
import json
import shlex
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


QFABCLI_DIR = Path.home() / "tools" / "qfabcli"
QCLI_BIN_CANDIDATES = [
    Path.home() / ".pyenv" / "versions" / "qcli-env" / "bin" / "qcli",
    Path.home() / ".pyenv" / "shims" / "qcli",
]


def find_qcli_bin():
    for path in QCLI_BIN_CANDIDATES:
        if path.exists():
            return str(path)
    return "qcli"


QCLI_BIN = find_qcli_bin()
MAX_POST_BYTES = 64 * 1024


def split_words(value):
    text = str(value or "").strip()
    if not text:
        return []
    return shlex.split(text)


def clean_csv(value):
    return ",".join(part.strip() for part in str(value or "").replace(" ", ",").split(",") if part.strip())


def add_if(args, flag, value):
    value = str(value or "").strip()
    if value:
        args.extend([flag, value])


def build_qcli_args(payload):
    region = str(payload.get("region") or "").strip().lower()
    building = str(payload.get("building") or "").strip()
    if not region:
        raise RuntimeError("Region is required, example: phx")
    if not building:
        raise RuntimeError("Building is required, example: 20")

    args = [QCLI_BIN, "hc-summary", "-r", region, "-g", building]

    add_if(args, "-rea", payload.get("realm"))
    add_if(args, "-i", payload.get("instance") or "2")

    fabric = str(payload.get("fabric") or "sp").strip().lower()
    if fabric == "sp":
        args.append("-sp")
    elif fabric == "ib":
        args.append("-ib")
    elif fabric == "cne":
        args.append("--cne")

    scope = str(payload.get("scope") or "dg").strip().lower()
    if scope == "dg":
        add_if(args, "-dg", clean_csv(payload.get("dg")))
    elif scope == "rack":
        racks = clean_csv(payload.get("racks"))
        add_if(args, "-rr", racks)
        if payload.get("filter_rack_same", True):
            add_if(args, "-fr", racks)
    elif scope == "block":
        add_if(args, "-b", payload.get("block"))
    elif scope == "column":
        add_if(args, "-c", payload.get("column"))
    elif scope == "device":
        add_if(args, "-d", clean_csv(payload.get("devices")))

    add_if(args, "-s", payload.get("state"))
    add_if(args, "-pl", clean_csv(payload.get("planar")))
    add_if(args, "-ctg", payload.get("customtag"))
    add_if(args, "-ec", payload.get("extended_checks"))
    add_if(args, "-p", payload.get("ppfile"))

    if payload.get("failures_only", True):
        args.append("--failures-only")
    if payload.get("placement_group", True):
        args.append("-pg")
    if payload.get("list_only"):
        args.append("-ls")
    if payload.get("lldp_only"):
        args.append("--lldp-check-only")
    if payload.get("t1_reports"):
        args.append("--t1-reports")
    if payload.get("raw"):
        args.append("--raw")
    if payload.get("verbose"):
        args.append("-v")
    if payload.get("optics_relax"):
        args.append("--optics-relax")
    if payload.get("no_apex_update"):
        args.append("-nau")
    if payload.get("slack"):
        args.append("--slack")

    args.extend(split_words(payload.get("extra_args")))
    return args


def shell_command(args):
    command = " ".join(shlex.quote(str(part)) for part in args)
    return f"cd {shlex.quote(str(QFABCLI_DIR))} && {command}"


def terminal_script(command):
    return (
        "echo 'Running qcli hc-summary from ~/tools/qfabcli'; "
        f"{command}; "
        "echo; echo 'qcli command finished. Press Enter to close this window.'; read"
    )


def run_in_terminal(command):
    script = terminal_script(command)
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Terminal"',
            "-e",
            f"do script {json.dumps(script)}",
            "-e",
            "activate",
            "-e",
            "end tell",
        ],
        check=True,
    )


def command_details(args):
    return "\n".join(
        [
            f"Working dir: {QFABCLI_DIR}",
            f"qcli bin   : {QCLI_BIN}",
            "",
            "Command args:",
            "\n".join(f"  {arg}" for arg in args),
        ]
    )


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QCLI HC Summary GUI</title>
  <style>
    :root {
      --bg: #f5f7fa;
      --panel: #ffffff;
      --line: #ccd5e1;
      --text: #17202a;
      --muted: #5d6b7a;
      --accent: #126b5f;
      --accent2: #214f8f;
      --danger: #a33a36;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      height: 54px;
      padding: 0 18px;
      background: #17202a;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
    main {
      padding: 14px;
      display: grid;
      gap: 12px;
      grid-template-rows: auto auto minmax(250px, 1fr);
      min-height: calc(100vh - 54px);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 10px;
      align-items: end;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      margin-bottom: 4px;
    }
    input, select, textarea {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 9px;
      font: inherit;
    }
    input[type="checkbox"] {
      width: auto;
      min-height: 0;
      margin: 0 7px 0 0;
    }
    .span-1 { grid-column: span 1; }
    .span-2 { grid-column: span 2; }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .check {
      min-height: 36px;
      display: flex;
      align-items: center;
      color: var(--text);
      font-weight: 520;
    }
    button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 12px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.secondary { color: var(--accent2); }
    button:disabled { opacity: 0.6; cursor: wait; }
    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .scope-fields { display: contents; }
    .hidden { display: none; }
    .command-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto auto;
      gap: 8px;
      align-items: stretch;
    }
    textarea {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      resize: vertical;
    }
    #command { min-height: 92px; }
    .split {
      display: grid;
      grid-template-columns: minmax(300px, 0.9fr) minmax(420px, 1.4fr);
      gap: 12px;
      min-height: 0;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .preset-list {
      display: grid;
      gap: 8px;
    }
    .preset {
      text-align: left;
      width: 100%;
      color: var(--accent2);
    }
    .section-title {
      margin: 0 0 8px;
      color: #344252;
      font-size: 13px;
      font-weight: 750;
    }
    #status { color: #fff; white-space: nowrap; font-size: 13px; }
    #status.error { color: #ffd9d7; }
    @media (max-width: 980px) {
      .grid, .split { grid-template-columns: 1fr; }
      .span-1, .span-2, .span-3, .span-4, .span-5, .span-6, .span-8 { grid-column: span 1; }
      .command-box { grid-template-columns: 1fr; }
      .actions { justify-content: stretch; }
      button { flex: 1; }
    }
  </style>
</head>
<body>
  <header>
    <h1>QCLI HC Summary GUI</h1>
    <div id="status">Ready</div>
  </header>
  <main>
    <section class="panel">
      <div class="grid">
        <div class="span-1">
          <label for="region">Region</label>
          <input id="region" value="phx">
        </div>
        <div class="span-1">
          <label for="building">Building</label>
          <input id="building" value="20">
        </div>
        <div class="span-1">
          <label for="instance">Instance</label>
          <input id="instance" value="2">
        </div>
        <div class="span-1">
          <label for="realm">Realm optional</label>
          <input id="realm" placeholder="oc1">
        </div>
        <div class="span-1">
          <label for="fabric">Fabric</label>
          <select id="fabric">
            <option value="sp" selected>Spectrum</option>
            <option value="ib">InfiniBand</option>
            <option value="cne">CNE</option>
            <option value="none">None</option>
          </select>
        </div>
        <div class="span-2">
          <label for="scope">Scope</label>
          <select id="scope">
            <option value="dg" selected>Deployment Group</option>
            <option value="rack">Rack list</option>
            <option value="block">Block</option>
            <option value="column">Column</option>
            <option value="device">Device range</option>
          </select>
        </div>
        <div class="span-1 scope-field" data-scope="dg">
          <label for="dg">DG</label>
          <input id="dg" value="3">
        </div>
        <div class="span-3 scope-field hidden" data-scope="rack">
          <label for="racks">Racks</label>
          <input id="racks" placeholder="1335,1336,1435,1436">
        </div>
        <div class="span-1 scope-field hidden" data-scope="block">
          <label for="block">Block</label>
          <input id="block">
        </div>
        <div class="span-1 scope-field hidden" data-scope="column">
          <label for="column">Column</label>
          <input id="column">
        </div>
        <div class="span-2 scope-field hidden" data-scope="device">
          <label for="devices">Devices</label>
          <input id="devices" placeholder="1,2 or 1-4">
        </div>
        <div class="span-1 scope-field hidden" data-scope="rack">
          <label>&nbsp;</label>
          <div class="check"><input id="filterRackSame" type="checkbox" checked>FR same</div>
        </div>
        <div class="span-1">
          <label for="state">State</label>
          <input id="state" placeholder="deployed">
        </div>
        <div class="span-2">
          <label for="planar">Planar</label>
          <input id="planar" placeholder="1-4">
        </div>
        <div class="span-2">
          <label for="customtag">Custom tag</label>
          <input id="customtag" placeholder="lldp_interfaces_check">
        </div>
        <div class="span-2">
          <label for="extendedChecks">Extended checks</label>
          <select id="extendedChecks">
            <option value="">None</option>
            <option value="all">all</option>
            <option value="sp-checks">sp-checks</option>
            <option value="hostnamevalidation">hostnamevalidation</option>
            <option value="nvidia-linkflap-audit">nvidia-linkflap-audit</option>
            <option value="nvidia-linkflap-clear">nvidia-linkflap-clear</option>
            <option value="configdiff">configdiff</option>
            <option value="deviceconfigstate">deviceconfigstate</option>
            <option value="silencer">silencer</option>
            <option value="spztpcheck">spztpcheck</option>
            <option value="spinfo">spinfo</option>
          </select>
        </div>
        <div class="span-3">
          <label for="ppfile">PP file optional</label>
          <input id="ppfile" placeholder="/Users/bduhan/tools/qfabcli/qcli/hc_summary_data/...xlsx">
        </div>
        <div class="span-3">
          <label for="extraArgs">Extra args</label>
          <input id="extraArgs" placeholder="--dg-batch-rack-limit 16">
        </div>
        <div class="span-1"><div class="check"><input id="failuresOnly" type="checkbox" checked>Failures</div></div>
        <div class="span-1"><div class="check"><input id="placementGroup" type="checkbox" checked>PG</div></div>
        <div class="span-1"><div class="check"><input id="listOnly" type="checkbox">List</div></div>
        <div class="span-1"><div class="check"><input id="lldpOnly" type="checkbox">LLDP</div></div>
        <div class="span-1"><div class="check"><input id="t1Reports" type="checkbox">T1</div></div>
        <div class="span-1"><div class="check"><input id="raw" type="checkbox">Raw</div></div>
        <div class="span-1"><div class="check"><input id="verbose" type="checkbox">Verbose</div></div>
        <div class="span-1"><div class="check"><input id="opticsRelax" type="checkbox">Optics</div></div>
        <div class="span-1"><div class="check"><input id="noApexUpdate" type="checkbox">No Apex</div></div>
        <div class="span-1"><div class="check"><input id="slack" type="checkbox">Slack</div></div>
        <div class="span-2 actions">
          <button id="build" class="primary">Build</button>
        </div>
      </div>
    </section>

    <section class="panel">
      <p class="section-title">Command</p>
      <div class="command-box">
        <textarea id="command" spellcheck="false"></textarea>
        <button id="copyCommand" class="secondary">Copy</button>
        <button id="runTerminal" class="primary">Run in Terminal</button>
        <button id="saveScript" class="secondary">Save .sh</button>
      </div>
    </section>

    <section class="split">
      <div class="panel">
        <p class="section-title">Quick Presets</p>
        <div class="preset-list">
          <button class="preset" data-preset="phx20dg3">PHX20 DG3 Spectrum</button>
          <button class="preset" data-preset="phx20racks">PHX20 racks 1335/1336/1435/1436</button>
          <button class="preset" data-preset="iad60dg3">IAD60 DG3 instance 3</button>
          <button class="preset" data-preset="jbp19dg16">JBP19 DG16 Spectrum</button>
          <button class="preset" data-preset="lldp">LLDP custom tag only</button>
        </div>
      </div>
      <div class="panel">
        <p class="section-title">Resolved Details</p>
        <pre id="details">Build a command to see details.</pre>
      </div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    let latestCommand = "";
    let latestPayloadSignature = "";

    function setStatus(text, isError = false) {
      $("status").textContent = text;
      $("status").className = isError ? "error" : "";
    }

    function payload() {
      return {
        region: $("region").value,
        building: $("building").value,
        instance: $("instance").value,
        realm: $("realm").value,
        fabric: $("fabric").value,
        scope: $("scope").value,
        dg: $("dg").value,
        racks: $("racks").value,
        filter_rack_same: $("filterRackSame").checked,
        block: $("block").value,
        column: $("column").value,
        devices: $("devices").value,
        state: $("state").value,
        planar: $("planar").value,
        customtag: $("customtag").value,
        extended_checks: $("extendedChecks").value,
        ppfile: $("ppfile").value,
        extra_args: $("extraArgs").value,
        failures_only: $("failuresOnly").checked,
        placement_group: $("placementGroup").checked,
        list_only: $("listOnly").checked,
        lldp_only: $("lldpOnly").checked,
        t1_reports: $("t1Reports").checked,
        raw: $("raw").checked,
        verbose: $("verbose").checked,
        optics_relax: $("opticsRelax").checked,
        no_apex_update: $("noApexUpdate").checked,
        slack: $("slack").checked
      };
    }

    function payloadSignature(data) {
      return JSON.stringify(data);
    }

    async function ensureFreshCommand() {
      const currentPayload = payload();
      if (!latestCommand.trim() || latestPayloadSignature !== payloadSignature(currentPayload)) {
        const ok = await buildCommand(currentPayload);
        if (!ok) return "";
      }
      return latestCommand.trim();
    }

    function updateScopeFields() {
      const scope = $("scope").value;
      document.querySelectorAll(".scope-field").forEach((field) => {
        field.classList.toggle("hidden", field.dataset.scope !== scope);
      });
    }

    async function buildCommand(requestPayload = payload()) {
      setStatus("Building...");
      try {
        const signature = payloadSignature(requestPayload);
        const response = await fetch("/api/build", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: signature
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Build failed");
        latestCommand = data.command;
        latestPayloadSignature = signature;
        $("command").value = data.command;
        $("details").textContent = data.details;
        setStatus("Command ready");
        return true;
      } catch (error) {
        latestCommand = "";
        latestPayloadSignature = "";
        setStatus(error.message, true);
        alert(error.message);
        return false;
      }
    }

    async function runTerminal() {
      setStatus("Opening Terminal...");
      const requestPayload = payload();
      const signature = payloadSignature(requestPayload);
      const response = await fetch("/api/run-terminal", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: signature
      });
      const data = await response.json();
      if (!response.ok) {
        setStatus(data.error || "Terminal launch failed", true);
        alert(data.error || "Terminal launch failed");
        return;
      }
      latestCommand = data.command;
      latestPayloadSignature = signature;
      $("command").value = data.command;
      setStatus("Started in Terminal");
    }

    async function copyCommand() {
      if (!await ensureFreshCommand()) return;
      await navigator.clipboard.writeText(latestCommand);
      setStatus("Command copied");
    }

    async function saveScript() {
      if (!await ensureFreshCommand()) return;
      const script = "#!/bin/zsh\n" + latestCommand + "\n";
      const blob = new Blob([script], {type: "text/x-shellscript"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "run_qcli_hc_summary.sh";
      link.click();
      URL.revokeObjectURL(url);
      setStatus("Script saved");
    }

    function preset(name) {
      const presets = {
        phx20dg3: {region:"phx", building:"20", instance:"2", scope:"dg", dg:"3", fabric:"sp"},
        phx20racks: {region:"phx", building:"20", instance:"2", scope:"rack", racks:"1335,1336,1435,1436", fabric:"sp", state:"deployed"},
        iad60dg3: {region:"iad", building:"60", instance:"3", scope:"dg", dg:"3", fabric:"sp"},
        jbp19dg16: {region:"jbp", building:"19", instance:"2", scope:"dg", dg:"16", fabric:"sp"},
        lldp: {customtag:"lldp_interfaces_check", lldpOnly:true}
      }[name];
      if (!presets) return;
      for (const [key, value] of Object.entries(presets)) {
        const id = {
          lldpOnly: "lldpOnly"
        }[key] || key;
        if (!$(id)) continue;
        if ($(id).type === "checkbox") $(id).checked = Boolean(value);
        else $(id).value = value;
      }
      updateScopeFields();
      buildCommand();
    }

    $("scope").addEventListener("change", updateScopeFields);
    $("build").addEventListener("click", buildCommand);
    $("copyCommand").addEventListener("click", copyCommand);
    $("runTerminal").addEventListener("click", runTerminal);
    $("saveScript").addEventListener("click", saveScript);
    document.querySelectorAll(".preset").forEach((button) => {
      button.addEventListener("click", () => preset(button.dataset.preset));
    });
    updateScopeFields();
    buildCommand();
  </script>
</body>
</html>
"""

HTML_BYTES = HTML.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send_bytes(self, status, body, content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status, body, content_type="text/plain; charset=utf-8"):
        self._send_bytes(status, body.encode("utf-8"), content_type)

    def _json(self, status, payload):
        self._send(
            status,
            json.dumps(payload, separators=(",", ":")),
            "application/json; charset=utf-8",
        )

    def _read_json_payload(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RuntimeError("Invalid Content-Length") from exc

        if length > MAX_POST_BYTES:
            raise RuntimeError(f"Request body too large; max is {MAX_POST_BYTES} bytes")

        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self):
        if urlparse(self.path).path in {"/", "/index.html"}:
            self._send_bytes(200, HTML_BYTES, "text/html; charset=utf-8")
            return
        self._send(404, "Not found")

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path not in {"/api/build", "/api/run-terminal"}:
                self._json(404, {"error": "Not found"})
                return

            payload = self._read_json_payload()
            args = build_qcli_args(payload)
            command = shell_command(args)

            if path == "/api/build":
                self._json(200, {"command": command, "details": command_details(args)})
            else:
                run_in_terminal(command)
                self._json(200, {"command": command, "started": True})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, format, *args):
        return


def find_free_port(host, requested_port):
    for port in range(requested_port, requested_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free port found from {requested_port} to {requested_port + 49}")


def parse_args():
    parser = argparse.ArgumentParser(description="Local web GUI for qcli hc-summary")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    port = find_free_port(args.host, args.port)
    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()
    print(f"QCLI HC Summary GUI: {url}")
    print("This is a separate script. It does not modify qcli.")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
