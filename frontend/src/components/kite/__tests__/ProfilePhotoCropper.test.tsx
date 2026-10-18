import { describe, expect, it } from 'vitest';
import { constrainCrop, cropSourceRect, INITIAL_CROP } from '../ProfilePhotoCropper';

describe('profile photo crop geometry', () => {
  it('starts centered and fits the source image to the circular viewport', () => {
    expect(cropSourceRect(800, 400, INITIAL_CROP)).toEqual({
      sx: 200, sy: 0, sw: 400, sh: 400,
    });
    expect(cropSourceRect(400, 800, INITIAL_CROP)).toEqual({
      sx: 0, sy: 200, sw: 400, sh: 400,
    });
  });

  it('updates the saved source rectangle when zooming or panning', () => {
    expect(cropSourceRect(800, 400, { zoom: 2, x: 0, y: 0 })).toEqual({
      sx: 300, sy: 100, sw: 200, sh: 200,
    });
    expect(cropSourceRect(800, 400, { zoom: 1, x: 0.5, y: 0 })).toEqual({
      sx: 0, sy: 0, sw: 400, sh: 400,
    });
  });

  it('clamps pan and zoom to avoid blank edges in the avatar', () => {
    expect(constrainCrop(800, 400, { zoom: 0, x: 99, y: 99 })).toEqual({
      zoom: 1, x: 0.5, y: 0,
    });
    expect(constrainCrop(800, 400, { zoom: 4, x: -99, y: -99 })).toEqual({
      zoom: 3, x: -2.5, y: -1,
    });
    expect(constrainCrop(500, 500, { zoom: 1, x: 1, y: -1 })).toEqual(INITIAL_CROP);
  });

  it('keeps the cropped rectangle entirely inside the source at every edge', () => {
    for (const width of [320, 500, 1600]) {
      for (const height of [320, 1000, 1900]) {
        for (const zoom of [1, 1.25, 2, 3]) {
          for (const x of [-5, 0, 5]) {
            for (const y of [-5, 0, 5]) {
              const { sx, sy, sw, sh } = cropSourceRect(width, height, { zoom, x, y });
              expect(sx).toBeGreaterThanOrEqual(-1e-9);
              expect(sy).toBeGreaterThanOrEqual(-1e-9);
              expect(sx + sw).toBeLessThanOrEqual(width + 1e-9);
              expect(sy + sh).toBeLessThanOrEqual(height + 1e-9);
            }
          }
        }
      }
    }
  });
});
