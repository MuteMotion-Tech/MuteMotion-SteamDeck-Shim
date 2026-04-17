import {
    definePlugin,
    call,
} from "@decky/api";
import {
    PanelSection,
    PanelSectionRow,
    ToggleField,
    SliderField,
    DropdownItem,
    DialogButton,
    staticClasses,
} from "@decky/ui";

import React, { VFC, useState, useEffect } from "react";
import { routerHook, toaster } from "@decky/api";

import { StateBoolean, StateString, StateNumber } from "./state";
import { useUIComposition, UIComposition } from "./uiComposition";
import { HorizonBar } from "./components/HorizonBar";
import { BallView } from "./components/BallView";
import { DotGridView } from "./components/DotGridView";
import { LiquidLevelView } from "./components/LiquidLevelView";
import { DebugHUD } from "./components/DebugHUD";
import { PresetMode } from "./types";

// ============================================================
// overlay preset registry
// add new presets here and they automatically show up in the dropdown
// ============================================================
const PRESET_OPTIONS: { label: string; data: PresetMode }[] = [
    { label: "Dot Grid (Default)", data: "dotgrid" },
    { label: "Single Dot",         data: "dot" },
    { label: "Horizon Bar",        data: "horizon" },
    { label: "Liquid Level",       data: "liquid" },
];

// map preset mode → component
const PresetComponent: Record<PresetMode, VFC> = {
    dot: BallView,
    dotgrid: DotGridView,
    horizon: HorizonBar,
    liquid: LiquidLevelView,
};

const Overlay: VFC<{
    enabledState: StateBoolean,
    presetState: StateString,
    sensorOverlayState: StateBoolean,
    opacityState: StateNumber
}> = React.memo(({ enabledState, presetState, sensorOverlayState, opacityState }) => {
    const [visible, setVisible] = useState(enabledState.GetState());
    const [preset, setPreset] = useState(presetState.GetState());
    const [useDebug, setUseDebug] = useState(sensorOverlayState.GetState());

    // "ghost mode" - use notification layer because the footer kept stealing my inputs
    useUIComposition(visible || useDebug ? UIComposition.Notification : UIComposition.Hidden);

    useEffect(() => {
        const onEnabledChange = (val: boolean) => setVisible(val);
        const onPresetChange = (val: string) => setPreset(val);
        const onDebugChange = (val: boolean) => setUseDebug(val);

        enabledState.onStateChanged(onEnabledChange);
        presetState.onStateChanged(onPresetChange);
        sensorOverlayState.onStateChanged(onDebugChange);

        return () => {
            enabledState.offStateChanged(onEnabledChange);
            presetState.offStateChanged(onPresetChange);
            sensorOverlayState.offStateChanged(onDebugChange);
        };
    }, [enabledState, presetState, sensorOverlayState]);

    // input isolation voodoo (click-through hack)
    useEffect(() => {
        const applyStyles = (element: HTMLElement) => {
            element.style.setProperty("pointer-events", "none", "important");
            element.style.setProperty("touch-action", "none", "important");
            element.style.setProperty("user-select", "none", "important");
            element.style.setProperty("-webkit-user-drag", "none", "important");
        };

        const resetStyles = (element: HTMLElement) => {
            element.style.removeProperty("pointer-events");
            element.style.removeProperty("touch-action");
            element.style.removeProperty("user-select");
            element.style.removeProperty("-webkit-user-drag");
        };

        const root = document.getElementById("root");

        if (visible || useDebug) {
            applyStyles(document.body);
            applyStyles(document.documentElement);
            if (root) applyStyles(root);
        } else {
            resetStyles(document.body);
            resetStyles(document.documentElement);
            if (root) resetStyles(root);
        }
        return () => {
            resetStyles(document.body);
            resetStyles(document.documentElement);
            if (root) resetStyles(root);
        };
    }, [visible, useDebug]);

    if (!visible && !useDebug) return null;

    // dynamically render the selected preset component
    const ActivePreset = PresetComponent[preset as PresetMode] || DotGridView;

    return (
        <div
            style={{
                position: "fixed",
                top: 0,
                left: 0,
                width: "100vw",
                height: "100vh",
                pointerEvents: "none",
                userSelect: "none",
                touchAction: "none",
                zIndex: 7002,
                backgroundColor: "transparent",
            }}
        >
            {visible && <ActivePreset />}
            {useDebug && <DebugHUD />}
        </div>
    );
});

