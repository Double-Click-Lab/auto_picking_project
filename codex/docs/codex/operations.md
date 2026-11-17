# 통합 실행 순서와 대기·복구

이 문서는 세 노트북이 함께 사용하는 진행 기준이다. 장치 ROS state는 initializing/ready/busy/fault이고, 아래 단계명은 사람용 작업 보고다.

## 단계별 소유자와 통과 조건

| 단계 | 서버 Codex | 미로봇 Codex | 카메라·모터 Codex | 다음 단계 조건 |
|---|---|---|---|---|
| 0 역할 확인 | server 폴더·브랜치 확인 | mirobot 확인 | camera 확인 | 각자 실제 경로·SHA 기록 |
| 1 공통 준비 | LAN·시각·7/0/RMW | 동일 | 동일 | 노트북 간 DDS 통신 확인 |
| 2 로컬 준비 | Python 의존성 | 액션·어댑터 빌드 | 노드 빌드 | 각 로컬 준비 완료 |
| 3 서버 시작 | FastAPI 후 중계 | 서버 heartbeat 대기 가능 | 노드 먼저 실행 가능 | 정상 HTTP·중계 heartbeat |
| 4 장치 시작 | 최근 장치 상태 대기 | 액션 서버+어댑터 유지 | 노드 하나 유지 | motor/camera/robot simulation/ready |
| 5 사전 점검 | picking_check + robot status | status 정상·명령 대기 | 상태 정상·명령 대기 | check 성공 + status success/mode 정상 |
| 6 주문 | A=1 모의 주문 시작 | 서버 robot 명령 처리 | 서버 motor→camera 처리 | 각 result와 주문 waiting_box |
| 7 완료 확인 | 모의 box-ready·completed 확인 | 미확인 결과 없는지 확인 | 미확인 결과 없는지 확인 | 실제 주문 완료 증거 확보 |

노트북 세션은 서로의 파일·현재 작업 상황을 자동 공유하지 않는다. GitHub 문서는 공통 기준이고, 실시간 준비 상태는 ROS 토픽/서비스와 각 세션 보고로 확인한다.

서버가 시작되기 전 미로봇 어댑터의 initializing은 정상 대기일 수 있다. 카메라 simulation ready는 서버 없이도 가능하다. 장치 ready, 서버 연결 성공, 주문 완료, 실물 집기 성공은 각각 다른 확인 결과다.

## ROS 통신 규약 요약

모든 아래 토픽은 `std_msgs/msg/String`, UTF-8 JSON, KEEP_LAST depth=10, RELIABLE, VOLATILE이다.

| 토픽 | 발행 소유자 | 구독 |
|---|---|---|
| /picking/motor/command | 서버 중계 | 카메라·모터 |
| /picking/camera/command | 서버 중계 | 카메라·모터 |
| /picking/robot/command | 서버 중계 | 미로봇 어댑터 |
| /picking/server_heartbeat | 서버 중계 | 두 장치 노트북 |
| /picking/device_status | 각 장치 노드 | 서버·조회 |
| /picking/device_events | 각 장치 노드 | 서버 중계 |
| /picking/result_ack | 서버 중계 | 두 장치 노트북 |
| /picking/cancel | 서버 중계 | 두 장치 노트북 |

명령 action은 motor=move_to_bin, camera=align_bin, robot=pick_and_place다.

robot payload는 정확히 `item_id`, `bin`, `quantity`, `destination_id` 네 필드다. quantity는 정수 1이고 destination_id는 order_id와 같다. HTTP 메타데이터, 좌표, marker_id 등을 robot payload에 추가하지 않는다. 고정 좌표는 미로봇 로컬 YAML에서 읽는다.

motor/camera에는 marker_id, position_mm, motor_length_mm가 추가된다. 범위 검증과 실제 mm 위치 제어의 구현 여부는 구분한다.

주요 장치 수락 조건:

- 같은 모드와 서버 세션.
- 최근 5초 이내 heartbeat, 서버 시각 차이 1초 이내.
- 명령 만료까지 2초 초과.
- 기존 작업/fault/미확인 결과가 없어야 함.

accepted는 명령 수락 신호이며 완료가 아니다. result의 success가 최종 장치 결과다. 서버 result_ack의 accepted=true는 해당 결과를 처리했다는 뜻이고 장치 성공과 다르다. result_ack만으로 주문 completed를 추론하지 않는다.

중복 command_id는 새 동작으로 취급하지 않는다. 결과는 같은 event_id로 ack까지 재전송한다. ID/본문을 임의 변경하거나 과거 timestamp/UUID 예시를 새 명령으로 복사하지 않는다.

## 즉시 주문을 멈추고 확인할 조건

- 모드 불일치, 시간 동기화 실패, 장치 fault 또는 결과 불명확.
- 서버 heartbeat·명령 발행자가 둘 이상 존재.
- robot status 실패·모드 불일치·액션 서버 없음.
- 다른 장치나 운영 프로그램이 같은 USB·카메라·로봇을 소유.

서버 중계 하나만 명령/heartbeat를 발행한다. 세 노트북 전체 시험 중 별도 `simulation_server`, `picking_sim_robot.py`, `picking_sim_test_publisher`, 수동 `ros2 topic pub` 명령을 함께 실행하지 않는다.

## 대기가 길어질 때

