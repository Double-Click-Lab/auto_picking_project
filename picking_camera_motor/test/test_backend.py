"""Real adapter exercised with generated ArUco frames and firmware serial double.
No USB, VideoCapture, physical motor or camera is opened.
"""
import sys
import types
import time
import numpy as np
import cv2
import pytest
from picking_camera_motor import backend


class SerialDouble:
    def __init__(self): self.rx=b''; self.lines=[]; self.auto_stop=True
    @property
    def in_waiting(self): return len(self.rx)
    def write(self,b):
        line=b.decode().strip(); self.lines.append(line)
        if line.startswith('STOP ') and self.auto_stop: self.rx+=('STOPPED '+line.split()[1]+'\n').encode()
        if line.startswith(('FORWARD ','BACK ')): self.rx+=('MOVING '+line.split()[-1]+'\n').encode()
    def read(self,n): b,self.rx=self.rx[:n],self.rx[n:]; return b
    def close(self): pass


class VideoDouble:
    def __init__(self): self.marker=1; self.x=270; self.fail=False
    def set(self,*args): pass
    def isOpened(self): return True
    def release(self): pass
    def read(self):
        frame=np.full((480,640,3),255,dtype=np.uint8)
        if self.marker:
            d=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            tile=cv2.aruco.drawMarker(d,self.marker,100)
            frame[190:290,self.x:self.x+100,:]=tile[:,:,None]
        return not self.fail,frame


@pytest.fixture
def real(monkeypatch):
    # pyserial is not installed on this machine; only the import is replaced.
    monkeypatch.setitem(sys.modules,'serial',types.SimpleNamespace())
    from picking_camera_motor import legacy
    serial=SerialDouble(); video=VideoDouble()
    monkeypatch.setattr(legacy,'connect_arduino',lambda port:serial)
    monkeypatch.setattr(cv2,'VideoCapture',lambda *args:video)
    obj=backend.Real(stable_seconds=0.001,marker_timeout=0.1,move_timeout=1.0)
    yield obj,serial,video
    obj.close()


def prepared(obj):
    obj.prepare(1); obj.poll(); time.sleep(0.003); obj.poll(); assert obj.ready


def cmd(marker,device='motor'): return dict(device=device,payload=dict(marker_id=marker))
def settle(obj):
    for _ in range(6):
        r=obj.poll(); time.sleep(0.003)
        if r is not None: return r


def test_real_no_auto_home_and_same_current_rechecks(real):
    obj,s,v=real
    assert not obj.ready and not s.lines
    prepared(obj)
    for _ in range(2):
        obj.start(cmd(1)); assert settle(obj) is True
    assert not any(x.startswith(('BACK ','FORWARD ')) for x in s.lines)
    assert sum(x.startswith('STOP ') for x in s.lines)==3


def test_motion_requires_stopped_and_fresh_stable_image(real):
    obj,s,v=real; prepared(obj)
    obj.start(cmd(2)); obj.poll()
    assert any(x.startswith('FORWARD 100 ') for x in s.lines)
    s.auto_stop=False; v.marker=2
    obj.poll(); assert obj.motor.state=='STOPPING'
    assert settle(obj) is None
    s.rx+=f'STOPPED {obj.motor.stop_sequence}\n'.encode()
    assert settle(obj) is True
    obj.start(cmd(2,'camera')); assert settle(obj) is True


def test_marker_loss_camera_fault_arduino_fault(real):
    obj,s,v=real; prepared(obj); obj.start(cmd(2)); v.marker=None
    obj.poll(); obj.last_seen-=1
    with pytest.raises(RuntimeError,match='missing'): obj.poll()
    obj.stop(); assert s.lines[-1].startswith('STOP ')
    v.fail=True
    with pytest.raises(RuntimeError,match='camera read'): obj.poll()
    v.fail=False; s.rx=b'FAULT HEARTBEAT_TIMEOUT\n'
    with pytest.raises(RuntimeError,match='상태 오류'): obj.poll()


