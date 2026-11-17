# 공통 준비

세 노트북 Codex 모두 이 문서를 먼저 읽고 자기 역할 문서로 이동한다.

## 1. 변경 전 로컬 환경 확인

~~~bash
pwd
lsb_release -ds
uname -m
/usr/bin/python3 --version
ip -br addr
timedatectl status
~~~

저장소 폴더를 찾았을 때 다음을 실행한다.

~~~bash
git rev-parse --show-toplevel
git remote -v
git status --short --branch
git branch --show-current
git rev-parse HEAD
~~~

Git 메타데이터가 없거나 읽기 제한으로 실패한 경우를 구분해서 보고한다. 실패를 이유로 현재 폴더를 재초기화하거나 clone으로 덮지 않는다. 기존 파일의 실제 경로와 내용부터 확인한다.

사용자의 역할 지정을 우선한다. 다른 노트북과 사용자명·설치 경로·ROS overlay가 같다고 가정하지 않는다. API 키 원문이나 전체 환경변수를 출력하지 않는다.

## 2. 소스 준비

처음 설치하고 대상 폴더가 아직 없을 때 자기 역할 명령 하나만 실행한다.

~~~bash
# 서버
git clone --branch server --single-branch https://github.com/Double-Click-Lab/auto_picking_project.git ~/server
~~~

~~~bash
# 미로봇
git clone --branch mirobot --single-branch https://github.com/Double-Click-Lab/auto_picking_project.git ~/ros2_ws
~~~

~~~bash
# 카메라·모터
git clone --branch camera --single-branch https://github.com/Double-Click-Lab/auto_picking_project.git ~/aruco_project
~~~

기존 저장소는 수정사항 확인 후 자기 역할 브랜치에서 `git pull --ff-only origin 브랜치명`으로 갱신한다. 브랜치 전환 전 미커밋 변경을 보존한다. 충돌·분기 때문에 갱신이 거절되면 강제 초기화하지 않는다.

소스 기준은 [sources.json](sources.json)에 기록했다. 최신 HEAD가 다르면 실제 SHA와 바뀐 실행 옵션·계약을 확인하고 보고한다. 기록된 SHA로 강제로 되돌리지는 않는다.

GitHub 플러그인으로 ref=server, 경로 codex/README.md부터 문서를 읽는 방법이 우선이다. 문서 checkout이 꼭 필요하면 기존 실행 폴더와 다른 새 폴더에 server 브랜치를 clone하고 codex/ 폴더를 읽는다. 미로봇·카메라 실행 폴더의 브랜치를 문서 열람을 위해 전환하지 않는다.

## 3. Ubuntu·ROS2와 기본 도구

Ubuntu 22.04, ROS2 Humble, 시스템 Python 3.10을 기준으로 한다.

~~~bash
ls /opt/ros/humble/setup.bash
~~~

없다면 [공식 Humble 설치 안내](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)를 따라 ROS apt 저장소와 Humble을 설치한다. ROS 설치가 끝난 세 곳에서:

~~~bash
sudo apt update
sudo apt install git build-essential python3-venv python3-colcon-common-extensions python3-rosdep ros-humble-rclpy ros-humble-std-msgs ros-humble-demo-nodes-cpp ros-humble-rmw-fastrtps-cpp
~~~

권한이 필요한 설치는 해당 노트북의 도구 승인 절차를 따른다. 서버 HTTP용 .venv 이외에 ROS 실행용 가상환경을 새로 만들지 않는다. Conda나 다른 Python 환경이 활성화되어 있다면 ROS 실행 터미널에서 벗어난 뒤 시스템 Python을 사용한다.

## 4. 모든 새 ROS 터미널의 공통 설정

~~~bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=7
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
~~~

그 다음 역할별 overlay를 source한다.

| 역할 | 빌드 후 추가 설정 |
|---|---|
| 서버 | 별도 colcon overlay 없음. 중계는 /usr/bin/python3 사용 |
| 미로봇 | `source ~/ros2_ws/install/setup.bash` |
| 카메라 | `source ~/aruco_project/install/setup.bash` |

~~~bash
printenv ROS_DISTRO ROS_DOMAIN_ID ROS_LOCALHOST_ONLY RMW_IMPLEMENTATION
/usr/bin/python3 -c 'import rclpy; from std_msgs.msg import String; print("ROS imports OK")'
~~~

환경 출력 순서는 humble / 7 / 0 / rmw_fastrtps_cpp여야 한다. 미로봇 어댑터는 7/0을 검사한다. 다른 시험용 도메인 179를 섞지 않는다. 환경을 바꿨으면 조회 터미널에서 `ros2 daemon stop`을 실행한 뒤 다시 조회한다.

