import React, { VFC, useState, useEffect, useRef } from "react";
import { call } from "@decky/api";
import { TelemetryData } from "../types";

export const DebugHUD: VFC = () => {
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const frameRef = useRef<number>();
    
    // polling loop - ~60-90fps (16ms to 11ms depending on OLED/LCD)
    useEffect(() => {
        let isMounted = true;
        
        const pollData = async () => {
            if (!isMounted) return;
            try {
                // tell the python daemon to cough up the numbers
                const resp: any = await call("get_visual_offset", {});
                
                // -99.9 is the safe mode sentinel value if the C++ brain dies
                if (resp && resp.success && resp.result && resp.result.offset !== -99.9) {
                    setTelemetry(resp.result);
                }
            } catch (e) {
                console.error("[MuteMotion] HUD telemetry poll failed:", e);
            }
            frameRef.current = requestAnimationFrame(pollData);
        };

        frameRef.current = requestAnimationFrame(pollData);

        return () => {
            isMounted = false;
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
        };
    }, []);

    if (!telemetry) return null;

    return (
        <div style={{
            position: "absolute",
            top: "20px",
            left: "20px",
            color: "#00ffcc",
            fontFamily: "monospace",
            fontSize: "12px",
            backgroundColor: "rgba(0,0,0,0.7)",
            padding: "10px",
            borderRadius: "5px",
            pointerEvents: "none",
            zIndex: 9999
        }}>
            <div style={{ marginBottom: "5px", fontWeight: "bold" }}>RAW: SYSTEM ONLINE</div>
            <div>Offset: {telemetry.offset.toFixed(4)}</div>
            <div>AX: {telemetry.ax.toFixed(4)}g</div>
            <div>AY: {telemetry.ay.toFixed(4)}g</div>
            <div>AZ: {telemetry.az.toFixed(4)}g</div>
            <div>RX (Gyro X): {telemetry.rx.toFixed(2)} dps</div>
            <div>RY (Gyro Y): {telemetry.ry.toFixed(2)} dps</div>
            <div>RZ (Gyro Z): {telemetry.rz.toFixed(2)} dps</div>
        </div>
    );
};
