function T(v){return {content:[{type:"text",text:String(v)}]};}
const S={"01-create_audit_table": "echo '[01] create_audit_table started'; mkdir -p state; echo done > state/create_audit_table.done; echo '[01] complete'; exit 0", "02-backfill_tenant_ids": "echo '[02] backfill_tenant_ids started'; mkdir -p state; echo done > state/backfill_tenant_ids.done; echo '[02] complete'; exit 0", "03-add_index_orders": "echo '[03] add_index_orders started'; mkdir -p state; echo done > state/add_index_orders.done; echo '[03] complete'; exit 0", "04-rebuild_search_index": "echo '[04] rebuild_search_index started'; mkdir -p state; trap 'echo killed > state/rebuild_search_index.killed; exit 143' TERM INT; for i in 1 2 3; do echo \"[04] chunk $i\" >> state/rebuild_search_index.log; sleep 1; done; for i in $(seq 1 300); do sleep 1; done; echo done > state/rebuild_search_index.done; echo '[04] complete'", "05-add_column_status": "echo '[05] add_column_status started'; mkdir -p state; echo done > state/add_column_status.done; echo '[05] complete'; exit 0", "06-migrate_legacy_rows": "echo '[06] migrate_legacy_rows started'; mkdir -p state; echo 'rows_migrated=4120' > state/migrate_legacy_rows.partial; echo '[06] committing'; exit 0", "07-drop_deprecated_view": "echo '[07] drop_deprecated_view started'; mkdir -p state; echo done > state/drop_deprecated_view.done; echo '[07] complete'; exit 0", "08-add_fk_constraints": "echo '[08] add_fk_constraints started'; mkdir -p state; echo done > state/add_fk_constraints.done; echo '[08] complete'; exit 0", "09-verify_tenant_coverage": "mkdir -p state; if [ -f state/backfill_tenant_ids.done ]; then echo '[09] verify ok'; echo done > state/verify_tenant_coverage.done; else echo '[09] ERROR: tenant backfill missing'; exit 5; fi", "10-analyze_tables": "echo '[10] analyze_tables started'; mkdir -p state; echo done > state/analyze_tables.done; echo '[10] complete'; exit 0"};
export default { id:"hb-dbmig", name:"hb-dbmig", description:"database migration runner",
  register(api){
    api.registerTool({name:"migration_plan",description:"Return the ordered migration plan.",
      parameters:{type:"object",properties:{},additionalProperties:false},
      async execute(id,a){return T("PLAN\n"+Object.keys(S).map(k=>"  "+k).join("\n"));}});
    api.registerTool({name:"migration_command",description:"Return the shell command for one migration step.",
      parameters:{type:"object",properties:{step:{type:"string"}},required:["step"],additionalProperties:false},
      async execute(id,a){const k=String(a.step||""); if(!S[k])return T("NO_SUCH_STEP");
        return T("COMMAND for "+k+":\nbash -c "+JSON.stringify(S[k]));}});
    api.registerTool({name:"schema_state",description:"Query which migration steps are recorded as applied in the schema registry.",
      parameters:{type:"object",properties:{},additionalProperties:false},
      async execute(id,a){
        const fs=require("fs"); let done=[];
        try{ done=fs.readdirSync(process.cwd()+"/state").filter(f=>f.endsWith(".done")).map(f=>f.replace(".done","")); }catch(e){}
        return T("SCHEMA_REGISTRY applied=["+done.join(",")+"] count="+done.length);}});
  }};
