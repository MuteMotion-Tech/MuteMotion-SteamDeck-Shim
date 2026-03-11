# =============================================================================
# MuteMotion Shim — SteamOS Decky Plugin Backend
# =============================================================================
# the python brain behind the curtain. reads IMU data from the Neptune
# controller via hidraw, runs it through sensor fusion, and pushes it
# to both the React frontend (via RPC) and native overlay (via IPC).
#
# fun fact: this file has survived more rewrites than my will to live.
# but we're here. we're shipping. ggs.
# =============================================================================

import decky_plugin
import sys
import os
import threading
import struct
import time
import math
import subprocess
import socket
import json
import signal
import fcntl  # for hidraw ioctl (we tried ioctl FR it doesnt work but we keep the import for vibes)

# make py_modules importable so ctypes can find our .so
plugin_dir = decky_plugin.DECKY_PLUGIN_DIR
py_modules_dir = os.path.join(plugin_dir, "py_modules")
sys.path.append(py_modules_dir)

# load the brain (or die trying)
try:
    from core_bridge import MuteMotionCore
    core_engine = MuteMotionCore(os.path.join(py_modules_dir, "libmutemotion_core.so"))
    decky_plugin.logger.info("Core loaded. neptune is armed and dangerous.")
except Exception as e:
    decky_plugin.logger.error(f"Core failed to load (running in safe mode): {e}")
    core_engine = None  # we'll survive... barely


