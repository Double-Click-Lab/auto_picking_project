# 카메라·리니어모터 노트북 (ROS2 Humble / 계약 v1)

## 현재 동작 변경 — 2026-11-18

서버 노드의 기본 `motion_algorithm`은 이제 `original`이다. `camera_linear.py`와
`legacy.py` 원본 파일은 수정하지 않았다. 아래의 이전 검증 기록과 수동 준비 전용
설명은 `verified` 방식의 설명이며, 현재 기본 방식과 구분한다.

`original`의 실제 동작:

- real 시작 시 STOP 연결 확인 후, 첫 새 영상으로 1번 마커를 확인한다. 중앙 박스
  밖이면 원본 `MotorController.home()`으로 BACK 이동하고, 1번 마커 중심이 박스에
  들어오면 STOP한다. 일치하는 STOPPED를 받은 뒤 ready가 된다. **노드 시작만으로
  실제 BACK 이동이 시작될 수 있다.** simulation에서는 장치를 열지 않는다.
- 서버 motor 명령은 원본 `start_target()`으로 현재보다 큰 번호에 FORWARD,
  작은 번호에 BACK을 보낸다. 목표 중심이 박스 안에 들어오면 STOP하고 일치하는
  STOPPED 응답으로 motor 성공을 기록한다. 이동 중 목표 미검출 2초 및 로컬 전체
  20초 제한은 motor 명령에 적용하지 않는다.
- 같은 번호는 이동하지 않는다. 서버에 잘못된 성공을 기록하지 않도록 현재의
  새 영상에서 해당 마커가 중앙인지 확인한다. 벗어나 있으면 수동 복구가 필요하다.
- 후속 camera 명령은 기존대로 같은 주문의 motor 성공을 요구하고, 새 영상에서
  가로 ±20px / 세로 ±60px 안에 0.5초 연속 정렬을 확인한다. camera 명령에는 기존
  marker_timeout/move_timeout이 적용된다. motor 성공만으로 주문 완료가 아니다.
- 명령의 server expiry, heartbeat 5초 단절, 서버 세션 변경, cancel, 시리얼/영상
  오류는 기존대로 STOP/실패 처리한다. 서버의 COMMAND_TIMEOUT이 전체 주문 명령의
  수행 시간을 제한하므로, 서버 연동이 무제한 대기를 뜻하지 않는다.
- 시작 시 기존 DB fault 또는 미확인 결과가 있으면 자동 탐색을 막는다. 기존
  기록·ack를 처리하고 실제 정지 및 위치 확인 후 수동 `prepare N`으로 복구한다.
  로컬 자동 1번 탐색 자체에는 원본처럼 이동 시간 제한이 없다. 정렬 대상이 보이지
  않거나 끝에 도달하면 운영자가 정지해야 한다.

`ros2 run ... --ros-args -p motion_algorithm:=original`로 명시할 수 있고,
이전 수동 준비·정지 후 motor 영상 검증 방식은 `motion_algorithm:=verified`로
선택한다. launch도 `motion_algorithm:=original|verified`를 지원한다.
실시간 표시(`display:=true`)는 중앙 박스·검출 중심·오차·모터 상태를 보여준다.
서버 토픽, UUID, SQLite 결과 보존/재전송/ack 규약과 실제 DB 경로는 바꾸지 않았다.

이번 변경은 자동 테스트와 빌드로 검증하며, 변경된 real 노드를 실행하거나
자동 홈밍하지 않았다. 단독 조작기와 real 서버 노드를 동시에 실행하지 않는다.

## 이전 구현·검증 기록

이 패키지는 motor와 camera만 담당한다. robot 토픽 발행이나 `/move_action`, `/pick_place` 호출은 없다. 기본 simulation이며 USB/카메라를 열지 않는다. 실장비 구동 시험과 세 노트북 LAN 통합 시험은 수행하지 않았다.

## 확인한 기존 파일과 보존

