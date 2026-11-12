from glob import glob
from setuptools import setup, find_packages

package_name = 'mirobot_pick_place'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
        ('share/' + package_name + '/config',
         glob('config/*.yaml')),
        ('share/' + package_name + '/launch',
         glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='leehanbin',
    maintainer_email='leehanbin@todo.todo',
    description='WLKATA Mirobot Pick and Place',
    license='MIT',
    entry_points={
        'console_scripts': [
            'pick_place_node = mirobot_pick_place.pick_place_node:main',
            'pick_place_action_server = mirobot_pick_place.pick_place_action_server:main',
            'pick_place_action_client = mirobot_pick_place.pick_place_action_client:main',
        ],
    },
)
