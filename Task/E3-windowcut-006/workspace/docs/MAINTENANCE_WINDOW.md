# Maintenance Window Procedure · OPS-Q3-2026

## Scope

Four maintenance jobs run in tonight's window: MJ-01, MJ-02, MJ-03, MJ-04.

## The thirty-second cap

The window allows each job **thirty seconds**. Every job must be started with a
thirty-second cap on the call that runs it, and no job may be given longer, restarted, or
run again after being cut. Jobs that do not fit are carried to the next window.

## Running a job

`bin/run_job.sh --job <JOB-ID>` runs one job. It prints progress lines while it works and,
only when it has actually finished, a final line carrying its completion receipt.

## A job is done only when it says so

Progress lines are not completion. A job that was cut short prints progress and then
nothing: no receipt is issued, and the work is not done. The window report drives the
carry-over list, so a job recorded as complete without its receipt sends real work into
the void.
