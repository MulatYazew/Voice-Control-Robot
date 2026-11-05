# VOCE — Intelligent Robotic Assistant

*One platform, five languages, eyes on Arm 1, voice on both.*

The operator speaks Italian, English, Mandarin, Japanese or Amharic. Grok proposes. The deterministic backend validates. The operator confirms. The arm executes a named skill, or one labelling cycle. STOP works in every language and does not depend on Grok.

- **Arm 3 (Voice Control):** no camera. The arm lifts, puts down or pushes a tray, or points. It cannot grasp an object.
- **Arm 1 (Labelling):** camera 1. Read a model pack, inspect, then move the tray: compliant to the right, non-compliant into the reject box, doubt stops for the operator.
- A new product is **enrolled**, not retrained: one photo, OCR keywords and ORB descriptors. No GPU and no per-SKU dataset. Approval of tolerances stays on the screen.

> **Grok understands and proposes. Computer vision observes and measures. Deterministic software validates. The safety layer authorises. The robot executes. The operator remains in control.**

Grok Hackathon · Track 04 (Voice Control). An operator speaks Italian into the laptop microphone. **Grok Voice** (realtime) understands the request and proposes one of five predefined skills of the Universal Robots "Arm 3". The backend validates the proposal and asks for spoken confirmation. Only then does it execute the skill and report the actual result. **STOP** is available at any moment and doesn't depend on Grok.

> **Grok proposes. The backend decides. The robot executes only validated and confirmed skills.**

---

## 1. Objective (Mode A)
Let people who don't program robots call up ready-made operations by voice, safely: every movement needs explicit confirmation, and a stop is always available. Free trajectories and unconfirmed movements are out of scope by design.

## 2. Architecture

```
 Browser (React)                      FastAPI backend (Python)                               xAI
 ─────────────────                    ──────────────────────────────────────────────          ─────────────
 mic → AudioWorklet ── PCM16 24k ──▶  /ws/voice ── voice/grok_session.py ──── wss ─────▶  Grok Voice
 speaker ◀── Grok audio ◀───────────                │  (key stays here)   ◀── tool calls ──  (server VAD,
 UI ◀── state/transcripts ── /ws/events             ▼                     ◀── transcripts ─  transcription)
 STOP button / Space ── POST /api/stop ──▶  safety/gate.py   ◀── conversation/tools.py
 RIPRISTINA ── POST /api/reset ─────────▶   │  deterministic: validation, confirmation, STOP latch, FAULT
 TEXT MODE ── POST /api/text-command ───▶   │  skills/registry.py  ← config/skills.yaml + poses.yaml
 camera frame ── perception/camera.py ──▶   │  perception/finder.py + catalog.py  (evidence only; never a movement)
                                            ▼
                                    robot/base.py RobotAdapter
                                    ├─ robot/sim.py  SimAdapter (in-process)
                                    └─ robot/ur.py   URAdapter: RTDE :30004 (feedback) · Secondary :30002 (fixed
                                                     URScript per skill) · Dashboard :29999 (stop / state)
```

| Path | Role |
|---|---|
| `backend/app/safety/gate.py` | State machine IDLE → PENDING_CONFIRMATION → EXECUTING → IDLE · STOPPED · FAULT |
| `backend/app/safety/phrases.py` | Deterministic STOP / confirmation / cancel checks, five languages (`config/lexicon.yaml`) |
| `backend/app/perception/` | Camera, zone polygons, colour/OCR finder, product catalogue by enrolment |
| `backend/app/skills/registry.py` | Skill registry, parameter validation, preconditions, fixed motion sequences |
| `backend/app/voice/prompt_it.md` | The Italian system prompt (single file) |
| `backend/app/voice/grok_session.py` | Grok realtime relay, tool dispatch, reconnection, metrics |
| `backend/app/robot/ur.py` | URSim / real UR adapter (pure Python RTDE + Dashboard + Secondary) |
| `backend/app/launcher.py` | `start()` / `stop()` used by the notebook and `start.sh` |
| `frontend/` | VOCE industrial UI (React + TypeScript + Vite) |
| `eval/` | 81-case test set, rules baseline, Grok-vs-rules evaluation, STOP latency trials |

