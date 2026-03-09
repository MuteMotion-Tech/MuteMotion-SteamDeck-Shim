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
                
                // decky v3 returns the dict directly, no .success/.result wrapper
                // -88.8 is the error sentinel from the python exception handler
                if (resp && resp.offset !== undefined && resp.offset !== -88.8) {
                    setTelemetry(resp);
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
        </div>
    );
};
