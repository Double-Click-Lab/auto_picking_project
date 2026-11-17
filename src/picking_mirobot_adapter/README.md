# picking_mirobot_adapter

ROS 2 Humble node for the Mirobot notebook. The server sends a command on a
ROS topic. The adapter runs one fixed, local 10-step pick-and-place plan; the
command does not contain coordinates. This adapter does not use `/pick_place`
or `/mirobot/status`, and it never controls the camera or linear motor.

## Fixed plan

The plan is loaded once from `config/fixed_robot_plan.yaml`. Units follow the
existing Mirobot controller: XYZ in millimetres and RX/RY/RZ in degrees.
Approach distance is 60 mm along the tool axis, calculated from the saved RPY;
feedrate is 3000 mm/min. The executed sequence is always:

1. Move to configured HOME coordinates.
2. Move to PICK_APPROACH.
3. Move to PICK.
4. Vacuum ON.
5. Move back to PICK_APPROACH.
6. Move to PLACE_APPROACH.
7. Move to PLACE.
8. Vacuum OFF.
9. Move back to PLACE_APPROACH.
10. Move to configured HOME coordinates.

The robot command payload's item and bin are metadata only in this version;
they do not select alternate coordinates. Each unit is one command with
`quantity: 1`; the server must create a fresh `command_id` for another item.
The local fixed plan is not automatically inferred from camera pixels, marker
IDs, or motor positions.

## Safety and operation

Default mode is `simulation`. It runs a software-only stub and does not import
or instantiate the serial controller. A command whose `mode` differs from the
node mode is rejected. Real mode requires an explicit hardware controller
configuration accepted by the existing `validate_config`: set
`simulation_mode: false`, `hardware_enable: true`,
`coordinates_confirmed: true`, measured `workspace_bounds`,
`tool_axis_approach: true`, and `poses.home` matching the fixed-plan HOME.
Real startup never homes automatically. It holds the serial connection and
reports fault if the robot starts in Alarm/not-Idle. After checking that the
workspace is clear and the cup is empty, an operator can explicitly request
homing through the local `/picking/robot/home` Trigger service. This service is
available only in real mode when `homing_enable: true`; the controller also
requires pump and valve PWM to be zero. It runs in the same process and keeps
the serial connection open. The server command topic remains disabled until
the controller reports ready. The controller still checks workspace, serial
ACK, and final coordinates for every move.

No command causes automatic retry, an emergency stop, pump reset, or return
movement after a failure. Cancel, heartbeat loss, and deadline expiry set a
local cancellation flag checked between plan steps. A serial operation already
in progress can continue until its controller call returns; the event and
status explicitly say that physical stop is unconfirmed. Inspect the robot,
cup, held-object state, and workspace before using the documented recovery.
The default local plan deadline is 45 seconds; server command expiry must
leave enough time for the complete run and reporting. Adjust both ends only
after observing the plan duration.

Run only one process that controls this robot. Do not run this adapter beside
the legacy action server, an HTTP bridge, `camera_mirobot_sim.py`, the teaching
terminal, or another direct serial controller. For real mode, keep the
`Wlkata_Mirobot_Ros2/wlkatapython` SDK on `PYTHONPATH` if it is not already
available in the environment.

## Topic contract v1

All messages are `std_msgs/msg/String` containing a UTF-8 JSON object. QoS is
KEEP_LAST depth 10, RELIABLE, VOLATILE. Use `ROS_DOMAIN_ID=7` and
`ROS_LOCALHOST_ONLY=0` on all notebooks.

| Topic | Direction | Meaning |
| --- | --- | --- |
| `/picking/robot/command` | server → Mirobot | `device=robot`, `action=pick_and_place` |
| `/picking/device_events` | Mirobot → server | accepted and final result |
| `/picking/device_status` | Mirobot → server | one-second status |
| `/picking/server_heartbeat` | server → devices | one-second session heartbeat |
| `/picking/result_ack` | server → devices | confirm a persisted final result |
| `/picking/cancel` | server → Mirobot | cancel the matching active command |

The exact JSON schemas and validation live in `picking_mirobot_adapter/protocol.py`.
The node checks canonical UUIDs, exact field sets/types, quantity exactly 1,
and `destination_id == order_id`. The fixed local plan is loaded and checked
at startup, including finite coordinates, tool-axis approach points, and real
mode workspace bounds.

