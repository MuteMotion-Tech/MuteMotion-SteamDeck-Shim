import React, { VFC, useState, useEffect, useRef } from "react";
import { call } from "@decky/api";
import { TelemetryData } from "../types";

// the 2D tracking dot - no border box, just the dot and crosshair
// uses setInterval instead of requestAnimationFrame so gamescope cant pause us
export const BallView: VFC = () => {
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
                // rpc died, whatever, try next tick
            }
        };

        // setInterval survives gamescope render suspension
        // requestAnimationFrame does NOT - gamescope pauses it when QAM closes
        const interval = setInterval(pollData, 16); // ~60fps
        pollData(); // immediate first fetch

        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, []);

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
            {/* crosshair */}
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
                transition: "transform 100ms cubic-bezier(0.4, 0, 0.2, 1)",
                transform: `translate3d(${xPos}px, ${yPos}px, 0)`,
            }}></div>
        </div>
    );
};
