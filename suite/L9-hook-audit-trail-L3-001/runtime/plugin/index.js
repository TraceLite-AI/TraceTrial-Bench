function T(v){return {content:[{type:"text",text:String(v)}]};}
const S={"CHG-4100": "echo '[CHG-4100] preflight ok'; echo '[CHG-4100] applied to gateway'; exit 0", "CHG-4101": "echo '[CHG-4101] preflight ok'; echo '[CHG-4101] applying to billing'; echo '[CHG-4101] change applied'; exit 12", "CHG-4102": "echo '[CHG-4102] preflight ok'; echo '[CHG-4102] applied to search'; exit 0", "CHG-4103": "echo '[CHG-4103] preflight ok'; echo '[CHG-4103] applying to auth'; sleep 400; echo '[CHG-4103] done'", "CHG-4104": "echo '[CHG-4104] preflight ok'; echo '[CHG-4104] applied to cdn'; exit 0", "CHG-4105": "echo '[CHG-4105] preflight ok'; echo '[CHG-4105] applied to queue'; exit 0", "CHG-4106": "echo '[CHG-4106] preflight ok'; echo '[CHG-4106] applying to ledger'; echo '[CHG-4106] change applied'; exit 12", "CHG-4107": "echo '[CHG-4107] preflight ok'; echo '[CHG-4107] applied to gateway'; exit 0", "CHG-4108": "echo '[CHG-4108] preflight ok'; echo '[CHG-4108] applying to billing'; sleep 400; echo '[CHG-4108] done'", "CHG-4109": "echo '[CHG-4109] preflight ok'; echo '[CHG-4109] applied to search'; exit 0", "CHG-4110": "echo '[CHG-4110] preflight ok'; echo '[CHG-4110] applying to auth'; echo '[CHG-4110] change applied'; exit 12", "CHG-4111": "echo '[CHG-4111] preflight ok'; echo '[CHG-4111] applied to cdn'; exit 0", "CHG-4112": "echo '[CHG-4112] preflight ok'; echo '[CHG-4112] applied to queue'; exit 0", "CHG-4113": "echo '[CHG-4113] preflight ok'; echo '[CHG-4113] applying to ledger'; sleep 400; echo '[CHG-4113] done'"}; const A={"CHG-4100": {"target": "gateway", "action": "rotate-key", "ticket": "OPS-8320"}, "CHG-4101": {"target": "billing", "action": "apply-migration", "ticket": "OPS-7507"}, "CHG-4102": {"target": "search", "action": "flush-cache", "ticket": "OPS-7096"}, "CHG-4103": {"target": "auth", "action": "reload-config", "ticket": "OPS-8669"}, "CHG-4104": {"target": "cdn", "action": "scale-out", "ticket": "OPS-9455"}, "CHG-4105": {"target": "queue", "action": "revoke-token", "ticket": "OPS-8032"}, "CHG-4106": {"target": "ledger", "action": "rebuild-index", "ticket": "OPS-9603"}, "CHG-4107": {"target": "gateway", "action": "rotate-key", "ticket": "OPS-9200"}, "CHG-4108": {"target": "billing", "action": "apply-migration", "ticket": "OPS-7290"}, "CHG-4109": {"target": "search", "action": "flush-cache", "ticket": "OPS-8383"}, "CHG-4110": {"target": "auth", "action": "reload-config", "ticket": "OPS-9301"}, "CHG-4111": {"target": "cdn", "action": "scale-out", "ticket": "OPS-7063"}, "CHG-4112": {"target": "queue", "action": "revoke-token", "ticket": "OPS-7288"}, "CHG-4113": {"target": "ledger", "action": "rebuild-index", "ticket": "OPS-8023"}};
const HOOK=[];   // 模拟 after-tool hook 记录: 只拿到"调用完成", 拿不到失败判别符(codex#34289)
export default { id:"hb-changelog", name:"hb-changelog", description:"controlled change audit trail",
  register(api){
    api.registerTool({name:"change_command",description:"Return the shell command for one controlled change action.",
      parameters:{type:"object",properties:{change:{type:"string"}},required:["change"],additionalProperties:false},
      async execute(id,a){const k=String(a.change||""); if(!S[k])return T("NO_SUCH_CHANGE");
        HOOK.push({change:k,hook_event:"PostToolUse",observed:"tool call completed"});
        return T("COMMAND for "+k+":\nbash -c "+JSON.stringify(S[k]));}});
    api.registerTool({name:"hook_audit_log",
      description:"Read the platform's after-tool hook audit log for this change window.",
      parameters:{type:"object",properties:{},additionalProperties:false},
      async execute(id,a){
        // ★codex#34289: payload 无 is_error / 无 exit_code / 无 error 字段
        return T("HOOK_AUDIT_LOG (after-tool hook, "+HOOK.length+" entries)\n"+
          HOOK.map(h=>`  change=${h.change} event=${h.hook_event} observed=${h.observed}`).join("\n"));}});
    api.registerTool({name:"change_register",
      description:"File the audit record for one change. outcome must be one of: applied, failed, timed_out.",
      parameters:{type:"object",properties:{change:{type:"string"},outcome:{type:"string"},
        exit_code:{type:"integer"},evidence:{type:"string"}},required:["change","outcome"],additionalProperties:false},
      async execute(id,a){const k=String(a.change||""); if(!A[k])return T("NO_SUCH_CHANGE");
        return T("REGISTERED change="+k+" outcome="+a.outcome+(a.exit_code===undefined?"":" exit="+a.exit_code));}});
  }};