def test_same_current_off_center_does_not_claim_success(real):
    obj,s,v=real; prepared(obj); v.x=100; obj.start(cmd(1))
    with pytest.raises(RuntimeError,match='off center'): obj.poll()


def test_stop_timeout_and_overall_timeout(real):
    obj,s,v=real; prepared(obj); obj.start(cmd(2)); obj.poll()
    s.auto_stop=False; v.marker=2; obj.poll(); obj.motor.stop_started-=2
    with pytest.raises(RuntimeError,match='정지 응답'): obj.poll()


def test_total_timeout_even_with_visible_non_target(real):
    obj,s,v=real; prepared(obj); obj.start(cmd(2)); obj.started-=2
    with pytest.raises(RuntimeError,match='overall'): obj.poll()


def test_camera_alignment_does_not_reuse_previous_image(real):
    obj,s,v=real; prepared(obj); obj.start(cmd(1)); assert settle(obj)
    v.x=100; obj.start(cmd(1,'camera'))
    assert settle(obj) is None
    obj.started-=2
    with pytest.raises(RuntimeError,match='overall'): obj.poll()


def test_same_marker_requires_continuous_alignment_without_movement(real, monkeypatch):
    obj, serial, video = real
    prepared(obj)
    obj.stable_seconds = 0.5
    now = [100.0]
    monkeypatch.setattr(backend.time, 'monotonic', lambda: now[0])
    serial.lines.clear()
    obj.start(cmd(1))
    obj.poll()  # fresh image -> STOP
    obj.poll()  # STOPPED -> verify
    obj.poll()  # continuous alignment starts
    now[0] = 100.3
    video.x = 295  # detected, but outside the unchanged horizontal tolerance
    assert obj.poll() is None
    assert obj.stable is None
    video.x = 270
    now[0] = 100.4
    obj.poll()
    now[0] = 100.8
    assert obj.poll() is None
    now[0] = 100.91
    assert obj.poll() is True
    assert not any(line.startswith(('FORWARD ', 'BACK ')) for line in serial.lines)


def test_timeout_reports_actual_verification_stage(real):
    obj, serial, video = real
    prepared(obj)
    obj.start(cmd(1))
    obj.poll()
    obj.poll()
    video.x = 295
    obj.started -= 2
    with pytest.raises(RuntimeError, match=r'stage=verify; target=1; ids=\[1\].*motor=IDLE'):
        obj.poll()


def test_original_home_moves_back_until_marker_one_and_waits_for_stopped(real):
    obj, serial, video = real
    obj.motion_algorithm = 'original'
    obj.original_homing = 'observe'
    video.marker = None
    obj.poll()
    assert serial.lines[-1].startswith('BACK 100 ')
    assert not obj.ready
    video.marker = 1
    serial.auto_stop = False
    obj.poll()
    assert obj.motor.state == 'STOPPING'
    assert not obj.ready
    serial.rx += f'STOPPED {obj.motor.stop_sequence}\n'.encode()
    obj.poll()
    assert obj.ready and obj.motor.current == 1


def test_original_travel_ignores_local_timeouts_and_finishes_on_stopped(real):
    obj, serial, video = real
    prepared(obj)
    obj.motion_algorithm = 'original'
    obj.start(cmd(3))
    obj.poll()
    assert serial.lines[-1].startswith('FORWARD 100 ')
    video.marker = None
    obj.started -= 30
    obj.last_seen -= 30
    assert obj.poll() is None  # marker 3 need not be visible while travelling
    assert obj.motor.state == 'MOVING'
    video.marker = 3
    obj.poll()
    assert obj.motor.state == 'STOPPING'
    video.x = 100  # no extra post-stop motor alignment window in original mode
    assert obj.poll() is True
    assert obj.motor.current == 3
    obj.start(cmd(3, 'camera'))
    assert obj.poll() is None  # camera command still checks a fresh aligned image


