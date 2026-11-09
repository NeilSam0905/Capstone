/* Motion timings shared by the components that animate their own exit.
 *
 * They mirror the --dur-*-exit tokens in styles/redesign.css: an exit is
 * about 20% shorter than its entrance, so leaving never feels slower than
 * arriving. A component that closes waits this long, with its `is-closing`
 * class on, before it unmounts. */
export const POP_EXIT_MS = 145;     // dropdowns and popovers (enter 180ms)
export const MODAL_EXIT_MS = 175;   // dialogs (enter 220ms)

/** True when the reader has asked the system for less motion: closes then
 *  happen at once instead of waiting for an exit that will not play. */
export function reducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
