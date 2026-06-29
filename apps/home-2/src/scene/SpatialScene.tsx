import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { parseBinaryLittleEndianPly } from "./ply";
import type { ApartmentDevice, ApartmentModel, Track, Vec3 } from "../lib/home-core/types";
import { roomCenter } from "../lib/home-core/apartment";

export type SceneMode = "hybrid" | "cloud" | "photo" | "mesh";

export type AnchorPosition = {
  left: number;
  top: number;
  visible: boolean;
};

type Props = {
  assetBase: string;
  model: ApartmentModel;
  activeRoomId: string | null;
  track: Track | null;
  mode: SceneMode;
  daylight: boolean;
  selectedCameraId: string | null;
  onCameraSelect: (device: ApartmentDevice) => void;
  onAnchorChange: (anchor: AnchorPosition) => void;
};

type NodeRecord = {
  object: THREE.Object3D;
  device: ApartmentDevice;
};

type SceneRuntime = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  root: THREE.Group;
  pointMaterial: THREE.PointsMaterial;
  activeRoomGroup: THREE.Group;
  nodeGroup: THREE.Group;
  dot: THREE.Mesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
  pulse: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>;
  trail: THREE.Line;
  raycaster: THREE.Raycaster;
  pointer: THREE.Vector2;
  nodes: NodeRecord[];
  frame: number;
};

const cameraVector = new THREE.Vector3();
const projectedVector = new THREE.Vector3();

