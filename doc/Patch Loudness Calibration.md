# Patch Loudness Calibration

Design notes for the automated LUFS measurement and normalization tool

## Goal

Bring the factory patch library into line on perceived loudness, and give us a repeatable way to check new patches against that line. This is an internal maintainer tool, not something shipped to patch authors, so it does not need a stable published protocol - we can change the rules whenever we learn something.

Two separate criteria, deliberately kept apart:

- **Loudness anchor** - what a patch sounds like when played normally. This is what gets corrected, by writing an offset into the patch's global volume.
- **Headroom safety** - whether a patch clips or spikes when hammered. This is only ever reported, never auto-corrected. See "Why the safety pass must not feed back" below.

## Tooling

The tool is a Python script driving `surgepy`, living in `scripts/patch-loudness/`, rather than a C++ mode in `surge-testrunner`.

### Why Python

`surgepy` already exposes everything the protocol needs:

| Need | Binding |
| --- | --- |
| Load and save patches | `loadPatch(path)` / `savePatch(path)` - takes a path, so the script enumerates the FXP tree itself |
| Render | `processMultiBlock(buf)` into a numpy buffer |
| Audio-input patches | `processMultiBlockWithInput(in, out)` (`surgepy.cpp:787`) |
| Notes and velocity | `playNote` / `releaseNote` / `allNotesOff` |
| MPE | `mpeEnabled` property (`surgepy.cpp:1200`), `channelAftertouch`, `channelController` for timbre |
| Patch introspection | `getPatch()` returns a dict tree containing `polymode` (`:311`) and `scenemode` (`:347`); `ot_*` and `adsr_ampeg` constants are exported |
| Correction | `setParamVal` on global volume, then `savePatch` |

`surgepy` is also built on every PR (`build-pr.yml` targets it on Windows and Linux), so it is a maintained target rather than something quietly bit-rotted.

The decisive argument is the one in "Use `pyloudnorm`" below - it removes the riskiest piece of the project. Secondary benefits: the sweep analysis (working range selection, the stabilization-derivative test, per-note windowing) is a few lines of numpy instead of a chunk of C++, and enumerating FXP files in Python sidesteps the `playOnEveryPatch` index bug entirely.

There is no performance concern. `processMultiBlock` renders many blocks per call, so the audio loop stays in C++; only per-note event scheduling happens in Python.

### Bootstrap

Anyone who has built the repo should be able to run the tool immediately, so the script bootstraps its own environment.

- **Virtual environment**: `python -m venv --system-site-packages .venv` inside `scripts/patch-loudness/`. The `--system-site-packages` flag is exactly the behavior wanted - the venv can see the global interpreter's packages, so `pip` installs only what is actually missing rather than duplicating an existing numpy or scipy.
- **Dependencies**: a `requirements.txt` listing `numpy`, `scipy` and `pyloudnorm`, specified with lower bounds rather than exact pins so that an already-installed global version satisfies them.
- **Finding surgepy**: two paths, tried in order:
  1. Probe the usual build directories (`build/`, `build/Release`, `cmake-build-*`) for a built `_surgepy` extension and, if found, drop a `.pth` file into the venv pointing at it. Takes seconds.
  2. Otherwise fall back to `pip install <repo>/src/surge-python`, which is supported already through scikit-build (`src/surge-python/setup.py`). This works for someone who has never built the repo, but performs a full CMake build of Surge and takes on the order of ten to twenty minutes.
  3. A `--surgepy-path` override covers unusual build layouts.
- `.venv/` needs adding to `.gitignore`; there is currently no venv entry there.

Note that `surgepy` is not part of a default local configure - it needs `-DSURGE_BUILD_PYTHON_BINDINGS=TRUE`. For an internal maintainer tool this is acceptable, but it does mean the setup is not quite zero-effort.

## What already exists

Besides the `surgepy` bindings above, there is prior art in C++ worth reading even though the tool no longer builds on it:

