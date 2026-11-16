import copy
import json
import uuid
import pytest
from picking_camera_motor.core import Engine
from picking_camera_motor.sim_server import command


class Clock:
    t=1000.0
    m=10.0
    def wall(self): return self.t
    def mono(self): return self.m
    def advance(self,x): self.t+=x; self.m+=x


class Fake:
    ready=True
    starts=0
    outcome=None
    error=None
    stopped=False
    success_detail='fake fresh image stable + STOPPED'
    def start(self,c): self.starts+=1
    def poll(self):
        if self.error: raise RuntimeError(self.error)
        result,self.outcome=self.outcome,None
        return result
    def stop(self): self.stopped=True; return 'STOP requested'
    def close(self): pass


@pytest.fixture
def rig(tmp_path):
    clock=Clock(); hw=Fake(); out=[]
    engine=Engine(tmp_path/'state.db',hw,out.append,{'ITEM-001/A01':1},wall=clock.wall,mono=clock.mono)
    session=str(uuid.uuid4())
    c=command(session,str(uuid.uuid4()),'motor'); c.update(issued_at=999.0,expires_at=1030.0)
    engine.receive('heartbeat',json.dumps(dict(v=1,server_session_id=session,sent_at=1000.0)))
    yield engine,hw,out,clock,c
    engine.close()


def send(e,c): e.receive(c['device'],json.dumps(c))
def ack(e,r): e.receive('ack',json.dumps(dict(r,v=1,accepted=True)))
def result(out): return [x for x in out if x.get('phase')=='result'][-1]
def complete(e,h,out): h.outcome=True; e.tick(); return result(out)


def test_motor_camera_and_same_item_new_order(rig):
    e,h,out,clock,c=rig
    for _ in range(2):
        send(e,c); r=complete(e,h,out); assert r['success']; ack(e,r)
        cam=copy.deepcopy(c); cam.update(device='camera',action='align_bin',command_id=str(uuid.uuid4()))
        send(e,cam); r=complete(e,h,out); assert r['success']; ack(e,r)
        c['command_id']=str(uuid.uuid4()); c['order_id']=str(uuid.uuid4()); c['payload']['destination_id']=c['order_id']
    assert h.starts==4


def test_duplicate_and_content_conflict(rig):
    e,h,out,_,c=rig
    send(e,c); send(e,c); assert h.starts==1
    altered=copy.deepcopy(c); altered['payload']['position_mm']=1.0; send(e,altered)
    assert h.starts==1 and 'different content' in e.detail
    r=complete(e,h,out); send(e,c); assert result(out)==r


def test_ack_loss_and_identifiers(rig):
    e,h,out,clock,c=rig
    send(e,c); r=complete(e,h,out)
    clock.advance(1.1); e.tick(); assert result(out)==r
    bad=dict(r,device='camera'); ack(e,bad)
    assert e.db.execute('SELECT ack FROM commands').fetchone()[0]==0
    ack(e,r); assert e.db.execute('SELECT ack FROM commands').fetchone()[0]==1


def test_restart_unknown_and_result_survives(rig,tmp_path):
    e,h,out,clock,c=rig
    send(e,c)
    # Simulate abrupt process loss: release DB/lock without clean shutdown.
    e.db.close(); e.lock.close()
    other=Engine(tmp_path/'state.db',Fake(),out.append,{'ITEM-001/A01':1},wall=clock.wall,mono=clock.mono)
    assert other.fault and result(out)['success'] is False
    original=result(out); other.tick(); assert result(out)==original
    e.db=other.db; e.lock=other.lock; e.active=None


@pytest.mark.parametrize('mutation',[
    {'expires_at':1001.0}, {'mode':'real'}, {'issued_at':True}, {'payload':[]},
    {'device':'robot'}, {'order_id':'bad'}, {'expires_at':float('nan')}
])
def test_invalid_no_motion(rig,mutation):
    e,h,out,_,c=rig; c.update(mutation)
    e.receive('motor',json.dumps(c)); assert h.starts==0


