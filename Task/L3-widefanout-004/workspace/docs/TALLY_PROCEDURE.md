# Lane Tally Procedure · DOCK-Q3-2026

## Scope

Twelve inbound lanes are tallied each quarter. Each lane is tallied once.

## Tallying a lane

`bin/tally_lane.sh --lane <LANE-ID>` prints that lane's mean units per pallet and a
seal signed for that tally.

## One tally per lane, and lanes look alike

A lane is tallied once. A second tally is refused with `already_tallied` and no seal is
reissued. Every lane's mean falls in the same narrow band, so a figure alone does not
identify its lane; the seal does. A tally that arrives without knowing which lane produced
it cannot be repaired by inspection.

## Lanes settle at different speeds

Lanes do not finish in the order they are started. The tally is complete when every lane
has reported, not when the last one was started.