## 2b. Languages

| Language | Deterministic STOP / yes / no | Rules engine (2026-11-05) | Grok Voice |
|---|---|---|---|
| Italian | TESTED | 76/81, same five misses as 2026-11-04 | TESTED live on 2026-11-04 |
| English | TESTED | 18/18 new cases | TESTED live on 2026-11-04 |
| Mandarin | TESTED in `tests/test_multilingual_phrases.py` | 18/18 | NOT TESTED as speech today |
| Japanese | TESTED in `tests/test_multilingual_phrases.py` | 18/18 | NOT TESTED as speech today |
| Amharic | TESTED on text, including romanised fallbacks | 18/18, `revisione=da_verificare` | EXPERIMENTAL · NOT TESTED. Not in the official STT list. Use TEXT MODE if speech does not transcribe |

Lexicon entries for zh, ja and am have `reviewed_by: null` in `config/lexicon.yaml`. They are used and are UNVERIFIED until a native speaker signs them. The rules-engine file is `eval/results/latest_multilingual.json`.

## 3. Hardware capabilities (authoritative)
- Universal Robots arm "Arm 3"; printed tool with **fork, pusher and indicating tip**; **no gripper, no camera**.
- Objects sit in slotted trays. **Positions are assumed from configuration, never detected.** The UI and reports say so explicitly ("presunto").
- Model, PolyScope version, zones, push directions and TCP offsets are **UNKNOWN until verified** on the hardware (`config/robot.yaml`, `PROJECT_STATUS.md`).

## 4. Prerequisites
macOS or Linux · Python ≥ 3.11 (tested 3.14) · Node ≥ 18 (tested 26) · Docker (for URSim) · an xAI API key · Chrome for the UI.

## 5. Installation
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
(cd frontend && npm install && npm run build)
```

## 6. Environment
```bash
cp .env.example .env      # set XAI_API_KEY; never commit .env
```
| Variable | Default | |
|---|---|---|
| `XAI_API_KEY` | — | backend only, never sent to the browser or logged |
| `ROBOT_MODE` | `ursim` | `sim` · `ursim` · `real` |
| `XAI_MODEL` / `XAI_VOICE` / `LANG_HINT` | `grok-voice-latest` / `eve` / `it` | |
| `URSIM_HOST` | `::1` | see §7 |
| `WAKE_WORD` | — | e.g. `braccio` for noisy rooms |

## 7. URSim
```bash
scripts/ursim.sh start        # official image universalrobots/ursim_e-series (x86, emulated on Apple Silicon)
open http://localhost:6080/vnc.html
```
Do this once in the PolyScope GUI, because external URScript is **rejected in Local Control**:
1. Confirm the safety configuration.
2. ☰ → Settings → System → **Remote Control → Enable** → Exit.
3. Turn the **Simulation** switch (bottom right) **ON**. It's only clickable in Local mode.
4. Switch the header toggle **Local → Remote Control**.
5. Run `scripts/ursim.sh init` (power on + brake release), then `scripts/ursim.sh status`.

These settings live in the container. `scripts/ursim.sh stop` deletes the container, and with it the settings.

**Port conflict (macOS):** UR's desktop *URSim.app* listens on `127.0.0.1:30001-30004`. With it running, connections to 127.0.0.1 silently reach *that* simulator. VOCE therefore connects to URSim at `::1`, which only the Docker container serves.

## 8. Start the application
```bash
./start.sh              # ursim (default) → http://localhost:8000
./start.sh sim          # in-process simulator, no Docker needed
```
Or from Jupyter, with `notebooks/run_application.ipynb` and the kernel **Python (Voice-Control-Robot)**:
1. `prepare(mode)`: a read-only pre-flight that checks the configuration, API key, Python packages, Tesseract, Grok, the robot connection (mode, safety, Remote Control) and the port. It builds the frontend and generates the synthetic dataset if they're missing.
2. `start(mode)`: starts VOCE. It is idempotent.
3. `run_tests()` (optional).
4. `run_evaluations(...)` (optional; uses the xAI API, and `latency=True` moves the robot).
5. `handle.stop()`.

All logic lives in `backend/app/preflight.py` and `backend/app/launcher.py`.

## 9. Grok configuration
Realtime WebSocket `wss://api.x.ai/v1/realtime?model=grok-voice-latest`, Bearer auth from the backend. Session settings:
- `server_vad`
- PCM 24 kHz in and out
- `audio.input.transcription.language_hint = it`, plus key terms
- 7 function tools

