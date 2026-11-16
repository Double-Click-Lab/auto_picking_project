# 최신 세 브랜치로 처음 연결하기

확인한 장치 코드: camera `a61b5945f213b1ab9871a32d298b62f3f60d59fe`,
mirobot `94ff136d10d797f8bedd7c1a98efae58b6054327`.
서버는 로봇 payload를 최신 미로봇의 엄격한 네 필드 규약에 맞춘 수정까지 받아야 한다.

## 1. 세 노트북의 코드와 환경 준비

기존 제어 작업이 없는 상태에서 각 저장소의 변경사항을 보존하고 최신 브랜치를 받는다.
로컬 수정 때문에 switch/pull이 거절되면 강제 초기화하지 않는다.

| 노트북 | 폴더 | 브랜치 |
|---|---|---|
| 서버 | `/home/chan/server` | server |
| 미로봇 | `~/ros2_ws` | mirobot |
| 카메라·모터 | README 기준 `/home/ms/aruco_project`, 실제 경로 확인 | camera |

각 폴더에서 해당 브랜치로 `git switch 브랜치` 후 `git pull --ff-only origin 브랜치`를 한다.
서버의 최초 Python 설치는 다음과 같다.

```bash
cd /home/chan/server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

미로봇 노트북에서 빌드한다.

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
```

카메라 노트북의 실제 camera 저장소 폴더에서 빌드한다.

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths picking_camera_motor --packages-select picking_camera_motor
source install/setup.bash
```

ROS2 Humble이 세 노트북에 설치된 것을 전제로 한다. 카메라 README의 apt 의존성은
`python3-colcon-common-extensions python3-yaml python3-serial python3-opencv
ros-humble-rclpy ros-humble-std-msgs ros-humble-launch-ros`다.
세 노트북을 서로 통신 가능한 같은 LAN에 연결한다. 노트북 사이의 작업 통신은 ROS2이며
아래 HTTP 주소 127.0.0.1은 서버 노트북 안에서만 사용한다. SSH는 이 시험에 필수가 아니다.

**앞으로 ROS 명령을 실행하는 모든 새 터미널**에서 공통 설정을 적용한다.

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
```

미로봇은 여기에 `source ~/ros2_ws/install/setup.bash`, 카메라는 자신의
`source install/setup.bash`도 적용한다. 최신 미로봇 어댑터는 환경변수가 정확히 7/0이
아니면 시작을 거절한다. 예전 로봇 대역용 가이드의 도메인 179와 섞지 않는다.
도메인을 바꿨다면 조회 터미널에서 `ros2 daemon stop`으로 이전 검색 캐시를 종료한다.

세 곳에서 `timedatectl status`의 시간 동기화 상태를 확인한다. 필요하면
`sudo timedatectl set-ntp true`로 동기화하고 완료를 기다린다. 실제 시각 차이는 1초 이내여야 한다.
기존 HTTP bridge, camera_linear.py, camera_mirobot_sim.py, simulation_server,
picking_sim_robot.py, 별도 테스트 발행기와 중복 어댑터는 종료한다.
전체 시험에서는 서버 relay 하나가 heartbeat와 명령을 발행해야 한다.

## 2. 먼저 세 장치 노드 모두 simulation으로 연결

이 시험은 USB·카메라·팔을 구동하지 않는다. 서버 노트북 3개, 미로봇 노트북 2개,
카메라 노트북 1개, 총 6개 터미널을 사용한다.

### 서버 터미널 1 — FastAPI

```bash
cd /home/chan/server
mkdir -p /tmp/picking-simulation
set -a
source config/picking.simulation.env.example
set +a
.venv/bin/python tools/run_server.py --port 18000
```

이 터미널은 실행한 채 둔다. 모의 DB와 공개 예시 키를 사용하며 실제 `.env`는 수정하지 않는다.

### 서버 터미널 2 — HTTP↔ROS2 relay

공통 ROS 설정 후 실행한다.

