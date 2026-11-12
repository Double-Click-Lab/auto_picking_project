import time
import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from example_interfaces.action import Fibonacci

class FibActionServer(Node):
    def __init__(self):
        super().__init__('fib_action_server')
        self.server = ActionServer(
            self,
            Fibonacci,
            'fibonacci',
            execute_callback=self.execute_cb,
            goal_callback=self.goal_cb,
            cancel_callback=self.cancel_cb,
        )

        self.get_logger().info('Action ready: /fibonacci')

    def goal_cb(self, goal_request: Fibonacci.Goal):
        self.get_logger().info(f'Goal request: order={goal_request.order}')
        if goal_request.order < 0:
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def cancel_cb(self, goal_handle):
        self.get_logger().info('Cancel request received')
        return CancelResponse.ACCEPT

    def execute_cb(self, goal_handle):
        order = goal_handle.request.order
        feedback = Fibonacci.Feedback()
        feedback.sequence = [0, 1]

        for i in range(2, order):
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                self.get_logger().info('Goal canceled')
                return Fibonacci.Result(sequence=feedback.sequence)

            feedback.sequence.append(
                feedback.sequence[i - 1] + feedback.sequence[i - 2]
            )
            goal_handle.publish_feedback(feedback)
            self.get_logger().info(f'Feedback: {feedback.sequence}')
            time.sleep(0.5)

        goal_handle.succeed()
        result = Fibonacci.Result()
        result.sequence = feedback.sequence[:max(order, 2)]
        self.get_logger().info(f'Result: {result.sequence}')

        return result

def main(args=None):
    rclpy.init(args=args)
    node = FibActionServer()
    rclpy.spin(node)
    rclpy.shutdown()