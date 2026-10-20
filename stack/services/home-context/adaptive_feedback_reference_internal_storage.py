"""Explicit fictional R71 origin-request storage; original wire stays exact.

No public representation, authority/FIRST cache, constructor rerun, migration,
clock callback or quota change. The selected helper only encodes immutable
origin request text and verifies its complete lossless recovery.
"""
import copy
import hashlib
import importlib
import json
import re
import struct
import weakref
import zlib
from pathlib import Path

import adaptive_feedback_validation as base
import adaptive_feedback_reference_validation as reference
import adaptive_feedback_reference_phase_views as phase
import journal as journal_module

INTERFACE_VERSION = "home-adaptive-feedback-reference-internal-storage-inprocess-1"
MODE = "origin_request_zlib_1"
CODEC_VERSION = 1
REFERENCE_PROFILE = reference.REFERENCE_PROFILE
MAGIC = b"HOME-R71-ORIGIN-REQ-ZLIB-1\0"
MAX_BYTES = base.MAXIMUM_REQUEST_BYTES
_HEADER_BYTES = len(MAGIC) + 4 + 32
_HERE = Path(__file__).resolve().parent
SOURCE_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_SOURCES = {
    name: hashlib.sha256((_HERE / name).read_bytes()).hexdigest()
    for name in ("adaptive_feedback_reference_internal_storage.py", "adaptive_feedback_validation.py",
                 "adaptive_feedback_reference_validation.py", "adaptive_feedback_reference_phase_views.py", "journal.py", "schema_registry.py")
}
_BINDINGS = weakref.WeakKeyDictionary()
_REFERENCE_CLASS = reference.AdaptiveFeedbackReferenceProfile
_PHASE_CLASS = phase._InvocationPhaseViewProfile
_PHASE_GUARD = _PHASE_CLASS._commissioned_clock
_BASE_CLASS = base.PrivateOriginLedger
_ENTRY_NAMES = frozenset(("lookup", "retry", "remember", "_encode_request_storage", "_decode_request_storage"))
_STATE_NAMES = frozenset(("connection", "maximum_origins"))
_SEALED_NAMES = frozenset(("__getattribute__", "__setattr__", "__delattr__", "__hash__", "__eq__",
                           "__bases__", "__class__", "__dict__", "__slots__", "__getattr__"))
_PIN_NAMES = tuple(sorted(_ENTRY_NAMES | _STATE_NAMES | _SEALED_NAMES))
_MISSING = object()


def _static_method(cls, name):
    """Read the compiled class shape without invoking substituted descriptors."""
    for parent in type.__getattribute__(cls, "__mro__"):
        namespace = type.__getattribute__(parent, "__dict__")
        if name in namespace:
            return namespace[name]
    return _MISSING


_BASE_METHODS = tuple(_static_method(_BASE_CLASS, name) for name in _PIN_NAMES)
_BASE_LOOKUP = _static_method(_BASE_CLASS, "lookup")
_BASE_RETRY = _static_method(_BASE_CLASS, "retry")
_BASE_REMEMBER = _static_method(_BASE_CLASS, "remember")


def selection_configuration():
    return {"interface_version": INTERFACE_VERSION, "mode": MODE, "profile": REFERENCE_PROFILE,
            "source_digest": SOURCE_DIGEST, "codec_version": CODEC_VERSION}


def _fail(code="origin_storage_corrupt"):
    raise journal_module.JournalError(code, "selected immutable origin storage could not be verified", 409)


def _source_guard():
    try:
        if any(hashlib.sha256((_HERE / name).read_bytes()).hexdigest() != pin for name, pin in _SOURCES.items()):
            _fail("origin_storage_source_changed")
    except OSError:
        _fail("origin_storage_source_changed")
    if (hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != SOURCE_DIGEST
            or base.PrivateOriginLedger is not _BASE_CLASS
            or _OriginStorageLedger is not _ORIGIN_CLASS
            or any(_static_method(_ORIGIN_CLASS, name) is not method for name, method in zip(_PIN_NAMES, _ORIGIN_METHODS))
            or reference.AdaptiveFeedbackReferenceProfile is not _REFERENCE_CLASS
            or phase._InvocationPhaseViewProfile is not _PHASE_CLASS
            or _PHASE_CLASS._commissioned_clock is not _PHASE_GUARD
            or any(_static_method(_BASE_CLASS, name) is not method for name, method in zip(_PIN_NAMES, _BASE_METHODS))):
        _fail("origin_storage_source_changed")


