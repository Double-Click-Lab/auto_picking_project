# Mirobot Pick & Place

ROS 2 Humble용 Action 기반 10단계 Pick & Place 패키지입니다.
기본 YAML은 시뮬레이션이며 실제 장비 제어는 비활성화되어 있습니다.
하드웨어 경로는 FakeSerial로 검증했고, 실물 홈밍·Z +2 mm 이동·빈 컵 펌프 ON/OFF와
기존 수직 접근 dryrun을 확인했습니다. 실제 물체 흡착·해제와 새 도구축 방향의 동작은
검증되지 않았습니다. 도구축 dryrun에서는 HOME 이후 ACK 시간 초과가 관찰되어 진단 중입니다.

## 구조

- `pick_place_action_client.py`: 좌표 Goal 전송, Feedback/Result 수신, 취소 요청
- `pick_place_action_server.py`: Goal 검증, 중복 작업 방지, 10단계 실행, 초기화 서비스
- `mirobot_controller.py`: SDK/Serial 연결, 응답 처리, 좌표·PWM 검증
- `configuration.py`: 장치 연결 전 설정 검증과 작업 영역 검사
- `pick_place_node.py`: 기존 YAML 기반 단독 실행 경로
- `mirobot_pick_place_interfaces/action/PickPlace.action`: Goal/Result/Feedback

Action 경로는 HOME만 YAML에서 읽습니다. PICK/PLACE는 Goal에서 받고,
기본 접근 위치는 각각 `z + approach_height`로 계산하며, 도구축 모드에서는
저장된 RPY 자세를 기준으로 접근 방향을 계산합니다.
단독 노드는 YAML의 다섯 위치를 모두 사용하고 시작 직후 시퀀스를 실행합니다.
하드웨어 운용에는 초기화와 복구 상태를 관리하는 Action 경로를 사용하세요.
MoveIt 패키지와 이 Action은 별도 경로입니다. 기존 MoveIt 브리지 노드는 시작 시
자동 홈밍하므로 진단 중 실행하지 않습니다.

## 빌드와 시뮬레이션

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select mirobot_pick_place_interfaces mirobot_pick_place --symlink-install
source install/setup.bash
ros2 launch mirobot_pick_place pick_place.launch.py
```

다른 터미널에서도 ROS와 workspace를 source한 뒤 실행합니다.

```bash
ros2 run mirobot_pick_place pick_place_action_client
ros2 run mirobot_pick_place pick_place_action_client --ros-args -p cancel_after_step:=4
```

좌표 예시(시뮬레이션용):

```bash
ros2 run mirobot_pick_place pick_place_action_client --ros-args \
  -p pick_x:=110.0 -p pick_z:=45.0 \
  -p place_x:=120.0 -p place_y:=85.0 -p place_z:=55.0 \
  -p approach_height:=25.0
