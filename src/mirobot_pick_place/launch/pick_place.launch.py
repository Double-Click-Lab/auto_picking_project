from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():

    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file', default_value='',
            description='Configuration YAML; empty uses package default.'
        ),

        Node(
            package='mirobot_pick_place',
            executable='pick_place_action_server',
            name='mirobot_pick_place_action_server',
            output='screen',
            parameters=[{'config_file': LaunchConfiguration('config_file')}]
        )

    ])
