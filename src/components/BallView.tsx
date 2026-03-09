import React, { VFC, useState, useEffect, useRef } from "react";
import { call } from "@decky/api";
import { TelemetryData } from "../types";

// the 2D tracking dot - no border box, just the dot and crosshair
export const BallView: VFC = () => {
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
                console.error("[MuteMotion] Ball telemetry poll failed (skill issue):", e);
            }
            // requestAnimationFrame is way smoother than setInterval
            frameRef.current = requestAnimationFrame(pollData);
        };

        frameRef.current = requestAnimationFrame(pollData);

        return () => {
            isMounted = false;
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
        };
    }, []);

    // clamp so the dot stays visible, no giant boundary box needed
    const xPos = telemetry ? Math.max(-400, Math.min(400, telemetry.offset_x * 8)) : 0;
    const yPos = telemetry ? Math.max(-250, Math.min(250, telemetry.offset_y * 8)) : 0;

    return (
        <div style={{
            position: "relative",
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center"
        }}>
            {/* just the crosshair, no border box - cleaner look */}
            <div style={{
                position: "absolute",
                width: "20px",
                height: "2px",
                backgroundColor: "rgba(255,255,255,0.2)",
            }}></div>
            <div style={{
                position: "absolute",
                width: "2px",
                height: "20px",
                backgroundColor: "rgba(255,255,255,0.2)",
            }}></div>

            {/* The Dot */}
            <div style={{
                width: "30px",
                height: "30px",
                backgroundColor: "#00ffcc",
                borderRadius: "50%",
                boxShadow: "0 0 15px 5px rgba(0, 255, 204, 0.4)",
                willChange: "transform",
                // smooth cubic-bezier so it dont jitter
                transition: "transform 100ms cubic-bezier(0.4, 0, 0.2, 1)",
                transform: `translate3d(${xPos}px, ${yPos}px, 0)`,
            }}></div>
        </div>
    );
};