```bash
cd /home/chan/server
env -u DEVICE_KEY /usr/bin/python3 tools/picking_server.py \
  --mode simulation --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example \
  --mapping config/picking_items.example.json \
  --journal /tmp/picking-simulation/relay.sqlite3
```

장치가 아직 없으면 Waiting 메시지가 정상이다. relay의 heartbeat가 먼저 시작되어야
미로봇 어댑터가 initializing에서 ready로 바뀐다.

### 카메라 터미널 1 — motor와 camera 담당 노드

공통 ROS 설정과 camera workspace의 setup 파일을 source한 뒤 실행한다.

```bash
ros2 run picking_camera_motor camera_motor_node --ros-args \
  -p simulation:=true \
  -p database:=/tmp/picking-simulation/camera.sqlite3 \
  -p local_control_file:=/tmp/picking-simulation/camera.prepare
```

기본 매핑은 ITEM-001/A01→1, ITEM-002/A02→2, ITEM-003/A03→3, ITEM-004/A04→4다.
서버의 picking_items.example.json과 동일해야 한다. simulation에서는 prepare 명령이 필요 없다.

### 미로봇 터미널 1 — 기존 PickPlace 액션 서버

공통 ROS 설정과 미로봇 workspace의 setup 파일을 source한 뒤 실행한다.

```bash
ros2 launch mirobot_pick_place pick_place.launch.py \
  config_file:=$HOME/ros2_ws/src/mirobot_pick_place/config/poses.yaml
```

현재 이 YAML은 simulation_mode=true, hardware_enable=false다.
로그에 Mode: SIMULATION을 확인한다. 이 단계에서 initialize를 호출하지 않는다.

### 미로봇 터미널 2 — 토픽 어댑터

공통 ROS 설정과 미로봇 workspace의 setup 파일을 source한 뒤 실행한다.

```bash
ros2 run picking_mirobot_adapter mirobot_topic_node --ros-args \
  -p mode:=simulation \
  -p simulation_action_backend:=false \
  -p controller_config:=$HOME/ros2_ws/src/mirobot_pick_place/config/poses.yaml \
  -p coordinate_file:=$HOME/ros2_ws/src/picking_mirobot_adapter/config/robot_poses.example.yaml \
  -p database_path:=/tmp/picking-simulation/mirobot.sqlite3
```

이 옵션은 액션 서버의 존재와 status를 확인한 뒤 대역 성공을 보고한다.
액션 Goal은 보내지 않는다. 예시 YAML은 이 움직임 없는 단계에만 쓴다.
서버의 picking_sim_robot.py를 추가 실행하지 않는다.

### 서버 터미널 3 — 준비 확인 후 주문 1개

공통 ROS 설정 후 실행한다.

```bash
cd /home/chan/server
env -u DEVICE_KEY /usr/bin/python3 tools/picking_check.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
```

HTTP mode=simulation, device_transport=topics, fault=null, worker_alive=true;
motor/camera/robot 모두 mode=simulation, state=ready;
ROS relay heartbeat=fresh와 연결 점검 성공을 확인한다.

```bash
env -u API_KEY python3 tools/test_client.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
```

9번 → 주문자 test-1 → A=1, B=C=D=0 → s → 시작 y.
motor→camera→robot→waiting_box 순서를 확인하고 모의 상자 확인 y를 입력하면 completed가 된다.
다음으로 A=2, D=1을 시험하면 motor,camera,robot,robot,motor,camera,robot의 명령 7개가 나와야 한다.
waiting_box는 실패가 아니라 작업자 상자 확인을 기다리는 상태다.

## 3. 실물 전환 순서

위 시험의 성공은 세 프로그램의 통신·순서·완료 보고 확인이다. 실제 집기 성공이 아니다.
검증된 실물 controller YAML, 실물 home/pick/place/approach_height YAML,
카메라 중앙↔집기 위치 보정 및 품목/마커 배치 확인이 먼저 필요하다.
예시 좌표의 플래그만 바꿔 실물 좌표로 만들지 않는다.

