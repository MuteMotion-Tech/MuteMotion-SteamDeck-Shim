import ctypes
import os
import sys
import math


# xyz doubles bc the kernel constants already got divided out before we get here
class SensorData(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
        ("z", ctypes.c_double)
    ]

class MuteMotionCore:
    """
    ctypes bridge to libmutemotion_core.so
    the middleman between python and the C++ brain
    
    talks to init_core, process_motion, cleanup_core
    thats literally it. three functions. simple as.
    """

    def __init__(self, library_path=None):
        if library_path is None:
            # check next to this script first, then build/lib
            current_dir = os.path.dirname(os.path.abspath(__file__))
            library_path = os.path.join(current_dir, "libmutemotion_core.so")
            if not os.path.exists(library_path):
                # fallback to build dir (for dev environments)
                library_path = os.path.join(current_dir, "build", "lib", "libmutemotion_core.so")

        if not os.path.exists(library_path):
            raise FileNotFoundError(f"cant find the .so at: {library_path} (skill issue)")

        try:
            self.lib = ctypes.CDLL(library_path)

            # tell ctypes what our functions look like
            # void* init_core(double sensitivity)
            self.lib.init_core.argtypes = [ctypes.c_double]
            self.lib.init_core.restype = ctypes.c_void_p

            # double process_motion(void* instance, SensorData gyro, SensorData accel, double dt)
            self.lib.process_motion.argtypes = [ctypes.c_void_p, SensorData, SensorData, ctypes.c_double]
            self.lib.process_motion.restype = ctypes.c_double

            # void cleanup_core(void* instance)
            self.lib.cleanup_core.argtypes = [ctypes.c_void_p]
            self.lib.cleanup_core.restype = None

            # fire it up with default sensitivity
            self.instance = self.lib.init_core(1.0)
            print("[CoreBridge] Library loaded and engine initialized.")

        except Exception as e:
            print(f"[CoreBridge] bruh moment: {e}")
            raise

    def set_sensitivity(self, sensitivity):
        """restart the engine with new sensitivity (theres no setter in C rn)"""
        if self.instance:
            # try to create new instance first — if it fails, keep the old one
            new_instance = self.lib.init_core(float(sensitivity))
            if new_instance:
                self.lib.cleanup_core(self.instance)
                self.instance = new_instance
            else:
                print("[CoreBridge] WARNING: init_core returned NULL (OOM?), keeping old instance")

    def process(self, gyro_tuple, accel_tuple, dt=0.004):
        """
        feed it sensor data, get back an offset
        
        gyro_tuple: (x, y, z) in degrees/sec (already divided by 16.0)
        accel_tuple: (x, y, z) in g-force (already divided by 16384.0)
        dt: time delta in seconds (0.004 = 250Hz)
        
        returns: offset as a double (how far to move the visual indicator)
        """
        if not self.instance:
            return 0.0  # engine is dead, return nothing

        g_data = SensorData(*gyro_tuple)
        a_data = SensorData(*accel_tuple)

        result = self.lib.process_motion(self.instance, g_data, a_data, ctypes.c_double(dt))
        
        # safety net in case something goes wrong in C land
        if math.isnan(result) or math.isinf(result):
            return 0.0
        
        return result

    def __del__(self):
        if hasattr(self, 'instance') and self.instance:
            self.lib.cleanup_core(self.instance)
            print("[CoreBridge] Engine cleaned up. rip lil buddy.")


# standalone verification test (python -m core_bridge)
if __name__ == "__main__":
    print("--- Testing MuteMotion Core Bridge ---")
    try:
        core = MuteMotionCore()

        # same test as the OG core_bridge.py from the steam deck
        # left turn: lateral accel 0.5g, slight roll 0.1 deg/s
        gyro = (0.0, 0.0, 0.1)
        accel = (0.5, 0.0, 0.0)

        # feed it a bunch of frames so the EMA filter converges
        result = 0.0
        for _ in range(200):
            result = core.process(gyro, accel, 0.004)

        print(f"Input: Gyro{gyro}, Accel{accel}")
        print(f"Output (converged): {result}")

        expected = 0.85
        if abs(result - expected) < 0.01:
            print("✅ Bridge verification passed! Core is cooking.")
        else:
            print(f"❌ Mismatch! Expected ~{expected}, got {result}")

    except Exception as e:
        print(f"Test Failed: {e}")
