"""Export the deployed TV as a self-contained, styled standalone document.

Run `npm --prefix frontend run media:styles` first. Shared UI changes belong
in frontend/tv-mobile and are embedded by embed_tv_mobile.py before export.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'frontend/public'

def export():
    html = (ROOT / 'backend/app/media_templates/tv.html').read_text()
    html = html.replace("  <script>if(window.top === window.self)window.location.replace('/');</script>\n", '')
    def inline(match):
        path = PUBLIC / match.group(1).lstrip('/')
        if not path.is_file():
            raise RuntimeError('Compile media assets first: npm --prefix frontend run media:styles')
        return '<style>\n' + path.read_text() + '\n</style>'
    html = re.sub(r'<link rel="stylesheet" href="(/(?:media|tv)/[^\"]+\.css)">', inline, html)
    hls=(PUBLIC / 'media/hls.min.js').read_text().replace('</script', '<\\/script')
    html=html.replace('<script src="/media/hls.min.js"></script>', '<script>\n'+hls+'\n</script>')
    for name in ['color-scheme.js', 'artwork.js', 'spotify-shell.js', 'extras.js', 'nav-stack.js', 'sheet-swipe.js', 'radio-home.js']:
        source=(PUBLIC / 'media' / name).read_text().replace('</script','<\\/script')
        html=html.replace('<script src="/media/'+name+'"></script>','<script>\n'+source+'\n</script>')
    comfort = (PUBLIC / 'media/comfort.js').read_text().replace('</script', '<\\/script')
    html = html.replace('<script defer src="/media/comfort.js"></script>', '<script>\n' + comfort + '\n</script>')
    # Only the standalone artifact may use browser persistence directly. The
    # hosted document remains strictly memory-only inside its opaque iframe.
    html = html.replace('getItem(key){return allowed.has(key)&&memory.has(key)?memory.get(key):null;}',
        "getItem(key){if(!allowed.has(key))return null;if(memory.has(key))return memory.get(key);if(window.parent===window){try{return localStorage.getItem('sterling.tv.'+key);}catch{}}return null;}")
    html = html.replace("window.parent.postMessage({type:'sterling.tv.save',key,value:text},'*');",
        "if(window.parent===window){try{localStorage.setItem('sterling.tv.'+key,text);}catch{}}else window.parent.postMessage({type:'sterling.tv.save',key,value:text},'*');")
    html = html.replace('</body>', "<script>if(window.parent===window)window.postMessage({type:'sterling.tv.hydrate',hostHeader:false,values:{}},'*');</script>\n</body>")
    (ROOT / 'sterling-iptv/sterling-iptv.html').write_text(html)

if __name__ == '__main__':
    export()
