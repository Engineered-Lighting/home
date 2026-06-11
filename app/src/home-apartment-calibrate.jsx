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

    function CalibrateOverlay({ cam, trackerBase, onPickRequest, onDone, onIntrinsics, registerApi }) {
        const [pairs, setPairs] = useState([]);
        const [pendingPx, setPendingPx] = useState(null);
        const [busy, setBusy] = useState(false);
        const [result, setResult] = useState(null);
        const [error, setError] = useState(null);
        const [thumbs, setThumbs] = useState([]);     // capture thumbnails
        const [views, setViews] = useState(0);        // accepted board-views
        const [lens, setLens] = useState(null);       // solved intrinsics
        const imgRef = useRef(null);

        const snap = async () => {
            setBusy(true); setError(null);
            try {
                const r = await (window.tauriFetch || fetch)(`${trackerBase}/calib/${cam}/capture/snap`, { method: "POST" });
                const j = await r.json();
                if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
                setThumbs((t) => [...t.slice(-7), { src: j.thumb, n: j.new_views }]);
                setViews(j.total_views);
            } catch (e) { setError(String(e.message || e)); }
            setBusy(false);
        };

        const solveLens = async () => {
            setBusy(true); setError(null);
            try {
                const r = await (window.tauriFetch || fetch)(`${trackerBase}/calib/${cam}/capture/solve`, { method: "POST" });
                const j = await r.json();
                if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
                setLens(j);
                onIntrinsics && onIntrinsics(j);   // host persists into the model
            } catch (e) { setError(String(e.message || e)); }
            setBusy(false);
        };

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
                const r = await (window.tauriFetch || fetch)(`${trackerBase}/calib/${cam}/extrinsics`, {
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
            // SIDE-BY-SIDE: snapshot panel docked left, the live 3D mesh stays
            // visible and clickable on the right half (user feedback — the
            // full-screen overlay hid the mesh during correspondence picking)
            style: { position: "absolute", top: 0, left: 0, bottom: 0, width: "46%",
                     zIndex: 40, background: "rgba(5,6,9,0.96)",
                     borderRight: "1px solid #2a3242", overflowY: "auto",
                     display: "flex", flexDirection: "column", padding: 14, gap: 10,
                     color: "var(--hg-ice, #cfe2ff)" },
        },
            React.createElement("div", { style: { ...mono, fontSize: 11 } },
                `calibrate · ${cam} — STEP 1 lens: scatter the dot boards in view, `
                + `snap, rearrange, snap again (≥8 board-views, more is better), then solve lens. `
                + `STEP 2 pose: click a point in the snapshot, then the same spot in 3D (≥8 pairs).`
                + (pendingPx ? "  → now click the 3D point" : "")),
            React.createElement("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
                React.createElement("button", { style: mono, disabled: busy, onClick: snap }, "snap boards"),
                React.createElement("button", { style: mono, disabled: busy || views < 8, onClick: solveLens },
                    `solve lens (${views} views)`),
                React.createElement("button", { style: mono, disabled: busy, onClick: async () => {
                    await (window.tauriFetch || fetch)(`${trackerBase}/calib/${cam}/capture/reset`, { method: "POST" });
                    setThumbs([]); setViews(0); setLens(null);
                } }, "reset"),
                lens && React.createElement("span", { style: mono },
                    `lens solved · rms ${lens.rms_px.toFixed(2)} px · ${lens.views} views`)),
            thumbs.length > 0 && React.createElement("div",
                { style: { display: "flex", gap: 4, overflowX: "auto" } },
                thumbs.map((t, i) => React.createElement("img", {
                    key: i, src: t.src,
                    title: `${t.n} new view(s)`,
                    style: { height: 74, border: t.n ? "1px solid #4a8" : "1px solid #844" },
                }))),
            React.createElement("img", {
                ref: imgRef, onClick: clickImage,
                src: `${trackerBase}/calib/${cam}/snapshot`,
                style: { width: "100%", border: "1px solid #2a3242", cursor: "crosshair",
                         opacity: pendingPx ? 0.55 : 1 },
            }),
            pendingPx && React.createElement("div",
                { style: { ...mono, color: "#ffd479" } },
                "→ now click the SAME spot in the 3D view on the right"),
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