// live diagnostics panel that polls the python backend's watchdog state
const WatchdogStatus: VFC = () => {
    const [status, setStatus] = useState<any>(null);

    useEffect(() => {
        const poll = setInterval(() => {
            call("get_watchdog_status", {})
                .then((res: any) => { if (res) setStatus(res); })
                .catch(() => {});
        }, 2000);
        call("get_watchdog_status", {})
            .then((res: any) => { if (res) setStatus(res); })
            .catch(() => {});
        return () => clearInterval(poll);
    }, []);

    if (!status) return <div style={{ fontSize: "12px", color: "#888" }}>loading...</div>;

    const fresh = status.data_age_seconds < 1.0;
    return (
        <div style={{ fontSize: "12px", fontFamily: "monospace", color: "#ccc", padding: "4px 0" }}>
            <div>Thread: <span style={{ color: status.thread_alive ? "#0f0" : "#f00" }}>
                {status.thread_alive ? "ALIVE" : "DEAD"}
            </span></div>
            <div>Data Age: <span style={{ color: fresh ? "#0f0" : "#ff0" }}>
                {status.data_age_seconds}s
            </span></div>
            <div>Active FDs: {status.active_fds}</div>
            <div>Watchdog Fires: <span style={{ color: status.watchdog_fires > 0 ? "#ff0" : "#0f0" }}>
                {status.watchdog_fires}
            </span></div>
        </div>
    );
};

