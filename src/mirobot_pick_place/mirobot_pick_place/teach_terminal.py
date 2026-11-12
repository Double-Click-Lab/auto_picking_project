"""Interactive Cartesian teaching; simulation default, no automatic motion."""

import argparse
from datetime import datetime
import logging
import math
from pathlib import Path
import re
import shlex
import json
import threading

import yaml

from .configuration import AXES, finite_number, validate_pose
from .first_motion_check import REFERENCE
from .mirobot_controller import MirobotController
from .sequence import approach_pose, build_plan, positions


HELP = """
home                 명시적 홈밍 ($H). 여러 축이 움직임. 자동 수행하지 않음.
status               현재 보고 좌표 조회 (s 도 가능)
x+50 / x-10          지정 mm만큼 X 이동 (y/z도 가능, 1~100 mm)
x 10 / y -10 / z 1 해당 축을 지정한 mm만큼 상대 이동 (1~100 mm)
rx+5 / rx-2          지정한 도만큼 Rx 변경 (ry/rz도 가능, 1~90도)
rx 5 / ry -10        지정한 도만큼 자세각 변경 (1~90도, XYZ 유지)
goto X Y Z           절대 XYZ 지정. 현재 위치와 거리 50 mm 이하만 허용
speed 200            이동 속도 설정 (1~3000 mm/min). speed만 입력하면 현재값 확인
save home            현재 보고 좌표 저장
save pick            PICK 저장 (pick_approach/place/place_approach도 가능)
list                 저장한 좌표와 파일 경로 표시
load FILE            기존 좌표 파일 읽기. 장치 이동 없음
approach 10          자동 접근 높이(mm). 기본 10, 저장한 접근점이 있으면 우선 사용
tool_axis on/off     도구축 방향 접근·상승 ON / 기존 수직 Z 접근 OFF (기본 OFF)
default              속도 3000, tool_axis ON, 접근 40 mm 기본 좌표 설정
plan                 실행할 좌표·순서 표시. 이동 없음
dryrun               펌프 없이 실제 전체 경로 1회 검증 (팔이 움직임)
run                  흡착·이동·해제·HOME 복귀 1회 (기본은 dryrun 필요)
cancel               실행 중 다음 단계를 취소. 현재 명령 완료 대기, 비상정지 아님
limits               이 세션의 소프트웨어 제한 표시
help                 이 도움말
quit                 연결 종료. 자동 이동·펌프 OFF·복귀 없음
XYZ 명령은 자세 유지, Rx/Ry/Rz 명령은 보고 XYZ 유지. 기본 G01 F200.
좌표 저장만으로 흡착하지 않음. run에서만 펌프 작동. OFF=실제 해제 보장 아님.
Ctrl+C는 비상 정지가 아니며 진행 중인 명령은 계속될 수 있음.
"""
POSE_NAMES = {"home", "pick", "pick_approach", "place", "place_approach"}
MAX_DISTANCE_MM = 100.0
MAX_ANGLE_DEG = 90.0
MAX_GOTO_DISTANCE_MM = 50.0
TEACH_FEEDRATE = 200.0
MAX_FEEDRATE = 3000.0
DEFAULT_PICK = {
    "x": 279.674, "y": 9.985, "z": 100.666,
    "rx": 0.0, "ry": -30.009, "rz": 0.0,
}
DEFAULT_PLACE = {
    "x": -1.278, "y": -199.965, "z": 77.093,
    "rx": 0.0, "ry": 0.0, "rz": 0.0,
}