def _journal_class_binding(journal):
    """Only the original class or the fixed diagnostic helper is admitted."""
    if type(journal) is journal_module.Journal:
        return {"class": journal_module.Journal, "transaction": journal_module.Journal._transaction,
                "diagnostic_module": None, "diagnostic_source_digest": None}
    try:
        diagnostic = importlib.import_module("fixture_storage_diagnostics")
    except ModuleNotFoundError as error:
        if error.name == "fixture_storage_diagnostics":
            raise ValueError("unsupported journal class for origin codec") from None
        raise
    fixed = _HERE / "fixture_storage_diagnostics.py"
    if (type(journal) is not diagnostic.StorageDiagnosticJournal
            or diagnostic.StorageDiagnosticJournal.__bases__ != (journal_module.Journal,)
            or Path(diagnostic.__file__).resolve() != fixed
            or hashlib.sha256(fixed.read_bytes()).hexdigest() != diagnostic.SOURCE_DIGEST):
        raise ValueError("fixed executing storage diagnostic journal required")
    return {"class": diagnostic.StorageDiagnosticJournal, "transaction": diagnostic.StorageDiagnosticJournal._transaction,
            "diagnostic_module": diagnostic, "diagnostic_source_digest": diagnostic.SOURCE_DIGEST}


def _canonical_raw(text, expected=None):
    if type(text) is not str:
        _fail()
    try:
        raw = text.encode("utf-8", errors="strict")
        if len(raw) > MAX_BYTES:
            _fail()
        value = base.snapshot_json(json.loads(text))
        if journal_module.canonical(value) != text:
            _fail()
        actual = hashlib.sha256(raw).hexdigest()
        if expected is not None and (type(expected) is not str or len(expected) != 64 or actual != expected):
            _fail()
        return raw, actual
    except (ValueError, TypeError, UnicodeError, RecursionError, journal_module.JournalError):
        _fail()


def _encode_value(text):
    raw, _ = _canonical_raw(text)
    frame = MAGIC + struct.pack(">I", len(raw)) + hashlib.sha256(raw).digest() + zlib.compress(raw, 9)
    return frame if len(frame) < len(raw) else text


def _decode_value(stored, expected):
    if type(expected) is not str or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        _fail()
    if type(stored) is str:
        _canonical_raw(stored, expected)
        return stored
    if type(stored) is not bytes or len(stored) > MAX_BYTES or len(stored) <= _HEADER_BYTES or not stored.startswith(MAGIC):
        _fail()
    declared = struct.unpack(">I", stored[len(MAGIC):len(MAGIC) + 4])[0]
    raw_digest = stored[len(MAGIC) + 4:_HEADER_BYTES]
    if declared > MAX_BYTES or declared < 1 or len(stored) >= declared:
        _fail()
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(stored[_HEADER_BYTES:], min(declared, MAX_BYTES) + 1)
        if (len(raw) != declared or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data
                or hashlib.sha256(raw).digest() != raw_digest):
            _fail()
        text = raw.decode("utf-8", errors="strict")
        _canonical_raw(text, expected)
        return text
    except (ValueError, TypeError, UnicodeError, zlib.error, RecursionError, journal_module.JournalError):
        _fail()