```

별도 YAML은 `ros2 launch ... config_file:=/절대경로/설정.yaml`로 전달합니다.
이는 ROS parameter YAML 형식이 아니라 `config/poses.yaml`과 같은 일반 YAML입니다.
`simulation_mode` 등은 YAML 설정이며 ROS 파라미터로 직접 덮어쓰지 않습니다.

## 하드웨어 초기화와 동작 계약

실제 동작 시험은 사용자의 별도 승인이 필요합니다. 아래는 구현된 동작 설명입니다.
현재 기본 설정을 하드웨어로 변경하거나 실제 서비스를 호출하지 않았습니다.

장치 연결 전에 다음 조건을 검사합니다.

- YAML boolean `simulation_mode: false`, `hardware_enable: true`,
  `coordinates_confirmed: true`
- 실측·검토한 `workspace_bounds`: x/y/z/rx/ry/rz 각각 `[최소, 최대]`
- 유한한 HOME, PICK/PLACE 및 자동 계산한 접근 위치
- 양의 `motion_feedrate_mm_min` (프로그램 상한 3000)
- 유효한 제한시간, 대기시간, 좌표 허용 오차

기본 `workspace_bounds: null`은 하드웨어 시작을 거부합니다. 임의 작업 영역 예시는
제공하지 않습니다. HOME `(0, 0, 0)`도 현재 시뮬레이션용 예시입니다.
HOME 좌표 이동과 기계 원점 복귀(홈밍)는 서로 다릅니다.

서버 시작은 포트 연결과 상태 확인만 수행합니다. 연결 시 장치가 재부팅되고
Alarm이 나타날 수 있으며, DTR/RTS 설정으로 이를 방지한다고 보장하지 않습니다.
서버는 초기화를 요청받기 전 하드웨어 Goal을 거절합니다.

- `/mirobot/status` (`std_srvs/srv/Trigger`): 캐시된 상태, initialized,
  ready, busy, recovery_required 반환. 실시간 상태 조회 명령은 전송하지 않습니다.
- `/mirobot/initialize` (`std_srvs/srv/Trigger`): **실제 홈밍 동작**입니다.
  별도 `homing_enable: true`와 명시적 호출이 필요합니다. Goal/다른 초기화 중에는
  거절합니다. 펌프·밸브 PWM이 0인지 확인한 뒤 본체 USB에 `$H`를 한 번 전송하고,
  `ok`와 연속 Idle을 확인합니다. 저장 파일은 실행하지 않습니다.

승인 후 실제 운용은 서버 연결 → 명시적 초기화 → 같은 연결을 유지한 Goal 순서입니다.
외부 프로그램이나 확장 모듈 버튼으로 동시에 제어하지 않습니다.
초기화 실패, 재부팅·통신 오류, 취소 후에는 새 Goal을 차단합니다.
작업 영역에는 실제 홈밍 완료 위치도 포함되어야 합니다.
홈밍 자체의 경로는 작업 영역 검사로 제한되지 않습니다.

하드웨어 이동은 매 명령에 `G01`과 `F`를 포함합니다. 단위는 mm/min입니다.
기본 100은 검증 전 설정값이며 승인된 운용 속도를 의미하지 않습니다.
설정한 속도와 이동 거리를 고려해 `motion_timeout_sec`를 정해야 합니다.
명령마다 `ok`를 기다린 뒤 연속 Idle과 최종 보고 좌표(기본 1 mm / 1°),
펌프 PWM(ON=1000, OFF=0)을 검사합니다.

직접 Controller를 사용하는 기존 호출은 feedrate 미지정 시 G00 동작을 유지합니다.
하드웨어 Action과 단독 노드는 설정 검증을 통해 명시적 G01 feedrate를 요구합니다.

## 취소·오류·종료

- 시뮬레이션: 단계 사이에서 취소하고 가상 펌프를 OFF로 되돌립니다.
- 하드웨어: 현재 명령 완료를 기다리고 다음 단계를 시작하지 않습니다.
  펌프를 임의로 끄거나 HOME으로 이동하지 않습니다. `recovery_required`를 설정하고
  새 Goal을 거절합니다. 물체를 든 상태에서 홈밍을 시도하면 PWM 검사로 거절합니다.
- 실패: 원래 오류를 Result에 보존합니다. 시뮬레이션 정리 작업이 실패해도
  Goal을 abort하고 중복 Goal 잠금을 해제합니다.
- 시간 초과나 통신 실패는 로봇이 정지했다는 뜻이 아닙니다. Action 취소와 Ctrl+C도
  비상 정지가 아닙니다. 진행 중인 명령이 계속될 수 있습니다.
- 하드웨어 오류·취소 후 물체와 로봇 상태를 확인하고, 승인된 복구 절차를 별도로
  수행해야 합니다. 자동 재시도·재연결·흡착 해제·복귀는 하지 않습니다.

## 검증 범위와 남은 시험

작업 영역은 끝점 좌표를 제한할 뿐, 로봇 전체 링크·관절 한계·충돌·IK 도달 가능성을
증명하지 않습니다. G01 경로와 설치 환경은 실제 시험 전 별도 검토해야 합니다.
PWM 확인은 흡착 성공이나 실제 물체 해제를 확인하는 센서가 아닙니다.
펌프 OFF 시 해제용 밸브/블로우가 필요한지는 실제 공압 장치에서 검증해야 합니다.

프로토콜에 명령 ID가 없어 `ok`를 완전하게 상관시키지는 못합니다. 단일 명령 직렬화,
전송 전 대기 응답 처리, ACK 대기 중 조회 금지로 혼선을 줄입니다.
실제 펌웨어에서 ACK 시점과 지연 응답을 추가 검증해야 합니다.

실제 시험은 연결/Alarm 차단 → 승인된 작은 이동 → 펌프/해제 단독 시험 →
실측 좌표 설정 → 물체 없는 전체 경로 → 물체를 사용한 작업 순으로 진행합니다.
이번 개발에서는 실제 USB 포트를 열지 않습니다.

`first_motion_check.py`는 전체 Action 이전의 단일 동작 시험 전용 도구입니다.
기본 실행은 계획 출력만 하며 포트를 열지 않습니다. `--execute`를 지정하면 실제로
홈밍 1회 후 기존 로그의 홈밍 보고 좌표와 0.5 mm/° 이내인지 확인하고, 같은 연결에서
자세·X/Y를 유지하여 Z +2 mm를 F100으로 한 번 명령합니다. 자동 복귀·펌프·파일 실행은
없습니다. 홈밍은 여러 축의 큰 움직임을 포함하므로 작은 이동 승인과 함께 별도 검토가
필요합니다. 이 도구는 workspace 검증을 대체하거나 일반 좌표 이동에 사용하지 않습니다.
실제 실행은 사용자가 이 구체적인 시험 내용을 승인한 뒤에만 안내합니다.

`pump_check.py`는 **빈 흡착컵**을 위한 별도 시험입니다. 기본 실행은 계획만 출력하며
포트를 열지 않습니다. 승인 후 `--execute`로 실행하면 홈밍 1회 → 홈밍 보고 좌표와
Idle 확인 → 펌프 ON 확인 → 2초 유지 → OFF 확인 순서입니다. 홈밍 후에는 좌표 이동,
블로우(`M3 S500`), 저장 파일 실행을 하지 않습니다. ON/OFF 시 좌표와 PWM을 출력합니다.
물체를 붙이거나 들어 올리는 데 사용하지 마세요. 중단 시에도 정상 준비 상태가 유지될
때만 OFF를 한 번 시도하며, 오류·통신 실패로 OFF를 확인하지 못하면 펌프가 동작 중일 수
있다고 보고합니다. 장비 소리와 공압 동작은 사용자 관찰로 별도 확인해야 합니다.
OFF가 물체를 확실히 해제한다는 뜻은 아닙니다. 실제 해제·필요 시 블로우 시험은
이 ON/OFF 확인 다음에 별도로 진행합니다.

## 터미널에서 좌표 설정과 실행

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
python3 -m mirobot_pick_place.teach_terminal --execute
```

