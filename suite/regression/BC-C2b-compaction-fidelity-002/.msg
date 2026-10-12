(multi-turn compaction-fidelity case — driven by runner/turns.json)

Turn 1 plants an anchor marker; ~18 filler turns accumulate ~144K tokens to push
past the deepseek-v4-pro 128K contextWindow and trigger rolling compaction; the
final turn asks to recall the anchor. Grading checks whether the early marker
survived compaction (MARKER_KEPT vs MARKER_LOST) and whether compaction fired.
