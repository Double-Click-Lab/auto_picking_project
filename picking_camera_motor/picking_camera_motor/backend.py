import time


class Simulation:
    success_detail = 'simulation: fresh virtual image stable after virtual STOPPED; no hardware opened'
    def __init__(self, stable_seconds=0.5, **kwargs):
        self.ready = True
        self.current = 1
        self.until = None
        self.starts = 0
        self.stable_seconds = stable_seconds
    def start(self, c):
        self.starts += 1
        self.target = c['payload']['marker_id']
        self.until = time.monotonic() + self.stable_seconds + 0.1
    def poll(self):
        if self.until is not None and time.monotonic() >= self.until:
            self.until = None
            self.current = self.target
            return True
    def stop(self):
        self.until = None
        return 'virtual STOPPED'
    def close(self):
        self.stop()


class Real:
    success_detail = 'STOPPED received and fresh image stable; output acknowledgement does not prove physical standstill'
    def __init__(self, camera=0, port='/dev/ttyACM0', baud=115200, pwm=100,
                 tolerance_x=20.0, tolerance_y=60.0, stable_seconds=0.5,
                 marker_timeout=2.0, move_timeout=20.0, display=False, **kwargs):
        # Imported only in real mode. This object and all its resources have one owner thread.
        from .legacy import create_detector, MotorController, connect_arduino
        import cv2
        if baud != 115200: raise ValueError('verified firmware requires 115200 baud')
        if not 1 <= pwm <= 255: raise ValueError('pwm must be 1..255')
        self.cv2 = cv2
        self.detect = create_detector()
        self.connection = connect_arduino(port)  # STOP handshake only; never home
        self.motor = MotorController(self.connection, pwm)
        self.camera = cv2.VideoCapture(camera, cv2.CAP_V4L2)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.camera.isOpened():
            self.connection.close()
            raise RuntimeError('camera open failed')
        self.ready = False
        self.active = None
        self.display = display
        self.tx, self.ty = tolerance_x, tolerance_y
        self.stable_seconds, self.marker_timeout, self.move_timeout = stable_seconds, marker_timeout, move_timeout
        self.stable = None
        self.preparing = False
        self.prepare_marker = None
        self.last_seen = time.monotonic()

    def prepare(self, marker):
        # Explicit local readiness: operator manually positions a visible marker.
        # No automatic movement/homing is permitted here.
        if self.active: raise RuntimeError('busy')
        self.ready = False
        self.prepare_marker = marker
        self.preparing = True
        self.stable = None
        self.motor.target = marker
        self.motor.stop(reached=True)

    def start(self, c):
        if not self.ready or self.motor.state != 'IDLE': raise RuntimeError('not prepared/stopped')
        self.active = c
        self.started = self.last_seen = time.monotonic()
        self.stable = None
        self.target = c['payload']['marker_id']
        self.stage = 'observe'  # same-current commands MUST pass fresh video

    def poll(self):
        self.motor.update()
        ok, frame = self.camera.read()
        if not ok: raise RuntimeError('camera read failed')
        now = time.monotonic()
        h, w = frame.shape[:2]
        corners, ids, _ = self.detect(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY))
        markers = {} if ids is None else {int(i): c.reshape(4,2).mean(axis=0) for c,i in zip(corners,ids.flatten())}
        target = self.prepare_marker if self.preparing else self.target if self.active else None
        center = markers.get(target)
        centered = center is not None and abs(center[0]-w/2) <= self.tx and abs(center[1]-h/2) <= self.ty
        if self.display:
            self.cv2.imshow('picking camera (owner)', frame)
            if self.cv2.waitKey(1) & 255 == ord('q'): raise RuntimeError('local display quit')
        if self.preparing:
            if centered and self.motor.state == 'IDLE':
                self.stable = self.stable or now
                if now-self.stable >= self.stable_seconds:
                    self.motor.current = target
                    self.ready, self.preparing = True, False
            else: self.stable = None
            return
        if not self.active: return
        if now-self.started > self.move_timeout: raise RuntimeError('overall movement/alignment timeout')
        if center is not None: self.last_seen = now
        if now-self.last_seen > self.marker_timeout: raise RuntimeError('target marker missing timeout')
        if self.stage == 'observe':
            if self.active['device'] == 'camera':
                self.stage = 'verify'
            elif centered:
                self.motor.target = target
                self.motor.stop(reached=True)
                self.stage = 'stopping'
            elif target == self.motor.current:
                raise RuntimeError('same marker off center; manual recovery required')
            else:
                self.motor.target = target
                self.motor.move('FORWARD' if target > self.motor.current else 'BACK')
                self.stage = 'moving'
        elif self.stage == 'moving' and centered:
            self.motor.stop(reached=True)
            self.stage = 'stopping'
        elif self.stage == 'stopping' and self.motor.state == 'IDLE':
            self.stage = 'verify'
            self.stable = None
        elif self.stage == 'verify':
            if self.motor.state != 'IDLE': raise RuntimeError('motor not stopped')
            if centered:
                self.stable = self.stable or now
                if now-self.stable >= self.stable_seconds:
                    self.active = None
                    return True
            else: self.stable = None

    def stop(self):
        self.active = None
        self.ready = False
        self.preparing = False
        self.motor.stop(reached=False)
        self.stop_deadline = time.monotonic()+1.1
        # poll() continues STOPPED handling, without blocking ROS callbacks.
        return 'STOP requested; STOPPED not yet confirmed'

    def close(self):
        try: self.connection.write(b'STOP 999999\n')
        finally:
            self.connection.close()
            self.camera.release()
            if self.display: self.cv2.destroyAllWindows()
