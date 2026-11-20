---
name: build-ats-ecosim
description: Build or rebuild ATS with the EcoSIM process kernel using ats-helper's Ubuntu 24.04 Docker configuration, persistent caches, and runtime checks. Use for requests such as "build ats-ecosim" or "compile ATS with EcoSIM". Download-only requests belong to download-ats.
---

# Build ATS–EcoSIM

Use the existing [launcher](../../../run_docker.sh) and
[build script](../../../docker/build-ats-ecosim.sh). Keep compilation logic in
those files; do not reconstruct the bootstrap command in this skill.

Resolve the execution environment using "Select the container" below before
running source-preparation commands. Reuse the full container ID selected by a
preceding download task. When a container is selected, run all source inspection,
Git revision/submodule operations, compilation, and runtime checks inside it,
using `/ats-helper` as the source workspace. Host commands only orchestrate Docker
and access the local session record or exported logs.

## Prepare the source and environment

Work from the `ats-helper` root containing `run_docker.sh` and `docker/`.
Read the current scripts and [README](../../../README.md) before execution.

- Check Docker availability, running builds, CPU architecture, allocated memory,
  and available Docker storage. Avoid two builders using the same build tree.
  Monitor an already-running intended build rather than starting it again.
- Expect Amanzi at `repos/amanzi`, ATS at `repos/amanzi/src/physics/ats`, and
  EcoSIM at `repos/ecosim`. If source is missing, use the
  [download-ats skill](../download-ats/SKILL.md), then check the ATS revision.
- Inspect origins, commit hashes, working trees, and recursive submodule status.
  Honor user-specified revisions. Otherwise reuse a compatible checkout already
  containing the EcoSIM PK; do not update branch tips just to rebuild.
- For the paired ATS-EcoSIM / standalone EcoSIM work (`cases/pft_tests`), use the sources
  pinned in `cases/pft_tests/case.json`: Amanzi from `jinyun1tang/amanzi`, ATS from
  `AndrewGraus/ats` (branch `ecosim-pk-only`; its commit is not the one Amanzi records, so fetch
  it from its own URL and check it out in the ATS submodule), and EcoSIM from `EcoSIM-model/EcoSIM`
  (branch `agraus/standalone_match`). `./reproduce.sh pft_tests` does this in its own volume.
- The older known pairing is Amanzi `master`, ATS `agraus/ecosim_pk`, and the
  EcoSIM ref declared in Amanzi's `config/SuperBuild/TPLVersions.cmake`
  (`agraus/PrescribedPhenology` in the validated build). Confirm the ATS source
  contains `src/pks/ecosim/EcoSIM_ATS_interface.cc`.

For an ordinary coupled build, if the ATS checkout lacks the PK and is clean,
select its available `origin/agraus/ecosim_pk` ref, then update submodules inside
ATS with `--init --recursive`. Fetch a missing required ref if needed. Preserve
explicit revision choices and local changes; do not reset, clean, or commit user
work. The parent Amanzi status may show a changed ATS submodule pointer after
this intentional selection. Inspect the nested working tree to distinguish that
pointer change from edits. A parent-level submodule update would restore
Amanzi's recorded ATS commit, so do not run it after selecting the EcoSIM PK.

The build script mirrors EcoSIM's committed HEAD into the build volume. Tracked
EcoSIM edits cannot be built by that script until handled explicitly; do not
silently discard or commit them. An existing cached EcoSIM source must match
that HEAD. For a conflict or incompatible compiler/dependency configuration,
preserve the cache and explain the need for a separate build tree. The launcher
currently fixes the volume name; it has no custom-volume flag.

## Select the container

An explicitly supplied container ID or name takes precedence. Otherwise inspect
the local `.docker/container.json` written by `run_docker.sh`. Per-session records
are in `.docker/containers/<full-id>.json`; the latest pointer is not changed when
an older launcher exits. Parse JSON as data, not as shell code.

Treat this file as a locator, not proof that a container is still running.
Inspect the full ID in its recorded Docker context. Confirm the live ID,
running state, image identity, `/ats-helper` working directory, writable bind
mount from this workspace to `/ats-helper`, and expected build volume at
`/ats-build`. Check current builder processes before launching another one.
Use the live inspection rather than the saved state snapshot, which is recorded
before attachment and refreshed when the launcher finishes.

If the intended container is running and matches, reuse it:

```bash
docker --context "$DOCKER_CONTEXT_NAME" exec -e JOBS=2 -w /ats-helper "$CONTAINER_ID" \
    bash --login /ats-helper/docker/build-ats-ecosim.sh
```

Set these variables from the verified record or explicit user target, and honor
the requested job count. Do not invoke the launcher merely to rebuild inside an
already selected container. Keep runtime validation in that container as well.
If an explicit target is unavailable or mismatched, report it rather than
silently choosing another container. If a saved record is stale and no particular
session was requested, explain that and use the launcher below for a new session.
For older unrecorded sessions, inspect Docker's running containers and their
mounts. If several indistinguishable candidates remain and the user requires a
specific session, request its ID or name.

## Build and monitor

Use native architecture unless the user specifies another target. Start with
two jobs; honor an explicit job count or adjust it based on measured resources.
The validated ARM64 build used four jobs within Docker's 8 GB allocation.

```bash
JOBS=2 ./run_docker.sh --ubuntu --build-ats-ecosim
```

For an explicitly requested x86 Linux target, add `--platform linux/amd64`.
The launcher uses image `ats-helper-ubuntu:24.04` and volume
`ats-helper-build-<image architecture>` mounted at `/ats-build`. Keep validation
on the same architecture and volume. `--fresh` rebuilds the compiler image
without layer cache; it does not clear source, TPLs, or installed libraries.

Use the tool's persistent execution session to monitor long builds. If a detached
container is necessary, preserve the launcher's image, mounts, environment, and
command, choose a unique task-specific name, and retain its exit status and logs.
Do not report success merely because a detached container started. Give progress
updates until the build finishes. TPL compiler output may be redirected to
`/ats-build/build/tpls/<package>/<package>-timestamps/*-build-*.log`; quiet
top-level output alone does not mean a hang.

On failure, inspect the failing stage's logs, address an evidenced build problem,
and resume incrementally. Preserve logs and completed objects. Avoid blind
retries, global Docker pruning, unrelated container changes, and deleting caches.
Use the environment's network approval flow if required for dependency downloads.

## Verify and report

Require a successful build exit. The build script verifies the installed
`/ats-build/install/amanzi/bin/ats`, resolves shared libraries with `ldd`, prints
version information, and confirms `EcoSIM for ATS` in `ats --list_pks`.

Also check two-rank MPI startup and a short physics run using
[references/validation.md](references/validation.md). It records the input
adjustment and the full-regression limitation found during the validated build.
Treat compilation, executable startup, smoke checks, and regression comparisons
as distinct results. Do not change reference results or model physics to make a
build report pass. A failed science case does not erase an installed binary;
report the failure and keep scientific debugging within the user's request.

Preserve `/ats-build/logs/ats-ecosim-build.log`, `source-revisions.txt`,
`ats-libraries.txt`, and `ats-pks.txt`, plus runtime diagnostics. Export relevant
logs to the ignored `build-logs/` directory when useful. Report the architecture,
three source commits, executable path inside Docker, checks actually run and
their outcomes, unresolved failures, and the command to rebuild or open a shell.
