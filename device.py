"""Bounded ADB operations and the AAOS 12L vehicle/environment adapter."""
from __future__ import annotations

import re
import json
from pathlib import Path
import shlex
import subprocess
import threading
import time


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, serial, adb="adb", timeout=10, profile=None, record=None):
        self.prefix = [adb, "-s", serial]
        self.timeout = timeout
        self.profile = profile if profile is not None else json.loads((Path(__file__).parent/"configs/baseline.json").read_text())
        self.vehicle = self.profile["vehicle"]
        self.record = record or (lambda *a, **k: None)
        self.deadline = None
        self.cancel = threading.Event()

    def call(self, *args, check=True, timeout=None):
        before = time.monotonic()
        limit = timeout or self.timeout
        if self.cancel.is_set():
            raise TimeoutError("attempt cancelled")
        if self.deadline is not None:
            limit = min(limit, self.deadline-before)
        if limit <= 0:
            raise TimeoutError("attempt watchdog deadline exceeded")
        try:
            result = subprocess.run(self.prefix+list(args), text=True, capture_output=True, timeout=limit)
            reading = dict(args=list(args), returncode=result.returncode, stdout=result.stdout,
                           stderr=result.stderr, host_start=before, host_end=time.monotonic())
        except subprocess.TimeoutExpired as exc:
            self.record("command_timeout", args=list(args), host_start=before, host_end=time.monotonic())
            raise TimeoutError(f"ADB command exceeded {limit:.2f}s: {args[0]}") from exc
        self.record("command", **reading)
        if check and result.returncode:
            raise DeviceError(f"ADB {args[0]}: {result.stderr or result.stdout}")
        return reading

    def shell(self, command, **kwargs):
        return self.call("shell", command if isinstance(command, str) else shlex.join(map(str, command)), **kwargs)

    def root(self, command, **kwargs):
        prefix = self.profile["root_shell"]
        return self.shell(prefix + [command], **kwargs)

    def text(self, command):
        return self.shell(command)["stdout"].strip()

    def restore(self, snapshot):
        result = self.call("emu", "avd", "snapshot", "load", snapshot, timeout=60)
        if "KO:" in result["stdout"] or "Error" in result["stdout"]:
            raise DeviceError(result["stdout"])
        self.record("restoration", snapshot=snapshot, state="matched")

    def ready(self, timeout):
        end = time.monotonic()+timeout
        last = "not queried"
        while time.monotonic() < end:
            try:
                if self.call("get-state", timeout=min(3, max(.1, end-time.monotonic())))["stdout"].strip() == "device":
                    boot = self.text(["getprop", "sys.boot_completed"])
                    services = self.profile["services"]
                    present = [self.text(["service", "check", s]) for s in services]
                    if boot == "1" and all(": found" in s for s in present):
                        self.record("readiness", state="matched", services=present)
                        return
                last = "boot or required services unavailable"
            except (DeviceError, TimeoutError) as exc:
                last = str(exc)
            time.sleep(min(.25, max(0, end-time.monotonic())))
        self.record("readiness", state="unverified", error=last)
        raise DeviceError(f"readiness timeout: {last}")

    def environment(self):
        readings = {}
        for key, command in {
            "fingerprint": ["getprop", "ro.build.fingerprint"],
            "sdk": ["getprop", "ro.build.version.sdk"],
            "kernel": ["uname", "-a"],
            "boot_id": ["cat", "/proc/sys/kernel/random/boot_id"],
        }.items():
            try:
                readings[key] = self.text(command)
            except (DeviceError, TimeoutError) as exc:
                readings[key] = None
                readings[key+"_error"] = str(exc)
        return readings

    def clock_sample(self):
        result = self.shell(["cat", "/proc/uptime"])
        uptime = float(result["stdout"].split()[0])
        sample = dict(host_mid=(result["host_start"]+result["host_end"])/2,
                      device_boottime_s=uptime,
                      uncertainty_s=(result["host_end"]-result["host_start"])/2+.01,
                      source="/proc/uptime; host monotonic midpoint; 10ms quantization")
        self.record("clock_sample", **sample)
        return sample

    def context_reading(self):
        readings = {"driving": None, "requires_do": None, "restrictions": None}
        for service, key in ((self.vehicle["driving_service"], "driving"), (self.vehicle["ux_service"], "ux")):
            try:
                result = self.shell(["dumpsys", "car_service", "--services", service])
                readings[key+"_raw"] = result["stdout"]
                if key == "driving":
                    match = re.search(r"Current Driving State:\s*(-?\d+)", result["stdout"])
                    readings["driving"] = int(match[1]) if match else None
                else:
                    for match in re.finditer(r"Port:\s+(0x[0-9a-fA-F]+)\s+UXR:\s+DO:\s+(true|false)\s+UxR:\s+(\d+)", result["stdout"]):
                        if int(match[1], 16) == self.vehicle["ux_display_port"]:
                            readings.update(requires_do=match[2] == "true", restrictions=int(match[3]))
                            break
            except (DeviceError, TimeoutError) as exc:
                readings[key+"_error"] = str(exc)
        return readings

    def property_reading(self, prop, kind):
        result = self.shell(["cmd", "car_service", "get-property-value", prop])
        match = re.search(kind+r"Values:\s*\[([^\]]*)\]", result["stdout"])
        status = re.search(r"status:\s*(\d+)", result["stdout"])
        if not match or not match[1].strip() or not status or status[1] != "0":
            return None
        return float(match[1].split(",")[0])

    def set_speed(self, value):
        return self.root(shlex.join(["lshal", "debug", self.profile["vhal_service"], "--set", self.vehicle["speed_property"], "-f", str(value)]))

    def set_context(self, state, verify=True, timeout=5, inject=True, speed_mps=None):
        expected = self.vehicle["states"][state]
        speed_mps = expected["default_speed_mps"] if speed_mps is None else speed_mps
        if inject:
            service = self.profile["vhal_service"]
            # VHAL and car_service are both driven, but only independent reads establish state.
            if state == "PARK":
                self.set_speed(speed_mps)
            for prop in self.vehicle["gear_properties"]:
                self.root(shlex.join(["lshal", "debug", service, "--set", prop, "-i", str(expected["gear"])]))
            if state == "DRIVE":
                self.set_speed(speed_mps)
            self.shell(["cmd", "car_service", "emulate-driving-state", state.lower()])
        if not verify:
            result = dict(state="unverified", requested=state, requested_speed_mps=speed_mps, reason="dispatch-only: independent readback disabled", injected=inject)
            self.record("context_check", **result)
            return result
        end, last = time.monotonic()+timeout, None
        while True:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise TimeoutError("scenario watchdog expired during context readback")
            last = self.context_reading()
            known = last["driving"] is not None and last["requires_do"] is not None
            matched = known and last["driving"] == expected["driving"] and last["requires_do"] == expected["requires_do"]
            status = "matched" if matched else "mismatched" if known else "unverified"
            self.record("context_sample", requested=state, state=status, reading=last)
            if matched or time.monotonic() >= end:
                break
            time.sleep(.1)
        result = dict(state=status, requested=state, requested_speed_mps=speed_mps, reading=last, injected=inject)
        self.record("context_check", **result)
        return result

    def audit(self, checks, packages):
        context = self.context_reading()
        values = {}
        for key, prop, kind in (("speed", self.vehicle["speed_property"], "float"), ("gear", self.vehicle["gear_properties"][0], "int32")):
            try:
                values[key] = self.property_reading(prop, kind)
            except (DeviceError, TimeoutError):
                values[key] = None
        absent, resumed = None, None
        if packages:
            states = []
            for package in packages:
                try:
                    result = self.shell("pidof "+shlex.quote(package)+"; printf '\\nCARVE_RC=%s\\n' $? ")
                    match = re.search(r"CARVE_RC=(\d+)", result["stdout"])
                    states.append(int(match[1]) == 1 if match and match[1] in ("0", "1") else None)
                except (DeviceError, TimeoutError):
                    states.append(None)
            absent = all(states) if all(x is not None for x in states) else None
            try:
                activity = self.text(["dumpsys", "activity", "activities"])
                top = [line for line in activity.splitlines() if "ResumedActivity" in line]
                resumed = not any(p in line for p in packages for line in top) if top else None
            except (DeviceError, TimeoutError):
                pass
        parked = self.vehicle["states"]["PARK"]
        tolerance = self.vehicle["speed_tolerance_mps"]
        predicates = {
            "parked": None if context["driving"] is None else context["driving"] == parked["driving"],
            "vhal_parked": None if None in values.values() else abs(values["speed"]) < tolerance and values["gear"] == parked["gear"],
            "speed_zero": None if values["speed"] is None else abs(values["speed"]) < tolerance,
            "gear_park": None if values["gear"] is None else values["gear"] == parked["gear"],
            "ux_unrestricted": None if context["requires_do"] is None else not context["requires_do"] and context["restrictions"] == 0,
            "workload_absent": absent, "activity_absent": resumed,
        }
        results = {name: "unverified" if predicates[name] is None else "matched" if predicates[name] else "mismatched" for name in checks}
        self.record("baseline_check", checkpoint="after provisioning; before collection", checks=results, context=context, vhal=values)
        return results

    def marker(self, remote, token, label):
        # A short executable name is also visible in process-start trace records.
        alias = "cv"+token[:12]
        path = remote+"/"+alias
        command = f"ln -sf /system/bin/sleep {shlex.quote(path)}; {shlex.quote(path)} 0.05 >/dev/null 2>&1 & "
        command += "log -t CARVE "+shlex.quote(label)
        self.shell(command)
        self.root("printf %s "+shlex.quote("I|0|"+label)+" > /sys/kernel/tracing/trace_marker", check=False)
        return alias

    def geo(self, sample, write_speed=False):
        result = self.call("emu", "geo", "fix", str(sample["lon"]), str(sample["lat"]))
        if "KO:" in result["stdout"]:
            raise DeviceError(result["stdout"])
        if write_speed:
            self.set_speed(sample["speed_mps"])


def expand(value, variables):
    """Only substitute documented placeholders; shell braces retain their meaning."""
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace("{"+name+"}", str(replacement))
    return value


def action_command(action, variables):
    if action["driver"] == "shell":
        command = action["command"]
        return [expand(v, variables) for v in command] if isinstance(command, list) else expand(command, variables)
    command = ["am", "startservice" if action["driver"] == "service" else "start"]
    if action["driver"] != "service":
        command += ["-W"]
    if action.get("component"):
        command += ["-n", action["component"]]
    if action.get("url"):
        command += ["-a", "android.intent.action.VIEW", "-d", expand(action["url"], variables)]
    for name, extra in sorted(action.get("extras", {}).items()):
        flag = {"string": "--es", "int": "--ei", "long": "--el", "bool": "--ez", "float": "--ef"}[extra["type"]]
        value = expand(extra["value"], variables)
        command += [flag, name, str(value).lower() if isinstance(value, bool) else str(value)]
    return command
