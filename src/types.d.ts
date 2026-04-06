export type PresetMode = "dot" | "dotgrid" | "horizon" | "liquid";

export interface MuteMotionState {
    isEnabled: boolean;
    presetMode: PresetMode;
    showDebugArea: boolean;
    sensitivity: number;
    intensity: number;
}

export interface TelemetryData {
    offset: number;
    offset_x: number;
    offset_y: number;
    rx: number;
    ry: number;
    rz: number;
    ax: number;
    ay: number;
    az: number;
    intensity: number;
    opacity: number;
    invert_axis: boolean;
}
