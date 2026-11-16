# camera 브랜치와 출고 서버 호환성 검토

검토일: 2026-11-16. 서버는 `/home/chan/server`의 로컬 파일 기준이다. 카메라는 GitHub `camera` 브랜치의 `e651bacb82df6ee441caf3ef76e0c142b281b241`, 로봇은 `mirobot` 브랜치의 `85ca285ca821289a807db5614a01a3d6b8e78bb2`를 확인했다.

후속 요구: 서버·카메라/모터·미로봇을 각각 다른 노트북에서 실행하고 ROS2 Humble, `ROS_DOMAIN_ID=7`의 토픽으로 연결한다. 향후 통합 방향은 [세 노트북 ROS2 연결 설계](ros2_distributed_design.md)를 우선한다. 아래 HTTP 연결 방법은 초기 대안이며 현재 구현 여부에 대한 분석은 그대로 유효하다.

## 판정

앱과 여러 장치가 같은 서버에 요청하는 구조는 지원한다. 그러나 camera 브랜치를 그대로 실행하면 전체 출고 작업은 연결되지 않는다. 모터·카메라의 HTTP 연결 프로그램이 없고, 카메라의 MoveIt 예제는 기존 로봇팔 브리지와 다른 실행 경로를 사용한다.

HTTP 요청을 반복하는 방식이며 모든 장치의 지속 연결을 유지하는 구조는 아니다. 동시에 접속할 수 있어도 물리 작업은 한 주문의 모터 → 카메라 → 로봇팔 순서로 실행한다. `/health`의 `worker_alive`는 서버 작업자의 상태이며 모든 장치의 준비 상태를 뜻하지 않는다.

## 확인한 차이

| 항목 | 현재 코드 | 통합에 필요한 일 |
|---|---|---|
| 모터 명령 | 서버는 `motor/move_to_bin` HTTP 명령을 생성한다. `camera_linear.py`는 터미널 목표 번호와 USB 시리얼을 사용한다. | 서버 명령 조회, 목표 변환, 실제 완료 보고를 추가한다. |
| 카메라 명령 | 서버는 `camera/align_bin` 완료를 기다린다. 카메라 파일에는 서버 통신이 없다. | 목표 마커의 정렬 및 정지 후 재확인 결과를 해당 명령 ID로 보고한다. |
| 위치 | 서버는 품목 ID·보관함·`position_mm`·`motor_length_mm`를 전송한다. 카메라는 마커 1~4와 번호 대소 관계로 방향을 정한다. | 실제 설치를 확인해 품목/보관함과 마커 번호를 매핑한다. mm 위치 제어가 필요하면 별도 위치 측정·제어가 필요하다. |
| 로봇 실행 | 서버 브리지는 `/pick_place` (`PickPlace`)를 사용한다. `camera_mirobot_sim.py`는 `/move_action` (`MoveGroup`)을 사용한다. | 기존 브리지를 유지한다면 카메라는 정렬만 담당하고 서버가 로봇을 시작하도록 한다. |
| 카메라 로봇 예제 | 화면에 마커 1~4가 보이면 0.5초 후 같은 관절 목표 자세로 이동하고 시작 자세로 복귀한다. 다음 실행 전 마커가 1초간 없어야 한다. | 주문 품목·수량·정렬 완료·모터 정지 조건으로 구동되는 출고 절차와 구분한다. 예제 자체에는 흡착/배출 출고 절차가 없다. |
| 실제 실행 설정 | 로컬 `.env`의 `SIMULATION=true`. 미로봇 저장소 기본 설정도 `simulation_mode=true`, `hardware_enable=false`이다. | 실제 운전에는 각 장치의 검증된 설정과 좌표가 필요하다. 서버 모드만 바꿔도 로봇이 실물 모드가 되는 것은 아니다. |

카메라 원본:

