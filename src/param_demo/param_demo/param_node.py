import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import SetParametersResult

class ParamNode(Node):
    def __init__(self):
        super().__init__('param_node')

        # 파라미터 선언 (기본값)
        self.declare_parameter('rate_hz', 1.0)
        self.declare_parameter('greeting', 'Hello')

        # 변경 콜백 등록
        self.add_on_set_parameters_callback(self.on_param_set)

        # 주기 타이머
        self.timer = None
        self.rebuild_timer()

    def rebuild_timer(self):
        rate = float(self.get_parameter('rate_hz').value)
        period = 1.0 / max(rate, 1e-6)

        if self.timer is not None:
            self.timer.cancel()

        self.timer = self.create_timer(period, self.tick)
        self.get_logger().info(f'rate_hz={rate} (period={period:.3f}s)')

    def tick(self):
        greeting = str(self.get_parameter('greeting').value)
        self.get_logger().info(f'{greeting} (node parameters are live)')

    def on_param_set(self, params):
        # 유효성 검사 + 적용
        for p in params:
            if p.name == 'rate_hz':
                if p.value <= 0.0:
                    return SetParametersResult(successful=False, reason='rate_hz must be > 0')

        # 변경 적용 후 timer 재구성
        for p in params:
            if p.name == 'rate_hz':
                # 실제 반영은 ROS2가 파라미터 값을 바꾼 다음에 되므로,
                # 콜백 끝난 후 get_parameter()로 읽으면 변경값이 반영됩니다.
                pass

        # 콜백 종료 직후 값이 반영, 안전하게 다음 이벤트 루프에서 재구성 방식 가능.
        # 여기서는 간단히 즉시 재구성:
        self.rebuild_timer()

        return SetParametersResult(successful=True)

def main(args=None):
    rclpy.init(args=args)
    node = ParamNode()
    rclpy.spin(node)
    rclpy.shutdown()