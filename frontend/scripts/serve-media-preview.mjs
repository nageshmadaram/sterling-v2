// Loopback-only static server for isolated media browser tests.
import http from 'node:http';
import {createReadStream} from 'node:fs';
import {stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../public');
const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.json':'application/json'};
const server=http.createServer(async(req,res)=>{
  try{
    const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    if(pathname==='/'){res.writeHead(200,{'Content-Type':'text/plain'});res.end('Media preview ready');return;}
    const file=path.resolve(root,'.'+pathname);
    if(!file.startsWith(root+path.sep)||!(await stat(file)).isFile()){res.writeHead(404);res.end();return;}
    res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
    createReadStream(file).on('error',()=>res.destroy()).pipe(res);
  }catch{res.writeHead(404);res.end();}
});
server.listen(5188,'127.0.0.1',()=>console.log('Media preview: http://127.0.0.1:5188'));
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));
process.on('SIGINT',()=>server.close(()=>process.exit(0)));
