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
        self.mode = "dotgrid"  # default
        self.intensity = 1.0
        self.tick = 0
        
        # ipc reconnect backoff (exponential: 1s → 2s → 4s → 8s → cap 10s)
        self._ipc_backoff = 1.0
        self._ipc_max_backoff = 10.0
        self._ipc_last_attempt = 0.0
        self._last_ipc_data_time = 0.0  # for idle detection
        
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
    
    def _hex_to_rgb(self, hex_val, alpha=1.0):
        """In 32-bit ARGB, format is AARRGGBB"""
        # For full opacity, alpha channel must be 0xFF
        a_int = max(0, min(255, int(255 * alpha)))
        return (a_int << 24) | (hex_val & 0xFFFFFF)

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
    
    def draw_imu_bar(self, offset_x, offset_y, intensity=1.0, opacity=0.8, invert_axis=True):
        """draw the horizon bar using live IMU data"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Cyan line
        self.gc.change(
            foreground=self._hex_to_rgb(0x00FFCC),
            line_width=4
        )
        bar_w = int(sw * 0.9)
        # pitch (offset_y in degrees): 1 degree = 6 pixels of vertical movement * intensity
        bar_y = sh // 2 + int(offset_y * 6.0 * intensity)
        
        cx = sw // 2
        cy = bar_y
        # roll (offset_x in degrees): scale by intensity, limited rotation still
        scaled_roll = max(-45, min(45, offset_x * 1.5 * intensity))
        angle_rad = math.radians(scaled_roll)
        half_w = bar_w // 2
        
        x1 = int(cx - half_w * math.cos(angle_rad))
        y1 = int(cy + half_w * math.sin(angle_rad))
        x2 = int(cx + half_w * math.cos(angle_rad))
        y2 = int(cy - half_w * math.sin(angle_rad))
        
        self.window.line(self.gc, x1, y1, x2, y2)
        self.display.flush()
    
    def draw_imu_ball(self, offset_x, offset_y, intensity=1.0, opacity=0.8, invert_axis=True):
        """draw the tracking ball using live IMU data"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Crosshair grey
        self.gc.change(foreground=self._hex_to_rgb(0x444444), line_width=1)
        self.window.line(self.gc, sw//2 - 10, sh//2, sw//2 + 10, sh//2)
        self.window.line(self.gc, sw//2, sh//2 - 10, sw//2, sh//2 + 10)
        
        # Green ball - clamped to 30px travel
        invert_mult = -1.0 if invert_axis else 1.0
        bx = int(sw // 2 + max(-30, min(30, offset_x * 12.0 * intensity * invert_mult)))
        by = int(sh // 2 + max(-30, min(30, offset_y * 12.0 * intensity * invert_mult)))
        self.gc.change(foreground=self._hex_to_rgb(0x00FFCC))
        self.window.fill_arc(self.gc, bx - 15, by - 15, 30, 30, 0, 360 * 64)
        
        self.display.flush()

    def draw_imu_dotgrid(self, offset_x, offset_y, intensity=1.0, opacity=0.8, invert_axis=True):
        """draw the edge-based dot cues (Apple Car Motion Cues style - vertical edges only)"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        self.window.clear_area(0, 0, sw, sh)
        
        # Dots move OPPOSITE to tilt to simulate inertia if invert_axis is True
        max_travel = 30.0
        invert_mult = -1.0 if invert_axis else 1.0
        shift_x = max(-max_travel, min(max_travel, offset_x * 12.0 * intensity * invert_mult))
        shift_y = max(-max_travel, min(max_travel, offset_y * 12.0 * intensity * invert_mult))
        
        # Use cyan dots as per request
        self.gc.change(foreground=self._hex_to_rgb(0x00FFCC, alpha=opacity))
        dot_r = 4  # Slightly smaller dots
        margin = 60 # Distance from screen edge
        
        # 4 dots per side, distributed from 25% to 75% height
        dot_count = 4
        
        for i in range(dot_count):
            pct = 0.25 + (i * 0.5) / (dot_count - 1)
            y_base = int(sh * pct)
            
            # Apply IMU shift
            x_left = margin + int(shift_x)
            x_right = sw - margin - int(shift_x) # Opposite side mirrored correctly in horizontal layout? Actually we just shift them parallel.
            y_pos = y_base + int(shift_y)
            
            # Left edge dot
            self.window.fill_arc(self.gc, x_left - dot_r, y_pos - dot_r, dot_r*2, dot_r*2, 0, 360*64)
            # Right edge dot
            self.window.fill_arc(self.gc, sw - margin - int(shift_x) - dot_r, y_pos - dot_r, dot_r*2, dot_r*2, 0, 360*64)

        self.display.flush()

    def draw_imu_crosshair(self, offset_x, offset_y, intensity=1.0, opacity=0.8, invert_axis=True):
        """draw the minimal drifting crosshair"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # Crosshair center moves slightly with IMU - clamped to 30px
        invert_mult = -1.0 if invert_axis else 1.0
        cx = int(sw // 2 + max(-30, min(30, offset_x * 10.0 * intensity * invert_mult)))
        cy = int(sh // 2 + max(-30, min(30, offset_y * 7.0 * intensity * invert_mult)))
        
        self.gc.change(foreground=self._hex_to_rgb(0x88FFFFFF, alpha=opacity), line_width=2)
        arm = 20
        gap = 6
        
        # 4 arms
        self.window.line(self.gc, cx, cy - gap - arm, cx, cy - gap)
        self.window.line(self.gc, cx, cy + gap, cx, cy + gap + arm)
        self.window.line(self.gc, cx - gap - arm, cy, cx - gap, cy)
        self.window.line(self.gc, cx + gap, cy, cx + gap + arm, cy)
        
        # tiny center dot
        self.gc.change(foreground=self._hex_to_rgb(0x00FFCC, alpha=opacity))
        self.window.fill_arc(self.gc, cx - 2, cy - 2, 4, 4, 0, 360 * 64)
        
        self.display.flush()
    
    def draw_idle_indicator(self):
        """pulsing dot — overlay is alive but waiting for IPC data"""
        sw = self.screen.width_in_pixels
        sh = self.screen.height_in_pixels
        
        self.window.clear_area(0, 0, sw, sh)
        
        # pulsing cyan dot in bottom-right corner (non-intrusive)
        # opacity pulses via size: 6px → 12px → 6px over ~2s
        pulse = abs(math.sin(self.tick / 60.0))
        radius = int(6 + pulse * 6)
        
        self.gc.change(foreground=self._hex_to_rgb(0x00CCAA))
        dot_x = sw - 40
        dot_y = sh - 40
        self.window.fill_arc(
            self.gc, dot_x - radius, dot_y - radius,
            radius * 2, radius * 2, 0, 360 * 64
        )
        self.display.flush()
    
    def connect_ipc(self):
        """connect to the python backend's unix socket for live data"""
        now = time.time()
        # enforce backoff — dont spam reconnect attempts
        if now - self._ipc_last_attempt < self._ipc_backoff:
            return None
        self._ipc_last_attempt = now
        
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.connect(SOCK_PATH)
            print(f"[OVERLAY] connected to IPC socket {SOCK_PATH}")
            # reset backoff on success
            self._ipc_backoff = 1.0
            self._last_ipc_data_time = time.time()
            return sock
        except (ConnectionRefusedError, FileNotFoundError):
            # increase backoff: 1s → 2s → 4s → 8s → cap 10s
            self._ipc_backoff = min(self._ipc_backoff * 2, self._ipc_max_backoff)
            return None
    
    def run(self):
        self.create_window()
        ipc_sock = self.connect_ipc()
        ipc_buffer = b""
        frame_time = 1.0 / TARGET_FPS
        x_reconnect_attempts = 0
        max_x_reconnects = 5
        
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
                                self.intensity = data.get("intensity", self.intensity)
                                self.opacity = data.get("opacity", getattr(self, "opacity", 0.8))
                                self.invert_axis = data.get("invert_axis", getattr(self, "invert_axis", True))
                                self._last_ipc_data_time = time.time()
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
                # no connection — try reconnect (backoff handled inside connect_ipc)
                ipc_sock = self.connect_ipc()
            
            # draw next frame — wrapped in try/except for X display disconnect
            try:
                self.tick += 1
                ipc_data_age = time.time() - self._last_ipc_data_time if self._last_ipc_data_time > 0 else 999
                
                if ipc_sock and ipc_data_age < 2.0 and (self.offset_x != 0 or self.offset_y != 0):
                    # live IMU data flowing — render the real overlay
                    if self.mode == "dot" or self.mode == "ball":
                        self.draw_imu_ball(self.offset_x, self.offset_y, self.intensity, getattr(self, "opacity", 0.8), getattr(self, "invert_axis", True))
                    elif self.mode == "horizon" or self.mode == "bar":
                        self.draw_imu_bar(self.offset_x, self.offset_y, self.intensity, getattr(self, "opacity", 0.8), getattr(self, "invert_axis", True))
                    elif self.mode == "dotgrid":
                        self.draw_imu_dotgrid(self.offset_x, self.offset_y, self.intensity, getattr(self, "opacity", 0.8), getattr(self, "invert_axis", True))
                    elif self.mode == "crosshair":
                        self.draw_imu_crosshair(self.offset_x, self.offset_y, self.intensity, getattr(self, "opacity", 0.8), getattr(self, "invert_axis", True))
                    else:
                        self.draw_imu_dotgrid(self.offset_x, self.offset_y, self.intensity, getattr(self, "opacity", 0.8), getattr(self, "invert_axis", True))
                elif ipc_sock or self._last_ipc_data_time > 0:
                    # connected but no fresh data — show idle indicator
                    self.draw_idle_indicator()
                else:
                    # no IPC connection at all — show test animation
                    self.draw_test_rectangle()
                
                # reset X reconnect counter on successful frame
                x_reconnect_attempts = 0
                
            except Exception as e:
                # X display connection lost (gamescope restart, display change, etc)
                x_reconnect_attempts += 1
                print(f"[OVERLAY] X display error ({x_reconnect_attempts}/{max_x_reconnects}): {e}")
                
                if x_reconnect_attempts >= max_x_reconnects:
                    print("[OVERLAY] FATAL: max X reconnect attempts reached, exiting")
                    self.running = False
                    break
                
                # wait and try to recreate the window
                time.sleep(2)
                try:
                    self.create_window()
                    print("[OVERLAY] X display reconnected successfully")
                except Exception as re_err:
                    print(f"[OVERLAY] X reconnect failed: {re_err}")
            
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
