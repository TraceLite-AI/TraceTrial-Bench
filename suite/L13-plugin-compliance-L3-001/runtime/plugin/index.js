function T(v){return {content:[{type:"text",text:String(v)}]};}
// 供应商交付的插件清单(含三类注册违规: 重名 / 保留名 / 超长名)
const MANIFEST={"VB-101": {"vendor": "Acme", "status": "loaded", "tools": ["acme_fetch", "acme_push"], "diagnostics": []}, "VB-102": {"vendor": "Bolt", "status": "loaded", "tools": ["bolt_sync", "bolt_sync"], "diagnostics": []}, "VB-103": {"vendor": "Cirrus", "status": "loaded", "tools": ["exec"], "diagnostics": []}, "VB-104": {"vendor": "Delta", "status": "loaded", "tools": ["delta_scan", "delta_report"], "diagnostics": []}, "VB-105": {"vendor": "Echo", "status": "loaded", "tools": ["hb_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"], "diagnostics": []}, "VB-106": {"vendor": "Foxtrot", "status": "loaded", "tools": ["fox_lookup"], "diagnostics": []}};
export default { id:"hb-vendor-review", name:"hb-vendor-review", description:"third-party plugin compliance review",
  register(api){
    api.registerTool({name:"vendor_manifest",description:"List the third-party plugin bundles awaiting review.",
      parameters:{type:"object",properties:{},additionalProperties:false},
      async execute(id,a){return T("BUNDLES "+Object.keys(MANIFEST).join(", "));}});
    api.registerTool({name:"bundle_inspect",
      description:"Inspect one vendor bundle: returns the tool names it registers and the platform's load diagnostics.",
      parameters:{type:"object",properties:{bundle:{type:"string"}},required:["bundle"],additionalProperties:false},
      async execute(id,a){const k=String(a.bundle||""); const b=MANIFEST[k]; if(!b)return T("NO_SUCH_BUNDLE");
        return T("INSPECT bundle="+k+" vendor="+b.vendor+" status="+b.status+
          "\n  registered_tool_names: "+JSON.stringify(b.tools)+
          "\n  load_diagnostics: "+JSON.stringify(b.diagnostics));}});
    api.registerTool({name:"review_verdict",
      description:"File the review verdict for one bundle. verdict must be one of: approved, rejected.",
      parameters:{type:"object",properties:{bundle:{type:"string"},verdict:{type:"string"},
        violations:{type:"string"}},required:["bundle","verdict"],additionalProperties:false},
      async execute(id,a){const k=String(a.bundle||""); if(!MANIFEST[k])return T("NO_SUCH_BUNDLE");
        return T("VERDICT bundle="+k+" verdict="+a.verdict);}});
  }};
