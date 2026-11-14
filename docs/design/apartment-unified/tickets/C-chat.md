# C · Chat

The existing Home chat in the right column, bound to the room.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-C01 · Chat in the column
**Depends on:** APT-S04
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&col=chat`, ui-chat.jsx
**Spec:** SPEC.md › Home chat
**Likely files:** home-events.jsx, home-app.jsx

**Build**
- Mount the existing chat feed, cards, slash dispatcher, voice, stop, preference review and Travel Mode in the Chat tab. Take only layout from the prototype.
- Proactive messages set the unread dot on Chat.

**Done when**
- [ ] Every chat feature from before works in the tab.
- [ ] Travel Mode still blocks all light output.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-C02 · Entity chips
**Depends on:** APT-C01, APT-R01
**Spec:** SPEC.md › Home chat

**Build**
- Lights and named aims in replies render as chips; hover highlights in the room, click selects.

**Done when**
- [ ] Chips resolve to the current home only.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-C03 · Selection as context
**Depends on:** APT-C01
**Spec:** SPEC.md › Home chat

**Build**
- Removable "About <selection>" chip sent with the message.

**Done when**
- [ ] "Brighter" with a light selected changes that light.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-C04 · Chat aim proposals
**Depends on:** APT-C01, APT-A04
**Spec:** SPEC.md › Home chat; SPEC.md › Aim lifecycle

**Build**
- Aim proposals from chat use origin "chat", preview in the room, and use the same Set aim and Discard path.

**Done when**
- [ ] Setting from the card or the room bar gives the same result.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-C05 · Composer and header
**Depends on:** APT-C01
**Spec:** SPEC.md › Home chat

**Build**
- Slash menu with keyboard completion, mic, Stop; header with explain last answer, copy transcript, new conversation.

**Done when**
- [ ] Existing slash commands unchanged.
- [ ] Tests added in tools/, existing suites green, change note added