- [camera_linear.py](https://github.com/Double-Click-Lab/auto_picking_project/blob/e651bacb82df6ee441caf3ef76e0c142b281b241/camera_linear.py)
- [camera_mirobot_sim.py](https://github.com/Double-Click-Lab/auto_picking_project/blob/e651bacb82df6ee441caf3ef76e0c142b281b241/camera_mirobot_sim.py)
- [Arduino 펌웨어](https://github.com/Double-Click-Lab/auto_picking_project/blob/e651bacb82df6ee441caf3ef76e0c142b281b241/arduino/sketch_nov12b_copy_20261112210620.ino)
- [미로봇 기본 설정](https://github.com/Double-Click-Lab/auto_picking_project/blob/85ca285ca821289a807db5614a01a3d6b8e78bb2/src/mirobot_pick_place/config/poses.yaml)

## 기존 서버를 유지하는 연결 방법

1. 주문앱은 `X-API-Key`로 `POST /orders/app-inventor`를 호출한다. 주문은 `waiting`이 된다.
2. 관리자앱이 선택한 주문의 `POST /orders/{order_id}/start`를 호출한다.
3. 하나의 카메라·모터 연결 프로그램이 `X-Device-Key`로 `/devices/motor/commands/next`를 조회한다. 매핑한 목표 마커까지 이동하고 `STOPPED` 응답을 확인한 뒤 해당 motor 명령의 완료 API를 호출한다. 이 경우 motor 단계가 영상 기반 이동까지 수행한다는 규약을 문서화한다.
4. 같은 프로그램이 camera 명령을 받아 정지 후 목표 마커가 여전히 허용 범위 안인지 확인하고 완료를 보고한다. 모터와 카메라를 두 프로세스로 나누어 같은 카메라/시리얼 포트를 경쟁하게 할 필요는 없다.
5. 기존 `tools/mirobot_bridge.py`가 robot 명령을 받아 `/pick_place` 액션을 실행하고 결과를 보고한다.
6. 수량이 여러 개면 서버가 로봇 명령을 개수만큼 생성한다. 모든 품목 처리가 끝나면 `waiting_box`가 된다.
7. 사람이 상자를 교체하고 관리자앱이 `/box-ready`를 호출한다. 다음 앱 주문은 다시 `/start`를 눌러야 한다.

완료 보고는 `POST /devices/{device}/commands/{command_id}/complete`와 `{"success":true,"detail":"설명"}` 형식이다. 실패도 같은 API에 `success=false`로 보고한다. 카메라 좌표 같은 추가 필드는 현재 Result 스키마에서 허용하지 않으며, 로봇 브리지는 검증된 고정 pick/place 좌표를 사용한다. 픽셀 오차를 로봇 좌표로 변환해 전달하는 기능은 없다. 카메라 중앙과 실제 집기 위치의 관계는 현장에서 보정해야 한다.

## 연결 프로그램에서 처리해야 하는 조건

- `camera_linear.py`는 이미 같은 목표 번호에 있으면 영상 확인 없이 반환한다. 연속 주문에서 같은 상품을 다시 처리할 때도 현재 영상으로 검증하고 새 명령에 완료를 보고해야 한다.
- 원본은 마커가 없어도 시리얼 heartbeat가 이어지면 계속 이동한다. 기본 서버 명령 제한은 생성 시점부터 60초이다. 서버의 오류 처리는 DB 상태 변경이며 물리 정지 명령을 보내지 않는다. 연결 프로그램은 명령 유효 시간과 오류를 처리해 모터를 정지시키고 실패를 보고해야 한다. Arduino의 1초 heartbeat 제한은 PC와 Arduino 사이의 단절만 감지한다.
- `STOPPED`는 모터 드라이버 출력 정지 응답이다. 실제 기구의 관성 및 정지 후 마커 위치도 확인해야 한다. 기본 허용 범위는 영상 중앙에서 가로 ±20px, 세로 ±60px이며 실제 집기 정확도를 보증하지 않는다.
- HTTP 완료 응답 유실이나 재접속 시 같은 명령 ID로 물리 동작을 중복 수행하지 않도록 실행 기록과 재전송 처리가 필요하다. 기존 로봇 브리지에는 journal이 있지만 카메라·모터 코드에는 서버 명령 실행 기록이 없다.
- 기존 로봇 브리지와 마커로 로봇을 자동 구동하는 카메라 예제를 같은 팔의 독립 제어기로 함께 실행하지 않는다.

## 검증 결과와 한계

기존 테스트: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q` → **71 passed, 1 skipped**. 샌드박스의 TestClient 시작 정지 문제로 제한 밖에서 재실행했다. 건너뛴 항목은 ROS2 DDS 통합 테스트이며 이번에는 실제 ROS2 또는 장비 통합 성공을 확인하지 않았다.

별도 임시 DB에서 서버를 `simulation=False`로 생성하고 HTTP ASGI 클라이언트로 주문앱·관리자앱 조회와 가상 motor/camera/robot 명령 루프를 동시에 실행했다. 가상 장치만 성공을 보고하며 실제 장비와 실행 중인 서버에는 명령을 보내지 않았다.

- A 2개 + D 1개 주문의 명령 순서: `motor, camera, robot, robot, motor, camera, robot`.
- 장치 처리 중 앱 조회 72회 성공. 처리 중 추가 주문 접수 성공, 그 주문의 시작은 409로 차단.
- 동일 완료 보고 재전송 성공. 상자 확인 전 다음 시작 차단, 확인 후에도 다음 주문은 waiting 유지, 별도 시작 요청 성공.
- 연결 프로그램 없이 시작하면 첫 motor 명령에서 시간 초과로 실패하고 camera/robot 명령은 생성되지 않음을 재현.
- 원본 MotorController를 시리얼 대역으로 실행해 STOPPED 수신 후 위치가 갱신됨과 같은 목표 요청 시 재검증 없이 반환함을 확인.

이는 서버 통신 규약 및 상태 전이 검증이다. 실제 네트워크의 동시 연결 성능, USB 장치 동시 사용, 카메라 정확도, 실제 모터 정지, 로봇 집기 성공은 검증하지 않았다. 앱인벤터 프로젝트 파일도 제공되지 않아 실제 앱 블록의 요청 형식은 확인하지 못했다. 실제 연결 시험에는 위 연결 프로그램과 품목/마커 매핑, 보정된 집기 좌표, 장치별 실행 시간이 필요하다.