- `NonTest::statsFromPlayingEveryPatch()`
  (`src/surge-testrunner/HeadlessNonTestFunctions.cpp`) is this tool's skeleton in miniature - it plays a C major scale on every factory patch and prints peak, L1 and RMS.
- `NonTest::restreamTemplatesWithModifications()` is the precedent for bulk rewriting FXP files, which is what the correction phase does.
- `Surge::Headless::playOnEveryPatch()` (`src/surge-testrunner/Player.cpp`) is the C++ equivalent of the enumerate-and-render loop.

So the scaffolding is cheap either way. Essentially all of the difficulty is in the test protocol, which is what the rest of this document is about.

Unrelated housekeeping noticed while reading that code: `playOnEveryPatch()` reports `patch_list[patchOrdering[i]]` but plays patch index `i` (`Player.cpp:165-174`), so names and audio do not line up. The Python tool enumerates FXP files itself and never touches this, so it is not on the critical path - but the bug is real and deserves its own commit.

## Measurement

### Use `pyloudnorm`, do not implement BS.1770 ourselves

The issue links `klangfreund/LUFSMeter`. That is a JUCE realtime metering plugin, and vendoring it as a submodule to obtain four filter coefficients would be the wrong trade. But writing BS.1770 by hand is also the wrong trade, and for a sharper reason: a hand-rolled K-weighting with a subtly wrong coefficient produces plausible-looking numbers that are systematically wrong across all 3000+ patches, and nothing downstream would catch it. That is the highest-risk piece of correctness in the whole project.

Since the tool is written in Python (see "Tooling" below), `pyloudnorm` supplies a conformance-tested BS.1770-4 implementation and the risk disappears.

What `pyloudnorm` does *not* supply, and we add ourselves:

- **Short-term (3 s) loudness** - run its meter across sliding windows.
- **True peak** - a 4x `scipy.signal.resample_poly` upsample ahead of peak detection, per Annex 2 of the same spec. Worth having rather than sample peak, since the target below is quoted in dBTP.

Both are a few lines each, and neither carries the silent-systematic-error risk that the K-weighting filter does.

### Report three numbers per patch

Integrated LUFS on its own lets a patch pass at -19 while true-peaking at +2 dBTP. Emit:

| Metric | Purpose |
| --- | --- |
| Integrated LUFS (gated) | the loudness anchor; drives the volume trim |
| Max short-term (3 s) | catches "quiet pad, vicious transient" |
| True peak (dBTP) | clipping safety |

### Target

- **-19 LUFS integrated**, matching what NI use for NKS preset previews.
- **-3 dBTP** as a reported ceiling.

The dBTP figure is a **flag, not a constraint**. NKS applies -3 dBTP to a rendered, limited preview file; we would be applying it to a live patch driven at worst case, which is much harsher. A fat unison patch at velocity 127 in an 8 note chord will exceed it routinely. If we enforced it by trimming, the ceiling would silently become the real normalizer and drag a large part of the library well below -19 LUFS at normal playing levels.

## Test protocol

### Scope

Every factory category is measured and normalized **except Templates**. Templates are starting points rather than finished patches, so there is no "correct" level for them to hit and trimming them would work against what they are for.

Tutorials *are* included. Some of them are genuinely decent-sounding patches that people will play rather than merely read, and a user browsing through them should not run into a level jump.

Audio input patches are measured, but against a different criterion - see "Audio input patches" below.

### Two passes, two velocities

| Pass | Condition | Measures | Consequence |
| --- | --- | --- | --- |
| Anchor | velocity 100, 4-note chord (mono patches: single line) | integrated LUFS | sets the `patch.volume` trim |
| Safety | velocity 127, 8-note chord (or polylimit if deliberately lower) | true peak, max short-term | flags only |

#### Why not calibrate loudness at velocity 127 alone

Peak-at-worst-case is a clipping safety criterion, not a perceptual match criterion. Camel Audio's Alchemy spec (-2 dB peak) was solving the former; we want both, and they need different measurement points. Alchemy could not separate them because integrated LUFS was not standardized until BS.1770 (2006) and R128 (2010).

