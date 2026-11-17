# 미로봇 노트북

소스 브랜치 `mirobot`, 기본 폴더 `~/ros2_ws`. [공통 준비](common.md), [통합 순서](operations.md)를 먼저 읽는다.

## 책임

기존 `/pick_place` 액션 서버 하나와 `mirobot_topic_adapter` 하나를 실행한다. 어댑터가 서버의 robot 토픽 명령을 받아 처리하고 결과와 상태를 돌려준다. 모터·카메라는 제어하지 않는다.

이 문서의 기본값 `simulation_action_backend=false`는 액션 서버의 상태/존재를 검사한 뒤 stub 결과를 보고한다. 실제 ROS 액션 Goal, USB, 로봇 제어를 수행하지 않는다. 서버가 보내는 명령을 기다리는 통합 구성이다.

## 1. 준비와 빌드

공통 ROS 설정 후:

~~~bash
cd ~/ros2_ws
git status --short --branch
git rev-parse HEAD
~~~

rosdep 최초 사용 시에만:

~~~bash
sudo rosdep init
~~~

이미 초기화되어 있으면 반복하지 않는다. 이후:

~~~bash
rosdep update
rosdep install \
  --from-paths src/mirobot_pick_place_interfaces \
               src/mirobot_pick_place \
               src/picking_mirobot_adapter \
  --ignore-src --rosdistro humble -y
colcon build --symlink-install --packages-up-to picking_mirobot_adapter
source install/setup.bash
~~~

기존 MoveIt·데모 패키지 전체를 빌드하거나 자동 홈밍하는 기존 브리지를 띄울 필요는 없다.

`src/mirobot_pick_place/config/poses.yaml`의 simulation_mode=true, hardware_enable=false를 확인한다. 로컬 YAML이 이미 실물 설정이면 덮어쓰거나 그대로 시험에 사용하지 말고 별도 simulation YAML을 준비한다. 예시 설정을 실물 좌표로 해석하지 않는다.

## 2. 미로봇 터미널 1: 액션 서버

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source ~/ros2_ws/install/setup.bash
ros2 launch mirobot_pick_place pick_place.launch.py \
  config_file:=$HOME/ros2_ws/src/mirobot_pick_place/config/poses.yaml
~~~

별도 simulation YAML을 준비했다면 위 경로를 그 파일로 바꾸고 어댑터에도 같은 파일을 지정한다. 로그 Mode: SIMULATION을 확인하고 유지한다. 통신 시험을 위해 initialize를 호출하지 않는다.

## 3. 미로봇 터미널 2: 토픽 어댑터

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source ~/ros2_ws/install/setup.bash
mkdir -p /tmp/picking-simulation
ros2 run picking_mirobot_adapter mirobot_topic_node --ros-args \
  -p mode:=simulation \
  -p simulation_action_backend:=false \
  -p controller_config:=$HOME/ros2_ws/src/mirobot_pick_place/config/poses.yaml \
  -p coordinate_file:=$HOME/ros2_ws/src/picking_mirobot_adapter/config/robot_poses.example.yaml \
  -p database_path:=/tmp/picking-simulation/mirobot.sqlite3
~~~

시작 시 `waiting for server heartbeat`, state=initializing이면 서버 중계를 기다린다. 같은 어댑터를 다시 띄우거나 임의의 heartbeat를 발행하지 않는다. 서버 정상 heartbeat 수신 후 ready로 바뀌는지 확인한다.

## 4. 조회

두 프로그램은 계속 실행하고, 필요하면 별도 조회 터미널에 공통 ROS 설정과 overlay를 적용한다.

~~~bash
source ~/ros2_ws/install/setup.bash
ros2 service call /mirobot/status std_srvs/srv/Trigger "{}"
ros2 action list -t
ros2 topic echo /picking/server_heartbeat std_msgs/msg/String \
  --qos-reliability reliable --qos-durability volatile
~~~

status success=true, message mode=simulation과 `/pick_place` 존재를 확인한다. echo는 Ctrl+C로 종료하고 다음 조회를 할 수 있다.

~~~bash
ros2 topic echo /picking/device_status std_msgs/msg/String \
  --qos-reliability reliable --qos-durability volatile
~~~

`device_id=mirobot_laptop`, `device=robot`, `mode=simulation`, `state=ready`를 관찰한다. 다른 노트북의 motor/camera 상태가 보이는지도 기록하되, 상태 수신만으로 명령 왕복 완료를 주장하지 않는다.

서버가 주문을 발행하면 accepted/result가 서버로 돌아간다. 결과는 서버 ack까지 재전송될 수 있다. 재전송을 중복 집기나 자동 실패로 간주하지 않는다.

## 5. 대기와 오류

| 상황 | 기다리거나 확인할 내용 |
|---|---|
| 로컬 빌드 완료·서버 없음 | LOCAL_PREPARED 또는 WAITING_SERVER 보고 |
| heartbeat 대기 | 서버 FastAPI·중계 실행, 같은 LAN/7/0/RMW, 시각 동기화 |
| robot ready지만 주문 실패 | 액션 서버 존재·status success·컨트롤러 mode |
| previous result awaiting acknowledgement | 서버 중계·result_ack 수신·기존 결과 영속 기록. DB 삭제 금지 |
| fault | 원래 command/event ID와 원인 보존. 운영자 대조·복구 전 재실행 금지 |
| process lock 오류 | 기존 어댑터 소유 세션 확인. 다른 DB로 우회하지 않음 |

별도 `picking_sim_test_publisher`, `camera_mirobot_sim.py`, HTTP bridge, 두 번째 액션 서버/어댑터를 함께 실행하지 않는다. 서버의 세 장치 준비 점검을 기다린다.

실물 전환의 별도 조건과 SDK 경로는 operations.md를 따른다. 기본 예시 YAML은 stub 시험용이고 실물 좌표 검증 결과가 아니다.

## 확인한 소스

- [어댑터 README](https://github.com/Double-Click-Lab/auto_picking_project/blob/94ff136d10d797f8bedd7c1a98efae58b6054327/src/picking_mirobot_adapter/README.md)
- [어댑터 코드](https://github.com/Double-Click-Lab/auto_picking_project/blob/94ff136d10d797f8bedd7c1a98efae58b6054327/src/picking_mirobot_adapter/picking_mirobot_adapter/adapter_node.py)
- [기본 컨트롤러 설정](https://github.com/Double-Click-Lab/auto_picking_project/blob/94ff136d10d797f8bedd7c1a98efae58b6054327/src/mirobot_pick_place/config/poses.yaml)