`--execute`가 없으면 시뮬레이션입니다. 실제 실행은 별도 승인을 받은 뒤 진행합니다.
실행 자체로 홈밍하거나 이동하지 않습니다. `home`을 직접 입력하면 기계 원점 복귀를
수행하고, 이후 보고 위치를 이 세션의 기본 복귀 HOME으로 기억합니다.
`save home`으로 복귀 위치를 명시적으로 바꿀 수도 있습니다.

명령은 `mirobot>`에서 한 줄씩 입력합니다.

| 명령 | 동작 |
|---|---|
| `speed 200` / `speed` | 이동 속도 설정 / 확인. 기본 200, 코드상 허용 1~3000 mm/min |
| `x+50`, `y-10`, `z+5` | 축 방향으로 지정한 거리 이동. 1~100 mm |
| `rx+5`, `ry-10`, `rz+1` | 해당 자세각을 지정한 만큼 변경. 1~90° |
| `x 10`, `ry -5` | 위 단축 명령과 같은 상대 이동 표기 |
| `goto X Y Z` | 절대 XYZ 지정, 현재 위치에서 총 거리 50 mm 이하 |
| `status` | 현재 좌표 조회. 작업 중에는 캐시만 표시 |
| `save pick`, `save place` | 접촉 위치와 자세 저장. 펌프는 작동하지 않음 |
| `load /경로/파일.yaml` | 이전 좌표 읽기. 홈밍·이동 없음 |
| `approach 10` | 자동 접근 간격 설정. 기본 10 mm |
| `tool_axis on` / `tool_axis off` | 자세각 기준 도구축 접근 / 기존 수직 Z 접근. 기본 OFF |
| `default` | speed 3000, tool_axis ON, 접근 40 mm 및 기본 PICK/PLACE 좌표 설정. dryrun 생략 가능 |
| `plan` | 실행 순서와 모든 목표 좌표 표시. 이동 없음 |
| `dryrun` | **팔을 실제로 움직이며** 전체 경로 확인. 펌프는 생략 |
| `run` | 기본은 dryrun 검증을 요구. `default` 설정 후에는 저장 PICK/PLACE로 바로 실행 |
| `cancel` | 현재 명령 완료 후 후속 단계 취소. 비상 정지가 아님 |
| `quit` | 작업이 없을 때 연결 종료. 자동 복귀 없음 |

