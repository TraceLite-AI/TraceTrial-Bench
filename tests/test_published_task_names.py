from __future__ import annotations

import collections
import json
import re
import unittest
from pathlib import Path

from tracetrial_bench.batch import resolve_task_inputs
from tracetrial_bench.score_config import load_score_sheet
from tracetrial_bench.task_mechanism_chains import CHAINS, NO_CHAIN
from tracetrial_bench.task_names import TASK_ID_RENAMES
from tracetrial_bench.task_resolver import resolve_task


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = {
    'C1': 3, 'C2': 2, 'C3': 2, 'C4': 3, 'C5': 3, 'C6': 2,
    'E1': 1, 'E2': 3, 'E3': 6, 'E4': 3, 'E5': 3, 'E6': 3,
    'E7': 3, 'E8': 3, 'E9': 2, 'G1': 2, 'G2': 3, 'G3': 1, 'G4': 3,
    'L1': 1, 'L2': 2, 'L3': 4, 'L4': 1, 'L5': 3, 'L6': 3, 'L7': 2, 'L8': 3,
    'O1': 4, 'O2': 3, 'T1': 2, 'T2': 1, 'T3': 3, 'T4': 1, 'T5': 1,
    'T6': 3, 'T7': 2, 'T8': 2, 'T9': 1, 'T10': 1, 'V1': 1,
}


class PublishedTaskNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = resolve_task_inputs(
            tasks=(), smoke=False, task_manifest=ROOT / 'Task/final.manifest',
            repository_root=ROOT,
        )

    def test_paper_selection_and_node_numbering(self):
        self.assertEqual(len(self.paths), 95)
        self.assertEqual(len(TASK_ID_RENAMES), 95)
        self.assertEqual({p.name for p in self.paths}, set(TASK_ID_RENAMES.values()))
        counts = collections.Counter()
        for path in self.paths:
            with self.subTest(task=path.name):
                self.assertTrue(path.is_dir())
                self.assertRegex(path.name, r'^[CEGLOTV]\d+-[a-z0-9-]+-\d{3}$')
                node = path.name.split('-')[0]
                number = path.name.rsplit('-', 1)[1]
                counts[node] += 1
                self.assertEqual(int(number), counts[node])
        self.assertEqual(dict(counts), EXPECTED_COUNTS)
        dimensions = collections.Counter()
        for node, count in counts.items():
            dimensions[node[0]] += count
        self.assertEqual(dict(dimensions), dict(C=15, E=27, G=9, L=19, O=7, T=17, V=1))

    def test_runtime_metadata_and_score_sheets_resolve(self):
        for path in self.paths:
            with self.subTest(task=path.name):
                task = resolve_task(path)
                self.assertEqual(task.task_id, path.name)
                node = path.name.split('-')[0]
                self.assertEqual(task.metadata['harness']['subdimension'], node)
                self.assertEqual(task.metadata['harness']['dimension'], node[0])
                self.assertEqual(task.metadata['intensity']['level'], 'L3')
                profile = json.loads((path / 'runtime/execution.json').read_text(encoding='utf-8'))
                self.assertEqual(profile['profile'], task.task_id)
                self.assertTrue((path / task.verifier.entrypoint).is_file())
                self.assertIsNotNone(task.scoring)
                for layer in ('harness', 'agent'):
                    sheet = load_score_sheet(getattr(task.scoring, layer+'_score_path'), expected_layer=layer)
                    self.assertTrue(sheet.items)
                commands = (path / 'run.md').read_text(encoding='utf-8')
                for referenced in re.findall(r'--task\s+(\S+)', commands):
                    self.assertEqual((ROOT / referenced).resolve(), path)

    def test_old_results_keep_identical_attribution_chains(self):
        for old_id, new_id in TASK_ID_RENAMES.items():
            with self.subTest(task=new_id):
                self.assertEqual(CHAINS.get(old_id), CHAINS.get(new_id))
                self.assertEqual(NO_CHAIN.get(old_id), NO_CHAIN.get(new_id))
        self.assertIn('E3-restore-latency-004', CHAINS)
        self.assertIn('T10-request-intake-001', CHAINS)
        self.assertNotEqual(TASK_ID_RENAMES['T1g-request-intake-L3-001'].split('-')[0],
                            TASK_ID_RENAMES['N1-quota-askuser-L3-001'].split('-')[0])

    def test_final_collection_is_complete_and_paper_ordered(self):
        names = [p.name for p in self.paths]
        directories = {p.name for p in (ROOT / "Task").iterdir() if p.is_dir()}
        self.assertEqual(directories, set(names))
        self.assertNotIn("T1b-hostrecord-duplicate-001", names)
        expected_nodes = [node for node, count in EXPECTED_COUNTS.items() for _ in range(count)]
        self.assertEqual([name.split("-")[0] for name in names], expected_nodes)



if __name__ == '__main__':
    unittest.main()
