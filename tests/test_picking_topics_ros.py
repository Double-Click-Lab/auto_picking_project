"""Opt-in: HTTP server + relay + actual camera simulation node + robot protocol peer.

PICKING_CAMERA_SOURCE points at a checkout containing picking_camera_motor/node.py.
Uses a separate DB, port, DDS domain and localhost-only discovery; no real hardware.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


@pytest.mark.skipif(os.getenv('PICKING_ROS_INTEGRATION') != '1', reason='Opt-in ROS2 simulation integration')
def test_order_over_dds_to_camera_and_robot_peer_and_back(tmp_path):
    root = Path(__file__).resolve().parents[1]
    camera_source = Path(os.environ['PICKING_CAMERA_SOURCE']).resolve()
    assert (camera_source/'picking_camera_motor/node.py').is_file()
    with socket.create_server(('127.0.0.1',0)) as probe:
        port=probe.getsockname()[1]
    env=dict(os.environ, ROS_DOMAIN_ID='179', ROS_LOCALHOST_ONLY='1',
             SIMULATION='true',DEVICE_TRANSPORT='topics',API_KEY='topics-app-test',
             DEVICE_KEY='topics-device-test',DB_PATH=str(tmp_path/'warehouse.db'),
             CATALOG_PATH=str(root/'catalog.json'),CLEAR_ORDERS_ON_START='false',
             REQUIRE_BOX_CONFIRMATION='true',COMMAND_TIMEOUT='45',
             PYTHONPATH=str(camera_source)+os.pathsep+os.environ.get('PYTHONPATH',''))
    opener=build_opener(ProxyHandler({}))
    base=f'http://127.0.0.1:{port}'
    def request(method,path,body=None):
        data=None if body is None else json.dumps(body).encode()
        with opener.open(Request(base+path,method=method,data=data,headers={
                'Content-Type':'application/json','X-API-Key':'topics-app-test'}),timeout=2) as response:
            return json.load(response)
    commands=[
        ('http',[str(root/'.venv/bin/python'),str(root/'tools/run_server.py'),'--port',str(port)]),
        ('camera',['/usr/bin/python3','-c','from picking_camera_motor.node import main; main()',
                   '--ros-args','-p','simulation:=true','-p',f'mapping_file:={root}/config/picking_items.example.json',
                   '-p',f'database:={tmp_path}/camera.db','-p',f'local_control_file:={tmp_path}/camera.prepare']),
        ('robot',['/usr/bin/python3',str(root/'tools/picking_sim_robot.py'),'--journal',str(tmp_path/'robot.db')]),
        ('relay',['/usr/bin/python3',str(root/'tools/picking_server.py'),'--url',base,
                  '--mapping',str(root/'config/picking_items.example.json'),'--journal',str(tmp_path/'relay.db')]),
    ]
    processes=[]; logs={}
    try:
        for name,args in commands:
            logs[name]=(tmp_path/(name+'.log')).open('w+')
            processes.append(subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.DEVNULL,
                                               stdout=logs[name],stderr=subprocess.STDOUT))
        until=time.monotonic()+15
        while True:
            try:
                assert request('GET','/health')['device_transport']=='topics'
                break
            except URLError:
                if time.monotonic()>until: raise
                time.sleep(.1)
        checked=subprocess.run(['/usr/bin/python3',str(root/'tools/picking_check.py'),'--url',base],
                               cwd=root,env=env,capture_output=True,text=True,timeout=15)
        assert checked.returncode==0,checked.stdout+checked.stderr
        assert '명령 전송 없음' in checked.stdout
        (tmp_path/'readonly-check.log').write_text(checked.stdout)
        body={'request_id':'dds-full-order','orders':[
            ['dds-customer',{'A':2},{'B':0},{'C':0},{'D':1}]]}
        accepted=request('POST','/orders/app-inventor',body)
        oid=accepted['orders'][0]['order_id']
        assert accepted['orders'][0]['status']=='waiting'
        request('POST',f'/orders/{oid}/start')
        until=time.monotonic()+75
        while time.monotonic()<until:
            order=request('GET',f'/orders/{oid}')
            if order['status'] in ('waiting_box','failed'): break
            assert all(p.poll() is None for p in processes), 'A simulation process exited'
            time.sleep(.2)
        assert order['status']=='waiting_box',order
        assert [c['device'] for c in order['commands']]==['motor','camera','robot','robot','motor','camera','robot']
        assert len({c['id'] for c in order['commands']})==7
        for c in order['commands']:
            outcome=json.loads(c['result'])
            assert outcome['success'] is True
            assert 'simulation' in outcome['detail']
        # Resending an App Inventor request must not create another order/motion.
        assert request('POST','/orders/app-inventor',body)['orders'][0]['order_id']==oid
        request('POST',f'/orders/{oid}/box-ready')
        assert request('GET',f'/orders/{oid}')['status']=='completed'
        (tmp_path/'verified-order.json').write_text(json.dumps(order,ensure_ascii=False,indent=2))
    except Exception:
        for name,log in logs.items():
            log.flush();log.seek(0)
            print('\n'+name+' simulation log:\n'+log.read())
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=3)
        for log in logs.values(): log.close()
