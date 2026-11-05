# Motion guard: no protective stops from any start pose

## Why the arm stops (UR3e, PolyScope 5)

| Cause | Where it came from in this code | Fix in the guard |
| --- | --- | --- |
| **movel through a singularity** (C154; joint speed limit): wrist `sin q5 ~ 0`, elbow stretched/folded `sin q3 ~ 0`, wrist centre on the base axis | `point_above` / `translate` / `goto_table_point` send `movel` to joint targets; the old path check interpolated **joints**, but the controller moves the TCP on a **straight line** | movel is re-simulated along the straight line with IK; refused if any joint must turn faster than `movel_max_rad_per_m` per metre of tool travel, or swapped for an equivalent movej when that stays within 2 cm of the line |
| **movej sweep through the table, the base or the arm itself** | named-pose skills go movej from wherever the arm is, with no path check; `max_joint_change_rad` was raised to 2π | every movej is sampled every 0.03 rad against capsules of the links (table, base column, self, `obstacles`) |
| **joint position limit** | — | per-joint limits with margin (`joint_limits_rad`, elbow 160°) |
| blocked direct move → stop | — | **via points**: lifted start, lifted goal, hub poses, then random samples; lazy PRM + A* on travel time, then shortcut |
| hard stop at every via point (C204 with high accelerations) | no blends | `r=` blend (≤ 2 cm, ≤ 40 % of the neighbouring segments) only on added via points |
| wrong payload / TCP / centre of gravity | pendant installation | **configuration, not code**: set them on the pendant (Installation > General > TCP / Payload) |

## How it runs

1. The operator confirms a motion as before (the gate is unchanged).
2. Just before sending, `Gate._guarded_motion` reads the **actual joints** and calls `MotionGuard.secure`
   (in a worker thread, ~0.1–0.5 s; STOP still works and cancels it).
3. Each step: movel checked on its real straight line; movej checked in joint space and, if blocked,
   replaced by the fastest safe chain of via points **ending at the same confirmed target**.
4. Anything still unsafe is **not sent** (`NOT_STARTED`, "percorso non sicuro: …"). Guard errors fail closed.
5. `point_at_saved_cup`: if the arc planner refuses the start pose (e.g. home, tool not pointing down),
   `hover_and_lower` tries **every IK branch (8) × 8 tool headings**, keeps the cheapest safe route by
   movej time, then a slow vertical descent checked as a movel.

Offline results (sim poses): 25/25 random blocked movej solved, mean 1 via point, planning 0.24 s
mean / 0.52 s max; hover from `home` to 5 table points that the arc planner refused: all planned.

## Configure on the cell (`config/robot.yaml → guard`)

* `tool_length_m`: the **physical** tool beyond the flange, equal to the TCP z set on the pendant (the planner's
  15 cm virtual tip is not a rigid needle). Bare flange = 0.
* `joint_limits_rad`: copy the pendant's Safety > Joint Limits if the staff restricted them.
* `obstacles`: boxes in the base frame (camera stand, cardboard box, laptop).
* `hubs`: named poses usable as via points; in real mode only poses with `verificata_reale: true`.
* `enabled: false` restores the old behaviour.

## Not covered

* Exact link geometry: capsules on the DH chain with inflated radii, not the CAD mesh. Lateral link offsets
  are horizontal, so the table check is exact enough; the self-collision margin is 2 cm.
* Speed/force safety limits of the pendant are not read; motions keep the conservative real-mode speeds.
* Time-optimal retiming (TOPP-RA) is not used: the robot's own movej profile is kept.

## References

* Universal Robots, *Understanding protective stops* and C153 preventive actions (payload/TCP, blends,
  singularities, accelerations).
* K. Hawkins, *Analytic Inverse Kinematics for the Universal Robots UR-5/UR-10 Arms*, 2013 (8 IK branches).
* R. Bohlin, L. Kavraki, *Path Planning Using Lazy PRM*, ICRA 2000; OMPL / MoveIt use the same PRM family.
* Q.-C. Pham, H. Pham, *TOPP-RA*, IEEE T-RO 2018 (possible next step for time-optimal timing).
