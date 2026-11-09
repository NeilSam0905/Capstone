import { useLayoutEffect, useRef } from 'react';

/**
 * One highlight that slides to whichever sibling is active - the segmented
 * tabs' dark pill and the sidebar's active row - instead of each option
 * painting its own and the highlight jumping between them. The web version
 * of reusing one shared element (a Framer Motion layoutId) across states.
 *
 * Drop it in as the first child of the group. It finds the child marked
 * `.is-active` (or `.active`), follows it whenever a class changes or the
 * group resizes, and moves with ease-in-out, since it is something already on
 * screen changing place. While it travels it stretches about 12% along the
 * way it moves, then settles, so it reads as one object being carried over.
 *
 * The first placement does not animate: the highlight starts where it
 * belongs, and only later changes slide.
 */
export default function ActiveThumb({ axis = 'x', className = '' }) {
  const ref = useRef(null);

  useLayoutEffect(() => {
    const thumb = ref.current;
    const group = thumb?.parentElement;
    if (!thumb || !group) return undefined;
    let prev = null;
    let frame = 0;

    const place = () => {
      const active = group.querySelector(':scope > .is-active, :scope > .active');
      if (!active) { thumb.style.opacity = '0'; prev = null; return; }
      const box = {
        left: active.offsetLeft, top: active.offsetTop,
        width: active.offsetWidth, height: active.offsetHeight,
      };
      const moved = prev && (prev.left !== box.left || prev.top !== box.top);
      if (moved && thumb.classList.contains('is-ready')) {
        thumb.classList.remove('is-moving');
        void thumb.offsetWidth;          // restart the stretch
        thumb.classList.add('is-moving');
      }
      thumb.style.opacity = '1';
      thumb.style.left = `${box.left}px`;
      thumb.style.top = `${box.top}px`;
      thumb.style.width = `${box.width}px`;
      thumb.style.height = `${box.height}px`;
      prev = box;
    };

    place();
    // Transitions only once it sits in place, so the first paint is still.
    frame = requestAnimationFrame(() => thumb.classList.add('is-ready'));
    const mo = new MutationObserver(place);
    mo.observe(group, { subtree: true, attributes: true, attributeFilter: ['class'] });
    const ro = new ResizeObserver(place);
    ro.observe(group);
    return () => { cancelAnimationFrame(frame); mo.disconnect(); ro.disconnect(); };
  }, []);

  return <span ref={ref} className={`thumb thumb--${axis}${className ? ` ${className}` : ''}`} aria-hidden="true" />;
}
