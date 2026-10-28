import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {publicStaticFile} from '../public_static_asset.mjs';

function fixture(t) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'sterling-static-'));
  t.after(() => fs.rmSync(parent, {recursive:true, force:true}));
  const publicRoot = path.join(parent, 'releases', 'a'.repeat(40));
  fs.mkdirSync(path.join(publicRoot,'assets'), {recursive:true});
  fs.writeFileSync(path.join(publicRoot,'index.html'),'<html>sterling</html>');
  fs.writeFileSync(path.join(publicRoot,'assets','app.js'),'PUBLIC JS');
  fs.writeFileSync(path.join(publicRoot,'.env'),'VERY PRIVATE');
  fs.mkdirSync(path.join(publicRoot,'private'),{recursive:true});
  fs.writeFileSync(path.join(publicRoot,'private','.token'),'PRIVATE TOKEN');
  const outside = path.join(parent, 'outside-secret');
  fs.writeFileSync(outside, 'OUTSIDE SECRET');
  const pointer = path.join(parent, 'published');
  fs.symlinkSync(publicRoot,pointer,'dir');
  return {publicRoot,pointer,outside};
}
test('serves genuine static assets and SPA routes from active release symlink',t=>{
  const {pointer}=fixture(t);
  assert.equal(fs.readFileSync(publicStaticFile(pointer,'/assets/app.js'),'utf8'),'PUBLIC JS');
  assert.equal(fs.readFileSync(publicStaticFile(pointer,'/settings'),'utf8'),'<html>sterling</html>');
  assert.equal(fs.readFileSync(publicStaticFile(pointer,'/'),'utf8'),'<html>sterling</html>');
});
test('existing dotfiles cannot be served',t=>{
  const {pointer}=fixture(t);
  for(const item of ['/.env','/%2eenv','/private/.token','/private/%2etoken','/.git/config','/.well-known/test'])
    assert.equal(publicStaticFile(pointer,item),null,item);
});
test('symlink targets outside active release cannot be served',t=>{
  const {publicRoot,pointer,outside}=fixture(t);
  fs.symlinkSync(outside,path.join(publicRoot,'assets','secret.txt'));
  fs.symlinkSync(outside,path.join(publicRoot,'assets','untyped'));
  for(const item of ['/assets/secret.txt','/assets/untyped'])
    assert.equal(publicStaticFile(pointer,item),null,item);
});
test('nested folder symlinks cannot escape public build',t=>{
  const {publicRoot,pointer,outside}=fixture(t);
  fs.symlinkSync(path.dirname(outside),path.join(publicRoot,'jump'),'dir');
  assert.equal(publicStaticFile(pointer,'/jump/outside-secret'),null);
});
test('invalid paths and missing assets fail closed, without SPA fallback',t=>{
  const {pointer}=fixture(t);
  for(const item of ['/assets/missing.js','/docs','/redoc','/openapi/v1','/%00','/%E0%A4%A','/..%2Fsecret','/private/.','/back\\slash'])
    assert.equal(publicStaticFile(pointer,item),null,item);
});
test('plain dist remains supported without a symlink',t=>{
  const {publicRoot}=fixture(t);
  assert.equal(fs.readFileSync(publicStaticFile(publicRoot,'/assets/app.js'),'utf8'),'PUBLIC JS');
});
