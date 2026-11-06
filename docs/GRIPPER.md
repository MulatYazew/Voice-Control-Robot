# Semicircle gripper (config/semicircle_gripper.step)

Answers to `GrokArm_UR3e_Gripper_TCP_Recalibration.md`, for the code as it is on 2026-11-05.

## 1. Existing TCP and motion architecture

| Question | Answer in this repo |
| --- | --- |
| Interface | URScript sent to the secondary port 30002 (`robot/ur.py: urscript`), state from RTDE 30004, mode/safety from Dashboard 29999 |
| Motion commands | always **joint vectors** (`movej q` / `movel q`); the controller never receives a Cartesian pose from us |
| TCP | the app does **not** call `set_tcp`. Cartesian targets are solved by our own IK (`robot/kinematics.py`, `robot/ur_ik.py`) for the **flange** (guard `tool_length_m: null` = 0). The pendant TCP therefore does not change any target. |
| Payload | `set_payload(gripper.mass_kg, gripper.cog_m)` is the first line of every program (`urscript(..., gripper)`) |
| Home / rest | named joint poses in `config/poses.yaml` (`home`, `attesa`), reached by movej through the motion guard |
| Gripper actuation | none: the gripper is passive. Grasp = geometry + motion (`skills/grasp.py`) |
| Camera → robot | `data/plane.json`: homography from the four orange tapes (left eye of the ELP stereo pair, 1600x1200) to table X/Y in the base frame (`vision/plane.py`). It returns a **point on the table (cup base)**, not a flange or TCP target. |
| Safety before sending | `skills/motion_guard.py` re-checks every step from the actual joints (links, gripper capsules, table, limits, singularities). `guard.ignore` lists checks switched off. |

No double compensation: the tool geometry is used only in `grasp.py` (target → flange) and in the guard capsules.

## 2. Gripper geometry (STEP + photos)

STEP: one solid, mm. Flange plate ⌀63 x 8 mm with 4 x M6 clearance holes on ⌀50 (ISO 9409-1-50-4-M6) and a
⌀31.3 centring boss. A beam runs **perpendicular to the flange axis** (photos 2026-11-05); at its end a level half
ring, inner ⌀53, outer ⌀67, 10 mm tall, mouth facing along the flange axis. Bounding box 116.5 x 67 x 63 mm.
The CAD centre of mass is not used as a TCP.

## 3. Flange → gripper transform used

Flange frame F (UR: z out of the flange). Beam direction `b` = one of ±x_F / ±y_F (`gripper.offset_axis_flange`).

* ring centre `c = o_F + 0.012 z_F + 0.1115 b`   (`ring_axial_m`, `ring_offset_m`)
* ring axis = `b`; mouth towards `+z_F`

Evidence: offset 111.5 mm and ring radii from the STEP; axial 12 mm and "beam perpendicular" from the photos.
`b` is **not** in the STEP (4 bolts allow 4 rolls): it is learned from the first actual pose where the flange axis is
level and one flange axis points straight down (ring hanging), or written in `offset_axis_flange`.

## 4. TCP

Effective grasp point = ring centre `c` above. As a UR pose relative to the flange it would be
`p[0.1115·b_x, 0.1115·b_y, 0.012, 0, 0, 0]` (m, rad; no rotation). It is **not applied** with `set_tcp`, because no
motion uses the pendant TCP (section 1) and `b` is not yet measured.

## 5. Measurements still needed

1. `offset_axis_flange`: on the pendant, with the ring hanging straight down, which flange axis points down.
2. `ring_axial_m`: flange face → ring centre along the flange axis (ruler).
3. `mass_kg`, `cog_m`: weigh the part.
4. The cups: bottom diameter, top diameter, height (`grasp:`). The ring only enters where the cup is < ⌀53.
5. Table height in the base frame (`guard.table_z_m` / `relative.table_z_m`) if the base is not on the table plane.
6. Real link radii, to switch `autocollisione` / `pinza_braccio` back on (`guard.ignore`).

## 6. Pick / place sequence (`skills/grasp.py`)

Over every cup → down behind the target → slide the ring around the base (2 cm/s) → lift 8 cm. `place_cup`
reverses it. Approach clearance, ring height, slide and lift are in `robot.yaml → grasp:`. Every plan is checked by
the motion guard and re-checked from the actual joints before it is sent.

## 7. Validation from Home (staff present, reduced speed, hand on the E-stop)

1. `home`; record joints and the pendant's flange pose.
2. Put the arm with the flange axis level and the ring down; note which flange axis points down → `offset_axis_flange`.
3. Text mode: `往左 2 厘米`, `往上 2 厘米`: the ring must follow.
4. One cup between the tapes: `指一下蓝色杯子` (pointing) must hover above it.
5. `拿起蓝色纸杯` → read the proposal → confirm → watch the slide; STOP if the ring touches the cup wall.
6. `放下杯子`, then `home`.
