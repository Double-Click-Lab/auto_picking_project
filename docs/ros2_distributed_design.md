# 세 노트북의 ROS2 토픽 연결 설계

2026-11-16 사용자 요구 반영. 이 문서는 설계안이며 토픽 노드가 구현되었거나 다른 노트북과 통신 시험이 끝났다는 의미는 아니다. 기존 HTTP 장치 연결안보다 이 구성을 우선한다.

## 배치

| 위치 | 실행할 구성 | 역할 |
|---|---|---|
| 서버 노트북 | 기존 FastAPI + 서버 ROS2 변환 노드 | 앱 주문 접수, 순차 작업 관리, 장치 명령 발행 및 결과 수신 |
| 카메라·리니어모터 노트북 | 카메라·모터 ROS2 노드 | 카메라와 Arduino를 하나의 프로세스에서 관리하고 이동·정렬 결과 발행 |
| 미로봇 노트북 | 로봇 토픽 어댑터 + 기존 `/pick_place` 액션 서버 | 토픽 명령을 로컬 PickPlace 액션으로 전달하고 실제 액션 결과 발행 |

앱인벤터는 기존 HTTP API를 사용한다. 노트북 사이의 작업 통신은 ROS2 토픽을 사용한다. 서버 변환 노드는 같은 서버 노트북의 `127.0.0.1` HTTP 장치 API를 사용해 기존 주문 상태 전이를 재사용할 수 있다. 이 구조는 원격 장치에서 서버 HTTP를 조회할 필요가 없다. FastAPI 자체를 ROS2 노드로 재작성하는 작업도 피할 수 있다.

기존 `tools/mirobot_bridge.py`의 HTTP 수신 역할은 서버 변환 노드 쪽으로 옮기고, journal 및 RosPickPlace 실행 로직은 로봇 토픽 어댑터에서 재사용할 수 있다. 기존 브리지를 별도 제어기로 동시에 실행하지 않는다.

## 토픽 규약 제안

아래 이름과 메시지는 새로 구현할 규약이며 기존 저장소에 존재하는 토픽이 아니다.

| 토픽 | 발행 → 구독 | 내용 |
|---|---|---|
| `/picking/motor/command` | 서버 → 카메라·모터 노트북 | 서버 명령 ID, 주문 ID, 대상 품목/보관함, 목표 마커, 유효 시간 |
| `/picking/camera/command` | 서버 → 카메라·모터 노트북 | 서버 명령 ID, 정지 후 목표 마커 정렬 확인 요청 |
| `/picking/robot/command` | 서버 → 미로봇 노트북 | 서버 명령 ID, 주문 ID, 물품 1개 이송 요청 |
| `/picking/device_events` | 각 장치 → 서버 | 명령 수락·진행·성공·실패와 해당 명령 ID, 장치 이름, 설명 |
| `/picking/device_status` | 각 장치 → 서버 | 주기적 준비/작업/오류 상태, 현재 명령 ID, 장치 세션 ID |
| `/picking/cancel` | 서버 → 각 장치 | 특정 명령 취소 요청. 각 장치는 로컬에서 지원하는 정지/취소 절차와 결과 보고 수행 |

장치별 구현 프롬프트를 배포하는 v1에서는 모든 공통 토픽을 `std_msgs/msg/String`의 JSON으로 통일한다. 메시지 정의와 heartbeat/result_ack/cancel 규칙은 [토픽 통신 규약 v1](picking_topic_protocol_v1.md)을 따른다. 이 규약이 위의 초기 제안보다 우선한다. `Bool` 완료 신호만으로는 이전 주문의 늦은 신호와 현재 주문을 구분할 수 없으므로 명령 ID를 필수로 사용한다.

## 작업 순서

1. 앱 주문 접수 후 관리자가 작업 시작을 요청한다.
2. 서버 변환 노드가 motor 명령을 발행한다. 카메라·모터 노드는 지정한 마커로 이동하고 정지 응답을 확인한다.
3. motor 성공 결과를 서버에 반영한 후 camera 명령을 발행한다. 같은 장치 노드가 정지 후 현재 영상에서 정렬을 다시 확인한다.
4. camera 성공 결과가 반영되면 robot 명령을 발행한다. 로봇 어댑터가 로컬 `/pick_place` 액션을 실행하고 액션 성공/실패를 보고한다.
5. 서버가 주문 수량만큼 로봇 명령을 생성한다. 모든 품목 완료 후 상자 교체 확인 절차를 진행한다.

