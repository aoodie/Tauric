// Reproducible security backports for dependencies bundled inside upstream tarballs.
// npm overrides cannot replace inBundle entries. Preserve the upstream core pin
// and copy only separately locked, integrity-checked packages into those slots.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const patches = [
  {target:'openclaw/node_modules/undici',replacement:'tauric-undici8',name:'undici',from:'8.10.2',to:'8.12.2'},
  {target:'npm/node_modules/undici',replacement:'tauric-undici6',name:'undici',from:'6.28.0',to:'6.29.1'},
  {target:'npm/node_modules/brace-expansion',replacement:'tauric-braces5',name:'brace-expansion',from:'5.0.9',to:'5.0.12'},
];

function inventory(root) {
  const rows=[];
  function walk(folder) {
    for(const name of fs.readdirSync(folder).sort()) {
      const p=path.join(folder,name),s=fs.lstatSync(p);
      if(s.isSymbolicLink()) throw new Error('Runtime replacement packages cannot contain symlinks');
      if(s.isDirectory()) walk(p);
      else if(s.isFile()) rows.push([path.relative(root,p),crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')]);
      else throw new Error('Runtime replacement packages require regular files');
    }
  }
  walk(root);
  return crypto.createHash('sha256').update(JSON.stringify(rows)).digest('hex');
}

export function patchRuntime(root) {
  const modules=fs.realpathSync(path.join(root,'node_modules'));
  const core=JSON.parse(fs.readFileSync(path.join(modules,'openclaw/package.json'),'utf8'));
  if(core.version!=='2026.9.7') throw new Error('Review the security backports before changing the pinned OpenClaw core');
  const plan=patches.map(p=>{
    const target=path.join(modules,p.target),replacement=path.join(modules,p.replacement);
    for(const folder of [target,replacement]) {
      if(fs.lstatSync(folder).isSymbolicLink() || !fs.realpathSync(folder).startsWith(modules+path.sep)) throw new Error('Runtime patch cannot escape the installed dependency tree');
    }
    const before=JSON.parse(fs.readFileSync(path.join(target,'package.json'),'utf8'));
    const after=JSON.parse(fs.readFileSync(path.join(replacement,'package.json'),'utf8'));
    if(before.name!==p.name || ![p.from,p.to].includes(before.version) || after.name!==p.name || after.version!==p.to) throw new Error('Unexpected bundled or replacement package identity');
    return {...p,target,replacement,tree_sha256:inventory(replacement)};
  });
  for(const p of plan) {
    const pending=p.target+'.tauric-pending',old=p.target+'.tauric-before';
    if(fs.existsSync(pending)||fs.existsSync(old)) throw new Error('An interrupted dependency patch needs reconciliation');
    fs.cpSync(p.replacement,pending,{recursive:true});
    if(inventory(pending)!==p.tree_sha256) throw new Error('Replacement package changed while copying');
    // Overlay image layers cannot rename a lower-layer directory (EXDEV).
    // Keep a complete original until the verified replacement is published.
    fs.cpSync(p.target,old,{recursive:true});
    fs.rmSync(p.target,{recursive:true});
    fs.renameSync(pending,p.target);
    fs.rmSync(old,{recursive:true});
  }
  const result={upstream_core:'2026.9.7',assembly:'tauric_security_backports_v1',
    patches:plan.map(({target,replacement,...p})=>p)};
  fs.writeFileSync(path.join(root,'runtime-patches.json'),JSON.stringify(result,null,2)+'\n');
  return result;
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  console.log(JSON.stringify(patchRuntime(path.resolve(process.argv[2]??'openclaw'))));
}