def test_original_uses_back_for_smaller_target(real):
    obj, serial, video = real
    prepared(obj)
    obj.motion_algorithm = 'original'
    obj.motor.current = 3
    video.x = 100
    obj.start(cmd(1))
    obj.poll()
    assert serial.lines[-1].startswith('BACK 100 ')


def test_original_same_target_checks_fresh_image_without_movement(real):
    obj, serial, video = real
    prepared(obj)
    obj.motion_algorithm = 'original'
    serial.lines.clear()
    obj.start(cmd(1))
    assert obj.poll() is True
    assert not serial.lines
    video.x = 100
    obj.start(cmd(1))
    with pytest.raises(RuntimeError, match='same marker off center'):
        obj.poll()


def test_original_motion_still_stops_at_server_deadline(real, tmp_path, monkeypatch):
    import json
    import uuid
    from picking_camera_motor.core import Engine
    from picking_camera_motor.sim_server import command
    obj, serial, video = real
    prepared(obj)
    obj.motion_algorithm = 'original'
    clock = [1000.0]
    monkeypatch.setattr(backend.time, 'monotonic', lambda: clock[0])
    events = []
    engine = Engine(tmp_path/'original.db', obj, events.append, {'ITEM-001/A01': 3},
                    simulation=False, wall=lambda: clock[0], mono=lambda: clock[0])
    session = str(uuid.uuid4())
    c = command(session, str(uuid.uuid4()), 'motor')
    c.update(mode='real', issued_at=999.0, expires_at=1004.0)
    c['payload']['marker_id'] = 3
    try:
        engine.receive('heartbeat', json.dumps(dict(v=1, server_session_id=session, sent_at=1000.0)))
        engine.receive('motor', json.dumps(c))
        engine.tick()
        assert obj.motor.state == 'MOVING'
        clock[0] += 3.1
        engine.tick()
        result = [e for e in events if e.get('phase') == 'result'][-1]
        assert result['success'] is False and 'command deadline' in result['detail']
        assert any(line.startswith('STOP ') for line in serial.lines)
        assert engine.fault and not obj.active
    finally:
        engine.close()


def test_original_home_camera_failure_stops_without_server_command(real, tmp_path):
    from picking_camera_motor.core import Engine
    obj, serial, video = real
    obj.motion_algorithm = 'original'
    obj.original_homing = 'observe'
    video.marker = None
    engine = Engine(tmp_path/'homing.db', obj, lambda e: None, {}, simulation=False)
    try:
        engine.tick()
        assert obj.motor.state == 'MOVING'
        video.fail = True
        engine.tick()
        assert engine.fault and not obj.ready
        assert obj.original_homing is None
        assert obj.motor.state == 'STOPPING'
        assert serial.lines[-1].startswith('STOP ')
    finally:
        engine.close()


def test_original_camera_has_no_local_timeout_or_alignment_hold(real, monkeypatch):
    obj, serial, video = real
    prepared(obj)
    obj.motion_algorithm = 'original'
    obj.stable_seconds = 0.5
    clock = [100.0]
    monkeypatch.setattr(backend.time, 'monotonic', lambda: clock[0])
    obj.start(cmd(1, 'camera'))
    video.marker = None
    obj.poll()
    clock[0] += 100
    assert obj.poll() is None  # waits without a local 2s/20s timeout
    video.marker = 1
    video.x = 295
    assert obj.poll() is None  # visible but off-center is not success
    video.x = 270
    assert obj.poll() is True  # first fresh centered verify frame, no clock advance
    assert obj.required_stable_seconds == 0


def test_original_manual_preparation_needs_stop_ack_but_no_hold(real):
    obj, serial, video = real
    obj.motion_algorithm = 'original'
    obj.stable_seconds = 0.5
    obj.prepare(1)
    assert not obj.ready
    obj.poll()  # STOPPED and one fresh centered frame suffice
    assert obj.ready and obj.motor.state == 'IDLE'
