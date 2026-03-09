export interface MuteMotionState {
    isEnabled: boolean;
    useBallMode: boolean;
    showDebugArea: boolean;
    sensitivity: number;
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
}
