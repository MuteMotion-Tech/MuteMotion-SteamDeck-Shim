import decky_plugin
import sys
import os
import threading
import struct
import time
import math

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
        multiplexed hidraw polling loop
        opens ALL neptune endpoints and listens on all of them simultaneously
        using select() bc epoll is overkill and this isnt a kernel driver
        
        anti-zero-lock: skips endpoints sending all zeros (dead/inactive)
        auto-locks onto whichever endpoint has actual IMU data
        """
        decky_plugin.logger.info("starting multiplexed hidraw polling...")
        import select

        # wait for neptune to show up (might not be ready on boot)
        hidraw_paths = []
        while self._running:
            hidraw_paths = self._find_hidraw_devices()
            if hidraw_paths:
                decky_plugin.logger.info(f"neptune found: {hidraw_paths}")
                break
            time.sleep(2)  # chill and try again

        if not self._running:
            return

        # open all endpoints in non-blocking mode
        fds = []
        for path in hidraw_paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                fds.append(fd)
                decky_plugin.logger.info(f"opened {path} (fd={fd})")
            except Exception as e:
                decky_plugin.logger.error(f"cant open {path}: {e}")

        if not fds:
            decky_plugin.logger.error("no hidraw endpoints opened. sensor data unavailable.")
            return

        tick_counter = 0
        imu_active_endpoint = None

        while self._running:
            try:
                # poll all fds at once - whoever has data, we read it
                readable, _, _ = select.select(fds, [], [], 1.0)
                for r_fd in readable:
                    data = os.read(r_fd, 64)
                    if len(data) == 64:
                        # grab the IMU payload (bytes 24-36, 6x int16_t)
                        raw_imu = data[24:36]

                        # anti-zero lock: skip dead endpoints
                        # some hidraw endpoints send all zeros, useless
                        if raw_imu == b'\x00' * 12:
                            continue

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

            except BlockingIOError:
                pass  # non-blocking read, nothing available, thats fine
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

    async def get_visual_offset(self, **kwargs):
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
            ox = float(self.accel_x) * 30.0   # lateral
            oy = float(self.accel_z) * 40.0   # forward/back

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
        """plugin shutdown - stop the thread and clean up"""
        self._running = False
        decky_plugin.logger.info("MuteMotion Shim dismounted. see u next boot.")
