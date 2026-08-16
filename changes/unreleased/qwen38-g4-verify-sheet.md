---
title: Make the negatives corpus actually reviewable
target: internal
type: fixed
---

The G4 verification step printed a file path per frame and asked for a
yes/no, which meant opening thirty-five JPEGs by hand in another window. It
now writes a local contact sheet: every frame on one page at a size you can
judge, annotated with each model's caption and outlined in red where a model
claimed to see something, since those are the frames that decide whether the
corpus is honest. Marking frames in the page builds the command that records
the verdicts, and `--reject` / `--accept` also work directly.

The sheet is a plain file with relative image paths and no external
references. These are interior photographs of the owner's home, and nothing
about reviewing them sends them anywhere.

The sheet is also self-contained and written into the home directory. The
browser on this host is a snap, and snap confinement blocks it from reading
anything outside the home directory, so a sheet under `/srv` with relative
image paths could not be opened at all.