Calibrating loudness at 127 systematically penalizes velocity-sensitive patches. If patch A routes velocity deeply into amplitude and patch B barely at all, trimming both to match at 127 leaves A audibly quieter than B across the entire range a human actually plays - a velocity that occurs in a small minority of real notes ends up defining the patch's level everywhere.

#### Why the safety pass must not feed back into the trim

If a patch blows the ceiling at velocity 127, the cause is nearly always excessive velocity-to-amplitude depth. The correct fix is editing that routing. Pulling the master volume down instead makes the patch quiet at every normal velocity in order to fix a level it rarely reaches. So: flag it, hand it to a human.

### Polyphony: do not read the chord size off the patch

`DEFAULT_POLYLIMIT` is 16 (`src/common/globals.h:68`), and `SurgePatch.cpp:2683` actively rewrites any patch at revision <= 15 with polylimit 8 up to 16 on load. Polylimit in the factory library is therefore 16 almost universally (at least the Claes patches) and carries no authorial intent - it is a CPU guard, not a statement about voicing. Sizing the chord from it yields 16-note clusters across the board.

Use a fixed chord size per pass, clamped downward by polylimit only when it has been deliberately lowered (a patch saved at 2 or 4 means something; 16 does not).

### Derive the test case from the patch, not the folder

The issue proposes per-category MIDI. Category is a weak proxy and disappears entirely for a patch not yet filed into a factory folder. The patch itself already carries most of what we need:

- `scene[n].polymode` - `pm_mono*` means a chord measures voice stealing rather than the patch, so play a monophonic line. `pm_latch` needs care: note-on latches, releases happen via the next note, and a naive all-notes-off at the end leaves it ringing.
- `polylimit` - only when deliberately lowered, as above.
- `scenemode` - `sm_split` / `sm_chsplit` patches sound only one half against a figure that sits entirely on one side of `splitpoint`. Needs notes either side, or the channel split honoured.
- Any oscillator of type `ot_audioinput` - the patch is silent without input. Covers the whole Vocoder category and parts of FX.
- Amp EG release and FX tail - informs render length.

Category ends up feeding into nothing at all. It was originally kept for register selection, but the octave sweep below supersedes that too - see "No per-category register window". The folder a patch sits in is a label, not an input.

### Register: the octave sweep

Register is the largest single error source in the design, because K-weighting deliberately shelves the low end down. Playing a bass patch two octaves below where it is meant to sit does not merely sound wrong - it *reads* several LU quiet, and the tool then pushes it up. The error biases in one direction rather than averaging out.

Two approaches were considered and one was discarded.

**Discarded - parameter heuristics.** Sounding pitch is oscillator pitch plus oscillator octave plus scene pitch plus scene octave plus tuning plus anything modulating any of them, per oscillator, and a sub-oscillator or a detuned pair makes "the" transposition ill-defined. That amounts to reimplementing part of the pitch path, and it would be wrong on exactly the patches that matter.

**Also discarded - fundamental detection on a probe render.** Playing a single note and deriving the transposition by FFT/autocorrelation works, but it is strictly less useful than the sweep below, and it fails on inharmonic material (percussion, FX, noise-based patches).

**Adopted - a seven-octave C sweep.** Play C1 through C7 as seven sequential notes in a **single** render, at the anchor velocity, and read per-note short-term loudness out of that one buffer. It is better than fundamental detection for two reasons:

- It handles transposition *implicitly*. A patch transposed down two octaves puts its sounding range two octaves below the played range, and the region that measures well is already expressed in played-note terms with the offset folded in. We never need to know the offset, because we only ever wanted it in order to correct for it, and the sweep corrects for it by construction.
- It answers a question detection cannot touch at all: *does the patch work here?* A wavetable patch that aliases into mush above C5, or a bass whose lowpass kills everything past C4, is invisible to fundamental detection. That is the actual content of "where the patch works".

