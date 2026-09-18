// عارض ثلاثي الأبعاد كامل (WebGL2) لخريطة الحملة:
// أرضية مجسّمة من حقل الارتفاع، جنود instanced، قلاع وثكنات إجرائية،
// شمس تتبع ساعة اللعبة، دماء وندوب على الأرض، وواجهة لمس (سحب/تدوير/قرص).
// كل الأشكال مولّدة بالكود — لا ملفات نماذج، لا أصول خارجية.

import type { Game } from '../sim/game';
import type { TerrainType } from '../sim/types';
import { nightAmount, skyTint } from './daylight';
import { battleScars, buildWorldFX } from './worldFX';

const EXAG = 4.6; // مبالغة الارتفاع لإبراز الجبال
const SOLDIER_BUDGET = 600; // جودة متوسطة ثابتة
const TEX_PX = 4; // بكسلات نسيج الأرض لكل خلية

const TERRAIN_BASE: Record<TerrainType, [number, number, number]> = {
  plains: [196, 178, 122],
  forest: [82, 112, 64],
  hills: [170, 138, 92],
  mountains: [148, 141, 128],
  desert: [224, 198, 140],
  water: [46, 90, 122],
};

function hsh(n: number): number {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

// ==================== رياضيات (مصفوفات عمود-major) ====================

export type M4 = Float32Array;

export function mat4Mul(a: M4, b: M4): M4 {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] =
        a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

export function mat4Persp(fovy: number, aspect: number, near: number, far: number): M4 {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  const o = new Float32Array(16);
  o[0] = f / aspect;
  o[5] = f;
  o[10] = (far + near) * nf;
  o[11] = -1;
  o[14] = 2 * far * near * nf;
  return o;
}

export function mat4LookAt(eye: number[], tgt: number[]): M4 {
  let fx = tgt[0] - eye[0];
  let fy = tgt[1] - eye[1];
  let fz = tgt[2] - eye[2];
  let l = Math.hypot(fx, fy, fz) || 1;
  fx /= l; fy /= l; fz /= l;
  // right = fwd × worldUp(0,0,1)
  let sx = fy;
  let sy = -fx;
  let sz = 0;
  l = Math.hypot(sx, sy, sz) || 1;
  sx /= l; sy /= l;
  const ux = sy * fz;
  const uy = -sx * fz;
  const uz = sx * fy - sy * fx;
  const o = new Float32Array(16);
  o[0] = sx; o[1] = ux; o[2] = -fx; o[3] = 0;
  o[4] = sy; o[5] = uy; o[6] = -fy; o[7] = 0;
  o[8] = 0; o[9] = uz; o[10] = -fz; o[11] = 0;
  o[12] = -(sx * eye[0] + sy * eye[1]);
  o[13] = -(ux * eye[0] + uy * eye[1] + uz * eye[2]);
  o[14] = fx * eye[0] + fy * eye[1] + fz * eye[2];
  o[15] = 1;
  return o;
}

export function cameraBasis(eye: number[], tgt: number[]) {
  let f0 = tgt[0] - eye[0];
  let f1 = tgt[1] - eye[1];
  let f2 = tgt[2] - eye[2];
  const l = Math.hypot(f0, f1, f2) || 1;
  f0 /= l; f1 /= l; f2 /= l;
  let r0 = f1;
  let r1 = -f0;
  const rl = Math.hypot(r0, r1) || 1;
  r0 /= rl; r1 /= rl;
  const u0 = r1 * f2;
  const u1 = -r0 * f2;
  const u2 = r0 * f1 - r1 * f0;
  return { fwd: [f0, f1, f2], right: [r0, r1, 0], up: [u0, u1, u2] };
}

export function projectPoint(
  mvp: M4, x: number, y: number, z: number, cw: number, ch: number,
): { x: number; y: number; depth: number } | null {
  const cx = mvp[0] * x + mvp[4] * y + mvp[8] * z + mvp[12];
  const cy = mvp[1] * x + mvp[5] * y + mvp[9] * z + mvp[13];
  const w = mvp[3] * x + mvp[7] * y + mvp[11] * z + mvp[15];
  if (w <= 0.001) return null;
  const ndx = cx / w;
  const ndy = cy / w;
  if (ndx < -1.12 || ndx > 1.12 || ndy < -1.12 || ndy > 1.12) return null;
  return { x: (ndx * 0.5 + 0.5) * cw, y: (1 - (ndy * 0.5 + 0.5)) * ch, depth: w };
}

// ==================== حقل الارتفاع ====================

export function nodeWaterFlag(W: number, H: number, terrain: TerrainType[]): Uint8Array {
  const nw = new Uint8Array((W + 1) * (H + 1));
  for (let gy = 0; gy <= H; gy++) {
    for (let gx = 0; gx <= W; gx++) {
      let water = 1;
      let any = 0;
      for (let dy = -1; dy <= 0; dy++) {
        for (let dx = -1; dx <= 0; dx++) {
          const x = gx + dx;
          const y = gy + dy;
          if (x < 0 || y < 0 || x >= W || y >= H) continue;
          any = 1;
          if (terrain[y * W + x] !== 'water') water = 0;
        }
      }
      nw[gy * (W + 1) + gx] = any && water ? 1 : 0;
    }
  }
  return nw;
}

export function nodeHeights(W: number, H: number, elev: Float32Array, water: Uint8Array): Float32Array {
  const NW = W + 1;
  const hs = new Float32Array(NW * (H + 1));
  for (let gy = 0; gy <= H; gy++) {
    for (let gx = 0; gx <= W; gx++) {
      let sum = 0;
      let n = 0;
      for (let dy = -1; dy <= 0; dy++) {
        for (let dx = -1; dx <= 0; dx++) {
          const x = gx + dx;
          const y = gy + dy;
          if (x < 0 || y < 0 || x >= W || y >= H) continue;
          sum += elev[y * W + x];
          n++;
        }
      }
      const i = gy * NW + gx;
      hs[i] = water[i] ? -0.1 : Math.max(0, sum / (n || 1) - 0.02) * EXAG + 0.04;
    }
  }
  return hs;
}

export function makeHeightAt(W: number, H: number, hs: Float32Array): (x: number, y: number) => number {
  const NW = W + 1;
  return (x, y) => {
    const fx = Math.max(0, Math.min(W - 0.0001, x));
    const fy = Math.max(0, Math.min(H - 0.0001, y));
    const gx = Math.floor(fx);
    const gy = Math.floor(fy);
    const tx = fx - gx;
    const ty = fy - gy;
    const a = hs[gy * NW + gx];
    const b = hs[gy * NW + gx + 1];
    const c = hs[(gy + 1) * NW + gx];
    const d = hs[(gy + 1) * NW + gx + 1];
    return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
  };
}

export function rayCellPick(
  eye: number[],
  dir: number[],
  heightAt: (x: number, y: number) => number,
  W: number,
  H: number,
): { x: number; y: number } | null {
  let prev = 0;
  const maxT = 380;
  const N = 72;
  for (let i = 1; i <= N; i++) {
    const t = (i / N) * maxT;
    const px = eye[0] + dir[0] * t;
    const py = eye[1] + dir[1] * t;
    const pz = eye[2] + dir[2] * t;
    const inside = px >= 0 && py >= 0 && px < W && py < H;
    if (inside && pz - heightAt(px, py) <= 0) {
      let a = prev;
      let b = t;
      for (let k = 0; k < 18; k++) {
        const m = (a + b) / 2;
        const mx = eye[0] + dir[0] * m;
        const my = eye[1] + dir[1] * m;
        const mz = eye[2] + dir[2] * m;
        if (mz - heightAt(mx, my) <= 0) b = m;
        else a = m;
      }
      const cx = Math.floor(eye[0] + dir[0] * b);
      const cy = Math.floor(eye[1] + dir[1] * b);
      if (cx < 0 || cy < 0 || cx >= W || cy >= H) return null;
      return { x: cx, y: cy };
    }
    prev = t;
  }
  return null;
}

// ==================== هندسة ====================

export function boxGeo(): Float32Array {
  // مكعب وحدة: x,y ∈ [-.5,.5]، z ∈ [0,1] — وجوه CCW للخارج مع normals
  const V: [number, number, number][] = [
    [-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0],
    [-0.5, -0.5, 1], [0.5, -0.5, 1], [0.5, 0.5, 1], [-0.5, 0.5, 1],
  ];
  const F: [number[], number[]][] = [
    [[4, 5, 6, 4, 6, 7], [0, 0, 1]],
    [[0, 3, 2, 0, 2, 1], [0, 0, -1]],
    [[1, 2, 6, 1, 6, 5], [1, 0, 0]],
    [[0, 4, 7, 0, 7, 3], [-1, 0, 0]],
    [[3, 7, 6, 3, 6, 2], [0, 1, 0]],
    [[0, 1, 5, 0, 5, 4], [0, -1, 0]],
  ];
  const out: number[] = [];
  for (const [tri, n] of F) {
    for (const vi of tri) {
      out.push(V[vi][0], V[vi][1], V[vi][2], n[0], n[1], n[2]);
    }
  }
  return new Float32Array(out);
}

export function soldierGeo(): Float32Array {
  const unit = boxGeo();
  const parts: [number[], number[]][] = [
    [[0, 0, 0.22], [0.3, 0.26, 0.44]], // الساقان/الجذع الأسفل
    [[0, 0.03, 0.62], [0.5, 0.36, 0.5]], // الجسم
    [[0, 0, 0.95], [0.24, 0.24, 0.22]], // الرأس
    [[0.3, 0.06, 0.75], [0.07, 0.07, 1.1]], // الرمح
  ];
  const out = new Float32Array((unit.length / 6) * parts.length * 6);
  let o = 0;
  for (const [c, sz] of parts) {
    for (let i = 0; i < unit.length; i += 6) {
      out[o++] = unit[i] * sz[0] + c[0];
      out[o++] = unit[i + 1] * sz[1] + c[1];
      out[o++] = unit[i + 2] * sz[2] + c[2];
      out[o++] = unit[i + 3];
      out[o++] = unit[i + 4];
      out[o++] = unit[i + 5];
    }
  }
  return out;
}

export interface SolidInst {
  x: number; y: number; z: number; yaw: number;
  sx: number; sy: number; sz: number;
  r: number; g: number; b: number;
  kind: number; // 0 صندوق، 1 هرم سقف، 2 علم، 3 جندي ثابت، 4 جندي زاحف
  phase: number;
}

export function packSolids(list: SolidInst[]): Float32Array {
  const o = new Float32Array(list.length * 12);
  for (let n = 0; n < list.length; n++) {
    const it = list[n];
    const b = n * 12;
    o[b] = it.x; o[b + 1] = it.y; o[b + 2] = it.z; o[b + 3] = it.yaw;
    o[b + 4] = it.sx; o[b + 5] = it.sy; o[b + 6] = it.sz; o[b + 7] = it.kind;
    o[b + 8] = it.r; o[b + 9] = it.g; o[b + 10] = it.b; o[b + 11] = it.phase;
  }
  return o;
}

export function squadSize(soldiers: number, cap: number): number {
  return Math.max(4, Math.min(cap, Math.round(soldiers / 110)));
}

// ==================== GLSL ====================

const SH_LIGHT = `
uniform vec3 uSun; uniform vec3 uAmb; uniform vec3 uLightCol; uniform float uLightMul; uniform vec3 uFog;
`;

const VS_TERRAIN = `
attribute vec3 aPos; attribute vec3 aNrm; attribute vec2 aUV; attribute float aWater;
uniform mat4 uMVP; uniform vec3 uEye;
varying vec3 vN; varying vec2 vUV; varying float vDist; varying float vWater;
void main(){
  gl_Position = uMVP * vec4(aPos, 1.0);
  vN = aNrm; vUV = aUV; vWater = aWater;
  vDist = length(aPos - uEye);
}`;

const FS_TERRAIN = `
precision mediump float;
varying vec3 vN; varying vec2 vUV; varying float vDist; varying float vWater;
uniform sampler2D uTex; uniform float uTime;
${SH_LIGHT}
void main(){
  vec3 albedo = texture2D(uTex, vUV).rgb;
  float diff = max(dot(normalize(vN), uSun), 0.0);
  vec3 col = albedo * (uAmb + uLightCol * diff * uLightMul);
  if (vWater > 0.5) {
    float sp = sin(uTime * 1.7 + vUV.x * 150.0) * sin(uTime * 2.3 + vUV.y * 120.0);
    col += vec3(0.04, 0.08, 0.11) * max(0.0, sp) * (0.35 + uLightMul * 0.75);
  }
  float f = clamp((vDist - 36.0) / 95.0, 0.0, 1.0) * 0.66;
  col = mix(col, uFog, f);
  gl_FragColor = vec4(col, 1.0);
}`;

const VS_SOLID = `
attribute vec3 aP; attribute vec3 aN;
attribute vec4 aI0; // pos.xyz yaw
attribute vec4 aI1; // scale.xyz kind
attribute vec4 aI2; // rgb phase
uniform mat4 uMVP; uniform vec3 uEye; uniform float uTime;
varying vec3 vC; varying vec3 vN; varying float vDist;
void main(){
  float kind = aI1.w;
  vec3 lp = aP * aI1.xyz;
  if (kind > 0.5 && kind < 1.5) {
    float t = clamp(aP.z, 0.0, 1.0);
    lp.x = mix(lp.x, 0.0, t * 0.88);
    lp.y = mix(lp.y, 0.0, t * 0.88);
  }
  float c = cos(aI0.w), s = sin(aI0.w);
  vec3 rp = vec3(lp.x * c - lp.y * s, lp.x * s + lp.y * c, lp.z);
  if (kind > 1.5 && kind < 2.5) {
    float amt = clamp(aP.x + 0.5, 0.0, 1.0);
    rp.y += sin(uTime * 4.5 + aI2.w * 6.283 + aP.z * 5.0) * 0.25 * amt * aI1.x;
  }
  if (kind > 2.5) {
    float mv = step(3.5, kind);
    rp.z += abs(sin(uTime * 5.5 + aI2.w * 6.283 + rp.x * 2.0)) * 0.05 * mv;
    rp.x += sin(uTime * 3.0 + aI2.w * 12.0) * 0.012 * mv;
  }
  vec3 wp = rp + aI0.xyz;
  gl_Position = uMVP * vec4(wp, 1.0);
  vN = vec3(aN.x * c - aN.y * s, aN.x * s + aN.y * c, aN.z);
  vC = aI2.rgb;
  vDist = length(wp - uEye);
}`;

const FS_SOLID = `
precision mediump float;
varying vec3 vC; varying vec3 vN; varying float vDist;
${SH_LIGHT}
void main(){
  float diff = max(dot(normalize(vN), uSun), 0.0);
  vec3 col = vC * (uAmb + uLightCol * diff * uLightMul);
  float f = clamp((vDist - 36.0) / 95.0, 0.0, 1.0) * 0.66;
  col = mix(col, uFog, f);
  gl_FragColor = vec4(col, 1.0);
}`;

const VS_DECAL = `
attribute vec2 aQ;
attribute vec4 aD0; // x y z rot
attribute vec4 aD1; // lenX lenY r g
attribute vec4 aD2; // b a kind phase
uniform mat4 uMVP;
varying vec2 vQ; varying vec3 vC; varying float vA; varying float vKind;
void main(){
  float c = cos(aD0.w), s = sin(aD0.w);
  vec2 p = aQ * (aD1.xy * 0.5);
  vec2 r = vec2(p.x * c - p.y * s, p.x * s + p.y * c);
  gl_Position = uMVP * vec4(aD0.xy + r, aD0.z, 1.0);
  vQ = aQ;
  vC = vec3(aD1.z, aD1.w, aD2.x);
  vA = aD2.y;
  vKind = aD2.z;
}`;

const FS_DECAL = `
precision mediump float;
varying vec2 vQ; varying vec3 vC; varying float vA; varying float vKind;
void main(){
  float d = length(vQ);
  float a;
  if (vKind < 0.5) a = smoothstep(1.0, 0.76, d) * smoothstep(0.5, 0.72, d);
  else if (vKind < 1.5) a = smoothstep(1.0, 0.05, d) * 0.9;
  else a = 1.0;
  gl_FragColor = vec4(vC, a * vA);
}`;

const VS_BILL = `
attribute vec2 aQ;
attribute vec4 aB0; // x y z size
attribute vec4 aB1; // r g b a
attribute vec4 aB2; // kind flick _ _
uniform mat4 uMVP; uniform vec3 uRight; uniform vec3 uUp;
varying vec2 vQ; varying vec4 vCol; varying float vKind;
void main(){
  vec3 wp = aB0.xyz + uRight * (aQ.x * aB0.w) + uUp * (aQ.y * aB0.w);
  gl_Position = uMVP * vec4(wp, 1.0);
  vQ = aQ; vCol = aB1; vKind = aB2.x;
  vCol.a *= 0.72 + 0.28 * aB2.y;
}`;

const FS_BILL = `
precision mediump float;
varying vec2 vQ; varying vec4 vCol; varying float vKind;
void main(){
  float d = length(vQ);
  float a = smoothstep(1.0, 0.04, d);
  if (vKind > 0.5 && vKind < 1.5) a = smoothstep(1.0, 0.55, d);
  if (vKind > 1.5) a = smoothstep(0.6, 0.2, d);
  gl_FragColor = vec4(vCol.rgb, a * vCol.a);
}`;

const QUAD = new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]);

