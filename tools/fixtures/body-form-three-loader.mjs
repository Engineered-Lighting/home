// Node tests use the exact browser-vendored Three, without an npm Three install.
export async function resolve(specifier,context,nextResolve){
  const vendor=new URL('../../app/src/vendor/three-0.180.0/',import.meta.url).href;
  const result=specifier==='three'?{url:vendor+'three.module.js',shortCircuit:true}:await nextResolve(specifier,context);
  // Node 20 does not infer ESM for these browser .js files in a CommonJS repo.
  return result.url.startsWith(vendor)?{...result,format:'module'}:result;
}
