export default {
  id: "harnessbench-t1d", name: "HarnessBench T1d", description: "Over-long tool name probe",
  register(api) {
    api.registerTool({
      name: "hb_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      description: "Tool whose name is 144 characters long.",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      async execute() { return { content: [{ type: "text", text: "LONGNAME_EXECUTED" }] }; }
    });
  }
};