def _live(ledger):
    b = _entry_guard(ledger)
    _source_guard()
    j, cfg = b["journal"], b["config"]
    p = j.dynamic
    jc = b["journal_class"]
    diagnostic = jc["diagnostic_module"]
    if diagnostic is not None:
        try:
            if (diagnostic.StorageDiagnosticJournal is not jc["class"]
                    or diagnostic.SOURCE_DIGEST != jc["diagnostic_source_digest"]
                    or hashlib.sha256((_HERE / "fixture_storage_diagnostics.py").read_bytes()).hexdigest() != jc["diagnostic_source_digest"]):
                _fail("origin_storage_source_changed")
        except OSError:
            _fail("origin_storage_source_changed")
    if (type(j) is not jc["class"] or jc["class"]._transaction is not jc["transaction"]
            or "_transaction" in j.__dict__ or j.profile != REFERENCE_PROFILE
            or type(p) not in (_REFERENCE_CLASS, _PHASE_CLASS) or p.j is not j or p.origins is not ledger
            or j.db is not b["db"] or ledger.connection is not b["db"]
            or j.lock is not b["lock"] or j.clock is not b["clock"]
            or j.schemas is not b["schemas"] or j.schemas.interface_digest != b["interface_digest"]
            or j.schemas.schemas != b["schema_values"] or j.schemas.payload_versions != b["payload_versions"]
            or j.schemas.publication_families != b["publication_families"]
            or j.grants is not b["grants"] or j.grants != b["grant_values"]
            or j.limits is not b["limits"] or j.limits != b["limit_values"]
            or j.adaptive_feedback_reference_config is not cfg or j.adaptive_feedback_config is not cfg
            or journal_module.canonical(cfg) != b["config_text"]
            or p.config_digest != b["config_digest"] or p.identity != cfg["identity"] or p.clock_profile != cfg["clock_profile"]
            or p._worker_producer != b["worker"] or ledger.maximum_origins != b["maximum_origins"]
            or type(p) is _PHASE_CLASS and ("_commissioned_clock" in p.__dict__ or not _PHASE_GUARD(p))):
        _fail("origin_storage_binding_changed")
    return j


def _initialized_state(ledger, connection):
    """The original constructor creates exactly these two inert state fields."""
    state = object.__getattribute__(ledger, "__dict__")
    if (type(state) is not dict or any(type(key) is not str for key in state)
            or set(state) != _STATE_NAMES or state["connection"] is not connection
            or type(state["maximum_origins"]) is not int
            or not 1 <= state["maximum_origins"] <= base.MAXIMUM_ORIGINS):
        return None
    return state


def _entry_guard(ledger):
    """Refuse ordinary method/state shadowing before resolving a public method.

    This is a fixed helper composition check, not isolation from privileged
    Python reflection or arbitrary replacement of the executing module.
    """
    if type(ledger) is not _ORIGIN_CLASS:
        _fail("origin_storage_uncommissioned")
    if any(_static_method(_BASE_CLASS, name) is not method for name, method in zip(_PIN_NAMES, _BASE_METHODS)):
        _fail("origin_storage_source_changed")
    if (type.__getattribute__(_ORIGIN_CLASS, "__mro__") != (_ORIGIN_CLASS, _BASE_CLASS, object)
            or any(_static_method(_ORIGIN_CLASS, name) is not method for name, method in zip(_PIN_NAMES, _ORIGIN_METHODS))):
        _fail("origin_storage_binding_changed")
    b = _BINDINGS.get(ledger)
    if b is None:
        _fail("origin_storage_uncommissioned")
    state = _initialized_state(ledger, b["db"])
    if state is None or state["maximum_origins"] != b["maximum_origins"]:
        _fail("origin_storage_binding_changed")
    return b


class _FixedLedgerType(type):
    """Ordinary class assignment cannot replace the entry guard itself."""
    def __setattr__(cls, name, value):
        if name in _SEALED_NAMES:
            raise TypeError("fixed origin ledger structural methods are sealed")
        return type.__setattr__(cls, name, value)

    def __delattr__(cls, name):
        if name in _SEALED_NAMES:
            raise TypeError("fixed origin ledger structural methods are sealed")
        return type.__delattr__(cls, name)


