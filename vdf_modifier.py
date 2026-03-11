"""
MuteMotion VDF Configuration Override — "The Decoy Binding"

the idea is beautifully stupid:
Steam puts the gyro to sleep when no controller profile uses it.
so we inject a fake gyro_to_mouse group into every default template,
forcing Steam to keep the IMU awake indefinitely. Steam literally
thinks the user wants gyro aim. it doesn't. WE want the raw data.
but Steam doesn't need to know that. gaslighting? no. engineering.

that alone isnt enough tho — Steam caches configs in memory at startup
so we ALSO fire a steam:// URI to make it re-read our patches.
"""

import os
import re
import shutil
import logging

# fallback logger for when we run standalone (not inside Decky)
logger = logging.getLogger("mutemotion_vdf_injector")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(ch)

# if Decky is available, use its logger instead (it goes to the real log files)
try:
    import decky_plugin
    logger = decky_plugin.logger
except ImportError:
    pass  # standalone mode, the fallback logger is fine

# ============================================================
# where to find Neptune controller templates on the Deck
# (yes there are two paths because Valve loves symlinks)
# ============================================================
STEAM_CONTROLLER_BASE = "/home/deck/.steam/steam/controller_base/templates"
STEAM_CONTROLLER_BASE_ALT = "/home/deck/.local/share/Steam/controller_base/templates"

# the templates that games actually use as defaults
# (if a game has no custom profile, one of these gets loaded)
NEPTUNE_TARGETS = [
    "controller_neptune_gamepad_joystick.vdf",
    "controller_neptune_gamepad+mouse.vdf",
    "controller_neptune_gamepad_fps.vdf",
]

# the gyro group we inject — matches the real format from controller_neptune_gamepad_mouse_gyro.vdf
# group ID 99 is arbitrary but unlikely to collide with existing groups
DECOY_GYRO_GROUP = '''	"group"
	{
		"id"		"99"
		"mode"		"gyro_to_mouse"
		"inputs"
		{
		}
		"gameactions"
		{
		}
	}'''

# the binding reference that goes inside group_source_bindings
# this tells Steam "hey, group 99 should be active when the gyro is on"
DECOY_BINDING_REF = '			"99"		"gyro active"'

# our signature so we know if we already patched a file (idempotency ftw)
SIGNATURE = "mutemotion_decoy_patched"


def find_neptune_templates():
    """hunt down all Neptune VDF templates on the filesystem. returns a list of paths."""
    found = []
    for base in [STEAM_CONTROLLER_BASE, STEAM_CONTROLLER_BASE_ALT]:
        if not os.path.isdir(base):
            continue
        for name in NEPTUNE_TARGETS:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                found.append(path)
    
    # also scan userdata for any per-user Neptune VDFs (community profiles etc.)
    userdata = "/home/deck/.steam/steam/userdata"
    if os.path.isdir(userdata):
        for uid in os.listdir(userdata):
            uid_path = os.path.join(userdata, uid)
            if not os.path.isdir(uid_path) or uid in ("anonymous", "0"):
                continue  # skip system dirs
            for root, _, files in os.walk(uid_path):
                for f in files:
                    if "neptune" in f.lower() and f.endswith(".vdf"):
                        found.append(os.path.join(root, f))

    return found


