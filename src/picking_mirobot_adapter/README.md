# picking_mirobot_adapter

ROS 2 Humble topic adapter for the Mirobot notebook. It accepts the v1 robot
command topic and delegates approved motion to the existing `/pick_place`
action. It does not control the camera or linear motor.

## Safety and current behavior

The default launch runs in `simulation` mode with a non-moving stub backend.
That backend checks `/mirobot/status` and `/pick_place` availability, then
records a simulated success without sending an action goal, opening serial, or
calling a controller. `simulation_action_backend:=true` is an explicit opt-in
for integration testing against the existing simulation action server. Real
mode requires a separately configured, safety-enabled action server and a
confirmed coordinate file. No physical robot test is part of this package's
build or test commands.

Do not run this adapter alongside any other process that controls the same
Mirobot, including an HTTP bridge, `camera_mirobot_sim.py`, another action
server, or a second adapter instance. The SQLite process lock rejects a second
adapter using the same database. Keep the existing action server as the only
robot controller.

## Existing ROS interfaces

The adapter uses the repository's existing
`mirobot_pick_place_interfaces/action/PickPlace` at `/pick_place`. Its goal has
`start`, pick and place XYZ/RX/RY/RZ fields, and `approach_height`; its result
has `success` and `message`. Before every command, it calls the existing
`std_srvs/srv/Trigger` service `/mirobot/status` and checks `success` plus the
machine-readable `mode=simulation|real` in its message. The action server's
status message was extended with that mode label; readiness behavior was not
changed. Status checking never initializes, homes, or moves the robot.

For hardware, retain the existing controller's `simulation_mode`,
`hardware_enable`, `coordinates_confirmed`, `workspace_bounds`,
`homing_enable`, and explicit initialization procedure. The adapter validates
the selected controller config with the existing `validate_config` function.
It never bypasses that controller or writes serial commands itself.

## Protocol v1

All inter-notebook data is `std_msgs/msg/String` containing a UTF-8 JSON
object. Every publisher and subscriber uses KEEP_LAST depth 10, RELIABLE,
VOLATILE QoS. ROS domain is 7 and `ROS_LOCALHOST_ONLY=0`.

| Topic | Direction | Use |
| --- | --- | --- |
| `/picking/robot/command` | server → robot notebook | `device=robot`, `action=pick_and_place` |
| `/picking/device_events` | robot notebook → server | accepted and final result events |
| `/picking/device_status` | robot notebook → server | one-second robot status |
| `/picking/server_heartbeat` | server → devices | one-second server session heartbeat |
| `/picking/result_ack` | server → devices | confirm a persisted final result |
| `/picking/cancel` | server → device | cancel the matching active command |

The adapter validates exact protocol field names and types, canonical UUIDs,
quantity exactly integer `1`, and `destination_id == order_id`. Robot commands
do not carry motion coordinates: the adapter reads its fixed local pose YAML
once at startup. It checks that all six axes are finite and that
`approach_height` is positive; existing workspace and approach calculations
are applied. The robot action goal uses the incoming command UUID as its ROS
goal UUID. Success requires both action status `SUCCEEDED` and
`PickPlace.Result.success == true`.

The device checks heartbeat clock skew (≤1 s), recency (≤5 s), matching server
session, and command expiry (>2 s remaining). On acceptance it converts
`expires_at - time.time() - 1 s` into a monotonic deadline. A heartbeat gap,
session change, expiry, or action timeout requests the existing action cancel
where possible and records failure with the stop state explicitly unconfirmed.
An action cancel topic is not an emergency stop.

## Durable delivery and recovery

The SQLite ledger stores canonical command JSON before starting work. It
deduplicates command IDs, logs same-ID/different-content protocol errors
without replacing the original, and stores the full final event including its
event UUID before publishing. Unacknowledged results are resent once per
second across process restarts. An acknowledgement ends retries only when
server session, command, device, event UUID and `accepted=true` all match.
Another new command is not accepted until the previous result is acknowledged.