- 로컬 브랜치 `camera`, 기준 커밋 `fdb1f7655cf28e1094ec203e35edb88f77d5843f`. 시작 시 작업 트리 깨끗함.
- 루트 `camera_linear.py`를 실제로 읽었고 원격 camera 브랜치 원본과 `cmp`로 동일함을 확인했다. 원본은 수정하지 않았다. 재사용 스냅샷 `picking_camera_motor/legacy.py`는 원본과 동일하다.
- 지정 `arduino/sketch_nov12b_copy_20261112210620.ino`는 로컬에 없었다. 원격 camera 브랜치에서 받아 읽은 파일을 `vendor/`에 보존했다. 원격 URL: https://github.com/Double-Click-Lab/auto_picking_project/blob/camera/arduino/sketch_nov12b_copy_20261112210620.ino
- 펌웨어 확인: `FORWARD <speed> <token>`, `BACK <speed> <token>`, `STOP <token>`, `HB`; 응답 `MOVING <token>`, `STOPPED <token>`, 부팅 `READY`, 오류 `FAULT ...`. 115200 baud, HB 단절 1000ms 초과 시 출력 차단. 실제 속도/위치 피드백 및 mm 위치 제어는 없다.
- 기존 `MotorController`, ArUco DICT_4X4_50 검출기와 연결 STOP handshake를 재사용한다. 기존 자동 홈밍을 호출하지 않고, `start_target()`의 동일 current 조기 반환을 사용하지 않는다. 별도 상태기가 매 새 명령의 현재 영상을 확인한다. 기존 무제한 이동 대신 marker/전체 제한 시간을 적용한다.

## 설치와 빌드

Ubuntu 22.04 + ROS2 Humble, 시스템 Python 3.10 기준. 저장소의 기존 venv는 사용하지 않는다. 실제 어댑터를 쓰려면 OpenCV ArUco와 pyserial이 필요하다. 현재 시스템에는 OpenCV 4.5.4 ArUco가 있었고 pyserial은 없었다. simulation에는 두 모듈이 필요 없다.

```bash
cd /home/ms/aruco_project
sudo apt update
sudo apt install python3-colcon-common-extensions python3-pytest python3-yaml python3-serial python3-opencv ros-humble-rclpy ros-humble-std-msgs ros-humble-launch-ros
source /opt/ros/humble/setup.bash
colcon build --base-paths picking_camera_motor --packages-select picking_camera_motor
source install/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
ros2 launch picking_camera_motor camera_motor.launch.py
```

개별 노드 실행도 가능하다.

```bash
source /opt/ros/humble/setup.bash
source /home/ms/aruco_project/install/setup.bash
export ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0
ros2 run picking_camera_motor camera_motor_node
```

`config/params.yaml`에 camera=0, port=/dev/ttyACM0, baud=115200, pwm=100, display=false, 가로 ±20px/세로 ±60px, 안정 시간 0.5초, 목표 미검출 2초, 전체 동작 20초 기본값이 있다. YAML에서 수정하거나 `ros2 run ... --ros-args -p pwm:=100` 형태로 시작 시 전달한다. 고정 펌웨어와 다른 baud는 거절한다. mm 값은 유한성/범위만 검증하며 이동 제어에 환산하지 않는다.

`config/items.yaml`은 **실제 설치 검증이 필요한 예시**다. ITEM-001/A01→1, ITEM-002/A02→2, ITEM-003/A03→3, ITEM-004/A04→4. item/bin/marker 불일치는 실패다. 픽셀 허용 범위는 기존 영상 기준이며 실제 집기 정확도와 좌표 보정 검증을 대체하지 않는다.

## 실제 장치를 열지 않는 통신 점검

두 번째 터미널에서 다음을 실행하면 새 시각과 UUID로 simulation 명령을 만든다. 서버 대역은 1초 heartbeat, 동일 ID 재시도, motor 성공 및 ack 후 camera 명령, 결과 ack를 수행하며 robot 명령은 만들지 않는다. real 모드 명령 옵션은 제공하지 않는다.

```bash
source /opt/ros/humble/setup.bash
source /home/ms/aruco_project/install/setup.bash
export ROS_DOMAIN_ID=7 ROS_LOCALHOST_ONLY=0
ros2 run picking_camera_motor simulation_server
```

`PASS simulation motor -> camera; hardware unopened`와 motor/camera의 `mode=simulation, success=true`를 확인한다. 단순 토픽 echo가 완료 판단 근거가 아니다. 결과가 실패하거나 45초 안에 완료하지 못하면 테스트 발행기는 실패로 종료한다. 테스트 서버 종료 후 heartbeat가 사라지므로 활성 명령은 실패한다.

