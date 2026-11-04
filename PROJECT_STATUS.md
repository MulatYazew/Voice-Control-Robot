# PROJECT STATUS — VOCE (Track 04, Voice Control)

Deadline: **Thu 5 Nov 2026, 16:00 Europe/Rome**
Labels: DONE · TESTED · PARTIALLY TESTED · NOT TESTED · BLOCKED

---

## Phase 1 — Inspection and baseline (2026-11-04)

### Repository as found
The master prompt describes a `voice-arm/` starter (`voicearm/skills.py`, `gate.py`, `arm.py`, `voice.py`, 27 tests, `eval/`). **It does not exist in this repository or anywhere under the user's home folder** (searched with `find ~ -maxdepth 5`). The owner decided (2026-11-04) to treat this repository's existing code as the starter.

Existing code at baseline (written earlier the same day):

| Path | Role | Maps to spec |
|---|---|---|
| `robot_voice/world.py` | skill catalogue, world model, plan dry-run, ambiguity detection | `voicearm/skills.py` |
| `robot_voice/controller.py` | confirmation gate, stop handling, executor | `voicearm/gate.py` |
| `robot_voice/arm.py` | `SimArm` (animated), `RealArm` (stub) | `voicearm/arm.py` |
| `robot_voice/session.py` | Grok Voice realtime relay, tool dispatch | `voicearm/voice.py` |
| `robot_voice/prompts.py` | Italian system prompt + tool schemas | — |
| `robot_voice/server.py` | aiohttp server, `/ws`, `/api/stop`, `/api/reset` | — |
| `robot_voice/mic_client.py` | native laptop-mic client | — |
| `web/` | vanilla JS UI, AudioWorklet mic capture, playback | — |
| `tests/test_safety.py` | **26 tests** | — |
| `scripts/rehearse_demo.py` | spoken end-to-end rehearsal against live Grok | — |
| `Voice_Control_Robot.ipynb` | long tutorial notebook | replaced by `notebooks/run_application.ipynb` |

Snapshot of this baseline saved outside the repo before restructuring (`baseline_snapshot.tgz` in the session scratchpad).

