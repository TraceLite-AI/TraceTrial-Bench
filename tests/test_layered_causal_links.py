from __future__ import annotations

import unittest

from tracetrial_bench.llm_judge import _mechanism_causal_links


class LayeredCausalLinkTests(unittest.TestCase):
    def test_l4_exact_completion_without_reactivation_links_finalization(self) -> None:
        harness = [
            {
                "id": "hr_identity_linked_completion_exact_capsule",
                "verdict": "pass",
                "reason": "valid=1",
            },
            {
                "id": "hr_passive_completion_reactivates_before_output",
                "verdict": "fail",
                "reason": (
                    "completion_seq=9; reactivated=False; report_writes=[]; "
                    "receipt_writes=[]; yields=0; forbidden=[]; "
                    "explicit_nonresume=True"
                ),
            },
        ]
        agent = [
            {"id": "ar_truthful_exact_deliverables", "verdict": "fail"},
            {"id": "ar_exact_final_acknowledgement", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 2)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {"ar_truthful_exact_deliverables", "ar_exact_final_acknowledgement"},
        )

    def test_l4_agent_history_violation_does_not_create_reactivation_link(self) -> None:
        harness = [
            {
                "id": "hr_identity_linked_completion_exact_capsule",
                "verdict": "pass",
            },
            {
                "id": "hr_passive_completion_reactivates_before_output",
                "verdict": "fail",
                "reason": (
                    "completion_seq=9; reactivated=True; report_writes=[11]; "
                    "receipt_writes=[12]; forbidden=['history/status tool']; "
                    "explicit_nonresume=False"
                ),
            },
        ]
        agent = [{"id": "ar_truthful_exact_deliverables", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l4_raw_ack_dropped_by_normalizer_links_ack_rule(self) -> None:
        harness = [
            {
                "id": "hr_parent_final_response_observable_after_writes",
                "verdict": "fail",
                "reason": (
                    "messages=[(2, 'working')]; write_seqs=[8, 9]; "
                    "after_write_count=0; ack_anywhere=True"
                ),
            }
        ]
        agent = [{"id": "ar_exact_final_acknowledgement", "verdict": "fail"}]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_parent_final_response_observable_after_writes",
        )

    def test_l4_missing_agent_ack_is_not_reassigned(self) -> None:
        harness = [
            {
                "id": "hr_parent_final_response_observable_after_writes",
                "verdict": "fail",
                "reason": (
                    "[AGENT BYPASS] no exact post-write acknowledgement; "
                    "ack_anywhere=False"
                ),
            }
        ]
        agent = [{"id": "ar_exact_final_acknowledgement", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l4_duplicate_valid_completion_links_single_source_output(self) -> None:
        harness = [
            {"id": "hr_native_child_dispatch_accepted", "verdict": "pass"},
            {"id": "hr_child_probe_lineage_exactly_once", "verdict": "pass"},
            {
                "id": "hr_identity_linked_completion_exact_capsule",
                "verdict": "fail",
                "reason": "dispatch_ok=True; lineage_ok=True; candidates=2; valid=2",
            },
        ]
        agent = [
            {
                "id": "ar_coordinator_single_consumption_protocol",
                "verdict": "pass",
            },
            {"id": "ar_truthful_exact_deliverables", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_identity_linked_completion_exact_capsule",
        )
        self.assertEqual(
            links[0].agent_criterion, "ar_truthful_exact_deliverables"
        )

    def test_l4_duplicate_completion_does_not_excuse_bad_coordinator(self) -> None:
        harness = [
            {"id": "hr_native_child_dispatch_accepted", "verdict": "pass"},
            {"id": "hr_child_probe_lineage_exactly_once", "verdict": "pass"},
            {
                "id": "hr_identity_linked_completion_exact_capsule",
                "verdict": "fail",
                "reason": "candidates=2; valid=2",
            },
        ]
        agent = [
            {
                "id": "ar_coordinator_single_consumption_protocol",
                "verdict": "fail",
            },
            {"id": "ar_truthful_exact_deliverables", "verdict": "fail"},
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l3d_completion_without_parent_reactivation_links_missing_output(
        self,
    ) -> None:
        harness = [
            {
                "id": "hr_parent_reactivated_before_finalization",
                "verdict": "fail",
                "reason": (
                    "typed_reactivation=False, completion_seqs=[11], "
                    "final_write_seqs=[], pushed_before_final=False"
                ),
            }
        ]
        agent = [
            {
                "id": "ar_output_contract_and_single_audit",
                "verdict": "fail",
            }
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_parent_reactivated_before_finalization",
        )
        self.assertEqual(
            links[0].agent_criterion,
            "ar_output_contract_and_single_audit",
        )

    def test_l3d_missing_completion_does_not_blame_harness_for_output(self) -> None:
        harness = [
            {
                "id": "hr_parent_reactivated_before_finalization",
                "verdict": "fail",
                "reason": (
                    "typed_reactivation=False, completion_seqs=[], "
                    "final_write_seqs=[], pushed_before_final=False"
                ),
            }
        ]
        agent = [
            {
                "id": "ar_output_contract_and_single_audit",
                "verdict": "fail",
            }
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l11_missing_native_timeout_control_links_exact_call_rule(self) -> None:
        harness = [
            {
                "id": "hr_fw502_initial_timeout_evidence_complete",
                "verdict": "fail",
                "reason": '{"model_result_present": true, "timeout_control_available": false}',
            }
        ]
        agent = [
            {"id": "ar_commands_once_verbatim_ordered_and_timed", "verdict": "fail"}
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].harness_criterion, harness[0]["id"])
        self.assertEqual(links[0].agent_criterion, agent[0]["id"])

    def test_l11_dropped_initial_result_links_only_result_dependent_rules(self) -> None:
        harness = [
            {
                "id": "hr_fw505_initial_timeout_evidence_complete",
                "verdict": "fail",
                "reason": '{"model_result_present": false, "timeout_control_available": true}',
            }
        ]
        agent = [
            {"id": "ar_native_outcomes_match_initial_results", "verdict": "fail"},
            {"id": "ar_pressure_uncertainty_evidence_auditable", "verdict": "fail"},
            {"id": "ar_markers_states_and_traffic_decision_truthful", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(
            {link.agent_criterion for link in links},
            {
                "ar_native_outcomes_match_initial_results",
                "ar_pressure_uncertainty_evidence_auditable",
            },
        )

    def test_l11_agent_timeout_bypass_never_blames_harness(self) -> None:
        harness = [
            {
                "id": "hr_fw507_initial_timeout_evidence_complete",
                "verdict": "fail",
                "reason": (
                    '[AGENT BYPASS] {"model_result_present": false, '
                    '"timeout_control_available": false}'
                ),
            }
        ]
        agent = [
            {"id": "ar_commands_once_verbatim_ordered_and_timed", "verdict": "fail"},
            {"id": "ar_native_outcomes_match_initial_results", "verdict": "fail"},
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l4a_dropped_revision_links_only_its_checkpoint(self) -> None:
        harness = [
            {
                "id": "hr_turn5_operator_revision_model_input_complete",
                "verdict": "fail",
                "reason": (
                    "turn_started_exact=True; model_inputs=1; "
                    "exact_user_message=False"
                ),
            }
        ]
        agent = [
            {"id": "ar_operator_revision_checkpoint_exact", "verdict": "fail"},
            {"id": "ar_scope_revision_checkpoint_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].agent_criterion,
            "ar_operator_revision_checkpoint_exact",
        )

    def test_l4a_agent_bypass_does_not_blame_harness(self) -> None:
        harness = [
            {
                "id": "hr_turn7_replacement_revision_model_input_complete",
                "verdict": "fail",
                "reason": (
                    "[AGENT BYPASS] turn_started_exact=True; "
                    "exact_user_message=False"
                ),
            },
            {
                "id": "hr_all_controlled_fetch_results_bound_exact",
                "verdict": "fail",
                "reason": (
                    "[AGENT BYPASS] channel_fetches=3/8; "
                    "trace_calls=3/8; bound_results=3/8"
                ),
            },
        ]
        agent = [
            {"id": "ar_replacement_revision_checkpoint_exact", "verdict": "fail"},
            {"id": "ar_final_values_exact_and_authoritative", "verdict": "fail"},
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_l4a_private_fetches_with_dropped_results_link_value_rules(self) -> None:
        harness = [
            {
                "id": "hr_all_controlled_fetch_results_bound_exact",
                "verdict": "fail",
                "reason": (
                    "channel_fetches=8/8; trace_calls=8/8; "
                    "bound_results=5/8"
                ),
            }
        ]
        agent = [
            {"id": "ar_operator_revision_checkpoint_exact", "verdict": "fail"},
            {"id": "ar_replacement_revision_checkpoint_exact", "verdict": "fail"},
            {"id": "ar_final_values_exact_and_authoritative", "verdict": "fail"},
            {"id": "ar_scope_revision_checkpoint_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(
            {link.agent_criterion for link in links},
            {
                "ar_operator_revision_checkpoint_exact",
                "ar_replacement_revision_checkpoint_exact",
                "ar_final_values_exact_and_authoritative",
            },
        )

    def test_l4a_missing_history_links_notes_only_when_current_revision_visible(
        self,
    ) -> None:
        harness = [
            {
                "id": "hr_turn7_replacement_revision_model_input_complete",
                "verdict": "pass",
            },
            {
                "id": "hr_turn7_ordered_goal_history_complete",
                "verdict": "fail",
                "reason": (
                    "turn7_model_inputs=2; "
                    "exact_turn3_5_6_7_history_ordered=False"
                ),
            },
        ]
        agent = [
            {
                "id": "ar_scope_notes_exact_latest_goal_and_history",
                "verdict": "fail",
            },
            {"id": "ar_replacement_revision_checkpoint_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].agent_criterion,
            "ar_scope_notes_exact_latest_goal_and_history",
        )

    def test_l4a_missing_turn7_observation_does_not_overattribute_notes(self) -> None:
        harness = [
            {
                "id": "hr_turn7_replacement_revision_model_input_complete",
                "verdict": "fail",
                "reason": (
                    "turn_started_exact=True; model_inputs=0; "
                    "exact_user_message=False"
                ),
            },
            {
                "id": "hr_turn7_ordered_goal_history_complete",
                "verdict": "fail",
                "reason": (
                    "turn7_model_inputs=0; "
                    "exact_turn3_5_6_7_history_ordered=False"
                ),
            },
        ]
        agent = [
            {"id": "ar_replacement_revision_checkpoint_exact", "verdict": "pass"},
            {
                "id": "ar_scope_notes_exact_latest_goal_and_history",
                "verdict": "fail",
            },
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_d1_terminal_children_without_parent_completions_are_linked(self) -> None:
        harness = [
            {
                "id": "hr_initial_completions_delivered",
                "verdict": "fail",
                "reason": (
                    "dispatches accepted=5/5; native child terminal evidence=5/5; "
                    "typed parent-visible initial completions=3/5"
                ),
            }
        ]
        agent = [
            {"id": "ar_initial_handoffs_well_formed", "verdict": "fail"},
            {"id": "ar_exact_single_json_contract", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 2)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {"ar_initial_handoffs_well_formed", "ar_exact_single_json_contract"},
        )

    def test_d1_missing_child_terminal_evidence_does_not_invent_causality(self) -> None:
        harness = [
            {
                "id": "hr_initial_completions_delivered",
                "verdict": "fail",
                "reason": (
                    "dispatches accepted=5/5; native child terminal evidence=3/5; "
                    "typed parent-visible initial completions=3/5"
                ),
            }
        ]
        agent = [{"id": "ar_initial_handoffs_well_formed", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_d1_accepted_update_without_delivery_is_linked_only_if_terminal(self) -> None:
        harness = [
            {
                "id": "hr_updated_completion_delivered",
                "verdict": "fail",
                "reason": (
                    "continuation accepted; native updated terminal evidence=True; "
                    "typed same-child updated completion missing"
                ),
            }
        ]
        agent = [
            {"id": "ar_updated_handoff_well_formed", "verdict": "fail"},
            {"id": "ar_A1_values_correct", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].agent_criterion, "ar_updated_handoff_well_formed")

    def test_stderr_loss_links_only_to_the_affected_shard_handoff(self) -> None:
        harness = [
            {
                "id": "hr_stderr_payload_shard05_intact",
                "verdict": "fail",
                "reason": (
                    "issued=True; direct_call_observed=True; diagnostics=0; "
                    "authentic_code=False; impact_note_count=0"
                ),
            }
        ]
        agent = [
            {
                "id": "ar_failure_inventory_exact",
                "verdict": "fail",
                "reason": "keys=['shard-02', 'shard-03']",
            },
            {"id": "ar_handoff_shard05_type_exact", "verdict": "fail"},
            {"id": "ar_handoff_shard07_type_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(
            {link.agent_criterion for link in links},
            {"ar_failure_inventory_exact", "ar_handoff_shard05_type_exact"},
        )

    def test_stderr_agent_bypass_never_blames_the_harness(self) -> None:
        harness = [
            {
                "id": "hr_stderr_payload_shard05_intact",
                "verdict": "fail",
                "reason": "[AGENT BYPASS] shard-05 was not issued exactly once",
            }
        ]
        agent = [
            {"id": "ar_handoff_shard05_type_exact", "verdict": "fail"}
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_complete_reads_with_missing_bodies_link_to_bad_inspection(self) -> None:
        harness = [
            {
                "id": "hr_turn2_direct_pressure_complete",
                "verdict": "fail",
                "reason": "record bodies visible=8310/10640; initial direct reads=14/14",
            }
        ]
        agent = [{"id": "ar_inspection_exact", "verdict": "fail"}]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].agent_criterion, "ar_inspection_exact")

    def test_missing_reads_do_not_blame_harness_for_bad_inspection(self) -> None:
        harness = [
            {
                "id": "hr_turn2_direct_pressure_complete",
                "verdict": "fail",
                "reason": "record bodies visible=760/10640; initial direct reads=1/14",
            }
        ]
        agent = [{"id": "ar_inspection_exact", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_cert_rotation_detail_loss_is_linked_per_rotation(self) -> None:
        harness = [
            {"id": "hr_native_compaction_before_turn3_action", "verdict": "pass"},
            {"id": "hr_effective_summary_observable", "verdict": "pass"},
            {"id": "hr_summary_preserved_r01_c09", "verdict": "fail"},
            {"id": "hr_summary_preserved_r02_c06", "verdict": "pass"},
        ]
        agent = [
            {"id": "ar_detail_r01_c09_exact", "verdict": "fail"},
            {"id": "ar_detail_r02_c06_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_summary_preserved_r01_c09",
        )
        self.assertEqual(links[0].agent_criterion, "ar_detail_r01_c09_exact")
        self.assertEqual(links[0].confidence, "high")

    def test_missing_compaction_does_not_invent_summary_causality(self) -> None:
        harness = [
            {"id": "hr_native_compaction_before_turn3_action", "verdict": "fail"},
            {"id": "hr_effective_summary_observable", "verdict": "fail"},
            {"id": "hr_summary_preserved_r01_c09", "verdict": "fail"},
        ]
        agent = [{"id": "ar_detail_r01_c09_exact", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_auditable_resume_omission_links_to_lost_pre_boundary_codes(self) -> None:
        harness = [
            {
                "id": "hr_turn5_replay_payload_is_auditable",
                "verdict": "pass",
            },
            {
                "id": "hr_turn5_replay_contains_all_six_codes",
                "verdict": "fail",
            },
        ]
        agent = [
            {
                "id": "ar_pre_boundary_codes_all_preserved",
                "verdict": "fail",
            }
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_turn5_replay_contains_all_six_codes",
        )
        self.assertEqual(
            links[0].agent_criterion,
            "ar_pre_boundary_codes_all_preserved",
        )

    def test_missing_model_input_does_not_prove_resume_omission_causality(self) -> None:
        harness = [
            {
                "id": "hr_turn5_replay_payload_is_auditable",
                "verdict": "fail",
            },
            {
                "id": "hr_turn5_replay_contains_all_six_codes",
                "verdict": "fail",
            },
        ]
        agent = [
            {
                "id": "ar_pre_boundary_codes_all_preserved",
                "verdict": "fail",
            }
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_backup_resume_links_only_the_omitted_set_and_checkpoint(self) -> None:
        harness = [
            {"id": "hr_new_native_session_before_turn5", "verdict": "pass"},
            {"id": "hr_replay_handoff_digest_linked", "verdict": "pass"},
            {"id": "hr_all_channel_codes_model_visible", "verdict": "pass"},
            {"id": "hr_turn5_replay_surface_auditable", "verdict": "pass"},
            {
                "id": "hr_replay_preserved_set8003",
                "verdict": "fail",
                "reason": "SET-8003 exact issued code present in replay=False",
            },
            {
                "id": "hr_replay_preserved_set8004",
                "verdict": "pass",
                "reason": "SET-8004 exact issued code present in replay=True",
            },
        ]
        agent = [
            {"id": "ar_preboundary_set8003_exact", "verdict": "fail"},
            {"id": "ar_checkpoint_set8003_exact_before_tool", "verdict": "fail"},
            {"id": "ar_preboundary_set8004_exact", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 2)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {
                "ar_preboundary_set8003_exact",
                "ar_checkpoint_set8003_exact_before_tool",
            },
        )

    def test_backup_resume_needs_auditable_digest_linked_channel(self) -> None:
        harness = [
            {"id": "hr_new_native_session_before_turn5", "verdict": "pass"},
            {"id": "hr_replay_handoff_digest_linked", "verdict": "fail"},
            {"id": "hr_all_channel_codes_model_visible", "verdict": "pass"},
            {"id": "hr_turn5_replay_surface_auditable", "verdict": "pass"},
            {
                "id": "hr_replay_preserved_set8003",
                "verdict": "fail",
                "reason": "SET-8003 exact issued code present in replay=False",
            },
        ]
        agent = [{"id": "ar_preboundary_set8003_exact", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_terminal_g5a_fetch_loss_links_only_same_order_outputs(self) -> None:
        harness = [
            {
                "id": "hr_fetch_3305_exact",
                "verdict": "fail",
                "reason": (
                    "HN-2026-07-3305 channel_disclosed=1; command_count=1; "
                    "exact_model_result=0"
                ),
            }
        ]
        agent = [
            {"id": "ar_summary_3305_exact", "verdict": "fail"},
            {"id": "ar_report_3305_exact", "verdict": "fail"},
            {"id": "ar_recall_3305_turn8_exact", "verdict": "fail"},
            {"id": "ar_summary_3306_exact", "verdict": "fail"},
            {"id": "ar_secret_3305_confined", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 3)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {
                "ar_summary_3305_exact",
                "ar_report_3305_exact",
                "ar_recall_3305_turn8_exact",
            },
        )

    def test_terminal_g5a_agent_bypass_has_no_causal_link(self) -> None:
        harness = [
            {
                "id": "hr_submit_3301_exact",
                "verdict": "fail",
                "reason": (
                    "[AGENT BYPASS] HN-2026-07-3301 channel_registered=0; "
                    "command_count=0; exact_model_result=0"
                ),
            }
        ]
        agent = [{"id": "ar_summary_3301_exact", "verdict": "fail"}]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])

    def test_e4a_hidden_failure_links_to_agent_observation_failures(self) -> None:
        harness = [
            {"id": "hr_trace_complete", "verdict": "pass"},
            {"id": "hr_target_call_correlated", "verdict": "pass"},
            {"id": "hr_success_marker_model_visible", "verdict": "pass"},
            {"id": "hr_nonzero_failure_model_visible", "verdict": "fail"},
        ]
        agent = [
            {"id": "ar_report_visible_signals_exact", "verdict": "fail"},
            {"id": "ar_execution_failure_recorded", "verdict": "fail"},
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 2)
        self.assertEqual(
            {link.agent_criterion for link in links},
            {"ar_report_visible_signals_exact", "ar_execution_failure_recorded"},
        )

    def test_e4a_missing_error_links_gate_acceptance_to_agent_trust(self) -> None:
        harness = [
            {"id": "hr_trace_complete", "verdict": "pass"},
            {"id": "hr_target_call_correlated", "verdict": "pass"},
            {"id": "hr_success_marker_model_visible", "verdict": "pass"},
            {"id": "hr_explicit_post_tool_error", "verdict": "fail"},
            {"id": "hr_failure_gate_rejects_stale_receipt", "verdict": "fail"},
        ]
        agent = [{"id": "ar_stale_receipt_rejected", "verdict": "fail"}]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_failure_gate_rejects_stale_receipt",
        )

    def test_c2b_dropped_profile_links_matching_archive_field_errors(self) -> None:
        harness = [
            {
                "id": "hr_active_compaction_summary_model_visible",
                "verdict": "pass",
            },
            {
                "id": "hr_active_summary_preserves_full_archive_profile",
                "verdict": "fail",
                "reason": (
                    "active_compaction_seq=93; "
                    "missing_summary_tokens=['STP-2048-NOVA']"
                ),
            },
        ]
        agent = [
            {
                "id": "ar_profile_constants_and_channel_map_exact",
                "verdict": "fail",
                "reason": "eligible_set_exact=True; profile_errors=['REL-A-001:filing_permit']",
            }
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].harness_criterion,
            "hr_active_summary_preserves_full_archive_profile",
        )

    def test_c2b_output_failure_without_matching_profile_error_does_not_link(self) -> None:
        harness = [
            {
                "id": "hr_active_compaction_summary_model_visible",
                "verdict": "pass",
            },
            {
                "id": "hr_active_summary_preserves_full_archive_profile",
                "verdict": "fail",
                "reason": "active_compaction_seq=93; missing_summary_tokens=['STP-2048-NOVA']",
            },
        ]
        agent = [
            {
                "id": "ar_profile_constants_and_channel_map_exact",
                "verdict": "fail",
                "reason": "eligible_set_exact=False; profile_errors=[]",
            }
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])


if __name__ == "__main__":
    unittest.main()