노트북 간 점검은 장치 노드와 서버 대역을 서로 다른 노트북에서 실행한다. 양쪽 모두 패키지를 빌드/source하고 DOMAIN=7/LOCALHOST_ONLY=0, 같은 LAN, 멀티캐스트/방화벽 및 NTP/chrony 시각 동기화를 확인한다. 실제 서버는 동시에 실행하지 않는다. 세 번째 미로봇 노트북은 아래 상태 토픽 관찰만 해도 되며, 이 시험은 로봇 이송 통합을 검증하지 않는다.

```bash
ros2 node list
ros2 topic info /picking/device_events -v
ros2 topic echo /picking/device_status std_msgs/msg/String --qos-reliability reliable --qos-durability volatile --qos-history keep_last --qos-depth 10
```

heartbeat에 찍힌 서버 시각과 로컬 수신 시각 차이가 1초를 초과하면 신규 동작 실패다. 네트워크 지연도 이 차이에 포함되므로 성공으로 우회하지 않는다. fresh heartbeat(5초 이내), 같은 서버 세션, 만료까지 2초 초과가 모두 필요하다.

## 토픽 및 계약 처리

아래 모든 토픽은 `std_msgs/msg/String`, data=UTF-8 JSON 객체, KEEP_LAST(depth=10), RELIABLE, VOLATILE이다. 커스텀 msg/service/action을 추가하지 않았다.

| 토픽 | 이 노드 역할 |
|---|---|
| /picking/motor/command | 구독: motor / move_to_bin |
| /picking/camera/command | 구독: camera / align_bin |
| /picking/server_heartbeat | 구독: 서버 세션/시각/수신 freshness |
| /picking/result_ack | 구독: 네 식별자 일치 + accepted=true |
| /picking/cancel | 구독: 활성 세션/명령/장치 일치 시 취소 |
| /picking/device_events | 발행: accepted(success=null), result(success=boolean) |
| /picking/device_status | 1초마다 motor/camera 각각 발행 |

원본 서버 세션/명령/주문 ID와 mode를 이벤트에 보존한다. detail은 500자 이하다. 상태의 장치 ID는 camera_motor_laptop, session_id는 프로세스 시작마다 새 UUID다. 다른 장치/잘못된 JSON/타입은 실행하지 않고 SQLite errors에 기록한다. 유효하지만 모드/시간/매핑/준비 조건을 만족하지 못하는 명령은 accepted 없이 영속 실패 result를 반환한다. 이 실패 결과도 서버가 ack해야 한다.

수락 전 SQLite WAL + synchronous=FULL로 정규화 JSON과 ID를 commit한다. 중복은 기존 accepted 또는 저장된 result만 재발행한다. 같은 ID/다른 내용은 errors에 기록하며 원래 기록을 바꾸지 않는다. 미확인 결과가 하나라도 있으면 신규 동작을 시작하지 않는다. 최종 결과 전체와 event_id를 저장해 1초마다 동일하게 발행한다. ack는 server_session_id/command_id/device/event_id 전부 일치하고 accepted가 JSON true일 때만 반영한다. accepted=true는 결과의 업무 성공을 뜻하지 않는다.

수락 때 `expires_at-time.time()-1`을 monotonic deadline으로 바꾼다. 시계가 뒤로 가도 제한 시간이 늘어나지 않는다. 시계가 앞으로 뛰어 만료가 확인돼도 중단한다. 서버 세션 변경/5초 단절/만료/취소/하드웨어 오류 시 STOP을 요청하고 실패 결과 및 fault를 남긴다. STOPPED는 출력 정지 응답이지 실제 기구 정지 보증이 아니다. 토픽 취소는 물리 비상정지가 아니다.

## 장치 소유·준비·복구

