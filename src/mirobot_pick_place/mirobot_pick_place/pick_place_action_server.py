import threading
import os
import math
import yaml

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from ament_index_python.packages import get_package_share_directory
from mirobot_pick_place_interfaces.action import PickPlace
from std_srvs.srv import Trigger

from .mirobot_controller import MirobotController
from .sequence import build_plan, positions
from .configuration import (
    controller_options, validate_config, validate_pose,
)


class PickPlaceActionServer(Node):

    def __init__(self, **kwargs):
        super().__init__("mirobot_pick_place_action_server", **kwargs)

        self.controller = None
        self.declare_parameter("config_file", "")
        config_path = self.get_parameter("config_file").value
        if not config_path:
            config_path = os.path.join(
                get_package_share_directory("mirobot_pick_place"),
                "config", "poses.yaml"
            )
        with open(config_path, "r", encoding="utf-8") as file:
            self.config = validate_config(yaml.safe_load(file))
        self.simulation_mode = self.config["simulation_mode"]
        self.poses = self.config["poses"]
        self.initialized = self.simulation_mode
        self.initialization_active = False
        self.recovery_required = False
        self.controller = MirobotController(
            logger=self.get_logger(), **controller_options(self.config)
        )

        self.pump_settle_sec = float(
            self.config.get("pump_settle_sec", 1.0)
        )

        self.release_settle_sec = float(
            self.config.get("release_settle_sec", 0.5)
        )

        self.goal_lock = threading.Lock()
        self.goal_active = False
        self.cancel_event = threading.Event()

        self.callback_group = ReentrantCallbackGroup()

        # Create Action Server
        self.action_server = ActionServer(
            self,
            PickPlace,
            "/pick_place",
            execute_callback=self.execute_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
            callback_group=self.callback_group
        )

        self.initialize_service = self.create_service(
            Trigger, "/mirobot/initialize", self.initialize_callback,
            callback_group=self.callback_group
        )
        self.status_service = self.create_service(
            Trigger, "/mirobot/status", self.status_callback,
            callback_group=self.callback_group
        )

        self.get_logger().info(
            "===================================="
        )
        self.get_logger().info(
            "MIROBOT PICK AND PLACE ACTION SERVER"
        )
        self.get_logger().info(
            "Action name: /pick_place"
        )
        self.get_logger().info(
            f"Mode: {'SIMULATION' if self.simulation_mode else 'HARDWARE'}"
        )
        self.get_logger().info(
            "Action Server listening; "
            "hardware requires explicit initialization."
        )
        self.get_logger().info(
            "===================================="
        )

    def validate_goal(self, goal):
        fields = (
            "pick_x", "pick_y", "pick_z",
            "pick_rx", "pick_ry", "pick_rz",
            "place_x", "place_y", "place_z",
            "place_rx", "place_ry", "place_rz"
        )

        try:
            values = [float(getattr(goal, field)) for field in fields]
            height = float(goal.approach_height)
        except (AttributeError, TypeError, ValueError):
            self.get_logger().warning("Goal has invalid coordinates.")
            return False

        if not all(math.isfinite(value) for value in values):
            self.get_logger().warning("Goal contains non-finite coordinates.")
            return False

        if not math.isfinite(height) or height <= 0:
            self.get_logger().warning("Approach height must be positive.")
            return False

        try:
            for pose in self.goal_poses(goal).values():
                validate_pose(pose, self.config["workspace_bounds"])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            self.get_logger().warning(
                f"Goal outside configured bounds: {error}"
            )
            return False
        return True

    def goal_poses(self, request):
        pick = {axis: float(getattr(request, f"pick_{axis}"))
                for axis in ("x", "y", "z", "rx", "ry", "rz")}
        place = {axis: float(getattr(request, f"place_{axis}"))
                 for axis in ("x", "y", "z", "rx", "ry", "rz")}
        return positions(
            self.poses["home"], pick, place, request.approach_height,
            tool_axis=self.config["tool_axis_approach"],
        )

    def status_callback(self, request, response):
        response.success = self.initialized and (
            self.simulation_mode or self.controller.ready
        ) and not (self.recovery_required or self.goal_active
                   or self.initialization_active)
        response.message = (
            f"state={self.controller.last_state}, "
            f"ready={self.controller.ready}, initialized={self.initialized}, "
            f"busy={self.goal_active or self.initialization_active}, "
            f"recovery_required={self.recovery_required} (cached status)"
        )
        return response

    def initialize_callback(self, request, response):
        with self.goal_lock:
            if self.goal_active or self.initialization_active:
                response.success = False
                response.message = "Cannot initialize while work is active."
                return response
            if not self.simulation_mode and not self.config["homing_enable"]:
                response.success = False
                response.message = (
                    "Explicit homing is disabled in configuration."
                )
                return response
            self.initialization_active = True
            self.initialized = False
        try:
            self.controller.home()
            self.initialized = True
            self.recovery_required = False
            response.success = True
            response.message = (
                "Explicit homing completed; no task was started."
            )
        except Exception as error:
            self.recovery_required = True
            response.success = False
            response.message = f"Initialization failed: {error}"
        finally:
            with self.goal_lock:
                self.initialization_active = False
        return response

    # --------------------------------------
    # Goal request
    # --------------------------------------
    def goal_callback(self, goal_request):

        if not goal_request.start:
            self.get_logger().warning(
                "Goal rejected: start must be True."
            )
            return GoalResponse.REJECT

        if not self.validate_goal(goal_request):
            self.get_logger().warning("Goal rejected: invalid coordinates.")
            return GoalResponse.REJECT

        with self.goal_lock:

            if (self.initialization_active or self.recovery_required
                    or not self.initialized
                    or (not self.simulation_mode
                        and not self.controller.ready)):
                self.get_logger().warning(
                    "Goal rejected: initialization/recovery is required."
                )
                return GoalResponse.REJECT

            if self.goal_active:
                self.get_logger().warning(
                    "Goal rejected: another task is running."
                )
                return GoalResponse.REJECT

            self.goal_active = True
            self.cancel_event.clear()

        self.get_logger().info("New Pick & Place goal accepted.")

        return GoalResponse.ACCEPT

    # --------------------------------------
    # Cancel request
    # --------------------------------------
    def cancel_callback(self, goal_handle):

        self.get_logger().warning(
            "Cancel request received."
        )
        self.cancel_event.set()

        return CancelResponse.ACCEPT

    # --------------------------------------
    # Publish feedback
    # --------------------------------------
    def publish_progress(
        self,
        goal_handle,
        step,
        state
    ):

        feedback = PickPlace.Feedback()

        feedback.current_step = step
        feedback.total_steps = 10
        feedback.current_state = state
        feedback.progress = step * 10.0

        goal_handle.publish_feedback(feedback)

        self.get_logger().info(
            f"Progress: {feedback.progress:.0f}% | {state}"
        )

    # --------------------------------------
    # Handle cancellation
    # --------------------------------------
    def finish_cancel(self, goal_handle, step):

        if self.simulation_mode:
            self.controller.set_pump(False)
            detail = "Simulation pump reset to OFF."
        else:
            self.initialized = False
            self.recovery_required = True
            self.controller.ready = False
            detail = (
                "Canceled at an operation boundary; pump left unchanged. "
                "No return motion was sent. Inspect the object and robot "
                "before recovery. This is not an emergency stop."
            )

        goal_handle.canceled()

        result = PickPlace.Result()
        result.success = False
        result.message = (
            f"Task canceled after step {step}. "
            + detail
        )

        self.get_logger().warning(result.message)

        return result

    # --------------------------------------
    # Execute Pick & Place
    # --------------------------------------
    def execute_callback(self, goal_handle):

        result = PickPlace.Result()

        poses = self.goal_poses(goal_handle.request)
        steps = [
            (step.name, lambda step=step: step.execute(self.controller))
            for step in build_plan(poses)
        ]

        try:

            self.get_logger().info(
                "Pick & Place execution started."
            )

            for index, (state, operation) in enumerate(
                steps, start=1
            ):

                # Check cancellation between steps.
                if (
                    goal_handle.is_cancel_requested
                    or self.cancel_event.is_set()
                ):
                    return self.finish_cancel(
                        goal_handle, index - 1
                    )

                self.get_logger().info(
                    f"[{index}/10] {state}"
                )

                operation()

                # Wait for pump stabilization.
                if state == "VACUUM_ON":
                    self.cancel_event.wait(timeout=self.pump_settle_sec)

                elif state == "VACUUM_OFF":
                    self.cancel_event.wait(timeout=self.release_settle_sec)

                self.publish_progress(
                    goal_handle,
                    index,
                    state
                )

                # Allow cancellation callback to run
                # before starting the next operation.
                self.cancel_event.wait(timeout=0.2)

            # Check cancellation after the last operation.
            if (
                goal_handle.is_cancel_requested
                or self.cancel_event.is_set()
            ):
                return self.finish_cancel(
                    goal_handle, 10
                )

            goal_handle.succeed()

            result.success = True
            result.message = (
                "Pick and Place completed successfully. "
                "Robot returned to HOME."
            )

            self.get_logger().info(result.message)

            return result

        except Exception as error:

            cleanup_message = ""
            if self.simulation_mode:
                try:
                    self.controller.set_pump(False)
                except Exception as cleanup_error:
                    cleanup_message = f"; pump cleanup failed: {cleanup_error}"
            else:
                self.initialized = False
                self.recovery_required = True
                self.controller.ready = False
                cleanup_message = (
                    "; hardware pump left unchanged; no recovery motion sent. "
                    "An in-flight command may still be running. "
                    "Inspect the robot; recovery is required."
                )

            goal_handle.abort()

            result.success = False
            result.message = str(error) + cleanup_message

            self.get_logger().error(
                f"Pick & Place failed: {error}"
            )

            return result

        finally:

            with self.goal_lock:
                self.goal_active = False

    def destroy_node(self):

        if self.controller is not None:
            self.controller.close()
        super().destroy_node()


def main(args=None):

    rclpy.init(args=args)

    node = None
    executor = MultiThreadedExecutor(num_threads=2)

    try:

        node = PickPlaceActionServer()

        executor.add_node(node)
        executor.spin()

    except KeyboardInterrupt:
        pass

    except Exception as error:
        print(f"Action Server error: {error}")

    finally:

        executor.shutdown()

        if node is not None:
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
