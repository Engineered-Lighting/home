Fix adding a person, which failed for every caller.

`POST /v1/household-person` bound `subject_person_id` and `predicate` — fields
belonging to the *partner* kernel call — onto the owner-person kernel call, whose
dataclass has neither. Those dataclasses use `slots=True`, so the request raised
`AttributeError` before any SQL ran, and because `AttributeError` is not a
`DBAPIError` the adapter's retry and error mapping did not catch it either.

The SQL never referenced the two parameters, so removing them is the whole fix.
A new structural test asserts that every `value.<field>` a kernel call reads
exists on its dataclass, which covers the other kernels against the same
copy-paste.
