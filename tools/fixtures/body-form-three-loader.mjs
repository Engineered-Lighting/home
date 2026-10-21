// Node tests use the exact browser-vendored Three, without an npm Three install.
export async function resolve(specifier,context,nextResolve){
  if(specifier==='three')return {url:new URL('../../app/src/vendor/three-0.180.0/three.module.js',import.meta.url).href,shortCircuit:true};
  return nextResolve(specifier,context);
}
