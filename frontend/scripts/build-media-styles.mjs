import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync,copyFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const output=path.join(root,'public/media');
mkdirSync(output,{recursive:true});
execFileSync(process.execPath,[path.join(root,'node_modules/tailwindcss/lib/cli.js'),'-c','tailwind.media.config.mjs','-i','media/utilities.css','-o','public/media/interface.css','--minify'],{cwd:root,stdio:'inherit'});
// Embed font files: opaque-origin media sandboxes cannot rely on same-origin
// font CORS. Data URLs keep fonts available without widening iframe access.
const embed=(file,mime)=>`data:${mime};base64,${readFileSync(file).toString('base64')}`;
const fa=path.join(root,'node_modules/@fortawesome/fontawesome-free');
let icons=['fontawesome','solid','regular','brands'].map(name=>readFileSync(path.join(fa,`css/${name}.min.css`),'utf8')).join('\n');
icons=icons.replace(/,url\([^)]*\.ttf\) format\("truetype"\)/g,'');
icons=icons.replace(/url\(\.\.\/webfonts\/([^\)]+)\)/g,(_,name)=>`url(${embed(path.join(fa,'webfonts',name),name.endsWith('.woff2')?'font/woff2':'font/ttf')})`);
writeFileSync(path.join(output,'icons.css'),icons);
let fonts='';
for(const family of ['inter','roboto']) {
  for(const weight of [400,500,600,700,800]) {
    const font=path.join(root,`node_modules/@fontsource/${family}/files/${family}-latin-${weight}-normal.woff2`);
    fonts+=`@font-face{font-family:${family==='inter'?'Inter':'Roboto'};font-style:normal;font-weight:${weight};font-display:swap;src:url(${embed(font,'font/woff2')}) format('woff2')}\n`;
  }
}
writeFileSync(path.join(output,'fonts.css'),fonts);

copyFileSync(path.join(root,'node_modules/hls.js/dist/hls.min.js'),path.join(output,'hls.min.js'));
