# Mutating the architecture rules

**Status:** audited. 44 rules, 43 hold. One had lost its subject (#181), and one
claim the split rested on had no rule at all (#179). Both closed.
**Date:** 2026-08-18

`test_architecture.py` is 44 rules and the only thing standing between the layer
diagram and prose. Nothing had ever asked whether the rules themselves fire.

They should have been asked, because this file's own history says so. Three of
its rules exist *because* a rule stopped working silently: `_modules_in` learned
to recurse after nine rules would have dropped a subpackage while all nine kept
passing; `test_no_rule_here_is_parametrized_over_nothing` exists because the
`server/`→`presentation/` rename took one rule from fifteen modules to zero;
`_repository_root` was rewritten after three mutations of the old walk survived.
Every one of those was found by mutation, and none by the suite going green.

## Method

Plant the violation each rule forbids in the real tree, run the file, record
which rules name themselves. A harness applied each mutation, asserted it landed
before trusting the result, restored the file afterwards and verified the tree
was clean. 41 mutations, eight batches and one planted by hand; 40 landed, one
named a string the file did not contain and was rewritten.

Three kinds of mutation, because the rules are three kinds of thing:

| rule shape | mutation |
|---|---|
| reads real code (`domain` imports only stdlib) | plant a real import, call or class |
| reads packaging or config | plant a real file, or break a real declaration |
| asserts a helper on constructed inputs | break the helper the rule exists to guard |

The third only became clear during the audit. `test_a_subpackage_is_judged_by_its_own_area`
and two others were first classified as rules over real code; they never read
`src/` at all. They are self-tests of `_area_of`, `_undeclared` and `_module_id`,
and the only mutation that means anything to them is one that breaks the helper.

## What it found

One defective rule, and one claim that was never a rule. Worth separating: only
the first is a failure of this file, and counting them together would overstate
what the audit turned up.

**`test_the_server_uses_the_library_only_through_its_public_api` had stopped
covering the server.** `_consumer_modules` read `SRC / name`; the service became
`kingfisher-service`, a wheel outside that root; the rule kept its name, kept
passing, and ran against four CLI files. This is the second time that rule has
lost its subject — the first, during the rename, took it to *zero* modules and
produced the empty-parametrisation guard. Going to four rather than zero is why
that guard could not fire. Fixed in #181 by naming the roots and asserting each
still yields modules, because a count cannot tell "moved" from "shrank".

**The harness boundary's one-way claim had decayed.** `infrastructure/harness/`
earns its folder by carrying a rule, and the note that created it documented one
outward edge and reasoned about it. There were three; two arrived nine hours
later. Nothing was wrong — the enforced rule is scoped to foreign packages
deliberately — but nothing made a fourth edge a decision either. Fixed in #179.

That one is not a rule that broke — no rule existed. It is the same shape as
what this file already does elsewhere: a claim in a comment, turned into a test
so it cannot decay quietly.

The other 43 rules fired on the first correctly-built mutation.

## Two results worth keeping

**Getting the root wrong now fails loudly.** Pointing `_repository_root` out of
the checkout fails **17 rules at once**. That is the failure mode both historical
gaps came from, and it is no longer quiet.

**`test_the_root_holds_this_file` and `test_the_root_is_the_nearest_one_not_an_outer_one`
are not redundant.** Climbing *up* to `/Users` leaves this file still inside the
root, so the first rule passes; only climbing *sideways* to a sibling clone fires
it. Each covers the failure its docstring claims and neither covers the other's.

## Limits of this audit

Stated because "44/44" reads stronger than it is.

- A rule fires on *a* violation, not every violation. Coverage here is one
  mutation per rule, occasionally two.
- Three of the mutations were wrong before they were right. Two planted string
  constants where the rule correctly looks for an `ast.Call` or a real
  `monkeypatch.setattr` line; one mutated an assertion rather than planting a
  violation. Each time the rule was right and the mutation was adjacent to the
  claim — the same failure this repository keeps finding, arriving one level up,
  in the test of the test.
- So a survival is a hypothesis about the rule **or** the mutation. Every
  survival here was resolved by reading the rule's implementation and
  re-planting, never by assuming the rule was weak.

## What this suggests for the next rule

Everything found today, in this file and outside it, was a measurement reading
something *adjacent* to its claim:

- a collector reading the wrong root;
- an import scan that kept the module of an `ImportFrom` and discarded the names,
  so `from pkg.harness import agent` arrived as the bare package — the form
  nearly every such import is written in, and a first draft of the new harness
  rule detected none of them and passed;
- a grep whose zeros were the pattern rather than the code;
- a mutation planting a string where the rule looked for a call.

Two habits follow, and both are cheap:

1. **Feed a new rule one example you know it should catch, before trusting it.**
   A rule that has never failed has never been shown to be about anything.
2. **Where nothing in the tree violates the rule, give the predicate a self-test
   over named inputs.** Gutting `_reaches_past_the_public_api` to `return False`
   passed every module it was pointed at, because nothing violates it — a rule
   switchable off in silence. `kingfisher_service.app` is the case worth naming
   in such a table: it looks like a reach and is not, and a prefix match on
   `"kingfisher"` would fail the consumer most subject to the rule for importing
   itself.
