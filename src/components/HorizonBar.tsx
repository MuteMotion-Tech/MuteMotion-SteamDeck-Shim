import React, { VFC, useState, useEffect } from "react";

// static sine wave test - uses setInterval instead of rAF so gamescope cant pause it
// if this animates during gameplay (QAM closed), the render persistence fix works
export const HorizonBar: VFC = () => {
    const [tick, setTick] = useState(0);

    useEffect(() => {
        let isMounted = true;
        
        // setInterval keeps ticking even when gamescope hides the overlay
        // requestAnimationFrame gets paused - thats the whole gamescope freeze bug
        const interval = setInterval(() => {
            if (!isMounted) return;
            setTick(t => t + 1);
        }, 16); // ~60fps

        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, []);

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
                height: "4px",
                backgroundColor: "#ff0044",
                boxShadow: "0 0 20px 5px rgba(255, 0, 68, 0.5)",
                borderRadius: "2px",
                willChange: "transform",
                transform: `rotate(${rawOffset * 0.5}deg) translateY(${yPos}px)`,
            }}></div>
        </div>
    );
};
