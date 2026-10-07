# CARVE

CARVE runs YAML-defined experiments on Android Automotive OS and checks the resulting evidence. **Start with the two supplied examples below**, then use the field reference to author your own scenario.

**Scenario format:** `version: 1`

## Contents

- [1. Run the Examples](#1-run-the-examples)
  - [1.1 Install Dependencies](#11-install-dependencies)
  - [1.2 Validate Both Scenarios](#12-validate-both-scenarios)
  - [1.3 Select the Emulator and Trace Processor](#13-select-the-emulator-and-trace-processor)
  - [1.4 Run Example 1 — Shell I/O](#14-run-example-1--shell-io)
  - [1.5 Run Example 2 — JPEG APK](#15-run-example-2--jpeg-apk)
  - [1.6 Locate and Interpret the Results](#16-locate-and-interpret-the-results)
- [2. YAML Conventions](#2-yaml-conventions)
- [3. Create Your Own Scenario](#3-create-your-own-scenario)
- [4. Reference Scenario](#4-reference-scenario)
- [5. Field Reference](#5-field-reference)
  - [5.1 Top-Level Fields](#51-top-level-fields)
  - [5.2 Baseline](#52-baseline)
  - [5.3 Vehicle Context](#53-vehicle-context)
  - [5.4 Actions](#54-actions)
  - [5.5 Action Evidence](#55-action-evidence)
  - [5.6 Observation](#56-observation)
  - [5.7 Policy](#57-policy)
  - [5.8 Markers](#58-markers)
  - [5.9 Routes](#59-routes)
- [6. Modification Procedures](#6-modification-procedures)
- [7. Results and Acceptance](#7-results-and-acceptance)

## 1. Run the Examples

**Run the examples unchanged before writing your own YAML.** Complete Example 1 first, review its result, then continue to Example 2.

| Order | Scenario | What it demonstrates | Planned duration |
| --- | --- | --- | --- |
| **1** | [example.yaml](scenarios/example.yaml) | Shell I/O, scheduling, log evidence, and trace collection; **no APK required** | **12 s**, with the action scheduled at **3–9 s** |
| **2** | [jpeg.yaml](scenarios/jpeg.yaml) | Install and launch the supplied JPEG APK, request **4 decodes**, and verify application evidence | **30 s**, with the action scheduled at **7–23 s** |

Both examples keep the vehicle in **PARK** and inject **no GPS route**. Preparation and collection add time beyond the planned duration.

### 1.1 Install Dependencies

Open a terminal in **CARVE-core**, the directory containing `carve.py`, and run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Use an existing suitable `.venv` if already prepared. Python **3.10 or later** is required. Validation and planning need only these Python dependencies and the supplied input files.

### 1.2 Validate Both Scenarios

```sh
.venv/bin/python carve.py validate scenarios/example.yaml
.venv/bin/python carve.py plan scenarios/example.yaml \
  --output /tmp/carve-example-plan.json

.venv/bin/python carve.py validate scenarios/jpeg.yaml
.venv/bin/python carve.py plan scenarios/jpeg.yaml \
  --output /tmp/carve-jpeg-plan.json
```

**Expected result:** each validation prints **`Validation passed.`** and exits with code **0**. Each `plan` command prints a readable preview: preparation, workload inputs, a timeline, and the checks required for acceptance. `--output` additionally saves the full JSON plan.

> [!NOTE]
> **No emulator is needed for this step.** Validation and planning read the inputs without installing or launching the APK. A device run is needed to verify actual execution and collection.

To inspect a scenario without exporting JSON:

```sh
.venv/bin/python carve.py plan scenarios/jpeg.yaml
```

Read the **TIMELINE** to see when phases and actions start and stop. Times are relative to scenario start; preparation and extra capture time are listed separately. The preview describes planned work, not observed results. To save this text, redirect standard output to a text file.

### 1.3 Select the Emulator and Trace Processor

Device runs require **Android SDK adb**, a **rooted AAOS 12L emulator** compatible with [baseline.json](configs/baseline.json), and **Perfetto trace_processor_shell** for trace verification.

Start the emulator in **PARK**, at **zero speed**, with an unrestricted driver display. List connected devices:

```sh
adb devices
```

Set the following two values in the same terminal used for the remaining commands. **Replace the example serial and executable path with your own.**

```sh
CARVE_SERIAL=emulator-5554
CARVE_TRACE_PROCESSOR=/absolute/path/to/trace_processor_shell
```

Both YAMLs explicitly use **`restore: false`**: they audit the prepared environment without restoring a snapshot. For restored repetitions, see [Section 3.2](#32-repeat-from-a-saved-baseline).

### 1.4 Run Example 1 — Shell I/O

```sh
.venv/bin/python carve.py run scenarios/example.yaml \
  --serial "$CARVE_SERIAL" \
  --output results/example \
  --trace-processor "$CARVE_TRACE_PROCESSOR"
```

**Expected behavior:** at 3 s, the shell action starts four iterations of writing and checking a 4 KiB file, emitting start/progress/completion records. Its window ends at 9 s; the scenario ends at 12 s.

**Review before continuing:** use the attempt directory printed by the command, under `results/example/`, and open `checks.json`. A successful run reports **`analysis_eligible: true`** with **`checks.actions.io.state: matched`**.

### 1.5 Run Example 2 — JPEG APK

After Example 1 passes, confirm the emulator is still in PARK and run:

```sh
.venv/bin/python carve.py run scenarios/jpeg.yaml \
  --serial "$CARVE_SERIAL" \
  --output results/first-jpeg \
  --trace-processor "$CARVE_TRACE_PROCESSOR"
```

**Expected behavior:** CARVE installs `attack_apps/apks/jpegcve.apk` and stops any existing instance before the baseline audit. At 7 s it launches the application, requesting four JPEG decodes with a 100 ms delay after each decode. It stops the package at 23 s; the scenario ends at 30 s. No manual installation or button press is required.

**Review:** open the printed attempt directory under `results/first-jpeg/`. A successful run reports **`analysis_eligible: true`** with **`checks.actions.jpeg_decode.state: matched`**, including the requested progress count and successful completion evidence.

### 1.6 Locate and Interpret the Results

Each run prints its exact **`directory`**, **`execution`**, and **`analysis_eligible`** values. Open these files in that directory:

| File | Check |
| --- | --- |
| **`checks.json`** | Acceptance result and individual evidence checks |
| `logcat.txt` | Workload start, progress, and completion records |
| `trace.pftrace` | Collected Perfetto trace |
| `events.jsonl` | Requested and recorded action times |

If either example is ineligible, inspect its checks before continuing. Without a usable trace processor, trace evidence remains unverified. Detailed result meanings are in [Section 7](#7-results-and-acceptance).

**Next:** after both examples pass, follow [Section 3](#3-create-your-own-scenario) to make a copy and change the experiment settings.

## 2. YAML Conventions

### 2.1 Keywords and Values

| Item | Example | Rule |
| --- | --- | --- |
| **Fixed keyword** | `driver`, `start`, `stop` | Preserve spelling and nesting. |
| **Fixed choice** | `activity`, `PARK` | Select an allowed value; capitalization matters. |
| **Custom name** | `jpeg_decode`, `payload` | Choose an identifier and keep its references consistent. |
| **Experiment setting** | `start: 7` | Choose a value within the documented limits. |
| **Application identifier** | Package, component, extra name | Copy the exact value from the APK's matching template or interface. |

`baseline.restore` and `actions[]` are reference notation for nested fields and list items. Use indentation in the actual YAML.

### 2.2 Formatting Rules

- Use **two spaces per indentation level** and align sibling fields.
- Write `key: value`; use `-` for list items and `#` for comments.
- Write Boolean values as **`true` or `false` without quotes**. Use `null` for no route.
- Put regular expressions in single quotes, for example `'iteration=\d+'`.
- Define each key once within its mapping.

## 3. Create Your Own Scenario

### 3.1 Copy and Modify an Example

Start from the example closest to your experiment. For an APK scenario:

```sh
cp scenarios/jpeg.yaml scenarios/my_experiment.yaml
```

Choose a new destination name if the file already exists. Keep the copy in `scenarios/` so its relative paths remain valid. Change its name, schedule, or workload count using [Section 6](#6-modification-procedures); consult [Section 5](#5-field-reference) for accepted keywords and defaults.

Validate, inspect the plan, then run with the device values set in Section 1.3:

```sh
.venv/bin/python carve.py validate scenarios/my_experiment.yaml
.venv/bin/python carve.py plan scenarios/my_experiment.yaml \
  --output /tmp/carve-my-experiment-plan.json
.venv/bin/python carve.py run scenarios/my_experiment.yaml \
  --serial "$CARVE_SERIAL" \
  --output results/my-experiment \
  --trace-processor "$CARVE_TRACE_PROCESSOR"
```

### 3.2 Repeat from a Saved Baseline

Set **`baseline.restore: true`** and **`baseline.snapshot`** to an existing clean PARK snapshot. Then add `--repeat N` to the run command, where `N` is a positive integer. By default, an ineligible attempt stops subsequent repetitions.

## 4. Reference Scenario

### 4.1 Complete YAML

This listing matches [jpeg.yaml](scenarios/jpeg.yaml), with English comments. Use the supplied file directly for Example 2.

```yaml
version: 1
name: first-jpeg-while-parked
duration_s: 30

# Prepare and audit the initial environment before timed execution.
baseline:
  restore: false
  # To restore a saved baseline, set restore to true and specify:
  # snapshot: YOUR_CLEAN_PARK_SNAPSHOT
  packages: [com.example.jpegcve]
  checks:
    - parked
    - vhal_parked
    - speed_zero
    - gear_park
    - ux_unrestricted
    - workload_absent

# Keep the vehicle parked throughout the experiment.
context:
  route: null
  verify: true
  vehicle_states:
    - {state: PARK, start: 0, end: 30}

# Request one application launch with four internal decode iterations.
actions:
  - id: jpeg_decode
    role: target
    phase: payload
    start: 7
    stop: 23
    driver: activity
    apk: ../attack_apps/apks/jpegcve.apk
    package: com.example.jpegcve
    component: com.example.jpegcve/.MainActivity
    provision:
      - am force-stop com.example.jpegcve
    extras:
      com.example.jpegcve.extra.AUTORUN: {type: bool, value: true}
      com.example.jpegcve.extra.START_DELAY_MS: {type: long, value: 0}
      com.example.jpegcve.extra.FINISH_AFTER_RUN: {type: bool, value: true}
      com.example.jpegcve.extra.LOAD_FIRST: {type: bool, value: true}
      com.example.jpegcve.extra.STRESS_ITERATIONS: {type: int, value: 4}
      com.example.jpegcve.extra.STRESS_DELAY_MS: {type: long, value: 100}
    evidence:
      start: 'CARVE_PAYLOAD_START family=jpegcve\b'
      progress: 'CARVE_PAYLOAD_PROGRESS family=jpegcve iteration=(?P<count>\d+) bytes=[1-9]\d*'
      min_progress: 4
      min_counter: 4
      complete: 'CARVE_PAYLOAD_END family=jpegcve success=true completed=4 requested=4\b'
      process: com.example.jpegcve

# Record the whole scenario and organize the trace into three phases.
observation:
  trace: ../configs/perfetto.pbtxt
  pre_roll_s: 1
  post_roll_s: 1
  phases:
    - {id: pre_payload, start: 0, end: 5}
    - {id: payload, start: 5, end: 25}
    - {id: post_payload, start: 25, end: 30}

# Require evidence for the declared conditions before analysis.
policy:
  require_baseline: true
  require_context: true
  require_actions: true
  require_trace: true
  require_route: false
```

> [!IMPORTANT]
> **Change experiment settings first.** Preserve the APK interface and log expressions when adjusting the schedule. When changing the decode count, update the matching evidence values using [Section 6.2](#62-change-the-decode-count).

## 5. Field Reference

**Required** fields must be supplied. **Conditional** fields are required in the stated circumstances. Optional fields use the listed defaults. Names in each table are relative to the containing section.

### 5.1 Top-Level Fields

| Field | Requirement | Type and accepted value | Default or omission behavior |
| --- | --- | --- | --- |
| `version` | **Required** | Integer 1 | No default |
| `name` | Optional; recommended | Descriptive string | No default |
| `duration_s` | **Required** | Positive time value | No default |
| `baseline` | **Required** | Mapping | See Section 5.2 |
| `context` | Optional; explicit declaration recommended | Mapping | PARK for the whole run, no route, injection and verification enabled |
| `actions` | **Conditional** for workload execution | List of action mappings | Empty list; no APK is selected automatically |
| `observation` | **Required** | Mapping | See Section 5.6 |
| `policy` | Optional | Mapping | Defaults in Section 5.7 |
| `markers` | Optional | List of marker mappings | Empty list |

### 5.2 Baseline

**CARVE automatically loads [configs/baseline.json](configs/baseline.json) from its installation directory.** No `profile` field is needed in YAML. The supplied configuration supports the rooted AAOS 12L environment used by the examples; changing the APK or schedule does not require editing this JSON. Automatic selection for other AAOS versions is not implemented.

The resolved environment settings are saved in `plan.json`, and the JSON is copied into each attempt's `inputs/` directory.

| Field | Requirement | Type and meaning | Default or example |
| --- | --- | --- | --- |
| `restore` | Optional; specify explicitly | Boolean; restore a saved snapshot before preparation | **true**; the reference scenario explicitly uses false |
| `snapshot` | **Conditional** when restore is true | Existing emulator snapshot name | Inherited from the default JSON if omitted; the supplied JSON has no snapshot name |
| `checks` | Optional | List of allowed predicate names below | The default JSON's checks; an explicit list replaces rather than extends it |
| `packages` | **Conditional** for workload_absent or activity_absent | Nonempty list of actual package names | Empty list; not inferred from an APK |
| `readiness_timeout_s` | Optional | Time allowed for device and service readiness | Default JSON setting, otherwise 60 seconds; supplied value is 60 |

> [!IMPORTANT]
> **`restore` defaults to `true`.** Supply an existing snapshot name, or explicitly set `restore: false` for an already prepared emulator. The supplied JSON has no snapshot name.

Allowed values in `checks`:

| Predicate | Condition assessed |
| --- | --- |
| `parked` | The driving-state service reports PARK. |
| `vhal_parked` | The VHAL readings indicate near-zero speed and the configured PARK gear. |
| `speed_zero` | The speed reading is within the configured zero-speed tolerance. |
| `gear_park` | The gear reading matches the configured PARK value. |
| `ux_unrestricted` | The driver display has no declared driving restrictions. |
| `workload_absent` | All listed workload processes are absent. |
| `activity_absent` | None of the listed packages has a resumed Activity in the available readings. |

The baseline audit occurs **after provisioning and before collection**. Unavailable readings are `unverified`.

### 5.3 Vehicle Context

| Field | Requirement | Type and accepted value | Default or constraint |
| --- | --- | --- | --- |
| `vehicle_states` | Optional | Ordered list of state intervals | One PARK interval covering the run |
| `vehicle_states[].state` | **Required** per interval | **`PARK`** or **`DRIVE`** | Case-sensitive |
| `vehicle_states[].start` | **Required** per interval | Time offset | Must satisfy complete interval coverage |
| `vehicle_states[].end` | **Required** per interval | Time offset | Greater than start |
| `vehicle_states[].speed_mps` | Optional | Finite numeric speed in metres per second | PARK requires 0; DRIVE requires a positive value; default speeds are 0 and 10 respectively |
| `route` | Optional | null or route mapping | null; fields in Section 5.9 |
| `verify` | Optional | Boolean; read back the requested context | true |
| `inject` | Optional | Boolean; actively set vehicle state and speed | true; false retains reading without injection |
| `timeout_s` | Optional | Time allowed for context readback | 5 seconds |

**Keep `verify: true`** with the default policy. Disabling readback produces `unverified`, which fails the required context check. Readback assesses driving state and distraction restrictions; it does not establish constant speed throughout the interval.

### 5.4 Actions

| Field | Requirement | Type and meaning | Default or example |
| --- | --- | --- | --- |
| `id` | **Required** | Unique action identifier | `jpeg_decode` |
| `role` | Optional | `target` or `background` | target |
| `driver` | **Required** | `activity`, `service`, `shell`, or `url` | No default |
| `phase` | **Required** | Existing phase id or reserved value scenario | payload in the reference scenario |
| `start` | **Required** | Planned start offset | 7 in the reference scenario |
| `stop` | **Required** | Planned stop offset | Must exceed start and fit the phase and observation window |
| `apk` | Optional; supply when installation is required | Path to an existing APK | No installation if omitted |
| `package` | Optional; recommended for APK actions | Actual package name used by default cleanup | com.example.jpegcve |
| `component` | **Conditional** for activity/service | Android package/class component string | com.example.jpegcve/.MainActivity |
| `extras` | **Conditional** on the application interface | Mapping from exact extra names to type/value objects | None supplied if omitted |
| `provision` | Optional | List of device shell commands, each a string or argument list | Executed before baseline auditing; not host shell commands |
| `evidence` | Optional syntactically; needed to establish action behavior | Evidence mapping | Empty evidence: unverified |
| `command` | **Conditional** for shell | Command string or nonempty argument list | **Required** for the shell driver |
| `url` | **Conditional** for url | URL passed as Android VIEW Intent data | Not a general-purpose WebView input field |
| `interval_s` | Optional | Interval between repeated driver invocations | 0; zero or omission invokes the driver once |
| `stop_command` | Optional | Custom device stop command, as string or argument list | Otherwise force-stop the declared package, or clean up the shell action's identifiable process group |

Driver selection:

| Driver | Operation | Principal required input |
| --- | --- | --- |
| `activity` | Start an Activity using am start | component |
| `service` | Start a service using am startservice | component |
| `shell` | Execute a command on the device | command |
| `url` | Start a VIEW Intent with a target address | url; component may also be needed |

**`interval_s` repeats the driver invocation.** APK iteration parameters control work inside the application. For the supplied `url.apk`, use `activity` with its `TARGET_URL` extra.

**Passing inputs to the target app**

`extras` carries Android Intent parameters to the target Activity or service. The JPEG app's [MainActivity.kt](attack_apps/jpegcve/mobile/src/main/java/com/example/jpegcve/MainActivity.kt) defines `EXTRA_*` names and reads them in `onCreate()`. For example:

```kotlin
private const val EXTRA_STRESS_ITERATIONS = "com.example.jpegcve.extra.STRESS_ITERATIONS"
// Read in onCreate():
val iterations = intent.getIntExtra(EXTRA_STRESS_ITERATIONS, 25).coerceIn(1, 100)
```

The corresponding YAML input is:

```yaml
extras:
  com.example.jpegcve.extra.AUTORUN: {type: bool, value: true}
  com.example.jpegcve.extra.STRESS_ITERATIONS: {type: int, value: 4}
```

**Copy the exact name from the app; match `type` to its reader; set `value` for the experiment.** Here, `AUTORUN` enters the automatic execution path, and `getIntExtra()` reads the requested four iterations. CARVE passes these values when launching the Activity. An extra has an effect only if the target app handles it.

The supplied JPEG example uses these app-defined inputs (names shortened here):

| Extra | Why it is supplied |
| --- | --- |
| `AUTORUN` | Run without the reviewer's button clicks. |
| `START_DELAY_MS` | Set to 0 because CARVE already schedules the launch at 7 s. |
| `FINISH_AFTER_RUN` | Ask the Activity to close itself after decoding. |
| `LOAD_FIRST` | Display a scaled preview before the four full-size decodes; the preview is not part of that count. |
| `STRESS_ITERATIONS` | Choose the amount of work; evidence must require the same count. |
| `STRESS_DELAY_MS` | Control the pause after each decode. |

### 5.5 Action Evidence

**`extras` requests work; `evidence` checks that it happened.** The JPEG app's `startStressDecodeFromAssets()` writes start, progress, and completion messages to logcat. The YAML expressions match those messages; they do not generate them.

For example, a successful decode produces a line in this form:

```text
CARVE_PAYLOAD_PROGRESS family=jpegcve iteration=1 bytes=576000000
```

The example's `progress` expression captures the iteration with `(?P<count>\d+)` so `min_counter` can read it, and requires a positive bitmap size with `bytes=[1-9]\d*`. Its `complete` expression requires `success=true completed=4 requested=4`. The separate `process` check looks for CPU scheduling in the trace.

**For another APK, use its actual log messages.** If it provides no suitable messages, add logs where the workload starts, makes progress, and finishes. An Activity launch alone does not establish workload completion.

| Field | Type and meaning | Default or dependency |
| --- | --- | --- |
| `start` | Regular expression for a workload-start log record | Optional; included in the example |
| `progress` | Regular expression for progress records | Used by min_progress and, where requested, min_counter |
| `complete` | Regular expression for successful completion | Included in the example |
| `min_progress` | Integer at least 1; minimum number of matching progress records | 1 |
| `min_counter` | Integer at least 1; minimum observed maximum counter value | Requires a named count group, such as `(?P<count>\d+)`, in `progress` |
| `process` | Actual process name expected in trace scheduling evidence | Optional; included in the example |
| `corroborate` | Regular expression for an additional log evidence source | Optional |
| `corroborate_min` | Integer at least 1; minimum corroborating record count | 1 when omitted |
| `overlap_bin_s` | Positive time value for background-progress coverage bins | 1 second when overlap is assessed |

**`min_progress` counts matching records; `min_counter` checks the largest extracted count.** Keep both consistent with the completion expression and requested work. Log matching uses the recorded action-association window.

### 5.6 Observation

| Field | Requirement | Type and meaning | Default or constraint |
| --- | --- | --- | --- |
| `trace` | **Required** | Path to an existing Perfetto text configuration | Example: ../configs/perfetto.pbtxt; not an output filename |
| `phases` | **Required** | Ordered list of named analysis intervals | Must cover the full scenario |
| `phases[].id` | **Required** per phase | Unique string identifier | scenario is reserved |
| `phases[].start` | **Required** per phase | Time offset | Must satisfy interval constraints |
| `phases[].end` | **Required** per phase | Time offset | Greater than start |
| `start` | Optional | Beginning of the observation window | 0 |
| `end` | Optional | End of the observation window | duration_s |
| `pre_roll_s` | Optional | Additional collection time before the scenario clock starts | 1 second |
| `post_roll_s` | Optional | Additional collection time after execution | 1 second |
| `require_syscalls` | Optional | Boolean; require syscall events within the observation window | true |

The observation window must satisfy **`0 <= start < end <= duration_s`** and contain every action. Phase names organize data; vehicle-state requests are declared separately.

**Omit `duration_ms` from the Perfetto configuration.** CARVE adds its own collection cap when starting the trace.

### 5.7 Policy

| Field | Accepted value | Default and behavior |
| --- | --- | --- |
| `require_baseline` | Boolean | true; require baseline evidence |
| `require_context` | Boolean | true; require vehicle-context evidence |
| `require_actions` | Boolean | true; require evidence for all declared actions |
| `require_trace` | Boolean | true; require trace integrity and coverage |
| `require_route` | Boolean | true when a route is declared, otherwise false; true without a route is invalid |
| `require_overlap` | Boolean | false; when true, require actual target/background overlap evidence |
| `scheduler` | absolute or relative | absolute; relative supports a relative-wait comparison condition |
| `command_timeout_s` | Positive time value | 10 seconds; bounds routine ADB calls, with separate limits for some preparation operations |
| `watchdog_s` | Positive time value | duration_s + 30 seconds; bounds timed execution, not all preparation and collection work |
| `worker_grace_s` | Positive time value | 3 seconds; allowance used in worker shutdown handling |
| `continue_on_failure` | Boolean | false; an ineligible attempt stops subsequent designated repetitions |

Changing a `require_*` option changes the acceptance conditions; the individual check still retains its observed result.

### 5.8 Markers

Optional top-level `markers` entries contain:

| Field | Requirement | Value |
| --- | --- | --- |
| `id` | **Required** per marker | A marker identifier; a unique simple string is recommended |
| `at` | **Required** per marker | A nonnegative offset no greater than duration_s |

Fragment to add to a complete scenario:

```yaml
markers:
  - {id: checkpoint, at: 12}
```

### 5.9 Routes

Use these fields within `context.route` when a route is declared. The supplied route is [scenarios/route.gpx](scenarios/route.gpx); use `path: route.gpx` for a YAML in `scenarios/`:

| Field | Requirement | Type and meaning | Default or constraint |
| --- | --- | --- | --- |
| `path` | **Required** | Existing GPX path | At least two valid ordered trkpt/rtept coordinates with positive total route distance |
| `start` | Optional | Route start offset | 0 |
| `duration_s` | Optional | Positive route duration | Scenario duration; start plus duration must remain inside the scenario |
| `tick_s` | Optional | Positive sampling interval | 1 second; duration_s divided by tick_s must not exceed 100000 |
| `speed_profile` | Optional | `constant`, `urban`, or `stop_go` | constant |
| `seed` | Optional | Random seed; use an integer for documented experiments | 0 |
| `stop_count` | Optional | Nonnegative integer number of stops for stop_go | 0 |
| `stop_duration_s` | Optional | Nonnegative stop duration for stop_go | 0 |
| `write_speed` | Optional | Boolean; also write route-derived VHAL speed | false; location injection only |

GPX timestamps are ignored; YAML controls sampling. **PARK does not pause location updates.** With `write_speed: true`, align route-derived speed with the vehicle-state schedule.

## 6. Modification Procedures

Apply each procedure to a copy of the reference scenario, retain other fields, then **validate and inspect the plan**.

### 6.1 Extend the Run to 60 Seconds

| Location | New value |
| --- | --- |
| `name` | `my-jpeg-60s` |
| `duration_s` | **60** |
| Single vehicle interval | PARK, start **0**, end **60** |
| pre_payload phase | start **0**, end **10** |
| payload phase | start **10**, end **50** |
| post_payload phase | start **50**, end **60** |
| jpeg_decode action | start **12**, stop **48** |

**Check:** `10 <= 12 < 48 <= 50`. The omitted observation start/end default to the new full duration.

### 6.2 Change the Decode Count

For eight decodes, update all five values:

| Location | Change |
| --- | --- |
| STRESS_ITERATIONS extra `value` | **4 → 8** |
| `evidence.min_progress` | **4 → 8** |
| `evidence.min_counter` | **4 → 8** |
| `evidence.complete` | **completed=4 → completed=8** |
| `evidence.complete` | **requested=4 → requested=8** |

If execution exceeds the action window, inspect the logs before extending the action and phase boundaries.

### 6.3 Add Driving Intervals

Replace `context` in the original 30-second scenario with this fragment:

```yaml
context:
  route: null
  verify: true
  vehicle_states:
    - {state: DRIVE, start: 0, end: 5, speed_mps: 5}
    - {state: PARK, start: 5, end: 25}
    - {state: DRIVE, start: 25, end: 30, speed_mps: 5}
```

**Check:** DRIVE at 0 s, PARK at 5 s, DRIVE at 25 s; the JPEG action remains within PARK. This variant ends in DRIVE, so **restore or prepare PARK before another attempt**.

### 6.4 Add a Route or Background Action

For a route, follow Section 5.9. For another action, add a list item with a unique id and an explicit start/stop window; use `role: background` where appropriate and `phase: scenario` when spanning phases.

With **`require_overlap: true`**, both actions need matched start/progress/completion evidence. The background must cover the target's observed interval and show progress in every complete `overlap_bin_s` bin.

## 7. Results and Acceptance

### 7.1 Output Files

Each attempt has its own output subdirectory. Available files depend on execution progress.

| File | Purpose |
| --- | --- |
| `scenario.yaml`, `plan.json` | Original input and resolved schedule |
| `runtime.json`, `environment.json` | Runtime bindings and device details |
| `inputs/` | Copies of the default environment JSON and referenced configuration/route files; locations are recorded in `runtime.json` |
| `events.jsonl` | Preparation, dispatch, timing, and collection records |
| `logcat.txt`, `trace.pftrace` | Collected logs and trace |
| `outcome.json` | Execution result and lifecycle stage |
| **`checks.json`** | **Evidence checks and analysis eligibility** |

Retain the APK separately; its path is recorded in `runtime.json`, and it is not copied into each attempt directory.

### 7.2 Acceptance Results

| Result | Meaning |
| --- | --- |
| **`matched`** | Evidence satisfies the declared check. |
| **`mismatched`** | Adequate evidence does not satisfy the condition. |
| **`unverified`** | Evidence is insufficient to decide. |
| **`analysis_eligible: true`** | Execution completed, boot continuity was established, and every required check matched. |
| **`analysis_eligible: false`** | Inspect the failed or unverified checks and execution outcome. |

> [!IMPORTANT]
> **Use `analysis_eligible` to assess acceptance.** `execution: completed` or a successful Activity launch alone does not establish that the requested work and collection requirements were met.

### 7.3 Exit Codes and Reassessment

| Code | Meaning |
| --- | --- |
| **0** | Validation/planning succeeded, or run/check met its eligibility requirements. |
| **1** | A handled input or operational error. |
| **2** | An ineligible run/check, fewer attempts than requested, or invalid CLI arguments; inspect the command output. |

To reassess an existing attempt after obtaining a trace processor:

```sh
.venv/bin/python carve.py check YOUR_ATTEMPT_DIRECTORY \
  --trace-processor /path/to/trace_processor_shell
```

The command reassesses the saved logs, trace, and execution records and updates `checks.json`, retaining the collected logs and traces.
