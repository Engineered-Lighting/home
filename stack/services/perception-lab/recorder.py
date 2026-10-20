"""Bounded sampled numerical ingestion and explicitly admitted stream-copy recording.

No DepthAI imports. FFmpeg reads only a provisioned encoded stream; this module
does not own USB, encode video, load models, or infer exposure time from PTS.
"""
from dataclasses import dataclass
from pathlib import Path
import ast
import io
import json
import math
import os
import re
import struct
import subprocess
import threading
import time
import zipfile
from store import LabError, encode, identifier


def bounded_number(value, label, minimum=0):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < minimum:
        raise LabError("invalid "+label,422,"invalid_sample")
    return value


def validate_npz(data, maximum):
    """Validate bounded numeric NPY members without importing numpy or unpickling."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members=archive.infolist()
            if not 1 <= len(members) <= 32 or sum(i.file_size for i in members)>maximum:
                raise ValueError()
            names=set()
            for member in members:
                if member.filename in names or "/" in member.filename or "\\" in member.filename or not member.filename.endswith(".npy") or member.flag_bits & 1:
                    raise ValueError()
                names.add(member.filename)
                with archive.open(member) as source:
                    prefix=source.read(8)
                    if len(prefix)!=8 or prefix[:6]!=b"\x93NUMPY" or prefix[6] not in (1,2,3):
                        raise ValueError()
                    n=2 if prefix[6]==1 else 4
                    raw=source.read(n)
                    if len(raw)!=n:
                        raise ValueError()
                    header_size=int.from_bytes(raw,"little")
                    if not 1 <= header_size <= 16384:
                        raise ValueError()
                    header=ast.literal_eval(source.read(header_size).decode("utf-8" if prefix[6]==3 else "latin1"))
                    dtype=header["descr"]
                    shape=header["shape"]
                    # Numeric arrays only: never object/pickle or structured dtypes.
                    if not isinstance(dtype,str) or not __import__('re').fullmatch(r"[<>=|][buif][1248]",dtype):
                        raise ValueError()
                    if not isinstance(shape,tuple) or len(shape)>4 or any(type(x) is not int or x < 0 for x in shape):
                        raise ValueError()
                    expected=math.prod(shape)*int(dtype[2:])
                    if expected>maximum or member.file_size != 8+n+header_size+expected:
                        raise ValueError()
                    # Read through CRC verification in bounded chunks.
                    remaining=expected
                    while remaining:
                        block=source.read(min(65536,remaining))
                        if not block:
                            raise ValueError()
                        remaining-=len(block)
                    if source.read(1):
                        raise ValueError()
    except (ValueError,KeyError,TypeError,SyntaxError,UnicodeError,zipfile.BadZipFile,EOFError,NotImplementedError,RuntimeError):
        raise LabError("invalid bounded numeric NPZ",422,"invalid_npz") from None


class SampleRecorder:
    def __init__(self,store):
        self.store=store

    def ingest(self,sid,data,metadata,mime_type="application/octet-stream"):
        session=self.store.get_session(sid)
        if metadata.get("site_id") != session["site_id"] or metadata.get("camera_id") != session["camera_id"]:
            raise LabError("sample source does not match session",403,"source_mismatch")
        for key in ("capture_epoch","stream"):
            identifier(metadata.get(key))
        sequence=metadata.get("sequence")
        if type(sequence) is not int or sequence<0:
            raise LabError("invalid sequence",422,"invalid_sample")
        bounded_number(metadata.get("source_timestamp_seconds"),"source timestamp")
        if metadata.get("source_clock") not in {"device_monotonic","host_monotonic","unix","media_pts"}:
            raise LabError("explicit source clock required",422,"invalid_sample")
        if metadata.get("timestamp_semantics") not in {"exposure","device_frame","packaging","presentation","unverified"}:
            raise LabError("timestamp semantics required",422,"invalid_sample")
        if metadata.get("uncertainty_seconds") is not None:
            bounded_number(metadata["uncertainty_seconds"],"clock uncertainty")
        if metadata.get("format") == "depth_u16_le":
            width,height=metadata.get("width"),metadata.get("height")
            if type(width) is not int or type(height) is not int or not 1<=width<=4096 or not 1<=height<=4096 or len(data)!=width*height*2:
                raise LabError("depth byte count does not match dimensions",422,"invalid_depth")
            if metadata.get("units")!="millimetres" or metadata.get("invalid_value")!=0 or metadata.get("alignment_frame")!="rectified_left" or not metadata.get("calibration_id"):
                raise LabError("depth units, left frame, invalid value and calibration required",422,"invalid_depth")
        elif metadata.get("format") == "numeric_npz":
            validate_npz(data,self.store.settings.max_object_bytes*2)
        else:
            raise LabError("unsupported numerical sample format",422,"invalid_sample")
        key=encode([metadata["capture_epoch"],metadata["stream"],sequence])
        # Receipt time never replaces or refreshes the source timestamp.
        return self.store.add_media(sid,data,mime_type,metadata,key)


@dataclass(frozen=True)
class StreamSource:
    id: str
    camera_id: str
    url: str
    enabled: bool = False
    admitted: bool = False
    ffmpeg: str = "ffmpeg"
    segment_seconds: int = 10
    staging_reserve_bytes: int = 64*1024**2
    codec: str = ""
    codec_validation_id: str = ""

    def __post_init__(self):
        identifier(self.id); identifier(self.camera_id)
        if not self.url.startswith(("rtsp://","rtsps://")) or any(c in self.url for c in "\r\n\x00"):
            raise ValueError("only provisioned RTSP encoded streams are supported")
        if not 2<=self.segment_seconds<=30 or self.staging_reserve_bytes<32*1024**2:
            raise ValueError("invalid segment bounds")
        if self.codec and not re.fullmatch(r"avc1\.[0-9a-fA-F]{6}",self.codec):
            raise ValueError("registered H.264 RFC 6381 codec required")


# How long a listed segment may be absent (its .tmp rename pending) while
# FFmpeg still runs before the recording ends as segment_missing.
MISSING_SEGMENT_GRACE_S=5.0

# Everything the recorder's FFmpeg input may open: the stdin list and one
# RTSP(S) session over TCP. Never a local file or another protocol.
FFMPEG_INPUT_PROTOCOLS="pipe,rtsp,rtsps,tcp,tls"


def ffconcat_input(url):
    """One-entry ffconcat list naming the reader URL, written to FFmpeg's stdin.

    The URL can carry the relay's reader credential. Every local user can read
    argv (ps, /proc/<pid>/cmdline); only the owner can read the pipe. FFmpeg 4.4
    (the Jetson's /usr/bin/ffmpeg) takes an input URL only in argv or in a
    concat list. The concat demuxer does not pass -rtsp_transport to the stream
    it opens, so TCP is requested with FFmpeg's RTSP URL option "tcp", which
    FFmpeg removes from the path before contacting the relay.
    """
    entry=url+("&" if "?" in url else "?")+"tcp"
    return "ffconcat version 1.0\nfile '"+entry.replace("'","'\\''")+"'\n"


class StreamCopySupervisor:
    """Opt-in FFmpeg supervisor; instantiate/start only after runtime admission.

    Staging is monitored, bounded by a reserved allowance and never reused by a
    different session. An OS filesystem quota is still required for a strict
    hard bound against an unexpectedly fast or malicious upstream writer.
    """
    def __init__(self,store,source,popen=subprocess.Popen):
        self.store,self.source,self.popen=store,source,popen
        self.process=None
        self.thread=None
        self.stop_event=threading.Event()
        self.sid=None
        self.imported=set()
        self.initialization_id=None
        # Listed segments not yet on disk: first-miss time (monotonic) by name.
        self.missing={}
        self.monotonic=time.monotonic

    def command(self,directory):
        # The source URL is never in argv: start() writes ffconcat_input to stdin.
        return [self.source.ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-f","concat","-safe","0","-protocol_whitelist",FFMPEG_INPUT_PROTOCOLS,"-i","pipe:0","-map","0:v:0","-an","-c:v","copy","-f","hls","-hls_time",str(self.source.segment_seconds),"-hls_playlist_type","event","-hls_list_size","0","-hls_segment_type","fmp4","-hls_flags","temp_file","-hls_fmp4_init_filename","init.mp4","-hls_segment_filename",str(Path(directory)/"%08d.m4s"),str(Path(directory)/"index.m3u8")]

    def start(self,session):
        if not self.source.enabled or not self.source.admitted:
            return {"state":"blocked","reason":"encoded_source_not_admitted"}
        if not self.source.codec or not self.source.codec_validation_id:
            return {"state":"blocked","reason":"source_codec_not_validated"}
        if (session["camera_id"]!=self.source.camera_id
                or (self.thread is not None and self.thread.is_alive())
                or (self.process is not None and self.process.poll() is None)):
            raise LabError("recorder source busy or mismatched")
        self.store.check_capacity(self.source.staging_reserve_bytes)
        directory=self.store.root/"staging"/session["id"]
        if directory.is_symlink() or (self.store.root/"staging").is_symlink():
            raise LabError("unsafe staging path",403,"unsafe_path")
        directory.mkdir(parents=True,exist_ok=False)
        # FFmpeg writes here outside the Store: measured live, never estimated,
        # until termination is confirmed (see _monitor's finally).
        self.store.track_staging(session["id"],directory)
        # Reuse only after affirmative local termination. A fresh session must
        # not inherit stop state, imported segment names or its predecessor's init.
        self.stop_event=threading.Event()
        self.imported=set()
        self.initialization_id=None
        self.missing={}
        self.thread=None
        self.process=None
        self.sid,self.directory=session["id"],directory
        # No shell and no inherited stdout/stderr that could print stream
        # credentials; the URL reaches FFmpeg only through its stdin pipe.
        try:
            self.process=self.popen(self.command(directory),stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False)
        except BaseException:
            self.store.release_staging(session["id"])
            raise
        try:
            # FFmpeg reads the list to EOF before it opens the source.
            self.process.stdin.write(ffconcat_input(self.source.url).encode("utf-8"))
        except OSError:
            pass  # FFmpeg already exited; the poll below reports it.
        finally:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is not None:
            self.store.release_staging(session["id"])
            return {"state":"blocked","reason":"encoded_source_process_exited"}
        self.thread=threading.Thread(target=self._monitor,name="lab-stream-copy",daemon=True)
        self.thread.start()
        return {"state":"recording","reason":None,"source_id":self.source.id,"encoded_timing":"unverified_pts","capture_ownership":"existing_relay","container":"fragmented_mp4","codec":self.source.codec}

    def _collect(self,final=False):
        if final and (self.process is None or self.process.poll() is None):
            raise LabError("capture termination required before finalization",409,"termination_unconfirmed")
        playlist=self.directory/"index.m3u8"
        if not playlist.exists():
            return
        if playlist.is_symlink() or playlist.stat().st_size>128*1024:
            raise LabError("invalid recording playlist",422,"invalid_playlist")
        segments=parse_playlist(playlist.read_text(encoding="utf-8"))
        if not segments:
            return
        common={"source_id":self.source.id,"format":"fmp4","codec":self.source.codec,"mime_codec":f'video/mp4; codecs="{self.source.codec}"',"codec_validation_id":self.source.codec_validation_id,"timestamp_semantics":"presentation","clock_mapping":"unverified","timeline_basis":"encoded_sequence_relative_seconds","depth_association":"unavailable"}
        if self.initialization_id is None:
            init=self.directory/"init.mp4"
            if init.is_symlink() or not init.exists() or not 1<=init.stat().st_size<=self.store.settings.max_object_bytes:
                raise LabError("initialization object unavailable",422,"invalid_segment")
            item=self.store.add_media(self.sid,init.read_bytes(),"video/mp4",{**common,"kind":"encoded_init","role":"init"},"encoded:init",finalized_fragment=final)
            self.initialization_id=item["id"]
        for segment in segments:
            path=self.directory/segment["filename"]
            if path.name in self.imported:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
                raise LabError("unsafe segment",403,"unsafe_path")
            try:
                size=path.stat().st_size
                data=path.read_bytes() if 0<size<=self.store.settings.max_object_bytes else None
            except FileNotFoundError:
                # FFmpeg can list a segment a moment before its .tmp rename
                # lands; under CPU pressure that moment is visible here. While
                # capturing, retry at the next poll, in order, but only
                # briefly: FFmpeg renames in order, so a later segment on disk,
                # or MISSING_SEGMENT_GRACE_S without it, means it is missing.
                # After termination a listed segment that is still absent is
                # missing at once.
                if final:
                    raise LabError("listed segment missing after termination",409,"segment_missing") from None
                first=self.missing.setdefault(path.name,self.monotonic())
                if self.monotonic()-first>=MISSING_SEGMENT_GRACE_S or self._later_segment_on_disk(segment["segment_index"]):
                    raise LabError("listed segment missing during capture",409,"segment_missing") from None
                break
            self.missing.pop(path.name,None)
            if size==0:
                continue
            if size>self.store.settings.max_object_bytes:
                raise LabError("encoded segment exceeds configured bound",413,"segment_limit")
            self.store.add_media(self.sid,data,"video/mp4",{**common,**segment,"kind":"encoded_segment","role":"media","initialization_id":self.initialization_id,"partial":False,"independent_decoding":"requires_init_and_source_GOP_validation"},"segment:"+path.stem,finalized_fragment=final)
            self.imported.add(path.name)
            # This removes only the imported temporary copy, never a recorded object.
            path.unlink()

    def _terminate(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)

    def _later_segment_on_disk(self,index):
        with os.scandir(self.directory) as entries:
            return any(re.fullmatch(r"\d{8}\.m4s",entry.name) and int(entry.name[:8])>index for entry in entries)

    def _staging_bytes(self):
        """Bytes now in this session's staging directory.

        FFmpeg renames its .tmp segment and playlist while this lists them: an
        entry gone between listing and stat is counted under its new name at
        the next 0.2 s poll, never reported as a recorder failure.
        """
        total=0
        with os.scandir(self.directory) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        total+=entry.stat().st_size
                except FileNotFoundError:
                    continue
        return total

    def _requested_stop(self):
        """The controller's recorded stop reason, once the session is stopping.

        A runtime whose stop returns before this recorder drains (the managed
        pair) lets Controller.stop move the session to 'stopping' first. That
        is the requested stop, not an unexplained state change."""
        try:
            if self.store.get_session(self.sid)["state"]!="stopping":
                return None
            return self.store.requested_stop(self.sid)
        except LabError:
            return None

    def _monitor(self):
        reason="source_ended"
        failure=None
        try:
            while not self.stop_event.wait(0.2):
                session=self.store.get_session(self.sid)
                if session["state"]=="starting":
                    continue
                if session["state"]!="recording":
                    reason=self._requested_stop() or "recording_state_changed"; break
                if self.store.clock()-session["started_at"]>=self.store.settings.max_session_seconds:
                    reason="duration_limit"; break
                staging=self._staging_bytes()
                if staging>=self.source.staging_reserve_bytes:
                    reason="staging_limit"; break
                # The Store's exact running total plus this directory's bytes
                # just measured: no storage scan on this 0.2 s poll.
                self.store.check_capacity(self.source.staging_reserve_bytes,measured_staging={self.sid:staging})
                self._collect()
                if self.process.poll() is not None:
                    break
            else:
                # Stop signalled in-process: report the controller's recorded
                # reason (controller_shutdown, quota_exceeded, duration_limit,
                # ...) truthfully; an unrecorded stop is the user's.
                reason=self.store.requested_stop(self.sid) or "user_stop"
        except LabError as exc:
            reason=exc.code
            if exc.code=="conflict":
                # A capture import refused because Stop moved the session to
                # 'stopping' between this poll's check and the import: the
                # final collection below still commits that segment.
                reason=self._requested_stop() or reason
        except Exception as exc:
            reason="recorder_failure"
            # Class name only (never the message): which local operation failed.
            name=type(exc).__name__
            failure=name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",name) else "unclassified"
        finally:
            try:
                self._terminate()
            except Exception:
                self.store.append_event(self.sid,"recording.indeterminate",{"reason":"recorder_termination_unconfirmed"},critical=True)
                if self.store.get_session(self.sid)["state"] in {"starting","recording","stopping"}:
                    self.store.transition(self.sid,"indeterminate","recorder_termination_unconfirmed")
                return
            collection="not_attempted"
            try:
                if self.store.get_session(self.sid)["state"] in {"recording","stopping"}:
                    self._collect(final=True)
                    collection="committed_listed_segments"
            except Exception:
                collection="failed"
                self.store.append_event(self.sid,"recording.incomplete",{"reason":"final_segment_not_committed"},critical=True)
            # FFmpeg has terminated: its remaining staging bytes join the
            # Store's running total before the terminal state reconciles it.
            self.store.release_staging(self.sid)
            receipt={
                "source_id":self.source.id,"reason":reason,"process_terminal":True,
                "final_collection":collection,"committed_segments":len(self.imported),
                "capture_completeness":"unverified" if reason in {"user_stop","duration_limit"} and collection=="committed_listed_segments" else "partial_or_unverified",
                "synchronization":"unverified","geometric_validity":"unvalidated"}
            if failure is not None:
                receipt["failure_class"]=failure
            self.store.append_event(self.sid,"recording.finalized",receipt,critical=True)
            state=self.store.get_session(self.sid)["state"]
            if state in {"starting","recording","stopping"}:
                self.store.transition(self.sid,"stopped",reason)

    def stop(self,session):
        if self.sid!=session["id"]:
            return {"state":"blocked","reason":"recorder_session_mismatch"}
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                return {"state":"stopping","reason":"recorder_drain_pending"}
        if self.process is not None and self.process.poll() is None:
            return {"state":"indeterminate","reason":"recorder_termination_unconfirmed"}
        return {"state":"stopped"}


def parse_playlist(text):
    """Accept only our local FFmpeg event playlist, never URLs or arbitrary paths."""
    if len(text)>128*1024 or not text.startswith("#EXTM3U"):
        raise LabError("invalid recording playlist",422,"invalid_playlist")
    duration=None
    elapsed=0.0
    segments=[]
    mapped=False
    for line in text.splitlines():
        line=line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-KEY"):
            raise LabError("encrypted playlist is unsupported",422,"invalid_playlist")
        if line.startswith("#EXT-X-MAP:"):
            if line!='#EXT-X-MAP:URI="init.mp4"':
                raise LabError("unexpected initialization path",422,"invalid_playlist")
            mapped=True
        elif line.startswith("#EXTINF:"):
            try:
                duration=float(line[8:].split(",",1)[0])
            except ValueError:
                duration=None
            if duration is None or not math.isfinite(duration) or not 0<duration<=120:
                raise LabError("invalid segment duration",422,"invalid_playlist")
        elif not line.startswith("#"):
            if not mapped or duration is None or not re.fullmatch(r"\d{8}\.m4s",line) or int(line[:8])!=len(segments):
                raise LabError("invalid segment sequence",422,"invalid_playlist")
            segments.append({"filename":line,"segment_index":len(segments),"sequence":len(segments),"start_seconds":elapsed,"duration_seconds":duration,"end_seconds":elapsed+duration})
            elapsed+=duration
            duration=None
            if len(segments)>1000 or elapsed>1920:
                raise LabError("recording playlist exceeds duration bound",422,"invalid_playlist")
    if duration is not None:
        raise LabError("incomplete playlist entry",422,"invalid_playlist")
    return segments
