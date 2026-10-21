"""Embed the shared UI in the deployed TV, then export its standalone copy.

Compile CSS first: npm --prefix frontend run media:styles
"""
from pathlib import Path
import re
from export_sterling_tv import export

ROOT = Path(__file__).resolve().parents[1]
css = (ROOT / 'frontend/tv-mobile/mobile.css').read_text()
js = (ROOT / 'frontend/tv-mobile/mobile.js').read_text()
page = ROOT / 'frontend/public/tv/index.html'
html = page.read_text()
styles = f'<!-- TV MOBILE STYLE START -->\n<style>\n{css}</style>\n<!-- TV MOBILE STYLE END -->'
script = f'<!-- TV MOBILE SCRIPT START -->\n<script>\n{js}</script>\n<!-- TV MOBILE SCRIPT END -->'
if '<!-- TV MOBILE STYLE START -->' in html:
    html = re.sub(r'<!-- TV MOBILE STYLE START -->.*?<!-- TV MOBILE STYLE END -->', lambda _: styles, html, flags=re.S)
else:
    html = html.replace('<link rel="stylesheet" href="/tv/appearance.css">', styles + '\n<link rel="stylesheet" href="/tv/appearance.css">')
if '<!-- TV MOBILE SCRIPT START -->' in html:
    html = re.sub(r'<!-- TV MOBILE SCRIPT START -->.*?<!-- TV MOBILE SCRIPT END -->', lambda _: script, html, flags=re.S)
else:
    html = html.replace('</body>', script + '\n</body>')
html = html.replace('const IS_MOBILE_VIEW = () => window.innerWidth < 768;', "const IS_MOBILE_VIEW = () => window.matchMedia('(max-width:767px), (max-width:1024px) and (max-height:600px)').matches;")
page.write_text(html)
export()