class _OriginStorageLedger(_BASE_CLASS, metaclass=_FixedLedgerType):
    def __getattribute__(self, name):
        if name in _ENTRY_NAMES:
            _entry_guard(self)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name in _SEALED_NAMES:
            raise TypeError("fixed origin ledger structural state is sealed")
        return object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name in _SEALED_NAMES:
            raise TypeError("fixed origin ledger structural state is sealed")
        return object.__delattr__(self, name)

    def lookup(self, authentication, event_id):
        _entry_guard(self)
        return _BASE_LOOKUP(self, authentication, event_id)

    def retry(self, authentication, event_id, request):
        _entry_guard(self)
        return _BASE_RETRY(self, authentication, event_id, request)

    def remember(self, authentication, event_id, request, first_receipt, *, validate_receipt):
        _entry_guard(self)
        return _BASE_REMEMBER(self, authentication, event_id, request, first_receipt, validate_receipt=validate_receipt)

    def _encode_request_storage(self, canonical_text):
        binding = _entry_guard(self)
        with binding["journal"].lock:
            _live(self)
            return _encode_value(canonical_text)

    def _decode_request_storage(self, stored_value, expected_request_digest):
        binding = _entry_guard(self)
        with binding["journal"].lock:
            _live(self)
            return _decode_value(stored_value, expected_request_digest)


_ORIGIN_CLASS = _OriginStorageLedger
_ORIGIN_METHODS = tuple(_static_method(_ORIGIN_CLASS, name) for name in _PIN_NAMES)


def create_origin_storage_ledger(journal, selection):
    """Return adopted origins; install before phase commissioning/listener."""
    expected = selection_configuration()
    if (type(selection) is not dict or any(type(key) is not str for key in selection)
            or set(selection) != set(expected)
            or any(type(selection[key]) is not str for key in expected if key != "codec_version")
            or type(selection["codec_version"]) is not int or selection != expected):
        raise ValueError("exact initialized R71 journal and selected internal storage required")
    journal_class = _journal_class_binding(journal)
    with journal.lock:
        _source_guard()
        p, cfg = journal.dynamic, journal.adaptive_feedback_reference_config
        if type(p) is not _REFERENCE_CLASS or type(p.origins) is not _BASE_CLASS:
            raise ValueError("original initialized R71 origin ledger required")
        state = _initialized_state(p.origins, journal.db)
        if state is None:
            raise ValueError("origin ledger initialized state must contain exactly connection and maximum_origins")
        if (journal.profile != REFERENCE_PROFILE or type(p) is not _REFERENCE_CLASS or p.j is not journal
                or type(p.origins) is not _BASE_CLASS or p.origins.connection is not journal.db
                or journal.adaptive_feedback_config is not cfg or p.config_digest != cfg["config_digest"]
                or p.identity != cfg["identity"] or p.clock_profile != cfg["clock_profile"]
                or journal.db.in_transaction
                or journal.db.execute("SELECT count(*) FROM workers").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM publications").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM receipts WHERE event_id LIKE 'worker-commit/%'").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM adaptive_feedback_origins").fetchone()[0]):
            raise ValueError("empty initialized R71 origin ledger required before listener startup")
        configured = base.validate_producer_epochs(cfg["producer_epochs"])
        if any({"principal_id": g.principal_id, "producer_id": g.producer_id, "producer_epoch": g.producer_epoch} not in configured
               or not g.expected_consumer_id or "worker_commit" in g.operations and g.expected_worker_id != g.producer_id
               for g in journal.grants):
            raise ValueError("origin codec grants differ from initialized configured epochs")
        workers = {g.producer_id for g in journal.grants if "worker_commit" in g.operations
                   and (p.identity["site_id"], p.identity["room_id"]) in g.scopes}
        if workers != {p._worker_producer}:
            raise ValueError("origin codec worker grants differ from initialized profile")
        adopted = object.__new__(_OriginStorageLedger)
        adopted.connection = state["connection"]
        adopted.maximum_origins = state["maximum_origins"]
        _BINDINGS[adopted] = {"journal": journal, "journal_class": journal_class, "db": journal.db, "lock": journal.lock, "clock": journal.clock,
            "config": cfg, "config_text": journal_module.canonical(cfg), "config_digest": p.config_digest,
            "schemas": journal.schemas, "interface_digest": journal.schemas.interface_digest,
            "schema_values": copy.deepcopy(journal.schemas.schemas),
            "payload_versions": copy.deepcopy(journal.schemas.payload_versions),
            "publication_families": copy.deepcopy(journal.schemas.publication_families),
            "grants": journal.grants, "grant_values": copy.deepcopy(journal.grants),
            "limits": journal.limits, "limit_values": copy.deepcopy(journal.limits),
            "worker": p._worker_producer, "maximum_origins": p.origins.maximum_origins}
        return adopted
