# 세 노트북 연결과 앱 없는 시험

최신 미로봇 어댑터가 공개된 뒤의 시작 순서는 [최신 세 브랜치 연결](first_connection.md)을 따른다.
아래 도메인 179 절차는 서버의 로봇 **대역** 시험용이다. 실제 미로봇 어댑터는 도메인 7을 필수로 검사한다.

## 구현과 확인 범위

서버의 `tools/picking_server.py`가 기존 HTTP 장치 API를 토픽 규약 v1로 변환한다.
`DEVICE_TRANSPORT=topics`에서는 서버가 장치 결과를 기다린다. `SIMULATION=true`여도
기존 자동 완료를 하지 않으므로 실제 토픽 왕복을 시험할 수 있다. 기본 `http` 모드는
이전과 같고 기존 `.env`는 수정하지 않았다.

- 서버: FastAPI + `picking_server.py` + 메뉴형 `test_client.py`.
- 카메라·모터: camera 브랜치의 `picking_camera_motor` 패키지.
  최신 확인 커밋은 `a61b5945f213b1ab9871a32d298b62f3f60d59fe`.
- 미로봇: `94ff136d10d797f8bedd7c1a98efae58b6054327`의 picking_mirobot_adapter,
  YAML 좌표 형식, 실행 옵션과 엄격한 네 필드 robot payload 검사를 확인했다.
  실제 노트북 LAN·장치 구동 검증은 별도다. 임의의 JSON 좌표 파일로 교체하지 않는다.
- `picking_sim_robot.py`는 서버 검증용 **시뮬레이션 전용 대역**이다.
  ROS Action, 시리얼, 흡착컵, 팔을 호출하지 않는다. 실제 어댑터와 함께 실행하지 않는다.

알려진 미로봇 작업 폴더는 `~/ros2_ws`, 브랜치는 `mirobot`이다.
IP·SSH 연결과 카메라 노트북의
주소·폴더는 미확인이다. `poses.yaml`은 시뮬레이션 설정이며
`robot_poses.example.yaml`도 대역 예시다. 검증된 실물 설정·좌표로 간주하지 않는다.

## 먼저 장비 없이 실행

현재 운전용 서버/DB와 분리한 포트 18000, DB, 도메인 179를 사용한다.
서로 다른 노트북에서 시험할 때는 아래 도메인 설정을 세 곳에서 동일하게 적용한다.
한 컴퓨터에서만 격리 시험하려면 `ROS_LOCALHOST_ONLY=1`로 바꾼다.

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=179
export ROS_LOCALHOST_ONLY=0
```

### 서버 노트북, 터미널 1 — 주문 서버

```bash
cd /home/chan/server
mkdir -p /tmp/picking-simulation
set -a
source config/picking.simulation.env.example
set +a
.venv/bin/python tools/run_server.py --port 18000
```

파일의 공개 예시 키는 격리된 모의 시험에만 쓴다. 실물 서버의 키나 `.env`를 바꾸지 않는다.
`/health`에서 `mode=simulation`, `device_transport=topics`, `worker_alive=true`,
`fault=null`을 확인한다. 기존 자동 모의 모드에는 `device_transport` 필드가 없다.

### 서버 노트북, 터미널 2 — ROS2 변환 노드

공통 ROS 설정 후 실행한다. 이 노드는 서버 노트북의 로컬 HTTP API만 조회한다.

```bash
cd /home/chan/server
/usr/bin/python3 tools/picking_server.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example \
  --mapping config/picking_items.example.json \
  --journal /tmp/picking-simulation/relay.sqlite3
```

### 카메라·모터 노트북 — 기존 패키지의 simulation 노드

실제 camera 저장소 폴더에서 패키지를 빌드하고 공통 ROS 설정을 적용한다.

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths picking_camera_motor --packages-select picking_camera_motor
source install/setup.bash
export ROS_DOMAIN_ID=179 ROS_LOCALHOST_ONLY=0
ros2 run picking_camera_motor camera_motor_node --ros-args \
  -p simulation:=true \
  -p database:=/tmp/picking-simulation/camera.sqlite3 \
  -p local_control_file:=/tmp/picking-simulation/camera.prepare
```