class TeachTerminal:
    def __init__(self, controller, output, simulation=False, xyz_bounds=None):
        self.controller = controller
        self.output = Path(output)
        self.simulation = simulation
        self.xyz_bounds = xyz_bounds
        self.approach_height = 10.0
        self.tool_axis = False
        self.initialized = False
        self.home_pose = None
        self.poses = {}
        self._busy = False
        self._run_lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker = None
        self._verified_plan = None
        self._default_run_enabled = False
        self.last_run_result = None
        self.active_step = None
        self._owns_output = False
        self._sim_status = dict(REFERENCE, pump=0.0, valve=0.0)
        if xyz_bounds is not None:
            for low, high in xyz_bounds.values():
                if not math.isfinite(low) or not math.isfinite(high):
                    raise ValueError("Bounds must be finite.")
                if low >= high:
                    raise ValueError("Bounds must have min < max.")

    def _check_bounds(self, pose):
        if self.xyz_bounds is not None:
            for axis in ("x", "y", "z"):
                low, high = self.xyz_bounds[axis]
                if not low <= pose[axis] <= high:
                    raise ValueError(
                        f"{axis}={pose[axis]}: session bounds [{low}, {high}]"
                    )

    def _fresh_status(self):
        if self.busy:
            raise RuntimeError("작업 중에는 status의 캐시값만 확인할 수 있습니다.")
        if self.simulation:
            return dict(self._sim_status)
        try:
            self.controller.wait_until_idle(reject_busy=True)
        except Exception:
            self.initialized = False
            raise
        status = self.controller.last_status
        if status is None:
            self.initialized = False
            raise RuntimeError("No valid status received.")
        return dict(status)

    def status(self):
        try:
            status = self._fresh_status()
        except Exception as error:
            print(f"상태 조회: {error}", flush=True)
            status = (dict(self._sim_status) if self.simulation
                      else self.controller.last_status)
        state = ("Idle" if self.initialized else "Uninitialized") \
            if self.simulation else self.controller.last_state
        print(f"state={state}, "
              f"initialized={self.initialized}, "
              f"speed={self.controller.motion_feedrate_mm_min:g} mm/min, "
              f"tool_axis={'on' if self.tool_axis else 'off'}, "
              f"step={self.active_step or '-'}, "
              f"phase={self.controller.operation_phase}",
              flush=True)
        if status is not None:
            coordinates = " ".join(
                f"{a.upper()}={status[a]:.3f}" for a in AXES
            )
            print("  " + coordinates, flush=True)
            print(f"  Pump PWM={status['pump']} Valve PWM={status['valve']}",
                  flush=True)

    def _pose_for_work(self):
        if not self.initialized:
            raise RuntimeError("먼저 home 명령으로 초기화하세요.")
        status = self._fresh_status()
        if status["pump"] != 0 or status["valve"] != 0:
            self.initialized = False
            raise RuntimeError("빈 흡착컵/PWM 0에서만 좌표를 설정하세요.")
        pose = validate_pose(status)
        self._check_bounds(pose)
        return pose

    def home(self):
        self.initialized = False
        self._verified_plan = None
        self.controller.home()
        if self.simulation:
            self._sim_status = dict(REFERENCE, pump=0.0, valve=0.0)
        status = self._fresh_status()
        if status["pump"] != 0 or status["valve"] != 0:
            raise RuntimeError("Pump/valve PWM is not zero after homing.")
        self._check_bounds(validate_pose(status))
        self.home_pose = validate_pose(status)
        self.initialized = True
        self.status()

    def move(self, axis=None, delta=None, xyz=None):
        if xyz is None and axis not in AXES:
            raise ValueError("축은 x/y/z/rx/ry/rz 중 하나여야 합니다.")
        current = self._pose_for_work()
        target = dict(current)
        if xyz is not None:
            target.update(zip(("x", "y", "z"), xyz))
        else:
            target[axis] += delta
        target = validate_pose(target)
        self._check_bounds(target)
        distance = math.dist(
            [current[a] for a in ("x", "y", "z")],
            [target[a] for a in ("x", "y", "z")],
        )
        if axis in ("rx", "ry", "rz"):
            angle = abs(target[axis] - current[axis])
            if not 1 <= angle <= MAX_ANGLE_DEG:
                raise ValueError("한 번 자세각 변경은 1~90도입니다.")
        elif axis in ("x", "y", "z"):
            delta_mm = abs(target[axis] - current[axis])
            if not 1 <= delta_mm <= MAX_DISTANCE_MM:
                raise ValueError("한 번 축 이동은 1~100 mm입니다.")
        elif distance <= 0 or distance > MAX_GOTO_DISTANCE_MM + 1e-9:
            raise ValueError("goto는 현재 위치에서 50 mm 이내여야 합니다.")
        self._verified_plan = None
        self._default_run_enabled = False
        try:
            self.controller.move_to("MANUAL_TEACH", target)
        except (Exception, KeyboardInterrupt):
            self.initialized = False
            raise
        if self.simulation:
            self._sim_status.update(target)
        self.status()

    def save(self, name):
        if name not in POSE_NAMES:
            raise ValueError(
                "저장 이름: home/pick/pick_approach/place/place_approach"
            )
        pose = self._pose_for_work()
        poses = dict(self.poses, **{name: pose})
        poses.setdefault("home", dict(self.home_pose))
        data = {
            "simulation_mode": True,
            "hardware_enable": False,
            "coordinates_confirmed": False,
            "homing_enable": False,
            "approach_height": self.approach_height,
            "tool_axis_approach": self.tool_axis,
            "motion_feedrate_mm_min": self.controller.motion_feedrate_mm_min,
            "metadata": {
                "source": "simulation" if self.simulation else "robot_status",
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "note": "Reported coordinates; cup TCP not calibrated.",
            },
            "poses": poses,
        }
        # First save exclusively creates a NEW file; never overwrite an input
        # configuration or an existing taught file from another session.
        mode = "w" if self._owns_output else "x"
        with self.output.open(mode, encoding="utf-8") as file:
            yaml.safe_dump(data, file, sort_keys=False, allow_unicode=True)
        self._owns_output = True
        self.poses = poses
        self._verified_plan = None
        self._default_run_enabled = False
        print(f"저장: {name} = {pose}\n파일: {self.output}", flush=True)

    @property
    def busy(self):
        with self._run_lock:
            return self._busy

    def load(self, path):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(data, dict)
                or not isinstance(data.get("poses"), dict)):
            raise ValueError("poses 매핑을 포함한 YAML 파일이 필요합니다.")
        if (not self.simulation
                and data.get("metadata", {}).get("source") != "robot_status"):
            raise ValueError("실물 모드에서는 실물에서 저장한 좌표만 읽습니다.")
        poses = {name: validate_pose(pose)
                 for name, pose in data["poses"].items() if name in POSE_NAMES}
        if not poses:
            raise ValueError("저장된 좌표가 없습니다.")
        for pose in poses.values():
            self._check_bounds(pose)
        height = finite_number(data.get("approach_height", 10),
                               "approach_height", positive=True)
        tool_axis = data.get("tool_axis_approach", False)
        if type(tool_axis) is not bool:
            raise ValueError("tool_axis_approach는 YAML boolean이어야 합니다.")
        self.poses = poses
        self.approach_height = height
        self.tool_axis = tool_axis
        self._verified_plan = None
        self._default_run_enabled = False
        print(f"좌표 읽기 완료: {path}. 이동·펌프 작동 없음.", flush=True)

    def _make_plan(self):
        if not self.initialized:
            raise RuntimeError("먼저 home으로 초기화하세요.")
        missing = {"pick", "place"} - self.poses.keys()
        if missing:
            raise ValueError(f"먼저 save로 저장하세요: {sorted(missing)}")
        poses = positions(
            self.poses.get("home", self.home_pose), self.poses["pick"],
            self.poses["place"], self.approach_height,
            self.poses.get("pick_approach"), self.poses.get("place_approach"),
            tool_axis=self.tool_axis,
        )
        for pose in poses.values():
            self._check_bounds(pose)
        signature = json.dumps((poses, self.approach_height, self.tool_axis,
                                self.controller.motion_feedrate_mm_min),
                               sort_keys=True)
        return build_plan(poses), signature

    def plan(self):
        plan, _ = self._make_plan()
        current = self._pose_for_work()
        print(f"실행 속도: {self.controller.motion_feedrate_mm_min:g} mm/min",
              flush=True)
        print(f"tool_axis={'on' if self.tool_axis else 'off'}, "
              f"접근 {'경로 거리' if self.tool_axis else 'Z 높이'}="
              f"{self.approach_height:g} mm", flush=True)
        if self.tool_axis:
            print("RPY/장착축 가정에 따른 계산입니다. 실제 방향 확인 필요. "
                  "직접 저장한 접근점 대신 접촉 자세에서 접근점을 계산합니다.",
                  flush=True)
        if not self._at_pose(current, plan[0].pose):
            clearance = approach_pose(current, self.approach_height,
                                      tool_axis=self.tool_axis)
            self._check_bounds(clearance)
            print(f"dryrun 시작 전 현재 위치에서 상승: {clearance}", flush=True)
        for index, step in enumerate(plan, 1):
            detail = step.pose if step.pose is not None else (
                "pump ON" if step.pump else "pump OFF"
            )
            print(f"[{index}/10] {step.name}: {detail}", flush=True)
        print("접근 위치·전체 경로를 확인한 뒤 dryrun을 먼저 실행하세요.", flush=True)

    @staticmethod
    def _at_pose(current, target):
        return all(
            (abs(current[a] - target[a]) if a in ("x", "y", "z")
             else abs((current[a] - target[a] + 180) % 360 - 180)) <= 0.5
            for a in AXES
        )

    def start_sequence(self, dry_run=False):
        if self.busy:
            raise RuntimeError("이미 실행 중입니다.")
        plan, signature = self._make_plan()
        current = self._pose_for_work()
        if not dry_run:
            if (not self._default_run_enabled
                    and self._verified_plan != signature):
                raise RuntimeError("동일 좌표로 dryrun을 먼저 완료하세요.")
            if not self._at_pose(current, plan[0].pose):
                self._verified_plan = None
                raise RuntimeError("HOME 위치가 아닙니다. dryrun을 다시 하세요.")
        clearance = None
        if dry_run and not self._at_pose(current, plan[0].pose):
            clearance = approach_pose(current, self.approach_height,
                                      tool_axis=self.tool_axis)
            self._check_bounds(clearance)
        with self._run_lock:
            if self._busy:
                raise RuntimeError("이미 실행 중입니다.")
            self._cancel.clear()
            self._busy = True
        self._verified_plan = None
        self.last_run_result = None
        self._worker = threading.Thread(
            target=self._sequence_worker,
            args=(plan, signature, dry_run, clearance),
            name="mirobot-taught-sequence",
        )
        try:
            self._worker.start()
        except Exception:
            with self._run_lock:
                self._busy = False
            self._worker = None
            raise

    def _sequence_worker(self, plan, signature, dry_run, clearance):
        label = "DRYRUN (펌프 없음)" if dry_run else "PICK & PLACE"
        print(f"{label} 시작. cancel로 다음 단계를 중단할 수 있습니다.", flush=True)
        try:
            if clearance is not None and not self._cancel.is_set():
                self.active_step = "START_CLEARANCE"
                print(f"시작 위치에서 상승 후 HOME 이동: {clearance}", flush=True)
                self.controller.move_to("START_CLEARANCE", clearance)
                if self.simulation:
                    self._sim_status.update(clearance)
            for index, step in enumerate(plan, 1):
                if self._cancel.is_set():
                    break
                self.active_step = f"{index}/10 {step.name}"
                print(f"[{index}/10] {step.name} 시작", flush=True)
                step.execute(self.controller, pumps=not dry_run)
                if self.simulation:
                    if step.pose is not None:
                        self._sim_status.update(step.pose)
                    elif not dry_run:
                        self._sim_status["pump"] = 1000.0 if step.pump else 0.0
                if step.pump is not None and not dry_run:
                    self._cancel.wait(1.0 if step.pump else 0.5)
                print(f"[{index}/10] {step.name} 완료"
                      + (" (펌프 생략)" if dry_run and step.pose is None else ""),
                      flush=True)
            if self._cancel.is_set():
                self.last_run_result = "canceled"
                self.initialized = False
                self.controller.ready = False
                print("취소 완료. 펌프 유지, 자동 복귀 없음. 상태 확인이 필요합니다.",
                      flush=True)
            else:
                self._verified_plan = signature
                self.last_run_result = "succeeded"
                print(f"{label} 완료, HOME 복귀. "
                      "실제 흡착·해제 여부는 눈으로 확인하세요.", flush=True)
        except Exception as error:
            self.initialized = False
            self.controller.ready = False
            self.last_run_result = f"failed: {error}"
            print(f"작업 실패: {error}. 펌프 유지, 자동 복귀 없음. "
                  "진행 중인 명령이 계속될 수 있습니다.", flush=True)
        finally:
            with self._run_lock:
                self._busy = False

    def shutdown(self):
        self._cancel.set()
        if self._worker is not None:
            self._worker.join()

    def execute(self, line):
        parts = shlex.split(line)
        if not parts:
            return True
        command = parts[0].lower()
        args = parts[1:]
        allowed_while_busy = {
            "status", "s", "cancel", "help", "list", "limits"
        }
        if self.busy and command not in allowed_while_busy:
            raise RuntimeError("작업 중입니다. cancel 후 완료될 때까지 기다리세요.")
        if command in ("quit", "q", "exit") and not args:
            return False
        if command == "home" and not args:
            self.home()
        elif command in ("status", "s") and not args:
            self.status()
        elif command == "help" and not args:
            print(HELP, flush=True)
        elif command == "limits" and not args:
            print(f"축 이동: 1~100 mm, 회전 변경: 1~90도\n"
                  f"goto 총 거리 ≤{MAX_GOTO_DISTANCE_MM:g} mm, "
                  f"F{self.controller.motion_feedrate_mm_min:g}\n"
                  f"XYZ 제한: {self.xyz_bounds or '미설정 (실제 도달 범위 아님)'}\n"
                  "이 제한은 IK/충돌/흡착컵 접촉 높이를 검증하지 않습니다.",
                  flush=True)
        elif command == "speed" and len(args) <= 1:
            if args:
                value = finite_number(args[0], "speed", positive=True)
                if not 1 <= value <= MAX_FEEDRATE:
                    raise ValueError("speed는 1~3000 mm/min입니다.")
                if value != self.controller.motion_feedrate_mm_min:
                    self.controller.motion_feedrate_mm_min = value
                    self._verified_plan = None
                    self._default_run_enabled = False
            print(f"speed={self.controller.motion_feedrate_mm_min:g} mm/min",
                  flush=True)
        elif command in AXES and len(args) == 1:
            self.move(axis=command, delta=finite_number(args[0], "delta"))
        elif (match := re.fullmatch(
                r"(x|y|z|rx|ry|rz)([+-])(.+)", command)) and not args:
            axis, direction, amount_text = match.groups()
            amount = finite_number(amount_text, "이동량", positive=True)
            maximum = (MAX_ANGLE_DEG if axis.startswith("r")
                       else MAX_DISTANCE_MM)
            unit = "도" if axis.startswith("r") else "mm"
            if not 1 <= amount <= maximum:
                raise ValueError(f"{axis} 이동량은 1~{maximum:g} {unit}입니다.")
            self.move(axis=axis, delta=amount if direction == "+" else -amount)
        elif command == "goto" and len(args) == 3:
            self.move(xyz=[finite_number(v, "coordinate") for v in args])
        elif command == "save" and len(args) == 1:
            self.save(args[0].lower())
        elif command == "load" and len(args) == 1:
            self.load(args[0])
        elif command == "default" and not args:
            self.controller.motion_feedrate_mm_min = 3000.0
            self.approach_height = 40.0
            self.tool_axis = True
            default_home = self.home_pose or dict(REFERENCE)
            self.poses.update({
                "home": validate_pose(default_home),
                "pick": dict(DEFAULT_PICK),
                "place": dict(DEFAULT_PLACE),
            })
            self._verified_plan = None
            self._default_run_enabled = True
            print("기본 실행 설정: speed=3000 mm/min, tool_axis=on, "
                  "approach=40 mm 및 plan에 표시된 기본 PICK/PLACE 좌표 설정.\n"
                  "이 설정에서는 dryrun 없이 run 가능하며, run은 실제 팔과 "
                  "펌프를 작동시킵니다. 먼저 현재 위치가 HOME인지 확인하세요.",
                  flush=True)
        elif command == "approach" and len(args) == 1:
            self.approach_height = finite_number(
                args[0], "height", positive=True
            )
            self._verified_plan = None
            self._default_run_enabled = False
            print(f"approach_height={self.approach_height} mm", flush=True)
        elif command == "tool_axis" and len(args) <= 1:
            if args:
                mode = args[0].lower()
                if mode not in ("on", "off"):
                    raise ValueError("tool_axis on 또는 tool_axis off입니다.")
                enabled = mode == "on"
                if enabled != self.tool_axis:
                    self.tool_axis = enabled
                    self._verified_plan = None
                    self._default_run_enabled = False
            print(f"tool_axis={'on' if self.tool_axis else 'off'}. "
                  "변경 후 plan과 dryrun을 다시 확인하세요.", flush=True)
        elif command == "plan" and not args:
            self.plan()
        elif command in ("dryrun", "run") and not args:
            self.start_sequence(dry_run=command == "dryrun")
        elif command == "cancel" and not args:
            if self.busy:
                self._cancel.set()
                print("취소 요청. 현재 명령 완료 후 다음 단계를 중단합니다.", flush=True)
            else:
                print("실행 중인 작업이 없습니다.", flush=True)
        elif command == "list" and not args:
            print(yaml.safe_dump(self.poses, sort_keys=False), flush=True)
            print(f"파일: {self.output}", flush=True)
        else:
            raise ValueError("알 수 없는 명령입니다. help를 입력하세요.")
        return True


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="실제 USB 연결. home/축 명령을 입력할 때만 동작.")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--xyz-bounds", nargs=6, type=float,
        metavar=("XMIN", "XMAX", "YMIN", "YMAX", "ZMIN", "ZMAX")
    )
    options = parser.parse_args(args)
    bounds = None
    if options.xyz_bounds is not None:
        bounds = dict(zip(("x", "y", "z"), zip(
            options.xyz_bounds[::2], options.xyz_bounds[1::2]
        )))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = options.output or Path.cwd() / f"taught_poses_{stamp}.yaml"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    controller = None
    trace_stream = None
    try:
        # Validate options before a physical device is opened.
        session = TeachTerminal(None, output, simulation=not options.execute,
                                xyz_bounds=bounds)
        if output.exists():
            raise FileExistsError(f"기존 파일을 덮어쓰지 않습니다: {output}")
        trace_callback = None
        if options.execute:
            trace_path = Path.cwd() / f"mirobot_serial_trace_{stamp}.log"
            trace_stream = trace_path.open("x", encoding="utf-8")

            def trace_callback(event, message):
                timestamp = datetime.now().isoformat(timespec="milliseconds")
                trace_stream.write(f"{timestamp} {event} {message!r}\n")
                trace_stream.flush()

            print(f"시리얼 진단 로그: {trace_path}", flush=True)
        controller = MirobotController(
            logging.getLogger("teach_terminal"),
            simulation_mode=not options.execute, serial_port=options.port,
            motion_feedrate_mm_min=TEACH_FEEDRATE, motion_timeout_sec=180.0,
            position_tolerance_mm=0.5, orientation_tolerance_deg=0.5,
            simulation_step_delay_sec=0.0, log_movement_targets=False,
            trace_callback=trace_callback,
        )
        session.controller = controller
        print("MODE: " + ("HARDWARE" if options.execute else "SIMULATION"),
              flush=True)
        print(HELP, flush=True)
        session.execute("limits")
        print("자동 홈밍 없음. 초기화하려면 home을 직접 입력하세요.", flush=True)
        while True:
            try:
                if not session.execute(input("mirobot> ")):
                    break
            except (ValueError, KeyError, OSError, RuntimeError) as error:
                print(f"명령 실패: {error}", flush=True)
                if controller.ready is False and options.execute:
                    session.initialized = False
                    print("오류 후 자동 재시도/복귀 없음. 상태를 확인하세요.",
                          flush=True)
        return 0
    except (EOFError, KeyboardInterrupt):
        print("종료. 정지 명령은 보내지 않으며 진행 중 동작은 계속될 수 있습니다.",
              flush=True)
        return 0
    except Exception as error:
        print(f"시작 실패: {error}", flush=True)
        return 1
    finally:
        if controller is not None:
            session.shutdown()
        if controller is not None:
            controller.close()
        if trace_stream is not None:
            trace_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
