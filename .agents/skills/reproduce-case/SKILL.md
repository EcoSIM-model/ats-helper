---
name: reproduce-case
description: Reproduce a named ATS–EcoSIM case bundled in ats-helper by fetching exact recursive source revisions, building and running in Docker, and comparing results. Use for requests such as "reproduce 200day-adaptive"; ordinary downloads and development builds use their own skills.
---

# Reproduce an ATS–EcoSIM case

From the `ats-helper` root, use the maintained command:

```bash
./reproduce.sh 200day-adaptive
```

Replace the name with the requested folder under `cases/`. Run `./reproduce.sh
--help` to list cases. If the user has not named one and several are available,
clarify the case; do not invent a case definition. Docker and Git must be
available on the host. Start with `JOBS=2` unless the user specifies otherwise.
The initial dependency build can take hours and needs network access and ample
Docker disk space. Honor an explicit architecture with `--platform linux/arm64`
or `--platform linux/amd64`.

The command uses the existing Docker launcher and build script. All model Git,
recursive submodule, build, and simulation operations run in Docker. A dedicated
volume identified by case manifest, architecture, and compiler image holds source and build
products; it leaves `repos/` and the development build volume alone. Do not
redirect this command into an unrelated running development container or run a
second builder against its case volume. The launcher records its exact container
in `.docker/container.json`, with mode `reproduce` and its own `/ats-build` volume.

Read `cases/<name>/case.json` for the locked revisions, input checksums,
comparison tolerances, reference environment, and known limitations. The case's
EcoSIM commit takes precedence over Amanzi's older named branch through the
build script's local Git mirror. Never replace pins with current branch tips,
regenerate the timestep data, alter solver settings, or update reference outputs
to obtain a pass. For `200day-adaptive`, preserve the documented historical
EcoSIM clock behavior; a corrected clock needs a separate case version.

Monitor the command until it exits. A completed download (`--prepare-only`),
successful build, completed simulation, and passing checkpoint comparison are
different outcomes. On failure, inspect the recorded stage and logs, address
the evidenced operational problem, and rerun the same command to reuse its
cache. Preserve unexpected source changes; do not reset or clean a checkout.

Report the actual outcome, source commits, architecture, and clickable path to
`build-logs/reproduce/<case>/<run>/index.html`. That directory also contains
`report.json`, build logs, `model.stdout`, `verification.json`, checkpoints,
`timesteps.csv`, and PNG/PDF plots after a completed run. A strict comparison
failure on another platform is a failure to reproduce the numerical baseline,
even if the simulation reaches day 200. Source/input pins do not lock all Ubuntu
packages or transitive downloads, and do not guarantee bitwise results across
machines.
