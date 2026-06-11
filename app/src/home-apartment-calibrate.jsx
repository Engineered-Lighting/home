/* home-apartment-calibrate.jsx — extrinsics correspondence capture (P4 §4.2).
 *
 * Overlay for edit mode: shows the camera's Frigate snapshot; the user
 * alternates clicking a pixel on the snapshot and the matching 3D point in
 * the scene (picked against the collision mesh by the host view, which calls
 * api.acceptScenePoint(xyz)). At >=8 pairs, "solve" POSTs
 *   {pairs:[{px:[u,v], xyz:[x,y,z]}...], image_size:[w,h]}
 * to <trackerBase>/calib/<cam>/extrinsics and reports RMS / per-point error.
 *
 * Contract with home-apartment.jsx:
 *   window.HomeApartmentCalibrate.open({ cam, trackerBase, onPickRequest, onDone })
 *     - onPickRequest(active): host enables 3D click-picking; for each pick it
 *       calls the returned api.acceptScenePoint([x,y,z]) (apartment frame).
 *     - onDone(result|null): solved extrinsics (server response) or cancel.
 */
(function () {
    const { useState, useRef, useEffect } = React;

    function CalibrateOverlay({ cam, trackerBase, onPickRequest, onDone, registerApi }) {
        const [pairs, setPairs] = useState([]);
        const [pendingPx, setPendingPx] = useState(null);
        const [busy, setBusy] = useState(false);
        const [result, setResult] = useState(null);
        const [error, setError] = useState(null);
        const imgRef = useRef(null);

        useEffect(() => {
            registerApi({
                acceptScenePoint: (xyz) => {
                    setPendingPx((px) => {
                        if (!px) return px;            // ignore picks with no pixel staged
                        setPairs((p) => [...p, { px, xyz }]);
                        return null;
                    });
                },
            });
        }, [registerApi]);

        useEffect(() => { onPickRequest(!!pendingPx); }, [!!pendingPx]);

        const clickImage = (e) => {
            const img = imgRef.current;
            const r = img.getBoundingClientRect();
            const u = ((e.clientX - r.left) / r.width) * img.naturalWidth;
            const v = ((e.clientY - r.top) / r.height) * img.naturalHeight;
            setPendingPx([Math.round(u), Math.round(v)]);
        };

        const solve = async () => {
            setBusy(true); setError(null);
            try {
                const img = imgRef.current;
                const r = await fetch(`${trackerBase}/calib/${cam}/extrinsics`, {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ pairs, image_size: [img.naturalWidth, img.naturalHeight] }),
                });
                const j = await r.json();
                if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
                setResult(j);
            } catch (e) { setError(String(e.message || e)); }
            setBusy(false);
        };

        const mono = { fontFamily: "'Geist Mono', monospace", fontSize: 10, letterSpacing: "0.08em" };
        return React.createElement("div", {
            // while a pixel is staged the wrapper lets clicks fall through to
            // the 3D canvas for the matching scene pick
            style: { position: "absolute", inset: 0, zIndex: 40,
                     background: pendingPx ? "transparent" : "rgba(5,6,9,0.92)",
                     pointerEvents: pendingPx ? "none" : "auto",
                     display: "flex", flexDirection: "column", padding: 18, gap: 10, color: "var(--hg-ice, #cfe2ff)" },
        },
            React.createElement("div", { style: { ...mono, fontSize: 11 } },
                `calibrate · ${cam} — click a recognizable point in the snapshot, then click the same `
                + `spot in the 3D view behind this panel. ${pairs.length} pair(s); need ≥8.`
                + (pendingPx ? "  → now click the 3D point" : "")),
            React.createElement("img", {
                ref: imgRef, onClick: clickImage,
                src: `${trackerBase}/calib/${cam}/snapshot`,
                style: { maxWidth: "62%", border: "1px solid #2a3242", cursor: "crosshair",
                         opacity: pendingPx ? 0.45 : 1 },
            }),
            result && React.createElement("div", { style: mono },
                `solved · rms ${result.rms_px?.toFixed?.(2)} px · pos σ ${result.pos_sigma_m ?? "?"} m`),
            error && React.createElement("div", { style: { ...mono, color: "#ff8989" } }, error),
            React.createElement("div", { style: { display: "flex", gap: 8 } },
                React.createElement("button", { style: mono, disabled: pairs.length < 8 || busy, onClick: solve }, "solve"),
                React.createElement("button", { style: mono, onClick: () => setPairs((p) => p.slice(0, -1)) }, "undo pair"),
                React.createElement("button", { style: mono, onClick: () => onDone(result) }, result ? "accept · close" : "cancel")));
    }

    window.HomeApartmentCalibrate = { Component: CalibrateOverlay };
})();
