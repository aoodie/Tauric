import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {patchRuntime,patches} from '../patch_runtime_dependencies.mjs';

function fixture() {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tauric-runtime-patches-'));
  function pkg(name,manifest,text) {
    const folder=path.join(root,'node_modules',name);
    fs.mkdirSync(folder,{recursive:true});
    fs.writeFileSync(path.join(folder,'package.json'),JSON.stringify(manifest));
    fs.writeFileSync(path.join(folder,'index.js'),text);
    return folder;
  }
  pkg('openclaw',{name:'openclaw',version:'2026.9.7'},'core unchanged');
  for(const p of patches) {
    pkg(p.target,{name:p.name,version:p.from},'old vulnerable implementation');
    pkg(p.replacement,{name:p.name,version:p.to},'patched published implementation');
  }
  return root;
}

test('locked replacements update real bundled code and retain a reproducible inventory',()=>{
  const root=fixture();
  try {
    const result=patchRuntime(root);
    for(const p of patches) {
      const installed=path.join(root,'node_modules',p.target);
      assert.equal(JSON.parse(fs.readFileSync(path.join(installed,'package.json'))).version,p.to);
      assert.equal(fs.readFileSync(path.join(installed,'index.js'),'utf8'),'patched published implementation');
    }
    assert.deepEqual(patchRuntime(root),result);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root,'runtime-patches.json'))),result);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('unexpected upstream versions fail before changing any bundled implementation',()=>{
  const root=fixture();
  try {
    const first=path.join(root,'node_modules',patches[0].target,'package.json');
    fs.writeFileSync(first,JSON.stringify({name:'undici',version:'0.0.0-unreviewed'}));
    assert.throws(()=>patchRuntime(root),/identity/);
    assert.equal(fs.readFileSync(path.join(root,'node_modules',patches[1].target,'index.js'),'utf8'),'old vulnerable implementation');
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('a replacement symlink cannot import a host file into the privileged build',()=>{
  const root=fixture();
  try {
    fs.symlinkSync('/etc/passwd',path.join(root,'node_modules',patches[0].replacement,'outside'));
    assert.throws(()=>patchRuntime(root),/symlinks/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
