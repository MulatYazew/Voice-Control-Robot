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
| Tests | TESTED | 185 passed (26 baseline ported + 159 new) |
| Evaluation | TESTED | 81 cases, rules vs Grok, latency file |
| Documentation | DONE | README, this file |
| Real Arm 3 | BLOCKED | supervised slot |

### Next steps (before Thu 5 Nov 16:00)
1. Owner: try the browser UI with your own voice and a headset; record a backup video **with audio**.
2. Collect 10–20 commands from people outside the team into `eval/testset.csv` (`autore=esterni`) and rerun `python -m eval.run_eval`.
3. Hardware slot: Phase 8 checklist above.

---

## Phase 10 — Mode B: Smart Labelling + bilingual platform (2026-11-04 evening) — DONE · TESTED (synthetic + URSim)
Request: add labelling from the model pack (second track brief) + Italian/English everywhere (master prompt v3).

Created: `backend/app/labelling/{vision,ocr,grok_vision,specification,model_reader,label_verification,compliance,service,sorting}.py`,
`backend/app/api/labelling.py`, `config/{labelling,camera}.yaml`, `scripts/make_synthetic_packs.py`, `eval/labelling/run_eval.py`,
`frontend/src/{i18n/strings.ts,services/labelling.ts,labelling/Labelling.tsx}`, `tests/{test_labelling,test_bilingual}.py`.
Modified: gate (cycle authorisation: `authorize_cycle` / `run_cycle_step` / STOP ends it), world (push `scarto` = into reject box),
robot.yaml + poses.yaml (direction `scarto`, URSim placeholder poses), phrases + rules (English), skills.yaml (English wording),
text mode (bilingual), Grok session (`?lang=it|en|auto`, English reports), prompt (bilingual + labelling tools), UI (6 sections, language selector).

Environment added: Tesseract 5.5.4 (ita+eng, Homebrew), OpenCV 5.0, pytesseract, rapidfuzz, python-multipart.
Grok vision verified live: `grok-4.7` (image+text in, strict `json_schema` out), ≈6–50 s per call depending on load.

### What was verified
- Unit/integration tests: **185 passed** (`pytest -q tests`), incl. 18 labelling (decisions, approval blockers, Grok downgrade-only,
  sheet MATCH/MISMATCH, cycle accept / reject / review-hold / STOP abort / out-of-cycle move refused) and 25 bilingual.
- **URSim desktop app, through the real UI (headless Chromium):** learn held-out product → approve → sheet MATCH → AVVIA CICLO →
  fork moved pack left→centre (9.6 s) → photos → COMPLIANT → fork centre→right. Then a wrong-INCI pack → NON_COMPLIANT →
  `spingi centro scarto` executed on URSim. TESTED.
- English through Grok text mode: "Lift the tray on the left." → `solleva_vassoio{posizione: sinistra}` proposed, English reply. TESTED.

### Honest findings from held-out products (each found a real problem; each was then fixed generically)
| Run | Held-out product | Finding | Fix |
|---|---|---|---|
| 1 | nebbia_tonico_rosa | **1 FALSE ACCEPTANCE**: bubbles on a pale label below the residual threshold (45) | threshold 30, validated: 0/40 undamaged labels with any residual |
| 1 | (dev) | creased label accepted (area threshold 0.4% too high for thin creases) | any crease-shaped residual or > 0.1% → NON_COMPLIANT |
| 2 | ambra_balsamo_labbra | 0 false acceptances, but black pack flagged "bad exposure"; blurred photo called "missing label" | exposure judged on the whole photo; blurred unmatched region → REVIEW |
| 3 | salvia_shampoo_solido | **spec could not be extracted** (sage pack on grey-green table: pack/background contrast too low) — safe failure, nothing approved | multi-threshold pack segmentation; low dynamic range no longer = bad exposure |
After each fix the product joined the dev set; results of run 4 below are therefore all on products seen during development.
**No truly unseen-product result exists after the last fix** — the jury's pack will be the first.

### Not done / not verified
- No real camera photos, no real packs (Cristina Portinaro), no compliance-mentor review of thresholds. BLOCKED on hardware/station access.
- Bottle_defect dataset not downloaded (needs Roboflow API key). CosIng not integrated.
- Station camera model/resolution/calibration UNKNOWN; reject-box position UNKNOWN (placeholder `scarto` poses).
- Stretch goals (check after each label, sheet run-out detection, learning from video): NOT DONE.

