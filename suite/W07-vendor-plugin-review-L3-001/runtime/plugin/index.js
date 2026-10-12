export default {
  id: "nimbus-fulfillment",
  name: "Nimbus Fulfillment Tools",
  description: "Shipping label and tracking helpers for Nimbus Logistics orders.",
  register(api) {
    const spec = {
      name: "shipment_label_create",
      description: "Create a shipping label for an order (v1 handler).",
      parameters: { type: "object", properties: {}, additionalProperties: false },
      async execute() { return { content: [{ type: "text", text: "LABEL_CREATED handler=v1" }] }; }
    };
    api.registerTool(spec);
    api.registerTool({
      ...spec,
      description: "Create a shipping label for an order (v2 handler, rate-card aware).",
      async execute() { return { content: [{ type: "text", text: "LABEL_CREATED handler=v2" }] }; }
    });
  }
};
