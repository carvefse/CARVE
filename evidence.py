"""Collect per-attempt evidence and assess declared requirements without guessing."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time

from device import expand


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+"\n")


class Journal:
    def __init__(self, path):
        self.path, self.lock = Path(path), threading.Lock()

    def __call__(self, kind, **data):
        row = dict(kind=kind, host_time=time.monotonic(), **data)
        with self.lock, self.path.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True)+"\n")


def read_journal(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).exists() else []


class Collection:
    def __init__(self, device, directory, run_id, record):
        self.device, self.directory, self.run_id, self.record = device, Path(directory), run_id, record
        self.remote = "/data/local/tmp/carve/"+run_id
        self.trace = "/data/misc/perfetto-traces/"+run_id+".pftrace"
        self.key = "carve_"+run_id
        self.log_process = None
        self.handles = []
        self.trace_started = False

    def start(self, config, max_duration_s):
        self.device.shell(["mkdir", "-p", self.remote])
        # Duration is a safety cap; normal shutdown stops only this named session.
        content = Path(config).read_text()
        if re.search(r"\bduration_ms\s*:", content):
            raise ValueError("Perfetto config must omit duration_ms; CARVE supplies the watchdog cap")
        resolved = self.directory/"perfetto.pbtxt"
        resolved.write_text(content+f"\nduration_ms: {int(max_duration_s*1000)}\n")
        self.device.call("push", str(resolved), self.remote+"/config.pbtxt")
        out = (self.directory/"logcat.txt").open("w")
        err = (self.directory/"logcat.stderr").open("w")
        self.handles = [out, err]
        self.log_process = subprocess.Popen(self.device.prefix+["logcat", "-b", "main", "-b", "system", "-b", "events", "-b", "crash", "-v", "monotonic", "-T", "1"], stdout=out, stderr=err, text=True)
        # perfetto drops privileges before opening a config; pipe it from the root shell.
        self.trace_started = True
        self.device.root("cat "+shlex.quote(self.remote+"/config.pbtxt")+" | "+shlex.join(["perfetto", "--txt", "-c", "-", "-o", self.trace, "--detach="+self.key]))
        self.device.shell(["log", "-t", "CARVE", "COLLECTION_START "+self.run_id])
        self.record("collection_start", remote_trace=self.trace, session=self.key)

    def stop(self):
        status = dict(trace_stopped=False, trace_pulled=False, logs_alive=False, errors=[])
        if self.trace_started:
            try:
                self.device.root(shlex.join(["perfetto", "--attach="+self.key, "--stop"]))
                status["trace_stopped"] = True
            except Exception as exc:
                status["errors"].append("stop: "+str(exc))
            try:
                export = self.remote+"/trace.pftrace"
                self.device.root("cat "+shlex.quote(self.trace)+" > "+shlex.quote(export)+" && chmod 644 "+shlex.quote(export))
                self.device.call("pull", export, str(self.directory/"trace.pftrace"))
                status["trace_pulled"] = True
            except Exception as exc:
                status["errors"].append("pull: "+str(exc))
        if self.log_process:
            status["logs_alive"] = self.log_process.poll() is None
            try:
                self.device.shell(["log", "-t", "CARVE", "COLLECTION_END "+self.run_id])
                time.sleep(.3)
            except Exception as exc:
                status["errors"].append("end marker: "+str(exc))
            self.log_process.terminate()
            try:
                self.log_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.log_process.kill()
                self.log_process.wait(timeout=2)
        for handle in self.handles:
            handle.close()
        self.record("collection_stop", **status)
        return status


def trace_evidence(directory, processor, markers, processes):
    trace = Path(directory)/"trace.pftrace"
    if not trace.exists() or not trace.stat().st_size or not processor:
        return {"state": "unverified", "reason": "trace or trace_processor_shell unavailable"}
    sql = """
