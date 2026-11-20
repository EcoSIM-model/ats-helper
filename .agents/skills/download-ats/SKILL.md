---
name: download-ats
description: Recursively download ATS through Amanzi and the EcoSIM source selected by Amanzi, including nested submodules of both repositories. Use for requests such as "download ats-ecosim", "download ATS and EcoSIM", or clone/download ATS source. Building and installing are separate tasks.
---

# Download ATS and EcoSIM source

Use `https://github.com/amanzi/amanzi.git` as the parent repository. ATS is a
submodule at `src/physics/ats`; cloning Amanzi alone does not populate ATS.
Also download EcoSIM as a separate sibling repository. It is an external build
dependency, not an Amanzi or ATS submodule.

Treat "download ats-ecosim" and obvious spelling variants as a request for the
complete workflow below: download both ATS and EcoSIM with recursive submodules.
Do not stop after downloading only one repository.

## Select the execution environment

Honor an explicitly requested Docker container first. Otherwise check the
workspace's `.docker/container.json` written by `run_docker.sh`. Parse it as
data and inspect the full ID in its recorded Docker context. Confirm that it is
running, its image matches the recorded image identity, and its writable
`/ats-helper` mount points to this workspace. Check `/ats-build` is the expected
build volume when downloading in preparation for a build. The JSON state is a
snapshot; live Docker inspection determines whether the container is usable.

When a matching session is running, execute all source inspection, Git clones,
revision selection, recursive submodule operations, and verification through
`docker exec` in that same container, with `/ats-helper` as the working directory.
Use `/ats-helper/repos/amanzi` and `/ats-helper/repos/ecosim` as the default
container destinations. Also report their corresponding host paths through the
verified bind mount. Docker orchestration and reading the session record happen
on the host; downloads happen in the selected container.

Carry the selected full container ID into a subsequent `build-ats-ecosim` task;
do not start another container between downloading and building. If a record is
stale or mismatched, explain that it cannot identify a usable session. For an
explicitly requested Docker workflow, create a configured session when authorized
or report the missing environment; do not silently fall back to host downloads.
Without a session record or a Docker request, the host workflow below still
applies. An explicit host-only request overrides automatic Docker reuse.

## Choose the destination and revision

- Honor the user's destination. Otherwise use `repos/amanzi` under the current
  workspace and report its absolute path.
- Put EcoSIM at `repos/ecosim` by default. For a custom Amanzi destination, use
  an `ecosim` directory beside it unless the user supplies another location.
- Use Amanzi's default branch unless the user specifies an Amanzi branch, tag,
  or commit. Do not guess a release number.
- An ATS revision and an Amanzi revision are different. If the user requests a
  particular ATS release without a corresponding Amanzi revision, consult the
  upstream release documentation to establish the pairing before checking out
  source. Do not apply an ATS tag to the Amanzi repository.

For either destination that already exists, inspect its repository root, origin,
working tree, and submodule status first. Reuse only the intended repository.
Preserve local work; do not reset, clean, delete, change remotes, or switch
revisions in a dirty checkout. If unrelated files or local changes prevent
completion, report the conflict and request a different destination or direction.
Do not pull or advance an existing checkout unless the user requested an update.

## Download Amanzi and ATS

For a new destination, run the following with `AMANZI_SRC_DIR` set to the chosen
absolute path:

```bash
mkdir -p "$(dirname "$AMANZI_SRC_DIR")"
git clone --recursive https://github.com/amanzi/amanzi.git "$AMANZI_SRC_DIR"
```

If the user supplied an Amanzi revision, check it out before the final recursive
submodule update. For a reproducible checkout of an available branch, tag, or
commit:

```bash
git -C "$AMANZI_SRC_DIR" checkout --detach "$AMANZI_REF"
```

The recursive clone populates ATS and its nested submodules. After selecting a
different Amanzi revision, or when completing an existing checkout, synchronize
submodule URLs and populate the commits recorded by their parent repositories,
subject to the existing-checkout protections above:

```bash
git -C "$AMANZI_SRC_DIR" submodule sync --recursive
git -C "$AMANZI_SRC_DIR" submodule update --init --recursive
```

Do not add `--remote`: it would select newer submodule commits instead of the
versions recorded by Amanzi. Downloading source does not require running
`bootstrap.sh`, `build_ATS_generic.sh`, or installing build dependencies.

## Download EcoSIM automatically

Read `config/SuperBuild/TPLVersions.cmake` in the selected Amanzi checkout.
Use its active `ECOSIM_GIT_REPOSITORY` and `ECOSIM_GIT_TAG` assignments as
`ECOSIM_REPOSITORY` and `ECOSIM_REF`. Ignore commented-out alternatives. Read
these values as data; do not source the CMake file as shell code. If they are
missing, ambiguous, or require unresolved CMake substitutions, inspect the
relevant build configuration or report the missing information rather than
guessing a branch.

