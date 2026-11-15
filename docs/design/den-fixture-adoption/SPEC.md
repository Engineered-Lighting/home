# Adopting the den's robotic fixtures in Home: spec

How the owner adopts the custom robotic spotlights into the den one at a time, replacing the Hue bulbs: 1 robotic + 3 Hue,
then 2 + 2, then 3 + 1, then 4 + 0. It also covers how Home's den view and coordinator panel show a mixed set.

**Status: design only.** Nothing here is built. No deploys, and nothing goes to hardware. Physical commands stay off
until the bench sittings pass and the owner says go (§7).

**Revision 2** follows the spine's review of the first draft (four confirmed findings and 48 to check):
- the bench now comes before the ceiling;
- the socket record has its own store, and it's the only map of which board serves which socket;
- den-live's sender enforces each socket's switch;
- the mount yaw reaches the geometry den-live aims from.

**Inputs:**
- the firmware chat's adoption handoff: bench commissioning A1–A13, Home adoption B1–B11, states and faults,
  capabilities;
- the coordinator chat's mixed-set plan: sockets by kind, aim jobs and room jobs, steps A1–A7;
- the den program's contracts, rulings and progress log;
- Home on `main` (`8a8e8422`), and den-live's integration branch (`7140369c`), for the desk sender.

**The clickable mockup** is `mockup.html` beside this file, also published as a private Artifact (the link is in the den
program's notes). Its room, names and readings are invented. It has no camera frames, faces, addresses, tokens or
coordinates.

## 0. Words

| Word | Meaning |
|---|---|
| **Socket** | One of the den's four ceiling positions. Its key is the `fixture_id` the geometry export, den-live, the trace and learning already use. **F1–F4 is its display label**, mapped once in the contract (S1). No id is renamed. |
| **Kind** | What a socket holds: **robotic** (an adopted C12 fixture), **hue** (a Hue bulb), or **empty** (mid-swap, or nothing there). |
| **Board** | A real C12 fixture. Its record has a **board fixture number** (a u32 set at the bench) and `board_mac`. Home keys a board on those two, never on the HA device id, because they survive a re-pairing. |
| **Role** | What the coordinator asks of a socket right now: *task light*, *standby*, *pre-aim*, *accent*, *fill*, *glow*, *reading light* (the fallback bulb), or *rests*. |
| **Adopted** | Bound to a socket, its record read, its mount recorded. The coordinator plans an adopted fixture. |
| **Virtual until the go** | Adopted, but nothing that moves the board or lights its spot is sent. The coordinator's plan for it is drawn in Home, as the virtual fixtures are today. |
| **The transport** | den-live's C12 sending, switched on by the spine's reviewed install with the owner's yes. Today that's the desk file (`den-c12-desk-1`). It's on or off for the whole program, and dark or lit. |
| **The go** | The owner's word after a fixture's bench sittings pass. It opens the transport. Each socket then steps up by its own switch. |

## 1. The shape

Adoption has three parts, in this order. Home owns the second and third.

| Part | Where | What it needs | What it leaves |
|---|---|---|---|
| **Bench** (A1–A13) | The one app over USB, at the bench, **before the fixture goes up** | The owner at every press | A valid record (identity, motors, zero, limits, motion profile, lighting map), plus sittings 2–4 (commissioning, the dark shoot-out, the lit bench test) and A13 (before the ceiling) done |
| **Adopt** (B1–B5, B7, B8, B10, B11) | Home, at the den | A paired fixture with a valid record. **No motion and no lit spot.** | The socket turns robotic: **adopted · virtual until the go** |
| **Go live** (B9, B6) | Home and den-live, at the den | The transport open (the go), and this socket's own switch | Test moves, then the coordinator drives the board, then the aim check, which lets it light the spot |

**Between adopt and the go,** the owner watches the coordinator plan 1 + 3 in Home, with the fixture drawn but never
sent to. **Nothing reaches the board** until the transport is on and the socket's switch allows it (§4.10). Both are
enforced where commands leave: in den-live's sender for the coordinator, and in Home's gateway for Home's own calls.

## 2. Where adoption lives in Home

**In the den's Controls panel, by socket.** The panel's fixture list (today: four rows with "Modeled" and "Not
commissioned") becomes the **socket list**. Each row is a socket: its kind, name, role and state. A row that isn't
robotic has **Adopt a fixture here**. A robotic row opens the inspector Home already has (Light, Aim, Setup). Its Setup
tab gains **Fixture**, with the board's details, and **Go live**.

**A "New fixture found" notice.** When Home sees a C12 that isn't bound to a socket, the den's status capsule shows
`New fixture found · Adopt`. It opens the same flow, with the socket chosen at the binding step.

**The flow is a sheet over the den, not a separate page.** The room stays in view, because every step points at
something in it: the socket being adopted lights up, and the bulb that's leaving, the front arrow and the aim targets
are drawn in the room.
- On a desktop, the sheet takes the inspector column.
- On a phone, it's Home's bottom sheet, at full height, with the room shrunk above it. That's the same step-down the
  pipeline uses.
- Reloading, or opening it on another device, resumes at the same step (§3, the draft).

**Who can use it:** signed in, every step's button. Look-only shows each socket's kind, state and role, with no
buttons, as Home's look-only does everywhere.

**Why not Setup's device picker.** The unmerged October branch `codex/home-adoption-ui` put v1-era room assignment
there. Adoption is about sockets and the den's light, and the owner reaches it from the den's controls, so it lives
there. That branch's good ideas carry over: one immutable request id per write, no automatic retry, and an unknown
outcome checked against its original receipt. Its v1 contract (`c12_home`) doesn't carry over: the v2 path is ZHA plus
the `c12` integration.

## 3. What adoption writes (proposed)

### 3.1 The adoption store: the one map of boards to sockets

A small private store beside the equipment store, with its own compare-and-swap revision. It sits **outside the room
model's binding**, so its writes never retire the sofa mark, the desk target or the named aims, which are sealed to the
equipment revision. It has two parts.

**`sockets`, keyed by the socket's `fixture_id`:**

```
{ "kind": "robotic",                  // robotic | hue | empty
  "state": "adopted",                 // robotic only: calibrating | adopted
  "physical": "off",                  // robotic only: off | tests | on (§4.10)
  "board": { "number": 2, "profile_id": "…", "ha_device": "…" },   // robotic only
  "name": "Sofa spot",
  "replaced_light": "light.<bulb>",   // for the swap back
  "aim_check": { "result": "not_run" | "passed" | "out_of_date", "at": <ts>, "fwhm_deg": 20 },
  "beam_fwhm_deg": 20, "lumens": null, "assumed": ["beam_fwhm_deg", "lumens"] }
```

**What it doesn't hold:**
- **The bulb map.** That's den_lighting's `sockets:` map (plan 04, step 6), the one source. Home and den-live read it
  through den_lighting's status.
- **Anything the board reports:** limits, motion, lit travel, capabilities. den-live and Home read those from
  `c12.get_profile`, so they can't disagree.
- **Offline, fault or stopped.** Those come from den-live's health reads, merged into the snapshot's `sockets{}`. They
  never make a revision.

**`boards`, keyed by board number + `board_mac`. Private, never exported:**
- the HA device id and IEEE address;
- `fw_build`, `capabilities`, and `profile_id` and `mount_revision` at adoption;
- the zone map: which endpoints are the radials, and which is the downlight (or none). It's entered from the bench, and
  shown as assumed from Light 1's check until then;
- the bench results: the owner's dates for sittings 2–4;
- Home's history for that board: beam FWHM and lux at distance, mount yaw against `mount_revision`, aim checks.

So a board moved to another socket keeps its measurements.

**Every write is one compare-and-swap,** with its own request id, never retried by itself. A write happens when:
- the swap starts;
- the fixture is adopted, reconnected or taken down;
- the aim check passes or goes out of date;
- `physical` changes.

**The export to den-live.** The `sockets` part, with the bound board's HA device id, goes to den-live as a private
credential, as the geometry does. The MAC and IEEE address never leave the store.
- den-live re-reads it on every new store revision and **starts a new run**: a new run header with the new profiles and
  devices, all leases dropped, learning kept. A lit spot pays a short relight. Writes are rare (adoption, the switch,
  the aim check), so that's acceptable, and it keeps the trace honest.
- **A missing or unreadable export means every socket is `off`.** That's the program's dark-by-default rule.

### 3.2 The equipment record: only the mount yaw

**The mount yaw is geometry.** den-live aims, and runs its eye-safety path check, from each socket's
`horizontal_world_reference`. So the yaw goes where den-live reads geometry:
- the socket's fixture record in the equipment document already has `orientation {direction_unit, roll_rad, status}`;
- adoption writes `direction_unit` (the modelled forward, turned by the yaw) as a new equipment revision;
- the export's `horizontal_world_reference` comes from it, where today it's derived from the anchor and the room's
  centre;
- the same step re-seals the marks bound to the equipment revision (the sofa mark and the desk target, from their raw
  corners) and re-binds the named aims, so nothing is retired.

**It's written** at mounting ("It faces another way"), at the first dark test move ("Turned"), and when the aim check
corrects it. Each yaw write also marks the aim check `out_of_date` and drops `physical` from on to tests.

**Taking a fixture down** writes the modelled default back.

**The document stays version 1:** `orientation` is already an exact field. **`physical_commands: false` stays on the
geometry export, with its present meaning:** this geometry is a model, not a permission to send. den-live's start,
install and desk checks keep it unchanged. A socket's `physical` can only narrow what the transport allows. It never
grants sending.

### 3.3 The draft

An adoption in progress is a draft in the adoption store, holding its step, board, socket and ticks. A reload, or the
other device, resumes from it. A draft isn't a revision. **Cancel** discards it, unless the bulb is already out
(§4.5).

## 4. The flow, screen by screen

Each step says what Home shows, what the owner does by hand, what Home does by itself, and how a failure recovers.
Steps 4.0 to 4.9 send the board nothing but reads, and the owner's room-light pulse.

### 4.0 Before the first fixture

| | |
|---|---|
| **Shows** | Two prerequisites. **The fixture integration:** Home Assistant has the `c12.get_profile` service. **The bulb map:** den_lighting's `sockets:` places all four bulbs. |
| **Owner, by hand** | Installs the ZHA quirk and the `c12` integration (B1): copies the files, adds the `configuration.yaml` line, restarts Home Assistant. Once. The steps and the undo are in the firmware's `ha/README.md`. And maps the bulbs, if not done yet (plan 04 step 6, two minutes): **Pulse** next to each socket shows which bulb is where. |
| **Home** | Reads Home Assistant's service list and den_lighting's status. It installs nothing and writes no Home Assistant configuration. |
| **Failure** | `The fixture integration isn't in Home Assistant yet. That's your step, once.` or `The bulb map isn't complete.` Then **Check again**. |

### 4.1 Unadopted

Two ways in:
- **A socket that isn't robotic.** Its row says `F2 · Hue · Sofa bulb`, with **Adopt a fixture here**.
- **A board that isn't bound.** It's paired and answering, but no socket has it. The capsule shows `New fixture found`,
  and the row in the flow says `Board 2 · not in a socket yet`.

The firmware doesn't know it's unadopted: this state is Home's alone.

### 4.2 Discovering

| | |
|---|---|
| **Shows** | `Power the fixture near the den, then open joining in Home Assistant (Zigbee → Add device).` A slow ring breathes at the chosen socket. Then a list of what has turned up: new C12 boards, and adopted ones greyed out. |
| **Owner, by hand** | Powers the commissioned fixture where it reaches the mesh, before it goes up. Opens joining in Home Assistant (B2). |
| **Home** | Watches Home Assistant's device registry for `EngineeredLight` / `c12-v2` (B3). It reads; it never opens joining itself. |
| **Failure** | After 3 minutes with nothing new: `Nothing new yet.` Then a checklist (is joining open, is the fixture powered, is it within a few metres of a mains-powered Zigbee light), and **Keep looking**. |

### 4.3 Found: identity, record and the bench

**Home calls `c12.get_profile` once** and shows:
- `Robotic fixture · board 2`;
- `firmware build 2031 · profile 3fa2` (short);
- its capabilities in words: `timed moves · fades · joins a flight · moves at rest`;
- **what the bench left:** `record valid` and `lit travel: yes` (both read from the record), and the dates of
  sittings 2–4. The board can't report the shoot-out or the lit bench test, so the owner enters their dates here, once
  per board, into the boards store.

**Missing dates** show a plain notice, `Finish its bench sittings before it goes up.` It's not a block: adoption is
virtual and harmless. The dates are lock 1 (§4.10).

| `get_profile` result | Screen | Recovery |
|---|---|---|
| `ok`, record valid | Continue | — |
| `no_record` or `record_invalid` | **Not commissioned.** `It works as nine ordinary lights, but its spot and motion stay off. Commission it at the bench, then come back.` | Bench commissioning in the one app. Home can't fix it. **Leave it for now** keeps it unbound. |
| `no_answer` | `It joined but isn't answering yet.` Home asks again 3 times, 5 s apart. | **Check again**. If it never answers: power-cycle it. |
| `capabilities` null, or `contract_version` ≠ 1 | **Firmware update needed.** `Home needs firmware v2-b or later.` | At the bench. |
| Same board number, different MAC, as an adopted board | `Two boards say they're board 2.` | A bench visit to renumber one. Home adopts neither until then. |
| Same board number and MAC as an adopted board, new HA device id | `This is F3's fixture, paired again.` **Reconnect to F3.** | Writes the new HA device id to the socket (a store revision), so den-live's sender follows it. No re-adoption, unless `profile_id` changed (§5). |

**"Which one is this?"** offers **Pulse its room light**, the owner's press. It pulses endpoint 1, the room light, at
a low level: an ordinary Zigbee light change. Nothing moves, and the spot stays dark. If the firmware later answers
Zigbee Identify (decided, §8), the button uses that instead.

### 4.4 Binding to a socket

| | |
|---|---|
| **Shows** | A plan of the den's four sockets, from above (abstract, no measurements), each with what it holds now, from the bulb map and the store: `Hue · Reading bulb`, `Robotic · Desk spot`, `Empty`. If the coordinator's coverage report has run (A6), each free socket shows `lights the sofa from 7 of 9 seats`. **Light 1 goes into F3**, as planned. The report helps place the later fixtures. |
| **Owner** | Picks the socket. Home names the bulb the map says is there: `The map says the Reading bulb is in F3.` |
| **Home** | **Checks the board:** the board number and MAC from `get_profile` match the board picked in 4.3. It draws the choice in the room: the socket glows, and the leaving bulb is outlined. It writes nothing yet: the choice goes into the draft. |
| **Failure** | A socket that's already robotic isn't offered. To move a fixture, take it down first (§5). If the map looks wrong: `Not that bulb? Fix the map in den_lighting first.` Home never keeps a second bulb map. |

### 4.5 Mounting (the owner's hands)

A checklist. Each line is the owner's tick, and Home checks what it can.

1. **Make the socket safe** (switch or breaker, as the owner does today). A socket on a wall switch is marked here, so
   "offline" there can say "or the wall switch".
2. **Take the bulb out.** Home watches Home Assistant: `Reading bulb · unavailable ✓`.
   - If a different bulb goes unavailable: `That was the Corner bulb: the map may be wrong.` **Back** returns to the
     binding step, and the owner fixes the map in den_lighting.
   - If nothing goes: `Home still hears the Reading bulb. Is it out?` Then **It's out** (it may be on another power
     path) or **Back**.
3. **Point den_lighting's F3 at the fixture's room light.** This is the owner's edit of the bulb map, in den_lighting.
   From now on, the fixture's room light (endpoint 1, an ordinary Zigbee light) takes the bulb's place for the real
   room (§6.3).
4. **Mount it level, front to the arrow.**
   - The room shows an arrow at the socket: the way its front should face.
   - The equipment record models yaw only, so a tilted or rolled mount isn't modelled.
   - **It faces another way** turns the arrow, in 15° steps or by tapping where the front points. At adoption, Home
     writes the yaw to the equipment record (§3.2).
5. **Power on.** Home waits for any answer, then reads the state:

   | State | Goes to |
   |---|---|
   | 2 (ready) | Continue |
   | 5 (stopped) | `Stopped · resume it when you like`. Resume is the owner's press, here or in Home Assistant. Adoption continues: moves are refused anyway. |
   | 1 with `attention` 2 or 3 | 4.6's failure |
   | 6 | Fault (§5) |

**The swap starts at tick 2.** Home writes the socket as `robotic · calibrating` (a store revision). The coordinator
leaves F3 out of every job, and the other sockets cover its room light.

**Cancel after tick 2 is "Put the bulb back":** the socket goes back to `hue`, and the owner points den_lighting back at
the bulb.

**Failure:** no answer within 2 minutes of power-on. That's the offline screen (§5), with **Keep waiting**.

### 4.6 Homing (rest pose and zero)

There's no homing switch and no homing move. Zero is a number in the bench record. At every boot the board checks that
its motors are the ones in the record, and that each reads inside its range.

| | |
|---|---|
| **Shows** | `Zero: set at the bench (profile 3fa2) ✓ · Motors: the same as at the bench ✓ · Both axes in range ✓`. |
| **Owner** | Nothing here. The head's rest pose is checked by eye at the first dark test move (§4.11), after the go. |
| **Home** | Reads `state` and `attention`. |
| **Failure** | `attention` 2, `motor_identity_mismatch`: `Its motors aren't the ones the bench recorded.` `attention` 3, `out_of_range`: `An axis reads outside its range.` Either way it's a bench visit, to the one app's recovery screen. |

### 4.7 Limits and capabilities (read only)

**Limits are the fixture's own.** For a robotic socket, Home keeps no limits of its own:
- den-live reads the record's joint limits, inset by the record's `host_inset_cdeg`;
- Home shows the same range, as one dial per axis:
  `pan: fixture ±180° · usable ±175°` and `tilt: fixture −95° to 90° · usable −90° to 85°`.

**The board's frame** is `c12-fixture-joints-1`: tilt 0 is level, + up; pan 0 is its front, + left. Home converts it to
its own aim frame **in one function**, with a test, for its Aim sliders. There's nothing to reconcile and no narrowing
by hand.

**Capabilities, in words, read from the record:**
- `lit travel: yes` (or `no`, as `lit_motion` says);
- `retargets mid-move: no`;
- `timed moves · fades · joins a flight · moves at rest`;
- `spot warmth: one fixed white until its warmth map is measured`.

**The room light,** from the boards store's zone map: `radials Z1–Z6 · downlight Z7`, marked `assumed` until the bench
confirms it for this board.

**Assumed values, said plainly:** `Beam width: assumed 20° · not measured`, `Light output: assumed · not measured`.
**Enter bench measurements** takes the beam FWHM and lux at set distances into the boards store, and the current
values into the socket (`assumed` shrinks).

**Failure:** a record whose limits are missing or inverted is treated as not commissioned.

### 4.8 Naming

- One field, with a suggestion from the socket, such as `Sofa spot`. The owner edits it (B10).
- Home shows `F3 · Sofa spot` everywhere. The board fixture number shows only in the fixture's details, for matching
  it at the bench.
- Home doesn't rename the device in Home Assistant.

### 4.9 Review and adopt

**A summary:**
- the socket and the bulb it replaces;
- the name;
- the board, firmware and profile;
- the bench dates;
- the usable range;
- the capabilities;
- the assumed values.

**What changes, in one paragraph:** `From now on the coordinator plans F3 as a robotic fixture, and Home draws it.
Nothing that moves it or lights its spot is sent until the go. Until then, the real room treats F3 as a bulb socket:
its room light stands in for the Reading bulb.`

**Adopt into F3** makes two writes, each a compare-and-swap:
- the socket `robotic · adopted · physical off`, as a store revision (B11);
- the mount yaw, if it differs from the model, as an equipment revision.

**Done:**
- the orb at F3 takes its kind;
- the label reads `F3 · Sofa spot · adopted · virtual until the go`;
- one line points ahead: `Next: the go opens the transport. Then F3's own switch, here.`

**Failure:** a revision conflict (someone else saved). `The den's setup changed while you were adopting.` **Review
again** reloads and keeps the draft. The write is never retried by itself.

### 4.10 The three locks (Go live, per socket)

The Go live card lists the locks. Each one says whether it's open.

| Lock | Open when | Who opens it | Enforced by |
|---|---|---|---|
| **1. The bench** | The record is valid, and the owner has dated sittings 3 and 4 for this board. It stays a standing check, because re-commissioning can change the record. `lit_motion` stays the board's own rule: a board that travels dark can still go live. | The owner, at the bench (dates entered in 4.3) | Home (shown); the board refuses every move without a valid record |
| **2. The transport** | den-live's C12 transport is on. It's dark or lit for the whole program. | The owner's go, carried out by the spine's reviewed install | den-live: no transport, no sender |
| **3. The socket's switch** (`physical`) | **off:** only Stop reaches the board. **tests:** the owner's Go live presses (dark test moves, the aim check), Stop and Resume. **on:** the coordinator's moves too. | The owner, here. Each step up is one hold-to-confirm, enabled only when locks 1 and 2 are open. Each step down is one tap, at once. | den-live's sender, on the coordinator's path; Home's gateway, on Home's path |

**The spot lights only after the aim check.** At `on`, the coordinator drives F3, and its spot may light only when
the transport is lit **and** F3's aim check has passed. Until then, den-live sends F3's moves dark, at level 0 with no
keep-lit, as the desk's `light: false` does today. The eye-safety check reasons from the aim, so an unchecked aim never
lights. That's the floor, not an extra gate.

This also keeps den-live's dark rehearsal from a sofa cue: F3 at `on` with the transport dark.

**In den-live's sender** (step A1), per socket, from the store's export:
- coordinator rows for a socket below `on` are recorded with a new outcome, `dropped_not_live`, and never called;
- a revision that lowers `physical` starts a new run, so the socket's unsent move and keep-lit are gone and its lease
  lapses;
- **before it sends anything but Stop** to a socket, at start and on every new run, den-live calls `c12.get_profile`
  on the socket's device. The board number and `profile_id` must match the socket's.
  - On a mismatch or no answer, it sends that socket nothing but Stop, and publishes it as a fault: `not the board
    adopted here`.
  - The `profile_id` is a checksum of the record, which includes the MAC, so the MAC never leaves Home.

**Lock 2 alone sends nothing** to a socket below `on`.

**Built before the go.** The card ships with the Home flow (H6), its buttons disabled until lock 2 opens. There's no
separate approval: the go is the owner's one word.

### 4.11 Dark test moves (B9; physical = tests)

**The owner's tick first:** `Hands clear of the head; nobody under it; the beam somewhere harmless.`

Each move is the owner's press. den-live's Go live route (D1) sends one at a time, never repeats one, and stays under
the radio budget (2 commands a second per fixture, Stop included). These moves never become learning events.
1. **Go to rest, dark.** Pan 0, tilt 0. Then the owner checks by eye: `Is the head level, facing its front?`
   - **Yes.** That checks the zero and the mount yaw together.
   - **No, it's turned.** Home asks how far and writes the yaw (§3.2).
   - **No, not level.** A bench visit.
2. **Turn left 30°, dark**, then **Tilt down 30°, dark**, then back to rest. Each shows its predicted time (the ACK's
   `predicted_ms`), then `done` with the measured time.
3. **The Stop drill.** The owner presses Stop, Home's or Home Assistant's. Home shows `Stopped · the spot is dark ·
   Stop survives a reboot`. Then the owner presses Resume.

**Pass:** each move done, Stop latched, Resume answered.

**Failure:**
- a refusal (`out_of_range`, `needs_commissioning`, `stopped`) is shown in words, and nothing is retried;
- a timeout: `No answer from F3`, which goes to offline;
- a fault goes to its fault words (§5).

**After a pass,** the owner may step F3 to `on`, and the coordinator drives it dark until its aim check passes.

### 4.12 The aim check: a spot lit at rest (B6; physical = tests or on; the transport lit)

**The lit step needs a lit transport.** If the go was dark only, the card says `The transport is dark: the aim check
needs a lit go.`

**The owner prepares:** the den lit as usual, so the camera sees; and nobody in the beam's path.

**The owner picks two targets** by tapping visible surfaces in the room. Two, because one target can't tell a turned
mount from an offset socket:
- one where the fixture serves the sofa;
- one off to its side.

Home checks each against the usable range.

**Run aim check.** For each target, den-live (D1, P1):
1. holds F3 out of the coordinator's plan;
2. moves it dark to the aim, and waits for `move_done`;
3. lights it at the same aim, at rest, at 30%, as **its own leased MOVE with keep-lit** about every second. If Home or
   den-live stops, the board's own 3 s lease darkens the spot;
4. takes 5 frames and finds the spot's centre in each (`aim_check.py`, workstream 02);
5. darkens it.

**The eye-safety floor runs throughout.** Before lighting, and on every frame, den-core's eye rule checks the beam to
the target against den-live's tracked heads. If a head is near the beam, the spot goes dark at once. People in the
room are fine; a head in the beam isn't.

**The result** is drawn as a target, not a picture:
- the predicted point at the centre;
- a dashed circle at half the beam's FWHM radius at that throw;
- five dots for the frames: `4 of 5 within · off by 3 cm`.

No frame is shown or leaves LA.

**Pass:** at least 4 of 5 frames within, on both targets. The socket's `aim_check` becomes `passed`, recording the
FWHM it used. If a measured FWHM is entered later, the check goes `out_of_date`.

**Failure:**

| What | Says | Recovery |
|---|---|---|
| Both targets off the same way | `Both landed 3° to the left: the mount is turned.` | **Correct the yaw by 3°** writes the yaw (an equipment revision). The run waits until den-live reports it has re-bound, then goes again. |
| Spread, not offset | `The spot moved around: 2 of 5 within.` | **Run again.** If it repeats, the beam width may be wrong (assumed): measure it at the bench. |
| Spot not found | `The camera couldn't find the spot.` | Choose another target, or lower the room light |
| A head near the beam | `Dark: a head came near the beam.` | Run again when the path is clear |
| Stop pressed | `Stopped.` | Resume is the owner's |

### 4.13 Live

With F3 at `on`, a lit transport and the aim check passed, the label loses its `virtual` tag and reads `F3 · Sofa spot ·
live`. Lit travel still needs the record's `lit_motion` and the coordinator's eye-safety check. Until then the board
travels dark, whatever is asked.

## 5. After adoption: the states

**What's stored** (the adoption store) is `calibrating` or `adopted`, plus `physical` and `aim_check`. **What's live**
(den-live's health, about every 5 s, merged into the snapshot's `sockets{}`) is everything else.

| State | Source | Room (orb) | Row | Recovery |
|---|---|---|---|---|
| **Adopted · virtual until the go** | store | The orb, with the coordinator's plan drawn | `F3 · Sofa spot · task light · virtual` | The go (§4.10) |
| **Tests** / **On, dark** / **Live** | store | As above | `· tests`, `· on · dark until its aim check`, `· live` | — |
| **Held by hand** | den-live | The solid ice ring (the orb WIP's held mark) | `in your hands · Return to auto` | Home's existing hand holds. The board flags an EP 9 or hand MOVE. A change to the radials or downlight (EP 1–8) isn't flagged by the board: Home detects it as it does for a Hue bulb. |
| **Stopped** | den-live | The core dims, with a small square under it | `Stopped · Resume` | **Resume is always the owner's press.** Stop survives a reboot. |
| **Offline** | den-live | A hollow dashed ring, no glow | `Offline since 21:40 · check its power` (on a wall switch: `· or the wall switch`) | It rejoins by itself. If it left the network, pair it again: Home knows it by board number and MAC, so **Reconnect to F3** needs no re-adoption. |
| **Fault** | den-live | The amber attention diamond on the orb | The fault in words, from `contracts/c12v2/zcl-fc13.md` | A motion fault (13–19): dark and holding, **Resume** after the owner checks it. A light fault: dark until a power cycle. Over temperature: the spot is out until it cools 2 °C below the trip. Anything else: a bench visit. |
| **Not the board adopted here** | den-live | Diamond | `F3's board doesn't match its adoption` | Nothing but Stop goes to it. Reconnect or re-adopt. |
| **Motors changed or out of range** | den-live | Diamond | `Needs a bench visit` | The one app's recovery screen |
| **Re-commissioned** | den-live | Orb, with a small amber dot | `Aim check out of date` | den-live reads `profile_id` with its health. On a change it sends nothing but Stop and publishes the change. Home re-reads the profile, records the new `profile_id`, and marks the aim check `out_of_date` (`physical` on → tests). Virtual planning and the room light go on. |
| **Firmware too old** | den-live | Diamond | `Firmware too old for Home` | At the bench |
| **Updating** | den-live | Hollow ring | `Updating · dark and still` | Wait |

**Stop has one route.** Go live's Stop and the den's Stop are the same control: den-live's coordinator Stop, which
reaches the sender's Stop lane first. Home's gateway forwards Stop and Resume in every mode; today it forwards them only
in virtual mode, and H2 fixes that. Home Assistant's own Stop on the board works even with LA down.

**Taking a fixture down (robotic → Hue, or empty).** **Take it down** runs the mounting checklist backwards: the
fixture is unpowered, the bulb goes back (Home sees it answer), and the owner points den_lighting back at the bulb.
Then:
- the socket is written `hue` (or `empty`);
- the yaw goes back to the modelled default;
- the coordinator carries the socket's learned glow back to the bulb (coordinator A4);
- the board keeps its boards-store entry, so re-adopting it elsewhere is short.

**One reader.** Every read counts against the radio budget. Before a board is adopted, only Home's gateway reads it, no
faster than once a second. Once adopted, only den-live reads it (one multi-attribute read about every 5 s, plus
`get_profile` at each new run and whenever the board comes back online), and Home shows what den-live publishes.
Attribute reporting isn't configured yet, so den-live reads rather than waits for reports.

## 6. The mixed den in Home

### 6.1 Each socket drawn by kind

The fixture-orb design chat owns the look. This spec owns what each look must say. Robotic and Hue have to be told
apart at a glance, in both themes, and at phone size.

| Kind | In the room | Label (Lighting view, hover or tap; always in the Controls list) |
|---|---|---|
| **Robotic** | The orb: a small core, the glow tinted by its channels and grown by its light, the beam when lit, and the WIP's marks (dashed warm ring = aimed and waiting dark; solid ice ring = in your hands) | `F3 · Sofa spot · task light 70%` plus the `virtual` tag until it's live |
| **Hue** | **A bulb's glow:** no core, no beam, a softer and wider warm disc at the socket, sized by the bulb's level. While Bulbs is off, the bulb plan is a shadow: the disc becomes a dashed outline at the size it *would* be. | `F1 · Corner bulb · fill · would set 35%` |
| **Empty** | A small dotted ring at the socket | `F2 · empty · swap in progress` |
| **Offline or fault** | The robotic orb, its core grey and no glow, with a hollow ring or the attention diamond | `F4 · offline` |

**Role** is the label's last part. It comes from den-live's `sockets{F: {kind, state, role}}` in
`den-virtual-spots-1`. Home never infers it.

**Facing admits, it doesn't move.** By the owner's ruling, a lit light is never refused for facing. Facing only decides
what newly lights: a new task light, or which bulb becomes the fallback reading light. Once lit, the task light and the
reading bulb keep their roles whichever way the owner turns. Roles move only for:
- the head or the eyes;
- range;
- the end of the task;
- another head in the beam.

### 6.2 The mixes

A reader on the sofa, with typical roles (the coordinator plan, §3). Any socket can be the robotic one, and the tests
cover every subset (§9).

| Mix | Robotic sockets | Hue sockets | When no fixture can serve the reader |
|---|---|---|---|
| **0 + 4** (once A3 is built) | — | glow; fill from the bulb in front; the bulb behind is the reading light | — |
| **1 + 3** | task light; its radials and downlight join the glow | fill (in front, soft); accent (nearest labelled art, warm and low); glow (the rest) | The fixture rests or glows. **The bulb behind the reader becomes the reading light**, up to its house level. |
| **2 + 2** | task light; standby (aimed and dark, for a crossfade) | fill; glow | The fixtures rest and glow. A bulb behind the reader, if there is one, becomes the reading light. |
| **3 + 1** | task light; standby; accent on art (or pre-aim) | fill or glow, by where the reader faces when it lights | The same, if the last bulb is behind the reader |
| **4 + 0** | task light; standby; accent; pre-aim | — | The fixtures' radials and downlights carry the room |

### 6.3 Before the go: the drawn plan and the real room

**The rule:** until a robotic socket is live (at `on`, with a lit transport and the aim check passed), its spot **can't
serve the real room**.
- **Home draws** the coordinator's plan, with the fixture as the task light, tagged `virtual`.
- **The real bulbs' plan** (sent only while Bulbs is on, as today) counts that spot as unable to serve, so the fallback
  reading light applies.
- **The fixture's room light** (EP 1, an ordinary Zigbee light) stands in for the bulb it replaced, through
  den_lighting's map. So the room is never a light short.

### 6.4 Rehearse the full set

**Rehearse the full set** keeps today's view. With every socket `hue`, the installed view would show no spots at all,
and today the coordinator plans four virtual fixtures. So the rehearsal is a coordinator setting, built into A1 itself:
- **On while no socket is robotic** (today's view, unchanged until fixture 1).
- **Off from the first adoption.** The owner can turn it back on at any time, for testing.
- **While it's on:**
  - the coordinator plans, and Home draws, the four virtual fixtures;
  - **den-live's sender drops every coordinator row** (Stop still passes);
  - the bulbs follow the rehearsal plan as today, only while Bulbs is on;
  - the capsule says `Rehearsing all four · not what's installed`.

### 6.5 The coordinator panel

**A new Sockets section, first in the drawer.** One row per socket:

```
F1  Hue       Corner bulb     fill · would set 35% · 2700 K
F2  Hue       Sofa bulb       reading light · would set 60%
F3  Robotic   Sofa spot       task light · 70% · 2700 K · virtual
F4  Hue       Art-side bulb   accent · would set 20%
Assumed for F3: beam 20°, light output
The bulbs plan as if no spot serves: F3 is virtual until the go.
```

- **Bulbs** folds into Sockets: each bulb line is keyed by its socket.
- **Why** stays as it is, and names sockets with their names.
- **Learned** stays.

**The summary line** gains the mix: `Lighting coordinator · On · Reading · 1 robotic, 3 Hue`.

**Home's spot playback** today drops any snapshot whose fixtures don't match its four. With the mixed set, it takes its
scope from the same snapshot's `sockets{}` instead: the robotic sockets, or all four while rehearsing (H4).

### 6.6 The Controls list

- The header's `Modeled · Not commissioned` badges become the mix (`1 robotic · 3 Hue`) and, while any robotic socket
  isn't live, `Virtual until the go`.
- Each row has its kind's mark, its name, its role and its state.
- A Hue row opens the bulb's control, through den_lighting and Home Assistant, as the bulb's own control does. A
  robotic row opens the inspector.

### 6.7 Where "virtual until the go" shows

1. The robotic orb's label: a `virtual` tag.
2. Each robotic row in the Controls list and in the coordinator's Sockets section.
3. The Controls header's badge.
4. The adoption's last screen: `adopted · virtual until the go`.
5. The socket's Go live card, with its three locks.
6. The coordinator's actuation line, unchanged: `Moving the virtual spots in Home`.

It goes away for a socket only when it's live (§4.13).

## 7. Safety rules

- **Where each lock is enforced** (§4.10):
  - the board refuses every move without a valid record, and lit travel without `lit_motion`;
  - den-live sends nothing without the transport, and enforces each socket's `physical` on the coordinator's path,
    with lit only after the aim check;
  - Home's gateway enforces `physical` on Home's own calls.
- **Home's gateway, below `tests`,** refuses `c12.move` and `c12.keep_lit` for that board. Everything else passes:
  `c12.get_profile`, reads, `c12.stop`, the owner's `c12.resume`, and the room light and zones (EP 1–8) as ordinary
  lights.
- **EP 9, the hand spot, is the owner's own manual path,** as with any lamp in Home Assistant. Home's den view doesn't
  offer it below `tests`, and nothing automatic uses it. **den-live never uses EP 9:** it's the hand path, and it
  takes the spot from the coordinator.
- **Stop always works:** one route through den-live to the sender's Stop lane (§5), and Home Assistant's own Stop on
  the board, even with LA down.
- **In the adoption and Go live flow:** every move is the owner's press, one at a time. Nothing is chained, repeated or
  retried, Home never resumes on its own, and the only lit step is the aim check's leased spot at rest. After a socket
  is live, the owner's hand changes follow the same rules as the coordinator's (the move style setting, the eye-safety
  floor).
- **Only the fixture's own limits.** Home keeps none of its own for a robotic socket, and the board refuses the rest
  anyway.
- **Every write is one compare-and-swap** with its own request id, never retried by itself. An unknown outcome is
  checked against its receipt.
- **No private data leaves:**
  - no frames from the aim check, only within or out per frame;
  - no MAC or IEEE address outside the store;
  - everything said in words goes in through `textContent`.

## 8. Owner questions, merged, and what's decided

The firmware chat's 12 (F1–F12) and the coordinator chat's 5 (C1–C5), de-duplicated.

**Decided** (by a ruling, by the handoff, or a close call this design just makes):

| From | Decision |
|---|---|
| F1 / C2: which socket first | **Light 1 into F3**, as the program already planned. The coverage report helps place the later fixtures. |
| F2: names | The owner names it; Home suggests one from the socket. Home shows `F3 · Sofa spot`, and the board number only in its details. Home Assistant's device name is left alone. |
| F3: owners | The owner mounts, by hand. Workstream 02 owns the aim check's code and pass rule. Production Home owns the screens. The spine assigns fixtures 2–4. |
| F5: where adoption runs | Home at the den. The bench stays in the one app, and Home shows only its result and dates. |
| F4: the downlight | Which endpoint is the downlight is the bench's to confirm per board (the boards store's zone map, assumed until then). Whether EP 1 includes Z7 is set in the record at the bench (A10). Home reads it. |
| F8: "which one is this?" | Ask the firmware chat for a slow room-light pulse on Zigbee Identify. Until then: **Pulse its room light**. |
| F11: firmware updates | At the bench. OTA, when it exists, is outside this spec. |
| F12: Resume | Never automatic. Always the owner's press. |
| C3: the full set | Show what's installed. **Rehearse the full set** stays on until the first adoption, and is a toggle after (§6.4). |
| Who may adopt | Signed in only. Look-only shows states. |
| The per-socket go | The transport is the owner's one go. Each socket's switch is one hold-to-confirm per step, built before the go and enabled by it. There's no separate approval. |

**For the owner** (each with the default Home would build):

| # | Question | From | Recommended default |
|---|---|---|---|
| 1 | **The bulb map:** which bulb is in which socket today? | F1, C1 | Mapped once, before fixture 1, in den_lighting (plan 04 step 6, two minutes, with **Pulse** per socket). It's the one map: Home confirms it when each bulb comes out. |
| 2 | **Wall switches:** is any socket on one? | F6 | None assumed. If one is, the mounting step marks it, and "offline" there says `or the wall switch`. |
| 3 | **The aim check:** what to aim at, at what level, and who's in the room? | F7 | Two visible surfaces, one where it serves the sofa and one to its side; 30%, lit at rest; people may stay, and the spot goes dark if a head comes near the beam. |
| 4 | **Re-commissioning:** must the aim check run again when the record changes? | F9 | Yes: den-live notices, the aim check goes out of date, and the socket drops to tests until it passes. Virtual planning and the room light go on. |
| 5 | **Beam angle and lux at distance:** measure at the bench before the ceiling? | F10 | Yes: a lux meter on axis at 1, 2 and 3 m, and the beam's half-maximum edge. Entered in Home's **Enter bench measurements**. Until then, `assumed 20°` everywhere. |
| 6 | **The fallback reading light:** may the bulb behind you go brighter than its usual level when no spot can serve? | C4 | Up to its house level. Beyond that only if you've taught it to. |
| 7 | **Fill and accent:** do you want them at all? | C5 | A soft fill from the bulb in front of you, and an accent only on labelled art. |
| 8 | **Before the go,** should the fixture's room light stand in for the bulb it replaced, and the bulbs plan as if its spot can't serve? (new) | — | Yes (§6.3). The room is never a light short while the fixture is virtual. |

## 9. Build steps (later), owners and tests

None of this starts without the spine's word. Home PRs follow the repo's rules: a `changes/unreleased/` fragment, the
private-data scan, and no push or merge without the spine.

### Production Home (the production chat)

| Step | What | Tests |
|---|---|---|
| **H1** | **The adoption store** (sockets and boards, compare-and-swap, private folder) and its export to den-live. **The mount yaw in the equipment record's `orientation`**, with the export's `horizontal_world_reference` taken from it, and the sofa mark, desk target and named aims re-sealed in the same step. `physical_commands: false` unchanged on the export. | Store: each kind's fields; a conflict is a 409, never retried; the export carries no MAC or IEEE. Yaw: a write makes one equipment revision whose export has the turned reference, the marks re-sealed and valid, and the aim check marked out of date. Take down writes the default back. |
| **H2** | **The read path in the gateway:** Home Assistant's service list, the device registry filtered to `EngineeredLight` / `c12-v2`, `c12.get_profile`, attribute reads, and den_lighting's status for the bulb map. At most one read a second per board, and none once adopted. **The command guard:** below `tests`, refuse `c12.move` and `c12.keep_lit`. **Stop and Resume forwarded in every mode.** | A fake Home Assistant for each `get_profile` result, old firmware, a duplicate board number, re-pairing; the rate cap. The guard: below tests, move and keep-lit refused while Stop, Resume and EP 1–8 pass. Stop reaches den-live with the gateway in each mode. |
| **H3** | **The flow** (§4.0–4.9): a pure model (`home-den-adoption-model.mjs`: steps, draft, failure transitions) and the sheet. Behind `HOME_WEB_DEN_ADOPTION`, off by default. | Model: every step's success and failure path; resuming a draft; "put the bulb back" after the swap starts; the board check at binding; a stopped board at power-on. Browser, desktop and phone, with a fake Home Assistant: the flow sends nothing but reads and the pulse; look-only has no buttons; text through `textContent`. |
| **H4** | **The mixed den** (coordinator A5): drawing by kind and role (with the orb chat's look), the Controls list, the coordinator panel's Sockets section, the "virtual until the go" placements, and **spot playback scoped by the snapshot's `sockets{}`**. | Every mix with every subset of sockets as the robotic ones. Roles only from `sockets{}`. 1 + 3 and rehearse on and off get through the playback guard. Offline, fault and empty drawing. The `virtual` tag wherever a socket isn't live. Phone sizes in both themes. |
| **H5** | **Fixture health** from den-live's snapshot (§5): the states in words, re-commissioned, not the board adopted here, reconnect, firmware too old, taking a fixture down. | Each state from a fake snapshot. Re-pairing keeps the adoption. A re-commissioned board drops on → tests. |
| **H6** | **Go live** (§4.10–4.13): the card and its locks, the socket's switch, the dark test moves, the Stop drill and the aim-check screen. Built before the go, with buttons disabled until lock 2 opens. | Every button disabled unless its locks are open. Hold-to-confirm. One command in flight; nothing retried; Resume never automatic; Stop always enabled. The aim result from a fake den-live reply, with no frame in it. |

### den-live and the coordinator (the coordinator chat)

| Step | What | Tests |
|---|---|---|
| **A1** | **Read the socket export, and enforce the sockets in the sender.** desk.json shrinks to the transport switch (`light`, dark or lit). The board for each socket comes from the export. Today's one-fixture desk keeps working until A1 replaces it. The per-socket rules: off is Stop only; tests adds the Go live routes, Stop and Resume; on adds the coordinator's rows. Lit only with a lit transport and the aim check passed. `dropped_not_live` rows. The board check (number and `profile_id`) before sending. A new run on each store revision. A missing export means all off. **Rehearse the full set**, on while no socket is robotic, with the sender dropping coordinator rows while it's on. Read `host_inset_cdeg` from the record. | With the fake Home Assistant and F3 mapped with a lit transport: at off and tests, the coordinator's MOVE and keep-lit make no `c12` call, and Stop passes. At tests, a Go live move goes. At on with no aim check, moves go dark. At on with it passed, lit. Lowering on → tests mid-run stops the next row. A wrong board, or a changed `profile_id`, gets only Stop. Rehearse on: nothing but Stop goes. |
| **A2–A4** | As the coordinator plan has them: aim jobs on robotic sockets only; room roles and the fallback reading light; learning carried over. Plus the **real-room rule** (§6.3) and **facing admits, it doesn't move** (§6.1). | The plan's tests over every mix. A socket below live counts as unable to serve for the bulbs. A lit task light stays lit as the reader turns. |
| **A1b** | `sockets{F: {kind, state, role}}` in `den-virtual-spots-1` (contract 0/4); boards' health, including `profile_id`, in the coordinator state. | Snapshot shape per mix; health refreshed at about 5 s, never faster than the budget. |
| **A6** | The coverage report, published in the coordinator state, for Home's binding step. | Shares per socket from registered geometry, offline. |
| **A7** | verify-producer follows the robotic count (with the spine). | — |
| **D1** | **The Go live routes:** the owner's dark test moves, and the aim check's leased lit step, at `tests` or above. Never learning events. | Refused below tests. One command in flight. The lit step's keep-lit stops if the route dies. |

### Perception and calibration (workstream 02)

| Step | What | Tests |
|---|---|---|
| **P1** | `aim_check.py` as a den-live call: two targets, 5 frames each, the result per frame (within or out, the offset), a yaw-or-offset verdict, and the eye rule against tracked heads on every frame. No frames leave LA. | A synthetic spot set: pass, a turned mount, scatter, spot not found, a head near the beam. |

### The firmware chat

| Step | What |
|---|---|
| **W1** | Answer Zigbee Identify with a slow room-light pulse. |
| **W2** | Attribute reporting for 0xFC13 (FW-5), so health isn't stale. |
| **W3** | The quirk and integration install with the owner (FW-5): what §4.0 checks for. |
| **W4** | At A10, whether EP 1 includes Z7, and which endpoint is the downlight, recorded per board. |

### The spine

| Step | What |
|---|---|
| **S1** | Contracts: the adoption store's export (contract 1), the socket id ↔ F1–F4 label, `sockets{}` in the snapshot (0/4), the trace header's profiles from adopted boards (5), `dropped_not_live`. |
| **S2** | verify-producer and the trace header follow the robotic count (with A7). |
| **S3** | The go: the transport's install, with the owner. |

### Lighting direction

| Step | What |
|---|---|
| **L1** | How each role is played: the fill's ratio, accent levels, and the crossfade when a role moves between sockets. |

**The order that works:**
1. H1 + A1 (with Rehearse, so today's view never goes) + S1;
2. H2;
3. H3;
4. A2–A4 + A1b + H4 + H5 + H6;
5. **per fixture:** the bench (sittings 2–4 and A13) → adopt (it plans 1 + 3 virtually) → the go → its switch → D1 +
   P1 for its tests and aim check.

## 10. Privacy

- The mockup and every test use an invented den, invented names and synthetic readings.
- No camera frame, face, network address, token or room coordinate is shown, stored in git, or sent anywhere.
- The MAC and IEEE address live only in the private adoption store. The export carries the HA device id, as a private
  credential.
- The aim check returns within or out per frame, and an offset in centimetres. It never returns an image.
