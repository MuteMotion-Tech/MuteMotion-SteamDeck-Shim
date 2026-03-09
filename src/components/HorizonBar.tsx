import React, { VFC, useState, useEffect, useRef } from "react";
import { call } from "@decky/api";
import { TelemetryData } from "../types";

export const HorizonBar: VFC = () => {
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const frameRef = useRef<number>();

    // polling loop - ~60-90fps (16ms to 11ms depending on OLED/LCD)
    useEffect(() => {
        let isMounted = true;
        
        const pollData = async () => {
            if (!isMounted) return;
            try {
                // ask the python daemon nicely for the offset
                const resp: any = await call("get_visual_offset", {});
                
                // -99.9 is the safe mode sentinel value if the C++ brain dies
                if (resp && resp.success && resp.result && resp.result.offset !== -99.9) {
                    setTelemetry(resp.result);
                }
            } catch (e) {
                console.error("[MuteMotion] Horizon telemetry poll failed (rip bozo):", e);
            }
            frameRef.current = requestAnimationFrame(pollData);
        };

        frameRef.current = requestAnimationFrame(pollData);

        return () => {
            isMounted = false;
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
        };
    }, []);

    // calculate the angle of the dangle
    // neptune's output is clamped ±50 but we scale it 8x for the visuals
    // Math.max/min to keep it mostly on screen
    const rawOffset = telemetry ? telemetry.offset : 0;
    const yPos = Math.max(-350, Math.min(350, rawOffset * 8));

    return (
        <div style={{
            position: "relative",
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center"
        }}>
            <div style={{
                width: "90%",
                height: "4px", // a bit thicker than the static one
                backgroundColor: "#ff0044",
                boxShadow: "0 0 20px 5px rgba(255, 0, 68, 0.5)",
                borderRadius: "2px",
                willChange: "transform",
                // NFR-1.2 cubic-bezier smoothing - incident 035 fix
                transition: "transform 100ms cubic-bezier(0.4, 0, 0.2, 1)",
                transform: `rotate(${rawOffset * 0.5}deg) translateY(${yPos}px)`, // compound movement for max sickness defeat
            }}></div>
        </div>
    );
};