### Run 4 (final, 2026-11-04 17:48, with Grok) — TESTED on synthetic data
False acceptance 0/54 · false rejection 0/18 · 1/18 compliant held for review (Grok) · exact 71/72 · defects P 1.00 R 0.92 ·
spec extraction 100 % (6/6, exact INCI + legal) · sheets 17/18 (one REVIEW, safe). All 6 products were seen during development.
Known residual issues: bubbles can corrupt OCR of the front label (rejected as "wrong label" instead of "bubble");
Grok second opinion occasionally flags a correct pack (→ REVIEW, throughput cost).

### Test count at the start of 2026-11-05
`pytest -q tests` → **185 passed** before this session's work packages. After WP-2 through WP-8: **275 passed** (see the section below).

## WP-1 — Multilingual deterministic phrases (2026-11-05) — DONE · TESTED

`config/lexicon.yaml` holds stop / affirm / negative / uncertain / cancel / zones / directions for it, en, zh, ja, am.
`phrases.normalize` uses NFKC, strips combining marks only on Latin letters, and keeps Han, Hiragana, Katakana and Ethiopic.
Latin and Ethiopic match as tokens; Han and Kana match as substrings. Verdict order is unchanged: STOP → NEGATIVE → UNCERTAIN → AFFIRM → ABSENT.

Evidence:
```
$ .venv/bin/python -I -c "from app.safety.phrases import is_stop; print(is_stop('停下！'), is_stop('止まって'), is_stop('አቁም'))"
True True True
$ .venv/bin/python -m pytest -q tests/test_phrases.py tests/test_multilingual_phrases.py tests/test_registry.py tests/test_bilingual.py tests/test_conversation.py tests/test_baseline_ported.py tests/test_gate.py
226 passed
```
`reviewed_by` is null for zh, ja and am. Those lists are used and are **UNVERIFIED** until a native speaker fills `reviewed_by` in `config/lexicon.yaml`. Amharic romanisations (`aqum`, `awo`, `ay`, `minalbat`, …) are fallbacks for an ASR that does not emit Ethiopic.

### Next steps for Smart Labelling (before the jury)
1. Station slot: photograph real model packs + defective examples with camera 1; re-run `eval/labelling/run_eval.py`-style checks on them; re-tune `config/labelling.yaml` thresholds with the Cristina Portinaro mentor.
2. Record real poses incl. the reject-box push (`*_spinta_scarto_*`) with `scripts/record_pose.py`.
3. Provide a Roboflow key to import Bottle_defect for defect-recall testing on real photos.

## WP-2 — Five voice languages (2026-11-05) — DONE · TESTED for text, NOT TESTED for Amharic speech

`/ws/voice` accepts `auto|it|en|zh|ja|am`. `language_hint` is sent only for it, en, zh, ja. Amharic and auto omit the hint. Confirmation sentences for zh, ja and am live in `config/i18n/messages.yaml`. Italian and English strings in `config/skills.yaml` were not rewritten.

Evidence: `tests/test_voice_langs.py` (zh websocket accepted with a fake session, am session has no `language_hint`, Chinese lift / 不要 / 是的, Japanese はい confirms an English proposal, Chinese 是的 starts a cycle through `cycle_starter`, 也许 does not, 止まって latches STOPPED).

Amharic voice probe:
```
$ .venv/bin/python scripts/probe_language.py
EXPERIMENTAL · NOT TESTED
```
Log: `eval/results/language_probe.json`. No `recordings/am_*.wav`. The UI says so when አማርኛ is selected. TEXT MODE with `lang=am` is the fallback. Web Speech `am-ET` was not wired.

## WP-3 — Station camera (2026-11-05) — DONE · TESTED in process, live camera NOT TESTED

`camera.available` is false (Arm 3). `GET /api/camera/scene` on the running sim returned `available: false`. `POST /api/camera/frame` returns `nessuna_telecamera`. Sources `opencv:N`, `folder:` and browser push are implemented in `backend/app/perception/camera.py`. Zone polygons and staleness are covered by `tests/test_perception.py`. A live OpenCV device was not opened. The click-four-corners page is the `POST /api/camera/zones` endpoint; the panel shows the frame and candidates when a camera is available and does not itself collect the four clicks.