#### Why octaves rather than a finer interval

A finer sweep (minor thirds from C1 to C6, say) was considered. The reason to reject it is not cost - since the sweep is one render containing N sequential notes rather than N renders, 21 notes is barely more expensive than 7. The reason is that **sampling finer than the decision granularity buys nothing**. The sweep's output is a register choice quantized to roughly an octave, so locating the edge of the working range to within a minor third rather than an octave produces a more precise picture of something that is then discarded.

#### Selecting a register from the sweep

Do **not** simply take the loudest octave - that biases toward whatever register carries the most low-mid energy and systematically drags the trim. Instead compute the **working range**: every octave within roughly 6 LU of the sweep maximum. Measure at the **center of the working range**, taking the lower of the two central octaves when the range has even width, so the rule is deterministic.

The working range can never come out empty, since it is defined relative to the sweep's own maximum and the loudest octave is trivially within 0 LU of itself. There is no degenerate case to handle.

#### No per-category register window

An earlier draft intersected the working range with a preferred register window per category (bass around C1-C2, lead around C4-C5, and so on). That is dropped, for two reasons.

The working range makes it redundant. A bass and a lead that genuinely differ in register will differ in working range too - that is substantially what makes one a bass and the other a lead. The category window only did real work in the case where two patches of different categories share a working range, and in that case measuring both at the same place is the correct answer rather than a lost distinction.

It also double-counts. Measuring bass patches lower makes them read quieter through K-weighting, so they would be trimmed up relative to leads - but compensating for low-frequency perception is precisely what K-weighting already does. Applying a category-based register bias on top of it corrects twice for the same thing.

Dropping the window also means there is no question of which categories get a window, and no "patch is filed in a category it does not play in" flag. The working range itself is still worth emitting to the CSV as a diagnostic - it is useful for eyeballing whether a patch is filed sensibly - but it is data, not an input to the decision.

### Note hold length

Hold length derives from the patch, not from a tempo. This applies to the sweep and to the anchor pass alike.

A patch with a 4 second attack measured over a chord held for 2 seconds reads far too quiet, gets trimmed *up*, and then screams once it is actually sustaining. Fixed-duration musical phrasing (quarter notes at 120 BPM and so on) is the wrong model when the thing being measured is loudness rather than musicality.

Two mechanisms, both needed:

- **Lower bound from parameters** - amp EG attack plus decay (`scene[n].adsr[adsr_ampeg]`, `SurgeStorage.h:610` and `:878`) plus about a
  second of sustain. Unlike pitch, envelope timing *is* reliably readable.
- **Adaptive on top** - hold until short-term loudness stops rising (derivative under a threshold across a 1 s window), capped at about 8 s per note.

Parameters alone miss patches whose level develops through a slow LFO or a step sequencer rather than through the envelope. Adaptive alone can terminate early on a patch that happens to be momentarily flat mid-attack. Together they are robust.

#### The sweep needs a uniform hold, not per-note adaptation

Applying the adaptive rule per note across the octave sweep would be wrong. Each octave would be held for a different duration, so the seven measurements would be taken under seven different conditions and the resulting loudness ranking would partly reflect hold length rather than the patch. Sharp filter keytracking makes this worst exactly where register selection matters most.

So for the sweep, derive the hold **once** - amp EG attack plus decay as the floor, with a single adaptive stabilization probe on the middle note to catch LFO or step-sequencer development - and apply that same figure to all seven notes. Per-note adaptation remains correct for the anchor and safety passes, where there is a single measurement and nothing to compare it against.

Worst case that makes the sweep 56 seconds of audio; typical will be far less, and it is still a single render.

### Audio-input patches

A full-scale sine is the wrong excitation. It is a single partial, so a vocoder has essentially no spectrum to shape and reads near-silent; and for any filter- or EQ-shaped FX patch the reading depends entirely on whether the tone happens to sit in the passband. That measures the test signal, not the patch.

