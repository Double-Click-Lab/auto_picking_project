import rclpy
import sys

from rclpy.node import Node
from rclpy.action import ActionClient
from action_msgs.msg import GoalStatus

from mirobot_pick_place_interfaces.action import PickPlace


class PickPlaceActionClient(Node):

    def __init__(self):
        super().__init__("mirobot_pick_place_action_client")

        self.declare_parameter("cancel_after_step", 0)

        coordinate_defaults = {
            "pick_x": 100.0,
            "pick_y": 0.0,
            "pick_z": 50.0,
            "pick_rx": 0.0,
            "pick_ry": 0.0,
            "pick_rz": 0.0,
            "place_x": 100.0,
            "place_y": 100.0,
            "place_z": 50.0,
            "place_rx": 0.0,
            "place_ry": 0.0,
            "place_rz": 0.0,
            "approach_height": 30.0
        }

        for name, default in coordinate_defaults.items():
            self.declare_parameter(name, default)

        self.cancel_after_step = int(
            self.get_parameter("cancel_after_step").value
        )

        self.goal_handle = None
        self.cancel_sent = False

        self.client = ActionClient(
            self, PickPlace, "/pick_place"
        )

    def feedback_callback(self, msg):

        feedback = msg.feedback

        self.get_logger().info(
            f"Progress: {feedback.progress:.0f}% | "
            f"Step: {feedback.current_step}/"
            f"{feedback.total_steps} | "
            f"State: {feedback.current_state}"
        )

        # Send cancellation at the requested step.
        if (
            self.cancel_after_step > 0
            and feedback.current_step >= self.cancel_after_step
            and not self.cancel_sent
            and self.goal_handle is not None
        ):
            self.cancel_sent = True

            self.get_logger().warning(
                "Sending cancellation request..."
            )

            future = self.goal_handle.cancel_goal_async()
            future.add_done_callback(
                self.cancel_response_callback
            )

    def cancel_response_callback(self, future):

        try:
            response = future.result()

            if response.goals_canceling:
                self.get_logger().warning(
                    "Cancellation accepted by server."
                )
            else:
                self.get_logger().warning(
                    "Cancellation was not accepted."
                )

        except Exception as error:
            self.get_logger().error(
                f"Cancellation request failed: {error}"
            )

    def send_goal(self):

        self.get_logger().info(
            "Waiting for Action Server..."
        )

        if not self.client.wait_for_server(timeout_sec=10.0):
            self.get_logger().error(
                "Action Server not available."
            )
            return 1

        goal = PickPlace.Goal()
        goal.start = True

        coordinate_fields = (
            "pick_x", "pick_y", "pick_z",
            "pick_rx", "pick_ry", "pick_rz",
            "place_x", "place_y", "place_z",
            "place_rx", "place_ry", "place_rz",
            "approach_height"
        )

        for field in coordinate_fields:
            setattr(
                goal,
                field,
                float(self.get_parameter(field).value)
            )

        self.get_logger().info(
            f"PICK=({goal.pick_x}, {goal.pick_y}, {goal.pick_z})"
        )

        self.get_logger().info(
            f"PLACE=({goal.place_x}, {goal.place_y}, {goal.place_z})"
        )

        self.get_logger().info(
            f"Approach height={goal.approach_height}"
        )

        self.get_logger().info(
            "Sending Pick & Place goal..."
        )

        future = self.client.send_goal_async(
            goal,
            feedback_callback=self.feedback_callback
        )

        rclpy.spin_until_future_complete(self, future)

        self.goal_handle = future.result()

        if (
            self.goal_handle is None
            or not self.goal_handle.accepted
        ):
            self.get_logger().error("Goal rejected.")
            return 1

        self.get_logger().info("Goal accepted.")

        result_future = self.goal_handle.get_result_async()

        rclpy.spin_until_future_complete(
            self, result_future
        )

        wrapped = result_future.result()

        if wrapped is None:
            self.get_logger().error("No result received.")
            return 1

        result = wrapped.result

        if wrapped.status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info(
                "PICK AND PLACE SUCCESS!"
            )
            self.get_logger().info(result.message)
            return 0

        if wrapped.status == GoalStatus.STATUS_CANCELED:
            self.get_logger().warning(
                "PICK AND PLACE CANCELED"
            )
            self.get_logger().warning(result.message)
            return 0

        self.get_logger().error(
            f"PICK AND PLACE FAILED: {result.message}"
        )
        return 1


def main(args=None):

    rclpy.init(args=args)
    node = PickPlaceActionClient()

    exit_code = 1
    try:
        exit_code = node.send_goal()

    except KeyboardInterrupt:
        node.get_logger().warning(
            "Client interrupted."
        )

    finally:
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
