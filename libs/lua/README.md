# libs/lua

This directory owns which Lua implementation Surge XT is built against. Everything
else in the tree links `surge::lua` and does not need to know what is behind it.

```
-DSURGE_LUA_IMPL=luajit   LuaJIT, built out of band by libs/luajitlib (the default)
-DSURGE_LUA_IMPL=puc      PUC-Rio Lua 5.1.5 plus Lua BitOp, built here from source
-DSURGE_LUA_IMPL=none     no Lua; Formula Modulator and Wavetable Script compile out
```

`SURGE_SKIP_LUA=TRUE` still works as a deprecated alias for `none`, because the
`SURGE_BUILD_RS` branch in the top level `CMakeLists.txt` sets it.

## Why a second Lua exists

LuaJIT cannot be built for Windows on ARM. Its interpreter is hand-written DynASM
assembly, and ARM64EC needs entry thunks and a different ABI mapping than the
existing `arm64` backend provides. That is VM-level porting work on LuaJIT's
assembly, not a configuration flag, so it is not something Surge can carry.

Before this directory existed, the Windows ARM64EC and ARM64 targets were built
with `-DSURGE_SKIP_LUA=TRUE` and shipped with no Formula Modulator and no
Wavetable Script at all. Running stock Lua on those two targets trades interpreter
speed for having the features exist, which is a much better deal than losing them.

**LuaJIT remains the default on every platform where it builds.** It is faster and
there is no reason to give that up; `puc` is a fallback, not a replacement.

## What is vendored

| Directory | Upstream | Version | Licence |
|---|---|---|---|
| `lua-5.1.5/` | <https://www.lua.org/ftp/lua-5.1.5.tar.gz> | 5.1.5 | MIT (`lua-5.1.5/COPYRIGHT`) |
| `luabitop-1.0.4/` | <https://bitop.luajit.org/download/LuaBitOp-1.0.4.tar.gz> | 1.0.4 | MIT (`luabitop-1.0.4/README`) |

```
lua-5.1.5.tar.gz      sha256  2640fc56a795f29d28ef15e13c34a47e223960b0240e8cb0a82d9b0738695333
LuaBitOp-1.0.4.tar.gz sha256  adc32785579fc62f7736881332f70f50fe168b2ae8d0ddb741ff018a76f48c80
```

Lua 5.1.5 is the right version rather than a newer one because LuaJIT 2.1 is
Lua 5.1 with a few 5.2 extensions, so 5.1.5 is what matches the syntax and library
surface that Surge's formulas and both preludes are written against. Lua 5.2 and
later remove `setfenv`, which Surge's formula sandbox is built on.

Both drops are byte-for-byte upstream except that files have been removed. Nothing
has been edited, so re-extracting either tarball and diffing is enough to verify
this directory. Note that this repository sets `* text=auto`, so compare against
what git stores rather than a working copy that may have been checked out CRLF.

### Files left out of the drop

From `lua-5.1.5/src`:

- `lua.c`, `luac.c`, `print.c` — the standalone interpreter and bytecode compiler.
  Not wanted in a plugin, and `lua.c` defines `main`.
- `linit.c` — replaced by `surge-lua-openlibs.c`, see below.

Also dropped: upstream's makefiles, `doc/`, `etc/`, `test/`, and Lua BitOp's
makefiles, `doc/` and its benchmark and test scripts. `COPYRIGHT` and `README` are
kept in both for licence and provenance.

## Why `bit` needs Lua BitOp

Surge's formula sandbox whitelists `bit` alongside `math`, `string` and `table`
(see `sandboxLibraryTables` in `src/common/LuaSupport.cpp`). `bit` is built into
LuaJIT but is not part of stock Lua, so without it any formula calling `bit.band`
would break on exactly the platforms this backend serves.

Lua BitOp is the right source for it rather than a hand-rolled C++ implementation:
LuaJIT's built-in `bit` *is* Lua BitOp, same author and same semantics, so a
formula behaves identically on both backends instead of relying on Surge to
reproduce int32 wraparound, shift-count masking and `tobit`/`tohex`/`bswap`/`rol`
exactly. `UnitTestsLUA.cpp` already exercises `bit.tobit(0xffffffff)`, which makes
the existing test suite a real parity gate.

## `surge-lua-openlibs.c`

Upstream's `linit.c` with `{"bit", luaopen_bit}` added to the table of libraries
that `luaL_openlibs` opens, which is what LuaJIT does for its own `bit`. Doing it
here means none of Surge's `lua_State` creation sites — three in `src`, fourteen in
the tests — need to know which backend they are talking to.

It is a separate file rather than a patch to `linit.c` so that every file under
`lua-5.1.5/` stays verifiably unmodified upstream.

## Invariant: Lua C functions must stay free of RAII

Both backends propagate errors out of a `lua_CFunction` with `longjmp`, so C++
destructors in that function's frame **do not run**. Surge's Lua C functions
already respect this — look at `pffftTransform` in `src/common/LuaSupport.cpp`,
which frees every buffer by hand before each `luaL_error` — and new ones must too.

Building the vendored sources as C++ would switch Lua to `throw`/`catch` and make
this moot, but it would also give the whole Lua API C++ linkage and break the
`extern "C"` include block in `LuaSupport.h`, so the sources are compiled as C
exactly as upstream intends.

## What stock Lua does not provide

`ffi`, `jit.*`, `table.new` and `table.clear` are LuaJIT extensions and are absent
on this backend. Surge uses none of them: the only C API difference it actually
runs into is `LUA_OK`, which LuaJIT backports from 5.2 and which `LuaSupport.h`
defines when it is missing. Losing `ffi` is a small security gain for a plugin that
runs scripts out of patch files.

The real cost is speed. `Formula::valueAt` runs a `lua_pcall` per voice per block,
and PUC Lua's C interpreter is slower than LuaJIT's assembly one even before the
JIT engages, so patches with many formula LFOs will cost more CPU on the ARM
targets than the same patch does on x86_64.

## Updating

1. Download the new tarball and check its hash.
2. Replace the directory contents, keeping the omissions listed above, and rename
   the directory to the new version.
3. Update the paths and the source list in `CMakeLists.txt`. Sources are listed
   explicitly rather than globbed so that a new file is a deliberate, reviewable
   change.
4. Update the table and hashes above.
5. Build with `-DSURGE_LUA_IMPL=puc` and run `surge-testrunner`. The Lua tests pass
   unmodified on both backends, and that is the check that matters.
