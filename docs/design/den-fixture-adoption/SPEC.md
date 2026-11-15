# Adopting the den's robotic fixtures in Home: spec

How the owner adopts the custom robotic spotlights into the den one at a time, replacing the Hue bulbs: 1 robotic + 3 Hue,
then 2 + 2, then 3 + 1, then 4 + 0. It also covers how Home's den view and coordinator panel show a mixed set.

**Status: design only.** Nothing here is built. No deploys, and nothing goes to hardware. Physical commands stay off
until the bench sitting passes and the owner says go (§7).

**Inputs:**
- the firmware chat's adoption handoff: bench commissioning A1–A13, Home adoption B1–B11, states and faults,
  capabilities;
- the coordinator chat's mixed-set plan: sockets by kind, aim jobs and room jobs, steps A1–A7;
- the den program's contracts (room model, controls) and progress log;
- Home's den view as it is on `main` (`8a8e8422`).

**The clickable mockup** is a private Artifact (the link is in the den program's notes). Its room, names and readings
are invented. It has no camera frames, faces, addresses, tokens or coordinates.

**Words used for status:** *built* (in code and tested), *planned* (agreed, not built), *proposed* (this spec's idea,
not agreed yet), *unknown*.

## 0. Words

| Word | Meaning |
|---|---|
| **Socket** | One of the den's four ceiling positions, F1–F4. den-live and Home's snapshot call it `fixture_id`. A socket never changes. What sits in it does. |
| **Kind** | What a socket holds: **robotic** (an adopted C12 fixture), **hue** (a Hue bulb), or **empty** (mid-swap, or nothing there). |
| **Board** | A real C12 fixture. Its record has a **board fixture number** (a u32 set at the bench) and `board_mac`. Home keys a board on those two, never on the HA device id, because they survive a re-pairing. The board fixture number is not the socket. |
| **Role** | What the coordinator asks of a socket right now: *task light*, *standby*, *pre-aim*, *accent*, *fill*, *glow*, *reading light* (the fallback bulb), or *rests*. |
| **Adopted** | Bound to a socket, its record read and its limits checked. An adopted fixture is planned by the coordinator. |
| **Virtual until the go** | Adopted, but nothing is sent to it. The coordinator drives Home's drawing of it, as it drives the virtual fixtures today. |
| **The go** | The owner's word, per fixture, after that fixture's bench sittings 2–4 pass. Before it, Home sends that board nothing that moves it or lights its spot. |

## 1. The shape

Adoption has three parts. Home owns the second and third.

| Part | Where | What it needs | What it leaves |
|---|---|---|---|
| **Bench commissioning** (A1–A13) | The one app over USB, at the bench | The owner at every press | A valid record: identity, motors, zero, limits, motion profile, lighting map |
| **Adopt** (B1–B5, B7, B8, B10, B11) | Home, at the den | A paired fixture with a valid record. **No motion and no lit spot.** | The socket turns robotic: **adopted · virtual until the go** |
| **Go live** (B6, B9) | Home, at the den | Bench sittings 2–4 passed for this board, and the owner's go | Test moves, the aim check, and then the coordinator may drive the board |

This split is what makes the owner's sequence work today. The owner can adopt fixture 1 into its socket and watch the
coordinator plan 1 + 3 in Home before the go. The coordinator plans and Home draws it as though it were live. Nothing
reaches the board until each lock in §4.10 is open.

## 2. Where adoption lives in Home

**In the den's Controls panel, by socket.** The panel's fixture list (today: four rows with "Modeled" and "Not
commissioned") becomes the **socket list**. Each row is a socket: its kind, name, role and state. A row that isn't
robotic has **Adopt a fixture here**. A robotic row opens the inspector Home already has (Light, Aim, Setup). Its Setup
tab gains **Fixture**, with the board's details, and **Go live**.

**A "New fixture found" notice.** When Home sees a C12 that isn't bound to a socket, the den's status capsule shows
`New fixture found · Adopt`. It opens the same flow, with the socket chosen at the binding step.

**The flow is a sheet over the den, not a separate page.** The room stays in view, because every step points at
something in it: the socket being adopted lights up, and the bulb that's leaving, the front arrow, the limits and the aim
target are all drawn in the room.
- On a desktop, the sheet takes the inspector column.
- On a phone, it's Home's bottom sheet, at full height, with the room shrunk above it. That's the same step-down the
  pipeline uses.
- Reloading, or opening it on another device, resumes at the same step (§3, the draft).

**Who can use it:**
- Signed in, it shows every step's button.
- Look-only shows each socket's kind, state and role, and has no buttons.

**Why not Setup's device picker.** The unmerged October branch `codex/home-adoption-ui` put v1-era room assignment
there. Adoption is about sockets and the den's light, and the owner reaches it from the den's controls, so it lives
there. That branch's good ideas carry over: one immutable request id per write, no automatic retry, and an unknown
outcome checked against its original receipt. Its v1 contract (`c12_home`, `contracts/c12-home/v1/adoption`) does not
carry over. The v2 path is ZHA plus the `c12` integration.

## 3. What adoption writes (proposed)

**1. The socket record, in the equipment document.** Each socket's entry gains what the coordinator plan's contract 1
asks for, plus one field for the physical lock.

```
"F3": { "kind": "robotic",                 // robotic | hue | empty
        "state": "adopted",                // robotic only: calibrating | adopted | offline | fault
        "physical": "off",                 // robotic only: off | tests | on (§4.10)
        "light": null,                     // hue only: den_lighting's entity, or null while unmapped
        "replaced_light": "light.<bulb>",  // the bulb this fixture replaced, for the swap back
        "fixture": { "board": <board fixture number>, "firmware": <fw_build>, "profile_id": <short> },
        "name": "Sofa spot",
        "geometry": { "aim_origin_m", "horizontal_world_reference", "limits" },
        "aim_check": { "result": "passed" | "failed" | "not_run" | "out_of_date", "at": <ts> },
        "profile": { … the coordinator plan's §2 profile; each unmeasured value carries "assumed": true } }
```

- **Every write is a new equipment revision,** through the store's compare-and-swap. Writes happen when the swap
  starts, when the fixture is adopted, when the aim check passes or goes out of date, at each change of `physical`,
  and when a fixture is removed.
- The revision and digest are in every binding, so den-live re-binds (coordinator A1) and Home's view re-binds.
- **A known cost:** each revision is a new room model, so tracks, spots and the figure restart dark for a moment. One
  adoption makes about four revisions, spread over the evening. That's acceptable during adoption. It's never done in
  the steady state.
- **Home's equipment validator is exact-field.** Adding `sockets` means document version 2, with Home's validator and
  the geometry export (`tools/den-room/`) updated in the same PR.
- A one-time migration writes the first version-2 revision with every socket `hue` and `light: null` (unmapped). The
  bulb map fills `light` in.
- **`physical` per socket replaces the document-wide `physical_commands: false`.** Every socket starts `off`.

**2. The boards store.** This is a small private store beside the equipment store, with the same compare-and-swap and
private-folder rules. It holds one entry per board, keyed by the board fixture number plus `board_mac`. Each entry has:
- the HA device id and IEEE address;
- `fw_build`, `contract_version` and `capabilities`;
- `profile_id` and `mount_revision` at adoption;
- the socket, the replaced bulb, the name, the state and the date;
- Home's own measurements, which aren't on the board: **beam FWHM, lux at distance, and the mount yaw** (the firmware
  handoff's suggestion);
- the aim-check history.

Everything else is read from the board each time, so the two can never disagree.

The MAC and IEEE address are stored only here: never shown whole, never logged, never in git.

**3. The draft.** An adoption in progress is a draft in the boards store, holding its step, board, socket and ticks. A
reload, or the other device, resumes from it. A draft isn't an equipment revision. **Cancel** discards it, unless the
bulb is already out (§4.5).

## 4. The flow, screen by screen

Each step says what Home shows, what the owner does by hand, what Home does by itself, and how a failure recovers.
**Stop is on every screen of Go live** and works unconditionally. Steps 4.1 to 4.9 send nothing to the board but reads.

### 4.0 Before the first fixture: the integration

| | |
|---|---|
| **Shows** | Whether Home Assistant has the `c12` integration: the `c12.get_profile` service exists. |
| **Owner, by hand** | Installs the ZHA quirk and the `c12` integration (B1): copies the files, adds the `configuration.yaml` line, restarts Home Assistant. Only once. The steps and the undo are in the firmware's `ha/README.md`. |
| **Home** | Reads Home Assistant's service list. It installs nothing. |
| **Failure** | Not installed: `The fixture integration isn't in Home Assistant yet. That's your step, once.` Then **Check again**. |

### 4.1 Unadopted

Two ways in:
- **A socket that isn't robotic.** Its row says `F2 · Hue · Corner bulb` (or `bulb not mapped`), with **Adopt a fixture
  here**.
- **A board that isn't bound.** It's paired and answering, but no socket has it. The capsule shows `New fixture found`,
  and the row in the flow says `Board 2 · not in a socket yet`.

The firmware doesn't know it's unadopted: this state is Home's alone.

### 4.2 Discovering

| | |
|---|---|
| **Shows** | `Power the fixture near the den, then open joining in Home Assistant (Zigbee → Add device).` A slow ring breathes at the chosen socket. Then a list of what has turned up: new C12 boards, and already-adopted ones greyed out. |
| **Owner, by hand** | Powers the fixture where it reaches the mesh (on a table is fine: it's mounted later). Opens joining in Home Assistant (B2). |
| **Home** | Watches Home Assistant's device registry for `EngineeredLight` / `c12-v2` (B3). It reads; it never opens joining itself. |
| **Failure** | After 3 minutes with nothing new: `Nothing new yet.` Then a checklist (is joining open, is the fixture powered, is it within a few metres of a mains-powered Zigbee light), and **Keep looking**. |

### 4.3 Found: identity and record

**Home calls `c12.get_profile` once** and shows:
- `Robotic fixture · board 2`;
- `firmware build 2031 · profile 3fa2` (short);
- its capabilities in words: `timed moves · fades · joins a flight · moves at rest`;
- `last heard 2 s ago`.

| `get_profile` result | Screen | Recovery |
|---|---|---|
| `ok`, record valid | Continue | — |
| `no_record` or `record_invalid` | **Not commissioned.** `It works as nine ordinary lights, but its spot and motion stay off. Commission it at the bench, then come back.` | Bench commissioning in the one app (§3A of the handoff). Home can't fix it. **Leave it for now** keeps it unbound. |
| `no_answer` | `It joined but isn't answering yet.` Home asks again 3 times, 5 s apart. | **Check again**. If it never answers: power-cycle it. |
| `capabilities` null, or `contract_version` ≠ 1 | **Firmware update needed.** `Home needs firmware v2-b or later.` | At the bench, today. Zigbee OTA is planned, not built. |
| Same board number, different MAC, as an adopted board | `Two boards say they're board 2.` | A bench visit to renumber one. Home adopts neither until then. |
| Same board number and MAC as an adopted board, new HA device id | `This is F3's fixture, paired again.` **Reconnect to F3.** | Updates the HA device id only. No re-adoption, unless `profile_id` or `mount_revision` changed (§5). |

**"Which one is this?"** offers **Pulse its room light**, the owner's press. It pulses endpoint 1, the room light, at
a low level: an ordinary Zigbee light change. Nothing moves, and the spot stays dark. If the firmware later answers
Zigbee Identify (owner question 8), the button uses that instead.

### 4.4 Binding to a socket

| | |
|---|---|
| **Shows** | A plan of the den's four sockets, from above (abstract, no measurements), each with what it holds now: `Hue · Corner bulb`, `Hue · bulb not mapped`, `Robotic · Desk spot`, `Empty`. If the coordinator's coverage report has run (coordinator A6), each free socket shows `lights the sofa from 7 of 9 seats`. The recommended socket is marked. |
| **Owner** | Picks the socket. Then answers **Which bulb is leaving F3?**, from den_lighting's lights not yet placed, or **It's empty already**. That answers the bulb map for this socket (owner question 1). |
| **Home** | Draws the choice in the room: the socket glows, and the leaving bulb is outlined. It writes nothing yet: the choice goes into the draft. |
| **Failure** | A socket that's already robotic isn't offered. To move a fixture, take it down first (§5, Removing). If the bulb map says this bulb is in another socket: `The map says the Reading bulb is in F2. Which is right?` The answer fixes the map. |

### 4.5 Mounting (the owner's hands)

A checklist. Each line is the owner's tick, and Home checks what it can.

1. **Make the socket safe** (switch or breaker, as the owner does today). If the socket is on a wall switch (owner
   question 6), the row says so from then on, so "offline" can mean "switched off at the wall".
2. **Take the bulb out.** Home watches Home Assistant: `Reading bulb · unavailable ✓`.
   - If a different bulb goes unavailable: `That was the Corner bulb. Is this socket F1?` The answer fixes the map.
   - If nothing goes: `Home still hears the Reading bulb. Is it out?` Then **It's out** (it may be on another power
     path) or **Back**.
3. **Mount it level, front to the arrow.**
   - The room shows an arrow at the socket: the way its front should face, from the socket's modelled yaw.
   - The equipment record models yaw only, so a tilted or rolled mount isn't modelled.
   - **It faces another way** turns the arrow (in 15° steps, or tap where the front points). Home records the yaw in
     the boards store, against the board's `mount_revision`.
4. **Power on.** Home waits for it to answer again, with `state` 2 (ready).

**The swap starts at tick 2.** Home writes the socket as `robotic · calibrating` (a new equipment revision). From then
on, the coordinator leaves F3 out of every job, and the other sockets cover its room light.

**Cancel after tick 2 is "Put the bulb back"**: the socket goes back to `hue` with its bulb. Leaving the flow open
keeps the socket `calibrating`. The row says `F3 · swap in progress · Continue`.

**Failure:** it doesn't answer within 2 minutes of power-on. That's the offline screen (§5), with **Keep waiting**.

### 4.6 Homing (rest pose and zero)

There's no homing switch and no homing move. Zero is a number in the bench record. At every boot the board checks that
its motors are the ones in the record, and that each reads inside its range.

| | |
|---|---|
| **Shows** | `Zero: set at the bench (profile 3fa2) ✓ · Motors: the same as at the bench ✓ · Both axes in range ✓`. In the room, the orb draws its rest pose (tilt level, pan to its front) as a faint arrow. |
| **Owner** | Nothing here. The head's actual rest pose is checked by eye at the first dark test move (§4.11), after the go. |
| **Home** | Reads `state` and `attention`. |
| **Failure** | `attention` 2, `motor_identity_mismatch`: `Its motors aren't the ones the bench recorded.` `attention` 3, `out_of_range`: `An axis reads outside its range.` Either way it's a bench visit, to the one app's recovery screen. `state` 1: not commissioned (§4.3). |

### 4.7 Limits and capabilities

**Pan and tilt.** One dial per axis, with three arcs:
- the board's range (the record's `joint_min_cdeg` and `joint_max_cdeg`);
- the same range inset by `host_inset_cdeg` (5°): that's what the host may use;
- Home's equipment limits for this socket.

**Rule: Home's limits sit inside the board's inset range.** The board's limits are the physical truth.
- If Home's reach past them: `Home's limits for F3 go past what this fixture can turn.` **Use the fixture's** is the
  default, and the only way to continue.
- **Narrow by hand** drags either end in, for example to keep the beam off a doorway. It never goes past the inset.

**Capabilities, in words, from the record:**
- `lit travel: no` (until `lit_motion` = 1);
- `retargets mid-move: no`;
- `timed moves · fades · joins a flight · moves at rest`;
- `spot warmth: one fixed white until its warmth map is measured`;
- `room light: zones Z1–Z6 · downlight Z7 (unconfirmed)`, from `room_light_zones`.

**Assumed values, said plainly:**
- `Beam width: assumed 20° · not measured`;
- `Light output: assumed · not measured`;
- `Motion: bench placeholders` (until the shoot-out measures this board).

**Enter bench measurements** takes the beam FWHM and lux at set distances (owner question 10) into the boards store.

**Failure:** a record whose limits are missing or inverted is treated as not commissioned.

### 4.8 Naming

- One field, with a suggestion from the socket, such as `Sofa spot`. The owner edits it (B10).
- Home shows `F3 · Sofa spot` everywhere. The board fixture number shows only in the fixture's details, for matching
  it at the bench.
- Home doesn't rename the device in Home Assistant. The owner can, by hand.

### 4.9 Review and adopt

**A summary:**
- the socket and the bulb it replaces;
- the name;
- the board, firmware and profile;
- the limits;
- the capabilities;
- the assumed values.

**What changes, in one paragraph:** `From now on the coordinator plans F3 as a robotic fixture. It's the task light
when it can serve you, and its radials and downlight join the room glow. The other three bulbs become fill, accent and
glow. Nothing is sent to F3 until the go.`

**Adopt into F3** writes the socket `robotic · adopted · physical off`, a new equipment revision (B11).

**Done:**
- the room's orb at F3 takes its kind;
- the label reads `F3 · Sofa spot · adopted · virtual until the go`;
- one line points ahead: `Next: the go. Bench sittings 2–4 for board 2, then your word.`

**Failure:** a revision conflict (someone else saved). `The den's setup changed while you were adopting.` **Review
again** reloads and keeps the draft. The write is never retried by itself.

### 4.10 The go: three locks (Go live, per socket)

The Go live card lists the locks. Each one says whether it's open.

| Lock | Open when | Who opens it |
|---|---|---|
| **1. Bench sittings 2–4** | Commissioning (the record is valid), the dark shoot-out, and the lit bench test (the record's `lit_motion` = 1). Home reads the first and third from the board. The shoot-out is recorded with its date. | The owner, at the bench |
| **2. The program's physical transport** | den-live's C12 transport is on: the den program's standing rule is that it stays off until the owner directs otherwise. Home reads it from the coordinator's state. | The owner's go, carried out by the spine's reviewed install |
| **3. This socket's `physical`** | **off → tests:** the owner's own presses only (dark test moves, Stop and Resume, the aim check). The coordinator still plans F3 virtually. **tests → on:** the coordinator drives F3, which needs the aim check passed. | The owner, here. Each step up is the owner's hold-to-confirm, enabled only when the locks above are open. Each step down is one tap, at once. |

**Before the go,** lock 2 is shut. The card says `Waits for the go` and its buttons are disabled. This ships turned
off: phase 2 sits behind its own flag, which needs the owner's separate approval.

Any Stop drops nothing: the locks stay where they are, and the board latches Stop by itself.

### 4.11 Dark test moves (B9; physical = tests)

Each is the owner's press. Home sends one at a time, as `source: hand`, never repeats one, and stays under the radio
budget (2 commands a second per fixture, Stop included).
1. **Go to rest, dark.** Pan 0, tilt 0. Then the owner checks by eye: `Is the head level, facing its front?`
   - **Yes.** That checks the zero and the mount yaw together.
   - **No.** `Turned` (Home asks how far, and re-records the mount yaw) or `Not level` (a bench visit).
2. **Turn left 30°, dark**, then **Tilt down 30°, dark**, then back to rest. Each shows its predicted time (the ACK's
   `predicted_ms`), then `done` with the measured time, from `c12_move_done`.
3. **The Stop drill.** The owner presses Stop: Home's, or Home Assistant's. Home shows `Stopped · the spot is dark ·
   Stop survives a reboot`. Then the owner presses Resume.

**Pass:** each move done, Stop latched, Resume answered.

**Failure:**
- a refusal (`out_of_range`, `needs_commissioning`, `stopped`) is shown in words, and nothing is retried;
- a timeout: `No answer from F3`, which goes to offline;
- a fault goes to its fault words (§5).

Afterwards, Home ends its hand hold, so the coordinator may plan F3 again.

### 4.12 The aim check: a spot lit at rest (B6; physical = tests)

**The owner prepares:**
- the den lit as usual, so the camera sees;
- nobody in the beam's path;
- the owner beside it, out of the beam.

**The owner picks two targets** by tapping visible surfaces in the room. Two, because one target can't tell a turned
mount from an offset socket:
- one where the fixture serves the sofa;
- one off to its side.

Home checks each against the limits before offering it.

**Run aim check** does this for each target:
1. a dark move to the aim, and its `move_done`;
2. a MOVE to the same aim, lit at the chosen level (30% by default), at once, at rest, as `source: hand`;
3. den-live's aim check (`aim_check.py`, workstream 02) takes 5 frames and finds the spot's centre in each;
4. the spot goes dark, and Home ends the hand hold.

**The result** is drawn as a target, not a picture:
- the predicted point at the centre;
- a dashed circle at half the beam's FWHM radius at that throw (the FWHM is assumed 20° until it's measured);
- five dots for the frames: `4 of 5 within · off by 3 cm`.

No frame is shown or leaves LA.

**Pass:** at least 4 of 5 frames within, on both targets. The socket's `aim_check` becomes `passed` (a new revision),
and **Let the coordinator drive F3** is enabled (tests → on).

**Failure:**

| What | Says | Recovery |
|---|---|---|
| Both targets off the same way | `Both landed 3° to the left: the mount is turned.` | **Correct the yaw by 3°** re-records it and runs again |
| Spread, not offset | `The spot moved around: 2 of 5 within.` | **Run again.** If it repeats, the beam width may be wrong (assumed): measure it at the bench |
| Spot not found | `The camera couldn't find the spot.` | Choose another target, or lower the room light |
| A person came into view | `Someone walked in: stopped and dark.` | Run again when the beam's path is clear |
| Stop pressed | `Stopped.` | Resume is the owner's |

### 4.13 Live (physical = on)

The owner's hold-to-confirm opens **Let the coordinator drive F3**. The label loses its `virtual` tag and reads
`F3 · Sofa spot · live`. Lit moves still need the record's `lit_motion` and the coordinator's eye-safety check. Until
then the board travels dark, whatever is asked.

## 5. After adoption: the states

The coordinator's socket `state` is in brackets; it decides whether a socket is planned. **The words are Home's.**

| State | Room (orb) | Row | Recovery |
|---|---|---|---|
| **Adopted · virtual until the go** (adopted) | The orb, with the coordinator's light drawn | `F3 · Sofa spot · task light · virtual` | The go (§4.10) |
| **Adopted · tests** (adopted) | As above | `· tests` | — |
| **Live** (adopted) | As above | `· live` | — |
| **Held by hand** (adopted; the coordinator passes it by) | The solid ice ring (the orb WIP's held mark) | `in your hands · Return to auto` | Home's existing hand holds. The board flags an EP 9 or hand MOVE. A change to the radials or downlight (EP 1–8) isn't flagged by the board: Home detects it as it does for a Hue bulb. |
| **Stopped** (adopted; dark until Resume) | The core dims and a small square sits under it | `Stopped · Resume` | **Resume is always the owner's press** (owner question 12). Stop survives a reboot. |
| **Offline** (offline) | A hollow dashed ring, no glow | `Offline since 21:40 · check its power` (on a wall switch: `· or the wall switch`) | It rejoins by itself, retrying every second (untested on a board). If it left the network: pair it again. Home knows it by board number and MAC, so **Reconnect to F3** needs no re-adoption. |
| **Fault** (fault) | The amber attention diamond on the orb | The fault in words, from `contracts/c12v2/zcl-fc13.md` | A motion fault (13–19): dark and holding, **Resume** after the owner checks it. A light fault: dark until a power cycle. Over temperature: the spot is out until it cools 2 °C below the trip. Anything else: a bench visit. |
| **Motors changed or out of range** (fault) | Diamond | `Needs a bench visit: its motors aren't the ones recorded` | The one app's recovery screen |
| **Not commissioned** (fault) | Diamond | `Needs commissioning` | The bench |
| **Needs a fresh aim check** (adopted, physical held at tests) | Orb, with a small amber dot | `Aim check out of date` | Triggered by a changed `profile_id` or `mount_revision`. Home re-reads the profile and re-checks the limits by itself. Physical drops from on to tests until the aim check passes again. Virtual planning and the room light go on (owner question 9). |
| **Firmware update needed** (fault) | Diamond | `Firmware too old for Home` | At the bench today. OTA is planned (P7). |
| **Updating** (offline) | Hollow ring, `Updating` | `Updating · dark and still` | Wait |

**Removing a fixture (robotic → Hue, or empty).** **Take it down** runs the mounting checklist backwards:
- the fixture is unpowered;
- the bulb goes back (Home sees it answer);
- the socket is written `hue` with its bulb, or `empty`.

The coordinator carries the socket's learned glow back to the bulb (coordinator A4). The board keeps its entry in the
boards store, with the beam, lux and history, so re-adopting it elsewhere is short. Its mount yaw resets.

**Who reads the boards in the steady state.** Every read counts against the radio budget, so there is one reader:
- **Once adopted,** den-live reads `state`, `attention`, `fault_code` and `stop_latched` about every 5 s, because the
  coordinator needs them anyway, and publishes them in the coordinator's state. Home shows that.
- **During the flow,** Home's gateway reads the board directly, no faster than once a second.
- Attribute reporting isn't configured yet (a firmware open item), so both read, never wait for reports.

## 6. The mixed den in Home

### 6.1 Each socket drawn by kind

The fixture-orb design chat owns the look. This spec owns what each look must say. Robotic and Hue have to be told
apart at a glance, in both themes, and at phone size.

| Kind | In the room | Label (Lighting view, hover or tap; always in the Controls list) |
|---|---|---|
| **Robotic** | The orb: a small core, the glow tinted by its channels and grown by its light, the beam when lit, and the WIP's marks (dashed warm ring = aimed and waiting dark; solid ice ring = in your hands) | `F3 · Sofa spot · task light 70%` plus the `virtual` tag until the go |
| **Hue** | **A bulb's glow:** no core, no beam, a softer and wider warm disc at the socket, sized by the bulb's level. While Bulbs is off, the bulb plan is a shadow: the disc becomes a dashed outline at the size it *would* be. | `F1 · Corner bulb · fill · would set 35%` |
| **Empty** | A small dotted ring at the socket | `F2 · empty · swap in progress` |
| **Offline or fault** | The robotic orb, its core grey and no glow, with a hollow ring or the attention diamond | `F4 · offline` |

**Role** is the label's last part. It comes from den-live's new `sockets{F: {kind, state, role}}` in
`den-virtual-spots-1`; Home never infers it. A role that moves between sockets as the reader turns crossfades in the
light. In the label, the word just changes.

### 6.2 The mixes

A reader on the sofa, typical roles (the coordinator plan, §3). Any socket can be the robotic one, and the tests cover
every subset (§9).

| Mix | Robotic sockets | Hue sockets | When no fixture can serve the reader |
|---|---|---|---|
| **0 + 4** (today) | — | glow; fill from the bulb in front; the bulb behind is the reading light | — |
| **1 + 3** | task light; its radials and downlight join the glow | fill (in front, soft); accent (nearest labelled art, warm and low); glow (the rest) | The fixture rests or glows. **The bulb behind the reader becomes the reading light**, up to its house level. |
| **2 + 2** | task light; standby (aimed and dark, for a crossfade) | fill; glow | The fixtures rest and glow. A bulb behind the reader, if there is one, becomes the reading light. |
| **3 + 1** | task light; standby; accent on art (or pre-aim) | fill or glow, by the reader's facing | The same, if the last bulb is behind the reader |
| **4 + 0** | task light; standby; accent; pre-aim | — | The fixtures' radials and downlights carry the room |

**Before the first adoption, today's view stays.** Today the coordinator plans four virtual fixtures. With every
socket `hue`, the installed view would show no spots at all. So den-live's **Rehearse the full set** setting (proposed)
is on while no socket is robotic: the coordinator plans, and Home draws, the four virtual fixtures, as now.
- From the first adoption it's off: Home shows what's installed (owner question 13).
- The owner can turn it on at any time, for testing.
- While it's on, the capsule says `Rehearsing all four · not what's installed`.

### 6.3 The coordinator panel

**A new Sockets section, first in the drawer.** One row per socket:

```
F1  Hue       Corner bulb     fill · would set 35% · 2700 K
F2  Hue       Sofa bulb       glow · would set 20%
F3  Robotic   Sofa spot       task light · 70% · 2700 K · virtual
F4  Hue       bulb not mapped glow · den_lighting's own rules
Assumed for F3: beam 20°, light output, motion (bench placeholders)
```

- **Bulbs** folds into Sockets: each bulb line is keyed by its socket.
- **Why** stays as it is, and names sockets with their names.
- **Learned** stays.

**The summary line** gains the mix: `Lighting coordinator · On · Reading · 1 robotic, 3 Hue`.

### 6.4 The Controls list

- The header's `Modeled · Not commissioned` badges become the mix (`1 robotic · 3 Hue`) and, while any socket's
  physical isn't on, `Virtual until the go`.
- Each row has its kind's mark, its name, its role and its state.
- A Hue row opens the bulb's control, through den_lighting and Home Assistant, as the bulb's own control does. A
  robotic row opens the inspector.
- `bulb not mapped` rows offer **Map the bulbs**: four rows, each with **Pulse** (the owner's press) to find which bulb
  is where. That's plan 04's two-minute step 6.

### 6.5 Where "virtual until the go" shows

1. The robotic orb's label: a `virtual` tag.
2. Each robotic row in the Controls list and in the coordinator's Sockets section.
3. The Controls header's badge.
4. The adoption's last screen: `adopted · virtual until the go`.
5. The socket's Go live card, with its three locks.
6. The coordinator's actuation line, unchanged: `Moving the virtual spots in Home`.

It goes away for a socket only at `physical: on` (§4.13).

## 7. Safety rules Home keeps

- **Nothing that moves the board or lights its spot before the go:**
  - before lock 3 is at tests, Home's gateway refuses any `c12.move`, `c12.keep_lit` or `c12.resume` for that board;
  - it allows only `c12.get_profile`, attribute reads, `c12.stop`, and the room light's pulse;
  - the refusal is in the gateway, not just the button.
- **Stop always works:** in every screen of Go live, and from Home Assistant. It never waits behind another command.
- **Every move is the owner's press,** one at a time, as `source: hand`. Home never chains, retries or repeats a
  command, and never resumes on its own.
- **Never on endpoint 9** for anything the coordinator does. EP 9 is the hand path.
- **No lit moves** (`lit_during_move`) from Home, ever. The aim check lights only at rest.
- **The board's limits win.** Home's limits are always inside them. The board refuses the rest anyway.
- **Every write is one compare-and-swap** with its own request id, never retried by itself. An unknown outcome is
  checked against its receipt.
- **No private data leaves:**
  - no frames from the aim check, only the within or out per frame;
  - no MAC or IEEE address shown whole;
  - everything said in words goes in through `textContent`.

## 8. Owner questions, merged

The firmware chat's 12 (F1–F12) and the coordinator chat's 5 (C1–C5), de-duplicated, with the default Home would build.

| # | Question | From | Recommended default |
|---|---|---|---|
| 1 | **The socket map and the order.** Which bulb is in which socket today? Which socket gets each fixture, in what order? | F1, C1, C2 | **Light 1 into F3**, as planned, unless the coverage report clearly favours F2 (they're near a tie). After that, the next socket the coverage report picks. **The bulb map is answered as you go:** at the binding step ("which bulb is leaving"), confirmed when that bulb goes unavailable. **Map the bulbs** does the rest in two minutes. |
| 2 | **Names.** Who names a fixture? Does Home show the board fixture number? | F2 | You name it; Home suggests one from the socket (`Sofa spot`). Home shows `F3 · Sofa spot`. The board number appears only in the fixture's details, for matching at the bench. Home doesn't rename the device in Home Assistant. |
| 3 | **Owners.** Who owns the ceiling mount and the per-socket aim check? | F3 | You mount, by hand. Workstream 02 owns the aim check's code and its pass rule. Production Home owns the screens, and you press Run. The spine assigns fixtures 2–4. |
| 4 | **Where adoption runs.** Home at the den, with bench commissioning in the one app? Or should Home show bench progress? | F5 | Home at the den. Bench commissioning stays in the one app. Home shows only the bench's result: record valid, profile, `lit_motion`, and the dates of sittings 2–4. |
| 5 | **The downlight.** Is zone Z7 the downlight on every fixture? Should the room light (EP 1) include it? | F4 | Z7 counts as the downlight only once the bench confirms it per fixture. EP 1 leaves it out, so the coordinator can use the downlight on its own. Home reads `room_light_zones` from the record and never assumes. |
| 6 | **Wall switches.** Is any socket on a wall switch? | F6 | None assumed. If one is, the mounting step marks it, and "offline" on that socket says `or the wall switch`. |
| 7 | **The aim check.** What to aim at, at what level, who's in the room, and should its moves be `hand`? | F7 | Two targets: a visible flat surface where it serves the sofa, and one off to its side. Never a seat or a person. The spot lit at rest at 30%. Nobody in the beam's path, and you beside it. Sent as `hand`, so the coordinator leaves it alone, with the hand hold ended after. |
| 8 | **"Which one is this?"** Should the firmware answer Zigbee Identify, for example with a slow room-light pulse? | F8 | Yes, a small firmware change. Until then, **Pulse its room light** (your press) is an ordinary light change on EP 1: nothing moves, and the spot stays dark. |
| 9 | **Re-commissioning.** When `profile_id` or `mount_revision` changes, must Home re-run the aim check? Should it take the fixture from the coordinator until then? | F9 | Home re-reads the profile and re-checks the limits by itself. The aim check is marked out of date, and physical drops from on to tests until it passes again. Virtual planning and the room light go on. |
| 10 | **Beam angle and lux at distance.** Measure them at the bench before the ceiling? Who records them, and where? | F10 | Yes, at the bench: a lux meter on axis at 1, 2 and 3 m, and the beam's half-maximum edge. You enter them in Home's **Enter bench measurements**. They're kept in Home's boards store, keyed by board number and MAC. Until then, `assumed 20°` everywhere. |
| 11 | **Firmware updates.** From Home over Zigbee once OTA exists, or always at the bench? | F11 | At the bench, until OTA is built and passes its trial (A13). Then Home may offer it as your press: one fixture at a time, taken from the coordinator first, dark and still while it updates. |
| 12 | **Resume.** May Home ever resume a stopped fixture by itself? | F12 | Never. Resume is always your press. |
| 13 | **Simulate the full set while only some are robotic?** | C3 | Home shows what's installed. **Rehearse the full set** (a coordinator setting) brings back the four virtual fixtures for testing, clearly labelled. **Before the first adoption it stays on,** so today's view is unchanged until fixture 1. |
| 14 | **The fallback reading light.** May the bulb behind you go brighter than its usual level when no spot can serve? | C4 | Up to its house level. Beyond that only if you've taught it to. |
| 15 | **Fill and accent.** Do you want them at all? | C5 | A soft fill from the bulb in front of you, and an accent only on labelled art. |

**Two more, from this design:**

| # | Question | Recommended default |
|---|---|---|
| 16 | **Where the per-socket go lives.** Should a Home button open each socket's physical lock (off → tests → on)? | Yes, as your hold-to-confirm. It's enabled only when the bench sittings and the program's transport are open (§4.10). It ships disabled behind its own flag until you approve phase 2. |
| 17 | **Who may adopt.** | Signed in only. Look-only views show the sockets and their states, and send nothing. |

## 9. Build steps (later), owners and tests

None of this starts without the spine's word. Home PRs follow the repo's rules: a `changes/unreleased/` fragment, the
private-data scan, and no push or merge without the spine.

### Production Home (the production chat)

| Step | What | Tests |
|---|---|---|
| **H1** | The socket record: equipment document version 2 (`sockets`), its validator, the one-time migration (every socket `hue`, unmapped), and the geometry export carrying every socket. Plus the boards store (compare-and-swap, private folder). | Validator: each kind's fields, exact-field refusals, `physical` per socket, assumed flags. Store: a revision conflict is a 409 and is never retried; a migration from revision 3 is idempotent. Export: every socket present, only robotic sockets as spot rows, the revision and digest current. |
| **H2** | The read path for adoption, in the gateway: Home Assistant's service list, the device registry filtered to `EngineeredLight` / `c12-v2`, `c12.get_profile`, attribute reads. At most one a second per board. **The command guard:** refuse move, keep-lit and resume while a socket's physical is `off`. | A fake Home Assistant for each `get_profile` result (`ok`, `no_record`, `record_invalid`, `no_answer`), old firmware, a duplicate board number, re-pairing. The rate cap. **The guard test:** with physical `off`, every motion or lit service is refused at the gateway, and Stop passes. |
| **H3** | The flow, phase 1 (§4.0–4.9): a pure model (`home-den-adoption-model.mjs`: steps, draft, failure transitions) and the sheet. Behind `HOME_WEB_DEN_ADOPTION`, off by default. | Model: every step's success and failure path, and resuming a draft; "put the bulb back" after the swap starts; the bulb-map corrections. Browser, desktop and phone, with a fake Home Assistant: the whole flow sends nothing but reads; look-only has no buttons; text goes in through `textContent`. |
| **H4** | The mixed den (coordinator A5): drawing by kind and role (with the orb chat's look), the Controls list, the coordinator panel's Sockets section, **Map the bulbs**, and the "virtual until the go" placements. | Every mix (0+4, 1+3, 2+2, 3+1, 4+0) with every subset of F1–F4 as the robotic ones. Roles only from `sockets{}`. Offline, fault and empty drawing. The `virtual` tag present wherever physical isn't on. A rehearsal banner while rehearsing. Phone sizes in both themes. |
| **H5** | Fixture health: states and faults in words (§5); "needs a fresh aim check" on a changed `profile_id` or `mount_revision`; reconnect after re-pairing; firmware too old; taking a fixture down. | Each fault code group, from a fake coordinator state. Re-pairing keeps the adoption. A changed profile drops physical from on to tests. Taking a fixture down writes `hue` and keeps the board's entry. |
| **H6** | Phase 2 (§4.10–4.13): the Go live card and its locks, dark test moves, the Stop drill, the aim-check screen, and the physical steps. Behind its own flag, off, **built only after the owner approves phase 2.** | Every button disabled unless its locks are open. Hold-to-confirm. One command in flight; nothing retried; Resume never automatic; Stop always enabled. The aim-check result drawing from a fake den-live reply, with no frame in it. |

### den-live and the coordinator (the coordinator chat)

| Step | What | Tests |
|---|---|---|
| **A1** | Read the socket export from den-live's state folder; re-bind on a new equipment revision without a restart. | Rows rebuilt; leases and holds dropped on changed sockets only; learning kept; a stale revision refused. |
| **A1b** (new) | `sockets{F: {kind, state, role}}` in `den-virtual-spots-1` (contract 0/4); boards' health in the coordinator state; the **Rehearse the full set** setting (on while no socket is robotic). | Snapshot shape per mix; rehearse on and off; health refreshed at about 5 s and never faster than the budget. |
| **A2–A4** | As the coordinator plan has them: aim jobs on robotic sockets only, room roles and the fallback reading light, learning carried over. | The plan's tests over every mix. |
| **A6** | The coverage report, published in the coordinator state, so Home's binding step can show it. | Shares per socket from registered geometry; offline. |
| **D1** (phase 2) | den-live routes for the owner's hand moves and the aim check, behind the physical transport, refusing while a socket isn't at tests. | Refusals per lock; one command in flight; `source: hand`. |

### Perception and calibration (workstream 02)

| Step | What | Tests |
|---|---|---|
| **P1** (phase 2) | `aim_check.py` as a den-live call: two targets, 5 frames each, the result per frame (within or out, the offset), and a yaw-or-offset verdict. No frames leave LA. | A synthetic spot set: pass, a turned mount, scatter, spot not found, a person in view. |

### The firmware chat

| Step | What |
|---|---|
| **W1** | Answer Zigbee Identify with a slow room-light pulse (owner question 8). |
| **W2** | Attribute reporting for 0xFC13 (FW-5), so health isn't stale. |
| **W3** | The quirk and integration install with the owner (FW-5): what §4.0 checks for. |

### The spine

| Step | What |
|---|---|
| **S1** | Contracts 1, 0/4 and 5 updated: the socket record, `physical` per socket, `sockets{}` in the snapshot, the trace header from adopted profiles. |
| **S2** | verify-producer follows the robotic count (coordinator A7). |
| **S3** | The go, per fixture, with the owner: lock 2's install. |

### Lighting direction

| Step | What |
|---|---|
| **L1** | How each role is played: the fill's ratio, accent levels, and the crossfade when a role moves between sockets (contract 7). |

**The order that works:** H1 + A1 + S1 → H2 → H3 → A1b + H4 (the owner can adopt fixture 1 virtually here) → H5 → the
bench sittings → the go → D1 + P1 + H6.

## 10. Privacy

- The mockup and every test use an invented den, invented names and synthetic readings.
- No camera frame, face, network address, token or room coordinate is shown, stored in git, or sent anywhere.
- The MAC and IEEE address live only in the private boards store.
- The aim check returns within or out per frame, and an offset in centimetres. It never returns an image.