### Baseline tests
```
$ .venv/bin/python -m pytest -q tests
26 passed in 3.52s
```
(The spec's "27 original tests" refer to the missing starter; the 26 baseline tests above are the ones that will be preserved, ported to the new module paths.)

### Working at baseline (TESTED earlier on 2026-11-04 against live Grok)
- Grok Voice realtime: `wss://api.x.ai/v1/realtime?model=grok-voice-latest` (server reports `grok-voice-3.0`), Bearer auth from backend, `session.update`, PCM16 24 kHz in/out, `server_vad`, `input_audio_transcription.language_hint`.
- Events observed live: `session.created`, `session.updated`, `conversation.created`, `ping`, `input_audio_buffer.speech_started/speech_stopped/committed`, `conversation.item.input_audio_transcription.completed` (≈0.7 s after speech end), `response.created/done`, `response.output_audio.delta`, `response.output_audio_transcript.delta/done`, `response.output_item.added/done`, `response.function_call_arguments.delta/done`, `conversation.item.added`.
- Function calls: flat `{type:"function", name, description, parameters}` tools; output via `conversation.item.create` + `function_call_output` + `response.create`.
- Spoken rehearsal (macOS `say` audio streamed in real time): ambiguity → clarification → confirmation → execution → report → spoken "Ferma!" halted the sim arm (≈2.4 s from start of the word).

### Broken / mismatched with the authoritative hardware facts
- Skill names/parameters were invented for a generic gripper arm (`lift_tray(tray=red)`, slots A/B/C). The real Arm 3 is a **UR arm with a printed tool (fork, pusher, indicating tip), no gripper, no camera**, zones `sinistra/centro/destra`. → must be redone.
- One confirmation per multi-step plan; spec requires one confirmation **per movement**.
- No STOPPED latch requiring operator reset; no FAULT state; plan TTL 45 s (spec: 20 s).
- Confirmation accepted "neutral" phrases only partially; no uncertainty words ("forse", "non sono sicuro").
- No URSim adapter; `RealArm` is a stub.
- aiohttp server on :8080; spec requires FastAPI on :8000 with `/api/status`, `/api/text-command`, `/ws/voice`, `/ws/events`.
- No text mode without Grok, no eval harness, no metrics.

### Docs verified
- xAI: realtime guide (`docs.x.ai/docs/guides/voice/agent`) + live probing. The two URLs in the spec (`/developers/model-capabilities/audio/speech-to-speech.md`, `/cookbook/examples/voice-agent-web.md`) return **HTTP 404** as of 2026-11-04.
- UR client interfaces (official overview): Primary 30001, Secondary 30002 (accept URScript, 10 Hz state), Real-time 30003, Dashboard 29999, RTDE 30004, Interpreter 30020.
- UR Dashboard Server e-Series (manual SW 5.25): `stop`, `play`, `pause`, `power on`, `brake release`, `unlock protective stop` require **Remote Control**; `robotmode`, `safetystatus`, `programState`, `running`, `is in remote control` do not.
- RTDE guide: big-endian header `uint16 size, uint8 type`; types V/v/M/U/O/I/S/P; `actual_q`, `actual_qd` (VECTOR6D), `robot_mode`, `safety_status` (INT32), `runtime_state` (UINT32: 0 STOPPING, 1 STOPPED, 2 RUNNING, 3 PAUSING, 4 PAUSED, 5 RESUMING, 6 RETRACTING).

### Environment
- macOS arm64, Python 3.14.2 venv.
- Node: **not installed** at baseline → installed via Homebrew (v26.13.0, npm 11.20.0).
- Docker: **not installed** at baseline → Docker Desktop installed via Homebrew; daemon needs first launch / terms acceptance by the owner.
- `ur_rtde`: only a source tarball exists for Python 3.14 (needs Boost/CMake build). Decision: implement a minimal RTDE reader + Dashboard client in pure Python, following the official protocol docs above.

### Hardware assumptions (UNVERIFIED — to confirm during the supervised slot)
| Item | Value used | Status |
|---|---|---|
| Robot model | UNKNOWN (UR, "Arm 3") | UNVERIFIED |
| PolyScope version | UNKNOWN | UNVERIFIED |
| Zones | sinistra, centro, destra | UNVERIFIED |
| Push directions | sinistra, destra | UNVERIFIED |
| Tool | single printed tool with fork / pusher / indicating tip, TCP offset per feature | UNVERIFIED |
| Initial tray positions | configurable in `config/robot.yaml` | ASSUMED, never sensed |
| Joint targets | URSim-only placeholders until recorded with `scripts/record_pose.py` | NOT VALID FOR REAL ROBOT |

### Integration risks
1. URSim image is x86-64; on Apple Silicon it runs under emulation (slower; to be measured).
2. e-Series Remote Control mode may be required to send URScript / dashboard `stop`.
3. Laptop mic in a noisy hall: background chatter reached Grok in a test → prompt rule + optional wake word added; a headset is strongly recommended.
4. No camera: tray presence is an assumption; reports must say "completed per configured motion sequence".

---

## Phase 2 — Configuration and safety (2026-11-04) — DONE · TESTED
Files created: `config/robot.yaml`, `config/skills.yaml`, `config/poses.yaml`, `backend/app/config.py`,
`backend/app/skills/{registry,world}.py`, `backend/app/safety/{phrases,gate}.py`, `backend/app/robot/{base,sim}.py`,
`backend/app/monitoring/events.py`, `pyproject.toml`, `tests/{conftest,test_phrases,test_registry,test_gate,test_robot}.py`.
- Registry from YAML; missing/invalid parameter → `richiesta_ambigua` + valid options; preconditions → `precondizione_non_soddisfatta`; never auto-fills.
- Gate states IDLE / PENDING_CONFIRMATION / EXECUTING / STOPPED / FAULT, every transition logged (logs/voce.jsonl).
- Confirmation: pending exists, not expired (20 s), question asked, operator transcript **after** the question, explicit yes (backend phrase check), same action id, one-time (action and utterance), robot available.
- STOP latch until operator reset (`POST /api/reset` with `operator_confirmed: true`); voice cannot reset; reset verifies robot state.
- FAULT on unknown outcome (timeout, comms loss, final pose ≠ target); never retried.
- Bugs found by tests and fixed: known pose not updated after completion; sim final state reported `moving: True`.

## Phase 3 — Grok integration (2026-11-04) — DONE · TESTED (live)
Files: `backend/app/voice/{grok_session.py,prompt_it.md}`, `backend/app/conversation/{tools,rules,text_mode}.py`, `backend/app/main.py`, `backend/app/launcher.py`.
- Tools given to Grok: `propose_skill, confirm, cancel, stop, status` (+ P2 `propose_sequence, save_pose`). No joint/URScript/network tool.
- Operator transcripts (`conversation.item.input_audio_transcription.completed`, partial `.updated`) go to the gate for STOP spotting + confirmation validation.
- Reconnection: Grok drop → pending cleared, fresh session with back-off (max 5), never restores a pending action. Browser drop while moving → STOP (`safety.stop_on_voice_loss: true`, deliberate fail-safe choice).
- TEXT MODE — NOT VOICE: deterministic rules engine, works without Grok; also "Grok (text)" engine for evaluation.
- Live Grok text-mode run of the 5 jury commands: all handled; found and fixed (a) Grok refusing to call `confirm` for "Procedi" → prompt now says the backend decides, (b) report wording implying sensors, (c) duplicate STOP overwriting the first pathway.

## Phase 4 — Frontend (2026-11-04) — DONE · TESTED (headless Chromium)
React 18 + TypeScript + Vite in `frontend/`, built to `frontend/dist`, served by FastAPI on `http://localhost:8000`.
- AVVIA ASSISTENTE VOCALE (user gesture) → mic permission → AudioWorklet → `/ws/voice`; level meter; pause / close mic; playback with barge-in flush; reconnect.
- Header badges (SIMULAZIONE · URSim / red ROBOT REALE), Grok + robot status, 11 UI states, pending card + 20 s countdown, execution progress (per-movement events), schematic top view, assumed tray state, action history, latency table, STOP bar (Space), RIPRISTINA, TEXT MODE banner, Valutazione tab.
- `npm run build`: typecheck clean.
- Browser E2E (Chromium fake mic = synthesized Italian WAV → real Grok → URSim): see Phase 6.

## Phase 5 — URSim (2026-11-04) — DONE · TESTED
- `scripts/ursim.sh` (official `universalrobots/ursim_e-series`, amd64 under emulation on Apple Silicon). PolyScope **5.26.3**.
- **Root-cause found:** on this Mac, UR's own *URSim.app* (Lima, launched by the owner earlier) holds `127.0.0.1:30001-30004`. RTDE/URScript to 127.0.0.1 silently went to that second, powered-off simulator (RTDE robot_mode 3 while Docker URSim was RUNNING). Fix: `connections.ursim.host: "::1"` (Docker also binds IPv6), overridable with `URSIM_HOST`. URSim.app was not touched.
- URSim setup done through its GUI (VNC): confirmed default safety configuration, **enabled Remote Control** (Local Control rejects external URScript — verified), switched Simulation ON, Remote mode. These settings live in the container; re-creating it requires redoing them (see README).
- Verified with feedback: `vai_a_posa`, `indica`, `spingi`, `solleva_vassoio`, `appoggia_vassoio` complete (RTDE `runtime_state` + final joints within 0.01 rad); STOP mid-motion → ack ≈ 17 ms, halt (|qd|<0.005) ≈ 200–370 ms.
- `scripts/record_pose.py` (list / dry-run tested; refuses to record while moving — tested).
- Poses in `config/poses.yaml` are still **placeholders** (formula-generated) → NOT valid for the real robot.

## Phase 6 — End-to-end (2026-11-04) — PARTIALLY TESTED
- Browser → Grok Voice → gate → URSim with spoken (synthesized) Italian: T1 lift left (confirm, executed 7.85 s, report), T2 "Potresti indicarmi la zona centrale?" (confirmed with "Procedi"), T5 "Fermati immediatamente" during motion → STOPPED via transcript path (ack 26 ms), Grok `stop` tool also fired. No browser console errors.
- **Full five-test jury rehearsal** (`scripts/rehearse_jury.py`, browser + Grok Voice + URSim, synthesized speech), final run 15:20:
  - T1 «Solleva il vassoio a sinistra» → `solleva_vassoio(sinistra)` → «Sì, confermo» → completed 6.1–8.1 s → spoken report. TESTED
  - T2 «Potresti indicarmi la zona centrale?» → `indica(centro)` → «Sì» → completed 6.6 s. TESTED
  - T3 «Spostalo dall'altra parte» → no proposal, clarification → «Appoggialo al centro» → `appoggia_vassoio(centro)` → fresh «Sì, procedi» → completed 3.6 s. TESTED
  - T4 «Disegna un cerchio» → refusal + list of the five skills, no tool call. TESTED
  - T5 «Torna a casa» → «Sì» → «Fermati immediatamente!» **during motion** → STOPPED via transcript pathway (robot ack 22.7 ms; Grok `stop` tool 18.8 ms later as backup), report «fermo, posa non nominata», then «Vai nella posizione di attesa» refused until RIPRISTINA. TESTED
  - Also observed: STOP during confirmation («Sì. Fermati…» in one turn → STOPPED, nothing authorised). TESTED
  - Found and fixed during rehearsal: «home» transcribed as «ON» → pose names/aliases added to transcription keyterms; report claiming «in movimento» right after halt → waits for observed standstill; duplicate STOP report removed.
  - Backup screen recording (video, no audio) + screenshots: `recordings/` (git-ignored). A recording WITH audio and a human voice still has to be made by the team.
- NOT TESTED: a human voice through a real laptop microphone in the venue (needs the owner); real Arm 3 (Phase 8).

## Phase 7 — Evaluation (2026-11-04) — DONE · TESTED
- `eval/testset.csv`: 81 cases (13 categories). Field `autore`: 5 cases authored by the organisers (the jury script in the brief), 76 by the developers. **No commands from other people collected yet** — add them before the jury.
- `eval/run_eval.py` (rules vs Grok text mode, identical cases, judged on gate behaviour only), `eval/baseline_rules.py`, `eval/latency.py`.
- Results: see `eval/results/latest.json` and README §17–19.

### Test inventory (executed 2026-11-04)
```
$ .venv/bin/python -m pytest -q tests
142 passed
tests/test_phrases.py 43 · test_registry.py 16 · test_gate.py 19 · test_robot.py 8 · test_conversation.py 21
tests/test_api.py 9 · test_baseline_ported.py 26 (the 26 baseline tests, ported by intent)
```
No test needs xAI or URSim. Live checks (Grok, URSim, browser) are scripts, not unit tests.

### Known issues / limitations
- Grok guessed a push direction once (M03 "Spingi il vassoio a destra" → direzione destra) instead of asking: the backend cannot detect a guess when all parameters are filled; the spoken confirmation is the safeguard.
- Grok read "Fammi vedere la destra" as `vai_a_posa destra_sopra` (I07) and handled a bare correction "No, la destra" by cancelling without re-proposing (K01).
- STOP via speech depends on transcription latency (≈0.5 s after speech end) + VAD; the UI button / Space are faster.
- URSim runs under x86 emulation: timings are indicative, not real-robot values.
- Laptop-speaker echo can reach the mic; browser echo cancellation is on, a headset is recommended.

---

## Phase 8 — Real hardware — BLOCKED (needs the supervised slot)
To do during the slot: verify model + PolyScope, set `connections.real.host` / `enabled`, enable Remote Control, record every pose with `scripts/record_pose.py <name> --mode real`, measure TCP offsets, test each skill at `motion.real` speeds, test software STOP, confirm a person at the physical E-stop. Nothing about the real robot is claimed until then.

## Phase 9 — Stretch goals — PARTIALLY TESTED
| Goal | Status |
|---|---|
| Multi-skill sequences, one confirmation **per movement** | DONE · unit-TESTED (`test_sequence_needs_confirmation_per_movement`); not yet rehearsed by voice |
| Voice-recorded named poses (`save_pose`, current joints only, never overwrites configured poses) | DONE · unit-TESTED; not yet tried by voice |
| Additional languages | Prompt answers in the operator's language; Grok replied in English in a text-mode test (earlier baseline). PARTIALLY TESTED |

## Final acceptance (2026-11-04 15:25)
| Requirement | Status | Evidence |
|---|---|---|
| Microphone | PARTIALLY TESTED | Browser AudioWorklet capture tested with Chromium fake mic (WAV); laptop mic captured real room audio with the native client earlier today. Real human voice in the browser: owner to test |
| Grok real-time Italian | TESTED | browser rehearsal + 10 spoken STOP trials |
| Five skills selectable | TESTED | eval (Grok 32/34 skill selection), URSim execution of all five |
| Parameters validated | TESTED | unit tests + eval |
| Ambiguity → clarification | TESTED | eval 11/12 (Grok), 12/12 (rules); jury T3 |
| Confirmation before each movement | TESTED | gate tests; eval 7/7 unauthorised blocked |
| No arbitrary robot commands | TESTED | tool list; X08 URScript request refused; registry rejects joints/pose names |
| Independent STOP | TESTED | UI ×10, transcript ×10, spoken ×10 on URSim |
| STOP during motion with feedback | TESTED | RTDE halt median 228 ms (UI) |
| Operator reset required | TESTED | API + gate tests, jury T5 |
| URSim end to end | TESTED | jury rehearsal |
| Success only after actual result | TESTED | RTDE completion + final-joint check; FAULT on unknown |
| Text mode distinguished | TESTED | banner + label on every response |
| Notebook two cells | TESTED | executed incl. idempotency |
| Tests | TESTED | 142 passed (26 baseline ported + 116 new) |
| Evaluation | TESTED | 81 cases, rules vs Grok, latency file |
| Documentation | DONE | README, this file |
| Real Arm 3 | BLOCKED | supervised slot |

### Next steps (before Thu 5 Nov 16:00)
1. Owner: try the browser UI with your own voice and a headset; record a backup video **with audio**.
2. Collect 10–20 commands from people outside the team into `eval/testset.csv` (`autore=esterni`) and rerun `python -m eval.run_eval`.
3. Hardware slot: Phase 8 checklist above.
