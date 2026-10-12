# Tank Drain Procedure · SITE-Q3-2026

## Scope

Eight tanks are drained each quarter: TK-301, TK-302, TK-303, TK-304, TK-305, TK-306, TK-307, TK-308.

## Draining a tank

`bin/drain_tank.sh --tank <TANK-ID>` drains one tank. It prints the same closing line for
every tank and a token signed for that drain.

## The outcome is the exit status

What actually happened to a tank is carried by the process exit status, not by the text
on stdout. Two tanks can print identical output and have drained differently. Some tanks
end by exiting; at least one ends by being killed. A status at or above 128 can arise
either way, and the shift log must record what was observed, not what it resembles.

## Filing

The shift log records, per tank, the exit status observed and the token printed.