// ==================== العارض ====================

export class MapView3D {
  static supported(): boolean {
    try {
      const c = document.createElement('canvas');
      return !!c.getContext('webgl2');
    } catch {
      return false;
    }
  }

  onTap: ((cell: { x: number; y: number }) => void) | null = null;

  private gl: WebGL2RenderingContext;
  private canvas: HTMLCanvasElement;
  private labelC: HTMLCanvasElement;
  private labelCtx: CanvasRenderingContext2D;
  private W = 1;
  private H = 1;
  private cw = 1;
  private ch = 1;

  private heightAt: (x: number, y: number) => number = () => 0;
  private terrainBuf: WebGLBuffer | null = null;
  private terrainIdx: WebGLBuffer | null = null;
  private terrainCount = 0;
  private terrainVAO: WebGLVertexArrayObject | null = null;
  private cellRef: unknown = null;
  private fx: ReturnType<typeof buildWorldFX> | null = null;

  private tex: WebGLTexture | null = null;
  private texCanvas: HTMLCanvasElement;
  private texCtx: CanvasRenderingContext2D;
  private texSigNum = -1;

  private progT: WebGLProgram;
  private progS: WebGLProgram;
  private progD: WebGLProgram;
  private progB: WebGLProgram;
  private boxVAO: WebGLVertexArrayObject;
  private soldierVAO: WebGLVertexArrayObject;
  private decalVAO: WebGLVertexArrayObject;
  private decalInst: WebGLBuffer;
  private billVAO: WebGLVertexArrayObject;
  private billInst: WebGLBuffer;
  private solidInst: WebGLBuffer;