## WP-4 — Object identification (2026-11-05) — DONE · TESTED on synthetic decisions, live scene NOT TESTED

`locate_object` and `sposta_vassoio` are in the registry. The decision rule (one zone, ambiguous, not found, disagreement → incerto, uncalibrated, no camera, off-tray) is in `tests/test_perception.py`, including a drawn red blob. `scripts/make_synthetic_scenes.py` wrote `data/synthetic_scenes/` (3 png). Grok vision is not called between confirmation and motion. The async "while IDLE" Grok vision cache is **NOT DONE**; a query uses the deterministic colour/OCR result. `perception.detector` stays `none`.

With the camera off, "bring me the red cup" does not propose a movement. That is what the rules eval records as `answer`.

## WP-5 — Catalogue by enrolment (2026-11-05) — DONE · TESTED on synthetic images

`tests/test_catalog.py` enrols three textured images, matches a rotated/scaled view of each by keyword plus ORB, and rejects a blank unknown. Thresholds in `config/perception.yaml` are engineering defaults. No company-sample photos were enrolled. `scripts/enroll_products.py <folder>` is the batch path for the slot.

## WP-6 — Voice-started labelling cycle (2026-11-05) — DONE · TESTED with a fake starter

`propose_labelling_cycle` creates a pending action of kind `cycle`. The same `confirm()` path starts `SortingCycle` only after a valid confirmation. `tests/test_voice_langs.py::test_cycle_confirm_in_zh_and_stop` covers Chinese yes, uncertain, and STOP. REVIEW still has no voice accept (existing `tests/test_labelling.py`). A full cycle on URSim was not run in this session.

## WP-7 — Evaluation (2026-11-05) — rules DONE · TESTED, Grok text see the file, URSim jury latency NOT TESTED

`eval/testset.csv` is 153 rows: 81 Italian plus 18 each of en, zh, ja, am. Amharic rows have `revisione=da_verificare`.

Rules, in-process simulator, `eval/results/20261105-113702_rules.json` and `eval/results/latest_multilingual.json`:

| Language | Overall |
|---|---|
| it | 76/81 (0.938), same five misses as 2026-11-04: O03, P02, I04, K01, K02 |
| en | 18/18 |
| zh | 18/18 |
| ja | 18/18 |
| am | 18/18 |
| all | 148/153 (0.967) |

Safety rows on that run (blocked confirmation, valid confirmation, cancel, STOP, refusal) are 1.0.

Grok text, `eval/results/20261105-114128_grok.json`, finished 2026-11-05 11:45: **149/153 (0.974)**. By language: it 80/81, en 18/18, zh 17/18, ja 17/18, am 17/18. Safety rows are 1.0 (skill selection 47/47, refusal 17/17, blocked confirmation 11/11, valid confirmation 10/10, cancel 8/8, STOP 13/13). Misses: M03 (same Italian direction guess as 2026-11-04) and ZH16, JA16, AM16, where the case expected a clarification and Grok proposed `vai_a_posa`. This is text mode, not speech.

The jury metric "end of confirmation → first RTDE motion" was **NOT TESTED**. `eval/results/latency_latest.json` is the 2026-11-04 15:09 URSim STOP latency (emulated). It is not today's confirm-to-motion figure.

## WP-8 — Jury UI (2026-11-05) — DONE · TESTED in the browser against `./start` sim

`npm run build` (`tsc --noEmit && vite build`) succeeded. Chrome at `http://127.0.0.1:8000` showed the pipeline, the station badge `ARM 3 · senza telecamera`, `SIMULAZIONE · interna`, the voice selector (UI stayed Italiano), the STOP bar `STOP · FERMA · 停止 · 止まれ · አቁም`, and the perception card stating there is no camera.

A Mandarin text command `请把左边的托盘抬起来` with the voice language set to 中文 reached `PENDING_CONFIRMATION` for `solleva_vassoio` / `sinistra`, and the card showed `我用货叉抬起左边区域的托盘。确认吗？`. `是的` returned `ok: true` and the gate entered `EXECUTING` (program PLAYING on the in-process simulator). The Ethiopic glyphs on the STOP bar depend on the OS font; the characters in the source are `አቁም`.

