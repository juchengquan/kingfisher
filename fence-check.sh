#!/bin/sh
# Tell "this kernel has no fence" apart from "this validator is broken", and
# refuse to certify a run that exercised nothing.
#
# Both conditions exit 1 from pytest: a fixture that fails to import errors at
# setup, and a genuinely absent fence reports a failure. The first measured run
# of this image hit that -- `evals/` was missing from the build context, so every
# test that would have run errored at setup, the two unfenced controls never
# executed, and the container exited 1 looking precisely like an honest "no
# Landlock here". A validator whose own breakage is indistinguishable from the
# condition it validates is worse than none, because the exit code gets
# believed. So errors get their own code: 1 stays "no fence, or an escape got
# through", and 2 means do not trust this run at all.

set -u

log=/tmp/fence-check.log

# Forced here, not inherited, and this is the load-bearing line in the file.
# Measured: `docker compose run -e CI=0` makes `test_ci_ran_against_a_real_fence`
# skip itself, pytest then exits 0 with all seventeen tests skipped or trivial,
# and this script printed "FENCE HELD" on a kernel with no Landlock and no
# bubblewrap. `ENV CI=true` in the Dockerfile is a default, and a default is
# exactly what `-e`, a compose `environment:` block, or a Kubernetes manifest
# setting CI for its own reasons will override. The suite's anti-vacuous guard
# is only armed when CI is true, so arming it is not the caller's decision.
CI=true
export CI

pytest tests/linux/ -rs --tb=short > "$log" 2>&1
code=$?
cat "$log"

# Matched against pytest's own summary line and setup banners rather than the
# word "error" anywhere, which appears in assertion text on a run that worked.
if grep -qE "ERROR at setup|ERROR at teardown|^E +ImportError|^=+ .*[0-9]+ errors?" "$log"; then
    echo
    echo "VALIDATOR BROKEN: tests errored at setup or collection rather than running."
    echo "Nothing here is a statement about the fence -- fix the image, then re-run."
    exit 2
fi

# 5 is pytest's "no tests collected", which is the quietest way this could lie.
if [ "$code" -eq 5 ]; then
    echo
    echo "VALIDATOR BROKEN: pytest collected no tests, so this proved nothing."
    exit 2
fi

# A second opinion on success, independent of the exit code. `code` being 0 says
# nothing failed; it does not say anything ran against a real fence, and those
# come apart precisely when the escapes skip. `test_the_half_that_ran_is_named_in
# _the_log` prints which mechanism was live, so a pass with neither named is a
# pass over an empty shelf. Erring toward refusing to certify is deliberate: a
# false "broken" costs a re-run, a false "held" costs the thing this protects.
if [ "$code" -eq 0 ] && ! grep -qE "fence coverage:.*(Landlock=yes|bubblewrap=yes)" "$log"; then
    echo
    echo "VALIDATOR BROKEN: the run passed without naming a live fence, so the"
    echo "escapes skipped and nothing was exercised. Do not read this as a pass."
    exit 2
fi

if [ "$code" -eq 0 ]; then
    echo
    echo "FENCE HELD: a fence was live, and every escape on the list failed against it."
else
    echo
    echo "NO FENCE, OR AN ESCAPE GOT THROUGH: read the failures above."
    echo "A kernel below Landlock ABI 6 with no bubblewrap reports this, and it means"
    echo "KINGFISHER_SHELL_SANDBOX=auto would run the agent's shell unconfined here."
fi
exit "$code"