카메라 노드는 마커 발견만으로 팔을 직접 구동하지 않는다. 한 단계의 실패나 장치 상태 유효 시간 초과 시 다음 단계로 진행하지 않는다. 기존 품목/마커 매핑과 실제 집기 좌표 보정은 여전히 필요하다.

## 메시지 유실·재접속 처리

- 명령은 `RELIABLE` + `VOLATILE`을 시작점으로 제안한다. 늦게 시작한 구독자에 대한 전달과 실행 완료는 QoS만으로 해결되지 않는다. 서버는 장치 준비 및 명령 수락을 확인하고 같은 ID로 재전송한다.
- 장치는 실행 전에 ID를 영속 기록한다. 같은 명령을 다시 받으면 동작을 반복하지 않고 기존 상태/결과를 반환한다. 동작 중 재시작으로 결과가 불명확하면 자동 재실행하지 않고 실패·확인 필요 상태로 보고한다.
- 완료 이벤트도 서버 수신 확인까지 재전송해야 한다. 서버가 HTTP 완료 API로 결과 반영에 성공한 다음 해당 장치에 수신 확인하는 규약을 구현한다. 수신 확인 토픽/메시지는 구현 시 공통 인터페이스에 포함한다.
- 서버는 현재 활성 명령과 일치하는 결과만 반영한다. ROS2 내부의 이벤트 필드를 그대로 HTTP 완료 API로 보내지 않고 `success`, `detail`로 변환한다.
- 서버 단절 시 취소 토픽도 도착하지 않을 수 있다. 장치 로컬의 유효 시간/heartbeat 감시로 정지·취소를 처리한다. Arduino 시리얼 heartbeat는 노트북 사이 통신 단절을 감지하지 못한다.
- 단말 간 시계 차이를 고려해 유효 시간과 재전송 시 남은 시간을 설계해야 한다. 기존 서버 `expires_in`을 원격 노트북의 monotonic 시각과 단순 비교하지 않는다. 지연된 명령의 실행 방지와 로컬 동작 제한 시간을 함께 구현한다.

## 네트워크 조건과 첫 검증

사용자 확인: 세 노트북 모두 ROS2 Humble을 사용하며 도메인 ID는 7로 통일한다. 사용자가 지정한 `07`은 설정에서 `ROS_DOMAIN_ID=7`로 표기한다. 다른 노트북의 실행 환경에 실제 적용되었는지는 아직 확인하지 않았다.

각 ROS2 노드를 실행하는 터미널에서 다음 공통 설정을 적용한다. 프로젝트의 `config/ros2.env`에도 두 환경변수를 저장했다. 이 파일은 ROS2용 셸 설정이며 FastAPI의 `.env`와 별개이다.

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
```

프로젝트 루트에서는 두 export 명령 대신 `source config/ros2.env`를 사용할 수 있다. 사용하는 장치 workspace의 `install/setup.bash`도 노드 실행 전에 불러온다. 이미 실행 중인 프로세스에는 새 터미널 환경변수가 소급 적용되지 않는다.

- 세 노트북을 서로 통신 가능한 LAN에 연결하고 같은 `ROS_DOMAIN_ID`를 사용한다. ID만 맞추어도 라우팅·방화벽·공유기 단말 격리 문제가 해결되는 것은 아니다.
- Humble 환경에서는 `ROS_LOCALHOST_ONLY=1`이면 다른 노트북과 통신하지 못하므로 원격 통신이 허용되는 설정을 사용한다. 현재 저장소의 ROS 통합 테스트는 격리를 위해 `ROS_LOCALHOST_ONLY=1`을 쓰므로 실제 다중 PC 실행 설정과 구분한다.
- 토픽 이름·메시지 타입·QoS를 맞춘다. 미들웨어 설정도 우선 통일하면 문제를 좁히기 쉽다.
- 장비를 움직이기 전에 노트북 간 talker/listener를 양방향으로 확인한다. 그다음 가상 장치로 주문 순서와 중복 방지를 검사하고 실제 정렬·이송 시험으로 진행한다.

참고: ROS2 공식 문서 소스의 [환경 설정](https://github.com/ros2/ros2_documentation/blob/humble/source/Tutorials/Beginner-CLI-Tools/Configuring-ROS2-Environment.rst), [Discovery](https://github.com/ros2/ros2_documentation/blob/humble/source/Concepts/Basic/About-Discovery.rst), [QoS](https://github.com/ros2/ros2_documentation/blob/humble/source/Concepts/Intermediate/About-Quality-of-Service-Settings.rst).