## WP-9 — NOT STARTED in the morning session

P2 items (check after each label, sheet run-out, YOLO, Bottle_defect) were not started in the morning. YOLO stays off. No Roboflow key was available. The afternoon continuation below adds the label check and the sheet count. Video learning and Bottle_defect stay NOT DONE.

## Phase 11 — continuation 2026-11-05 12:15

The merged brief said the suite was 190 passed and that `tests/test_voice_session.py` already covered a 12:00 voice-session fix. This tree has no `tests/test_voice_session.py`. `backend/app/voice/grok_session.py` was not edited in this continuation. The suite after the work below is the measured one.

`./start.sh sim`, `GET /api/status` and the Italian text command were not repeated in this continuation. The browser check from the morning is under WP-8.

### WP-0 — Capabilities and task record — DONE · TESTED
`GET /api/capabilities` is built from config and live status. With `station: arm3` the camera is reported unavailable and the gripper is false. `TaskRequest` rejects unknown fields. Tool calls are appended to `logs/voce.jsonl` (gitignored).

### WP-L1 — Cycle without an upload — DONE · TESTED with a fake camera
`SortingCycle` captures from a non-browser station camera: settle, sharpest of three frames, optional mirror crop. A required face that no view covers becomes REVIEW and is not accepted. A missing frame blocks the start. A frame that disappears after the preflight becomes REVIEW. STOP during the settle aborts the cycle. `confirm_each_move` defaults to false; when true the cycle waits before each move. A manual `add_photo` increments `interventions`. The existing upload tests still pass.

### WP-L2 — Sheet opinion — DONE · TESTED with a fake Grok
`grok.sheet_check` defaults to false. When it is on, a MATCH can become REVIEW_REQUIRED and a MISMATCH stays a MISMATCH. A failure keeps the deterministic decision and adds "AI check not available". INCI text is not rewritten.

### WP-L3 — Unseen pack — DONE · TESTED on a rendered pack, NOT a real pack
Seed `20261105` is in `scripts/make_synthetic_packs.py`. The renderer draws front and back only. It does not draw a cap or a bottom.

`eval/results/unseen_pack_20261105-122417.json` (rerun after the matching sheet was selected again; the earlier `unseen_pack_20261105-122329.json` stopped before the cycle because the last sheet stored was the missing-back sheet):

- Raw spec vs the generator: label count and faces matched, position error 0.29%. Product name, brand, kind, INCI and legal text did **not** match.
- Inspection, no Grok: false acceptances 0, false rejections 0, one REVIEW (the blurred pack). Every other generated defect was NON_COMPLIANT.
- Sheets: correct sheet MATCH with `ONE_LEFT`; wrong sheet and missing-back sheet MISMATCH with `SHEET_EMPTY`.
- Cycle: started, `interventions: 0`, decision NON_COMPLIANT, rejected 1, accepted 0. The camera replayed `correct_1_front.jpg` for both faces, so this is not a completed accept of a good pack.

No threshold was changed after this run.

### WP-L4 — PARTIAL
- Check one label: `LabellingService.inspect_labels` and `POST /api/labelling/labels/check`. A defective front sets `LABEL_DEFECTIVE` and the advice not to apply the back label. No motion. Tested with the inspector replaced by a fake.
- Sheet run-out: `remaining` and `sheet_state`. `SHEET_EMPTY` refuses the cycle. Tested.
- Learning a cycle from video: NOT DONE.
- CosIng and Bottle_defect: NOT DONE. No inventory file and no Roboflow key.

### Tests this continuation actually ran
```
$ .venv/bin/python -m pytest -q tests
296 passed, 1 warning in 71.55s
```
The warning is Starlette's httpx deprecation notice. Frontend `npm run build` was not re-run: no frontend file changed.

## What this session did not measure
- Amharic, Mandarin or Japanese speech through `/ws/voice`.
- A real camera, calibrated zones, or a company-sample catalogue.
- URSim or the real arm, including the jury confirm-to-motion latency.
- Native-speaker review of the zh, ja and am lexicon.
- The unseen-pack cycle with a real back-face view. The 12:24 run reused the front photo for both faces.