터미널의 기본 이동은 G01 F200으로 이전 F100의 두 배 feedrate를 지정합니다.
`speed 100`, `speed 200`, `speed 400`처럼 다음 수동 이동과 자동 시퀀스의 속도를
바꿀 수 있습니다. 설정 명령 자체는 이동이나 Serial 명령을 전송하지 않습니다.
실행 중에는 변경할 수 없고, 변경 후에는 같은 속도로 dryrun을 다시 완료해야 합니다.
`default`는 speed 3000, tool_axis ON, 접근거리 40 mm와 아래 기본 PICK/PLACE 좌표를
설정하고 `run`에서 dryrun 검사를 생략합니다. `plan`은 선택사항입니다. 실행 전 HOME
위치와 아래 좌표가 현재 작업물에 맞는지 확인하세요. 기본 설정이나 좌표를 바꾸면 직접
실행 허용이 해제되어 다시 dryrun이 필요합니다.

```text
PICK  = (279.674, 9.985, 100.666, 0, -30.009, 0)
PLACE = (-1.278, -199.965, 77.093, 0, 0, 0)
```
`default`는 실제 실행을 안전 검증하지 않습니다. `run`은 즉시 팔과 펌프를 작동시키므로,
tool-axis 방향과 전체 경로를 이미 확인한 경우에만 사용하세요.
좌표를 저장할 때 현재 속도도 함께 저장합니다. load는 좌표와 접근 높이만 읽으며 현재
속도는 유지하므로 필요한 경우 speed로 별도 지정합니다. 코드의 1~3000 범위는 실제
설치 환경에서의 안전 속도를 보장하는 값이 아닙니다. 홈밍 속도는 이 명령으로 바뀌지
않습니다. 매우 낮은 속도에서 긴 거리를 이동하면 180초 제한시간을 초과할 수 있으며,
시간 초과는 장비 정지를 의미하지 않습니다.
실제 소요 시간은 가감속·관절 제한·자세 변화에 따라 정확히 절반이 되지는 않습니다.
F 값은 자세 변경 시 200°/분을 보장하는 별도 각속도 설정이 아닙니다.
수동 이동 화면에는 이동 후 현재 보고 좌표만 표시하고 목표 좌표는 출력하지 않습니다.
표시 변경과 관계없이 이동 완료 검사와 E118 등 펌웨어 오류 처리는 유지합니다.
Rx/Ry/Rz는 RPY 자세값이며 개별 관절 각도나 반드시 도구 로컬 축 회전은 아닙니다.
흡착컵 TCP를 교정하지 않았으므로 보고 XYZ가 같아도 실제 컵 끝은 움직일 수 있습니다.

일반적인 순서:

1. `home`으로 홈밍하고 복귀 위치를 기억합니다.
2. 물체 표면에 흡착컵이 닿을 PICK 위치로 조절한 뒤 `save pick`을 입력합니다.
3. 물체를 놓을 PLACE 위치로 조절하고 `save place`를 입력합니다.
4. `plan`으로 HOME, 접근점, PICK/PLACE 위치·자세를 확인합니다.
5. `dryrun`으로 펌프 없이 전체 경로를 한 번 확인합니다.
6. 완료되어 HOME으로 돌아온 뒤, 승인된 실물 시험에서 `run`을 입력합니다.

기본 `tool_axis off`에서는 접촉 위치의 Z + approach 값으로 접근점을 만들며,
X/Y/자세는 유지합니다. 직접 저장한 `pick_approach`, `place_approach`가 있으면 우선
사용합니다. `tool_axis on`에서는 직접 저장한 접근점 대신 PICK·PLACE 각각의 자세로
도구축 접근점을 계산하고, 접촉점↔접근점에서 같은 Rx/Ry/Rz를 유지합니다.
이때 approach는 수직 높이가 아니라 경로를 따라 이동하는 거리(mm)입니다.
각 접촉점과 접근점은 G01 직선으로 연결됩니다.

