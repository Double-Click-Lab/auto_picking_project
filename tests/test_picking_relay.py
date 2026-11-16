import asyncio
import copy
import json
from urllib.error import URLError, HTTPError
from uuid import uuid4

import httpx
import pytest

from app.main import create_app
from tools.picking_relay import Relay, RelayJournal, load_mapping
from tools.picking_sim_robot import SimRobot


class Clock:
    def __init__(self): self.now = 1000.0
    def wall(self): return self.now
    def mono(self): return self.now
    def advance(self, seconds): self.now += seconds


class HTTP:
    def __init__(self):
        self.state = dict(mode='simulation',device_transport='topics',worker_alive=True,fault=None)
        self.commands = {}
        self.reports = []
        self.failure = None
    def health(self): return self.state
    def next_device(self, d): return self.commands.get(d)
    def complete_device(self, c, result):
        if self.failure: raise self.failure
        self.reports.append((c['command_id'], result))
        self.commands.pop(c['device'], None)
        return {'accepted':True}


def source(clock, device='motor'):
    oid = str(uuid4())
    return dict(id=str(uuid4()),order_id=oid,device=device,
                action={'motor':'move_to_bin','camera':'align_bin','robot':'pick_and_place'}[device],
                expires_at=clock.wall()+60,payload=dict(item_id='ITEM-001',bin='A01',quantity=1,
                destination_id=oid,position_mm=100,motor_length_mm=1000))


def status(clock, device, session=None, mode='simulation', state='ready'):
    return dict(v=1,device=device,device_id='mirobot_laptop' if device=='robot' else 'camera_motor_laptop',
                session_id=session or str(uuid4()),mode=mode,state=state,active_command_id=None,
                detail='',sent_at=clock.wall())


def result(c, success=True):
    return dict(v=1,phase='result',event_id=str(uuid4()),success=success,detail='test',
                **{k:c[k] for k in ('server_session_id','command_id','order_id','device','mode')})


@pytest.fixture
def rig(tmp_path):
    clock, http, emitted = Clock(), HTTP(), []
    journal = RelayJournal(tmp_path/'relay.sqlite3')
    relay = Relay(http,journal,{'ITEM-001/A01':1},lambda t,e:emitted.append((t,copy.deepcopy(e))),
                  wall=clock.wall,mono=clock.mono)
    relay.tick()
    clock.advance(2.1)
    for device in ('motor','camera','robot'): relay.status(json.dumps(status(clock,device)))
    yield relay,http,clock,emitted,journal
    journal.close()


def test_ready_gating_and_mapping_and_immutable_retries(rig):
    relay,http,clock,out,journal = rig
    http.commands['motor'] = source(clock)
    relay.statuses = {k:v for k,v in relay.statuses.items() if k[0]!='robot'}
    relay.tick()
    assert not out
    relay.status(json.dumps(status(clock,'robot')))
    relay.tick()
    c = out[-1][1]
    assert c['mode']=='simulation' and c['payload']['marker_id']==1
    clock.advance(1.1)
    relay.tick()
    assert out[-1][1]==c
    assert journal.get(c['command_id'])['result'] is None


def test_robot_payload_matches_strict_mirobot_v1_contract(rig):
    relay,http,clock,out,journal=rig
    original=source(clock,'robot')
    original['payload'].update(customer_id='person',order_id=original['order_id'],destination='shipping_box')
    command=relay.make_command(original)
    assert command['payload']=={'item_id':'ITEM-001','bin':'A01','quantity':1,
                                'destination_id':original['order_id']}
    assert 'position_mm' in original['payload']  # source HTTP command remains intact


def test_result_retry_and_exact_ack_after_http_commit(rig):
    relay,http,clock,out,journal = rig
    http.commands['motor'] = source(clock)
    relay.tick()
    c = out[-1][1]
    event = result(c)
    relay.event(json.dumps(event))
    http.failure = URLError('response lost')
    with pytest.raises(URLError): relay.tick()
    assert not any(t.endswith('result_ack') for t,e in out)
    http.failure = None
    relay.tick()
    assert http.reports == [(c['command_id'], {'success':True,'detail':'test'})]
    assert out[-1][0]=='/picking/result_ack'
    ack = out[-1][1]
    relay.event(json.dumps(event))
    assert out[-1][1]==ack
    assert sum(t.endswith('/command') for t,e in out)==1


