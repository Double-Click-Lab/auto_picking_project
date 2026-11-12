import rclpy
from rclpy.node import Node
from moveit_msgs.msg import DisplayTrajectory
import serial
import math
import time

from wlkatapython import Mirobot_UART

class TrajectorySubscriber(Node):
    
    def __init__(self):
        super().__init__('trajectory_subscriber')
        try:
            self.ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)
            self.get_logger().info("Serial port opened successfully.")
            self.mirobot=Mirobot_UART(True,False)
            self.mirobot.init(self.ser,-1)
            self.get_logger().info("arm homing...")
            self.mirobot.homing()
            self.get_logger().info("Ok,Waiting for motion command of robotic arm")

        except serial.SerialException as e:
            self.get_logger().error(f"Failed to open serial port: {e}")
            raise

        self.subscription = self.create_subscription(
            DisplayTrajectory,
            '/display_planned_path',
            self.listen_trajectory,
            10
        )

    def listen_trajectory(self, msg):
        if not msg.trajectory:
            self.get_logger().warn("Received empty trajectory list.")
            return

        traj = msg.trajectory[0].joint_trajectory
        if not traj.points:
            self.get_logger().warn("Trajectory has no points.")
            return

        self.get_logger().info(f"Processing {len(traj.points)} trajectory points.")

        for point in traj.points:
            if len(point.positions) < 6:
                self.get_logger().warn("Skipping point with fewer than 6 joints.")
                continue

            # Convert radians to degrees
            degs = [round((p / math.pi) * 180, 2) for p in point.positions[:6]]
            j1, j2, j3, j4, j5, j6 = degs

            # Format G-code with spaces and newline
            command = f"M21 G90 G00 X{j1} Y{j2} Z{j3} A{j4} B{j5} C{j6}\n"
            self.get_logger().debug(f"Sending: {command.strip()}")

            try:
                self.ser.write(command.encode('utf-8'))
                response = self.ser.readline()
                if response:
                    self.get_logger().info(f"Response: {response.decode().strip()}")
                else:
                    self.get_logger().warn("No response from device (timeout).")
            except Exception as e:
                self.get_logger().error(f"Serial communication error: {e}")
                break  # or continue, depending on robustness needs

            # Optional: add delay to avoid overwhelming the controller
            # time.sleep(0.05)

        self.get_logger().info("Finished sending trajectory segment.")
        print("___________________________________")

    def destroy_node(self):
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.close()
            self.get_logger().info("Serial port closed.")
        super().destroy_node()


def main():
    rclpy.init()
    try:
        node = TrajectorySubscriber()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
