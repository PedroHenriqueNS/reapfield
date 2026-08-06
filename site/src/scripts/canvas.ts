/* ---------------------------------------------------------------------------
   The hero field.

   A sparse field of stalks. Each carries its own threshold; as progress rises,
   stalks flip from gold (a derivation was paid for) to mint (it replays free).

   Gold is NEVER interpolated toward mint. The midpoint of #E8B04B and #5FE3B0
   is olive, so each stalk is only ever exactly one of the two brand colours and
   the field transitions as a wipe of individually flipping stalks, separated by
   the dark background. Same principle as the hero token.

   render() never reads layout. All geometry comes from w/h cached by the
   ResizeObserver callback.
--------------------------------------------------------------------------- */

const GOLD = "#E8B04B";
const MINT = "#5FE3B0";
const COUNT = 420;

/** mulberry32. Seeded so the field is identical on every load. */
function rng(seed: number): () => number {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Stalk {
  /** normalised position, so a resize never regenerates the field */
  nx: number;
  ny: number;
  len: number;
  lean: number;
  alpha: number;
  /** progress value at which this stalk flips gold -> mint */
  t: number;
}

function build(): Stalk[] {
  const r = rng(0x1eaf);
  const out: Stalk[] = [];
  for (let i = 0; i < COUNT; i++) {
    const nx = r();
    out.push({
      nx,
      ny: 0.18 + r() * 0.82,
      len: 14 + r() * 46,
      lean: (r() - 0.5) * 10,
      alpha: 0.1 + r() * 0.42,
      // Threshold correlates with x, so the wipe sweeps left to right instead
      // of dissolving at random, plus jitter so the edge is not a hard line.
      t: Math.min(0.999, Math.max(0, nx * 0.82 + r() * 0.28)),
    });
  }
  return out;
}

export interface Field {
  render: (progress: number) => void;
  destroy: () => void;
}

export function createField(canvas: HTMLCanvasElement): Field | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const stalks = build();
  let w = 0;
  let h = 0;
  /* Defaults to the COMPOSED end state: fully cached, all mint. With JS
     disabled or reduced motion preferred, this is the only frame ever drawn.
     The no-preference branch explicitly supplies the start by calling
     render(0) before it wires the scrub. */
  let last = 1;

  function render(progress: number): void {
    last = progress;
    if (!ctx || w === 0 || h === 0) return;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 1;
    ctx.lineCap = "round";
    for (const s of stalks) {
      const x = s.nx * w;
      const y = s.ny * h;
      // Discrete flip. No colour interpolation, ever.
      ctx.strokeStyle = s.t < progress ? MINT : GOLD;
      ctx.globalAlpha = s.alpha;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + s.lean, y - s.len);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  const ro = new ResizeObserver((entries) => {
    const e = entries[0];
    if (!e) return;

    // devicePixelContentBoxSize is exact device pixels with no rounding drift.
    const dpbs = e.devicePixelContentBoxSize?.[0];
    const dpr = window.devicePixelRatio || 1;
    const pxW = dpbs ? dpbs.inlineSize : Math.round(e.contentRect.width * dpr);
    const pxH = dpbs ? dpbs.blockSize : Math.round(e.contentRect.height * dpr);
    if (pxW === 0 || pxH === 0) return;

    // Writing width/height resets the whole 2D context state, so the DPR
    // transform has to be re-applied after every single resize.
    canvas.width = pxW;
    canvas.height = pxH;
    w = pxW / dpr;
    h = pxH / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    render(last);
  });

  ro.observe(canvas);

  return {
    render,
    destroy: () => ro.disconnect(),
  };
}