## 5. LAN과 시간

서로 통신 가능한 같은 LAN에 연결한다. 각 IP·서브넷·사용 인터페이스를 결과에 기록한다. 휴대폰 게스트망, AP/클라이언트 격리, VPN 또는 멀티캐스트 차단이 있으면 서로 발견되지 않을 수 있다.

`ping -c 3 상대IP`로 다른 두 노트북을 확인한다. IP는 실제 값으로 바꾼다. ROS2는 DDS로 발견하며 각 장치에 서버 HTTP 주소를 설정하는 구조가 아니다. 중계의 127.0.0.1은 서버 노트북 내부의 FastAPI다.

~~~bash
timedatectl status
~~~

NTP가 꺼져 있으면:

~~~bash
sudo timedatectl set-ntp true
~~~

System clock synchronized: yes를 확인한다. chrony 등 별도 시간 동기화 환경은 그 서비스의 상태를 확인한다. 세 노트북과 heartbeat의 실제 시각 차이가 1초 이내여야 한다. 시간대를 같게 표시하는 것만으로 시계가 동기화되지는 않는다. 코드의 시각 검사를 해제하지 않는다.

## 6. 프로젝트 실행 전 통신 시험

각 시험 터미널에 4절 설정을 적용한다.

수신 노트북:

~~~bash
ros2 multicast receive
~~~

송신 노트북:

~~~bash
ros2 multicast send
~~~

다른 노트북에서 보낸 Hello World와 발신 IP 수신을 확인한다. 서버↔미로봇, 서버↔카메라 양방향으로 시험하고 어느 방향을 실제 확인했는지 기록한다. 한 노트북 안에서의 성공은 LAN 통신 증거가 아니다.

다음으로 서버에서:

~~~bash
ros2 run demo_nodes_cpp talker
~~~

미로봇·카메라에서 각각:

~~~bash
ros2 run demo_nodes_cpp listener
~~~

두 곳의 I heard 수신을 확인한다. 시험 종료 시 자신의 demo 프로세스만 Ctrl+C로 종료한다.

실패하면 먼저:

~~~bash
sudo ufw status
~~~

방화벽이 활성화되어 있다면 신뢰할 수 있는 시험 LAN의 UDP 통신을 허용하도록 설정한다. 예를 들어 실제 LAN이 192.168.0.0/24인 경우에만 다음 규칙을 사용할 수 있다.

~~~bash
sudo ufw allow from 192.168.0.0/24 to any proto udp
~~~

예시 서브넷을 그대로 적용하지 말고 실제 네트워크에 맞춘다. 다른 서비스를 포함한 방화벽 전체를 끄는 방식으로 해결하지 않는다. 기존 DDS XML·Discovery Server 환경설정이 있으면 이름과 적용 여부를 확인하고 기존 의도를 보존한다.

## 7. 프로세스 유지와 자료 보존

연결 요청은 장기 실행을 포함한다. 터미널을 계속 열거나 해당 환경에서 지속되는 세션을 사용한다. Codex의 도구가 반환한 실행 세션 ID, 프로세스 PID, 로그 위치를 로컬 보고에 남긴다. 세션이 턴 종료 후에도 유지되는지는 실제 확인한다. 유지되지 않는 환경이면 사용자 터미널에서 실행할 정확한 명령과 실행하지 못한 이유를 전달한다.

새로 시작하기 전 기존 역할 프로세스와 소유자를 확인한다. 이미 정상 실행 중인 동일 역할 프로세스를 재실행하지 않는다. 다른 사람의 프로세스나 장비 제어 프로그램을 임의 종료하지 않는다.

각 노트북의 /tmp 경로는 서로 다른 로컬 파일이다. 공유 디스크로 맞출 필요가 없다. 모의 DB도 실행 중이거나 결과 미확인 상태에서는 지우지 않는다. /tmp는 재부팅 후 사라질 수 있으며 실물 운용에는 고정 영속 경로를 사용한다.

## 공식 참고

- [환경 설정](https://docs.ros.org/en/humble/Tutorials/Beginner-CLI-Tools/Configuring-ROS2-Environment.html)
- [멀티캐스트와 통신 문제](https://docs.ros.org/en/humble/How-To-Guides/Installation-Troubleshooting.html)
- [RMW 설정](https://docs.ros.org/en/humble/How-To-Guides/Working-with-multiple-RMW-implementations.html)
- [rosdep](https://docs.ros.org/en/humble/Tutorials/Intermediate/Rosdep.html)

공식 사이트 열람이 막히면 [공식 문서 저장소 humble 소스](https://github.com/ros2/ros2_documentation/tree/humble/source)를 참고한다.
