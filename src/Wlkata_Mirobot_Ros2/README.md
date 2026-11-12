[**English**](README.md) | [中文](README-ZH.md)

## Project Overview

This project is an integrated control and teaching example for the **WLKATA Mirobot robotic arm**. It mainly includes:

- **Robot description** (`wlkata_mirobot_description`): URDF/Xacro models, joint configuration and related files for visualizing the robot in RViz / simulation.
- **MoveIt motion planning configuration** (`wlkata_mirobot_moveit_config`): a MoveIt 2 configuration package for planning and visualizing Mirobot trajectories.
- **Real robot control node** (`wlkata_arm_move`): subscribes to trajectories planned by MoveIt, converts them to G-code, and sends them to the physical robot via a serial connection.
- **Python control SDK** (`wlkatapython`): a standalone Python package for controlling Mirobot, E4, MT4, MS4220 and related devices over serial using G-code.

Scenarios where this project is useful:

- Motion planning and visualization for Mirobot using ROS 2 + MoveIt.
- Streaming trajectories from MoveIt directly to a real Mirobot.
- Building higher-level applications or host programs on top of `wlkatapython`.




## Directory Layout

Key directories in this project:

- `wlkata_mirobot_description/`
  - `urdf/`: URDF/Xacro robot model files, such as `wlkata_mirobot_description.urdf`.
  - `config/`: joint names and configuration YAML files.
  - `launch/display.launch.py`: launch file to show the Mirobot model in RViz.

- `wlkata_mirobot_moveit_config/`
  - `config/`: MoveIt configuration files such as `kinematics.yaml`, `ros2_controllers.yaml`, `wlkata_mirobot_description.srdf`, etc.
  - `launch/`: MoveIt-related launch files:
    - `demo.launch.py`: typical MoveIt + RViz demo entry point.
    - `move_group.launch.py`, `moveit_rviz.launch.py`, `rsp.launch.py`, etc.
  - `package.xml`: MoveIt configuration package manifest.

- `wlkata_arm_move/`
  - `wlkata_arm_move/mirobot_moveit_move.py`: the core Python node that subscribes to `/display_planned_path` (`moveit_msgs/DisplayTrajectory`), converts trajectories to G-code commands, and sends them via serial to the robot.
  - `package.xml`: ROS 2 Python package manifest (depends on `rclpy`, `moveit_msgs`, `wlkata_interfaces`, `action_msgs`, `std_msgs`, etc.).
  - `setup.py`: exposes the `mirobot_moveit_move` node via `console_scripts`.

- `wlkatapython/`
  - `wlkatapython.py` and multiple sub-directories (`Mirobot_robot/`, `E4_robot/`, `MT4_robot/`, `MS4220_robot/`, etc.) implementing serial control for different devices.
  - `README.md`: detailed documentation on how to control Mirobot/E4/MT4/MS4220 with Python + serial + G-code, including examples and wiring diagrams.
  - `setup.py`: packaging configuration for installing the SDK via `pip`.


## Environment Requirements