If the process restarts with an unresolved accepted command, the adapter
records an outcome-unknown failure, never retries the action, and latches
`fault`. After acknowledging all outstanding events and physically inspecting
the robot, stop the adapter and explicitly clear that latch:

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0
ros2 run picking_mirobot_adapter picking_mirobot_recover \
  --confirm-physical-inspection
```

This command only updates the local ledger; it sends no ROS or robot command.
Then restart the adapter. Do not clear the fault while robot position,
held-object state, or action completion is unknown.

## Build and default launch

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
ros2 launch picking_mirobot_adapter mirobot_topic.launch.py
```

Start the existing action server separately when testing the adapter. Its
controller configuration must match the adapter's `controller_config` mode
and HOME. Keep that server in simulation for the default setup.

For action-level simulation integration only, run the adapter with:

```bash
ros2 run picking_mirobot_adapter mirobot_topic_node --ros-args \
  -p mode:=simulation -p simulation_action_backend:=true \
  -p controller_config:=/path/to/simulation_poses.yaml \
  -p coordinate_file:=/path/to/simulation_robot_poses.yaml
```

Use `config/robot_poses.example.yaml` only with the no-action stub. It contains
example values and is not a real robot calibration. To configure actual
coordinates, create a separate local YAML with the same keys `home`, `pick`,
`place` (each x/y/z/rx/ry/rz), and `approach_height`, plus
`simulation_only: false` and `coordinates_confirmed: true`. Supply it through
`coordinate_file`; never edit the example into apparent real coordinates.
Also supply the same hardware-enabled controller YAML to both the existing
action server and this adapter. Real mode is not launched by the provided
launch file.

`action_timeout_sec` defaults to 45 seconds. Set the server-side command
expiry longer than the expected action duration plus clock/reporting margin;
otherwise the adapter will cancel locally at command expiry and report
failure. All three notebooks need synchronized clocks and the same LAN.

## Non-moving communication check

Inspect topics without publishing commands:

```bash
ros2 topic list | grep '^/picking/'
ros2 topic echo /picking/device_status std_msgs/msg/String
ros2 topic info /picking/robot/command --verbose
```

Status should appear once per second with `device_id=mirobot_laptop`. To check
the server heartbeat, run `ros2 topic echo /picking/server_heartbeat
std_msgs/msg/String` on the robot notebook. Do not send a hand-written real
command with `ros2 topic pub`.

## Simulation command publisher

The one-shot publisher generates a new `command_id`, `order_id`, and current
timestamps on each run. It only emits `mode=simulation`; pass the current
server session UUID shown by the server heartbeat:

```bash
ros2 run picking_mirobot_adapter picking_sim_test_publisher \
  --server-session-id <server_session_uuid> --item-id demo --bin A
```

This publisher does not claim completion. Completion is reported only by the
adapter event after its selected stub or explicit simulation action backend.
The server must process the result and publish the exact `result_ack` before
another new command can be accepted.

## Tests

Hardware-free unit tests exercise strict JSON/schema validation, command
admission (mode, session, heartbeat skew/age, expiry and result gating),
duplicate and changed command IDs, durable result acknowledgement, restart
outcome uncertainty, operator recovery gating, and pose validation:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0 colcon test \
  --python-testing pytest --packages-select picking_mirobot_adapter
```

The optional action integration test is separate from the unit tests and can
be requested with `MIROBOT_RUN_SIM_INTEGRATION=1 python3 -m pytest -q
src/picking_mirobot_adapter/test/test_sim_action_integration.py`. It starts
the existing simulation action server and uses `simulation_action_backend:=true`.
The provided example is a stub-only simulation configuration. The three
notebooks have not been jointly exercised here; the server notebook still
needs to implement the matching topic contract, result persistence/ack,
heartbeat, and end-to-end workflow tests. No hardware movement test was run.
