# 역할별 결과 보고 양식

아래를 복사해 로컬 보고서나 사용자 응답에 작성한다. 빈 값은 미확인으로 남긴다. 이 템플릿은 현재 준비 상태를 주장하는 보고서가 아니다.

자동으로 GitHub에 업로드하거나 다른 노트북에 메시지를 보내는 절차는 없다. 사용자가 결과 공유를 요청한 경우에만 공유하며 비밀 값과 개인정보를 제외한다.

~~~text
역할: server / mirobot / camera 중 하나
보고 시각: 현지 시간과 시간대
현재 단계: LOCAL_PREPARED / WAITING_SERVER / WAITING_DEVICES /
           READY_SIMULATION / TEST_COMPLETED / BLOCKED 중 해당 값
※ 위 단계명은 사람용 분류이며 ROS state가 아님

확인한 문서: server 브랜치 codex/ 문서 커밋 SHA
실행 저장소 경로:
실행 소스 브랜치 / HEAD SHA:
로컬 수정 유무와 보존 내용:
Ubuntu / ROS / 시스템 Python:
LAN 인터페이스와 IP: 사용자에게 필요한 범위만
ROS_DOMAIN_ID / ROS_LOCALHOST_ONLY / RMW_IMPLEMENTATION:
시간 동기화 상태:

의존성 설치 / 빌드 결과:
실행 프로그램 / PID / 유지 세션 / 로그 경로:
현재도 프로세스가 살아 있는지:
mode: simulation / real
heartbeat: 실제 수신 여부 / session / freshness
device_status: motor / camera / robot 중 실제 관찰한 항목, state, mode
mirobot status와 /pick_place 확인: 해당 역할만

실제로 수행한 네트워크 시험:
- 로컬 시험인지 노트북 간 시험인지:
- 상대 노트북 / 방향 / 결과:

서버 읽기 전용 점검: 실행 여부 / 실제 결과
모의 주문: 미실행이면 미실행 / order_id / 관찰한 단계 / 최종 상태
모의 상자 확인 여부:
실물 장비 사용 여부:

현재 기다리는 조건:
상대 역할이 해야 하는 다음 작업:
오류 원문 요약 / 저장 위치:
다음에 실행할 작업:
~~~

API_KEY/DEVICE_KEY, .env 원문, 고객 주문 데이터, DB 파일, 실측 좌표 전체는 첨부하지 않는다. 오류에서 이러한 값이 포함됐으면 마스킹한다.

실제 측정 없이 다른 노트북 준비 완료·LAN 왕복 완료·실물 성공으로 채우지 않는다. 문서에 있는 과거 테스트 결과도 이번 실행 결과로 복사하지 않는다.