simulation 노드는 USB와 카메라를 열지 않는다. 기본 items.yaml은
`ITEM-001/A01→1, ITEM-002/A02→2, ITEM-003/A03→3, ITEM-004/A04→4`이며 서버의 예시와 같다.
매핑을 바꿀 때는 양쪽 모두 같은 내용으로 바꾼다. 서버 JSON은 YAML로도 유효하므로
카메라의 `mapping_file` 파라미터에 동일 JSON 파일을 전달할 수 있다.

### 로봇 대역 — 세 번째 노트북 또는 서버의 별도 터미널

서버 브랜치의 `tools` 폴더를 사용할 수 있는 폴더에서 공통 ROS 설정 후 실행한다.
시뮬레이션 대역만 사용할 때는 실제 미로봇 workspace를 빌드할 필요가 없다.

```bash
/usr/bin/python3 tools/picking_sim_robot.py \
  --journal /tmp/picking-simulation/robot.sqlite3
```

이 대역과 기존 미로봇 어댑터를 동시에 실행하면 동일 robot 장치가 둘로 발견되어
서버가 명령을 시작하지 않는다. 실제 팔 제어 프로그램은 이 시험에 필요 없다.

### 서버 노트북, 터미널 3 — 조회만 하고 주문 시험

공통 ROS 설정 후 먼저 점검한다. 이 명령은 토픽을 발행하거나 장치 명령을 가져오지 않는다.

```bash
cd /home/chan/server
/usr/bin/python3 tools/picking_check.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
```

세 장치가 `ready`, `mode=simulation`으로 보여야 한다. 그다음 앱 대신 실행한다.

```bash
env -u API_KEY python3 tools/test_client.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
```

9번 → 주문자 입력 → A=1, 나머지 0 → s → 시작 y.
`motor → camera → robot → waiting_box` 순서를 확인한다. 대역 시험의 상자 준비 질문에
y를 입력하면 completed가 된다. 수량 시험 A=2, D=1은
`motor, camera, robot, robot, motor, camera, robot`의 명령 7개가 나와야 한다.

추가 로그는 다음으로 확인한다.

```bash
env -u API_KEY python3 tools/signal_monitor.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
ros2 topic echo /picking/device_status std_msgs/msg/String
```

모니터를 계속 실행하는 터미널과 토픽 echo 터미널은 별도로 연다.

## 기존 미로봇 어댑터와 맞출 내용

기존 어댑터의 README·YAML 예시를 확인한 후 그 어댑터의 시뮬레이션 실행 명령을 사용한다.
대역을 종료하고 기존 어댑터 하나만 실행한다. 서버와 맞아야 할 사항은 다음과 같다.

- robot 명령 `/picking/robot/command`, `action=pick_and_place`, quantity는 정확히 1.
- JSON String 규약 v1과 RELIABLE/VOLATILE/depth 10.
- `/picking/server_heartbeat` 수신 및 `/picking/result_ack` 처리.
- 상태 `device_id=mirobot_laptop`, `device=robot`, `mode=simulation|real`.
- 결과의 command_id/order_id/server_session_id/mode를 원래 명령과 동일하게 반환.
- result 전체와 event_id를 영속 저장하고 ack까지 재전송. 같은 ID로 재이송하지 않음.
- YAML 좌표 로더 및 하드웨어 모드 확인은 기존 어댑터의 검증 절차를 유지.

## 결과 유실·중단·복구

서버 relay는 실행 전에 명령 JSON을 SQLite에 저장하며 같은 명령을 재발행할 때
시각·내용을 바꾸지 않는다. 결과를 저장한 뒤 HTTP 완료 API에 반영하고 ack를 발행한다.
HTTP 응답 유실은 저장된 결과만 재보고한다. 결과 재전송 중에는 다음 명령을 잠시 지연한다.

