"""Attempt lifecycle, common-clock scheduling, and bounded asynchronous workers."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import shlex
import threading
import time
import uuid

from device import Device, DeviceError, action_command, expand
from evidence import Collection, Journal, assess, save


def schedule(events, dispatch, t0, *, clock=time.monotonic, sleep=time.sleep,
             mode="absolute", watchdog_s=float("inf"), record=lambda *a, **k: None):
    previous = 0
    for event in events:
        deadline = t0+event["offset_s"]
        delay = max(0, deadline-clock()) if mode == "absolute" else event["offset_s"]-previous
        remaining = t0+watchdog_s-clock()
        if remaining <= 0:
            raise TimeoutError("scenario watchdog expired")
        sleep(min(delay, remaining))
        if delay >= remaining or clock() >= t0+watchdog_s:
            raise TimeoutError("scenario watchdog expired")
        started, error = clock(), None
        try:
            dispatch(event)
            if clock() >= t0+watchdog_s:
                raise TimeoutError("scenario watchdog expired during handler")
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            record("dispatch", event_id=event["id"], operation=event["kind"], source=event["source"],
                   planned_deadline=deadline, dispatched=started, completed=clock(),
                   lateness_s=max(0, started-deadline), error=error)
        previous = event["offset_s"]


class Workers:
    def __init__(self, device, spec, run_id, t0, record):
        self.device, self.spec, self.run_id, self.t0, self.record = device, spec, run_id, t0, record
        self.active = {}
        self.errors = []
        self.lock = threading.Lock()

    def launch(self, key, fn):
        stop = threading.Event()
        def guarded():
            try:
                fn(stop)
            except Exception as exc:
                with self.lock:
                    self.errors.append({"worker": key, "error": str(exc), "timeout": isinstance(exc, TimeoutError)})
                self.record("worker_error", worker=key, error=str(exc))
            finally:
                self.record("worker_end", worker=key)
        thread = threading.Thread(target=guarded, name="carve-"+key, daemon=True)
        self.active[key] = (thread, stop)
        thread.start()

    def workload(self, action):
        def work(stop):
            variables = dict(run_id=self.run_id, action_id=action["id"], duration_s=action["duration_s"], duration_ms=round(action["duration_s"]*1000))
            iteration = 0
            while not stop.is_set() and time.monotonic() < self.t0+action["stop"]:
                variables["iteration"] = iteration
                command = action_command(action, variables)
                if action["driver"] == "shell":
                    pidfile = f"/data/local/tmp/carve/{self.run_id}/{action['id']}.pid"
                    body = shlex.join(map(str, command)) if isinstance(command, list) else command
                    command = ["setsid", "sh", "-c", "echo $$ > "+shlex.quote(pidfile)+"; "+body]
                result = self.device.shell(command, check=False)
                if result["returncode"] and not stop.is_set():
                    raise DeviceError(result["stderr"] or result["stdout"])
                if action["driver"] != "shell" and ("Error:" in result["stdout"] or "Exception" in result["stdout"]):
                    raise DeviceError(result["stdout"])
                self.record("driver_result", action_id=action["id"], iteration=iteration, result=result,
                            interpretation="request completed; payload evidence checked separately")
                if not action["interval_s"]:
                    return
                iteration += 1
                deadline = self.t0+action["start"]+iteration*action["interval_s"]
                stop.wait(max(0, deadline-time.monotonic()))
        self.launch(action["id"], work)

    def route(self, route):
        def work(stop):
            for index, sample in enumerate(route["samples"]):
                deadline = self.t0+route["start"]+sample["offset_s"]
                if stop.wait(max(0, deadline-time.monotonic())):
                    return
                began = time.monotonic()
                try:
                    self.device.geo(sample, route["write_speed"])
                except Exception as exc:
                    self.record("route_sample", index=index, sample=sample, planned_deadline=deadline, dispatched=began, ok=False, error=str(exc))
                    raise
                self.record("route_sample", index=index, sample=sample, planned_deadline=deadline, dispatched=began, ok=True)
        self.launch("route", work)

    def stop(self, key, finish=False):
        if key in self.active:
            thread, event = self.active[key]
            if finish:
                thread.join(timeout=self.spec["policy"]["worker_grace_s"])
            event.set()

    def finish(self):
        for _, stop in self.active.values():
            stop.set()
        end = time.monotonic()+self.spec["policy"]["command_timeout_s"]+self.spec["policy"]["worker_grace_s"]
        for thread, _ in self.active.values():
            thread.join(timeout=max(0, end-time.monotonic()))
        alive = [key for key, (thread, _) in self.active.items() if thread.is_alive()]
        self.record("workers_finished", errors=self.errors, alive=alive)
        return alive


def attempt(plan, output, serial, adb="adb", processor=None, index=1, device_factory=Device):
    run_id = "r"+uuid.uuid4().hex[:16]
    directory = Path(output).resolve()/f"{index:03d}-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    record = Journal(directory/"events.jsonl")
    spec, bindings = plan["scenario"], plan["bindings"]
    save(directory/"plan.json", {k: v for k, v in plan.items() if k != "bindings"})
    saved_inputs = {reference: f"inputs/{number:03d}-{Path(source).name}"
                    for number, (reference, source) in enumerate(bindings["files"].items(), 1)
                    if Path(source).suffix.lower() != ".apk"}
    save(directory/"runtime.json", dict(bindings=bindings, inputs=saved_inputs, serial=serial,
                                       adb=adb, trace_processor=processor, attempt=index))
    inputs = directory/"inputs"
    inputs.mkdir()
    policy = spec["policy"]
    device = device_factory(serial, adb, policy["command_timeout_s"], spec["baseline"]["environment"], record)
    collection = Collection(device, directory, run_id, record)
    outcome = dict(run_id=run_id, attempt=index, execution="preparation_failed", stage="created", boot_unchanged=None)
    workers, initial_environment = None, {}
    stopped = set()
    def stop_action(action):
        if action["id"] in stopped:
            return
        if workers:
            workers.stop(action["id"])
        if action.get("stop_command"):
            command = action["stop_command"]
            variables = dict(run_id=run_id, action_id=action["id"])
            device.shell([expand(v, variables) for v in command] if isinstance(command, list) else expand(command, variables))
        elif action.get("package"):
            device.shell(["am", "force-stop", action["package"]])
        elif action["driver"] == "shell":
            pidfile = f"/data/local/tmp/carve/{run_id}/{action['id']}.pid"
            # Only signal our own still-identifiable process group; never blanket-kill shell/apps.
            command = "p=$(cat "+shlex.quote(pidfile)+" 2>/dev/null); "
            command += "case \"$p\" in ''|*[!0-9]*) exit 0;; esac; "
            command += "if tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -Fq "+shlex.quote(pidfile)+"; then kill -TERM -- -$p; fi"
            device.shell(command, check=False)
        stopped.add(action["id"])
    try:
        outcome["stage"] = "inputs"
        shutil.copyfile(bindings["scenario"], directory/"scenario.yaml")
        for reference, destination in saved_inputs.items():
            shutil.copyfile(bindings["files"][reference], directory/destination)
        baseline = spec["baseline"]
        outcome["stage"] = "restore"
        if baseline["restore"]:
            device.restore(baseline["snapshot"])
        else:
            record("restoration", state="skipped", reason="scenario disables restoration")
        outcome["stage"] = "readiness"
        device.ready(baseline["readiness_timeout_s"])
        initial_environment = device.environment()
        save(directory/"environment.json", initial_environment)
        outcome["stage"] = "provision"
        for action in spec["actions"]:
            if "apk" in action:
                device.call("install", "-r", bindings["files"][action["apk"]], timeout=60)
            for command in action.get("provision", []):
                device.shell(command)
            record("provision", action_id=action["id"], state="matched")
        outcome["stage"] = "baseline"
        checks = device.audit(baseline["checks"], baseline["packages"])
        if policy["require_baseline"] and (not checks or any(s != "matched" for s in checks.values())):
            raise DeviceError("required baseline predicate mismatched or unverified")
        outcome["stage"] = "collection"
        observation = spec["observation"]
        collection.start(bindings["files"][observation["trace"]], policy["watchdog_s"]+observation["pre_roll_s"]+observation["post_roll_s"]+15)
        time.sleep(observation["pre_roll_s"])
        device.clock_sample()
        t0 = time.monotonic()
        outcome.update(stage="execution", execution="failed", t0_host_monotonic=t0)
        device.deadline = t0+policy["watchdog_s"]
        workers = Workers(device, spec, run_id, t0, record)
        actions = {a["id"]: a for a in spec["actions"]}
        def dispatch(event):
            kind = event["kind"]
            if workers.errors:
                if any(e["timeout"] for e in workers.errors):
                    raise TimeoutError("workload or route command timed out")
                raise DeviceError("workload or route worker failed")
            if kind == "vehicle_state":
                context = spec["context"]
                result = device.set_context(event["params"]["state"], context["verify"], context["timeout_s"], context["inject"], event["params"]["speed_mps"])
                if policy["require_context"] and result["state"] != "matched":
                    raise DeviceError("required context mismatched or unverified")
            elif kind == "workload_start":
                workers.workload(actions[event["source"]])
            elif kind == "workload_stop":
                stop_action(actions[event["source"]])
            elif kind == "route_start":
                workers.route(spec["context"]["route"])
            elif kind == "route_stop":
                workers.stop("route", finish=True)
            else:
                label = f"{run_id} {event['id']} {kind} {event['source']}"
                token = uuid.uuid4().hex
                alias = device.marker(collection.remote, token, label)
                record("marker", event_id=event["id"], operation=kind, source=event["source"], alias=alias, label=label)
        schedule(plan["events"], dispatch, t0, mode=policy["scheduler"], watchdog_s=policy["watchdog_s"], record=record)
        outcome["execution"] = "completed"
    except KeyboardInterrupt:
        outcome.update(execution="interrupted", error="user interruption")
    except TimeoutError as exc:
        outcome.update(execution="timeout", error=str(exc))
    except Exception as exc:
        status = "preparation_failed" if outcome["stage"] != "execution" else "failed"
        if device.deadline is not None and time.monotonic() >= device.deadline:
            status = "timeout"
        outcome.update(execution=status, error=str(exc))
    finally:
        # Existing ADB requests remain bounded while workers stop; collection cleanup gets fresh bounds.
        device.deadline = None
        for action in spec["actions"]:
            if workers and action["id"] in workers.active:
                try:
                    stop_action(action)
                except Exception as exc:
                    record("cleanup_error", action_id=action["id"], error=str(exc))
                    if outcome["execution"] == "completed":
                        outcome["execution"] = "failed"
        if workers:
            alive = workers.finish()
            if alive or workers.errors:
                outcome["execution"] = "timeout" if alive or any(e["timeout"] for e in workers.errors) else "failed"
                outcome["worker_errors"] = workers.errors
                outcome["workers_alive"] = alive
        if collection.trace_started:
            time.sleep(spec["observation"]["post_roll_s"])
        outcome["collection"] = collection.stop()
        try:
            final_environment = device.environment()
            outcome["final_environment"] = final_environment
            before, after = initial_environment.get("boot_id"), final_environment.get("boot_id")
            outcome["boot_unchanged"] = before == after if before and after else None
            if outcome["boot_unchanged"] is False:
                outcome["execution"] = "rebooted"
            if workers:
                device.clock_sample()
        except Exception as exc:
            outcome["recovery_error"] = str(exc)
        outcome["finished_host_monotonic"] = time.monotonic()
        save(directory/"outcome.json", outcome)
    result = assess(directory, processor)
    return directory, result


def run_attempts(plan, output, serial, adb="adb", processor=None, repeat=1):
    if repeat < 1:
        raise ValueError("repeat must be positive")
    results = []
    for index in range(1, repeat+1):
        directory, result = attempt(plan, output, serial, adb, processor, index)
        results.append({"directory": str(directory), "attempt": index, "execution": result["execution"], "analysis_eligible": result["analysis_eligible"]})
        print(json.dumps(results[-1]), flush=True)
        if not result["analysis_eligible"] and not plan["scenario"]["policy"]["continue_on_failure"]:
            break
    save(Path(output)/("attempts-"+uuid.uuid4().hex[:12]+".json"), {"designated": repeat, "attempted": len(results), "replacement_attempts": 0, "results": results})
    return results