- **Operating system**: Ubuntu 22.04 or 24.04 with ROS 2 + MoveIt 2 support.
- **ROS 2 + MoveIt 2**: includes `ament_cmake`, `moveit_ros_move_group`, `moveit_ros_visualization`, `robot_state_publisher`, `rviz2`, etc. (see each package's `package.xml`).
- **Python**: Python ≥ 3.9.
- **Build tool**: `colcon`.


## Workspace Layout & Where to Run Commands

Recommended ROS 2 workspace layout (example):

```text
~/ros2_ws/
  ├─ src/
  │   ├─ wlkata_mirobot_description/
  │   ├─ wlkata_mirobot_moveit_config/
  │   ├─ wlkata_arm_move/
  │   └─ wlkatapython/
  └─ ...
```

- **Where to put this repository**
  - Place the whole repository under `~/ros2_ws/src/`, so that the above packages appear inside `src/`.

- **Where to run commands**
  - `sudo apt install ...`: can be run from any directory.
  - `cd ~/ros2_ws`: go to the ROS 2 workspace root (contains `src/`, `build/`, `install/`, etc.).
  - `colcon build`: run from the workspace root `~/ros2_ws`.
  - `source install/setup.bash`: run from the workspace root (or any shell where you want to use this workspace).
  - `ros2 launch ...` / `ros2 run ...`: run in a terminal **after** sourcing `install/setup.bash`; current directory is not important (often `~/ros2_ws`).


## Quick Start (Minimal)

The following minimal command sets have been tested on Ubuntu 22.04 / 24.04, assuming ROS 2 is already installed.

### Ubuntu 24.04 + ROS 2 Jazzy

```bash
sudo apt install \
  ros-jazzy-joint-state-publisher-gui \
  ros-jazzy-moveit \
  ros-jazzy-controller-manager \
  ros-jazzy-joint-trajectory-controller \
  ros-jazzy-joint-state-broadcaster

cd ~/ros2_ws          # your workspace
colcon build
source install/setup.bash

ros2 launch wlkata_mirobot_description display.launch.py
ros2 launch wlkata_mirobot_moveit_config demo.launch.py
```

- `ros2 launch wlkata_mirobot_description display.launch.py`: launches the robot description and RViz, showing the **Mirobot model** and joint-state publisher GUI. Mainly used to verify URDF and joint configuration (no MoveIt planning UI).
- `ros2 launch wlkata_mirobot_moveit_config demo.launch.py`: launches the **MoveIt + RViz demo**. The MotionPlanning panel appears on the left for interactive planning and execution (in simulation or together with the real robot control node).


### Ubuntu 22.04 + ROS 2 Humble

Package names are almost the same; just replace the `ros-jazzy-*` prefix with `ros-humble-*`:

```bash
sudo apt install \
  ros-humble-joint-state-publisher-gui \
  ros-humble-moveit \
  ros-humble-controller-manager \
  ros-humble-joint-trajectory-controller \
  ros-humble-joint-state-broadcaster

cd ~/ros2_ws          # your workspace
colcon build
source install/setup.bash

ros2 launch wlkata_mirobot_description display.launch.py
ros2 launch wlkata_mirobot_moveit_config demo.launch.py
```


## Dependency Notes (Ubuntu 22.04 / 24.04)

- **Ubuntu 24.04 + ROS 2 Jazzy**
  - Core dependencies are installed via the `apt` commands above.
  - For development, you may additionally install `python3-colcon-common-extensions`, `python3-rosdep`, etc.

- **Ubuntu 22.04 + ROS 2 Humble**
  - Replace `ros-jazzy-*` with `ros-humble-*` in the package names.
  - If ROS 2 is not installed yet, follow the official ROS 2 desktop installation guide first.


## Serial Communication & Controller

Serial communication is mainly described in `wlkatapython/README.md`. In short:

- Devices are controlled via **G-code over serial** (RS485 or UART).
- A **multi-function controller** is typically required; some SDK functions may not work when connecting the arm directly without it.

Example (UART mode, Mirobot homing):

```python
import wlkatapython
import serial

Serial1 = serial.Serial("/dev/ttyUSB0", 115200)
Mirobot1 = wlkatapython.Mirobot_UART()
Mirobot1.init(Serial1, -1)
Mirobot1.homing()
Serial1.close()
```

In `mirobot_moveit_move.py`, the default serial device is:

```python
serial.Serial("/dev/ttyUSB0", 115200, timeout=1)
```

Please confirm the actual device name on your system (typically `/dev/ttyUSB0`, `/dev/ttyUSB1`, etc.) and adjust accordingly.


## Package Summary

### `wlkata_mirobot_description`

- Provides Mirobot URDF/Xacro models, joint configuration and launch files required for RViz visualization and basic simulation.
- Useful for checking the model and coordinate frames.

### `wlkata_mirobot_moveit_config`

- Generated using MoveIt Setup Assistant; includes kinematics, controllers and planning scene configuration.
- Provides `demo.launch.py` and other launch files for starting MoveIt + RViz demo scenes.

### `wlkata_arm_move`

- ROS 2 Python package with the core node `mirobot_moveit_move`:
  - Receives trajectories planned by MoveIt.
  - Converts them to WLKATA-compatible G-code.
  - Sends them via serial to the real robot and handles responses.

### `wlkatapython`

- Standalone Python package installable via `pip`, supporting Mirobot, E4, MT4, MS4220 and other devices.
- Supports both RS485 and UART communication; its `README.md` contains many examples and wiring diagrams.
- The ROS 2 control node in this repository reuses the communication logic from `wlkatapython`.