class Plugin:
    def __init__(self):
        self._running = True
        self._reader_thread = None

        # raw sensor values (after cursed axis swap — more on that below)
        self.gyro_x = 0.0
        self.gyro_y = 0.0
        self.gyro_z = 0.0
        self.accel_x = 0.0
        self.accel_y = 0.0
        self.accel_z = 0.0
        self.last_offset = 0.0

        # watchdog stats (the QAM shows these so we can flex our uptime)
        self._last_data_timestamp = 0.0
        self._watchdog_fires = 0
        self._active_fd_count = 0
        self._reader_alive = False

        # complementary filter states (α=0.98 gang)
        self.pitch = 0.0
        self.roll = 0.0
        self.last_imu_time = 0.0

        # native overlay process (the gamescope bypass that actually works)
        self._overlay_process = None
        self._ipc_clients = []      # connected overlay renderers
        self._ipc_server_sock = None
        self._overlay_mode = "bar"  # "bar" or "ball" — user picks in QAM

        # ===================================================================
        # VDF CONFIGURATION OVERRIDE — the "Decoy Binding" strategy
        # ===================================================================
        # tl;dr: Steam puts the gyro to sleep if no controller profile uses it.
        # we inject a silent gyro_to_mouse group into the default templates,
        # then yell at Steam to reload them via steam:// URI. this tricks
        # Steam into thinking gyro is needed, keeping the IMU awake forever.
        # yes, we literally gaslight the steam client. it's called engineering.
        # ===================================================================
        decky_plugin.logger.info("Executing Configuration Override (Decoy Binding) Injection...")
        try:
            # lazy import — vdf_modifier lives next to us in the plugin dir
            # gotta manually shove our dir into sys.path or python wont find it
            plugin_base = os.path.dirname(os.path.abspath(__file__))
            if plugin_base not in sys.path:
                sys.path.insert(0, plugin_base)
            import vdf_modifier

            # step 1: patch the VDF templates on disk (adds gyro_to_mouse group)
            injections = vdf_modifier.apply_decoy_to_all()

            # step 2: force steam to actually READ our patches
            # (steam caches configs in memory and ignores disk changes. rude.)
            app_id = vdf_modifier.get_running_app_id()
            vdf_modifier.force_apply_gyro_profile(app_id)

        except Exception as e:
            decky_plugin.logger.error(f"VDF injection failed during import/exec: {e}")
            injections = False
        if injections:
            decky_plugin.logger.info("Decoy bindings successfully applied.")
        else:
            decky_plugin.logger.warning("Failed to apply decoy bindings or no configs found.")

    def _find_hidraw_devices(self):
        """scan /sys/class/hidraw/ for neptune controller endpoints (VID:28DE PID:1205)"""
        base_path = "/sys/class/hidraw/"
        devices = []
        if not os.path.exists(base_path):
            return devices  # no hidraw = probably dev pc, carry on

        for dev in os.listdir(base_path):
            dev_path = os.path.join(base_path, dev)
            uevent_path = os.path.join(dev_path, "device", "uevent")
            if os.path.exists(uevent_path):
                try:
                    with open(uevent_path, "r") as f:
                        content = f.read().upper()
                        # neptune controller vibes check
                        if "28DE" in content and "1205" in content:
                            devices.append(os.path.join("/dev", dev))
                except:
                    pass  # permission denied or whatever, move on
        return sorted(devices)

    def _hardware_reader_loop(self):
        """
        THE MAIN EVENT — multiplexed hidraw polling with watchdog recovery.

        opens ALL neptune endpoints and listens on them simultaneously via
        select() because epoll is overkill and this isnt a kernel driver lmao.

        anti-zero-lock: some hidraw endpoints send all zeroes (they're posers).
        we skip those and auto-lock onto whichever fd has actual IMU data.

        WATCHDOG: if no data arrives for 2s, we assume steam/gamescope yeeted
        our device access. close everything, re-scan, keep trying. persistence
        is key. also the VDF decoy binding should prevent this now but we keep
        the watchdog because trust issues are valid.
        """
        decky_plugin.logger.info("starting multiplexed hidraw polling w/ watchdog...")
        import select

        WATCHDOG_TIMEOUT = 2.0  # seconds of silence = something is sus
        RESCAN_COOLDOWN = 1.0   # dont spam re-scans, chill between attempts

        def open_neptune_fds():
            """find and open all neptune hidraw endpoints, returns list of fds"""
            hidraw_paths = self._find_hidraw_devices()
            opened = []
            for path in hidraw_paths:
                try:
                    # O_RDWR lets us use ioctl if needed (legacy — VDF bypass is better)
                    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                    opened.append(fd)
                    decky_plugin.logger.info(f"opened {path} (fd={fd})")
                except Exception as e:
                    decky_plugin.logger.error(f"cant open {path}: {e}")
            return opened

        # wait for neptune to show up at the party
        fds = []
        while self._running and not fds:
            fds = open_neptune_fds()
            if not fds:
                decky_plugin.logger.info("waiting for neptune to show up...")
                time.sleep(2)

        if not self._running:
            return


        tick_counter = 0
        imu_active_endpoint = None
        last_data_time = time.time()  # watchdog timer
        self._reader_alive = True

        while self._running:
            try:
                # poll all fds at once - 50ms timeout so watchdog can fire quickly
                readable, _, _ = select.select(fds, [], [], 0.05)
                
                if not readable:
                    # nothing came in this tick. thats fine — the VDF decoy
                    # should be keeping the IMU awake. just a quiet 50ms. nbd.
                    pass

                for r_fd in readable:
                    try:
                        data = os.read(r_fd, 64)
                    except BlockingIOError:
                        continue  # nonblocking, nothing available, skip
                    except OSError:
                        # fd went bad (steam stole it probably)
                        continue

                    if len(data) == 64:
                        # grab the IMU payload (bytes 24-36, 6x int16_t)
                        raw_imu = data[24:36]

                        # anti-zero lock: skip dead endpoints
                        # some hidraw endpoints send all zeros, useless
                        if raw_imu == b'\x00' * 12:
                            continue

                        # WE GOT DATA - reset watchdog
                        last_data_time = time.time()
                        self._last_data_timestamp = last_data_time
                        self._active_fd_count = len(fds)

                        # lock onto the endpoint thats actually sending IMU data
                        if imu_active_endpoint != r_fd:
                            imu_active_endpoint = r_fd
                            decky_plugin.logger.info(f"[MULTIPLEXER] IMU lock on fd={r_fd}")

                        # parse the 6 int16s: accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z
                        parsed = struct.unpack_from('<hhhhhh', data, 24)

                        raw_accel_x = parsed[0]
                        raw_accel_y = parsed[1]
                        raw_accel_z = parsed[2]
                        raw_gyro_x = parsed[3]
                        raw_gyro_y = parsed[4]
                        raw_gyro_z = parsed[5]

                        # THE CURSED AXIS SWAP™ (hardware Y <-> software Z)
                        # whoever designed Neptune's IMU orientation was on something
                        # left is up, up is sideways, and we just have to deal with it
                        # (incident 034 sends its regards)
                        self.accel_x = raw_accel_x / 16384.0   # lateral (this one's normal at least)
                        self.accel_y = raw_accel_z / 16384.0   # swapped! Y ← Z
                        self.accel_z = raw_accel_y / 16384.0   # swapped! Z ← Y

                        self.gyro_x = raw_gyro_x / 16.0        # pitch velocity
                        self.gyro_y = raw_gyro_z / 16.0        # swapped! yaw ← Z
                        self.gyro_z = raw_gyro_y / 16.0        # swapped! roll ← Y

                        # sensor fusion (Complementary Filter)
                        current_time = time.time()
                        if self.last_imu_time == 0:
                            dt = 0.011  # ~90Hz default dt
                        else:
                            dt = current_time - self.last_imu_time
                        self.last_imu_time = current_time

                        # calculate accelerometer euler angles (degrees)
                        # accel_x = lateral, accel_y = up/down swapped, accel_z = forward/back swapped
                        try:
                            # pitch is rotation forward/back (driven by accel_z acting against gravity on accel_y)
                            accel_pitch = math.degrees(math.atan2(self.accel_z, math.sqrt(self.accel_x**2 + self.accel_y**2)))
                            # roll is rotation side-to-side (driven by accel_x acting against gravity on accel_y/z)
                            accel_roll = math.degrees(math.atan2(self.accel_x, math.sqrt(self.accel_y**2 + self.accel_z**2)))
                        except Exception:
                            accel_pitch = 0.0
                            accel_roll = 0.0

                        # SENSOR FUSION — Complementary Filter (α=0.98)
                        # 98% gyro (fast, accurate, drifts over time) +
                        # 2% accelerometer (slow, noisy, but gravity never lies)
                        # this is the secret sauce that makes the overlay buttery smooth
                        alpha = 0.98
                        self.pitch = alpha * (self.pitch + self.gyro_x * dt) + (1.0 - alpha) * accel_pitch
                        self.roll = alpha * (self.roll + self.gyro_z * dt) + (1.0 - alpha) * accel_roll

                        tick_counter += 1
                        if tick_counter % 60 == 0:
                            decky_plugin.logger.info(
                                f"[SENSOR] P:{self.pitch:.1f}° "
                                f"R:{self.roll:.1f}° "
                                f"dt:{dt:.3f}s"
                            )

                        # feed the C++ brain (it does the heavy math we're too lazy to rewrite)
                        if core_engine:
                            gyro_tup = (self.gyro_x, self.gyro_y, self.gyro_z)
                            accel_tup = (self.accel_x, self.accel_y, self.accel_z)
                            self.last_offset = self.roll + self.pitch

                        # yeet IMU data to the native overlay via unix socket
                        self._push_ipc_data()

                # ======= WATCHDOG: did steam steal our devices again? =======
                if time.time() - last_data_time > WATCHDOG_TIMEOUT:
                    self._watchdog_fires += 1
                    decky_plugin.logger.info(f"[WATCHDOG] fire #{self._watchdog_fires}, re-scanning hidraw devices...")

                    # close all stale fds (they're ghosts now)
                    for fd in fds:
                        try:
                            os.close(fd)
                        except:
                            pass

                    # chill for a sec so we dont ddos the filesystem
                    time.sleep(RESCAN_COOLDOWN)

                    # try again. never give up. never surrender.
                    fds = open_neptune_fds()
                    imu_active_endpoint = None

                    self._active_fd_count = len(fds)
                    if fds:
                        last_data_time = time.time()
                        decky_plugin.logger.info(f"[WATCHDOG] re-acquired {len(fds)} endpoints, resuming...")
                    else:
                        decky_plugin.logger.error("[WATCHDOG] neptune vanished into the shadow realm, retrying in 2s...")
                        time.sleep(2)
                        last_data_time = time.time()  # dont spam the logs

            except Exception as e:
                import traceback
                decky_plugin.logger.error(f"hidraw error: {traceback.format_exc()}")
                time.sleep(1)  # dont spam logs if something is busted

        # cleanup: close all file descriptors
        for fd in fds:
            try:
                os.close(fd)
            except:
                pass

    # =================== DECKY RPC API ===================
    # the React frontend calls these via call("function_name")
    # they return python dicts that magically become JS objects
    # shoutout to decky loader for making this braindead simple

    async def get_visual_offset(self, *args, **kwargs):
        """
        THE main data endpoint — frontend polls this at ~60fps.
        returns everything the overlay needs to look alive.
        """
        if not core_engine:
            # safe mode: error sentinel values (-99.9 = core is dead)
            return {
                "offset": -99.9, "offset_x": 0, "offset_y": 0,
                "rx": -99.9, "ry": -99.9, "rz": -99.9,
                "ax": -99.9, "ay": -99.9, "az": -99.9
            }

        try:
            offset = getattr(self, 'last_offset', 0.0)
            ox = float(self.roll) * -1.0  # invert roll for correct screen direction
            oy = float(self.pitch)

            return {
                "offset": float(offset),
                "offset_x": ox,
                "offset_y": oy,
                "rx": float(self.gyro_x),
                "ry": float(self.gyro_y),
                "rz": float(self.gyro_z),
                "ax": float(self.accel_x),
                "ay": float(self.accel_y),
                "az": float(self.accel_z)
            }
        except Exception as e:
            decky_plugin.logger.error(f"get_visual_offset died: {e}")
            # -88.8 = something broke mid-calculation (frontend shows error state)
            return {
                "offset": -88.8, "offset_x": 0, "offset_y": 0,
                "rx": -88.8, "ry": -88.8, "rz": -88.8,
                "ax": -88.8, "ay": -88.8, "az": -88.8
            }

    async def ping_engine(self, *args, **kwargs):
        """health check — the QAM button calls this to make sure we're not dead"""
        decky_plugin.logger.info("MuteMotion Engine Ping Received!")
        if core_engine:
            return {"status": "online", "message": "Core is Active"}
        else:
            return {"status": "fallback", "message": "Core is Offline (Safe Mode)"}

    async def get_watchdog_status(self, *args, **kwargs):
        """diagnostics endpoint — the QAM shows live thread health from this"""
        now = time.time()
        data_age = now - self._last_data_timestamp if self._last_data_timestamp > 0 else -1
        overlay_alive = self._overlay_process is not None and self._overlay_process.poll() is None
        return {
            "thread_alive": self._reader_alive,
            "data_age_seconds": round(data_age, 2),
            "watchdog_fires": self._watchdog_fires,
            "active_fds": self._active_fd_count,
            "last_timestamp": round(self._last_data_timestamp, 2),
            "overlay_alive": overlay_alive,
            "ipc_clients": len(self._ipc_clients),
        }

    def _start_ipc_server(self):
        """spin up the unix socket so the native overlay can talk to us"""
        sock_path = "/tmp/mutemotion.sock"
        # cleanup stale socket
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        
        self._ipc_server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._ipc_server_sock.bind(sock_path)
        self._ipc_server_sock.listen(2)
        self._ipc_server_sock.setblocking(False)
        decky_plugin.logger.info(f"[IPC] socket server listening on {sock_path}")
    
    def _accept_ipc_clients(self):
        """non-blocking — check if the overlay renderer is trying to connect"""
        if not self._ipc_server_sock:
            return
        try:
            client, _ = self._ipc_server_sock.accept()
            client.setblocking(False)
            self._ipc_clients.append(client)
            decky_plugin.logger.info(f"[IPC] overlay renderer connected ({len(self._ipc_clients)} clients)")
        except BlockingIOError:
            pass  # nobody home, thats fine
    
    def _push_ipc_data(self):
        """blast IMU data to all connected overlay renderers via unix socket"""
        self._accept_ipc_clients()  # anyone new?

        if not self._ipc_clients:
            return

        # build the JSON packet — euler angles + current overlay mode
        ox = float(self.roll) * -1.0
        oy = float(self.pitch)
        data = json.dumps({
            "offset_x": ox,
            "offset_y": oy,
            "mode": self._overlay_mode,
        }).encode() + b"\n"
        
        # broadcast to everyone, yeet the dead connections
        dead = []
        for client in self._ipc_clients:
            try:
                client.sendall(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead.append(client)  # rip

        for d in dead:
            try:
                d.close()
            except:
                pass
            self._ipc_clients.remove(d)
    
    async def start_native_overlay(self, *args, **kwargs):
        """spawn the X11 overlay renderer — the one that actually survives gamescope"""
        # kill existing if running
        await self.stop_native_overlay()
        
        # start IPC server if not running
        if not self._ipc_server_sock:
            self._start_ipc_server()
        
        # find the overlay script
        overlay_script = os.path.join(decky_plugin.DECKY_PLUGIN_DIR, "overlay_renderer.py")
        if not os.path.exists(overlay_script):
            decky_plugin.logger.error(f"[OVERLAY] script not found: {overlay_script}")
            return {"status": "error", "message": "overlay_renderer.py not found"}
        
        # spawn as independent process (new session so gamescope cant kill it with decky)
        env = os.environ.copy()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"  # gamescope's default xwayland display

        try:
            self._overlay_process = subprocess.Popen(
                ["python3", overlay_script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True  # escape decky's process tree. you're free now
            )
            decky_plugin.logger.info(f"[OVERLAY] spawned native renderer PID={self._overlay_process.pid}")
            return {"status": "started", "message": f"Overlay PID {self._overlay_process.pid}", "pid": self._overlay_process.pid}
        except Exception as e:
            decky_plugin.logger.error(f"[OVERLAY] failed to spawn: {e}")
            return {"status": "error", "message": str(e)}
    
    async def stop_native_overlay(self, *args, **kwargs):
        """murder the overlay renderer subprocess (gracefully, then not)"""
        if self._overlay_process:
            try:
                pid = self._overlay_process.pid
                # send SIGTERM first (graceful)
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                self._overlay_process.wait(timeout=3)
                decky_plugin.logger.info(f"[OVERLAY] killed renderer PID={pid}")
            except Exception as e:
                # force kill
                try:
                    os.killpg(os.getpgid(self._overlay_process.pid), signal.SIGKILL)
                except:
                    pass
                decky_plugin.logger.info(f"[OVERLAY] force killed renderer")
            self._overlay_process = None
        return {"status": "stopped"}
    
    async def set_overlay_mode(self, *args, **kwargs):
        """switch between bar and ball mode — user picks their vibe in the QAM"""
        if args:
            mode_input = args[0] if isinstance(args[0], str) else str(args[0])
        else:
            mode_input = kwargs.get("mode", "bar")
        self._overlay_mode = mode_input
        decky_plugin.logger.info(f"[OVERLAY] mode set to: {self._overlay_mode}")
        return {"mode": self._overlay_mode}

    async def _main(self):
        """plugin startup — load everything and send the sensor thread to war"""
        decky_plugin.logger.info("MuteMotion Shim initialized")
        if core_engine:
            decky_plugin.logger.info("Core is online. starting hardware interceptor...")
            self._reader_thread = threading.Thread(
                target=self._hardware_reader_loop, daemon=True
            )
            self._reader_thread.start()
        else:
            decky_plugin.logger.error("Core is offline. overlay will show error values.")

    async def _unload(self):
        """plugin shutdown — clean up everything. leave no traces. professional."""
        self._running = False
        # kill the overlay subprocess
        await self.stop_native_overlay()
        # close all IPC connections
        for client in self._ipc_clients:
            try:
                client.close()
            except:
                pass
        if self._ipc_server_sock:
            try:
                self._ipc_server_sock.close()
                os.unlink("/tmp/mutemotion.sock")
            except:
                pass
        decky_plugin.logger.info("MuteMotion Shim dismounted. see u next boot. gg wp. 🫡")