ROS 콜백은 큐에 넣고 즉시 반환하며, 타이머가 결과를 배출한다. 단일 소유 스레드가 프로토콜 상태, SQLite, 카메라 및 시리얼을 순차 처리한다. motor/camera 동작은 전체 한 번에 하나만 허용한다. DB 경로 기반 flock과 pyserial exclusive 잠금을 사용한다. 같은 장치에 다른 DB를 지정해 중복 노드를 띄우지 않는다. 기존 camera_linear.py GUI/터미널, 시리얼 모니터 및 다른 카메라 프로그램은 먼저 종료한다. 기존 프로그램에 새 잠금 규칙을 소급 적용하지는 않는다.

DB 기본 위치는 `~/.local/state/picking_camera_motor/state.sqlite3`. 시험용으로 `--ros-args -p database:=/tmp/my-picking-test/state.sqlite3`를 지정할 수 있다. 실제 운영 중에는 DB를 지우거나 경로를 바꾸지 않는다. 실행 중 강제 종료되었던 명령은 재시작 때 결과 불명확 실패로 기록하고 자동 재실행하지 않는다. fault는 ack 후에도 유지된다. 완료 결과는 재시작 후에도 원래 event_id 그대로 재전송한다. 재시작하면 과거 motor 성공의 정렬 허가는 잃으므로 새 motor 명령부터 필요하다.

실제 모드는 별도 사용자 지시 후에만 아래처럼 시작한다. **이번 작업에서 실행하지 않았다.** 시작 때 STOP handshake만 수행하고 자동 홈밍/이동은 하지 않는다. 실제 모드의 초기 상태는 initializing이다.

```bash
# 사용자 실장비 시험 지시 이후에만 실행
ros2 launch picking_camera_motor camera_motor.launch.py simulation:=false
```

명시적 로컬 준비 절차: 운영자가 전원/기구 정지와 카메라·시리얼 연결, 마커 설치/방향, 실제 원점/현재 위치를 확인한다. 안전한 수동 조작으로 해당 마커를 중앙에 배치한다. 모든 기존 결과를 서버가 기록·ack한 뒤 아래 로컬 파일 요청으로 해당 마커의 정지 응답 및 현재 영상 연속 0.5초 정렬을 확인한다. **자동 이동 홈밍 대신 수동 원점 배치 + 영상 준비를 제공한다.** 1이 아닌 현재 마커도 prepare 2/3/4로 확인 가능하다.

```bash
printf 'prepare 1\n' > /tmp/picking_camera_motor.prepare
```

로컬 요청은 ROS 토픽이 아니다. 시작 전에 남은 파일은 버린다. 준비 중에는 원래 current 값을 믿어 이동하지 않고 새 STOPPED와 현재 영상을 요구하며 준비 성공 후 ready가 된다. 실패 후에는 자동 홈밍/재시작하지 않는다. 원인을 해결하고 미확인 결과를 서버에서 처리·ack하고, 기구 정지를 직접 확인한 뒤 동일 준비 절차를 다시 수행한다. 통신/카메라가 복구되지 않으면 프로세스를 정상 종료 후 재시작하고 준비한다.

motor는 현재 영상 목표 마커→STOP 요청→일치 STOPPED→정지 후 새 영상 안정 시간을 모두 통과해야 성공한다. 같은 current의 새 명령도 다시 STOP/영상 확인하며, 같은 current인데 중앙에서 벗어나면 임의 이동하지 않고 실패·수동 복구한다. camera는 같은 서버 세션/주문/품목/bin/marker의 motor 성공과 정지 상태를 요구하며 새 영상을 재확인한다. 다른 주문의 motor 성공을 재사용하지 않는다.

카메라 드라이버의 read가 멈추면 ROS 콜백은 계속 수신하나 소유 스레드의 판정/상태 배출은 지연될 수 있다. 이때 시리얼 HB도 멈추므로 펌웨어의 1초 링크 감시가 출력을 차단하는 구조다. 드라이버 정지나 출력 차단으로 물리 정지를 보증할 수 없으므로 실제 설치에서 USB 단절/카메라 정지 및 기계적 정지 특성을 별도 검증해야 한다. 물리 비상정지는 독립 장치를 사용한다.

## 로컬 검증 결과와 남은 시험

2026-11-16 최종 대역 테스트: **32 passed**. Humble colcon build 성공. 실제 장치 구동 없음.