1. 모의 주문을 완료하고 모의 relay·장치 노드·액션 서버를 종료한다.
2. 서버는 실제 `.env`의 키와 DB를 사용하되 `SIMULATION=false`, `DEVICE_TRANSPORT=topics`,
   `CLEAR_ORDERS_ON_START=false`로 실행한다. 모의 키를 실제 운용에 사용하지 않는다.
   relay는 `--mode real`, 실제 `.env`, 검증한 품목/마커 JSON과 고정된 실물용 journal을 사용한다.
3. 카메라는 `simulation:=false`로 시작하고 실제 port/camera/pwm/제한시간을 지정한다.
   시작은 STOP handshake이며 자동 이동/홈밍은 없다. 실제로 중앙에 배치한 마커가 1이라면
   카메라 노트북에서 `printf 'prepare 1\n' > /tmp/picking_camera_motor.prepare`를 실행해
   STOPPED와 새 영상 정렬을 확인한다. 현재 마커가 다른 번호면 실제 번호를 쓴다.
4. 미로봇 액션 서버에는 검증한 하드웨어 controller YAML을 config_file로 지정한다.
   기존 절차에 따라 명시 초기화한다. `/mirobot/initialize`는 **실제 홈밍**이며,
   homing_enable 및 실제 이동 경로 확인이 필요하다.
   `/mirobot/status`가 success=true, mode=real인 것을 확인한다.
5. 미로봇 어댑터는 `mode:=real`, 액션 서버와 **같은 controller_config**,
   검증된 실물 YAML의 coordinate_file을 지정한다. 실물 좌표 YAML은
   simulation_only=false, coordinates_confirmed=true이고 home이 controller와 같아야 한다.
   실제 소요 시간에 맞춰 서버 COMMAND_TIMEOUT과 어댑터 action_timeout_sec를 함께 설정한다.
6. picking_check.py도 `--mode real`과 실제 URL/키 파일로 실행한다.
   세 장치 ready 및 별도 미로봇 status 확인 후 A 1개만 시험한다.
   실제 물품을 이송하고 사람이 상자를 교체한 뒤에만 box-ready를 확인한다.

실제 파일 경로·좌표·이동 범위가 확인되지 않았다면 3단계부터 진행하지 않는다.
실패·시간 초과·취소는 장비 정지를 뜻하지 않는다. 명령 재시작이나 journal 삭제로
복구를 우회하지 않고 각 장치와 서버의 복구 절차를 따른다.

## 첫 문제를 구분하는 방법

| 표시 | 먼저 볼 곳 |
|---|---|
| 미로봇 시작 시 set ROS_DOMAIN_ID=7 오류 | 실행 터미널의 7/0 설정과 workspace source |
| robot initializing | 서버 relay heartbeat와 시각 동기화 |
| motor/camera 상태가 안 보임 | camera 노드 오류 로그, LAN/DDS 및 도메인 |
| robot ready지만 주문에서 status 실패 | 미로봇 액션 서버 실행 여부, status의 success와 mode |
| fields mismatch / robot payload | 네 필드만 보내는 최신 서버 수정 반영 여부 |
| relay Waiting | 세 장치 ready·모드 일치, 중복 장치/발행기, 최근 상태 수신 |
| fault / unacknowledged result | 저장된 실패 결과·서버 로그와 장치 복구 절차. DB를 삭제하지 않음 |
| waiting_box | 작업자 상자 준비 확인 필요 |

이 문서 작성 시 최신 소스와 미로봇의 실제 명령 검증 함수까지 확인했다.
서버 명령을 최신 미로봇 validate_command에 전달해 수락을 확인했고,
서버 전체 테스트는 92 passed, 선택 실행 2 skipped였다.
새 노트북 3대의 LAN 왕복과 실제 장비 구동을 원격으로 수행한 것은 아니다.
