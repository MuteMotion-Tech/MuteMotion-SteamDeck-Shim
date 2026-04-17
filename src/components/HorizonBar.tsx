import React, { VFC, useEffect, useRef } from "react";
import { call } from "@decky/api";

// =============================================================================
// HorizonBar — Canvas 2D + Critically Damped Spring Physics (Artificial Horizon)
// =============================================================================
// Render loop driven by setInterval (NOT requestAnimationFrame) to survive
// Gamescope CEF render-pause during game launch.
// =============================================================================

const OMEGA = 20.0;           // spring constant
const TRAIL_LENGTH = 6;       // length of ghost tail
const GAIN_ROLL = 1.5;        // multiplier for rotation (deg)
const GAIN_PITCH = 8.0;       // multiplier for vertical translation (px)
const MAX_ROLL = 30.0;        // maximum rotation in degrees
const MAX_PITCH = 240.0;      // maximum vertical displacement
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

export const HorizonBar: VFC = () => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const lastTimeRef = useRef<number>(0);
    const targetRef = useRef({ roll: 0, pitch: 0 });
    const opacityRef = useRef(0.8);

    const stateRef = useRef({
        posRoll: 0, velRoll: 0,
        posPitch: 0, velPitch: 0,
        trail: [] as { r: number, p: number }[]
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

            // save to history buffer
            s.trail.push({ r: s.posRoll, p: s.posPitch });
            if (s.trail.length > TRAIL_LENGTH) s.trail.shift();

            const barWidth = w * 0.9;
            const barHeight = 4;
            const radius = barHeight / 2;

            ctx.save();
            ctx.translate(w / 2, h / 2);

            // paint trails behind main
            for (let t = 0; t < s.trail.length; t++) {
                const hist = s.trail[t];
                const tAlpha = baseAlpha * ((t + 1) / s.trail.length) * 0.3;
                const tWidth = barWidth * (0.8 + 0.2 * ((t + 1) / s.trail.length));

                ctx.save();
                ctx.rotate((hist.r * Math.PI) / 180);
                ctx.translate(0, hist.p);
                ctx.fillStyle = `rgba(0, 255, 204, ${tAlpha})`;
                ctx.beginPath();
                ctx.roundRect(-tWidth / 2, -barHeight / 2, tWidth, barHeight, radius);
                ctx.fill();
                ctx.restore();
            }

            // paint main active bar
            ctx.rotate((s.posRoll * Math.PI) / 180);
            ctx.translate(0, s.posPitch);

            ctx.fillStyle = `rgba(0, 255, 204, ${baseAlpha})`;
            ctx.shadowColor = `rgba(0, 255, 204, ${baseAlpha * 0.5})`;
            ctx.shadowBlur = 20;

            // draw rounded rectangle
            ctx.beginPath();
            ctx.roundRect(-barWidth / 2, -barHeight / 2, barWidth, barHeight, radius);
            ctx.fill();

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