def test_wrong_and_conflicting_results_never_advance(rig):
    relay,http,clock,out,journal = rig
    http.commands['robot'] = source(clock,'robot'); relay.tick()
    c=out[-1][1]; event=result(c)
    wrong=dict(event,order_id=str(uuid4()))
    with pytest.raises(ValueError): relay.event(json.dumps(wrong))
    assert journal.get(c['command_id'])['result'] is None
    relay.event(json.dumps(event))
    with pytest.raises(ValueError): relay.event(json.dumps(dict(event,success=False)))
    assert journal.fault_detail


@pytest.mark.parametrize('kind', ['restart','lost','expiry','fault','other_command'])
def test_active_device_failure_stops_dispatch(rig,kind):
    relay,http,clock,out,journal = rig
    http.commands['motor']=source(clock); relay.tick()
    session=next(k[1] for k in relay.statuses if k[0]=='motor')
    if kind=='restart': relay.status(json.dumps(status(clock,'motor')))
    elif kind=='lost': clock.advance(5.1)
    elif kind=='expiry': clock.advance(59.1)
    elif kind=='other_command': relay.status(json.dumps(dict(status(clock,'motor',session,state='busy'),active_command_id=str(uuid4()))))
    else: relay.status(json.dumps(status(clock,'motor',session,state='fault')))
    relay.tick()
    assert journal.fault_detail
    assert any(t=='/picking/cancel' for t,e in out)
    assert sum(t.endswith('/command') for t,e in out)==1
    relay.tick()
    assert http.reports[0][1]['success'] is False


def test_restart_never_republishes_old_command(tmp_path):
    clock,http,out=Clock(),HTTP(),[]
    path=tmp_path/'relay.db'; j=RelayJournal(path)
    relay=Relay(http,j,{'ITEM-001/A01':1},lambda *e:out.append(e),wall=clock.wall,mono=clock.mono)
    c=relay.make_command(source(clock)); j.record(c); j.close()
    j=RelayJournal(path)
    relay=Relay(http,j,{},lambda *e:out.append(e),wall=clock.wall,mono=clock.mono)
    relay.tick()
    assert not out and http.reports[0][1]['success'] is False
    assert j.fault_detail
    j.close()


def test_http_rejection_records_fault_and_ack_means_recorded(rig):
    relay,http,clock,out,journal=rig
    http.commands['robot']=source(clock,'robot'); relay.tick()
    c=out[-1][1]; relay.event(json.dumps(result(c)))
    http.failure=HTTPError('http://test',409,'expired',None,None)
    relay.tick()
    assert journal.get(c['command_id'])['posted']==2
    assert journal.fault_detail and out[-1][0]=='/picking/result_ack'


def test_late_success_is_reported_as_failure(rig):
    relay,http,clock,out,journal=rig
    http.commands['robot']=source(clock,'robot');relay.tick()
    c=out[-1][1];clock.advance(61)
    relay.event(json.dumps(result(c)));relay.tick()
    assert http.reports[0][1]['success'] is False
    assert 'expiry' in http.reports[0][1]['detail']


def test_result_retries_delay_next_command_until_ack_quiet_period(rig):
    relay,http,clock,out,journal=rig
    http.commands['robot']=source(clock,'robot');relay.tick()
    c=out[-1][1];event=result(c);relay.event(json.dumps(event));relay.tick()
    http.commands['robot']=source(clock,'robot')
    clock.advance(1);relay.event(json.dumps(event));clock.advance(.3);relay.tick()
    assert sum(t.endswith('/command') for t,e in out)==1


def test_heartbeat_stops_on_http_disconnect_and_preserves_outcome(rig):
    relay,http,clock,out,journal=rig
    http.commands['robot']=source(clock,'robot');relay.tick()
    relay.disconnected('offline')
    assert relay.heartbeat() is None and journal.fault_detail
    relay.tick()
    assert http.reports[0][1]['success'] is False


def test_clock_skew_modes_and_multiple_devices_block_commands(rig):
    relay,http,clock,out,journal=rig
    http.commands['robot']=source(clock,'robot')
    with pytest.raises(ValueError): relay.status(json.dumps(dict(status(clock,'robot'),sent_at=clock.wall()+2)))
    robot_key=next(k for k in relay.statuses if k[0]=='robot')
    relay.statuses.pop(robot_key)
    relay.status(json.dumps(status(clock,'robot',mode='real')))
    relay.tick(); assert not out
    relay.status(json.dumps(status(clock,'robot')))
    relay.tick(); assert not out