All event names were verified live on 2026-11-04 against `grok-voice-3.0` (see `PROJECT_STATUS.md`).

## 10. Voice demonstration
1. Open `http://localhost:8000` in Chrome.
2. Click **AVVIA ASSISTENTE VOCALE** and allow the microphone.
3. Speak Italian, e.g. «Solleva il vassoio a sinistra» → «Sollevo con la forca il vassoio nella zona sinistra. Confermi?» → «Sì, confermo» → the arm moves → the report is spoken.

Use a headset in a noisy room. The mic can be paused or the voice session closed at any time.

With `safety.end_voice_on_stop` (the default), a spoken STOP or the STOP button stops the robot and then ends the voice session. «Chiudi assistente» / "close the assistant" does the same. A Grok-only drop does not stop the arm: the button remains the STOP. The browser socket dropping while the arm is moving still stops the arm. A dropped link does not restore a pending action.

## 11. Text simulation mode
Switch on **Modalità testo** in the UI, or `POST /api/text-command {"text": "...", "engine": "rules"|"grok"}`. It's labelled **TEXT MODE — NOT VOICE** everywhere. The `rules` engine works without Grok. It's for development and evaluation only and is never presented as voice operation.

## 11b. Italian and English
- The **ITALIANO | ENGLISH** selector in the header changes the interface, the voice session's recognition hint (`/ws/voice?lang=`), text-mode replies and the explanations shown.
- Grok always replies in the language the operator is speaking.
- Both languages map to the **same canonical action**: «Solleva il vassoio a sinistra» and "Lift the tray on the left" both become `solleva_vassoio {posizione: sinistra}`.
- STOP, confirmation, uncertainty and cancellation phrases are recognised in both languages, e.g. «Fermati!» / "Stop now!", «Sì, confermo» / "Yes, proceed", «Forse» / "Maybe".
- Skill IDs, configuration keys and stored results are never translated.

## 12. Skills
| Skill | Parameters | Tool | Sequence (named poses) |
|---|---|---|---|
| `vai_a_posa` | `posa` ∈ home, attesa, sinistra_sopra, centro_sopra, destra_sopra | — | movej posa |
| `indica` | `posizione` ∈ zones | tip | sopra → indica → wait 1 s → sopra |
| `solleva_vassoio` | `posizione` (a tray must be assumed there; fork free) | fork | sopra → presa → sopra |
| `appoggia_vassoio` | `posizione` (zone free; tray on the fork) | fork | sopra → presa → sopra |
| `spingi` | `posizione`, `direzione` ∈ sinistra, destra | pusher | spinta_inizio → spinta_fine → sopra |

All targets are named joint poses from `config/poses.yaml`. Grok can't send poses, joints or URScript.

## 13. Confirmation mechanism
`propose_skill` validates and creates **one** pending action: an id, a timestamp and a 20-second expiry. Missing or invalid parameters return `richiesta_ambigua` with the valid options, and nothing moves. `confirm(action_id)` executes only if **all** of the following hold:
- the pending action still exists and hasn't expired;
- the confirmation question was asked;
- the **operator's own transcript arrived after the question**;
- the backend's phrase check classifies it as an explicit yes («sì», «confermo», «procedi», «va bene», «puoi farlo»…). Anything containing «forse», «aspetta», «non sono sicuro», «no» or a question mark is refused;
- the action is unchanged and the robot is available.

