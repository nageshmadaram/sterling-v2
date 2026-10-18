import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, WheelEvent } from 'react';

export type CropView = { zoom: number; x: number; y: number };
export const INITIAL_CROP: CropView = { zoom: 1, x: 0, y: 0 };
const MIN_ZOOM = 1;
const MAX_ZOOM = 3;
const OUTPUT_SIZE = 256;
// The account API accepts up to 200,000 decoded bytes for a photo.
const MAX_DATA_URL_LENGTH = 260000;

const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n));

/** Image occupies the entire square viewport; x/y are viewport-relative pan offsets. */
export function constrainCrop(width: number, height: number, draft: CropView): CropView {
  const zoom = clamp(Number.isFinite(draft.zoom) ? draft.zoom : 1, MIN_ZOOM, MAX_ZOOM);
  if (!(width > 0 && height > 0)) return { zoom, x: 0, y: 0 };
  const cover = Math.max(1 / width, 1 / height) * zoom;
  const maxX = Math.max(0, (width * cover - 1) / 2);
  const maxY = Math.max(0, (height * cover - 1) / 2);
  // Normalize negative zero at a fully constrained axis (and reject NaN pan).
  return { zoom, x: clamp(draft.x, -maxX, maxX) || 0, y: clamp(draft.y, -maxY, maxY) || 0 };
}

/** Exact source rectangle seen inside the round viewport, before its circular CSS mask. */
export function cropSourceRect(width: number, height: number, input: CropView) {
  const view = constrainCrop(width, height, input);
  const scale = Math.max(1 / width, 1 / height) * view.zoom;
  const sw = 1 / scale;
  const sh = 1 / scale;
  return { sx: width / 2 - (0.5 + view.x) / scale,
    sy: height / 2 - (0.5 + view.y) / scale, sw, sh };
}

export function renderProfileCrop(image: HTMLImageElement, input: CropView): string {
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!(width > 0 && height > 0)) throw Error('Image not loaded');
  const canvas = document.createElement('canvas');
  canvas.width = OUTPUT_SIZE;
  canvas.height = OUTPUT_SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw Error('Image editor unavailable');
  const { sx, sy, sw, sh } = cropSourceRect(width, height, input);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
  ctx.drawImage(image, sx, sy, sw, sh, 0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
  for (const quality of [0.85, 0.7, 0.5, 0.35]) {
    const result = canvas.toDataURL('image/jpeg', quality);
    if (result.startsWith('data:image/jpeg;base64,') && result.length <= MAX_DATA_URL_LENGTH) return result;
  }
  throw Error('The cropped photo is too large. Try a different picture.');
}

type Props = {
  file: File;
  onApply: (photo: string) => void;
  onCancel: () => void;
};

type Point = { x: number; y: number };
function center(points: Point[]): Point {
  return { x: points.reduce((sum, p) => sum + p.x, 0) / points.length,
    y: points.reduce((sum, p) => sum + p.y, 0) / points.length };
}
function distance(a: Point, b: Point) { return Math.hypot(a.x - b.x, a.y - b.y); }

