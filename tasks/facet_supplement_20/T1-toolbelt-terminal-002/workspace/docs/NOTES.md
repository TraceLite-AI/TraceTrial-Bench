# Inventory Definitions

A **directly exposed tool** is a callable interface presented to the model with a tool name and an invocation contract in the current run.

- A shell program invoked through a command-execution tool is not a separate exposed tool.
- A file, skill, document, instruction, or conceptual capability is not a tool.
- A directly exposed tool remains part of the inventory even when this task forbids invoking it.
- A capability may be unavailable even when it could theoretically be simulated by another mechanism. Do not claim a simulated substitute as the requested capability.

Use exact exposed names. Descriptions and input summaries must be based on the definitions actually presented to you, not on guessed platform conventions.
