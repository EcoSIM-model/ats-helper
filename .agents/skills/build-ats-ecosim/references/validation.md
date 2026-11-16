# ATS–EcoSIM runtime validation

Use this after installation. Run commands in a container using the launcher's
compiler image, matching architecture, source mount, and persistent build volume.
Load `/etc/profile.d/ats.sh` through a login shell. Do not start another build
against that volume while checking the executable.

## Startup checks

The build script checks libraries and PK registration. Confirm a fresh container
can also initialize MPI:

```bash
set -euo pipefail
source /etc/profile.d/ats.sh
ats --print_version
ats --list_pks > /ats-build/logs/ats-pks.txt
grep -F 'EcoSIM for ATS' /ats-build/logs/ats-pks.txt
mpiexec --allow-run-as-root --stdin none -n 2 ats --version
```

The root option is for the default Docker shell. This MPI check exercises startup;
it does not establish that a coupled simulation runs on multiple ranks.

## One-day coupling check

For the known `agraus/ecosim_pk` workflow, the regression repository's
`origin/agraus/ecosim_pk` ref contains `13_ecosim/ecosim_freeze_thaw.xml` and its
data. Inspect that ref and record its hash. If it is missing, fetch it explicitly
or report that this validation fixture is unavailable; do not replace the user's
test checkout. For a different ATS revision, check its input contract before
applying this procedure.

Extract the fixture into a new directory in the build volume:

```bash
test_repo="$ATS_SRC_DIR/testing/ats-regression-tests"
validation_dir=$(mktemp -d /ats-build/validation-XXXXXX)
git -c safe.directory="$test_repo" -C "$test_repo" \
    rev-parse origin/agraus/ecosim_pk > "$validation_dir/test-revision.txt"
git -c safe.directory="$test_repo" -C "$test_repo" \
    archive origin/agraus/ecosim_pk 13_ecosim | tar -x -C "$validation_dir"
export ATS_ECOSIM_VALIDATION_DIR="$validation_dir"
```

The older fixture defines constant evaluators for `surface-ecosim_source` and
`surface-ecosim_water_source`. The current PK owns these fields and rejects those
definitions. Remove only those obsolete constants in a generated input, and
shorten the end time to one day. Keep the extracted original and gold checkpoints.

```bash
python3 - <<'PY'
import os
from pathlib import Path
import xml.etree.ElementTree as ET

base = Path(os.environ['ATS_ECOSIM_VALIDATION_DIR']) / '13_ecosim'
tree = ET.parse(base / 'ecosim_freeze_thaw.xml')
root = tree.getroot()
evaluators = root.find("./ParameterList[@name='state']/ParameterList[@name='evaluators']")
if evaluators is None:
    raise SystemExit('Unexpected input schema: state evaluators not found')
removed = []
for element in list(evaluators):
    if element.get('name') in ('surface-ecosim_source', 'surface-ecosim_water_source'):
        evaluator_type = element.find("./Parameter[@name='evaluator type']")
        if evaluator_type is None or evaluator_type.get('value') != 'independent variable constant':
            raise SystemExit('Inspect the changed evaluator definition before migrating this input')
        removed.append(element.get('name'))
        evaluators.remove(element)
driver = root.find("./ParameterList[@name='cycle driver']")
if driver is None or driver.find("./Parameter[@name='end time units']").get('value') != 'd':
    raise SystemExit('Unexpected cycle-driver time units')
driver.find("./Parameter[@name='end time']").set('value', '1.0')
tree.write(base / 'ecosim_smoke.xml', encoding='unicode')
(base / 'smoke-input-changes.txt').write_text(
    'End time set to one day. Removed obsolete constant evaluators: '
    + ', '.join(removed) + '\n'
)
print('Generated smoke input; removed:', removed)
PY

case_dir="$validation_dir/13_ecosim"
mkdir "$case_dir/smoke"
cd "$case_dir/smoke"
ats --xml_file=../ecosim_smoke.xml > smoke.stdout 2>&1

python3 - <<'PY'
import h5py
import numpy as np

with h5py.File('checkpoint_final.h5', 'r') as checkpoint:
    if not np.isclose(np.asarray(checkpoint.attrs['time']).item(), 86400.0):
        raise SystemExit('Smoke run did not reach one simulated day')
    print('Smoke check passed; final cycle:', checkpoint.attrs['cycle'])
PY
```

Check the simulation exit status as well as the checkpoint. On failure, save
`smoke.stdout` and report the last diagnostic. Report any input migration; this
is a smoke check with adapted input, not an unmodified regression-suite pass.

## Recorded baseline and limitation

The successful Ubuntu 24.04 ARM64 build used:

| Component | Commit |
| --- | --- |
| Amanzi | `e38fee614cbf0829b51405020a24011ba2b75d1e` |
| ATS | `07f491d691e4cbcc97a080614017e70f21a26c4e` |
| EcoSIM | `fe3afa72e260dc002ceb9751638b7bc0840f9e46` |
| Regression fixtures | `7bd1fcd26705c40cdd68fee1df28f106ac44a7a8` |

GCC 13.3 and CMake 3.28.3 built the code. Library resolution, PK registration,
two-rank MPI startup, and the adapted one-day case passed. The one-day case
completed 24 steps and wrote a checkpoint at 86400 seconds.

The original full case failed on the obsolete evaluator definitions. After the
same input correction, the 200-day case stopped with
`TimeStepManager: manually prescribed timestep failed` after temperatures exceeded
the constitutive model's bounds. It did not reach reference-checkpoint comparison.
The full regression has not passed for this baseline. These are historical
results; distinguish them from checks run on the current build.

Do not rerun the known failing full case by default for each rebuild. Run it when
requested or when a relevant change justifies reevaluation. Preserve reference
checkpoints and log the result independently of build success. Changes to model
physics, bounds, timestep policy, or tolerances require their own task scope.
