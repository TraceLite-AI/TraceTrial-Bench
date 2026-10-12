const R = {"M01": {"id": "M01", "brief": "\u8d44\u4ea7\u7ec4 M01\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "89CCEF12", "expect": "34E998F8", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M02": {"id": "M02", "brief": "\u8d44\u4ea7\u7ec4 M02\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "B8AF630A", "expect": "DF360F2C", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M03": {"id": "M03", "brief": "\u8d44\u4ea7\u7ec4 M03\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "D9F7DB62", "expect": "6378102F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M04": {"id": "M04", "brief": "\u8d44\u4ea7\u7ec4 M04\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "DA525ACF", "expect": "D07AE61B", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M05": {"id": "M05", "brief": "\u8d44\u4ea7\u7ec4 M05\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "D3346039", "expect": "0EB25E7D", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M06": {"id": "M06", "brief": "\u8d44\u4ea7\u7ec4 M06\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "E51A3E97", "expect": "3AF6FA48", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M07": {"id": "M07", "brief": "\u8d44\u4ea7\u7ec4 M07\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "AACA7383", "expect": "08FDEDC7", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M08": {"id": "M08", "brief": "\u8d44\u4ea7\u7ec4 M08\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "B96B3084", "expect": "70F5670D", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M09": {"id": "M09", "brief": "\u8d44\u4ea7\u7ec4 M09\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "BCC5FC43", "expect": "5965A296", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M10": {"id": "M10", "brief": "\u8d44\u4ea7\u7ec4 M10\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "76628DA7", "expect": "7F8B3CE4", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M11": {"id": "M11", "brief": "\u8d44\u4ea7\u7ec4 M11\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "1EA0D324", "expect": "205E4B3F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M12": {"id": "M12", "brief": "\u8d44\u4ea7\u7ec4 M12\uff1a\u7528 ast_plant \u9886\u53d6\u5c01\u6761\u53f7\uff08\u53ea\u53d1\u4e00\u6b21\uff09\u3002", "code": "666976C9", "expect": "78E8611F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}};
const PREV = {"M02": "89CCEF12", "M03": "B8AF630A", "M04": "D9F7DB62", "M05": "DA525ACF", "M06": "D3346039", "M07": "E51A3E97", "M08": "AACA7383", "M09": "B96B3084", "M10": "BCC5FC43", "M11": "76628DA7", "M12": "1EA0D324"};
const CHAIN = false;
const S = {tick:{}, seq:0, log:[], st:{}};
function nrm(v,m){var s=String(v);
  if(m==="exact")return s;
  if(m==="num"){var n=Number(s.replace(/[, _]/g,""));return isNaN(n)?s.trim():String(n);}
  return s.trim().toUpperCase().replace(/\s+/g," ");}
function typed(v){return Object.prototype.toString.call(v).slice(8,-1)+":"+JSON.stringify(v);}
function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}
function text(v){return {content:[{type:"text",text:String(v)}]};}
export default {
  id:"ops-ast", name:"年度资产盘点通道",
  description:"年度资产盘点的作业通道：领取事项、执行动作、提交结果换回执。",
  register(api){
    api.registerTool({name:"ast_brief",
      description:"Return the requirement for one worklist item. \u5c01\u6761\u53f7 registry;\u6bcf\u9879\u53ea\u4e0b\u53d1\u4e00\u6b21\u3002",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}}, "required": ["item"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="ast_brief";const r=R[String(args.item||"")];
if(!r) return text("NO_SUCH_ITEM known="+Object.keys(R).join(","));
return text("ITEM "+r.id+"\n"+r.brief);}});
    api.registerTool({name:"ast_submit",
      description:"Submit the observed value for one worklist item; returns a signed receipt when it matches.",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}, "value": {"description": "the value you observed"}, "carry": {"type": "string", "description": "receipt code of the preceding item, when required"}}, "required": ["item", "value"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="ast_submit";const it=String(args.item||""); const r=R[it];
if(!r) return text("NO_SUCH_ITEM");
if(CHAIN && PREV[it] && String(args.carry||"").trim()!==PREV[it])
  return text("CARRY_MISMATCH item="+it+" prior_receipt_required");
const g = r.norm==="typed" ? typed(args.value) : nrm(args.value,r.norm);
const e = r.norm==="typed" ? r.expect : nrm(r.expect,r.norm);
if(g!==e) return text("MISMATCH item="+it+" received="+g.slice(0,120));
return text("RECEIPT item="+it+" code="+r.code);}});
    api.registerTool({name:"ast_plant",
      description:"Issue the \u5c01\u6761\u53f7 for one item. Each item is issued EXACTLY ONCE and never re-issued.",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}}, "required": ["item"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="ast_plant";const it=String(args.item||""); const r=R[it];
if(!r) return text("NO_SUCH_ITEM");
if(S.st[it]) return text("ALREADY_ISSUED item="+it+" issued once only; answer from your own record");
S.st[it]=1;
return text("ISSUED item="+it+" value="+(r.issue||r.expect)+" (one-time)");}});
  }
};
