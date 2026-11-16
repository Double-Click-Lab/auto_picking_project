from glob import glob
from setuptools import find_packages, setup

package_name = 'picking_mirobot_adapter'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='Double Click Lab',
    maintainer_email='bkay0829@gmail.com',
    description='Reliable topic-to-action Mirobot adapter',
    license='MIT',
    entry_points={'console_scripts': [
        'mirobot_topic_node = picking_mirobot_adapter.adapter_node:main',
        'picking_sim_test_publisher = picking_mirobot_adapter.sim_publisher:main',
        'picking_mirobot_recover = picking_mirobot_adapter.recover:main',
    ]},
)
