/**
 * Resizable side panels: a drag handle between a panel and the preview. Drag it with the
 * mouse or a finger, or focus it and use the arrow keys; double-click puts it back.
 * Widths are remembered in this browser.
 */

import { useEffect, useRef, useState } from 'preact/hooks';
import { getPref, setPref } from '../prefs';

export interface PanelWidths {
  left: number;
  right: number;
}

export const PANEL_DEFAULTS: PanelWidths = { left: 250, right: 340 };
const LIMITS = { left: [190, 560], right: [280, 720] } as const;
const PREVIEW_MIN = 380; // the preview always keeps at least this much room
const KEY = 'editor-panels';
const isWidths = (value: unknown): value is PanelWidths =>
  typeof value === 'object' && value !== null &&
  typeof (value as PanelWidths).left === 'number' && typeof (value as PanelWidths).right === 'number';

function load(): PanelWidths {
  try {
    // Carry over widths saved by earlier versions, under the old name.
    const old = localStorage.getItem('mailstudio.editor.panels');
    if (old !== null) {
      localStorage.removeItem('mailstudio.editor.panels');
      const parsed: unknown = JSON.parse(old);
      if (isWidths(parsed)) setPref(KEY, parsed);
    }
  } catch {
    // Nothing to carry over.
  }
  return getPref(KEY, PANEL_DEFAULTS, isWidths);
}

/** Panel widths, kept within limits for the space available, and remembered. */
export function usePanelWidths(container: { current: HTMLElement | null }) {
  const [widths, setWidths] = useState<PanelWidths>(load);

  const clamp = (side: keyof PanelWidths, value: number, current: PanelWidths): number => {
    const [min, max] = LIMITS[side];
    const other = side === 'left' ? current.right : current.left;
    const total = container.current?.clientWidth ?? window.innerWidth;
    const room = total - other - PREVIEW_MIN - 16; // 16: the two handles
    return Math.round(Math.max(min, Math.min(max, room, value)));
  };

  const resize = (side: keyof PanelWidths, value: number) =>
    setWidths((current) => {
      const next = { ...current, [side]: clamp(side, value, current) };
      setPref(KEY, next);
      return next;
    });

  // Keep both panels within range when the window gets narrower.
  useEffect(() => {
    const fit = () => setWidths((current) => {
      const left = clamp('left', current.left, current);
      return { left, right: clamp('right', current.right, { ...current, left }) };
    });
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  return { widths, resize };
}

export function Splitter({ side, width, onResize, label }: {
  side: keyof PanelWidths;
  width: number;
  onResize: (width: number) => void;
  label: string;
}) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [active, setActive] = useState(false);
  // Where the width readout sits (px down the splitter), and whether it's showing.
  const [readout, setReadout] = useState<{ y: number; visible: boolean }>({ y: 0, visible: false });
  const hide = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flash = (y?: number) => {
    if (hide.current) clearTimeout(hide.current);
    setReadout((r) => ({ y: y ?? r.y, visible: true }));
    hide.current = setTimeout(() => setReadout((r) => ({ ...r, visible: false })), 900);
  };
  const yIn = (e: PointerEvent) => e.clientY - (e.currentTarget as HTMLElement).getBoundingClientRect().top;
  const [min, max] = LIMITS[side];
  // Dragging right widens the left panel but narrows the right one.
  const direction = side === 'left' ? 1 : -1;

  const end = () => {
    drag.current = null;
    setActive(false);
    document.body.classList.remove('is-resizing');
    flash(); // the readout lingers briefly after letting go
  };

  return (
    <div
      class={`splitter${active ? ' is-active' : ''}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, width };
        setActive(true);
        if (hide.current) clearTimeout(hide.current);
        setReadout({ y: yIn(e), visible: true });
        // Stops the preview frame swallowing the pointer while dragging over it.
        document.body.classList.add('is-resizing');
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        onResize(drag.current.width + direction * (e.clientX - drag.current.x));
        setReadout({ y: yIn(e), visible: true });
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={end}
      onDblClick={(e) => {
        onResize(PANEL_DEFAULTS[side]);
        flash(yIn(e as unknown as PointerEvent));
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        const keys: Record<string, number> = {
          ArrowLeft: width - direction * step,
          ArrowRight: width + direction * step,
          Home: min,
          End: max,
        };
        const next = keys[e.key];
        if (next === undefined) return;
        e.preventDefault();
        onResize(next);
        const middle = (e.currentTarget as HTMLElement).getBoundingClientRect().height / 2;
        flash(readout.visible ? undefined : middle);
      }}
    >
      {readout.visible && (
        <span class={`splitter__readout splitter__readout--${side}`} style={{ top: `${readout.y}px` }} aria-hidden="true">
          {width} px{width <= min ? ' · min' : width >= max ? ' · max' : ''}
        </span>
      )}
    </div>
  );
}