## Part A — voice session stays up until the operator closes it (2026-11-05)

Baseline before this step: **296 passed**. After `tests/test_voice_session.py` plus the phrase and voice-language files: **142 passed** in that slice. The full suite is recorded again after Part B.

| Cause | Fix | Test |
|---|---|---|
| Grok gave up after 5 reconnects | No attempt cap. Back-off 1, 2, 4, 8, then 15 s. The counter resets after a connect. | `test_ten_drops_and_seven_failed_connects_still_reconnect` |
| A half-open `/ws/voice` refused the next browser | The new socket closes the old one with code 4000 and takes the slot. Only the owner clears it. | `test_new_browser_replaces_the_old_socket` |
| No application heartbeat; sleep and a lost mic looked connected | Browser ping every 5 s, backend `pong` even while Grok is down. 15 s of silence reconnects. `AudioContext` resumes. A dead mic track is reopened after 500 ms. Wake Lock is requested when the browser has it. | `test_ping_keeps_a_quiet_session` |
| Spoken "stop" must not close the assistant | `is_terminate` matches only phrases that name the assistant or the session. STOP still stops the robot and the session stays up. Both in one sentence: stop first, then close. | `test_spoken_close_and_stop`, `test_stop_and_close_in_one_sentence`, `test_stop_leaves_the_session_able_to_answer_status` |
| Closing from the UI | `{"type":"end_session"}` sends `session_end`, closes with code 1000, and stops the robot only if it is moving. The browser does not reconnect. | `test_end_session_stops_a_moving_robot` |
| A Grok drop must not revive a pending action | The drop clears the pending action. The next connect does not put it back. | `test_grok_drop_clears_pending` |

A Grok-only drop while the arm is moving still stops the robot, because the spoken STOP spotter depends on the transcript. The README says this.

Amharic close phrases are in the lexicon and marked **NOT VERIFIED**.

Manual soak (30 min open, Wi-Fi off for 10 s, laptop lid, headset unplug): **NOT TESTED**. Nobody ran those on this machine in this session.

## Part B — simulated station camera (2026-11-05)

URSim has no camera and no objects. `camera.source: sim` renders a 1500×900 picture: three trays on top, mirrored back faces below, labelled **SIMULATED CAMERA · SYNTHETIC PRODUCTS**. A pack moves only when the gate reports `completato`. Any other outcome sets `uncertain` and leaves the packs where they are. There is no Gazebo, Isaac or MuJoCo scene.

Endpoints: `GET /api/camera/frame.jpg`, `GET/POST /api/sim/scene`, `POST /api/sim/scene/preset`, `POST /api/sim/scene/reset`, `GET /api/sim/identify`. `/api/sim/*` is 409 unless the source is `sim`, and 409 when the mode is `real`. Changing the scene during EXECUTING is 409.

Presets in `config/scenes/`: `demo`, `defects`, `ambiguous`, `empty`, `unseen`.

Tools, none of which move the arm: `identify_zone`, `find_product`, `check_labels`. «Cosa c'è sul tavolo?» and "What do you see?" list the zones. «Guarda cosa c'è sul tavolo.» stays a refusal, as in the earlier eval.

The picture was checked in the browser at `http://127.0.0.1:8765` with the source set to `sim` in that process only (the committed `camera.yaml` is still the Arm 3 browser default). The banner, the three trays, the zone cards and the preset buttons were on screen. Empty preset cleared the trays. «Cosa c'è sul tavolo?» replied `sinistra: vuota; centro: vuota; destra: vuota`.

Identification of the demo frame, three zones: **0.432 s**. Left cream matched `lumen_crema_viso` (ratio 100, the type word was not in the OCR text). Centre empty. Right lip balm: unknown product.

Synthetic evaluation, `eval/results/perception_synthetic_20261105-133822.json`, 6 products × 12 variants × difficulties `normal` and `hard` (144 rows). Not a real camera.

| | normal + hard |
|---|---|
| Product id matched | 10 / 144 |
| Type word matched | 2 / 144 |
| Unknown product | 134 / 144 |
| False acceptances (a row marked COMPLIANT) | **0** |
| Unseen pack | pack present, product unknown, compliance not checkable |