이전 프로세스의 실행 중 명령은 재시작 시 결과 불명확 실패로 보고하고 재실행하지 않는다.
장치 fault·세션 변경·상태 단절·명령 만료·서버 오류는 후속 명령을 막고 취소를 요청한다.
HTTP 연결이 끊기면 heartbeat도 중단한다. 취소·시간 초과는 물리 정지 확인이 아니다.
서버와 장치 시각 차이가 1초를 넘으면 명령을 시작하지 않는다.

HTTP 404/409로 완료 결과가 거절되면 relay journal에 거절 상태를 기록하고 fault를 유지한다.
해당 결과 이벤트에 대한 ack는 **기록했다는 뜻**이며 출고 성공을 뜻하지 않는다.
기록 파일을 지우거나 새 경로로 바꿔 오류/중복 방지를 우회하지 않는다.

실물 복구는 물품·기구 상태를 먼저 확인하고 각 장치 담당 절차로 준비한다.
서버 `/system/reset`은 실제 정지나 홈밍이 아니며, 확인 후 앱 키로 명시 호출한다.
relay를 한 번 정상 실행해 저장된 결과 보고를 끝내고 종료한 뒤, 기존 journal을 유지한 채
동일 실행 명령에 `--recover`를 추가하면 운영자가 확인한 relay fault를 해제할 수 있다.
미보고 기록이 남아 있거나 HTTP fault가 있으면 recover가 거절된다.
장치 어댑터의 fault 해제는 장치 자체 복구 절차로 별도 수행한다.

## 실물 전환 전에 남은 현장 작업

현재 작업에서는 실물 모드, 홈밍, 모터 이동, 팔 동작을 실행하지 않았다.
세 노트북 LAN/SSH, 기존 미로봇 어댑터의 실제 실행 옵션 및 YAML 형식,
실측 품목↔마커 배치·이동 방향·이동 한계, 정렬과 집기 위치의 보정,
실물용 로봇 설정·좌표·작업 영역·이송 시간 확인이 필요하다.
기본 `poses.yaml`과 `robot_poses.example.yaml`은 실물 좌표로 사용하지 않는다.

확인 후에만 기존 장치 어댑터들을 실제 모드로 준비하고, 실제 운전용 서버에
`SIMULATION=false`, `DEVICE_TRANSPORT=topics`를 적용하며 relay에는 `--mode real`을 지정한다.
실물 도메인은 공통 설정 `ROS_DOMAIN_ID=7`, `ROS_LOCALHOST_ONLY=0`을 사용한다.
기존 HTTP 미로봇 브리지·마커 자동 로봇 예제·별도 테스트 발행기를 함께 실행하지 않는다.

## 자동 검증

2026-11-16 검증: 일반 테스트 **91 passed, 2 skipped**. 별도 DDS 통합 시험 **1 passed**.
조회 전용 점검 성공, A 2개+D 1개 주문의 명령 7개와 상자 확인 후 완료,
같은 접수 ID 재전송 시 추가 주문이 생기지 않는 것을 확인했다.
실제 카메라 소스의 simulation 코드와 로봇 프로토콜 대역을 사용한 단일 컴퓨터 시험이다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
```

추가 DDS 시험은 source된 Humble 환경과 camera 패키지 소스가 필요하다.
`PICKING_CAMERA_SOURCE`는 그 안에 `picking_camera_motor/node.py`가 있는 폴더다
(일반 checkout에서는 저장소의 `picking_camera_motor` 폴더).

```bash
source /opt/ros/humble/setup.bash
PICKING_ROS_INTEGRATION=1 PICKING_CAMERA_SOURCE=/실제/camera/picking_camera_motor \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q tests/test_picking_topics_ros.py
```

이 시험은 도메인 179 / LOCALHOST_ONLY=1 / 별도 HTTP 포트·DB를 강제한다.
카메라의 실제 simulation 코드, 서버 relay, 로봇 프로토콜 대역으로 주문을 왕복한다.
로봇의 실제 어댑터·PickPlace 액션·서로 다른 노트북 LAN·실제 장비 시험과 구분한다.
