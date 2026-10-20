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
html = re.sub(r'\n<!-- TV MOBILE STYLE START -->.*?<!-- TV MOBILE STYLE END -->\n', '\n', html, flags=re.S)
html = re.sub(r'\n<!-- TV MOBILE SCRIPT START -->.*?<!-- TV MOBILE SCRIPT END -->\n', '\n', html, flags=re.S)
styles = f'\n<!-- TV MOBILE STYLE START -->\n<style>\n{css}</style>\n<!-- TV MOBILE STYLE END -->\n'
# Theme appearance follows the structural adapter in the cascade.
anchor = '<link rel="stylesheet" href="/tv/appearance.css">'
html = html.replace(anchor, styles + anchor) if anchor in html else html.replace('</head>', styles + '</head>')
html = html.replace('</body>', f'\n<!-- TV MOBILE SCRIPT START -->\n<script>\n{js}</script>\n<!-- TV MOBILE SCRIPT END -->\n</body>')
html = html.replace('const IS_MOBILE_VIEW = () => window.innerWidth < 768;', "const IS_MOBILE_VIEW = () => window.matchMedia('(max-width:767px), (max-width:1024px) and (max-height:600px)').matches;")
page.write_text(html)
export()
