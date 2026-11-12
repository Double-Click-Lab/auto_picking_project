import rclpy
from rclpy.node import Node
from example_interfaces.srv import AddTwoInts



class AddTwoIntsClient(Node):
    def __init__(self):
        super().__init__('add_two_ints_client')
        self.cli = self.create_client(AddTwoInts, 'add_two_ints')
        while not self.cli.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('waiting for /add_two_ints ...')


    def call(self, a: int, b: int) -> int:
        req = AddTwoInts.Request()
        req.a = a
        req.b = b
        future = self.cli.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        if future.result() is None:
            raise RuntimeError(f"Service call failed: {future.exception()}")
        
        return future.result().sum


def main(args=None):
    rclpy.init(args=args)
    node = AddTwoIntsClient()

    a, b = 10, 32
    s = node.call(a, b)
    node.get_logger().info(f"response: {a}+{b}={s}")

    node.destroy_node()
    rclpy.shutdown()