def test_bad_json_and_payload_types(rig):
    e,h,out,_,c=rig
    for raw in ('{','[]','null','{"v":true}','{"v":1,"payload":NaN}'):
        e.receive('motor',raw)
    for key,value in [('quantity',True),('marker_id',True),('position_mm',-1),('motor_length_mm',0),('destination_id','wrong')]:
        x=copy.deepcopy(c); x['payload'][key]=value; send(e,x)
    assert h.starts==0


@pytest.mark.parametrize('failure',['serial disconnected','camera read failed','FAULT INVALID_COMMAND'])
def test_hardware_failure_stop_and_fault(rig,failure):
    e,h,out,_,c=rig; send(e,c); h.error=failure; e.tick()
    assert h.stopped and e.fault and result(out)['success'] is False


def test_heartbeat_loss(rig):
    e,h,out,clock,c=rig; send(e,c); clock.advance(5.1); e.tick()
    assert h.stopped and not result(out)['success']


def test_session_change_and_cancel(rig):
    e,h,out,_,c=rig; send(e,c)
    e.receive('cancel',json.dumps(dict(v=1,server_session_id=c['server_session_id'],command_id=str(uuid.uuid4()),device='motor',reason='wrong')))
    assert not h.stopped
    e.receive('heartbeat',json.dumps(dict(v=1,server_session_id=str(uuid.uuid4()),sent_at=1000.0)))
    assert h.stopped and not result(out)['success']


def test_monotonic_deadline_clock_rollback(rig):
    e,h,out,clock,c=rig; send(e,c)
    clock.t-=100; clock.m+=29.1
    e.hb=(c['server_session_id'],clock.m,0)
    e.tick(); assert h.stopped


def test_clock_skew_and_mapping(rig):
    e,h,out,clock,c=rig
    e.hb=(c['server_session_id'],clock.m,1.01); send(e,c)
    assert not result(out)['success'] and h.starts==0


def test_process_lock(rig,tmp_path):
    with pytest.raises(BlockingIOError): Engine(tmp_path/'state.db',Fake(),lambda _:None,{})


def test_unacked_blocks_new_order_and_camera_order(rig):
    e,h,out,_,c=rig; send(e,c); r=complete(e,h,out)
    second=copy.deepcopy(c); second['command_id']=str(uuid.uuid4()); send(e,second)
    assert h.starts==1 and not result(out)['success']
    ack(e,result(out)); ack(e,r)
    cam=command(c['server_session_id'],str(uuid.uuid4()),'camera'); cam.update(issued_at=999.0,expires_at=1030.0)
    send(e,cam); assert not result(out)['success'] and h.starts==1


def test_matching_cancel_and_ack_malformed_does_not_crash(rig):
    e,h,out,_,c=rig; send(e,c)
    e.receive('ack',json.dumps(dict(v=1,accepted=True,command_id=[])))
    e.receive('cancel',json.dumps(dict(v=1,server_session_id=c['server_session_id'],command_id=c['command_id'],device='motor',reason='operator')))
    assert h.stopped and e.fault and not result(out)['success']


def test_result_survives_restart_exactly(rig,tmp_path):
    e,h,out,clock,c=rig; send(e,c); original=complete(e,h,out)
    e.db.close(); e.lock.close()
    new=Engine(tmp_path/'state.db',Fake(),out.append,{'ITEM-001/A01':1},wall=clock.wall,mono=clock.mono)
    new.tick(); assert result(out)==original and not new.fault
    e.db=new.db; e.lock=new.lock


def test_expiry_during_execution(rig):
    e,h,out,clock,c=rig; send(e,c)
    clock.advance(29.1); e.hb=(c['server_session_id'],clock.m,0); e.tick()
    assert h.stopped and 'deadline' in result(out)['detail']


def test_mapping_mismatch(rig):
    e,h,out,_,c=rig; c['payload']['marker_id']=2; send(e,c)
    assert h.starts==0 and 'mapping' in result(out)['detail']
