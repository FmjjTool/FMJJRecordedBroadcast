# -*- coding: utf-8 -*-
"""Windows Job Object 进程树管理（父关子关）。

把子进程纳入 Job Object（KILL_ON_JOB_CLOSE）后：
- 本软件退出（Job 句柄随进程关闭）→ 整棵子进程树一并结束
- 主动 TerminateJobObject → 整棵子进程树立即结束
"""

import ctypes
import sys

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def create_kill_job():
    """创建「句柄关闭即杀全树」的 Job Object，失败返回 None。"""
    if sys.platform != "win32":
        return None

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_void_p),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    try:
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def assign_job(job, proc):
    """把进程（及其后续子进程）纳入 Job Object。"""
    try:
        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(job, int(proc._handle)))
    except Exception:
        return False


def terminate_job(job):
    """终止整棵进程树并释放句柄。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateJobObject(job, 0)
        kernel32.CloseHandle(job)
        return True
    except Exception:
        return False
