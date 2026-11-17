# 세 노트북 Codex 작업 지침

대상 저장소: `Double-Click-Lab/auto_picking_project`. 이 파일은 `server` 브랜치의 `codex/` 폴더에 있는 운영 문서 진입점이다.

## 읽는 순서와 역할

1. [README.md](README.md), [공통 준비](docs/codex/common.md), [통합 순서와 복구](docs/codex/operations.md)를 읽는다.
2. 사용자가 지정한 역할에 따라 [서버](docs/codex/server.md), [미로봇](docs/codex/mirobot.md), [카메라·리니어모터](docs/codex/camera.md) 중 하나를 읽는다.
3. 역할이 대화에서 이미 정해졌으면 다시 묻지 않는다. 역할이 불명확하면 OS·경로·Git remote·브랜치·실행 프로세스를 읽기 전용으로 점검한 뒤 역할만 확인한다. 호스트명이나 현재 폴더 이름만으로 역할을 확정하지 않는다.

## 작업 범위

- `server` 브랜치의 `codex/`는 공통 운영 문서 폴더다. 실제 실행 폴더는 각각 `server`, `mirobot`, `camera` 브랜치다. 미로봇·카메라 실행 폴더를 문서 열람을 위해 다른 브랜치로 전환하지 않는다.
- GitHub에서 읽을 때 문서의 ref는 `server`, 경로 접두사는 `codex/`로 지정한다. 저장소 루트 README만 읽고 준비를 완료했다고 하지 않는다. 상대 링크는 `codex/` 내부 문서 위치를 기준으로 같은 ref에서 읽는다.
- 문서는 자동으로 다른 노트북에 적용되지 않는다. 각 노트북의 사용자가 README의 자기 역할용 프롬프트를 해당 Codex에 전달해야 한다.
- 우선 현재 사용자 요청 범위에서 로컬 검사·준비·빌드·모의 시험을 진행한다. 이미 허용된 작업을 반복 확인하지 않는다. OS/도구가 요구하는 설치·권한 승인은 해당 환경의 승인 절차를 따른다.
- 문서 읽기 요청만 받았다면 프로그램을 시작하지 않는다. 실행 요청을 받은 경우에만 실행하며, 기본 통합 모드는 `simulation`이다.
- 사용자의 실물 시험 지시와 현장 설정 확인 없이 `real` 전환, initialize/홈밍, 모터 이동, 펌프 동작을 수행하지 않는다. 예시 좌표를 실물 좌표라고 취급하지 않는다.
- 로컬 수정, `.env`, 실제 DB/journal, teach 좌표를 보존한다. 강제 reset/clean, DB 삭제·교체, 포트 충돌 프로세스 일괄 종료로 문제를 숨기지 않는다.
- 사용자 지시 없이 기존 세 소스 브랜치나 공통 통신 규약을 변경하지 않는다. 불일치는 정확한 소스 SHA와 증상을 먼저 보고한다.
- API_KEY/DEVICE_KEY, `.env` 원문, 개인정보가 든 DB나 실제 좌표 전체를 GitHub나 결과 보고서에 올리지 않는다.

## 역할별 소유권과 진행 기준

- 서버만 통합 시험의 heartbeat와 motor/camera/robot 명령을 발행한다. 별도 simulation_server·picking_sim_robot·수동 토픽 발행기를 함께 띄우지 않는다.
- 미로봇은 기존 `/pick_place` 액션 서버 하나가 제어를 담당하고 토픽 어댑터 하나가 서버와 연결한다.
- 카메라·모터는 하나의 `camera_motor_node`가 motor와 camera 둘 다 담당한다.
- 세 곳 모두 Humble, `ROS_DOMAIN_ID=7`, `ROS_LOCALHOST_ONLY=0`, 이 안내에서는 `rmw_fastrtps_cpp`를 사용한다. 모든 새 ROS 터미널에 적용한다.
- 기다리는 이유를 `LOCAL_PREPARED`, `WAITING_SERVER`, `READY_SIMULATION`, `BLOCKED` 등으로 구분하고 근거를 보고한다. 이 이름들은 사람용 보고 분류이며 ROS state나 새 프로토콜 필드가 아니다.
- `ready`만 보고 집기 성공을 주장하지 않는다. 서버의 읽기 전용 점검과 모의 주문 completed까지 서로 다른 검증 단계로 보고한다.
- 실행 프로세스의 소유 세션·PID·로그 위치를 남기고 요청 종료 후 생존 여부를 확인한다. 실행 중인 장기 프로세스 때문에 도구가 멈추면 조회용 터미널을 따로 사용한다.
- 다른 노트북에 직접 접근했다고 가정하지 않는다. 실제 ROS 수신과 해당 노트북에서 받은 결과를 구분한다. 공유 파일이나 채팅의 준비 보고는 현재 ROS 상태를 대체하지 않는다.
- 작업 결과는 [보고 양식](docs/codex/status-template.md)에 맞춰 로컬에 남기고 사용자에게 요약한다. 세 노트북 LAN 통합이나 실물 시험을 수행하지 않았다면 명시한다.