def test_sim_peer_duplicate_ack_and_restart(tmp_path):
    clock,http,out=Clock(),HTTP(),[]
    j=RelayJournal(tmp_path/'relay.db')
    relay=Relay(http,j,{},lambda *x:None,wall=clock.wall,mono=clock.mono)
    c=relay.make_command(source(clock,'robot'))
    path=tmp_path/'peer.db'
    peer=SimRobot(path,lambda t,e:out.append((t,e)),clock.wall,clock.mono)
    hb=dict(v=1,server_session_id=relay.session,sent_at=clock.wall())
    peer.receive('heartbeat',json.dumps(hb));peer.receive('robot',json.dumps(c));peer.receive('robot',json.dumps(c))
    assert peer.starts==1
    clock.advance(.4);peer.tick()
    event=next(e for t,e in out if e.get('phase')=='result')
    assert event['success'] is True
    peer.close()
    out.clear();peer=SimRobot(path,lambda t,e:out.append((t,e)),clock.wall,clock.mono)
    peer.tick()
    assert next(e for t,e in out if e.get('phase')=='result')==event
    peer.receive('ack',json.dumps(dict(v=1,accepted=True,**{k:event[k] for k in ('command_id','server_session_id','device','event_id')})))
    assert peer.journal.get(c['command_id'])['posted']==1
    peer.close();j.close()


@pytest.mark.parametrize('reason', ['mode','expired','heartbeat'])
def test_sim_peer_refuses_real_expired_and_stale_commands(tmp_path,reason):
    clock,http,out=Clock(),HTTP(),[]
    j=RelayJournal(tmp_path/'relay.db');relay=Relay(http,j,{},lambda *x:None,wall=clock.wall,mono=clock.mono)
    c=relay.make_command(source(clock,'robot'))
    peer=SimRobot(tmp_path/'peer.db',lambda t,e:out.append(e),clock.wall,clock.mono)
    peer.receive('heartbeat',json.dumps(dict(v=1,server_session_id=relay.session,sent_at=clock.wall())))
    if reason=='mode': c['mode']='real'
    elif reason=='expired': clock.advance(59)
    else: clock.advance(6)
    peer.receive('robot',json.dumps(c))
    assert peer.starts==0 and out[-1]['success'] is False
    peer.close();j.close()


def test_topics_simulation_waits_for_device_results(tmp_path,monkeypatch):
    monkeypatch.setenv('DEVICE_TRANSPORT','topics')
    monkeypatch.setenv('API_KEY','test-app');monkeypatch.setenv('DEVICE_KEY','test-device')
    monkeypatch.setenv('CLEAR_ORDERS_ON_START','false')
    monkeypatch.setenv('REQUIRE_BOX_CONFIRMATION','true')
    app=create_app(tmp_path/'server.db',True)
    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                                         headers={'X-API-Key':'test-app','X-Device-Key':'test-device'}) as client:
                health=(await client.get('/health')).json()
                assert health['mode']=='simulation' and health['device_transport']=='topics'
                accepted=(await client.post('/orders/app-inventor',json={'request_id':'topic-sim','orders':[
                    ['person',{'A':1},{'B':0},{'C':0},{'D':0}]]})).json()
                oid=accepted['orders'][0]['order_id']
                assert (await client.post(f'/orders/{oid}/start')).status_code==200
                for device in ('motor','camera','robot'):
                    for _ in range(40):
                        c=(await client.get(f'/devices/{device}/commands/next')).json()['command']
                        if c: break
                        await asyncio.sleep(.05)
                    assert c and c['device']==device
                    await asyncio.sleep(.2)
                    assert (await client.get(f'/devices/{device}/commands/next')).json()['command']['id']==c['id']
                    assert (await client.post(f'/devices/{device}/commands/{c["id"]}/complete',
                                              json={'success':True,'detail':'simulation peer'})).status_code==200
                await asyncio.sleep(.2)
                assert (await client.get(f'/orders/{oid}')).json()['status']=='waiting_box'
                assert (await client.post(f'/orders/{oid}/box-ready')).status_code==200
                assert (await client.get(f'/orders/{oid}')).json()['status']=='completed'
    asyncio.run(check())


def test_invalid_mapping_rejects_boolean_marker(tmp_path):
    p=tmp_path/'bad.json';p.write_text('{"items":{"ITEM-001/A01":true}}')
    with pytest.raises(ValueError): load_mapping(p)
