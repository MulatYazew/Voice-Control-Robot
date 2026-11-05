# Hardware slot checklist

A person stays at the physical E-stop for the whole slot. The software STOP is not a safety-rated E-stop.

Poses in `config/poses.yaml` are URSim placeholders (`verificata_reale: false`). Real mode refuses them until they are recorded.

1. Read the model and the PolyScope version from the pendant. Write them into `config/robot.yaml`. Enable Remote Control. Do not guess the model.
2. Jog to each named pose and record it:
   `python scripts/record_pose.py <name> --mode real`
   Include `*_spinta_scarto_*` (reject box) and the delivery zone. `workspace.delivery_zone` is `destra` and is UNVERIFIED until this step.
3. Run each skill alone at `motion.real` speeds. Then STOP mid-motion with the button, with Space, and with a spoken STOP in Italian, English, Chinese, Japanese and Amharic (or typed Amharic if the voice probe did not pass).
4. Camera 1 only (Arm 1). Set `camera.available: true` and the source in `config/camera.yaml`. Calibrate the three zone polygons from the UI. Capture the empty-tray background with `POST /api/camera/background`. Enrol the real packs (`scripts/enroll_products.py` or the labelling approval screen). Arm 3 stays `camera.available: false`.
5. Photograph real compliant and defective packs into `data/real/`. Rerun `python -m eval.labelling.run_eval`. Change `config/labelling.yaml` only if a measured result requires it, and write the change in `PROJECT_STATUS.md`.
6. Measure confirmation-end → first motion on the real arm, at least 5 trials. Record a backup video with audio. Write the numbers into `eval/results/` from the measurement, not from an estimate.

Amharic voice is EXPERIMENTAL · NOT TESTED until `scripts/probe_language.py` has a transcript log. Chinese, Japanese and Amharic lexicon entries have `reviewed_by: null` until a native speaker signs them.
