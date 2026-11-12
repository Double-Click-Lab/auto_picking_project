# Mirobot ROS2 연결

`tools/mirobot_bridge.py`는 출고 서버의 HTTP 장치 명령을 ROS2 `/pick_place` 액션으로 전달합니다. 브리지는 ROS2 Humble와 미로봇 workspace가 설치된 로봇 PC에서 실행하는 구성을 권장합니다. 출고 서버는 라즈베리파이에 그대로 둘 수 있습니다. 브리지와 액션 서버는 같은 ROS_DOMAIN_ID를 사용해야 합니다.

확인한 담당자 코드: [Double-Click-Lab/auto_picking_project, mirobot 브랜치](https://github.com/Double-Click-Lab/auto_picking_project/tree/85ca285ca821289a807db5614a01a3d6b8e78bb2). 인터페이스는 `mirobot_pick_place_interfaces/action/PickPlace`이며 액션 이름은 `/pick_place`, 준비 상태 조회는 `/mirobot/status` (`std_srvs/srv/Trigger`)입니다.

## 전달 흐름

1. 주문앱은 주문을 `waiting`으로 접수하고 관리자앱이 `/start`를 호출합니다.
2. 기존 모터 이동과 카메라 정렬이 완료되면 서버가 `robot/pick_and_place` 명령을 만듭니다.
3. 브리지가 `GET /devices/robot/commands/next`로 명령을 받습니다.
4. 준비 상태를 확인하고 `start=true`와 고정 좌표를 `/pick_place`로 보냅니다.
5. 액션이 `STATUS_SUCCEEDED`이고 `result.success=true`일 때만 HTTP 완료 API에 성공을 보고합니다. 취소·거절·실패·시간 초과는 실패로 보고합니다.
6. 수량이 여러 개면 기존 서버가 1개 이송마다 새 명령 ID를 발급합니다. 모든 품목을 처리하면 기존 상자 교체 확인 흐름으로 진행합니다.

서버가 `SIMULATION=true`이면 실제 장치 명령은 전달하지 않습니다. `SIMULATION=false`에서는 모터와 카메라도 기존 HTTP 규약으로 완료 신호를 보내야 로봇 단계가 시작됩니다. 모터·카메라를 연결하지 않은 상태에서 전체 주문을 시작하면 해당 단계에서 대기/시간 초과가 됩니다. 브리지는 초기화/홈밍 서비스를 자동 호출하지 않습니다. 담당자의 초기화 절차가 끝나 `/mirobot/status`가 준비 상태를 반환해야 합니다.

출고 서버의 SIMULATION과 미로봇 YAML의 simulation_mode는 별개입니다. 서버가 실제 모드여도 미로봇 쪽이 simulation_mode=true이면 팔은 모의 동작합니다. 실물 운전 설정은 담당자가 검증한 미로봇 설정을 사용하세요.

## 고정 동작 좌표

현재 담당자 액션은 start만 보내면 좌표가 자동 선택되는 인터페이스가 아닙니다. 브리지는 좌표 파일을 시작할 때 한 번 읽어 각 명령에 같은 좌표를 전달합니다. 한 번 시작한 브리지 안에서는 파일을 수정해도 동작 중 좌표를 바꾸지 않습니다.

`config/mirobot_goal.example.json`은 저장소 클라이언트의 **모의 실행용 예시 값**입니다. 실제 운전에는 담당자가 검증한 pick/place 좌표와 approach_height를 별도 파일로 저장해 `--goal-file`로 지정하세요. 좌표와 회전 단위, HOME, 접근 방향 모드는 담당자 액션 서버 설정을 따릅니다. HOME과 하드웨어 활성화/작업 영역/접근 모드는 액션 서버의 YAML에서 관리합니다. 임의로 그 설정을 해제하지 않습니다.

## 로봇 PC에서 실행

로봇 PC에 `tools/mirobot_bridge.py`와 검증된 좌표 파일을 복사하세요. `--env-file`에 사용할 파일에는 서버 `.env`와 동일한 `DEVICE_KEY`를 넣습니다. 앱용 API_KEY가 아닌 장치 키를 사용합니다. 환경변수 DEVICE_KEY가 있으면 파일보다 우선합니다. 브리지 자체의 추가 pip 의존성은 없으며, ROS2 패키지는 기존 미로봇 workspace에서 가져옵니다.

각 터미널에서 환경을 먼저 불러옵니다. 아래 workspace 경로는 실제 설치 경로로 바꾸세요.

```bash
source /opt/ros/humble/setup.bash
source /경로/auto_picking_project/install/setup.bash
```

담당자의 `/pick_place` 액션 서버를 실행한 뒤, **동작 없는 연결 점검**을 합니다. 서버 주소는 경로 없는 기본 주소를 사용합니다.

```bash
/usr/bin/python3 tools/mirobot_bridge.py --check --url http://chan-16ZD90RU-GX56K.local:8000 --env-file /경로/bridge.env
```

연결 점검은 서버의 모드/오류/작업자와 로봇의 준비 상태를 표시하며 RUN이나 초기화 명령을 보내지 않습니다. 서버가 모의 모드인 경우에도 조회할 수 있습니다.

검증된 좌표와 장치 준비를 확인한 뒤 명령 수신을 시작합니다.

```bash
/usr/bin/python3 tools/mirobot_bridge.py --run --url http://chan-16ZD90RU-GX56K.local:8000 --env-file /경로/bridge.env --goal-file /경로/검증된-goal.json --journal /경로/mirobot-bridge.sqlite3
```

서버의 새 `expires_at`/`expires_in` 필드 적용에는 서버 재시작이 필요합니다. `.env`의 CLEAR_ORDERS_ON_START가 true이면 재시작 시 주문 기록이 삭제됩니다. 브리지를 같은 라즈베리파이에서 실행하면 `--url http://127.0.0.1:8000`도 사용할 수 있습니다. 서버가 다른 컴퓨터로 이동하면 앱과 브리지의 주소를 변경하세요.

## 시간 제한과 재시도

브리지의 기본 액션 제한 시간은 45초입니다. 서버의 기본 COMMAND_TIMEOUT=60초 안에서 결과를 보고할 시간을 확보합니다. 긴 동작이라면 담당자가 측정한 실행 시간에 맞춰 서버의 COMMAND_TIMEOUT을 늘린 뒤 재시작하고 브리지에도 `--action-timeout`을 지정하세요. 예를 들어 서버 300초와 브리지 240초를 사용할 수 있습니다. 브리지는 서버가 보낸 남은 시간 expires_in과 로컬 단조 시계를 사용하므로 두 PC의 시계 차이에 의존하지 않습니다. 유효 시간이 부족하면 새 RUN을 보내지 않습니다.

명령 ID는 ROS Goal UUID에도 사용하고, 실행 전에 SQLite journal에 기록합니다. 완료 보고 응답이 유실되면 같은 결과만 재전송하며 팔 동작은 반복하지 않습니다. 프로세스가 동작 중 중단되면 다음 시작에서 해당 명령을 실패로 보고하고 자동 재실행하지 않습니다. 같은 기록 파일은 한 브리지만 열 수 있으며 **로봇 하나에는 브리지 하나만 실행**하세요. 다른 경로의 새 journal로 중복 실행 방지를 우회하면 안 됩니다.

서버가 명령 기록을 삭제해 완료 보고에 404를 반환하거나 이미 명령을 실패 처리해 409를 반환하면 자동으로 완료한 척하지 않습니다. 실제 로봇과 물품 상태를 확인하고 서버와 journal을 함께 정리해야 합니다. journal은 실행 기록이므로 임의 삭제하지 않습니다. 취소는 담당자 액션의 동작 단계 경계에서 처리되며 물리 비상정지를 대체하지 않습니다.

## 검증

일반 테스트는 ROS 설치 없이도 실행할 수 있습니다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
```

추가 DDS 통합 테스트는 ROS2와 담당자 workspace를 source한 환경에서 실행합니다. 담당자 저장소의 실제 액션 서버를 simulation_mode=true로 기동하며, 별도 HTTP 포트·DB와 ROS_DOMAIN_ID=178, ROS_LOCALHOST_ONLY=1을 사용합니다. 모터·카메라는 테스트 대역입니다. 실제 장비나 현재 실행 중인 출고 서버에 명령을 보내지 않습니다.

```bash
MIROBOT_ROS_INTEGRATION=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q tests/test_mirobot_ros.py
```