SELECT 'bounds' kind, 'start' name, start_ts ts, end_ts value FROM trace_bounds
UNION ALL SELECT 'events', 'sched', min(ts), max(ts+dur) FROM sched
UNION ALL SELECT 'count', 'syscalls', 0, count(*) FROM raw WHERE name='sys_enter'
UNION ALL SELECT 'loss', name, 0, value FROM stats WHERE value>0 AND severity IN ('error','data_loss')
UNION ALL SELECT 'process', p.name, p.start_ts, count(s.id)
 FROM process p LEFT JOIN thread t USING(upid) LEFT JOIN sched s USING(utid)
 GROUP BY p.upid
"""
    def boundary(operation):
        aliases = [m["alias"] for m in markers if m.get("operation") == operation]
        if not aliases:
            return "NULL"
        alias = aliases[0].replace("'", "''")
        return f"(SELECT min(start_ts) FROM process WHERE name='{alias}' OR name GLOB '*/{alias}')"
    sql += ("UNION ALL SELECT 'count', 'observation_syscalls', 0, count(*) FROM raw "
            "WHERE name='sys_enter' AND ts >= "+boundary("observation_start")+
            " AND ts < "+boundary("observation_stop"))
    try:
        result = subprocess.run([processor, "-Q", sql, str(trace)], capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ValueError(result.stderr[-3000:])
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        save(Path(directory)/"trace_evidence.json", rows)
        def number(value):
            return None if value in (None, "", "[NULL]", "NULL") else int(value)
        bounds = next(r for r in rows if r["kind"] == "events" and r["name"] == "sched")
        losses = [r for r in rows if r["kind"] == "loss"]
        points = {}
        for marker in markers:
            hits = [number(r["ts"]) for r in rows if r["kind"] == "process" and r["name"].rsplit("/", 1)[-1] == marker["alias"] and number(r["ts"]) is not None]
            points[marker["event_id"]] = min(hits) if hits else None
        scheduled = {name: sum(number(r["value"]) or 0 for r in rows if r["kind"] == "process" and r["name"] == name) for name in processes}
        return dict(state="matched" if not losses else "mismatched", losses=losses,
                    start_ns=number(bounds["ts"]), end_ns=number(bounds["value"]),
                    syscall_count=number(next(r["value"] for r in rows if r["kind"] == "count" and r["name"] == "syscalls")),
                    observation_syscall_count=number(next(r["value"] for r in rows if r["kind"] == "count" and r["name"] == "observation_syscalls")),
                    markers=points, scheduled_processes=scheduled,
                    marker_source="Perfetto process.start_ts of boundary executable")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as exc:
        return dict(state="unverified", reason=str(exc))


def log_records(text):
    records = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+\.\d+)\s+", line)
        if match:
            records.append((float(match[1]), line))
    return records


def assess_action(action, records, run_id, coverage, trace):
    requirements, found, counters = action["evidence"], {}, []
    variables = {"run_id": run_id, "action_id": action["id"]}
    for key in ("start", "progress", "complete", "corroborate"):
        if key in requirements:
            pattern = re.compile(expand(requirements[key], variables))
            found[key] = [t for t, line in records if pattern.search(line)]
            if key == "progress" and "min_counter" in requirements:
                counters = [int(m["count"]) for _, line in records if (m := pattern.search(line)) and m["count"] is not None]
    tests = {key: len(values) >= (requirements.get("min_progress", 1) if key == "progress" else requirements.get("corroborate_min", 1) if key == "corroborate" else 1) for key, values in found.items()}
    if "min_counter" in requirements:
        tests["counter"] = bool(counters and max(counters) >= requirements["min_counter"])
    if all(found.get(key) for key in ("start", "progress", "complete")):
        tests["order"] = min(found["start"]) <= min(found["progress"]) <= max(found["progress"]) <= max(found["complete"])
    if "process" in requirements:
        scheduled = trace.get("scheduled_processes", {}).get(requirements["process"])
        tests["process"] = None if scheduled is None else scheduled > 0
    # A launch record, an uninstrumented driver, or a missing observation cannot prove payload execution.
    if not tests:
        state = "unverified"
    elif all(v is True for v in tests.values()):
        state = "matched"
    elif not coverage or any(v is None for v in tests.values()):
        state = "unverified"
    else:
        state = "mismatched"
    return dict(state=state, requirements=tests, observed=found, counters=counters, adequately_observed=coverage)


def overlap_check(target, background, bin_s):
    if target["state"] != "matched" or background["state"] != "matched":
        return dict(state="unverified", reason="both workload execution checks must be matched")
    t, b = target["observed"], background["observed"]
    if not all(x.get("start") and x.get("complete") and x.get("progress") for x in (t, b)):
        return dict(state="unverified", reason="start/progress/completion required for overlap")
    start, end = min(t["start"]), max(t["complete"])
    covered = min(b["start"]) <= start < end <= max(b["complete"])
    cursor, bins = start, []
    while cursor+bin_s <= end+1e-9:
        bins.append(any(cursor <= stamp < cursor+bin_s for stamp in b["progress"]))
        cursor += bin_s
    return dict(state="matched" if covered and all(bins) else "mismatched", target_start_s=start,
                target_end_s=end, interval_covered=covered, complete_bins=bins, bin_s=bin_s)


def assess_trace(trace, expected, collection, require_syscalls=True):
    points = trace.get("markers", {})
    missing = [event for event in expected if points.get(event) is None]
    trace.update(missing_boundaries=missing, syscalls_required=require_syscalls)
    if trace["state"] == "matched":
        actual = [points[event] for event in expected if points.get(event) is not None]
        coverage = bool(actual and trace["start_ns"] is not None and trace["end_ns"] is not None
                        and trace["start_ns"] <= min(actual) and trace["end_ns"] >= max(actual))
        trace["coverage"] = coverage
        events_present = not require_syscalls or trace.get("observation_syscall_count", 0) > 0
        trace["state"] = "matched" if coverage and not missing and events_present and collection.get("trace_stopped") else "mismatched"
    return trace


def assess(directory, processor=None):
    directory = Path(directory)
    plan = json.loads((directory/"plan.json").read_text())
    outcome = json.loads((directory/"outcome.json").read_text())
    spec, run_id = plan["scenario"], outcome["run_id"]
    journal = read_journal(directory/"events.jsonl")
    text = (directory/"logcat.txt").read_text(errors="replace") if (directory/"logcat.txt").exists() else ""
    logs = log_records(text)
    sentinel_start = [t for t, line in logs if "COLLECTION_START "+run_id in line]
    sentinel_end = [t for t, line in logs if "COLLECTION_END "+run_id in line]
    collection = outcome.get("collection", {})
    log_coverage = bool(sentinel_start and sentinel_end and sentinel_end[-1] > sentinel_start[0]
                        and collection.get("logs_alive") and outcome.get("boot_unchanged") is True
                        and not re.search(r"chatty.*expire|dropped \d+|logcat:.*(error|failed)", text, re.I))
    markers = [r for r in journal if r["kind"] == "marker"]
    processes = [a["evidence"]["process"] for a in spec["actions"] if "process" in a["evidence"]]
    trace = trace_evidence(directory, processor, markers, processes)
    baseline = [r for r in journal if r["kind"] == "baseline_check"]
    context = [r for r in journal if r["kind"] == "context_check"]
    checks = {}
    def aggregate(states):
        states = list(states)
        return "mismatched" if "mismatched" in states else "unverified" if not states or "unverified" in states else "matched"
    checks["baseline"] = {"state": aggregate(baseline[-1]["checks"].values()) if baseline else "unverified", "records": baseline}
    expected_context = len(spec["context"]["vehicle_states"])
    checks["context"] = {"state": aggregate([r["state"] for r in context]+(["unverified"] if len(context) < expected_context else [])), "records": context}
    checks["actions"] = {}
    clocks = [r for r in journal if r["kind"] == "clock_sample"]
    dispatches = [r for r in journal if r["kind"] == "dispatch"]
    trace["phase_windows"] = {}
    for phase in spec["observation"]["phases"]:
        boundaries = [next(e for e in plan["events"] if e["kind"] == kind and e["source"] == phase["id"])
                      for kind in ("phase_start", "phase_end")]
        window = dict(planned_offsets_s=[phase["start"], phase["end"]],
                      trace_bounds_ns=[trace.get("markers", {}).get(e["id"]) for e in boundaries])
        if clocks and "t0_host_monotonic" in outcome:
            offset = clocks[0]["device_boottime_s"]-clocks[0]["host_mid"]
            completions = [next((r["completed"] for r in dispatches if r["event_id"] == e["id"]), None) for e in boundaries]
            window.update(planned_device_estimate_ns=[round((outcome["t0_host_monotonic"]+e["offset_s"]+offset)*1e9) for e in boundaries],
                          host_completion_estimate_ns=[round((t+offset)*1e9) if t is not None else None for t in completions],
                          mapping_uncertainty_s=clocks[0]["uncertainty_s"])
        trace["phase_windows"][phase["id"]] = window
    observation_events = [next(e for e in plan["events"] if e["kind"] == kind)
                          for kind in ("observation_start", "observation_stop")]
    trace["observation_window"] = dict(
        planned_offsets_s=[spec["observation"]["start"], spec["observation"]["end"]],
        trace_bounds_ns=[trace.get("markers", {}).get(e["id"]) for e in observation_events],
        source="in-trace observation-boundary processes; collection also retains surrounding context")
    for action in spec["actions"]:
        start = next((r for r in dispatches if r["source"] == action["id"] and r["operation"] == "workload_start"), None)
        end = next((r for r in dispatches if r["source"] == action["id"] and r["operation"] == "workload_stop"), None)
        selected, window = [], None
        if clocks and start:
            clock = clocks[0]
            offset = clock["device_boottime_s"]-clock["host_mid"]
            low = start["dispatched"]+offset-clock["uncertainty_s"]
            high = (end["completed"] if end else outcome["finished_host_monotonic"])+offset+clock["uncertainty_s"]
            selected = [(t, line) for t, line in logs if low <= t <= high]
            window = [low, high]
        check = assess_action(action, selected, run_id, log_coverage and start is not None and end is not None and outcome["execution"] == "completed", trace)
        check["association_window_device_s"] = window
        check["association_source"] = "host dispatch mapped with clock-sample uncertainty; log timestamps retained"
        checks["actions"][action["id"]] = check
    expected = [e["id"] for e in plan["events"] if e["kind"] in ("phase_start", "phase_end", "observation_start", "observation_stop")]
    checks["trace"] = assess_trace(trace, expected, collection, spec["observation"].get("require_syscalls", True))
    route = spec["context"].get("route")
    route_records = [r for r in journal if r["kind"] == "route_sample"]
    checks["route"] = {"state": "not_requested" if route is None else "matched" if len(route_records) == len(route["samples"]) and all(r["ok"] for r in route_records) else "unverified" if outcome["execution"] != "completed" else "mismatched", "expected_samples": len(route["samples"]) if route else 0, "observed_samples": len(route_records)}
    checks["overlap"] = {}
    for target in (a for a in spec["actions"] if a["role"] == "target"):
        for background in (a for a in spec["actions"] if a["role"] == "background"):
            checks["overlap"][target["id"]+"/"+background["id"]] = overlap_check(checks["actions"][target["id"]], checks["actions"][background["id"]], background["evidence"].get("overlap_bin_s", 1))
    required = []
    for name in ("baseline", "context", "trace", "route"):
        if spec["policy"]["require_"+name]:
            required.append(checks[name]["state"])
    if spec["policy"]["require_actions"]:
        required.extend(v["state"] for v in checks["actions"].values())
    if spec["policy"]["require_overlap"]:
        required.extend([v["state"] for v in checks["overlap"].values()] or ["unverified"])
    result = dict(checks=checks, log_coverage=log_coverage, execution=outcome["execution"],
                  analysis_eligible=outcome["execution"] == "completed" and outcome.get("boot_unchanged") is True and all(s == "matched" for s in required))
    save(directory/"checks.json", result)
    return result