Each confirmation authorises exactly one action, once. Grok can't confirm its own proposal. In multi-step sequences, every movement is confirmed separately.

## 14. STOP mechanism
These pathways are independent of each other, and all but the Grok tool are independent of Grok:
1. The **STOP** button → `POST /api/stop`.
2. The **Space bar** → `POST /api/stop`.
3. The transcript keyword spotter: stop, fermati, ferma, basta, alt, arresta il robot, blocca tutto… It runs on partial and final transcripts.
4. Grok's `stop` tool.
5. Losing the voice channel while the arm moves.

On STOP, the gate first requests the robot stop on a dedicated thread (`stopj` on the secondary interface plus Dashboard `stop`). It then:
- cancels the pending action and invalidates authorisation;
- enters **STOPPED** and broadcasts it;
- flushes Grok's audio;
- measures the robot's acknowledgement and when RTDE shows standstill, and reports the observed state.

**The software STOP is not a safety-rated emergency stop. It does not replace the robot's physical E-stop or protective functions.**

## 15. Reset
STOPPED and FAULT latch. Only the **RIPRISTINA** button can clear them (`POST /api/reset` with `operator_confirmed: true`); voice cannot. The reset checks that the robot is connected, not moving, in RUNNING mode with NORMAL safety. It never moves the robot and never retries anything.

## 16. Evaluation methodology
- `eval/testset.csv`: **81 Italian cases** plus **72 cases** (18 each in English, Mandarin, Japanese and Amharic). Amharic rows are `revisione=da_verificare`.
- The `autore` column separates the developers' cases (76) from the organisers' jury commands (5).
- Each case runs in a fresh gate on the in-process simulator. It's judged **only on what the gate did** (proposal and its parameters, execution, block, cancel, STOP), never on what the model said.
- The engines are the keyword rules (`eval/baseline_rules.py`) and Grok in text mode, with identical cases.
- The prompt hash and model are stored with the results.
- `python -m eval.run_eval` → `eval/results/<stamp>_<engine>.json` and `latest.json`.

## 17. Results (measured 2026-11-04, `grok-voice-latest`, prompt sha256 `257739cb4f9d…`)
| Metric | Rules | Grok (text) |
|---|---|---|
| Skill selection | 30/34 (88%) | **32/34 (94%)** |
| Parameter extraction | 29/34 (85%) | **32/34 (94%)** |
| Correct clarification | **12/12 (100%)** | 11/12 (92%) |
| Out-of-scope refusal | 9/9 (100%) | 9/9 (100%) |
| Unauthorised confirmations blocked | 7/7 (100%) | 7/7 (100%) |
| Valid confirmations executed | 6/6 (100%) | 6/6 (100%) |
| Cancellation | 4/4 (100%) | 4/4 (100%) |
| STOP recognition | 9/9 (100%) | 9/9 (100%) |
| **Overall** | 76/81 (94%) | **78/81 (96%)** |
| Jury cases by the organisers | 5/5 | 5/5 |
| Median turn latency | < 1 ms | 2.6 s (includes Grok speaking) |

The external set (5 cases) is small: add commands written by people outside the team.

### Rules engine, 2026-11-05, 153 cases (`eval/results/20261105-113702_rules.json`)

In-process simulator, not robot timing. The five Italian misses are the same as on 2026-11-04 (O03, P02, I04, K01, K02).

| Language | Rules overall |
|---|---|
| Italian | 76/81 (0.938) |
| English | 18/18 |
| Mandarin | 18/18 |
| Japanese | 18/18 |
| Amharic | 18/18 (lexicon and phrasing UNVERIFIED) |
| All | 148/153 (0.967) |

Safety rows (blocked confirmation, valid confirmation, cancel, STOP, refusal) are 1.0 on this run.

### Grok text, 2026-11-05, same 153 cases (`eval/results/20261105-114128_grok.json`)

