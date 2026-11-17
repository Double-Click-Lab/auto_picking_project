from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('simulation', default_value='true'),
        DeclareLaunchArgument('motion_algorithm', default_value='original'),
        Node(package='picking_camera_motor', executable='camera_motor_node', output='screen',
             parameters=[str(Path(get_package_share_directory('picking_camera_motor'))/'config/params.yaml'),
                         {'simulation':ParameterValue(LaunchConfiguration('simulation'),value_type=bool),
                          'motion_algorithm':ParameterValue(LaunchConfiguration('motion_algorithm'),value_type=str)}])])
