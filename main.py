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

# make py_modules importable
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
    core_engine = None


class Plugin:
    def __init__(self):
        self._running = True
        self._reader_thread = None
        # raw sensor values (after divisor + axis swap)
        self.gyro_x = 0.0
        self.gyro_y = 0.0
        self.gyro_z = 0.0
        self.accel_x = 0.0
        self.accel_y = 0.0
        self.accel_z = 0.0
        self.last_offset = 0.0
        # watchdog diagnostics (exposed via get_watchdog_status RPC)
        self._last_data_timestamp = 0.0
        self._watchdog_fires = 0
        self._active_fd_count = 0
        self._reader_alive = False
        # native overlay subprocess (gamescope bypass)
        self._overlay_process = None
        self._ipc_clients = []  # connected overlay renderers
        self._ipc_server_sock = None
        self._overlay_mode = "bar"  # "bar" or "ball"

    def _find_hidraw_devices(self):
        """scan /sys/class/hidraw/ for neptune controller endpoints (28DE:1205)"""
        base_path = "/sys/class/hidraw/"
        devices = []
        if not os.path.exists(base_path):
            return devices  # not on a device with hidraw, probably dev pc

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
        multiplexed hidraw polling loop with watchdog recovery
        opens ALL neptune endpoints and listens on all of them simultaneously
        using select() bc epoll is overkill and this isnt a kernel driver
        
        anti-zero-lock: skips endpoints sending all zeros (dead/inactive)
        auto-locks onto whichever endpoint has actual IMU data
        
        WATCHDOG: if no data arrives for 2 seconds, assumes steam/gamescope
        stole the device. closes everything and re-scans hidraw endpoints.
        this is the gamescope bypass - we just keep trying until steam lets go.
        """
        decky_plugin.logger.info("starting multiplexed hidraw polling w/ watchdog...")
        import select

        WATCHDOG_TIMEOUT = 2.0  # seconds of silence before we re-scan
        RESCAN_COOLDOWN = 1.0   # dont spam re-scans, chill between attempts

        def open_neptune_fds():
            """find and open all neptune hidraw endpoints, returns list of fds"""
            hidraw_paths = self._find_hidraw_devices()
            opened = []
            for path in hidraw_paths:
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    opened.append(fd)
                    decky_plugin.logger.info(f"opened {path} (fd={fd})")
                except Exception as e:
                    decky_plugin.logger.error(f"cant open {path}: {e}")
            return opened

        # initial device scan - wait for neptune to show up on boot
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

                        # the cursed axis swap (hardware Y <-> software Z)
                        # neptune decided left is up and up is sideways
                        # incident 034 wants you to remember this
                        self.accel_x = raw_accel_x / 16384.0   # lateral
                        self.accel_y = raw_accel_z / 16384.0   # swapped!
                        self.accel_z = raw_accel_y / 16384.0   # swapped!

                        self.gyro_x = raw_gyro_x / 16.0
                        self.gyro_y = raw_gyro_z / 16.0        # swapped!
                        self.gyro_z = raw_gyro_y / 16.0        # swapped!

                        tick_counter += 1
                        if tick_counter % 60 == 0:
                            decky_plugin.logger.info(
                                f"[SENSOR] AX:{self.accel_x:.2f}g "
                                f"AZ:{self.accel_z:.2f}g "
                                f"GZ:{self.gyro_z:.1f}dps"
                            )

                        # feed the brain
                        if core_engine:
                            gyro_tup = (self.gyro_x, self.gyro_y, self.gyro_z)
                            accel_tup = (self.accel_x, self.accel_y, self.accel_z)
                            # the frontend uses separate axes for 2D ball mode
                            # lateral (side-to-side tilt) + forward (vehicle accel/brake)
                            lateral = self.accel_x * 30.0
                            forward = self.accel_y * 40.0
                            self.last_offset = lateral + forward

                        # push IMU data to native overlay via IPC socket
                        self._push_ipc_data()

                # === WATCHDOG: detect steam/gamescope stealing our device ===
                if time.time() - last_data_time > WATCHDOG_TIMEOUT:
                    self._watchdog_fires += 1
                    decky_plugin.logger.info(f"[WATCHDOG] fire #{self._watchdog_fires}, re-scanning hidraw devices...")
                    
                    # close all stale fds
                    for fd in fds:
                        try:
                            os.close(fd)
                        except:
                            pass
                    
                    # cooldown so we dont hammer the filesystem
                    time.sleep(RESCAN_COOLDOWN)
                    
                    # re-open fresh
                    fds = open_neptune_fds()
                    imu_active_endpoint = None
                    
                    self._active_fd_count = len(fds)
                    if fds:
                        last_data_time = time.time()  # reset watchdog
                        decky_plugin.logger.info(f"[WATCHDOG] re-acquired {len(fds)} endpoints, resuming...")
                    else:
                        decky_plugin.logger.error("[WATCHDOG] neptune disappeared, will retry in 2s...")
                        time.sleep(2)
                        last_data_time = time.time()  # prevent watchdog spam

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

    # === DECKY CALLABLE API ===
    # the frontend calls these via call("function_name")
    # they return python dicts that become JS objects

    async def get_visual_offset(self, *args, **kwargs):
        """
        the main data endpoint - frontend polls this at ~60fps
        returns everything the overlay needs to render
        """
        if not core_engine:
            # safe mode: return error sentinel values
            # frontend checks for -99.9 to know something is wrong
            return {
                "offset": -99.9, "offset_x": 0, "offset_y": 0,
                "rx": -99.9, "ry": -99.9, "rz": -99.9,
                "ax": -99.9, "ay": -99.9, "az": -99.9
            }

        try:
            offset = getattr(self, 'last_offset', 0.0)
            # separate axes for 2D ball mode
            # accel_x = lateral (side-to-side tilt)
            # accel_z = forward/backward tilt (hardware Y after axis swap)
            ox = float(self.accel_x) * -30.0  # inverted so tilt left = ball goes left
            oy = float(self.accel_z) * 40.0   # forward/back (this one was already correct)

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
            return {
                "offset": -88.8, "offset_x": 0, "offset_y": 0,
                "rx": -88.8, "ry": -88.8, "rz": -88.8,
                "ax": -88.8, "ay": -88.8, "az": -88.8
            }

    async def ping_engine(self, *args, **kwargs):
        """test endpoint to verify RPC bridge is alive - now with toast notifications"""
        decky_plugin.logger.info("MuteMotion Engine Ping Received!")
        if core_engine:
            return {"status": "online", "message": "Core is Active"}
        else:
            return {"status": "fallback", "message": "Core is Offline (Safe Mode)"}

    async def get_watchdog_status(self, *args, **kwargs):
        """diagnostics endpoint - shows sensor thread health in the QAM"""
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
        """start unix domain socket server for overlay IPC"""
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
        """non-blocking accept of new overlay connections"""
        if not self._ipc_server_sock:
            return
        try:
            client, _ = self._ipc_server_sock.accept()
            client.setblocking(False)
            self._ipc_clients.append(client)
            decky_plugin.logger.info(f"[IPC] overlay renderer connected ({len(self._ipc_clients)} clients)")
        except BlockingIOError:
            pass  # no new connections, thats fine
    
    def _push_ipc_data(self):
        """push latest IMU data to all connected overlay renderers"""
        self._accept_ipc_clients()  # check for new connections
        
        if not self._ipc_clients:
            return
        
        # build the data packet
        ox = float(self.accel_x) * -30.0
        oy = float(self.accel_z) * 40.0
        data = json.dumps({
            "offset_x": ox,
            "offset_y": oy,
            "mode": self._overlay_mode,
        }).encode() + b"\n"
        
        # send to all clients, remove dead ones
        dead = []
        for client in self._ipc_clients:
            try:
                client.sendall(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead.append(client)
        
        for d in dead:
            try:
                d.close()
            except:
                pass
            self._ipc_clients.remove(d)
    
    async def start_native_overlay(self, *args, **kwargs):
        """spawn the native overlay renderer subprocess"""
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
        
        # spawn as independent subprocess
        # inherit DISPLAY so it can connect to xwayland
        env = os.environ.copy()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"  # gamescope default
        
        try:
            self._overlay_process = subprocess.Popen(
                ["python3", overlay_script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True  # independent from decky process tree
            )
            decky_plugin.logger.info(f"[OVERLAY] spawned native renderer PID={self._overlay_process.pid}")
            return {"status": "started", "message": f"Overlay PID {self._overlay_process.pid}", "pid": self._overlay_process.pid}
        except Exception as e:
            decky_plugin.logger.error(f"[OVERLAY] failed to spawn: {e}")
            return {"status": "error", "message": str(e)}
    
    async def stop_native_overlay(self, *args, **kwargs):
        """kill the native overlay renderer subprocess"""
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
        """switch between bar and ball mode on the native overlay"""
        if args:
            mode_input = args[0] if isinstance(args[0], str) else str(args[0])
        else:
            mode_input = kwargs.get("mode", "bar")
        self._overlay_mode = mode_input
        decky_plugin.logger.info(f"[OVERLAY] mode set to: {self._overlay_mode}")
        return {"mode": self._overlay_mode}

    async def _main(self):
        """plugin startup - load everything and start the sensor thread"""
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
        """plugin shutdown - stop everything and clean up"""
        self._running = False
        # kill native overlay
        await self.stop_native_overlay()
        # cleanup IPC
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
        decky_plugin.logger.info("MuteMotion Shim dismounted. see u next boot.")
