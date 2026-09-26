"""Tie native decoder/ASR lifetime to its parent even on force-close (Windows)."""
import ctypes
from ctypes import wintypes

class _Basic(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),
              ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',wintypes.DWORD),
              ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
class _IO(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class _Extended(ctypes.Structure):
    _fields_=[('basic',_Basic),('io',_IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
              ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]

class ChildJob:
    def __init__(self,process,*,memory_bytes=2*1024**3):
        k=ctypes.WinDLL('kernel32',use_last_error=True);self.k=k;self.handle=None
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
        k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];k.SetInformationJobObject.restype=wintypes.BOOL
        k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];k.AssignProcessToJobObject.restype=wintypes.BOOL
        k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
        self.handle=k.CreateJobObjectW(None,None)
        info=_Extended();info.basic.flags=0x2000 | 0x100  # kill on close + per-process memory bound
        info.process_memory=memory_bytes
        if not self.handle or not k.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(self.handle,wintypes.HANDLE(process._handle)):
            self.close();raise OSError('Could not bind local voice subprocess lifetime')
    def close(self):
        if self.handle:self.k.CloseHandle(self.handle);self.handle=None
