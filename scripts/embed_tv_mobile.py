"""Embed shared mobile UI assets in both standalone and deployed TV documents."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
css = (ROOT / 'frontend/tv-mobile/mobile.css').read_text()
js = (ROOT / 'frontend/tv-mobile/mobile.js').read_text()
for relative in ('frontend/public/tv/index.html', 'sterling-iptv/sterling-iptv.html'):
    page = ROOT / relative
    html = page.read_text()
    html = re.sub(r'\n<!-- TV MOBILE STYLE START -->.*?<!-- TV MOBILE STYLE END -->\n', '\n', html, flags=re.S)
    html = re.sub(r'\n<!-- TV MOBILE SCRIPT START -->.*?<!-- TV MOBILE SCRIPT END -->\n', '\n', html, flags=re.S)
    html = html.replace('</head>', f'\n<!-- TV MOBILE STYLE START -->\n<style>\n{css}</style>\n<!-- TV MOBILE STYLE END -->\n</head>')
    html = html.replace('</body>', f'\n<!-- TV MOBILE SCRIPT START -->\n<script>\n{js}</script>\n<!-- TV MOBILE SCRIPT END -->\n</body>')
    html = html.replace('const IS_MOBILE_VIEW = () => window.innerWidth < 768;', "const IS_MOBILE_VIEW = () => window.matchMedia('(max-width:767px), (max-width:1024px) and (max-height:600px)').matches;")
    page.write_text(html)
