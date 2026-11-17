# 서버 노트북

소스 브랜치 `server`, 기본 폴더 `~/server`. [공통 준비](common.md), [통합 순서](operations.md)를 먼저 읽는다.

## 책임과 다른 노트북을 기다리는 조건

- 주문 접수·시작·상자 확인 HTTP API와 SQLite를 관리한다.
- FastAPI는 HTTP를 담당한다. `tools/picking_server.py` 중계가 같은 노트북의 HTTP와 노트북 사이 ROS2 토픽을 연결한다.
- 통합 시험의 heartbeat, 장치 명령, result_ack는 이 중계 하나가 발행한다.
- motor/camera/robot 모두 최근 상태가 있고 simulation/ready일 때까지 신규 주문을 시작하지 않는다.
- robot ready만으로 액션 서버의 실행·status 성공을 단정하지 않는다. `/mirobot/status`도 확인한다.
- 명령 완료는 accepted나 화면 메시지가 아니라 장치 result, 서버 반영, 주문 상태로 판단한다.

## 1. 준비

기존 서비스가 실제 장치를 제어 중인지 먼저 확인한다. 시험 포트는 18000이고 실제 운용 DB와 분리한다. 모의/실물 중계가 같은 ROS 도메인에서 동시에 명령을 발행하면 안 된다.

~~~bash
cd ~/server
git status --short --branch
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
~~~

ROS 실행용 시스템 Python에 필요한 패키지:

~~~bash
sudo apt install ros-humble-rclpy ros-humble-std-msgs ros-humble-std-srvs
~~~

이미 정상 설치되어 있으면 재설치할 필요 없다. 이 준비만 끝난 상태는 보고 분류 `LOCAL_PREPARED`다.

## 2. 서버 터미널 1: FastAPI

~~~bash
cd ~/server
mkdir -p /tmp/picking-simulation
set -a
source config/picking.simulation.env.example
set +a
.venv/bin/python tools/run_server.py --port 18000
~~~

`source`를 생략하지 않는다. `tools/run_server.py`는 설정 파일을 자동으로 읽어주는 도구가 아니다. 기본 `.env`를 수정할 필요는 없다.

설정 파일의 핵심 값:

| 항목 | 값 |
|---|---|
| SIMULATION | true |
| DEVICE_TRANSPORT | topics |
| DB_PATH | /tmp/picking-simulation/warehouse.sqlite3 |
| COMMAND_TIMEOUT | 60 |
| CLEAR_ORDERS_ON_START | false |
| REQUIRE_BOX_CONFIRMATION | true |

시험 키는 이 파일에 있는 공개 simulation 전용 값이다. `DEVICE_TRANSPORT=topics`가 아니면 장치 연결 없이 서버 내부 모의 처리로 끝날 수 있으므로 통합 성공으로 간주하지 않는다.

터미널을 계속 유지한다. 포트가 사용 중이면 기존 소유 프로세스부터 확인하고 다른 작업을 임의 종료하지 않는다.

## 3. 서버 터미널 2: HTTP↔ROS2 중계

새 터미널에서:

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
cd ~/server
env -u DEVICE_KEY /usr/bin/python3 tools/picking_server.py \
  --mode simulation \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example \
  --mapping config/picking_items.example.json \
  --journal /tmp/picking-simulation/relay.sqlite3
~~~

HTTP용 가상환경을 activate하지 않는다. 127.0.0.1은 같은 서버 노트북의 FastAPI다. 다른 노트북 IP를 넣지 않는다.

이 터미널을 유지한다. 장치가 아직 없으면 `Waiting for ready motor/camera/robot...`가 정상이다. 정상 HTTP 연결 후 중계 heartbeat가 발행되어야 미로봇 어댑터의 heartbeat 대기가 풀린다.

## 4. 서버 터미널 3: 조회 전용 점검

새 터미널에서:

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
cd ~/server
env -u DEVICE_KEY /usr/bin/python3 tools/picking_check.py \
  --mode simulation \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
~~~

최대 약 10초 동안 실제 상태를 모은다. 다음을 모두 확인한다.

- HTTP mode=simulation, device_transport=topics, fault=null, worker_alive=true.
- motor/camera/robot 각각 mode=simulation, state=ready.
- heartbeat=fresh, 마지막에 `연결 점검 성공 (명령 전송 없음)`.

미로봇의 사전 조건도 조회한다.

