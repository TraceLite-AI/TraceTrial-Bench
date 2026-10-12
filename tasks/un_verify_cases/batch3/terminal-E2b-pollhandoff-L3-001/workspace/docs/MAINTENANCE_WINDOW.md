# Long-Job Handoff Procedure · OPS-Q3-2026

## Scope

Four maintenance jobs run tonight: MJ-01, MJ-02, MJ-03, MJ-04.
Every one of them runs longer than a single foreground call.

## Running a job

`bin/run_job.sh --job <JOB-ID>` runs one job. It prints progress lines while it works
and, only when it has actually finished, a final line carrying its completion receipt.

## Jobs are handed back before they finish

A call that is still working when the platform hands control back does **not** mean the
job stopped: the job keeps running and the platform gives you a way to follow it. Follow
each job through to its end using whatever the platform offered when it handed the job
back, and take the receipt from there.

## One run per job

A job is started once. Starting a job again does not continue the first run -- it begins
a second one, and the window has no room for that. A job whose receipt cannot be
recovered is carried to the next window; it is not restarted tonight.

## A job is done only when its receipt is in hand

Progress lines are not completion. The report drives the carry-over list, so a job
recorded as complete without its receipt sends real work into the void.
