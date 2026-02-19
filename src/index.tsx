import {
    definePlugin,
    ServerAPI,
} from "@decky/api";
import {
    PanelSection,
    PanelSectionRow,
    ToggleField,
    staticClasses,
} from "@decky/ui";
import { FaShip } from "react-icons/fa";
import React, { VFC, useState, useEffect } from "react";
import { routerHook } from "@decky/api";
import { StateBoolean } from "./state";
import { useUIComposition, UIComposition } from "./uiComposition";

const Overlay: VFC<{ state: StateBoolean }> = React.memo(({ state }) => {
    const [visible, setVisible] = useState(state.GetState());

    // "ghost mode" - use notification layer because the footer kept stealing my inputs
    useUIComposition(visible ? UIComposition.Notification : UIComposition.Hidden);

    useEffect(() => {
        const onStateChange = (val: boolean) => setVisible(val);
        state.onStateChanged(onStateChange);
        return () => state.offStateChanged(onStateChange);
    }, [state]);

    // input isolation voodoo (click-through hack)
    useEffect(() => {
        const applyStyles = (element: HTMLElement) => {
            element.style.setProperty("pointer-events", "none", "important");
            element.style.setProperty("touch-action", "none", "important");
            element.style.setProperty("user-select", "none", "important");
            element.style.setProperty("-webkit-user-drag", "none", "important");
        };

        const resetStyles = (element: HTMLElement) => {
            element.style.removeProperty("pointer-events");
            element.style.removeProperty("touch-action");
            element.style.removeProperty("user-select");
            element.style.removeProperty("-webkit-user-drag");
        };

        const root = document.getElementById("root");

        if (visible) {
            applyStyles(document.body);
            applyStyles(document.documentElement);
            if (root) applyStyles(root);
        } else {
            resetStyles(document.body);
            resetStyles(document.documentElement);
            if (root) resetStyles(root);
        }
        return () => {
            resetStyles(document.body);
            resetStyles(document.documentElement);
            if (root) resetStyles(root);
        };
    }, [visible]);

    if (!visible) return null;

    return (
        <div
            style={{
                position: "fixed",
                top: 0,
                left: 0,
                width: "100vw",
                height: "100vh",
                pointerEvents: "none",
                userSelect: "none",
                touchAction: "none",
                zIndex: 7002, // magic altitude that fits between game and steam ui
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                backgroundColor: "transparent",
                willChange: "transform",
                transform: "translate3d(0, 0, 0)",
                backfaceVisibility: "hidden",
            }}
        >
            <style>
                {`
                    @keyframes tilt {
                        0% { transform: rotate(0deg); }
                        25% { transform: rotate(5deg); }
                        50% { transform: rotate(0deg); }
                        75% { transform: rotate(-5deg); }
                        100% { transform: rotate(0deg); }
                    }
                `}
            </style>
            <div
                style={{
                    width: "90%",
                    height: "2px",
                    backgroundColor: "red",
                    boxShadow: "0 0 15px 2px red",
                    animation: "tilt 6s infinite ease-in-out",
                    opacity: 0.8,
                    willChange: "transform",
                    transform: "translateZ(0)",
                }}
            ></div>
        </div>
    );
});

const Content: VFC<{ serverAPI: ServerAPI; state: StateBoolean }> = React.memo(({ state }) => {
    const [isEnabled, setIsEnabled] = useState(state.GetState());

    const handleToggle = (val: boolean) => {
        setIsEnabled(val);
        state.SetState(val);
    };

    useEffect(() => {
        const handler = (val: boolean) => setIsEnabled(val);
        state.onStateChanged(handler);
        return () => state.offStateChanged(handler);
    }, [state]);

    return (
        <PanelSection title="MuteMotion Controls">
            <PanelSectionRow>
                <ToggleField
                    label="Artificial Horizon"
                    description="Displays a visual reference to reduce motion sickness."
                    checked={isEnabled}
                    onChange={handleToggle}
                />
            </PanelSectionRow>
        </PanelSection>
    );
});

export default definePlugin((serverApi) => {
    console.log('[MuteMotion] Initializing...');

    // shared state stuff
    const state = new StateBoolean(false); // default off so it doesnt annoy people

    // routerhook check because sometimes it just disappears
    if (routerHook) {
        console.log('[MuteMotion] Registering overlay...');
        routerHook.addGlobalComponent("MuteMotionOverlay", () => (
            <Overlay state={state} />
        ));
    } else {
        console.error("[MuteMotion] routerHook is undefined! Overlay will not be registered.");
    }

    return {
        title: <div className={staticClasses.Title}>MuteMotion</div>,
        content: <Content serverAPI={serverApi} state={state} />,
        icon: <FaShip />,
        onDismount() {
            console.log('[MuteMotion] Dismounting...');
            if (routerHook) {
                routerHook.removeGlobalComponent("MuteMotionOverlay");
            }
        },
    };
});
