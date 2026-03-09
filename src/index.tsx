import {
    definePlugin,
    call,
} from "@decky/api";
import {
    PanelSection,
    PanelSectionRow,
    ToggleField,
    DialogButton,
    staticClasses,
} from "@decky/ui";
import { FaShip } from "react-icons/fa";
import React, { VFC, useState, useEffect } from "react";
import { routerHook } from "@decky/api";

import { StateBoolean } from "./state";
import { useUIComposition, UIComposition } from "./uiComposition";
import { HorizonBar } from "./components/HorizonBar";
import { BallView } from "./components/BallView";

const Overlay: VFC<{ 
    enabledState: StateBoolean, 
    ballModeState: StateBoolean 
}> = React.memo(({ enabledState, ballModeState }) => {
    const [visible, setVisible] = useState(enabledState.GetState());
    const [useBall, setUseBall] = useState(ballModeState.GetState());

    // "ghost mode" - use notification layer because the footer kept stealing my inputs
    useUIComposition(visible ? UIComposition.Notification : UIComposition.Hidden);

    useEffect(() => {
        const onEnabledChange = (val: boolean) => setVisible(val);
        const onModeChange = (val: boolean) => setUseBall(val);
        
        enabledState.onStateChanged(onEnabledChange);
        ballModeState.onStateChanged(onModeChange);
        
        return () => {
            enabledState.offStateChanged(onEnabledChange);
            ballModeState.offStateChanged(onModeChange);
        };
    }, [enabledState, ballModeState]);

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
                backgroundColor: "transparent",
            }}
        >
            {useBall ? <BallView /> : <HorizonBar />}
        </div>
    );
});

const Content: VFC<{ 
    enabledState: StateBoolean,
    ballModeState: StateBoolean
}> = React.memo(({ enabledState, ballModeState }) => {
    const [isEnabled, setIsEnabled] = useState(enabledState.GetState());
    const [isBallMode, setIsBallMode] = useState(ballModeState.GetState());

    useEffect(() => {
        const eHandler = (val: boolean) => setIsEnabled(val);
        const bHandler = (val: boolean) => setIsBallMode(val);
        
        enabledState.onStateChanged(eHandler);
        ballModeState.onStateChanged(bHandler);
        
        return () => {
            enabledState.offStateChanged(eHandler);
            ballModeState.offStateChanged(bHandler);
        };
    }, [enabledState, ballModeState]);

    return (
        <PanelSection title="Mitigation Engine">
            <PanelSectionRow>
                <ToggleField
                    label="Active Mitigation"
                    description="Enable IMU-driven vestibular synchronization overlay"
                    checked={isEnabled}
                    onChange={(val) => {
                        setIsEnabled(val);
                        enabledState.SetState(val);
                    }}
                />
            </PanelSectionRow>
            
            <PanelSectionRow>
                <ToggleField
                    label="2D Ball Mode"
                    description="Use 2D dot tracking instead of full horizon bar"
                    checked={isBallMode}
                    onChange={(val) => {
                        setIsBallMode(val);
                        ballModeState.SetState(val);
                    }}
                    disabled={!isEnabled}
                />
            </PanelSectionRow>
            
            <PanelSectionRow>
                <DialogButton onClick={() => {
                    // mostly just making sure the bridge is alive
                    call("get_visual_offset", {})
                        .then((res: any) => console.log("[MuteMotion] Brain pulse check:", res))
                        .catch((err: any) => console.error("[MuteMotion] Brain is fried:", err));
                }}>
                    Test Engine Ping
                </DialogButton>
            </PanelSectionRow>
        </PanelSection>
    );
});

export default definePlugin(() => {
    console.log('[MuteMotion] Initializing Core Injection...');

    // state nodes
    const enabledState = new StateBoolean(false); 
    const ballModeState = new StateBoolean(false);

    // Mount overlay function (incident 026b fix: routerhook re-registrations)
    // we make this a standalone function so we can re-mount if it dies
    const mountOverlay = () => {
        if (routerHook) {
            console.log('[MuteMotion] RouterHook found, mounting ghost overlay...');
            // remove before add just in case there's an orphan overlay
            routerHook.removeGlobalComponent("MuteMotionOverlay");
            routerHook.addGlobalComponent("MuteMotionOverlay", () => (
                <Overlay enabledState={enabledState} ballModeState={ballModeState} />
            ));
            return true;
        } else {
            console.error("[MuteMotion] routerHook is undefined! the plugin is basically bricked.");
            return false;
        }
    };

    // Initial mount
    mountOverlay();

    return {
        name: "MuteMotion",
        title: <div className={staticClasses.Title}>MuteMotion</div>,
        content: <Content enabledState={enabledState} ballModeState={ballModeState} />,
        icon: <FaShip />,
        onDismount() {
            console.log('[MuteMotion] Plugin dismounting, dropping hooks...');
            if (routerHook) {
                routerHook.removeGlobalComponent("MuteMotionOverlay");
            }
        },
    };
});
