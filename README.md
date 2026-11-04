# VOCE — Italian voice control for a robotic arm

*Parla naturalmente. Il robot capisce. Tu confermi.*

Grok Hackathon · Track 04 (Voice Control). An operator speaks Italian into the laptop microphone. **Grok Voice** (realtime) understands the request and proposes one of five predefined skills of the Universal Robots "Arm 3". The backend validates the proposal and asks for spoken confirmation. Only then does it execute the skill and report the actual result. **STOP** is available at any moment and doesn't depend on Grok.

> **Grok proposes. The backend decides. The robot executes only validated and confirmed skills.**

---

## 1. Objective
Let people who don't program robots call up ready-made operations by voice, safely: every movement needs explicit confirmation, and a stop is always available. Free trajectories and unconfirmed movements are out of scope by design.

## 2. Architecture

```
 Browser (React)                      FastAPI backend (Python)                               xAI
 ─────────────────                    ──────────────────────────────────────────────          ─────────────
 mic → AudioWorklet ── PCM16 24k ──▶  /ws/voice ── voice/grok_session.py ──── wss ─────▶  Grok Voice
 speaker ◀── Grok audio ◀───────────                │  (key stays here)   ◀── tool calls ──  (server VAD,
 UI ◀── state/transcripts ── /ws/events             ▼                     ◀── transcripts ─  transcription)
 STOP button / Space ── POST /api/stop ──▶  safety/gate.py   ◀── conversation/tools.py (5+2 tools only)
 RIPRISTINA ── POST /api/reset ─────────▶   │  deterministic: validation, confirmation, STOP latch, FAULT
 TEXT MODE ── POST /api/text-command ───▶   │  skills/registry.py  ← config/skills.yaml + poses.yaml
                                            ▼
                                    robot/base.py RobotAdapter
                                    ├─ robot/sim.py  SimAdapter (in-process)
                                    └─ robot/ur.py   URAdapter: RTDE :30004 (feedback) · Secondary :30002 (fixed
                                                     URScript per skill) · Dashboard :29999 (stop / state)
```

| Path | Role |
|---|---|
| `backend/app/safety/gate.py` | State machine IDLE → PENDING_CONFIRMATION → EXECUTING → IDLE · STOPPED · FAULT |
| `backend/app/safety/phrases.py` | Deterministic STOP / confirmation / cancel phrase checks on the operator's transcript |
| `backend/app/skills/registry.py` | Skill registry, parameter validation, preconditions, fixed motion sequences |
| `backend/app/voice/prompt_it.md` | The Italian system prompt (single file) |
| `backend/app/voice/grok_session.py` | Grok realtime relay, tool dispatch, reconnection, metrics |
| `backend/app/robot/ur.py` | URSim / real UR adapter (pure Python RTDE + Dashboard + Secondary) |
| `backend/app/launcher.py` | `start()` / `stop()` used by the notebook and `start.sh` |
| `frontend/` | VOCE industrial UI (React + TypeScript + Vite) |
| `eval/` | 81-case test set, rules baseline, Grok-vs-rules evaluation, STOP latency trials |

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
Or from Jupyter, with `notebooks/run_application.ipynb` (two cells: `start(mode="ursim")` / `handle.stop()`). `start()` is idempotent.

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

## 11. Text simulation mode
Switch on **Modalità testo** in the UI, or `POST /api/text-command {"text": "...", "engine": "rules"|"grok"}`. It's labelled **TEXT MODE — NOT VOICE** everywhere. The `rules` engine works without Grok. It's for development and evaluation only and is never presented as voice operation.

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
- `eval/testset.csv`: **81 Italian cases** in 13 categories (clear, word order, polite, informal, corrections, missing parameters, ambiguous, out of scope, valid/invalid confirmation, cancellation, STOP, jury demo).
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
