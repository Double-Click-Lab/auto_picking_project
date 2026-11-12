[English](README.md) | **中文 / Chinese**

## 工程简介

本工程是一个用于 **WLKATA Mirobot 系列机械臂** 的综合控制与示教工程，主要包含：

- **机械臂模型与描述**（`wlkata_mirobot_description`）：URDF/Xacro、关节配置等，用于在 RViz / 仿真中显示机器人。
- **MoveIt 运动规划配置**（`wlkata_mirobot_moveit_config`）：基于 MoveIt 2 的配置包，用于对 Mirobot 进行路径规划与可视化。
- **真实机械臂控制节点**（`wlkata_arm_move`）：订阅 MoveIt 规划出的轨迹，转换为 G-code，通过串口下发给真实机械臂。
- **Python 控制 SDK**（`wlkatapython`）：独立的 Python 包，通过串口 + G-code 控制 Mirobot、E4、MT4、MS4220 等设备。

适用场景：

- 使用 ROS 2 + MoveIt 进行 **Mirobot 运动规划与可视化**；
- 将 MoveIt 规划轨迹 **无缝下发到真实机械臂**；
- 基于 `wlkatapython` 进行 **二次开发、上位机编程**。


## 目录结构

工程关键目录：

- `wlkata_mirobot_description/`
  - `urdf/`：`wlkata_mirobot_description.urdf`、`.xacro` 等机械臂模型文件。
  - `config/`：关节名称、配置等 YAML 文件。
  - `launch/display.launch.py`：在 RViz 中显示 Mirobot 模型。

- `wlkata_mirobot_moveit_config/`
  - `config/`：MoveIt 所需的 `kinematics.yaml`、`ros2_controllers.yaml`、`wlkata_mirobot_description.srdf` 等。
  - `launch/`：
    - `demo.launch.py`：常见的 MoveIt + RViz 演示入口。
    - `move_group.launch.py`、`moveit_rviz.launch.py`、`rsp.launch.py` 等。
  - `package.xml`：MoveIt 配置包定义。

- `wlkata_arm_move/`
  - `wlkata_arm_move/mirobot_moveit_move.py`：核心 Python 节点，订阅 `/display_planned_path`（`moveit_msgs/DisplayTrajectory`）话题，将规划出的关节轨迹转换为 G-code 命令，经串口发送给机械臂。
  - `package.xml`：ROS 2 Python 包定义，依赖 `rclpy`、`moveit_msgs`、`wlkata_interfaces`、`action_msgs`、`std_msgs` 等。
  - `setup.py`：通过 `entry_points` 暴露 `mirobot_moveit_move` 可执行节点。

- `wlkatapython/`
  - `wlkatapython.py` 及多个子目录（`Mirobot_robot/`、`E4_robot/`、`MT4_robot/`、`MS4220_robot/` 等）：各类设备的串口控制实现。
  - `README.md`：详细介绍了如何通过 Python + 串口 + G-code 控制 Mirobot/E4/MT4/MS4220 等设备（含示例代码与接线图）。
  - `setup.py`：独立 Python 包的打包配置，可单独 `pip install` 使用。


## 环境依赖

- **操作系统**：推荐使用支持 ROS 2 + MoveIt 2 的 Ubuntu 22.04 / 24.04。
- **ROS 2 + MoveIt 2**：已含 `ament_cmake`、`moveit_ros_move_group`、`moveit_ros_visualization`、`robot_state_publisher`、`rviz2` 等依赖（见各 `package.xml`）。
- **Python 环境**：Python ≥ 3.9。
- **构建工具**：`colcon`。


## 工作目录与命令运行位置

推荐的 ROS 2 工作区结构（示例）：

```text
~/ros2_ws/
  ├─ src/
  │   ├─ wlkata_mirobot_description/
  │   ├─ wlkata_mirobot_moveit_config/
  │   ├─ wlkata_arm_move/
  │   └─ wlkatapython/
  └─ ...
```

- **仓库拷贝位置**：将本工程整个文件夹放到 `~/ros2_ws/src/` 下（保证 `src/` 里能看到上面几个包）。
- **命令运行位置**：
  - `sudo apt install ...`：任意目录均可执行；
  - `cd ~/ros2_ws`：进入 ROS 2 工作区根目录；
  - `colcon build`：在 `~/ros2_ws` 下执行；
  - `source install/setup.bash`：在 `~/ros2_ws` 或其他终端中执行，用于加载该工作区环境；
  - `ros2 launch ...` / `ros2 run ...`：在已经执行过 `source install/setup.bash` 的终端中运行，当前目录不限。


