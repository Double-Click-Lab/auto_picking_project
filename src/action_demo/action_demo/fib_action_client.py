import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from example_interfaces.action import Fibonacci

class FibActionClient(Node):
    def __init__(self):
        super().__init__('fib_action_client')
        self.client = ActionClient(self, Fibonacci, 'fibonacci')

    def send_goal(self, order: int):
        goal_msg = Fibonacci.Goal()
        goal_msg.order = order
        self.client.wait_for_server()
        self.get_logger().info(f'Sending goal: order={order}')
        send_future = self.client.send_goal_async(goal_msg, feedback_callback=self.feedback_cb)
        send_future.add_done_callback(self.goal_response_cb)


    def goal_response_cb(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().info('Goal rejected')
            rclpy.shutdown()
            return

        self.get_logger().info('Goal accepted')
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.result_cb)

    def feedback_cb(self, feedback_msg):
        seq = feedback_msg.feedback.sequence
        self.get_logger().info(f'Feedback: {seq}')

    def result_cb(self, future):
        result = future.result().result
        self.get_logger().info(f'Result: {result.sequence}')
        rclpy.shutdown()

def main(args=None):
    rclpy.init(args=args)
    node = FibActionClient()
    node.send_goal(order=10)
    rclpy.spin(node)