def inject_decoy_binding(vdf_path):
    """
    inject our fake gyro_to_mouse group into a Neptune VDF template.

    how it works:
    1. stick the gyro group right before the "preset" block
    2. add a binding reference so Steam actually activates group 99
    3. backup the original file because we're not barbarians
    """
    if not os.path.isfile(vdf_path):
        logger.error(f"[VDF] File not found: {vdf_path}")
        return False

    try:
        with open(vdf_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # already got the signature? skip it, we're idempotent like that
        if SIGNATURE in content:
            logger.info(f"[VDF] Already patched: {vdf_path}")
            return True

        # template already has gyro stuff? someone beat us to it. nice.
        if 'gyro_to_mouse' in content or 'gyro_to_joystick' in content:
            logger.info(f"[VDF] Template already has gyro bindings: {vdf_path}")
            return True

        modified = False

        # --- step 1: inject the gyro group right before the "preset" block ---
        preset_match = re.search(r'(\t"preset")', content)
        if preset_match:
            insert_pos = preset_match.start()
            content = (content[:insert_pos] +
                       f"// {SIGNATURE}\n" +
                       DECOY_GYRO_GROUP + "\n" +
                       content[insert_pos:])
            modified = True
            logger.info(f"[VDF] Injected gyro_to_mouse group in: {vdf_path}")
        else:
            logger.warning(f"[VDF] No 'preset' block found in: {vdf_path}")
            return False  # if theres no preset block this file is cooked

        # --- step 2: add binding reference so Steam knows group 99 exists ---
        gsb_match = re.search(r'("group_source_bindings"\s*\{)', content)
        if gsb_match:
            insert_after = gsb_match.end()
            content = (content[:insert_after] + "\n" +
                       DECOY_BINDING_REF +
                       content[insert_after:])
            logger.info(f"[VDF] Added group_source_bindings reference in: {vdf_path}")
        else:
            logger.warning(f"[VDF] No group_source_bindings found in: {vdf_path}")
            # not fatal — some templates might not have this block

        if modified:
            # backup the original (first time only, we're not monsters)
            backup = vdf_path + ".mutemotion_backup"
            if not os.path.exists(backup):
                shutil.copy2(vdf_path, backup)
                logger.info(f"[VDF] Backup saved: {backup}")

            with open(vdf_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"[VDF] Successfully patched: {vdf_path}")
            return True  # gg ez

    except Exception as e:
        logger.error(f"[VDF] Failed to patch {vdf_path}: {e}")
        return False

    return False


def apply_decoy_to_all():
    """find every Neptune template and inject the decoy gyro binding into each one."""
    configs = find_neptune_templates()
    logger.info(f"[VDF] Found {len(configs)} Neptune templates to patch.")
    
    success = 0
    for cfg in configs:
        if inject_decoy_binding(cfg):
            success += 1

    logger.info(f"[VDF] Patched {success}/{len(configs)} templates.")
    return success > 0


def force_apply_gyro_profile(app_id=None):
    """
    force Steam to reload controller configs via steam:// URI.

    patching VDF files on disk is necessary but not enough — Steam caches
    configs in memory and won't re-read them until told to. this function
    yells at Steam through its own protocol handler to wake up and smell
    the freshly patched gyro groups. Steam then sends the HID Feature Report
    that wakes the IMU. its like inception but for controller configs.

    strategy:
    1. call /usr/bin/steam with the steam:// URI directly
    2. also try xdg-open as backup (it routes through Steam's protocol handler)
    """
    import subprocess
    
    # the template we patched (has the injected gyro_to_mouse group now)
    template_name = "controller_neptune_gamepad_joystick"

    # target a specific game if we know which one is running
    if app_id:
        uri = f"steam://controllerconfig/{app_id}/{template_name}"
    else:
        # appid 0 = desktop/global config
        uri = f"steam://controllerconfig/0/{template_name}"
    
    logger.info(f"[VDF] Forcing controller profile reload via: {uri}")
    
    try:
        # method 1: call Steam directly via absolute path
        # on SteamOS, /usr/bin/steam -> steam-jupiter (the Deck-specific binary)
        steam_bin = "/usr/bin/steam"
        if not os.path.exists(steam_bin):
            steam_bin = "steam"  # fallback to PATH resolution

        result = subprocess.run(
            [steam_bin, uri],
            capture_output=True, text=True, timeout=5
        )
        logger.info(f"[VDF] steam:// URI sent via {steam_bin} (exit={result.returncode})")

        # method 2: xdg-open as backup (routes through Steam's URI handler)
        subprocess.Popen(
            ["xdg-open", uri],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        logger.info(f"[VDF] xdg-open fallback sent for: {uri}")
        
        return True
    except subprocess.TimeoutExpired:
        logger.warning("[VDF] steam:// URI timed out (Steam might still process it tho)")
        return True  # timeout doesnt mean it failed
    except FileNotFoundError:
        logger.error("[VDF] cant find 'steam' binary anywhere. are we even on a Deck?")
        return False
    except Exception as e:
        logger.error(f"[VDF] force_apply_gyro_profile failed: {e}")
        return False


def get_running_app_id():
    """
    try to figure out which game is currently running.
    returns the AppId string, or None if nothing is happening.
    
    how: Steam launches games through a "reaper" process that has
    the AppId baked into its command line args. we grep for it.
    """
    import subprocess
    try:
        # hunt for the reaper process (Steam's game launcher wrapper)
        result = subprocess.run(
            ["pgrep", "-a", "reaper"],
            capture_output=True, text=True, timeout=3
        )
        if result.stdout:
            # parse AppId from the command line: "... AppId=307690 ..."
            for line in result.stdout.splitlines():
                if "AppId=" in line:
                    for part in line.split():
                        if part.startswith("AppId="):
                            return part.split("=")[1]
    except Exception:
        pass  # no game running, or pgrep isnt available
    return None


if __name__ == "__main__":
    # standalone mode — patch everything and try to force-apply
    logger.info("[VDF] Running standalone configuration scan...")
    result = apply_decoy_to_all()
    logger.info(f"[VDF] Template patch result: {'SUCCESS' if result else 'NO CONFIGS FOUND'}")

    logger.info("[VDF] Attempting to force-apply gyro profile via steam:// URI...")
    app_id = get_running_app_id()
    if app_id:
        logger.info(f"[VDF] Detected running game: AppId={app_id}")
    force_apply_gyro_profile(app_id)
