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
