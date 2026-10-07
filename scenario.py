"""Resolve portable scenario inputs and construct a deterministic event plan."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET

import yaml


DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent / "configs/baseline.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def seconds(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a duration")
    if isinstance(value, str):
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m)?", value)
        if not match:
            raise ValueError(f"invalid duration: {value}")
        value = float(match[1]) * {None: 1, "ms": .001, "s": 1, "m": 60}[match[2]]
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("duration must be finite and nonnegative")
    return value


def fields(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: expected a mapping")
    unknown = set(obj) - set(allowed.split())
    if unknown:
        raise ValueError(f"{where}: unknown fields {sorted(unknown)}")


def partition(items, duration, name):
    cursor = 0
    for item in items:
        item["start"], item["end"] = seconds(item["start"]), seconds(item["end"])
        if abs(item["start"] - cursor) > 1e-9 or item["end"] <= cursor:
            raise ValueError(f"{name}: intervals must form an ordered, gap-free partition")
        cursor = item["end"]
    if abs(cursor - duration) > 1e-9:
        raise ValueError(f"{name}: intervals must cover duration_s")


def speed(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("speed must be a finite nonnegative number in m/s")
    return float(value)


def validate_profile(profile):
    fields(profile, "name services root_shell vhal_service vehicle checks readiness_timeout_s snapshot", "profile")
    for key in ("services", "root_shell"):
        if not isinstance(profile.get(key), list) or not profile[key] or any(not isinstance(v, str) or not v for v in profile[key]):
            raise ValueError(f"profile.{key} must be a nonempty string list")
    if not isinstance(profile.get("vhal_service"), str) or not profile["vhal_service"]:
        raise ValueError("profile.vhal_service is required")
    vehicle = profile["vehicle"]
    fields(vehicle, "speed_property gear_properties driving_service ux_service ux_display_port speed_tolerance_mps states", "profile.vehicle")
    for key in ("driving_service", "ux_service"):
        if not isinstance(vehicle.get(key), str) or not vehicle[key]:
            raise ValueError(f"profile.vehicle.{key} is required")
    if not isinstance(vehicle.get("gear_properties"), list) or not vehicle["gear_properties"]:
        raise ValueError("vehicle.gear_properties must be a nonempty list")
    for prop in [vehicle["speed_property"], *vehicle["gear_properties"]]:
        if not isinstance(prop, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", prop):
            raise ValueError("vehicle property IDs must be hexadecimal strings")
    if type(vehicle["ux_display_port"]) is not int or vehicle["ux_display_port"] < 0:
        raise ValueError("ux_display_port must be a nonnegative integer")
    if speed(vehicle["speed_tolerance_mps"]) <= 0:
        raise ValueError("speed_tolerance_mps must be positive")
    fields(vehicle["states"], "PARK DRIVE", "vehicle.states")
    for name in ("PARK", "DRIVE"):
        state = vehicle["states"][name]
        fields(state, "gear driving requires_do default_speed_mps", "vehicle state adapter")
        if any(type(state[k]) is not int for k in ("gear", "driving")) or type(state["requires_do"]) is not bool:
            raise ValueError("vehicle state needs integer gear/driving values and boolean requires_do")
        value = speed(state["default_speed_mps"])
        if (name == "PARK" and value != 0) or (name == "DRIVE" and value <= 0):
            raise ValueError("PARK needs zero speed; DRIVE needs positive speed")


def route_samples(path, spec):
    """Ignore GPX timestamps; integrate a seeded speed curve over route geometry."""
    root = ET.parse(path).getroot()
    points = [(float(e.attrib["lat"]), float(e.attrib["lon"])) for e in root.iter()
              if e.tag.split("}")[-1] in ("trkpt", "rtept")]
    if len(points) < 2 or any(not (-90 <= a <= 90 and -180 <= b <= 180) for a, b in points):
        raise ValueError("GPX requires at least two valid ordered coordinates")
    distances = [0.0]
    for (a, b), (c, d) in zip(points, points[1:]):
        x, y = math.radians(c-a), math.radians(d-b)
        h = math.sin(x/2)**2 + math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(y/2)**2
        distances.append(distances[-1] + 6371000 * 2 * math.asin(min(1, math.sqrt(h))))
    duration, tick = spec["duration_s"], spec["tick_s"]
    if duration <= 0 or tick <= 0 or duration/tick > 100000 or distances[-1] <= 0:
        raise ValueError("route requires positive distance/duration/tick and <=100000 samples")
    times = [i*tick for i in range(math.ceil(duration/tick))] + [duration]
    rng = random.Random(spec["seed"])
    p, q = rng.uniform(0, math.tau), rng.uniform(0, math.tau)
    stop_centers = sorted(rng.uniform(.15*duration, .85*duration) for _ in range(spec["stop_count"]))
    weights = []
    for t in times:
        w = 1.0
        if spec["speed_profile"] != "constant":
            w = max(.05, 1 + .28*math.sin(math.tau*1.35*t/duration+p)
                    + .14*math.sin(math.tau*3.1*t/duration+q))
            ramp = min(8, max(2, duration*.12))
            w *= min(1, t/ramp, (duration-t)/ramp)
        if spec["speed_profile"] == "stop_go":
            for center in stop_centers:
                w *= min(1, max(0, (abs(t-center)-spec["stop_duration_s"]/2)/max(tick, 1)))
        weights.append(w)
    integral = [0.0]
    for i in range(1, len(times)):
        integral.append(integral[-1] + (weights[i-1]+weights[i])*.5*(times[i]-times[i-1]))
    if integral[-1] <= 0:
        raise ValueError("speed profile leaves no route motion")
    scale, segment, samples = distances[-1]/integral[-1], 0, []
    for t, w, area in zip(times, weights, integral):
        reached = area*scale
        while segment < len(points)-2 and distances[segment+1] <= reached:
            segment += 1
        length = distances[segment+1]-distances[segment]
        fraction = (reached-distances[segment])/length if length else 0
        lat, lon = (points[segment][j] + fraction*(points[segment+1][j]-points[segment][j]) for j in (0, 1))
        samples.append({"offset_s": t, "lat": lat, "lon": lon, "speed_mps": w*scale})
    return samples


PRIORITY = {name: i for i, name in enumerate((
    "scenario_start", "phase_end", "workload_stop", "route_stop", "observation_stop",
    "vehicle_state", "phase_start", "observation_start", "route_start", "workload_start",
    "marker", "scenario_end"))}


def build_plan(spec):
    events = []
    def add(kind, offset, source, **params):
        events.append(dict(kind=kind, offset_s=offset, source=source, params=params))
    add("scenario_start", 0, "scenario")
    add("scenario_end", spec["duration_s"], "scenario")
    for phase in spec["observation"]["phases"]:
        for boundary in ("start", "end"):
            add("phase_"+boundary, phase[boundary], phase["id"])
    for boundary in ("start", "stop"):
        add("observation_"+boundary, spec["observation"]["start" if boundary == "start" else "end"], "observation")
    for interval in spec["context"]["vehicle_states"]:
        add("vehicle_state", interval["start"], "context", state=interval["state"], speed_mps=interval["speed_mps"])
    for action in spec["actions"]:
        for boundary in ("start", "stop"):
            add("workload_"+boundary, action[boundary], action["id"])
    route = spec["context"].get("route")
    if route:
        add("route_start", route["start"], "route")
        add("route_stop", route["start"]+route["duration_s"], "route")
    for marker in spec["markers"]:
        add("marker", marker["at"], marker["id"])
    events.sort(key=lambda e: (e["offset_s"], PRIORITY[e["kind"]], e["source"], canonical(e["params"])))
    for index, event in enumerate(events):
        event["id"] = f"e{index:04d}"
    return events


def resolve(path):
    path = Path(path).resolve()
    spec = yaml.safe_load(path.read_text())
    fields(spec, "version name duration_s baseline context actions observation policy markers", "scenario")
    if spec.get("version") != 1:
        raise ValueError("scenario version must be 1")
    spec["duration_s"] = seconds(spec["duration_s"])
    duration = spec["duration_s"]
    if duration <= 0:
        raise ValueError("duration_s must be positive")
    bindings = {"scenario": str(path), "files": {}}
    def artifact(value):
        source = (path.parent / value).resolve()
        # Check readability without loading large APKs into memory.
        with source.open("rb"):
            pass
        bindings["files"][value] = str(source)
        return value
    baseline = spec.setdefault("baseline", {})
    fields(baseline, "snapshot restore checks packages readiness_timeout_s", "baseline")
    # Load the bundled environment independently of the YAML location, and retain
    # it as a bound input so every run saves the configuration used.
    profile_ref = artifact(os.path.relpath(DEFAULT_BASELINE_PATH, path.parent))
    profile = json.loads(Path(bindings["files"][profile_ref]).read_text())
    validate_profile(profile)
    baseline["environment"] = profile
    baseline.setdefault("snapshot", profile.get("snapshot"))
    baseline.setdefault("restore", True)
    if baseline["restore"] and not baseline["snapshot"]:
        raise ValueError("restore requires baseline.snapshot")
    baseline.setdefault("checks", profile.get("checks", []))
    if set(baseline["checks"]) - set("parked vhal_parked speed_zero gear_park ux_unrestricted workload_absent activity_absent".split()):
        raise ValueError("unknown baseline check")
    baseline.setdefault("packages", [])
    if set(baseline["checks"]) & {"workload_absent", "activity_absent"} and not baseline["packages"]:
        raise ValueError("absence checks require baseline.packages")
    baseline["readiness_timeout_s"] = seconds(baseline.get("readiness_timeout_s", profile.get("readiness_timeout_s", 60)))
    context = spec.setdefault("context", {})
    fields(context, "vehicle_states verify inject timeout_s route", "context")
    context.setdefault("verify", True)
    context.setdefault("inject", True)
    context["timeout_s"] = seconds(context.get("timeout_s", 5))
    context.setdefault("vehicle_states", [{"state": "PARK", "start": 0, "end": duration}])
    for interval in context["vehicle_states"]:
        fields(interval, "state start end speed_mps", "vehicle state")
        if interval["state"] not in ("PARK", "DRIVE"):
            raise ValueError("vehicle state must be PARK or DRIVE")
        interval["speed_mps"] = speed(interval.get("speed_mps", profile["vehicle"]["states"][interval["state"]]["default_speed_mps"]))
        if (interval["state"] == "PARK" and interval["speed_mps"] != 0) or (interval["state"] == "DRIVE" and interval["speed_mps"] <= 0):
            raise ValueError("PARK needs zero speed; DRIVE needs positive speed")
    partition(context["vehicle_states"], duration, "context")
    route = context.setdefault("route", None)
    if route is not None:
        fields(route, "path start duration_s tick_s speed_profile seed stop_count stop_duration_s write_speed", "route")
        if not isinstance(route.get("path"), str) or not route["path"]:
            raise ValueError("context.route requires a GPX path; use null for no route")
        for key, default in (("start", 0), ("duration_s", duration), ("tick_s", 1), ("stop_duration_s", 0)):
            route[key] = seconds(route.get(key, default))
        route.setdefault("speed_profile", "constant")
        route.setdefault("seed", 0)
        route.setdefault("stop_count", 0)
        route.setdefault("write_speed", False)
        if not isinstance(route["write_speed"], bool):
            raise ValueError("route.write_speed must be YAML true/false")
        if route["speed_profile"] not in ("constant", "urban", "stop_go") or not isinstance(route["stop_count"], int) or route["stop_count"] < 0:
            raise ValueError("invalid route speed profile or stop count")
        if route["start"]+route["duration_s"] > duration:
            raise ValueError("route extends beyond scenario")
        route["path"] = artifact(route["path"])
        route["samples"] = route_samples(bindings["files"][route["path"]], route)
    observation = spec["observation"]
    fields(observation, "trace start end pre_roll_s post_roll_s phases require_syscalls", "observation")
    observation.setdefault("require_syscalls", True)
    if not isinstance(observation["require_syscalls"], bool):
        raise ValueError("observation.require_syscalls must be YAML true/false")
    observation["trace"] = artifact(observation["trace"])
    for key, default in (("start", 0), ("end", duration), ("pre_roll_s", 1), ("post_roll_s", 1)):
        observation[key] = seconds(observation.get(key, default))
    if not 0 <= observation["start"] < observation["end"] <= duration:
        raise ValueError("observation interval outside scenario")
    for phase in observation["phases"]:
        fields(phase, "id start end", "phase")
    partition(observation["phases"], duration, "phases")
    phases = {p["id"]: p for p in observation["phases"]}
    if len(phases) != len(observation["phases"]):
        raise ValueError("duplicate phase id")
    if "scenario" in phases:
        raise ValueError("'scenario' is reserved for actions spanning multiple phases")
    phases["scenario"] = {"start": 0, "end": duration}
    ids = set()
    for action in spec.setdefault("actions", []):
        fields(action, "id role driver phase start stop command stop_command interval_s package component url extras apk provision evidence", "action")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", action["id"]) or action["id"] in ids:
            raise ValueError("action ids must be unique simple identifiers")
        ids.add(action["id"])
        if action["id"] == "route":
            raise ValueError("action id 'route' is reserved for the route worker")
        action.setdefault("role", "target")
        if action["role"] not in ("target", "background") or action["driver"] not in ("shell", "activity", "service", "url"):
            raise ValueError("invalid action role or driver")
        action["start"], action["stop"] = seconds(action["start"]), seconds(action["stop"])
        phase = phases[action["phase"]]
        if not phase["start"] <= action["start"] < action["stop"] <= phase["end"]:
            raise ValueError(f"action {action['id']} must fit its phase")
        if not observation["start"] <= action["start"] < action["stop"] <= observation["end"]:
            raise ValueError("action must fit observation interval")
        if action["driver"] == "shell" and not action.get("command"):
            raise ValueError("shell action requires command")
        for command in [action[k] for k in ("command", "stop_command") if k in action]+action.get("provision", []):
            if not isinstance(command, str) and not (isinstance(command, list) and command and all(isinstance(v, (str, int, float)) for v in command)):
                raise ValueError("commands must be shell strings or nonempty argument lists")
        if action["driver"] in ("activity", "service") and not action.get("component"):
            raise ValueError("activity/service requires component")
        if action["driver"] == "url" and not action.get("url"):
            raise ValueError("url driver requires url")
        action["duration_s"] = action["stop"]-action["start"]
        action["interval_s"] = seconds(action.get("interval_s", 0))
        if "apk" in action:
            action["apk"] = artifact(action["apk"])
        for extra in action.get("extras", {}).values():
            fields(extra, "type value", "extra")
            if extra["type"] not in ("string", "int", "long", "bool", "float"):
                raise ValueError("unsupported Android extra type")
        evidence = action.setdefault("evidence", {})
        fields(evidence, "start progress complete corroborate min_progress min_counter corroborate_min process overlap_bin_s", "action evidence")
        evidence.setdefault("min_progress", 1)
        if not isinstance(evidence["min_progress"], int) or evidence["min_progress"] < 1:
            raise ValueError("min_progress must be a positive count of observed records")
        for key in ("start", "progress", "complete", "corroborate"):
            if key in evidence:
                re.compile(evidence[key].replace("{run_id}", "run").replace("{action_id}", "action"))
        for key in ("min_counter", "corroborate_min"):
            if key in evidence and (not isinstance(evidence[key], int) or evidence[key] < 1):
                raise ValueError(f"{key} must be a positive integer")
        if "min_counter" in evidence and "count" not in re.compile(evidence.get("progress", "")).groupindex:
            raise ValueError("min_counter requires a named (?P<count>...) progress group")
        if "overlap_bin_s" in evidence:
            evidence["overlap_bin_s"] = seconds(evidence["overlap_bin_s"])
            if evidence["overlap_bin_s"] <= 0:
                raise ValueError("overlap_bin_s must be positive")
    policy = spec.setdefault("policy", {})
    fields(policy, "scheduler command_timeout_s watchdog_s worker_grace_s require_baseline require_context require_actions require_trace require_route require_overlap continue_on_failure", "policy")
    defaults = dict(scheduler="absolute", command_timeout_s=10, watchdog_s=duration+30,
                    worker_grace_s=3, require_baseline=True, require_context=True,
                    require_actions=True, require_trace=True, require_route=route is not None,
                    require_overlap=False, continue_on_failure=False)
    for key, value in defaults.items():
        policy.setdefault(key, value)
    if policy["require_route"] and route is None:
        raise ValueError("policy.require_route requires context.route with a GPX path")
    if policy["scheduler"] not in ("absolute", "relative"):
        raise ValueError("scheduler must be absolute or relative")
    for key in ("command_timeout_s", "watchdog_s", "worker_grace_s"):
        policy[key] = seconds(policy[key])
        if policy[key] <= 0:
            raise ValueError(f"{key} must be positive")
    for obj, keys in ((baseline, ["restore"]), (context, ["verify", "inject"]), (policy, [k for k, v in defaults.items() if isinstance(v, bool)])):
        if any(not isinstance(obj[k], bool) for k in keys):
            raise ValueError("boolean options must be YAML true/false")
    for marker in spec.setdefault("markers", []):
        fields(marker, "id at", "marker")
        marker["at"] = seconds(marker["at"])
        if marker["at"] > duration:
            raise ValueError("marker outside scenario")
    spec["actions"].sort(key=lambda action: action["id"])
    spec["markers"].sort(key=lambda marker: (marker["at"], marker["id"]))
    baseline["checks"] = sorted(set(baseline["checks"]))
    baseline["packages"] = sorted(set(baseline["packages"]))
    events = build_plan(spec)
    return {"scenario": spec, "events": events, "bindings": bindings}
