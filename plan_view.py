"""Readable, offline preview of a resolved scenario and its ordered events."""
import json
from itertools import groupby
from pathlib import Path


def format_plan(plan):
    spec = plan["scenario"]
    baseline, observation, context = (spec[k] for k in ("baseline", "observation", "context"))
    actions = {a["id"]: a for a in spec["actions"]}
    number = lambda value: f"{value:g}"
    duration = number(spec["duration_s"])
    name = spec.get("name") or Path(plan["bindings"]["scenario"]).stem
    lines = [f"PLAN: {name}", "Preview only; no device commands have been executed.",
             f"Scenario: {duration} s | Scheduler: {spec['policy']['scheduler']}",
             f"Environment: {baseline['environment'].get('name', 'bundled configuration')}",
             f"Observe: {number(observation['start'])}-{number(observation['end'])} s",
             f"Capture margin: {number(observation['pre_roll_s'])} s before scenario, "
             f"{number(observation['post_roll_s'])} s after scenario",
             "", "BEFORE THE CLOCK STARTS"]
    lines.append(f"  Restore snapshot: {baseline['snapshot']}" if baseline["restore"]
                 else "  Use the prepared emulator without restoring a snapshot.")
    lines.append(f"  Wait up to {number(baseline['readiness_timeout_s'])} s for device readiness.")
    for action in actions.values():
        if action.get("apk"):
            lines.append(f"  Install {action['apk']} for {action['id']}.")
        for command in action.get("provision", []):
            display = command if isinstance(command, str) else json.dumps(command)
            lines.append(f"  Prepare {action['id']}: {display}")
    if baseline["checks"]:
        lines.append("  Check initial conditions: " + ", ".join(baseline["checks"]) + ".")
    route = context.get("route")
    lines += ["", "ROUTE"]
    if route:
        lines.append(f"  Play {route['path']} from {number(route['start'])} s for "
                     f"{number(route['duration_s'])} s, using the {route['speed_profile']} speed profile.")
        lines.append(f"  Generate {len(route['samples'])} GPS samples at intervals of "
                     f"{number(route['tick_s'])} s (including the route endpoint).")
        lines.append("  Also write route speed to the vehicle." if route["write_speed"]
                     else "  Inject GPS positions only; vehicle speed follows the context settings.")
    else:
        lines.append("  No GPS route will be injected.")
    lines += ["", "WORKLOADS"]
    for action in actions.values():
        driver = action["driver"]
        target = action.get("component") if driver in ("activity", "service") else action.get("url")
        description = f"{driver}: {target}" if target else "configured device shell command"
        lines.append(f"  {action['id']} ({action['role']}), {number(action['start'])}-{number(action['stop'])} s")
        lines.append(f"    Run {description}.")
        if action["interval_s"]:
            lines.append(f"    Repeat the launch/command every {number(action['interval_s'])} s within this window.")
        extras = action.get("extras", {})
        prefix = action.get("package", "") + ".extra."
        shorten = bool(extras) and all(key.startswith(prefix) for key in extras)
        if extras:
            lines.append(f"    Intent inputs (prefix: {prefix}):" if shorten else "    Intent inputs:")
            for key, extra in extras.items():
                label = key[len(prefix):] if shorten else key
                lines.append(f"      {label} = {json.dumps(extra['value'], ensure_ascii=False)} ({extra['type']})")
        evidence = action["evidence"]
        requirements = []
        if "start" in evidence: requirements.append("matching start log")
        if "progress" in evidence: requirements.append(f"at least {evidence['min_progress']} matching progress logs")
        if "min_counter" in evidence: requirements.append(f"observed counter reaches {evidence['min_counter']}")
        if "complete" in evidence: requirements.append("matching completion log")
        if "corroborate" in evidence: requirements.append(f"at least {evidence.get('corroborate_min', 1)} corroborating logs")
        if "process" in evidence: requirements.append(f"CPU scheduling for {evidence['process']}")
        lines.append("    Evidence to look for: " + ("; ".join(requirements) if requirements else "none declared; action remains unverified") + ".")
    if not actions:
        lines.append("  No workload actions declared.")

    def describe(event):
        kind, source = event["kind"], event["source"]
        if kind == "scenario_start": return "Begin scenario"
        if kind == "scenario_end": return "End scenario"
        if kind == "observation_start": return "Open observation window"
        if kind == "observation_stop": return "Close observation window"
        if kind == "phase_start": return f"Begin phase '{source}'"
        if kind == "phase_end": return f"End phase '{source}'"
        if kind == "vehicle_state":
            params = event["params"]
            if context["inject"]:
                text = f"Set {params['state']} at {number(params['speed_mps'])} m/s"
            else:
                text = f"Expect {params['state']} without injecting vehicle state"
            return text + ("; verify driving state and UX restrictions" if context["verify"] else "; readback disabled")
        if kind == "workload_start": return f"Start action '{source}'"
        if kind == "workload_stop":
            action = actions[source]
            if action.get("stop_command"): return f"Stop action '{source}' using its custom stop command"
            if action.get("package"): return f"Stop action '{source}'; force-stop {action['package']}"
            if action["driver"] == "shell": return f"Stop action '{source}'; clean up its shell process group"
            return f"End action '{source}' dispatch window; no app stop command configured"
        if kind == "route_start": return "Start GPS route injection"
        if kind == "route_stop": return "Finish GPS route injection"
        if kind == "marker": return f"Record marker '{source}'"
        raise ValueError(f"unknown plan event: {kind}")

    lines += ["", "TIMELINE (seconds from scenario start; same-time events keep execution order)"]
    for offset, events in groupby(plan["events"], key=lambda e: e["offset_s"]):
        for index, event in enumerate(events):
            label = f"{number(offset)} s" if index == 0 else ""
            lines.append(f"  {label:>8} | {describe(event)}")
    required = [label for key, label in (("baseline", "initial conditions"), ("context", "vehicle context"),
                ("actions", "action evidence"), ("trace", "trace"), ("route", "route"),
                ("overlap", "target/background overlap")) if spec["policy"]["require_"+key]]
    lines += ["", "ACCEPTANCE", "  Execution must complete without a reboot.",
              "  Required checks: " + (", ".join(required) if required else "none") + ".",
              "  Evidence is checked after execution; this preview does not establish success."]
    return "\n".join(lines)
