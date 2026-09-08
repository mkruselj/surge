"""
Tests for Surge XT Python bindings.
"""

import numpy as np
import surgepy


def test_getVersion():
    version = surgepy.getVersion()
    assert isinstance(version, str)


def test_createSurge():
    surgepy.createSurge(44100)


def test_render_note():
    """
    Test rendering a note into a buffer.
    """
    s = surgepy.createSurge(44100)
    n_blocks = int(2 * s.getSampleRate() / s.getBlockSize())
    buf = s.createMultiBlock(n_blocks)
    s.playNote(0, 60, 127, 0)
    s.processMultiBlock(buf)
    assert not np.all(buf == 0.0)

def test_render_note_with_input():
    """
    Test rendering a note with an input buffer into an output buffer.
    """
    s = surgepy.createSurge(44100)
    n_blocks = int(2 * s.getSampleRate() / s.getBlockSize())
    in_buf = s.createMultiBlock(n_blocks)
    in_buf[0] = np.random.normal(0, 1000, size=in_buf[0].size)
    in_buf[1] = np.random.normal(0, 1000, size=in_buf[1].size)
    out_buf = s.createMultiBlock(n_blocks)
    s.playNote(0, 60, 127, 0)
    s.processMultiBlockWithInput(in_buf, out_buf)
    assert not np.all(out_buf == 0.0)


def test_default_mpeEnabled():
    """
    Test that mpeEnabled flag is False by default.
    """
    s = surgepy.createSurge(44100)
    assert s.mpeEnabled is False


def test_set_mpeEnabled():
    """
    Test that setting mpeEnabled really changes its value.
    """
    s = surgepy.createSurge(44100)
    s.mpeEnabled = True
    assert s.mpeEnabled is True


def test_default_tuningApplicationMode():
    s = surgepy.createSurge(44100)
    assert s.tuningApplicationMode == surgepy.TuningApplicationMode.RETUNE_MIDI_ONLY


def test_set_tuningApplicationMode():
    s = surgepy.createSurge(44100)
    s.tuningApplicationMode = surgepy.TuningApplicationMode.RETUNE_ALL
    assert s.tuningApplicationMode == surgepy.TuningApplicationMode.RETUNE_ALL


def _render_seeded(seed, osc_type):
    """
    Render half a second of a single note with a given osc type, having seeded
    the RNG. Returns a copy of the buffer, since createMultiBlock hands back a
    view onto memory the synth owns.
    """
    s = surgepy.createSurge(44100)
    patch = s.getPatch()
    s.setParamVal(patch["scene"][0]["osc"][0]["type"], osc_type)
    s.seedRNG(seed)

    n_blocks = int(0.5 * s.getSampleRate() / s.getBlockSize())
    buf = s.createMultiBlock(n_blocks)
    s.playNote(0, 60, 127, 0)
    s.processMultiBlock(buf)
    return np.array(buf, copy=True)


def test_seedRNG_renders_are_reproducible():
    """
    The same seed must give a bit-identical render. Sample and hold noise is
    used because it draws from the global std::rand() rather than from the
    storage RNG, so it exercises the part of seed_rand() that is easiest to
    forget.
    """
    first = _render_seeded(8675309, surgepy.constants.ot_shnoise)
    second = _render_seeded(8675309, surgepy.constants.ot_shnoise)

    assert not np.all(first == 0.0)
    assert np.array_equal(first, second)


def test_seedRNG_different_seeds_render_differently():
    """
    Guards against seed_rand() being a no-op, which would make the test above
    pass for the wrong reason.
    """
    first = _render_seeded(1, surgepy.constants.ot_shnoise)
    second = _render_seeded(2, surgepy.constants.ot_shnoise)

    assert not np.all(first == 0.0)
    assert not np.array_equal(first, second)