export function SpatialScene({
  assetBase,
  model,
  activeRoomId,
  track,
  mode,
  daylight,
  selectedCameraId,
  onCameraSelect,
  onAnchorChange
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<SceneRuntime | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "fallback">("loading");

  const activeRoom = useMemo(
    () => model.zones.find((zone) => zone.id === activeRoomId) || model.zones.find((zone) => zone.id === "kitchen") || model.zones[0],
    [activeRoomId, model.zones]
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x020303, 0.035);

    const camera = new THREE.PerspectiveCamera(43, container.clientWidth / Math.max(1, container.clientHeight), 0.05, 120);
    camera.position.set(7.4, 9.5, 7.2);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(7.1, 3.7, 0.8);
    controls.enableDamping = true;
    controls.dampingFactor = 0.075;
    controls.minDistance = 5;
    controls.maxDistance = 26;
    controls.maxPolarAngle = Math.PI * 0.48;
    controls.update();

    const ambient = new THREE.AmbientLight(0xbfd8ff, 0.18);
    scene.add(ambient);
    const key = new THREE.DirectionalLight(0xffd29b, 1.2);
    key.position.set(4, 5, 7);
    scene.add(key);

    const root = new THREE.Group();
    scene.add(root);

    const pointMaterial = new THREE.PointsMaterial({
      size: 0.022,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.36,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });

    const fallbackGeometry = new THREE.BoxGeometry(14.2, 8.2, 2.4);
    const fallbackEdges = new THREE.EdgesGeometry(fallbackGeometry);
    const fallback = new THREE.LineSegments(
      fallbackEdges,
      new THREE.LineBasicMaterial({ color: 0x273241, transparent: true, opacity: 0.34 })
    );
    fallback.position.set(7.1, 3.9, 1.2);
    root.add(fallback);

    fetch(`${assetBase.replace(/\/+$/, "")}/points.sim.ply`, { cache: "force-cache" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then((buffer) => {
        const parsed = parseBinaryLittleEndianPly(buffer, 135_000);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(parsed.positions, 3));
        geometry.setAttribute("color", new THREE.BufferAttribute(parsed.colors, 3));
        const points = new THREE.Points(geometry, pointMaterial);
        root.remove(fallback);
        root.add(points);
        setLoadState("ready");
      })
      .catch((error) => {
        console.warn("[home2] point cloud load failed", error);
        setLoadState("fallback");
      });

    const activeRoomGroup = new THREE.Group();
    root.add(activeRoomGroup);

    const nodeGroup = new THREE.Group();
    root.add(nodeGroup);

    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(0.12, 32, 16),
      new THREE.MeshStandardMaterial({
        color: 0x63a8ff,
        emissive: 0x2b7cff,
        emissiveIntensity: 2.3,
        roughness: 0.38,
        metalness: 0.1
      })
    );
    dot.visible = false;
    root.add(dot);

    const pulse = new THREE.Mesh(
      new THREE.CircleGeometry(0.72, 64),
      new THREE.MeshBasicMaterial({
        color: 0x63a8ff,
        transparent: true,
        opacity: 0.12,
        side: THREE.DoubleSide,
        depthWrite: false
      })
    );
    pulse.rotation.x = -Math.PI / 2;
    pulse.visible = false;
    root.add(pulse);

    const trailGeometry = new THREE.BufferGeometry();
    trailGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(28 * 3), 3));
    const trail = new THREE.Line(
      trailGeometry,
      new THREE.LineBasicMaterial({ color: 0x3f86ff, transparent: true, opacity: 0.42 })
    );
    root.add(trail);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    runtimeRef.current = {
      renderer,
      scene,
      camera,
      controls,
      root,
      pointMaterial,
      activeRoomGroup,
      nodeGroup,
      dot,
      pulse,
      trail,
      raycaster,
      pointer,
      nodes: [],
      frame: 0
    };

    const resize = () => {
      camera.aspect = container.clientWidth / Math.max(1, container.clientHeight);
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };

    const onPointerDown = (event: PointerEvent) => {
      const runtime = runtimeRef.current;
      if (!runtime) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(runtime.nodes.map((node) => node.object), true);
      const hit = hits.find((candidate) => findCameraNode(candidate.object, runtime.nodes));
      const record = hit ? findCameraNode(hit.object, runtime.nodes) : null;
      if (record) onCameraSelect(record.device);
    };

    const animate = () => {
      const runtime = runtimeRef.current;
      if (!runtime) return;
      runtime.frame = window.requestAnimationFrame(animate);
      const time = performance.now() / 1000;
      controls.update();
      const scale = 1 + Math.sin(time * 2.8) * 0.08;
      pulse.scale.setScalar(scale);
      pulse.material.opacity = 0.1 + (Math.sin(time * 2.8) + 1) * 0.035;
      dot.scale.setScalar(1 + (Math.sin(time * 7.2) + 1) * 0.08);
      updateAnchor(runtime, dot, onAnchorChange);
      renderer.render(scene, camera);
    };

    window.addEventListener("resize", resize);
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    animate();

    return () => {
      window.removeEventListener("resize", resize);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      const runtime = runtimeRef.current;
      if (runtime) {
        window.cancelAnimationFrame(runtime.frame);
        runtime.controls.dispose();
        runtime.renderer.dispose();
      }
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, [assetBase, onAnchorChange, onCameraSelect]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.pointMaterial.opacity = mode === "cloud" ? 0.56 : mode === "photo" ? 0.18 : 0.34;
    runtime.pointMaterial.size = mode === "cloud" ? 0.028 : 0.022;
    runtime.scene.fog = new THREE.FogExp2(daylight ? 0xf7f5ee : 0x020303, daylight ? 0.018 : 0.035);
    runtime.renderer.setClearColor(daylight ? 0xf7f5ee : 0x000000, daylight ? 0.96 : 0);
  }, [daylight, mode]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    rebuildActiveRoom(runtime.activeRoomGroup, activeRoom || null, daylight, mode);
  }, [activeRoom, daylight, mode]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    rebuildNodes(runtime, model, selectedCameraId);
    const selected = model.devices.find((device) => device.id === selectedCameraId && device.type === "camera");
    if (selected?.pos) flyToCamera(runtime, selected);
  }, [model, selectedCameraId]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const precise = track?.pos || roomCenter(model, track?.room);
    if (!precise) {
      runtime.dot.visible = false;
      runtime.pulse.visible = false;
      return;
    }
    const isRoomOnly = !track?.pos;
    runtime.dot.visible = !isRoomOnly;
    runtime.pulse.visible = true;
    runtime.dot.position.set(precise[0], precise[1], 0.18);
    runtime.pulse.position.set(precise[0], precise[1], 0.025);
    updateTrail(runtime.trail, precise);
  }, [model, track]);

  return (
    <div className="spatial-scene" ref={containerRef}>
      <div className={`scene-load-state ${loadState === "ready" ? "is-hidden" : ""}`}>
        <span>{loadState === "loading" ? "loading scan" : loadState === "ready" ? "scan shell loaded" : "scan shell fallback"}</span>
      </div>
    </div>
  );
}

function rebuildActiveRoom(group: THREE.Group, room: { floor_polygon: [number, number][]; name?: string } | null, daylight: boolean, mode: SceneMode) {
  group.clear();
  if (!room || mode === "cloud") return;
  const shape = new THREE.Shape(room.floor_polygon.map(([x, y]) => new THREE.Vector2(x, y)));
  const floor = new THREE.Mesh(
    new THREE.ShapeGeometry(shape),
    new THREE.MeshStandardMaterial({
      color: daylight ? 0xe7d8c1 : 0x8f6843,
      roughness: 0.62,
      metalness: 0,
      transparent: true,
      opacity: daylight ? 0.56 : 0.42,
      side: THREE.DoubleSide
    })
  );
  floor.position.z = 0.018;
  group.add(floor);

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.ShapeGeometry(shape)),
    new THREE.LineBasicMaterial({ color: daylight ? 0x5b6f87 : 0xffc878, transparent: true, opacity: daylight ? 0.3 : 0.42 })
  );
  edges.position.z = 0.04;
  group.add(edges);

  const glow = new THREE.Mesh(
    new THREE.ShapeGeometry(shape),
    new THREE.MeshBasicMaterial({
      color: 0xffc878,
      transparent: true,
      opacity: daylight ? 0.08 : 0.12,
      side: THREE.DoubleSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    })
  );
  glow.position.z = 0.045;
  group.add(glow);
}

