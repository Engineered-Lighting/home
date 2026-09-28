"use strict";
const fs=require("node:fs"),path=require("node:path");
const root=path.resolve(__dirname,"..");
const output=path.join(root,"app/src/home-agent/preference-review.js");
const source="/* Generated local-only preference review. */\n"+[
  "app/src/home-connection-registry.js","app/src/home-agent/preference-review-controller.js"
].map(file=>fs.readFileSync(path.join(root,file),"utf8")).join(";\n");
if(process.argv.includes("--check")) {
  if(!fs.existsSync(output) || fs.readFileSync(output,"utf8")!==source) throw new Error("preference review bundle is stale");
} else fs.writeFileSync(output,source);
