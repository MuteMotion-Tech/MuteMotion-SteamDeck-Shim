import React, { VFC, useEffect, useRef } from "react";
import { call } from "@decky/api";

// =============================================================================
// LiquidLevelView — Canvas 2D + Critically Damped Spring Physics (Fluid Ripple)
// =============================================================================
// Render loop driven by setInterval (NOT requestAnimationFrame) to survive
// Gamescope CEF render-pause during game launch.
// =============================================================================

const OMEGA = 18.0;           // slightly softer spring for liquid feel
const GAIN_ROLL = 1.5;        // multiplier for rotation (deg)
const GAIN_PITCH = 8.0;       // multiplier for vertical translation (px)
const MAX_ROLL = 45.0;        // maximum rotation in degrees
const MAX_PITCH = 250.0;      // maximum vertical displacement
const RENDER_INTERVAL = 16;   // ~60fps via setInterval

function updateSpring(
    pos: number, vel: number, target: number, dt: number
): { pos: number; vel: number } {
    if (dt > 0.1) dt = 0.1;
    const x0 = pos - target;
    const v0 = vel;
    const exp = Math.exp(-OMEGA * dt);
    const term = (v0 + OMEGA * x0) * dt;
    return {
        pos: target + (x0 + term) * exp,
        vel: (v0 - OMEGA * term) * exp,
    };
}

export const LiquidLevelView: VFC = () => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const lastTimeRef = useRef<number>(0);
    const targetRef = useRef({ roll: 0, pitch: 0 });
    const opacityRef = useRef(0.8);

    const stateRef = useRef({
        posRoll: 0, velRoll: 0,
        posPitch: 0, velPitch: 0
    });

    useEffect(() => {
        let mounted = true;
        const poll = async () => {
            if (!mounted) return;
            try {
                const r: any = await call("get_visual_offset", {});
                if (r && r.offset !== undefined && r.offset !== -88.8) {
                    const inv = r.invert_axis === false ? 1.0 : -1.0;
                    
                    // x is roll, y is pitch in this context
                    const rTarget = (r.offset_x || 0) * GAIN_ROLL * inv;
                    const pTarget = (r.offset_y || 0) * GAIN_PITCH * inv;
                    
                    targetRef.current.roll = Math.max(-MAX_ROLL, Math.min(MAX_ROLL, rTarget));
                    targetRef.current.pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, pTarget));
                    
                    if (r.opacity !== undefined) opacityRef.current = r.opacity;
                }
            } catch (_) {}
        };
        const iv = setInterval(poll, 16);
        poll();
        return () => { mounted = false; clearInterval(iv); };
    }, []);

    // render loop — setInterval driven to survive gamescope render pause
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        lastTimeRef.current = 0;

        const renderFrame = () => {
            if (!ctx) return;
            const now = performance.now();

            const cw = canvas.clientWidth || 1280;
            const ch = canvas.clientHeight || 800;

            if (canvas.width !== cw || canvas.height !== ch) {
                canvas.width = cw;
                canvas.height = ch;
            }

            if (lastTimeRef.current === 0) lastTimeRef.current = now;
            let dt = (now - lastTimeRef.current) / 1000;
            lastTimeRef.current = now;
            if (dt <= 0 || dt > 0.5) dt = 1 / 60;

            const w = canvas.width;
            const h = canvas.height;
            const target = targetRef.current;
            const baseAlpha = opacityRef.current;

            ctx.clearRect(0, 0, w, h);

            const s = stateRef.current;
            const rRoll = updateSpring(s.posRoll, s.velRoll, target.roll, dt);
            const rPitch = updateSpring(s.posPitch, s.velPitch, target.pitch, dt);
            s.posRoll = rRoll.pos; s.velRoll = rRoll.vel;
            s.posPitch = rPitch.pos; s.velPitch = rPitch.vel;

            ctx.save();
            ctx.translate(w / 2, h / 2);
            ctx.rotate((s.posRoll * Math.PI) / 180);
            ctx.translate(0, s.posPitch);

            // the liquid surface ripples harder the faster the device rotates
            const waveAmp = Math.min(25, Math.abs(s.velRoll) * 0.6) + 3;
            const phase = (now / 1000) * 4 + (s.posRoll * 0.08);

            ctx.beginPath();
            // fill an ultra wide bounding box that covers edges regardless of rotation
            const EXTENT = 2000; 
            ctx.moveTo(-EXTENT, EXTENT); // deep bottom left
            ctx.lineTo(-EXTENT, 0); // surface left
            
            // draw sine wave surface
            for (let x = -EXTENT; x <= EXTENT; x += 40) {
                const y = Math.sin(x * 0.015 + phase) * waveAmp;
                ctx.lineTo(x, y);
            }
            
            ctx.lineTo(EXTENT, EXTENT); // deep bottom right
            ctx.closePath();

            // glowing neon fluid
            ctx.fillStyle = `rgba(0, 255, 204, ${baseAlpha * 0.4})`;
            ctx.fill();

            // draw an intense highlight line right at the meniscus barrier
            ctx.beginPath();
            for (let x = -EXTENT; x <= EXTENT; x += 40) {
                const y = Math.sin(x * 0.015 + phase) * waveAmp;
                if (x === -EXTENT) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = `rgba(0, 255, 204, ${baseAlpha})`;
            ctx.lineWidth = 4;
            ctx.shadowColor = `rgba(0, 255, 204, ${baseAlpha})`;
            ctx.shadowBlur = 10;
            ctx.stroke();

            ctx.restore();
        };

        const iv = setInterval(renderFrame, RENDER_INTERVAL);
        return () => clearInterval(iv);
    }, []);

    return (
        <canvas
            ref={canvasRef}
            style={{
                position: "fixed",
                top: 0, 
                left: 0,
                width: "100vw", 
                height: "100vh",
                pointerEvents: "none",
                zIndex: 9999,
            }}
        />
    );
};