function rebuildNodes(runtime: SceneRuntime, model: ApartmentModel, selectedCameraId: string | null) {
  runtime.nodeGroup.clear();
  runtime.nodes = [];
  for (const device of model.devices || []) {
    if (!device.pos) continue;
    if (device.type === "camera") {
      const node = makeCameraNode(device, selectedCameraId === device.id);
      runtime.nodeGroup.add(node);
      runtime.nodes.push({ object: node, device });
    } else if (device.type === "light") {
      runtime.nodeGroup.add(makeLightNode(device));
    }
  }
}

function makeLightNode(device: ApartmentDevice): THREE.Object3D {
  const group = new THREE.Group();
  group.position.set(device.pos?.[0] || 0, device.pos?.[1] || 0, device.pos?.[2] || 0);
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(0.07, 18, 10),
    new THREE.MeshBasicMaterial({ color: 0xffc878 })
  );
  group.add(core);
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.32, 24, 12),
    new THREE.MeshBasicMaterial({ color: 0xffb84c, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending, depthWrite: false })
  );
  group.add(halo);
  const light = new THREE.PointLight(0xffc878, 0.55, 3.2);
  group.add(light);
  return group;
}

function makeCameraNode(device: ApartmentDevice, selected: boolean): THREE.Object3D {
  const group = new THREE.Group();
  const [x, y, z] = device.pos || [0, 0, 0];
  group.position.set(x, y, z);
  group.rotation.z = device.yaw_rad || 0;
  const color = selected ? 0x8fc3ff : 0x376fae;
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(0.11, 0.08, 0.08),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: selected ? 0.94 : 0.72 })
  );
  group.add(body);
  const lineGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0.85, -0.42, -0.3),
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0.85, 0.42, -0.3),
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0.85, -0.42, 0.3),
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0.85, 0.42, 0.3),
    new THREE.Vector3(0.85, -0.42, -0.3),
    new THREE.Vector3(0.85, 0.42, -0.3),
    new THREE.Vector3(0.85, 0.42, -0.3),
    new THREE.Vector3(0.85, 0.42, 0.3),
    new THREE.Vector3(0.85, 0.42, 0.3),
    new THREE.Vector3(0.85, -0.42, 0.3),
    new THREE.Vector3(0.85, -0.42, 0.3),
    new THREE.Vector3(0.85, -0.42, -0.3)
  ]);
  const frustum = new THREE.LineSegments(
    lineGeometry,
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: selected ? 0.74 : 0.36 })
  );
  group.add(frustum);
  group.userData.kind = "camera";
  group.userData.deviceId = device.id;
  return group;
}

function findCameraNode(object: THREE.Object3D, nodes: NodeRecord[]): NodeRecord | null {
  let current: THREE.Object3D | null = object;
  while (current) {
    const found = nodes.find((node) => node.object === current);
    if (found) return found;
    current = current.parent;
  }
  return null;
}

function flyToCamera(runtime: SceneRuntime, device: ApartmentDevice) {
  const [x, y, z] = device.pos || [7, 4, 2.2];
  const yaw = device.yaw_rad || 0;
  const forward = new THREE.Vector3(Math.cos(yaw), Math.sin(yaw), -0.18).normalize();
  const target = new THREE.Vector3(x, y, Math.max(0.8, z - 0.45)).add(forward.clone().multiplyScalar(2.1));
  const cameraPos = new THREE.Vector3(x, y, z).add(forward.clone().multiplyScalar(-1.45)).add(new THREE.Vector3(0, 0, 0.7));
  runtime.camera.position.lerp(cameraPos, 0.72);
  runtime.controls.target.lerp(target, 0.72);
  runtime.controls.update();
}

function updateTrail(line: THREE.Line, pos: Vec3) {
  const attribute = line.geometry.getAttribute("position") as THREE.BufferAttribute;
  for (let i = attribute.count - 1; i > 0; i -= 1) {
    attribute.setXYZ(i, attribute.getX(i - 1), attribute.getY(i - 1), attribute.getZ(i - 1));
  }
  attribute.setXYZ(0, pos[0], pos[1], 0.16);
  attribute.needsUpdate = true;
}

function updateAnchor(
  runtime: SceneRuntime,
  dot: THREE.Object3D,
  onAnchorChange: (anchor: AnchorPosition) => void
) {
  if (!dot.visible && !runtime.pulse.visible) {
    onAnchorChange({ left: 0, top: 0, visible: false });
    return;
  }
  cameraVector.copy(dot.visible ? dot.position : runtime.pulse.position);
  cameraVector.z += 0.82;
  projectedVector.copy(cameraVector).project(runtime.camera);
  const width = runtime.renderer.domElement.clientWidth;
  const height = runtime.renderer.domElement.clientHeight;
  onAnchorChange({
    left: (projectedVector.x * 0.5 + 0.5) * width,
    top: (-projectedVector.y * 0.5 + 0.5) * height,
    visible: projectedVector.z > -1 && projectedVector.z < 1
  });
}
