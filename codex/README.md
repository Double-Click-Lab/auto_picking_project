# 세 노트북 연결을 위한 Codex 운영 안내

Ubuntu 22.04 / ROS2 Humble / `Double-Click-Lab/auto_picking_project`.

이 폴더는 `server` 브랜치의 `codex/`에 게시하는 공통 운영 문서다. 각 노트북에서 실행 중인 Codex가 자기 역할과 실행 순서, 대기 조건을 같은 기준으로 확인하게 하는 것이 목적이다. 기본 절차는 실제 장비를 열지 않는 simulation 통합 시험이다.

**처음 시작할 때 [AGENTS.md](AGENTS.md)와 자기 역할 문서를 읽는다. 문서를 읽거나 이 폴더를 내려받았다는 사실만으로 세 노트북이 연결되지는 않는다.**

## 문서와 소스의 위치

| 역할 | 실행 소스 브랜치 | 이 안내의 기본 폴더 | 역할 안내 |
|---|---|---|---|
| 서버 | [server](https://github.com/Double-Click-Lab/auto_picking_project/tree/server) | `~/server` | [server.md](docs/codex/server.md) |
| 미로봇 | [mirobot](https://github.com/Double-Click-Lab/auto_picking_project/tree/mirobot) | `~/ros2_ws` | [mirobot.md](docs/codex/mirobot.md) |
| 카메라·리니어모터 | [camera](https://github.com/Double-Click-Lab/auto_picking_project/tree/camera) | `~/aruco_project` | [camera.md](docs/codex/camera.md) |

공통 문서의 위치는 `server:codex/`이며 세 장치의 소스를 합친 실행 폴더가 아니다. 장치 실행 폴더의 브랜치는 위 표대로 유지한다. 서버의 기존 README 확인본은 [server-readme.md](docs/codex/server-readme.md)에 보존했다.

- [공통 준비·네트워크](docs/codex/common.md)
- [통합 순서·대기 조건·종료·실물 전환](docs/codex/operations.md)
- [역할별 결과 보고 양식](docs/codex/status-template.md)
- [확인한 소스와 버전](docs/codex/sources.json)

## 각 노트북의 Codex에 전달할 프롬프트

아래에서 자기 노트북에 해당하는 것 하나를 전달한다. 이 프롬프트는 로컬 준비와 모의 연결 점검을 요청한다. 실제 장비는 구동하지 않는다.

### 서버 노트북

~~~text
이 노트북은 서버 담당이야.
GitHub Double-Click-Lab/auto_picking_project의 server 브랜치에서
codex/AGENTS.md, codex/README.md, codex/docs/codex/common.md,
codex/docs/codex/server.md, codex/docs/codex/operations.md,
codex/docs/codex/status-template.md를 읽어줘.
GitHub 도구를 사용해 ref=server로 문서를 읽고, 실제 실행은 server 브랜치에서 해줘.
로컬 수정과 기존 설정을 보존하면서 현재 환경을 점검하고 필요한 준비·빌드와
문서의 simulation 서버/중계 실행, 읽기 전용 연결 점검까지 진행해줘.
다른 노트북 준비는 실제 ROS 상태로 확인하고, 세 장치와 미로봇 status가 정상일 때
모의 테스트 주문 1개를 진행해줘. 모의 상자 확인은 실제 상자 확인과 구분해줘.
준비되지 않은 조건은 기다리는 이유와 확인할 신호를 보고해줘.
실물 제어와 기존 세 브랜치의 코드 수정은 하지 말고 결과 양식으로 정리해줘.
~~~

### 미로봇 노트북

~~~text
이 노트북은 미로봇 담당이야.
GitHub Double-Click-Lab/auto_picking_project의 server 브랜치에서
codex/AGENTS.md, codex/README.md, codex/docs/codex/common.md,
codex/docs/codex/mirobot.md, codex/docs/codex/operations.md,
codex/docs/codex/status-template.md를 읽어줘.
GitHub 도구를 사용해 ref=server로 문서를 읽고, 실제 실행은 mirobot 브랜치에서 해줘.
로컬 수정과 좌표를 보존하면서 환경을 점검하고 필요한 준비·빌드 후
simulation 액션 서버와 토픽 어댑터를 실행해줘.
서버 heartbeat를 기다리고 /mirobot/status와 robot 상태를 확인해줘.
독립 시험 발행기는 실행하지 말고, 서버에서 오는 모의 명령을 받을 수 있게 유지해줘.
실제 USB·홈밍·펌프·로봇 이동과 코드 수정은 하지 말고 결과 양식으로 정리해줘.
~~~

### 카메라·리니어모터 노트북

~~~text
이 노트북은 카메라와 리니어모터 담당이야.
GitHub Double-Click-Lab/auto_picking_project의 server 브랜치에서
codex/AGENTS.md, codex/README.md, codex/docs/codex/common.md,
codex/docs/codex/camera.md, codex/docs/codex/operations.md,
codex/docs/codex/status-template.md를 읽어줘.
GitHub 도구를 사용해 ref=server로 문서를 읽고, 실제 실행은 camera 브랜치에서 해줘.
로컬 수정과 기존 장치 설정을 보존하면서 환경을 점검하고 필요한 준비·빌드 후
simulation camera_motor_node 하나를 실행해줘.
motor와 camera 상태를 각각 확인하고 서버 heartbeat와 모의 명령을 기다려줘.
별도 simulation_server는 띄우지 말고, 실제 카메라·USB·모터는 열거나 구동하지 마.
코드 수정 없이 결과 양식으로 정리해줘.
~~~

## 진행 순서

1. 각 노트북의 Codex에 역할별 프롬프트 전달.
2. 세 곳의 LAN·시각·ROS 환경 점검과 역할별 빌드.
3. 서버 FastAPI와 중계 실행.
4. 카메라 노드, 미로봇 액션 서버와 어댑터 실행.
5. 서버 읽기 전용 점검 성공 및 미로봇 status 확인.
6. 서버만 A=1 모의 주문 → motor → camera → robot → waiting_box → 모의 확인 → completed.

기본 총 6개 터미널: 서버 3개, 미로봇 2개, 카메라 1개. 상세 명령은 역할 문서를 따른다.

## 이 문서가 확인한 범위

2026-11-17에 세 원격 브랜치의 소스와 옵션을 대조했다. 확인한 SHA는 sources.json에 있다. 이 안내 작성 과정에서 세 노트북의 LAN 통합이나 실제 장비 구동을 수행하지 않았다. 각 소스 README의 과거 시험 기록은 이번 실행 결과와 구분한다.
