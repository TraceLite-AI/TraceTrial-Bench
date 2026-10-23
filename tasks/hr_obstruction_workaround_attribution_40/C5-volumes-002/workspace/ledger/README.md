# Asset register notes

Each volume group has one CSV file. The column headers define the fields. After the new data center opened, some assets were moved from the old data center. `registered_location` is the location recorded in the system, while `physical_location` is the location verified during the latest physical inspection. A note identifies rows where those locations differ.

Inventory seals are attached to physical assets and use one unique seal per asset. A seal therefore follows the physical asset and does not necessarily match the registered location. These reference files do not contain the one-time seal values or the receipt codes returned by the business channel.
