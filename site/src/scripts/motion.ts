/* ---------------------------------------------------------------------------
   The one motion module.

   THE RULE THIS FILE OBEYS:
   The stylesheet encodes the FINAL state of everything. GSAP supplies only the
   START, and only inside the no-preference branch. Where an element has to be
   hidden before it animates in, THIS FILE hides it (gsap.set) rather than the
   stylesheet, so a reduced-motion visitor, or anyone whose JS fails to load,
   sees the fully composed page instead of a blank one.

   Reduced motion is therefore not a separate code path to keep in sync. It is
   the absence of a code path, which is why it cannot rot.

   Every animated property here resolves to `transform` or `opacity`. Nothing
   triggers layout. There is no ScrollTrigger pin anywhere on this page: pinning
   injects a pin-spacer with explicit width/height, which is layout work done
   outside any tween and the usual cause of horizontal overflow on mobile.
--------------------------------------------------------------------------- */

import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { createField } from "./canvas";

gsap.registerPlugin(ScrollTrigger);

declare global {
  interface Window {
    __motion?: { destroy: () => void; count: () => number };
  }
}

const all = <T extends HTMLElement>(sel: string): T[] =>
  gsap.utils.toArray<T>(sel);

/* The field renders in every case, including reduced motion, where it is drawn
   exactly once at progress 1 (the composed end state) by its ResizeObserver. */
const canvasEl = document.querySelector<HTMLCanvasElement>("#field");
const field = canvasEl ? createField(canvasEl) : null;

const mm = gsap.matchMedia();

mm.add("(prefers-reduced-motion: no-preference)", () => {
  const hero = document.querySelector<HTMLElement>("#hero");
  const words = all(".hero__w");
  const lede = document.querySelector<HTMLElement>(".hero__lede");
  const cta = document.querySelector<HTMLElement>(".hero__cta");
  const runs = all(".hero__run");
  const gold = document.querySelector<HTMLElement>(".tok-gold");
  const mint = document.querySelector<HTMLElement>(".tok-free");
  const reveals = all("[data-reveal]");
  const rules = all("[data-rule]");

  /* ---- hero, on load ---------------------------------------------------- */

  const heroBits = [lede, cta].filter(Boolean) as HTMLElement[];
  if (words.length) gsap.set(words, { opacity: 0, y: 20 });
  if (heroBits.length) gsap.set(heroBits, { opacity: 0, y: 14 });
  if (runs.length) gsap.set(runs, { opacity: 0, y: 22 });

  const tl = gsap.timeline({ defaults: { ease: "power3.out" } });

  if (words.length)
    tl.to(words, { opacity: 1, y: 0, duration: 0.75, stagger: 0.04 });
  if (heroBits.length)
    tl.to(heroBits, { opacity: 1, y: 0, duration: 0.6, stagger: 0.08 }, "-=0.45");
  if (runs.length)
    tl.to(runs, { opacity: 1, y: 0, duration: 0.7, stagger: 0.12 }, "-=0.35");

  /* ---- the drain: gold is the outgoing state, mint sits underneath ------ */

  if (gold) {
    // will-change only on the one continuously-animating element. Each layer
    // promotion costs roughly w * h * 4 bytes of GPU memory, so this is not
    // applied broadly.
    gsap.set(gold, { willChange: "transform, opacity" });
    tl.fromTo(
      gold,
      { opacity: 1, y: 0 },
      { opacity: 0, y: "-0.4em", duration: 0.85, ease: "power2.inOut" },
      "+=0.55",
    );
  }
  if (mint) {
    tl.from(
      mint,
      { opacity: 0, y: "0.4em", duration: 0.7, ease: "power2.out" },
      "<0.2",
    );
  }

  /* ---- the field, scrubbed across the hero ------------------------------ */

  const scrub = { p: 0 };
  if (hero && field) {
    // Supply the START. Without this the field would paint its composed end
    // state first and then snap back to gold when the scrub initialises.
    field.render(0);
    gsap.to(scrub, {
      p: 1,
      ease: "none",
      // Driving render from onUpdate rather than a persistent gsap.ticker
      // callback: it only paints when the value actually changes, and the
      // tween's own lifecycle is the teardown.
      onUpdate: () => field.render(scrub.p),
      scrollTrigger: {
        trigger: hero,
        start: "top top",
        end: "bottom top",
        scrub: 0.4,
        invalidateOnRefresh: true,
      },
    });
  }

  /* ---- section reveals -------------------------------------------------- */

  if (reveals.length) {
    gsap.set(reveals, { opacity: 0, y: 16 });
    ScrollTrigger.batch(reveals, {
      start: "top 90%",
      once: true,
      onEnter: (batch) =>
        gsap.to(batch, {
          opacity: 1,
          y: 0,
          duration: 0.6,
          stagger: 0.06,
          ease: "power3.out",
          overwrite: true,
        }),
    });
  }

  /* ---- hairlines: scaleX, never width ----------------------------------- */

  if (rules.length) {
    gsap.set(rules, { scaleX: 0 });
    ScrollTrigger.batch(rules, {
      start: "top 92%",
      once: true,
      onEnter: (batch) =>
        gsap.to(batch, { scaleX: 1, duration: 0.9, ease: "power3.out" }),
    });
  }

  /* Runs when the query stops matching, e.g. the visitor flips the OS
     reduced-motion setting mid-session. GSAP reverts its own tweens, sets and
     ScrollTriggers automatically; this releases the layer promotion. */
  return () => {
    if (gold) gsap.set(gold, { willChange: "auto" });
    // Return the field to its composed state, since the visitor has just
    // asked for reduced motion.
    field?.render(1);
  };
});

/* An escape hatch that makes the teardown claim testable rather than asserted:
   ScrollTrigger.getAll().length is N before this runs and 0 after. */
function destroy(): void {
  mm.revert();
  ScrollTrigger.getAll().forEach((t) => t.kill());
  ScrollTrigger.clearScrollMemory();
  field?.destroy();
}

window.__motion = { destroy, count: () => ScrollTrigger.getAll().length };

// Genuinely fires during development, and genuinely leaks ScrollTriggers
// without it.
import.meta.hot?.dispose(destroy);
