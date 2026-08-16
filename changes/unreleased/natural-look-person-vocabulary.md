---
title: Stop the look classifier missing people and alerting on furniture
target: desktop
type: fixed
---

The deep-look finding classifier decided whether a camera caption was worth
surfacing by matching posture words — "standing", "sitting" — and had no
word for a person. So a bicycle standing upright raised a top-priority
person alert, while "a man in a white t-shirt walks through a dining room"
was filed at importance 10, alongside the furniture, and never surfaced.

Measured on fifty real daylight frames from the five house cameras: six
captions describing a person were filed below alert level, three of them as
nothing-to-report, and two captions describing an empty living room raised a
person alert because of the bicycle.

The classifier now keys on human nouns instead. The posture words were
redundant whenever a person was actually named and harmful whenever one was
not; "walking" survives, since furniture does not walk. On the same fifty
frames this takes missed people from six to one and phantom alerts from two
to zero, while genuine model hallucinations on verified-empty frames still
classify as people, which is what the hallucination gate needs.

Cases are asserted on both the app classifier and its Python port so the
two cannot drift apart.
