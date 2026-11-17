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
    @property
    def required_stable_seconds(self):
        return 0.0 if self.motion_algorithm == 'original' else self.stable_seconds
    def __init__(self, camera=0, port='/dev/ttyACM0', baud=115200, pwm=100,
                 tolerance_x=20.0, tolerance_y=60.0, stable_seconds=0.5,
                 marker_timeout=2.0, move_timeout=20.0, display=False,
                 preparation_image='', motion_algorithm='verified', **kwargs):
        # Imported only in real mode. This object and all its resources have one owner thread.
        from .legacy import create_detector, MotorController, connect_arduino
        import cv2
        if baud != 115200: raise ValueError('verified firmware requires 115200 baud')
        if not 1 <= pwm <= 255: raise ValueError('pwm must be 1..255')
        if motion_algorithm not in ('verified', 'original'):
            raise ValueError('motion_algorithm must be verified or original')
        self.motion_algorithm = motion_algorithm
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
        self.preparation_diagnostics = None
        self.preparation_image = preparation_image
        self.preparation_image_saved = False
        self.alignment_diagnostics = None
        self.original_homing = 'observe' if motion_algorithm == 'original' else None

    def prepare(self, marker):
        # Explicit local readiness: operator manually positions a visible marker.
        # No automatic movement/homing is permitted here.
        if self.active: raise RuntimeError('busy')
        self.ready = False
        self.original_homing = None
        self.prepare_marker = marker
        self.preparing = True
        self.max_stable_seconds = 0.0
        self.stable = None
        self.motor.target = marker
        self.preparation_image_saved = False
        self.motor.stop(reached=True)

    def start(self, c):
        if not self.ready or self.motor.state != 'IDLE': raise RuntimeError('not prepared/stopped')
        self.active = c
        self.started = self.last_seen = time.monotonic()
        self.stable = None
        self.target = c['payload']['marker_id']
        self.stage = 'observe'  # same-current commands MUST pass fresh video
        self.max_stable_seconds = 0.0

    def poll_original_motor(self, centered):
        """Original target comparison / marker-in-box / STOPPED algorithm.

        Server expiry, cancellation and heartbeat remain enforced by Engine.
        A same-position command still requires a fresh centered observation.
        """
        if self.stage == 'observe':
            if self.target == self.motor.current:
                if not centered:
                    raise RuntimeError('same marker off center; manual recovery required')
                self.success_detail = 'original algorithm: same marker freshly centered; motor IDLE; no movement needed'
                self.active = None
                return True
            self.motor.start_target(self.target, already_centered=bool(centered))
            if self.motor.state not in ('MOVING', 'STOPPING'):
                raise RuntimeError('original controller did not start/stop target')
            self.stage = 'moving' if self.motor.state == 'MOVING' else 'stopping'
        elif self.stage == 'moving' and centered:
            self.motor.stop(reached=True)
            self.stage = 'stopping'
        elif self.stage == 'stopping' and self.motor.state == 'IDLE':
            if self.motor.current != self.target:
                raise RuntimeError('STOPPED did not confirm target')
            self.success_detail = ('original algorithm: target entered center box and matching STOPPED received; '
                                   'fresh stopped alignment is checked by the camera command')
            self.active = None
            return True

    def poll_original_home(self, centered):
        if self.original_homing == 'observe':
            self.motor.home(already_centered=bool(centered))
            self.original_homing = 'moving' if self.motor.state == 'MOVING' else 'stopping'
        elif self.original_homing == 'moving' and centered:
            self.motor.stop(reached=True)
            self.original_homing = 'stopping'
        elif self.original_homing == 'stopping' and self.motor.state == 'IDLE':
            if self.motor.current != 1:
                raise RuntimeError('original homing did not confirm marker 1')
            self.original_homing = None
            self.ready = True

    def poll(self):
        self.motor.update()
        ok, frame = self.camera.read()
        if not ok: raise RuntimeError('camera read failed')
        now = time.monotonic()
        h, w = frame.shape[:2]
        corners, ids, _ = self.detect(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY))
        markers = {} if ids is None else {int(i): c.reshape(4,2).mean(axis=0) for c,i in zip(corners,ids.flatten())}
        target = 1 if self.original_homing else self.prepare_marker if self.preparing else self.target if self.active else self.motor.current
        center = markers.get(target)
        centered = center is not None and abs(center[0]-w/2) <= self.tx and abs(center[1]-h/2) <= self.ty
        checking_stability = self.preparing or (self.active is not None and self.stage == 'verify')
        stable_elapsed = now-self.stable if checking_stability and self.stable is not None and centered and self.motor.state == 'IDLE' else 0.0
        self.max_stable_seconds = max(getattr(self, 'max_stable_seconds', 0.0), stable_elapsed)
        self.alignment_diagnostics = dict(
            observed_at=time.time(), command_id=self.active.get('command_id') if self.active else None,
            stage='home_'+self.original_homing if self.original_homing else 'prepare' if self.preparing else self.stage if self.active else 'idle',
            detected_ids=sorted(markers), target=target, current=self.motor.current,
            target_error_px=None if center is None else [float(center[0]-w/2), float(center[1]-h/2)],
            centered=bool(centered), motor_state=self.motor.state,
            stable_seconds=stable_elapsed, max_stable_seconds=self.max_stable_seconds,
            required_stable_seconds=self.required_stable_seconds, ready=self.ready)
        if self.display:
            self.cv2.rectangle(frame, (int(w/2-self.tx), int(h/2-self.ty)),
                               (int(w/2+self.tx), int(h/2+self.ty)), (0,255,0), 2)
            self.cv2.drawMarker(frame, (w//2,h//2), (0,255,0))
            for marker_id, point in markers.items():
                point = tuple(int(v) for v in point)
                self.cv2.circle(frame, point, 4, (0,0,255), -1)
                self.cv2.putText(frame, str(marker_id), point, self.cv2.FONT_HERSHEY_SIMPLEX, .6, (0,0,255), 2)
            error = self.alignment_diagnostics['target_error_px']
            text = f"target={target} error={error} {self.motor.state} stable={stable_elapsed:.2f}/{self.required_stable_seconds:.2f}s"
            self.cv2.putText(frame, text, (5,20), self.cv2.FONT_HERSHEY_SIMPLEX, .4, (0,255,0), 1)
            self.cv2.imshow('picking camera (owner)', frame)
            if self.cv2.waitKey(1) & 255 == ord('q'): raise RuntimeError('local display quit')
        if self.original_homing:
            return self.poll_original_home(centered)
        if self.preparing:
            if self.preparation_image and not self.preparation_image_saved:
                self.preparation_image_saved = bool(self.cv2.imwrite(self.preparation_image, frame))
            if centered and self.motor.state == 'IDLE':
                self.stable = self.stable or now
                if now-self.stable >= self.required_stable_seconds:
                    self.motor.current = target
                    self.ready, self.preparing = True, False
            else: self.stable = None
            marker_one = markers.get(1)
            self.preparation_diagnostics = dict(
                detected_ids=sorted(markers), target=target,
                frame_size=[w, h],
                marker_1_error_px=None if marker_one is None else
                    [float(marker_one[0]-w/2), float(marker_one[1]-h/2)],
                centered=bool(centered), motor_state=self.motor.state,
                stable_seconds=0.0 if self.stable is None else now-self.stable,
                required_stable_seconds=self.required_stable_seconds,
                tolerance_px=[self.tx, self.ty], ready=self.ready)
            return
        if not self.active: return
        if self.motion_algorithm == 'original' and self.active['device'] == 'motor':
            return self.poll_original_motor(centered)
        self.success_detail = type(self).success_detail
        if self.motion_algorithm == 'original':
            self.success_detail = 'original algorithm: matching motor STOPPED and fresh centered image; no alignment hold required'
        if self.motion_algorithm != 'original' and now-self.started > self.move_timeout:
            d = self.alignment_diagnostics
            raise RuntimeError(f"overall movement/alignment timeout; stage={self.stage}; "
                               f"target={target}; ids={d['detected_ids']}; error_px={d['target_error_px']}; "
                               f"motor={self.motor.state}; max_stable={self.max_stable_seconds:.3f}/{self.stable_seconds:.3f}s")
        if center is not None: self.last_seen = now
        if self.motion_algorithm != 'original' and now-self.last_seen > self.marker_timeout:
            raise RuntimeError('target marker missing timeout')
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
                if now-self.stable >= self.required_stable_seconds:
                    self.active = None
                    return True
            else: self.stable = None

    def stop(self):
        self.active = None
        self.ready = False
        self.preparing = False
        self.original_homing = None
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
