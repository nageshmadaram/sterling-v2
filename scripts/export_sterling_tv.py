"""Export the deployed TV as a self-contained, styled standalone document.

Run `npm --prefix frontend run media:styles` first. Shared UI changes belong
in frontend/tv-mobile and are embedded by embed_tv_mobile.py before export.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'frontend/public'

def export():
    html = (PUBLIC / 'tv/index.html').read_text()
    html = html.replace("  <script>if(window.top === window.self)window.location.replace('/');</script>\n", '')
    def inline(match):
        path = PUBLIC / match.group(1).lstrip('/')
        if not path.is_file():
            raise RuntimeError('Compile media assets first: npm --prefix frontend run media:styles')
        return '<style>\n' + path.read_text() + '\n</style>'
    html = re.sub(r'<link rel="stylesheet" href="(/(?:media|tv)/[^\"]+\.css)">', inline, html)
    hls=(PUBLIC / 'media/hls.min.js').read_text().replace('</script', '<\\/script')
    html=html.replace('<script src="/media/hls.min.js"></script>', '<script>\n'+hls+'\n</script>')
    artwork=(PUBLIC / 'media/artwork.js').read_text().replace('</script','<\\/script')
    html=html.replace('<script src="/media/artwork.js"></script>','<script>\n'+artwork+'\n</script>')
    comfort = (PUBLIC / 'media/comfort.js').read_text().replace('</script', '<\\/script')
    html = html.replace('<script defer src="/media/comfort.js"></script>', '<script>\n' + comfort + '\n</script>')
    (ROOT / 'sterling-iptv/sterling-iptv.html').write_text(html)

if __name__ == '__main__':
    export()
