export default {
  id: "harnessbench-t1b",
  name: "TraceTrial_Bench T1b",
  description: "Duplicate tool-name registration probe",
  register(api) {
    const spec = {
      name: "hb_duplicate_name_probe",
      description: "First duplicate-name probe.",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      async execute() { return { content: [{ type: "text", text: "FIRST" }] }; }
    };
    api.registerTool(spec);
    api.registerTool({ ...spec, description: "Second duplicate-name probe." });
  }
};
