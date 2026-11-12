import os
import time
import yaml

import rclpy
from rclpy.node import Node
from ament_index_python.packages import get_package_share_directory

from .mirobot_controller import MirobotController
from .configuration import controller_options, validate_config


class PickPlaceNode(Node):

    def __init__(self):
        super().__init__("mirobot_pick_place")

        self.controller = None

        self.declare_parameter("config_file", "")
        config_file = self.get_parameter("config_file").value

        if not config_file:
            package_share = get_package_share_directory(
                "mirobot_pick_place"
            )
            config_file = os.path.join(
                package_share, "config", "poses.yaml"
            )

        self.get_logger().info(
            f"Loading configuration: {config_file}"
        )

        with open(config_file, "r", encoding="utf-8") as file:
            self.config = yaml.safe_load(file)

        self.config = validate_config(
            self.config, pose_names=(
                "home", "pick_approach", "pick", "place_approach", "place"
            )
        )
        self.simulation_mode = self.config["simulation_mode"]
        self.poses = self.config["poses"]
        self.controller = MirobotController(
            logger=self.get_logger(), **controller_options(self.config)
        )

        self.pump_settle_sec = float(
            self.config.get("pump_settle_sec", 1.0)
        )

        self.release_settle_sec = float(
            self.config.get("release_settle_sec", 0.5)
        )

        self.get_logger().info(
            f"Mode: {'SIMULATION' if self.simulation_mode else 'HARDWARE'}"
        )

        self.get_logger().info(
            "Pick and Place node initialized."
        )

    # --------------------------------------
    # Position movement
    # --------------------------------------
    def move(self, pose_name):

        self.controller.move_to(
            pose_name.upper(),
            self.poses[pose_name]
        )

    # --------------------------------------
    # Pump control
    # --------------------------------------
    def pump_on(self):

        self.controller.set_pump(True)

        self.get_logger().info(
            "Waiting for object suction..."
        )

        time.sleep(self.pump_settle_sec)

    def pump_off(self):

        self.controller.set_pump(False)

        self.get_logger().info(
            "Waiting for object release..."
        )

        time.sleep(self.release_settle_sec)

    # --------------------------------------
    # Main Pick and Place sequence
    # --------------------------------------
    def run_sequence(self):

        self.get_logger().info(
            "===================================="
        )
        self.get_logger().info(
            "PICK AND PLACE START"
        )
        self.get_logger().info(
            "===================================="
        )

        try:

            # STEP 1: HOME
            self.get_logger().info("[1/10] Move to HOME")
            self.move("home")

            # STEP 2: Move above object
            self.get_logger().info("[2/10] Move to PICK_APPROACH")
            self.move("pick_approach")

            # STEP 3: Descend to object
            self.get_logger().info("[3/10] Move to PICK")
            self.move("pick")

            # STEP 4: Activate vacuum
            self.get_logger().info("[4/10] Vacuum ON")
            self.pump_on()

            # STEP 5: Lift object
            self.get_logger().info("[5/10] Lift to PICK_APPROACH")
            self.move("pick_approach")

            # STEP 6: Move above destination
            self.get_logger().info("[6/10] Move to PLACE_APPROACH")
            self.move("place_approach")

            # STEP 7: Descend to destination
            self.get_logger().info("[7/10] Move to PLACE")
            self.move("place")

            # STEP 8: Release object
            self.get_logger().info("[8/10] Vacuum OFF")
            self.pump_off()

            # STEP 9: Lift from destination
            self.get_logger().info("[9/10] Lift to PLACE_APPROACH")
            self.move("place_approach")

            # STEP 10: Return HOME
            self.get_logger().info("[10/10] Return to HOME")
            self.move("home")

            self.get_logger().info(
                "===================================="
            )
            self.get_logger().info(
                "PICK AND PLACE COMPLETED SUCCESSFULLY"
            )
            self.get_logger().info(
                "===================================="
            )

        except Exception as error:

            self.get_logger().error(
                f"Pick and Place failed: {error}"
            )

            if self.simulation_mode:
                try:
                    self.controller.set_pump(False)
                except Exception as pump_error:
                    self.get_logger().error(f"Pump OFF failed: {pump_error}")
            else:
                self.controller.ready = False
                self.get_logger().error(
                    "Hardware pump left unchanged; inspect before recovery."
                )

            raise

    def destroy_node(self):

        if self.controller is not None:
            self.controller.close()

        super().destroy_node()


def main(args=None):

    rclpy.init(args=args)
    node = None

    try:

        node = PickPlaceNode()
        node.run_sequence()

    except KeyboardInterrupt:

        if (node is not None and node.controller is not None
                and node.simulation_mode):
            try:
                node.controller.set_pump(False)
            except Exception:
                pass

    except Exception as error:

        if node is not None:
            node.get_logger().error(str(error))
        else:
            print(f"Node initialization failed: {error}")

    finally:

        if node is not None:
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
