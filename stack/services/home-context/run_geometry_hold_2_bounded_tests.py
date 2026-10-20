"""Windows-only focused unittest supervisor: 128 MiB per process, 30 seconds.

Creates the child suspended, assigns its kill-on-close job before resuming,
and bounds the complete child tree. This is workstation synthetic test evidence.
"""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def main(targets):
    if sys.platform != "win32" or not targets:
        raise ValueError("Windows and explicit focused unittest targets required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    native = ctypes.WinDLL("ntdll")
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = ctypes.c_long
    job = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100
    limits.ProcessMemoryLimit = 128 * 1024 * 1024
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    here = Path(__file__).resolve().parent
    started = time.monotonic()
    child = None
    try:
        child = subprocess.Popen([sys.executable, "-B", "-m", "unittest", "-v", *targets], cwd=here,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 creationflags=0x4 | 0x08000000)
        handle = wintypes.HANDLE(int(child._handle))
        if not kernel.AssignProcessToJobObject(job, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        if native.NtResumeProcess(handle) != 0:
            raise RuntimeError("could not resume bounded test process")
        try:
            stdout, stderr = child.communicate(timeout=30)
            timed_out = False
        except subprocess.TimeoutExpired:
            kernel.TerminateJobObject(job, 124)
            stdout, stderr = child.communicate(timeout=5)
            timed_out = True
        print(json.dumps({"status": child.returncode, "timed_out": timed_out,
                          "elapsed_s": round(time.monotonic() - started, 3),
                          "process_memory_limit_mib": 128, "timeout_s": 30,
                          "targets": targets, "stdout": stdout, "stderr": stderr}))
        return 0 if child.returncode == 0 else 1
    finally:
        if child is not None and child.poll() is None:
            kernel.TerminateJobObject(job, 125)
            child.wait(timeout=5)
        kernel.CloseHandle(job)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