const Content: VFC<{
    enabledState: StateBoolean,
    presetState: StateString,
    intensityState: StateNumber,
    opacityState: StateNumber,
    invertAxisState: StateBoolean,
    debugModeState: StateBoolean,
    sensorOverlayState: StateBoolean
}> = React.memo(({ enabledState, presetState, intensityState, opacityState, invertAxisState, debugModeState, sensorOverlayState }) => {
    const [isEnabled, setIsEnabled] = useState(enabledState.GetState());
    const [currentPreset, setCurrentPreset] = useState(presetState.GetState());
    const [intensity, setIntensity] = useState(intensityState.GetState());
    const [opacity, setOpacity] = useState(opacityState.GetState());
    const [invertAxis, setInvertAxis] = useState(invertAxisState.GetState());
    const [isDevMode, setIsDevMode] = useState(debugModeState.GetState());
    const [isSensorOverlay, setIsSensorOverlay] = useState(sensorOverlayState.GetState());

    useEffect(() => {
        const eHandler = (val: boolean) => setIsEnabled(val);
        const pHandler = (val: string) => setCurrentPreset(val);
        const iHandler = (val: number) => setIntensity(val);
        const opHandler = (val: number) => setOpacity(val);
        const invHandler = (val: boolean) => setInvertAxis(val);
        const dHandler = (val: boolean) => setIsDevMode(val);
        const sHandler = (val: boolean) => setIsSensorOverlay(val);

        enabledState.onStateChanged(eHandler);
        presetState.onStateChanged(pHandler);
        intensityState.onStateChanged(iHandler);
        opacityState.onStateChanged(opHandler);
        invertAxisState.onStateChanged(invHandler);
        debugModeState.onStateChanged(dHandler);
        sensorOverlayState.onStateChanged(sHandler);

        return () => {
            enabledState.offStateChanged(eHandler);
            presetState.offStateChanged(pHandler);
            intensityState.offStateChanged(iHandler);
            opacityState.offStateChanged(opHandler);
            invertAxisState.offStateChanged(invHandler);
            debugModeState.offStateChanged(dHandler);
            sensorOverlayState.offStateChanged(sHandler);
        };
    }, [enabledState, presetState, intensityState, opacityState, invertAxisState, debugModeState, sensorOverlayState]);

    return (
        <>
        <PanelSection title="MuteMotion Settings">
            <PanelSectionRow>
                <ToggleField
                    label="Enable MuteMotion"
                    description="Activates the anti-sickness visualizer"
                    checked={isEnabled}
                    onChange={(val) => {
                        setIsEnabled(val);
                        enabledState.SetState(val);
                        if (val) {
                            // auto-calibrate on activation: reset offset to zero first
                            // so the user's current position becomes the new neutral
                            call("calibrate_imu", {}).then(() => {
                                call("start_native_overlay", {}).then((res: any) => {
                                    toaster.toast({ title: "MuteMotion", body: "Overlay Active" });
                                }).catch(() => {
                                    toaster.toast({ title: "MuteMotion", body: "Failed to start overlay" });
                                });
                            });
                        } else {
                            call("stop_native_overlay", {}).then(() => {
                                toaster.toast({ title: "MuteMotion", body: "Overlay Stopped" });
                            });
                        }
                    }}
                />
            </PanelSectionRow>

            <PanelSectionRow>
                <DropdownItem
                    label="Visual Style"
                    description="Choose the shape of the motion anchor"
                    rgOptions={PRESET_OPTIONS.map((opt, idx) => ({
                        label: opt.label,
                        data: opt.data,
                    }))}
                    selectedOption={currentPreset}
                    onChange={(option: { data: string; label: string }) => {
                        setCurrentPreset(option.data);
                        presetState.SetState(option.data);
                        call("set_overlay_mode", option.data);
                    }}
                    disabled={!isEnabled}
                />
            </PanelSectionRow>

            <PanelSectionRow>
                <SliderField
                    label="Motion Intensity"
                    description="How strongly the visual reacts to movement"
                    value={Math.round(intensity * 100)}
                    min={0}
                    max={200}
                    step={1}
                    showValue={true}
                    onChange={(val: number) => {
                        const trueVal = val / 100.0;
                        setIntensity(trueVal);
                        intensityState.SetState(trueVal);
                        call("set_intensity", { intensity: trueVal });
                    }}
                    disabled={!isEnabled}
                />
            </PanelSectionRow>

            <PanelSectionRow>
                <SliderField
                    label="Opacity"
                    description="Base visibility of the motion cues"
                    value={Math.round(opacity * 100)}
                    min={5}
                    max={100}
                    step={1}
                    showValue={true}
                    onChange={(val: number) => {
                        const trueVal = val / 100.0;
                        setOpacity(trueVal);
                        opacityState.SetState(trueVal);
                        call("set_opacity", { opacity: trueVal });
                    }}
                    disabled={!isEnabled}
                />
            </PanelSectionRow>

            <PanelSectionRow>
                <ToggleField
                    label="Invert Axis"
                    description="Move opposite to tilt"
                    checked={invertAxis}
                    onChange={(val) => {
                        setInvertAxis(val);
                        invertAxisState.SetState(val);
                        call("set_invert_axis", { invert_axis: val });
                    }}
                    disabled={!isEnabled}
                />
            </PanelSectionRow>

            <div style={{ height: 1, backgroundColor: "#3a3a3c", margin: "12px 0" }} />

            <PanelSectionRow>
                <DialogButton
                    disabled={!isEnabled}
                    onClick={() => {
                        call("calibrate_imu", {})
                            .then(() => {
                                toaster.toast({
                                    title: "MuteMotion",
                                    body: "Calibrated — current position is now zero"
                                });
                            })
                            .catch(() => {
                                toaster.toast({
                                    title: "MuteMotion",
                                    body: "Calibration failed"
                                });
                            });
                    }}
                >
                    Calibrate
                </DialogButton>
            </PanelSectionRow>

            <PanelSectionRow>
                <DialogButton
                    onClick={() => {
                        call("reset_settings", {})
                            .then((res: any) => {
                                if (res) {
                                    // sync all react state with the new defaults
                                    setCurrentPreset(res.preset || "dotgrid");
                                    presetState.SetState(res.preset || "dotgrid");
                                    setIntensity(res.intensity ?? 1.0);
                                    intensityState.SetState(res.intensity ?? 1.0);
                                    setOpacity(res.opacity ?? 0.8);
                                    opacityState.SetState(res.opacity ?? 0.8);
                                    setInvertAxis(res.invert_axis ?? true);
                                    invertAxisState.SetState(res.invert_axis ?? true);
                                }
                                toaster.toast({
                                    title: "MuteMotion",
                                    body: "Settings reset to defaults"
                                });
                            })
                            .catch(() => {
                                toaster.toast({
                                    title: "MuteMotion",
                                    body: "Reset failed"
                                });
                            });
                    }}
                >
                    Reset to Defaults
                </DialogButton>
            </PanelSectionRow>

        </PanelSection>

        <PanelSection title="Developer Mode">
            <PanelSectionRow>
                <ToggleField
                    label="Developer Mode"
                    description="Show advanced diagnostics and raw data"
                    checked={isDevMode}
                    onChange={(val) => {
                        setIsDevMode(val);
                        debugModeState.SetState(val);
                    }}
                />
            </PanelSectionRow>

            {isDevMode && (
                <>
                    <PanelSectionRow>
                        <ToggleField
                            label="Raw Sensor Overlay"
                            description="Display live IMU data on screen"
                            checked={isSensorOverlay}
                            onChange={(val) => {
                                setIsSensorOverlay(val);
                                sensorOverlayState.SetState(val);
                            }}
                        />
                    </PanelSectionRow>

                    <PanelSectionRow>
                        <DialogButton onClick={() => {
                            call("ping_engine", {})
                                .then((res: any) => {
                                    toaster.toast({
                                        title: "MuteMotion Core",
                                        body: res?.message || "Engine is Online"
                                    });
                                })
                                .catch(() => {
                                    toaster.toast({
                                        title: "MuteMotion Core Error",
                                        body: "Engine is Offline (Safe Mode)"
                                    });
                                });
                        }}>
                            Test Core Engine Connection
                        </DialogButton>
                    </PanelSectionRow>

                    <PanelSection title="Sensor Thread Diagnostics">
                        <WatchdogStatus />
                    </PanelSection>
                </>
            )}
        </PanelSection>
        </>
    );
});