At the time of writing, Amanzi `master` selects
`https://github.com/EcoSIM-model/EcoSIM` and `agraus/PrescribedPhenology`.
The checked-out Amanzi configuration takes precedence over these examples. For the paired
runs of `cases/pft_tests`, the Amanzi, ATS and EcoSIM URLs and commits to download are the
ones pinned in `cases/pft_tests/case.json`, not Amanzi's defaults.
Honor an explicit user-specified EcoSIM URL or revision and report that override.

For a new EcoSIM destination, set `ECOSIM_SRC_DIR` to the chosen absolute path,
then run:

```bash
mkdir -p "$(dirname "$ECOSIM_SRC_DIR")"
git clone --recursive "$ECOSIM_REPOSITORY" "$ECOSIM_SRC_DIR"
git -C "$ECOSIM_SRC_DIR" fetch origin "$ECOSIM_REF"
git -C "$ECOSIM_SRC_DIR" checkout --detach FETCH_HEAD
git -C "$ECOSIM_SRC_DIR" submodule sync --recursive
git -C "$ECOSIM_SRC_DIR" submodule update --init --recursive
```

Use `--recursive` on both new clones. After a revision change, the subsequent
`submodule update --init --recursive` ensures nested submodules match that
revision rather than the initially cloned default branch. Do not combine
`--no-checkout` with the recursive clone, because Git skips submodule
initialization in that mode.

Fetching the selected ref explicitly supports a branch that is not the remote's
default branch, a tag, or a commit. Record the resolved commit because a branch
can move. For an existing EcoSIM checkout, check its current commit against the
selected ref using its local refs when available. Report a mismatch or an
unverifiable ref without silently advancing or replacing the checkout.

Downloading EcoSIM does not enable the EcoSIM PK. Keep the recorded ATS
revision unless a different one was requested; do not automatically switch to
`agraus/ecosim_pk`. An EcoSIM-enabled build needs an ATS revision containing the
PK and `--enable-ecosim`. Amanzi's build system normally downloads EcoSIM into
its own TPL build tree; this sibling checkout is a source copy, not an automatic
override of that build dependency.

If a network or authentication error occurs, use the environment's supported
network approval flow when available. Otherwise report the failing command and
what is needed to resume. Preserve partial downloads for inspection or retry.

## Verify and report

```bash
git -C "$AMANZI_SRC_DIR" remote get-url origin
git -C "$AMANZI_SRC_DIR" rev-parse HEAD
git -C "$AMANZI_SRC_DIR" submodule status --recursive
git -C "$AMANZI_SRC_DIR/src/physics/ats" rev-parse HEAD
test -f "$AMANZI_SRC_DIR/src/physics/ats/CMakeLists.txt"
git -C "$ECOSIM_SRC_DIR" remote get-url origin
git -C "$ECOSIM_SRC_DIR" rev-parse --show-toplevel
git -C "$ECOSIM_SRC_DIR" rev-parse HEAD
git -C "$ECOSIM_SRC_DIR" submodule status --recursive
test -f "$ECOSIM_SRC_DIR/CMakeLists.txt"
```

Confirm the ATS submodule is listed and populated. A leading `-`, `+`, or `U` in
submodule status means it is uninitialized, differs from its recorded commit, or
has a conflict; resolve within the requested scope or report the incomplete
state. Do not report success based only on `rev-parse` inside an empty submodule
directory, since Git can find the parent repository there.

Confirm EcoSIM's repository root is the requested destination, its origin matches
the selected repository, and a new checkout's HEAD matches the fetched ref.
Apply the same submodule checks to EcoSIM if it has submodules.

Report the absolute Amanzi, ATS, and EcoSIM source paths and all three commit
hashes, plus the selected EcoSIM ref. Report partial completion if either ATS or
EcoSIM could not be downloaded or verified; do not declare the whole download
successful when EcoSIM is missing.

## Upstream references

- [Amanzi repository](https://github.com/amanzi/amanzi)
- [ATS submodule declaration](https://github.com/amanzi/amanzi/blob/master/.gitmodules)
- [ATS installation guide](https://github.com/amanzi/amanzi/blob/master/INSTALL_ATS.md)
- [EcoSIM dependency selection](https://github.com/amanzi/amanzi/blob/master/config/SuperBuild/TPLVersions.cmake)
- [EcoSIM repository](https://github.com/EcoSIM-model/EcoSIM)