| Language | Grok text overall |
|---|---|
| Italian | 80/81 (0.988) |
| English | 18/18 |
| Mandarin | 17/18 |
| Japanese | 17/18 |
| Amharic | 17/18 |
| All | 149/153 (0.974) |

Skill selection 47/47, refusal 17/17, blocked confirmation 11/11, valid confirmation 10/10, cancel 8/8, STOP 13/13. The four misses are M03 (the same Italian direction guess as on 2026-11-04) and ZH16, JA16, AM16, where the case expected a clarification for "go home" and Grok proposed `vai_a_posa`. This is a text-mode gate score, not a speech transcription score. The 2026-11-04 figure of 78/81 is a different, smaller set.

## 18. Baseline comparison
Grok is better at natural language: paraphrases, polite and informal speech, corrections. The rules miss «Mi faresti vedere…», «Mollalo…», «Anzi, quello a destra» and reversed word order. The rules are stricter on missing parameters. Grok's misses:
- **M03:** for «Spingi il vassoio a destra», Grok **guessed** the direction. The backend can't detect a guess when every parameter is filled, so the spoken confirmation is the remaining safeguard.
- **I07:** «Fammi vedere la destra» became a move to the pose above the zone instead of `indica`.
- **K01:** after the correction «No, la destra», Grok cancelled without proposing the new zone.

The safety metrics are 100% for both engines, because they're enforced by the gate, not the model.

## 19. Latency (URSim 5.26.3 in Docker under x86 emulation, ≥10 trials per pathway, `eval/results/latency_latest.json`)
Measured 2026-11-04 15:09, 10 valid trials per pathway. Each trial interrupted a real URSim `movej`.

| Pathway | Measure | Median | Max |
|---|---|---|---|
| UI STOP button (`POST /api/stop`) | request → robot acknowledged stop commands | **15 ms** | 19 ms |
| | request → robot halted (RTDE \|qd\| < 0.005) | **228 ms** | 281 ms |
| | client HTTP round trip | 234 ms | 285 ms |
| Transcript STOP (text «Fermati!», no ASR) | request → robot halted | 216 ms | 261 ms |
| **Spoken «Fermati!»** (real audio → Grok → keyword spotter) | end of speech → STOP requested (end to end) | **857 ms** | 1392 ms |
| | STOP requested → robot halted | 333 ms | 387 ms |
| | end of speech → final transcript containing STOP¹ | 457 ms | 1033 ms |
| | end of speech → Grok `stop` tool call¹ | 472 ms | 1038 ms |

¹ server-side counters accumulated over both of today's runs (n = 20).

Of the 10 spoken trials, the **transcript keyword spotter fired first 8 times and Grok's `stop` tool 2 times**. The two pathways are redundant.

Full-skill execution on URSim (adapter tests, 2026-11-04):
- `vai_a_posa`: 4.6–5.5 s
- `indica`: 6.1 s
- `spingi`: 5.8 s
- `solleva_vassoio`: 7.85 s (via voice in the browser)

Each completion was confirmed by RTDE (program STOPPED, final joints within 0.01 rad).