  private cam = { fx: 0, fy: 0, d: 56, yaw: Math.PI * 0.5, pitch: 0.8 };
  private simSpeed = 1;
  private nightK = 0;

  private pointers = new Map<number, { x: number; y: number }>();
  private gest = {
    mode: 'none' as 'none' | 'pan' | 'orbit' | 'pinch',
    sx: 0, sy: 0, dist: 0, mx: 0, my: 0, downT: 0,
  };

  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext('webgl2', { antialias: true, alpha: false });
    if (!gl) throw new Error('WebGL2 غير مدعوم');
    this.gl = gl;
    this.canvas = canvas;
    canvas.style.touchAction = 'none';
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.display = 'block';

    this.labelC = document.createElement('canvas');
    this.labelC.style.position = 'absolute';
    this.labelC.style.inset = '0';
    this.labelC.style.width = '100%';
    this.labelC.style.height = '100%';
    this.labelC.style.pointerEvents = 'none';
    const lc = this.labelC.getContext('2d');
    if (!lc) throw new Error('Canvas 2D غير مدعوم');
    this.labelCtx = lc;
    (canvas.parentElement ?? document.body).appendChild(this.labelC);

    const tc = document.createElement('canvas');
    this.texCanvas = tc;
    const tctx = tc.getContext('2d');
    if (!tctx) throw new Error('Canvas 2D غير مدعوم');
    this.texCtx = tctx;

    this.progT = this.link(VS_TERRAIN, FS_TERRAIN, ['aPos', 'aNrm', 'aUV', 'aWater']);
    this.progS = this.link(VS_SOLID, FS_SOLID, ['aP', 'aN', 'aI0', 'aI1', 'aI2']);
    this.progD = this.link(VS_DECAL, FS_DECAL, ['aQ', 'aD0', 'aD1', 'aD2']);
    this.progB = this.link(VS_BILL, FS_BILL, ['aQ', 'aB0', 'aB1', 'aB2']);

    this.solidInst = gl.createBuffer()!;
    this.boxVAO = this.mkSolidVAO(boxGeo());
    this.soldierVAO = this.mkSolidVAO(soldierGeo());
    const dv = this.mkQuadVAO();
    this.decalVAO = dv.vao;
    this.decalInst = dv.buf;
    const bv = this.mkQuadVAO();
    this.billVAO = bv.vao;
    this.billInst = bv.buf;

