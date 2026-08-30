"""Reliability tests for steps.terrain_gen.macro_topo (macro terrain generation)."""


def test_macro_generate_deterministic_and_coarse():
    from vcmi_mapgen.steps.terrain_gen import macro_topo as MT
    g1 = MT.generate(48, 48, seed=1)
    g2 = MT.generate(48, 48, seed=1)
    assert g1 == g2, "macro terrain must be seed-deterministic"
    rep = MT.report(g1)
    # the §4.3 gate: the macro layer must NOT fragment (the markov failure mode)
    assert rep["big_share"] >= 0.7, rep
