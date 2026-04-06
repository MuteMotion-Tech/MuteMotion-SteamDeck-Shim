import React, { VFC, useState, useEffect } from "react";
import { call } from "@decky/api";
import { TelemetryData } from "../types";

// debug overlay - uses setInterval so gamescope cant freeze our polling
export const DebugHUD: VFC = () => {
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    
    useEffect(() => {
        let isMounted = true;
        
        const pollData = async () => {
            if (!isMounted) return;
            try {
                const resp: any = await call("get_visual_offset", {});
                if (resp && resp.offset !== undefined && resp.offset !== -88.8) {
                    setTelemetry(resp);
                }
            } catch (e) {
                // rpc died, try next tick
            }
        };

        // setInterval survives gamescope render suspension
        const interval = setInterval(pollData, 16); // ~60fps
        pollData();

        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, []);

    if (!telemetry) return null;

    return (
        <div style={{
            position: "absolute",
            bottom: "40px",
            left: "40px",
            color: "#00ff00",
            fontFamily: "monospace",
            fontSize: "14px",
            backgroundColor: "rgba(0,0,0,0.85)",
            padding: "15px",
            borderRadius: "8px",
            pointerEvents: "none",
            zIndex: 9999,
            whiteSpace: "pre-line"
        }}>
            <div style={{ marginBottom: "5px", fontWeight: "bold", borderBottom: "1px solid #00ff00", paddingBottom: "5px" }}>RAW: SYSTEM ONLINE</div>
            <div>Offset:  {telemetry.offset.toFixed(4)}</div>
            <div>Accel X: {telemetry.ax.toFixed(4)}g</div>
            <div>Accel Y: {telemetry.ay.toFixed(4)}g</div>
            <div>Accel Z: {telemetry.az.toFixed(4)}g</div>
            <div>Gyro X:  {telemetry.rx.toFixed(2)} dps</div>
            <div>Gyro Y:  {telemetry.ry.toFixed(2)} dps</div>
            <div>Gyro Z:  {telemetry.rz.toFixed(2)} dps</div>
            <div style={{ marginTop: "8px", borderTop: "1px solid #00ff00", paddingTop: "5px", fontWeight: "bold" }}>SETTINGS STATE</div>
            <div>Intensity:  {telemetry.intensity !== undefined ? telemetry.intensity.toFixed(2) : "N/A"}</div>
            <div>Invert:     {telemetry.invert_axis !== undefined ? String(telemetry.invert_axis) : "N/A"}</div>
            <div>Opacity:    {telemetry.opacity !== undefined ? telemetry.opacity.toFixed(2) : "N/A"}</div>
        </div>
    );
};
