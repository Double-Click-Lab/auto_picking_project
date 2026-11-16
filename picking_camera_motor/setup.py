from setuptools import setup
from glob import glob
setup(name='picking_camera_motor', version='1.0.0', packages=['picking_camera_motor'],
      data_files=[('share/ament_index/resource_index/packages',['resource/picking_camera_motor']),
                  ('share/picking_camera_motor',['package.xml','README.md']),
                  ('share/picking_camera_motor/config',glob('config/*.yaml')),
                  ('share/picking_camera_motor/launch',glob('launch/*.launch.py'))],
      install_requires=['setuptools','PyYAML'], zip_safe=True,
      maintainer='Camera Motor Team', maintainer_email='team@example.com',
      description='Durable camera/motor v1 JSON device node', license='Apache-2.0',
      entry_points={'console_scripts':['camera_motor_node=picking_camera_motor.node:main',
                                       'simulation_server=picking_camera_motor.sim_server:main']})
