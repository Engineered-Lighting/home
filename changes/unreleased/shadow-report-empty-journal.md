# Shadow report: refuse a week that wrote nothing

`tools/shadow-report.py` carries the acceptance verdict for the seven shadow
nights. Pointed at a journal directory holding no file at all, it reported zero
unexplained latches, printed "the acceptance bar is zero over seven nights",
and exited 0.

That is the exact signature of the failure the publisher is most likely to hit:
the journal never raises on I/O failure, by design, because a full disk must
not stop the house being lit. An empty directory is therefore the only way a
volume the container cannot write ever announces itself, and the report was
answering it with a success code.

It now refuses a directory with no journal file, naming the likely cause, the
same way it already refuses person evidence nobody supplied. `--no-journal`
acknowledges an empty run for anyone who wants the receipt anyway.

A range with no record is left alone: asking about a day the publisher was not
running is an ordinary empty answer, and the directory still holds the week's
files. The two cases are now told apart by whether any journal file exists at
all rather than by how many fell inside the range.
