"""NetConfig plan slice (Phase 2): semantic parsing -> typed change
plan -> deterministic compilation for VLANs and trunks.

Modules: state (typed device state), schema (plan validation),
apply (S' = apply(S, Δ)), render (IOS XE CLI), diff (state diff for
scoring). See docs/PLAN_SLICE.md.
"""
