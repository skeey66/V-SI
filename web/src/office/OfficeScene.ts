/** 3D 사무실 장면. **판단은 없고 그리기만 한다.**
 *
 * 무엇을 그릴지는 `officeState.ts` 가 정한다(순수 함수, 테스트 있음). 이
 * 파일은 WebGL 이라 node 에서 돌릴 수 없어 자동 검증이 없다 — 그래서 로직을
 * 여기 두지 않는 것이 규율이다. `apply()` 가 받는 것은 이미 결정된 상태다.
 *
 * React 밖에 산다. 렌더 루프는 `requestAnimationFrame` 이고 React 의 렌더
 * 주기와 무관하다 — 엮으면 초당 60번 리렌더가 난다.
 */
import * as THREE from "three";
import type { AgentId, AgentMotion, HandUp, OfficeState } from "./officeState";

const ROT = (Math.PI * 38) / 180; // 책상 회전. 캐릭터가 모니터를 보면서도 얼굴이 보인다.
const DZ = 60;
const SEAT_Y = 13; // 의자 좌석 높이. 걸을 땐 0 으로 내려온다.
const LANE = 142; // 책상 앞 복도 — 걸어갈 때 책상을 통과하지 않는다.
const DT_CAP = 0.05; // 탭이 멈췄다 돌아와도 순간이동하지 않게.

type Seat = {
  id: AgentId;
  label: string;
  color: number;
  x: number;
  step: number;
};

const SEATS: Seat[] = [
  { id: "planner", label: "기획팀", color: 0xd4805c, x: -285, step: 1 },
  { id: "dev", label: "개발팀", color: 0x6f9fd8, x: -95, step: 2 },
  { id: "qa", label: "QA팀", color: 0xd8b64a, x: 95, step: 3 },
  { id: "security", label: "보안팀", color: 0x79ad86, x: 285, step: 4 },
];

type Guy = {
  group: THREE.Group;
  body: THREE.Mesh;
  arms: THREE.Mesh[];
  legs: THREE.Group[];
  home: THREE.Vector3;
  rot: number;
  desk: { x: number; z: number };
  t: number;
};

type Trip = {
  guy: Guy;
  pts: THREE.Vector3[];
  idx: number;
  t: number;
  phase: "go" | "back";
  to: AgentId;
  paper: THREE.Mesh;
  hold: number;
};

export class OfficeScene {
  private readonly host: HTMLElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly clock = new THREE.Clock();

  private readonly guys = new Map<AgentId, Guy>();
  private readonly screens = new Map<AgentId, THREE.Mesh>();
  private readonly spills = new Map<AgentId, THREE.PointLight>();
  private readonly hands = new Map<AgentId, THREE.Mesh>();
  private readonly boards = new Map<
    AgentId,
    { canvas: HTMLCanvasElement; tex: THREE.CanvasTexture; seat: Seat }
  >();
  private readonly boardText = new Map<AgentId, string>();
  private readonly ceilingLights: THREE.PointLight[] = [];
  private readonly leds: THREE.Mesh[] = [];
  private readonly rbCache = new Map<string, THREE.BufferGeometry>();
  private readonly disposables: { dispose(): void }[] = [];

  private paperPass!: THREE.Mesh;
  private paperReject!: THREE.Mesh;
  private trip: Trip | null = null;
  private lastHandoffId = 0;
  private state: OfficeState | null = null;

  private raf = 0;
  private running = false;
  private blink = 0;
  private ang = 0.1;
  private dist = 545;
  private hi = 132;
  private ty = 80;
  private dragging = false;
  private lastX = 0;
  private downX = 0;
  private downY = 0;
  /** 캐릭터를 클릭했을 때 부를 콜백. 드래그(카메라 회전)와 구분해서 부른다. */
  onPick: ((agent: AgentId) => void) | null = null;
  private readonly raycaster = new THREE.Raycaster();

  private readonly ro: ResizeObserver;
  private readonly onPointerDown: (e: PointerEvent) => void;
  private readonly onPointerUp: (e: PointerEvent) => void;
  private readonly onPointerMove: (e: PointerEvent) => void;
  private readonly onWheel: (e: WheelEvent) => void;
  private readonly onVisibility: () => void;
  private readonly onContextLost: (e: Event) => void;
  private readonly onContextRestored: () => void;

