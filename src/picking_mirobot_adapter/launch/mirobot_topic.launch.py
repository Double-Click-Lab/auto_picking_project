from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        SetEnvironmentVariable('ROS_DOMAIN_ID', '7'),
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '0'),
        Node(
            package='picking_mirobot_adapter',
            executable='mirobot_topic_node',
            name='mirobot_topic_adapter',
            output='screen',
            parameters=[{
                'mode': 'simulation',
                'plan_timeout_sec': 45.0,
            }],
        ),
    ])
