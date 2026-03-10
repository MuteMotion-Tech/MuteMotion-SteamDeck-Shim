#!/usr/bin/env python3
"""
mutemotion native overlay renderer
===================================
this is the gamescope bypass - runs as a SEPARATE PROCESS from the decky plugin
so steam cant SIGSTOP us when it freezes the CEF frontend

uses raw ctypes to libX11 so we dont need ANY pip dependencies
(steamos has libX11.so in the base image, always)

the magic trick: GAMESCOPE_EXTERNAL_OVERLAY atom on our X11 window
gamescope sees this and composites us on top of the game layer
independent of the steam UI / QAM state

launched by main.py as a subprocess when user enables the overlay
receives IMU data via unix domain socket from the sensor thread
"""

import ctypes
import ctypes.util
import struct
import socket
import os
import sys
import time
import math
import signal
import json

# ============================================================
# X11 ctypes bindings (zero deps, just needs libX11.so)
# ============================================================

# load X11 shared lib
_x11_path = ctypes.util.find_library("X11")
if not _x11_path:
    print("[OVERLAY] FATAL: libX11.so not found, cant create overlay window")
    sys.exit(1)

_x11 = ctypes.cdll.LoadLibrary(_x11_path)

# X11 constants
ExposureMask = 1 << 15
StructureNotifyMask = 1 << 17
InputOutput = 1
CopyFromParent = 0
CWBackPixel = 1 << 1
CWBorderPixel = 1 << 3
CWOverrideRedirect = 1 << 9
CWColormap = 1 << 13
CWEventMask = 1 << 11
PropModeReplace = 0
XA_CARDINAL = 6
TrueColor = 4
AllocNone = 0

# X11 structures
class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong),
        ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong),
        ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("cursor", ctypes.c_ulong),
    ]

class XVisualInfo(ctypes.Structure):
    _fields_ = [
        ("visual", ctypes.c_void_p),
        ("visualid", ctypes.c_ulong),
        ("screen", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("class_", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
        ("colormap_size", ctypes.c_int),
        ("bits_per_rgb", ctypes.c_int),
    ]

# X11 function signatures
_x11.XOpenDisplay.restype = ctypes.c_void_p
_x11.XOpenDisplay.argtypes = [ctypes.c_char_p]

_x11.XDefaultScreen.restype = ctypes.c_int
_x11.XDefaultScreen.argtypes = [ctypes.c_void_p]

_x11.XDefaultRootWindow.restype = ctypes.c_ulong
_x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]

_x11.XCreateWindow.restype = ctypes.c_ulong
_x11.XCreateWindow.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong,  # display, parent
    ctypes.c_int, ctypes.c_int,        # x, y
    ctypes.c_uint, ctypes.c_uint,      # width, height
    ctypes.c_uint, ctypes.c_int,       # border_width, depth
    ctypes.c_uint, ctypes.c_void_p,    # class, visual
    ctypes.c_ulong,                    # valuemask
    ctypes.POINTER(XSetWindowAttributes)  # attributes
]

_x11.XMapWindow.restype = ctypes.c_int
_x11.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

_x11.XInternAtom.restype = ctypes.c_ulong
_x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]

_x11.XChangeProperty.restype = ctypes.c_int
_x11.XChangeProperty.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong,  # display, window
    ctypes.c_ulong, ctypes.c_ulong,   # property, type
    ctypes.c_int, ctypes.c_int,       # format, mode
    ctypes.c_void_p, ctypes.c_int     # data, nelements
]

_x11.XFlush.restype = ctypes.c_int
_x11.XFlush.argtypes = [ctypes.c_void_p]

_x11.XSync.restype = ctypes.c_int
_x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

_x11.XDefaultGC.restype = ctypes.c_void_p
_x11.XDefaultGC.argtypes = [ctypes.c_void_p, ctypes.c_int]

_x11.XSetForeground.restype = ctypes.c_int
_x11.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]

_x11.XFillRectangle.restype = ctypes.c_int
_x11.XFillRectangle.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint
]

_x11.XFillArc.restype = ctypes.c_int
_x11.XFillArc.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
    ctypes.c_int, ctypes.c_int
]

_x11.XClearWindow.restype = ctypes.c_int
_x11.XClearWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

_x11.XDisplayWidth.restype = ctypes.c_int
_x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]

_x11.XDisplayHeight.restype = ctypes.c_int
_x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]

_x11.XDestroyWindow.restype = ctypes.c_int
_x11.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

_x11.XCloseDisplay.restype = ctypes.c_int
_x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

_x11.XDrawLine.restype = ctypes.c_int
_x11.XDrawLine.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
]

_x11.XSetLineAttributes.restype = ctypes.c_int
_x11.XSetLineAttributes.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int
]

# ============================================================
# overlay config
# ============================================================
SCREEN_W = 1280  # steam deck LCD resolution
SCREEN_H = 800
SOCK_PATH = "/tmp/mutemotion.sock"
TARGET_FPS = 60