| 관찰 | 구분할 원인 | 다음 확인 |
|---|---|---|
| ROS 노드가 서로 안 보임 | 네트워크/도메인/overlay/RMW | multicast, demo talker/listener, 환경과 daemon |
| HTTP 정상인데 heartbeat 없음 | 중계 오류/중복 발행자/HTTP 인증 | 중계 로그·토픽 publisher count |
| robot initializing | heartbeat/시각 문제 | 서버 중계와 미로봇 수신 시각 |
| robot ready인데 주문 실패 | 로컬 액션 backend 준비 | /mirobot/status, /pick_place, controller mode |
| 카메라 ready이나 서버가 안 봄 | DDS/다른 도메인/노드 중복 | 서버 쪽 status 수신과 device session |
| 이전 결과 ack 대기 | 서버 결과 처리 실패 | event/result_ack 식별자와 서버 journal |
| waiting_box | 정상 사용자 단계 | simulation/real에 맞는 상자 확인 |

일정 시간 수신을 관찰해도 바뀌지 않으면 계속 새 프로세스를 띄우지 말고 정확한 장애·확인 결과·필요 조치를 보고한다. 다른 노트북이 실제 실행 중인지 추정으로 채우지 않는다.

## 실패와 재시작 복구

1. 추가 주문과 재시도를 중단하고 원래 오류를 보존한다.
2. order_id, command_id, server_session_id, device session_id, event_id, 각 프로세스 시작 시각, HTTP 상태를 기록한다. 비밀 값은 제외한다.
3. 서버 DB/journal과 장치 ledger의 명령/결과를 대조한다. DB 삭제나 새 경로로의 변경은 복구 절차가 아니다.
4. 실제 장치가 연관된 경우 현재 위치·보유 물체·정지 상태를 사람이 확인한다. timeout/cancel/Ctrl+C는 물리 정지를 보증하지 않는다.
5. 장치 README의 복구 조건을 충족한 뒤 해당 역할의 복구만 수행한다. 서버 시스템 오류 해제, 중계 journal의 recover, 미로봇 ledger 복구, 카메라 로컬 prepare는 서로 다른 절차다.
6. 서버와 장치의 미확인 결과 처리가 완료되고 모든 상태가 다시 정상일 때 읽기 전용 점검부터 재개한다.

미로봇의 전용 recover 명령이나 서버의 `--recover`를 연결 실패 때마다 자동 실행하지 않는다. 프로세스 재시작은 기존 명령을 자동 재실행하는 수단이 아니다. 서버 세션이 바뀌면 진행 명령에 영향이 있으므로 작업 중 중계를 재시작하지 않는다.

## 종료와 다음 실행

모의 주문이 completed이고 진행 중 명령·미확인 결과가 없음을 먼저 확인한다. 자신의 세션에서 장치 프로그램을 정상 종료하고, 서버 중계, FastAPI를 정상 종료한다. 실제 장치가 작업 중이면 이 일반 모의 종료 순서를 적용하지 않고 현장 정지·복구 절차를 따른다.

다음 실행에는 소스 SHA와 설정을 다시 기록하고 같은 DB의 기존 fault/미확인 결과부터 점검한다. API는 기존 주문을 보존할 수 있으며 시험 주문이 남아 있으면 새 주문을 무작정 추가하지 않는다.

## 실물 전환은 별도 단계

사용자가 실제 장비 시험을 요청하고 현장 설정을 확인한 뒤 아래 조건을 맞춘다. 이 문서의 기본 모의 실행 프롬프트는 실물 구동을 포함하지 않는다.

| 역할 | 실물 전환 조건 |
|---|---|
| 서버 | SIMULATION=false, DEVICE_TRANSPORT=topics, 실제 키·DB, CLEAR_ORDERS_ON_START=false, --mode real 중계, 검증한 매핑과 영속 journal |
| 카메라·모터 | simulation=false, 실제 port/camera, 115200 baud, 마커·방향·한계·정지 확인, STOP handshake 후 실제 현재 마커의 로컬 prepare와 새 영상 정렬 |
| 미로봇 액션 서버 | simulation_mode=false, hardware_enable=true, coordinates_confirmed=true, 실측 workspace_bounds·HOME·속도·경로, 명시적 초기화 절차 |
| 미로봇 어댑터 | mode=real, 액션 서버와 같은 controller_config, 실측 coordinate_file, simulation_only=false와 coordinates_confirmed=true, HOME 일치 |

미로봇 real backend는 `Mirobot_robot.Mirobot_UART`를 import한다. 설치가 안 되어 있으면 저장소의 `src/Wlkata_Mirobot_Ros2/wlkatapython` 경로를 Python이 찾는지 확인한다. 이 SDK는 simulation에는 필요 없고 ROS 패키지 빌드만으로 설치가 완료됐다고 가정하면 안 된다.

`/mirobot/initialize`는 실제 홈밍이다. 통신 점검으로 호출하지 않는다. 예시 HOME/PICK/PLACE의 플래그만 바꾸어 실물 좌표로 만들지 않는다. 기본 미로봇 예시 YAML은 stub 전용이다.

서버 COMMAND_TIMEOUT과 어댑터 action_timeout_sec는 실제 최대 수행 시간과 여유를 고려해 함께 정한다. 카메라 정렬과 집기 위치 보정, 실제 흡착·해제, USB/카메라 단절 및 물리 정지 특성은 별도 시험이다.

실물 준비 후 `picking_check.py --mode real`와 미로봇 status의 success=true/mode=real을 확인하고, 검증된 작은 시험부터 진행한다. 실제 box-ready는 작업자가 상자를 교체한 뒤에만 보낸다.

## 소스 기준

장치 README 일부의 “서버 미구현” 문구는 과거 작성 시점 설명이다. 확인한 server SHA에는 HTTP↔ROS2 중계와 결과 처리 코드가 있다. 반대로 서버 코드 존재가 세 노트북 LAN 통합이나 실물 검증 완료를 뜻하지 않는다.

원본 규약과 실행 순서는 [sources.json](sources.json)의 고정 SHA 링크와 역할별 소스 링크를 참조한다.