Hard difficulty matched no product. The rendered, resized, disturbed label is often not readable by Tesseract. No approved specification was loaded, so compliance stays "not checkable" and is never COMPLIANT. Real-camera numbers: **not measured**.

`scripts/sim_station_e2e.py` wrote `eval/results/sim_station_20261105-133630.json`: **NOT RUN**. `::1:30004` refused the connection, so URSim was not up and no motion result is claimed.

### Tests
```
$ .venv/bin/python -m pytest -q tests
320 passed, 1 warning in 75.18s
```
Before this prompt the suite was **296 passed**. The warning is Starlette's httpx deprecation notice. `npm run build` in `frontend/` succeeded after the voice and camera panels changed.

## NOT VERIFIED — the owner has to do these
- Amharic close phrases (ረዳቱን ዝጋ, ክፍለ ጊዜውን ጨርስ) and the rest of the Amharic lexicon. No recording was played.
- The 30-minute voice soak, Wi-Fi drop, laptop lid, and headset unplug.
- A real webcam and real packs. The synthetic numbers above are not that.
- URSim and the real arm. The station end-to-end file is NOT RUN.
- Native-speaker review of the zh and ja close phrases. They match as substrings in the tests; that is not a speaker check.

---

## Incident 2026-11-05 — voice assistant "stopping" without STOP — FIXED · TESTED
**Symptom:** the voice session ended during use although no STOP word, STOP button or Grok stop tool was used.
**Evidence (logs/voce.jsonl):** zero STOP events all day; every end of a voice session (11:48, 11:52, 11:55, 12:31, 12:52, 15:33,
16:01) = `voice session dropped (browser disconnesso)` + server `shutdown` in the same second. 16:00–16:01 run: notebook cells
31–35 executed back-to-back (Run All) → `handle.stop()` ran 84 s after start, during pending proposal A7.
**Root causes:** (1) "Run All" executed the final `handle.stop()` cell; (2) VOCE ran inside the Jupyter kernel, so kernel restart /
closing the notebook also killed the server (and its logs leaked into whatever cell was running).
**Fixes:** `app/launcher.py` runs VOCE in its own process (own session, pid file `logs/voce_server.json`, log `logs/server.log`);
`start()` re-attaches from any kernel/terminal; `handle.stop()` = SIGTERM → graceful lifespan shutdown (motion stopped first) and
records the reason in both logs. Notebook: stop and test cells guarded by `STOP_VOCE = False` / `RUN_TESTS = False`; new status/log cell.
Operator and assistant transcripts are now written to `logs/voce.jsonl` (to explain cancellations like A2–A5).
Bug found while fixing: stop() waited 20 s then SIGKILLed an already-exited child (zombie answered kill(pid,0)) → now reaped.
**Verified:** Run All + kernel shutdown → VOCE still running; live voice session stayed connected while a new kernel ran start();
`tests/test_launcher.py` (re-attach, survive caller, mode switch refused, traceable stop); full suite 329 passed.

## Incident 2026-11-05 16:19 — "voice assistant not controlling the movement" — FIXED · TESTED on URSim
**Trace (logs/voce.jsonl):** voice pipeline worked (heard → `vai_a_posa destra_sopra` proposed → "Yes, I confirm." validated →
program sent), but A2/A3 ended "non avviata: il controller non ha avviato il programma (Remote Control?)". RTDE showed the arm
was ALREADY at `destra_sopra` (distance 0.0000 rad, moved there at 16:01:23). A movej to the current pose finishes faster than
the 8 ms feedback period, so the adapter never saw `runtime_state = RUNNING` and wrongly reported a controller failure.
A probe program was accepted by URSim at the same time (Remote Control was fine).
**Fixes:** (1) gate: `vai_a_posa` to the pose the arm is verifiably at → `gia_in_posa` (IT/EN), no confirmation, no motion;
(2) URAdapter: not observed running but joints at the final target → COMPLETED (verified by feedback), never "not started";
(3) `_robot_stop` delivered a result to a timed-out future (InvalidStateError in server.log at the 16:20 STOP) → guarded;
(4) prompt rule for `gia_in_posa`; (5) English rules "go above / move the arm above <zone>".
**Verified on URSim:** same-pose execute → completed in 0.03 s; "Move the arm of the robot to the right." via Grok →
"already at pose destra sopra… where would you like to move it?"; "Go home" → real move completed. Tests: 330 passed.

