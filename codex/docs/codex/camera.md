# 카메라·리니어모터 노트북

소스 브랜치 `camera`, 기본 폴더 `~/aruco_project`. [공통 준비](common.md), [통합 순서](operations.md)를 먼저 읽는다.

## 책임

`camera_motor_node` 하나가 motor/camera 명령을 구독하고 각 장치의 상태와 결과를 발행한다. 로봇 명령이나 PickPlace 액션은 호출하지 않는다.

simulation=true에서는 USB·카메라를 열지 않는다. 기존 GUI 프로그램이나 별도 simulation_server와 함께 실행하지 않는다.

## 1. 준비와 빌드

공통 ROS 설정 후:

~~~bash
sudo apt install python3-yaml python3-serial python3-opencv ros-humble-launch-ros
cd ~/aruco_project
git status --short --branch
git rev-parse HEAD
colcon build --base-paths picking_camera_motor --packages-select picking_camera_motor
source install/setup.bash
~~~

저장소의 `.venv`, `.rosvenv`를 activate하지 않는다. 해당 환경 파일이 Git에 있어도 다른 노트북에서 재사용하는 설치 방식이 아니다.

## 2. 카메라 터미널 1: 노드 실행

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source ~/aruco_project/install/setup.bash
mkdir -p /tmp/picking-simulation
ros2 run picking_camera_motor camera_motor_node --ros-args \
  -p simulation:=true \
  -p database:=/tmp/picking-simulation/camera.sqlite3 \
  -p local_control_file:=/tmp/picking-simulation/camera.prepare
~~~

실행 터미널을 유지한다. 모의 시험에서는 `prepare` 파일을 작성할 필요가 없다.

기본 항목/보관함/마커 매핑:

| item_id | bin | marker_id |
|---|---|---|
| ITEM-001 | A01 | 1 |
| ITEM-002 | A02 | 2 |
| ITEM-003 | A03 | 3 |
| ITEM-004 | A04 | 4 |

`picking_camera_motor/config/items.yaml`과 서버의 `config/picking_items.example.json`이 같은 매핑이어야 한다. 서로 다르면 임의로 성공 처리하지 않고 실제 파일과 차이를 보고한다.

## 3. 서버를 기다리며 확인할 내용

조회용 터미널을 쓴다면 공통 ROS 설정과 overlay를 적용한 뒤:

~~~bash
source ~/aruco_project/install/setup.bash
ros2 topic echo /picking/device_status std_msgs/msg/String \
  --qos-reliability reliable --qos-durability volatile
~~~

다음 두 상태를 각각 확인한다.

- device_id=camera_motor_laptop, device=motor, mode=simulation, state=ready.
- device_id=camera_motor_laptop, device=camera, mode=simulation, state=ready.

echo는 Ctrl+C로 종료할 수 있고 노드 터미널은 유지한다. 다음으로:

~~~bash
ros2 topic echo /picking/server_heartbeat std_msgs/msg/String \
  --qos-reliability reliable --qos-durability volatile
~~~

카메라 노드는 서버 없이도 simulation ready를 발행할 수 있다. 따라서 ready만으로 서버 연결 성공을 선언하지 않는다. 실제 heartbeat와 서버 측 읽기 전용 점검 결과가 필요하다.

## 4. 명령 처리

서버가 motor 명령을 보내면 결과를 보고하고 ack를 기다린다. 그 후 서버가 camera 명령을 보내면 정렬 결과를 보고한다. 순서는 서버가 조정한다.

camera 명령에는 같은 서버 세션·주문·품목·bin·marker의 motor 성공이 필요하다. 독립적으로 camera 명령을 임의 발행해서 시험하지 않는다.

| 상황 | 조치 |
|---|---|
| ready이나 heartbeat 없음 | WAITING_SERVER 보고. 서버 FastAPI·중계와 네트워크 확인 |
| 항목/마커 불일치 | 서버와 camera 매핑 대조 |
| unacknowledged result | 서버 중계·ack 수신 확인. DB·기록 보존 |
| 같은 DB process lock | 기존 노드 소유자 확인. DB 경로를 바꿔 중복 실행하지 않음 |
| fault | 원인·명령 ID 기록 후 서버와 상태 대조 |

실물에서 fault를 해제하는 prepare 절차를 simulation 연결 장애의 만능 해결책으로 사용하지 않는다.

## 실물 전환 전 알아둘 점

현재 펌웨어는 115200 baud의 FORWARD/BACK/STOP/HB 기반이며 mm 단위 위치 제어가 아니다. ArUco 마커와 새 영상·STOPPED 응답을 이용한다. 실제 모터 방향, 마커 순서, 정지·정렬, 카메라 중앙과 집기 위치의 보정은 별도 검증 대상이다.

real 시작 시 자동 이동/홈밍 대신 STOP handshake와 수동 위치 준비가 필요하다. 사용자의 실물 시험 요청과 실제 설치 확인 전에는 전환하지 않는다. 상세 조건은 operations.md와 아래 소스를 따른다.

## 확인한 소스

- [카메라·모터 README](https://github.com/Double-Click-Lab/auto_picking_project/blob/a61b5945f213b1ab9871a32d298b62f3f60d59fe/picking_camera_motor/README.md)
- [노드 옵션·토픽](https://github.com/Double-Click-Lab/auto_picking_project/blob/a61b5945f213b1ab9871a32d298b62f3f60d59fe/picking_camera_motor/picking_camera_motor/node.py)