```bash
source /opt/ros/humble/setup.bash
cd /home/ms/aruco_project
PYTHONPATH="$PWD/picking_camera_motor:$PYTHONPATH" /usr/bin/python3 -m pytest -q picking_camera_motor/test
```

`test/test_core.py`는 시각·시리얼/카메라 오류 대역과 실제 SQLite를 사용한다. `test/test_backend.py`는 USB/VideoCapture를 열지 않고 생성한 DICT_4X4_50 영상, 실제 ArUco 검출기, 기존 MotorController 및 펌웨어 응답 대역으로 실제 어댑터를 검증한다. pyserial import만 대역으로 치환했다.

검증 항목: 정상 motor→camera, 같은 품목 연속 주문, 같은 current 새 영상 확인, 중복 accepted/result, 같은 ID 내용 변경, ack 유실 및 틀린 식별자, 완료 결과 재시작 보존, 실행 중 프로세스 소실의 결과 불명확 복구, 만료/monotonic 시계 역행, JSON/타입/다른 장치 오류, 시각 차이/매핑 불일치, heartbeat 단절/서버 세션 변경, 일치/불일치 cancel, 시리얼/카메라/Arduino FAULT, STOPPED 누락/지연, 마커 미검출, 전체 제한, 카메라 정렬 실패, 프로세스 잠금, unacked 신규 작업 차단.

실제 ROS2 두 프로세스의 simulation motor→camera/result/ack 로컬 왕복을 DOMAIN=7, LOCALHOST_ONLY=0으로 확인했다. 처음 샌드박스에서는 UDP socket 제한 오류가 있었으나 공유메모리 통신으로 성공했고, 승인 후 제한 밖에서 재실행하여 UDP 오류 없이 성공했다. 실제 실행 시각/UUID가 담긴 출력은 `test/ros_integration.log`, 생성 JSON 전체 예시는 `test/generated_examples.json`에 보존한다. 예시의 과거 timestamp/UUID를 새 명령으로 복사하지 않는다.

남은 시험: 서로 다른 노트북 간 DDS/방화벽/NTP, 미구현 서버의 HTTP→ROS 변환 및 결과 DB ack, 서버→motor→camera→robot 전체 순서, 실제 USB/Arduino/카메라 연결, 마커 순서/모터 방향/이동 한계, 실제 정지·정렬 정확도/집기 보정. 로컬 simulation 성공은 이 시험들의 완료를 뜻하지 않는다.

## 추가 운영 확인 (2026-11-16)

사용자가 전달한 실행 로그에서 다음을 확인했다.

- `camera_motor_node --ros-args -p simulation:=true` 실행 후 motor와 camera가 각각 `mode=simulation`, `state=ready`, `active_command_id=null` 상태를 주기적으로 발행했다.
- 같은 `/picking/device_status` 구독에서 `device_id=mirobot_laptop`, `device=robot` 상태도 수신했다. 해당 robot 상태는 `simulation / initializing`, detail은 `waiting for server heartbeat`였다. 미로봇 상태 수신은 확인했으나 다른 노트북과의 양방향 명령 왕복 및 전체 통합 완료를 뜻하지 않는다.
- 사용자가 실행한 simulation 서버 로그에서도 같은 주문의 motor → camera가 모두 `success=true`였으며 `PASS simulation motor -> camera; hardware unopened`로 완료했다.
- 사용자는 Arduino 업로드 및 시리얼 모니터의 `STOP 0` → `STOPPED 0` 확인 성공을 보고했다. 이는 사용자 보고이며, ROS 실제 모드의 카메라 정렬·모터 이동 성공을 검증한 결과는 아니다.

서버 통합 대기 시 simulation 장치 노드를 실행한 터미널을 계속 켜 둔다. `ros2 topic echo`로 상태만 보는 별도 터미널은 종료해도 된다. simulation은 Arduino USB·모터 전원·실제 카메라 없이 실행 가능하며, 실제 장비를 사용하기 전에는 시리얼 모니터를 닫아 포트 충돌을 방지한다.

현재 남은 통합 확인은 서버 heartbeat 수신, 서버 발행 명령의 motor → camera → robot 순차 처리, 결과 ack 및 주문 완료 반영이다. 실제 모터 이동·영상 정렬·집기 정확도 역시 아직 확인되지 않았다.