## 快速开始（极简版）

### Ubuntu 24.04 + ROS 2 Jazzy

```bash
sudo apt install \
  ros-jazzy-joint-state-publisher-gui \
  ros-jazzy-moveit \
  ros-jazzy-controller-manager \
  ros-jazzy-joint-trajectory-controller \
  ros-jazzy-joint-state-broadcaster

cd ~/ros2_ws          # 换成你的工作区
colcon build
source install/setup.bash

ros2 launch wlkata_mirobot_description display.launch.py
ros2 launch wlkata_mirobot_moveit_config demo.launch.py
```

- `ros2 launch wlkata_mirobot_description display.launch.py`：只启动机器人描述与 RViz，可看到 **Mirobot 模型**、关节状态发布/GUI，主要用于检查 URDF、关节配置是否正确，不包含 MoveIt 规划界面。
- `ros2 launch wlkata_mirobot_moveit_config demo.launch.py`：启动 **MoveIt + RViz 演示场景**，在 RViz 左侧会出现 MotionPlanning 面板，可以交互规划轨迹并执行（仿真或配合真实机械臂节点）。


### Ubuntu 22.04 + ROS 2 Humble

包名基本一致，仅前缀从 `ros-jazzy-*` 换成 `ros-humble-*`：

```bash
sudo apt install \
  ros-humble-joint-state-publisher-gui \
  ros-humble-moveit \
  ros-humble-controller-manager \
  ros-humble-joint-trajectory-controller \
  ros-humble-joint-state-broadcaster

cd ~/ros2_ws          # 换成你的工作区
colcon build
source install/setup.bash

ros2 launch wlkata_mirobot_description display.launch.py
ros2 launch wlkata_mirobot_moveit_config demo.launch.py
```


## 在 Ubuntu 22.04 / 24.04 上的依赖说明（简要）

- Ubuntu 24.04 + ROS 2 Jazzy：核心依赖通过上面的 `apt` 命令即可安装，开发时可额外安装 `python3-colcon-common-extensions`、`python3-rosdep` 等。
- Ubuntu 22.04 + ROS 2 Humble：将所有 `ros-jazzy-*` 包名改为 `ros-humble-*` 即可，如未安装 ROS 2，建议先按照 ROS 官方文档安装 desktop 版本。


## 串口与控制器说明

串口通信部分主要参考 `wlkatapython/README.md`：

- 使用 **G-code 协议 + 串口通信（RS485/UART）** 控制机械臂、滑台、输送带等设备；
- 通常需要 **多功能控制器** 才能完整使用全部功能，如果机械臂直接串口连接，部分函数可能不可用。

示例（UART 模式，Mirobot 回零）：

```python
import wlkatapython
import serial

Serial1 = serial.Serial("/dev/ttyUSB0", 115200)
Mirobot1 = wlkatapython.Mirobot_UART()
Mirobot1.init(Serial1, -1)
Mirobot1.homing()
Serial1.close()
```

在 `mirobot_moveit_move.py` 中，默认串口设备为：

```python
serial.Serial("/dev/ttyUSB0", 115200, timeout=1)
```

- 请确认实际串口号（通常为 `/dev/ttyUSB0`、`/dev/ttyUSB1` 等），并根据自己的环境调整。


## 关键包简要说明

### `wlkata_mirobot_description`

- 提供 Mirobot 机械臂的模型描述（URDF/Xacro）、关节配置与显示/仿真所需的 Launch 文件，可用于检查模型与坐标系是否正确。

### `wlkata_mirobot_moveit_config`

- 使用 MoveIt Setup Assistant 自动生成，包含运动学、控制器、规划场景等配置，提供 `demo.launch.py` 等入口，用于启动 MoveIt + RViz 演示场景。

### `wlkata_arm_move`

- ROS 2 Python 包，核心节点 `mirobot_moveit_move`：接收 MoveIt 规划出的轨迹，将其转换为符合 WLKATA 控制器的 G-code，通过串口发送给实际机械臂，并处理返回信息。

### `wlkatapython`

- 独立 Python 包，可单独 `pip install` 使用，支持 Mirobot、E4、MT4、MS4220 等多种设备，支持 RS485 与 UART 通信；其 `README.md` 中包含大量示例与接线图，本工程中的 ROS 2 控制节点直接复用了其中的通信逻辑。

