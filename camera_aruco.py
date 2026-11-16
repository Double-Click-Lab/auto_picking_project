import cv2

CAMERA_INDEX = 0# 현재 웹캠이 작동하는 번호
CENTER_TOLERANCE = 40  # 중앙 허용 범위, 픽셀

dictionary = cv2.aruco.getPredefinedDictionary(
    cv2.aruco.DICT_4X4_50
)
parameters = cv2.aruco.DetectorParameters()
detector = cv2.aruco.ArucoDetector(dictionary, parameters)

cap = cv2.VideoCapture(CAMERA_INDEX)

if not cap.isOpened():
    raise RuntimeError("Cannot open camera.")

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

try:
    while True:
        success, frame = cap.read()

        if not success:
            print("Failed to read camera frame.")
            break

        # 그림을 그리기 전에 원본에서 마커 검출
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = detector.detectMarkers(gray)

        height, width = frame.shape[:2]
        origin_x = width // 2
        origin_y = height // 2

        # 화면 중앙을 지나는 좌표축
        cv2.line(
            frame, (0, origin_y), (width - 1, origin_y),
            (130, 130, 130), 1
        )
        cv2.line(
            frame, (origin_x, 0), (origin_x, height - 1),
            (130, 130, 130), 1
        )

        centered_ids = []
        detected_ids = []
        text_row = 0

        if ids is not None:
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)

            for marker_corners, marker_id in zip(
                corners, ids.flatten()
            ):
                marker_id = int(marker_id)

                if marker_id not in (1, 2, 3, 4):
                    continue

                detected_ids.append(marker_id)

                # 마커 네 꼭짓점의 평균 = 마커 중심
                points = marker_corners.reshape(4, 2)
                center_x, center_y = points.mean(axis=0)

                # 화면 중앙 기준 좌표, 위쪽이 y 양수
                x = float(center_x - origin_x)
                y = float(origin_y - center_y)

                is_centered = (
                    abs(x) <= CENTER_TOLERANCE
                    and abs(y) <= CENTER_TOLERANCE
                )

                if is_centered:
                    centered_ids.append(marker_id)
                    color = (0, 255, 0)
                    state = "CENTER OK"
                else:
                    color = (0, 0, 255)
                    state = "OFF CENTER"

                marker_center = (
                    int(round(center_x)),
                    int(round(center_y))
                )

                cv2.circle(frame, marker_center, 6, color, -1)
                cv2.line(
                    frame,
                    (origin_x, origin_y),
                    marker_center,
                    color,
                    2
                )

                # 각 마커의 좌표와 상태 표시
                label = (
                    f"ID {marker_id}: "
                    f"x={x:+.0f}, y={y:+.0f} px | {state}"
                )

                cv2.putText(
                    frame,
                    label,
                    (10, 65 + text_row * 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1
                )

                text_row += 1

        # 하나 이상의 마커 중심이 중앙 영역 안에 있으면 초록색
        if centered_ids:
            box_color = (0, 255, 0)
            status = "CENTER OK: " + ", ".join(
                str(i) for i in sorted(centered_ids)
            )
        elif detected_ids:
            box_color = (0, 0, 255)
            status = "OFF CENTER"
        else:
            box_color = (0, 255, 255)
            status = "NO TARGET MARKER"

        cv2.rectangle(
            frame,
            (
                origin_x - CENTER_TOLERANCE,
                origin_y - CENTER_TOLERANCE
            ),
            (
                origin_x + CENTER_TOLERANCE,
                origin_y + CENTER_TOLERANCE
            ),
            box_color,
            2
        )

        cv2.drawMarker(
            frame,
            (origin_x, origin_y),
            box_color,
            cv2.MARKER_CROSS,
            16,
            2
        )

        cv2.putText(
            frame,
            status,
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            box_color,
            2
        )

        cv2.imshow("ArUco Position - Q to quit", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()