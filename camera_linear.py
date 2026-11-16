import argparse
import queue
import sys
import threading
import time

import cv2
import serial


def create_detector():
    aruco = cv2.aruco

    dictionary = aruco.getPredefinedDictionary(
        aruco.DICT_4X4_50
    )

    if hasattr(aruco, "ArucoDetector"):
        detector = aruco.ArucoDetector(
            dictionary,
            aruco.DetectorParameters(),
        )
        return detector.detectMarkers

    if hasattr(aruco, "DetectorParameters_create"):
        parameters = aruco.DetectorParameters_create()
    else:
        parameters = aruco.DetectorParameters()

    return lambda image: aruco.detectMarkers(
        image,
        dictionary,
        parameters=parameters,
    )


def marker_in_box(
    markers,
    target,
    width,
    height,
    box_width,
    tolerance,
):
    if target not in markers:
        return False

    x, y = markers[target]

    half_width = box_width // 2
    half_height = tolerance

    return (
        abs(x - width / 2) <= half_width
        and abs(y - height / 2) <= half_height
    )


def terminal_reader(commands):
    # 입력을 기다리는 동안에도 카메라는 계속 작동
    for line in sys.stdin:
        commands.put(line.strip())

    commands.put("q")


def connect_arduino(port):
    connection = serial.Serial(
        port,
        115200,
        timeout=0,
        write_timeout=0.2,
        exclusive=True,
    )

    try:
        # USB 연결 시 아두이노 재시작 대기
        time.sleep(2.0)

        connection.reset_input_buffer()
        connection.write(b"STOP 0\n")

        received = b""
        deadline = time.monotonic() + 3.0

        while time.monotonic() < deadline:
            received += connection.read(
                connection.in_waiting or 1
            )

            if b"STOPPED 0\n" in received.replace(b"\r", b""):
                print("아두이노 연결 완료", flush=True)
                return connection

            time.sleep(0.01)

        raise RuntimeError(
            "아두이노 응답 없음. USB 포트와 업로드 상태를 "
            "확인하고 시리얼 모니터/플로터를 닫아주세요."
        )

    except BaseException:
        try:
            connection.write(b"STOP 0\n")
        except Exception:
            pass

        connection.close()
        raise