The server heartbeat must match the command session, be no older than 5
seconds, and have clock skew no more than 1 second. A new command must have
more than 2 seconds until expiry. On acceptance, the node converts the
remaining expiry (minus 1 second) to a monotonic deadline and caps it at
`plan_timeout_sec` (45 seconds by default). Heartbeat loss/session change or
deadline expiry records a failed, outcome-uncertain result and latches fault.

## Durable delivery and recovery

SQLite records normalized command content before execution and stores the
complete final event with its event UUID. Same-ID retries reissue only the
saved accepted/result event. Reuse of an ID with changed content is logged as
a protocol error without replacing the original. Results are resent every
second until an exact acknowledgement matches server session, command, device,
event UUID, and `accepted: true`. A new command is blocked while any result is
unacknowledged.

If the process restarts while a command has no final event, it records a
failed outcome-unknown result and never reruns that command. To clear a fault,
first acknowledge pending results, stop the adapter, physically inspect the
robot and work area, then run:

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0
ros2 run picking_mirobot_adapter picking_mirobot_recover \
  --confirm-physical-inspection
```

This changes only the local ledger; it does not publish a topic or send a
robot command.

## Build and launch

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
ros2 launch picking_mirobot_adapter mirobot_topic.launch.py
```

The launch defaults to simulation. Hardware-free status checks:

```bash
ros2 topic list | grep '^/picking/'
ros2 topic echo /picking/device_status std_msgs/msg/String
ros2 topic echo /picking/server_heartbeat std_msgs/msg/String
ros2 topic info /picking/robot/command --verbose
```

To explicitly start real mode, provide a separately reviewed controller YAML
with the safety values described above. Its measured workspace bounds must
contain HOME, PICK, PLACE, and both computed tool-axis approaches. For
example, after preparing that file:

```bash
export PYTHONPATH="$PWD/src/mirobot_pick_place:$PWD/src/Wlkata_Mirobot_Ros2/wlkatapython:$PYTHONPATH"
ros2 run picking_mirobot_adapter mirobot_topic_node --ros-args \
  -p mode:=real \
  -p controller_config:=/absolute/path/to/verified_mirobot_controller.yaml \
  -p plan_file:="$PWD/src/picking_mirobot_adapter/config/fixed_robot_plan.yaml"
```

This launch command is intentionally not run as part of build/test. Do not
start the legacy `/pick_place` action server, HTTP bridge, or teaching
terminal at the same time. Starting real mode opens the serial port and reads
status but does not send homing, movement, or pump commands. Homing occurs
only if an operator calls the explicit local service; the fixed pick-and-place
plan starts only after a valid server topic command arrives.

For explicit homing while the real adapter is running, keep the robot clear
and the cup unloaded, then call:

```bash
ros2 service call /picking/robot/home std_srvs/srv/Trigger "{}"
```

Wait for the response `success: true` and status `ready` before allowing the
server to publish a robot command. A service call rejected because
`homing_enable` is false requires a reviewed controller config update; do not
work around it by sending `$H` or other raw serial strings while this node
owns the port.

## Simulation command publisher

The one-shot test publisher emits only a simulation command and creates new
UUIDs/timestamps each run. Use the current session UUID from the server
heartbeat:

```bash
ros2 run picking_mirobot_adapter picking_sim_test_publisher \
  --server-session-id <server_session_uuid> --item-id demo --bin A
```

It requests the same fixed plan in software simulation. A final success event
comes only from the simulation worker after its plan steps complete; this is
not a physical success claim. The server must publish the exact result ack
before another new command can be admitted.

## Tests and integration boundary

Hardware-free tests cover protocol validation, persistence, duplicate IDs,
result acknowledgements, restart uncertainty, recovery gating, pose loading,
the ten-step plan, and its software-only execution path. Run them with:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0 colcon test \
  --python-testing pytest --packages-select picking_mirobot_adapter
```

The optional DDS topic integration check starts only the simulation adapter
and needs a working local ROS 2 DDS network:

```bash
MIROBOT_RUN_SIM_INTEGRATION=1 python3 -m pytest -q \
  src/picking_mirobot_adapter/test/test_sim_topic_integration.py
```

The server notebook must publish the matching command, heartbeat, cancel, and
result-ack topics and persist accepted/result events. Camera/motor notebooks
and the server are separate computers; successful local builds do not confirm
three-notebook DDS connectivity. No real Mirobot movement test is performed
by this package's build or test commands.
