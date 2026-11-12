"""Validate configuration before opening a device or accepting work."""

import math


AXES = ("x", "y", "z", "rx", "ry", "rz")


def finite_number(value, name, minimum=None, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not a boolean.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite.")
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive.")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return result


def validate_pose(pose, bounds=None):
    if not isinstance(pose, dict):
        raise ValueError("Pose must be a mapping.")
    result = {axis: finite_number(pose[axis], axis) for axis in AXES}
    if bounds is not None:
        for axis, value in result.items():
            low, high = bounds[axis]
            if not low <= value <= high:
                raise ValueError(f"{axis}={value} outside [{low}, {high}].")
    return result


def validate_config(config, pose_names=("home",)):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a mapping.")
    result = dict(config)
    for key, default in (
        ("simulation_mode", True), ("hardware_enable", False),
        ("coordinates_confirmed", False), ("homing_enable", False),
        ("tool_axis_approach", False),
    ):
        value = config.get(key, default)
        if type(value) is not bool:
            raise ValueError(f"{key} must be a YAML boolean.")
        result[key] = value
    hardware = not result["simulation_mode"]
    if hardware and not (
        result["hardware_enable"] and result["coordinates_confirmed"]
    ):
        raise ValueError("Hardware and verified coordinates must be enabled.")

    bounds = config.get("workspace_bounds")
    if hardware and not isinstance(bounds, dict):
        raise ValueError("Hardware requires measured workspace_bounds.")
    if bounds is not None:
        checked = {}
        for axis in AXES:
            pair = bounds[axis]
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"workspace_bounds.{axis} needs [min, max].")
            low, high = [finite_number(v, axis) for v in pair]
            if low >= high:
                raise ValueError(f"Invalid workspace interval for {axis}.")
            checked[axis] = (low, high)
        bounds = checked
    result["workspace_bounds"] = bounds
    result["poses"] = {
        name: validate_pose(config["poses"][name], bounds)
        for name in pose_names
    }
    defaults = {
        "motion_timeout_sec": 60.0, "startup_observation_sec": 3.0,
        "simulation_step_delay_sec": 0.5, "pump_settle_sec": 1.0,
        "release_settle_sec": 0.5, "position_tolerance_mm": 1.0,
        "orientation_tolerance_deg": 1.0,
    }
    for key, default in defaults.items():
        result[key] = finite_number(
            config.get(key, default), key, minimum=0,
            positive=key == "motion_timeout_sec",
        )
    feedrate = config.get("motion_feedrate_mm_min")
    if hardware or feedrate is not None:
        feedrate = finite_number(feedrate, "motion_feedrate_mm_min",
                                 positive=True)
        if feedrate > 3000:
            raise ValueError("Feedrate exceeds the configured maximum 3000.")
    result["motion_feedrate_mm_min"] = feedrate
    return result


def controller_options(config):
    return {
        "simulation_mode": config["simulation_mode"],
        "serial_port": config.get("serial_port", "/dev/ttyUSB0"),
        "baudrate": int(config.get("baudrate", 115200)),
        "motion_timeout_sec": config["motion_timeout_sec"],
        "simulation_step_delay_sec": config["simulation_step_delay_sec"],
        "startup_observation_sec": config["startup_observation_sec"],
        "position_tolerance_mm": config["position_tolerance_mm"],
        "orientation_tolerance_deg": config["orientation_tolerance_deg"],
        "motion_feedrate_mm_min": config["motion_feedrate_mm_min"],
        "workspace_bounds": config["workspace_bounds"],
    }
