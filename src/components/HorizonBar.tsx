import React, { VFC, useState, useEffect, useRef } from "react";

// static sine wave test - proves the bar CAN move without needing sensor data
// if this works on deck, the rendering pipeline is valid and we just need to wire real data
export const HorizonBar: VFC = () => {
    const [tick, setTick] = useState(0);
    const frameRef = useRef<number>();

    // animation loop - oscillates the bar back and forth like a pendulum
    // no RPC, no python, no C++, just pure vibes and math
    useEffect(() => {
        let isMounted = true;
        
        const animate = () => {
            if (!isMounted) return;
            setTick(t => t + 1);
            frameRef.current = requestAnimationFrame(animate);
        };

        frameRef.current = requestAnimationFrame(animate);

        return () => {
            isMounted = false;
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
        };
    }, []);

    // sine wave goes from -1 to 1, we scale it to ±30 for offset and ±240 for Y translation
    // speed is controlled by the divisor (60 = ~1 full cycle per second at 60fps)
    const rawOffset = Math.sin(tick / 60) * 30;
    const yPos = Math.sin(tick / 60) * 240;

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
                height: "4px", // thicc enough to see
                backgroundColor: "#ff0044",
                boxShadow: "0 0 20px 5px rgba(255, 0, 68, 0.5)",
                borderRadius: "2px",
                willChange: "transform",
                // no transition needed - requestAnimationFrame is already smooth
                transform: `rotate(${rawOffset * 0.5}deg) translateY(${yPos}px)`, // the bar oscillates like its seasick
            }}></div>
        </div>
    );
};
