"""Shared Pick & Place plan for ROS Actions and interactive teaching."""

from dataclasses import dataclass
import math

from .configuration import finite_number, validate_pose


@dataclass(frozen=True)
class Step:
    name: str
    pose: dict = None
    pump: bool = None

    def execute(self, controller, pumps=True):
        if self.pose is not None:
            controller.move_to(self.name, self.pose)
        elif pumps:
            controller.set_pump(self.pump)


def approach_pose(contact, distance, tool_axis=False):
    """Retract along base Z, or Rz(yaw) Ry(pitch) Rx(roll) times local +Z.

    Local +Z is defined as the retract direction for the installed downward
    cup at zero RPY. This mounting/frame assumption needs physical validation.
    """
    if type(tool_axis) is not bool:
        raise ValueError("tool_axis must be a boolean.")
    contact = validate_pose(contact)
    distance = finite_number(distance, "approach_height", positive=True)
    direction = (0.0, 0.0, 1.0)
    if tool_axis:
        roll, pitch, yaw = (math.radians(contact[a])
                            for a in ("rx", "ry", "rz"))
        sr, cr = math.sin(roll), math.cos(roll)
        sp, cp = math.sin(pitch), math.cos(pitch)
        sy, cy = math.sin(yaw), math.cos(yaw)
        direction = (cy * sp * cr + sy * sr,
                     sy * sp * cr - cy * sr, cp * cr)
        if direction[2] <= 1e-6:
            raise ValueError(
                "Tool-axis retract must point upward; check RPY/mounting."
            )
    target = dict(contact)
    for axis, component in zip(("x", "y", "z"), direction):
        target[axis] += distance * component
    return validate_pose(target)


def positions(
    home, pick, place, height, pick_approach=None, place_approach=None,
    tool_axis=False,
):
    if type(tool_axis) is not bool:
        raise ValueError("tool_axis must be a boolean.")
    height = finite_number(height, "approach_height", positive=True)
    home, pick, place = [validate_pose(p) for p in (home, pick, place)]
    if tool_axis:
        # ON always derives both approaches from their contact attitudes;
        # explicit taught approach points remain available when switched OFF.
        pick_approach = approach_pose(pick, height, tool_axis=True)
        place_approach = approach_pose(place, height, tool_axis=True)
    pick_approach = validate_pose(
        pick_approach if pick_approach is not None
        else approach_pose(pick, height)
    )
    place_approach = validate_pose(
        place_approach if place_approach is not None
        else approach_pose(place, height)
    )
    for approach, contact in ((pick_approach, pick), (place_approach, place)):
        if approach["z"] <= contact["z"]:
            raise ValueError("Approach Z must be above contact Z.")
    return dict(home=home, pick=pick, place=place,
                pick_approach=pick_approach, place_approach=place_approach)


def build_plan(poses):
    return (
        Step("HOME", pose=dict(poses["home"])),
        Step("PICK_APPROACH", pose=dict(poses["pick_approach"])),
        Step("PICK", pose=dict(poses["pick"])),
        Step("VACUUM_ON", pump=True),
        Step("LIFT_FROM_PICK", pose=dict(poses["pick_approach"])),
        Step("PLACE_APPROACH", pose=dict(poses["place_approach"])),
        Step("PLACE", pose=dict(poses["place"])),
        Step("VACUUM_OFF", pump=False),
        Step("LIFT_FROM_PLACE", pose=dict(poses["place_approach"])),
        Step("RETURN_HOME", pose=dict(poses["home"])),
    )
