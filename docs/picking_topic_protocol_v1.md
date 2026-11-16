# 피킹 토픽 통신 규약 v1

공통 통신 규약 v1 — 아래 이름·필드·타입을 임의로 변경하지 말 것.
- ROS2 Humble / Python rclpy / ROS_DOMAIN_ID=7 / ROS_LOCALHOST_ONLY=0.
- 앱인벤터는 서버 HTTP를 사용한다. 노트북 사이 장치 통신은 ROS2 토픽이다.
- 모든 아래 토픽 타입은 std_msgs/msg/String이며 data는 UTF-8 JSON 객체다. 이번 v1에서는 커스텀 .msg를 만들지 않는다.
- QoS는 KEEP_LAST(depth=10), RELIABLE, VOLATILE로 발행/구독을 맞춘다.
- 명령 토픽:
  /picking/motor/command   device=motor, action=move_to_bin
  /picking/camera/command  device=camera, action=align_bin
  /picking/robot/command   device=robot, action=pick_and_place
- 공통 토픽:
  /picking/device_events  장치→서버, 수락/최종 결과
  /picking/device_status  장치→서버, 1초 주기 상태
  /picking/server_heartbeat  서버→장치, 1초 주기
  /picking/result_ack  서버→장치, 최종 결과의 서버 반영 확인
  /picking/cancel  서버→장치, 특정 명령 취소 요청
- command JSON의 필수 필드:
  {"v":1,"server_session_id":"서버 프로세스 UUID","command_id":"명령 UUID",
   "order_id":"주문 UUID","device":"motor|camera|robot",
   "action":"해당 장치 action","mode":"simulation|real",
   "issued_at":0.0,"expires_at":0.0,"payload":{}}
  issued_at/expires_at은 실제 발행 시각/만료 시각의 Unix seconds 숫자로 채운다.
  payload 공통 필드: item_id(str), bin(str), quantity(int, 항상 1), destination_id(str, order_id와 동일).
  motor/camera에는 marker_id(int 1~4), position_mm(유한한 0 이상 숫자), motor_length_mm(유한한 양수 숫자)를 추가한다.
  position_mm <= motor_length_mm를 검증하되 기존 모터가 mm 위치 제어를 제공한다고 간주하지 않는다.
  robot은 로컬에서 검증한 고정 좌표 파일을 사용한다.
- device_events JSON:
  {"v":1,"server_session_id":"원래 서버 세션","event_id":"이벤트 UUID",
   "command_id":"원래 명령 UUID","order_id":"원래 주문 UUID",
   "device":"motor|camera|robot","mode":"simulation|real",
   "phase":"accepted|result","success":null,"detail":""}
  accepted의 success는 null, result의 success는 JSON boolean이다. detail은 최대 500자.
  최종 result는 event_id와 내용 전체를 영속 저장하고 동일한 결과를 1초마다 재전송한다.
- result_ack JSON:
  {"v":1,"server_session_id":"원래 서버 세션","command_id":"원래 명령 UUID",
   "device":"해당 장치","event_id":"수신한 result 이벤트 UUID","accepted":true}
  위 식별자가 모두 일치해야 재전송을 끝낸다. accepted=true는 서버가 결과를 처리/기록했다는 뜻이며 장치 작업 성공 여부와 별개다.
- device_status JSON:
  {"v":1,"device_id":"camera_motor_laptop|mirobot_laptop",
   "device":"motor|camera|robot","session_id":"장치 시작마다 새 UUID",
   "mode":"simulation|real","state":"initializing|ready|busy|fault",
   "active_command_id":null,"detail":"","sent_at":0.0}
  active_command_id는 null 또는 UUID 문자열. sent_at은 Unix seconds.
  카메라·모터 노트북은 motor와 camera 상태를 각각 발행한다.
- server_heartbeat JSON:
  {"v":1,"server_session_id":"서버 프로세스 UUID","sent_at":0.0}
- cancel JSON:
  {"v":1,"server_session_id":"원래 서버 세션","command_id":"취소할 UUID",
   "device":"해당 장치","reason":"설명"}
  활성 명령과 일치할 때만 취소한다. 토픽 취소는 물리 비상정지가 아니다.

공통 실행 규칙:
- 기본 simulation=true. 노드 모드와 command.mode가 다르면 실행을 거절한다. 실제 장비 구동 시험은 사용자의 별도 지시 전까지 하지 않는다.
- 세 노트북은 같은 LAN과 시간 동기화를 전제로 한다. 장치와 서버 heartbeat의 시각 차이가 1초를 초과하면 신규 동작을 거절하고 원인을 보고한다. 설정/시간 동기화 문제를 임의 성공 처리하지 않는다.
- 신규 명령은 일치하는 서버 세션의 heartbeat를 최근 5초 이내 수신했고 만료까지 2초보다 많이 남았을 때만 수락한다.
- 수락 시 expires_at-time.time()-1초를 로컬 monotonic 제한 시간으로 변환한다. 동작 중 시스템 시각 변경으로 제한 시간이 늘어나면 안 된다.
- 서버 heartbeat가 5초 넘게 끊기거나 서버 세션이 바뀌거나 명령이 만료되면 장치의 로컬 정지/취소 절차를 수행하고 실패 결과를 기록한다. 이때도 장비가 실제로 정지했다고 근거 없이 표시하지 않는다.
- 서버는 같은 command_id로 재전송할 수 있다. 실행 전 SQLite에 정규화한 명령 내용과 ID를 기록하고 프로세스 잠금을 둔다. 같은 명령은 다시 움직이지 않고 accepted 또는 저장된 result만 재발행한다.
- 같은 ID에 다른 내용이 오면 프로토콜 오류로 기록하고 실행하지 않는다. 원래 실행 기록/결과를 덮어쓰지 않는다.
- 신규 동작은 기존 result가 ack될 때까지 시작하지 않는다. 완료 후 재시작해도 미확인 결과를 그대로 재전송한다.
- 프로세스 재시작 시 실행 중이었던 명령은 결과 불명확 실패로 기록하고 자동 재실행하지 않는다. 운영자 복구 전 fault 상태를 유지한다.
- JSON 오류·잘못된 타입·다른 장치 명령 때문에 노드가 종료되거나 장비가 움직이면 안 된다.
- 수락/완료/재전송은 짧은 ROS 콜백과 타이머로 처리한다. 실제 동작 대기 때문에 heartbeat, cancel, 상태 발행이 막히면 안 된다.
- 미완성 서버 대신 쓸 테스트 발행기는 simulation 명령만 기본 제공한다. 명령 시각/UUID는 실행 시 새로 만든다. 임시 문자열 echo만으로 실제 완료를 주장하지 않는다.

상태: 프롬프트와 향후 구현을 맞추기 위해 정한 계약이다. 서버와 장치 노드의 구현 완료를 뜻하지 않는다.

