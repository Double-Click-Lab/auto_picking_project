import cv2

dictionary = cv2.aruco.getPredefinedDictionary(
    cv2.aruco.DICT_4X4_50
)

for marker_id in (1, 2, 3, 4):
    marker = cv2.aruco.generateImageMarker(
        dictionary, marker_id, 400
    )

    # 검은 테두리 바깥에 흰 여백 추가
    marker = cv2.copyMakeBorder(
        marker,
        60, 60, 60, 60,
        cv2.BORDER_CONSTANT,
        value=255
    )

    filename = f"marker_{marker_id}.png"
    cv2.imwrite(filename, marker)
    print(f"Saved: {filename}")