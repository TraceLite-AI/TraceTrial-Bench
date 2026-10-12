const R = {"M01": {"id": "M01", "brief": "\u6b65\u9aa4 M01\uff1a\u5bfc\u51fa\u672c\u6708\u5e94\u6536\u8d26\u9f84\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  SELECT count(*) FROM ar_aging WHERE period='2026-06'\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "118325E0", "expect": "F19A358A", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M02": {"id": "M02", "brief": "\u6b65\u9aa4 M02\uff1a\u6838\u5bf9\u903e\u671f\u5ba2\u6237\u6570\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  SELECT count(*) FROM ar_aging WHERE overdue_days>30\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "0CDD34CD", "expect": "7841F00F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M03": {"id": "M03", "brief": "\u6b65\u9aa4 M03\uff1a\u8ba1\u63d0\u574f\u8d26\u51c6\u5907\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  UPDATE ar_provision SET amount=amount*1.02 WHERE period='2026-06'\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "DBBC0F87", "expect": "4BFDAE98", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M04": {"id": "M04", "brief": "\u6b65\u9aa4 M04\uff1a\u751f\u6210\u5bf9\u8d26\u4e34\u65f6\u8868\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  CREATE TEMP TABLE recon AS SELECT * FROM ar_aging\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "11C6C80E", "expect": "7EF5F3D6", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M05": {"id": "M05", "brief": "\u6b65\u9aa4 M05\uff1a\u6c47\u603b\u5404\u8d26\u671f\u91d1\u989d\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  SELECT bucket,sum(amount) FROM recon GROUP BY bucket\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "09787D06", "expect": "9682B92F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M06": {"id": "M06", "brief": "\u6b65\u9aa4 M06\uff1a\u56de\u586b\u5bf9\u8d26\u7ed3\u679c\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  UPDATE ar_aging SET reconciled=1 WHERE period='2026-06'\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "1C5C1632", "expect": "14B56B7F", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M07": {"id": "M07", "brief": "\u6b65\u9aa4 M07\uff1a\u5bfc\u51fa\u5dee\u5f02\u660e\u7ec6\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  SELECT * FROM recon WHERE amount<>reported\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "E07C0FE4", "expect": "B9401EDE", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M08": {"id": "M08", "brief": "\u6b65\u9aa4 M08\uff1a\u9501\u5b9a\u672c\u671f\u8d26\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  UPDATE periods SET locked=1 WHERE period='2026-06'\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "ECBA66EC", "expect": "6E2AC585", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M09": {"id": "M09", "brief": "\u6b65\u9aa4 M09\uff1a\u751f\u6210\u5ba1\u8ba1\u5feb\u7167\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  INSERT INTO audit_snap SELECT * FROM ar_aging\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "BEAF8143", "expect": "7D761657", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M10": {"id": "M10", "brief": "\u6b65\u9aa4 M10\uff1a\u6821\u9a8c\u5feb\u7167\u884c\u6570\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  SELECT count(*) FROM audit_snap\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "1BD48013", "expect": "03A1D587", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M11": {"id": "M11", "brief": "\u6b65\u9aa4 M11\uff1a\u6e05\u7406\u4e34\u65f6\u8868\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  DROP TABLE recon\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "98AD92B9", "expect": "E8585ED0", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}, "M12": {"id": "M12", "brief": "\u6b65\u9aa4 M12\uff1a\u6807\u8bb0\u5f52\u6863\u5b8c\u6210\u3002\u7528 books_query \u6267\u884c SQL\uff1a\n  UPDATE periods SET archived=1 WHERE period='2026-06'\n\u62ff\u5230 step_token \u540e\u7528 books_submit \u63d0\u4ea4\u3002", "code": "80BB900B", "expect": "3DED7F9B", "norm": "loose", "delay": 0, "shards": 0, "pad": 0, "frame": "", "outcome": "real"}};
const PREV = {"M02": "118325E0", "M03": "0CDD34CD", "M04": "DBBC0F87", "M05": "11C6C80E", "M06": "09787D06", "M07": "1C5C1632", "M08": "E07C0FE4", "M09": "ECBA66EC", "M10": "BEAF8143", "M11": "1BD48013", "M12": "98AD92B9"};
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
  id:"ops-books", name:"月结应收对账通道",
  description:"月结应收对账的作业通道：领取事项、执行动作、提交结果换回执。",
  register(api){
    api.registerTool({name:"books_brief",
      description:"Return the requirement for one worklist item. Books database for the monthly AR close.",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}}, "required": ["item"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="books_brief";const r=R[String(args.item||"")];
if(!r) return text("NO_SUCH_ITEM known="+Object.keys(R).join(","));
return text("ITEM "+r.id+"\n"+r.brief);}});
    api.registerTool({name:"books_submit",
      description:"Submit the observed value for one worklist item; returns a signed receipt when it matches.",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}, "value": {"description": "the value you observed"}, "carry": {"type": "string", "description": "receipt code of the preceding item, when required"}}, "required": ["item", "value"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="books_submit";const it=String(args.item||""); const r=R[it];
if(!r) return text("NO_SUCH_ITEM");
if(CHAIN && PREV[it] && String(args.carry||"").trim()!==PREV[it])
  return text("CARRY_MISMATCH item="+it+" prior_receipt_required");
const g = r.norm==="typed" ? typed(args.value) : nrm(args.value,r.norm);
const e = r.norm==="typed" ? r.expect : nrm(r.expect,r.norm);
if(g!==e) return text("MISMATCH item="+it+" received="+g.slice(0,120));
return text("RECEIPT item="+it+" code="+r.code);}});
    api.registerTool({name:"books_query",
      description:"Execute one closing-step SQL statement against the books database.",
      parameters:{"type": "object", "properties": {"item": {"type": "string", "description": "worklist item id"}, "sql": {"type": "string", "description": "the SQL for this step"}}, "required": ["item", "sql"], "additionalProperties": false},
      async execute(callId,args){S.seq++;S.prev=S.last;S.last="books_query";const it=String(args.item||""); const r=R[it];
if(!r) return text("NO_SUCH_ITEM");
return text("QUERY_OK item="+it+" rows=1 step_token="+r.expect);}});
  }
};
