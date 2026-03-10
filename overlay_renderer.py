#!/usr/bin/env python3
"""
mutemotion native overlay renderer (python-xlib implementation)
=============================================================
this is the gamescope bypass - runs as a SEPARATE PROCESS from the decky plugin.

we MUST create a 32-bit TrueColor visual with an alpha channel, or Gamescope
will paint the background as solid black and obscure the game.

uses python-xlib, which is natively available on SteamOS.
"""

import socket
import os
import sys
import time
import math
import signal
import json
from Xlib import X, display, Xatom
from Xlib.ext import shape

# ============================================================
# overlay config
# ============================================================
SCREEN_W = 1280  # steam deck LCD resolution
SCREEN_H = 800
SOCK_PATH = "/tmp/mutemotion.sock"
TARGET_FPS = 90

# ============================================================
# main overlay class
# ============================================================
class NativeOverlay:
    def __init__(self):
        self.running = True
        self.display = None
        self.screen = None
        self.window = None
        self.gc = None
        
        # imu state
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.mode = "bar"  # "bar", "ball"
        self.tick = 0
        
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        print(f"[OVERLAY] caught signal {signum}, shutting down...")
        self.running = False
    
    def get_32bit_visual(self):
        """Find the 32-bit TrueColor visual for alpha transparency"""
        for depth_info in self.screen.allowed_depths:
            if depth_info.depth == 32:
                for v in depth_info.visuals:
                    if v.visual_class == X.TrueColor:
                        return v.visual_id
        return None

    def create_window(self):
        """create a frameless 32-bit transparent X11 window with GAMESCOPE_EXTERNAL_OVERLAY"""
        display_env = os.environ.get("DISPLAY", ":0")
        try:
            self.display = display.Display(display_env)
        except Exception:
            try:
                self.display = display.Display(":1")
            except Exception as e:
                print(f"[OVERLAY] FATAL: cant connect to X display: {e}")
                sys.exit(1)
        
        self.screen = self.display.screen()
        
        # Must request the 32-bit Visual for gamescope compositor transparency!
        visual_id = self.get_32bit_visual()
        if not visual_id:
            print("[OVERLAY] FATAL: System does not support 32-bit visuals. Transparency impossible.")
            sys.exit(1)
            
        colormap = self.screen.root.create_colormap(visual_id, X.AllocNone)
        
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        print(f"[OVERLAY] screen: {sw}x{sh}, visual=32-bit TrueColor")
        
        self.window = self.screen.root.create_window(
            0, 0, sw, sh, 0,
            32,  # 32-bit depth for Alpha channel
            X.InputOutput,
            visual_id,
            background_pixel=0,
            border_pixel=0,
            colormap=colormap,
            override_redirect=True,
            event_mask=X.ExposureMask
        )
        
        # === THE MAGIC: set GAMESCOPE_EXTERNAL_OVERLAY atom ===
        atom_overlay = self.display.intern_atom("GAMESCOPE_EXTERNAL_OVERLAY")
        self.window.change_property(
            atom_overlay, Xatom.CARDINAL, 32,
            [1], X.PropModeReplace
        )
        print(f"[OVERLAY] set GAMESCOPE_EXTERNAL_OVERLAY atom on window")
        
        self.gc = self.window.create_gc(
            foreground=self.screen.white_pixel,
            background=self.screen.black_pixel
        )
        
        print("[OVERLAY] Mapping window...")
        self.window.map()
        self.display.flush()
    
    def _hex_to_rgb(self, hex_val):
        """In 32-bit ARGB, format is AARRGGBB"""
        # For full opacity, alpha channel must be 0xFF
        return 0xFF000000 | hex_val

    def draw_test_rectangle(self):
        """phase 1 validation animation"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Red bar
        self.gc.change(foreground=self._hex_to_rgb(0xFF0044))
        bar_w = int(sw * 0.6)
        bar_h = 6
        bar_x = (sw - bar_w) // 2
        bar_y = sh // 2
        
        self.tick += 1
        y_offset = int(math.sin(self.tick / 60.0) * 200)
        
        self.window.fill_rectangle(self.gc, bar_x, bar_y + y_offset, bar_w, bar_h)
        
        # Green dot
        self.gc.change(foreground=self._hex_to_rgb(0x00FFCC))
        dot_x = sw // 2 + int(math.sin(self.tick / 45.0) * 200)
        dot_y = sh // 2 + int(math.cos(self.tick / 30.0) * 150)
        
        # Xlib arc expects 64ths of a degree
        self.window.fill_arc(self.gc, dot_x - 15, dot_y - 15, 30, 30, 0, 360 * 64)
        self.display.flush()
    
    def draw_imu_bar(self, offset_x, offset_y):
        """draw the horizon bar using live IMU data"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Red line
        self.gc.change(
            foreground=self._hex_to_rgb(0xFF0044),
            line_width=4
        )
        bar_w = int(sw * 0.9)
        bar_y = sh // 2 + int(offset_y * 6)
        
        cx = sw // 2
        cy = bar_y
        angle_rad = offset_x * 0.01
        half_w = bar_w // 2
        
        x1 = int(cx - half_w * math.cos(angle_rad))
        y1 = int(cy + half_w * math.sin(angle_rad))
        x2 = int(cx + half_w * math.cos(angle_rad))
        y2 = int(cy - half_w * math.sin(angle_rad))
        
        self.window.line(self.gc, x1, y1, x2, y2)
        self.display.flush()
    
    def draw_imu_ball(self, offset_x, offset_y):
        """draw the tracking ball using live IMU data"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Crosshair grey
        self.gc.change(foreground=self._hex_to_rgb(0x444444), line_width=1)
        self.window.line(self.gc, sw//2 - 10, sh//2, sw//2 + 10, sh//2)
        self.window.line(self.gc, sw//2, sh//2 - 10, sw//2, sh//2 + 10)
        
        # Green ball
        self.gc.change(foreground=self._hex_to_rgb(0x00FFCC))
        bx = int(sw // 2 + max(-400, min(400, offset_x * 8)))
        by = int(sh // 2 + max(-250, min(250, offset_y * 8)))
        self.window.fill_arc(self.gc, bx - 15, by - 15, 30, 30, 0, 360 * 64)
        
        self.display.flush()
    
    def connect_ipc(self):
        """connect to the python backend's unix socket for live data"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.connect(SOCK_PATH)
            print(f"[OVERLAY] connected to IPC socket {SOCK_PATH}")
            return sock
        except (ConnectionRefusedError, FileNotFoundError):
            return None
    
    def run(self):
        self.create_window()
        ipc_sock = self.connect_ipc()
        ipc_buffer = b""
        frame_time = 1.0 / TARGET_FPS
        
        print(f"[OVERLAY] entering render loop at {TARGET_FPS}fps")
        
        while self.running:
            t_start = time.time()
            
            # handle incoming IPC data
            if ipc_sock:
                try:
                    chunk = ipc_sock.recv(1024)
                    if chunk:
                        ipc_buffer += chunk
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
                        ipc_sock = self.connect_ipc()
                except BlockingIOError:
                    pass
                except Exception:
                    ipc_sock = self.connect_ipc()
                    ipc_buffer = b""
            else:
                if self.tick % (TARGET_FPS * 2) == 0:
                    ipc_sock = self.connect_ipc()
            
            # draw next frame
            if ipc_sock and (self.offset_x != 0 or self.offset_y != 0):
                if self.mode == "ball":
                    self.draw_imu_ball(self.offset_x, self.offset_y)
                else:
                    self.draw_imu_bar(self.offset_x, self.offset_y)
            else:
                self.draw_test_rectangle()
            
            # frame pacing
            elapsed = time.time() - t_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        print("[OVERLAY] shutting down...")
        if ipc_sock:
            ipc_sock.close()
        if self.display and self.window:
            self.window.unmap()
            self.display.close()
        print("[OVERLAY] clean exit")

if __name__ == "__main__":
    print(f"[OVERLAY] starting mutemotion native overlay renderer (PID={os.getpid()})...")
    overlay = NativeOverlay()
    overlay.run()
