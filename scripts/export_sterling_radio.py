"""Export canonical Radio with local assets embedded. Build media styles first."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'frontend/public'


def export():
    html = (PUBLIC / 'radio/index.html').read_text()

    def inline(match):
        return '<style>\n' + (PUBLIC / match.group(1).lstrip('/')).read_text() + '\n</style>'

    html = re.sub(r'<link rel="stylesheet" href="(/(?:media|radio)/[^\"]+\.css)"\s*/?>', inline, html)
    hls = (PUBLIC / 'media/hls.min.js').read_text().replace('</script', '<\\/script')
    html = html.replace('<script src="/media/hls.min.js"></script>', '<script>\n' + hls + '\n</script>')
    (ROOT / 'sterling-radio/sterling-radio.html').write_text(html)


if __name__ == '__main__':
    export()
