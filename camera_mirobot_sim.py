import time

import cv2
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import JointState
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints, JointConstraint


# ================= 설정 =================

CAMERA_INDEX = 2
GROUP_NAME = "mirobot_group"

JOINT_NAMES = [
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
]

DETECT_SECONDS = 0.5
CLEAR_SECONDS = 1.0

# RViz에서 확인한 새 목표 자세
# 단위: 라디안
TARGET_POSE = {
    "joint1": -1.3418052396586206,
    "joint2": 1.1845714535152645,
    "joint3": -0.9045956354886513,
    "joint4": 0.0000870054231464611,
    "joint5": -0.2800306495173888,
    "joint6": 1.3418329313909132,
}

# ========================================


class RobotTask(Node):
    def __init__(self):
        super().__init__("camera_mirobot_task")

        self.positions = {}
        self.initial_pose = None
        self.last_state_time = 0.0

        self.phase = "WAITING"
        self.status = "WAITING FOR ROBOT"

        self.subscription = self.create_subscription(
            JointState,
            "/joint_states",
            self.receive_state,
            qos_profile_sensor_data,
        )

        self.client = ActionClient(
            self,
            MoveGroup,
            "/move_action",
        )

    def receive_state(self, msg):
        self.positions.update(zip(msg.name, msg.position))
        self.last_state_time = time.monotonic()

        # 初回に受信した全関節の姿勢を開始姿勢として保存
        if (
            self.initial_pose is None
            and all(name in self.positions for name in JOINT_NAMES)
        ):
            self.initial_pose = {
                name: float(self.positions[name])
                for name in JOINT_NAMES
            }

            self.phase = "READY"
            self.status = "REMOVE MARKERS TO ARM"

            print(
                "시작 자세 저장 완료. 마커를 화면에서 치워 주세요.",
                flush=True,
            )

    def start_task(self, marker_id):
        if self.phase != "READY":
            return False

        if time.monotonic() - self.last_state_time > 2.0:
            self.status = "JOINT STATES NOT READY"
            return False

        if not self.client.server_is_ready():
            self.status = "MOVEIT NOT READY"
            return False

        self.phase = "TASK"
        self.status = f"MOVING TO TARGET: ID {marker_id}"

        print(
            f"마커 {marker_id}: 지정한 목표 자세로 이동",
            flush=True,
        )

        self.send_pose(
            TARGET_POSE.copy(),
            returning=False,
        )
        return True

    def send_pose(self, target, returning):
        try:
            goal = MoveGroup.Goal()
            request = goal.request

            request.group_name = GROUP_NAME
            request.allowed_planning_time = 5.0
            request.num_planning_attempts = 3

            request.max_velocity_scaling_factor = 0.1
            request.max_acceleration_scaling_factor = 0.1

            # 실제 현재 상태에서 계획 시작
            request.start_state.is_diff = True

            constraints = Constraints()

            for name in JOINT_NAMES:
                joint = JointConstraint()
                joint.joint_name = name
                joint.position = float(target[name])
                joint.tolerance_above = 0.01
                joint.tolerance_below = 0.01
                joint.weight = 1.0

                constraints.joint_constraints.append(joint)

            request.goal_constraints = [constraints]

            options = goal.planning_options
            options.plan_only = False
            options.planning_scene_diff.is_diff = True
            options.planning_scene_diff.robot_state.is_diff = True

            future = self.client.send_goal_async(goal)

            future.add_done_callback(
                lambda result: self.goal_response(
                    result,
                    returning,
                )
            )

        except Exception as error:
            self.fail(str(error))

    def goal_response(self, future, returning):
        try:
            handle = future.result()

            if not handle.accepted:
                self.fail("Goal rejected")
                return

            result_future = handle.get_result_async()

            result_future.add_done_callback(
                lambda result: self.motion_finished(
                    result,
                    returning,
                )
            )

        except Exception as error:
            self.fail(str(error))

    def motion_finished(self, future, returning):
        try:
            code = future.result().result.error_code.val

            if code != 1:
                self.fail(f"MoveIt error: {code}")
                return

            if not returning:
                self.phase = "RETURNING"
                self.status = "RETURNING TO START"

                print(
                    "목표 자세 도착 → 시작 자세로 복귀",
                    flush=True,
                )

                self.send_pose(
                    self.initial_pose.copy(),
                    returning=True,
                )

            else:
                self.phase = "READY"
                self.status = "DONE: REMOVE MARKERS TO ARM"

                print(
                    "복귀 완료. 다음 실행을 위해 마커를 "
                    "1초 이상 치워 주세요.",
                    flush=True,
                )

        except Exception as error:
            self.fail(str(error))

    def fail(self, message):
        # 실패하면 자동 재실행하지 않음
        self.phase = "ERROR"
        self.status = "ERROR: CHECK TERMINAL"

        print(
            "작업 중단:",
            message,
            flush=True,
        )


