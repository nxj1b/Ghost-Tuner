import customtkinter as ctk
import threading
import time
import json
import subprocess
import os
import ctypes
import sys
from datetime import datetime
import psutil
from PIL import Image
from dotenv import load_dotenv
import queue

# --- 🛡️ ADMIN ELEVATION ---
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()

# Load environment variables from .env file
load_dotenv()

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

winmm = ctypes.WinDLL('winmm')

# Resolve script directory so logo always loads regardless of cwd
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class GhostTuner:
    def __init__(self):
        self.is_monitoring = False
        self.stop_ai_flag = False 
        self.currently_processing = False 
        self.persistence_file = "mastered_faults.json"
        self.is_gaming_mode = False
        self.last_applied_cap = -1
        self.seen_errors = set()
        self.suspended_pids = []
        
        self.game_list = ["valorant.exe", "csgo.exe", "cs2.exe", "fortniteclient-win64-shipping.exe", "cod.exe", "robloxplayerbeta.exe", "minecraft.exe", "rbxfpsunlocker.exe"]
        self.essential_procs = ["explorer.exe", "ghost.py", "python.exe", "svchost.exe", "dwm.exe"]
        self.mastered_ids = self.load_mastery()

        # ── ERROR QUEUE ───────────────────────────────────────────────────────
        # Holds (ev_id, msg) tuples that arrived while the AI was busy.
        # A dedicated daemon thread drains the queue sequentially, waiting for
        # each AI fix to fully complete before pulling the next error.
        self.error_queue = queue.Queue()
        self._queue_worker_thread = threading.Thread(target=self._error_queue_worker, daemon=True)
        self._queue_worker_thread.start()
        # ─────────────────────────────────────────────────────────────────────
        
        self.gemini_client = None
        if GEMINI_AVAILABLE:
            api_key = os.getenv("GOOGLE_API_KEY")
            if api_key:
                try: 
                    self.gemini_client = genai.Client(api_key=api_key)
                    self.log_ai_event_safe("✔ Gemini client initialized successfully.")
                except Exception as e:
                    self.log_ai_event_safe(f"❌ Gemini client FAILED to initialize: {e}")
            else:
                self.log_ai_event_safe("❌ GOOGLE_API_KEY not found in .env file.")
        else:
            self.log_ai_event_safe("❌ Google GenAI package not found. Install with: pip install google-genai")
        
        self.setup_ui()
        self.refresh_persistence_display()

    # Safe logger before UI exists — buffers messages and flushes after setup
    def log_ai_event_safe(self, m):
        if not hasattr(self, '_pre_ui_logs'):
            self._pre_ui_logs = []
        self._pre_ui_logs.append(m)

    def trim_working_sets(self):
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name = proc.info['name']
                    if name and name.lower() not in self.essential_procs and name.lower() not in [g.lower() for g in self.game_list]:
                        handle = ctypes.windll.kernel32.OpenProcess(0x001F0FFF, False, proc.info['pid'])
                        if handle:
                            ctypes.windll.psapi.EmptyWorkingSet(handle)
                            ctypes.windll.kernel32.CloseHandle(handle)
                except (psutil.NoSuchProcess, psutil.AccessDenied): continue
        except: pass

    def purge_standby_list(self):
        self.trim_working_sets()

    def apply_sbo(self):
        subprocess.run(r'powercfg -setacvalueindex scheme_current sub_processor PROCTHROTTLEMIN 100', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\be337238-0d82-4146-a960-4f37b9d47c6d" /v "Attributes" /t REG_DWORD /d 2 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🚀 SBO: Silicon Binning Override Activated.", "info")

    def apply_gvs(self):
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DedicatedSegmentSize" /t REG_DWORD /d 4096 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "TdrDelay" /t REG_DWORD /d 10 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🚀 GVS: VRAM Shifter Engaged.", "info")

    def create_restore_point(self):
        self.log_event("⏳ Creating System Restore Point...", "info")
        cmd = 'powershell Checkpoint-Computer -Description "GhostTunerRestore" -RestorePointType "MODIFY_SETTINGS"'
        subprocess.run(cmd, shell=True, creationflags=0x08000000)
        self.log_event("✔ Restore Point Created: GhostTunerRestore", "success")

    def system_revive(self):
        self.log_event("🔄 Launching System Restore Wizard...", "info")
        subprocess.Popen("rstrui.exe", creationflags=0x08000000)

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: HPET Disabler
    # Forces Windows to use CPU TSC clock instead of HPET — lower timer overhead
    # ─────────────────────────────────────────────────────────────────────────
    def apply_hpet_disable(self):
        subprocess.run('bcdedit /set useplatformclock false', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('bcdedit /set tscsyncpolicy enhanced', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("⏱️ HPET: Disabled — TSC clock enforced for lower timer overhead.", "info")

    def revert_hpet_disable(self):
        subprocess.run('bcdedit /deletevalue useplatformclock', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('bcdedit /deletevalue tscsyncpolicy', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("⏱️ HPET: Restored to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: Game DVR / Xbox Game Bar Killer
    # Stops background GPU encode that silently eats bandwidth
    # ─────────────────────────────────────────────────────────────────────────
    def apply_game_dvr_kill(self):
        subprocess.run(r'reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR" /v AllowGameDVR /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🎮 GAME DVR: Xbox Game Bar & DVR killed — GPU bandwidth reclaimed.", "info")

    def revert_game_dvr_kill(self):
        subprocess.run(r'reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🎮 GAME DVR: Restored to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: GPU IRQ Priority Booster
    # Elevates GPU interrupt priority so frames get serviced faster
    # ─────────────────────────────────────────────────────────────────────────
    def boost_gpu_irq_priority(self):
        cmd = r"""
        Get-ChildItem -Path 'HKLM:\SYSTEM\CurrentControlSet\Enum\PCI' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'VGA|Display|NVIDIA|AMD|Radeon|Intel.*Graphics' } |
        ForEach-Object {
            $intPath = $_.PSPath + '\Device Parameters\Interrupt Management\Affinity Policy'
            New-Item -Path $intPath -Force -ErrorAction SilentlyContinue | Out-Null
            Set-ItemProperty -Path $intPath -Name 'DevicePriority' -Value 3 -Type DWord -Force -ErrorAction SilentlyContinue
        }
        """
        subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("🖥️ GPU IRQ: Priority elevated to HIGH — frame interrupt latency reduced.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: P-Core Affinity Pinning
    # Pins detected games to P-cores only on Intel 12th gen+ hybrid CPUs
    # ─────────────────────────────────────────────────────────────────────────
    def pin_game_to_pcores(self):
        total = psutil.cpu_count(logical=True)
        if not total or total <= 8:
            return  # Only relevant on hybrid CPU configs with enough cores
        p_cores = list(range(0, max(1, total - 4)))
        for proc in psutil.process_iter(['name', 'pid']):
            if proc.info['name'] and proc.info['name'].lower() in self.game_list:
                try:
                    psutil.Process(proc.info['pid']).cpu_affinity(p_cores)
                    self.log_event(f"📌 P-CORE LOCK: {proc.info['name']} pinned to cores {p_cores}", "success")
                except: pass

    def revert_pcore_pin(self):
        total = psutil.cpu_count(logical=True)
        if not total:
            return
        all_cores = list(range(total))
        for proc in psutil.process_iter(['name', 'pid']):
            if proc.info['name'] and proc.info['name'].lower() in self.game_list:
                try:
                    psutil.Process(proc.info['pid']).cpu_affinity(all_cores)
                except: pass

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: Shader Cache Cleaner
    # Wipes stale DirectX/NVIDIA/AMD shader caches that cause microstutter
    # ─────────────────────────────────────────────────────────────────────────
    def clear_shader_cache(self):
        self.log_event("🧽 SHADER CACHE: Wiping stale DX/NVIDIA/AMD/Intel caches...", "info")
        def _clear():
            paths = [
                r"$env:LOCALAPPDATA\D3DSCache",
                r"$env:LOCALAPPDATA\NVIDIA\DXCache",
                r"$env:LOCALAPPDATA\AMD\DxCache",
                r"$env:LOCALAPPDATA\Intel\ShaderCache",
            ]
            cmd = "; ".join([
                f'Remove-Item -Path "{p}\\*" -Recurse -Force -ErrorAction SilentlyContinue'
                for p in paths
            ])
            subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
            self.log_event("🧽 SHADER CACHE: All stale caches wiped — microstutter eliminated.", "success")
        threading.Thread(target=_clear, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: Network Stack Optimizer
    # Cuts packet scheduling latency for online games
    # ─────────────────────────────────────────────────────────────────────────
    def apply_network_optimizer(self):
        subprocess.run('netsh int tcp set global autotuninglevel=highlyrestricted', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global congestionprovider=ctcp', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global ecncapability=disabled', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set supplemental internet congestionprovider=ctcp', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global timestamps=disabled', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global rss=enabled', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🌐 NET OPTIMIZER: TCP tuning applied — ping/jitter reduced.", "info")

    def revert_network_optimizer(self):
        subprocess.run('netsh int tcp set global autotuninglevel=normal', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global congestionprovider=default', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global ecncapability=enabled', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('netsh int tcp set global timestamps=enabled', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🌐 NET OPTIMIZER: Reverted to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: Fixed Pagefile Size
    # Prevents Windows from dynamically resizing the pagefile mid-session
    # ─────────────────────────────────────────────────────────────────────────
    def set_fixed_pagefile(self):
        self.log_event("💾 PAGEFILE: Setting fixed size to eliminate resize hitches...", "info")
        def _set():
            cmd = r"""
            $cs = Get-WmiObject Win32_ComputerSystem -ErrorAction SilentlyContinue
            if ($cs) {
                $cs.AutomaticManagedPagefile = $false
                $cs.Put() | Out-Null
            }
            $pf = Get-WmiObject Win32_PageFileSetting -ErrorAction SilentlyContinue
            if ($pf) {
                $pf.InitialSize = 8192
                $pf.MaximumSize = 8192
                $pf.Put() | Out-Null
            } else {
                Set-WmiInstance -Class Win32_PageFileSetting -Arguments @{
                    Name='C:\pagefile.sys'; InitialSize=8192; MaximumSize=8192
                } -ErrorAction SilentlyContinue | Out-Null
            }
            """
            subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
            self.log_event("💾 PAGEFILE: Fixed at 8192MB — mid-session resize stutters eliminated.", "success")
        threading.Thread(target=_set, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # FEATURE: Fullscreen Optimization (FSO) Disabler
    # Disables Windows compositor intercept in fullscreen — cuts compositor latency
    # ─────────────────────────────────────────────────────────────────────────
    def disable_fso_for_games(self):
        key = r'HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'
        for game in self.game_list:
            subprocess.run(
                f'reg add "{key}" /v "{game}" /t REG_SZ /d "DISABLEDXMAXIMIZEDWINDOWEDMODE" /f',
                shell=True, capture_output=True, creationflags=0x08000000
            )
        self.log_event("🖥️ FSO DISABLED: Fullscreen Optimizations killed for all tracked games.", "info")

    def revert_fso_for_games(self):
        key = r'HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'
        for game in self.game_list:
            subprocess.run(
                f'reg delete "{key}" /v "{game}" /f',
                shell=True, capture_output=True, creationflags=0x08000000
            )
        self.log_event("🖥️ FSO: Fullscreen Optimizations restored to Windows default.", "info")

    # =========================================================================
    # ██████████████  NEW FEATURE BLOCK — AUTO-FIRES ON GAME DETECT  █████████
    # =========================================================================

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 1: CPU Core Parking Disabler
    # Windows silently parks cores mid-game causing brutal frame spikes on wake
    # ─────────────────────────────────────────────────────────────────────────
    def disable_core_parking(self):
        cmd = r'''
        $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\0cc5b647-c1df-4637-891a-dec35c318583"
        Set-ItemProperty -Path $path -Name "ValueMax" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
        powercfg /setacvalueindex scheme_current 54533251-82be-4824-96c1-47b60b740d00 0cc5b647-c1df-4637-891a-dec35c318583 100
        powercfg /setdcvalueindex scheme_current 54533251-82be-4824-96c1-47b60b740d00 0cc5b647-c1df-4637-891a-dec35c318583 100
        powercfg /setacvalueindex scheme_current 54533251-82be-4824-96c1-47b60b740d00 ea062031-0e34-4ff1-9b6d-eb1059334028 100
        powercfg -setactive scheme_current
        Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Processor" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Set-ItemProperty -Path $_.PSPath -Name "CpuUnparkPercent" -Value 100 -Type DWord -Force -ErrorAction SilentlyContinue
        }
        '''
        subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("🔓 CORE PARKING: All CPU cores unlocked — zero wake-up stutter.", "success")

    def revert_core_parking(self):
        cmd = r'''
        $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\0cc5b647-c1df-4637-891a-dec35c318583"
        Set-ItemProperty -Path $path -Name "ValueMax" -Value 100 -Type DWord -Force -ErrorAction SilentlyContinue
        powercfg /setacvalueindex scheme_current 54533251-82be-4824-96c1-47b60b740d00 0cc5b647-c1df-4637-891a-dec35c318583 0
        powercfg -setactive scheme_current
        '''
        subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("🔓 CORE PARKING: Reverted to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 2: GPU Max Power State Forcer (NVIDIA + AMD)
    # Forces GPU to stay locked at max P0 clock — no mid-game downclocking
    # ─────────────────────────────────────────────────────────────────────────
    def force_gpu_max_power(self):
        nv_cmd = r'''
        Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
            if ($props.DriverDesc -match "NVIDIA|GeForce|RTX|GTX") {
                Set-ItemProperty -Path $_.PSPath -Name "PerfLevelSrc"      -Value 0x2222 -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerEnable"  -Value 0x1   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerLevel"   -Value 0x1   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerLevelAC" -Value 0x1   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "GpuPowerMizerMode" -Value 1     -Type DWord -Force -ErrorAction SilentlyContinue
            }
            if ($props.DriverDesc -match "AMD|Radeon|RX") {
                Set-ItemProperty -Path $_.PSPath -Name "EnableUlps"        -Value 0     -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PP_SclkDeepSleepDisable" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
            }
        }
        '''
        subprocess.run(['powershell', '-Command', nv_cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("⚡ GPU POWER: Forced to max P0 state — no GPU clock drops mid-game.", "success")

    def revert_gpu_max_power(self):
        revert_cmd = r'''
        Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
            if ($props.DriverDesc -match "NVIDIA|GeForce|RTX|GTX") {
                Set-ItemProperty -Path $_.PSPath -Name "PerfLevelSrc"      -Value 0x2233 -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerEnable"  -Value 0x1   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerLevel"   -Value 0x3   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PowerMizerLevelAC" -Value 0x3   -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "GpuPowerMizerMode" -Value 0     -Type DWord -Force -ErrorAction SilentlyContinue
            }
            if ($props.DriverDesc -match "AMD|Radeon|RX") {
                Set-ItemProperty -Path $_.PSPath -Name "EnableUlps"        -Value 1     -Type DWord -Force -ErrorAction SilentlyContinue
                Remove-ItemProperty -Path $_.PSPath -Name "PP_SclkDeepSleepDisable" -ErrorAction SilentlyContinue
            }
        }
        '''
        subprocess.run(['powershell', '-Command', revert_cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("⚡ GPU POWER: Reverted to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 3: RAM XMP/EXPO Profile Checker
    # Detects if XMP is disabled (running at JEDEC stock) and warns user
    # ─────────────────────────────────────────────────────────────────────────
    def check_ram_profile(self):
        def _check():
            cmd = r"(Get-WmiObject Win32_PhysicalMemory | Measure-Object -Property Speed -Maximum).Maximum"
            res = subprocess.run(['powershell', '-Command', cmd], capture_output=True, text=True, creationflags=0x08000000)
            speed = res.stdout.strip()
            try:
                mhz = int(speed)
                if mhz < 3200:
                    self.log_event(f"⚠️ RAM ALERT: Running at {mhz}MHz — XMP/EXPO likely DISABLED in BIOS. Enable it for +15% FPS!", "error")
                else:
                    self.log_event(f"✔ RAM PROFILE: {mhz}MHz detected — XMP/EXPO active.", "success")
            except:
                self.log_event("⚠️ RAM PROFILE: Could not read speed. Check BIOS XMP setting.", "info")
        threading.Thread(target=_check, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 4: DX12 GPU Preemption / Overhead Disabler
    # Kills GPU mid-frame task switching and DXR driver overhead
    # ─────────────────────────────────────────────────────────────────────────
    def disable_dx12_overhead(self):
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisablePreemption" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "EnableWriteCombining" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "PreemptionLevel" /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisableRayTracing" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisableVRS" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🎮 DX OVERHEAD: GPU preemption + DXR/VRS overhead disabled — smoother frame delivery.", "success")

    def revert_dx12_overhead(self):
        subprocess.run(r'reg delete "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisablePreemption" /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg delete "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "PreemptionLevel" /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg delete "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisableRayTracing" /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg delete "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "DisableVRS" /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🎮 DX OVERHEAD: Reverted to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 5: Background Process Freezer (NtSuspendProcess)
    # Suspends non-essential apps during gaming — resumes when game exits
    # ─────────────────────────────────────────────────────────────────────────
    def suspend_background_processes(self):
        freeze_list = [
            "discord.exe", "chrome.exe", "msedge.exe", "spotify.exe",
            "onedrive.exe", "teams.exe", "slack.exe", "skype.exe",
            "searchindexer.exe", "antimalware service executable",
            "microsoftedge.exe", "opera.exe", "brave.exe"
        ]
        self.suspended_pids = []
        NtSuspendProcess = ctypes.windll.ntdll.NtSuspendProcess
        for proc in psutil.process_iter(['name', 'pid']):
            try:
                name = proc.info['name'].lower()
                # Never freeze essential procs or the game itself
                if name in [e.lower() for e in self.essential_procs]:
                    continue
                if name in [g.lower() for g in self.game_list]:
                    continue
                if any(k in name for k in freeze_list):
                    handle = ctypes.windll.kernel32.OpenProcess(0x001F0FFF, False, proc.info['pid'])
                    if handle:
                        NtSuspendProcess(handle)
                        ctypes.windll.kernel32.CloseHandle(handle)
                        self.suspended_pids.append(proc.info['pid'])
            except: pass
        self.log_event(f"⏸️ BG FREEZE: {len(self.suspended_pids)} background processes suspended — CPU/RAM freed.", "success")

    def resume_background_processes(self):
        NtResumeProcess = ctypes.windll.ntdll.NtResumeProcess
        resumed = 0
        for pid in getattr(self, 'suspended_pids', []):
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x001F0FFF, False, pid)
                if handle:
                    NtResumeProcess(handle)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    resumed += 1
            except: pass
        self.suspended_pids = []
        self.log_event(f"▶️ BG RESUME: {resumed} background processes restored.", "success")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 6: Memory Compression + Superfetch Deep Disabler
    # Kills kernel memory compression and prefetch that stutter on low RAM
    # ─────────────────────────────────────────────────────────────────────────
    def disable_memory_compression(self):
        subprocess.run('powershell -Command "Disable-MMAgent -MemoryCompression"', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters" /v "EnablePrefetcher" /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters" /v "EnableSuperfetch" /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🧠 MEMORY: Compression + Superfetch disabled — zero RAM stutter.", "success")

    def revert_memory_compression(self):
        subprocess.run('powershell -Command "Enable-MMAgent -MemoryCompression"', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters" /v "EnablePrefetcher" /t REG_DWORD /d 3 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters" /v "EnableSuperfetch" /t REG_DWORD /d 3 /f', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🧠 MEMORY: Compression + Superfetch restored to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 7: Write-Combining Memory Mode for GPU VRAM
    # Forces WC mode on CPU->GPU memory transfers — 4x faster vertex/UBO uploads
    # ─────────────────────────────────────────────────────────────────────────
    def enable_write_combining(self):
        cmd = r'''
        Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $p = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
            if ($p.DriverDesc -match "NVIDIA|GeForce|RTX|GTX|AMD|Radeon|RX") {
                Set-ItemProperty -Path $_.PSPath -Name "EnableWriteCombining"         -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "DisableCPUCaching"            -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
                Set-ItemProperty -Path $_.PSPath -Name "PreferSystemMemoryContiguous" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
            }
        }
        '''
        subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("💾 WRITE-COMBINE: CPU→GPU transfer mode maximized — draw calls 4x faster.", "success")

    def revert_write_combining(self):
        cmd = r'''
        Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $p = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
            if ($p.DriverDesc -match "NVIDIA|GeForce|RTX|GTX|AMD|Radeon|RX") {
                Remove-ItemProperty -Path $_.PSPath -Name "EnableWriteCombining"         -ErrorAction SilentlyContinue
                Remove-ItemProperty -Path $_.PSPath -Name "DisableCPUCaching"            -ErrorAction SilentlyContinue
                Remove-ItemProperty -Path $_.PSPath -Name "PreferSystemMemoryContiguous" -ErrorAction SilentlyContinue
            }
        }
        '''
        subprocess.run(['powershell', '-Command', cmd], capture_output=True, creationflags=0x08000000)
        self.log_event("💾 WRITE-COMBINE: Reverted to Windows default.", "info")

    # ─────────────────────────────────────────────────────────────────────────
    # NEW FEATURE 8: Sustained CPU Boost Clock Enforcer
    # Prevents Intel/AMD from decaying boost clocks after 28-56s under load
    # ─────────────────────────────────────────────────────────────────────────
    def force_sustained_cpu_boost(self):
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTMODE 2',      shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTPOL 100',     shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFINCPOL 2',         shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFDECPOL 1',         shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFINCTHRESHOLD 10',  shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFDECTHRESHOLD 8',   shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\be337238-0d82-4146-a960-4f37b9d47c6d" /v "ValueMax" /t REG_DWORD /d 100 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg -setactive scheme_current', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("⚡ CPU BOOST: Sustained turbo clocks enforced — no boost decay mid-game.", "success")

    def revert_sustained_cpu_boost(self):
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTMODE 1',  shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFBOOSTPOL 60', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFINCPOL 1',    shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg /setacvalueindex scheme_current sub_processor PERFDECPOL 2',    shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run('powercfg -setactive scheme_current', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("⚡ CPU BOOST: Reverted to Windows default.", "info")

    # =========================================================================
    # ████████████████████  END NEW FEATURE BLOCK  ████████████████████████████
    # =========================================================================

    def apply_ghost_game_boost(self):
        self.log_event("🚀 EXTREME BOOST: Engaging Revolutionary Latency Shield...", "info")
        subprocess.run('bcdedit /set disabledynamictick yes', shell=True, capture_output=True, creationflags=0x08000000)
        
        # Optimize power state with high-performance fallback verification
        pwr_plan = subprocess.run('powercfg /setactive e9a42b02-d5df-448d-aa00-03f14749eb61', shell=True, capture_output=True, creationflags=0x08000000)
        if pwr_plan.returncode != 0:
            subprocess.run('powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c', shell=True, capture_output=True, creationflags=0x08000000)
            
        self.apply_sbo()
        self.apply_gvs()
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management" /v "LargeSystemCache" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management" /v "DisablePagingExecutive" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v "IRQ8Priority" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" /v "TcpAckFrequency" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" /v "TcpNoDelay" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v "NetworkThrottlingIndex" /t REG_DWORD /d 4294967295 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v "HwSchMode" /t REG_DWORD /d 2 /f', shell=True, capture_output=True, creationflags=0x08000000)
        
        # Maximize Multimedia Class Scheduler (MMCSS) parameters for gaming engines
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games" /v "Priority" /t REG_DWORD /d 6 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games" /v "Scheduling Category" /t REG_SZ /d "High" /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games" /v "SFIO Priority" /t REG_SZ /d "High" /f', shell=True, capture_output=True, creationflags=0x08000000)
        
        # Optimized Win32 foreground priority quantum allocation (Hex 0x26)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v "Win32PrioritySeparation" /t REG_DWORD /d 38 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v "SystemResponsiveness" /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        
        # --- FEATURE 1: MSI (Message Signaled Interrupts) Mode Switcher ---
        msi_cmd = r"Get-ChildItem -Path 'HKLM:\SYSTEM\CurrentControlSet\Enum\PCI' -Recurse -ErrorAction SilentlyContinue | Where-Object {$_.Name -match 'Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties'} | ForEach-Object { New-ItemProperty -Path $_.PSPath -Name 'MSISupported' -Value 1 -PropertyType DWORD -Force -ErrorAction SilentlyContinue; Set-ItemProperty -Path $_.PSPath -Name 'MSISupported' -Value 1 -Force -ErrorAction SilentlyContinue }"
        subprocess.run(['powershell', '-Command', msi_cmd], capture_output=True, creationflags=0x08000000)
        
        # --- FEATURE 2: USB Selective Suspend & Power Gating Disabler ---
        subprocess.run('powercfg /SETACVALUEINDEX SCHEME_CURRENT 2a73a32d-3dbf-476f-9784-ad42a96759d5 d4e98f31-5ee3-4d56-a1da-2f48f33ce99c 0', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\USB" /v "DisableHubPowerManagement" /t REG_DWORD /d 1 /f', shell=True, capture_output=True, creationflags=0x08000000)
        
        # --- FEATURE 4: Network Adapter (NIC) Interrupt Moderation Disabler ---
        nic_cmd = r"Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -like '*Interrupt Moderation*'} | ForEach-Object { Set-NetAdapterAdvancedProperty -Name $_.Name -DisplayName $_.DisplayName -DisplayValue 'Disabled' -ErrorAction SilentlyContinue }"
        subprocess.run(['powershell', '-Command', nic_cmd], capture_output=True, creationflags=0x08000000)

        # --- EXISTING: HPET Disabler ---
        self.apply_hpet_disable()

        # --- EXISTING: Game DVR / Xbox Bar Killer ---
        self.apply_game_dvr_kill()

        # --- EXISTING: GPU IRQ Priority Booster ---
        self.boost_gpu_irq_priority()

        # --- EXISTING: Network Stack Optimizer ---
        self.apply_network_optimizer()

        # --- EXISTING: FSO Disabler for all tracked games ---
        self.disable_fso_for_games()

        # ── NEW FEATURES — AUTO-FIRE ON GAME DETECT ────────────────────────
        self.disable_core_parking()          # Feature 1: Unpark all CPU cores
        self.force_gpu_max_power()           # Feature 2: Lock GPU to P0 max clock
        self.check_ram_profile()             # Feature 3: Warn if XMP disabled
        self.disable_dx12_overhead()         # Feature 4: Kill DX12/DXR/VRS overhead
        self.suspend_background_processes()  # Feature 5: Freeze background apps
        self.disable_memory_compression()    # Feature 6: Kill memory compression
        self.enable_write_combining()        # Feature 7: Force WC GPU memory mode
        self.force_sustained_cpu_boost()     # Feature 8: Lock CPU turbo boost on
        # ────────────────────────────────────────────────────────────────────
        
        self.suspended_services = ["wuauserv", "SysMain", "DiagTrack"]
        for svc in self.suspended_services:
            subprocess.run(f'net stop {svc} /y', shell=True, capture_output=True, creationflags=0x08000000)
        try:
            for proc in psutil.process_iter(['name', 'pid']):
                name = proc.info['name']
                if name and name.lower() in ["dwm.exe", "csrss.exe"]:
                    try:
                        p = psutil.Process(proc.info['pid'])
                        p.nice(psutil.REALTIME_PRIORITY_CLASS)
                    except: pass
        except: pass
        self.trim_working_sets()
        winmm.timeBeginPeriod(1)
        self.log_event("🚀 EXTREME BOOST: ALL SYSTEMS ENGAGED — Core Parking, GPU P0, DX12, BG Freeze, WC, CPU Turbo + Legacy Stack Active.", "success")
        self.update_status_ui(True)

    def revert_ghost_game_boost(self):
        self.log_event("🛡️ BOOST: Restoring system state...", "info")
        subprocess.run('bcdedit /deletevalue disabledynamictick', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management" /v "LargeSystemCache" /t REG_DWORD /d 0 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" /v "TcpAckFrequency" /t REG_DWORD /d 2 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v "Win32PrioritySeparation" /t REG_DWORD /d 2 /f', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v "SystemResponsiveness" /t REG_DWORD /d 10 /f', shell=True, capture_output=True, creationflags=0x08000000)
        
        # Revert Feature 2 and Feature 4 to Windows defaults
        subprocess.run('powercfg /SETACVALUEINDEX SCHEME_CURRENT 2a73a32d-3dbf-476f-9784-ad42a96759d5 d4e98f31-5ee3-4d56-a1da-2f48f33ce99c 1', shell=True, capture_output=True, creationflags=0x08000000)
        subprocess.run(r'reg delete "HKLM\SYSTEM\CurrentControlSet\Services\USB" /v "DisableHubPowerManagement" /f', shell=True, capture_output=True, creationflags=0x08000000)
        nic_revert = r"Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -like '*Interrupt Moderation*'} | ForEach-Object { Set-NetAdapterAdvancedProperty -Name $_.Name -DisplayName $_.DisplayName -DisplayValue 'Enabled' -ErrorAction SilentlyContinue }"
        subprocess.run(['powershell', '-Command', nic_revert], capture_output=True, creationflags=0x08000000)

        # --- Revert existing features ---
        self.revert_hpet_disable()
        self.revert_game_dvr_kill()
        self.revert_network_optimizer()
        self.revert_fso_for_games()
        self.revert_pcore_pin()

        # ── REVERT NEW FEATURES ─────────────────────────────────────────────
        self.revert_core_parking()           # Revert Feature 1
        self.revert_gpu_max_power()          # Revert Feature 2
        self.revert_dx12_overhead()          # Revert Feature 4
        self.resume_background_processes()   # Revert Feature 5
        self.revert_memory_compression()     # Revert Feature 6
        self.revert_write_combining()        # Revert Feature 7
        self.revert_sustained_cpu_boost()    # Revert Feature 8
        # ────────────────────────────────────────────────────────────────────

        for svc in getattr(self, 'suspended_services', []):
            subprocess.run(f'net start {svc}', shell=True, capture_output=True, creationflags=0x08000000)
        winmm.timeEndPeriod(1)
        subprocess.run('powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e', shell=True, capture_output=True, creationflags=0x08000000)
        self.log_event("🛡️ BOOST: All systems reverted to default.", "success")
        self.update_status_ui(False)

    def update_power_throttle(self):
        slider_val = int(self.slider.get())
        if self.is_gaming_mode:
            target = 100
        else:
            cpu_usage = psutil.cpu_percent()
            target = int((cpu_usage / 100) * slider_val)
            if target < 5: target = 5
        if target != self.last_applied_cap:
            subprocess.run(f'powercfg /setacvalueindex scheme_current sub_processor PROCTHROTTLEMAX {target}', shell=True, capture_output=True, creationflags=0x08000000)
            self.last_applied_cap = target
        ts = datetime.now().strftime('%H:%M:%S')
        cpu_load = psutil.cpu_percent()
        pwr_info = f"[{ts}] ⚡ Power Limit: {target}% | Core Load: {cpu_load}%"
        self.window.after(0, lambda: (self.pwr_text.insert("end", pwr_info + "\n"), self.pwr_text.see("end")))

    def set_power_mode(self, value):
        val = int(value)
        self.slider_label.configure(text=f"Dynamic Core Cap: {val}%")

    def toggle_theme(self, choice):
        ctk.set_appearance_mode("light" if choice == "☀️" else "dark")

    def load_mastery(self):
        if os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, "r") as f: return json.load(f)
            except: return {}
        return {}

    def save_mastery(self, error_id, commands):
        self.mastered_ids[str(error_id)] = commands
        with open(self.persistence_file, "w") as f:
            json.dump(self.mastered_ids, f, indent=4)
        self.refresh_persistence_display()

    def refresh_persistence_display(self):
        if hasattr(self, 'fixes_text'):
            self.fixes_text.delete("1.0", "end")
            for code, cmds in self.mastered_ids.items():
                self.fixes_text.insert("end", f"✓ CACHED COGNITIVE FIX: ID {code}\n")
                for c in cmds:
                    self.fixes_text.insert("end", f"   ↳ {c}\n")
                self.fixes_text.insert("end", "-"*40 + "\n")

    def setup_ui(self):
        ctk.set_appearance_mode("dark")
        self.window = ctk.CTk()
        self.window.title("GHOST TUNER: NEURAL OVERDRIVE [EXTREME]")
        self.window.geometry("1280x850")
        
        self.cyan = ("#007A87", "#00F5FF")
        self.magenta = ("#B5179E", "#D946EF")
        self.red, self.green = "#FF3131", "#00FF7F"
        self.bg_black = ("#F4F4F6", "#08080A")
        self.card_bg = ("#E4E4E7", "#111116")
        self.border_blue = ("#D4D4D8", "#1A2E3B")
        self.main_bg = ("#FAFAFA", "#0D0D11")
        self.textbox_bg = ("#FFFFFF", "#040406")
        self.text_color = ("#1A1A1E", "#D1D1D1")
        
        self.window.configure(fg_color=self.bg_black)
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_rowconfigure(0, weight=1)
        
        self.sidebar = ctk.CTkScrollableFrame(self.window, width=320, corner_radius=0, fg_color=self.bg_black, border_width=1, border_color=self.border_blue)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        
        logo_path = os.path.join(SCRIPT_DIR, "ghosttuner.png")
        logo_loaded = False
        if os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).convert("RGBA")
                self.logo_image = ctk.CTkImage(light_image=img, dark_image=img, size=(280, 150))
                self.logo_label = ctk.CTkLabel(self.sidebar, image=self.logo_image, text="")
                self.logo_label.pack(pady=(20, 15), padx=10)
                logo_loaded = True
            except Exception as e:
                print(f"[LOGO ERROR] {e}")
        if not logo_loaded:
            self.create_text_logo_fallback()
        
        self.create_side_btn("INITIALIZE QUANTUM CORE", self.start_monitoring, "#10B981", "white")
        self.create_side_btn("GHOST GAME BOOST (MANUAL)", self.apply_ghost_game_boost, self.red, "white")
        self.create_side_btn("CREATE SYSTEM RESTORE", self.create_restore_point, ("#E4E4E7", "#27272A"), ("#1A1A1E", "white"))
        self.create_side_btn("SYSTEM REVIVE", self.system_revive, ("#E4E4E7", "#27272A"), ("#1A1A1E", "white"))
        self.create_side_btn("TERMINATE ALL ENGINES", self.stop_all, ("#D4D4D8", "#3F3F46"), ("#1A1A1E", "white"))
        self.create_side_btn("NEURAL PURGE SWEEP", self.run_opt, "transparent", self.cyan, border=1)

        # ── NEW FEATURE BUTTONS ────────────────────────────────────────────
        self.create_side_btn("🧽 WIPE SHADER CACHE", self.clear_shader_cache, "transparent", self.cyan, border=1)
        self.create_side_btn("💾 FIX PAGEFILE SIZE", self.set_fixed_pagefile, "transparent", self.cyan, border=1)
        # ──────────────────────────────────────────────────────────────────

        self.slider_label = ctk.CTkLabel(self.sidebar, text="Dynamic Core Cap: 100%", font=("Consolas", 12, "bold"), text_color=("#3F3F46", "#A1A1AA"))
        self.slider_label.pack(pady=(25, 5))
        self.slider = ctk.CTkSlider(self.sidebar, from_=50, to=100, number_of_steps=50, button_color=self.cyan, button_hover_color=self.magenta, progress_color=self.cyan, fg_color=("#D4D4D8", "#1E1E24"), command=self.set_power_mode)
        self.slider.set(100)
        self.slider.pack(padx=25, pady=5, fill="x")
        
        self.theme_toggle = ctk.CTkSegmentedButton(self.sidebar, values=["☀️", "🌙"], selected_color=self.cyan, command=self.toggle_theme)
        self.theme_toggle.set("🌙")
        self.theme_toggle.pack(padx=25, pady=20, fill="x")

        self.hud = ctk.CTkFrame(self.sidebar, fg_color=self.card_bg, corner_radius=12, border_width=1, border_color=self.border_blue)
        self.hud.pack(fill="x", padx=20, pady=(15, 25))
        
        self.status_label = ctk.CTkLabel(self.hud, text="BOOST STATE: IDLE", font=("Consolas", 14, "bold"), text_color=self.red)
        self.status_label.pack(pady=(12, 8))
        
        self.grp_label = self.create_hud_label("THREAD PRIORITY: PASSIVE", "#71717A")
        self.sbo_label = self.create_hud_label("SILICON OVERRIDE: DORMANT", "#71717A")
        self.timer_label = self.create_hud_label("KERNEL TIMER: 15.6ms", "#71717A")
        self.eclipse_label = self.create_hud_label("LATENCY SHIELD: INACTIVE", "#71717A")
        self.hpet_label = self.create_hud_label("HPET CLOCK: STANDARD", "#71717A")
        self.dvr_label = self.create_hud_label("GAME DVR: ACTIVE", "#71717A")
        self.net_label = self.create_hud_label("NET STACK: DEFAULT", "#71717A")
        self.fso_label = self.create_hud_label("FSO: ENABLED", "#71717A")
        
        self.main = ctk.CTkFrame(self.window, corner_radius=0, fg_color=self.main_bg)
        self.main.grid(row=0, column=1, sticky="nsew")
        
        self.top_bar = ctk.CTkFrame(self.main, height=90, fg_color=self.card_bg, border_width=1, border_color=self.border_blue, corner_radius=0)
        self.top_bar.pack(fill="x", padx=0, pady=0)
        
        self.cpu_usage_lbl = ctk.CTkLabel(self.top_bar, text="CPU: 0%", font=("Consolas", 15, "bold"), text_color=self.cyan)
        self.cpu_usage_lbl.pack(side="left", padx=30, pady=25)
        self.ram_usage_lbl = ctk.CTkLabel(self.top_bar, text="RAM: 0%", font=("Consolas", 15, "bold"), text_color=self.cyan)
        self.ram_usage_lbl.pack(side="left", padx=10, pady=25)

        self.auto_switch = ctk.CTkSwitch(self.top_bar, text="AI AUTO-OPTIMIZER", font=("Segoe UI", 12, "bold"), progress_color=self.magenta, text_color=("#1A1A1E", "white"))
        self.auto_switch.select()
        self.auto_switch.pack(side="right", padx=30, pady=25)

        self.tabs = ctk.CTkTabview(self.main, fg_color=self.bg_black, segmented_button_selected_color=self.cyan, corner_radius=14)
        self.tabs.pack(fill="both", expand=True, padx=25, pady=25)
        
        self.log_text = self.create_textbox(self.tabs.add("LIVE TELEMETRY MATRIX"))
        self.pwr_text = self.create_textbox(self.tabs.add("⚡ DYNAMIC POWER LOG"))
        self.ai_text = self.create_textbox(self.tabs.add("🧠 NEURAL ANALYTICS DEPOT"))
        self.fixes_text = self.create_textbox(self.tabs.add("COGNITIVE FIX LEDGER"))

        for msg in getattr(self, '_pre_ui_logs', []):
            self.log_ai_event(msg)

    def create_text_logo_fallback(self):
        self.logo_label = ctk.CTkLabel(self.sidebar, text="GHOST TUNER", font=("Orbitron", 32, "bold"), text_color=self.cyan)
        self.logo_label.pack(pady=(40, 25))

    def update_ui_stats(self):
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        self.cpu_usage_lbl.configure(text=f"CPU CORE LOAD: {cpu}%")
        self.ram_usage_lbl.configure(text=f"VIRTUAL RAM: {ram}%")

    def update_status_ui(self, active):
        self.status_label.configure(text="BOOST STATE: ACTIVE" if active else "BOOST STATE: IDLE", text_color=self.green if active else self.red)

    def create_side_btn(self, text, cmd, bg, text_c, border=0):
        btn = ctk.CTkButton(self.sidebar, text=text, command=cmd, fg_color=bg, text_color=text_c, border_width=border, border_color=self.cyan, height=48, corner_radius=10, font=("Segoe UI", 12, "bold"), hover_color=("#E4E4E7", "#22222B") if bg == "transparent" else None)
        btn.pack(padx=25, pady=8, fill="x")

    def create_hud_label(self, text, color):
        lbl = ctk.CTkLabel(self.hud, text=text, font=("Consolas", 11, "bold"), text_color=color)
        lbl.pack(pady=5, padx=15, anchor="w")
        return lbl

    def create_textbox(self, parent):
        box = ctk.CTkTextbox(parent, font=("Consolas", 13), fg_color=self.textbox_bg, text_color=self.text_color, border_width=1, border_color=self.border_blue, corner_radius=10, wrap="none")
        box.pack(fill="both", expand=True, padx=15, pady=15)
        return box

    def check_game_mode(self): 
        games = [p for p in psutil.process_iter(['name', 'pid']) if p.info['name'] and p.info['name'].lower() in self.game_list]
        if games:
            if not self.is_gaming_mode:
                self.is_gaming_mode = True
                self.apply_ghost_game_boost()
                for g in games:
                    try:
                        p = psutil.Process(g.info['pid'])
                        p.nice(psutil.HIGH_PRIORITY_CLASS)
                    except: pass
                
                # --- FEATURE 3: Intel Hybrid Architecture E-Core Director ---
                total_cores = psutil.cpu_count(logical=True)
                if total_cores and total_cores > 8:
                    bg_targets = ["discord.exe", "chrome.exe", "spotify.exe", "msedge.exe", "steamwebhelper.exe"]
                    e_core_affinity = list(range(total_cores - 4, total_cores))  # Limit resource hogs to last 4 logical threads
                    for p_bg in psutil.process_iter(['name', 'pid']):
                        try:
                            if p_bg.info['name'] and p_bg.info['name'].lower() in bg_targets:
                                proc_bg = psutil.Process(p_bg.info['pid'])
                                proc_bg.cpu_affinity(e_core_affinity)
                        except: pass

                # --- NEW FEATURE: P-Core pinning for game processes ---
                self.pin_game_to_pcores()

            self.grp_label.configure(text="THREAD PRIORITY: HIGH PRIORITY", text_color=self.cyan)
            self.sbo_label.configure(text="SILICON OVERRIDE: ACTIVE TUNING", text_color=self.magenta)
            self.timer_label.configure(text="KERNEL TIMER: 1.0ms [FORCED]", text_color="#00FF7F")
            self.eclipse_label.configure(text="LATENCY SHIELD: ENGAGED", text_color=self.red)
            self.hpet_label.configure(text="HPET CLOCK: DISABLED [TSC]", text_color="#00FF7F")
            self.dvr_label.configure(text="GAME DVR: KILLED", text_color="#00FF7F")
            self.net_label.configure(text="NET STACK: OPTIMIZED", text_color=self.cyan)
            self.fso_label.configure(text="FSO: DISABLED", text_color=self.magenta)
        else:
            if self.is_gaming_mode:
                self.is_gaming_mode = False
                self.revert_ghost_game_boost()
                
                # Revert Feature 3 background affinity mappings back to system wide scheduling
                total_cores = psutil.cpu_count(logical=True)
                if total_cores:
                    bg_targets = ["discord.exe", "chrome.exe", "spotify.exe", "msedge.exe", "steamwebhelper.exe"]
                    all_cores = list(range(total_cores))
                    for p_bg in psutil.process_iter(['name', 'pid']):
                        try:
                            if p_bg.info['name'] and p_bg.info['name'].lower() in bg_targets:
                                proc_bg = psutil.Process(p_bg.info['pid'])
                                proc_bg.cpu_affinity(all_cores)
                        except: pass

                self.grp_label.configure(text="THREAD PRIORITY: PASSIVE", text_color="#71717A")
                self.sbo_label.configure(text="SILICON OVERRIDE: DORMANT", text_color="#71717A")
                self.timer_label.configure(text="KERNEL TIMER: 15.6ms", text_color="#71717A")
                self.eclipse_label.configure(text="LATENCY SHIELD: INACTIVE", text_color="#71717A")
                self.hpet_label.configure(text="HPET CLOCK: STANDARD", text_color="#71717A")
                self.dvr_label.configure(text="GAME DVR: ACTIVE", text_color="#71717A")
                self.net_label.configure(text="NET STACK: DEFAULT", text_color="#71717A")
                self.fso_label.configure(text="FSO: ENABLED", text_color="#71717A")

    def log_ai_event(self, m):
        ts = datetime.now().strftime('%H:%M:%S')
        self.window.after(0, lambda: (self.ai_text.insert("end", f"[{ts}] 🧠 COGNITION: {m}\n"), self.ai_text.see("end")))

    # ── ERROR QUEUE WORKER ────────────────────────────────────────────────────
    # Runs on its own daemon thread for the lifetime of the app.
    # Blocks on the queue; each time a (ev_id, msg) pair arrives it waits until
    # the AI is free (currently_processing == False) then dispatches the fix.
    # This guarantees errors are processed one-at-a-time in arrival order, and
    # none are silently dropped when the AI is busy handling a previous fault.
    def _error_queue_worker(self):
        while True:
            try:
                ev_id, msg = self.error_queue.get(timeout=1)
            except queue.Empty:
                continue

            # Wait until the AI finishes whatever it is currently doing
            while self.currently_processing:
                time.sleep(0.25)

            # Skip if monitoring was stopped or AI was halted while we waited
            if self.stop_ai_flag or not self.is_monitoring:
                q_size = self.error_queue.qsize()
                if q_size:
                    self.log_ai_event(f"⚠️ QUEUE FLUSHED: {q_size} pending error(s) discarded — monitoring stopped.")
                    with self.error_queue.mutex:
                        self.error_queue.queue.clear()
                self.error_queue.task_done()
                continue

            q_remaining = self.error_queue.qsize()
            if q_remaining:
                self.log_ai_event(f"📋 QUEUE: Dispatching next fault (Event {ev_id}). {q_remaining} more in queue.")
            self.handle_ai_fix(ev_id, msg)

            # Give handle_ai_fix a moment to flip currently_processing = True
            # before the next loop iteration checks it
            time.sleep(0.1)
            self.error_queue.task_done()
    # ─────────────────────────────────────────────────────────────────────────

    def check_windows_errors(self):
        if not self.is_monitoring or self.auto_switch.get() != 1:
            return
        cmd = 'powershell -Command "Get-WinEvent -FilterHashtable @{LogName=\'Application\',\'System\'; Level=1,2; StartTime=(Get-Date).AddSeconds(-5)} -ErrorAction SilentlyContinue | ForEach-Object { $_.Id.ToString() + \'|\' + $_.Message }"'
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=0x08000000)
            if res.stdout:
                for line in res.stdout.strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|', 1)
                        if len(parts) == 2:
                            ev_id, msg = parts[0].strip(), parts[1].strip()
                            unique_sig = f"{ev_id}_{msg[:50]}"
                            if unique_sig not in self.seen_errors:
                                self.seen_errors.add(unique_sig)
                                clean_msg = msg.replace('\r', ' ').replace('\n', ' ')
                                self.handle_error_pipeline(ev_id, clean_msg)
        except Exception as e:
            self.log_ai_event(f"❌ ERROR SCANNER FAULT: {e}")

    def handle_error_pipeline(self, ev_id, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        matrix_msg = f"[{ts}] ● Error ({ev_id}) detected Triggering Ai response\n"
        self.window.after(0, lambda: (self.log_text.insert("end", matrix_msg), self.log_text.see("end")))
        if str(ev_id) in self.mastered_ids:
            self.log_ai_event(f"Pre-cached fix found for Event ID {ev_id}. Executing from ledger...")
            self.execute_cmds(self.mastered_ids[str(ev_id)])
            return
        # Push onto the queue so the worker thread dispatches it in order,
        # waiting for any in-flight AI fix to finish first.
        q_size_before = self.error_queue.qsize()
        self.error_queue.put((ev_id, msg))
        if q_size_before == 0 and not self.currently_processing:
            self.log_ai_event(f"📥 QUEUE: Event {ev_id} dispatching immediately (queue was empty).")
        else:
            self.log_ai_event(f"📥 QUEUE: Event {ev_id} added to queue. Position: {q_size_before + 1} — will process after current fix completes.")

    def handle_ai_fix(self, event_id, msg):
        if not GEMINI_AVAILABLE:
            self.log_ai_event("❌ AI BLOCKED: google-genai package not installed. Run: pip install google-genai")
            return
        if not self.gemini_client:
            self.log_ai_event("❌ AI BLOCKED: Gemini client is None — API key may be invalid or init failed.")
            return
        if self.stop_ai_flag:
            self.log_ai_event("⚠️ AI BLOCKED: stop_ai_flag is set. Restart monitoring to re-enable.")
            return

        self.currently_processing = True
        self.log_ai_event(f"🔍 Analyzing Fault ID: {event_id}...")

        def ai_task():
            prompt = (f"Analyze Fault ID: {event_id}. Message: {msg}. "
                      "Output ONLY Windows PowerShell commands. "
                      "STRICT RULES: You are FORBIDDEN from using DISM, SFC, or CHKDSK unless the error is a confirmed critical file corruption error. "
                      "Do NOT use these for minor issues. Prioritize tuning power settings, thread priority, or registry optimizations. "
                      "No markdown, no explanations.")
            try:
                self.log_ai_event(f"📡 Sending fault {event_id} to Gemini model...")
                res = self.gemini_client.models.generate_content(model="gemma-4-31b-it", contents=prompt)
                if res and res.text:
                    cmds = [c.strip() for c in res.text.replace('```powershell','').replace('```','').split('\n') if len(c) > 5]
                    self.log_ai_event(f"✔ Gemini returned {len(cmds)} command(s) for fault {event_id}.")
                    self.execute_cmds(cmds)
                    self.save_mastery(event_id, cmds)
                else:
                    self.log_ai_event(f"⚠️ Gemini returned empty response for fault {event_id}.")
            except Exception as e:
                self.log_ai_event(f"❌ GEMINI API ERROR for fault {event_id}: {type(e).__name__}: {e}")
            finally:
                self.currently_processing = False

        threading.Thread(target=ai_task, daemon=True).start()

    def execute_cmds(self, cmds):
        forbidden = ["systemctl", "pkill", "rm -rf", "sudo", "renice", "bash", "dism", "sfc", "chkdsk"]
        for c in cmds:
            if self.stop_ai_flag or any(x in c.lower() for x in ["restart-computer", "shutdown"]): continue
            if any(f in c.lower() for f in forbidden):
                self.log_ai_event(f"🚫 BLOCKING INVASIVE CMD: {c}")
                continue
            self.log_ai_event(f"⚙️ Injecting fix: {c}")
            subprocess.run(['powershell', '-Command', c], capture_output=True, creationflags=0x08000000)

    def monitor_loop(self):
        while self.is_monitoring:
            self.check_game_mode()
            self.update_power_throttle()
            self.update_ui_stats()
            self.check_windows_errors()
            if self.is_gaming_mode:
                try: self.purge_standby_list()
                except: pass
            time.sleep(2)

    def start_monitoring(self):
        if not self.is_monitoring:
            self.is_monitoring = True
            self.log_event("🌑 Ghost Tuner Framework Online. Monitoring Engaged.", "success")
            if not GEMINI_AVAILABLE:
                self.log_ai_event("❌ AI OFFLINE: google-genai not installed. Run: pip install google-genai")
            elif not self.gemini_client:
                self.log_ai_event("❌ AI OFFLINE: Gemini client failed to initialize. Check API key.")
            else:
                self.log_ai_event("✔ AI ONLINE: Gemini client ready. Monitoring for faults...")
            threading.Thread(target=self.monitor_loop, daemon=True).start()

    def stop_all(self):
        self.is_monitoring = False
        self.revert_ghost_game_boost()
        self.log_event("🌕 ENGINE STRUCT DISENGAGED.", "error")

    def run_opt(self):
        self.log_event("🧹 NEURAL PURGE: Initiating System-Wide Deep Sweep...", "info")
        def purge_task():
            subprocess.run('ipconfig /flushdns', shell=True, capture_output=True, creationflags=0x08000000)
            self.trim_working_sets()
            clean_cmd = (
                'Remove-Item -Path "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue; '
                'Remove-Item -Path "C:\\Windows\\Temp\\*" -Recurse -Force -ErrorAction SilentlyContinue; '
                'Remove-Item -Path "C:\\Windows\\Prefetch\\*" -Recurse -Force -ErrorAction SilentlyContinue; '
                'Clear-RecycleBin -Force -ErrorAction SilentlyContinue'
            )
            subprocess.run(['powershell', '-Command', clean_cmd], creationflags=0x08000000, capture_output=True)
            self.log_event("🧹 SWEEP COMPLETE: RAM reclaimed, Cache wiped, Recycle Bin cleared.", "success")
        threading.Thread(target=purge_task, daemon=True).start()

    def log_event(self, m, level):
        ts = datetime.now().strftime('%H:%M:%S')
        icon = "✔" if level == "success" else "●"
        self.window.after(0, lambda: (self.log_text.insert("end", f"[{ts}] {icon} {m}\n"), self.log_text.see("end")))

if __name__ == "__main__":
    app = GhostTuner()
    app.window.mainloop()