  constructor(host: HTMLElement) {
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.92;
    host.appendChild(this.renderer.domElement);

    this.scene.background = new THREE.Color(0x07080b);
    this.scene.fog = new THREE.Fog(0x07080b, 820, 1900);

    this.camera = new THREE.PerspectiveCamera(40, 1, 1, 3200);

    this.buildEnvironment();
    this.buildLights();
    this.buildRoom();
    this.buildDesks();
    this.buildProps();
    this.buildPapers();
    this.place();

    // ── 생명주기. 목업에는 없던 부분이고, 없으면 HMR 로 몇 번 고치는 사이에
    //    WebGL 컨텍스트가 쌓여 브라우저가 죽는다.
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();

    this.onPointerDown = (e) => {
      this.dragging = true;
      this.lastX = e.clientX;
      this.downX = e.clientX;
      this.downY = e.clientY;
      host.style.cursor = "grabbing";
    };
    this.onPointerUp = (e) => {
      const wasDown = this.dragging;
      this.dragging = false;
      host.style.cursor = "grab";
      if (!wasDown) return;
      // 드래그로 카메라를 돌린 것과 캐릭터를 고른 것을 구분한다. 몇 픽셀
      // 흔들리는 것은 클릭으로 본다 — 완전히 정지한 클릭은 드물다.
      if (Math.hypot(e.clientX - this.downX, e.clientY - this.downY) > 5) return;
      const hit = this.pick(e);
      if (hit) this.onPick?.(hit);
    };
    this.onPointerMove = (e) => {
      if (!this.dragging) return;
      this.ang -= (e.clientX - this.lastX) * 0.006;
      this.lastX = e.clientX;
      this.place();
    };
    this.onWheel = (e) => {
      e.preventDefault();
      this.dist = Math.max(230, Math.min(1000, this.dist + e.deltaY * 0.5));
      this.hi = this.dist * 0.24;
      this.place();
    };
    // 보이지도 않는 장면에 GPU 를 태우지 않는다.
    this.onVisibility = () => {
      if (document.hidden) this.stop();
      else this.start();
    };
    this.onContextLost = (e) => {
      e.preventDefault();
      this.stop();
    };
    this.onContextRestored = () => this.start();

    host.style.cursor = "grab";
    host.addEventListener("pointerdown", this.onPointerDown);
    host.addEventListener("wheel", this.onWheel, { passive: false });
    window.addEventListener("pointerup", this.onPointerUp);
    window.addEventListener("pointermove", this.onPointerMove);
    document.addEventListener("visibilitychange", this.onVisibility);
    this.renderer.domElement.addEventListener("webglcontextlost", this.onContextLost);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.onContextRestored);
  }

  // ───────────────────────────────────────────────────────── 공개 API

  /** 결정된 사무실 상태를 반영한다. 매 이벤트마다 불려도 싸다 — 걸음처럼
   * 한 번만 재생해야 하는 것은 `id` 로 걸러낸다. */
  apply(next: OfficeState): void {
    this.state = next;
    for (const seat of SEATS) {
      const guy = this.guys.get(seat.id);
      if (!guy) continue;
      const hand = this.hands.get(seat.id);
      const motion = next.motions[seat.id];

      // 보드는 문구가 **바뀔 때만** 다시 그린다 — 캔버스 재업로드는 GPU 로
      // 텍스처를 다시 올리는 일이라 매 이벤트마다 할 일이 아니다.
      const board = this.boards.get(seat.id);
      if (board) {
        const [status, tone] = this.boardStatus(
          motion,
          next.handUp?.agent === seat.id ? next.handUp.tone : undefined,
        );
        if (this.boardText.get(seat.id) !== status) {
          this.boardText.set(seat.id, status);
          this.drawBoard(board.canvas, board.seat, status, tone);
          board.tex.needsUpdate = true;
        }
      }
      if (hand) {
        hand.visible = motion === "hand-up";
        if (hand.visible) {
          const mat = hand.material as THREE.MeshStandardMaterial;
          const warn = next.handUp?.tone === "warn";
          mat.color.setHex(warn ? 0xd9a04f : 0xc05c5c);
          mat.emissive.setHex(warn ? 0x4a3a1d : 0x4a2020);
        }
      }
    }
    if (next.handoff && next.handoff.id !== this.lastHandoffId) {
      this.lastHandoffId = next.handoff.id;
      this.startTrip(next.handoff.from, next.handoff.to, next.handoff.kind === "reject");
    }
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.clock.getDelta(); // 쌓인 시간을 버린다.
    const tick = () => {
      if (!this.running) return;
      this.step(Math.min(DT_CAP, this.clock.getDelta()));
      this.renderer.render(this.scene, this.camera);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  /** **반드시 언마운트에서 불러야 한다.** 브라우저는 WebGL 컨텍스트 수를
   * 제한하므로(보통 8~16개), 정리하지 않으면 탭을 몇 번 오가는 것만으로
   * 장면이 안 뜨기 시작한다. */
  dispose(): void {
    this.stop();
    this.ro.disconnect();
    this.host.removeEventListener("pointerdown", this.onPointerDown);
    this.host.removeEventListener("wheel", this.onWheel);
    window.removeEventListener("pointerup", this.onPointerUp);
    window.removeEventListener("pointermove", this.onPointerMove);
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.renderer.domElement.removeEventListener("webglcontextlost", this.onContextLost);
    this.renderer.domElement.removeEventListener("webglcontextrestored", this.onContextRestored);

    this.scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose();
      const m = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(m)) m.forEach((x) => x.dispose());
      else m?.dispose();
    });
    for (const g of this.rbCache.values()) g.dispose();
    this.rbCache.clear();
    for (const d of this.disposables) d.dispose();
    this.scene.clear();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  resetCamera(): void {
    this.ang = 0.1;
    this.dist = 545;
    this.hi = 132;
    this.ty = 80;
    this.place();
  }

  // ───────────────────────────────────────────────────── 장면 만들기

  private rb(w: number, h: number, d: number, r = 2.4): THREE.BufferGeometry {
    const rr = Math.max(0.4, Math.min(r, w / 2.6, h / 2.6, d / 2.6));
    const key = `${w}|${h}|${d}|${rr.toFixed(2)}`;
    const hit = this.rbCache.get(key);
    if (hit) return hit;
    const sw = w - 2 * rr;
    const sh = h - 2 * rr;
    const rc = rr * 0.5;
    const s = new THREE.Shape();
    s.moveTo(-sw / 2 + rc, -sh / 2);
    s.lineTo(sw / 2 - rc, -sh / 2);
    s.quadraticCurveTo(sw / 2, -sh / 2, sw / 2, -sh / 2 + rc);
    s.lineTo(sw / 2, sh / 2 - rc);
    s.quadraticCurveTo(sw / 2, sh / 2, sw / 2 - rc, sh / 2);
    s.lineTo(-sw / 2 + rc, sh / 2);
    s.quadraticCurveTo(-sw / 2, sh / 2, -sw / 2, sh / 2 - rc);
    s.lineTo(-sw / 2, -sh / 2 + rc);
    s.quadraticCurveTo(-sw / 2, -sh / 2, -sw / 2 + rc, -sh / 2);
    const g = new THREE.ExtrudeGeometry(s, {
      depth: Math.max(0.1, d - 2 * rr),
      bevelEnabled: true,
      bevelSize: rr,
      bevelThickness: rr,
      bevelSegments: 3,
      curveSegments: 3,
      steps: 1,
    });
    g.center();
    g.computeVertexNormals();
    this.rbCache.set(key, g);
    return g;
  }

  private std(c: number, r = 0.55, m = 0.18) {
    return new THREE.MeshStandardMaterial({ color: c, roughness: r, metalness: m });
  }
  private toy(c: number) {
    return new THREE.MeshPhysicalMaterial({
      color: c,
      roughness: 0.42,
      metalness: 0.03,
      clearcoat: 0.75,
      clearcoatRoughness: 0.3,
    });
  }
  private R(w: number, h: number, d: number, mat: THREE.Material, r = 2.4) {
    return new THREE.Mesh(this.rb(w, h, d, r), mat);
  }
  private B(w: number, h: number, d: number, mat: THREE.Material) {
    return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  }
  private add<T extends THREE.Object3D>(m: T, x: number, y: number, z: number): T {
    m.position.set(x, y, z);
    this.scene.add(m);
    return m;
  }
  private at<T extends THREE.Object3D>(m: T, x: number, y: number, z: number): T {
    m.position.set(x, y, z);
    return m;
  }

  /** 가상의 방을 구워 환경맵으로 쓴다 — 표면이 주변 빛을 반사해야 평평해
   * 보이지 않는다. */
  private buildEnvironment(): void {
    const es = new THREE.Scene();
    const shell = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial({ side: THREE.BackSide, color: 0x2a3344, roughness: 1 }),
    );
    shell.scale.setScalar(1400);
    es.add(shell);
    const glow = (hex: number, w: number, h: number, d: number, x: number, y: number, z: number, i: number) => {
      const b = new THREE.Mesh(
        new THREE.BoxGeometry(w, h, d),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(hex).multiplyScalar(i) }),
      );
      b.position.set(x, y, z);
      es.add(b);
    };
    glow(0xffffff, 900, 24, 700, 0, 520, 0, 3.0);
    glow(0xb9d2ff, 24, 420, 900, -640, 180, 0, 1.5);
    glow(0xffd6a6, 24, 420, 900, 640, 180, 0, 1.0);
    glow(0x8fb4e6, 900, 360, 24, 0, 190, -660, 0.9);
    const pm = new THREE.PMREMGenerator(this.renderer);
    const target = pm.fromScene(es, 0.035, 1, 2600);
    this.scene.environment = target.texture;
    this.scene.environmentIntensity = 0.3;
    this.disposables.push(target);
    pm.dispose();
    es.traverse((o) => {
      const mesh = o as THREE.Mesh;
      mesh.geometry?.dispose();
      (mesh.material as THREE.Material | undefined)?.dispose();
    });
  }

  private buildLights(): void {
    this.scene.add(new THREE.HemisphereLight(0x8ea6c4, 0x0d1016, 0.15));
    const key = new THREE.DirectionalLight(0xfff0d8, 2.3);
    key.position.set(230, 360, 300);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0006;
    key.shadow.normalBias = 1.2;
    Object.assign(key.shadow.camera, {
      left: -600, right: 600, top: 600, bottom: -600, near: 1, far: 1300,
    });
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x7fa8dd, 0.32);
    rim.position.set(-320, 180, -300);
    this.scene.add(rim);
    const face = new THREE.DirectionalLight(0xffe9cf, 0.55);
    face.position.set(260, 120, 420);
    this.scene.add(face);
  }

  private buildRoom(): void {
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(1400, 1200),
      new THREE.MeshStandardMaterial({ color: 0x101520, roughness: 0.44, metalness: 0.22 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.scene.add(floor);

    const rug = new THREE.Mesh(
      new THREE.PlaneGeometry(860, 330),
      new THREE.MeshStandardMaterial({ color: 0x181e28, roughness: 0.96, metalness: 0 }),
    );
    rug.rotation.x = -Math.PI / 2;
    rug.position.set(0, 0.6, 20);
    rug.receiveShadow = true;
    this.scene.add(rug);

    const wall = new THREE.MeshStandardMaterial({ color: 0x151a24, roughness: 0.88, metalness: 0.04 });
    this.add(this.B(1180, 300, 12, wall), 0, 150, -260).receiveShadow = true;
    this.add(this.B(12, 300, 900, wall), -585, 150, 80).receiveShadow = true;
    this.add(this.B(12, 300, 900, wall), 585, 150, 80).receiveShadow = true;
    this.add(this.R(1180, 9, 6, this.std(0x39434f, 0.5, 0.3), 1.6), 0, 4.5, -253);

    const frame = this.std(0x14181f, 0.45, 0.5);
    for (let i = 0; i < 5; i += 1) {
      const x = -420 + i * 210;
      this.add(this.R(164, 108, 6, frame, 2), x, 156, -254);
      this.add(this.B(148, 92, 2, new THREE.MeshBasicMaterial({ color: 0x16242f })), x, 156, -250.6);
      for (let k = 0; k < 22; k += 1) {
        const c = new THREE.Mesh(
          new THREE.PlaneGeometry(4, 5),
          new THREE.MeshBasicMaterial({
            color: new THREE.Color(Math.random() < 0.55 ? 0x5d82a6 : 0xb08f52).multiplyScalar(1.6),
          }),
        );
        this.add(c, x + (Math.random() - 0.5) * 130, 156 + (Math.random() - 0.5) * 74, -249.4);
      }
      this.add(this.R(158, 5, 5, this.std(0x39434f, 0.5, 0.3), 1.4), x, 100, -251);
    }
    this.add(this.sprite("V-SI", "VIRTUAL SI · AGENT FLOOR", "#eaf0f8", 50), 0, 238, -248).scale.set(210, 105, 1);

    for (const x of [-330, -110, 110, 330]) {
      this.add(
        this.R(162, 5, 46, new THREE.MeshBasicMaterial({ color: new THREE.Color(0xc9d8e8).multiplyScalar(0.8) }), 1.6),
        x, 228, -20,
      );
      this.add(this.R(172, 7, 54, this.std(0x2b333f, 0.5, 0.35), 2), x, 233, -20);
      const p = new THREE.PointLight(0xd9e6f4, 0.3, 540);
      this.add(p, x, 210, -20);
      this.ceilingLights.push(p);
    }
  }

  private buildDesks(): void {
    const YA = new THREE.Vector3(0, 1, 0);
    const back = new THREE.Vector3(0, 0, -44).applyAxisAngle(YA, ROT);
    const bp = new THREE.Vector3(0, 0, -86).applyAxisAngle(YA, ROT);
    const deskMat = this.std(0x3a4453, 0.32, 0.28);
    const legMat = this.std(0x20262f, 0.4, 0.55);

    SEATS.forEach((seat) => {
      // ── 책상 ──
      const g = new THREE.Group();
      const top = this.R(90, 5, 56, deskMat, 2.2);
      top.position.y = 30;
      top.castShadow = top.receiveShadow = true;
      g.add(top);
      for (const [a, b] of [[-40, -23], [40, -23], [-40, 23], [40, 23]]) {
        const l = this.R(5, 30, 5, legMat, 1.6);
        l.position.set(a, 15, b);
        l.castShadow = true;
        g.add(l);
      }
      const MX = 17; // 모니터 가로 오프셋 — 캐릭터 얼굴을 가리지 않게.
      g.add(this.at(this.R(8, 12, 8, legMat, 2), MX, 38, 16));
      const bez = this.R(38, 25, 4, this.std(0x15191f, 0.35, 0.5), 1.8);
      bez.position.set(MX, 53, 16);
      bez.rotation.y = -0.7;
      bez.castShadow = true;
      g.add(bez);
      const scr = new THREE.Mesh(
        new THREE.PlaneGeometry(32, 19),
        new THREE.MeshStandardMaterial({
          color: 0x0c1512, emissive: 0x1b3f39, emissiveIntensity: 0.42, roughness: 0.22, metalness: 0,
        }),
      );
      scr.position.set(MX - 1.3, 53, 16 - 1.6);
      scr.rotation.y = Math.PI - 0.7;
      g.add(scr);
      this.screens.set(seat.id, scr);
      const spill = new THREE.PointLight(0x7fd9c0, 0.22, 140);
      spill.position.set(MX - 6, 50, 2);
      g.add(spill);
      this.spills.set(seat.id, spill);
      const kb = this.R(33, 2.6, 12, this.std(0x2a323d, 0.5, 0.2), 1);
      kb.position.set(4, 33.4, -9);
      kb.rotation.y = -0.18;
      kb.castShadow = true;
      g.add(kb);
      const mug = this.R(8, 10, 8, this.toy(0xb8564f), 2.2);
      mug.position.set(-28, 37, -8);
      mug.castShadow = true;
      g.add(mug);
      const st = this.R(13, 4, 17, this.std(0xd8dee6, 0.72, 0.02), 0.8);
      st.position.set(-33, 34.5, 4);
      st.rotation.y = 0.2;
      st.castShadow = true;
      g.add(st);
      // 의자 — 등받이는 사람 뒤(-z)
      const ch = new THREE.Group();
      ch.add(this.at(this.R(32, 5, 32, this.std(0x2e3742, 0.65, 0.1), 3), 0, 13, 0));
      ch.add(this.at(this.R(30, 34, 7, this.std(0x2e3742, 0.65, 0.1), 3), 0, 32, -15));
      ch.add(this.at(this.R(7, 11, 7, legMat, 2), 0, 6, 0));
      ch.add(this.at(this.R(36, 4, 36, legMat, 2), 0, 2, 0));
      ch.traverse((o) => {
        (o as THREE.Mesh).castShadow = true;
      });
      ch.position.set(0, 0, -44);
      g.add(ch);
      g.position.set(seat.x, 0, DZ);
      g.rotation.y = ROT;
      this.scene.add(g);

      // ── 캐릭터 ──
      const guy = this.makeGuy(seat.color);
      guy.group.position.set(seat.x + back.x, SEAT_Y, DZ + back.z);
      guy.group.rotation.y = ROT;
      guy.home = guy.group.position.clone();
      guy.rot = ROT;
      guy.desk = { x: seat.x, z: DZ };
      this.scene.add(guy.group);
      this.guys.set(seat.id, guy);

      // ── 손 (사람을 부를 때만 보인다) ──
      const hand = this.R(9, 22, 9, new THREE.MeshStandardMaterial({
        color: 0xd9a04f, emissive: 0x4a3a1d, emissiveIntensity: 1.4, roughness: 0.4,
      }), 3);
      hand.position.set(0, 46, 0);
      hand.visible = false;
      guy.group.add(hand);
      this.hands.set(seat.id, hand);

      // ── 천장 현황 보드 ──
      const board = new THREE.Group();
      board.add(this.at(this.R(118 + 9, 53 + 9, 5, this.std(0x1a2029, 0.35, 0.5), 2.2), 0, 0, 0));
      const canvas = document.createElement("canvas");
      canvas.width = 512;
      canvas.height = 230;
      this.drawBoard(canvas, seat, "차례 기다림", "#7f8b9c");
      const tex = new THREE.CanvasTexture(canvas);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
      this.disposables.push(tex);
      const panel = new THREE.Mesh(
        new THREE.PlaneGeometry(118, 53),
        new THREE.MeshStandardMaterial({
          map: tex, emissiveMap: tex, emissive: 0xffffff, emissiveIntensity: 1.05,
          roughness: 0.2, metalness: 0,
        }),
      );
      board.add(this.at(panel, 0, 0, 2.9));
      this.boards.set(seat.id, { canvas, tex, seat });
      for (const dx of [-38, 38]) board.add(this.at(this.R(2.5, 70, 2.5, legMat, 0.8), dx, 53 / 2 + 35, -1));
      board.position.set(seat.x, 148, 38);
      board.rotation.x = 0.12;
      this.scene.add(board);

      // ── 파티션 ──
      const p = this.R(98, 32, 5, this.std(0x232a36, 0.75, 0.08), 2.2);
      p.castShadow = true;
      p.rotation.y = ROT;
      this.add(p, seat.x + bp.x, 16, DZ + bp.z);
      const cp = this.R(98, 3, 7, this.std(0x3a4453, 0.45, 0.35), 1.4);
      cp.rotation.y = ROT;
      this.add(cp, seat.x + bp.x, 33, DZ + bp.z);
    });
  }

  private makeGuy(hex: number): Guy {
    const group = new THREE.Group();
    const mat = this.toy(hex);
    const body = this.R(34, 25, 23, mat, 5.5);
    body.position.y = 26;
    body.castShadow = true;
    group.add(body);
    const em = new THREE.MeshPhysicalMaterial({
      color: 0x17110e, roughness: 0.22, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.08,
    });
    for (const dx of [-8.6, 8.6]) {
      const eye = this.R(5, 9.5, 2.2, em, 1.1);
      eye.position.set(dx, 28, 11.7);
      group.add(eye);
    }
    const arms: THREE.Mesh[] = [];
    for (const dx of [-19.5, 19.5]) {
      const a = this.R(6, 12, 10, mat, 2.4);
      a.position.set(dx, 27, 0);
      a.castShadow = true;
      group.add(a);
      arms.push(a);
    }
    const legs: THREE.Group[] = [];
    for (const [dx, dz] of [[-11, 7], [-11, -7], [11, 7], [11, -7]]) {
      const pivot = new THREE.Group();
      pivot.position.set(dx, 14.5, dz);
      const l = this.R(7, 15, 7, mat, 2.4);
      l.position.y = -7.5;
      l.castShadow = true;
      pivot.add(l);
      group.add(pivot);
      legs.push(pivot);
    }
    group.scale.setScalar(1.15);
    return {
      group, body, arms, legs,
      home: new THREE.Vector3(), rot: 0, desk: { x: 0, z: 0 }, t: Math.random() * 9,
    };
  }

  private buildProps(): void {
    const legMat = this.std(0x20262f, 0.4, 0.55);
    // 화이트보드
    const wb = new THREE.Group();
    wb.add(this.at(this.R(204, 124, 7, new THREE.MeshPhysicalMaterial({
      color: 0xeef2f7, roughness: 0.12, metalness: 0.02, clearcoat: 1, clearcoatRoughness: 0.05,
    }), 2.5), 0, 92, 0));
    wb.add(this.at(this.R(204, 7, 8, this.std(0x3f4a59, 0.45, 0.4), 2), 0, 28, 0));
    for (const dx of [-90, 90]) wb.add(this.at(this.R(7, 30, 28, this.std(0x3f4a59, 0.45, 0.4), 2), dx, 15, 0));
    for (let i = 0; i < 7; i += 1) {
      wb.add(this.at(new THREE.Mesh(
        new THREE.PlaneGeometry(30 + Math.random() * 100, 3),
        new THREE.MeshBasicMaterial({ color: 0x4d5e79 }),
      ), -50 + Math.random() * 60, 130 - i * 14, 3.7));
    }
    wb.scale.setScalar(0.72);
    wb.position.set(-548, 0, -205);
    wb.rotation.y = Math.PI * 0.4;
    wb.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).castShadow = true;
    });
    this.scene.add(wb);

    // 회의 테이블
    const deskMat = this.std(0x3a4453, 0.32, 0.28);
    const mt = this.R(174, 6, 100, deskMat, 2.4);
    mt.castShadow = mt.receiveShadow = true;
    this.add(mt, -390, 34, 225);
    for (const [a, b] of [[-72, -40], [72, -40], [-72, 40], [72, 40]]) {
      this.add(this.R(6, 34, 6, legMat, 2), -390 + a, 17, 225 + b).castShadow = true;
    }
    for (const [a, b] of [[-54, -74], [16, -74], [-54, 74], [16, 74]]) {
      this.add(this.R(32, 6, 32, this.std(0x3f4a59, 0.6, 0.12), 3), -390 + a, 24, 225 + b).castShadow = true;
      this.add(this.R(32, 34, 6, this.std(0x3f4a59, 0.6, 0.12), 3), -390 + a, 42, 225 + b - 15).castShadow = true;
    }
    this.add(this.sprite("회의실 A", "MEETING", "#eaf0f8", 44), -390, 88, 225).scale.set(96, 48, 1);

    // 서버랙
    this.add(this.R(74, 164, 56, this.std(0x181d25, 0.35, 0.6), 3), 470, 82, -160).castShadow = true;
    for (let i = 0; i < 10; i += 1) {
      this.add(this.R(60, 10, 3, this.std(0x272f3a, 0.4, 0.5), 1), 470, 148 - i * 14, -133);
      for (let k = 0; k < 4; k += 1) {
        this.leds.push(this.add(new THREE.Mesh(
          new THREE.BoxGeometry(3, 3, 1.4),
          new THREE.MeshBasicMaterial({ color: 0x2f6b4d }),
        ), 448 + k * 14, 148 - i * 14, -131));
      }
    }
    this.add(this.sprite("SERVER", "RACK-01", "#9fd7c0", 38), 470, 194, -132).scale.set(104, 52, 1);

    // 화분 · 정수기
    const plant = (x: number, z: number) => {
      const g = new THREE.Group();
      const pot = this.R(24, 22, 24, this.std(0x7a5342, 0.72, 0.05), 4);
      pot.position.y = 11;
      pot.castShadow = true;
      g.add(pot);
      for (let i = 0; i < 7; i += 1) {
        const lf = this.R(6, 34 + Math.random() * 18, 6, this.toy(0x3f7f50), 2.5);
        lf.position.set((Math.random() - 0.5) * 12, 30 + Math.random() * 8, (Math.random() - 0.5) * 12);
        lf.rotation.z = (Math.random() - 0.5) * 0.85;
        lf.rotation.x = (Math.random() - 0.5) * 0.5;
        lf.castShadow = true;
        g.add(lf);
      }
      g.position.set(x, 0, z);
      this.scene.add(g);
    };
    plant(-548, 120);
    plant(548, 150);
    plant(-10, -228);
    plant(190, -228);
    this.add(this.R(28, 38, 28, this.std(0x3f4a59, 0.5, 0.25), 3), 540, 19, -40).castShadow = true;
    this.add(this.R(20, 34, 20, new THREE.MeshPhysicalMaterial({
      color: 0x8fc4e6, roughness: 0.05, metalness: 0, transmission: 0.75, thickness: 8, clearcoat: 1,
    }), 3), 540, 54, -40).castShadow = true;
  }

  private buildPapers(): void {
    const mk = (bad: boolean) => {
      const p = this.R(18, 3, 23, this.std(bad ? 0xe0625c : 0xf2f5f8, 0.55, 0.02), 1);
      p.castShadow = true;
      p.visible = false;
      this.scene.add(p);
      return p;
    };
    this.paperPass = mk(false);
    this.paperReject = mk(true);
  }

  /** 천장 현황 보드의 그림을 캔버스에 그린다.
   *
   * 스프라이트가 아니라 **평면 메시**에 입힌다: 스프라이트는 카메라를 향해
   * 돌지만 해상도를 키워도 이 거리에서 글자가 뭉갰다(실측). 평면이면 보드
   * 기울기(rotation.x)도 그대로 먹는다. */
  private drawBoard(c: HTMLCanvasElement, seat: Seat, status: string, tone: string): void {
    const g = c.getContext("2d")!;
    g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = "#0b1118";
    g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = `#${seat.color.toString(16).padStart(6, "0")}`;
    g.fillRect(0, 0, c.width, 8);
    g.textAlign = "left";
    g.fillStyle = "#eef3fa";
    g.font = "700 62px ui-sans-serif,system-ui,sans-serif";
    g.fillText(seat.label, 30, 86);
    g.fillStyle = "rgba(176,194,220,.5)";
    g.font = "400 30px ui-sans-serif,system-ui,sans-serif";
    g.fillText(`${seat.step}번째 · 전체 4단계`, 32, 130);
    g.fillStyle = "rgba(255,255,255,.08)";
    g.fillRect(30, 148, c.width - 60, 2);
    g.fillStyle = tone;
    g.font = "700 34px ui-sans-serif,system-ui,sans-serif";
    g.fillText(status, 32, 196);
  }

  private boardStatus(motion: AgentMotion, tone: HandUp["tone"] | undefined): [string, string] {
    switch (motion) {
      case "typing":
        return ["하는 중", "#6fe0c0"];
      case "slumped":
        return ["멈췄습니다", "#f0a0a0"];
      case "hand-up":
        return tone === "warn" ? ["확인해 주세요", "#e8c07a"] : ["도와주세요", "#f0a0a0"];
      default:
        return ["차례 기다림", "#7f8b9c"];
    }
  }

  private sprite(main: string, sub: string, col: string, fs: number): THREE.Sprite {
    const c = document.createElement("canvas");
    c.width = 256;
    c.height = 128;
    const g = c.getContext("2d")!;
    g.textAlign = "center";
    g.fillStyle = col;
    g.font = `700 ${fs}px ui-sans-serif,system-ui,sans-serif`;
    g.fillText(main, 128, sub ? 56 : 80);
    if (sub) {
      g.fillStyle = "rgba(176,194,220,.55)";
      g.font = "400 21px ui-monospace,monospace";
      g.fillText(sub, 128, 88);
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    this.disposables.push(t);
    return new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false }));
  }

  // ──────────────────────────────────────────────────────── 움직임

  private startTrip(from: AgentId, to: AgentId, reject: boolean): void {
    const guy = this.guys.get(from);
    const target = this.guys.get(to);
    if (!guy || !target) return;
    const a = guy.home;
    const h = target.home;
    const side = a.x > h.x ? 1 : -1; // 온 방향 쪽에 선다 — 책상을 지나치지 않게.
    const stop = h.x + 96 * side;
    const paper = reject ? this.paperReject : this.paperPass;
    paper.visible = true;
    this.trip = {
      guy, to, paper, idx: 0, t: 0, phase: "go", hold: 0,
      pts: [
        new THREE.Vector3(a.x, 0, a.z),
        new THREE.Vector3(a.x, 0, LANE),
        new THREE.Vector3(stop, 0, LANE),
        new THREE.Vector3(stop, 0, h.z + 32),
      ],
    };
  }

  private step(dt: number): void {
    const state = this.state;

    // 캐릭터 애니메이션
    for (const seat of SEATS) {
      const guy = this.guys.get(seat.id);
      if (!guy) continue;
      guy.t += dt;
      const walking = this.trip?.guy === guy && this.trip.hold <= 0;
      const motion = state?.motions[seat.id] ?? "idle";
      const seated = walking ? 0 : SEAT_Y;
      guy.group.position.y += (seated - guy.group.position.y) * Math.min(1, dt * 8);

      if (walking) {
        guy.legs.forEach((p, i) => {
          p.rotation.x = Math.sin(guy.t * 13 + (i % 2) * Math.PI) * 0.5;
        });
        guy.body.position.y = 26 + Math.abs(Math.sin(guy.t * 13)) * 1.7;
        guy.arms.forEach((a) => { a.rotation.x = 0; });
      } else {
        guy.legs.forEach((p, i) => {
          const t = i < 2 ? -0.3 : -1.25;
          p.rotation.x += (t - p.rotation.x) * Math.min(1, dt * 8);
        });
        if (motion === "typing") {
          guy.body.position.y = 26 + Math.sin(guy.t * 18) * 0.6;
          guy.arms.forEach((a, i) => { a.rotation.x = Math.sin(guy.t * 22 + i * Math.PI) * 0.45; });
        } else if (motion === "slumped") {
          guy.body.position.y = 22;
          guy.body.rotation.x = 0.35;
          guy.arms.forEach((a) => { a.rotation.x = 0; });
        } else if (motion === "hand-up") {
          guy.body.position.y = 26 + Math.sin(guy.t * 3) * 0.7;
          guy.body.rotation.x = 0;
          guy.arms.forEach((a, i) => { a.rotation.x = i === 1 ? -2.2 : 0; });
        } else {
          guy.body.position.y = 26 + Math.sin(guy.t * 1.7) * 0.5;
          guy.body.rotation.x = 0;
          guy.arms.forEach((a) => { a.rotation.x = 0; });
        }
      }
    }

    // 모니터 · 조명
    for (const seat of SEATS) {
      const scr = this.screens.get(seat.id);
      const spill = this.spills.get(seat.id);
      const on = state?.monitors[seat.id] ?? false;
      if (scr) {
        const m = scr.material as THREE.MeshStandardMaterial;
        m.emissive.setHex(on ? 0x3f9d8b : 0x1b3f39);
        m.emissiveIntensity = on ? 1.35 : 0.42;
      }
      if (spill) spill.intensity = on ? 1.2 : 0.22;
    }
    const mood = state?.moodLight ?? "normal";
    for (const p of this.ceilingLights) {
      p.color.setHex(mood === "accepted" ? 0xcdf0dd : mood === "stopped" ? 0xf0dfc6 : 0xd9e6f4);
    }

    // 서버랙 LED
    this.blink += dt;
    if (this.blink > 0.28) {
      this.blink = 0;
      for (const l of this.leds) {
        (l.material as THREE.MeshBasicMaterial).color.setHex(Math.random() < 0.78 ? 0x2f6b4d : 0x63d99b);
      }
    }

    this.stepTrip(dt);
  }

  private stepTrip(dt: number): void {
    const trip = this.trip;
    if (!trip) return;
    const G = trip.guy.group;

    if (trip.hold > 0) {
      trip.hold -= dt;
      if (trip.hold <= 0) {
        trip.phase = "back";
        trip.pts = trip.pts.slice().reverse();
        trip.idx = 0;
        trip.t = 0;
      }
      return;
    }

    const a = trip.pts[trip.idx];
    const b = trip.pts[trip.idx + 1];
    const len = Math.max(1, Math.hypot(b.x - a.x, b.z - a.z));
    trip.t += (dt * 130) / len;
    const k = Math.min(1, trip.t);
    G.position.x = a.x + (b.x - a.x) * k;
    G.position.z = a.z + (b.z - a.z) * k;

    const want = Math.atan2(b.x - a.x, b.z - a.z);
    let d = want - G.rotation.y;
    while (d > Math.PI) d -= Math.PI * 2;
    while (d < -Math.PI) d += Math.PI * 2;
    G.rotation.y += d * Math.min(1, dt * 7);

    if (trip.phase === "go") {
      // 갈 때만 들고 간다 — 놓고 온 서류는 그대로 둔다.
      const fx = Math.sin(G.rotation.y);
      const fz = Math.cos(G.rotation.y);
      const rx = Math.cos(G.rotation.y);
      const rz = -Math.sin(G.rotation.y);
      trip.paper.position.set(G.position.x + fx * 13 + rx * 15, 30, G.position.z + fz * 13 + rz * 15);
      trip.paper.rotation.set(-1.05, G.rotation.y, 0);
    }

    if (k < 1) return;
    trip.idx += 1;
    trip.t = 0;
    if (trip.idx < trip.pts.length - 1) return;

    if (trip.phase === "go") {
      const target = this.guys.get(trip.to);
      if (target) {
        const ox = trip.paper === this.paperReject ? -16 : 10;
        trip.paper.position.set(target.desk.x + ox, 34.5, target.desk.z + 22);
        trip.paper.rotation.set(0, ROT, 0);
      }
      trip.hold = 0.7;
    } else {
      G.rotation.y = trip.guy.rot;
      G.position.copy(trip.guy.home);
      this.trip = null;
    }
  }

  /** 포인터 아래에 있는 캐릭터를 찾는다. 없으면 null. */
  private pick(e: PointerEvent): AgentId | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    let best: { agent: AgentId; dist: number } | null = null;
    for (const [agent, guy] of this.guys) {
      const hits = this.raycaster.intersectObject(guy.group, true);
      if (hits.length && (!best || hits[0].distance < best.dist)) {
        best = { agent, dist: hits[0].distance };
      }
    }
    return best?.agent ?? null;
  }

  private place(): void {
    this.camera.position.set(Math.sin(this.ang) * this.dist, this.hi, Math.cos(this.ang) * this.dist);
    this.camera.lookAt(0, this.ty, 0);
  }

  private resize(): void {
    const w = this.host.clientWidth || 1;
    const h = this.host.clientHeight || 1;
    // `updateStyle` 을 끄면 안 된다. 끄면 three 가 canvas 의 width/height
    // **속성**만 바꾸고 CSS 크기는 그대로 두는데, pixelRatio 가 2 이면 버퍼가
    // 2배라 캔버스가 컨테이너의 2배 크기로 표시된다(실측: 천장만 화면에 꽉 찼다).
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
}
