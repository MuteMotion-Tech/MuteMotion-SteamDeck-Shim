import React, { VFC, useEffect, useRef } from "react";
import { call } from "@decky/api";

// =============================================================================
// DotGridView — Canvas 2D + Critically Damped Spring Physics
// =============================================================================
// Uses analytical critically damped spring integration (ζ=1.0, ω=20 rad/s)
// with Canvas 2D rendering for sub-pixel accuracy.
// Render loop driven by setInterval (NOT requestAnimationFrame) to survive
// Gamescope CEF render-pause during game launch.
// =============================================================================

const OMEGA = 20.0;           // spring constant — 200ms settling time
const TRAIL_LENGTH = 8;       // number of ghost positions to render
const DOT_RADIUS = 5;
const DOT_COUNT = 4;           // per side (4 left + 4 right = 8 total)
const MARGIN_PERCENT = 0.05;   // 5% from each edge
const GAIN_X = 12.0;             // horizontal raw IMU offset scaling (original)
const GAIN_Y = 24.0;             // vertical raw IMU offset scaling (increased)
const MAX_TRAVEL_X = 30.0;       // max displacement horizontally in px
const MAX_TRAVEL_Y = 90.0;       // max displacement vertically in px
const RENDER_INTERVAL = 16;      // ~60fps via setInterval

interface SpringState {
    posX: number;
    posY: number;
    velX: number;
    velY: number;
    // circular buffer of past positions for trail rendering
    trail: { x: number; y: number }[];
}

/** Analytical critically damped spring — exact ODE solution, unconditionally stable */
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

export const DotGridView: VFC = () => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const lastTimeRef = useRef<number>(0);
    const targetRef = useRef({ x: 0, y: 0 });
    const opacityRef = useRef(0.8);

    // spring state: 8 dots (4 left + 4 right)
    const springsRef = useRef<SpringState[]>(
        Array.from({ length: DOT_COUNT * 2 }, () => ({
            posX: 0, posY: 0, velX: 0, velY: 0,
            trail: [],
        }))
    );

    // poll IMU data — decoupled from render loop
    useEffect(() => {
        let mounted = true;
        const poll = async () => {
            if (!mounted) return;
            try {
                const r: any = await call("get_visual_offset", {});
                if (r && r.offset !== undefined && r.offset !== -88.8) {
                    const inv = r.invert_axis === false ? 1.0 : -1.0;
                    const rx = (r.offset_x || 0) * GAIN_X * inv;
                    const ry = (r.offset_y || 0) * GAIN_Y * inv;
                    targetRef.current.x = Math.max(-MAX_TRAVEL_X, Math.min(MAX_TRAVEL_X, rx));
                    targetRef.current.y = Math.max(-MAX_TRAVEL_Y, Math.min(MAX_TRAVEL_Y, ry));
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

        // initialize spring anchors
        const w0 = canvas.clientWidth || 1280;
        const h0 = canvas.clientHeight || 800;
        const leftX0 = w0 * MARGIN_PERCENT;
        const rightX0 = w0 * (1 - MARGIN_PERCENT);
        const springs = springsRef.current;

        for (let i = 0; i < DOT_COUNT; i++) {
            const y = h0 * (0.25 + (i * 0.5) / (DOT_COUNT - 1));
            springs[i].posX = leftX0;
            springs[i].posY = y;
            springs[DOT_COUNT + i].posX = rightX0;
            springs[DOT_COUNT + i].posY = y;
        }

        lastTimeRef.current = 0;
        const ctx = canvas.getContext("2d");

        const renderFrame = () => {
            if (!ctx) return;
            const now = performance.now();

            // use actual DOM metrics to prevent 1x1 pixel buffer scaling bugs
            const w = canvas.clientWidth || 1280;
            const h = canvas.clientHeight || 800;

            if (canvas.width !== w || canvas.height !== h) {
                canvas.width = w;
                canvas.height = h;
            }

            // delta time
            if (lastTimeRef.current === 0) lastTimeRef.current = now;
            let dt = (now - lastTimeRef.current) / 1000;
            lastTimeRef.current = now;
            if (dt <= 0 || dt > 0.5) dt = 1 / 60;
            const target = targetRef.current;
            const baseAlpha = opacityRef.current;

            // force exact clear on the entire buffer
            ctx.clearRect(0, 0, w, h);

            // strictly user-specified opacity
            const dynAlpha = baseAlpha;

            const leftX = w * MARGIN_PERCENT;
            const rightX = w * (1 - MARGIN_PERCENT);

            for (let i = 0; i < DOT_COUNT; i++) {
                const anchorY = h * (0.25 + (i * 0.5) / (DOT_COUNT - 1));

                // --- left dot ---
                const ls = springs[i];
                const ltx = leftX + target.x;
                const lty = anchorY + target.y;
                const lx = updateSpring(ls.posX, ls.velX, ltx, dt);
                const ly = updateSpring(ls.posY, ls.velY, lty, dt);
                ls.posX = lx.pos; ls.velX = lx.vel;
                ls.posY = ly.pos; ls.velY = ly.vel;

                // push current position to trail, keep max length
                ls.trail.push({ x: ls.posX, y: ls.posY });
                if (ls.trail.length > TRAIL_LENGTH) ls.trail.shift();

                // draw trail (oldest = most transparent)
                for (let t = 0; t < ls.trail.length; t++) {
                    const trailAlpha = dynAlpha * ((t + 1) / ls.trail.length) * 0.4;
                    const trailRadius = DOT_RADIUS * ((t + 1) / ls.trail.length);
                    ctx.beginPath();
                    ctx.arc(ls.trail[t].x, ls.trail[t].y, trailRadius, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(0, 255, 204, ${trailAlpha})`;
                    ctx.fill();
                }

                // draw main dot
                ctx.beginPath();
                ctx.arc(ls.posX, ls.posY, DOT_RADIUS, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(0, 255, 204, ${dynAlpha})`;
                ctx.fill();

                // --- right dot ---
                const rs = springs[DOT_COUNT + i];
                const rtx = rightX + target.x;
                const rty = anchorY + target.y;
                const rx2 = updateSpring(rs.posX, rs.velX, rtx, dt);
                const ry2 = updateSpring(rs.posY, rs.velY, rty, dt);
                rs.posX = rx2.pos; rs.velX = rx2.vel;
                rs.posY = ry2.pos; rs.velY = ry2.vel;

                rs.trail.push({ x: rs.posX, y: rs.posY });
                if (rs.trail.length > TRAIL_LENGTH) rs.trail.shift();

                for (let t = 0; t < rs.trail.length; t++) {
                    const trailAlpha = dynAlpha * ((t + 1) / rs.trail.length) * 0.4;
                    const trailRadius = DOT_RADIUS * ((t + 1) / rs.trail.length);
                    ctx.beginPath();
                    ctx.arc(rs.trail[t].x, rs.trail[t].y, trailRadius, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(0, 255, 204, ${trailAlpha})`;
                    ctx.fill();
                }

                ctx.beginPath();
                ctx.arc(rs.posX, rs.posY, DOT_RADIUS, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(0, 255, 204, ${dynAlpha})`;
                ctx.fill();
            }
        };

        const iv = setInterval(renderFrame, RENDER_INTERVAL);
        return () => clearInterval(iv);
    }, []);

    return (
        <canvas
            ref={canvasRef}
            style={{
                position: "fixed",  // fixed so it doesn't scroll inside the DOM
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