export function ProfilePhotoCropper({ file, onApply, onCancel }: Props) {
  const [src, setSrc] = useState('');
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [view, setView] = useState<CropView>(INITIAL_CROP);
  const [error, setError] = useState('');
  const imageRef = useRef<HTMLImageElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const activePointers = useRef(new Map<number, Point>());
  const ready = !!src && size.width > 0 && size.height > 0 && !error;

  useEffect(() => {
    setSrc('');
    setSize({ width: 0, height: 0 });
    setView(INITIAL_CROP);
    setError('');
    activePointers.current.clear();
    try {
      const url = URL.createObjectURL(file);
      setSrc(url);
      return () => URL.revokeObjectURL(url);
    } catch {
      setError('Could not open this picture.');
    }
  }, [file]);

  const changeView = (update: (prev: CropView) => CropView) => {
    setView(prev => constrainCrop(size.width, size.height, update(prev)));
  };
  const positionImage = () => {
    const cover = Math.max(1 / size.width, 1 / size.height) * view.zoom;
    return {
      width: `${size.width * cover * 100}%`,
      height: `${size.height * cover * 100}%`,
      left: `calc(50% + ${view.x * 100}%)`,
      top: `calc(50% + ${view.y * 100}%)`,
      transform: 'translate(-50%, -50%)',
    };
  };
  const imageStyle = ready ? positionImage() : {};

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!ready) return;
    event.preventDefault();
    activePointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const oldPoint = activePointers.current.get(event.pointerId);
    if (!oldPoint || !ready) return;
    const before = [...activePointers.current.values()];
    activePointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const after = [...activePointers.current.values()];
    const side = viewportRef.current?.getBoundingClientRect().width || 1;
    const previousCenter = center(before);
    const nextCenter = center(after);
    const zoomRatio = before.length === 2 && after.length === 2
      ? distance(after[0], after[1]) / Math.max(1, distance(before[0], before[1])) : 1;
    changeView(prev => ({
      zoom: prev.zoom * zoomRatio,
      x: prev.x + (nextCenter.x - previousCenter.x) / side,
      y: prev.y + (nextCenter.y - previousCenter.y) / side,
    }));
  };
  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    activePointers.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (!ready) return;
    event.preventDefault();
    changeView(prev => ({ ...prev, zoom: prev.zoom * (event.deltaY < 0 ? 1.08 : 1 / 1.08) }));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!ready) return;
    const arrowSteps: Record<string, Point> = {
      ArrowLeft: { x: -0.025, y: 0 }, ArrowRight: { x: 0.025, y: 0 },
      ArrowUp: { x: 0, y: -0.025 }, ArrowDown: { x: 0, y: 0.025 },
    };
    const delta = arrowSteps[event.key];
    if (delta) {
      event.preventDefault();
      changeView(prev => ({ ...prev, x: prev.x + delta.x, y: prev.y + delta.y }));
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault();changeView(prev => ({ ...prev, zoom: prev.zoom + 0.1 }));
    } else if (event.key === '-') {
      event.preventDefault();changeView(prev => ({ ...prev, zoom: prev.zoom - 0.1 }));
    }
  };
  const apply = () => {
    if (!ready || !imageRef.current) return;
    try {
      setError('');
      onApply(renderProfileCrop(imageRef.current, view));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not crop this picture.');
    }
  };

  return (
    <section className="sterling-photo-cropper" aria-label="Crop profile picture">
      <h2>Crop profile picture</h2>
      <p>Drag to position. Pinch or use Zoom to resize. The circle shows exactly how your profile picture will appear.</p>
      <div className="sterling-crop-layout">
        <div className="sterling-crop-stage"
          ref={viewportRef} role="group" aria-label="Drag image to position profile picture"
          tabIndex={ready ? 0 : -1}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd}
          onLostPointerCapture={event => activePointers.current.delete(event.pointerId)}
          onWheel={onWheel} onKeyDown={onKeyDown}>
          {src && <img ref={imageRef} src={src} draggable={false} alt=""
            className="sterling-crop-image" style={imageStyle}
            onLoad={event => {
              const image = event.currentTarget;
              if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > 40000000) {
                setError('Picture dimensions are unsupported. Choose a smaller image.');
              } else {
                setSize({ width: image.naturalWidth, height: image.naturalHeight });
                setError('');
              }
            }}
            onError={() => setError('Could not load this image.')} />}
          <div className="sterling-crop-dimmer" aria-hidden="true" />
          <div className="sterling-crop-grid" aria-hidden="true">
            <i className="vertical first" /><i className="vertical second" />
            <i className="horizontal first" /><i className="horizontal second" />
          </div>
          <div className="sterling-crop-outline" aria-hidden="true" />
        </div>
        <div className="sterling-crop-preview-wrap">
          <div className="sterling-crop-preview" role="img" aria-label="Circular profile preview">
            {ready && <img src={src} alt="" draggable={false} style={imageStyle} />}
          </div>
          <span>Profile preview</span>
        </div>
      </div>
      <label className="sterling-crop-zoom">
        <span>Zoom <output>{view.zoom.toFixed(2)}×</output></span>
        <input type="range" aria-label="Profile picture zoom" min={1} max={3} step={0.01}
          value={view.zoom} disabled={!ready}
          onChange={event => changeView(prev => ({ ...prev, zoom: Number(event.target.value) }))} />
      </label>
      <div className="sterling-crop-actions">
        <button type="button" onClick={() => changeView(() => INITIAL_CROP)} disabled={!ready}>Reset</button>
        <button type="button" onClick={onCancel}>Cancel</button>
        <button type="button" className="sterling-crop-use" onClick={apply} disabled={!ready}>Use picture</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