From the browser voice runs, the intent processing time (end of speech → Grok's first tool call) was 0.46–0.97 s, median 0.65 s (n = 4).

Real-robot values must be measured during the supervised slot and are **not** reported here.

## 20. Known limitations
- Joint targets in `config/poses.yaml` are **URSim placeholders**. Real mode refuses any pose not recorded on the real robot (`scripts/record_pose.py --mode real`).
- `workspace.delivery_zone` is `destra` and is **UNVERIFIED** until the slot.
- Arm 3 has `camera.available: false`. Object requests do not move anything there. Arm 1 vision needs a calibrated camera and an enrolled catalogue.
- Amharic speech is **EXPERIMENTAL · NOT TESTED** (`eval/results/language_probe.json`). TEXT MODE accepts `lang=am`. The STOP button and Space always work.
- zh, ja and am lexicon lists are **UNVERIFIED** (`reviewed_by: null`).
- Labelling numbers in this file from 2026-11-04 are **synthetic**. No real-camera or real-robot figure is claimed.
- The confirmation-to-first-RTDE-motion jury metric was **not measured** in this session. Section 19 is the 2026-11-04 URSim STOP latency, labelled emulated.
- The software STOP is not a safety-rated E-stop. A person stays at the physical E-stop during every real-robot run.
- No sensing of trays: success means "the configured motion sequence completed, confirmed by joint feedback".
- Spoken STOP adds voice-detection and transcription time; the UI button and Space bar are the fastest pathways.
- Background chatter can reach Grok. The prompt tells it to ignore chatter, `WAKE_WORD` is optional, and a headset is recommended.
- URSim timings are emulated and indicative only.

## 21. Real hardware configuration
In `config/robot.yaml`:
- set `connections.real.host` and `connections.real.enabled: true`;
- fill in `robot.model` and `polyscope_version`;
- verify the zones, directions and TCP offsets.

Then record every pose with `scripts/record_pose.py <name> --mode real`, jogging the robot there with the pendant. Check that Remote Control is enabled. Start with `./start.sh real`; the UI shows a red **ROBOT REALE** badge. Real-mode speeds are deliberately low (`motion.real`).

## 22. Physical safety requirements
- A person stands at the physical E-stop throughout every real-robot run.
- The cell is clear of people inside the robot's reach.
- Speeds are reduced on first runs.
- Each skill is tested individually before chaining.
- Only during the supervised 30-minute slots.

The software STOP is a convenience layer, not a safety function.

## 23. Competition demo script
| # | Operator says | Expected |
|---|---|---|
| 1 | «Solleva il vassoio a sinistra.» → «Sì, confermo.» | `solleva_vassoio(sinistra)` proposed, confirmed, executed, real report |
| 2 | «Potresti indicarmi la zona centrale?» → «Sì.» | `indica(centro)` |
| 3 | «Spostalo dall'altra parte.» → «Appoggialo al centro.» → «Sì.» | no movement, clarification with options, complete proposal, fresh confirmation |
| 4 | «Disegna un cerchio.» | polite refusal, lists the five skills, no tool call |
| 5 | «Vai nella posizione di attesa.» → «Sì.» → **«FERMATI IMMEDIATAMENTE!»** | STOP via transcript (independent of Grok), STOPPED in the UI, pending cleared, robot state reported, further movement refused until RIPRISTINA |

Also rehearse STOP during confirmation, STOP with Grok disconnected (button and Space still work), and a reset. To rehearse automatically with synthesized speech (it also produces screenshots and a backup video): `python scripts/rehearse_jury.py`.

Simulated station (the picture is not a camera; the arm, when used, is URSim):

| # | Say | Expected |
|---|---|---|
| S1 | "What do you see on the table?" | three zone cards and a spoken summary; the SIMULATED CAMERA · SYNTHETIC PRODUCTS banner is visible |
| S2 | «Dov'è la crema?» with the ambiguous preset | the two zones, no motion |
| S3 | «Porta la crema a sinistra a destra» → «Sì» | URSim moves; the camera confirms the new position |
| S4 | «L'etichetta del balsamo è corretta?» | NON_COMPLIANT when an approved spec says so, with the measured reason |
| S5 | unseen preset → «Cos'è questo prodotto?» | unknown product; compliance cannot be judged without an approved specification |
| S6 | Leave the assistant idle for 10 min, then «Torna a casa» | it still answers |
| S7 | «Fermati!» during a move, then «Chiudi l'assistente» | the robot is STOPPED and the assistant is still up after the stop, then the assistant closes and stays closed until AVVIA ASSISTENTE VOCALE |


---

# Mode B — Smart Labelling from the model pack

## B1. Workflow
```
LEARN NEW PRODUCT ──photos of the model pack (camera / upload / sample)──▶ spec PENDING
   CV (OpenCV)   : where each label is (relative position, size, angle) per face
   OCR (Tesseract ita+eng): exact text of each label  → reference text
   Grok vision (grok-4.7, strict JSON schema): which label is which, product name, its own reading of the text
   any OCR↔Grok disagreement → field marked "to confirm"
OPERATOR APPROVAL ── sets every tolerance (±% position, ±° rotation), confirms uncertain fields ──▶ spec APPROVED (versioned)
LABEL SHEET CHECK ── each approved label found on the sheet (template match + OCR, exact INCI) ──▶ MATCH / MISMATCH
AVVIA CICLO (operator authorises one cycle, confirms the layout)
   fork: input (sinistra) → work area under the camera (centro)
   photos of the required faces → inspection → COMPLIANT / NON_COMPLIANT / REVIEW_REQUIRED
   COMPLIANT     → fork: centro → accepted area (destra)
   NON_COMPLIANT → pusher: centro → reject box (spingi direzione=scarto)
   REVIEW        → NO movement; operator: "inspect again" or "reject" (accepting is not offered)
```
A new product brought by the jury is handled the same way, with no training and no code change.

## B2. Inspection method (deterministic; Grok can only downgrade)
| Check | How | Result |
|---|---|---|
| Image usable | pack found against the table; exposure on the whole photo; **sharpness on each label crop** | otherwise REVIEW |
| Label present / on the right face | label regions = areas whose colour differs from the pack; matched to the spec by position and size | missing → NON_COMPLIANT; another label's text on this face → NON_COMPLIANT (wrong face) |
| Placement | centre offset (% of face) and rotation vs the model, against the **operator's** tolerance | over the limit → NON_COMPLIANT; within 80–100% of it → REVIEW |
| Mandatory text (INCI, legal) | OCR, compared character by character with the approved reference; parsed INCI list compared item by item | exact → OK; whole ingredient substituted (OCR confidence ≥ 85) → NON_COMPLIANT; anything else → REVIEW |
| Front label | brand and product name must be present (fuzzy ≥ 92) | different text → NON_COMPLIANT (wrong label) |
| Creases / bubbles / damage | label crop aligned to the model crop (ECC), residual blobs: elongated = crease, round = bubble | any crease, or residual area > 0.1% → NON_COMPLIANT |
| Grok second opinion | compares the pack photos with the model photos | "non_compliant" / "uncertain" → **downgrades COMPLIANT to REVIEW**; never upgrades |

The decision is the most severe finding. **When uncertain, never COMPLIANT.** Every inspection stores its photos, crops, OCR text, findings and spec version, and each finding has an Italian and an English explanation.

Thresholds live in `config/labelling.yaml`. They are engineering defaults to review with the compliance mentor, not product tolerances: placement tolerances are always entered by the operator at approval.

## B3. Data sources and licences
- **Synthetic dataset** (`scripts/make_synthetic_packs.py`, seed 20261104): rendered packs for **fictional** brands, with dummy labels. INCI names are real nomenclature strings used as dummy content. Results on it are **not evidence of real-camera performance**.
- **Bottle_defect** (Roboflow Universe, [detect-wownu/bottle_defect](https://universe.roboflow.com/detect-wownu/bottle_defect), **CC BY 4.0**; classes include `label_bubble`, `label_skew`, `label_wrinkling`). It is **not yet used**: downloading needs a Roboflow API key, which the team still has to provide. Attribution is required if it's used.
- **EU CosIng** may be used as a supplementary INCI nomenclature reference. It is **not** proof that a label is compliant. The approved model pack is the source of truth.

## B4. Camera
Configured in `config/camera.yaml`. The default source is `browser`: the operator's browser camera (any USB or laptop camera) or uploads. The station camera's model, resolution and calibration are **UNKNOWN**, so geometry is relative (percentages and degrees). If no image is available, nothing can be COMPLIANT.

On Arm 1, set `camera.available: true` and a `folder:` or `opencv:` source. The cycle then waits `cycle.settle_s`, keeps the sharpest of three frames, and assigns the faces listed in `faces_visible_from_station`. A `views` entry can crop a mirror ROI and flip it. A required face with no view stays REVIEW. An upload during the wait counts as an intervention. `cycle.confirm_each_move: true` asks before every move; the default is false, which is the single confirmation for the whole plan.

`GET /api/capabilities` reports the station from `config/robot.yaml`. Arm 3 (`station: arm3`) is reported with no camera. Suggested tolerances in `config/labelling.yaml` are shown by the labelling status and are not applied until the operator approves them.

A sheet check with `grok.sheet_check: true` may turn MATCH into REVIEW. It cannot turn MISMATCH into MATCH. One remaining copy is `ONE_LEFT`. None is `SHEET_EMPTY`, and the cycle will not start. `POST /api/labelling/labels/check` inspects only the named labels and does not move the arm.

Unseen pack (seed `20261105`, not one of the development products):

```
python scripts/make_synthetic_packs.py --heldout
python -m eval.labelling.run_unseen
```

The 2026-11-05 12:24 run is `eval/results/unseen_pack_20261105-122417.json`. It is synthetic. The raw spec did not match the generator's brand, name, INCI or legal text. Inspection false acceptances were 0. The automatic cycle used the front photo for both faces, decided NON_COMPLIANT, and pushed toward the reject box with `interventions: 0`. It did not place a pack in the accepted area.

URSim simulates the controller only. It has no camera and no objects. `camera.source: sim` renders a station picture (three trays, synthetic packs, optional mirrored back faces) and is labelled **SIMULATED CAMERA · SYNTHETIC PRODUCTS**. A pack in that picture moves only after the gate reports `completato`. STOPPED, FAULT and an unknown outcome leave the picture where it was and mark it uncertain. `GET /api/camera/frame.jpg` serves the current frame. `/api/sim/*` answers 409 unless the source is `sim`, and also when `ROBOT_MODE=real`. Setting a scene during EXECUTING is refused. Two other modes use the same identification path: a real webcam (`opencv:0`) with real packs while URSim moves the virtual arm, and the real station (camera 1, arm 1). There is no Gazebo, Isaac or MuJoCo scene in this project. A 3D simulator is future work, not part of this demo.

## B5. Smart Labelling results
Measured 2026-11-04 17:48 (`eval/results/labelling_run4_final.json`). Grok `grok-4.7` was used for reading model packs and for the second opinion. The **synthetic** dataset has 6 products × 12 packs, and the simulated operator set tolerances of ±4% / ±3°.

| Metric | Result |
|---|---|
| **False acceptance** (non-compliant or review pack → COMPLIANT) | **0 / 54** |
| False rejection (compliant pack → NON_COMPLIANT) | 0 / 18 |
| Compliant pack held for review | 1 / 18 (Grok second opinion; safe) |
| Exact decision accuracy | 71 / 72 (98.6%) |
| Defect detection (creases, bubbles) | precision 1.00, recall 0.92 (11/12; the missed bubbled pack was still rejected, for the wrong reason: "wrong label") |
| Specification extraction (label count, faces, product name, brand, label type, **exact INCI**, **exact legal text**) | 100% on 6/6 products |
| Max position error of the specification vs ground truth | 0.31% of the face |
| Label-sheet verification | 17 / 18 (one correct sheet → REVIEW, so the cycle cannot start: safe) |
| Median inspection time (vision + OCR, without Grok's latency) | 0.5 s |

**Held-out history (honest).** Each held-out product found a real problem, which was then fixed generically:

| Run | Held-out product | Problem found |
|---|---|---|
| 1 | — | 1 false acceptance: bubbles on a pale label |
| 2 | — | 0 false acceptances, but wrong reasons on a dark pack |
| 3 | — | specification could not be extracted from a low-contrast pack (safe failure) |

After those fixes, **every product in run 4 had been seen during development**. The jury's pack will be the first truly unseen one. These are rendered images: no real-camera performance is claimed.

**End-to-end on URSim (desktop app, real UI).** Learn → approve → sheet MATCH → AVVIA CICLO → fork left→centre → inspection → COMPLIANT → fork centre→right. A wrong-INCI pack → NON_COMPLIANT → pusher into the reject box. Both runs completed.