도구축 계산은 표준 RPY의 `Rz(yaw) Ry(pitch) Rx(roll)`을 사용합니다. RPY=0에서
아래를 향한 컵의 후퇴 방향을 local +Z로 정의한 소프트웨어/장착 가정입니다.
제조사 문서의 RPY 표기는 확인했지만, 실제 장착축·펌웨어 방향 일치는 아직 검증하지
않았습니다. 물체 없이 작은 간격의 경로로 방향을 먼저 확인해야 합니다.
예를 들어 Rx=0, Ry=-30°, Rz=0에서 40 mm 후퇴는 X 약 -20 mm, Z 약 +34.64 mm로
계산됩니다. OFF로 바꾸면 기존 수직 +40 mm로 돌아갑니다.
도구축이 수평이거나 아래를 향해 계산되면 상승 모드 실행을 거부합니다.

모드 변경 자체는 이동·펌프 명령을 보내지 않습니다. 모드를 바꾸면 dryrun을 다시
완료해야 하며 실행 중에는 변경할 수 없습니다. 모드도 좌표 파일에 저장하고 load로
복원하며, 이전 파일에 모드가 없으면 OFF로 읽습니다.

접근점 Z는 접촉점 Z보다 높아야 합니다. 현재 위치가 HOME이 아닌 상태에서 dryrun을
시작하면 현재 자세를 유지하여 선택한 모드의 approach 간격만큼 먼저 후퇴한 뒤
HOME으로 이동합니다.
이 추가 경로도 plan에 표시합니다. 1~100 mm 및 1~90° 제한은 수동 상대 명령에
적용됩니다. `goto`는 현재 위치에서 총 50 mm 이내로 제한하며, 자동 시퀀스는 저장한
목표까지 이동합니다. 전체 링크 충돌·IK·실제 접촉 높이를
검증하는 기능은 아니므로 실제 경로를 관찰해야 합니다.

run은 먼저 성공한 dryrun과 **같은 좌표·접근 높이·속도**이고 현재도 HOME에 있을 때만
시작됩니다. 좌표 재저장·수동 이동·홈밍·접근 간격/모드 변경·파일 읽기를 하면 dryrun을 다시
완료해야 합니다. dryrun은 검증된 시작 경로와 10단계를 완료할 때까지 새 조작을 막습니다.
작업 중에는 status/help/list/limits/cancel만 사용 가능합니다. 실행은 1회이며 자동 반복은
없습니다. run만 펌프 ON/OFF를 전송하고, save는 좌표를 저장하는 역할만 합니다.

실행은 같은 Controller/USB 연결을 사용하므로 별도 프로세스를 열어 연결을 끊지 않습니다.
터미널 run은 로컬 실행이며 DDS Action Goal 전송은 하지 않습니다. 기존 ROS Action
Server/Client는 유지되고, 공통 `sequence.py`의 10단계 계획을 사용합니다.
ROS Action Server는 YAML의 `tool_axis_approach: true/false`로 같은 모드를 선택합니다.
Action 필드는 변경하지 않으며 ON일 때 Goal의 approach_height는 경로 거리가 됩니다.
ROS Action 운용의 하드웨어 활성화·작업 영역 검증 설정도 그대로 유지됩니다.

저장 파일은 현재 폴더의 새로운 `taught_poses_날짜_시간.yaml`이며 기존 설정은
덮어쓰지 않습니다. HOME·PICK·PLACE와 자세, 접근 높이를 저장하지만, 파일의 하드웨어
활성화 플래그는 기본 false입니다. 재시작 후 이전 위치를 이어 쓰려면 새 세션에서
home으로 초기화하고 load 명령으로 파일을 읽은 뒤 plan/dryrun을 다시 확인합니다.
실물 모드에서는 실물에서 저장한 좌표 파일만 읽을 수 있습니다.

전체 XYZ 범위를 추정하지 않습니다. 기본 전역 XYZ 제한은 미설정이고,
`--xyz-bounds X최소 X최대 Y최소 Y최대 Z최소 Z최대`로 검토한 세션 범위를 지정할 수
있습니다. HOME과 자동 접근점도 이 범위 안에 있어야 하며, 홈밍 경로 자체는 제한되지
않습니다. 컵의 테이블 접촉 Z를 0으로 가정하지 마세요.