    this.attach();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement ?? canvas);
    this.resize();
  }

  setWorld(w: number, h: number): void {
    this.W = w;
    this.H = h;
    this.cellRef = null;
  }

  fit(): void {
    this.cam.d = this.W * 0.78;
    this.cam.fx = this.W / 2;
    this.cam.fy = this.H / 2;
  }

  centerOnCell(x: number, y: number): void {
    this.cam.fx = x;
    this.cam.fy = y;
  }

  setSpeed(v: number): void {
    this.simSpeed = v;
  }

  private resize(): void {
    const host = this.canvas.parentElement ?? this.canvas;
    const r = host.getBoundingClientRect();
    const dpr = Math.min(1.75, window.devicePixelRatio || 1);
    this.cw = Math.max(1, r.width);
    this.ch = Math.max(1, r.height);
    this.canvas.width = Math.max(1, Math.floor(this.cw * dpr));
    this.canvas.height = Math.max(1, Math.floor(this.ch * dpr));
    const ld = Math.min(2, window.devicePixelRatio || 1);
    this.labelC.width = Math.max(1, Math.floor(this.cw * ld));
    this.labelC.height = Math.max(1, Math.floor(this.ch * ld));
    this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  // ---------- أدوات GL ----------
  private compile(type: number, src: string): WebGLShader {
    const gl = this.gl;
    const sh = gl.createShader(type)!;
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh);
      gl.deleteShader(sh);
      throw new Error('shader: ' + log);
    }
    return sh;
  }

  private link(vs: string, fs: string, attribs: string[]): WebGLProgram {
    const gl = this.gl;
    const p = gl.createProgram()!;
    const v = this.compile(gl.VERTEX_SHADER, vs);
    const f = this.compile(gl.FRAGMENT_SHADER, fs);
    gl.attachShader(p, v);
    gl.attachShader(p, f);
    attribs.forEach((nm, i) => gl.bindAttribLocation(p, i, nm));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('link: ' + gl.getProgramInfoLog(p));
    return p;
  }

  private uCache = new WeakMap<WebGLProgram, Map<string, WebGLUniformLocation | null>>();
  private u(p: WebGLProgram, name: string): WebGLUniformLocation | null {
    let m = this.uCache.get(p);
    if (!m) {
      m = new Map();
      this.uCache.set(p, m);
    }
    let loc = m.get(name);
    if (loc === undefined) {
      loc = this.gl.getUniformLocation(p, name);
      m.set(name, loc);
    }
    return loc;
  }

  private mkSolidVAO(geo: Float32Array): WebGLVertexArrayObject {
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    const gb = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, gb);
    gl.bufferData(gl.ARRAY_BUFFER, geo, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.solidInst);
    for (const [idx, offset] of [[2, 0], [3, 16], [4, 32]] as const) {
      gl.enableVertexAttribArray(idx);
      gl.vertexAttribPointer(idx, 4, gl.FLOAT, false, 48, offset);
      gl.vertexAttribDivisor(idx, 1);
    }
    gl.bindVertexArray(null);
    return vao;
  }

  private mkQuadVAO(): { vao: WebGLVertexArrayObject; buf: WebGLBuffer } {
    const gl = this.gl;
    const vao = gl.createVertexArray()!;
    const buf = gl.createBuffer()!;
    gl.bindVertexArray(vao);
    const qb = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, qb);
    gl.bufferData(gl.ARRAY_BUFFER, QUAD, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    for (const [idx, offset] of [[1, 0], [2, 16], [3, 32]] as const) {
      gl.enableVertexAttribArray(idx);
      gl.vertexAttribPointer(idx, 4, gl.FLOAT, false, 48, offset);
      gl.vertexAttribDivisor(idx, 1);
    }
    gl.bindVertexArray(null);
    return { vao, buf };
  }

  // ---------- تحكم ----------
  private attach(): void {
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        this.gest = {
          mode: 'pinch', sx: 0, sy: 0,
          dist: Math.hypot(b.x - a.x, b.y - a.y) || 1,
          mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2,
          downT: performance.now(),
        };
      } else {
        this.gest.mode = e.shiftKey || e.button === 2 ? 'orbit' : 'pan';
        this.gest.sx = e.clientX;
        this.gest.sy = e.clientY;
        this.gest.downT = performance.now();
        c.setPointerCapture(e.pointerId);
      }
    });
    c.addEventListener('pointermove', (e) => {
      const prev = this.pointers.get(e.pointerId);
      if (!prev) return;
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.gest.mode === 'pinch' && this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        this.cam.d = Math.max(9, Math.min(150, (this.cam.d * this.gest.dist) / dist));
        this.cam.yaw += (mx - this.gest.mx) * 0.006;
        this.cam.pitch = Math.max(0.18, Math.min(1.42, this.cam.pitch + (my - this.gest.my) * 0.005));
        this.gest.dist = dist;
        this.gest.mx = mx;
        this.gest.my = my;
        return;
      }
      if (this.gest.mode === 'pan') {
        const k = this.cam.d * 0.0021;
        const dx = (e.clientX - prev.x) * k;
        const dy = (e.clientY - prev.y) * k;
        const cy = Math.cos(this.cam.yaw);
        const sy = Math.sin(this.cam.yaw);
        this.cam.fx += sy * dx + cy * dy;
        this.cam.fy += cy * dx - sy * dy;
        this.clampFocus();
      } else if (this.gest.mode === 'orbit') {
        this.cam.yaw += (e.clientX - prev.x) * 0.008;
        this.cam.pitch = Math.max(0.18, Math.min(1.42, this.cam.pitch - (e.clientY - prev.y) * 0.006));
      }
    });
    const up = (e: PointerEvent) => {
      this.pointers.delete(e.pointerId);
      const canTap = this.gest.mode !== 'pinch' && this.pointers.size === 0;
      if (this.pointers.size < 2) this.gest.mode = 'none';
      if (canTap && this.onTap) {
        const dt = performance.now() - this.gest.downT;
        const moved = Math.hypot(e.clientX - this.gest.sx, e.clientY - this.gest.sy);
        if (dt < 420 && moved < 9) {
          const hit = this.pickAt(e.clientX, e.clientY);
          if (hit) this.onTap(hit);
        }
      }
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', (e) => this.pointers.delete(e.pointerId));
    c.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        this.cam.d = Math.max(9, Math.min(150, this.cam.d * (e.deltaY > 0 ? 1.12 : 1 / 1.12)));
      },
      { passive: false },
    );
    c.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  private clampFocus(): void {
    this.cam.fx = Math.max(-4, Math.min(this.W + 4, this.cam.fx));
    this.cam.fy = Math.max(-4, Math.min(this.H + 4, this.cam.fy));
  }

  private eyePos() {
    const { fx, fy, d, yaw, pitch } = this.cam;
    const cp = Math.cos(pitch);
    return [fx + d * cp * Math.sin(yaw), fy + d * cp * Math.cos(yaw), d * Math.sin(pitch) + 1.2];
  }

  private pickAt(clientX: number, clientY: number): { x: number; y: number } | null {
    const r = this.canvas.getBoundingClientRect();
    const ndcx = ((clientX - r.left) / r.width) * 2 - 1;
    const ndcy = 1 - ((clientY - r.top) / r.height) * 2;
    const eye = this.eyePos();
    const { fwd, right, up } = cameraBasis(eye, [this.cam.fx, this.cam.fy, 0.4]);
    const tan = Math.tan(0.44);
    const aspect = r.width / r.height;
    const dir = [
      fwd[0] + right[0] * ndcx * tan * aspect + up[0] * ndcy * tan,
      fwd[1] + right[1] * ndcx * tan * aspect + up[1] * ndcy * tan,
      fwd[2] + right[2] * ndcx * tan * aspect + up[2] * ndcy * tan,
    ];
    const l = Math.hypot(dir[0], dir[1], dir[2]) || 1;
    return rayCellPick(eye, dir.map((v) => v / l), this.heightAt, this.W, this.H);
  }

  // ---------- العالم ----------
  private ensureWorld(g: Game): void {
    if (this.cellRef === g.cellTerrain && this.terrainVAO) return;
    this.cellRef = g.cellTerrain;
    this.fx = buildWorldFX(this.W, this.H, g.cellTerrain);
    const water = nodeWaterFlag(this.W, this.H, g.cellTerrain);
    const hs = nodeHeights(this.W, this.H, this.fx.elev, water);
    this.heightAt = makeHeightAt(this.W, this.H, hs);
    const gl = this.gl;
    const NW = this.W + 1;
    const verts = new Float32Array(NW * (this.H + 1) * 9);
    for (let gy = 0; gy <= this.H; gy++) {
      for (let gx = 0; gx <= this.W; gx++) {
        const i = gy * NW + gx;
        const hE = hs[gy * NW + Math.min(gx + 1, NW - 1)];
        const hW = hs[gy * NW + Math.max(gx - 1, 0)];
        const hS = hs[Math.min(gy + 1, this.H) * NW + gx];
        const hN = hs[Math.max(gy - 1, 0) * NW + gx];
        const nx = (hW - hE) / 2;
        const ny = (hN - hS) / 2;
        const nl = Math.hypot(nx, ny, 1) || 1;
        const b = i * 9;
        verts[b] = gx;
        verts[b + 1] = gy;
        verts[b + 2] = hs[i];
        verts[b + 3] = nx / nl;
        verts[b + 4] = ny / nl;
        verts[b + 5] = 1 / nl;
        verts[b + 6] = gx / this.W;
        verts[b + 7] = 1 - gy / this.H;
        verts[b + 8] = water[i];
      }
    }
    const idx = new Uint16Array(this.W * this.H * 6);
    let q = 0;
    for (let gy = 0; gy < this.H; gy++) {
      for (let gx = 0; gx < this.W; gx++) {
        const a = gy * NW + gx;
        idx[q++] = a; idx[q++] = a + 1; idx[q++] = a + NW + 1;
        idx[q++] = a; idx[q++] = a + NW + 1; idx[q++] = a + NW;
      }
    }
    if (!this.terrainVAO) this.terrainVAO = gl.createVertexArray()!;
    gl.bindVertexArray(this.terrainVAO);
    if (!this.terrainBuf) this.terrainBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.terrainBuf);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
    for (const [loc, size, off] of [[0, 3, 0], [1, 3, 12], [2, 2, 24], [3, 1, 32]] as const) {
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 36, off);
    }
    if (!this.terrainIdx) this.terrainIdx = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.terrainIdx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    this.terrainCount = idx.length;
    gl.bindVertexArray(null);
    this.texSigNum = -1;
  }

  private paintTex(g: Game): void {
    if (!this.fx) return;
    let sig = Math.floor(g.tick / 12) >>> 0;
    for (const p of g.provinces) sig = (sig * 31 + p.ownerId * 13 + (g.tick - p.lastBattleTick < 12 ? 7 : 0)) >>> 0;
    if (sig === this.texSigNum) return;
    this.texSigNum = sig;
    const { W, H } = this;
    const T = TEX_PX;
    const tc = this.texCanvas;
    if (tc.width !== W * T || tc.height !== H * T) {
      tc.width = W * T;
      tc.height = H * T;
    }
    const x2 = this.texCtx;
    const scars = battleScars(W, H, g.provinces, g.tick, g.seed % 97);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const t = g.cellTerrain[i];
        const base = TERRAIN_BASE[t];
        const sh = 0.62 + 0.5 * (this.fx.shade[i] - 0.7);
        const n = hsh(i * 2.31);
        let r = base[0] * sh * (0.92 + n * 0.16);
        let gg = base[1] * sh * (0.92 + n * 0.16);
        let b = base[2] * sh * (0.92 + n * 0.16);
        if (t !== 'water') {
          const owner = g.provinces[g.cellProvince[i]].ownerId;
          if (owner >= 0) {
            const c = this.hex01(g.nation(owner).color);
            r = r * 0.55 + c[0] * 255 * 0.45;
            gg = gg * 0.55 + c[1] * 255 * 0.45;
            b = b * 0.55 + c[2] * 255 * 0.45;
          }
          const sc = scars[i];
          if (sc > 0.02) {
            const blood = hsh(i * 7.7) > 0.4;
            const bl = blood ? [96, 22, 16] : [44, 32, 24];
            r = r * (1 - sc * 0.4) + bl[0] * sc * 0.4;
            gg = gg * (1 - sc * 0.4) + bl[1] * sc * 0.4;
            b = b * (1 - sc * 0.4) + bl[2] * sc * 0.4;
          }
        }
        x2.fillStyle = `rgb(${r | 0},${gg | 0},${b | 0})`;
        x2.fillRect(x * T, y * T, T, T);
      }
    }
    // أنهار
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const rv = this.fx.river[y * W + x];
        if (rv <= 0) continue;
        const w = 1 + Math.round(rv * 2.4);
        const o = (T - w) / 2;
        x2.fillStyle = `rgba(88,134,170,${0.7 + rv * 0.25})`;
        x2.fillRect(x * T + o, y * T + o, w, w);
        if (x > 0 && this.fx.river[y * W + x - 1] > 0) x2.fillRect(x * T, y * T + o, T - o + 1, w);
        if (x + 1 < W && this.fx.river[y * W + x + 1] > 0) x2.fillRect(x * T + o, y * T + o, T - o + 1, w);
        if (y > 0 && this.fx.river[(y - 1) * W + x] > 0) x2.fillRect(x * T + o, y * T, w, T - o + 1);
        if (y + 1 < H && this.fx.river[(y + 1) * W + x] > 0) x2.fillRect(x * T + o, y * T + o, w, T - o + 1);
      }
    }
    // حدود المقاطعات
    x2.strokeStyle = 'rgba(50,34,18,0.4)';
    x2.lineWidth = 1;
    x2.beginPath();
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const pv = g.cellProvince[i];
        if (x + 1 < W && g.cellProvince[i + 1] !== pv) {
          x2.moveTo(x * T + T, y * T);
          x2.lineTo(x * T + T, (y + 1) * T);
        }
        if (y + 1 < H && g.cellProvince[i + W] !== pv) {
          x2.moveTo(x * T, y * T + T);
          x2.lineTo((x + 1) * T, y * T + T);
        }
      }
    }
    x2.stroke();
    // حدود الدول
    x2.strokeStyle = 'rgba(24,14,7,0.85)';
    x2.lineWidth = 2;
    x2.beginPath();
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const o = g.provinces[g.cellProvince[i]].ownerId;
        if (x + 1 < W && g.provinces[g.cellProvince[i + 1]].ownerId !== o) {
          x2.moveTo(x * T + T, y * T);
          x2.lineTo(x * T + T, (y + 1) * T);
        }
        if (y + 1 < H && g.provinces[g.cellProvince[i + W]].ownerId !== o) {
          x2.moveTo(x * T, y * T + T);
          x2.lineTo((x + 1) * T, y * T + T);
        }
      }
    }
    x2.stroke();
    const gl = this.gl;
    if (!this.tex) this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, tc);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
  }

  private hex01(hex: string): [number, number, number] {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) return [0.8, 0.8, 0.8];
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
  }

  // ---------- بناء المشهد ----------
  private buildScene(g: Game): { solids: SolidInst[]; soldiers: SolidInst[]; decals: number[]; bills: number[] } {
    const solids: SolidInst[] = [];
    const soldiers: SolidInst[] = [];
    const decals: number[] = [];
    const bills: number[] = [];
    const now = performance.now();
    const ha = this.heightAt;

    // مدن وعواصم محصّنة وثكنات
    for (const p of g.provinces) {
      if (!p.city || p.terrain === 'water') continue;
      const cx = p.center.x + 0.5;
      const cy = p.center.y + 0.5;
      const z = ha(cx, cy);
      const owner = p.ownerId >= 0 ? g.nation(p.ownerId) : null;
      const cap = !!p.city.isCapital;
      const nRooms = cap ? 7 : 2;
      const rad = cap ? 1.9 : 0.8;
      const walls = this.hex01(owner?.color ?? '#c9b48a');
      for (let i = 0; i < nRooms; i++) {
        const a = hsh(p.id * 31 + i) * Math.PI * 2;
        const rr = 0.35 + hsh(p.id * 7 + i * 3) * rad;
        const bx = cx + Math.cos(a) * rr;
        const by = cy + Math.sin(a) * rr;
        const bz = ha(bx, by);
        const w = 0.26 + hsh(i + p.id) * 0.2;
        const h = 0.32 + hsh(i * 5 + p.id) * (cap ? 0.5 : 0.22);
        const yaw = hsh(i + p.id * 3) * Math.PI;
        solids.push({ x: bx, y: by, z: bz + 0.01, yaw, sx: w, sy: w, sz: h, r: 0.86, g: 0.8, b: 0.7, kind: 0, phase: 0 });
        solids.push({ x: bx, y: by, z: bz + 0.01 + h, yaw, sx: w * 1.28, sy: w * 1.28, sz: w * 0.62, r: 0.62, g: 0.27, b: 0.2, kind: 1, phase: 0 });
      }
      if (cap) {
        for (let i = 0; i < 4; i++) {
          const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
          const wx = cx + Math.cos(a) * 1.35;
          const wy = cy + Math.sin(a) * 1.35;
          solids.push({
            x: wx, y: wy, z: ha(wx, wy) + 0.01, yaw: a + Math.PI / 2,
            sx: 1.9, sy: 0.14, sz: 0.5,
            r: walls[0], g: walls[1], b: walls[2], kind: 0, phase: 0,
          });
          const tx = cx + Math.cos(a + Math.PI / 4) * 1.6;
          const ty = cy + Math.sin(a + Math.PI / 4) * 1.6;
          const tz = ha(tx, ty);
          solids.push({ x: tx, y: ty, z: tz + 0.01, yaw: 0, sx: 0.3, sy: 0.3, sz: 0.8, r: Math.min(1, walls[0] * 1.08), g: Math.min(1, walls[1] * 1.08), b: Math.min(1, walls[2] * 1.08), kind: 0, phase: 0 });
          solids.push({ x: tx, y: ty, z: tz + 0.81, yaw: 0, sx: 0.44, sy: 0.44, sz: 0.3, r: walls[0] * 0.6, g: walls[1] * 0.6, b: walls[2] * 0.6, kind: 1, phase: 0 });
        }
        solids.push({ x: cx, y: cy, z: z + 0.01, yaw: 0.4, sx: 0.6, sy: 0.6, sz: 1.35, r: Math.min(1, walls[0] * 1.15), g: Math.min(1, walls[1] * 1.15), b: Math.min(1, walls[2] * 1.15), kind: 0, phase: 0 });
        solids.push({ x: cx, y: cy, z: z + 1.36, yaw: 0.4, sx: 0.8, sy: 0.8, sz: 0.42, r: walls[0] * 0.55, g: walls[1] * 0.5, b: walls[2] * 0.5, kind: 1, phase: 0 });
        if (owner) {
          solids.push({ x: cx + 0.36, y: cy, z: z + 1.82, yaw: 0.4, sx: 0.5, sy: 0.06, sz: 0.34, r: walls[0], g: walls[1], b: walls[2], kind: 2, phase: (p.id * 0.13) % 1 });
          for (let i = 0; i < 2; i++) {
            const a = (i ? 2.4 : 0.6) + hsh(p.id) * 0.4;
            const bx = cx + Math.cos(a) * 2.7;
            const by = cy + Math.sin(a) * 2.7;
            solids.push({ x: bx, y: by, z: ha(bx, by) + 0.01, yaw: a, sx: 1.05, sy: 0.36, sz: 0.3, r: walls[0] * 0.9, g: walls[1] * 0.85, b: walls[2] * 0.8, kind: 0, phase: 0 });
            for (let t = 0; t < 3; t++) {
              const tx = bx + Math.cos(a + Math.PI / 2) * (t - 1) * 0.44 + Math.cos(a) * 0.55;
              const ty = by + Math.sin(a + Math.PI / 2) * (t - 1) * 0.44 + Math.sin(a) * 0.55;
              solids.push({ x: tx, y: ty, z: ha(tx, ty) + 0.01, yaw: 0, sx: 0.24, sy: 0.24, sz: 0.18, r: 0.82, g: 0.72, b: 0.5, kind: 1, phase: 0 });
            }
          }
        }
      }
      if (p.garrison > 240 && !cap) {
        const gx = cx + 0.7;
        const gy = cy - 0.5;
        solids.push({ x: gx, y: gy, z: ha(gx, gy) + 0.01, yaw: 0.5, sx: 0.32, sy: 0.32, sz: 0.22, r: 0.8, g: 0.7, b: 0.48, kind: 1, phase: 0 });
      }
    }

    // جيوش: تشكيلات جنود + أعلام
    const nArmies = Math.max(1, g.armies.length);
    const perCap = Math.max(6, Math.min(44, Math.floor(SOLDIER_BUDGET / nArmies)));
    for (const a of g.armies) {
      const n = g.nation(a.nationId);
      const cur = g.provinces[a.provinceId].center;
      let px = cur.x + 0.5;
      let py = cur.y + 0.5;
      const moving = a.path.length > 0 && a.progress > 0;
      if (moving) {
        const nx2 = g.provinces[a.path[0]].center;
        const t = Math.min(1, a.progress);
        px = cur.x + 0.5 + (nx2.x - cur.x) * t;
        py = cur.y + 0.5 + (nx2.y - cur.y) * t;
      }
      const col = this.hex01(n.color);
      const cnt = squadSize(a.soldiers, perCap);
      const cols = Math.ceil(Math.sqrt(cnt * 2));
      const rows = Math.max(1, Math.ceil(cnt / cols));
      const sp = 0.34;
      let yaw = -this.cam.yaw;
      if (moving) yaw = Math.atan2(py - (cur.y + 0.5), px - (cur.x + 0.5)) - Math.PI / 2;
      for (let i = 0; i < cnt; i++) {
        const cxi = (i % cols) - (cols - 1) / 2;
        const cyi = Math.floor(i / cols) - (rows - 1) / 2;
        const wx = px + cxi * sp;
        const wy = py + cyi * sp;
        const j = hsh(a.id * 17 + i * 3.1);
        soldiers.push({
          x: wx, y: wy, z: ha(wx, wy) + 0.01, yaw,
          sx: 0.72 + j * 0.14, sy: 0.72 + j * 0.14, sz: 0.8 + j * 0.18,
          r: Math.min(1, col[0] * (0.85 + j * 0.3)), g: Math.min(1, col[1] * (0.85 + j * 0.3)), b: Math.min(1, col[2] * (0.85 + j * 0.3)),
          kind: moving ? 4 : 3, phase: j,
        });
      }
      const fz = ha(px, py);
      solids.push({ x: px, y: py, z: fz + 0.02, yaw, sx: 0.52, sy: 0.06, sz: 0.38, r: col[0], g: col[1], b: col[2], kind: 2, phase: (a.id * 0.37) % 1 });
      // ظل
      decals.push(px, py, fz + 0.03, 0, cols * sp + 0.9, rows * sp + 0.9, 0.04, 0.03, 0.02, 0.3, 1, 0);
      if (g.selection?.type === 'army' && g.selection.id === a.id) {
        const pu = 0.65 + 0.3 * Math.sin(now / 240);
        decals.push(px, py, fz + 0.05, 0, (cols + 2) * sp * 1.3, (rows + 2) * sp * 2.1, 1, 0.85, 0.3, pu, 0, 0);
      }
      if (!moving && this.nightK > 0.2) {
        bills.push(px, py - 0.55, ha(px, py - 0.55) + 0.14, 0.5, 1, 0.55, 0.18, 0.75 * this.nightK, 0, hsh(a.id + Math.floor(now / 90)), 0, 0);
      }
    }

    // معارك حية على التضاريس
    for (const p of g.provinces) {
      const age = g.tick - p.lastBattleTick;
      if (age >= 12) continue;
      const cx = p.center.x + 0.5;
      const cy = p.center.y + 0.5;
      const z = ha(cx, cy) + 0.06;
      const fading = 1 - age / 12;
      for (let k = 0; k < 2; k++) {
        const t = ((now / 750) + k * 0.5) % 1;
        decals.push(cx, cy, z, 0, 1.4 + t * 3.4, 1.4 + t * 3.4, 1, 0.28, 0.16, (1 - t) * 0.55 * fading, 0, 0);
      }
      for (let i = 0; i < 8; i++) {
        const ph = hsh(p.id * 31 + i * 13.7);
        const t = (now / 600 + ph) % 1;
        const ang = ph * Math.PI * 2 + t * 2.2;
        const rr = 0.6 + t * 2.1;
        bills.push(
          cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr * 0.75, ha(cx, cy) + 0.5 + t * 0.9,
          0.14 * (1 - t) + 0.05,
          1, t < 0.5 ? 0.82 : 0.45, t < 0.5 ? 0.45 : 0.2,
          (1 - t) * fading, 2, t, 0, 0,
        );
      }
      if (p.city) {
        for (let i = 0; i < 3; i++) {
          const t = (now / 1500 + i / 3 + hsh(p.id + i)) % 1;
          bills.push(
            cx + (hsh(p.id * 7 + i) - 0.5) * 0.9, cy + (hsh(p.id * 11 + i) - 0.5) * 0.9,
            ha(cx, cy) + 0.55 + t * 1.9, 0.5 + t * 0.9,
            0.3, 0.27, 0.24, (1 - t) * 0.42 * fading, 1, t, 0, 0,
          );
        }
        bills.push(cx, cy, ha(cx, cy) + 0.4, 1.6, 1, 0.5, 0.16, 0.55 * fading, 0, (now / 130) % 1, 0, 0);
      }
    }

    // تحديد مقاطعة
    if (g.selection?.type === 'province') {
      const p = g.provinces[g.selection.id];
      const z = ha(p.center.x + 0.5, p.center.y + 0.5) + 0.07;
      const rad = Math.sqrt(p.cells.length) * 0.6 + 0.6;
      decals.push(p.center.x + 0.5, p.center.y + 0.5, z, 0, rad * 2, rad * 2, 1, 0.85, 0.3, 0.85, 0, 0);
    }

    // نقاط مسار الجيش المحدد
    if (g.selection?.type === 'army') {
      const a = g.armies.find((x) => x.id === (g.selection as { type: string; id: number }).id);
      if (a && a.path.length) {
        const pts = [g.provinces[a.provinceId].center, ...a.path.map((pid) => g.provinces[pid].center)];
        for (let i2 = 0; i2 + 1 < pts.length; i2++) {
          for (let d = 0; d < 4; d++) {
            const t = (d + ((now / 500) % 1)) / 4;
            if (t > 1) continue;
            const lx = pts[i2].x + (pts[i2 + 1].x - pts[i2].x) * t + 0.5;
            const ly = pts[i2].y + (pts[i2 + 1].y - pts[i2].y) * t + 0.5;
            decals.push(lx, ly, ha(lx, ly) + 0.07, 0, 0.26, 0.26, 1, 0.85, 0.3, 0.75, 1, 0);
          }
        }
      }
    }

    return { solids, soldiers, decals, bills };
  }

  // ---------- إطار ----------
  render(g: Game): void {
    const gl = this.gl;
    this.ensureWorld(g);
    this.paintTex(g);

    const now = performance.now() / 1000;
    const hour = g.clock.hour + 0.5;
    const [tr, tg, tb, ta] = skyTint(hour);
    const damp = this.simSpeed >= 4 ? 0.3 : this.simSpeed >= 2 ? 0.6 : 1;
    this.nightK = nightAmount(ta * damp);
    const day = 1 - this.nightK;

    const h01 = Math.max(0, Math.min(1, (hour - 5.5) / 13));
    const sunEl = Math.sin(h01 * Math.PI);
    const az = -Math.PI / 2 + h01 * Math.PI;
    let sun = [Math.cos(az) * 0.8, Math.sin(az) * 0.5, Math.max(0.18, sunEl)];
    let lmul = Math.max(0.18, sunEl * 1.35);
    let amb = [0.16 + 0.26 * day, 0.18 + 0.26 * day, 0.26 + 0.16 * day];
    let lightCol = [0.62 + 0.38 * sunEl, 0.52 + 0.45 * sunEl, 0.4 + 0.55 * sunEl];
    if (h01 <= 0.02) {
      sun = [-0.5, -0.6, 0.75];
      lmul = 0.14;
      amb = [0.1, 0.12, 0.2];
      lightCol = [0.5, 0.6, 0.85];
    }
    const sl = Math.hypot(sun[0], sun[1], sun[2]) || 1;
    sun = sun.map((v) => v / sl);
    const fog = [
      Math.min(1, (1 - this.nightK) * 0.5 + 0.12 + (tr / 255) * ta * 0.7),
      Math.min(1, (1 - this.nightK) * 0.48 + 0.13 + (tg / 255) * ta * 0.5),
      Math.min(1, (1 - this.nightK) * 0.45 + 0.18 + (tb / 255) * ta * 0.3),
    ];

    const eye = this.eyePos();
    const tgt = [this.cam.fx, this.cam.fy, 0.4];
    const proj = mat4Persp(0.88, this.cw / this.ch, 0.4, 430);
    const view = mat4LookAt(eye, tgt);
    const mvp = mat4Mul(proj, view);

    gl.enable(gl.DEPTH_TEST);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.clearColor(fog[0], fog[1], fog[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.CULL_FACE);
    gl.frontFace(gl.CCW);

    const setLight = (p: WebGLProgram) => {
      gl.uniform3fv(this.u(p, 'uSun'), new Float32Array(sun));
      gl.uniform3fv(this.u(p, 'uAmb'), new Float32Array(amb));
      gl.uniform3fv(this.u(p, 'uLightCol'), new Float32Array(lightCol));
      gl.uniform1f(this.u(p, 'uLightMul'), lmul);
      gl.uniform3fv(this.u(p, 'uFog'), new Float32Array(fog));
    };

    gl.useProgram(this.progT);
    gl.uniformMatrix4fv(this.u(this.progT, 'uMVP'), false, mvp);
    gl.uniform3fv(this.u(this.progT, 'uEye'), new Float32Array(eye));
    gl.uniform1f(this.u(this.progT, 'uTime'), now);
    setLight(this.progT);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.uniform1i(this.u(this.progT, 'uTex'), 0);
    gl.bindVertexArray(this.terrainVAO);
    gl.drawElements(gl.TRIANGLES, this.terrainCount, gl.UNSIGNED_SHORT, 0);
    gl.bindVertexArray(null);

    const scene = this.buildScene(g);

    if (scene.decals.length) {
      gl.useProgram(this.progD);
      gl.uniformMatrix4fv(this.u(this.progD, 'uMVP'), false, mvp);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.disable(gl.CULL_FACE);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.decalInst);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(scene.decals), gl.STREAM_DRAW);
      gl.bindVertexArray(this.decalVAO);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, Math.floor(scene.decals.length / 12));
      gl.bindVertexArray(null);
      gl.depthMask(true);
      gl.enable(gl.CULL_FACE);
    }

    gl.disable(gl.BLEND);
    gl.useProgram(this.progS);
    gl.uniformMatrix4fv(this.u(this.progS, 'uMVP'), false, mvp);
    gl.uniform3fv(this.u(this.progS, 'uEye'), new Float32Array(eye));
    gl.uniform1f(this.u(this.progS, 'uTime'), now);
    setLight(this.progS);
    if (scene.solids.length) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.solidInst);
      gl.bufferData(gl.ARRAY_BUFFER, packSolids(scene.solids), gl.STREAM_DRAW);
      gl.bindVertexArray(this.boxVAO);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, scene.solids.length);
      gl.bindVertexArray(null);
    }
    if (scene.soldiers.length) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.solidInst);
      gl.bufferData(gl.ARRAY_BUFFER, packSolids(scene.soldiers), gl.STREAM_DRAW);
      gl.bindVertexArray(this.soldierVAO);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 144, scene.soldiers.length);
      gl.bindVertexArray(null);
    }

    if (scene.bills.length) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.billInst);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(scene.bills), gl.STREAM_DRAW);
      gl.useProgram(this.progB);
      gl.uniformMatrix4fv(this.u(this.progB, 'uMVP'), false, mvp);
      const { right, up } = cameraBasis(eye, tgt);
      gl.uniform3fv(this.u(this.progB, 'uRight'), new Float32Array(right));
      gl.uniform3fv(this.u(this.progB, 'uUp'), new Float32Array(up));
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.depthMask(false);
      gl.disable(gl.CULL_FACE);
      gl.bindVertexArray(this.billVAO);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, Math.floor(scene.bills.length / 12));
      gl.bindVertexArray(null);
      gl.depthMask(true);
      gl.enable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
    }

    this.drawLabels(g, mvp);
  }

  private drawLabels(g: Game, mvp: M4): void {
    const lc = this.labelCtx;
    const dpr = this.labelC.width / this.cw;
    lc.setTransform(1, 0, 0, 1, 0, 0);
    lc.clearRect(0, 0, this.labelC.width, this.labelC.height);
    lc.scale(dpr, dpr);
    lc.textAlign = 'center';
    lc.textBaseline = 'middle';
    const d = this.cam.d;
    const ha = this.heightAt;
    for (const p of g.provinces) {
      const x = p.center.x + 0.5;
      const y = p.center.y + 0.5;
      const z = ha(x, y) + 0.5;
      const proj = projectPoint(mvp, x, y, z, this.cw, this.ch);
      if (!proj) continue;
      if (p.city?.isCapital) {
        lc.font = `600 ${Math.max(10, 13 - d * 0.03)}px sans-serif`;
        lc.lineWidth = 3;
        lc.strokeStyle = 'rgba(18,10,4,0.9)';
        lc.strokeText('👑 ' + p.name, proj.x, proj.y);
        lc.fillStyle = '#ffe9a8';
        lc.fillText('👑 ' + p.name, proj.x, proj.y);
        if (d < 68 && p.ownerId >= 0) {
          lc.font = `${Math.max(9, 11 - d * 0.03)}px sans-serif`;
          lc.strokeText(g.nation(p.ownerId).name, proj.x, proj.y + 12);
          lc.fillStyle = 'rgba(255,233,168,0.9)';
          lc.fillText(g.nation(p.ownerId).name, proj.x, proj.y + 12);
        }
      } else if (d < 48) {
        lc.font = `${Math.max(8, 11 - d * 0.05)}px sans-serif`;
        lc.lineWidth = 2.5;
        lc.strokeStyle = 'rgba(18,10,4,0.75)';
        lc.strokeText(p.name, proj.x, proj.y);
        lc.fillStyle = 'rgba(240,230,205,0.9)';
        lc.fillText(p.name, proj.x, proj.y);
      }
    }
    if (d < 95) {
      for (const a of g.armies) {
        const cur = g.provinces[a.provinceId].center;
        let x = cur.x + 0.5;
        let y = cur.y + 0.5;
        if (a.path.length > 0 && a.progress > 0) {
          const nx2 = g.provinces[a.path[0]].center;
          const t = Math.min(1, a.progress);
          x = cur.x + 0.5 + (nx2.x - cur.x) * t;
          y = cur.y + 0.5 + (nx2.y - cur.y) * t;
        }
        const proj = projectPoint(mvp, x, y, ha(x, y) + 1.4, this.cw, this.ch);
        if (!proj) continue;
        const label = a.soldiers >= 1000 ? `${(a.soldiers / 1000).toFixed(1)}k` : `${Math.round(a.soldiers)}`;
        lc.font = '700 11px sans-serif';
        lc.lineWidth = 3;
        lc.strokeStyle = 'rgba(12,8,4,0.9)';
        lc.strokeText(label, proj.x, proj.y);
        lc.fillStyle = '#ffe9a8';
        lc.fillText(label, proj.x, proj.y);
      }
    }
  }
}