Use **pink noise at the target level (-19 LUFS integrated)**. The measured output then reads directly as the patch's gain through the effect, and the criterion becomes "delta approximately 0 LU" rather than "hits -19 LUFS" - which is the right criterion anyway, since an FX patch's output should track its input rather than be pinned to an absolute. Vocoder patches need noise *and* held notes.

### MPE patches

Per-note channels 2-16, `mpeEnabled` on, pitch bend range 48, timbre at 64. Pressure follows the same two-pass logic as velocity: **1.0 for the safety pass, about 0.75 for the anchor**. Anchoring at maximum pressure would impose the identical systematic penalty described above.

### Fixed performance conventions

Everything here is arbitrary; the value is that it is written down.

- Velocity 100 (anchor) / 127 (safety)
- Mod wheel 0, pitch bend centered, no aftertouch
- MIDI channel 1 (except MPE patches)
- 48 kHz sample rate

### Tail and total render length

Hold length is covered above; this is about what happens after note-off.

Adaptive, not fixed. A fixed window truncates pad releases and pads plucks with silence. Render until output stays below about -90 dBFS for 500 ms, with a hard cap of 30 seconds *beyond the last note-off*, and a warning flag when the cap is hit. Drones, infinite feedback delays and self-oscillating filters will hit the cap, and those are exactly the patches wanting a human look.

The -70 LUFS absolute gate handles trailing silence well on its own, but it does not save us from a 12-second reverb tail being cut off at 4 seconds.

### Determinism

Rather than measure run-to-run variance and set a tolerance around it, remove the variance. `SurgeStorage::rngGen` is seeded from `system_clock::now()` at construction (`SurgeStorage.h:2107`), and `seed_rand` is sitting commented out at line 2159. Expose it, and seed `rngGen` and `noiseRng` to a fixed value per patch load in the tool. Unison spread, oscillator start phase, noise and sample-and-hold LFOs all become reproducible, and the tolerance question reduces to the musical one. This is independently useful for making other tests deterministic.

## Correction

The global volume is applied at `SurgeSynthesizer.cpp:5126`, after the entire FX chain and before only the global hardclip. It is a genuinely linear last gain stage, so a -3 dB change to the global volume moves integrated LUFS by exactly -3 LU. Correction is therefore one-pass: measure, compute the offset, `setParamVal` on the global volume, `savePatch` back over the FXP. No measure/adjust/re-measure loop.

Two cases that cannot be fixed this way and need flagging instead:

- Patches already near the top of the volume range, which cannot be pushed up.
- Patches clipping internally within their own FX chain, which trimming the master will not repair.

## Implementation phases

1. `scripts/patch-loudness/` skeleton: bootstrap (venv with `--system-site-packages`, `requirements.txt`, surgepy discovery with `pip` fallback), `.gitignore` entry, README.
2. Metering module - `pyloudnorm` for gated integrated loudness, plus our own short-term windowing and 4x-oversampled true peak. Verify against a known signal (a -20 dBFS 1 kHz sine must read -20.0 LUFS).
3. RNG seeding hook in `SurgeStorage` plus its surgepy binding - the one piece that stays in C++, small, and independently useful for making other tests deterministic.
4. Patch introspection (polymode, scenemode/splitpoint, `ot_audioinput`, polylimit-if-deliberate, amp EG timing), the octave sweep, and working range selection.
5. Report mode, emitting CSV: anchor LUFS, safety true peak and short-term, selected register, flags.
6. Apply mode with a dry run, writing global volume and re-saving the FXP.

Phase 5 onward is slow enough to be an on-demand job rather than anything attached to CI.

## Open questions

- **How wide the working range is.** 6 LU below the sweep maximum is a guess and wants checking against real patches. This is the *only* tuning knob in register selection, so it carries some weight - too tight and the range collapses to a single octave, too loose and it spans everything and the center becomes meaningless.
- **What the anchor chord actually is.** A four-note chord is proposed above as the most common real usage, but the thicker the test voicing, the more we penalize polyphonic patches in sparse use.