~~~bash
ros2 service call /mirobot/status std_srvs/srv/Trigger "{}"
ros2 action list -t
~~~

status의 success=true, message의 mode=simulation, `/pick_place` 액션 존재를 확인한다. 준비되지 않으면 미로봇 담당의 로그·보고와 맞추고 주문을 시작하지 않는다. 서버에 미로봇 커스텀 액션 패키지를 별도로 빌드할 필요는 없다.

부가 조회:

~~~bash
ros2 topic info /picking/server_heartbeat --verbose
ros2 topic info /picking/robot/command --verbose
~~~

각 명령/heartbeat 토픽의 발행자가 중계 하나인지 확인한다. 다른 시험 서버가 보이면 신규 명령을 중지하고 소유자를 확인한다.

## 5. 모의 주문

사용자가 모의 시험 실행을 요청했고 위 조건이 충족되면 같은 조회 터미널에서:

~~~bash
cd ~/server
env -u API_KEY /usr/bin/python3 tools/test_client.py \
  --url http://127.0.0.1:18000 \
  --env-file config/picking.simulation.env.example
~~~

상호작용 입력: 9번 → 주문자 test-1 → A=1, B=0, C=0, D=0 → s → 시작 질문 y.

실제 화면의 질문과 상태를 확인하면서 입력한다. 고정 문자열을 예상과 다르게 진행 중인 메뉴에 무조건 밀어 넣지 않는다.

관찰할 순서:

~~~text
waiting → queued/running → motor 완료 → camera 완료 → robot 완료
→ waiting_box → 모의 상자 준비 확인 y → completed
~~~

미로봇은 기본 stub 방식이며 이 결과가 실물 집기 성공이나 액션 시퀀스 실행을 증명하지 않는다. 모의 시험 실행 요청이 있으면 simulation 상자 확인을 모의 확인으로 명시해서 처리할 수 있다. 실제 운용의 box-ready는 사람이 상자를 실제 교체한 뒤에만 한다.

1개 성공 후 A=2, D=1 시험의 예상 장치 명령은 motor,camera,robot,robot,motor,camera,robot 총 7개다. 실제 관찰하지 않았다면 미실행으로 적는다.

## 6. 대기·오류·보고

| 상황 | 작업 |
|---|---|
| HTTP 연결 실패 | FastAPI 실행·포트·중계 env-file/DEVICE_KEY 출처 확인. 키 원문 출력 금지 |
| heartbeat 없음 | 중계와 HTTP 건강 상태·중복 발행자·시각 확인 |
| motor/camera 없음 | 카메라 담당의 노드 로그와 LAN/DDS 확인 |
| robot initializing | heartbeat와 미로봇 시각 확인 |
| robot ready이나 status 실패 | 미로봇 액션 서버·설정 mode 확인 |
| payload fields mismatch | sources.json 기준 최신 서버 변환부 확인 |
| waiting_box | 상자 확인 단계. 실패로 분류하지 않음 |
| fault/unacknowledged | 추가 주문 금지. operations.md의 기록 보존·대조·복구 적용 |

완료 후 [보고 양식](status-template.md)으로 실제 주문 ID, 각 단계 결과, HTTP/ROS 준비 상태, 프로세스 유지 위치를 남긴다. 실제 키·고객 데이터는 포함하지 않는다.

## 앱 연결은 그 다음

세 노트북의 장치 통신은 ROS2다. 앱은 서버의 LAN IP와 HTTP 포트로 접근한다. 이 모의 서버라면 `http://서버LAN_IP:18000`이고 중계 내부 주소 127.0.0.1을 휴대폰에 넣지 않는다. 앱 접속을 추가할 때는 해당 서버 TCP 포트의 방화벽과 앱 키를 확인한다.

## 확인한 소스

- [서버 최초 연결 안내](https://github.com/Double-Click-Lab/auto_picking_project/blob/008e80aa05bd7d52680f7d70c5d9421b3e74289f/docs/first_connection.md)
- [중계 실행 옵션](https://github.com/Double-Click-Lab/auto_picking_project/blob/008e80aa05bd7d52680f7d70c5d9421b3e74289f/tools/picking_server.py)
- [읽기 전용 점검](https://github.com/Double-Click-Lab/auto_picking_project/blob/008e80aa05bd7d52680f7d70c5d9421b3e74289f/tools/picking_check.py)