# ============================================================
# main overlay class
# ============================================================
class NativeOverlay:
    def __init__(self):
        self.running = True
        self.display = None
        self.window = None
        self.gc = None
        self.screen = 0
        
        # imu state (updated via socket)
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.mode = "bar"  # "bar", "ball", "debug"
        self.tick = 0
        
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        print(f"[OVERLAY] caught signal {signum}, shutting down...")
        self.running = False
    
    def create_window(self):
        """create a frameless transparent X11 window with GAMESCOPE_EXTERNAL_OVERLAY"""
        # connect to X display (gamescope's xwayland)
        display_env = os.environ.get("DISPLAY", ":0")
        self.display = _x11.XOpenDisplay(display_env.encode())
        if not self.display:
            # try :1 as fallback (gamescope sometimes uses :1)
            self.display = _x11.XOpenDisplay(b":1")
        if not self.display:
            print("[OVERLAY] FATAL: cant connect to X display")
            sys.exit(1)
        
        self.screen = _x11.XDefaultScreen(self.display)
        root = _x11.XDefaultRootWindow(self.display)
        
        # get screen dimensions
        sw = _x11.XDisplayWidth(self.display, self.screen)
        sh = _x11.XDisplayHeight(self.display, self.screen)
        print(f"[OVERLAY] screen: {sw}x{sh}")
        
        # create window attributes
        attrs = XSetWindowAttributes()
        attrs.override_redirect = 1  # no window decorations
        attrs.background_pixel = 0   # black bg (transparent with 32bit depth)
        attrs.border_pixel = 0
        attrs.event_mask = ExposureMask | StructureNotifyMask
        
        # create the window (fullscreen, no decorations)
        self.window = _x11.XCreateWindow(
            self.display, root,
            0, 0, sw, sh,  # x, y, w, h
            0,             # border width
            CopyFromParent,  # depth
            InputOutput,   # class
            None,          # visual (default)
            CWOverrideRedirect | CWBackPixel | CWBorderPixel | CWEventMask,
            ctypes.byref(attrs)
        )
        
        if not self.window:
            print("[OVERLAY] FATAL: XCreateWindow failed")
            sys.exit(1)
        
        # === THE MAGIC: set GAMESCOPE_EXTERNAL_OVERLAY atom ===
        atom = _x11.XInternAtom(self.display, b"GAMESCOPE_EXTERNAL_OVERLAY", 0)
        value = (ctypes.c_ulong * 1)(1)
        _x11.XChangeProperty(
            self.display, self.window,
            atom, XA_CARDINAL, 32,
            PropModeReplace,
            ctypes.cast(value, ctypes.c_void_p),
            1
        )
        print(f"[OVERLAY] set GAMESCOPE_EXTERNAL_OVERLAY atom on window {self.window}")
        
        # map (show) the window
        _x11.XMapWindow(self.display, self.window)
        _x11.XSync(self.display, 0)
        
        # grab the default GC for drawing
        self.gc = _x11.XDefaultGC(self.display, self.screen)
        
        print(f"[OVERLAY] window created and mapped, gamescope should composite us now")
    
    def draw_test_rectangle(self):
        """phase 1 test: just draw a red rectangle to prove the atom works"""
        sw = _x11.XDisplayWidth(self.display, self.screen)
        sh = _x11.XDisplayHeight(self.display, self.screen)
        
        # clear window (black)
        _x11.XClearWindow(self.display, self.window)
        
        # draw a red rectangle in the center
        # red = 0xFF0000
        _x11.XSetForeground(self.display, self.gc, 0xFF0044)
        bar_w = int(sw * 0.6)
        bar_h = 6
        bar_x = (sw - bar_w) // 2
        bar_y = sh // 2
        
        # animate: oscillate the bar up/down with a sine wave
        self.tick += 1
        y_offset = int(math.sin(self.tick / 60.0) * 200)
        angle = math.sin(self.tick / 60.0) * 15  # degrees
        
        # draw the bar
        _x11.XFillRectangle(
            self.display, self.window, self.gc,
            bar_x, bar_y + y_offset, bar_w, bar_h
        )
        
        # draw a green dot (ball mode test)
        _x11.XSetForeground(self.display, self.gc, 0x00FFCC)
        dot_x = sw // 2 + int(math.sin(self.tick / 45.0) * 200)
        dot_y = sh // 2 + int(math.cos(self.tick / 30.0) * 150)
        _x11.XFillArc(
            self.display, self.window, self.gc,
            dot_x - 15, dot_y - 15, 30, 30,
            0, 360 * 64  # full circle (X11 uses 64ths of degrees)
        )
        
        _x11.XFlush(self.display)
    
    def draw_imu_bar(self, offset_x, offset_y):
        """draw the horizon bar using live IMU data"""
        sw = _x11.XDisplayWidth(self.display, self.screen)
        sh = _x11.XDisplayHeight(self.display, self.screen)
        
        _x11.XClearWindow(self.display, self.window)
        
        # red bar
        _x11.XSetForeground(self.display, self.gc, 0xFF0044)
        bar_w = int(sw * 0.9)
        bar_h = 4
        bar_x = (sw - bar_w) // 2
        bar_y = sh // 2 + int(offset_y * 6)
        
        # rotation via line endpoints (ghetto rotation matrix)
        cx = sw // 2
        cy = bar_y
        angle_rad = offset_x * 0.01  # convert to radians-ish
        half_w = bar_w // 2
        
        x1 = int(cx - half_w * math.cos(angle_rad))
        y1 = int(cy + half_w * math.sin(angle_rad))
        x2 = int(cx + half_w * math.cos(angle_rad))
        y2 = int(cy - half_w * math.sin(angle_rad))
        
        _x11.XSetLineAttributes(self.display, self.gc, 4, 0, 0, 0)  # 4px thick
        _x11.XDrawLine(self.display, self.window, self.gc, x1, y1, x2, y2)
        
        _x11.XFlush(self.display)
    
    def draw_imu_ball(self, offset_x, offset_y):
        """draw the tracking ball using live IMU data"""
        sw = _x11.XDisplayWidth(self.display, self.screen)
        sh = _x11.XDisplayHeight(self.display, self.screen)
        
        _x11.XClearWindow(self.display, self.window)
        
        # crosshair
        _x11.XSetForeground(self.display, self.gc, 0x333333)
        _x11.XDrawLine(self.display, self.window, self.gc, sw//2 - 10, sh//2, sw//2 + 10, sh//2)
        _x11.XDrawLine(self.display, self.window, self.gc, sw//2, sh//2 - 10, sw//2, sh//2 + 10)
        
        # green ball
        _x11.XSetForeground(self.display, self.gc, 0x00FFCC)
        bx = int(sw // 2 + max(-400, min(400, offset_x * 8)))
        by = int(sh // 2 + max(-250, min(250, offset_y * 8)))
        _x11.XFillArc(self.display, self.window, self.gc,
                      bx - 15, by - 15, 30, 30, 0, 360 * 64)
        
        _x11.XFlush(self.display)
    
    def connect_ipc(self):
        """connect to the python backend's unix socket for IMU data"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.connect(SOCK_PATH)
            print(f"[OVERLAY] connected to IPC socket {SOCK_PATH}")
            return sock
        except (ConnectionRefusedError, FileNotFoundError):
            print(f"[OVERLAY] IPC socket not available yet, running in test mode")
            return None
    
    def run(self):
        """main render loop"""
        self.create_window()
        
        # try to connect to IPC (non-blocking, falls back to test animation)
        ipc_sock = self.connect_ipc()
        ipc_buffer = b""
        
        frame_time = 1.0 / TARGET_FPS
        
        print(f"[OVERLAY] entering render loop at {TARGET_FPS}fps")
        
        while self.running:
            t_start = time.time()
            
            # try to read IMU data from IPC socket
            if ipc_sock:
                try:
                    chunk = ipc_sock.recv(1024)
                    if chunk:
                        ipc_buffer += chunk
                        # parse latest complete JSON line
                        while b"\n" in ipc_buffer:
                            line, ipc_buffer = ipc_buffer.split(b"\n", 1)
                            try:
                                data = json.loads(line)
                                self.offset_x = data.get("offset_x", 0)
                                self.offset_y = data.get("offset_y", 0)
                                self.mode = data.get("mode", self.mode)
                            except json.JSONDecodeError:
                                pass
                    elif chunk == b"":
                        # socket closed, try reconnect
                        ipc_sock = self.connect_ipc()
                        ipc_buffer = b""
                except BlockingIOError:
                    pass  # no data yet, thats fine
                except Exception:
                    ipc_sock = self.connect_ipc()
                    ipc_buffer = b""
            else:
                # no IPC yet, try reconnecting every 2 seconds
                if self.tick % (TARGET_FPS * 2) == 0:
                    ipc_sock = self.connect_ipc()
            
            # render based on mode
            if ipc_sock and (self.offset_x != 0 or self.offset_y != 0):
                if self.mode == "ball":
                    self.draw_imu_ball(self.offset_x, self.offset_y)
                else:
                    self.draw_imu_bar(self.offset_x, self.offset_y)
            else:
                # test animation (phase 1 validation)
                self.draw_test_rectangle()
            
            # frame timing
            elapsed = time.time() - t_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        # cleanup
        print("[OVERLAY] shutting down...")
        if ipc_sock:
            ipc_sock.close()
        if self.display and self.window:
            _x11.XDestroyWindow(self.display, self.window)
            _x11.XCloseDisplay(self.display)
        print("[OVERLAY] clean exit")


if __name__ == "__main__":
    print("[OVERLAY] starting mutemotion native overlay renderer...")
    print(f"[OVERLAY] PID={os.getpid()}, DISPLAY={os.environ.get('DISPLAY', 'unset')}")
    overlay = NativeOverlay()
    overlay.run()
