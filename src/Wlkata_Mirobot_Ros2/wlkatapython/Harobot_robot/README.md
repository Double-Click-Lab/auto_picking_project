# Harobot_UART 使用说明

## 简介

`Harobot_UART` 是基于 WLKATA UART 协议的 Harobot 机械臂串口控制类，支持机械臂的初始化、回零、坐标/角度控制、夹爪/气泵控制、速度设置、状态获取等常用操作。适用于 Harobot 机械臂的二次开发和自动化控制。

## 安装

1. 克隆或下载本 SDK 到本地。
2. 安装依赖（如未安装 pyserial）：

```bash
pip install pyserial
```

3. 将 SDK 目录添加到你的 Python 工程路径下。

## 快速开始

### 1. 导入 Harobot_UART

```python
from Harobot_robot.Harobot_UART import Harobot_UART
import serial
import time
```

### 2. 初始化串口与机械臂

```python
# 创建 Harobot_UART 实例
robot = Harobot_UART(block_flag=True, message_flag=True)

# 初始化串口（以 COM4 为例，波特率 115200）
ser = serial.Serial('COM4', 115200, timeout=1)
robot.init(ser, -1)
```

### 3. 基本操作示例

#### 机械臂重启与回零

```python
robot.restart()
robot.homing()  # 回零
```

#### 控制气泵

```python
robot.pump(1)  # 打开气泵
time.sleep(1)
robot.pump(2)  # 气泵半功率
time.sleep(1)
robot.pump(0)  # 关闭气泵
```

#### 控制夹爪

```python
robot.gripper(1)  # 打开夹爪
time.sleep(1)
robot.gripper(0)  # 关闭夹爪
```

#### 角度控制

```python
robot.writeangle(0, axle1=70)  # 绝对角度模式，1号轴转到70度
```

#### 笛卡尔坐标控制

```python
robot.writecoordinate(0, 0, x=220)  # 快速绝对运动到X=220
```

#### 回到零点

```python
robot.zero()
```

#### 获取状态

```python
state = robot.getState()
print("机械臂状态:", state)

angle1 = robot.getAngle(1)
print("1号轴角度:", angle1)

coord_x = robot.getcoordinate(1)
print("末端X坐标:", coord_x)
```

## 常用方法说明

| 方法名                | 说明                       | 示例                           |
|----------------------|----------------------------|--------------------------------|
| `init(p, adr)`       | 初始化串口和地址           | `robot.init(ser, -1)`          |
| `restart()`          | 重启机械臂                 | `robot.restart()`              |
| `homing(mode=8)`     | 机械臂回零                 | `robot.homing()`               |
| `pump(num)`          | 气泵控制 0关 1开 2半功率   | `robot.pump(1)`                |
| `gripper(num)`       | 夹爪控制 0关 1开 2半开     | `robot.gripper(1)`             |
| `writeangle(...)`    | 角度控制                   | `robot.writeangle(0, axle1=70)`|
| `writecoordinate(...)`| 笛卡尔坐标控制            | `robot.writecoordinate(0,0,220)`|
| `zero()`             | 回到零点                   | `robot.zero()`                 |
| `getState()`         | 获取机械臂状态             | `robot.getState()`             |
| `getAngle(num)`      | 获取指定轴角度             | `robot.getAngle(1)`            |
| `getcoordinate(num)` | 获取末端坐标               | `robot.getcoordinate(1)`       |

## 注意事项

- 使用前请确保串口参数正确，机械臂已连接电脑。
- 建议操作前先执行 `restart()` 和 `homing()`，保证机械臂状态正常。
- 控制指令建议加适当延时，避免指令堆积。

---

如需更多高级 GPIO 控制、文件运行等功能，请参考源码中的注释和接口说明。

如有问题欢迎反馈！ 