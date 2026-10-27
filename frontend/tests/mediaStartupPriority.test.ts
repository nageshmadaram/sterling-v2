import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const root = resolve(process.cwd(), '..');
const tv = readFileSync(resolve(root, 'backend/app/media_templates/tv.html'), 'utf8');
const radio = readFileSync(resolve(root, 'backend/app/media_templates/radio.html'), 'utf8');

describe('hosted media startup priority', () => {
  it('starts TV streaming before rebuilding the large related-channel rail', () => {
    const start = tv.indexOf('function playStream(channel, stage = 0)');
    const end = tv.indexOf('function startHlsPlayback(', start);
    const player = tv.slice(start, end);
    expect(player).toContain('requestAnimationFrame(() => {');
    expect(player).toContain('if (playbackGeneration === queueGeneration) renderUpNextQueue(true);');
    expect(player).toContain('startHlsPlayback(channel, video, spinner);');
    expect(tv).toContain('video.readyState < 2 && !tvUserPaused');
  });

  it('does not block first radio sound on the remote station catalog', () => {
    expect(radio).toContain('if (radioBooted && radioCatalog) void radioCatalog.load();');
    expect(radio).toContain('liveSyncDurationCount: 2');
    expect(radio).toContain('audio.paused || audio.readyState < 2');
    expect(radio).toContain('Browser autoplay policy is not a broken stream');
  });
});