## Merge 2026-11-05 — grokarm-ur3e (Apache-2.0, attribution in `NOTICE`) — DONE · TESTED (491 + new tests pass)

Source: https://github.com/13032585789-spec/grokarm-ur3e. Vision / grokarm_vision / stereo depth / cartesian / grasp /
kinematics code was already identical here; only the pieces below were missing and were added.

| Added | Where | State |
|---|---|---|
| Mesh collision model (python-fcl + trimesh, macOS arm64 wheels) | `skills/mesh_model.py`, `config/meshes/ur3e/` | DONE · TESTED; **OFF** in `robot.yaml` (`guard.mesh.enabled: false`) because Arm 3's model is not confirmed as UR3e |
| Motion guard with mesh + `ignore` list | `skills/motion_guard.py` | DONE · TESTED; guard still `enabled: false` |
| Real-robot interlock `ALLOW_REAL_MOTION=STAFF_APPROVED` | `config.py` | DONE · TESTED |
| Panic words stop while moving, debounced bare "yes" (sim/URSim only) | `safety/phrases.py`, `safety/gate.py`, `voice/grok_session.py` | DONE · TESTED |
| Learned (named) instructions; each movement still confirmed | `save_instruction` / `execute_instruction` tools | DONE · TESTED |
| Chinese colloquial commands, corrections (不对/不是), "the other one" from the previous command, "what can you do" | `conversation/rules.py`, `config/lexicon.yaml`, `config/poses.yaml` | DONE · TESTED |
| Multilingual testsets it/en/zh/ja/es/fr/de (631 cases), `--testset` | `eval/` | DONE · run with rules |
| Camera & vision tab (webcam / phone feed, cups, table calibration, inspection) | `frontend/src/components/CameraPanel.tsx`, `InspectionPanel.tsx`, `i18n/vision.ts` | DONE · builds; NOT TESTED with a real phone |
| Phone camera service started by the launcher | `start(mode, phone_camera=True)`, notebook `PHONE_CAMERA` | DONE · TESTED start/stop + VOCE polling it; NOT TESTED with a phone |
| Collision viewer, beam-axis script, gripper doc | `tools/`, `scripts/beam_axis.py`, `docs/GRIPPER.md` | copied (macOS paths) |

**Deliberately NOT imported (safety / platform):** real-robot auto-execute without a confirmation; serving the whole app on
0.0.0.0 (only the phone page listens on the LAN; VOCE and the vision operator API stay on 127.0.0.1); Windows scripts;
the committed `frontend/dist`; VAD `silence_duration_ms=280` / `prefix_padding_ms=200` (not in the xAI Voice docs,
checked 2026-11-05: only `type` and `idle_timeout_ms` are documented). The fast-path proposal still waits for the
question to be spoken before a "yes" counts (theirs marked it immediately) — their tests were adapted, with the reason
written in the test.

**Safety bugs found by the imported testsets and FIXED:**
- Reported / conditional / sarcastic "yes" was accepted as a confirmation ("He said yes", "Pretend I said yes",
  "Yes, but the right one", "Sì ma piano", "他说可以", "はい…でも右の方で"). Now uncertain → no motion.
  `conferme_non_autorizzate_bloccate` = 100 % on every set (was 71–89 % on en/zh/it/ja).
- Cancel phrases were ignored while a question was open ("Forget it", "やっぱりやめます"). Fixed.
- Panic words now latch STOP in every state ("Hold it!", "Attento!", "Occhio!", "小心！", "危ない！"). Bare "no" /
  "help" stay motion-only, and a single "fermo!" is still not a STOP (spec test).

Rules engine after the merge (offline, `--engines rules`): testset 144/153 · it 56/69 · en 197/240 · zh 77/84 ·
ja 50/61. es/fr/de are low (~45 %) because the offline rules engine does not cover those languages; live Grok does.
Grok-engine and URSim runs of the new testsets: NOT RUN.