def main():
    rclpy.init()
    robot = RobotTask()
    cap = None

    try:
        dictionary = cv2.aruco.getPredefinedDictionary(
            cv2.aruco.DICT_4X4_50
        )

        detector = cv2.aruco.ArucoDetector(
            dictionary,
            cv2.aruco.DetectorParameters(),
        )

        cap = cv2.VideoCapture(CAMERA_INDEX)

        if not cap.isOpened():
            raise RuntimeError(
                f"Cannot open camera {CAMERA_INDEX}"
            )

        # 처음에도 마커 없는 화면을 확인한 뒤 실행 준비
        armed = False
        clear_since = None

        candidate_id = None
        candidate_since = None

        while rclpy.ok():
            rclpy.spin_once(robot, timeout_sec=0.0)

            success, frame = cap.read()

            if not success:
                print(
                    "Camera read failed.",
                    flush=True,
                )
                break

            # 화면에 그림을 그리기 전에 원본에서 마커 검출
            gray = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY,
            )

            corners, ids, _ = detector.detectMarkers(gray)

            height, width = frame.shape[:2]
            ox = width // 2
            oy = height // 2

            # 화면 중앙 좌표축
            cv2.line(
                frame,
                (0, oy),
                (width - 1, oy),
                (120, 120, 120),
                1,
            )

            cv2.line(
                frame,
                (ox, 0),
                (ox, height - 1),
                (120, 120, 120),
                1,
            )

            cv2.drawMarker(
                frame,
                (ox, oy),
                (0, 255, 255),
                cv2.MARKER_CROSS,
                20,
                2,
            )

            visible_ids = set()
            row = 0

            if ids is not None:
                cv2.aruco.drawDetectedMarkers(
                    frame,
                    corners,
                    ids,
                )

                for points, marker_id in zip(
                    corners,
                    ids.flatten(),
                ):
                    marker_id = int(marker_id)

                    if marker_id not in (1, 2, 3, 4):
                        continue

                    visible_ids.add(marker_id)

                    cx, cy = points.reshape(4, 2).mean(axis=0)
                    center = (int(cx), int(cy))

                    # 화면 중앙 기준 좌표
                    # 오른쪽 +x, 위쪽 +y
                    x = float(cx - ox)
                    y = float(oy - cy)

                    # 중앙과 마커를 연결하는 선
                    cv2.line(
                        frame,
                        (ox, oy),
                        center,
                        (0, 255, 255),
                        2,
                    )

                    # 마커 중심점
                    cv2.circle(
                        frame,
                        center,
                        6,
                        (0, 0, 255),
                        -1,
                    )

                    cv2.putText(
                        frame,
                        (
                            f"ID {marker_id}: "
                            f"x={x:+.0f}, y={y:+.0f} px"
                        ),
                        (10, 110 + row * 25),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )

                    row += 1

            now = time.monotonic()

            if robot.phase == "READY":

                if not armed:
                    candidate_id = None
                    candidate_since = None

                    # 복귀 후 마커가 1초 이상 없어야 재준비
                    if not visible_ids:
                        if clear_since is None:
                            clear_since = now

                        if now - clear_since >= CLEAR_SECONDS:
                            armed = True
                            clear_since = None
                            robot.status = "READY: SHOW MARKER 1-4"

                            print(
                                "준비 완료: 마커를 보여 주세요.",
                                flush=True,
                            )
                    else:
                        clear_since = None

                else:
                    clear_since = None

                    if visible_ids:
                        # 여러 마커가 있으면 하나만 선택
                        if candidate_id not in visible_ids:
                            candidate_id = min(visible_ids)
                            candidate_since = now

                        if (
                            now - candidate_since
                            >= DETECT_SECONDS
                        ):
                            if robot.start_task(candidate_id):
                                armed = False
                                candidate_id = None
                                candidate_since = None
                                clear_since = None

                    else:
                        candidate_id = None
                        candidate_since = None

            else:
                # 작업 및 복귀 중에는 새 작업 요청 차단
                candidate_id = None
                candidate_since = None
                clear_since = None

            if robot.phase == "ERROR":
                color = (0, 0, 255)

            elif armed and robot.phase == "READY":
                color = (0, 255, 0)

            else:
                color = (0, 255, 255)

            cv2.putText(
                frame,
                robot.status,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )

            cv2.putText(
                frame,
                f"Detected: {sorted(visible_ids)}",
                (10, 55),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
            )

            cv2.putText(
                frame,
                "ARMED" if armed else "LOCKED",
                (10, 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )

            cv2.putText(
                frame,
                "Q: quit when idle",
                (10, height - 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
            )

            cv2.imshow(
                "MiRobot Camera Task",
                frame,
            )

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                if robot.phase not in (
                    "TASK",
                    "RETURNING",
                ):
                    break

                print(
                    "이동 완료 후 Q를 다시 눌러 주세요.",
                    flush=True,
                )

    except KeyboardInterrupt:
        pass

    finally:
        if cap is not None:
            cap.release()

        cv2.destroyAllWindows()
        robot.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()