class MotorController:
    def __init__(self, connection, speed):
        self.connection = connection
        self.speed = speed

        self.current = None
        self.target = None

        self.state = "IDLE"
        self.homing = False

        self.sequence = 0
        self.stop_sequence = None
        self.target_reached = False

        self.stop_started = 0.0
        self.last_heartbeat = 0.0

        self.receive_buffer = ""

    def send(self, message):
        self.connection.write(
            (message + "\n").encode("ascii")
        )

    def move(self, direction):
        self.sequence += 1

        self.send(
            f"{direction} {self.speed} {self.sequence}"
        )

        self.state = "MOVING"
        self.last_heartbeat = 0.0

    def home(self, already_centered):
        if self.state != "IDLE":
            print(
                "먼저 정지한 뒤 h를 입력하세요.",
                flush=True,
            )
            return

        self.current = None
        self.target = 1
        self.homing = True

        if already_centered:
            print(
                "1번 마커가 이미 중앙 박스 안에 있습니다.",
                flush=True,
            )

            self.stop(reached=True)

        else:
            print(
                "초기 위치 찾기: BACK 이동\n"
                "1번 마커 중심이 중앙 박스에 들어오면 정지",
                flush=True,
            )

            self.move("BACK")

    def start_target(self, target, already_centered):
        if self.state != "IDLE":
            print(
                "작업 중입니다. 정지 후 입력하세요.",
                flush=True,
            )
            return

        if self.current is None:
            print(
                "현재 위치를 모릅니다. h를 입력해 "
                "1번 위치를 다시 찾아주세요.",
                flush=True,
            )
            return

        if target == self.current:
            print(
                f"현재 번호가 이미 {target}입니다.",
                flush=True,
            )
            return

        self.target = target
        self.homing = False

        if already_centered:
            print(
                f"{target}번 마커가 이미 중앙에 있습니다.",
                flush=True,
            )

            self.stop(reached=True)
            return

        # 큰 번호: FORWARD
        # 작은 번호: BACK
        direction = (
            "FORWARD"
            if target > self.current
            else "BACK"
        )

        print(
            f"현재 {self.current} -> "
            f"목표 {target}: {direction}",
            flush=True,
        )

        self.move(direction)

    def stop(self, reached=False):
        if self.state == "STOPPING":
            return

        if self.state == "MOVING" and not reached:
            self.current = None

        self.target_reached = reached

        self.sequence += 1
        self.stop_sequence = self.sequence

        self.state = "STOPPING"
        self.stop_started = time.monotonic()

        self.send(f"STOP {self.stop_sequence}")

    def update(self):
        now = time.monotonic()
        count = self.connection.in_waiting

        if count:
            self.receive_buffer += self.connection.read(
                count
            ).decode("ascii", errors="replace")

        while "\n" in self.receive_buffer:
            line, self.receive_buffer = (
                self.receive_buffer.split("\n", 1)
            )

            line = line.strip()

            if not line:
                continue

            expected = f"STOPPED {self.stop_sequence}"

            if (
                self.state == "STOPPING"
                and line == expected
            ):
                if self.target_reached:
                    self.current = self.target

                was_homing = self.homing

                self.state = "IDLE"
                self.target = None
                self.homing = False

                print(
                    "모터 출력 정지 확인. "
                    f"현재 번호: {self.current}",
                    flush=True,
                )

                if was_homing and self.target_reached:
                    print(
                        "초기 위치 설정 완료. "
                        "목표 번호 1~4를 입력하세요.",
                        flush=True,
                    )

            elif line.startswith("FAULT") or line == "READY":
                self.current = None
                self.state = "FAULT"

                self.send("STOP 0")

                raise RuntimeError(
                    f"아두이노 상태 오류: {line}"
                )

        if len(self.receive_buffer) > 4096:
            raise RuntimeError(
                "잘못된 시리얼 응답"
            )

        if self.state == "MOVING":
            # 이동 시간 제한 없음.
            # 정상 통신 중이라는 신호만 계속 보냄.
            if now - self.last_heartbeat >= 0.15:
                self.send("HB")
                self.last_heartbeat = now

        elif self.state == "STOPPING":
            # 이동 시간 제한이 아니라 정지 응답 확인
            if now - self.stop_started > 1.0:
                self.current = None

                raise RuntimeError(
                    "정지 응답 없음. 모터 상태를 확인하세요."
                )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        default="/dev/ttyACM0",
    )

    parser.add_argument(
        "--camera",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--speed",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--box-width",
        type=int,
        default=40,
    )

    parser.add_argument(
        "--tolerance",
        type=int,
        default=60,
    )

    args = parser.parse_args()

    if not 1 <= args.speed <= 255:
        parser.error(
            "speed는 1~255여야 합니다."
        )

    if args.box_width < 2 or args.box_width % 2 != 0:
        parser.error(
            "box-width는 2 이상의 짝수여야 합니다."
        )

    if args.tolerance <= 0:
        parser.error(
            "tolerance는 양수여야 합니다."
        )

    detect = create_detector()

    connection = None
    camera = None

    try:
        connection = connect_arduino(args.port)

        motor = MotorController(
            connection,
            args.speed,
        )

        camera = cv2.VideoCapture(
            args.camera,
            cv2.CAP_V4L2,
        )

        camera.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            640,
        )

        camera.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            480,
        )

        camera.set(
            cv2.CAP_PROP_BUFFERSIZE,
            1,
        )

        if not camera.isOpened():
            raise RuntimeError(
                f"카메라 {args.camera}를 열 수 없습니다."
            )

        commands = queue.Queue()

        threading.Thread(
            target=terminal_reader,
            args=(commands,),
            daemon=True,
        ).start()

        half_width = args.box_width // 2
        half_height = args.tolerance

        print(
            "\n"
            "자동 초기화: BACK -> 1번 중앙 정렬 -> 정지\n"
            f"중앙 박스: 가로 {args.box_width} "
            f"x 세로 {args.tolerance * 2} 픽셀\n"
            f"모터 출력: {args.speed}\n"
            "이동 시간 제한 없음\n"
            "\n"
            "터미널 조작:\n"
            "1~4 + Enter: 목표 번호\n"
            "s + Enter: 정지\n"
            "h + Enter: 1번 위치 다시 찾기\n"
            "q + Enter: 종료\n"
            "\n"
            "카메라 창: Space로 정지, q로 종료\n",
            flush=True,
        )

        startup = True

        while True:
            motor.update()

            success, frame = camera.read()

            if not success:
                raise RuntimeError(
                    "카메라 영상을 읽지 못했습니다."
                )

            height, width = frame.shape[:2]

            gray = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY,
            )

            corners, ids, _ = detect(gray)
            markers = {}

            if ids is not None:
                cv2.aruco.drawDetectedMarkers(
                    frame,
                    corners,
                    ids,
                )

                for corner, marker_id in zip(
                    corners,
                    ids.flatten(),
                ):
                    marker_id = int(marker_id)

                    center = corner.reshape(
                        4, 2
                    ).mean(axis=0)

                    x = float(center[0])
                    y = float(center[1])

                    markers[marker_id] = (x, y)

                    cv2.circle(
                        frame,
                        (int(x), int(y)),
                        5,
                        (0, 0, 255),
                        -1,
                    )

                    cv2.line(
                        frame,
                        (width // 2, height // 2),
                        (int(x), int(y)),
                        (0, 255, 255),
                        1,
                    )

            if startup:
                motor.home(
                    already_centered=marker_in_box(
                        markers,
                        1,
                        width,
                        height,
                        args.box_width,
                        args.tolerance,
                    )
                )

                startup = False

            if motor.state == "MOVING":
                if marker_in_box(
                    markers,
                    motor.target,
                    width,
                    height,
                    args.box_width,
                    args.tolerance,
                ):
                    print(
                        f"{motor.target}번 마커 중심이 "
                        "중앙 박스에 들어옴 -> 정지",
                        flush=True,
                    )

                    motor.stop(reached=True)

            active_target = (
                motor.target
                if motor.target is not None
                else motor.current
            )

            centered = marker_in_box(
                markers,
                active_target,
                width,
                height,
                args.box_width,
                args.tolerance,
            )

            box_color = (
                (0, 255, 0)
                if centered
                else (255, 150, 0)
            )

            # 실제 정지 판정과 동일한 크기
            cv2.rectangle(
                frame,
                (
                    width // 2 - half_width,
                    height // 2 - half_height,
                ),
                (
                    width // 2 + half_width,
                    height // 2 + half_height,
                ),
                box_color,
                2,
            )

            cv2.circle(
                frame,
                (width // 2, height // 2),
                4,
                (255, 255, 255),
                -1,
            )

            label = (
                "HOMING"
                if motor.homing
                else motor.state
            )

            cv2.putText(
                frame,
                f"{label} "
                f"current={motor.current} "
                f"target={motor.target}",
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                2,
            )

            cv2.putText(
                frame,
                "CENTER OK" if centered else "OFF CENTER",
                (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                box_color,
                2,
            )

            cv2.putText(
                frame,
                f"box={args.box_width}x{args.tolerance * 2} "
                f"speed={args.speed}",
                (10, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

            cv2.putText(
                frame,
                "Space=STOP  q=QUIT",
                (10, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

            cv2.imshow("ArUco Linear", frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if key in (ord("s"), ord(" ")):
                motor.stop(reached=False)

            if cv2.getWindowProperty(
                "ArUco Linear",
                cv2.WND_PROP_VISIBLE,
            ) < 1:
                break

            if not commands.empty():
                command = commands.get()

                if command == "q":
                    break

                elif command == "s":
                    motor.stop(reached=False)

                elif command == "h":
                    motor.home(
                        already_centered=marker_in_box(
                            markers,
                            1,
                            width,
                            height,
                            args.box_width,
                            args.tolerance,
                        )
                    )

                elif command in ("1", "2", "3", "4"):
                    target = int(command)

                    already_centered = marker_in_box(
                        markers,
                        target,
                        width,
                        height,
                        args.box_width,
                        args.tolerance,
                    )

                    motor.start_target(
                        target,
                        already_centered,
                    )

                elif command:
                    print(
                        "1~4, s, h, q 중 하나를 입력하세요.",
                        flush=True,
                    )

    finally:
        if connection is not None:
            try:
                connection.write(b"STOP 999999\n")
                time.sleep(0.1)

            except Exception:
                pass

            connection.close()

        if camera is not None:
            camera.release()

        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n종료했습니다.")

    except Exception as error:
        print(
            f"\n오류: {error}",
            file=sys.stderr,
        )

        sys.exit(1)