취소·오류 후에는 후속 실행을 차단하고 펌프를 유지하며 자동 복귀하지 않습니다.
펌프가 켜진 상태에서는 홈밍도 거절됩니다. 물체·장비를 확인하고 별도 승인된 복구를
진행해야 합니다. Ctrl+C·quit·cancel은 비상 정지가 아닙니다. EOF/Ctrl+C로 터미널을
종료할 때는 현재 명령이 끝나고 worker가 종료될 때까지 기다린 뒤 포트를 닫습니다.
펌프 OFF와 PWM 0 확인은 실제 물체 해제를 보장하지 않으므로 실제 해제 시험이 필요합니다.

## 응답 대기 진단

터미널은 각 단계의 시작/완료를 구분하여 출력합니다. `status`에는 현재/마지막 단계와
Controller의 phase가 표시됩니다. 작업 중 status는 기존 캐시만 표시하며 별도 Serial
조회로 ACK를 섞지 않습니다. `waiting_ack`는 명령의 ok 대기, `waiting_idle`은 ok 이후
Idle 대기, `preflight`/`draining_rx`는 이동 전 상태/대기 응답 처리입니다.
긴 대기는 5초마다 phase와 마지막 상태를 출력합니다. cancel은 현재 명령을 즉시 정지하는
기능이 아니며 ACK 대기 자체가 시간 초과될 때까지 끝나지 않을 수 있습니다.

실물 터미널을 시작하면 현재 폴더에 `mirobot_serial_trace_날짜_시간.log`를 새로 만들고
SDK 전송 명령·상태 조회·수신 줄을 기록합니다. 화면에는 목표 좌표를 숨기지만 파일에서는
정확한 전송 숫자와 응답을 확인할 수 있습니다. 실물에서 ACK가 누락되면 새 연결로 재조회하지
말고 같은 세션에서 취소 요청 후 오류/캐시 상태와 이 로그를 확인합니다.

도구축 계산의 긴 소수는 전송 시 소수점 셋째 자리로 반올림하여 명령 길이를 줄입니다.
완료 검사도 반올림하여 실제 전송한 좌표를 기준으로 수행합니다. 이는 전송 형식을 정리하는
변경이며, 실제 장비의 ACK 누락 원인이 명령 길이나 소수 자릿수라고 확정한 것은 아닙니다.
로봇 펌웨어의 정확한 길이/정밀도 제한은 확인되지 않았습니다. ACK나 최종 좌표 검사를
생략하거나 자동으로 재시도하지 않습니다.

## Mock 테스트 실행

ROS 환경을 source하고 workspace 루트에서 실행합니다. 실제 SDK를 사용하지만
모든 Serial 객체는 FakeSerial로 교체합니다.

```bash
PYTHONPATH="$PWD/src/mirobot_pick_place:$PWD/src/Wlkata_Mirobot_Ros2/wlkatapython:$PYTHONPATH" \
  python3 -m pytest -q src/mirobot_pick_place/test/test_controller_readiness.py \
  src/mirobot_pick_place/test/test_hardware_action.py \
  src/mirobot_pick_place/test/test_first_motion_check.py \
  src/mirobot_pick_place/test/test_pump_check.py \
  src/mirobot_pick_place/test/test_teach_terminal.py \
  src/mirobot_pick_place/test/test_tool_axis.py

python3 src/mirobot_pick_place/test/mock_sdk_controller_test.py
python3 src/mirobot_pick_place/test/mock_full_sequence_test.py
```

Action 콜백 직접 호출 테스트는 DDS Server–Client 통신 시험과 구분합니다.
제한된 실행 환경에서는 DDS 소켓 생성이 거부될 수 있으므로 통신은 일반 터미널에서
시뮬레이션 Server/Client를 실행하여 확인합니다.

## 참고 자료

- [WLKATA 프로토콜: 본체 USB, G-code 및 홈밍](https://uploads-ssl.webflow.com/5edc22ea8e58c13e2883e09b/5ee6b047a605db3ba4d1336c_WLKATA%20Mirobot%20Protocol%20Instruction%20V1.0.pdf)
- [컨트롤러 설명서: G01/F 단위, 저장 test 프로그램, 메뉴](https://cdn.yun.sooce.cn/1/10535/pdf/16564849484265b928c22b99c7e9d.pdf)
- [WLKATA SDK: 직접 본체 연결 시 일부 기능 제한](https://github.com/wlkata/WLKATA-Python-SDK-wlkatapython)
