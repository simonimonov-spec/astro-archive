"""Measured Windows resource sampling, without a polling helper process."""
import ctypes, multiprocessing, os, threading, time
from ctypes import wintypes

class Sampler:
    def __init__(self):self.previous=None;self.lock=threading.Lock()
    def read(self):
        class Memory(ctypes.Structure):_fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD),*[(x,ctypes.c_ulonglong) for x in ['total','available','page','availablePage','virtual','availableVirtual','extended']]]
        class Counters(ctypes.Structure):_fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),*[(x,ctypes.c_size_t) for x in ['peak','working','peakPaged','paged','peakNonpaged','nonpaged','pagefile','peakPagefile','private']]]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        memory=Memory();memory.length=ctypes.sizeof(memory);kernel.GlobalMemoryStatusEx(ctypes.byref(memory))
        ticks=lambda ft:(ft.dwHighDateTime<<32)|ft.dwLowDateTime
        idle,system,user=wintypes.FILETIME(),wintypes.FILETIME(),wintypes.FILETIME();kernel.GetSystemTimes(ctypes.byref(idle),ctypes.byref(system),ctypes.byref(user))
        process_ticks=0;working=0;pids={os.getpid(),*[p.pid for p in multiprocessing.active_children() if p.pid]}
        for pid in pids:
            handle=kernel.OpenProcess(0x410,False,pid)
            if not handle:continue
            try:
                a,b,c,d=(wintypes.FILETIME() for _ in range(4));counter=Counters();counter.cb=ctypes.sizeof(counter)
                if kernel.GetProcessTimes(handle,ctypes.byref(a),ctypes.byref(b),ctypes.byref(c),ctypes.byref(d)):process_ticks+=ticks(c)+ticks(d)
                if psapi.GetProcessMemoryInfo(handle,ctypes.byref(counter),counter.cb):working+=counter.working
            finally:kernel.CloseHandle(handle)
        now=time.monotonic();total=ticks(system)+ticks(user);idle_ticks=ticks(idle);cpu=all_cpu=None
        with self.lock:
            if self.previous:
                before,pt,st,it,prior_pids=self.previous;elapsed=now-before
                if elapsed>0 and pids==prior_pids:cpu=max(0,min(100,(process_ticks-pt)/1e7/elapsed/(os.cpu_count() or 1)*100))
                if total>st:all_cpu=max(0,min(100,(1-(idle_ticks-it)/(total-st))*100))
            self.previous=(now,process_ticks,total,idle_ticks,pids)
        return dict(cpuPercent=cpu,totalCpuPercent=all_cpu,privateWorkingSetBytes=working,totalRamBytes=memory.total,usedRamBytes=memory.total-memory.available,
            problem='Отчитат се Python хостът и числените му работни процеси. Браузърът и външните програми са в „Други“. RAM е resident working set, включително споделените страници.')