export default definePlugin(() => {
    console.log('[MuteMotion] Initializing Core Injection...');

    // state nodes
    const enabledState = new StateBoolean(false);
    const presetState = new StateString("dotgrid");   // default preset
    const intensityState = new StateNumber(0.5);       // default intensity
    const opacityState = new StateNumber(0.8);         // default opacity
    const invertAxisState = new StateBoolean(true);    // default inverted (apple style)
    const debugModeState = new StateBoolean(false);
    const sensorOverlayState = new StateBoolean(false);

    // Mount overlay function (incident 026b fix: routerhook re-registrations)
    const mountOverlay = () => {
        if (routerHook) {
            console.log('[MuteMotion] RouterHook found, mounting ghost overlay...');
            routerHook.removeGlobalComponent("MuteMotionOverlay");
            routerHook.addGlobalComponent("MuteMotionOverlay", () => (
                <Overlay 
                    enabledState={enabledState} 
                    presetState={presetState} 
                    sensorOverlayState={sensorOverlayState} 
                    opacityState={opacityState} 
                />
            ));
            return true;
        } else {
            console.error("[MuteMotion] routerHook is undefined! the plugin is basically bricked.");
            return false;
        }
    };

    mountOverlay();

    // hydrate state from sqlite on plugin init
    call("get_settings", {}).then((res: any) => {
        if (res) {
            if (res.preset) presetState.SetState(res.preset);
            if (res.intensity !== undefined) intensityState.SetState(res.intensity);
            if (res.opacity !== undefined) opacityState.SetState(res.opacity);
            if (res.invert_axis !== undefined) invertAxisState.SetState(res.invert_axis);
            console.log('[MuteMotion] Settings hydrated from SQLite:', res);
        }
    }).catch((e: any) => {
        console.error('[MuteMotion] Failed to hydrate settings:', e);
    });

    return {
        name: "MuteMotion SteamDeck",
        title: <div className={staticClasses.Title}>MuteMotion</div>,
        content: <Content
            enabledState={enabledState}
            presetState={presetState}
            intensityState={intensityState}
            opacityState={opacityState}
            invertAxisState={invertAxisState}
            debugModeState={debugModeState}
            sensorOverlayState={sensorOverlayState}
        />,
        icon: <svg viewBox="0 0 24 24" width="1em" height="1em" xmlns="http://www.w3.org/2000/svg">
            <path d="M3 20V4h3l6 8 6-8h3v16h-4V10l-5 6.5L7 10v10z" fill="#00ffcc" />
        </svg>,
        onDismount() {
            console.log('[MuteMotion] Plugin dismounting, dropping hooks...');
            if (routerHook) {
                routerHook.removeGlobalComponent("MuteMotionOverlay");
            }
        },
    };
});
